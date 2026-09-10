"""Recuperation et nettoyage du contenu d'un article.

Equivalent des nodes n8n "Fetch Article Content" + "Extract HTML",
avec en plus un plafond de taille pour ne pas noyer le LLM local.
"""

from __future__ import annotations

import re

import requests
from bs4 import BeautifulSoup

DROP_TAGS = (
    "script", "style", "noscript", "template", "iframe", "svg", "img", "picture",
    "figure", "nav", "header", "footer", "aside", "form", "button", "select",
)
MAIN_CANDIDATES = ("article", "main", '[role="main"]', ".post-content",
                   ".entry-content", ".article-content", "#content")


def html_to_text(html: str, max_chars: int = 0) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(list(DROP_TAGS)):
        tag.decompose()

    node = None
    for selector in MAIN_CANDIDATES:
        found = soup.select_one(selector)
        if found and len(found.get_text(" ", strip=True)) > 400:
            node = found
            break
    node = node or soup.body or soup

    text = node.get_text("\n", strip=True)
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if max_chars and len(text) > max_chars:
        cut = text.rfind(" ", 0, max_chars)
        text = text[: cut if cut > max_chars * 0.6 else max_chars].rstrip() + "…"
    return text.strip()


def fetch_text(url: str, session: requests.Session, *, timeout: int,
               user_agent: str, max_chars: int = 0) -> str:
    resp = session.get(
        url,
        timeout=timeout,
        headers={"User-Agent": user_agent,
                 "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                 "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"},
    )
    resp.raise_for_status()
    ctype = resp.headers.get("Content-Type", "")
    if "html" not in ctype and "xml" not in ctype and ctype:
        raise ValueError(f"contenu non HTML ({ctype})")
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding or "utf-8"
    return html_to_text(resp.text, max_chars=max_chars)
