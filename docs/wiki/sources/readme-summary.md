---
title: README Source Summary
type: source-summary
sources:
  - docs/raw/README.md
related:
  - "[[app]]"
  - "[[panel]]"
  - "[[fs]]"
  - "[[operations]]"
  - "[[dialogs]]"
  - "[[viewer]]"
  - "[[volumes]]"
  - "[[platform_util]]"
  - "[[keys]]"
  - "[[orthodox-file-manager]]"
  - "[[cross-platform-seams]]"
created: 2026-07-14
updated: 2026-07-14
confidence: high
---

# README.md Source Summary

## Overview
The README.md is the primary project documentation, covering features, prerequisites, running instructions, development commands, keybindings, cross-platform status, and project layout.

## Key Content Extracted

### Features
- Dual file panels with Name/Size/Modified columns, each with volume bar
- Keyboard navigation: arrows, PgUp/PgDn, Home/End, Enter (descend), Backspace (go up)
- Tab switches active panel; active panel visually distinct
- Insert toggles tag on current file, moves cursor down; tagged files highlighted
- Operations act on tagged set or cursor file if none tagged
- F-key bar (F1–F10): Help, View, Edit, Copy, Move/Rename, MkDir, Delete, Menu, Quit
- F3: built-in read-only viewer; F4: built-in editor (Ctrl+S/F2 save)
- Enter on file tries OS default app, falls back to built-in viewer
- Volume/drive bar + Alt+F1/Alt+F2 volume choosers (mount points on Linux)
- Ctrl+H hidden toggle, Ctrl+R refresh, Ctrl+F3/F5/F6 sort by name/date/size (toggle reverse)
- F1 Help cheat-sheet, F9 placeholder menu, F10 Quit with confirmation

### Prerequisites
- Python 3.11+
- uv package manager
- Tk system package (e.g., `sudo pacman -S tk` on Arch/Manjaro)

### Running
```bash
uv sync
uv run linux-commander
# or
uv run python -m linux_commander
```

### Development
```bash
uv run pytest       # run tests
uv run ruff format . # format
uv run ruff check .  # lint
```

### Keybindings Table
Full table in README with all keys F1–F10, navigation, selection, view options, operations.

### Cross-Platform Status
- Targets Linux today; Windows/macOS future
- OS-specific logic quarantined in `volumes.py` (drive enumeration) and `platform_util.py` (open with default app)
- Linux: parses `/proc/mounts`, filters pseudo-fs, includes `/media/$USER`, `/run/media/$USER`, `/mnt`, always includes `/` and `~`
- Windows: stub raises `NotImplementedError` for drive letters
- macOS: stub raises `NotImplementedError` for `/Volumes`
- `list_volumes()` catches stubs and returns `[]` for graceful degradation
- Cross-platform paths via `pathlib.Path`; root detection via `path.parent == path`

### Project Layout
```
linux_commander/
  app.py            CommanderApp: dual-panel window, F-key bar, key routing
  panel.py          FilePanel: one directory-listing pane (Treeview-backed)
  fs.py             Directory listing, sorting, size/date formatting
  operations.py     copy/move/delete/mkdir/rename, with progress + error collection
  dialogs.py        confirm/prompt/error/choose_from_list/ProgressDialog + threaded runner
  viewer.py         Built-in file viewer (F3) and editor (F4)
  volumes.py        Volume/drive enumeration (Linux now; Windows/macOS stubbed)
  platform_util.py  "Open with default app" OS seam
  keys.py           F1..F10 key table shared by key bar and global bindings
tests/              pytest suite for non-GUI modules
```

## Related Entities
All module entity pages link back to this source summary.