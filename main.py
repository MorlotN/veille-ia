#!/usr/bin/env python3
"""Veille IA: point d'entree.

Sans argument: interface graphique (c'est ce que fait le double-clic).
  --cli        execution en console, sans fenetre
  --dry-run    simulation: aucun envoi Telegram, aucune ecriture d'etat
  --test [N]   envoi reel de N article(s) (1 par defaut), sur une base jetable:
               les articles testes ne sont pas consommes
  --check      diagnostics uniquement (Ollama, Telegram, voix, base)
  --reset-rejected
               oublie les articles ecartes par le filtre, pour les reevaluer
               (a lancer apres avoir change le prompt ou le modele de filtrage)
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from veille_ia import config as cfg_mod  # noqa: E402
from veille_ia import journal  # noqa: E402
from veille_ia.config import ConfigError  # noqa: E402

ANSI = {"info": "", "ok": "\033[32m", "warn": "\033[33m", "err": "\033[31m",
        "step": "\033[36m", "reset": "\033[0m"}


def log_line(level: str, text: str, *, colored: bool) -> None:
    stamp = time.strftime("%H:%M:%S")
    line = f"{stamp}  {text}"
    if colored and sys.stdout.isatty():
        print(f"{ANSI.get(level, '')}{line}{ANSI['reset']}", flush=True)
    else:
        print(line, flush=True)
    journal.append(level, text)


def run_checks() -> int:
    from veille_ia.llm import Ollama
    from veille_ia.store import Store
    from veille_ia.telegram import Telegram
    from veille_ia import tts as tts_mod

    cfg_mod.ensure_config()
    try:
        cfg = cfg_mod.load()
    except ConfigError as exc:
        log_line("err", f"configuration: {exc}", colored=True)
        return 2

    rows: list[tuple[str, bool, str]] = []
    rows.append(("Ollama",) + Ollama(cfg).check())
    if cfg.telegram_ready:
        rows.append(("Telegram",) + Telegram(cfg.telegram.bot_token,
                                             cfg.telegram.chat_id).check())
    else:
        rows.append(("Telegram", False,
                     f"bot_token / chat_id a renseigner dans {cfg.path}"))
    backend, desc = tts_mod.build(cfg)
    rows.append(("Voix", backend is not None, desc))
    try:
        store = Store(cfg_mod.DB_PATH)
        counts = store.counts()
        store.close()
        rows.append(("Base", True, f"{counts['seen']} liens connus, "
                                   f"{counts['sent']} envois"))
    except Exception as exc:
        rows.append(("Base", False, str(exc)))
    rows.append(("Flux", True, f"{len(cfg.feeds)} configures"))

    worst = 0
    for name, ok, msg in rows:
        log_line("ok" if ok else "err",
                 f"{'✓' if ok else '✗'} {name:<9} {msg}", colored=True)
        worst = worst or (0 if ok else 1)
    return worst


def run_cli(dry_run: bool, test_limit: int | None = None) -> int:
    from veille_ia.pipeline import Pipeline
    from veille_ia.store import Store

    cfg_mod.ensure_config()
    try:
        cfg = cfg_mod.load()
    except ConfigError as exc:
        log_line("err", f"configuration: {exc}", colored=True)
        return 2
    if not dry_run and not cfg.telegram_ready:
        log_line("err", f"bot_token Telegram absent, renseigne-le dans {cfg.path}",
                 colored=True)
        return 2

    if test_limit:
        cfg = cfg_mod.test_variant(cfg, test_limit)
        db_path = cfg_mod.fresh_test_db()
        log_line("step", f"mode test: {test_limit} article(s) au maximum, envois "
                         f"Telegram reels, base jetable {db_path.name}", colored=True)
    else:
        db_path = cfg_mod.DB_PATH

    store = Store(db_path)
    try:
        pipeline = Pipeline(cfg, store,
                            on_event=lambda lvl, txt: log_line(lvl, txt, colored=True),
                            dry_run=dry_run, force_send=bool(test_limit))
        summary = pipeline.run()
    finally:
        store.close()
    return 0 if summary.errors == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="veille-ia", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cli", action="store_true", help="execution en console")
    parser.add_argument("--dry-run", action="store_true",
                        help="simulation, aucun envoi ni ecriture d'etat")
    parser.add_argument("--test", nargs="?", type=int, const=1, default=None,
                        metavar="N",
                        help="envoi reel de N article(s) sur une base jetable")
    parser.add_argument("--check", action="store_true", help="diagnostics uniquement")
    parser.add_argument("--reset-rejected", action="store_true",
                        help="oublie les articles ecartes, pour les reevaluer")
    args = parser.parse_args()

    if args.reset_rejected:
        from veille_ia.store import Store
        store = Store(cfg_mod.DB_PATH)
        removed = store.clear_rejected()
        store.close()
        log_line("ok", f"{removed} marque(s) de refus effacee(s), ces articles "
                       f"seront reevalues a la prochaine execution", colored=True)
        return 0
    if args.check:
        return run_checks()
    headless = not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    if args.cli or args.dry_run or args.test or headless:
        if headless and not (args.cli or args.dry_run or args.test):
            log_line("warn", "aucun affichage detecte, bascule en mode console",
                     colored=True)
        return run_cli(dry_run=args.dry_run, test_limit=args.test)

    from veille_ia.gui import main as gui_main
    return gui_main()


if __name__ == "__main__":
    sys.exit(main())
