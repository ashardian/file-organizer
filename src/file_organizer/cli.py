"""Command-line interface."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from . import __version__, actions
from . import config as config_mod, paths
from .journal import Journal, JournalError
from .rules import ConfigError, UnsafeDestination

# `resolve_folder` and `summarise` live in actions so the interactive UI
# refuses exactly the same targets; they are re-exported here because this
# has always been their import path.
from .actions import resolve_folder, summarise  # noqa: F401

KNOWN_COMMANDS = frozenset(
    {"organize", "undo", "rules", "status", "integrations", "menu"}
)

#: Flags that belong to the parser itself and must not be read as a folder.
GLOBAL_FLAGS = frozenset({"-h", "--help", "--version"})


def load_config(args):
    try:
        return config_mod.load(getattr(args, "config", None))
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        raise SystemExit(2)


def effective_options(cfg, args):
    options = dict(cfg.options)
    if getattr(args, "on_conflict", None):
        options["on_conflict"] = args.on_conflict
    if getattr(args, "include_hidden", False):
        options["skip_hidden"] = False
    if getattr(args, "include_incomplete", False):
        options["skip_incomplete"] = False
    if getattr(args, "follow_links", False):
        options["follow_links"] = True
    if getattr(args, "no_journal", False):
        options["journal"] = False
    return options


def _print_report(report):
    for line in report.lines:
        print(line)
    for line in report.errors:
        print(line, file=sys.stderr)


# --- organize ------------------------------------------------------------


def cmd_organize(args):
    cfg = load_config(args)
    options = effective_options(cfg, args)
    # Per-run only: not a config key, so it never reaches Config.
    options["stop_on_error"] = args.stop_on_error

    raw = args.folder if args.folder is not None else paths.downloads_dir()
    try:
        folder = resolve_folder(raw)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    plan = actions.build_plan_for(folder, cfg, options)

    if not plan.moves or args.dry_run:
        _print_report(actions.organize_report(
            actions.OrganizeOutcome(folder, plan, dry_run=args.dry_run)
        ))
        return 0

    for line in actions.plan_preview(folder, plan):
        print(line)

    if args.confirm and not args.yes and not _ask(len(plan.moves), folder):
        print("Cancelled, nothing moved.")
        return 0

    outcome = actions.execute_plan(folder, plan, options)
    _print_report(actions.organize_report(outcome))
    return 0 if outcome.result.ok else 1


def _ask(count, folder):
    try:
        answer = input(f"Move {count} file(s) in {folder}? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer.strip().lower() in {"y", "yes"}


# --- undo ----------------------------------------------------------------


def cmd_undo(args):
    journal = Journal()

    try:
        runs = journal.runs()
    except JournalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.list or not runs:
        _print_report(actions.run_list_report(runs))
        return 0

    run = runs[0]
    if args.run_id:
        matches = [r for r in runs if r["run_id"].startswith(args.run_id)]
        if len(matches) != 1:
            problem = "no undoable run matches" if not matches else (
                "more than one run matches")
            print(f"error: {problem} {args.run_id!r}; "
                  "see `file-organizer undo --list`", file=sys.stderr)
            return 1
        run = matches[0]

    if args.dry_run:
        _print_report(actions.undo_preview_report(run, journal))
        return 0

    report = actions.undo_report(run, journal)
    _print_report(report)
    return 0 if report.ok else 1


# --- rules / config ------------------------------------------------------


def cmd_rules(args):
    target = Path(args.config) if args.config else paths.config_file()

    if args.path:
        print(target)
        return 0

    if args.init:
        try:
            written = config_mod.write_template(target, overwrite=args.force)
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"Wrote {written}")
        return 0

    # Before the load below: a config that does not parse is exactly when
    # someone needs the editor, and `load` would refuse to get that far.
    if args.edit:
        try:
            if not target.exists():
                config_mod.write_template(target)
            command = config_mod.editor_command(target)
            return subprocess.call(command)
        except (OSError, ConfigError) as exc:
            print(f"error: cannot open the editor: {exc}", file=sys.stderr)
            return 1

    try:
        cfg = config_mod.load(target)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    if args.check:
        _print_report(actions.config_check_report(cfg, target))
        return 0

    # Default: describe the effective configuration.
    _print_report(actions.config_report(cfg, target))
    return 0


# --- status --------------------------------------------------------------


def cmd_status(args):
    _print_report(actions.status_report())
    return 0


# --- integrations --------------------------------------------------------


def cmd_integrations(args):
    from . import integrations

    report, found = actions.integrations_report()
    if not found:
        _print_report(report)
        return 0

    if not args.install:
        _print_report(report)
        return 0

    installed = 0
    for name, info in found.items():
        try:
            integrations.install(name)
            print(f"Installed {name}: {info['path']}")
            installed += 1
        except Exception as exc:  # noqa: BLE001 - report, don't abort the rest
            print(f"FAILED {name}: {exc}", file=sys.stderr)
    if installed:
        integrations.refresh()
        print("If the entry does not appear, restart your file manager.")
    return 0 if installed else 1


# --- parser --------------------------------------------------------------


def build_parser():
    parser = argparse.ArgumentParser(
        prog="file-organizer",
        description="Organize a folder's files into subfolders by type.",
    )
    parser.add_argument("--version", action="version",
                        version=f"file-organizer {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    organize = subparsers.add_parser(
        "organize", help="sort a folder's files into subfolders")
    organize.add_argument("folder", nargs="?", type=Path,
                          help="folder to organize (default: your Downloads)")
    organize.add_argument("--dry-run", action="store_true",
                          help="show what would happen")
    organize.add_argument("--confirm", action="store_true",
                          help="ask before moving anything")
    organize.add_argument("-y", "--yes", action="store_true",
                          help="do not ask, even with --confirm")
    organize.add_argument("--on-conflict", choices=config_mod.CONFLICT_POLICIES,
                          help="what to do when the destination exists")
    organize.add_argument("--include-hidden", action="store_true",
                          help="also organize dotfiles")
    organize.add_argument("--include-incomplete", action="store_true",
                          help="also organize in-progress downloads")
    organize.add_argument("--follow-links", action="store_true",
                          help="move symlinks instead of skipping them")
    organize.add_argument("--no-journal", action="store_true",
                          help="do not record this run (makes it un-undoable)")
    organize.add_argument("--stop-on-error", action="store_true",
                          help="abort at the first failure")
    organize.add_argument("--config", type=Path, help="use a specific config file")
    organize.set_defaults(func=cmd_organize)

    undo = subparsers.add_parser("undo", help="reverse a previous run")
    undo.add_argument("run_id", nargs="?", metavar="RUN_ID",
                      help="run to reverse (default: the most recent); "
                           "a unique prefix is enough")
    undo.add_argument("--list", action="store_true", help="list undoable runs")
    undo.add_argument("--dry-run", action="store_true",
                      help="show what would be restored")
    undo.set_defaults(func=cmd_undo)

    rules = subparsers.add_parser("rules", help="show or edit the configuration")
    rules.add_argument("--path", action="store_true", help="print the config path")
    rules.add_argument("--init", action="store_true", help="write a starter config")
    rules.add_argument("--edit", action="store_true", help="open it in $EDITOR")
    rules.add_argument("--check", action="store_true", help="validate it")
    rules.add_argument("--force", action="store_true",
                       help="with --init, overwrite an existing config")
    rules.add_argument("--config", type=Path, help="use a specific config file")
    rules.set_defaults(func=cmd_rules)

    status = subparsers.add_parser("status", help="show configuration and state")
    status.set_defaults(func=cmd_status)

    integrations = subparsers.add_parser(
        "integrations", help="manage desktop file-manager integration")
    integrations.add_argument("--install", action="store_true",
                              help="install integration for detected file managers")
    integrations.set_defaults(func=cmd_integrations)

    menu = subparsers.add_parser("menu", help="interactive menu")
    menu.set_defaults(func=_cmd_menu)

    return parser


def _cmd_menu(args):
    from . import tui
    return tui.run()


def _normalise(argv):
    """Let `file-organizer ~/Downloads` mean `organize ~/Downloads`.

    Flags too: `file-organizer --dry-run` is the documented way to preview
    your Downloads folder, so a leading flag means "organize", not "help".
    """
    if not argv:
        return argv
    first = argv[0]
    if first in KNOWN_COMMANDS or first in GLOBAL_FLAGS:
        return argv
    return ["organize"] + list(argv)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv:
        # Bare invocation: a menu on a real terminal, help otherwise.
        if sys.stdout.isatty() and sys.stdin.isatty():
            from . import tui
            try:
                return tui.run()
            except ConfigError as exc:
                # This branch used to sit outside the handler below, so a
                # malformed config escaped as a traceback.
                print(f"config error: {exc}", file=sys.stderr)
                return 2
            except KeyboardInterrupt:
                print("\nInterrupted.", file=sys.stderr)
                return 130
        build_parser().print_help()
        return 0

    parser = build_parser()
    args = parser.parse_args(_normalise(argv))

    if not getattr(args, "command", None):
        parser.print_help()
        return 0

    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    except UnsafeDestination as exc:
        print(f"unsafe destination: {exc}", file=sys.stderr)
        return 2
    except (JournalError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
