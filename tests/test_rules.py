"""Tests for categories, the rule engine, and destination safety."""
from __future__ import annotations

import time
import unittest

from support import TempDirCase

from file_organizer.rules import (
    ConfigError,
    Rules,
    UnsafeDestination,
    extension_of,
    is_incomplete,
    parse_size,
    resolve_within,
    split_name,
)


class SplitNameTests(unittest.TestCase):
    def test_compound_suffixes_are_one_unit(self):
        self.assertEqual(split_name("archive.tar.gz"), ("archive", ".tar.gz"))
        self.assertEqual(split_name("backup.tar.xz"), ("backup", ".tar.xz"))
        self.assertEqual(split_name("x.tar.zst"), ("x", ".tar.zst"))

    def test_single_suffix(self):
        self.assertEqual(split_name("photo.jpg"), ("photo", ".jpg"))

    def test_no_suffix(self):
        self.assertEqual(split_name("noextension"), ("noextension", ""))

    def test_lookalike_without_leading_dot_is_not_compound(self):
        # "tar.gz" is not ".tar.gz": the dot before "tar" is required.
        self.assertEqual(split_name("tar.gz"), ("tar", ".gz"))

    def test_suffix_case_is_preserved_but_matched_insensitively(self):
        self.assertEqual(split_name("ARCHIVE.TAR.GZ"), ("ARCHIVE", ".TAR.GZ"))
        self.assertEqual(extension_of("Photo.JPG"), ".jpg")


class IncompleteTests(unittest.TestCase):
    def test_download_artifacts(self):
        for name in ("a.part", "a.crdownload", "a.tmp", "a.opdownload"):
            self.assertTrue(is_incomplete(name), name)

    def test_libreoffice_lock(self):
        self.assertTrue(is_incomplete(".~lock.report.odt#"))

    def test_normal_file(self):
        self.assertFalse(is_incomplete("report.pdf"))


class ParseSizeTests(unittest.TestCase):
    def test_units(self):
        self.assertEqual(parse_size("1024"), 1024)
        self.assertEqual(parse_size("1KB"), 1024)
        self.assertEqual(parse_size("1.5MB"), 1572864)
        self.assertEqual(parse_size("2 GiB"), 2 * 1024 ** 3)

    def test_bad_input(self):
        with self.assertRaises(ConfigError):
            parse_size("large")
        with self.assertRaises(ConfigError):
            parse_size("10 furlongs")


class ResolveWithinTests(TempDirCase):
    def test_plain_name(self):
        target = resolve_within(self.tmp, "Images")
        self.assertEqual(target, self.tmp / "Images")

    def test_nested_template(self):
        target = resolve_within(self.tmp, "Photos/2024/03")
        self.assertEqual(target, self.tmp / "Photos" / "2024" / "03")

    def test_absolute_is_rejected(self):
        with self.assertRaises(UnsafeDestination):
            resolve_within(self.tmp, "/etc")

    def test_parent_escape_is_rejected(self):
        with self.assertRaises(UnsafeDestination):
            resolve_within(self.tmp, "../../.ssh")

    def test_escape_hidden_inside_path_is_rejected(self):
        with self.assertRaises(UnsafeDestination):
            resolve_within(self.tmp, "a/../../b")

    def test_empty_is_rejected(self):
        with self.assertRaises(UnsafeDestination):
            resolve_within(self.tmp, "   ")


class CategoryTests(unittest.TestCase):
    def setUp(self):
        self.rules = Rules()

    def destination(self, name, size=10, mtime=None):
        import os
        mtime = time.time() if mtime is None else mtime
        stat_result = os.stat_result((0, 0, 0, 0, 0, 0, size, mtime, mtime, 0))
        return self.rules.destination_for(name, stat_result, time.time())

    def test_known_extensions(self):
        self.assertEqual(self.destination("a.jpg"), "Images")
        self.assertEqual(self.destination("a.deb"), "Packages")
        self.assertEqual(self.destination("a.iso"), "DiskImages")
        self.assertEqual(self.destination("a.tar.xz"), "Archives")

    def test_case_insensitive(self):
        self.assertEqual(self.destination("TOOL.APPIMAGE"), "Packages")
        self.assertEqual(self.destination("PHOTO.JPG"), "Images")

    def test_unknown_falls_back(self):
        self.assertEqual(self.destination("mystery.xyz"), "Other")

    def test_user_category_beats_builtin(self):
        rules = Rules(categories={"WebPics": [".jpg"]})
        import os
        mtime = time.time()
        stat_result = os.stat_result((0, 0, 0, 0, 0, 0, 10, mtime, mtime, 0))
        self.assertEqual(
            rules.destination_for("a.jpg", stat_result, time.time()), "WebPics"
        )

    def test_defaults_can_be_disabled(self):
        rules = Rules(use_defaults=False)
        import os
        mtime = time.time()
        stat_result = os.stat_result((0, 0, 0, 0, 0, 0, 10, mtime, mtime, 0))
        self.assertEqual(
            rules.destination_for("a.jpg", stat_result, time.time()), "Other"
        )

    def test_bad_category_name_rejected(self):
        with self.assertRaises(ConfigError):
            Rules(categories={"../evil": [".x"]})
        with self.assertRaises(ConfigError):
            Rules(categories={"a/b": [".x"]})


class RuleTests(TempDirCase):
    def _stat(self, path):
        import os
        return os.stat(path)

    def test_ext_match(self):
        rules = Rules(rules=[{"match": {"ext": [".log"]}, "to": "Logs"}])
        path = self.make("a.log")
        self.assertEqual(
            rules.destination_for("a.log", self._stat(path), time.time()), "Logs"
        )

    def test_glob_match_is_case_insensitive(self):
        rules = Rules(rules=[{"match": {"glob": "*.bak"}, "to": "Backups"}])
        path = self.make("thing.BAK")
        self.assertEqual(
            rules.destination_for("thing.BAK", self._stat(path), time.time()),
            "Backups",
        )

    def test_regex_match(self):
        rules = Rules(rules=[{"match": {"regex": r"^IMG_\d+"}, "to": "Camera"}])
        path = self.make("IMG_1234.jpg")
        self.assertEqual(
            rules.destination_for("IMG_1234.jpg", self._stat(path), time.time()),
            "Camera",
        )

    def test_size_threshold(self):
        rules = Rules(rules=[{"match": {"size_gt": "1KB"}, "to": "Big"}])
        small = self.make("small.bin", b"x" * 10)
        big = self.make("big.bin", b"x" * 5000)
        self.assertEqual(
            rules.destination_for("small.bin", self._stat(small), time.time()),
            "Other",
        )
        self.assertEqual(
            rules.destination_for("big.bin", self._stat(big), time.time()), "Big"
        )

    def test_age_threshold(self):
        rules = Rules(
            rules=[{"match": {"older_than_days": 30}, "to": "Old"}]
        )
        old = self.make("old.txt", mtime=time.time() - 90 * 86400)
        new = self.make("new.txt")
        self.assertEqual(
            rules.destination_for("old.txt", self._stat(old), time.time()), "Old"
        )
        self.assertEqual(
            rules.destination_for("new.txt", self._stat(new), time.time()),
            "Documents",
        )

    def test_first_matching_rule_wins(self):
        rules = Rules(rules=[
            {"name": "first", "match": {"ext": [".log"]}, "to": "First"},
            {"name": "second", "match": {"ext": [".log"]}, "to": "Second"},
        ])
        path = self.make("a.log")
        self.assertEqual(
            rules.destination_for("a.log", self._stat(path), time.time()), "First"
        )

    def test_rules_beat_categories(self):
        rules = Rules(rules=[{"match": {"ext": [".jpg"]}, "to": "Camera"}])
        path = self.make("a.jpg")
        self.assertEqual(
            rules.destination_for("a.jpg", self._stat(path), time.time()), "Camera"
        )

    def test_date_placeholders_from_mtime(self):
        rules = Rules(rules=[{"match": {"ext": [".jpg"]}, "to": "{year}/{month}"}])
        # 2021-03-15 UTC-ish, computed from a fixed epoch day
        stamp = 1615800000
        path = self.make("a.jpg", mtime=stamp)
        expected = time.strftime("%Y/%m", time.localtime(stamp))
        self.assertEqual(
            rules.destination_for("a.jpg", self._stat(path), time.time()), expected
        )

    def test_unknown_match_key_is_an_error(self):
        with self.assertRaises(ConfigError):
            Rules(rules=[{"match": {"extention": [".x"]}, "to": "X"}])

    def test_missing_to_is_an_error(self):
        with self.assertRaises(ConfigError):
            Rules(rules=[{"match": {"ext": [".x"]}}])

    def test_bad_regex_is_an_error(self):
        with self.assertRaises(ConfigError):
            Rules(rules=[{"match": {"regex": "([unclosed"}, "to": "X"}])

    def test_absolute_destination_rejected_at_load(self):
        with self.assertRaises(ConfigError):
            Rules(rules=[{"match": {}, "to": "/etc/cron.d"}])

    def test_parent_escape_rejected_at_load(self):
        with self.assertRaises(ConfigError):
            Rules(rules=[{"match": {}, "to": "../../.ssh"}])

    def test_unknown_placeholder_is_an_error(self):
        rules = Rules(rules=[{"match": {"ext": [".a"]}, "to": "{nope}"}])
        path = self.make("a.a")
        with self.assertRaises(ConfigError):
            rules.destination_for("a.a", self._stat(path), time.time())


if __name__ == "__main__":
    unittest.main()
