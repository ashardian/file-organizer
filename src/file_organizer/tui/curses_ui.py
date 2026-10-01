"""The curses screens.

Thin by design: every screen here formats the result of a function in
`file_organizer.actions`. There is no planning, executing, or file-handling
code in this module, which is what keeps the menu honest about what the
command line does.
"""
from __future__ import annotations

import curses
import subprocess
from pathlib import Path
from typing import Dict, Optional

from .. import actions, config as config_mod, paths
from ..journal import Journal, JournalError
from ..rules import ConfigError
from .common import BULLET, folder_choices, options_summary, run_label
from .widgets import confirm, curs_set, form, menu, pager, prompt

MENU_ITEMS = (
    "Preview a folder (dry run)",
    "Organize a folder",
    "Organize with different options...",
    "Undo the last run",
    "Undo an earlier run...",
    "Configuration",
    "Status",
    "Desktop integration",
    "Quit",
)
QUIT = len(MENU_ITEMS) - 1


# --- shared helpers ------------------------------------------------------


def _validated(win, raw) -> Optional[Path]:
    """Check a picked path with the same rules the command line uses.

    This used to be re-implemented here and missed the refusal of `/`, so
    the menu would offer to reorganize the entire filesystem.
    """
    try:
        return actions.resolve_folder(raw)
    except ValueError as exc:
        pager(win, "Not allowed", [str(exc)], subtitle=str(raw))
        return None


def _pick_folder(win) -> Optional[Path]:
    choices = folder_choices()
    labels = [label for label, _ in choices]
    labels += ["Type a path...", "Back"]

    index = menu(win, "Which folder?", labels)
    if index is None or index == len(labels) - 1:
        return None
    if index == len(labels) - 2:
        raw = prompt(win, "Folder path:", str(paths.downloads_dir()),
                     title="Folder")
        return _validated(win, raw) if raw else None
    return _validated(win, choices[index][1])


def _load_runs(win):
    """Undoable runs, or None once the problem has been shown."""
    journal = Journal()
    try:
        return journal, journal.runs()
    except JournalError as exc:
        pager(win, "Journal", [f"Cannot read the journal: {exc}"])
        return journal, None


# --- organizing ----------------------------------------------------------


def _options_screen(win, cfg) -> Optional[Dict]:
    """Adjust this run's options. Returns them, or None if cancelled.

    Nothing here writes to disk. The values live in a local dict and are
    discarded when the run ends, exactly as a command-line flag would be.
    """
    options = actions.default_options(cfg)
    count = len(actions.OPTION_FIELDS)
    subtitle = f"for this run only {BULLET} your config file is not changed"

    while True:
        rows = [
            (field.label, actions.display_value(options, field))
            for field in actions.OPTION_FIELDS
        ]
        rows.append(("Done — now choose a folder", ""))

        index = form(win, "Options", rows, subtitle=subtitle)
        if index is None:
            return None
        if index == count:
            return options
        options = actions.cycle_option(options, actions.OPTION_FIELDS[index])


def _run(win, folder, cfg, options, dry_run=False) -> None:
    plan = actions.build_plan_for(folder, cfg, options)
    summary = options_summary(options)

    if not plan.moves or dry_run:
        outcome = actions.OrganizeOutcome(folder, plan, dry_run=dry_run)
        title = "Preview" if dry_run else "Nothing to do"
        pager(win, title, actions.organize_report(outcome).all,
              subtitle=f"{folder}  {BULLET}  {summary}")
        return

    # The plan is shown before the question, the same order the CLI uses.
    pager(win, "Plan", actions.plan_preview(folder, plan),
          subtitle=f"{len(plan.moves)} file(s)  {BULLET}  {summary}")

    if not confirm(win, f"Move {len(plan.moves)} file(s)?"):
        pager(win, "Cancelled", ["Cancelled, nothing moved."])
        return

    outcome = actions.execute_plan(folder, plan, options)
    report = actions.organize_report(outcome)
    pager(win, "Done" if report.ok else "Finished with errors", report.all,
          subtitle=summary)


def _do_preview(win, cfg) -> None:
    folder = _pick_folder(win)
    if folder is not None:
        _run(win, folder, cfg, actions.default_options(cfg), dry_run=True)


def _do_organize(win, cfg) -> None:
    folder = _pick_folder(win)
    if folder is not None:
        _run(win, folder, cfg, actions.default_options(cfg))


def _do_organize_with_options(win, cfg) -> None:
    options = _options_screen(win, cfg)
    if options is None:
        return
    folder = _pick_folder(win)
    if folder is not None:
        _run(win, folder, cfg, options)


# --- undo ----------------------------------------------------------------


def _undo_screen(win, journal, run) -> None:
    preview = actions.undo_preview_report(run, journal)
    pager(win, "Undo preview", preview.all, subtitle=run_label(run))

    if not confirm(win, "Restore these files?"):
        return

    report = actions.undo_report(run, journal)
    pager(win, "Undone" if report.ok else "Finished with errors", report.all)


def _do_undo_last(win, cfg) -> None:
    journal, runs = _load_runs(win)
    if runs is None:
        return
    if not runs:
        pager(win, "Undo", ["Nothing to undo."])
        return
    _undo_screen(win, journal, runs[0])


def _do_undo_pick(win, cfg) -> None:
    journal, runs = _load_runs(win)
    if runs is None:
        return
    if not runs:
        pager(win, "Undo", ["Nothing to undo."])
        return

    index = menu(win, "Undo which run?", [run_label(run) for run in runs],
                 subtitle=f"{len(runs)} undoable run(s)")
    if index is not None:
        _undo_screen(win, journal, runs[index])


# --- configuration -------------------------------------------------------


def _check_config(win) -> None:
    try:
        cfg = config_mod.load()
    except ConfigError as exc:
        pager(win, "Config is not valid", [str(exc)])
        return
    report = actions.config_check_report(cfg, paths.config_file())
    pager(win, "Config", report.all)


def _edit_config(win, target) -> None:
    try:
        if not target.exists():
            config_mod.write_template(target)
        command = config_mod.editor_command(target)
    except (OSError, ConfigError) as exc:
        pager(win, "Config", [f"cannot open the editor: {exc}"])
        return

    # Hand the terminal over first. Without this the editor draws into a
    # screen curses still owns, and returning leaves the display wrecked.
    curses.def_prog_mode()
    curses.endwin()
    try:
        code = subprocess.call(command)
    except OSError as exc:
        code, error = None, exc
    curses.reset_prog_mode()
    win.clear()
    win.refresh()

    if code is None:
        pager(win, "Config", [f"cannot run {command[0]}: {error}"])
        return
    if code != 0:
        pager(win, "Config", [f"{command[0]} exited with status {code}"])
        return
    _check_config(win)


def _do_config(win, cfg) -> None:
    target = paths.config_file()
    while True:
        choice = menu(win, "Configuration", [
            "Show the effective configuration",
            "Show the config file path",
            "Write a starter config",
            "Open it in your editor",
            "Check it for errors",
            "Back",
        ], subtitle=str(target))

        if choice is None or choice == 5:
            return
        if choice == 0:
            # Reloaded: `cfg` predates any edit made from this menu.
            try:
                current = config_mod.load()
            except ConfigError as exc:
                pager(win, "Config is not valid", [str(exc)])
            else:
                pager(win, "Configuration",
                      actions.config_report(current, target).all)
        elif choice == 1:
            pager(win, "Config path", [str(target)])
        elif choice == 2:
            try:
                written = config_mod.write_template(target)
            except ConfigError as exc:
                pager(win, "Config", [
                    str(exc), "",
                    "Choose 'Open it in your editor' to change the one you have.",
                ])
            else:
                pager(win, "Config", [f"Wrote {written}"])
        elif choice == 3:
            _edit_config(win, target)
        elif choice == 4:
            _check_config(win)


# --- status and integration ----------------------------------------------


def _do_status(win, cfg) -> None:
    pager(win, "Status", actions.status_report().all)


def _do_integrations(win, cfg) -> None:
    from .. import integrations

    report, found = actions.integrations_report()
    pager(win, "Desktop integration", report.all)
    if not found:
        return
    if not confirm(win, "Install for the detected file managers?"):
        return

    lines = []
    for name, info in found.items():
        try:
            integrations.install(name)
        except Exception as exc:  # noqa: BLE001 - report, keep going
            lines.append(f"FAILED {name}: {exc}")
        else:
            lines.append(f"Installed {name}: {info['path']}")
    if any(line.startswith("Installed") for line in lines):
        integrations.refresh()
        lines.append("If the entry does not appear, restart your file manager.")
    pager(win, "Desktop integration", lines)


_HANDLERS = (
    _do_preview,
    _do_organize,
    _do_organize_with_options,
    _do_undo_last,
    _do_undo_pick,
    _do_config,
    _do_status,
    _do_integrations,
)


def main(stdscr):
    curs_set(0)

    while True:
        # Reloaded each pass so editing the config is picked up without
        # restarting the menu.
        try:
            cfg = config_mod.load()
        except ConfigError as exc:
            pager(stdscr, "Config error", [str(exc)],
                  subtitle="the config file could not be read")
            if not confirm(stdscr, "Open the config in your editor to fix it?"):
                return 2
            _edit_config(stdscr, paths.config_file())
            continue

        choice = menu(stdscr, "File Organizer", MENU_ITEMS,
                      subtitle=actions.journal_summary())
        if choice is None or choice == QUIT:
            return 0

        _HANDLERS[choice](stdscr, cfg)
