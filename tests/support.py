"""Shared test scaffolding (no pytest: unittest only, stdlib only)."""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TempDirCase(unittest.TestCase):
    """A test case with a scratch directory that cleans up after itself."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="file-organizer-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def make(self, name, content=b"x", mtime=None):
        path = self.tmp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        if mtime is not None:
            import os
            os.utime(path, (mtime, mtime))
        return path
