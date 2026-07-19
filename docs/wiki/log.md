# Wiki Operation Log

## 2026-07-18 — SMB port default, real fix for Jottacloud deleted-item filtering, background operations, SVG viewer support

Continued dogfooding turned up a real bug in the previous session's deleted-item fix (it filtered correctly but parsed the trigger condition wrong), plus three new asks.

- **Jottacloud deleted items STILL didn't disappear** (`jotta_api.py`) — the earlier fix (added a `deleted` field, filtered on it) was structurally right but the parsing itself was wrong: `elem.get("deleted", "false").lower() == "true"` assumes JFS sets the attribute to the literal string `"true"`, but per jottalib's own reference parsing (`attrib.get('deleted', None)`, checked for `is None`, never compared against a literal), the real value is a **deletion timestamp**. The `== "true"` check silently never matched, so the filtering never actually triggered against a live account. Fixed via a shared `_is_deleted(elem)` helper checking *presence*, not exact value — added a regression test using a realistic `deleted="<timestamp>"` fixture (verified it fails against the old `== "true"` logic, confirming the previous session's test suite had a blind spot: all its `deleted=True` fixtures constructed the dataclass directly, bypassing XML parsing entirely).
- **SMB port default bug** (`ftp_dialog.py`) — `_update_protocol_state()`'s port-default swap was hardcoded to only toggle between ftp(21)/sftp(22); selecting SMB never applied its own 445 default despite `_DEFAULT_PORTS` already having the right value. Fixed to swap whenever the field holds *any* known default. (Separately: "SMB says not installed" turned out to be an environment mismatch, not a code bug — see [[smb_vfs]].)
- **Operations now run in the background** (`progress_dialog.py`) — removed `ProgressDialog`'s `grab_set()` (the thing that made it modal). `run_with_progress()`'s `wait_window()` still pumps Tk's event loop while waiting, so the app stays responsive and a second F5/F6/F7/F8/Shift+F5 can run concurrently (reentrant Tk callbacks, independent thread+dialog per call). Verified by querying `root.grab_current()` directly (not an indirect behavioral proxy) before/after the fix.
- **SVG rendering** (`image_viewer.py`) — new optional `svg` extra (`cairosvg`). `_decode_image_bytes()` rasterizes SVG to PNG bytes once at every raw-bytes-to-`Image.open()` boundary, so the *existing* PIL-based pipeline (zoom, rotate, flip, thumbnails) handles SVG with zero format-specific UI code. Degrades gracefully without the extra (existing `UnidentifiedImageError` error-dialog path); a malformed SVG is normalized to `OSError` since two call sites don't catch a bare `Exception`.
- Updated [[operations]], [[ftp_dialog]], [[smb_vfs]], [[image_viewer]] with dated sections. New `tests/test_image_viewer.py` (6 tests, real cairosvg rasterization verified against PNG magic bytes + decoded dimensions). Xvfb scripts verified the two purely-GUI fixes (grab state, and a full `view_image()` SVG round-trip including rotate/thumbnail/EXIF on the resulting PIL Image) against real Tk behavior, each confirmed to fail against the pre-fix code before being confirmed fixed.
- 424 tests pass (up from 418), ruff clean, mypy clean (added a `cairosvg.*` override to `[tool.mypy.overrides]`, matching the existing pattern for other untyped optional deps).

## 2026-07-18 — Second round of Jottacloud usage bugs: stat(), deleted-item filtering, save, compress naming, theme crash

Another follow-up after actually using the app against a live Jottacloud mount surfaced five more bugs beyond the previous session's fixes.

- **`stat()` searched the wrong listing** (`jotta_plugin.py`) — GETting a folder's own JFS path returns its *children*, not itself, so `stat()` on any Jottacloud folder always raised "Not found." `delete()`/`rename()` call `stat()` internally, so every folder delete/rename failed with a misleading "doesn't exist" error. Fixed to list the *parent* and search for a matching child, same as `list_dir()` already did correctly.
- **Deleted items didn't disappear from listings** (`jotta_api.py`, `jotta_plugin.py`) — JFS keeps trashed items in listings marked with a `deleted` attribute rather than dropping them; `JottaFile` didn't even have a `deleted` field (only `JottaFolder` did, and nothing read it). A successful delete call worked server-side but the item kept showing up in the panel. Added `JottaFile.deleted`, parsed it, and filter on it in `list_dir()`/`stat()`.
- **Shift+F4 save failed with "read only filesystem"** (`viewer.py`) — `_save_file()` only knew how to save via a local write-to-temp-then-replace dance, treating any backend with no real local file (`realpath() is None`) as unconditionally read-only, even when `path.fs.writable` and `open_write()` worked fine. Fixed to fall back to `path.fs.open_write()` for non-local writable backends.
- **Compressing to Jottacloud produced a wrong/temp-named result** (`compress_plugin.py`) — `open_fs()` used `materialize()` (spills to a *randomly named* temp file) instead of `spill_named_temp()` (preserves the whole filename) when browsing a compressed file with no real local path. Since this format derives its one member's name by stripping the outer suffix off the archive's *own* filename (`"backup.grp.zst"` → `"backup.grp"`), a random temp basename surfaced as the member name.
- **Theme selection crashed** (`app.py`) — `ttkbootstrap.Style.theme_use()` restyles every widget in a global `Publisher` registry that every `ttk.Combobox` auto-joins; cleanup on widget destruction is normally wired up by `ttkbootstrap.window.Window/Toplevel`'s own `__init__`, but this app subclasses plain `tk.Tk`, so it was never installed. Any closed dialog with a Combobox left a stale reference that crashed the next theme switch. Fixed with one call to `ttkbootstrap.window.apply_all_bindings(self)`. Verified by reproducing the exact crash under Xvfb (create+destroy a bare Combobox, then cycle `theme_use()`) both before and after the fix — this one can't be pytest-covered (GUI-only), so the Xvfb repro script was the verification method instead.
- Updated [[jotta_vfs]], [[viewer]], [[app]], [[plugins]] with dated sections for the above. 4 new regression tests added to `test_jotta_plugin.py`, 1 to `test_new_plugins.py`; the viewer save fix and theme fix were verified via ad-hoc Xvfb scripts (not committed to the test suite, since neither has a pytest-friendly seam — see CLAUDE.md's GUI-testing policy).
- 417 tests pass, ruff clean, mypy clean.

## 2026-07-18 — Post-deployment fixes: Jotta crash, per-file progress, VFS delete, duplicate finder rewrite

Follow-up session after the Jottacloud write/delete work below went live, triggered by real user-reported bugs while actually using the feature.

- **Fixed the `RuntimeError: <Event> is bound to a different event loop` crash** (`jotta_api.py`) — `SyncJottaAPI` used to fetch a thread-local event loop per call, but the shared `httpx.AsyncClient` instances bind their connection-pool internals to whichever loop first uses them. Called from both a background copy-worker thread and the Tk main thread (post-copy panel refresh), the second thread in always crashed. Fixed with one persistent background event-loop thread for the object's lifetime, routing calls through `asyncio.run_coroutine_threadsafe`. This is also *why* auto-refresh after a Jottacloud transfer appeared broken — `_refresh_both_panels()` already existed and was already called, it was just crashing silently. Regression test needed a hand-rolled shared `asyncio.Event` to reproduce, since `httpx.MockTransport` (used by every other test in the file) bypasses the real connection-pool locking that caused the bug.
- **Fixed misleading 1970-01-01 folder dates** (`fs.py`) — JFS folder listings never carry a `<modified>` timestamp (confirmed against real captured responses), which the app was rendering through `mtime=0.0` → a real formatted epoch date. `format_mtime()` now returns `""` for `timestamp <= 0`, the codebase-wide "no known mtime" sentinel — fixes this for archive-internal directories too, not just Jottacloud.
- **Per-file progress for copy/move/delete/compress** (`operations.py`, `archiving.py`) — `total` used to be `len(sources)` (top-level selection count), so copying a directory with 500 files always showed "1/1". Now a genuine recursive file count via the new `count_progress_units()`, with real per-file ticks on both the cross-backend stream path and (via `shutil.copytree`/`move`'s `copy_function` hook) the local fast path. `should_cancel()` is now checked between files, not just between top-level items.
- **Fixed `delete_entries()` silently failing on every non-local backend** (`operations.py`) — it unconditionally required `realpath()` to be non-`None`, so F8 delete against Jottacloud/SMB/WebDAV/SFTP always failed with "Cannot delete from a read-only filesystem" even though `fs.writable` had already passed and those backends implement `fs.delete()`. Fixed via `_delete_via_vfs()` (single recursive delete first, falls back to per-child deletion for empty-dir-only backends like SFTP).
- **Rewrote the duplicate finder's default comparison method** (`file_ops/duplicate_op.py`) — now: size differs → not duplicates; size matches → compare SHA256; checksum matches → full byte-for-byte content compare (immediate for files ≤ a new persisted `Settings.duplicate_large_file_mb` threshold, default 10MB; prompted — assume similar / assume different / compare content, with an apply-to-all-remaining checkbox — above it). Along the way: generalized the directory walk from local-only (`os.walk` + `isinstance(..., LocalFileSystem)`) to the generic VFS API (archives/remote mounts previously returned zero results silently), and fixed a real bug where singleton hash groups (files that are *not* duplicates) were being reported as one-file "duplicate groups" regardless.
- **New wiki page**: [[duplicate_op]]. Updated [[jotta_vfs]], [[operations]], [[archiving]], [[fs]], [[settings]] with dated sections for the above.
- 412 tests pass (up from 389), ruff clean, mypy clean.

## 2026-07-18 — Jottacloud write/delete support
- **Jottacloud Backend** (`jotta_api.py`, `plugins/jotta_plugin.py`) — Flipped `JottaFileSystem.writable` to `True` and implemented `open_write`/`mkdir`/`delete`/`rename` against the JFS REST write endpoints (`POST` with `umode=nomultipart&cphash=`, `mkDir=true`, `dl=true`/`dlDir=true`, `mv=`/`mvDir=`). Delete moves to Jottacloud's trash (recoverable), not a permanent hard-delete. Upload buffers full content to `BytesIO` (md5/size must be known up front for the `JMd5`/`JSize` headers) and POSTs on `close()`, mirroring `_WebDAVFile`.
- **Bug fix in `_jfs_request`/`_api_request`** — the 401-retry path re-popped `headers` from an already-emptied `kwargs` dict, silently dropping caller headers on retry. Latent and harmless for reads (no custom headers), but would have broken uploads (missing `JMd5`/`JSize` on a refresh-retry). Fixed by keeping the original headers in a local variable.
- **New wiki page**: [[jotta_vfs]] — documents the full read/write JFS API surface, the two-path auth model (fresh login-token exchange in `connect_fs()` vs. persisted-refresh-token restore in `ftp_dialog.py`'s `_connect_jotta_session()`), and the write-endpoint reference table.
- **Backfilled missing index.md links** — `smb_vfs`, `webdav_vfs`, and `credentials` (added in the 2026-07-18 Phase 5 entry below) were never actually linked from `index.md`; added a "Network VFS Backends" section alongside the new `jotta_vfs` link.
- Added `tests/test_jotta_plugin.py` (write-path tests against a mocked `SyncJottaAPI`) and extended `tests/test_jotta_api.py` with `httpx.MockTransport`-based request-shape tests for the four new write methods. All 389 tests pass, ruff clean, mypy clean.

## 2026-07-18 — Phase 5: Network VFS Backends (SMB/CIFS + WebDAV)
- **SMB/CIFS Backend** (`plugins/smb_plugin.py`) — Writable VFS using `smbprotocol` (SMB2/3). Registers `smb://` scheme. Supports full CRUD: list, read, write, mkdir, delete, rename via `FileRenameInfo`. FILETIME→Unix timestamp conversion. Keyring service `linux-commander-smb`.
- **WebDAV Backend** (`plugins/webdav_plugin.py`) — Writable VFS using `webdavclient3`. Registers `webdav://` and `webdavs://` (HTTPS) schemes. Full-file buffering for reads (download to BytesIO) and write-behind for writes (upload on close). RFC 1123/ISO 8601 date parsing. Keyring services `linux-commander-webdav`, `linux-commander-webdavs`.
- **Credential Management** (`credentials.py`) — `CredentialManager` with platform keyring (Windows Credential Manager, macOS Keychain, GNOME Keyring, KWallet) + modal prompt fallback. Composite keys: `{protocol}://{user}@{host}{path}`. Integrates with all network plugins.
- **Connections Dialog Extended** (`ftp_dialog.py`) — Added SMB, WebDAV, WebDAVS protocol options with protocol-specific fields (Share name for SMB, Path for WebDAV). Save/load sessions with credential references.
- **UI Integration** — "Network" button in volume bar + "Connect to Server..." in volume chooser (Alt+F1/F2). Protocol dropdown includes SMB, WebDAV, WebDAVS.
- **Optional Dependencies** — Added `smb` and `webdav` extras to `pyproject.toml` with mypy overrides.
- All 371 tests pass, ruff clean, mypy clean.

## 2026-07-17 — Phase 1: Core Navigation & Panel Enhancements
- **Hotlist/Bookmarks** (Ctrl+\) — Created `hotlist.py` (JSON storage at `~/.config/linux-commander/hotlist.json`) and `hotlist_dialog.py` (Treeview with Go To/Go To Other/Add/Rename/Remove). Added to Operations menu.
- **Directory History (Back/Forward)** — Added `_history` stack and `_history_index` to `FilePanel`. Implemented `go_back()`, `go_forward()`, `can_go_back()`, `can_go_forward()`. Modified `load()` to accept `add_to_history` parameter. Bound Alt+Left/Right for navigation.
- **Flat View Toggle** — Added `flat_view` state to `FilePanel`. Implemented `list_dir_flat()` in `LocalFileSystem` using `os.walk()` for recursive directory listing. Added "Flat View" toggle to View menu and panel header shows `[FLAT]` indicator.
- **Column Customization** — Replaced hardcoded columns with dynamic `visible_columns` list. Created `columns_dialog.py` with checkbuttons for Name, Size, Modified, Extension, Permissions, Owner, Group (drag to reorder). Persists to settings.json.
- All 370 tests pass (1 pre-existing failure excluded), ruff clean, mypy clean.

## 2026-07-17 — CLAUDE.md update and wiki expansion
- Updated CLAUDE.md with comprehensive project context from README.md and CONTRIBUTING.md
- Added key development workflow, VFS abstraction rules, plugin architecture, cross-platform seams
- Updated wiki index.md with new entity links: vfs, plugins, archiving, file_info, search_engine, image_viewer, compression_dialog, search_dialog, ftp_dialog, settings, syntax
- Added contributing-summary source page
- Documented plugin system architecture, optional dependency extras, syntax highlighting additions

## 2026-07-18 — Phase 2: File Operations (Batch Rename, Sync, Diff, Checksums)
- **Batch Rename** (`file_ops/rename_op.py`) — Regex preview dialog with find/replace, counter, case sensitivity, preserve extension, conflict detection (red highlight). Uses `operations.rename_entries()`.
- **Directory Sync** (`file_ops/sync_op.py`) — Mirror/Update/Backup modes with dry-run preview tree, include/exclude filters, progress reporting.
- **File Compare (Diff)** (`diff_viewer.py`) — Side-by-side/unified diff with syntax highlighting, prev/next change navigation, external tool integration (Meld, vimdiff), directory compare mode.
- **Checksum Generation & Verification** (`file_ops/checksum_op.py`) — MD5/SHA1/SHA256/SHA512, modes: single display, sidecar files, SUM file, verify against checksum file.
- All 371 tests pass, ruff clean, mypy clean.

## 2026-07-17 — CLAUDE.md update and wiki expansion
- Updated CLAUDE.md with comprehensive project context from README.md and CONTRIBUTING.md
- Added key development workflow, VFS abstraction rules, plugin architecture, cross-platform seams
- Updated wiki index.md with new entity links: vfs, plugins, archiving, file_info, search_engine, image_viewer, compression_dialog, search_dialog, ftp_dialog, settings, syntax
- Added contributing-summary source page
- Documented plugin system architecture, optional dependency extras, syntax highlighting additions

## 2026-07-14 — enhanced features (selection, menu, F3, syntax highlighting)
- **Selection enhancements**: `*` marks all files/folders; `+`/`-` pattern dialog with combobox history, case-sensitive and regex options (panel.py, dialogs.py)
- **File menu bar**: Added to CommanderApp with File menu (Font..., Quit/Ctrl+Q) and View menu (Show Hidden Files, Refresh, Sort by Name/Date/Size) (app.py)
- **F3 navigation**: F3 on a directory now enters it (classic OFM behavior); on a file opens viewer as before (app.py cmd_view)
- **Syntax highlighting**: Created `linux_commander/syntax/` package with `c.json` and `py.json` language definitions. `apply_highlighting()` applies keyword/type/string/comment/number coloring via Text widget tags in both viewer and editor (viewer.py)
- All 34 tests pass, ruff clean
- Created wiki directory structure: docs/wiki/, docs/wiki/entities/, docs/wiki/concepts/, docs/wiki/sources/, docs/raw/, docs/outputs/
- Created index.md, log.md, improvements.md
- Copied README.md to docs/raw/README.md
- Created source-summary page for README (docs/wiki/sources/readme-summary.md)
- Created entity pages for all linux_commander modules:
  - docs/wiki/entities/app.md
  - docs/wiki/entities/panel.md
  - docs/wiki/entities/fs.md
  - docs/wiki/entities/operations.md
  - docs/wiki/entities/dialogs.md
  - docs/wiki/entities/viewer.md
  - docs/wiki/entities/volumes.md
  - docs/wiki/entities/platform_util.md
  - docs/wiki/entities/keys.md
- Created concept pages:
  - docs/wiki/concepts/orthodox-file-manager.md
  - docs/wiki/concepts/cross-platform-seams.md
- Updated index.md with links to all entity and concept pages
- This bootstrap entry

## 2026-07-15 — viewer/editor overhaul, Windows improvements, new syntax langs

**Viewer/editor merged into `TextWindow`** (viewer.py):
- F3 opens read-only, F4 opens editable, F4 inside a read-only window promotes
  it to edit mode in place. `view_file()`/`edit_file()` remain as thin wrappers
  so app.py wiring is unchanged.
- **Search (Ctrl+F)**: bar with regex + ignore-case toggles, next/prev, wraps
  at ends. Active match highlighted orange, others dark blue. Works in both
  read-only and edit modes.
- **Hexdump (View menu)**: classic 8-hex-offset, 16-bytes-per-row, ASCII gutter
  display. Raw bytes read via `_read_raw_capped`. Editing disabled while active.
  Toggle restores original text on exit.
- **JSON Pretty-Print (View menu)**: `json.dumps(json.loads(...), indent=json_indent)`.
  Graceful error on invalid JSON. In edit mode marks modified; in read-only does not.
  New `json_indent: int = 2` setting added to `Settings`.
- **Syntax language picker (Syntax menu)**: radiobuttons for all loaded languages
  plus "Auto (by extension)". Re-highlights with chosen language. Fixed incomplete
  tag cleanup in syntax engine (`_clear_syntax_tags` now sweeps all `syntax_*` tags
  including dynamic per-color ones, so no stale colours remain after a switch).
- Cleaned up: duplicate `_viewer_font_dialog` definitions removed; single shared
  `_font_dialog()` helper; single `_center_over()` in viewer.py; redundant
  double-load in `edit_file` eliminated; `_read_raw_capped` / `_format_hexdump`
  added as pure unit-testable helpers.

**Syntax engine additions** (syntax/__init__.py):
- `available_languages() -> list[str]` and `lang_by_name(name) -> SyntaxLang|None`
  for the language picker menu.
- `apply_highlighting(widget, path, lang=None)` — new `lang` override param.
- `_clear_syntax_tags(widget)` — complete sweep replaces hardcoded static list.

**New syntax definitions** (syntax/bash.json, bat.json):
- Bash/sh: keywords, builtins, `$VAR`/`${VAR}` variable highlighting.
- Batch/cmd: keywords via `patterns` (case-insensitive `(?i:...)`), `%VAR%`
  variables, `:label` targets, both `REM` and `::` comments.

**Windows improvements**:
- `_list_volumes_windows()` implemented: enumerates drive letters (A:, C:, ...)
  via `os.listdrives()` (Python 3.12+) with `GetLogicalDrives()` bitmask fallback.
  Pure helper `_drive_letters_from_bitmask()` unit-tested without a real Windows host.
- Windows terminal default changed: `'start "" cmd /k {cmd}'` (opens a new
  window that stays open after the command finishes, replacing the old same-window
  `cmd /c` default). New setting `json_indent: int = 2` added.

**Tests**: 129 -> 152 passing (added test_syntax.py; extended test_viewer.py with
`_read_raw_capped` and `_format_hexdump` tests; extended test_volumes.py with
bitmask helper tests).

## 2026-07-15 — bug fixes: viewer File->Exit, FTP auth, ASCII-only UI

- **Viewer File->Exit menu**: Both `view_file()` (text) and `view_image()` (image) now have a File menu with an Exit item. Previously only a View menu existed; the window could only be closed via keyboard shortcuts or the X button (viewer.py)
- **FTP authentication fix**: `connect_fs()` was passing raw percent-encoded username/password from URL parsing directly to `ftplib.login()`. Passwords containing special characters (`@`, `:`, `#`, etc.) were being sent encoded and rejected by the server. Fixed by applying `unquote()` after URL parsing (plugins/ftp_plugin.py)
- **ASCII-only UI**: Replaced all non-ASCII characters in user-visible strings:
  - Sort arrows `▲`/`▼` in panel header -> `^`/`v` (panel.py)
  - Ellipsis `…` in menu labels and progress dialogs -> `...` (app.py)
  - Em dash `—` in window titles and truncation messages -> `-` (app.py, viewer.py)
  - Right arrow `→` in "Move -> Copy only" dialog title -> `->` (app.py)
- All 129 tests pass

## 2026-07-14 — notepad-style editor implementation (Phases 2-5)
- Rewrote `linux_commander/viewer.py` with `NotepadEditor` class implementing classic Windows 98-style Notepad:
  - **File menu**: New (Ctrl+N), Open... (Ctrl+O), Save (Ctrl+S), Save As... (F12), Exit
  - **Edit menu**: Undo (Ctrl+Z), Cut (Ctrl+X), Copy (Ctrl+C), Paste (Ctrl+V), Select All (Ctrl+A)
  - **View menu**: Status Bar toggle, Word Wrap toggle, Font... dialog
- Status bar shows "Ln X, Col Y" updated on cursor movement
- 3-way unsaved changes prompt (Yes/No/Cancel) on close/New/Open
- Atomic save via temp file + replace (preserves existing behavior)
- Window title shows "filename - Notepad" with * prefix when modified
- Monospace font default, font dialog filters for monospace families
- Kept `edit_file(parent, path, on_saved=None)` signature compatible with `app.py`
- All existing tests pass (34/34), ruff check/format clean
- Updated `docs/wiki/entities/viewer.md` with new editor documentation