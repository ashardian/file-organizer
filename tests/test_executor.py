"""Tests for conflict policies and move execution."""
from __future__ import annotations

import unittest

from support import TempDirCase

from file_organizer.executor import execute, unique_target
from file_organizer.models import Move, Plan


class FakeTrash:
    """Stands in for the real trash so tests don't touch ~/.local/share."""

    def __init__(self):
        self.trashed = []

    def __call__(self, path):
        self.trashed.append(path)
        path.unlink()


class UniqueTargetTests(TempDirCase):
    def test_free_name_is_returned_unchanged(self):
        target = self.tmp / "Images" / "a.jpg"
        self.assertEqual(unique_target(target), target)

    def test_counter_appended_on_clash(self):
        self.make("Images/a.jpg")
        self.assertEqual(
            unique_target(self.tmp / "Images" / "a.jpg"),
            self.tmp / "Images" / "a_1.jpg",
        )

    def test_compound_suffix_stays_intact(self):
        self.make("Archives/a.tar.gz")
        self.assertEqual(
            unique_target(self.tmp / "Archives" / "a.tar.gz"),
            self.tmp / "Archives" / "a_1.tar.gz",
        )


class ExecuteTests(TempDirCase):
    def plan_for(self, source, target):
        return Plan(folder=self.tmp, moves=[Move(source, target)])

    def test_plain_move(self):
        src = self.make("a.jpg")
        plan = self.plan_for(src, self.tmp / "Images" / "a.jpg")
        result = execute(plan, trash_func=FakeTrash())
        self.assertEqual(len(result.moved), 1)
        self.assertTrue((self.tmp / "Images" / "a.jpg").exists())
        self.assertFalse(src.exists())

    def test_keep_both_renames(self):
        self.make("Images/a.jpg", b"existing")
        src = self.make("a.jpg", b"incoming")
        plan = self.plan_for(src, self.tmp / "Images" / "a.jpg")

        result = execute(plan, policy="keep-both", trash_func=FakeTrash())

        self.assertEqual(len(result.moved), 1)
        self.assertEqual((self.tmp / "Images" / "a.jpg").read_bytes(), b"existing")
        self.assertEqual((self.tmp / "Images" / "a_1.jpg").read_bytes(), b"incoming")

    def test_skip_leaves_both_alone(self):
        self.make("Images/a.jpg", b"existing")
        src = self.make("a.jpg", b"incoming")
        plan = self.plan_for(src, self.tmp / "Images" / "a.jpg")

        result = execute(plan, policy="skip", trash_func=FakeTrash())

        self.assertEqual(result.moved, [])
        self.assertEqual(len(result.skipped), 1)
        self.assertTrue(src.exists())
        self.assertEqual((self.tmp / "Images" / "a.jpg").read_bytes(), b"existing")

    def test_replace_trashes_the_incumbent_not_the_newcomer(self):
        self.make("Images/a.jpg", b"existing")
        src = self.make("a.jpg", b"incoming")
        fake = FakeTrash()
        plan = self.plan_for(src, self.tmp / "Images" / "a.jpg")

        result = execute(plan, policy="replace", trash_func=fake)

        self.assertEqual(len(result.moved), 1)
        self.assertEqual(len(result.replaced), 1)
        self.assertEqual((self.tmp / "Images" / "a.jpg").read_bytes(), b"incoming")
        self.assertEqual(len(fake.trashed), 1)

    def test_trash_policy_trashes_the_incoming_duplicate(self):
        self.make("Images/a.jpg", b"existing")
        src = self.make("a.jpg", b"incoming")
        fake = FakeTrash()
        plan = self.plan_for(src, self.tmp / "Images" / "a.jpg")

        result = execute(plan, policy="trash", trash_func=fake)

        self.assertEqual(result.moved, [])
        self.assertEqual(len(result.trashed), 1)
        self.assertFalse(src.exists())
        self.assertEqual((self.tmp / "Images" / "a.jpg").read_bytes(), b"existing")

    def test_creates_nested_destination(self):
        src = self.make("a.jpg")
        plan = self.plan_for(src, self.tmp / "Photos" / "2024" / "03" / "a.jpg")
        execute(plan, trash_func=FakeTrash())
        self.assertTrue((self.tmp / "Photos" / "2024" / "03" / "a.jpg").exists())

    def test_failure_is_reported_and_others_continue(self):
        good = self.make("good.jpg")
        missing = self.tmp / "missing.jpg"
        plan = Plan(folder=self.tmp, moves=[
            Move(missing, self.tmp / "Images" / "missing.jpg"),
            Move(good, self.tmp / "Images" / "good.jpg"),
        ])

        result = execute(plan, trash_func=FakeTrash())

        self.assertEqual(len(result.failed), 1)
        self.assertEqual(len(result.moved), 1)
        self.assertTrue((self.tmp / "Images" / "good.jpg").exists())

    def test_stop_on_error_halts(self):
        missing = self.tmp / "missing.jpg"
        good = self.make("good.jpg")
        plan = Plan(folder=self.tmp, moves=[
            Move(missing, self.tmp / "Images" / "missing.jpg"),
            Move(good, self.tmp / "Images" / "good.jpg"),
        ])

        result = execute(plan, keep_going=False, trash_func=FakeTrash())

        self.assertEqual(len(result.failed), 1)
        self.assertEqual(result.moved, [])


if __name__ == "__main__":
    unittest.main()
