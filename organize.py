#!/usr/bin/env python3
"""Deprecated entry point.

Kept so the previously documented `python3 organize.py FOLDER` keeps
working. Use the `file-organizer` command instead.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from file_organizer.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
