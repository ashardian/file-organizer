"""Line-based menu, for terminals curses cannot drive.

Reached when the terminal is dumb, TERM is unset, or output is piped. It
offers the same operations as the curses UI, and every result it prints
comes from `file_organizer.actions` -- which is what stops the two
frontends disagreeing about what a run did.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Optional

from .. import actions, config as config_mod, paths
from ..journal import Journal, JournalError
from ..rules import ConfigError
from .common import folder_choices, options_summary, run_label

MENU = (
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
QUIT = len(MENU)


def _ask(question, default=""):
    """One line of input, or None at end of input."""
    try:
        answer = input(question).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    return answer or default


def _report(report) -> None:
    for line in report.lines:
        print(line)
    for line in report.errors:
        print(line)


def _pause() -> None:
    print()
    _ask("Press enter to continue.")


def _load_config():
    """The config, or None once the problem has been reported.

    `main()` checks the config before the menu opens, but the user can edit
    it in another window while this one is sitting at the prompt, so every
    reload needs the same guard.
    """
    try:
        return config_mod.load()
    except ConfigError as exc:
        print(f"Config is not valid: {exc}")
        return None


# --- picking a folder ----------------------------------------------------


def _validated(raw) -> Optional[Path]:
    """Check a path the way the command line does, `/` and $HOME included."""
    try:
        return actions.resolve_folder(raw)
    except ValueError as exc:
        print(f"Not allowed: {exc}")
        return None


def _pick_folder() -> Optional[Path]:
    choices = folder_choices()
    print()
    for index, (label, _) in enumerate(choices, 1):
        print(f"  {index}. {label}")
    type_index = len(choices) + 1
    print(f"  {type_index}. Type a path...")
    print(f"  {type_index + 1}. Back")

    raw = _ask("Choose: ")
    if raw is None or raw == str(type_index + 1):
        return None
    if raw == str(type_index):
        answer = _ask(f"Folder path [{paths.downloads_dir()}]: ",
                      str(paths.downloads_dir()))
        return _validated(answer) if answer else None
    if raw.isdigit() and 1 <= int(raw) <= len(choices):
        return _validated(choices[int(raw) - 1][1])
    print("Not a choice.")
    return None


# --- organizing ----------------------------------------------------------


def _options_screen(cfg) -> Optional[dict]:
    """Adjust this run's options. Returns them, or None if cancelled.

    Nothing here writes to disk: the values live in a local dict and are
    discarded when the run ends, exactly as a command-line flag would be.
    """
    options = actions.default_options(cfg)
    print()
    print("Options for this run only; your config file is not changed.")

    while True:
        print()
        for index, field in enumerate(actions.OPTION_FIELDS, 1):
            value = actions.display_value(options, field)
            print(f"  {index}. {field.label}: {value}")
        print(f"  {len(actions.OPTION_FIELDS) + 1}. Done")

        raw = _ask("Change which (enter to finish)? ")
        if raw is None or raw == str(len(actions.OPTION_FIELDS) + 1) or raw == "":
            return options
        if not raw.isdigit() or not 1 <= int(raw) <= len(actions.OPTION_FIELDS):
            print("Not a choice.")
            continue
        options = actions.cycle_option(
            options, actions.OPTION_FIELDS[int(raw) - 1]
        )


def _run(folder, cfg, options, dry_run=False) -> None:
    plan = actions.build_plan_for(folder, cfg, options)
    summary = options_summary(options)

    if not plan.moves or dry_run:
        outcome = actions.OrganizeOutcome(folder, plan, dry_run=dry_run)
        _report(actions.organize_report(outcome))
        print(summary)
        _pause()
        return

    # Plan first, then the question: the same order the CLI uses.
    for line in actions.plan_preview(folder, plan):
        print(line)
    print(summary)

    answer = _ask(f"Move {len(plan.moves)} file(s)? [y/N] ")
    if answer is None or answer.lower() not in ("y", "yes"):
        print("Cancelled, nothing moved.")
        _pause()
        return

    outcome = actions.execute_plan(folder, plan, options)
    _report(actions.organize_report(outcome))
    _pause()


def _do_preview() -> None:
    cfg = _load_config()
    if cfg is None:
        return
    folder = _pick_folder()
    if folder is not None:
        _run(folder, cfg, actions.default_options(cfg), dry_run=True)


def _do_organize() -> None:
    cfg = _load_config()
    if cfg is None:
        return
    folder = _pick_folder()
    if folder is not None:
        _run(folder, cfg, actions.default_options(cfg))


def _do_organize_with_options() -> None:
    cfg = _load_config()
    if cfg is None:
        return
    options = _options_screen(cfg)
    if options is None:
        return
    folder = _pick_folder()
    if folder is not None:
        _run(folder, cfg, options)


# --- undo ----------------------------------------------------------------


def _load_runs():
    journal = Journal()
    try:
        return journal, journal.runs()
    except JournalError as exc:
        print(f"Cannot read the journal: {exc}")
        return journal, None


def _undo_screen(journal, run) -> None:
    _report(actions.undo_preview_report(run, journal))
    answer = _ask("Restore these files? [y/N] ")
    if answer is None or answer.lower() not in ("y", "yes"):
        return
    _report(actions.undo_report(run, journal))
    _pause()


def _do_undo_last() -> None:
    journal, runs = _load_runs()
    if runs is None:
        return
    if not runs:
        print("Nothing to undo.")
        return
    _undo_screen(journal, runs[0])


def _do_undo_pick() -> None:
    journal, runs = _load_runs()
    if runs is None:
        return
    if not runs:
        print("Nothing to undo.")
        return

    print()
    for index, run in enumerate(runs, 1):
        print(f"  {index}. {run_label(run)}")
    raw = _ask("Undo which? ")
    if raw is None or not raw.isdigit() or not 1 <= int(raw) <= len(runs):
        return
    _undo_screen(journal, runs[int(raw) - 1])


# --- configuration -------------------------------------------------------


def _check_config() -> None:
    cfg = _load_config()
    if cfg is None:
        return
    _report(actions.config_check_report(cfg, paths.config_file()))


def _edit_config(target) -> None:
    """Hand the terminal to $EDITOR, then re-check what comes back."""
    try:
        if not target.exists():
            config_mod.write_template(target)
        command = config_mod.editor_command(target)
        code = subprocess.call(command)
    except (OSError, ConfigError) as exc:
        print(f"cannot open the editor: {exc}")
        return
    if code != 0:
        print(f"{command[0]} exited with status {code}")
        return
    _check_config()


def _do_config() -> None:
    target = paths.config_file()
    while True:
        print()
        print(f"Configuration ({target})")
        for index, item in enumerate((
            "Show the effective configuration",
            "Show the config file path",
            "Write a starter config",
            "Open it in your editor",
            "Check it for errors",
            "Back",
        ), 1):
            print(f"  {index}. {item}")

        raw = _ask("Choose: ")
        if raw is None or raw == "6":
            return
        if raw == "1":
            cfg = _load_config()
            if cfg is not None:
                _report(actions.config_report(cfg, target))
            _pause()
        elif raw == "2":
            print(target)
            _pause()
        elif raw == "3":
            try:
                print(f"Wrote {config_mod.write_template(target)}")
            except ConfigError as exc:
                print(exc)
            _pause()
        elif raw == "4":
            _edit_config(target)
            _pause()
        elif raw == "5":
            _check_config()
            _pause()


# --- status and integration ----------------------------------------------


def _do_status() -> None:
    _report(actions.status_report())
    _pause()


def _do_integrations() -> None:
    from .. import integrations

    report, found = actions.integrations_report()
    _report(report)
    if not found:
        _pause()
        return

    answer = _ask("Install for the detected file managers? [y/N] ")
    if answer is None or answer.lower() not in ("y", "yes"):
        return

    installed = False
    for name, info in found.items():
        try:
            integrations.install(name)
        except Exception as exc:  # noqa: BLE001 - report, keep going
            print(f"FAILED {name}: {exc}")
        else:
            print(f"Installed {name}: {info['path']}")
            installed = True
    if installed:
        integrations.refresh()
        print("If the entry does not appear, restart your file manager.")
    _pause()


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


def main():
    while True:
        try:
            config_mod.load()
        except ConfigError as exc:
            # Without this the fallback died on a traceback where the curses
            # frontend showed a readable message. And quitting here would
            # lock the user out of the editor that fixes it.
            print(f"config error: {exc}", file=sys.stderr)
            answer = _ask("Open the config in your editor to fix it? [y/N] ")
            if answer is None or answer.lower() not in ("y", "yes"):
                return 2
            _edit_config(paths.config_file())
            continue

        print()
        for index, item in enumerate(MENU, 1):
            print(f"  {index}. {item}")
        print(f"  {actions.journal_summary()}")

        raw = _ask("Choose: ")
        if raw is None or raw.lower() == "q" or raw == str(QUIT):
            return 0
        if not raw.isdigit() or not 1 <= int(raw) <= len(MENU):
            print(f"Pick 1-{len(MENU)}.")
            continue
        _HANDLERS[int(raw) - 1]()
