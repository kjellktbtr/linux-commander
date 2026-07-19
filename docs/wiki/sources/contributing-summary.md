---
title: CONTRIBUTING.md Summary
type: source-summary
sources:
  - CONTRIBUTING.md
related:
  - "[[readme-summary]]"
  - "[[vfs]]"
  - "[[plugins]]"
  - "[[archiving]]"
  - "[[syntax]]"
  - "[[settings]]"
  - "[[operations]]"
created: 2026-07-17
updated: 2026-07-17
confidence: high
---

# CONTRIBUTING.md — Source Summary

## Project Layout

```
linux_commander/
  app.py                 CommanderApp: dual-panel window, F-key bar, key routing
  panel.py               FilePanel: single directory-listing pane (Treeview-backed)
  fs.py                  Directory listing, sorting, size/date formatting
  operations.py          Copy/move/delete/mkdir/rename with progress + error collection
  file_ops/              Auto-discovered Operations-menu items (base64_op.py, crypt_op.py)
  archiving.py           Compression: container x codec matrix, encryption-stage wrapping
  compression_dialog.py  Shift+F5 dialog (container/codec/level/encrypt-output)
  dialogs.py             Confirm/prompt/error/choose_from_list/pattern_dialog/ProgressDialog
  progress_dialog.py     Threaded progress dialog with cancel (F5/F6/F8/compress)
  viewer.py              Built-in file viewer (F3) and editor (F4)
  image_viewer.py        Standalone image viewer (F3 on images)
  file_info_dialog.py    Shift+F3 file info (type, permissions, checksums)
  search_engine.py       Background search worker + criteria model, archive descent
  search_dialog.py       Search UI (Alt+F7 / Shift+F7)
  search_criteria.py     UI-layer mutable search criteria
  search_controller.py   Wires search dialog to panelized results panel
  ftp_dialog.py          Connections manager (FTP/SFTP sessions)
  vfs.py                 FileSystem ABC, VfsPath, LocalFileSystem, MountManager
  plugins/               Auto-discovered VFS + viewer-reader plugins
  install_extras.py      Reports/installs optional-dependency extras
  volumes.py             Volume/drive enumeration (Linux /proc/mounts backend)
  platform_util.py       "Open with default app" OS seam
  icons.py               Panel file/folder icons
  grp_names.py           8.3 filename truncation + collision suffixing for GRP
  keys.py                F1..F10 key table shared by key bar and global bindings
  settings.py            Settings dataclass, load/save (settings.json), StoredKey/FtpSession
  syntax/                Syntax highlighting definitions (*.json) + engine
tests/                   pytest suite for non-GUI modules
docs/wiki/               Auto-generated documentation wiki (NOT kept in sync manually)
```

## Development Workflow

```bash
uv sync --all-extras   # install everything including optional extras
uv run pytest          # run test suite
```

Post-edit workflow (must pass all three before commit):
```bash
uv run ruff format .
uv run ruff check .
uv run mypy linux_commander
```

GUI behavior verified with scripted drivers against real Tk display (not pytest).

## Cross-Platform Seams

Two small seams quarantine all OS-specific logic:

1. `linux_commander/volumes.py` — enumerates selectable roots (classic OFM "drive bar").
   - Linux: parses `/proc/mounts` directly, filters pseudo/virtual filesystems (`proc`, `tmpfs`, `overlay`, GVFS `fuse.*` mounts)
   - Windows/macOS: stubs raise `NotImplementedError`; `list_volumes()` catches and returns `[]`

2. `linux_commander/platform_util.py` — `open_with_default_app()` dispatches on `sys.platform`
   - Linux: `xdg-open`
   - Windows: `os.startfile`
   - macOS: `open`

All other code uses `pathlib.Path`; root/`..` detection via `path.parent == path` (works for both `/` and Windows drive root).

## Plugin System

`linux_commander/plugins/` is auto-discovered via `pkgutil.iter_modules` (see `plugins/__init__.py:_discover()`).

A plugin module may expose any subset of three independent extension points — no registration step:

```python
EXTENSIONS: tuple[str, ...] = (".zip",)   # VFS: mountable archive formats
def open_fs(host_fs, path: VfsPath) -> FileSystem: ...

SCHEMES: tuple[str, ...] = ("ftp",)       # VFS: network protocols
def connect_fs(url: str) -> FileSystem: ...

VIEW_EXTENSIONS: tuple[str, ...] = (".xlsx",)  # Viewer: document previews (not VFS)
def read_document(host_fs, path: VfsPath) -> ViewDocument: ...
```

Broken/unimportable modules silently skipped. Compound extensions matched longest-first (`.tar.gz` before `.gz`).

### Adding a VFS Archive/Protocol Plugin

1. Create `linux_commander/plugins/<name>_plugin.py`
2. Guard optional deps at module top level; register empty tuples when missing:
   ```python
   try:
       import py7zr
       EXTENSIONS = (".7z",)
   except ImportError:
       py7zr = None
       EXTENSIONS = ()
   ```
3. Implement `FileSystem` subclass (`vfs.py`). Only `list_dir`, `stat`, `open_read` are abstract. Read-only by default (`writable = False`); override write methods + set `writable = True` for writable.
4. Implement `open_fs(host_fs, path)` and/or `connect_fs(url)`.
5. If backend needs real seekable local file but host FS doesn't expose one (nested archive, FTP file), use helpers in `plugins/__init__.py`:
   - `materialize(host_fs, path) -> Path` — spills to temp when no `realpath()`
   - `spill_named_temp(data, name)` — preserves full filename (compound suffixes)
   - `cleanup_temp()` — removes temp afterward
6. Add `tests/test_<name>_plugin.py`; optional deps: `pytest.importorskip("libname")` at top; build real fixtures with library in `tmp_path` (not mocks). Mocking reserved for network plugins (see `test_sftp_plugin.py`).

Templates: `grp_plugin.py` (self-contained), `libarchive_plugin.py` (optional-dep guard, read-only).

### Adding a Viewer Document-Reader Plugin

1. Same file/module conventions; expose `VIEW_EXTENSIONS` and:
   ```python
   def read_document(host_fs: FileSystem, path: VfsPath) -> ViewDocument: ...
   ```
2. Return `ViewDocument` (`plugins/__init__.py`):
   - `kind="table"` + `rows: list[list[str]]` (rows[0] = header) → CSV/table view
   - `kind="text"` + `text: str` → plain text view
   - `truncated=True` if stopped early; respect `MAX_PREVIEW_ROWS` (5000)
3. Document previews always read-only; F4 promotion blocked.
4. Templates: `xlsx_plugin.py` (streaming `openpyxl`), `pandas_plugin.py` (heavy dep: `importlib.util.find_spec` at discovery, lazy `import pandas` in `read_document`).
5. Tests: same `pytest.importorskip` + real fixture pattern.

### Adding a Syntax-Highlighting Language

No code change — drop `<lang>.json` into `linux_commander/syntax/`. Engine (`syntax/__init__.py`) globs and loads all `*.json` at startup.

Schema keys:
| Key | Purpose |
|-----|---------|
| `name` | Display name in viewer's Syntax menu |
| `extensions` | File extensions this language applies to |
| `case_sensitive` | Keyword matching case sensitivity |
| `keywords` / `types` / `preprocessor` / `builtins` | Word → color maps, flattened |
| `string_color` / `comment_color` / `number_color` | Colors for token classes |
| `line_comment` | Explicit single-line comment prefix (`#`, `//`) — only if language actually has one |
| `patterns` | List of `{regex, color, multiline?, dotall?}`, applied last (highest priority) |

Use `linux_commander/syntax/py.json` as filled-in template.

### Adding a New Optional-Dependency Extra

1. Add package(s) to appropriate group (or new group) in `pyproject.toml` `[project.optional-dependencies]`.
2. If PyPI name differs from import name (e.g., `python-docx` → `docx`), add entry to `_IMPORT_NAME_OVERRIDES` in `linux_commander/install_extras.py`.

Auto-picked up by `linux-commander-install-extras` and **File > Optional Dependencies...** menu.

## Key Architecture Concepts

- **OFM (Orthodox File Manager)** — dual-pane, keyboard-driven, F-key command bar, Tab switches active panel
- **VFS (Virtual File System)** — unified `FileSystem` abstraction over local, archive, remote
- **MountManager** — refcounted shared backend so both panels browse same archive
- **Background threads** — F5/F6/F8/compress/search run on threads with cancellable progress dialogs
- **Auto-discovered plugins** — no registration, drop module in `plugins/` with right attributes
- **Optional dependencies** — features degrade gracefully when packages missing
- **Settings persistence** — `settings.json` in platform config dir (XDG / `%APPDATA%` / `~/Library/Application Support`), `chmod 0o600` on Unix