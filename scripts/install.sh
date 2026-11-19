#!/usr/bin/env bash
# Install (or update) NYXO for the current user. No sudo required.
#
#   scripts/install.sh               install/update, enable login autostart, start now
#   scripts/install.sh --no-autostart   install without enabling autostart
#   scripts/install.sh --no-start       don't (re)start NYXO after installing
#
# Layout:
#   ~/.local/opt/nyxo/app      application code + assets (replaced on update)
#   ~/.local/opt/nyxo/venv     private Python environment
#   ~/.local/bin/nyxo          launcher
#   ~/.local/share/applications/io.nyxo.Nyxo.desktop
#   ~/.config/systemd/user/nyxo.service
# User data lives separately in ~/.config/nyxo, ~/.local/share/nyxo, ~/.local/state/nyxo
set -euo pipefail

AUTOSTART=1
START=1
for a in "$@"; do
  case "$a" in
    --no-autostart) AUTOSTART=0 ;;
    --no-start) START=0 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "unknown option $a" >&2; exit 2 ;;
  esac
done

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX="$HOME/.local/opt/nyxo"
APP="$PREFIX/app"
VENV="$PREFIX/venv"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor"

say() { printf '\033[1;36m[Kings]\033[0m %s\n' "$*"; }

python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || { echo "Python 3.10+ is required" >&2; exit 1; }
python3 -c 'import venv' 2>/dev/null || { echo "python3-venv is required: sudo apt install python3-venv" >&2; exit 1; }

say "Installing Kings from $SRC"
mkdir -p "$PREFIX" "$BIN" "$APPS"

# 1. sprites (only if missing — they are committed artefacts)
if [ ! -f "$SRC/assets/animations/manifest.json" ]; then
  say "Generating sprites…"
  "${VENV}/bin/python" "$SRC/scripts/generate_sprites.py" 2>/dev/null \
    || "$SRC/.venv/bin/python" "$SRC/scripts/generate_sprites.py"
fi

# 2. application files (staged, then swapped in)
rm -rf "$APP.new"
mkdir -p "$APP.new"
cp -a "$SRC/nyxo" "$SRC/assets" "$APP.new/"
for f in README.md LICENSE requirements.txt pyproject.toml DEVELOPMENT_STATUS.md; do
  [ -f "$SRC/$f" ] && cp -a "$SRC/$f" "$APP.new/"
done
[ -d "$SRC/docs" ] && cp -a "$SRC/docs" "$APP.new/"
[ -d "$SRC/scripts" ] && cp -a "$SRC/scripts" "$APP.new/"
find "$APP.new" -name __pycache__ -type d -prune -exec rm -rf {} +
if [ -d "$APP" ]; then mv "$APP" "$APP.old"; fi
mv "$APP.new" "$APP"
rm -rf "$APP.old"
say "App files → $APP"

# 3. private virtualenv
if [ ! -x "$VENV/bin/python" ]; then
  say "Creating Python environment (first install downloads ~90 MB)…"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip --retries 10 --timeout 60 || true
fi
say "Installing Python packages…"
"$VENV/bin/pip" install -q --retries 10 --timeout 90 -r "$APP/requirements.txt"

# 3b. voice & speech models (best effort; NYXO falls back to espeak / text without them)
MODELS="$HOME/.local/share/nyxo/models"
mkdir -p "$MODELS/piper"
fetch() {  # url dest
  for t in 1 2 3 4 5; do curl -fsSL -C - --retry 3 -o "$2.part" "$1" && mv "$2.part" "$2" && return 0; sleep 3; done
  return 1
}
if ! ls "$MODELS"/piper/*.onnx >/dev/null 2>&1; then
  say "Downloading NYXO's voice (Piper en_GB-alan-medium, 63 MB)…"
  V=https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/en_GB-alan-medium.onnx
  fetch "$V.json" "$MODELS/piper/en_GB-alan-medium.onnx.json" && fetch "$V" "$MODELS/piper/en_GB-alan-medium.onnx" \
    || say "Voice download failed — Kings will use espeak until you retry (Settings → Voice)."
fi
if ! ls -d "$MODELS"/vosk-model* >/dev/null 2>&1; then
  say "Downloading speech-recognition model (Vosk small English, 40 MB)…"
  if fetch https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip "$MODELS/vosk.zip"; then
    "$VENV/bin/python" -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$MODELS/vosk.zip" "$MODELS"
    rm -f "$MODELS/vosk.zip"
  else
    say "Vosk download failed — push-to-talk stays off until you retry (Settings → Voice)."
  fi
fi

mkdir -p "$MODELS/wakeword"
for f in melspectrogram.onnx embedding_model.onnx hey_jarvis_v0.1.onnx; do
  if [ ! -f "$MODELS/wakeword/$f" ]; then
    fetch "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/$f" "$MODELS/wakeword/$f" \
      || { say "Wake-word model download failed — 'Hey Jarvis' stays off until you retry."; break; }
  fi
done

if [ ! -f "$MODELS/whisper/base.en/model.bin" ]; then
  say "Downloading Whisper speech model (base.en, 145 MB)…"
  for t in 1 2 3 4 5; do
    "$VENV/bin/python" -c "from faster_whisper.utils import download_model as d; d('base.en', output_dir='$MODELS/whisper/base.en')" \
      >/dev/null 2>&1 && break
    sleep 5
  done
  [ -f "$MODELS/whisper/base.en/model.bin" ] || say "Whisper download failed — voice input uses Vosk until you retry."
fi
if [ ! -x "$HOME/.local/opt/ollama/bin/ollama" ] && [ ! -e /usr/local/lib/ollama/llama-server ] && ! [ -e /usr/lib/ollama/llama-server ]; then
  say "Note: no complete Ollama runtime found. For local AI run: scripts/install_ollama.sh (no sudo, ~1.4 GB download)."
fi

# 3c. tiny GNOME Shell helper (full-screen detection; exposes a yes/no flag only)
EXT_UUID="nyxo-helper@nyxo.io"
EXT_DIR="$HOME/.local/share/gnome-shell/extensions/$EXT_UUID"
if command -v gnome-shell >/dev/null; then
  mkdir -p "$EXT_DIR"
  cp -a "$SRC/packaging/gnome-extension/$EXT_UUID/." "$EXT_DIR/"
  CUR="$(gsettings get org.gnome.shell enabled-extensions 2>/dev/null || echo '@as []')"
  case "$CUR" in
    *"$EXT_UUID"*) ;;
    *)
      mkdir -p "$HOME/.config/nyxo/backups"
      echo "$CUR" > "$HOME/.config/nyxo/backups/enabled-extensions-$(date +%Y%m%d-%H%M%S).txt"
      NEW="$(python3 -c 'import ast,sys; v=sys.argv[1].replace("@as ",""); l=ast.literal_eval(v) if v.strip() else []; l.append(sys.argv[2]); print(repr(l))' "$CUR" "$EXT_UUID")"
      gsettings set org.gnome.shell enabled-extensions "$NEW" && say "GNOME helper extension enabled (active after your next login)."
      ;;
  esac
fi

# 4. launcher
cat > "$BIN/nyxo" <<EOF
#!/bin/sh
# NYXO launcher — Your desktop. Your companion. Your AI.
export PYTHONPATH="$APP"
exec "$VENV/bin/python" -m nyxo "\$@"
EOF
chmod 755 "$BIN/nyxo"
say "Launcher → $BIN/nyxo"

# 5. icon + desktop entry
for n in 48 64 128 256 512; do
  mkdir -p "$ICONS/${n}x${n}/apps"
  cp "$APP/assets/icons/nyxo-$n.png" "$ICONS/${n}x${n}/apps/io.nyxo.Nyxo.png"
done
AVI="$HOME/.local/share/nyxo/avatars/kings/icons"
if [ -f "$AVI/icon-256.png" ]; then              # use the Kings portrait icon if it was built
  for n in 48 64 128 256 512; do cp "$AVI/icon-$n.png" "$ICONS/${n}x${n}/apps/io.nyxo.Nyxo.png"; done
fi
sed "s|@BIN@|$BIN|g" "$SRC/packaging/io.nyxo.Nyxo.desktop" > "$APPS/io.nyxo.Nyxo.desktop"
chmod 644 "$APPS/io.nyxo.Nyxo.desktop"
command -v update-desktop-database >/dev/null && update-desktop-database -q "$APPS" || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$ICONS" 2>/dev/null || true
say "Desktop launcher → $APPS/io.nyxo.Nyxo.desktop"

# 6. systemd user service (login autostart)
PYTHONPATH="$APP" "$VENV/bin/python" -c 'from nyxo.startup import manager as m; print(m.write_unit())' >/dev/null
say "Service → $HOME/.config/systemd/user/nyxo.service"
if [ "$AUTOSTART" = 1 ]; then
  systemctl --user enable nyxo.service 2>&1 | sed 's/^/        /'
  say "Autostart at login: enabled"
fi

# 7. (re)start so updates take effect
if [ "$START" = 1 ]; then
  # stop an unmanaged dev instance if one is running, then start the service
  "$BIN/nyxo" --quit >/dev/null 2>&1 || true
  sleep 1
  systemctl --user reset-failed nyxo.service 2>/dev/null || true
  systemctl --user restart nyxo.service
  sleep 3
  if systemctl --user is-active --quiet nyxo.service; then
    say "Kings is running. 🤖"
  else
    say "Kings failed to start — see: journalctl --user -u nyxo.service -e"
    exit 1
  fi
fi

case ":$PATH:" in *":$BIN:"*) ;; *) say "Note: add $BIN to your PATH to run 'nyxo' from a terminal." ;; esac
say "Done. Manage NYXO with: nyxo --status | nyxo --chat | nyxo --quit"
