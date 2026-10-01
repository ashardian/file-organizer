"""Tests for the XDG trash implementation."""
from __future__ import annotations

import unittest
import urllib.parse
from unittest import mock

from support import TempDirCase

from file_organizer import trash as trash_module
from file_organizer.trash import TrashError, trash


class TrashCase(TempDirCase):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / "Trash"
        self.work = self.tmp / "work"
        self.work.mkdir()

    def put(self, name, content=b"payload"):
        path = self.work / name
        path.write_bytes(content)
        return path

    def sidecar(self, stored):
        return self.root / "info" / (stored.name + ".trashinfo")

    def fields(self, stored):
        text = self.sidecar(stored).read_text(encoding="utf-8")
        result = {}
        for line in text.splitlines()[1:]:
            key, _, value = line.partition("=")
            result[key] = value
        return result


class TrashLayoutTests(TrashCase):
    def test_file_lands_in_the_files_directory(self):
        source = self.put("a.jpg")
        stored = trash(source, trash_root=self.root)

        self.assertEqual(stored.parent, self.root / "files")
        self.assertEqual(stored.read_bytes(), b"payload")
        self.assertFalse(source.exists())

    def test_sidecar_is_written_next_to_the_index(self):
        source = self.put("a.jpg")
        stored = trash(source, trash_root=self.root)

        info = self.sidecar(stored)
        self.assertTrue(info.exists())
        self.assertTrue(info.read_text(encoding="utf-8").startswith("[Trash Info]\n"))

    def test_trash_directories_are_private(self):
        import os
        import stat

        trash(self.put("a.jpg"), trash_root=self.root)

        for name in ("files", "info"):
            mode = stat.S_IMODE(os.stat(self.root / name).st_mode)
            self.assertEqual(mode, 0o700, name)

    def test_directory_records_its_original_absolute_path(self):
        source = self.put("a.jpg")
        stored = trash(source, trash_root=self.root)

        recorded = self.fields(stored)["Path"]
        self.assertEqual(urllib.parse.unquote(recorded), str(source.resolve()))

    def test_awkward_characters_are_percent_encoded(self):
        source = self.put("my file #1.jpg")
        stored = trash(source, trash_root=self.root)

        recorded = self.fields(stored)["Path"]
        self.assertNotIn(" ", recorded)
        self.assertNotIn("#", recorded)
        self.assertEqual(urllib.parse.unquote(recorded), str(source.resolve()))

    def test_deletion_date_is_local_time_without_a_zone(self):
        import re

        stored = trash(self.put("a.jpg"), trash_root=self.root)
        stamp = self.fields(stored)["DeletionDate"]

        self.assertRegex(stamp, re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$"))


class TrashCollisionTests(TrashCase):
    def test_name_collision_gets_a_suffix(self):
        first = trash(self.put("a.jpg", b"one"), trash_root=self.root)
        second = trash(self.put("a.jpg", b"two"), trash_root=self.root)

        self.assertEqual(first.name, "a.jpg")
        self.assertEqual(second.name, "a.jpg.2")
        self.assertEqual((self.root / "files" / "a.jpg").read_bytes(), b"one")
        self.assertEqual(second.read_bytes(), b"two")

    def test_sidecar_name_follows_the_renamed_file(self):
        trash(self.put("a.jpg"), trash_root=self.root)
        second = trash(self.put("a.jpg"), trash_root=self.root)

        # A sidecar keyed on the original name would describe the wrong file,
        # so the second one has to carry the ".2" too.
        self.assertTrue(self.sidecar(second).exists())
        self.assertTrue((self.root / "info" / "a.jpg.trashinfo").exists())
        self.assertTrue((self.root / "info" / "a.jpg.2.trashinfo").exists())


class TrashErrorTests(TrashCase):
    def test_missing_file_is_refused(self):
        with self.assertRaises(TrashError):
            trash(self.work / "nope.jpg", trash_root=self.root)

    def test_no_sidecar_is_left_when_the_move_fails(self):
        source = self.put("a.jpg")
        with mock.patch.object(trash_module.shutil, "move", side_effect=OSError("boom")):
            with self.assertRaises(TrashError):
                trash(source, trash_root=self.root)

        self.assertEqual(list((self.root / "info").iterdir()), [])
        self.assertTrue(source.exists())


class CrossDeviceTests(TrashCase):
    def test_foreign_filesystem_goes_to_gio_instead(self):
        source = self.put("a.jpg")
        with mock.patch.object(trash_module, "_same_device", return_value=False):
            with mock.patch.object(
                trash_module, "_gio_trash", return_value=source
            ) as gio:
                trash(source, trash_root=self.root)

        gio.assert_called_once()
        # The home trash must not have been touched.
        self.assertFalse((self.root / "files" / "a.jpg").exists())

    def test_gio_failure_is_reported(self):
        source = self.put("a.jpg")
        with mock.patch.object(trash_module, "_same_device", return_value=False):
            with mock.patch.object(
                trash_module, "_gio_trash", side_effect=TrashError("no gio")
            ):
                with self.assertRaises(TrashError):
                    trash(source, trash_root=self.root)


if __name__ == "__main__":
    unittest.main()
