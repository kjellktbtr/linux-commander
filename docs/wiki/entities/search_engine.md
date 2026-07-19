---
title: Search Engine — Background Search with Archive Descent
type: entity
sources:
  - linux_commander/search_engine.py
  - linux_commander/search_criteria.py
  - linux_commander/search_controller.py
  - CONTRIBUTING.md
related:
  - "[[vfs]]"
  - "[[plugins]]"
  - "[[panel]]"
  - "[[search_dialog]]"
created: 2026-07-17
updated: 2026-07-17
confidence: high
---

# Search Engine — Background Multi-criteria Search

`linux_commander/search_engine.py` implements the **Find Files** (Alt+F7 / Shift+F7) search: name, size, date, and content criteria, optionally descending into archives, all on a **background thread** with live streaming results.

## Search Criteria Model

```python
@dataclass
class SearchCriteria:
    # Name
    name_pattern: str = ""
    name_regex: bool = False
    name_case_sensitive: bool = False

    # Size
    size_min: int | None = None
    size_max: int | None = None

    # Date (mtime)
    date_from: float | None = None   # Unix timestamp
    date_to: float | None = None

    # Content
    content_mode: Literal["string", "regex", "hex"] = "string"
    content_pattern: str = ""
    content_case_sensitive: bool = False

    # Behavior
    search_archives: bool = False
```

Parsed from UI by `search_criteria.py` (mutable UI model) → `SearchCriteria` (engine model).

## Background Worker

```python
def search_worker(
    roots: list[VfsPath],
    criteria: SearchCriteria,
    on_match: Callable[[SearchMatch], None],  # called from worker thread
    should_stop: threading.Event,
) -> SearchResult:
    ...
```

- Walks each root `VfsPath` recursively
- For each file: stat, check criteria, if match → `on_match(SearchMatch)`
- If `search_archives` and file matches archive extensions → mount via `MountManager`, descend into members
- Skips files > 10 MB for content search
- Checks `should_stop.is_set()` frequently for cancellation

## Streaming Results

- `SearchController` (in `search_controller.py`) creates a **results panel** (a FilePanel subclass)
- `on_match` is called from worker thread; controller uses `root.after()` to marshal to UI thread
- Results appear **live** in the panel while search runs
- Panel is sortable (click column headers), retains standard panel keybindings

## SearchMatch / SearchResult

```python
@dataclass
class SearchMatch:
    path: VfsPath
    name: str
    size: int
    mtime: float
    is_dir: bool

@dataclass
class SearchResult:
    matches: int
    errors: list[tuple[str, Exception]]  # (path, error)
    stopped_early: bool
```

## Archive Descent

When `search_archives=True`:
1. Encounter archive → `MountManager.mount(host_fs, archive_path)`
2. Walk members recursively with same criteria
3. `SearchMatch.path` is the **inner VfsPath** (archive member)
4. On unmount: refcounted, shared with any panel browsing same archive

## Cross-Reference

- [[vfs]] — walks any FileSystem (local, archive-mounted, FTP/SFTP)
- [[plugins]] — archive plugins provide `open_fs` for descent
- [[panel]] — results panel is a FilePanel subclass
- [[search_dialog]] — Alt+F7 UI, builds criteria, starts search
- [[search_criteria]] — mutable UI model, converts to engine criteria