"""Orchestration complete de la veille.

Reproduit la chaine du workflow n8n, dans l'ordre:
intro texte -> lecture des 14 flux -> deduplication -> recuperation de
l'article -> filtrage LLM -> resume LLM -> message Telegram -> audio Telegram,
puis, apres le dernier article, l'audio de presentation du jour et le recap.

Differences voulues: un article n'est marque comme vu qu'apres un envoi reussi,
et la presentation vocale part en dernier parce que les audios s'ecoutent du
plus recent au plus ancien (elle est donc la premiere entendue).
"""

from __future__ import annotations

import html
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

import requests

from . import tts as tts_mod
from .config import Config
from .extract import fetch_text
from .feeds import Candidate, read_all
from .llm import Ollama, OllamaError
from .store import Store
from .telegram import Telegram

JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre")


def date_fr(now: datetime) -> tuple[str, str]:
    """Date et heure en francais sans dependre de la locale du systeme."""
    jour = JOURS[now.weekday()]
    mois = MOIS[now.month - 1]
    return f"{jour} {now.day} {mois} {now.year}", now.strftime("%H:%M")


def audio_performer(index: int, date_str: str) -> str:
    """Ligne « artiste » du lecteur Telegram, sous le titre de l'audio.

    Sans elle le telephone affiche « Artiste inconnu ». On y met le numero de
    l'article dans la veille du jour et la date, pour se reperer quand on ecoute
    la liste dans le desordre.
    """
    return f"Article {index} · {date_str}"


def count_label(sent: int) -> str:
    return f"{sent} article{'s' if sent > 1 else ''}"


def intro_speech(date_str: str, time_str: str, sent: int) -> str:
    """Texte de la presentation vocale du jour.

    Envoyee apres le dernier article, elle peut annoncer combien il y en a.
    """
    if sent == 0:
        detail = "Aucun nouvel article retenu aujourd'hui."
    elif sent == 1:
        detail = "Un seul article au programme aujourd'hui."
    else:
        detail = f"{sent} articles au programme aujourd'hui."
    return f"Bonjour. Veille IA du {date_str}, {time_str}. {detail}"


def render_article(title: str, link: str, source: str, summary: str, *,
                   show_commands: bool = False) -> str:
    """Message Telegram en HTML.

    Tout le texte est echappe: seules les balises <b> et <a> que l'on emet ici
    arrivent a l'analyseur de Telegram. Un & ou un < dans un titre ou une URL
    est la cause classique du "can't parse entities".
    """
    parts = [
        f"<b>📰 {html.escape(title[:200])}</b>",
        "",
        f"📝 {html.escape(summary or 'Pas de résumé disponible')}",
        "",
        f"🗞 {html.escape(source)}",
        f'<a href="{html.escape(link, quote=True)}">📖 Lire l\'article complet</a>',
    ]
    if show_commands:
        parts += ["", "<b>Commandes disponibles:</b>",
                  "💬 /info [votre question]", "⭐ /rate [1-5]", "📋 /list"]
    return "\n".join(parts)


@dataclass
class Summary:
    feeds_ok: int = 0
    feeds_ko: int = 0
    candidates: int = 0
    fresh: int = 0
    kept: int = 0
    sent: int = 0
    audio: int = 0
    errors: int = 0
    duration: float = 0.0
    stopped: bool = False
    messages: list[str] = field(default_factory=list)

    def as_text(self) -> str:
        return (f"{self.sent} article(s) envoyé(s) sur {self.kept} retenu(s), "
                f"{self.fresh} nouveaux parmi {self.candidates} candidats, "
                f"{self.feeds_ok}/{self.feeds_ok + self.feeds_ko} flux lus, "
                f"{self.errors} erreur(s), {self.duration:.0f}s")


class Pipeline:
    def __init__(self, cfg: Config, store: Store, *, on_event=None, on_progress=None,
                 stop: threading.Event | None = None, dry_run: bool = False,
                 force_send: bool = False):
        self.cfg = cfg
        self.store = store
        self.stop = stop or threading.Event()
        self.dry_run = dry_run
        # mode test: on montre le verdict du filtre mais on envoie quand meme,
        # pour que le test valide toujours la chaine de livraison
        self.force_send = force_send
        self._on_event = on_event or (lambda level, text: None)
        self._on_progress = on_progress or (lambda **kw: None)
        self.session = requests.Session()
        self.llm = Ollama(cfg, self.session)
        self.tg = Telegram(cfg.telegram.bot_token, cfg.telegram.chat_id,
                           self.session, dry_run=dry_run)
        self.tts, self.tts_desc = tts_mod.build(cfg, self.session)
        self._spoken_articles = 0  # sert a alterner les voix un article sur deux
        self.run_date, self.run_time = date_fr(datetime.now())  # refixes dans run()

    # -- utilitaires -----------------------------------------------------
    def log(self, level: str, text: str) -> None:
        self._on_event(level, text)

    def sleep(self, seconds: float) -> None:
        """Pause interruptible, pour que le bouton Arreter reponde tout de suite."""
        deadline = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < deadline:
            if self.stop.is_set():
                return
            time.sleep(min(0.2, deadline - time.monotonic()))

    @property
    def cancelled(self) -> bool:
        return self.stop.is_set()

    # -- mise en forme ---------------------------------------------------
    def format_message(self, item: Candidate, summary: str) -> str:
        return render_article(item.title, item.link, item.source, summary,
                              show_commands=self.cfg.telegram.show_commands)

    def audio_text(self, item: Candidate, summary: str) -> str:
        text = f"Nouvel article : {item.title}. {summary}"
        limit = self.cfg.tts.max_chars
        if limit and len(text) > limit:
            text = text[: limit - 3].rstrip() + "..."
        return text

    def next_voice(self) -> str | None:
        """Alterne les voix: un article sur deux est lu par la voix alternative.

        Renvoie None quand une seule voix est disponible, le backend prend alors
        sa voix par defaut.
        """
        self._spoken_articles += 1
        alt = self.cfg.tts.voice_alt.strip()
        if not alt or alt not in getattr(self.tts, "voices", ()):
            return None
        return alt if self._spoken_articles % 2 == 0 else self.cfg.tts.voice

    def speak(self, text: str, *, caption: str, title: str, performer: str = "",
              voice: str | None = None) -> bool:
        """Synthese + envoi audio. Renvoie False si l'audio est indisponible."""
        if self.tts is None or not self.cfg.telegram.send_audio:
            return False
        try:
            mp3 = self.tts.synthesize_mp3(text, voice)
        except Exception as exc:
            self.log("warn", f"synthèse vocale impossible: {exc}")
            if voice is None or voice == self.cfg.tts.voice:
                return False
            # la voix alternee a echoue: on retombe sur la voix principale
            try:
                mp3 = self.tts.synthesize_mp3(text, self.cfg.tts.voice)
            except Exception as exc2:
                self.log("warn", f"synthèse vocale impossible: {exc2}")
                return False
        try:
            self.tg.send_audio(mp3, caption=caption, title=title, performer=performer)
        except Exception as exc:
            self.log("warn", f"envoi audio impossible: {exc}")
            return False
        return True

    # -- etapes ----------------------------------------------------------
    def send_intro(self) -> None:
        """Message texte d'ouverture. La version audio part en dernier, voir send_presentation."""
        text = (f"🗓 <b>Veille IA du {self.run_date}</b>\n⏰ <b>{self.run_time}</b>\n\n"
                f"📡 Récupération des articles en cours...")
        self.tg.send_message(text)
        self.log("ok", f"intro envoyée ({self.run_date} {self.run_time})")

    def send_presentation(self, summary: Summary) -> None:
        """Audio de presentation du jour, envoye apres le dernier article.

        Les audios s'ecoutent du plus recent au plus ancien: place en dernier,
        celui-ci est le premier entendu, et il annonce le nombre d'articles.
        """
        label = count_label(summary.sent)
        spoken = self.speak(
            intro_speech(self.run_date, self.run_time, summary.sent),
            caption=f"🗓 Présentation de la veille du {self.run_date} · {label}",
            title=f"Veille IA du {self.run_date}",
            performer=f"Présentation · {label}")
        if spoken:
            self.log("ok", "audio de présentation envoyé")

    def collect(self, summary: Summary) -> list[Candidate]:
        def feed_done(feed, count, exc):
            if exc is None:
                summary.feeds_ok += 1
                self.log("info", f"{feed.name}: {count} entree(s)")
            else:
                summary.feeds_ko += 1
                self.log("warn", f"{feed.name}: {type(exc).__name__}")
            self._on_progress(feeds=summary.feeds_ok + summary.feeds_ko)

        items, errors = read_all(self.cfg, self.session, on_feed=feed_done,
                                 stop=self.stop)
        for name, msg in errors:
            summary.messages.append(f"flux {name}: {msg}")
        summary.candidates = len(items)
        self.log("step", f"{len(items)} candidats sur {len(self.cfg.feeds)} flux "
                         f"({summary.feeds_ko} en erreur)")
        return items

    def keep_fresh(self, items: list[Candidate], summary: Summary) -> list[Candidate]:
        fresh, seen_now = [], set()
        for item in items:
            keys = tuple(k for _, k in self.store.keys_for(item.link, item.title))
            if any(k in seen_now for k in keys):
                continue  # doublon entre deux flux dans la meme execution
            if self.store.is_seen(item.link, item.title):
                continue
            seen_now.update(keys)
            fresh.append(item)
        summary.fresh = len(fresh)
        skipped = len(items) - len(fresh)
        self.log("step", f"{len(fresh)} nouveaux articles ({skipped} déjà traités)")
        if self.cfg.fetch.max_total > 0 and len(fresh) > self.cfg.fetch.max_total:
            self.log("warn", f"limite max_total={self.cfg.fetch.max_total}: "
                             f"{len(fresh) - self.cfg.fetch.max_total} article(s) "
                             f"reportés à la prochaine exécution")
            fresh = fresh[: self.cfg.fetch.max_total]
        return fresh

    def download(self, items: list[Candidate], summary: Summary) -> list[Candidate]:
        """Recuperation du texte des articles, en parallele."""
        def work(item: Candidate) -> Candidate:
            if self.cancelled:
                return item
            try:
                item.content = fetch_text(
                    item.link, self.session,
                    timeout=self.cfg.fetch.http_timeout_s,
                    user_agent=self.cfg.fetch.user_agent,
                    max_chars=self.cfg.fetch.max_content_chars)
            except Exception as exc:
                item.content = ""
                summary.messages.append(f"{item.short_title}: {type(exc).__name__}")
            return item

        workers = max(1, min(self.cfg.fetch.workers, len(items) or 1))
        done = 0
        out: list[Candidate] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for item in pool.map(work, items):
                done += 1
                self._on_progress(fetched=done)
                if item.content:
                    out.append(item)
                else:
                    summary.errors += 1
        self.log("step", f"{len(out)}/{len(items)} articles téléchargés")
        return out

    def process(self, items: list[Candidate], summary: Summary) -> None:
        total = len(items)
        for index, item in enumerate(items, start=1):
            if self.cancelled:
                summary.stopped = True
                self.log("warn", "arrêt demandé, interruption")
                return
            self._on_progress(processed=index - 1, total=total)
            self.log("info", f"[{index}/{total}] {item.short_title}")

            try:
                keep, raw = self.llm.is_relevant(item.title, item.content)
            except OllamaError as exc:
                summary.errors += 1
                self.log("err", f"filtrage: {exc}")
                continue
            if not keep:
                if not self.force_send:
                    self.log("info", f"    écarté ({raw[:40]})")
                    if not self.dry_run and self.cfg.fetch.skip_rejected:
                        self.store.mark_rejected(item.link, item.title)
                    continue
                self.log("warn", f"    le filtre repond {raw[:20]}, envoi force "
                                 f"(mode test)")
            summary.kept += 1

            try:
                text = self.llm.summarize(item.title, item.content)
            except OllamaError as exc:
                summary.errors += 1
                self.log("err", f"résumé: {exc}")
                continue
            if not text:
                summary.errors += 1
                self.log("warn", "    résumé vide, article ignoré")
                continue

            try:
                self.tg.send_message(self.format_message(item, text))
            except Exception as exc:
                summary.errors += 1
                self.log("err", f"envoi Telegram: {exc}")
                continue

            summary.sent += 1
            self.log("ok", "    envoyé")
            with_audio = False
            if self.cfg.telegram.send_audio:
                self.sleep(self.cfg.pacing.between_audio_s)
                voice = self.next_voice()
                with_audio = self.speak(
                    self.audio_text(item, text),
                    caption=item.title, title=item.title,
                    performer=audio_performer(summary.sent, self.run_date),
                    voice=voice)
                if with_audio:
                    summary.audio += 1
                    self.log("ok", "    audio envoyé"
                                   + (f" ({voice})" if voice else ""))

            if not self.dry_run:
                self.store.mark_seen(item.link, item.title)
                self.store.record_sent(link=item.link, title=item.title,
                                       summary=text, source=item.source,
                                       audio=with_audio)
            self._on_progress(processed=index, total=total, sent=summary.sent)
            self.sleep(self.cfg.pacing.between_messages_s)

    def send_recap(self, summary: Summary) -> None:
        if not self.cfg.telegram.send_recap:
            return
        icon = "⏹" if summary.stopped else "✅"
        text = (f"{icon} <b>Veille terminée</b>\n"
                f"{summary.sent} article(s) envoyé(s), "
                f"{summary.fresh} nouveaux sur {summary.candidates} candidats\n"
                f"⏱ {summary.duration:.0f}s")
        try:
            self.tg.send_message(text)
        except Exception as exc:
            self.log("warn", f"récap non envoyé: {exc}")

    # -- point d'entree --------------------------------------------------
    def run(self) -> Summary:
        started = time.monotonic()
        self.run_date, self.run_time = date_fr(datetime.now())
        summary = Summary()
        run = self.store.start_run()
        self.log("step", "démarrage de la veille"
                         + (" (mode simulation, aucun envoi)" if self.dry_run else ""))
        self.log("info", f"voix: {self.tts_desc}")
        try:
            self.send_intro()
            items = self.collect(summary)
            if not self.cancelled:
                fresh = self.keep_fresh(items, summary)
                if fresh:
                    self.process(self.download(fresh, summary), summary)
                else:
                    self.log("ok", "rien de nouveau depuis la dernière exécution")
        except Exception as exc:
            summary.errors += 1
            self.log("err", f"échec: {type(exc).__name__}: {exc}")
        finally:
            summary.stopped = summary.stopped or self.cancelled
            summary.duration = time.monotonic() - started
            self.store.finish_run(
                run, feeds_ok=summary.feeds_ok, feeds_ko=summary.feeds_ko,
                candidates=summary.candidates, fresh=summary.fresh,
                kept=summary.kept, sent=summary.sent, errors=summary.errors,
                note="simulation" if self.dry_run else None)
            self.send_presentation(summary)
            self.send_recap(summary)
            self.log("step", summary.as_text())
        return summary
