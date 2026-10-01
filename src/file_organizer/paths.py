"""XDG base-directory resolution.

Kept in its own module so config, journal, and trash can share it without
importing one another.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

APP_NAME = "file-organizer"


def _xdg(env_var, fallback):
    """An XDG dir from the environment, ignoring relative paths (per spec)."""
    value = os.environ.get(env_var, "").strip()
    if value and os.path.isabs(value):
        return Path(value)
    return Path.home() / fallback


def config_dir():
    return _xdg("XDG_CONFIG_HOME", ".config") / APP_NAME


def config_file():
    return config_dir() / "config.json"


def state_dir():
    return _xdg("XDG_STATE_HOME", ".local/state") / APP_NAME


def journal_file():
    return state_dir() / "journal.jsonl"


def data_dir():
    return _xdg("XDG_DATA_HOME", ".local/share")


def home_trash():
    return data_dir() / "Trash"


def downloads_dir():
    """The user's download folder, resolved the way the desktop resolves it."""
    home = Path.home()
    try:
        result = subprocess.run(
            ["xdg-user-dir", "DOWNLOAD"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        text = result.stdout.strip()
        # An empty answer would become Path(""), which is the current
        # directory -- not a download folder.
        if text:
            path = Path(text)
            # xdg-user-dir echoes $HOME when the dir is unset, and organizing
            # the whole home folder is never what someone meant.
            if path.is_dir() and path.resolve() != home.resolve():
                return path
    except (OSError, subprocess.SubprocessError):
        pass
    return home / "Downloads"
