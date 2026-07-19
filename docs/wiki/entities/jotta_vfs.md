---
title: Jottacloud VFS Backend
type: entity
sources:
  - linux_commander/jotta_api.py
  - linux_commander/plugins/jotta_plugin.py
  - linux_commander/ftp_dialog.py
related:
  - "[[plugins]]"
  - "[[vfs]]"
  - "[[credentials]]"
  - "[[ftp_dialog]]"
  - "[[settings]]"
  - "[[fs]]"
  - "[[operations]]"
created: 2026-07-18
updated: 2026-07-18
confidence: high
---

# Jottacloud VFS Backend

`linux_commander/plugins/jotta_plugin.py` (backed by the hand-rolled JFS client in `linux_commander/jotta_api.py`) provides a **writable** VFS backend for Jottacloud, Norway's consumer cloud storage service. Unlike the other network plugins, this isn't wrapping a third-party client library — `jotta_api.py` implements the undocumented JFS (Jottacloud File System) XML REST API directly, based on reverse-engineering by `mifi/jotta` (TypeScript) and `albertony/jafs` (C#).

## Registration

- **Scheme**: `jotta` (registered in `SCHEMES` tuple)
- **Optional dependency**: `httpx` + `pydantic` (the `jotta` extra)
- **Auto-discovered**: Yes, via `plugins._discover()` → `SCHEME_MAP["jotta"] = connect_fs`

## Authentication model (unusual — read before touching)

Jottacloud has no API key. Auth starts from a **Personal Login Token** (a base64-encoded JSON blob containing a username + one-time auth code, obtained from the Jottacloud web UI at `https://www.jottacloud.com/web/secure`). That token is exchanged once for an OAuth2 access/refresh token pair via `id.jottacloud.com` — **the login token is single-use**; a second exchange fails with `invalid_grant`.

Consequently there are two distinct connection paths:

- `connect_fs(url)` (`jotta://TOKEN@device/mountpoint/path`) — always does a fresh login-token exchange. Only suitable for one-off/bootstrap connections; **not** used by the Connections dialog for saved sessions.
- `RemoteConnectionDialog._connect_jotta_session()` in `ftp_dialog.py` — the real path for saved sessions. On first connect it exchanges the login token and persists the resulting `access_token`/`refresh_token`/`token_expiry` onto the `JottaSession` (via `on_token_update` callback → `_persist_jotta_token()` → `save_settings()`). On every later connect it calls `SyncJottaAPI.restore(token, username)` to reuse the persisted refresh token instead of re-exchanging the dead login token, refreshing if expired.

`JottaAPI._ensure_valid_token()` proactively refreshes on any request if the access token is within 60s of expiry (`AuthToken.is_expired`); a 401 mid-request also triggers one refresh-and-retry.

## Path structure

JFS paths are `/username/device/mountpoint/path...`. `JottaFileSystem`'s VFS root maps to one specific `(device, mountpoint)` pair (e.g. device `Jotta`, mountpoint `Archive` — Jottacloud's defaults for the official desktop client), with an optional `root_path` sub-path within it. `_to_jfs_path()`/`_to_vfs_path()` convert between the VFS's root-relative parts tuple and the mountpoint-relative JFS path string.

## Implementation

### `JottaFileSystem` (`writable = True`)

| Method | JFS operation |
|--------|--------|
| `list_dir(path)` | `GET` the folder path, parses `<folders>`/`<files>` XML children |
| `stat(path)` | Same `GET`, scans the parent listing for an exact name match (JFS has no single-entry stat verb distinct from listing) |
| `open_read(path)` | `GET ?mode=bin`, buffers the full response into `BytesIO` |
| `open_write(path)` | Returns a `_JottaUploadFile` (buffers to `BytesIO`; uploads whole content on `close()`) |
| `mkdir(path)` | `POST ?mkDir=true` |
| `delete(path)` | `stat()`s first to resolve `is_dir`, then `POST ?dl=true` (file) or `?dlDir=true` (folder) |
| `rename(src, dst)` | `stat()`s `src` to resolve `is_dir`, then `POST ?mv=<abs>` (file) or `?mvDir=<abs>` (folder) |
| `close()` | `SyncJottaAPI.close()` — closes both underlying `httpx.AsyncClient`s |

All JFS-layer exceptions (`JottaAuthError`, `JottaAPIError`) are caught and re-raised as `OSError`, matching every other VFS backend's error contract.

**Delete is trash, not permanent.** JFS has no separate hard-delete verb exposed here — `dl`/`dlDir` moves the item to Jottacloud's trash, recoverable from the web UI. This was a deliberate choice (matches how the official clients behave) over a `rm=true` permanent-delete parameter that some JFS implementations also expose.

### `_JottaUploadFile`

Write-mode file handle, structurally identical to `_WebDAVFile` in `webdav_plugin.py`: buffers all writes to an in-memory `BytesIO` and only touches the network in `close()`. This is required, not just convenient — JFS upload needs the file's md5 and byte size known *before* the request (they're sent as headers), so there's no way to stream an upload incrementally through this API. Not tuned for very large files.

### JFS write-endpoint reference (`jotta_api.py`)

All write ops are `POST` to the same path used for `GET`s (`jfs.jottacloud.com/jfs/<user>/<device>/<mountpoint>/<path>`):

| Operation | Query params | Headers / body |
|---|---|---|
| `upload_file()` | `umode=nomultipart&cphash=<md5hex>` | `JMd5`, `JSize`, `JCreated`, `JModified`, `Content-Type: application/octet-stream`; body = raw bytes |
| `create_folder()` | `mkDir=true` | — |
| `delete_path(is_dir=False)` | `dl=true` | — |
| `delete_path(is_dir=True)` | `dlDir=true` | — |
| `move_path(is_dir=False)` | `mv=<absolute JFS path>` | — |
| `move_path(is_dir=True)` | `mvDir=<absolute JFS path>` | — |

The `mv`/`mvDir` target must be the **absolute** JFS path (`/username/device/mountpoint/newpath`), not mountpoint-relative — `move_path()` re-runs the destination through `_build_jfs_path()` to build it, same helper the read paths use.

`_fmt_jfs_date()` formats Python `datetime`s into the `%Y-%m-%dT%H:%M:%SZ` upload-header format; it's the write-side inverse of `_parse_jotta_date()`, which tolerates several JFS date variants on the read side (notably `%Y-%m-%d-T%H:%M:%SZ` — dash *and* T).

### `_jfs_request`/`_api_request` header-retry fix

Both request helpers pop `headers` out of `**kwargs` before the first attempt, then re-pop on the 401-retry path. Since the key had already been removed from `kwargs`, the retry used to silently drop any caller-supplied headers (fell back to `{}` + auth/Accept headers only). This was latent and harmless for the old read-only GETs (no caller ever passed custom headers), but would have broken uploads specifically: a 401 mid-upload would retry the `POST` *without* `JMd5`/`JSize`, and JFS rejects uploads missing those headers (see rclone/rclone#2462). Fixed by keeping `caller_headers` in a local variable instead of re-popping from the (already-emptied) `kwargs` dict.

## `SyncJottaAPI` cross-thread crash fix (2026-07-18)

`SyncJottaAPI` used to fetch a **thread-local** event loop per call (`asyncio.get_event_loop()`, creating a new one per calling thread on demand). But `JottaAPI.__init__` creates its `httpx.AsyncClient` instances (`_api_client`/`_jfs_client`) **once** and reuses them for the object's lifetime — and httpx/anyio/httpcore lazily bind the client's connection-pool internals (SSL streams, locks, `asyncio.Event` objects) to whichever event loop *first* drives a request through them.

Since a real session calls into the same `JottaFileSystem`/`SyncJottaAPI` instance from **two different threads** — a background copy/move/delete worker thread (`operations.py`, via `progress_dialog.run_with_progress`) during a transfer, and the Tk main thread immediately afterward (e.g. `app.py`'s post-copy `_refresh_both_panels()` → `list_dir()`) — the second thread to call in got a *different* event loop than the one the client's internals were already bound to, crashing with:

```
RuntimeError: <asyncio.locks.Event object ...> is bound to a different event loop
```

This is exactly why "browse a Jottacloud folder, copy a directory into it, watch the panel fail to auto-refresh" produced a Tkinter callback traceback — the auto-refresh call itself was crashing, silently (Tk callback exceptions print to the console but don't stop the app), on every cross-thread call following the first.

**Fix**: `SyncJottaAPI` now starts one persistent background thread running its own event loop (`asyncio.new_event_loop()` + `loop.run_forever()`) for the object's entire lifetime, and routes every call through `asyncio.run_coroutine_threadsafe(coro, self._loop)` instead of fetching a per-call thread-local loop. `close()` stops the loop (`call_soon_threadsafe(loop.stop)`) and joins the thread.

Regression test: `tests/test_jotta_api.py::test_sync_api_survives_calls_from_two_different_threads`. Note `httpx.MockTransport` (used by every other test in the file) **cannot** reproduce this bug — it bypasses httpcore's real connection pool entirely, so no loop-bound primitives are ever created. The regression test instead fakes just enough of httpcore's actual behavior (a shared `asyncio.Event` that every "request" `.wait()`s on, mirroring a connection-pool lock) to make the real loop-affinity violation trigger — verified by temporarily reverting the fix and confirming the test fails with the exact same `RuntimeError` shown above.

## Folder dates show 1970-01-01 (2026-07-18)

JFS listing XML **never includes a `<modified>` element on `<folder>` elements** — only `<file><currentRevision><modified>`. This isn't a parsing bug on this app's side (confirmed against real captured JFS responses in `tests/test_jotta_api.py`'s fixtures); JFS genuinely has no folder-modified-time concept to expose in a listing. `_parse_folder_element` correctly leaves `folder.modified` as `None` when absent, but `jotta_plugin.py`'s `list_dir`/`stat` converted that `None` into `mtime=0.0`, and `fs.py`'s `format_mtime()` unconditionally rendered any timestamp — including `0.0` — as a real formatted date (`1970-01-01 01:00`), which reads as a plausible (if very old) real date rather than "unknown".

Fixed at the shared display layer, not here: `format_mtime()` now returns `""` for any `timestamp <= 0` (see [[fs]]) — since `mtime=0.0` is already the codebase-wide sentinel for "this backend has no modification time for this entry" (every VFS plugin's synthetic `..` entry, and every archive-internal directory, already used it). This fixes the same misleading-epoch-date issue for ZIP/TAR/RAR-internal directories too, not just Jottacloud folders.

## `stat()` searched the wrong listing entirely (2026-07-18)

`stat(path)` used to `GET` `path`'s **own** JFS path and search the result's `folders`/`files` for an entry named `path.name`. That's backwards: a `GET` on a folder's own path returns that folder's **children** (its listing), not information about the folder itself — a folder essentially never has a child with its own exact name, so `stat()` on **any Jottacloud folder** always raised `OSError("Not found: ...")`. This was latent from before this repo's write support existed (nothing called `stat()` on a Jotta path before), but `delete()`/`rename()` both call `self.stat(path)` internally to decide `dl` vs `dlDir` / `mv` vs `mvDir` — so every folder delete or rename failed immediately with a misleading "doesn't exist" error, reported by a user as "deleting a sub folder on Jottacloud says it doesn't exist." (Files were unaffected: a `GET` on a *file's* own path returns a `<file>` document describing itself, not a listing, so the old logic accidentally worked there.)

Fixed: `stat()` now lists `path.parent` and searches *that* listing for a child named `path.name` — the same thing `list_dir()` already does correctly for every entry it displays. There is no lighter-weight single-item JFS endpoint to fall back to instead.

## Deleted items don't disappear from listings (2026-07-18)

JFS keeps a trashed file or folder in subsequent listings of its parent, just marked via a `deleted` XML attribute (confirmed against `jottalib`'s `JFSFile`/`JFSFolder.deleted` property, which every real JFS client must check explicitly — JFS does not filter this server-side). `JottaFolder` already had a `deleted: bool` field, but nothing ever read it; `JottaFile` didn't even have the field. So a successful `dl=true`/`dlDir=true` delete call actually worked, but the item kept showing up in the panel afterward — reported as "delete doesn't say it fails, but the file doesn't disappear either." Fixed: added `deleted: bool = False` to `JottaFile` (parsed from the `deleted` XML attribute in `_file_from_elem`, mirroring how `_parse_folder_element` already handled it for folders), and `jotta_plugin.py`'s `list_dir()`/`stat()` now skip any entry with `deleted=True`.

## Testing

- `tests/test_jotta_api.py` — pure-function/dataclass tests (date parsing, token expiry, XML listing parsing) plus request-shape tests for the four write methods using `httpx.MockTransport` (asserts exact query params/headers sent, no real network), plus the cross-thread event-loop regression test above.
- `tests/test_jotta_plugin.py` — `JottaFileSystem` write-path tests with a `MagicMock`-based fake `SyncJottaAPI` (mirrors `tests/test_sftp_plugin.py`'s pattern): `open_write`→`upload_file`, `mkdir`→`create_folder`, `delete`→`delete_path(is_dir=...)`, `rename`→`move_path(is_dir=...)`, plus regression tests for the `stat()` parent-lookup fix and deleted-item filtering above.
- Both skip entirely (`pytest.importorskip`) when the `jotta` extra (httpx/pydantic) isn't installed.
- No integration test against a real Jottacloud account — needs a live Personal Login Token, which is inherently manual/interactive.

## Cross-Reference

- [[vfs]] — `FileSystem` ABC these methods implement
- [[plugins]] — auto-discovery mechanism (`SCHEMES` tuple, `connect_fs`)
- [[credentials]] — legacy `FtpSession`/`JottaSession` token persistence in `settings.json` (see [[settings]])
- [[ftp_dialog]] — `RemoteConnectionDialog._connect_jotta_session()`, the real saved-session connect path
