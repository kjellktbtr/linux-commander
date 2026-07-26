# linux-commander — Claude Code guidelines

## Project Overview

A dual-pane "orthodox file manager" in the tradition of Norton Commander, Midnight Commander, and Total Commander — built with **plain tkinter** (no third-party GUI libraries). Features dual-pane browsing, built-in viewer/editor (hexdump, JSON, CSV/table, strings, syntax highlighting, spreadsheet/document preview), read/write archive browsing for 12+ formats, ChaCha20-Poly1305 file encryption, FTP/SFTP remote connections, and background multi-criteria file search with archive descent.

## Running the app

```bash
uv run linux-commander
# or
uv run python -m linux_commander
```

Requires a display (Tkinter/ttkbootstrap GUI).

## Post-edit workflow

After modifying any Python source file, run these three commands in order:

```bash
uv run ruff format .
uv run ruff check .
uv run mypy linux_commander
```

All three must pass (no errors) before committing. Fix any issues they report before moving on.

## Running tests

```bash
uv run pytest
```

GUI behavior (`panel.py`, `app.py`, `dialogs.py`, `viewer.py`'s `Toplevel` windows) is verified with scripted drivers against a real Tk display rather than under pytest, since it needs a live display and often blocking modal dialogs.

## Project layout

```
linux_commander/
  app.py                  CommanderApp: dual-panel window, F-key bar, menu bar, key routing
  panel.py                FilePanel: one directory-listing pane (Treeview-backed)
  fkey_bar.py             FKeyBar widget — F-key button row (extracted from app.py)
  command_prompt.py       CommandPrompt widget — command entry bar with history
  menu_bar.py             MenuBar builder with MenuCallbacks protocol
  panel_loading.py        Panel loading helpers — tree population, entry formatting
  sort_criteria/          Plugin-based sort criteria (name, size, mtime, extension)
  codecs/                 Plugin-based compression codecs (none, gz, bz2, xz, zstd)
  conflict_strategies/    Plugin-based conflict resolution (skip, replace, compare, etc.)
  fs.py                   Directory listing, sorting, size/date formatting
  operations.py           Copy/move/delete/mkdir/rename, with progress + error collection
  file_ops/               Auto-discovered Operations-menu items (base64_op.py, crypt_op.py, floppy_op.py)
  archiving.py            Compression: container x codec matrix, encryption-stage wrapping
  compression_dialog.py   Shift+F5 dialog (container/codec/level/encrypt-output)
  dialogs.py              Confirm/prompt/error/choose_from_list/pattern_dialog/ProgressDialog
  progress_dialog.py      Threaded progress dialog with cancel, used by F5/F6/F8/compress
  viewer.py               Built-in file viewer (F3) and editor (F4)
  image_viewer.py         Standalone image viewer (F3 on image files)
  file_info_dialog.py     Shift+F3 file info (type, permissions, checksums)
  search_engine.py        Background search worker + criteria model, archive descent
  search_dialog.py        Search UI (Alt+F7 / Shift+F7)
  search_criteria.py      UI-layer mutable search criteria, converted to engine form
  search_controller.py    Wires search dialog to a panelized results panel
  ftp_dialog.py           Connections manager (FTP/SFTP sessions)
  vfs.py                  FileSystem ABC, VfsPath, LocalFileSystem, MountManager
  fatfs.py                Pure-Python FAT12/FAT16 floppy image reader/writer
  plugins/                Auto-discovered VFS + viewer-reader plugins (see Plugin System)
  install_extras.py       Reports/installs optional-dependency extras
  volumes.py              Volume/drive enumeration (Linux /proc/mounts backend)
  platform_util.py        "Open with default app" OS seam
  icons.py                Panel file/folder icons
  grp_names.py            8.3 filename truncation + collision suffixing for GRP format
  keys.py                 F1..F10 key table shared by key bar and global bindings
  settings.py             Settings dataclass, load/save (settings.json), StoredKey/FtpSession
  syntax/                 Syntax highlighting definitions (*.json) + engine
tests/                    pytest suite for non-GUI modules
docs/wiki/                Auto-generated documentation wiki — update when things change
```

## Development workflow

```bash
uv sync --all-extras   # install everything, including optional-dependency extras
uv run pytest          # run the test suite
```

## VFS abstraction (CRITICAL)

All filesystem I/O goes through the `FileSystem` API in `linux_commander/vfs.py`.

**Never call methods directly on a `VfsPath`**. Use `path.fs.open_read(path)`, `path.fs.open_write(path)`, `path.fs.realpath(path)`, etc.

## Cross-platform seams

The app targets Linux today; Windows and macOS support are future goals. All OS-specific logic is quarantined behind two small seams:

- `linux_commander/volumes.py` — enumerates selectable roots (the classic OFM "drive bar"). Linux backend parses `/proc/mounts` directly, filtering out pseudo/virtual filesystems (`proc`, `tmpfs`, `overlay`, GVFS `fuse.*` mounts, etc.) rather than assuming a particular desktop's `/media/$USER` convention. Windows (drive letters) and macOS (`/Volumes`) each have a stub that raises `NotImplementedError`; `list_volumes()` catches that and returns `[]`, so the UI degrades to no volume bar instead of crashing.
- `linux_commander/platform_util.py` — `open_with_default_app()` dispatches on `sys.platform` (`xdg-open` / `os.startfile` / `open`).

Everywhere else, the code stays on `pathlib.Path`, and root/`..` detection uses `path.parent == path` (correct for both `/` and a Windows drive root).

## Plugin system

`linux_commander/plugins/` is an auto-discovered package (`pkgutil.iter_modules`, see `plugins/__init__.py`'s `_discover()`). A module in that package can expose any subset of three independent extension points — no registration step, just drop the file in:

```python
EXTENSIONS: tuple[str, ...] = (".zip",)   # VFS: mountable archive formats
def open_fs(host_fs, path: VfsPath) -> FileSystem: ...

SCHEMES: tuple[str, ...] = ("ftp",)       # VFS: network protocols
def connect_fs(url: str) -> FileSystem: ...

VIEW_EXTENSIONS: tuple[str, ...] = (".xlsx",)   # Viewer: document previews (not VFS)
def read_document(host_fs: FileSystem, path: VfsPath) -> ViewDocument: ...
```

Broken/unimportable plugin modules are silently skipped so one bad module can't block discovery of the rest. Compound extensions are matched longest-first (`.tar.gz` is tried before `.gz`), so a plugin registering `.tar.gz` takes priority over one registering plain `.gz` for a file named `archive.tar.gz`.

### Additional plugin systems

Beyond the VFS plugins above, three more auto-discovered plugin systems exist:

**Sort criteria** (`linux_commander/sort_criteria/`): Each module exposes a `criterion_class` attribute subclassing `SortCriterion`. Provides `name`, `label`, and `key(entry)` to sort file entries. New criteria (permissions, owner, etc.) can be added by dropping a module in.

**Compression codecs** (`linux_commander/codecs/`): Each module exposes a `codec_class` attribute subclassing `Codec`. Provides `name` and `compress(src, dst, level)`. New codecs (lz4, zstd, brotli) can be added without modifying core code.

**Conflict resolution strategies** (`linux_commander/conflict_strategies/`): Each module exposes a `strategy_class` attribute subclassing `ConflictStrategy`. Provides `name`, `label`, and `should_delete(conflict, dest_fs)`. Custom strategies can be added by dropping a module in.

### Adding a new VFS archive/protocol plugin

1. Create `linux_commander/plugins/<name>_plugin.py`.
2. Guard any third-party dependency at module top level and register nothing when missing, so the app degrades gracefully:
   ```python
   try:
       import py7zr
       EXTENSIONS = (".7z",)
   except ImportError:
       py7zr = None  # type: ignore[assignment]
       EXTENSIONS = ()
   ```
3. Implement a `FileSystem` subclass (`linux_commander/vfs.py`). Only `list_dir`, `stat`, and `open_read` are abstract and must be implemented; the format is read-only by default (`writable = False`, and `open_write`/`mkdir`/`delete`/`rename` already raise `OSError`) — override those plus set `writable = True` for a writable backend. Override `realpath()` if the backend is backed by a real file, and `read_prefix()` if you can stop an eager download early (see `ftp_plugin.py`/`tar_plugin.py`).
4. Implement `open_fs(host_fs, path) -> FileSystem` (extension plugins) and/or `connect_fs(url) -> FileSystem` (scheme plugins).
5. If your backend needs a real, seekable local file but the host filesystem doesn't expose one (an archive nested inside another archive, an FTP-hosted file, etc.), use the shared helpers in `plugins/__init__.py`: `materialize(host_fs, path) -> Path` spills to a temp file when there's no `realpath()`; `spill_named_temp(data, name)` preserves the *whole* filename (needed for compound-suffix formats); `cleanup_temp()` removes it afterward.
6. Add `tests/test_<name>_plugin.py`. If the dependency is optional, guard the whole module with `pytest.importorskip("libname")` at the top (see `tests/test_libarchive_plugin.py`) and build real fixtures with the library itself in `tmp_path` — that's the established pattern here, not mocking. Mocking is reserved for network-based plugins where hitting the real network isn't practical (see `tests/test_sftp_plugin.py`'s `MagicMock`-based fake paramiko client).

Good templates to read first: `grp_plugin.py` (self-contained format, no optional dependency) or `libarchive_plugin.py` (optional-dependency guard pattern, read-only).

### Adding a new viewer document-reader plugin

These preview a binary document format (spreadsheet, word processor, etc.) in the built-in viewer (F3/F4) — a separate mechanism from the VFS plugins above, since a document preview isn't a mountable filesystem.

1. Same file/module conventions as above, but expose `VIEW_EXTENSIONS` and:
   ```python
   def read_document(host_fs: FileSystem, path: VfsPath) -> ViewDocument: ...
   ```
2. Return a `ViewDocument` (`plugins/__init__.py`): `kind="table"` with `rows` (a `list[list[str]]`, `rows[0]` is the header) populates the viewer's CSV/table view; `kind="text"` with `text` inserts into the plain text view. Set `truncated=True` if you stopped early — respect `MAX_PREVIEW_ROWS` (currently 5000) as the cap on rows loaded, so a huge spreadsheet doesn't stall the UI or blow up memory.
3. Document previews are always opened read-only in the viewer, and F4 promotion is blocked — there's no way to save a generated table/text preview back over the original binary without corrupting it.
4. Templates: `xlsx_plugin.py` (straightforward streaming read with `openpyxl`) and `pandas_plugin.py` (the pattern for a *heavy* optional dependency — probe with `importlib.util.find_spec` at discovery time instead of importing eagerly, then `import pandas` lazily inside `read_document`).
5. Tests follow the same `pytest.importorskip` + real-fixture convention as VFS plugins.

### Adding a new viewer mode plugin

Viewer modes control how file content is displayed in the built-in viewer (hex dump,
JSON pretty-print, CSV table, strings scan). They live in `linux_commander/viewer_modes/`
and are auto-discovered at startup using the same `pkgutil.iter_modules` pattern as VFS
plugins.

1. Create `linux_commander/viewer_modes/<name>_mode.py`.
2. Subclass `ViewerMode` from `linux_commander.viewer_modes` and expose it as `mode_class`:
   ```python
   from linux_commander.viewer_modes import ViewerContext, ViewerMode

   class MyMode(ViewerMode):
       name = "MyMode"
       exclusive_group = "display"  # modes in same group are mutually exclusive

       def can_activate(self, ctx: ViewerContext) -> bool: ...
       def on_activate(self, ctx: ViewerContext) -> None: ...
       def on_deactivate(self, ctx: ViewerContext) -> None: ...
       def build_menu(self, ctx: ViewerContext, menu: tk.Menu) -> None: ...
   ```
3. The `ViewerContext` protocol provides access to the text widget, window, settings,
   file path, raw content, and helper methods (`clear_text`, `insert_text`,
   `set_title_suffix`, `apply_syntax_highlighting`, etc.).
4. Modes manage their own Tk state (`tk.BooleanVar`, submenus) in `build_menu()`.
5. Set `exclusive_group` to control mutual exclusion (e.g. `"display"` for hex/json/csv/strings).
6. Add `tests/test_viewer_modes.py` tests for discovery.

Templates: `hex_mode.py` (full-featured with submenu and background threads),
`json_mode.py` (simple toggle).

### Adding a syntax-highlighting language

No code change needed — drop a new `<lang>.json` file into `linux_commander/syntax/`. The engine (`syntax/__init__.py`) globs and loads every `*.json` file there at startup. Schema keys:

| Key | Purpose |
|---|---|
| `name` | Display name, shown in the viewer's Syntax menu |
| `extensions` | List of file extensions this language applies to |
| `case_sensitive` | Whether keyword matching is case-sensitive |
| `keywords` / `types` / `preprocessor` / `builtins` | Word -> color maps, flattened into one lookup |
| `string_color` / `comment_color` / `number_color` | Colors for those token classes |
| `line_comment` | Explicit single-line comment prefix (e.g. `#`, `//`) — only set this if the language actually has one, so formats like JSON aren't wrongly tinted |
| `patterns` | List of `{regex, color, multiline?, dotall?}`, applied last (highest visual priority) |

Use `linux_commander/syntax/py.json` as a filled-in example to copy from.

### Adding a new optional-dependency extra

1. Add the package(s) to the appropriate group (or a new group) in `pyproject.toml`'s `[project.optional-dependencies]`.
2. If the PyPI package name differs from its `import` name (e.g. `python-docx` is imported as `docx`), add an entry to `_IMPORT_NAME_OVERRIDES` in `linux_commander/install_extras.py`.

That's it — `linux-commander-install-extras` and the **File > Optional Dependencies...** menu both read `[project.optional-dependencies]` directly, so a new group is picked up automatically with no further wiring.

## Documentation wiki (docs/wiki/)

The wiki in `docs/wiki/` is the primary documentation source. **Update it whenever things are changed, implemented, or discoveries are made about the architecture or code.** Anything that needs to be investigated should be documented in the wiki so it can be easily found next time it's needed (and when it's out of context).

The wiki is auto-generated from source documents but should be treated as the authoritative reference — don't treat `CONTRIBUTING.md` or `README.md` as the wiki; they're source documents. When you make changes to the codebase, update the relevant wiki pages.

See `documentation-wiki` skill for wiki operations (ingest, query, lint, bootstrap).

## Key architecture concepts

- **Orthodox File Manager (OFM)** — dual-pane, keyboard-driven, F-key command bar, active/inactive panel switch with Tab. See `docs/wiki/orthodox-file-manager.md`.
- **VFS (Virtual File System)** — unified `FileSystem` abstraction over local, archive, and remote filesystems. See `docs/wiki/vfs.md`.
- **MountManager** — refcounted shared backend so both panels can browse the same archive at once.
- **Background threads** — F5/F6/F8/compress/search run on background threads with cancellable progress dialogs; UI stays responsive.
- **Auto-discovered plugins** — no registration, just drop a module in `plugins/` with the right module-level attributes.
- **Optional dependencies** — features degrade gracefully when optional packages are missing; the feature simply doesn't register.
- **Settings persistence** — `settings.json` under platform config dir (XDG / `%APPDATA%` / `~/Library/Application Support`), `chmod 0o600` on Unix.

## Key files to read for context

- `linux_commander/vfs.py` — VFS abstraction, `FileSystem` ABC, `MountManager`
- `linux_commander/plugins/__init__.py` — plugin discovery, `materialize`/`spill_named_temp` helpers
- `linux_commander/app.py` — main window, key routing, panel coordination
- `linux_commander/panel.py` — single panel, Treeview, navigation, tagging, sorting
- `linux_commander/viewer.py` — viewer/editor window, syntax highlighting, hex/CSV/strings/JSON views
- `linux_commander/archiving.py` — compression dialog logic, container×codec matrix, encryption wrapping
- `linux_commander/settings.py` — settings dataclass, load/save, StoredKey, FtpSession
- `linux_commander/search_engine.py` — background search worker, criteria, archive descent