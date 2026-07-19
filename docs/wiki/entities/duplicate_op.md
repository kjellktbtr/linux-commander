---
title: Duplicate File Finder
type: entity
sources:
  - linux_commander/file_ops/duplicate_op.py
related:
  - "[[operations]]"
  - "[[vfs]]"
  - "[[settings]]"
  - "[[dialogs]]"
created: 2026-07-18
updated: 2026-07-18
confidence: high
---

# Duplicate File Finder (`file_ops/duplicate_op.py`)

Registered as **Operations → Find Duplicates…** via the `file_ops/` auto-discovery mechanism (see [[operations]]'s "Operations Menu Plugins" section) — a single `FileOperation` in `duplicate_op.py`'s `OPERATIONS` list, with `prepare` showing `DuplicateFinderDialog` (collects scan directories + options) and `run` (`_run_duplicate_finder`) doing the scan on a background thread.

## Default comparison method

Applied in order, cheapest check first, so expensive work only happens on already-strong duplicate candidates:

1. **Size** — `_find_duplicates_by_size()` buckets files by exact size. Different size → not duplicates, no further comparison.
2. **Checksum** — `_hash_group()` computes SHA256 (via `_hash_file()`, chunked reads through the VFS `open_read()` API) for every file in a same-size bucket, splitting by hash. Only hash values shared by more than one file are kept as candidate groups.
3. **Content** — `_compare_content_group()` does a real byte-for-byte comparison (`_content_equal()`, 64KB chunks) on same-size/same-checksum groups before they're reported as duplicates. A checksum match makes an actual content mismatch astronomically unlikely, but it's still verified, not assumed, since these results can drive deletion.
   - Files **at or below** `Settings.duplicate_large_file_mb` (default 10MB, adjustable per-scan in the dialog — see [[settings]]) are compared immediately.
   - Files **above** the threshold trigger `LargeFileChoiceDialog` — a three-button prompt (**Assume Similar** / **Assume Different** / **Compare Content**) plus a checkbox ("do this for all remaining large files in this scan"). Checking the box remembers the chosen action for every later large-file group in the same scan, so a directory full of large same-checksum files doesn't prompt once per group. "Assume Similar" keeps the group as duplicates without reading the files; "Assume Different" drops the group entirely; "Compare Content" runs the real comparison.

## VFS-generalized directory walk (2026-07-18)

`_walk_for_duplicates()` used to check `isinstance(root.fs, LocalFileSystem)` and return an empty list for anything else — duplicate search **silently found nothing** when scanning an archive or a remote mount (Jottacloud, SMB, WebDAV, SFTP). Rewritten to recurse via the generic VFS `list_dir()`/`stat()` API (reading `FileEntry.size` directly rather than a separate per-file `os.stat()` call), so it now works identically across every backend.

## Fixed bug: singleton hash groups were reported as duplicates (2026-07-18)

The old `_hash_group()` had:
```python
for h, paths in hash_map.items():
    if len(paths) > 1:
        new_groups.append(DuplicateGroup(files=paths, size=group.size, hash_value=h))
    else:
        new_groups.append(DuplicateGroup(files=paths, size=group.size, hash_value=h))
```
Both branches did the exact same thing regardless of `len(paths) > 1` — a file whose checksum didn't match anything else in its size-bucket (i.e. **not** a duplicate) was still appended as a one-file "duplicate group" and shown in results. Fixed to only keep hash buckets with more than one file, matching the pattern `_find_duplicates_by_size()` already used correctly.

## Threading model

`_run_duplicate_finder` runs on `progress_dialog`'s background worker thread (same convention as every other `file_ops` plugin). Two things need a live Tk main thread mid-scan, both using the same `app.after(0, ...)` + `threading.Event().wait()` marshaling pattern (`app = CommanderApp.get_app()`):

- `_ask_large_file_action()` — blocks the worker on `LargeFileChoiceDialog` when a large-file group needs a decision. If `CommanderApp.get_app()` returns `None` (no live app instance — e.g. under pytest), it defaults to `"compare"` (do the real comparison) without showing anything, rather than silently assuming a result.
- The final `DuplicateResultsDialog` (a `ttk.Treeview` of duplicate groups with checkboxes, Select All/None, Delete Selected, Move Selected) — shown the same way after the scan completes.

`_run_duplicate_finder`'s only *public* return value is `list[OperationError]`; the actual `ScanResult` (groups found) is only ever surfaced by launching `DuplicateResultsDialog`, so it can't be asserted on directly without a live Tk display.

## Testing

`tests/test_duplicate_op.py` covers the pure pipeline functions directly (`_content_equal`, `_compare_content_group`, `_walk_for_duplicates`, `_find_duplicates_by_size`, `_hash_group`), including an end-to-end run of the full size→checksum→content pipeline minus the GUI dialogs, and a dedicated non-local-backend (ZIP-mounted) walk test for the VFS-generalization fix. Per CLAUDE.md, the GUI dialogs themselves (`DuplicateFinderDialog`, `LargeFileChoiceDialog`, `DuplicateResultsDialog`) are not covered under pytest — that requires a scripted driver against a real Tk display.

## Cross-Reference

- [[operations]] — shared `file_ops` plugin registration mechanism, `OperationError`/`ProgressCallback`/`CancelPredicate` types
- [[vfs]] — `list_dir()`/`stat()`/`open_read()` used throughout for backend-agnostic scanning and hashing
- [[settings]] — `duplicate_large_file_mb` persisted threshold default
- [[dialogs]] — `_center_over`/`confirm` helpers reused by this module's dialogs
