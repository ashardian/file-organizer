# file-organizer

Cleans up messy folders (like Downloads) by sorting files into subfolders such as
`Images`, `Documents`, and `Archives`.

Targets **Linux Mint 22.3 (Cinnamon)**. It knows the file types a Mint box actually
accumulates — `.deb`, `.AppImage`, `.iso`, `.tar.xz`, `.opus`, `.webm` — and can be driven
from Nemo or the Cinnamon menu.

## Usage

Preview first, so nothing is moved:

```bash
python3 organize.py ~/Downloads --dry-run
```

Then run it for real:

```bash
python3 organize.py ~/Downloads
```

Note `python3`, not `python` — Mint ships only `python3`.

With no folder argument it uses your Downloads folder, resolved the same way Mint resolves
it (`xdg-user-dir DOWNLOAD`):

```bash
python3 organize.py --dry-run
```

## Install on Linux Mint

```bash
chmod +x install.sh
./install.sh
```

That installs three things for the current user — no `sudo`, nothing system-wide:

- `~/.local/bin/file-organizer` — the tool itself
- `~/.local/share/nemo/actions/file-organizer.nemo_action` — a Nemo right-click action
- `~/.local/share/applications/file-organizer.desktop` — a Cinnamon menu launcher

Then either run `file-organizer`, or right-click a folder in Nemo and choose
**Organize this folder**. Both GUI entry points run with `--confirm`, so they show you the
plan and ask before moving anything.

You can also skip the installer and run the script directly:

```bash
chmod +x organize.py
./organize.py ~/Downloads --dry-run
```

## Options

| Flag | What it does |
|---|---|
| `--dry-run` | Show what would move, change nothing |
| `--confirm` | Show the plan, then ask before moving |
| `-y`, `--yes` | Don't ask, even with `--confirm` |
| `--include-hidden` | Also organize dotfiles (off by default) |
| `--include-incomplete` | Also organize in-progress downloads (off by default) |
| `--follow-links` | Move symlinks instead of skipping them (off by default) |

## Categories

`Images`, `Documents`, `Spreadsheets`, `Archives`, `Audio`, `Video`, `Code`, `Packages`
(`.deb`, `.rpm`, `.AppImage`, `.flatpak`, `.snap`), `DiskImages` (`.iso`, `.img`), `Fonts`.
Anything unrecognized goes to `Other`.

To customize, edit the `CATEGORIES` dictionary at the top of `organize.py`. Extensions are
matched case-insensitively, so `.JPG` and `.jpg` land together. Multi-part suffixes like
`.tar.gz` and `.tar.xz` are matched as a unit rather than by their trailing `.gz`/`.xz`.

## Safety

- **Nothing is overwritten.** Name clashes get `_1`, `_2`, and so on.
- **In-progress downloads are skipped** by default (`.part`, `.crdownload`, `.tmp`,
  LibreOffice `.~lock.` files). Moving a file that is still being written can corrupt it.
- **Symlinks are skipped** by default, including broken ones, so links aren't silently
  relocated.
- **Your home folder is refused** as a target, and so is `/`. `xdg-user-dir` reports `$HOME`
  when your download folder is unset, which would otherwise mean organizing everything.
- **Hidden files are skipped** by default.
- Existing folders are left alone, so running it twice is a no-op.

Everything skipped is reported at the end, with counts, so nothing disappears quietly.

## Requirements

Python 3.8+, no external packages. Target platform: Linux Mint 22.3 (Zena), Cinnamon,
Python 3.12.

## License

MIT
