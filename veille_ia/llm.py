"""Appels a Ollama: filtrage puis resume.

Equivalent des deux "AI Agent" n8n branches sur lmChatOllama.
"""

from __future__ import annotations

import re

import requests

from .config import Config

THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)
FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\s*|\s*```$", re.S)
YES_RE = re.compile(r"\$\$\s*oui\b|^\s*oui\s*[.!]?\s*$", re.I | re.M)
NO_RE = re.compile(r"\$\$\s*non\b|^\s*non\s*[.!]?\s*$", re.I | re.M)


class OllamaError(RuntimeError):
    pass


def strip_reasoning(text: str) -> str:
    return THINK_RE.sub("", text or "").strip()


def clean_summary(raw: str) -> str:
    """Applique la meme logique que les nodes 'Code' + 'nettoyage minimal'."""
    text = strip_reasoning(raw)
    marker = re.search(r"^\s*\$\$\s*", text, re.M)
    if marker:
        text = text[marker.end():]
    else:
        label = re.search(r"r[ée]sum[ée]\s*:\s*", text, re.I)
        if label:
            text = text[label.end():]
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        text = FENCE_RE.sub("", text)
    # Un $$ ou un fence residuel peut subsister si le LLM a inverse l'ordre.
    text = text.replace("```", "").lstrip("$").strip()
    return re.sub(r"\n{3,}", "\n\n", text).strip()


class Ollama:
    def __init__(self, cfg: Config, session: requests.Session | None = None):
        self.cfg = cfg.ollama
        self.prompts = cfg.prompts
        self.max_chars = cfg.fetch.max_content_chars
        self.session = session or requests.Session()

    # -- diagnostics -----------------------------------------------------
    def models(self) -> set[str]:
        resp = self.session.get(f"{self.cfg.base_url}/api/tags", timeout=10)
        resp.raise_for_status()
        return {m["name"] for m in resp.json().get("models", [])}

    def check(self) -> tuple[bool, str]:
        try:
            available = self.models()
        except Exception as exc:
            return False, f"Ollama injoignable sur {self.cfg.base_url} ({exc})"
        missing = [m for m in (self.cfg.filter_model, self.cfg.summary_model)
                   if m not in available]
        if missing:
            return False, "modele(s) absent(s): " + ", ".join(missing)
        return True, f"{self.cfg.filter_model} + {self.cfg.summary_model}"

    # -- generation ------------------------------------------------------
    def _chat(self, model: str, prompt: str, *, num_predict: int = -1) -> str:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "keep_alive": self.cfg.keep_alive,
            "options": {
                "temperature": 0,
                "num_ctx": self.cfg.num_ctx,
                "num_predict": num_predict,
            },
        }
        try:
            resp = self.session.post(f"{self.cfg.base_url}/api/chat", json=payload,
                                     timeout=self.cfg.timeout_s)
        except requests.RequestException as exc:
            raise OllamaError(f"appel a Ollama impossible: {exc}") from exc
        if resp.status_code != 200:
            raise OllamaError(f"Ollama HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        return (data.get("message") or {}).get("content", "")

    def is_relevant(self, title: str, content: str) -> tuple[bool, str]:
        prompt = self.prompts.filter_prompt.format(
            title=title, content=content[: self.max_chars])
        answer = strip_reasoning(self._chat(self.cfg.filter_model, prompt,
                                           num_predict=16))
        verdict = answer.strip().lower()
        if YES_RE.search(verdict):
            return True, answer.strip()
        if NO_RE.search(verdict):
            return False, answer.strip()
        # Reponse hors format: on ne garde pas, comme le node Code n8n.
        return False, answer.strip() or "(reponse vide)"

    def summarize(self, title: str, content: str) -> str:
        prompt = self.prompts.summary_prompt.format(
            title=title, content=content[: self.max_chars])
        return clean_summary(self._chat(
            self.cfg.summary_model, prompt,
            num_predict=self.cfg.summary_num_predict or -1))
