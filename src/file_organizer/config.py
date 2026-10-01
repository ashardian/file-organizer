"""Config file loading and validation.

JSON rather than TOML because tomllib needs Python 3.11, and Debian 11 /
Ubuntu 22.04 ship 3.9 / 3.10. A malformed config fails loudly instead of
quietly falling back to defaults, so a typo can't silently reorganize
things the user didn't ask for.
"""
from __future__ import annotations

import json
import os
import shlex
from pathlib import Path
from typing import Any, Dict, Optional

from . import paths
from .rules import ConfigError, Rules

CONFLICT_POLICIES = ("skip", "keep-both", "replace", "trash")

DEFAULT_OPTIONS = {
    "on_conflict": "keep-both",
    "skip_hidden": True,
    "skip_incomplete": True,
    "follow_links": False,
    "journal": True,
}

TOP_LEVEL_KEYS = frozenset(
    {"version", "categories", "rules", "options", "use_default_categories"}
)

TEMPLATE = {
    "version": 1,
    "options": {
        "on_conflict": "keep-both",
        "skip_hidden": True,
        "skip_incomplete": True,
        "follow_links": False,
        "journal": True,
    },
    "categories": {
        "Ebooks": [".epub", ".mobi", ".azw3"],
    },
    "rules": [
        {
            "name": "old photos, filed by month",
            "match": {"ext": [".jpg", ".png"], "older_than_days": 365},
            "to": "Photos/{year}/{month}",
        },
        {
            "name": "big archives",
            "match": {"ext": [".zip", ".tar.gz"], "size_gt": "100MB"},
            "to": "BigArchives",
        },
    ],
}


class Config:
    """Validated configuration: rules plus run options."""

    def __init__(self, data: Optional[Dict[str, Any]] = None, path=None):
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ConfigError("config must be a JSON object")

        unknown = set(data) - TOP_LEVEL_KEYS
        if unknown:
            raise ConfigError(
                f"unknown config key(s): {sorted(unknown)}; "
                f"known keys are {sorted(TOP_LEVEL_KEYS)}"
            )

        self.path = Path(path) if path else None
        self.version = data.get("version", 1)

        raw_options = data.get("options")
        if raw_options is None:
            raw_options = {}
        if not isinstance(raw_options, dict):
            raise ConfigError("'options' must be an object")
        unknown_options = set(raw_options) - set(DEFAULT_OPTIONS)
        if unknown_options:
            raise ConfigError(
                f"unknown option(s): {sorted(unknown_options)}; "
                f"known options are {sorted(DEFAULT_OPTIONS)}"
            )

        options = dict(DEFAULT_OPTIONS)
        options.update(raw_options)

        if options["on_conflict"] not in CONFLICT_POLICIES:
            raise ConfigError(
                f"on_conflict must be one of {list(CONFLICT_POLICIES)}, "
                f"not {options['on_conflict']!r}"
            )
        for key in ("skip_hidden", "skip_incomplete", "follow_links", "journal"):
            if not isinstance(options[key], bool):
                raise ConfigError(f"option {key!r} must be true or false")

        self.options = options

        rules = data.get("rules")
        if rules is None:
            rules = []
        if not isinstance(rules, list):
            raise ConfigError("'rules' must be a list")

        use_defaults = data.get("use_default_categories", True)
        if not isinstance(use_defaults, bool):
            raise ConfigError("'use_default_categories' must be true or false")

        self.rules = Rules(
            categories=data.get("categories"),
            rules=rules,
            use_defaults=use_defaults,
        )


def load(path=None) -> Config:
    """Load the config, or defaults if no file exists yet."""
    target = Path(path) if path else paths.config_file()

    if not target.exists():
        return Config({}, path=target)

    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {target}: {exc}")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"{target}: invalid JSON at line {exc.lineno} column {exc.colno}: "
            f"{exc.msg}"
        )

    return Config(data, path=target)


def editor_command(target) -> list:
    """The argv that opens `target` in the user's editor.

    $VISUAL and $EDITOR routinely hold a command with arguments, such as
    "code --wait", so they are split like a shell would rather than
    treated as one program name.
    """
    raw = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "nano"
    try:
        argv = shlex.split(raw)
    except ValueError:
        argv = [raw]
    return (argv or ["nano"]) + [str(target)]


def write_template(path=None, overwrite=False) -> Path:
    """Seed a starter config so `rules --edit` has something to open."""
    target = Path(path) if path else paths.config_file()
    if target.exists() and not overwrite:
        raise ConfigError(f"{target} already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(TEMPLATE, indent=2) + "\n", encoding="utf-8"
    )
    return target
