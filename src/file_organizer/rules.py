"""Extension categories and the user-rule engine.

The built-in category table and the suffix/skip helpers are carried over
unchanged from the version verified against Linux Mint 22.3, so existing
behaviour is preserved. The rule engine and the path-safety check are new.
"""
from __future__ import annotations

import fnmatch
import re
import time
from pathlib import Path
from typing import Any, Dict

FALLBACK_CATEGORY = "Other"

# --- built-in categories -------------------------------------------------

DEFAULT_CATEGORIES = {
    "Images": {
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp",
        ".tif", ".tiff", ".heic", ".heif", ".avif", ".ico", ".xcf",
    },
    "Documents": {
        ".pdf", ".doc", ".docx", ".txt", ".md", ".odt", ".rtf", ".tex",
        ".epub", ".mobi", ".azw3", ".djvu", ".ppt", ".pptx", ".odp",
    },
    "Spreadsheets": {".xls", ".xlsx", ".csv", ".ods", ".tsv"},
    "Archives": {
        ".zip", ".tar", ".gz", ".7z", ".rar", ".xz", ".bz2", ".zst",
        ".tgz", ".tbz2", ".lz4", ".lzma",
        ".tar.gz", ".tar.xz", ".tar.bz2", ".tar.zst", ".tar.lz4", ".tar.lzma",
    },
    "Audio": {
        ".mp3", ".wav", ".flac", ".m4a", ".ogg", ".opus", ".aac",
        ".wma", ".m4b",
    },
    "Video": {
        ".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".wmv",
        ".m4v", ".mpg", ".mpeg", ".srt", ".vtt", ".ass",
    },
    "Code": {
        ".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".scss",
        ".java", ".c", ".h", ".cpp", ".hpp", ".cs", ".go", ".rs", ".rb",
        ".php", ".pl", ".lua", ".r", ".sh", ".bash", ".zsh", ".fish",
        ".json", ".yaml", ".yml", ".toml", ".xml", ".sql", ".ini", ".cfg",
    },
    "Packages": {".deb", ".rpm", ".appimage", ".flatpak", ".snap", ".msi"},
    "DiskImages": {".iso", ".img"},
    "Fonts": {".ttf", ".otf", ".woff", ".woff2"},
}

# Multi-part suffixes matched as a unit, otherwise Path.suffix only sees the
# trailing ".gz" and ".tar.xz" ends up uncategorized.
COMPOUND_SUFFIXES = (
    ".tar.gz", ".tar.xz", ".tar.bz2", ".tar.zst", ".tar.lz4", ".tar.lzma",
)

# Files still being written. Moving these can corrupt an in-flight download.
INCOMPLETE_SUFFIXES = (
    ".part", ".crdownload", ".download", ".opdownload", ".partial",
    ".filepart", ".tmp",
)
INCOMPLETE_PREFIXES = (".~lock.",)


class ConfigError(ValueError):
    """A configuration problem the user needs to fix."""


class UnsafeDestination(ValueError):
    """A destination that would write outside the folder being organized."""


def split_name(name: str):
    """Split a filename into (stem, suffix), treating ".tar.gz" as one suffix."""
    lowered = name.lower()
    for suffix in COMPOUND_SUFFIXES:
        if lowered.endswith(suffix):
            cut = len(name) - len(suffix)
            return name[:cut], name[cut:]
    suffix = Path(name).suffix
    return name[: len(name) - len(suffix)], suffix


def extension_of(name: str) -> str:
    return split_name(name)[1].lower()


def normalise_ext(value: Any) -> str:
    """Lower-case an extension and make sure it has its leading dot.

    Config files get written by hand, and "jpg" is an easy thing to type
    where ".jpg" was meant. Left alone it would never match anything, with
    no error to say why.
    """
    text = str(value).strip().lower()
    if text and not text.startswith("."):
        text = "." + text
    return text


def is_incomplete(name: str) -> bool:
    return name.lower().endswith(INCOMPLETE_SUFFIXES) or name.startswith(
        INCOMPLETE_PREFIXES
    )


_SIZE_UNITS = {
    "": 1, "b": 1,
    "k": 1024, "kb": 1024, "kib": 1024,
    "m": 1024 ** 2, "mb": 1024 ** 2, "mib": 1024 ** 2,
    "g": 1024 ** 3, "gb": 1024 ** 3, "gib": 1024 ** 3,
    "t": 1024 ** 4, "tb": 1024 ** 4, "tib": 1024 ** 4,
}


def parse_size(value: Any) -> int:
    """Parse "10MB", "1.5 GiB", or a plain byte count."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([A-Za-z]*)\s*", str(value))
    if not match:
        raise ConfigError(f"not a size: {value!r} (try \"10MB\")")
    number, unit = match.group(1), match.group(2).lower()
    if unit not in _SIZE_UNITS:
        raise ConfigError(f"unknown size unit {unit!r} in {value!r}")
    return int(float(number) * _SIZE_UNITS[unit])


def resolve_within(folder: Path, relative: Any) -> Path:
    """Resolve `relative` under `folder`, refusing anything that escapes it.

    Category names and rule destinations come from a user-editable file, so
    "../../.ssh" or an absolute "/etc" would otherwise be a real
    write-outside-the-folder bug. Nothing hardcoded had this problem.
    """
    text = str(relative).strip()
    if not text:
        raise UnsafeDestination("empty destination")

    candidate = Path(text)
    # Path("/a") / "/etc" == Path("/etc"), so absolute must be rejected first.
    if candidate.is_absolute():
        raise UnsafeDestination(f"absolute destination is not allowed: {text!r}")
    if ".." in candidate.parts:
        raise UnsafeDestination(f"destination escapes the folder: {text!r}")

    folder_resolved = Path(folder).resolve()
    target = folder / candidate
    resolved = target.resolve()
    if resolved != folder_resolved and folder_resolved not in resolved.parents:
        raise UnsafeDestination(f"destination escapes the folder: {text!r}")
    return target


def _check_name(name: str, what: str) -> None:
    if not name or name in (".", ".."):
        raise ConfigError(f"invalid {what} name: {name!r}")
    if "/" in name or "\\" in name or "\0" in name:
        raise ConfigError(f"{what} name may not contain a path separator: {name!r}")


class Rule:
    """One ordered match rule from the config file."""

    #: match keys we understand; anything else is a config typo worth failing on
    MATCH_KEYS = frozenset(
        {"ext", "glob", "regex", "older_than_days", "newer_than_days",
         "size_gt", "size_lt"}
    )

    def __init__(self, spec: Dict[str, Any], index: int):
        if not isinstance(spec, dict):
            raise ConfigError(f"rule {index + 1} must be an object")
        self.name = str(spec.get("name") or f"rule {index + 1}")

        match = spec.get("match") or {}
        if not isinstance(match, dict):
            raise ConfigError(f"{self.name}: 'match' must be an object")
        unknown = set(match) - self.MATCH_KEYS
        if unknown:
            raise ConfigError(
                f"{self.name}: unknown match key(s) {sorted(unknown)}; "
                f"known keys are {sorted(self.MATCH_KEYS)}"
            )

        raw_ext = match.get("ext") or []
        if isinstance(raw_ext, str):
            raw_ext = [raw_ext]
        if not isinstance(raw_ext, (list, tuple, set)):
            raise ConfigError(f"{self.name}: 'ext' must be a string or a list")
        self.ext = {normalise_ext(item) for item in raw_ext}

        self.glob = match.get("glob")
        if self.glob is not None:
            self.glob = str(self.glob).lower()

        self.regex = None
        if match.get("regex") is not None:
            try:
                self.regex = re.compile(str(match["regex"]))
            except re.error as exc:
                raise ConfigError(f"{self.name}: bad regex: {exc}")

        self.older_than = self._days(match, "older_than_days")
        self.newer_than = self._days(match, "newer_than_days")
        self.size_gt = parse_size(match["size_gt"]) if "size_gt" in match else None
        self.size_lt = parse_size(match["size_lt"]) if "size_lt" in match else None

        self.to = spec.get("to")
        if not self.to:
            raise ConfigError(f"{self.name}: missing 'to'")

        # Static check so an obviously wrong destination fails at load time
        # rather than once per file. resolve_within() backstops this at runtime.
        template = str(self.to)
        if Path(template).is_absolute() or ".." in Path(template).parts:
            raise ConfigError(
                f"{self.name}: 'to' must be a path inside the folder, "
                f"not {template!r}"
            )

    def _days(self, match, key):
        if key not in match:
            return None
        try:
            return float(match[key])
        except (TypeError, ValueError):
            raise ConfigError(f"{self.name}: {key} must be a number of days")

    def matches(self, name: str, stat_result, now: float) -> bool:
        if self.ext and extension_of(name) not in self.ext:
            return False
        if self.glob and not fnmatch.fnmatch(name.lower(), self.glob):
            return False
        if self.regex and not self.regex.search(name):
            return False

        if self.older_than is not None or self.newer_than is not None:
            age_days = (now - stat_result.st_mtime) / 86400.0
            if self.older_than is not None and age_days < self.older_than:
                return False
            if self.newer_than is not None and age_days > self.newer_than:
                return False

        if self.size_gt is not None and stat_result.st_size <= self.size_gt:
            return False
        if self.size_lt is not None and stat_result.st_size >= self.size_lt:
            return False
        return True

    def destination(self, name: str, stat_result) -> str:
        """Render the destination path template against this file."""
        values = {
            "year": time.strftime("%Y", time.localtime(stat_result.st_mtime)),
            "month": time.strftime("%m", time.localtime(stat_result.st_mtime)),
            "day": time.strftime("%d", time.localtime(stat_result.st_mtime)),
            "name": split_name(name)[0],
            "ext": extension_of(name).lstrip("."),
        }
        try:
            return self.to.format(**values)
        except KeyError as exc:
            raise ConfigError(
                f"{self.name}: unknown placeholder {exc} in 'to'; "
                f"available: {sorted(values)}"
            )
        except (IndexError, ValueError) as exc:
            raise ConfigError(f"{self.name}: bad 'to' template: {exc}")


class Rules:
    """The resolved category table plus ordered user rules."""

    def __init__(self, categories=None, rules=(), use_defaults=True):
        user_categories = {} if categories is None else categories
        if not isinstance(user_categories, dict):
            raise ConfigError("'categories' must be an object")

        merged: Dict[str, set] = {}
        if use_defaults:
            merged.update({k: set(v) for k, v in DEFAULT_CATEGORIES.items()})

        for name, exts in user_categories.items():
            _check_name(name, "category")
            if isinstance(exts, str):
                exts = [exts]
            if not isinstance(exts, (list, tuple, set)):
                raise ConfigError(
                    f"category {name!r} must be a list of extensions"
                )
            merged[name] = {normalise_ext(item) for item in exts}

        self.categories = merged

        # User-declared categories take priority, so adding ".png" to a custom
        # category actually wins over the built-in Images one.
        order = [n for n in user_categories if n in merged]
        order += [n for n in merged if n not in order]

        self._ext_index: Dict[str, str] = {}
        for name in order:
            for ext in merged[name]:
                self._ext_index.setdefault(ext, name)

        self.rules = [Rule(spec, i) for i, spec in enumerate(rules)]

    def destination_for(self, name: str, stat_result, now: float) -> str:
        """Where this file should go, relative to the folder. Rules win."""
        for rule in self.rules:
            if rule.matches(name, stat_result, now):
                return rule.destination(name, stat_result)
        return self._ext_index.get(extension_of(name), FALLBACK_CATEGORY)
