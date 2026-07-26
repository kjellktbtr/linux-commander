---
title: Operations — File Operations with Progress
type: entity
sources:
  - linux_commander/operations.py
  - linux_commander/operations_controller.py
  - linux_commander/progress_dialog.py
  - linux_commander/conflict_dialog.py
  - linux_commander/file_ops/__init__.py
  - linux_commander/file_ops/rename_op.py
  - linux_commander/file_ops/sync_op.py
  - linux_commander/file_ops/checksum_op.py
  - linux_commander/diff_viewer.py
  - CONTRIBUTING.md
related:
  - "[[vfs]]"
  - "[[panel]]"
  - "[[app]]"
  - "[[dialogs]]"
  - "[[conflict_strategies]]"
  - "[[diff_viewer]]"
  - "[[checksums]]"
created: 2026-07-17
updated: 2026-07-22
confidence: high
---

# Operations — Copy/Move/Delete/Mkdir/Rename with Progress

`linux_commander/operations.py` implements the core file operations (F5 Copy, F6 Move, F8 Delete, F7 Mkdir, F6 Rename) with **background threading**, **progress reporting**, and **error collection**.

## SOLID Refactoring — OperationsController

`linux_commander/operations_controller.py` provides `OperationsController` — extracted from `CommanderApp` (SRP):
- Handles all file operations: copy, move, delete, mkdir, compress, new file, file info
- Provides refresh helpers and error reporting
- Composed with `CommanderApp` via dependency injection (callbacks for panel access, refresh, status updates)

```python
class OperationsController:
    def __init__(self, parent, settings, local_fs, mount_manager,
                 left_panel, right_panel, active_panel_getter,
                 other_panel_getter, refresh_both_panels,
                 report_errors, update_status) -> None: ...
    def cmd_copy(self) -> None: ...
    def cmd_move(self) -> None: ...
    def cmd_delete(self) -> None: ...
    def cmd_mkdir(self) -> None: ...
    def cmd_compress(self) -> None: ...
    def cmd_new_file(self) -> None: ...
    def cmd_file_info(self) -> None: ...
```

## Core Functions

| Function | Key | Description |
|----------|-----|-------------|
| `copy_entries()` | F5 | Tagged files (or cursor) → other panel / typed path |
| `move_entries()` | F6 | Same as copy, then delete source (skips delete if source is read-only) |
| `delete_entries()` | F8 | Permanent delete, confirmation dialog |
| `make_directory()` | F7 | Create a new directory |
| `rename_entry()` | F6 (single) | In-place rename |

All run on a **background thread** via `progress_dialog.run_with_progress()`.

## Per-file progress and an accurate `total` (2026-07-18)

`total` in every `on_progress(current, total, name)` call is a genuine **recursive file count** across the whole batch, computed up front by `count_progress_units()` (a file is 1 unit; an empty directory is 1 unit; a non-empty directory is the sum of its children). Before this, `total` was `len(sources)` — a single selected directory containing 500 files reported "1/1" for the entire transfer regardless of how many files it actually contained.

Per-file ticks now fire for every file actually transferred, on **both** code paths:
- **Stream path** (cross-backend, e.g. uploading a folder to Jottacloud, or copying out of a ZIP) — `_stream_copy_tree`/`_copy_file_with_progress` tick once per file as it streams, and `should_cancel()` is checked between files (not just between top-level selected items).
- **Local shutil fast path** (same-OS-filesystem copy/move) — `shutil.copytree`/`shutil.move` are given a `copy_function` hook (`_make_local_copy_function`) so per-file ticks fire even though the directory tree itself is walked by the stdlib, not by this module. `_catch_up_progress()` advances the counter to the precomputed total in one final tick after the shutil call returns, covering cases `copy_function` doesn't reach (empty subdirectories, or `shutil.move`'s same-filesystem `os.rename` shortcut, which is atomic and skips `copy_function` entirely).
- `delete_entries()`'s local fast path (`shutil.rmtree`) has no `copy_function`-equivalent hook in the stdlib, so it still reports the whole subtree's precomputed unit count in one tick — no per-file granularity there, only an accurate total.

`ProgressDialog._update_file_mode` (`progress_dialog.py`) already rendered `"{pct}% ({current}/{total} files)"` with files/s and ETA estimation before this change — it just was never fed real per-file data. No UI-layer changes were needed.

## `delete_entries()` now actually deletes from non-realpath backends (2026-07-18)

Previously `delete_entries()` unconditionally required `entry.fs.realpath(entry)` to be non-`None`, so **F8 delete against any writable backend with no real local file — Jottacloud, SMB, WebDAV, SFTP — always failed** with `"Cannot delete from a read-only filesystem"`, even after `cmd_delete`'s own `fs.writable` check had already passed and the backend implements `fs.delete()`. Fixed via `_delete_via_vfs()`: tries one recursive `fs.delete()` call first (most remote backends implement directory delete as a single recursive server-side operation — Jottacloud's `dlDir`, WebDAV's `DELETE` on a collection, this app's own zip writer), advancing the counter by the whole precomputed unit count in one jump; falls back to deleting children first, one genuine per-file tick each, for backends that only support removing empty directories (e.g. SFTP's `rmdir`).

## Progress dialog is non-modal — operations run in the background (2026-07-18)

`ProgressDialog` (`progress_dialog.py`) used to call `self.top.grab_set()`, making it a *modal* dialog — the user could not interact with anything else in the app while a copy/move/delete/compress was running. Removed. `run_with_progress()`'s `dialog.top.wait_window()` still pumps Tk's event loop while it waits (that's how a nested Tk event loop works — it isn't a hard block on the whole app), so once the dialog stops grabbing exclusive input, the user can click back into the panels and trigger another F5/F6/F7/F8/Shift+F5 command. Tk callbacks nest reentrantly, so a second `run_with_progress()` call gets its own independent worker thread, queue, and dialog — both operations proceed concurrently, each unwinding its own `wait_window()` independently when its dialog closes (not necessarily in start order).

**Known, accepted limitation**: nothing here serializes two concurrent operations that happen to write to the same destination directory. Not fixed — matches how most simple file managers behave.

Verified under Xvfb by directly checking `root.grab_current()` (Tk's actual grab-state query, not an indirect behavioral proxy) is `None` while a `ProgressDialog` is open, and by confirming a second `run_with_progress()` call completes while a first (deliberately slow) one is still in progress.

## Conflict Resolution for Copy/Move (2026-07-22)

Before a copy/move operation starts, `OperationsController._copy_or_move()` pre-scans all sources against the destination via `find_conflicts()` in `operations.py`. If conflicts are found, `conflict_dialog.resolve_conflicts()` shows a modal dialog listing all conflicting files with per-file resolution options:

| Resolution | Behavior |
|---|---|
| **Replace** | Overwrite the existing file |
| **Skip** | Don't copy/move this file |
| **Replace if newer** | Only if source mtime > dest mtime |
| **Replace if different size** | Only if source size != dest size |
| **Compare** | Open diff viewer, then skip (non-destructive) |

The dialog has an **Apply to All** checkbox that propagates the first file's choice to all remaining conflicts. After the user confirms, the controller resolves conflicts by dispatching to the [[conflict_strategies]] plugin system — each strategy's `should_delete()` method determines whether to delete the destination file before the operation proceeds.

`ConflictInfo` (source/dest paths, sizes, mtimes) lives in `conflict_strategies/__init__.py`. The `ConflictResolution` enum lives in `operations.py`. The dialog lives in `conflict_dialog.py`.

### SOLID Refactoring — Conflict Strategies Plugin System

The hardcoded `if/elif` conflict resolution logic was extracted into the [[conflict_strategies]] plugin system (`linux_commander/conflict_strategies/`). Each strategy is a separate module exposing a `strategy_class` attribute. `OperationsController` dispatches via `get_strategy()` using `ConflictResolution.name.lower()` to match plugin names.

## Progress & Error Handling

```python
def run_with_progress(
    parent,
    title: str,
    worker: Callable[[ProgressCallback], Result],
    on_done: Callable[[Result], None],
) -> None:
    ...
```

- `ProgressCallback(current, total, message)` — called by worker
- Worker returns `Result(success: bool, errors: list[tuple[str, Exception]])`
- Dialog shows progress bar, current file, cancel button
- On cancel: worker checks `cancel_event.is_set()` and stops cleanly
- On done: `on_done` receives result; errors shown in summary dialog

## Tagged Files vs Cursor File

All F5/F6/F8 operations act on **tagged set** (Insert to tag). If nothing tagged, use **cursor file only**.

## Operations Menu Plugins

`file_ops/` is auto-discovered like VFS plugins. Each module exposes:

```python
OPERATIONS: list[FileOperation] = [
    FileOperation(name="...", run=..., prepare=..., description="..."),
]
```

Registered in Operations menu (only when at least one plugin exists). Built-ins:
- `base64_op.py` — always available (stdlib): Base64 Encode / Decode
- `crypt_op.py` — needs `crypto` extra (Encrypt / Decrypt with ChaCha20-Poly1305)
- `rename_op.py` — Batch Rename with regex preview, find/replace, counter placeholders
- `sync_op.py` — Directory Sync (Mirror/Update/Backup modes) with dry-run preview
- `checksum_op.py` — Checksum Generation & Verification (MD5, SHA1, SHA256, SHA512)

## Batch Rename Operation (`rename_op.py`)

- **Menu**: Operations → Batch Rename…
- **Dialog**: Find/Replace (literal + regex toggle), counter (`{n:03d}`), date (`{date:%Y%m%d}`) placeholders, live preview table (Old Name → New Name) with conflict detection (red highlight)
- **Options**: Case sensitivity, extension preservation, simulate-only mode
- **Execution**: Runs renames via `operations.rename_entry()` (new helper)

## Directory Sync Operation (`sync_op.py`)

- **Menu**: Operations → Sync Directories…
- **Dialog**: Source ↔ Destination (pre-filled from two panels), Mode: Mirror (delete extra), Update (newer only), Backup (source→dest only), dry-run preview tree with checkboxes, filters (include/exclude patterns, size/time limits)
- **Progress**: Per-file with bytes, skip on error, summary at end
- **Modes**:
  - **Mirror**: Make destination identical to source (delete extra, update changed, copy new)
  - **Update**: Copy newer/missing files from source to destination (no deletions)
  - **Backup**: Copy all from source to destination, keep destination extras (no deletions, no overwrites of newer files)

## File Compare (Diff) Viewer (`diff_viewer.py`)

- **Menu**: Operations → Compare Files… (enabled when 2 files selected across panels)
- **Menu**: Operations → Compare Directories… (compares two panel directories)
- **View Modes**: Side-by-side (two panels, synchronized scrolling) or Unified diff
- **Syntax highlighting** of diff hunks (red/green) using existing syntax engine
- **Toolbar**: Prev/Next change, wrap lines, show whitespace, Open in Meld/Vimdiff, Save Patch…
- **Directory Compare**: Shows differing files list, double-click → file diff

## Checksum Generation & Verification (`checksum_op.py`)

- **Menu**: Operations → Checksums → Generate MD5/SHA256… / Verify…
- **Algorithms**: MD5, SHA1, SHA256, SHA512
- **Modes**:
  - Single file → show hash in dialog
  - Multiple files → write `.md5`/`.sha256` sidecar files (one per file or `SUM` file)
  - Verify mode — read `.md5`/`.sha256` and compare
- **Streaming hash** in chunks (memory efficient)
- **Output formats**: Standard (`hash  filename`), BSD (`HASH (filename) = hash`), GNU (`hash *filename`)

## Cross-Reference

- [[vfs]] — operations use `FileSystem` methods (open_read, open_write, delete, etc.)
- [[panel]] — FilePanel provides tagged/cursor files, target directory
- [[app]] — CommanderApp binds F5/F6/F7/F8 to operations
- [[dialogs]] — confirm/prompt for delete/rename/mkdir
- [[archiving]] — compression dialog uses same progress pattern
- [[diff_viewer]] — File compare viewer with side-by-side/unified diff
- [[checksums]] — Checksum generation and verification operations