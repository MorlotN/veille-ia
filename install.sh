#!/usr/bin/env bash
#
# Installe Veille IA: environnement Python isole, configuration, lanceur
# double-cliquable, et si demande une execution automatique quotidienne.
#
#   ./install.sh              installation complete (TTS local inclus)
#   ./install.sh --no-tts     sans piper-tts (~500 Mo en moins, audio via serveur HTTP)
#   ./install.sh --timer      ajoute le declenchement automatique a 20h00
#   ./install.sh --uninstall  retire lanceurs et timer (garde config et base)
#
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$DIR/.venv"
PY="$VENV/bin/python"
APPS_DIR="$HOME/.local/share/applications"
DESKTOP_FILE="$APPS_DIR/veille-ia.desktop"
UNIT_DIR="$HOME/.config/systemd/user"
WITH_TTS=1
WITH_TIMER=0
UNINSTALL=0

for arg in "$@"; do
  case "$arg" in
    --no-tts)    WITH_TTS=0 ;;
    --timer)     WITH_TIMER=1 ;;
    --uninstall) UNINSTALL=1 ;;
    -h|--help)   sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "Option inconnue: $arg" >&2; exit 1 ;;
  esac
done

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[1;32mOK\033[0m  %s\n' "$*"; }
warn() { printf '    \033[1;33m!!\033[0m  %s\n' "$*"; }
die()  { printf '\n\033[1;31mERREUR: %s\033[0m\n' "$*" >&2; exit 1; }

desktop_targets() {
  # Un seul vrai bureau: celui que declare XDG. Sinon on retombe sur les noms usuels.
  local xdg
  xdg="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
  if [ -n "$xdg" ] && [ -d "$xdg" ] && [ "$xdg" != "$HOME" ]; then
    echo "$xdg"
    return
  fi
  for d in "$HOME/Bureau" "$HOME/Desktop"; do
    [ -d "$d" ] && echo "$d" && return
  done
}

if [ "$UNINSTALL" = 1 ]; then
  say "Desinstallation des lanceurs"
  rm -f "$DESKTOP_FILE"
  while read -r d; do rm -f "$d/veille-ia.desktop" "$d/Veille IA.desktop"; done < <(desktop_targets)
  if systemctl --user list-unit-files 2>/dev/null | grep -q veille-ia.timer; then
    systemctl --user disable --now veille-ia.timer >/dev/null 2>&1 || true
  fi
  rm -f "$UNIT_DIR/veille-ia.service" "$UNIT_DIR/veille-ia.timer"
  systemctl --user daemon-reload 2>/dev/null || true
  update-desktop-database "$APPS_DIR" 2>/dev/null || true
  ok "lanceurs et timer retires (configuration et base conservees)"
  exit 0
fi

# ------------------------------------------------------------------ 1. venv
say "1/5 Environnement Python isole"
command -v python3 >/dev/null || die "python3 introuvable"
python3 -c 'import venv, ensurepip' 2>/dev/null \
  || die "module venv absent: sudo apt install python3-venv"
python3 -c 'import tkinter' 2>/dev/null \
  || warn "tkinter absent: l'interface graphique ne demarrera pas (sudo apt install python3-tk)"
[ -x "$PY" ] || python3 -m venv "$VENV"
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet feedparser beautifulsoup4 requests
ok "dependences de base installees ($("$PY" -V))"

if [ "$WITH_TTS" = 1 ]; then
  echo "    Installation de piper-tts (voix locale, telechargement de ~400 Mo)..."
  if "$PY" -m pip install --quiet "piper-tts>=1.7,<2"; then
    ok "piper-tts installe, synthese vocale dans le processus"
  else
    warn "piper-tts non installe: l'audio passera par le serveur HTTP s'il existe"
  fi
else
  ok "piper-tts ignore (--no-tts)"
fi
command -v ffmpeg >/dev/null || warn "ffmpeg absent, pas de MP3: sudo apt install ffmpeg"

# ---------------------------------------------------------- 2. voix francaises
say "2/5 Voix francaises"
# Deux voix: la lecture alterne l'une et l'autre, un article sur deux.
for VOICE in fr_FR-siwis-medium fr_FR-tom-medium; do
  VOICE_DIR=""
  for d in "$HOME/piper-models" "$HOME/code/jarvis/voiced/models"; do
    [ -s "$d/$VOICE.onnx" ] && VOICE_DIR="$d" && break
  done
  if [ -n "$VOICE_DIR" ]; then
    ok "voix $VOICE deja presente dans $VOICE_DIR"
  elif [ "$WITH_TTS" = 1 ] && "$PY" -c "import piper" 2>/dev/null; then
    mkdir -p "$HOME/piper-models"
    echo "    Telechargement de $VOICE..."
    if "$PY" -m piper.download_voices --download-dir "$HOME/piper-models" \
          "$VOICE" >/dev/null 2>&1; then
      ok "voix $VOICE telechargee dans ~/piper-models"
    else
      warn "telechargement de $VOICE echoue"
    fi
  else
    warn "voix $VOICE absente"
  fi
done

# --------------------------------------------------------- 3. configuration
say "3/5 Configuration"
"$PY" - "$DIR" <<'PYEOF'
import sys
sys.path.insert(0, sys.argv[1])
from veille_ia import config
print("   ", config.ensure_config())
PYEOF
CONF="${XDG_CONFIG_HOME:-$HOME/.config}/veille-ia/config.toml"
if grep -q METTRE_ICI_LE_TOKEN_DU_BOT "$CONF" 2>/dev/null; then
  warn "token Telegram a renseigner dans $CONF"
else
  ok "configuration presente"
fi

# ------------------------------------------------------------- 4. lanceurs
say "4/5 Lanceur double-clic"
mkdir -p "$APPS_DIR"
TERM_CMD=""
for t in gnome-terminal kgx x-terminal-emulator; do
  command -v "$t" >/dev/null && TERM_CMD="$t" && break
done
STATE_DIR_APP="${XDG_STATE_HOME:-$HOME/.local/state}/veille-ia"

cat > "$DESKTOP_FILE" <<UNIT
[Desktop Entry]
Type=Application
Version=1.0
Name=Veille IA
GenericName=Veille technologique
Comment=Veille IA quotidienne: RSS, Ollama, Telegram, voix Piper
Exec=$DIR/run.sh
Path=$DIR
Icon=$DIR/assets/icon.png
Terminal=false
Categories=Utility;
Keywords=veille;IA;RSS;Telegram;actualites;
StartupNotify=true
StartupWMClass=Veilleia
Actions=test;journal;
UNIT

if [ -n "$TERM_CMD" ]; then
  cat >> "$DESKTOP_FILE" <<UNIT

[Desktop Action test]
Name=Test (1 article)
Exec=$TERM_CMD -- $DIR/run.sh --test
UNIT
else
  cat >> "$DESKTOP_FILE" <<UNIT

[Desktop Action test]
Name=Test (1 article)
Exec=$DIR/run.sh --test
UNIT
fi

cat >> "$DESKTOP_FILE" <<UNIT

[Desktop Action journal]
Name=Ouvrir le journal
Exec=xdg-open $STATE_DIR_APP/veille.log
UNIT
chmod +x "$DESKTOP_FILE"
update-desktop-database "$APPS_DIR" 2>/dev/null || true
ok "present dans le menu des applications"

while read -r d; do
  rm -f "$d/veille-ia.desktop"          # ancien nom, evite une icone en double
  cp "$DESKTOP_FILE" "$d/Veille IA.desktop"
  chmod +x "$d/Veille IA.desktop"
  gio set "$d/Veille IA.desktop" metadata::trusted true 2>/dev/null || true
  ok "icone posee sur $d"
done < <(desktop_targets)

# ---------------------------------------------------------------- 5. timer
say "5/5 Execution automatique"
if [ "$WITH_TIMER" = 1 ]; then
  mkdir -p "$UNIT_DIR"
  cat > "$UNIT_DIR/veille-ia.service" <<UNIT
[Unit]
Description=Veille IA (execution quotidienne)
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$DIR
ExecStart=$DIR/run.sh --cli
TimeoutStartSec=0
UNIT
  cat > "$UNIT_DIR/veille-ia.timer" <<'UNIT'
[Unit]
Description=Declenche la veille IA chaque jour a 20h00

[Timer]
OnCalendar=*-*-* 20:00:00
Persistent=true
AccuracySec=1min

[Install]
WantedBy=timers.target
UNIT
  systemctl --user daemon-reload
  systemctl --user enable --now veille-ia.timer
  ok "timer actif: $(systemctl --user list-timers veille-ia.timer --no-pager | sed -n 2p | awk '{print $1,$2,$3}')"
else
  ok "aucun timer (relance avec --timer pour un declenchement a 20h00)"
fi

say "Diagnostics"
"$PY" "$DIR/main.py" --check || true

cat <<RESUME

------------------------------------------------------------------
Installe. Pour lancer: double-clic sur "Veille IA" (bureau ou menu).

  Application    : $DIR
  Configuration  : $CONF
  Base + journal : ${XDG_STATE_HOME:-$HOME/.local/state}/veille-ia
  Console        : $DIR/run.sh --cli
  Simulation     : $DIR/run.sh --dry-run
  Diagnostics    : $DIR/run.sh --check
------------------------------------------------------------------
RESUME
