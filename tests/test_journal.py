"""Tests for the move journal and undo."""
from __future__ import annotations

import time
import unittest

from support import TempDirCase

from file_organizer.executor import execute
from file_organizer.journal import Journal, new_run_id, undo_run, verify
from file_organizer.models import Move, Plan


class JournalCase(TempDirCase):
    def setUp(self):
        super().setUp()
        self.journal = Journal(self.tmp / "journal.jsonl")

    def run_once(self, names=("a.jpg", "b.pdf")):
        """Organize a couple of loose files, journaling as we go."""
        moves = []
        for name in names:
            source = self.make(name)
            moves.append(Move(source, self.tmp / "Sorted" / name))

        run_id = new_run_id()
        self.journal.record_run_start(run_id, self.tmp, "keep-both")
        result = execute(
            Plan(folder=self.tmp, moves=moves),
            journal=self.journal,
            run_id=run_id,
        )
        self.journal.record_run_end(run_id, len(result.moved), len(result.failed))
        return run_id, result


class JournalWriteTests(JournalCase):
    def test_moves_are_recorded(self):
        self.run_once()
        runs = self.journal.runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(len(runs[0]["moves"]), 2)

    def test_records_the_destination_paths(self):
        self.run_once()
        targets = {m["target"] for m in self.journal.runs()[0]["moves"]}
        self.assertEqual(
            targets,
            {str(self.tmp / "Sorted" / "a.jpg"),
             str(self.tmp / "Sorted" / "b.pdf")},
        )

    def test_torn_final_line_is_ignored(self):
        self.run_once()
        with open(self.journal.path, "a", encoding="utf-8") as handle:
            handle.write('{"type": "move", "run_id": "trunc')  # crash mid-write
        self.assertEqual(len(self.journal.runs()), 1)

    def test_empty_journal_has_no_runs(self):
        self.assertEqual(self.journal.runs(), [])


class UndoTests(JournalCase):
    def test_undo_restores_files(self):
        self.run_once()
        report = undo_run(self.journal, self.journal.runs()[0])

        self.assertEqual(report.touched, 2)
        self.assertTrue((self.tmp / "a.jpg").exists())
        self.assertTrue((self.tmp / "b.pdf").exists())
        self.assertFalse((self.tmp / "Sorted" / "a.jpg").exists())

    def test_undo_twice_does_not_re_reverse(self):
        self.run_once()
        undo_run(self.journal, self.journal.runs()[0])

        # The run is marked undone, so there is nothing left to undo.
        self.assertEqual(self.journal.runs(), [])
        self.assertTrue((self.tmp / "a.jpg").exists())

    def test_undo_skips_missing_files(self):
        self.run_once(names=("a.jpg",))
        (self.tmp / "Sorted" / "a.jpg").unlink()

        report = undo_run(self.journal, self.journal.runs()[0])

        self.assertEqual(report.touched, 0)
        self.assertEqual(len(report.missing), 1)

    def test_undo_refuses_to_reverse_a_changed_file(self):
        self.run_once(names=("a.jpg",))
        # Something edited the file after the run.
        (self.tmp / "Sorted" / "a.jpg").write_bytes(b"completely different")

        report = undo_run(self.journal, self.journal.runs()[0])

        self.assertEqual(report.touched, 0)
        self.assertEqual(len(report.changed), 1)
        self.assertFalse((self.tmp / "a.jpg").exists())

    def test_undo_does_not_clobber_a_replacement_at_the_original_path(self):
        self.run_once(names=("a.jpg",))
        # A new, different file took the original name while we were away.
        self.make("a.jpg", b"brand new file")

        report = undo_run(self.journal, self.journal.runs()[0])

        self.assertEqual(len(report.renamed), 1)
        self.assertEqual((self.tmp / "a.jpg").read_bytes(), b"brand new file")
        self.assertTrue((self.tmp / "a.restored1.jpg").exists())

    def test_dry_run_changes_nothing(self):
        self.run_once(names=("a.jpg",))
        report = undo_run(self.journal, self.journal.runs()[0], dry_run=True)

        self.assertEqual(report.touched, 1)
        self.assertTrue((self.tmp / "Sorted" / "a.jpg").exists())
        self.assertFalse((self.tmp / "a.jpg").exists())
        # Still undoable, because a dry run must not mark the run as undone.
        self.assertEqual(len(self.journal.runs()), 1)

    def test_partial_run_is_still_undoable(self):
        # Simulate a crash: the run started and one move completed, but no
        # "end" record was ever written.
        source = self.make("a.jpg")
        run_id = new_run_id()
        self.journal.record_run_start(run_id, self.tmp, "keep-both")
        execute(
            Plan(folder=self.tmp, moves=[Move(source, self.tmp / "Sorted" / "a.jpg")]),
            journal=self.journal,
            run_id=run_id,
        )

        runs = self.journal.runs()
        self.assertEqual(len(runs), 1)
        undo_run(self.journal, runs[0])
        self.assertTrue((self.tmp / "a.jpg").exists())

    def test_newest_run_is_undone_first(self):
        first_id, _ = self.run_once(names=("a.jpg",))
        second_id, _ = self.run_once(names=("b.pdf",))

        runs = self.journal.runs()
        self.assertEqual(runs[0]["run_id"], second_id)

        undo_run(self.journal, runs[0])
        self.assertTrue((self.tmp / "b.pdf").exists())
        # The older run is untouched.
        self.assertFalse((self.tmp / "a.jpg").exists())
        self.assertEqual([r["run_id"] for r in self.journal.runs()], [first_id])


class VerifyTests(JournalCase):
    def test_matching_file_passes(self):
        source = self.make("a.jpg")
        stat_result = source.stat()
        record = {
            "size": stat_result.st_size,
            "mtime": stat_result.st_mtime,
            "inode": stat_result.st_ino,
            "device": stat_result.st_dev,
        }
        self.assertIsNone(verify(record, source))

    def test_size_change_is_detected(self):
        source = self.make("a.jpg")
        stat_result = source.stat()
        record = {
            "size": stat_result.st_size + 1,
            "mtime": stat_result.st_mtime,
            "inode": None,
            "device": None,
        }
        self.assertEqual(verify(record, source), "changed")

    def test_missing_file_is_detected(self):
        record = {"size": 1, "mtime": time.time(), "inode": None, "device": None}
        self.assertEqual(verify(record, self.tmp / "nope"), "missing")


class TrimTests(JournalCase):
    def test_trim_keeps_the_newest_runs(self):
        for index in range(6):
            self.run_once(names=(f"f{index}.jpg",))

        self.assertEqual(len(self.journal.runs()), 6)
        self.journal.trim(keep_runs=2)
        self.assertEqual(len(self.journal.runs()), 2)


if __name__ == "__main__":
    unittest.main()
