"""Tests de la logique reprise des nodes n8n."""

import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from veille_ia.config import Config
from veille_ia.extract import html_to_text
from veille_ia.llm import NO_RE, YES_RE, clean_summary, strip_reasoning
from veille_ia.pipeline import (Pipeline, Summary, audio_performer, date_fr,
                                intro_speech, render_article)
from veille_ia.store import normalize_link, normalize_title
from veille_ia.telegram import Telegram, clip


class TestDedup(unittest.TestCase):
    def test_tracking_params_ignores(self):
        a = normalize_link("https://www.actuia.com/actu/x/?utm_source=rss&utm_medium=feed")
        b = normalize_link("http://actuia.com/actu/x")
        self.assertEqual(a, b)

    def test_real_params_kept(self):
        self.assertNotEqual(normalize_link("https://a.com/p?id=1"),
                            normalize_link("https://a.com/p?id=2"))

    def test_title_normalisation(self):
        self.assertEqual(normalize_title("L'IA Générative : État des lieux !"),
                         normalize_title("l ia generative etat des lieux"))

    def test_empty(self):
        self.assertEqual(normalize_link(""), "")
        self.assertEqual(normalize_title(None), "")


class TestFilterParsing(unittest.TestCase):
    def test_yes_tokens(self):
        for text in ("$$oui", "$$ oui", "oui", "Oui.", "  OUI  "):
            self.assertTrue(YES_RE.search(text.lower()), text)

    def test_no_tokens(self):
        for text in ("$$non", "$$ non", "non", "Non."):
            self.assertTrue(NO_RE.search(text.lower()), text)
            self.assertFalse(YES_RE.search(text.lower()), text)

    def test_garbage_is_not_yes(self):
        for text in ("peut-etre", "je ne sais pas", ""):
            self.assertFalse(YES_RE.search(text), text)


class TestSummaryCleaning(unittest.TestCase):
    def test_dollar_marker(self):
        self.assertEqual(clean_summary("$$ Le resume utile."), "Le resume utile.")

    def test_resume_label(self):
        self.assertEqual(clean_summary("RÉSUMÉ: Le texte."), "Le texte.")
        self.assertEqual(clean_summary("Resume : Le texte."), "Le texte.")

    def test_no_marker_keeps_all(self):
        self.assertEqual(clean_summary("Texte brut."), "Texte brut.")

    def test_fence_around_marker(self):
        """Cas ou le node n8n laissait le ``` final collé au resume."""
        self.assertEqual(clean_summary("```\n$$ Le texte.\n```"), "Le texte.")
        self.assertEqual(clean_summary("```markdown\n$$ Le texte.\n```"), "Le texte.")

    def test_reasoning_removed(self):
        self.assertEqual(clean_summary("<think>reflexion</think>$$ Le texte."),
                         "Le texte.")
        self.assertEqual(strip_reasoning("<think>a</think>b"), "b")

    def test_blank_lines_collapsed(self):
        self.assertEqual(clean_summary("$$ a\n\n\n\nb"), "a\n\nb")


class TestHtmlExtraction(unittest.TestCase):
    HTML = """<html><body>
        <nav>menu accueil contact</nav>
        <script>var x = 1;</script>
        <article>%s</article>
        <footer>mentions legales</footer>
    </body></html>""" % ("Le contenu utile de l'article. " * 30)

    def test_boilerplate_removed(self):
        text = html_to_text(self.HTML)
        self.assertIn("Le contenu utile", text)
        for junk in ("menu accueil", "var x", "mentions legales"):
            self.assertNotIn(junk, text)

    def test_truncation(self):
        text = html_to_text(self.HTML, max_chars=100)
        self.assertLessEqual(len(text), 101)
        self.assertTrue(text.endswith("…"))


class TestFormatting(unittest.TestCase):
    def test_date_fr(self):
        self.assertEqual(date_fr(datetime(2026, 8, 24, 20, 0)),
                         ("lundi 24 août 2026", "20:00"))
        self.assertEqual(date_fr(datetime(2026, 2, 1, 9, 5))[0],
                         "dimanche 1 février 2026")

    def test_clip(self):
        self.assertEqual(clip("court", 10), "court")
        out = clip("un texte nettement plus long que la limite", 20)
        self.assertLessEqual(len(out), 20)
        self.assertTrue(out.endswith("…"))


class TestTelegramHtml(unittest.TestCase):
    NASTY_TITLE = 'Nvidia & AMD: <script>alert("x")</script> "l\'IA" >> tout'
    NASTY_LINK = "https://a.com/p?a=1&b=2&c=<x>"

    def render(self, **kw):
        return render_article(self.NASTY_TITLE, self.NASTY_LINK, "Flux & Co",
                             'Resume avec & et <b> et "guillemets"', **kw)

    def test_no_raw_special_chars(self):
        out = self.render()
        # aucune balise autre que celles que l'on emet volontairement
        self.assertNotIn("<script>", out)
        self.assertIn("&amp;", out)
        self.assertIn("&lt;script&gt;", out)

    def test_only_allowed_tags(self):
        import re
        tags = set(re.findall(r"</?([a-zA-Z]+)", self.render()))
        self.assertLessEqual(tags, {"b", "a"})

    def test_href_is_escaped(self):
        out = self.render()
        self.assertIn('href="https://a.com/p?a=1&amp;b=2&amp;c=&lt;x&gt;"', out)

    def test_parses_as_xml(self):
        """Si ca se parse comme du XML, l'analyseur de Telegram l'accepte."""
        import xml.etree.ElementTree as ET
        ET.fromstring("<root>" + self.render() + "</root>")
        ET.fromstring("<root>" + self.render(show_commands=True) + "</root>")

    def test_long_title_truncated(self):
        out = render_article("T" * 500, "https://a.com", "S", "R")
        self.assertIn("T" * 200, out)
        self.assertNotIn("T" * 201, out)


class TestStore(unittest.TestCase):
    def setUp(self):
        import tempfile
        from veille_ia.store import Store
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.sqlite")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_seen_roundtrip(self):
        link, title = "https://a.com/article-1?utm_source=rss", "Un titre"
        self.assertFalse(self.store.is_seen(link, title))
        self.store.mark_seen(link, title)
        self.assertTrue(self.store.is_seen(link, title))
        # meme article vu par un autre flux, avec un lien decore differemment
        self.assertTrue(self.store.is_seen("http://www.a.com/article-1/", title))
        # article different
        self.assertFalse(self.store.is_seen("https://a.com/article-2", "Autre titre"))

    def test_same_title_other_link(self):
        self.store.mark_seen("https://a.com/x", "Titre partage")
        self.assertTrue(self.store.is_seen("https://b.com/y", "Titre partagé"))

    def test_run_and_history(self):
        run = self.store.start_run()
        self.store.record_sent(link="https://a.com/x", title="T", summary="S",
                               source="Flux", audio=True)
        self.store.finish_run(run, candidates=10, fresh=4, kept=2, sent=1, errors=0)
        counts = self.store.counts()
        self.assertEqual(counts["sent"], 1)
        self.assertIsNotNone(counts["last_run"])
        self.assertEqual(len(self.store.recent()), 1)

    def test_rejected_is_skipped_then_reset(self):
        link, title = "https://a.com/refuse", "Article refusé"
        self.store.mark_rejected(link, title)
        # ne sera pas re-filtre a la prochaine execution
        self.assertTrue(self.store.is_seen(link, title))
        counts = self.store.counts()
        self.assertEqual(counts["rejected"], 1)
        self.assertEqual(counts["seen"], 0)
        # apres un changement de prompt, on peut tout reevaluer
        self.assertEqual(self.store.clear_rejected(), 2)
        self.assertFalse(self.store.is_seen(link, title))

    def test_sent_is_not_cleared_by_reset(self):
        self.store.mark_seen("https://a.com/envoye", "Envoyé")
        self.store.mark_rejected("https://a.com/refuse", "Refusé")
        self.store.clear_rejected()
        self.assertTrue(self.store.is_seen("https://a.com/envoye", "Envoyé"))
        self.assertFalse(self.store.is_seen("https://a.com/refuse", "Refusé"))

    def test_mark_seen_is_idempotent(self):
        for _ in range(3):
            self.store.mark_seen("https://a.com/x", "T")
        self.assertEqual(self.store.counts()["seen"], 1)


class TestAlternanceDesVoix(unittest.TestCase):
    """Un article sur deux est lu par la voix alternative."""

    def make(self, voices, voice_alt="fr_FR-tom-medium"):
        pipe = Pipeline.__new__(Pipeline)  # sans __init__: ni reseau ni modele charge
        pipe.cfg = Config()
        pipe.cfg.tts.voice_alt = voice_alt
        pipe.tts = SimpleNamespace(voices=voices) if voices else None
        pipe._spoken_articles = 0
        return pipe

    def test_alterne_un_article_sur_deux(self):
        pipe = self.make(("fr_FR-siwis-medium", "fr_FR-tom-medium"))
        self.assertEqual([pipe.next_voice() for _ in range(4)],
                         ["fr_FR-siwis-medium", "fr_FR-tom-medium",
                          "fr_FR-siwis-medium", "fr_FR-tom-medium"])

    def test_voix_unique_si_alternative_absente(self):
        pipe = self.make(("fr_FR-siwis-medium",))
        self.assertEqual([pipe.next_voice() for _ in range(3)], [None, None, None])

    def test_voix_unique_si_desactivee(self):
        pipe = self.make(("fr_FR-siwis-medium", "fr_FR-tom-medium"), voice_alt="")
        self.assertIsNone(pipe.next_voice())

    def test_sans_synthese_vocale(self):
        self.assertIsNone(self.make(None).next_voice())


class FakeSession:
    """Capture les appels a l'API Telegram sans reseau."""

    def __init__(self):
        self.calls = []

    def post(self, url, data=None, files=None, timeout=None):
        self.calls.append((url, data, files))
        return SimpleNamespace(status_code=200,
                               json=lambda: {"ok": True, "result": {"message_id": 7}})


class TestMetadonneesAudio(unittest.TestCase):
    """Le lecteur du telephone affiche « Artiste inconnu » sans champ performer."""

    def test_performer_numero_et_date(self):
        self.assertEqual(audio_performer(3, "mercredi 9 septembre 2026"),
                         "Article 3 · mercredi 9 septembre 2026")

    def test_presentation_annonce_le_nombre(self):
        self.assertEqual(
            intro_speech("mercredi 9 septembre 2026", "05:00", 12),
            "Bonjour. Veille IA du mercredi 9 septembre 2026, 05:00. "
            "12 articles au programme aujourd'hui.")
        self.assertIn("Un seul article", intro_speech("d", "h", 1))
        self.assertIn("Aucun nouvel article", intro_speech("d", "h", 0))

    def test_send_audio_transmet_performer(self):
        session = FakeSession()
        Telegram("1:x", "42", session).send_audio(
            b"mp3", caption="c", title="t", performer="Article 3 · mercredi 9 septembre 2026")
        url, data, files = session.calls[0]
        self.assertTrue(url.endswith("/sendAudio"))
        self.assertEqual(data["performer"], "Article 3 · mercredi 9 septembre 2026")
        self.assertEqual(data["title"], "t")
        self.assertIn("audio", files)

    def test_send_audio_sans_performer(self):
        session = FakeSession()
        Telegram("1:x", "42", session).send_audio(b"mp3", title="t")
        self.assertNotIn("performer", session.calls[0][1])


class TestOrdreDesAudios(unittest.TestCase):
    """La presentation vocale part apres le dernier article, pas avec l'intro."""

    def make(self):
        pipe = Pipeline.__new__(Pipeline)  # sans __init__: ni reseau ni modele charge
        pipe.cfg = Config()
        pipe.run_date, pipe.run_time = "mercredi 9 septembre 2026", "05:00"
        pipe._on_event = lambda level, text: None
        pipe.tg = SimpleNamespace(messages=[], audios=[])
        pipe.tg.send_message = lambda text, **kw: pipe.tg.messages.append(text)
        pipe.tg.send_audio = lambda mp3, **kw: pipe.tg.audios.append(kw)
        pipe.tts = SimpleNamespace(voices=("fr_FR-siwis-medium",),
                                   synthesize_mp3=lambda text, voice=None: b"mp3")
        return pipe

    def test_intro_texte_seul(self):
        pipe = self.make()
        pipe.send_intro()
        self.assertEqual(len(pipe.tg.messages), 1)
        self.assertIn("mercredi 9 septembre 2026", pipe.tg.messages[0])
        self.assertEqual(pipe.tg.audios, [])

    def test_presentation_titre_et_compte(self):
        pipe = self.make()
        pipe.send_presentation(Summary(sent=12))
        self.assertEqual(len(pipe.tg.audios), 1)
        audio = pipe.tg.audios[0]
        self.assertEqual(audio["title"], "Veille IA du mercredi 9 septembre 2026")
        self.assertEqual(audio["performer"], "Présentation · 12 articles")
        self.assertIn("12 articles", audio["caption"])

    def test_presentation_un_seul_article(self):
        pipe = self.make()
        pipe.send_presentation(Summary(sent=1))
        self.assertEqual(pipe.tg.audios[0]["performer"], "Présentation · 1 article")

    def test_presentation_sans_audio_configure(self):
        pipe = self.make()
        pipe.cfg.telegram.send_audio = False
        pipe.send_presentation(Summary(sent=3))
        self.assertEqual(pipe.tg.audios, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
