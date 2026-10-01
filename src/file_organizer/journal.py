"""Append-only move journal, and undo.

One JSON line per *completed move*, not one record per run. That choice is
what makes undo safe rather than merely plausible: if a run dies halfway
through, the lines already flushed describe exactly what actually happened,
so undo reverses only real moves.

Undo verifies before it reverses. Each line records the size, mtime and
inode seen at move time; undo re-stats the destination and refuses to
reverse anything that has since changed or vanished. Inode only means
something on the same device, because shutil.move falls back to copy+delete
across filesystems, which allocates a new one.
"""
from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import paths
from .rules import split_name

#: Filesystem timestamp granularity varies; copy2 preserves mtime but not
#: always to the nanosecond on every filesystem.
MTIME_TOLERANCE = 2.0


def new_run_id() -> str:
    return f"{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"


def _unique_in(directory: Path, name: str) -> Path:
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem, suffix = split_name(name)
    counter = 1
    while True:
        candidate = directory / f"{stem}.restored{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


@dataclass
class UndoReport:
    run_id: str = ""
    restored: List[Tuple[Path, Path]] = field(default_factory=list)
    renamed: List[Tuple[Path, Path]] = field(default_factory=list)
    missing: List[Path] = field(default_factory=list)
    changed: List[Path] = field(default_factory=list)
    failed: List[Tuple[Path, str]] = field(default_factory=list)

    @property
    def touched(self) -> int:
        return len(self.restored) + len(self.renamed)


class JournalError(Exception):
    """Raised when the journal cannot be read or written."""


class Journal:
    def __init__(self, path=None):
        self.path = Path(path) if path else paths.journal_file()

    # -- writing ---------------------------------------------------------

    def _append(self, record: Dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, sort_keys=True) + "\n"
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    def record_run_start(self, run_id: str, folder, policy: str) -> None:
        self._append({
            "type": "run", "run_id": run_id, "time": time.time(),
            "folder": str(folder), "on_conflict": policy,
        })

    def record_move(self, run_id: str, source, target, stat_result,
                    device: Optional[int]) -> None:
        self._append({
            "type": "move", "run_id": run_id, "time": time.time(),
            "source": str(source), "target": str(target),
            "size": stat_result.st_size,
            "mtime": stat_result.st_mtime,
            "inode": stat_result.st_ino,
            "device": device,
        })

    def record_run_end(self, run_id: str, moved: int, failed: int) -> None:
        self._append({
            "type": "end", "run_id": run_id, "time": time.time(),
            "moved": moved, "failed": failed,
        })

    def record_undo(self, run_id: str) -> None:
        self._append({"type": "undo", "run_id": run_id, "time": time.time()})

    # -- reading ---------------------------------------------------------

    def read(self) -> List[Dict]:
        if not self.path.exists():
            return []
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise JournalError(f"cannot read {self.path}: {exc}")

        records = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                # A torn final line from a crash mid-write; skip it.
                continue
        return records

    def runs(self) -> List[Dict]:
        """Undoable runs, newest first."""
        records = self.read()
        undone = {r.get("run_id") for r in records if r.get("type") == "undo"}

        started: Dict[str, Dict] = {}
        for record in records:
            if record.get("type") == "run":
                started[record.get("run_id")] = record

        result = []
        for run_id, run in started.items():
            if run_id in undone:
                continue
            moves = [
                r for r in records
                if r.get("type") == "move" and r.get("run_id") == run_id
            ]
            if not moves:
                continue
            result.append({
                "run_id": run_id,
                "time": run.get("time") or 0,
                "folder": run.get("folder"),
                "moves": moves,
            })

        result.sort(key=lambda item: item["time"], reverse=True)
        return result

    def size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    # -- maintenance -----------------------------------------------------

    def trim(self, keep_runs: int = 20) -> None:
        """Rewrite the journal keeping only the newest N runs."""
        records = self.read()
        order: List[str] = []
        for record in records:
            run_id = record.get("run_id")
            if run_id and run_id not in order:
                order.append(run_id)
        keep = set(order[-keep_runs:])

        kept = [r for r in records if r.get("run_id") in keep]
        if len(kept) == len(records):
            return

        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        with open(temp, "w", encoding="utf-8") as handle:
            for record in kept:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, self.path)


def verify(record: Dict, target: Path) -> Optional[str]:
    """Why this move cannot be reversed, or None if it looks safe."""
    try:
        stat_result = target.lstat()
    except OSError:
        return "missing"

    if stat_result.st_size != record.get("size"):
        return "changed"

    recorded_mtime = record.get("mtime")
    if recorded_mtime is not None:
        if abs(stat_result.st_mtime - recorded_mtime) > MTIME_TOLERANCE:
            return "changed"

    # Inode only survives a same-device rename; across filesystems
    # shutil.move copies and allocates a new one, so it can't be checked.
    recorded_inode = record.get("inode")
    if recorded_inode is not None and record.get("device") is not None:
        if (stat_result.st_dev == record.get("device")
                and stat_result.st_ino != recorded_inode):
            return "changed"

    return None


def undo_run(journal: Journal, run: Dict, dry_run: bool = False) -> UndoReport:
    """Reverse one journaled run, newest move first."""
    report = UndoReport(run_id=run["run_id"])

    # Reverse order, so files that landed in the same folder unwind cleanly.
    for record in reversed(run["moves"]):
        target = Path(record["target"])
        source = Path(record["source"])

        problem = verify(record, target)
        if problem == "missing":
            report.missing.append(target)
            continue
        if problem == "changed":
            report.changed.append(target)
            continue

        destination = source
        if source.exists() or source.is_symlink():
            # Never clobber whatever took the original name while we were away.
            destination = _unique_in(source.parent, source.name)

        if dry_run:
            report.restored.append((target, destination))
            continue

        try:
            source.parent.mkdir(parents=True, exist_ok=True)
            os.replace(target, destination)
        except OSError:
            # Different filesystem: os.replace can't cross devices, so a
            # copy-and-delete move is the only option. That is not atomic,
            # hence the per-file error reporting below.
            try:
                shutil.move(str(target), str(destination))
            except (OSError, shutil.Error) as exc:
                report.failed.append((target, str(exc)))
                continue

        if destination != source:
            report.renamed.append((target, destination))
        else:
            report.restored.append((target, destination))

    # A run with failures stays listed, so the files that could not be
    # restored can be retried instead of being stranded.
    if not dry_run and not report.failed:
        journal.record_undo(report.run_id)

    return report
