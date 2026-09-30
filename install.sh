#!/usr/bin/env bash
# Install file-organizer for the current user on Linux Mint.
# No sudo, nothing system-wide.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

BIN_DIR="$HOME/.local/bin"
ACTION_DIR="$HOME/.local/share/nemo/actions"
APP_DIR="$HOME/.local/share/applications"

install -Dm755 "$SRC/organize.py"                   "$BIN_DIR/file-organizer"
install -Dm644 "$SRC/file-organizer.nemo_action"    "$ACTION_DIR/file-organizer.nemo_action"
install -Dm644 "$SRC/file-organizer.desktop"        "$APP_DIR/file-organizer.desktop"

# Refresh the menu and Nemo so the action appears without logging out.
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APP_DIR" 2>/dev/null || true
fi
if command -v nemo >/dev/null 2>&1; then
    nemo -q 2>/dev/null || true
fi

# Mint's default ~/.profile adds ~/.local/bin only if it exists at login.
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)  echo "NOTE: $BIN_DIR is not on your PATH yet."
        echo "      Add to ~/.profile:  export PATH=\"\$HOME/.local/bin:\$PATH\""
        echo "      (or log out and back in)" ;;
esac

echo
echo "Installed. Try it:"
echo "    file-organizer --dry-run"
echo "Or right-click a folder in Nemo -> 'Organize this folder'."
