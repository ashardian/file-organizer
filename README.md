# file-organizer

Sorts a messy folder — Downloads, usually — into subfolders by file type, and can undo
the whole thing afterwards.

Runs on any Linux desktop: **Linux Mint, Ubuntu, Debian**, and the rest. Pure Python
standard library, no packages to install, nothing distro-specific in the organizer itself.

```bash
file-organizer organize ~/Downloads --dry-run   # see the plan
file-organizer organize ~/Downloads             # do it
file-organizer undo                             # change your mind
```

## Requirements

- Python **3.8 or newer** (Debian 11, Ubuntu 20.04, Mint 20 all qualify)
- No third-party packages — standard library only
- A Linux desktop, for the right-click integration (the CLI works anywhere)

## Install

```bash
cd file-organizer
./install.sh
```

That copies the package into `~/.local/lib/file-organizer`, writes a launcher to
`~/.local/bin/file-organizer`, adds a menu entry, and wires up whichever supported file
managers it finds. No `sudo`, nothing system-wide.

`./install.sh --uninstall` reverses it.

The installer deliberately does **not** use `pip`. Debian 12, Ubuntu 23.04+ and Mint 21.3+
mark the system Python as externally managed (PEP 668), so `pip install --user .` fails
there with `externally-managed-environment`. Copying the package and using a launcher
behaves identically on every release.

You can also run it straight from the repo, without installing:

```bash
PYTHONPATH=src python3 -m file_organizer organize ~/Downloads --dry-run
```

## Commands

| Command | What it does |
|---|---|
| `file-organizer` | Interactive menu on a terminal, help otherwise |
| `file-organizer organize [FOLDER] [flags]` | Sort a folder; defaults to your Downloads |
| `file-organizer undo [RUN_ID] [--list] [--dry-run]` | Reverse the most recent run, or the one named (a unique prefix of its ID from `--list` is enough) |
| `file-organizer rules [--path\|--init\|--edit\|--check]` | Show, seed, edit or validate the config |
| `file-organizer status` | Config path, journal size, last run, detected file managers |
| `file-organizer integrations [--install]` | Show or install desktop integration |

`file-organizer ~/Downloads` is shorthand for `file-organizer organize ~/Downloads`.

### The interactive menu

Running `file-organizer` with no arguments on a terminal opens a menu. It covers everything
the command line does, so nothing is only reachable by remembering a flag:

| Menu entry | Equivalent |
|---|---|
| Preview a folder (dry run) | `organize --dry-run` |
| Organize a folder | `organize` |
| Organize with different options... | the `organize` flags, below |
| Undo the last run | `undo` |
| Undo an earlier run... | `undo --list`, then `undo RUN_ID` |
| Configuration | `rules`, `rules --path/--init/--edit/--check` |
| Status | `status` |
| Desktop integration | `integrations --install` |

**Options are per-run and never saved.** *Organize with different options* changes the
conflict policy, hidden files, in-progress downloads, symlinks, journalling and
stop-on-error for that run only; your config file is not touched. To change the defaults
permanently, edit the config.

Every folder you pick goes through the same checks the command line applies, home folder
and `/` included. The plan is shown and confirmed before anything moves.

On a dumb terminal, or when output is piped, the menu falls back to a numbered
line-based version with the same operations.

### organize flags

| Flag | What it does |
|---|---|
| `--dry-run` | Show what would move, change nothing |
| `--confirm` | Show the plan, then ask before moving |
| `-y`, `--yes` | Don't ask, even with `--confirm` |
| `--on-conflict POLICY` | `skip`, `keep-both` (default), `replace` or `trash` |
| `--include-hidden` | Also organize dotfiles |
| `--include-incomplete` | Also organize in-progress downloads |
| `--follow-links` | Move symlinks instead of skipping them |
| `--no-journal` | Don't record the run — it becomes un-undoable |
| `--stop-on-error` | Abort at the first failure instead of continuing |
| `--config FILE` | Use a specific config file |

### Conflict policies

What happens when the destination name is already taken:

- **`keep-both`** (default) — the incoming file becomes `report_1.pdf`. Nothing is lost.
- **`skip`** — leave the file where it is and say so.
- **`replace`** — the *existing* file goes to the trash, the incoming file takes the name.
  Trash, not `unlink`: a discarded file is still recoverable.
- **`trash`** — the incoming duplicate goes to the trash, the existing file stays.

## Undo

Every completed move is appended to `~/.local/state/file-organizer/journal.jsonl`, one
JSON line per file, fsynced as it happens. `file-organizer undo` reverses the newest run
that hasn't been undone yet.

Undo is deliberately cautious:

- **Nothing is reversed blindly.** Each line records the file's size, mtime and inode at
  move time. Undo re-stats the file first and *skips* anything that has since changed or
  disappeared, reporting it rather than moving it anyway.
- **Running undo twice is safe.** The first undo writes a marker; the second finds nothing
  to do.
- **A crash doesn't lose the record.** Because lines are written per move rather than per
  run, a run killed halfway through is still fully undoable for the moves that completed.
- **Nothing is clobbered.** If a new file has taken the original name, the restored file
  arrives as `name.restored1.ext` instead of overwriting it.
- **Empty folders are left behind.** Undo moves files back; it does not delete the category
  folders the run created. Those folders may have existed before the run or still hold files
  it never touched, and removing a directory is much less reversible than moving a file —
  so delete them yourself if you want them gone.

`file-organizer undo --list` shows what's undoable; `--dry-run` shows what undo would do;
`file-organizer undo RUN_ID` reverses an older run instead of the newest.

If a restore fails (a permission error, say), the run stays in the list so you can retry
it once the cause is fixed. Files that did come back are reported as missing on the
retry, which is harmless.

The journal keeps the last 20 runs and is trimmed automatically.

### Trash

`replace` and `trash` use the freedesktop.org trash specification — the file lands in
`~/.local/share/Trash/files` with a matching `.trashinfo` sidecar, so it shows up in your
file manager's Trash and can be restored from there. Files on another filesystem (a USB
stick, say) can't go in the home trash, so they're handed to `gio trash`, which puts them
in that volume's own trash.

## Configuration

The config lives at `~/.config/file-organizer/config.json`. It's optional — without it you
get the built-in categories. Seed one with:

```bash
file-organizer rules --init     # write a starter config
file-organizer rules --edit     # open it in $EDITOR
file-organizer rules --check    # validate it
```

A malformed config is an error, never a silent fallback to defaults — a typo shouldn't
quietly organize things you didn't ask for.

```json
{
  "version": 1,
  "options": {
    "on_conflict": "keep-both",
    "skip_hidden": true,
    "skip_incomplete": true,
    "follow_links": false,
    "journal": true
  },
  "categories": {
    "Ebooks": [".epub", ".mobi", ".azw3"]
  },
  "rules": [
    {
      "name": "old photos, filed by month",
      "match": { "ext": [".jpg", ".png"], "older_than_days": 365 },
      "to": "Photos/{year}/{month}"
    },
    {
      "name": "big archives",
      "match": { "ext": [".zip", ".tar.gz"], "size_gt": "100MB" },
      "to": "BigArchives"
    }
  ]
}
```

### categories

Add your own, or override the built-in ones. Extensions are matched case-insensitively,
the leading dot is optional (`"jpg"` means `".jpg"`), and `.tar.gz`-style suffixes are
treated as one unit. A category you declare wins over a
built-in one with the same extension, so `"Screenshots": [".png"]` really does take `.png`
away from `Images`.

Set `"use_default_categories": false` to start from an empty table.

### rules

Rules are evaluated **in order, first match wins**, and they run *before* the categories —
so a rule can override a category. Two rules matching the same file is not an error; the
earlier one simply takes it.

| Match key | Example | Notes |
|---|---|---|
| `ext` | `[".jpg", ".png"]` | One string or a list |
| `glob` | `"*.bak"` | Case-insensitive |
| `regex` | `"^IMG_\\d+"` | Python regex, unanchored search |
| `older_than_days` | `365` | From modification time |
| `newer_than_days` | `7` | From modification time |
| `size_gt` | `"100MB"` | Suffixes: `KB`, `MB`, `GB`, `TB`, or `KiB`/`MiB`/… |
| `size_lt` | `"1MB"` | Same units |

`to` is the **destination folder**, relative to the folder being organized — the file keeps
its own name inside it, so nothing is ever renamed. It may contain placeholders, filled from
the file's modification time:

`{year}` `{month}` `{day}` `{name}` `{ext}` — for example `"Photos/{year}/{month}"` files a
photo as `Photos/2026/09/holiday.jpg`.

Because `to` is a folder, `{name}` and `{ext}` produce *folders* named after the file rather
than renaming it — `"to": "ByExt/{ext}"` gives you `ByExt/jpg/holiday.jpg`.

**Destinations must stay inside the folder being organized.** An absolute path like
`/etc/cron.d` or an escape like `../../.ssh` is rejected when the config loads, and
checked again per file as a backstop.

## Categories it knows out of the box

`Images`, `Documents`, `Spreadsheets`, `Archives`, `Audio`, `Video`, `Code`, `Packages`
(`.deb`, `.rpm`, `.AppImage`, `.flatpak`, `.snap`), `DiskImages` (`.iso`, `.img`), `Fonts`.
Anything unrecognized goes to `Other`.

## Desktop integration

`file-organizer integrations --install` detects what you have and installs only that.
Everything routes through one helper that opens a terminal, so a right-click never
relocates files without showing you the plan first. The terminal waits for Enter when the
run finishes, so you can read the result. If a new entry doesn't show up, restart your
file manager; the installer doesn't do it for you, because that would close your open
windows.

| File manager | Where it installs | Status |
|---|---|---|
| Nemo (Cinnamon) | `~/.local/share/nemo/actions/` | Verified on Linux Mint 22.3 |
| Nautilus (GNOME) | `~/.local/share/nautilus/scripts/` | Written to spec, unverified |
| Caja (MATE) | `~/.local/share/caja/scripts/` | Written to spec, unverified |
| Dolphin (KDE) | `~/.local/share/kio/servicemenus/` | Written to spec, unverified |
| Thunar (XFCE) | merged into `~/.config/Thunar/uca.xml` | Written to spec, unverified |

The four unverified entries were written from each project's documented format but could
not be exercised without those desktops installed. Treat them as a starting point rather
than a promise.

Thunar is the one with real risk: it means rewriting an XML file you may already have
custom actions in. Your `uca.xml` is backed up to `uca.xml.bak` first, and an unparseable
file aborts the install rather than being overwritten.

## Safety

- **Nothing is deleted outright.** No conflict policy overwrites a file: `replace` and
  `trash` send the displaced file to the trash, where you can restore it.
- **In-progress downloads are skipped** (`.part`, `.crdownload`, `.tmp`, LibreOffice
  `.~lock.` files). Moving a file that's still being written can corrupt it.
- **Symlinks are skipped**, including broken ones. With `--follow-links` they are moved
  as links (the link itself, not what it points at), broken ones included.
- **Hidden files are skipped** by default.
- **Your home folder is refused**, and so is `/`. `xdg-user-dir` reports `$HOME` when your
  download folder is unset, which would otherwise mean organizing everything.
- **Existing folders are left alone**, so running it twice is a no-op.
- **Config destinations can't escape the folder** — that check exists because category
  names and rule targets come from a file you can edit, unlike the hardcoded constants in
  earlier versions.

Everything skipped is reported with counts, so nothing disappears quietly.

## Development

```bash
python3 -m unittest discover -s tests -t tests
```

Standard-library `unittest` only — no pytest, no fixtures, nothing to install. The suite
covers the rule engine, path-traversal rejection, the conflict policies, the journal and
undo, and the trash implementation.

The menu and the command line share their logic through `file_organizer.actions`, so the
tests in `tests/test_tui.py` and `tests/test_cli.py` cover both at once. The curses screens
themselves are not unit-tested: a pty cannot be faked usefully, so anything worth asserting
lives in `actions` rather than in a screen.

## License

MIT
