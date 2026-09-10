"""Persistance SQLite: deduplication et historique des envois.

Remplace le node "Remove Duplicates" de n8n, avec une difference voulue:
un article n'est marque comme vu qu'apres un envoi Telegram reussi.
Un plantage en cours de route ne fait donc perdre aucun article.
"""

from __future__ import annotations

import re
import sqlite3
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name", "fbclid", "gclid", "mc_cid", "mc_eid", "xtor",
    "ref", "at_medium", "at_campaign", "sh", "s",
}

SCHEMA = """
create table if not exists seen (
    key        text primary key,
    kind       text not null,
    link       text,
    title      text,
    seen_at    real not null
);
create table if not exists sent (
    id         integer primary key autoincrement,
    link       text,
    title      text,
    summary    text,
    source     text,
    audio      integer not null default 0,
    sent_at    real not null
);
create table if not exists runs (
    id           integer primary key autoincrement,
    started_at   real not null,
    finished_at  real,
    feeds_ok     integer default 0,
    feeds_ko     integer default 0,
    candidates   integer default 0,
    fresh        integer default 0,
    kept         integer default 0,
    sent         integer default 0,
    errors       integer default 0,
    note         text
);
create index if not exists idx_sent_at on sent(sent_at);
create index if not exists idx_seen_at on seen(seen_at);
"""


def normalize_link(link: str) -> str:
    """URL canonique: minuscule, sans www, sans parametres de tracking, sans fragment."""
    raw = (link or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    host = (parts.hostname or "").lower().removeprefix("www.")
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"
    query = urlencode([
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS
    ])
    path = re.sub(r"/+$", "", parts.path) or "/"
    return urlunsplit(("", host, path, query, "")).lstrip("/") or host


def normalize_title(title: str) -> str:
    """Titre canonique: sans accents, sans ponctuation, sans espaces."""
    txt = unicodedata.normalize("NFKD", str(title or ""))
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", txt.lower())[:160]


@dataclass
class RunStats:
    run_id: int
    started_at: float


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, timeout=30, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("pragma journal_mode=wal")
        self.db.execute("pragma synchronous=normal")
        self.db.executescript(SCHEMA)
        self._migrate()
        self.db.commit()

    def _migrate(self) -> None:
        """Ajoute la colonne status aux bases creees avant son introduction."""
        cols = {row[1] for row in self.db.execute("pragma table_info(seen)")}
        if "status" not in cols:
            self.db.execute("alter table seen add column status text "
                            "not null default 'sent'")

    # -- deduplication ---------------------------------------------------
    def keys_for(self, link: str, title: str) -> list[tuple[str, str]]:
        keys = []
        nl = normalize_link(link)
        if nl:
            keys.append(("link", "L:" + nl))
        nt = normalize_title(title)
        if nt:
            keys.append(("title", "T:" + nt))
        return keys

    def is_seen(self, link: str, title: str) -> bool:
        keys = [k for _, k in self.keys_for(link, title)]
        if not keys:
            return False
        marks = ",".join("?" * len(keys))
        row = self.db.execute(
            f"select 1 from seen where key in ({marks}) limit 1", keys
        ).fetchone()
        return row is not None

    def mark_seen(self, link: str, title: str, status: str = "sent") -> None:
        """status='sent' apres un envoi reussi, 'rejected' apres un refus du filtre."""
        now = time.time()
        self.db.executemany(
            "insert or ignore into seen(key, kind, link, title, seen_at, status)"
            " values(?,?,?,?,?,?)",
            [(key, kind, link, title, now, status)
             for kind, key in self.keys_for(link, title)],
        )
        self.db.commit()

    def mark_rejected(self, link: str, title: str) -> None:
        self.mark_seen(link, title, status="rejected")

    def clear_rejected(self) -> int:
        """A appeler apres avoir change le prompt ou le modele de filtrage."""
        cur = self.db.execute("delete from seen where status='rejected'")
        self.db.commit()
        return cur.rowcount

    # -- historique ------------------------------------------------------
    def record_sent(self, *, link: str, title: str, summary: str, source: str,
                    audio: bool) -> None:
        self.db.execute(
            "insert into sent(link, title, summary, source, audio, sent_at)"
            " values(?,?,?,?,?,?)",
            (link, title, summary, source, int(audio), time.time()),
        )
        self.db.commit()

    def start_run(self) -> RunStats:
        cur = self.db.execute("insert into runs(started_at) values(?)", (time.time(),))
        self.db.commit()
        return RunStats(run_id=int(cur.lastrowid), started_at=time.time())

    def finish_run(self, run: RunStats, **counts) -> None:
        allowed = ("feeds_ok", "feeds_ko", "candidates", "fresh", "kept", "sent",
                   "errors", "note")
        fields = {k: v for k, v in counts.items() if k in allowed}
        sets = ", ".join(f"{k}=?" for k in fields)
        args = list(fields.values()) + [time.time(), run.run_id]
        self.db.execute(
            f"update runs set {sets}{',' if sets else ''} finished_at=? where id=?", args
        )
        self.db.commit()

    def counts(self) -> dict:
        seen = self.db.execute(
            "select count(*) from seen where kind='link' and status='sent'"
        ).fetchone()[0]
        rejected = self.db.execute(
            "select count(*) from seen where kind='link' and status='rejected'"
        ).fetchone()[0]
        sent = self.db.execute("select count(*) from sent").fetchone()[0]
        last = self.db.execute(
            "select max(finished_at) from runs where finished_at is not null"
        ).fetchone()[0]
        return {"seen": seen, "rejected": rejected, "sent": sent,
                "last_run": last}

    def recent(self, limit: int = 20) -> list[sqlite3.Row]:
        return list(self.db.execute(
            "select title, link, source, sent_at, audio from sent"
            " order by sent_at desc limit ?", (limit,)))

    def prune(self, older_than_days: int = 365) -> int:
        cutoff = time.time() - older_than_days * 86400
        cur = self.db.execute("delete from seen where seen_at < ?", (cutoff,))
        self.db.commit()
        return cur.rowcount

    def close(self) -> None:
        self.db.close()
