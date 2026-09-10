#!/usr/bin/env bash
# Lanceur: utilise le venv de l'application.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$DIR/.venv/bin/python"
if [ ! -x "$PY" ]; then
  MSG="Environnement absent. Lance d'abord: $DIR/install.sh"
  command -v zenity >/dev/null && zenity --error --text="$MSG" 2>/dev/null || echo "$MSG" >&2
  exit 1
fi
exec "$PY" "$DIR/main.py" "$@"
