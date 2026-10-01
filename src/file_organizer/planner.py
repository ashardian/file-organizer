"""Turn a folder into a Plan, without touching anything.

Conflict resolution against files already on disk is deliberately *not*
done here -- that belongs to the executor, which knows the conflict
policy. The planner only de-duplicates within a single run.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional, Set

from .models import Move, Plan, Skipped
from .rules import UnsafeDestination, is_incomplete, resolve_within, split_name


def skip_reason(item, skip_hidden=True, skip_incomplete=True,
                follow_links=False) -> Optional[str]:
    """Why `item` should be left alone, or None to organize it."""
    name = item.name
    # Check incomplete before hidden: LibreOffice locks are both, and
    # "in-progress download" is the more useful thing to report.
    if skip_incomplete and is_incomplete(name):
        return "in-progress"
    if skip_hidden and name.startswith("."):
        return "hidden"
    if item.is_symlink():
        # Followed links are moved as links, so a broken one or one that
        # points at a folder is as movable as any other.
        return None if follow_links else "symlink"
    if not item.is_file():
        return "unreadable"
    return None


def _dedupe_in_run(target_dir: Path, name: str, taken: Set[Path]) -> Path:
    """Avoid two sources in one run claiming the same destination."""
    candidate = target_dir / name
    if candidate not in taken:
        return candidate
    stem, suffix = split_name(name)
    counter = 1
    while True:
        candidate = target_dir / f"{stem}_{counter}{suffix}"
        if candidate not in taken:
            return candidate
        counter += 1


def build_plan(folder, rules, skip_hidden=True, skip_incomplete=True,
               follow_links=False, now=None) -> Plan:
    folder = Path(folder)
    now = time.time() if now is None else now

    plan = Plan(folder=folder)
    taken: Set[Path] = set()

    for item in sorted(folder.iterdir()):
        # Existing folders (including ones this tool created) are expected.
        if item.is_dir() and not item.is_symlink():
            continue

        reason = skip_reason(item, skip_hidden, skip_incomplete, follow_links)
        if reason:
            plan.skipped.append(Skipped(item, reason))
            continue

        try:
            # lstat: a broken link has no target to stat.
            stat_result = item.lstat()
        except OSError:
            plan.skipped.append(Skipped(item, "unreadable"))
            continue

        destination = rules.destination_for(item.name, stat_result, now)

        try:
            target_dir = resolve_within(folder, destination)
        except UnsafeDestination:
            # A rule sent this file outside the folder. Report it loudly
            # rather than writing there.
            plan.skipped.append(Skipped(item, "unsafe"))
            continue

        target = _dedupe_in_run(target_dir, item.name, taken)
        taken.add(target)
        plan.moves.append(Move(item, target))

    return plan
