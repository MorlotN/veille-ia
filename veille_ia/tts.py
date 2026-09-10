"""Synthese vocale Piper, en local (dans le processus) ou via un serveur HTTP.

Deux backends interchangeables:
  - local: le paquet pip piper-tts, aucune dependance reseau, aucun service a lancer
  - http : un serveur piper deja en place (ex. /synthesize.mp3 sur le port 5000)
Le backend "auto" prend le local s'il est disponible, sinon le HTTP.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import wave
from pathlib import Path

import requests

from .config import Config

CANDIDATE_MODEL_DIRS = (
    Path.home() / "piper-models",
    Path.home() / "code/jarvis/voiced/models",
    Path.home() / ".local/share/piper-models",
)


class TtsError(RuntimeError):
    pass


def wav_to_mp3(wav_bytes: bytes) -> bytes:
    """Telegram sendAudio refuse le WAV: on convertit en MP3 via ffmpeg."""
    if not shutil.which("ffmpeg"):
        raise TtsError("ffmpeg absent, impossible de produire un MP3")
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
         "-f", "mp3", "-codec:a", "libmp3lame", "-b:a", "64k", "-ar", "22050",
         "pipe:1"],
        input=wav_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise TtsError("ffmpeg a echoue: "
                       + proc.stderr.decode("utf-8", "replace")[:300])
    return proc.stdout


def find_voice_dir(cfg: Config, voice: str) -> Path | None:
    """Repertoire contenant <voice>.onnx, ou None si la voix est introuvable."""
    if not voice:
        return None
    if cfg.tts.models_dir:
        path = Path(cfg.tts.models_dir).expanduser()
        return path if (path / f"{voice}.onnx").exists() else None
    for path in CANDIDATE_MODEL_DIRS:
        if (path / f"{voice}.onnx").exists():
            return path
    return None


def find_models_dir(cfg: Config) -> Path | None:
    return find_voice_dir(cfg, cfg.tts.voice)


class LocalPiper:
    """Voix chargees a la demande puis reutilisees pour toute la session.

    Plusieurs voix peuvent cohabiter (voix principale et voix alternee): chacune
    est un modele onnx d'environ 60 Mo, on ne la charge qu'au premier usage.
    """

    kind = "local"

    def __init__(self, cfg: Config, models_dir: Path, alt_dir: Path | None = None):
        from piper import PiperVoice, SynthesisConfig  # import tardif: dependance lourde

        self.cfg = cfg.tts
        self.models_dir = models_dir
        self._load = PiperVoice.load
        self._dirs: dict[str, Path] = {cfg.tts.voice: models_dir}
        if alt_dir is not None and cfg.tts.voice_alt:
            self._dirs[cfg.tts.voice_alt] = alt_dir
        self._loaded = {cfg.tts.voice: PiperVoice.load(models_dir / f"{cfg.tts.voice}.onnx")}
        self._syn = SynthesisConfig(length_scale=cfg.tts.length_scale)

    @property
    def voices(self) -> tuple[str, ...]:
        return tuple(self._dirs)

    def describe(self) -> str:
        return f"local (piper, {', '.join(self.voices)}, {self.models_dir})"

    def _voice_for(self, voice: str | None):
        name = voice if voice in self._dirs else self.cfg.voice
        if name not in self._loaded:
            self._loaded[name] = self._load(self._dirs[name] / f"{name}.onnx")
        return self._loaded[name]

    def synthesize_mp3(self, text: str, voice: str | None = None) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as out:
            configured = False
            for chunk in self._voice_for(voice).synthesize(text, self._syn):
                if not configured:
                    out.setframerate(chunk.sample_rate)
                    out.setsampwidth(chunk.sample_width)
                    out.setnchannels(chunk.sample_channels)
                    configured = True
                out.writeframes(chunk.audio_int16_bytes)
        if not configured:
            raise TtsError("Piper n'a produit aucun echantillon audio")
        return wav_to_mp3(buf.getvalue())


class HttpPiper:
    """Client pour un serveur piper_server.py deja en service."""

    kind = "http"

    def __init__(self, cfg: Config, session: requests.Session | None = None):
        self.cfg = cfg.tts
        self.session = session or requests.Session()
        self.base = cfg.tts.http_url.rstrip("/")

    @property
    def voices(self) -> tuple[str, ...]:
        """Le serveur choisit la voix par son nom: on lui fait confiance sur les deux."""
        if self.cfg.voice_alt and self.cfg.voice_alt != self.cfg.voice:
            return (self.cfg.voice, self.cfg.voice_alt)
        return (self.cfg.voice,)

    def describe(self) -> str:
        return f"http ({self.base}, {', '.join(self.voices)})"

    def available(self) -> bool:
        try:
            return self.session.get(f"{self.base}/health", timeout=3).ok
        except requests.RequestException:
            return False

    def synthesize_mp3(self, text: str, voice: str | None = None) -> bytes:
        body = {"text": text, "voice": voice or self.cfg.voice}
        if self.cfg.length_scale:
            body["length_scale"] = self.cfg.length_scale
        resp = self.session.post(f"{self.base}/synthesize.mp3", json=body, timeout=180)
        if resp.status_code != 200:
            raise TtsError(f"serveur piper HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.content


def build(cfg: Config, session: requests.Session | None = None):
    """Renvoie (backend, message). backend vaut None si la voix est indisponible."""
    if not cfg.tts.enabled or cfg.tts.backend == "off":
        return None, "desactive dans la configuration"

    want = cfg.tts.backend
    reasons: list[str] = []

    if want in ("auto", "local"):
        models_dir = find_models_dir(cfg)
        if models_dir is None:
            reasons.append(f"voix {cfg.tts.voice}.onnx introuvable dans "
                           + ", ".join(str(p) for p in CANDIDATE_MODEL_DIRS))
        else:
            alt_dir = find_voice_dir(cfg, cfg.tts.voice_alt)
            if cfg.tts.voice_alt and alt_dir is None:
                reasons.append(f"voix alternee {cfg.tts.voice_alt}.onnx introuvable, "
                               "une seule voix sera utilisee")
            try:
                backend = LocalPiper(cfg, models_dir, alt_dir)
                return backend, backend.describe()
            except ImportError:
                reasons.append("paquet piper-tts non installe dans le venv")
            except Exception as exc:
                reasons.append(f"chargement local impossible ({exc})")
        if want == "local":
            return None, "; ".join(reasons)

    if want in ("auto", "http"):
        backend = HttpPiper(cfg, session)
        if backend.available():
            return backend, backend.describe()
        reasons.append(f"aucun serveur piper sur {backend.base}")

    return None, "; ".join(reasons) or "aucun backend disponible"
