"""Journal sur disque, partage par l'interface graphique et la console.

Sans cela, une execution lancee depuis la fenetre ne laissait aucune trace:
en fermant la fenetre on perdait tout l'historique de l'execution.
"""

from __future__ import annotations

import threading
import time

from .config import LOG_PATH

MAX_BYTES = 5_000_000
_lock = threading.Lock()


def append(level: str, text: str) -> None:
    """Ajoute une ligne au journal. Ne leve jamais: journaliser n'est pas critique."""
    try:
        with _lock:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            if LOG_PATH.exists() and LOG_PATH.stat().st_size > MAX_BYTES:
                LOG_PATH.replace(LOG_PATH.with_suffix(".log.1"))
            with LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{level}] {text}\n")
    except OSError:
        pass
