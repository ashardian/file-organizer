"""Tests for the interactive front end's shared logic.

The curses screens are not driven here -- a pty cannot be faked usefully --
so everything worth asserting was moved into `file_organizer.actions`,
which both the menu and the command line call.
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from support import TempDirCase

from file_organizer import actions, config as config_mod
from file_organizer.journal import Journal
from file_organizer.models import Move, Plan
from file_organizer.rules import ConfigError
from file_organizer.tui import can_use_curses


def field(key):
    return next(item for item in actions.OPTION_FIELDS if item.key == key)


class FakeTrash:
    """Stands in for the real trash so tests never touch ~/.local/share."""

    def __init__(self):
        self.trashed = []

    def __call__(self, path):
        self.trashed.append(path)
        path.unlink()


class CursesAvailabilityTests(unittest.TestCase):
    def test_piped_input_disables_curses(self):
        with mock.patch("sys.stdin") as stdin, mock.patch("sys.stdout") as out:
            stdin.isatty.return_value = False
            out.isatty.return_value = True
            self.assertFalse(can_use_curses())

    def test_dumb_terminal_disables_curses(self):
        with mock.patch("sys.stdin") as stdin, mock.patch("sys.stdout") as out:
            stdin.isatty.return_value = True
            out.isatty.return_value = True
            with mock.patch.dict(os.environ, {"TERM": "dumb"}):
                self.assertFalse(can_use_curses())


class OptionFieldTests(unittest.TestCase):
    """The menu labels state the opposite of the stored key; check that."""

    def test_hidden_label_is_inverted(self):
        options = {"skip_hidden": True}
        self.assertEqual(
            actions.display_value(options, field("skip_hidden")), "no"
        )
        options = actions.cycle_option(options, field("skip_hidden"))
        self.assertEqual(
            actions.display_value(options, field("skip_hidden")), "yes"
        )
        self.assertFalse(options["skip_hidden"])

    def test_incomplete_label_is_inverted(self):
        options = {"skip_incomplete": True}
        options = actions.cycle_option(options, field("skip_incomplete"))
        self.assertFalse(options["skip_incomplete"])
        self.assertEqual(
            actions.display_value(options, field("skip_incomplete")), "yes"
        )

    def test_journal_toggle(self):
        options = {"journal": True}
        options = actions.cycle_option(options, field("journal"))
        self.assertFalse(options["journal"])

    def test_stop_on_error_toggle(self):
        options = {"stop_on_error": False}
        options = actions.cycle_option(options, field("stop_on_error"))
        self.assertTrue(options["stop_on_error"])

    def test_conflict_policy_cycles_through_every_value(self):
        spec = field("on_conflict")
        options = {"on_conflict": config_mod.CONFLICT_POLICIES[0]}
        seen = [options["on_conflict"]]
        for _ in config_mod.CONFLICT_POLICIES:
            options = actions.cycle_option(options, spec)
            seen.append(options["on_conflict"])
        self.assertEqual(
            seen,
            list(config_mod.CONFLICT_POLICIES) + [config_mod.CONFLICT_POLICIES[0]],
        )

    def test_cycling_does_not_mutate_the_input(self):
        # Cancel has to be able to restore the original, so the input dict
        # must survive a cycle untouched.
        options = {"skip_hidden": True}
        actions.cycle_option(options, field("skip_hidden"))
        self.assertTrue(options["skip_hidden"])


class ConfigOptionTests(TempDirCase):
    def test_stop_on_error_is_not_a_config_key(self):
        options = actions.default_options(config_mod.Config({}))
        self.assertIn("stop_on_error", options)
        self.assertNotIn("stop_on_error", actions.config_options(options))

    def test_config_rejects_a_per_run_option(self):
        # Why config_options() has to exist at all.
        with self.assertRaises(ConfigError):
            config_mod.Config({"options": {"stop_on_error": True}})

    def test_cycling_options_never_writes_the_config(self):
        target = self.tmp / "config.json"
        target.write_text('{"options": {"on_conflict": "skip"}}',
                          encoding="utf-8")
        before = target.read_text(encoding="utf-8")

        cfg = config_mod.load(target)
        options = actions.default_options(cfg)
        for spec in actions.OPTION_FIELDS:
            options = actions.cycle_option(options, spec)

        self.assertEqual(target.read_text(encoding="utf-8"), before)
        self.assertEqual(cfg.options["on_conflict"], "skip")


class FolderGuardTests(TempDirCase):
    """The menu used to re-implement these checks and missed `/`."""

    def test_root_is_refused(self):
        with self.assertRaises(ValueError):
            actions.resolve_folder("/")

    def test_home_is_refused(self):
        with self.assertRaises(ValueError):
            actions.resolve_folder(Path.home())

    def test_ordinary_folder_is_accepted(self):
        self.assertEqual(actions.resolve_folder(self.tmp), self.tmp.resolve())

    def test_missing_folder_is_refused(self):
        with self.assertRaises(ValueError):
            actions.resolve_folder(self.tmp / "nope")


class OrganizeReportTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.folder = self.tmp / "Downloads"
        self.folder.mkdir()
        self.cfg = config_mod.Config({})

    def options(self, **over):
        options = actions.default_options(self.cfg)
        options["journal"] = False
        options.update(over)
        return options

    def outcome_for(self, **over):
        options = self.options(**over)
        plan = actions.build_plan_for(self.folder, self.cfg, options)
        return actions.execute_plan(
            self.folder, plan, options, trash_func=FakeTrash()
        )

    def test_dry_run_moves_nothing(self):
        self.make("Downloads/a.jpg")
        options = self.options()
        plan = actions.build_plan_for(self.folder, self.cfg, options)
        outcome = actions.OrganizeOutcome(self.folder, plan, dry_run=True)

        lines = actions.organize_report(outcome).all
        self.assertTrue(any("would be organized" in l for l in lines))
        self.assertTrue((self.folder / "a.jpg").exists())

    def test_conflict_skip_is_reported(self):
        self.make("Downloads/Images/a.jpg", b"already here")
        self.make("Downloads/a.jpg", b"incoming")

        lines = actions.organize_report(
            self.outcome_for(on_conflict="skip")
        ).all
        self.assertTrue(any("Left alone" in l for l in lines))
        self.assertTrue((self.folder / "a.jpg").exists())

    def test_conflict_trash_is_reported(self):
        self.make("Downloads/Images/a.jpg", b"already here")
        self.make("Downloads/a.jpg", b"incoming")

        lines = actions.organize_report(
            self.outcome_for(on_conflict="trash")
        ).all
        self.assertTrue(
            any("sent to the trash" in l for l in lines),
            f"a discarded duplicate went unreported: {lines}",
        )

    def test_conflict_replace_names_the_replacement(self):
        self.make("Downloads/Images/a.jpg", b"old")
        self.make("Downloads/a.jpg", b"new")

        lines = actions.organize_report(
            self.outcome_for(on_conflict="replace")
        ).all
        self.assertTrue(
            any("replaced" in l for l in lines),
            f"an overwritten file went unreported: {lines}",
        )

    def test_skipped_files_are_summarised(self):
        self.make("Downloads/note.part")
        self.make("Downloads/.hidden")
        self.make("Downloads/a.jpg")

        lines = actions.organize_report(self.outcome_for()).all
        self.assertTrue(any(l.startswith("Skipped:") for l in lines))

    def test_stop_on_error_aborts_at_the_first_failure(self):
        # Both sources are missing, so both moves would fail if the run
        # kept going.
        plan = Plan(folder=self.folder, moves=[
            Move(self.folder / "gone1.txt", self.folder / "Documents" / "g1"),
            Move(self.folder / "gone2.txt", self.folder / "Documents" / "g2"),
        ])

        stopped = actions.execute_plan(
            self.folder, plan, self.options(stop_on_error=True)
        )
        self.assertEqual(len(stopped.result.failed), 1)

        carried_on = actions.execute_plan(
            self.folder, plan, self.options(stop_on_error=False)
        )
        self.assertEqual(len(carried_on.result.failed), 2)

    def test_failures_are_kept_out_of_the_normal_lines(self):
        # The CLI prints `errors` to stderr, so a failure must not be
        # sitting in `lines` where it would land on stdout.
        plan = Plan(folder=self.folder, moves=[
            Move(self.folder / "gone.txt", self.folder / "Documents" / "g"),
        ])
        report = actions.organize_report(
            actions.execute_plan(self.folder, plan, self.options())
        )
        self.assertTrue(report.errors)
        self.assertFalse(any("FAILED" in l for l in report.lines))
        self.assertFalse(report.ok)


class UndoTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.folder = self.tmp / "Downloads"
        self.folder.mkdir()
        self.journal = Journal(self.tmp / "journal.jsonl")

    def record_one_move(self):
        target = self.make("Downloads/Images/a.jpg", b"x")
        source = self.folder / "a.jpg"
        stat_result = target.stat()
        self.journal.record_run_start("r1", self.folder, "keep-both")
        self.journal.record_move(
            "r1", source, target, stat_result, stat_result.st_dev
        )
        self.journal.record_run_end("r1", 1, 0)
        return source, target, self.journal.runs()[0]

    def test_preview_restores_nothing(self):
        source, target, run = self.record_one_move()

        report = actions.undo_preview_report(run, self.journal)
        self.assertTrue(any("Would restore" in l for l in report.all))
        self.assertTrue(target.exists())
        self.assertFalse(source.exists())

    def test_undo_puts_the_file_back(self):
        source, target, run = self.record_one_move()

        report = actions.undo_report(run, self.journal)
        self.assertTrue(report.ok)
        self.assertTrue(source.exists())
        self.assertFalse(target.exists())

    def test_missing_runs_report_nothing_to_do(self):
        self.assertEqual(actions.run_list_report([]).all, ["Nothing to undo."])


class ReportSmokeTests(TempDirCase):
    def setUp(self):
        super().setUp()
        # status_report reads the real journal path; keep it in the sandbox.
        os.environ["XDG_STATE_HOME"] = str(self.tmp / "state")
        self.addCleanup(os.environ.pop, "XDG_STATE_HOME", None)

    def test_config_report_lists_the_built_in_categories(self):
        cfg = config_mod.Config({})
        lines = actions.config_report(cfg, self.tmp / "config.json").all
        self.assertTrue(any("Images" in l for l in lines))
        self.assertTrue(any("rules: none" in l for l in lines))

    def test_config_check_reports_the_rule_count(self):
        cfg = config_mod.Config({})
        lines = actions.config_check_report(cfg, self.tmp / "c.json").all
        self.assertTrue(any("OK" in l for l in lines))

    def test_status_report_names_the_journal_and_config(self):
        lines = actions.status_report().all
        self.assertTrue(any("journal:" in l for l in lines))
        self.assertTrue(any("config:" in l for l in lines))

    def test_integrations_report_is_never_empty(self):
        report, found = actions.integrations_report()
        self.assertTrue(report.all)
        self.assertIsInstance(found, dict)

    def test_journal_summary_without_a_journal(self):
        self.assertEqual(actions.journal_summary(), "nothing to undo yet")

    def test_human_age_scales(self):
        self.assertEqual(actions.human_age(30), "30s")
        self.assertEqual(actions.human_age(600), "10m")
        self.assertEqual(actions.human_age(7200), "2h")
        self.assertEqual(actions.human_age(172800), "2d")


if __name__ == "__main__":
    unittest.main()
