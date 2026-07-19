# linux-commander

A dual-pane "orthodox file manager" in the tradition of Norton Commander, Midnight
Commander, and Total Commander — built with **plain tkinter** (no third-party GUI
libraries).

Two file panels sit side by side. One panel is "active" at a time (switched with Tab);
navigation, tagging, and the classic F-key command bar all act on the active panel.

## Features

- Dual file panels (Name / Size / Modified columns), each with its own volume bar
- Keyboard navigation: arrows, PgUp/PgDn, Home/End, Enter to descend, Backspace to go up
- Tab to switch panels; the active panel is visually highlighted
- Insert-to-tag (with cursor auto-advance), `+`/`-`/`*` pattern select/deselect/invert
- F5 Copy, F6 Move/Rename, F7 MkDir, F8 Delete — all with confirmation/target dialogs
  and a threaded progress dialog (with cancel) for the file-moving operations
- F3 built-in read-only viewer, F4 built-in editor (Ctrl+S/F2 to save)
- Enter on a file tries the OS's default application first, falling back to the
  built-in viewer
- A volume/drive bar plus Alt+F1 / Alt+F2 volume choosers (mount points on Linux
  today; see "Cross-platform status" below)
- Ctrl+H hidden-file toggle, Ctrl+R refresh, Ctrl+F3/F5/F6 sort by name/date/size
  (press again to reverse)
- F1 Help cheat-sheet, F9 placeholder menu, F10 Quit (with confirmation)

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Tk must be installed at the system level (tkinter's `_tkinter` module links against
  it). On Arch/Manjaro:

  ```bash
  sudo pacman -S tk
  ```

  Verify with:

  ```bash
  python3 -c "import tkinter; print(tkinter.TkVersion)"
  ```

## Running

```bash
uv sync
uv run linux-commander
# or
uv run python -m linux_commander
```

## Development

```bash
uv run pytest       # run tests
uv run ruff format . # format
uv run ruff check .  # lint
```

## Keybindings

| Key(s) | Action |
| --- | --- |
| Up / Down / PgUp / PgDn / Home / End | Move the cursor in the active panel |
| Enter | Open a directory, or a file (default app, falling back to the built-in viewer) |
| Backspace | Go to the parent directory |
| Tab | Switch the active panel |
| Alt+F1 / Alt+F2 | Choose a volume for the left / right panel |
| Insert | Tag/untag the current file, move down |
| `+` / `-` / `*` | Tag / untag by glob pattern / invert the tag selection |
| Ctrl+H | Toggle hidden (dotfile) visibility |
| Ctrl+R | Refresh the active panel's listing |
| Ctrl+F3 / Ctrl+F5 / Ctrl+F6 | Sort by name / date / size (press again to reverse) |
| F1 | Help — show the keybindings cheat-sheet |
| F3 | View — read-only built-in viewer |
| F4 | Edit — built-in editor (Ctrl+S or F2 saves) |
| F5 | Copy tagged files (or the cursor file) to a target directory |
| F6 | Move, or — if you type just a new filename — rename in place |
| F7 | MkDir — create a new directory |
| F8 | Delete — permanently delete, after confirmation |
| F9 | Menu — placeholder pulldown |
| F10 | Quit — with confirmation |

Operations act on the tagged (marked) set in the active panel, or just the cursor file
if nothing is tagged. F5/F6's destination defaults to the other panel's current
directory. F5/F6/F8 run on a background thread with a cancellable progress dialog.

## Cross-platform status

The app targets Linux today; Windows and macOS support are a future goal, and the
architecture is already built for it. All OS-specific logic is quarantined behind two
small seams:

- `linux_commander/volumes.py` — enumerates selectable roots (the classic OFM "drive
  bar"). The Linux backend parses `/proc/mounts` directly, filtering out pseudo/virtual
  filesystems (`proc`, `tmpfs`, `overlay`, GVFS `fuse.*` mounts, etc.) rather than
  assuming a particular desktop's `/media/$USER` convention — so it picks up whatever is
  actually mounted, wherever it's mounted. Windows (drive letters) and macOS
  (`/Volumes`) each have a stub that raises `NotImplementedError`; `list_volumes()`
  catches that and returns `[]`, so the UI degrades to no volume bar instead of crashing.
- `linux_commander/platform_util.py` — `open_with_default_app()` dispatches on
  `sys.platform` (`xdg-open` / `os.startfile` / `open`).

Everywhere else, the code stays on `pathlib.Path`, and root/`..` detection uses
`path.parent == path` (correct for both `/` and a Windows drive root).

## Project layout

```
linux_commander/
  app.py            CommanderApp: the dual-panel window, F-key bar, key routing
  panel.py          FilePanel: one directory-listing pane (Treeview-backed)
  fs.py             Directory listing, sorting, size/date formatting
  operations.py     copy/move/delete/mkdir/rename, with progress + error collection
  dialogs.py        confirm/prompt/error/choose_from_list/ProgressDialog + threaded runner
  viewer.py         Built-in file viewer (F3) and editor (F4)
  volumes.py        Volume/drive enumeration (Linux now; Windows/macOS stubbed)
  platform_util.py  "Open with default app" OS seam
  keys.py           The F1..F10 key table shared by the key bar and global bindings
tests/              pytest suite for the non-GUI modules (fs, operations, viewer's pure
                    helpers, platform_util, volumes)
```

GUI behavior (panel.py, app.py, dialogs.py, viewer.py's Toplevel windows) is verified
with scripted drivers against a real Tk display rather than under pytest, since it
needs a live display and often blocking modal dialogs.
