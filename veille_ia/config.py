"""Chargement et validation de la configuration."""

from __future__ import annotations

import os
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "veille-ia"
PKG_DIR = Path(__file__).resolve().parent
APP_DIR = PKG_DIR.parent

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / APP_NAME
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / APP_NAME
CONFIG_PATH = CONFIG_DIR / "config.toml"
EXAMPLE_PATH = APP_DIR / "config.example.toml"
DB_PATH = STATE_DIR / "veille.sqlite"
LOG_PATH = STATE_DIR / "veille.log"

PLACEHOLDER = "METTRE_ICI_LE_TOKEN_DU_BOT"


class ConfigError(RuntimeError):
    """Configuration absente ou invalide."""


@dataclass(frozen=True)
class Feed:
    name: str
    url: str


@dataclass
class TelegramCfg:
    bot_token: str = ""
    chat_id: str = ""
    send_audio: bool = True
    send_recap: bool = True
    show_commands: bool = False


@dataclass
class OllamaCfg:
    base_url: str = "http://127.0.0.1:11434"
    filter_model: str = "llama3.1:8b"
    summary_model: str = "mistral-small3.2:latest"
    timeout_s: int = 300
    keep_alive: str = "10m"
    num_ctx: int = 8192
    summary_num_predict: int = 500  # borne la longueur du resume, 0 = illimite


@dataclass
class TtsCfg:
    enabled: bool = True
    backend: str = "auto"  # auto | local | http | off
    voice: str = "fr_FR-siwis-medium"
    voice_alt: str = "fr_FR-tom-medium"  # un article sur deux, vide = pas d'alternance
    models_dir: str = ""
    http_url: str = "http://127.0.0.1:5000"
    length_scale: float | None = None
    max_chars: int = 2000


@dataclass
class FetchCfg:
    max_per_feed: int = 0   # 0 = tout le flux
    max_total: int = 0      # 0 = aucun plafond
    skip_rejected: bool = True  # ne pas re-filtrer un article deja ecarte
    http_timeout_s: int = 20
    max_content_chars: int = 8000
    workers: int = 8
    user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 VeilleIA/1.0"
    )


@dataclass
class PacingCfg:
    between_messages_s: float = 5.0
    between_audio_s: float = 4.0


FILTER_PROMPT = """Vous etes un agent de veille IA pour un ingenieur en intelligence artificielle base a Lyon.

Analysez l'article suivant :
Titre: {title}
Contenu: {content}

Verifiez si l'un des criteres suivants s'applique :
1. Nouvelle technologie ou innovation IA
2. Aide/subvention de l'Etat francais, region Auvergne-Rhone-Alpes ou UE
3. Opportunite clients ou cas d'usage industriel

Repondez EXACTEMENT par l'un des deux tokens suivants, et rien d'autre :
- $$oui
- $$non
Ne justifiez pas, n'ajoutez aucun mot, pas de ponctuation."""

SUMMARY_PROMPT = """Ecrivez un resume concis et structure de cet article en francais :

Titre: {title}
Contenu: {content}

Le resume doit :
- Faire maximum 5 phrases
- Mettre en evidence les points cles
- Etre factuel et professionnel
- Mettre au debut du resume: $$

RESUME:"""


@dataclass
class PromptsCfg:
    filter_prompt: str = FILTER_PROMPT
    summary_prompt: str = SUMMARY_PROMPT


@dataclass
class Config:
    telegram: TelegramCfg = field(default_factory=TelegramCfg)
    ollama: OllamaCfg = field(default_factory=OllamaCfg)
    tts: TtsCfg = field(default_factory=TtsCfg)
    fetch: FetchCfg = field(default_factory=FetchCfg)
    pacing: PacingCfg = field(default_factory=PacingCfg)
    prompts: PromptsCfg = field(default_factory=PromptsCfg)
    feeds: list[Feed] = field(default_factory=list)
    path: Path = CONFIG_PATH

    @property
    def telegram_ready(self) -> bool:
        tok = self.telegram.bot_token.strip()
        return bool(tok) and tok != PLACEHOLDER and ":" in tok and bool(self.telegram.chat_id)


def test_variant(cfg: "Config", limit: int = 1) -> "Config":
    """Copie de la configuration bornee a quelques articles, sans temporisation.

    Sert au mode test: envois Telegram reels, mais volume minimal et etat isole.
    """
    import copy

    small = copy.deepcopy(cfg)
    small.fetch.max_per_feed = 1
    small.fetch.max_total = max(1, limit)
    small.pacing.between_messages_s = 0.0
    small.pacing.between_audio_s = 1.0
    return small


TEST_DB_PATH = STATE_DIR / "test.sqlite"


def fresh_test_db():
    """Base jetable: les articles du test ne sont pas consommes."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        candidate = TEST_DB_PATH.with_name(TEST_DB_PATH.name + suffix)
        candidate.unlink(missing_ok=True)
    return TEST_DB_PATH


def _fill(cls, data: dict):
    """Instancie une dataclass en ignorant les cles inconnues du fichier TOML."""
    known = {f.name for f in cls.__dataclass_fields__.values()}
    return cls(**{k: v for k, v in (data or {}).items() if k in known})


def ensure_config() -> Path:
    """Cree le fichier de configuration utilisateur au premier lancement."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        if not EXAMPLE_PATH.exists():
            raise ConfigError(f"Modele de configuration introuvable: {EXAMPLE_PATH}")
        shutil.copyfile(EXAMPLE_PATH, CONFIG_PATH)
        CONFIG_PATH.chmod(0o600)
    return CONFIG_PATH


def load(path: Path | None = None) -> Config:
    path = path or CONFIG_PATH
    if not path.exists():
        ensure_config()
    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    feeds = [
        Feed(name=str(f.get("name") or f.get("url", "?")), url=str(f["url"]))
        for f in raw.get("feeds", [])
        if f.get("url")
    ]
    cfg = Config(
        telegram=_fill(TelegramCfg, raw.get("telegram")),
        ollama=_fill(OllamaCfg, raw.get("ollama")),
        tts=_fill(TtsCfg, raw.get("tts")),
        fetch=_fill(FetchCfg, raw.get("fetch")),
        pacing=_fill(PacingCfg, raw.get("pacing")),
        prompts=_fill(PromptsCfg, raw.get("prompts")),
        feeds=feeds,
        path=path,
    )
    if not cfg.feeds:
        raise ConfigError(f"Aucun flux RSS dans {path} (section [[feeds]])")
    return cfg
