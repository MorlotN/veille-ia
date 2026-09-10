"""Lecture des flux RSS/Atom, en parallele."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import feedparser
import requests

from .config import Config, Feed


@dataclass
class Candidate:
    source: str
    title: str
    link: str
    published: float | None = None
    content: str = ""

    @property
    def short_title(self) -> str:
        return self.title if len(self.title) <= 70 else self.title[:69] + "…"


def _entry_time(entry) -> float | None:
    for key in ("published_parsed", "updated_parsed"):
        value = getattr(entry, key, None) or entry.get(key)
        if value:
            try:
                return time.mktime(value)
            except (TypeError, ValueError, OverflowError):
                continue
    return None


def read_feed(feed: Feed, cfg: Config, session: requests.Session) -> list[Candidate]:
    """Recupere un flux et renvoie ses entrees, les plus recentes d'abord."""
    resp = session.get(
        feed.url,
        timeout=cfg.fetch.http_timeout_s,
        headers={"User-Agent": cfg.fetch.user_agent, "Accept": "application/rss+xml, application/xml, text/xml, */*"},
    )
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    items: list[Candidate] = []
    for entry in parsed.entries:
        link = (entry.get("link") or "").strip()
        title = (entry.get("title") or "").strip()
        if not link or not title:
            continue
        items.append(Candidate(source=feed.name, title=title, link=link,
                               published=_entry_time(entry)))
    items.sort(key=lambda c: c.published or 0, reverse=True)
    if cfg.fetch.max_per_feed > 0:
        items = items[: cfg.fetch.max_per_feed]
    return items


def read_all(cfg: Config, session: requests.Session, on_feed=None,
             stop=None) -> tuple[list[Candidate], list[tuple[str, str]]]:
    """Lit tous les flux. Renvoie (candidats, erreurs)."""
    out: list[Candidate] = []
    errors: list[tuple[str, str]] = []

    def work(feed: Feed):
        if stop is not None and stop.is_set():
            return feed, [], None
        try:
            return feed, read_feed(feed, cfg, session), None
        except Exception as exc:  # reseau, XML casse, 403...
            return feed, [], exc

    workers = max(1, min(cfg.fetch.workers, len(cfg.feeds)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for feed, items, exc in pool.map(work, cfg.feeds):
            if exc is not None:
                errors.append((feed.name, f"{type(exc).__name__}: {exc}"))
            else:
                out.extend(items)
            if on_feed:
                on_feed(feed, len(items), exc)
    return out, errors
