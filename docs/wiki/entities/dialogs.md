---
title: dialogs — Modal Dialogs & Progress Runner
type: entity
sources:
  - linux_commander/dialogs.py
related:
  - "[[operations]]"
  - "[[app]]"
  - "[[viewer]]"
  - "[[panel]]"
  - "[[readme-summary]]"
created: 2026-07-14
updated: 2026-07-14
confidence: high
---

# dialogs — Modal Dialogs & Progress Runner

## Purpose
Provides all modal dialog primitives (confirm, prompt, error, choose-from-list, show-text) and a generic threaded-operation runner (`run_with_progress`) that drives a `ProgressDialog` from a worker thread via `queue.Queue` + `root.after()` polling.

## Public API

### `confirm(parent, message, title="Confirm") -> bool`
Yes/No dialog. Returns `True` on Yes. Enter=Yes, Escape=No.

### `prompt(parent, title, message, initial="") -> str | None`
Single-line text input. Returns entered string or `None` on Cancel. Enter=OK, Escape=Cancel.

### `error(parent, message, title="Error") -> None`
OK-only message dialog. Blocks until dismissed.

### `choose_from_list(parent, title, items: list[str]) -> int | None`
Modal Listbox single-selection. Returns selected index or `None` on Cancel. Double-click or Enter confirms.

### `show_text(parent, title, text) -> None`
Non-blocking read-only scrollable text window (used for F1 Help). Fixed-width font.

### `run_with_progress(parent, title, work) -> Any`
**Generic threaded operation runner.**

- `work(progress_callback, should_cancel)` runs on a background thread
- `progress_callback(current, total, name)` reports progress
- `should_cancel()` returns `True` if user clicked Cancel
- Returns `work`'s return value on success; raises `OperationError` (wrapped) on exception
- Drives a modal `ProgressDialog` on the main thread via `queue.Queue` + `root.after(50, poll)`
- Cancel button sets a `threading.Event` that `should_cancel()` reads

### `ProgressDialog` (class)
Modal progress window with:
- Label showing current item name
- `ttk.Progressbar` (determinate)
- Cancel button
- `update(current, total, name)` called from main thread poller

## Threading Model
```
Main thread: run_with_progress() → creates ProgressDialog → starts worker thread → polls queue via after()
Worker thread: executes work(progress_cb, cancel_cb) → puts (type, payload) on queue → returns or raises
Main thread poller: drains queue → updates ProgressDialog → on done: destroys dialog → returns result or re-raises
```

## Usage in App
- F5 Copy / F6 Move / F8 Delete → `run_with_progress` with `operations.copy_entries` etc.
- F7 MkDir / F6 Rename (single) → synchronous, uses `prompt`/`confirm` directly
- F1 Help → `show_text`
- Volume chooser (Alt+F1/F2) → `choose_from_list`

## Testing
No unit tests (GUI-dependent). Verified via scripted drivers (`verify_fileops.py`, `verify_volumes.py`) that schedule dialog responses via `root.after()` from inside the dialog's `wait_window()` loop.

## Related
- [[operations]] — batch ops designed to work with `run_with_progress` callbacks
- [[app]] — command handlers wire dialogs to operations
- [[viewer]] — unsaved-changes confirm uses `confirm`
- [[panel]] — pattern prompts use `simpledialog` (stdlib)