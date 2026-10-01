"""Perform a plan: move files, apply the conflict policy, journal each move.

Per-file error handling matters more than it looks. A single unreadable or
locked file should not abandon the rest of the run, and because each
completed move is journaled as it happens, a run that stops halfway is
still fully undoable.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .models import Move, Plan
from .rules import split_name
from .trash import TrashError, trash as default_trash


def unique_target(target: Path) -> Path:
    """A free name next to `target`, never overwriting an existing file."""
    if not os.path.lexists(target):
        return target
    stem, suffix = split_name(target.name)
    counter = 1
    while True:
        candidate = target.parent / f"{stem}_{counter}{suffix}"
        if not os.path.lexists(candidate):
            return candidate
        counter += 1


@dataclass
class ExecResult:
    moved: List[Move] = field(default_factory=list)
    replaced: List[Move] = field(default_factory=list)
    trashed: List[Move] = field(default_factory=list)
    skipped: List[Tuple[Move, str]] = field(default_factory=list)
    failed: List[Tuple[Move, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed


def execute(plan: Plan, policy: str = "keep-both", journal=None,
            run_id: Optional[str] = None, keep_going: bool = True,
            trash_func: Optional[Callable] = None,
            on_move: Optional[Callable] = None) -> ExecResult:
    result = ExecResult()
    do_trash = trash_func or default_trash

    for move in plan.moves:
        try:
            stat_result = move.source.lstat()
        except OSError as exc:
            result.failed.append((move, str(exc)))
            if not keep_going:
                break
            continue

        device = stat_result.st_dev
        target = move.target
        # lexists, so a broken symlink in the way still counts as taken.
        existed = os.path.lexists(target)

        try:
            if existed:
                if policy == "skip":
                    result.skipped.append((move, "already exists"))
                    continue
                if policy == "trash":
                    # The incoming file is the duplicate. It goes to the trash
                    # and the file already in place is left untouched.
                    do_trash(move.source)
                    result.trashed.append(move)
                    continue
                if policy == "replace":
                    do_trash(target)
                elif policy == "keep-both":
                    target = unique_target(target)
        except (OSError, TrashError, shutil.Error) as exc:
            result.failed.append((move, str(exc)))
            if not keep_going:
                break
            continue

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(move.source), str(target))
        except (OSError, shutil.Error) as exc:
            result.failed.append((move, str(exc)))
            if not keep_going:
                break
            continue

        final = Move(move.source, target)
        result.moved.append(final)
        if existed and policy == "replace":
            result.replaced.append(final)

        # Recorded after the move, then fsynced. A crash in the gap between
        # the two would leave one move unjournaled; recording before the move
        # would instead journal moves that never happened.
        if journal is not None and run_id:
            try:
                journal.record_move(
                    run_id, move.source, target, stat_result, device
                )
            except OSError as exc:
                # The file has moved but undo will not know about it. Say
                # so, rather than dying with a traceback mid-run.
                result.failed.append((
                    final,
                    f"moved, but could not be recorded in the journal: {exc}",
                ))
                if not keep_going:
                    break
                continue

        if on_move is not None:
            on_move(final)

    return result
