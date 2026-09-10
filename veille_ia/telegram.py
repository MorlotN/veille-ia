"""Client Telegram minimal: message HTML et envoi audio, avec gestion du 429."""

from __future__ import annotations

import time

import requests

TEXT_LIMIT = 4096
CAPTION_LIMIT = 1024


class TelegramError(RuntimeError):
    pass


def clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit - 1)
    return text[: cut if cut > limit * 0.6 else limit - 1].rstrip() + "…"


class Telegram:
    def __init__(self, token: str, chat_id: str,
                 session: requests.Session | None = None, *, dry_run: bool = False):
        self.token = token
        self.chat_id = str(chat_id)
        self.session = session or requests.Session()
        self.dry_run = dry_run

    @property
    def _base(self) -> str:
        return f"https://api.telegram.org/bot{self.token}"

    def _call(self, method: str, *, data=None, files=None, tries: int = 4):
        if self.dry_run:
            return {"ok": True, "result": {"message_id": 0, "dry_run": True}}
        last = ""
        for attempt in range(1, tries + 1):
            try:
                resp = self.session.post(f"{self._base}/{method}", data=data,
                                         files=files, timeout=120)
            except requests.RequestException as exc:
                last = f"reseau: {exc}"
            else:
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 429:
                    wait = 5
                    try:
                        wait = int(resp.json()["parameters"]["retry_after"]) + 1
                    except Exception:
                        pass
                    last = f"429, attente {wait}s"
                    time.sleep(min(wait, 60))
                    continue
                last = f"HTTP {resp.status_code}: {resp.text[:200]}"
                if 400 <= resp.status_code < 500:
                    break  # requete invalide: reessayer ne sert a rien
            if attempt < tries:
                time.sleep(2 * attempt)
        raise TelegramError(f"{method} a echoue ({last})")

    # -- API -------------------------------------------------------------
    def get_me(self) -> dict:
        return self._call("getMe", tries=1).get("result", {})

    def get_chat(self) -> dict:
        return self._call("getChat", data={"chat_id": self.chat_id},
                          tries=1).get("result", {})

    def check(self) -> tuple[bool, str]:
        """Verification en lecture seule: aucun message n'est envoye."""
        try:
            me = self.get_me()
        except Exception as exc:
            return False, f"token refuse ({exc})"
        name = me.get("username") or me.get("first_name") or "?"
        try:
            chat = self.get_chat()
        except Exception as exc:
            return False, (f"@{name} valide mais chat {self.chat_id} injoignable "
                           f"({exc}). Envoie /start au bot depuis ce compte.")
        who = (chat.get("username") or chat.get("title")
               or " ".join(filter(None, (chat.get("first_name"),
                                         chat.get("last_name")))) or self.chat_id)
        return True, f"@{name} -> {who} ({chat.get('type', '?')})"

    def send_message(self, text: str, *, parse_mode: str = "HTML",
                     preview: bool = False) -> int:
        data = {
            "chat_id": self.chat_id,
            "text": clip(text, TEXT_LIMIT),
            "parse_mode": parse_mode,
            "link_preview_options": '{"is_disabled": %s}' % ("false" if preview else "true"),
        }
        result = self._call("sendMessage", data=data)
        return int((result.get("result") or {}).get("message_id", 0))

    def send_audio(self, mp3: bytes, *, caption: str = "", title: str = "",
                   performer: str = "", filename: str = "veille.mp3") -> int:
        data = {"chat_id": self.chat_id}
        if caption:
            data["caption"] = clip(caption, CAPTION_LIMIT)
        if title:
            data["title"] = clip(title, 64)
        if performer:
            # sans ce champ, le lecteur du telephone affiche « Artiste inconnu »
            data["performer"] = clip(performer, 64)
        files = {"audio": (filename, mp3, "audio/mpeg")}
        result = self._call("sendAudio", data=data, files=files)
        return int((result.get("result") or {}).get("message_id", 0))
