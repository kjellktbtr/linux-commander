---
title: WebDAV VFS Backend
type: entity
sources:
  - linux_commander/plugins/webdav_plugin.py
  - linux_commander/credentials.py
  - linux_commander/ftp_dialog.py
related:
  - "[[plugins]]"
  - "[[vfs]]"
  - "[[credentials]]"
  - "[[ftp_dialog]]"
created: 2026-07-18
updated: 2026-07-18
confidence: high
---

# WebDAV VFS Backend

`linux_commander/plugins/webdav_plugin.py` provides a **writable** VFS backend for browsing WebDAV servers using the `webdavclient3` library. Supports both WebDAV (HTTP) and WebDAVS (HTTPS) protocols.

## Registration

- **Schemes**: `webdav`, `webdavs` (registered in `SCHEMES` tuple)
- **Optional dependency**: `webdavclient3` (install via `pip install linux-commander[webdav]`)
- **Auto-discovered**: Yes, via `plugins._discover()` → `SCHEME_MAP["webdav"] = connect_fs`, `SCHEME_MAP["webdavs"] = connect_fs`

## URL Format

```
webdav://[user:pass@]host[:port][/path]
webdavs://[user:pass@]host[:port][/path]  # HTTPS
```

Examples:
- `webdav://server/remote.php/webdav/` — guest access
- `webdavs://user:pass@server:443/remote.php/webdav/` — HTTPS with credentials
- `webdav://user@server/dav/` — user only, password from keyring

Default ports: 80 (webdav), 443 (webdavs).

## Implementation

### `WebDAVFileSystem`

Writable VFS backend (`writable = True`) backed by a `webdav3.client.Client`.

**Constructor**:
```python
WebDAVFileSystem(
    client: WebDAVClient,
    host: str,
    base_path: str,
    user: str = "",
)
```

- `base_path`: The remote root path (from URL path component), e.g., `/remote.php/webdav/`
- `display_prefix`: Shows as `webdav://host!` in UI

**Operations**:
| Method | Description |
|--------|-------------|
| `list_dir(path)` | Uses `client.list(path, get_info=True)`; parses RFC 1123 / ISO 8601 dates |
| `stat(path)` | Uses `client.info(path)` |
| `open_read(path)` | Downloads entire file to `BytesIO` via `client.download_sync()` |
| `open_write(path)` | Buffers writes to `BytesIO`, uploads on `close()` via `client.upload_sync()` |
| `mkdir(path)` | Uses `client.mkdir(path)` |
| `delete(path)` | Uses `client.clean(path)` for both files and directories |
| `rename(src, dst)` | Uses `client.move(src, dst)` (COPY+DELETE fallback) |
| `close()` | No-op (webdav3 uses per-request connections) |

### `_WebDAVFile`

File-like wrapper for WebDAV file handles. Since WebDAV doesn't support random access reads easily (no Range header support in webdav3), **reading downloads the entire file to a `BytesIO` buffer**, and **writing buffers to `BytesIO` then uploads on close**.

Implements:
- `read(size)`, `write(b)`, `seek(offset, whence)`, `tell()`
- `flush()`, `close()`, `closed` property
- `readable()`, `writable()`, `seekable()`, `fileno()`, `isatty()`
- Iterator protocol (`__iter__`, `__next__`), `writelines()`
- Context manager (`__enter__`, `__exit__`)

### `connect_fs(url) -> WebDAVFileSystem`

Parses `webdav://` or `webdavs://` URL:
1. Parse scheme, host, port, path, credentials
2. Build `webdav3` client options:
   ```python
   options = {
       "webdav_hostname": base_url,      # http(s)://host[:port]
       "webdav_login": user,
       "webdav_password": password,
       "webdav_root": base_path,         # root path for operations
   }
   client = WebDAVClient(options)
   ```
3. Verify connection with `client.list(base_path)`
4. Return `WebDAVFileSystem(client, host, base_path, user)`

## Credential Integration

Uses `CredentialManager` from `linux_commander/credentials.py`:

```python
creds = credential_manager.get_credentials("webdav", host, base_path, user)
# or for webdavs
creds = credential_manager.get_credentials("webdavs", host, base_path, user)
```

Keyring service names: `"linux-commander-webdav"`, `"linux-commander-webdavs"`

## UI Integration

- **Connections Dialog** (Ctrl+F / "FTP" menu): Added "WebDAV" and "WebDAVS (HTTPS)" protocol options
- **Protocol-specific fields**: Port (auto-defaults to 80/443), Path (required)
- **Volume bar**: Network button → "Connect to Server..." → WebDAV/WebDAVS in dropdown

## Timestamp Handling

WebDAV dates come in various formats (RFC 1123, ISO 8601). Parsing tries:
- `%a, %d %b %Y %H:%M:%S %Z` (RFC 1123)
- `%Y-%m-%dT%H:%M:%SZ` (ISO 8601 UTC)
- `%Y-%m-%d %H:%M:%S` (naive local)

## Error Handling

All `webdav3.exceptions.WebDavException` wrapped in `OSError` with descriptive paths.

## Testing

No dedicated test file yet. Requires real WebDAV server for integration testing.