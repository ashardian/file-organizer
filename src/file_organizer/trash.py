"""XDG trash support (freedesktop.org Trash specification).

Native implementation for files on the home filesystem, falling back to
`gio trash` for anything else. Home trash is only valid for files on the
same device -- a file on an external drive belongs in that volume's topdir
trash, which `gio` handles correctly and we do not reimplement.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
import urllib.parse
from pathlib import Path

from . import paths


class TrashError(Exception):
    """Raised when a file cannot be moved to the trash."""


def _ensure_dir(path: Path, mode: int = 0o700) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _unique_in(directory: Path, name: str) -> Path:
    """A free name inside `directory`, matching the stored sidecar name."""
    candidate = directory / name
    if not candidate.exists():
        return candidate
    counter = 2
    while True:
        candidate = directory / f"{name}.{counter}"
        if not candidate.exists():
            return candidate
        counter += 1


def _same_device(path: Path, other: Path) -> bool:
    try:
        return os.stat(path).st_dev == os.stat(other).st_dev
    except OSError:
        return False


def _gio_trash(path: Path) -> Path:
    gio = shutil.which("gio")
    if not gio:
        raise TrashError(
            f"{path} is on a different filesystem and `gio` is not installed, "
            "so it cannot be moved to the trash"
        )
    result = subprocess.run(
        [gio, "trash", "--", str(path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise TrashError(f"gio trash failed for {path}: {detail}")
    return path


def trash(path, trash_root=None) -> Path:
    """Move `path` to the trash, returning where it ended up.

    The path recorded in the sidecar is the absolute original location,
    percent-encoded, per the specification.
    """
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        raise TrashError(f"{path} does not exist")

    root = Path(trash_root) if trash_root else paths.home_trash()
    files_dir = root / "files"
    info_dir = root / "info"
    _ensure_dir(files_dir)
    _ensure_dir(info_dir)

    # The home trash is only valid on the filesystem that holds it.
    if not _same_device(files_dir, path.parent):
        return _gio_trash(path)

    stored = _unique_in(files_dir, path.name)
    info_path = info_dir / (stored.name + ".trashinfo")

    # Resolve only the directory. Resolving the file itself would record
    # where a symlink points rather than where the link lived, and a restore
    # would then drop the file on top of the link's target.
    try:
        original = str(path.parent.resolve() / path.name)
    except OSError:
        original = str(path.absolute())

    info_path.write_text(
        "[Trash Info]\n"
        f"Path={urllib.parse.quote(original)}\n"
        f"DeletionDate={time.strftime('%Y-%m-%dT%H:%M:%S')}\n",
        encoding="utf-8",
    )

    try:
        shutil.move(str(path), str(stored))
    except (OSError, shutil.Error) as exc:
        # Don't leave a sidecar pointing at a file that never arrived.
        try:
            info_path.unlink()
        except OSError:
            pass
        raise TrashError(f"could not trash {path}: {exc}")

    return stored
