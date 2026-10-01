"""Presentation helpers both frontends need and neither owns.

Kept out of `widgets` (which imports curses) so the line-based fallback can
use them on a terminal where curses is unavailable.
"""
from __future__ import annotations

import time
from pathlib import Path

from .. import paths

BULLET = "·"


def folder_choices():
    """The obvious folders, if they exist on this machine."""
    choices = []
    for label, path in (
        ("Downloads", paths.downloads_dir()),
        ("Documents", Path.home() / "Documents"),
        ("Desktop", Path.home() / "Desktop"),
    ):
        if path.is_dir():
            choices.append((f"{label}  ({path})", path))
    return choices


def options_summary(options) -> str:
    """A one-line reminder of what a run will actually do.

    Reads through `not` on the skip_* keys because the labels above them
    are the opposite of what the config stores.
    """
    return (
        f"conflict={options['on_conflict']}"
        f"  hidden={'on' if not options['skip_hidden'] else 'off'}"
        f"  incomplete={'on' if not options['skip_incomplete'] else 'off'}"
        f"  links={'on' if options['follow_links'] else 'off'}"
        f"  undo={'on' if options['journal'] else 'off'}"
    )


def run_label(run) -> str:
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(run["time"]))
    return f"{when}  {len(run['moves'])} file(s)  {run['folder']}"
