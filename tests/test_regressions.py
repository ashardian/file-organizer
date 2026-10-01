"""Regression tests for bugs found in review.

Each test names the behaviour that was wrong, so a failure reads as the
bug coming back rather than as an abstract assertion.
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

from support import TempDirCase

from file_organizer import actions, config as config_mod, integrations, paths
from file_organizer.executor import execute
from file_organizer.journal import Journal, new_run_id, undo_run
from file_organizer.models import Move, Plan
from file_organizer.planner import build_plan
from file_organizer.rules import ConfigError, Rules, split_name
from file_organizer.trash import trash
from test_cli import CliCase


class RuleEngineTests(unittest.TestCase):
    def test_tar_lzma_is_an_archive(self):
        # ".tar.lzma" was a compound suffix but missing from Archives, so
        # it fell through to "Other".
        self.assertEqual(Rules().destination_for("a.tar.lzma", None, 0),
                         "Archives")

    def test_ext_without_a_dot_still_matches(self):
        rules = Rules(rules=[{"match": {"ext": "jpg"}, "to": "Pics"}])
        self.assertEqual(rules.destination_for("a.jpg", mock.Mock(
            st_mtime=0, st_size=1), 0), "Pics")

    def test_category_ext_without_a_dot_still_matches(self):
        rules = Rules(categories={"Pics": ["jpg"]})
        self.assertEqual(rules.destination_for("a.jpg", None, 0), "Pics")

    def test_name_placeholder_agrees_with_split_name(self):
        rules = Rules(rules=[{"match": {"ext": [".tar.gz"]},
                              "to": "T/{name}"}])
        stat = mock.Mock(st_mtime=0, st_size=1)
        self.assertEqual(rules.destination_for("proj.tar.gz", stat, 0),
                         "T/" + split_name("proj.tar.gz")[0])

    def test_malformed_category_shapes_are_config_errors(self):
        # `categories or {}` used to swallow an empty list silently.
        for bad in ([], ["x"], {"Docs": 5}, {"Docs": None}):
            with self.subTest(bad=bad):
                with self.assertRaises(ConfigError):
                    Rules(categories=bad)


class ConfigValidationTests(unittest.TestCase):
    def test_non_object_top_level_is_rejected_even_when_empty(self):
        for bad in ([], "", 0):
            with self.subTest(bad=bad):
                with self.assertRaises(ConfigError):
                    config_mod.Config(bad)

    def test_use_default_categories_must_be_a_bool(self):
        # bool("false") is True, which silently kept the defaults.
        with self.assertRaises(ConfigError):
            config_mod.Config({"use_default_categories": "false"})

    def test_starter_template_does_not_capture_all_images(self):
        cfg = config_mod.Config(config_mod.TEMPLATE)
        stat = mock.Mock(st_mtime=1e12, st_size=1)
        self.assertEqual(cfg.rules.destination_for("a.png", stat, 1e12),
                         "Images")

    def test_editor_command_is_split_like_a_shell(self):
        with mock.patch.dict(os.environ, {"VISUAL": "code --wait"}):
            self.assertEqual(config_mod.editor_command("/x"),
                             ["code", "--wait", "/x"])


class DownloadsDirTests(unittest.TestCase):
    def test_empty_xdg_answer_is_not_the_current_directory(self):
        fake = mock.Mock(stdout="\n")
        with mock.patch("file_organizer.paths.subprocess.run",
                        return_value=fake):
            self.assertEqual(paths.downloads_dir(),
                             paths.Path.home() / "Downloads")


class SymlinkTests(TempDirCase):
    def test_followed_broken_link_is_planned_and_moved_as_a_link(self):
        link = self.tmp / "dangling"
        link.symlink_to(self.tmp / "nowhere")
        plan = build_plan(self.tmp, Rules(), follow_links=True)
        self.assertEqual([m.source for m in plan.moves], [link])

        result = execute(plan)
        self.assertTrue(result.ok, result.failed)
        self.assertTrue((self.tmp / "Other" / "dangling").is_symlink())

    def test_broken_link_in_the_way_counts_as_taken(self):
        self.make("a.jpg")
        (self.tmp / "Images").mkdir()
        (self.tmp / "Images" / "a.jpg").symlink_to(self.tmp / "nowhere")
        plan = build_plan(self.tmp, Rules())
        result = execute(plan, policy="keep-both")
        self.assertEqual(result.moved[0].target.name, "a_1.jpg")
        self.assertTrue((self.tmp / "Images" / "a.jpg").is_symlink())

    def test_trash_records_the_link_not_its_target(self):
        target = self.make("real.txt")
        link = self.tmp / "link.txt"
        link.symlink_to(target)
        root = self.tmp / "Trash"
        stored = trash(link, trash_root=root)
        info = (root / "info" / (stored.name + ".trashinfo")).read_text()
        self.assertIn(str(link), info.replace("%20", " "))
        self.assertNotIn("real.txt", info)


class UndoRetryTests(TempDirCase):
    def test_run_with_a_failed_restore_stays_undoable(self):
        journal = Journal(self.tmp / "journal.jsonl")
        src = self.make("a.jpg")
        plan = Plan(folder=self.tmp, moves=[Move(src, self.tmp / "I" / "a.jpg")])
        run_id = new_run_id()
        journal.record_run_start(run_id, self.tmp, "keep-both")
        execute(plan, journal=journal, run_id=run_id)

        run = journal.runs()[0]
        with mock.patch("file_organizer.journal.os.replace",
                        side_effect=OSError("nope")), \
                mock.patch("file_organizer.journal.shutil.move",
                           side_effect=OSError("nope")):
            report = undo_run(journal, run)
        self.assertTrue(report.failed)
        self.assertEqual(len(journal.runs()), 1,
                         "a failed undo must not strand the files")

        undo_run(journal, journal.runs()[0])
        self.assertEqual(journal.runs(), [])


class ReportWordingTests(unittest.TestCase):
    def test_plurals_are_real_words(self):
        text = actions.summarise({"unreadable": 2, "hidden": 1, "symlink": 3})
        self.assertIn("2 unreadable entries", text)
        self.assertIn("1 hidden file", text)
        self.assertIn("3 symlinks", text)
        self.assertNotIn("entrys", text)

    def test_preview_is_not_past_tense(self):
        # It is printed before anything has moved.
        plan = Plan(folder=paths.Path("/f"),
                    moves=[Move(paths.Path("/f/a.jpg"),
                                paths.Path("/f/Images/a.jpg"))])
        self.assertTrue(actions.plan_preview("/f", plan)[0]
                        .startswith("Will move"))


class IntegrationTests(TempDirCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, {"HOME": str(self.tmp),
                                               "XDG_DATA_HOME": ""})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_helper_installs_and_is_valid_shell(self):
        # str.format choked on the shell's ${1:-}, so nothing installed.
        import subprocess
        helper = integrations.install_helper()
        self.assertTrue(os.access(helper, os.X_OK))
        check = subprocess.run(["bash", "-n", str(helper)],
                               capture_output=True, text=True)
        self.assertEqual(check.returncode, 0, check.stderr)

    def test_every_manager_installs(self):
        for name in ("nemo", "nautilus", "caja", "dolphin", "thunar"):
            with self.subTest(name=name):
                self.assertTrue(integrations.install(name).exists())

    def test_reinstall_keeps_the_original_thunar_backup(self):
        uca = self.tmp / ".config" / "Thunar" / "uca.xml"
        uca.parent.mkdir(parents=True)
        original = "<actions></actions>"
        uca.write_text(original)
        integrations.install("thunar")
        integrations.install("thunar")
        self.assertEqual(uca.with_suffix(".xml.bak").read_text(), original)

    def test_thunar_is_not_installed_just_because_uca_xml_exists(self):
        uca = self.tmp / ".config" / "Thunar" / "uca.xml"
        uca.parent.mkdir(parents=True)
        uca.write_text("<actions></actions>")
        self.assertFalse(integrations._is_installed("thunar", uca))


class CliRegressionTests(CliCase):
    def test_undo_can_target_a_run_by_id(self):
        self.make("Downloads/a.jpg")
        self.run_cli(["organize", str(self.folder)])
        self.make("Downloads/b.pdf")
        self.run_cli(["organize", str(self.folder)])
        _, out, _ = self.run_cli(["undo", "--list"])
        older = out.splitlines()[-1].split()[0]

        code, _, _ = self.run_cli(["undo", older])
        self.assertEqual(code, 0)
        self.assertTrue((self.folder / "a.jpg").exists())
        self.assertTrue((self.folder / "Documents" / "b.pdf").exists())

    def test_undo_with_an_unknown_id_fails_cleanly(self):
        self.make("Downloads/a.jpg")
        self.run_cli(["organize", str(self.folder)])
        code, _, err = self.run_cli(["undo", "nope"])
        self.assertEqual(code, 1)
        self.assertIn("no undoable run", err)

    def test_edit_still_opens_a_broken_config(self):
        target = self.tmp / "config" / "file-organizer" / "config.json"
        target.parent.mkdir(parents=True)
        target.write_text("{ not json")
        with mock.patch("file_organizer.cli.subprocess.call",
                        return_value=0) as call:
            code, _, _ = self.run_cli(["rules", "--edit"])
        self.assertEqual(code, 0)
        self.assertEqual(call.call_args[0][0][-1], str(target))


if __name__ == "__main__":
    unittest.main()
