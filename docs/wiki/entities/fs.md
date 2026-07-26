---
title: fs — Filesystem Model
type: entity
sources:
  - linux_commander/fs.py
related:
  - "[[panel]]"
  - "[[operations]]"
  - "[[sort_criteria]]"
  - "[[vfs]]"
  - "[[readme-summary]]"
created: 2026-07-14
updated: 2026-07-22
confidence: high
---

# fs — Filesystem Model

## Purpose
Provides pure-data formatting helpers and delegates sorting to the [[sort_criteria]] plugin system. `FileEntry` and directory listing logic have moved to `linux_commander/vfs.py` (`LocalFileSystem.list_dir`). This module retains display-formatting functions that have no OS dependency.

## Public API

### `FileEntry` (dataclass)
Defined in `linux_commander/vfs.py`, re-exported from `fs.py` for import compatibility. A single row in a directory listing.
- `name: str` — Display name (e.g., "foo.txt" or "..")
- `path: Path` — Absolute path to the entry
- `is_dir: bool` — True for directories
- `size: int` — File size in bytes (0 for directories)
- `mtime: float` — Modification time as Unix timestamp
- `is_parent: bool` — True only for the synthetic ".." entry

### `sort_entries(entries, criterion_name: str = "name", reverse: bool = False) -> list[FileEntry]`
Delegates to the [[sort_criteria]] plugin system. Sorts with directories first (after ".."), then files. ".." always pinned at index 0.

### `format_size(num_bytes: int) -> str`
Human-readable: `123B`, `12.3K`, `12.3M`, etc. Base-1024.

### `format_mtime(timestamp: float) -> str`
Formats Unix timestamp as `YYYY-MM-DD HH:MM`. Returns `""` (blank) for any `timestamp <= 0` (2026-07-18) — that's the codebase-wide sentinel every VFS plugin uses for "no known mtime" (synthetic `..` entries, archive-internal directories, and Jottacloud folders — JFS never returns a `<modified>` timestamp for folders in its listing XML). Rendering `0.0` as a real formatted date used to show a misleading `1970-01-01`, which reads as a plausible old date rather than "unknown". `diff_viewer.py` has its own local duplicate of this formatter with the same guard.

## Types
```python
SortKey = Literal["name", "size", "mtime", "extension"]
```

## Testing
`tests/test_fs.py` uses `tmp_path` to verify listing, hidden filtering, sorting (dirs-first, ".." pinned), and formatter output.

## Related
- [[panel]] — consumes `format_size` and `format_mtime`
- [[sort_criteria]] — plugin-based sorting backend
- [[vfs]] — `FileEntry` and `LocalFileSystem.list_dir` live here