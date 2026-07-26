---
title: Search Engine — Background Search with Archive Descent
type: entity
sources:
  - linux_commander/search_engine.py
  - linux_commander/search_criteria.py
  - linux_commander/search_controller.py
  - linux_commander/search_mode.py
  - CONTRIBUTING.md
related:
  - "[[vfs]]"
  - "[[plugins]]"
  - "[[panel]]"
  - "[[search_dialog]]"
created: 2026-07-17
updated: 2026-07-22
confidence: high
---

# Search Engine — Background Multi-criteria Search

`linux_commander/search_engine.py` implements the **Find Files** (Alt+F7 / Shift+F7) search: name, size, date, and content criteria, optionally descending into archives, all on a **background thread** with live streaming results.

## SOLID Refactoring — VFS-Based Walker

The search engine was refactored to work through the VFS abstraction (OCP/DIP):
- Removed `isinstance(fs, LocalFileSystem)` guard — now works on any `ReadableFileSystem` backend
- Removed `fs._to_path()` private method call
- Unified `walk_dir(Path)` and `_walk_vfs(VfsPath)` into single `_walk_vfs()` using VFS `list_dir`/`stat` API
- Archive descent moved into `_try_archive_descent()` helper

## Search Criteria Model

```python
@dataclass(frozen=True, slots=True)
class SearchCriteria:
    # Name
    name_enabled: bool = False
    name_pattern: str = ""
    name_regex: bool = False
    name_case_sensitive: bool = False

    # Size
    size_enabled: bool = False
    size_min: int | None = None
    size_max: int | None = None

    # Date (mtime)
    date_enabled: bool = False
    date_from: datetime | None = None
    date_to: datetime | None = None

    # Content
    content_enabled: bool = False
    content_mode: Literal["string", "regex", "hex"] = "string"
    content_pattern: str = ""
    content_case_sensitive: bool = False

    # Behavior
    search_archives: bool = False
    root_path: VfsPath | None = None
```

Parsed from UI by `search_criteria.py` (mutable UI model) → `SearchCriteria` (engine model).

## Background Worker

```python
def search_files(
    criteria: SearchCriteria,
    on_found: OnFoundCallback,
    on_done: OnDoneCallback,
    should_cancel: Callable[[], bool],
    on_progress: OnProgressCallback | None = None,
) -> None:
    ...
```

- Walks `criteria.root_path` recursively via `_walk_vfs()` (VFS-based, works on any backend)
- For each file: check criteria, if match → `on_found(SearchResult)`
- If `search_archives` and file matches archive extensions → `_try_archive_descent()`
- Checks `should_cancel()` frequently for cancellation

## Search Mode Controller

`linux_commander/search_mode.py` provides `SearchModeController` — extracted from `FilePanel` (SRP):
- Manages search mode state: entering/exiting, accumulating results, re-rendering
- Composed with `FilePanel` rather than inheriting from it
- Methods: `enter()`, `exit()`, `add_results()`, `rerender()`, `count()`

## Streaming Results

- `SearchController` (in `search_controller.py`) creates a **results panel** (a FilePanel subclass)
- `on_found` is called from worker thread; controller uses `root.after()` to marshal to UI thread
- Results appear **live** in the panel while search runs
- Panel is sortable (click column headers), retains standard panel keybindings

## SearchResult

```python
@dataclass(frozen=True, slots=True)
class SearchResult:
    entry: FileEntry
    match_info: str = ""  # e.g., "content", "name", "size", "date"
```

## Archive Descent

When `search_archives=True`:
1. Encounter archive → `_try_archive_descent()` calls plugin's `open_fs()`
2. Walk members recursively with same criteria via `_walk_vfs()`
3. `SearchResult.entry.path` is the **inner VfsPath** (archive member)
4. On unmount: refcounted, shared with any panel browsing same archive

## Cross-Reference

- [[vfs]] — walks any FileSystem (local, archive-mounted, FTP/SFTP)
- [[plugins]] — archive plugins provide `open_fs` for descent
- [[panel]] — results panel is a FilePanel subclass
- [[search_dialog]] — Alt+F7 UI, builds criteria, starts search