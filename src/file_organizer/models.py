"""Shared data types.

Exists so planner, executor, journal, and cli can pass the same objects
around without importing each other.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List


@dataclass(frozen=True)
class Move:
    """One planned relocation of a file."""

    source: Path
    target: Path


@dataclass(frozen=True)
class Skipped:
    """Something left alone, and why."""

    path: Path
    reason: str


@dataclass
class Plan:
    """Everything a run intends to do, computed before anything moves."""

    folder: Path
    moves: List[Move] = field(default_factory=list)
    skipped: List[Skipped] = field(default_factory=list)

    def skip_counts(self):
        counts = {}
        for item in self.skipped:
            counts[item.reason] = counts.get(item.reason, 0) + 1
        return counts
