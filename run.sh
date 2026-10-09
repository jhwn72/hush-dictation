#!/usr/bin/env bash
# Sets up Hush on macOS (first run), builds ~/Applications/Hush.app, and opens it.
#   ./run.sh             set up if needed, then open Hush.app (no Terminal window needed afterwards)
#   ./run.sh --terminal  run in this Terminal instead, showing output (for debugging)
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Setting up Hush for the first time..."
  # Hush needs Python 3.10+; macOS's built-in python3 is often older
  PY=""
  for c in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
      PY="$c"; break
    fi
  done
  # no admin rights / no Homebrew: a Python installed by uv (https://docs.astral.sh/uv) works too
  if [ -z "$PY" ] && command -v uv >/dev/null 2>&1; then
    PY="$(uv python find '>=3.10' 2>/dev/null || true)"
  fi
  if [ -z "$PY" ] && [ -x "$HOME/.local/bin/uv" ]; then
    PY="$("$HOME/.local/bin/uv" python find '>=3.10' 2>/dev/null || true)"
  fi
  if [ -z "$PY" ]; then
    echo "Hush needs Python 3.10 or newer."
    echo "  With admin rights:    brew install python@3.12"
    echo "  Without admin rights: curl -LsSf https://astral.sh/uv/install.sh | sh   then   ~/.local/bin/uv python install 3.12"
    echo "Then run ./run.sh again."
    exit 1
  fi
  "$PY" -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
fi
# keep packages in step with the code after a git pull
if [ requirements.txt -nt .venv/.installed ]; then
  .venv/bin/python -m pip install -q -r requirements.txt && touch .venv/.installed
fi
if ! command -v ollama >/dev/null 2>&1 && [ ! -d /Applications/Ollama.app ] && [ ! -d "$HOME/Applications/Ollama.app" ]; then
  echo "Note: Ollama isn't installed, so cleanup needs Claude (Settings -> Cleanup model). Optional: ollama.com"
fi
if [ "$1" = "--terminal" ]; then
  exec .venv/bin/python -m hush
fi
./tools/install_mac_app.sh
pkill -f ".venv/bin/python -m hush" 2>/dev/null || true  # an older copy started from Terminal
open "$HOME/Applications/Hush.app"
echo "Hush is running. You can close this Terminal window; next time just open Hush from Spotlight or the Dock."
