"""Curses primitives.

Deliberately free of feature logic: these functions draw, they do not
decide. Everything that could be wrong about *what* the UI does lives in
`file_organizer.actions`, where it can be tested without a terminal.

Two constraints shape the drawing code. Writing the bottom-right cell of a
window always raises `curses.error`, so every write goes through `addstr`,
which swallows it. And a terminal can be resized between two keystrokes, so
the geometry is re-measured on every frame rather than cached.
"""
from __future__ import annotations

import curses
import textwrap
from typing import List, Optional, Sequence, Tuple

#: Below this the border costs more than it explains, so the layout goes
#: flat rather than smearing over itself.
MIN_HEIGHT = 12
MIN_WIDTH = 40

POINTER = "▸"
BULLET = "·"

FOOTER_MENU = "↑/↓ move   ⏎ select   q quit"
FOOTER_FORM = "↑/↓ move   ⏎ change   esc cancel"
FOOTER_PAGE = "↑/↓ scroll   space page   q close"
FOOTER_CONFIRM = "y yes   n no"


def addstr(win, y, x, text, attr=0):
    """Draw text, ignoring the bottom-right cell, which always raises."""
    try:
        win.addstr(y, x, text, attr)
    except curses.error:
        pass


def clip(text, width):
    return text[: max(0, width)]


def curs_set(visibility):
    """Show or hide the cursor, where the terminal supports it.

    `curses.curs_set` raises on a terminal without the capability, and an
    unguarded call there takes the whole menu down with it.
    """
    try:
        curses.curs_set(visibility)
    except curses.error:
        pass


def too_small(win) -> bool:
    height, width = win.getmaxyx()
    return height < MIN_HEIGHT or width < MIN_WIDTH


def init_colors() -> dict:
    """Attribute set for the theme, or a colourless fallback."""
    plain = {"header": curses.A_BOLD, "accent": curses.A_BOLD,
             "dim": curses.A_DIM, "warn": curses.A_BOLD}
    if not curses.has_colors():
        return plain
    try:
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(2, curses.COLOR_GREEN, -1)
        curses.init_pair(3, curses.COLOR_RED, -1)
    except curses.error:
        # A terminal that claims colour but refuses the pairs is not worth
        # failing over; monochrome still reads fine.
        return plain
    return {
        "header": curses.color_pair(1) | curses.A_BOLD,
        "accent": curses.color_pair(2) | curses.A_BOLD,
        "dim": curses.A_DIM,
        "warn": curses.color_pair(3),
    }


class Screen:
    """A titled window: header, optional subtitle, body, footer hints."""

    def __init__(self, win, title, subtitle=""):
        self.win = win
        self.title = title
        self.subtitle = subtitle
        self.colors = init_colors()
        self.measure()

    def measure(self):
        self.height, self.width = self.win.getmaxyx()
        self.flat = too_small(self.win)

    @property
    def body_top(self) -> int:
        # Flat layout has no border row and no rule under the subtitle.
        return 3 if self.subtitle and not self.flat else 2

    @property
    def room(self) -> int:
        """Columns available to body text."""
        indent = 2 if self.flat else 3
        return max(1, self.width - indent - (2 if self.flat else 4))

    @property
    def body_height(self) -> int:
        return max(1, self.height - self.body_top - 2)

    def _header(self):
        if self.flat:
            addstr(self.win, 0, 2, clip(self.title, self.width - 4),
                   curses.A_BOLD)
            if self.subtitle:
                addstr(self.win, 1, 2, clip(self.subtitle, self.width - 4),
                       self.colors["dim"])
            return

        self.win.border()
        addstr(self.win, 0, 2, f" {self.title} ", self.colors["header"])
        if self.subtitle:
            addstr(self.win, 1, 2, clip(self.subtitle, self.width - 4),
                   self.colors["dim"])
            # A rule under the subtitle, stopping short of the right border.
            try:
                self.win.hline(2, 1, curses.ACS_HLINE, self.width - 2)
            except curses.error:
                pass

    def render(self, body: Sequence[Tuple[str, int]], scroll=0,
               highlight: Optional[int] = None, footer=""):
        """Draw a frame. `body` is (text, extra-attr) pairs."""
        self.measure()
        self.win.erase()
        self._header()

        top = self.body_top
        indent = 2 if self.flat else 3
        room = self.room

        for offset in range(self.body_height):
            index = scroll + offset
            if index >= len(body):
                break
            text, attr = body[index]
            if index == highlight:
                # Pad so the reverse bar reaches the far edge instead of
                # stopping at the end of the text.
                addstr(self.win, top + offset, indent,
                       clip(text.ljust(room), room), attr | curses.A_REVERSE)
            else:
                addstr(self.win, top + offset, indent, clip(text, room), attr)

        if footer:
            row = max(0, self.height - 2)
            addstr(self.win, row, 2, clip(footer, self.width - 4),
                   self.colors["dim"])
        self.win.refresh()


def _scroll_for(selected, scroll, body_height) -> int:
    """Keep the selection inside the visible window."""
    if selected < scroll:
        return selected
    if selected >= scroll + body_height:
        return selected - body_height + 1
    return scroll


def menu(win, title, items, subtitle="", footer=FOOTER_MENU) -> Optional[int]:
    """Arrow-key menu. Returns the chosen index, or None on q/Esc."""
    screen = Screen(win, title, subtitle)
    selected = 0
    scroll = 0

    while True:
        scroll = _scroll_for(selected, scroll, screen.body_height)
        body = [
            (f"{POINTER} {item}" if i == selected else f"  {item}", 0)
            for i, item in enumerate(items)
        ]
        screen.render(body, scroll=scroll, highlight=selected, footer=footer)

        key = win.getch()
        if key in (curses.KEY_UP, ord("k")):
            selected = (selected - 1) % len(items)
        elif key in (curses.KEY_DOWN, ord("j")):
            selected = (selected + 1) % len(items)
        elif key == curses.KEY_HOME:
            selected = 0
        elif key == curses.KEY_END:
            selected = len(items) - 1
        elif key in (curses.KEY_ENTER, 10, 13):
            return selected
        elif key in (ord("q"), 27):
            return None


def form(win, title, rows, subtitle="", footer=FOOTER_FORM) -> Optional[int]:
    """A changeable list.

    `rows` is (label, value) pairs. Returns the index the user activated, or
    None on q/Esc -- it never edits anything itself, so the caller keeps
    ownership of the values and nothing here can write to disk.
    """
    screen = Screen(win, title, subtitle)
    selected = 0
    scroll = 0

    while True:
        scroll = _scroll_for(selected, scroll, screen.body_height)
        body = []
        for index, (label, value) in enumerate(rows):
            mark = POINTER if index == selected else " "
            body.append((f"{mark} {label}: {value}", 0))
        screen.render(body, scroll=scroll, highlight=selected, footer=footer)

        key = win.getch()
        if key in (curses.KEY_UP, ord("k")):
            selected = (selected - 1) % len(rows)
        elif key in (curses.KEY_DOWN, ord("j")):
            selected = (selected + 1) % len(rows)
        elif key in (curses.KEY_ENTER, 10, 13, ord(" ")):
            return selected
        elif key in (ord("q"), 27):
            return None


def prompt(win, label, default="", title="Input") -> str:
    """One line of text. Returns the default when the user just hits enter."""
    curses.echo()
    curs_set(1)
    try:
        screen = Screen(win, title)
        screen.render([(label, 0), (default, curses.A_DIM)], footer="")
        addstr(win, screen.body_top + 2, 3, "> ")
        win.refresh()
        raw = win.getstr(screen.body_top + 2, 5, max(1, screen.width - 8))
        text = raw.decode("utf-8", "replace").strip()
        return text or default
    except curses.error:
        return ""
    finally:
        curses.noecho()
        curs_set(0)


def confirm(win, question, title="Confirm") -> bool:
    screen = Screen(win, title)
    screen.render([(question, curses.A_BOLD), ("", 0)], footer=FOOTER_CONFIRM)
    while True:
        key = win.getch()
        if key in (ord("y"), ord("Y")):
            return True
        if key in (ord("n"), ord("N"), ord("q"), 27):
            return False


def wrap_lines(lines: List[str], width: int) -> List[str]:
    """Wrap to `width` so a long path is readable instead of cut off.

    The undo preview is what someone checks before agreeing, and a path
    clipped at the window edge hides the part that tells two files apart.
    """
    wrapped: List[str] = []
    for line in lines:
        wrapped.extend(
            textwrap.wrap(
                line, width, subsequent_indent="    ",
                break_long_words=True, break_on_hyphens=False,
            ) or [""]
        )
    return wrapped


def pager(win, title, lines: List[str], subtitle="") -> None:
    """Scrollable read-only view. Closes on q, Esc, or enter."""
    screen = Screen(win, title, subtitle)
    scroll = 0

    while True:
        # Re-wrapped every frame: the window can be resized between keys.
        screen.measure()
        shown = wrap_lines(lines, screen.room)
        last = max(0, len(shown) - screen.body_height)
        scroll = min(scroll, last)

        screen.render([(line, 0) for line in shown], scroll=scroll,
                      footer=FOOTER_PAGE)
        key = win.getch()

        if key in (ord("q"), 27, curses.KEY_ENTER, 10, 13):
            return
        if key in (curses.KEY_UP, ord("k")):
            scroll = max(0, scroll - 1)
        elif key in (curses.KEY_DOWN, ord("j")):
            scroll = min(last, scroll + 1)
        elif key in (curses.KEY_PPAGE, ord("b")):
            scroll = max(0, scroll - screen.body_height)
        elif key in (curses.KEY_NPAGE, ord(" ")):
            scroll = min(last, scroll + screen.body_height)
        elif key == curses.KEY_HOME:
            scroll = 0
        elif key == curses.KEY_END:
            scroll = last
