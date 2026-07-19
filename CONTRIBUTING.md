# Contributing to linux-commander

This file covers the project layout, the development workflow, and how to
extend linux-commander (a new archive format, a new viewer document preview,
a new syntax-highlighting language, a new optional-dependency extra). For
user-facing features and keyboard shortcuts, see [README.md](README.md).

## Project layout

```
linux_commander/
  app.py               CommanderApp: the dual-panel window, F-key bar, menu bar, key routing
  panel.py              FilePanel: one directory-listing pane (Treeview-backed)
  fs.py                 Directory listing, sorting, size/date formatting
  operations.py         copy/move/delete/mkdir/rename, with progress + error collection
  file_ops/             Auto-discovered Operations-menu items (base64_op.py, crypt_op.py)
  archiving.py          Compression: container x codec matrix, encryption-stage wrapping
  compression_dialog.py Shift+F5 dialog (container/codec/level/encrypt-output)
  dialogs.py             confirm/prompt/error/choose_from_list/pattern_dialog/ProgressDialog
  progress_dialog.py     Threaded progress dialog with cancel, used by F5/F6/F8/compress
  viewer.py              Built-in file viewer (F3) and editor (F4)
  image_viewer.py        Standalone image viewer (F3 on image files)
  file_info_dialog.py    Shift+F3 file info (type, permissions, checksums)
  search_engine.py       Background search worker + criteria model, archive descent
  search_dialog.py       Search UI (Alt+F7 / Shift+F7)
  search_criteria.py     UI-layer mutable search criteria, converted to the engine form
  search_controller.py   Wires the search dialog to a panelized results panel
  ftp_dialog.py           Connections manager (FTP/SFTP sessions)
  vfs.py                  FileSystem ABC, VfsPath, LocalFileSystem, MountManager
  plugins/                Auto-discovered VFS + viewer-reader plugins (see below)
  install_extras.py       Reports/installs optional-dependency extras (see README)
  volumes.py              Volume/drive enumeration (Linux now; Windows/macOS stubbed)
  platform_util.py        "Open with default app" OS seam
  icons.py                Panel file/folder icons
  grp_names.py            8.3 filename truncation + collision suffixing for the GRP format
  keys.py                 The F1..F10 key table shared by the key bar and global bindings
  settings.py             Settings dataclass, load/save (settings.json), StoredKey/FtpSession
  syntax/                 Syntax highlighting definitions (*.json) + engine
tests/                    pytest suite for the non-GUI modules
docs/wiki/                 Auto-generated documentation wiki -- separate from this file,
                            not kept in sync manually; don't treat it as authoritative
```

GUI behavior (`panel.py`, `app.py`, `dialogs.py`, `viewer.py`'s `Toplevel` windows) is
verified with scripted drivers against a real Tk display rather than under pytest, since
it needs a live display and often blocking modal dialogs.

## Development workflow

```bash
uv sync --all-extras   # install everything, including optional-dependency extras
uv run pytest          # run the test suite
```

After modifying any Python source file, run these three commands in order and make sure
all three pass clean before committing:

```bash
uv run ruff format .          # format
uv run ruff check .           # lint
uv run mypy linux_commander   # type-check
```

## Cross-platform seams

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

## The plugin system

`linux_commander/plugins/` is an auto-discovered package (`pkgutil.iter_modules`, see
`plugins/__init__.py`'s `_discover()`). A module in that package can expose any subset
of three independent extension points — no registration step, just drop the file in:

```python
EXTENSIONS: tuple[str, ...] = (".zip",)   # VFS: mountable archive formats
def open_fs(host_fs, path: VfsPath) -> FileSystem: ...

SCHEMES: tuple[str, ...] = ("ftp",)       # VFS: network protocols
def connect_fs(url: str) -> FileSystem: ...

VIEW_EXTENSIONS: tuple[str, ...] = (".xlsx",)   # Viewer: document previews (not VFS)
def read_document(host_fs, path: VfsPath) -> ViewDocument: ...
```

Broken/unimportable plugin modules are silently skipped so one bad module can't block
discovery of the rest. Compound extensions are matched longest-first (`.tar.gz` is tried
before `.gz`), so a plugin registering `.tar.gz` takes priority over one registering
plain `.gz` for a file named `archive.tar.gz`.

### Adding a new VFS archive/protocol plugin

1. Create `linux_commander/plugins/<name>_plugin.py`.
2. Guard any third-party dependency at module top level and register nothing when it's
   missing, so the app degrades gracefully:

   ```python
   try:
       import py7zr
       EXTENSIONS = (".7z",)
   except ImportError:
       py7zr = None  # type: ignore[assignment]
       EXTENSIONS = ()
   ```
3. Implement a `FileSystem` subclass (`linux_commander/vfs.py`). Only `list_dir`,
   `stat`, and `open_read` are abstract and must be implemented; the format is read-only
   by default (`writable = False`, and `open_write`/`mkdir`/`delete`/`rename` already
   raise `OSError`) — override those plus set `writable = True` for a writable backend.
   Override `realpath()` if the backend is backed by a real file, and `read_prefix()` if
   you can stop an eager download early (see `ftp_plugin.py`/`tar_plugin.py`).
4. Implement `open_fs(host_fs, path) -> FileSystem` (extension plugins) and/or
   `connect_fs(url) -> FileSystem` (scheme plugins).
5. If your backend needs a real, seekable local file but the host filesystem doesn't
   expose one (an archive nested inside another archive, an FTP-hosted file, etc.), use
   the shared helpers in `plugins/__init__.py`: `materialize(host_fs, path) -> Path`
   spills to a temp file when there's no `realpath()`; `spill_named_temp(data, name)`
   preserves the *whole* filename (needed for compound-suffix formats); `cleanup_temp()`
   removes it afterward.
6. Add `tests/test_<name>_plugin.py`. If the dependency is optional, guard the whole
   module with `pytest.importorskip("libname")` at the top (see
   `tests/test_libarchive_plugin.py`) and build real fixtures with the library itself in
   `tmp_path` — that's the established pattern here, not mocking. Mocking is reserved for
   network-based plugins where hitting the real network isn't practical (see
   `tests/test_sftp_plugin.py`'s `MagicMock`-based fake paramiko client).

Good templates to read first: `grp_plugin.py` (self-contained format, no optional
dependency) or `libarchive_plugin.py` (the optional-dependency guard pattern, read-only).

### Adding a new viewer document-reader plugin

These preview a binary document format (spreadsheet, word processor, etc.) in the
built-in viewer (F3/F4) — a separate mechanism from the VFS plugins above, since a
document preview isn't a mountable filesystem.

1. Same file/module conventions as above, but expose `VIEW_EXTENSIONS` and:
   ```python
   def read_document(host_fs: FileSystem, path: VfsPath) -> ViewDocument: ...
   ```
2. Return a `ViewDocument` (`plugins/__init__.py`): `kind="table"` with `rows` (a
   `list[list[str]]`, `rows[0]` is the header) populates the viewer's CSV/table view;
   `kind="text"` with `text` inserts into the plain text view. Set `truncated=True` if
   you stopped early — respect `MAX_PREVIEW_ROWS` (currently 5000) as the cap on rows
   loaded, so a huge spreadsheet doesn't stall the UI or blow up memory.
3. Document previews are always opened read-only in the viewer, and F4 promotion is
   blocked — there's no way to save a generated table/text preview back over the
   original binary without corrupting it.
4. Templates: `xlsx_plugin.py` (straightforward streaming read with `openpyxl`) and
   `pandas_plugin.py` (the pattern for a *heavy* optional dependency — probe with
   `importlib.util.find_spec` at discovery time instead of importing eagerly, then
   `import pandas` lazily inside `read_document`).
5. Tests follow the same `pytest.importorskip` + real-fixture convention as VFS plugins.

### Adding a syntax-highlighting language

No code change needed — drop a new `<lang>.json` file into `linux_commander/syntax/`.
The engine (`syntax/__init__.py`) globs and loads every `*.json` file there at startup.
Schema keys:

| Key | Purpose |
|---|---|
| `name` | Display name, shown in the viewer's Syntax menu |
| `extensions` | List of file extensions this language applies to |
| `case_sensitive` | Whether keyword matching is case-sensitive |
| `keywords` / `types` / `preprocessor` / `builtins` | Word -> color maps, flattened into one lookup |
| `string_color` / `comment_color` / `number_color` | Colors for those token classes |
| `line_comment` | Explicit single-line comment prefix (e.g. `#`, `//`) -- only set this if the language actually has one, so formats like JSON aren't wrongly tinted |
| `patterns` | List of `{regex, color, multiline?, dotall?}`, applied last (highest visual priority) |

Use `linux_commander/syntax/py.json` as a filled-in example to copy from.

### Adding a new optional-dependency extra

1. Add the package(s) to the appropriate group (or a new group) in `pyproject.toml`'s
   `[project.optional-dependencies]`.
2. If the PyPI package name differs from its `import` name (e.g. `python-docx` is
   imported as `docx`), add an entry to `_IMPORT_NAME_OVERRIDES` in
   `linux_commander/install_extras.py`.

That's it — `linux-commander-install-extras` and the **File > Optional Dependencies...**
menu both read `[project.optional-dependencies]` directly, so a new group is picked up
automatically with no further wiring.
