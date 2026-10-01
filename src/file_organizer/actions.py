"""Shared orchestration behind the CLI and the interactive UI.

Both frontends used to carry their own copy of this logic, which is why the
menu fell behind the command line: every flag added here had to be added
there too, and mostly wasn't. Keeping one implementation means a feature
cannot exist in the CLI and be missing from the UI.

Everything in this module is either pure or takes its collaborators as
arguments, so it can be exercised without a terminal attached.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import __version__, config as config_mod, paths, planner
from . import executor as executor_mod
from .executor import ExecResult
from .journal import Journal, JournalError, new_run_id, undo_run
from .models import Plan

#: (singular, plural). Spelled out because appending "s" turns
#: "unreadable entry" into "unreadable entrys".
SKIP_LABELS = {
    "in-progress": ("in-progress download", "in-progress downloads"),
    "symlink": ("symlink", "symlinks"),
    "hidden": ("hidden file", "hidden files"),
    "unreadable": ("unreadable entry", "unreadable entries"),
    "unsafe": ("unsafe destination", "unsafe destinations"),
}

#: The keys a config file understands. `stop_on_error` is deliberately not
#: among them: it is a per-run choice, and handing it to Config would raise.
CONFIG_OPTION_KEYS = frozenset(config_mod.DEFAULT_OPTIONS)


def summarise(counts) -> str:
    parts = []
    for reason, count in sorted(counts.items()):
        singular, plural = SKIP_LABELS.get(reason, (reason, reason))
        parts.append(f"{count} {singular if count == 1 else plural}")
    return ", ".join(parts)


def resolve_folder(raw):
    """Expand ~, resolve, and refuse targets that would be a disaster."""
    resolved = Path(os.path.expanduser(str(raw))).resolve()
    if not resolved.is_dir():
        raise ValueError(f"{raw} is not a folder")
    if resolved == Path.home().resolve():
        raise ValueError(
            "refusing to organize your entire home folder - pick a subfolder"
        )
    if resolved == Path(resolved.anchor):
        raise ValueError(f"refusing to organize {resolved}")
    return resolved


# --- the options a run is made of ----------------------------------------


@dataclass(frozen=True)
class OptionField:
    """One adjustable run option.

    `invert` records that the label states the opposite of the stored key:
    the menu offers "Include hidden files" while the config stores
    `skip_hidden`. Getting that backwards is the easy bug here, so the
    mapping lives in data rather than being re-derived in each frontend.
    """

    key: str
    label: str
    kind: str  # "choice" | "bool" | "run"
    help: str = ""
    invert: bool = False
    choices: Tuple[str, ...] = ()


OPTION_FIELDS = (
    OptionField(
        "on_conflict", "When the destination name is taken", "choice",
        help="what to do with a file that is already there",
        choices=config_mod.CONFLICT_POLICIES,
    ),
    OptionField(
        "skip_hidden", "Include hidden files", "bool", invert=True,
        help="dotfiles are left alone unless this is on",
    ),
    OptionField(
        "skip_incomplete", "Include in-progress downloads", "bool",
        invert=True,
        help="moving a file still being written can corrupt it",
    ),
    OptionField(
        "follow_links", "Move symlinks instead of skipping them", "bool",
        help="broken links included",
    ),
    OptionField(
        "journal", "Record the run so it can be undone", "bool",
        help="turn this off and the run is permanent",
    ),
    OptionField(
        "stop_on_error", "Stop at the first error", "run",
        help="by default the run continues past a failure",
    ),
)


def default_options(cfg) -> Dict:
    """The options a run starts from: the config's, plus per-run defaults."""
    options = dict(cfg.options)
    options.setdefault("stop_on_error", False)
    return options


def config_options(options) -> Dict:
    """Just the keys a config file understands; drops per-run-only ones."""
    return {k: v for k, v in options.items() if k in CONFIG_OPTION_KEYS}


def display_value(options, field: OptionField) -> str:
    """How a field reads on screen, accounting for inverted labels."""
    if field.kind == "choice":
        return str(options.get(field.key, "?"))
    stored = bool(options.get(field.key))
    enabled = not stored if field.invert else stored
    return "yes" if enabled else "no"


def cycle_option(options, field: OptionField) -> Dict:
    """Advance one field to its next value. Returns a new dict.

    Never mutates the input: the caller's original is what "cancel"
    restores, so an accidental write would silently make cancel a no-op.
    """
    updated = dict(options)
    if field.kind == "choice":
        try:
            index = field.choices.index(updated.get(field.key))
        except ValueError:
            index = -1
        updated[field.key] = field.choices[(index + 1) % len(field.choices)]
    else:
        updated[field.key] = not bool(updated.get(field.key))
    return updated


# --- organizing -----------------------------------------------------------


@dataclass
class Report:
    """Human-readable output.

    Split in two so the CLI can keep failures on stderr while the UI shows
    one flat list. A frontend that renders only `lines` drops the failures,
    so the two stay visibly separate rather than merged at the source.

    `ok` is the exit status the run deserves, which is not the same as
    "errors is empty": undo reports missing and changed files without
    failing, and only a genuine failure there is worth a non-zero exit.
    """

    lines: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    ok: bool = True

    @property
    def all(self) -> List[str]:
        return self.lines + self.errors


@dataclass
class OrganizeOutcome:
    folder: Path
    plan: Plan
    result: Optional[ExecResult] = None
    run_id: Optional[str] = None
    cancelled: bool = False
    dry_run: bool = False


def build_plan_for(folder, cfg, options) -> Plan:
    return planner.build_plan(
        folder,
        cfg.rules,
        skip_hidden=options["skip_hidden"],
        skip_incomplete=options["skip_incomplete"],
        follow_links=options["follow_links"],
    )


def plan_preview(folder, plan, dry_run=False) -> List[str]:
    """One line per planned move, shown before anything happens."""
    # Shown before anything moves, so never past tense.
    verb = "Would move" if dry_run else "Will move"
    return [
        f"{verb}: {move.source.name} -> {_relative_parent(move, folder)}/"
        for move in plan.moves
    ]


def execute_plan(folder, plan, options, journal_factory=Journal,
                 trash_func=None) -> OrganizeOutcome:
    """Carry out a plan. Assumes the caller has already confirmed it.

    `journal_factory` and `trash_func` are injectable so a caller can run a
    plan without touching the user's real journal or trash.
    """
    if not plan.moves:
        return OrganizeOutcome(folder=folder, plan=plan)

    journal = None
    run_id = None
    if options.get("journal", True):
        journal = journal_factory()
        run_id = new_run_id()
        journal.record_run_start(run_id, folder, options["on_conflict"])

    result = executor_mod.execute(
        plan,
        policy=options["on_conflict"],
        journal=journal,
        run_id=run_id,
        keep_going=not options.get("stop_on_error", False),
        trash_func=trash_func,
    )

    if journal is not None:
        journal.record_run_end(run_id, len(result.moved), len(result.failed))
        try:
            journal.trim()
        except (OSError, JournalError):
            pass

    return OrganizeOutcome(
        folder=folder, plan=plan, result=result, run_id=run_id
    )


def _relative_parent(move, folder) -> str:
    try:
        return str(move.target.parent.relative_to(folder))
    except ValueError:
        return str(move.target.parent)


def _skip_note(lines, plan) -> None:
    counts = plan.skip_counts()
    if counts:
        lines.append(f"Skipped: {summarise(counts)}.")


def organize_report(outcome: OrganizeOutcome) -> Report:
    """Render a run.

    Every outcome the executor can produce is named here. The older UI
    reported only moves and failures, so a `replace` or `trash` run looked
    like it had merely tidied up while actually discarding files.
    """
    plan = outcome.plan
    lines: List[str] = []
    errors: List[str] = []

    if outcome.cancelled:
        return Report(["Cancelled, nothing moved."])

    if not plan.moves:
        lines.append(f"Nothing to organize in {outcome.folder}.")
        lines.append("0 file(s) organized.")
        _skip_note(lines, plan)
        return Report(lines)

    if outcome.dry_run:
        lines.extend(plan_preview(outcome.folder, plan, dry_run=True))
        lines.append(f"{len(plan.moves)} file(s) would be organized.")
        _skip_note(lines, plan)
        return Report(lines)

    # An executed run does not repeat the move listing: both frontends show
    # that before asking for confirmation, and printing it twice reads as if
    # the files had moved a second time.
    result = outcome.result
    lines.append(f"{len(result.moved)} file(s) organized.")
    for move, why in result.skipped:
        lines.append(f"Left alone: {move.source.name} ({why}).")
    if result.trashed:
        lines.append(f"{len(result.trashed)} duplicate(s) sent to the trash.")
    if result.replaced:
        lines.append(
            f"{len(result.replaced)} existing file(s) replaced; the previous "
            f"versions are in the trash."
        )
    for move, why in result.failed:
        errors.append(f"FAILED {move.source.name}: {why}")

    _skip_note(lines, plan)
    if outcome.run_id and result.moved:
        lines.append(
            "Undo it with `file-organizer undo`, or from the menu."
        )
    return Report(lines, errors, ok=result.ok)


# --- undo -----------------------------------------------------------------


def run_list_report(runs) -> Report:
    if not runs:
        return Report(["Nothing to undo."])
    lines = []
    for run in runs:
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(run["time"]))
        lines.append(
            f"{run['run_id']}  {when}  {len(run['moves'])} file(s)  "
            f"{run['folder']}"
        )
    return Report(lines)


def undo_preview_report(run, journal: Journal) -> Report:
    report = undo_run(journal, run, dry_run=True)
    # Full paths here, unlike the real report: a preview is what you check
    # before agreeing, and two files called holiday.jpg would be ambiguous.
    lines = [f"Would restore: {d}" for _, d in report.restored]
    lines += [f"Gone, cannot restore: {t}" for t in report.missing]
    lines += [
        f"Changed since the run, leaving alone: {t}" for t in report.changed
    ]
    lines.append(f"{report.touched} file(s) would be restored.")
    return Report(lines)


def undo_report(run, journal: Journal) -> Report:
    report = undo_run(journal, run)
    lines = [f"Restored: {d.name}" for _, d in report.restored]
    lines += [f"Restored under a new name: {d.name}" for _, d in report.renamed]
    errors = [f"Gone, cannot restore: {t}" for t in report.missing]
    errors += [f"Changed since the run, left alone: {t}" for t in report.changed]
    errors += [f"FAILED {t}: {why}" for t, why in report.failed]
    lines.append(f"{report.touched} file(s) restored.")
    return Report(lines, errors, ok=not report.failed)


# --- config, status, integration -----------------------------------------


def config_report(cfg, target) -> Report:
    target = Path(target)
    lines = [
        f"config file: {target}"
        f"{'' if target.exists() else '  (not created yet; using defaults)'}",
        f"options: {cfg.options}",
        f"categories ({len(cfg.rules.categories)}):",
    ]
    for name, exts in cfg.rules.categories.items():
        lines.append(f"  {name}: {' '.join(sorted(exts))}")
    if cfg.rules.rules:
        lines.append(f"rules ({len(cfg.rules.rules)}), evaluated in order:")
        for rule in cfg.rules.rules:
            lines.append(f"  {rule.name} -> {rule.to}")
    else:
        lines.append("rules: none")
    return Report(lines)


def config_check_report(cfg, target) -> Report:
    return Report([
        f"{target}: OK",
        f"  {len(cfg.rules.rules)} rule(s), "
        f"{len(cfg.rules.categories)} categories",
    ])


def status_report() -> Report:
    from . import integrations

    cfg_path = paths.config_file()
    missing = "not created (using built-in defaults)"
    lines = [
        f"file-organizer {__version__}",
        "",
        f"config:  {cfg_path}",
        f"         {'exists' if cfg_path.exists() else missing}",
    ]

    journal = Journal()
    lines.append(f"journal: {journal.path}")
    try:
        runs = journal.runs()
        lines.append(
            f"         {journal.size()} bytes, {len(runs)} undoable run(s)"
        )
        if runs:
            last = runs[0]
            when = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(last["time"])
            )
            lines.append(f"         last run {when} in {last['folder']}")
    except JournalError as exc:
        lines.append(f"         unreadable: {exc}")

    lines.append("")
    lines.append("desktop integration:")
    found = integrations.detect()
    if not found:
        lines.append("  no supported file manager detected")
    for name, info in found.items():
        state = "installed" if info["installed"] else "not installed"
        lines.append(f"  {name}: {state}")
    return Report(lines)


def integrations_report() -> Tuple[Report, Dict]:
    """The detected file managers, and a report describing them."""
    from . import integrations

    found = integrations.detect()
    if not found:
        return Report(["No supported file manager detected."]), {}
    lines = [
        f"{name}: {'installed' if info['installed'] else 'available'} "
        f"({info['path']})"
        for name, info in found.items()
    ]
    lines.append("")
    pending = sorted(n for n, i in found.items() if not i["installed"])
    lines.append(
        f"Not yet installed: {', '.join(pending)}." if pending
        else "All detected file managers are already wired up."
    )
    return Report(lines), found


# --- status line ----------------------------------------------------------


def human_age(seconds) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def journal_summary() -> str:
    """One line for the UI footer: what is undoable, and how recently."""
    try:
        runs = Journal().runs()
    except JournalError as exc:
        return f"journal unreadable: {exc}"
    if not runs:
        return "nothing to undo yet"
    age = human_age(time.time() - runs[0]["time"])
    noun = "run" if len(runs) == 1 else "runs"
    return f"{len(runs)} undoable {noun} · last {age} ago"
