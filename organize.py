#!/usr/bin/env python3
"""Sort the files in a folder into subfolders by file type."""
import argparse
import os
import shutil
import subprocess
from pathlib import Path

CATEGORIES = {
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
        ".tar.gz", ".tar.xz", ".tar.bz2", ".tar.zst", ".tar.lz4",
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
    # Linux-native package formats, the reason this tool targets Mint.
    "Packages": {".deb", ".rpm", ".appimage", ".flatpak", ".snap", ".msi"},
    "DiskImages": {".iso", ".img"},
    "Fonts": {".ttf", ".otf", ".woff", ".woff2"},
}

# Multi-part suffixes that must be matched as a unit, otherwise Path.suffix
# only sees the trailing ".gz" and ".tar.xz" ends up uncategorized.
COMPOUND_SUFFIXES = (
    ".tar.gz", ".tar.xz", ".tar.bz2", ".tar.zst", ".tar.lz4", ".tar.lzma",
)

# Files still being written. Moving these can corrupt an in-flight download.
INCOMPLETE_SUFFIXES = (
    ".part", ".crdownload", ".download", ".opdownload", ".partial",
    ".filepart", ".tmp",
)
INCOMPLETE_PREFIXES = (".~lock.",)

EXTENSION_INDEX = {
    ext: category for category, exts in CATEGORIES.items() for ext in exts
}


def split_name(name):
    """Split a filename into (stem, suffix), treating ".tar.gz" as one suffix."""
    lowered = name.lower()
    for suffix in COMPOUND_SUFFIXES:
        if lowered.endswith(suffix):
            cut = len(name) - len(suffix)
            return name[:cut], name[cut:]
    suffix = Path(name).suffix
    return name[: len(name) - len(suffix)], suffix


def category_for(name):
    return EXTENSION_INDEX.get(split_name(name)[1].lower(), "Other")


def is_incomplete(name):
    lowered = name.lower()
    return lowered.endswith(INCOMPLETE_SUFFIXES) or name.startswith(
        INCOMPLETE_PREFIXES
    )


def skip_reason(item, include_hidden, include_incomplete, follow_links):
    """Why `item` should be left alone, or None to organize it."""
    name = item.name
    # Check incomplete before hidden: LibreOffice locks are both, and
    # "in-progress download" is the more useful thing to report.
    if not include_incomplete and is_incomplete(name):
        return "in-progress"
    if not include_hidden and name.startswith("."):
        return "hidden"
    if item.is_symlink() and not follow_links:
        return "symlink"
    if not item.is_file():
        return "unreadable"
    return None


def unique_target(target_dir, name, taken):
    """A non-colliding destination, never overwriting an existing file."""
    candidate = target_dir / name
    if not candidate.exists() and candidate not in taken:
        return candidate
    stem, suffix = split_name(name)
    counter = 1
    while True:
        candidate = target_dir / f"{stem}_{counter}{suffix}"
        if not candidate.exists() and candidate not in taken:
            return candidate
        counter += 1


def plan(folder, include_hidden=False, include_incomplete=False,
         follow_links=False):
    """Work out every move before making any. Returns (moves, skipped)."""
    moves = []
    skipped = {}
    taken = set()
    for item in sorted(folder.iterdir()):
        # Existing folders (including ones this tool created) are expected.
        if item.is_dir() and not item.is_symlink():
            continue
        reason = skip_reason(
            item, include_hidden, include_incomplete, follow_links
        )
        if reason:
            skipped.setdefault(reason, []).append(item.name)
            continue
        target_dir = folder / category_for(item.name)
        target = unique_target(target_dir, item.name, taken)
        taken.add(target)
        moves.append((item, target))
    return moves, skipped


def execute(moves):
    for source, target in moves:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))


def default_folder():
    """The XDG download dir, resolved the way Mint itself resolves it."""
    home = Path.home()
    try:
        result = subprocess.run(
            ["xdg-user-dir", "DOWNLOAD"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        path = Path(result.stdout.strip())
        # xdg-user-dir echoes $HOME when the dir is unset; organizing the whole
        # home folder is never what someone meant.
        if path.is_dir() and path.resolve() != home.resolve():
            return path
    except (OSError, subprocess.SubprocessError):
        pass
    return home / "Downloads"


def resolve_folder(raw):
    """Expand ~, resolve, and refuse targets that would be a disaster."""
    resolved = Path(os.path.expanduser(str(raw))).resolve()
    if not resolved.is_dir():
        raise ValueError(f"{raw} is not a folder")
    if resolved == Path.home().resolve():
        raise ValueError(
            "refusing to organize your entire home folder - pick a subfolder"
        )
    if resolved == Path(resolved.anchor):
        raise ValueError(f"refusing to organize {resolved}")
    return resolved


def ask(count, folder):
    """Opt-in confirmation prompt. Never reached unless --confirm is passed."""
    try:
        answer = input(f"Move {count} file(s) in {folder}? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer.strip().lower() in {"y", "yes"}


def summarise(skipped):
    labels = {
        "in-progress": "in-progress download",
        "symlink": "symlink",
        "hidden": "hidden file",
        "unreadable": "unreadable entry",
    }
    parts = []
    for reason, names in sorted(skipped.items()):
        label = labels.get(reason, reason)
        parts.append(f"{len(names)} {label}{'s' if len(names) != 1 else ''}")
    return ", ".join(parts)


def main():
    parser = argparse.ArgumentParser(
        description="Organize a folder by file type (Linux Mint friendly)",
    )
    parser.add_argument(
        "folder", nargs="?", type=Path,
        help="folder to organize (default: your Downloads folder)",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would happen")
    parser.add_argument("--confirm", action="store_true",
                        help="ask before moving anything")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="do not ask, even with --confirm")
    parser.add_argument("--include-hidden", action="store_true",
                        help="also organize dotfiles")
    parser.add_argument("--include-incomplete", action="store_true",
                        help="also organize in-progress downloads (.part and friends)")
    parser.add_argument("--follow-links", action="store_true",
                        help="move symlinks instead of skipping them")
    args = parser.parse_args()

    raw = args.folder if args.folder is not None else default_folder()
    try:
        folder = resolve_folder(raw)
    except ValueError as exc:
        parser.error(str(exc))

    moves, skipped = plan(
        folder,
        include_hidden=args.include_hidden,
        include_incomplete=args.include_incomplete,
        follow_links=args.follow_links,
    )

    if not moves:
        print(f"Nothing to organize in {folder}.")
    else:
        prefix = "Would move" if args.dry_run else "Moved"
        for source, target in moves:
            print(f"{prefix}: {source.name} -> {target.parent.name}/")

        if not args.dry_run:
            if args.confirm and not args.yes and not ask(len(moves), folder):
                print("Cancelled, nothing moved.")
                return
            execute(moves)

    print(f"{len(moves)} file(s) "
          f"{'would be ' if args.dry_run else ''}organized.")
    if skipped:
        print(f"Skipped: {summarise(skipped)}.")


if __name__ == "__main__":
    main()
