"""Interface graphique Tkinter: l'application qu'on lance au double-clic."""

from __future__ import annotations

import queue
import shutil
import subprocess
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import scrolledtext, ttk

from . import config as cfg_mod
from . import journal
from .config import Config, ConfigError
from .llm import Ollama
from .pipeline import Pipeline
from .store import Store
from .telegram import Telegram
from . import tts as tts_mod

COLORS = {
    "bg": "#12161c",
    "panel": "#1a2029",
    "text": "#e6e9ef",
    "muted": "#8b95a7",
    "accent": "#4c9be8",
    "ok": "#4ec98a",
    "warn": "#e8b84c",
    "err": "#e8615c",
}


def notify(title: str, body: str) -> None:
    if shutil.which("notify-send"):
        subprocess.Popen(["notify-send", "-a", "Veille IA", title, body],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def open_path(path: Path) -> None:
    if shutil.which("xdg-open"):
        subprocess.Popen(["xdg-open", str(path)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class App(tk.Tk):
    def __init__(self):
        # className fixe WM_CLASS, ce qui relie la fenetre au fichier .desktop:
        # bonne icone dans le dock, dans alt-tab et dans la barre de titre.
        super().__init__(className="VeilleIA")
        self.title("Veille IA")
        self.geometry("940x660")
        self.minsize(760, 520)
        self.configure(bg=COLORS["bg"])

        self.events: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.cfg: Config | None = None
        self.config_error: str | None = None
        self.counters = {"feeds": 0, "fetched": 0, "processed": 0, "total": 0, "sent": 0}

        self._build_style()
        self._build_ui()
        self._load_config()
        self.after(120, self._drain)
        self.after(200, self.check_env)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- construction ----------------------------------------------------
    def _build_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=COLORS["bg"], foreground=COLORS["text"],
                        fieldbackground=COLORS["panel"], borderwidth=0)
        style.configure("TFrame", background=COLORS["bg"])
        style.configure("Panel.TFrame", background=COLORS["panel"])
        style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"],
                        font=("Ubuntu", 10))
        style.configure("Panel.TLabel", background=COLORS["panel"])
        style.configure("Title.TLabel", font=("Ubuntu", 20, "bold"))
        style.configure("Sub.TLabel", foreground=COLORS["muted"], font=("Ubuntu", 10))
        style.configure("Mono.TLabel", foreground=COLORS["muted"],
                        font=("Ubuntu Mono", 9))
        style.configure("TButton", padding=(14, 9), font=("Ubuntu", 10),
                        background=COLORS["panel"], foreground=COLORS["text"])
        style.map("TButton", background=[("active", "#232b36"),
                                         ("disabled", "#171c23")],
                  foreground=[("disabled", COLORS["muted"])])
        style.configure("Go.TButton", background=COLORS["accent"],
                        foreground="#0b1017", font=("Ubuntu", 11, "bold"))
        style.map("Go.TButton", background=[("active", "#61a9ec"),
                                            ("disabled", "#2b3a4a")])
        style.configure("TProgressbar", background=COLORS["accent"],
                        troughcolor=COLORS["panel"], thickness=8)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=18)
        root.pack(fill="both", expand=True)

        head = ttk.Frame(root)
        head.pack(fill="x")
        ttk.Label(head, text="Veille IA", style="Title.TLabel").pack(side="left")
        self.clock = ttk.Label(head, text="", style="Sub.TLabel")
        self.clock.pack(side="right")
        ttk.Label(root, text="Flux RSS, filtrage et résumé par Ollama, envoi Telegram "
                             "avec voix Piper. Tout en local.",
                  style="Sub.TLabel").pack(fill="x", pady=(2, 14))

        checks = ttk.Frame(root, style="Panel.TFrame", padding=14)
        checks.pack(fill="x")
        self.check_labels: dict[str, ttk.Label] = {}
        for key, title in (("ollama", "Ollama"), ("telegram", "Telegram"),
                           ("tts", "Voix"), ("store", "Base locale")):
            row = ttk.Frame(checks, style="Panel.TFrame")
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=title, width=12, style="Panel.TLabel",
                      font=("Ubuntu", 10, "bold")).pack(side="left")
            label = ttk.Label(row, text="vérification...", style="Panel.TLabel",
                              foreground=COLORS["muted"])
            label.pack(side="left", fill="x", expand=True)
            self.check_labels[key] = label
        ttk.Button(checks, text="Revérifier", command=self.check_env).pack(
            anchor="e", pady=(8, 0))

        actions = ttk.Frame(root)
        actions.pack(fill="x", pady=14)
        self.btn_run = ttk.Button(actions, text="▶  Lancer la veille",
                                  style="Go.TButton", command=self.start)
        self.btn_run.pack(side="left")
        self.btn_stop = ttk.Button(actions, text="⏹  Arrêter",
                                   command=self.request_stop, state="disabled")
        self.btn_stop.pack(side="left", padx=8)
        self.btn_dry = ttk.Button(actions, text="🧪  Simulation",
                                  command=lambda: self.start(dry_run=True))
        self.btn_dry.pack(side="left", padx=(8, 0))
        self.btn_test = ttk.Button(actions, text="📨  Test (1 article)",
                                   command=lambda: self.start(test=True))
        self.btn_test.pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="⚙  Configuration",
                   command=self.open_config).pack(side="right")
        ttk.Button(actions, text="📄  Journal",
                   command=lambda: open_path(cfg_mod.LOG_PATH)).pack(
            side="right", padx=8)

        self.progress = ttk.Progressbar(root, mode="determinate", maximum=100)
        self.progress.pack(fill="x")
        self.counter_label = ttk.Label(root, text="Prêt.", style="Sub.TLabel")
        self.counter_label.pack(fill="x", pady=(6, 10))

        self.status = ttk.Label(root, text=str(cfg_mod.CONFIG_PATH), style="Mono.TLabel")
        self.status.pack(side="bottom", fill="x", pady=(8, 0))

        self.log = scrolledtext.ScrolledText(
            root, wrap="word", height=16, relief="flat",
            bg=COLORS["panel"], fg=COLORS["text"],
            insertbackground=COLORS["text"], font=("Ubuntu Mono", 10),
            padx=12, pady=10)
        self.log.pack(fill="both", expand=True)
        self.log.configure(state="disabled")
        for level, color in (("info", COLORS["text"]), ("ok", COLORS["ok"]),
                             ("warn", COLORS["warn"]), ("err", COLORS["err"]),
                             ("step", COLORS["accent"])):
            self.log.tag_configure(level, foreground=color)
        self.log.tag_configure("step", foreground=COLORS["accent"],
                               font=("Ubuntu Mono", 10, "bold"))

        self.log.vbar.configure(bg=COLORS["panel"], troughcolor=COLORS["bg"],
                                activebackground=COLORS["accent"],
                                highlightthickness=0, bd=0, width=12)
        self._tick()

    def _tick(self) -> None:
        self.clock.config(text=datetime.now().strftime("%d/%m/%Y  %H:%M:%S"))
        self.after(1000, self._tick)

    # -- configuration ---------------------------------------------------
    def _load_config(self) -> None:
        try:
            cfg_mod.ensure_config()
            self.cfg = cfg_mod.load()
            self.config_error = None
        except (ConfigError, Exception) as exc:
            self.cfg = None
            self.config_error = str(exc)
            self.write("err", f"configuration illisible: {exc}")

    def open_config(self) -> None:
        cfg_mod.ensure_config()
        open_path(cfg_mod.CONFIG_PATH)
        self.write("info", f"configuration: {cfg_mod.CONFIG_PATH}"
                           "  (Revérifier après modification)")

    # -- diagnostics -----------------------------------------------------
    def set_check(self, key: str, ok: bool | None, text: str) -> None:
        color = COLORS["muted"] if ok is None else (COLORS["ok"] if ok else COLORS["err"])
        mark = "…" if ok is None else ("✓" if ok else "✗")
        self.check_labels[key].config(text=f"{mark}  {text}", foreground=color)

    def check_env(self) -> None:
        self._load_config()
        for key in self.check_labels:
            self.set_check(key, None, "vérification...")
        threading.Thread(target=self._check_worker, daemon=True).start()

    def _check_worker(self) -> None:
        cfg = self.cfg
        if cfg is None:
            self.events.put(("check", ("ollama", False, self.config_error or "config absente")))
            return
        ok, msg = Ollama(cfg).check()
        self.events.put(("check", ("ollama", ok, msg)))

        if not cfg.telegram_ready:
            self.events.put(("check", ("telegram", False,
                                       "token ou chat_id manquant dans la configuration")))
        else:
            ok, msg = Telegram(cfg.telegram.bot_token, cfg.telegram.chat_id).check()
            self.events.put(("check", ("telegram", ok, msg)))

        backend, desc = tts_mod.build(cfg)
        self.events.put(("check", ("tts", backend is not None, desc)))

        try:
            store = Store(cfg_mod.DB_PATH)
            counts = store.counts()
            store.close()
            last = ("jamais" if not counts["last_run"]
                    else datetime.fromtimestamp(counts["last_run"]).strftime("%d/%m %H:%M"))
            self.events.put(("check", ("store", True,
                                       f"{counts['seen']} liens connus, "
                                       f"{counts['sent']} envois, dernière exécution: {last}")))
        except Exception as exc:
            self.events.put(("check", ("store", False, str(exc))))
        self.events.put(("checks_done", None))

    # -- execution -------------------------------------------------------
    def start(self, dry_run: bool = False, test: bool = False) -> None:
        if self.worker and self.worker.is_alive():
            return
        self._load_config()
        if self.cfg is None:
            self.write("err", "configuration invalide, corrige-la avant de lancer")
            return
        if not dry_run and not self.cfg.telegram_ready:
            self.write("err", "token Telegram absent: renseigne bot_token dans la "
                              "configuration (bouton Configuration)")
            return

        self.stop_event = threading.Event()
        self.counters = {"feeds": 0, "fetched": 0, "processed": 0, "total": 0, "sent": 0}
        self.progress.config(value=0)
        self.btn_run.config(state="disabled")
        self.btn_dry.config(state="disabled")
        self.btn_test.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.write("step", "=" * 60)
        if test:
            self.write("step", "mode test: 1 article, envoi Telegram reel, "
                               "base jetable (l'article restera disponible)")
        self.worker = threading.Thread(target=self._run_worker, args=(dry_run, test),
                                       daemon=True)
        self.worker.start()

    def request_stop(self) -> None:
        self.stop_event.set()
        self.btn_stop.config(state="disabled")
        self.write("warn", "arrêt demandé, fin de l'article en cours...")

    def _run_worker(self, dry_run: bool, test: bool = False) -> None:
        store = None
        try:
            cfg = cfg_mod.test_variant(self.cfg, 1) if test else self.cfg
            store = Store(cfg_mod.fresh_test_db() if test else cfg_mod.DB_PATH)
            pipeline = Pipeline(
                cfg, store,
                on_event=lambda level, text: self.events.put(("log", (level, text))),
                on_progress=lambda **kw: self.events.put(("progress", kw)),
                stop=self.stop_event, dry_run=dry_run, force_send=test)
            summary = pipeline.run()
            self.events.put(("done", summary))
        except Exception as exc:
            self.events.put(("log", ("err", f"{type(exc).__name__}: {exc}")))
            self.events.put(("done", None))
        finally:
            if store is not None:
                store.close()

    # -- boucle d'evenements ---------------------------------------------
    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self.write(*payload)
                elif kind == "check":
                    self.set_check(*payload)
                elif kind == "checks_done":
                    pass
                elif kind == "progress":
                    self._update_progress(payload)
                elif kind == "done":
                    self._finish(payload)
        except queue.Empty:
            pass
        self.after(120, self._drain)

    def _update_progress(self, data: dict) -> None:
        self.counters.update({k: v for k, v in data.items() if v is not None})
        total = self.counters.get("total") or 0
        done = self.counters.get("processed") or 0
        if total:
            self.progress.config(maximum=total, value=done)
        self.counter_label.config(
            text=f"flux lus: {self.counters['feeds']}   "
                 f"articles téléchargés: {self.counters['fetched']}   "
                 f"traités: {done}/{total or '?'}   "
                 f"envoyés: {self.counters['sent']}")

    def _finish(self, summary) -> None:
        self.btn_run.config(state="normal")
        self.btn_dry.config(state="normal")
        self.btn_test.config(state="normal")
        self.btn_stop.config(state="disabled")
        if summary is None:
            self.counter_label.config(text="Terminé avec une erreur.")
            notify("Veille IA", "Exécution interrompue par une erreur")
            return
        self.progress.config(value=self.progress["maximum"])
        self.counter_label.config(text=summary.as_text())
        notify("Veille IA", summary.as_text())
        self.check_env()

    def write(self, level: str, text: str) -> None:
        journal.append(level, text)
        stamp = time.strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"{stamp}  {text}\n", level)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _on_close(self) -> None:
        if self.worker and self.worker.is_alive():
            self.stop_event.set()
        self.destroy()


def main() -> int:
    App().mainloop()
    return 0
