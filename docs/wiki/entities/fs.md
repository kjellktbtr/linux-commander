---
title: fs — Filesystem Model
type: entity
sources:
  - linux_commander/fs.py
related:
  - "[[panel]]"
  - "[[operations]]"
  - "[[readme-summary]]"
created: 2026-07-14
updated: 2026-07-18
confidence: high
---

# fs — Filesystem Model

## Purpose
Provides the pure-data filesystem model: directory listing, sorting, and formatting. No UI code, no I/O side effects beyond `pathlib.Path` operations.

## Public API

### `FileEntry` (dataclass)
A single row in a directory listing.
- `name: str` — Display name (e.g., "foo.txt" or "..")
- `path: Path` — Absolute path to the entry
- `is_dir: bool` — True for directories
- `size: int` — File size in bytes (0 for directories)
- `mtime: float` — Modification time as Unix timestamp
- `is_parent: bool` — True only for the synthetic ".." entry

### `list_directory(path: Path, show_hidden: bool = True) -> list[FileEntry]`
Lists a directory, prepending a ".." entry unless at filesystem root (`path.parent == path`). Skips entries that raise `OSError`/`PermissionError` on `stat()`. Respects `show_hidden` for dotfiles.

### `sort_entries(entries, key: SortKey = "name", reverse: bool = False) -> list[FileEntry]`
Sorts with directories first (after ".."), then files. Keys: `"name"` (case-insensitive), `"size"`, `"mtime"`. ".." always pinned at index 0.

### `format_size(num_bytes: int) -> str`
Human-readable: `123B`, `12.3K`, `12.3M`, etc. Base-1024.

### `format_mtime(timestamp: float) -> str`
Formats Unix timestamp as `YYYY-MM-DD HH:MM`. Returns `""` (blank) for any `timestamp <= 0` (2026-07-18) — that's the codebase-wide sentinel every VFS plugin uses for "no known mtime" (synthetic `..` entries, archive-internal directories, and Jottacloud folders — JFS never returns a `<modified>` timestamp for folders in its listing XML). Rendering `0.0` as a real formatted date used to show a misleading `1970-01-01`, which reads as a plausible old date rather than "unknown". `diff_viewer.py` has its own local duplicate of this formatter with the same guard.

## Types
```python
SortKey = Literal["name", "size", "mtime"]
```

## Testing
`tests/test_fs.py` uses `tmp_path` to verify listing, hidden filtering, sorting (dirs-first, ".." pinned), and formatter output.

## Related
- [[panel]] — consumes `list_directory` and `sort_entries`
- [[operations]] — operates on `FileEntry.path` values