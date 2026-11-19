#!/usr/bin/env bash
# Remove NYXO for the current user.
#   scripts/uninstall.sh          remove the app, launcher, service and shortcut; KEEP your data
#   scripts/uninstall.sh --purge  also delete settings, chat history, memories, models and logs
set -euo pipefail
PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

PREFIX="$HOME/.local/opt/nyxo"
say() { printf '\033[1;36m[Kings]\033[0m %s\n' "$*"; }

# remove our GNOME shortcut entry (if installed) while the code still exists
if [ -x "$PREFIX/venv/bin/python" ]; then
  PYTHONPATH="$PREFIX/app" "$PREFIX/venv/bin/python" -c \
    'from nyxo.desktop import shortcut as s; s.installed() and s.remove()' 2>/dev/null || true
fi

say "Stopping and disabling the service…"
systemctl --user disable --now nyxo.service 2>/dev/null || true
"$HOME/.local/bin/nyxo" --quit >/dev/null 2>&1 || true
rm -f "$HOME/.config/systemd/user/nyxo.service"
systemctl --user daemon-reload || true
systemctl --user reset-failed nyxo.service 2>/dev/null || true

say "Removing the GNOME helper extension…"
EXT_UUID="nyxo-helper@nyxo.io"
CUR="$(gsettings get org.gnome.shell enabled-extensions 2>/dev/null || true)"
case "$CUR" in *"$EXT_UUID"*)
  NEW="$(python3 -c 'import ast,sys; v=sys.argv[1].replace("@as ",""); l=[x for x in ast.literal_eval(v) if x!=sys.argv[2]]; print(repr(l))' "$CUR" "$EXT_UUID")"
  gsettings set org.gnome.shell enabled-extensions "$NEW" || true ;;
esac
rm -rf "$HOME/.local/share/gnome-shell/extensions/$EXT_UUID"

say "Removing application files…"
rm -f "$HOME/.local/bin/nyxo"
rm -f "$HOME/.local/share/applications/io.nyxo.Nyxo.desktop"
for n in 48 64 128 256 512; do
  rm -f "$HOME/.local/share/icons/hicolor/${n}x${n}/apps/io.nyxo.Nyxo.png"
done
rm -rf "$PREFIX"
command -v update-desktop-database >/dev/null && update-desktop-database -q "$HOME/.local/share/applications" || true

if [ "$PURGE" = 1 ]; then
  say "Deleting your NYXO data…"
  rm -rf "$HOME/.config/nyxo" "$HOME/.local/share/nyxo" "$HOME/.local/state/nyxo"
else
  say "Your data was kept in ~/.config/nyxo, ~/.local/share/nyxo and ~/.local/state/nyxo"
  say "(run with --purge to delete it)."
fi
say "Kings has been uninstalled. Bye! 👋"
