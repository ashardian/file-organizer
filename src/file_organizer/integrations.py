"""Desktop file-manager integration.

Detects which file managers are present and installs the matching
integration. Everything routes through one helper script
(`file-organizer-gui`) that opens a terminal, so the user always sees the
plan and confirms before anything moves -- a GUI-launched action that
silently relocates files would be a nasty surprise.

Only the Nemo integration can be verified on the development machine. The
others are written to each file manager's documented format.
"""
from __future__ import annotations

import shutil
import stat
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict

from . import paths

GUI_HELPER_NAME = "file-organizer-gui"

# Opens a terminal so the run is visible and confirmed. Falls back to an
# explicit error rather than moving files with no preview.
GUI_HELPER = r"""#!/usr/bin/env bash
# File Organizer: run the organizer on a folder in a visible terminal.
# Installed by `file-organizer integrations --install`.
set -uo pipefail

target="${1:-}"
if [ -z "$target" ]; then
    echo "usage: file-organizer-gui FOLDER" >&2
    exit 2
fi

# A file may be selected rather than its folder; organize the folder.
if [ ! -d "$target" ]; then
    target="$(dirname "$target")"
fi

# A right-click action often runs with a thinner PATH than a login shell, so
# prefer the launcher installed next to this script.
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
organizer="file-organizer"
if [ -x "$here/file-organizer" ]; then
    organizer="$here/file-organizer"
fi

# Wait for a keypress at the end. A terminal that closes the moment the run
# finishes takes the result, and any error, with it.
inner='"$0" organize --confirm "$1"; status=$?; echo; read -r -p "Press Enter to close. " _; exit $status'
cmd=(bash -c "$inner" "$organizer" "$target")

# Terminals disagree about how `-e` works, so each gets its own spelling.
for term in x-terminal-emulator gnome-terminal konsole xfce4-terminal \
            mate-terminal lxterminal tilix alacritty kitty xterm; do
    command -v "$term" >/dev/null 2>&1 || continue
    case "$term" in
        gnome-terminal)
            exec "$term" -- "${cmd[@]}" ;;
        xfce4-terminal|mate-terminal)
            exec "$term" -x "${cmd[@]}" ;;
        kitty)
            exec "$term" "${cmd[@]}" ;;
        tilix)
            exec "$term" -e "$(printf '%q ' "${cmd[@]}")" ;;
        *)
            exec "$term" -e "${cmd[@]}" ;;
    esac
done

echo "No terminal emulator found; run this manually:" >&2
echo "    file-organizer organize --confirm \"$target\"" >&2
exit 1
"""

NEMO_ACTION = """[Nemo Action]
Name=Organize this folder
Comment=Sort files into subfolders by type
Exec={helper} %F
Icon=folder
Selection=S
Extensions=dir;
Quote=double
Terminal=false
"""

DOLPHIN_SERVICE = """[Desktop Entry]
Type=Service
MimeType=inode/directory;
ServiceTypes=KonqPopupMenu/Plugin
Actions=organizeFolder;
X-KDE-Submenu=File Organizer

[Desktop Action organizeFolder]
Name=Organize this folder
Icon=folder
Exec={helper} %f
"""

# Nautilus and Caja both pass selected paths through an environment variable.
SCRIPT_MANAGERS = {
    "nautilus": "NAUTILUS_SCRIPT_SELECTED_FILE_PATHS",
    "caja": "CAJA_SCRIPT_SELECTED_FILE_PATHS",
}

SCRIPT_TEMPLATE = """#!/usr/bin/env bash
# File Organizer script for {manager}. Installed by
# `file-organizer integrations --install`.
set -uo pipefail

selected="${{{var}:-}}"
target="${{selected%%$'\\n'*}}"

if [ -z "$target" ]; then
    target="${{*:-}}"
fi
if [ -z "$target" ]; then
    echo "Select a folder first." >&2
    exit 1
fi

exec {helper} "$target"
"""

THUNAR_UNIQUE_ID = "file-organizer-organize"


def _bin_dir() -> Path:
    return Path.home() / ".local" / "bin"


def _data_dir() -> Path:
    return paths.data_dir()


def _targets() -> Dict[str, Path]:
    data = _data_dir()
    return {
        "nemo": data / "nemo" / "actions" / "file-organizer.nemo_action",
        "nautilus": data / "nautilus" / "scripts" / "Organize this folder",
        "caja": data / "caja" / "scripts" / "Organize this folder",
        "dolphin": data / "kio" / "servicemenus" / "file-organizer.desktop",
        "thunar": Path.home() / ".config" / "Thunar" / "uca.xml",
    }


def _write(path: Path, content: str, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if executable:
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def install_helper() -> Path:
    """Write the shared terminal-launcher helper."""
    target = _bin_dir() / GUI_HELPER_NAME
    _write(target, GUI_HELPER, executable=True)
    return target


def _is_installed(name: str, path: Path) -> bool:
    """Whether our entry is there, not merely whether the file is.

    Thunar keeps every custom action in one shared uca.xml, so that file
    existing says nothing about whether *our* action is in it.
    """
    if not path.exists():
        return False
    if name != "thunar":
        return True
    try:
        return THUNAR_UNIQUE_ID in path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False


def detect() -> Dict[str, Dict]:
    """Which supported file managers are present, and are we wired into them?"""
    found = {}
    for name, path in _targets().items():
        if shutil.which(name) is None:
            continue
        found[name] = {
            "path": path,
            "installed": _is_installed(name, path),
            "manager": name,
        }
    return found


def _install_thunar(path: Path) -> None:
    """Merge our action into Thunar's uca.xml.

    This rewrites a file the user may already have custom actions in, so it
    is backed up first and a parse failure aborts rather than overwriting.
    """
    if path.exists():
        try:
            tree = ET.parse(path)
        except ET.ParseError as exc:
            raise RuntimeError(
                f"{path} is not valid XML ({exc}); refusing to overwrite it. "
                "Fix or move that file, then re-run."
            )
        # Only the first backup: on a re-install the file already holds our
        # action, and copying it over would destroy the user's original.
        backup = path.with_suffix(".xml.bak")
        if not backup.exists():
            shutil.copy2(path, backup)
        root = tree.getroot()
    else:
        root = ET.Element("actions")
        tree = ET.ElementTree(root)

    if root.tag != "actions":
        raise RuntimeError(f"{path}: unexpected root element <{root.tag}>")

    for existing in root.findall("action"):
        unique = existing.findtext("unique-id")
        if unique == THUNAR_UNIQUE_ID:
            root.remove(existing)

    action = ET.SubElement(root, "action")
    ET.SubElement(action, "icon").text = "folder"
    ET.SubElement(action, "name").text = "Organize this folder"
    ET.SubElement(action, "unique-id").text = THUNAR_UNIQUE_ID
    ET.SubElement(action, "command").text = f"{GUI_HELPER_NAME} %f"
    ET.SubElement(action, "description").text = (
        "Sort files into subfolders by type"
    )
    ET.SubElement(action, "patterns").text = "*"
    ET.SubElement(action, "directories")

    path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(path, encoding="utf-8", xml_declaration=True)


def install(name: str) -> Path:
    """Install integration for one file manager."""
    targets = _targets()
    if name not in targets:
        raise ValueError(f"unsupported file manager: {name}")

    install_helper()
    path = targets[name]

    if name == "nemo":
        _write(path, NEMO_ACTION.format(helper=GUI_HELPER_NAME))
    elif name == "dolphin":
        _write(path, DOLPHIN_SERVICE.format(helper=GUI_HELPER_NAME))
        # Older Plasma looks in kservices5 instead of kio.
        legacy = (Path.home() / ".local" / "share" / "kservices5"
                  / "ServiceMenus" / "file-organizer.desktop")
        _write(legacy, DOLPHIN_SERVICE.format(helper=GUI_HELPER_NAME))
    elif name in SCRIPT_MANAGERS:
        _write(
            path,
            SCRIPT_TEMPLATE.format(
                manager=name.capitalize(),
                var=SCRIPT_MANAGERS[name],
                helper=GUI_HELPER_NAME,
            ),
            executable=True,
        )
    elif name == "thunar":
        _install_thunar(path)

    return path


def refresh() -> None:
    """Ask the desktop to notice the new files, best effort."""
    # Deliberately not `nemo -q` and friends: that closes every window the
    # user has open. The entries are picked up when the file manager next
    # reads its action folders.
    command = ["update-desktop-database", str(_data_dir() / "applications")]
    if shutil.which(command[0]) is None:
        return
    try:
        subprocess.run(command, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass
