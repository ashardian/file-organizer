"""Tests for the command-line front end."""
from __future__ import annotations

import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from support import TempDirCase

from file_organizer import cli


class ResolveFolderTests(TempDirCase):
    def test_ordinary_folder_is_accepted(self):
        self.assertEqual(cli.resolve_folder(self.tmp), self.tmp.resolve())

    def test_home_is_refused(self):
        with self.assertRaises(ValueError):
            cli.resolve_folder(Path.home())

    def test_root_is_refused(self):
        with self.assertRaises(ValueError):
            cli.resolve_folder("/")

    def test_missing_folder_is_refused(self):
        with self.assertRaises(ValueError):
            cli.resolve_folder(self.tmp / "nope")

    def test_tilde_expands_before_the_check(self):
        with self.assertRaises(ValueError):
            cli.resolve_folder("~")


class NormaliseTests(unittest.TestCase):
    def test_bare_folder_becomes_organize(self):
        self.assertEqual(cli._normalise(["/tmp/x"]), ["organize", "/tmp/x"])

    def test_leading_flag_becomes_organize(self):
        self.assertEqual(
            cli._normalise(["--dry-run"]), ["organize", "--dry-run"]
        )

    def test_known_command_is_left_alone(self):
        self.assertEqual(
            cli._normalise(["undo", "--list"]), ["undo", "--list"]
        )

    def test_help_is_left_alone(self):
        self.assertEqual(cli._normalise(["--help"]), ["--help"])


class CliCase(TempDirCase):
    """A test case whose config, state and downloads stay in the sandbox."""

    def setUp(self):
        super().setUp()
        # XDG_DATA_HOME matters as much as the others: the `trash` conflict
        # policy writes to ~/.local/share/Trash, and a test must not put
        # files in the user's real trash.
        for var, name in (("XDG_STATE_HOME", "state"),
                          ("XDG_CONFIG_HOME", "config"),
                          ("XDG_DATA_HOME", "data")):
            os.environ[var] = str(self.tmp / name)
            self.addCleanup(os.environ.pop, var, None)
        self.folder = self.tmp / "Downloads"
        self.folder.mkdir()

    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = cli.main(argv)
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()


class OrganizeCommandTests(CliCase):
    def test_dry_run_changes_nothing(self):
        self.make("Downloads/a.jpg")
        code, out, _ = self.run_cli(["organize", str(self.folder), "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("would be organized", out)
        self.assertTrue((self.folder / "a.jpg").exists())

    def test_organize_moves_the_file(self):
        self.make("Downloads/a.jpg")
        code, out, _ = self.run_cli(["organize", str(self.folder)])
        self.assertEqual(code, 0)
        self.assertIn("1 file(s) organized.", out)
        self.assertTrue((self.folder / "Images" / "a.jpg").exists())

    def test_root_is_refused(self):
        code, _, err = self.run_cli(["organize", "/"])
        self.assertEqual(code, 2)
        self.assertIn("refusing", err)

    def test_duplicate_sent_to_trash_is_reported(self):
        self.make("Downloads/Images/a.jpg", b"already here")
        self.make("Downloads/a.jpg", b"incoming")
        code, out, _ = self.run_cli(
            ["organize", str(self.folder), "--on-conflict", "trash"]
        )
        self.assertEqual(code, 0)
        self.assertIn("trash", out)

    def test_no_journal_means_nothing_to_undo(self):
        self.make("Downloads/a.jpg")
        self.run_cli(["organize", str(self.folder), "--no-journal"])
        code, out, _ = self.run_cli(["undo", "--list"])
        self.assertEqual(code, 0)
        self.assertIn("Nothing to undo.", out)


class UndoCommandTests(CliCase):
    def test_organize_then_undo_round_trip(self):
        self.make("Downloads/a.jpg")

        code, out, _ = self.run_cli(["organize", str(self.folder)])
        self.assertEqual(code, 0)
        self.assertTrue((self.folder / "Images" / "a.jpg").exists())

        code, out, _ = self.run_cli(["undo"])
        self.assertEqual(code, 0)
        self.assertTrue((self.folder / "a.jpg").exists())
        self.assertFalse((self.folder / "Images" / "a.jpg").exists())

    def test_undo_dry_run_leaves_the_file_where_it_is(self):
        self.make("Downloads/a.jpg")
        self.run_cli(["organize", str(self.folder)])

        code, out, _ = self.run_cli(["undo", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("Would restore", out)
        self.assertTrue((self.folder / "Images" / "a.jpg").exists())


class ConfigFailureTests(CliCase):
    def make_broken_config(self):
        directory = Path(os.environ["XDG_CONFIG_HOME"]) / "file-organizer"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "config.json").write_text("{not json", encoding="utf-8")

    def test_broken_config_exits_two_without_a_traceback(self):
        self.make_broken_config()
        code, _, err = self.run_cli(["organize", str(self.folder)])
        self.assertEqual(code, 2)
        self.assertIn("config error", err)

    def test_status_still_works_with_a_broken_config(self):
        # `status` reports where the config is, so it must not depend on it.
        self.make_broken_config()
        code, out, _ = self.run_cli(["status"])
        self.assertEqual(code, 0)
        self.assertIn("config:", out)


class StatusCommandTests(CliCase):
    def test_status_names_the_sandbox_journal(self):
        code, out, _ = self.run_cli(["status"])
        self.assertEqual(code, 0)
        self.assertIn(str(self.tmp / "state"), out)


class RulesCommandTests(CliCase):
    def test_describe_lists_categories(self):
        code, out, _ = self.run_cli(["rules"])
        self.assertEqual(code, 0)
        self.assertIn("Images", out)

    def test_path_prints_the_config_location(self):
        code, out, _ = self.run_cli(["rules", "--path"])
        self.assertEqual(code, 0)
        self.assertIn("config.json", out)

    def test_init_writes_a_template(self):
        code, out, _ = self.run_cli(["rules", "--init"])
        self.assertEqual(code, 0)
        self.assertIn("Wrote", out)

    def test_check_validates(self):
        self.run_cli(["rules", "--init"])
        code, out, _ = self.run_cli(["rules", "--check"])
        self.assertEqual(code, 0)
        self.assertIn("OK", out)


if __name__ == "__main__":
    unittest.main()
