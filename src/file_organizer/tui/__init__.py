"""Interactive menu.

Curses when the terminal can take it, a line-based menu otherwise. Curses
is the wrong answer in a dumb terminal, over a broken TERM, or when output
is piped, so those cases fall back rather than crashing.

Both frontends share their logic through `file_organizer.actions`; this
package is only ever the presentation layer.
"""
from __future__ import annotations

import locale
import os
import sys


def can_use_curses() -> bool:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    if os.environ.get("TERM", "") in ("", "dumb", "unknown"):
        return False
    try:
        import curses  # noqa: F401
    except ImportError:
        return False
    return True


def run():
    # Without this, non-ASCII filenames misbehave under curses.
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass

    if can_use_curses():
        import curses

        # curses waits a full second after Esc to see whether it starts an
        # escape sequence, which makes "esc to go back" feel broken.
        os.environ.setdefault("ESCDELAY", "25")

        from . import curses_ui
        # curses.wrapper restores the terminal even if the UI raises, so a
        # ConfigError escapes with the screen still usable.
        return curses.wrapper(curses_ui.main)
    if sys.stdin.isatty():
        from . import plain
        return plain.main()
    print("Not a terminal. Try `file-organizer --help`.")
    return 0
