#!/usr/bin/env bash
# Install file-organizer for the current user. No sudo, nothing system-wide.
#
# Deliberately avoids `pip install`: Debian 12, Ubuntu 23.04+ and Mint 21.3+
# mark the system Python as externally managed (PEP 668), which makes
# `pip install --user .` fail with "externally-managed-environment". Copying
# the package and writing a launcher works the same on every release.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

BIN_DIR="$HOME/.local/bin"
LIB_DIR="$HOME/.local/lib/file-organizer"
APP_DIR="$HOME/.local/share/applications"

die() { printf 'error: %s\n' "$1" >&2; exit 1; }
info() { printf '  %s\n' "$1"; }

uninstall() {
    echo "Removing file-organizer:"
    rm -rf "$LIB_DIR"
    rm -f "$BIN_DIR/file-organizer"
    rm -f "$BIN_DIR/file-organizer-gui"
    rm -f "$APP_DIR/file-organizer.desktop"
    info "$LIB_DIR"
    info "$BIN_DIR/file-organizer"
    info "$BIN_DIR/file-organizer-gui"
    info "$APP_DIR/file-organizer.desktop"
    echo
    echo "Desktop actions left behind (remove by hand if you want):"
    echo "  ~/.local/share/nemo/actions/file-organizer.nemo_action"
    echo "  ~/.local/share/nautilus/scripts/Organize this folder"
    echo "  ~/.local/share/caja/scripts/Organize this folder"
    echo "  ~/.local/share/kio/servicemenus/file-organizer.desktop"
    echo "  ~/.config/Thunar/uca.xml (a backup is at uca.xml.bak)"
}

case "${1:-}" in
    -h|--help)
        cat <<'USAGE'
usage: ./install.sh [--uninstall]

Installs file-organizer into ~/.local for the current user and wires it
into whichever supported file managers are installed.
USAGE
        exit 0 ;;
    --uninstall)
        uninstall
        exit 0 ;;
    "")
        ;;
    *)
        die "unknown option: $1 (try --help)" ;;
esac

# --- checks ---------------------------------------------------------------

command -v python3 >/dev/null 2>&1 || die "python3 is not installed"

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)'; then
    die "python3 3.8 or newer is required (found $(python3 -V 2>&1))"
fi

[ -f "$SRC/src/file_organizer/cli.py" ] \
    || die "cannot find src/file_organizer next to this script; run it from the repo"

# --- install --------------------------------------------------------------

echo "Installing file-organizer:"

rm -rf "$LIB_DIR"
mkdir -p "$LIB_DIR" "$BIN_DIR"
cp -r "$SRC/src/file_organizer" "$LIB_DIR/file_organizer"
find "$LIB_DIR" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

# The launcher resolves its own location at run time, so the install keeps
# working if $HOME moves or the tree is copied elsewhere.
cat > "$BIN_DIR/file-organizer" <<'LAUNCHER'
#!/usr/bin/env bash
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for candidate in \
    "${FILE_ORGANIZER_LIB:-}" \
    "$here/../lib/file-organizer" \
    "$HOME/.local/lib/file-organizer"
do
    if [ -n "$candidate" ] && [ -d "$candidate/file_organizer" ]; then
        export PYTHONPATH="$candidate${PYTHONPATH:+:$PYTHONPATH}"
        exec python3 -m file_organizer "$@"
    fi
done

echo "file-organizer: cannot locate its library; re-run install.sh" >&2
exit 1
LAUNCHER
chmod 755 "$BIN_DIR/file-organizer"
info "$BIN_DIR/file-organizer"

install -Dm644 "$SRC/file-organizer.desktop" "$APP_DIR/file-organizer.desktop"
info "$APP_DIR/file-organizer.desktop"

# --- desktop integration --------------------------------------------------

echo
echo "Desktop integration:"
if ! "$BIN_DIR/file-organizer" integrations --install; then
    echo "  (integration was not installed - see the messages above)" >&2
    echo "  (the command line and menu work regardless)" >&2
fi

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APP_DIR" >/dev/null 2>&1 || true
fi

# --- PATH -----------------------------------------------------------------

echo
case ":$PATH:" in
    *":$BIN_DIR:"*)
        ;;
    *)
        echo "NOTE: $BIN_DIR is not on your PATH."
        echo "      Most desktops (Mint, Ubuntu, Debian with ~/.profile) add it at login."
        echo "      To use it in this shell now:  export PATH=\"\$HOME/.local/bin:\$PATH\""
        echo
        ;;
esac

echo "Done. Try it:"
echo "    file-organizer status"
echo "    file-organizer organize ~/Downloads --dry-run"
