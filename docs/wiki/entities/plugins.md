---
title: Plugins
type: entity
sources:
  - linux_commander/plugins/__init__.py
  - CONTRIBUTING.md
related:
  - "[[vfs]]"
  - "[[archiving]]"
  - "[[viewer]]"
  - "[[syntax]]"
created: 2026-07-17
updated: 2026-07-18
confidence: high
---

# Plugins — Auto-discovered Extensions

`linux_commander/plugins/` is an auto-discovered package via `pkgutil.iter_modules` (see `plugins/__init__.py:_discover()`). Dropping a Python module in that package with the right module-level attributes is all it takes to add support for a new format — no registration step.

## Three Independent Extension Points

A plugin module may expose any subset of these:

```python
# VFS: mountable archive formats (Enter to browse)
EXTENSIONS: tuple[str, ...] = (".zip",)
def open_fs(host_fs: FileSystem, path: VfsPath) -> FileSystem: ...

# VFS: network protocols (URL-based connections)
SCHEMES: tuple[str, ...] = ("ftp",)
def connect_fs(url: str) -> FileSystem: ...

# Viewer: document previews (F3/F4) — NOT a VFS filesystem
VIEW_EXTENSIONS: tuple[str, ...] = (".xlsx",)
def read_document(host_fs: FileSystem, path: VfsPath) -> ViewDocument: ...
```

## Discovery Rules

- Runs once at startup (`plugins/__init__.py` module load)
- Broken/unimportable modules are **silently skipped** — one bad plugin can't block the rest
- Compound extensions matched longest-first: `.tar.gz` tried before `.gz`
- Optional dependencies guarded at module top level; when missing, register empty tuples so the feature simply doesn't appear

```python
# Template for optional-dependency plugins
try:
    import py7zr
    EXTENSIONS = (".7z",)
except ImportError:
    py7zr = None
    EXTENSIONS = ()
```

## VFS Archive/Protocol Plugins

Implement a `FileSystem` subclass (`linux_commander/vfs.py`). Only `list_dir`, `stat`, `open_read` are abstract; the rest default to read-only (`writable = False`). Override `writable = True` plus the write methods for writable backends.

Optional overrides:
- `realpath(path) -> Path` — return a real local `Path` if the backend is backed by a real file (enables opening with system default app)
- `read_prefix(path, n) -> bytes` — return first N bytes without full download (used by viewer for quick type detection on FTP)

Implement `open_fs(host_fs, path) -> FileSystem` (extension plugins) and/or `connect_fs(url) -> FileSystem` (scheme plugins).

### Materialization Helpers (in `plugins/__init__.py`)

| Helper | Use Case |
|--------|----------|
| `materialize(host_fs, path) -> Path` | Spill to temp file when no `realpath()` exists |
| `spill_named_temp(data, name) -> Path` | Preserve full filename (compound suffix) for format detection |
| `cleanup_temp(path) -> None` | Remove temp file after use |

**Use `spill_named_temp`, not `materialize`, for any format that derives its own identity from the archive's *own filename*.** `materialize()` spills to `tempfile.mkstemp()`, which only preserves the last suffix (`.zst`) and gives the rest of the basename a random name — fine for formats that read their content structurally (tar/zip member names come from inside the archive, not the outer filename). But `compress_plugin.py` (single-file `.gz`/`.bz2`/`.xz`/`.zst`, no internal structure) derives its one member's name by stripping just the outer suffix off the *archive's own filename* (`"backup.grp.zst"` → `"backup.grp"`) — until 2026-07-18 it used `materialize()`, so opening one of these from a non-realpath host (Jottacloud, or nested inside another archive) surfaced the member as a random temp basename (e.g. `"tmpAbC123"`) instead of the real name, breaking Enter-to-navigate. Fixed in `compress_plugin.py:open_fs()` to read the source fully via `open_read()` and call `spill_named_temp(data, host_path.name)` when `realpath()` is `None`.

## Viewer Document-Reader Plugins

Separate from VFS — these preview binary documents (spreadsheets, Word, etc.) in the built-in viewer (F3/F4). Not mountable.

```python
def read_document(host_fs: FileSystem, path: VfsPath) -> ViewDocument: ...
```

Return a `ViewDocument` (`plugins/__init__.py`):

```python
@dataclass
class ViewDocument:
    kind: Literal["table", "text"]   # "table" -> CSV view; "text" -> plain text view
    rows: list[list[str]] | None     # for kind="table", rows[0] is header
    text: str | None                 # for kind="text"
    truncated: bool = False          # set True if stopped early (e.g., MAX_PREVIEW_ROWS)
```

Respect `MAX_PREVIEW_ROWS` (5000) to avoid UI stalls on huge spreadsheets. Document previews are always read-only; F4 promotion is blocked (no way to save a generated preview back to the original binary).

## Adding a New Plugin

1. Create `linux_commander/plugins/<name>_plugin.py`
2. Guard optional deps at top level, set extension tuples to `()` on `ImportError`
3. Implement the appropriate function(s) above
4. Add `tests/test_<name>_plugin.py`:
   - Optional dep: guard with `pytest.importorskip("libname")` at top
   - Build real fixtures with the library in `tmp_path` (not mocks)
   - Network plugins: mock the network layer (see `test_sftp_plugin.py`)

## Templates to Read First

- `grp_plugin.py` — self-contained, no optional dep
- `libarchive_plugin.py` — optional-dep guard pattern, read-only
- `xlsx_plugin.py` — viewer plugin, streaming read
- `pandas_plugin.py` — heavy optional dep, lazy import via `importlib.util.find_spec`

## Cross-Reference

- [[vfs]] — FileSystem ABC, MountManager, VfsPath
- [[archiving]] — compression dialog writes archives via VFS
- [[viewer]] — consumes VIEW_EXTENSIONS plugins for document preview
- [[syntax]] — separate plugin-like system (drop JSON files in `syntax/`)