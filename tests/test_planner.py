"""Planner tests, including the behaviour carried over from v1.

The expectations in PlannerRegressionTests are the ones captured from a real
run of the original single-file script, so they guard the port: if these
change, the rewrite changed behaviour it was not meant to.
"""
from __future__ import annotations

import unittest

from support import TempDirCase

from file_organizer.models import Plan
from file_organizer.planner import build_plan, skip_reason
from file_organizer.rules import Rules


class PlannerCase(TempDirCase):
    def plan(self, rules=None, **kwargs):
        return build_plan(self.tmp, rules or Rules(), **kwargs)

    def destinations(self, plan):
        return {m.source.name: m.target.parent.name for m in plan.moves}


class PlannerRegressionTests(PlannerCase):
    """The exact mapping the v1 script produced, byte for byte."""

    def setUp(self):
        super().setUp()
        for name in ("app.deb", "Tool.AppImage", "ubuntu.iso", "backup.tar.xz",
                     "photo.jpg", "notes.pdf", "song.mp3", "script.py",
                     "movie.mp4", "data.xlsx", "font.ttf", "mystery.xyz"):
            self.make(name)

    def test_extensions_land_where_v1_put_them(self):
        found = self.destinations(self.plan())
        self.assertEqual(found["app.deb"], "Packages")
        self.assertEqual(found["Tool.AppImage"], "Packages")
        self.assertEqual(found["ubuntu.iso"], "DiskImages")
        self.assertEqual(found["backup.tar.xz"], "Archives")
        self.assertEqual(found["photo.jpg"], "Images")
        self.assertEqual(found["notes.pdf"], "Documents")
        self.assertEqual(found["song.mp3"], "Audio")
        self.assertEqual(found["script.py"], "Code")
        self.assertEqual(found["movie.mp4"], "Video")
        self.assertEqual(found["data.xlsx"], "Spreadsheets")
        self.assertEqual(found["font.ttf"], "Fonts")
        self.assertEqual(found["mystery.xyz"], "Other")

    def test_skipped_counter_matches_v1(self):
        self.make(".hidden_file")
        self.make("installer.part")
        (self.tmp / "link_to_photo.jpg").symlink_to(self.tmp / "photo.jpg")
        (self.tmp / "broken_link").symlink_to(self.tmp / "nowhere")

        counts = self.plan().skip_counts()
        self.assertEqual(counts.get("hidden"), 1)
        self.assertEqual(counts.get("in-progress"), 1)
        self.assertEqual(counts.get("symlink"), 2)

    def test_existing_folders_are_ignored(self):
        (self.tmp / "Images").mkdir()
        plan = self.plan()
        self.assertNotIn("Images", [m.source.name for m in plan.moves])

    def test_nothing_is_planned_for_an_empty_folder(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        self.assertEqual(build_plan(empty, Rules()).moves, [])


class SkipTests(PlannerCase):
    def test_skip_reason_priority(self):
        # A LibreOffice lock is both hidden and in-progress; the more useful
        # label is the one that gets reported.
        lock = self.make(".~lock.report.odt#")
        self.assertEqual(skip_reason(lock), "in-progress")

    def test_hidden_can_be_included(self):
        hidden = self.make(".hidden_file")
        self.assertIsNone(skip_reason(hidden, skip_hidden=False))

    def test_symlinks_can_be_followed(self):
        target = self.make("real.jpg")
        link = self.tmp / "link.jpg"
        link.symlink_to(target)
        self.assertEqual(skip_reason(link), "symlink")
        self.assertIsNone(skip_reason(link, follow_links=True))

    def test_directory_is_not_a_move_candidate(self):
        folder = self.tmp / "sub"
        folder.mkdir()
        self.assertEqual(skip_reason(folder), "unreadable")


class DestinationShapeTests(PlannerCase):
    def test_file_keeps_its_own_name_inside_the_destination(self):
        rules = Rules(rules=[{"match": {"ext": [".jpg"]}, "to": "Photos"}])
        self.make("holiday.jpg")
        plan = build_plan(self.tmp, rules)
        self.assertEqual(plan.moves[0].target,
                         self.tmp / "Photos" / "holiday.jpg")

    def test_a_template_renders_a_folder_not_a_filename(self):
        # "to" is always a directory; "{name}" drops the extension and so
        # builds a folder called "a", with each file keeping its own name
        # inside it. This is why two files in one run cannot collide - and
        # why the tool stays idempotent, since nothing is ever renamed.
        rules = Rules(rules=[
            {"match": {"ext": [".jpg", ".png"]}, "to": "All/{name}"},
        ])
        self.make("a.jpg")
        self.make("a.png")

        plan = build_plan(self.tmp, rules)
        targets = sorted(str(m.target.relative_to(self.tmp)) for m in plan.moves)

        self.assertEqual(targets, ["All/a/a.jpg", "All/a/a.png"])

    def test_run_dedupe_is_a_backstop_that_stays_unused(self):
        # planner._dedupe_in_run exists for two sources claiming one
        # destination, which cannot arise while the file keeps its name -
        # directory entries are unique. Asserted so a future change that
        # starts renaming files has to reckon with it.
        rules = Rules(rules=[
            {"match": {"ext": [".jpg", ".png"]}, "to": "All/{name}"},
        ])
        self.make("a.jpg")
        self.make("a.png")

        targets = [m.target for m in build_plan(self.tmp, rules).moves]
        self.assertEqual(len(targets), len(set(targets)))


class RuleDestinationTests(PlannerCase):
    def test_nested_destination_keeps_idempotency(self):
        rules = Rules(rules=[
            {"match": {"ext": [".jpg"]}, "to": "Photos/{year}"},
        ])
        source = self.make("a.jpg")
        plan = build_plan(self.tmp, rules)

        target = plan.moves[0].target
        self.assertEqual(target.parent.parent, self.tmp / "Photos")

        # Land it, then re-plan: the file is no longer loose, so a second run
        # must be a no-op. This is the invariant the old design got for free
        # and the rules engine has to preserve.
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)
        self.assertEqual(build_plan(self.tmp, rules).moves, [])

    def test_plan_remembers_the_folder(self):
        plan = self.plan()
        self.assertIsInstance(plan, Plan)
        self.assertEqual(plan.folder, self.tmp)


if __name__ == "__main__":
    unittest.main()
