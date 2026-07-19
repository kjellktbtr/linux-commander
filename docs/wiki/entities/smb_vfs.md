---
title: SMB/CIFS VFS Backend
type: entity
sources:
  - linux_commander/plugins/smb_plugin.py
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

# SMB/CIFS VFS Backend

`linux_commander/plugins/smb_plugin.py` provides a **writable** VFS backend for browsing SMB/CIFS (Samba/Windows) shares using the `smbprotocol` library (modern SMB2/3 client).

## Registration

- **Scheme**: `smb` (registered in `SCHEMES` tuple)
- **Optional dependency**: `smbprotocol` (install via `pip install linux-commander[smb]`)
- **Auto-discovered**: Yes, via `plugins._discover()` → `SCHEME_MAP["smb"] = connect_fs`

## URL Format

```
smb://[user:pass@]host/share[/path]
```

Examples:
- `smb://server/share` — guest access to share root
- `smb://user:pass@server/share` — credentials in URL
- `smb://user@server/share/folder` — user only, password from keyring

If credentials are not in the URL, they are resolved via the credential provider chain (keyring → prompt).

## Implementation

### `SmbFileSystem`

Writable VFS backend (`writable = True`) backed by an SMB connection.

**Constructor**:
```python
SmbFileSystem(
    connection: Connection,
    session: Session,
    tree: TreeConnect,
    host: str,
    share: str,
    user: str,
    path: str = "/",
)
```

**Operations**:
| Method | Description |
|--------|-------------|
| `list_dir(path)` | Lists directory using `File.query_directory()` |
| `stat(path)` | Gets file info via `File.get_info()`; converts FILETIME to Unix timestamp |
| `open_read(path)` | Opens file with `FILE_READ_DATA`, returns `_SMBFile` wrapper |
| `open_write(path)` | Creates/overwrites with `FILE_WRITE_DATA`, returns `_SMBFile` wrapper |
| `mkdir(path)` | Creates directory with `FILE_DIRECTORY_FILE` |
| `delete(path)` | Deletes file or directory using `FILE_DELETE_ON_CLOSE` |
| `rename(src, dst)` | Uses `FileRenameInfo` with `replace_if_exists=True` |
| `close()` | Disconnects tree, session, connection |

### `_SMBFile`

File-like wrapper for an SMB file handle. Implements:
- `read(size)`, `write(b)`, `seek(offset, whence)`, `tell()`
- `flush()`, `close()`, `closed` property
- `readable()`, `writable()`, `seekable()`, `fileno()`, `isatty()`
- Context manager (`__enter__`, `__exit__`)

### `connect_fs(url) -> SmbFileSystem`

Parses `smb://` URL, establishes connection:
1. Parse host, share, credentials, port (default 445)
2. Create `Connection` → `connect()`
3. Create `Session(connection, user, password)` → `connect()`
4. Create `TreeConnect(session, f"\\\\{host}\\{share}")` → `connect()`
5. Return `SmbFileSystem(...)` with sub-path from URL

## Credential Integration

Uses `CredentialManager` from `linux_commander/credentials.py`:

```python
# In ftp_dialog.py connect flow
creds = credential_manager.get_credentials("smb", host, share, user)
# creds = (username, password) or None
```

Keyring service name: `"linux-commander-smb"`

## UI Integration

- **Connections Dialog** (Ctrl+F / "FTP" menu): Added "SMB" protocol option
- **Protocol-specific fields**: Share name (required), Port (default 445)
- **Volume bar**: Network button → "Connect to Server..." → SMB in dropdown

## Timestamp Handling

SMB uses **FILETIME** (100-ns intervals since 1601-01-01 UTC). Conversion to Unix timestamp:
```python
mtime = (filetime / 10_000_000) - 11644473600
```

## Error Handling

All `smbprotocol.SMBException` wrapped in `OSError` with descriptive paths.

## "Says not installed" is usually an environment mismatch, not a bug (2026-07-18)

If `smbprotocol` is genuinely `pip install`ed but the app/Optional Dependencies dialog still reports SMB as unavailable, the most likely cause is that the app is running under a *different* Python interpreter/venv than the one the extra was installed into — e.g. a stale `VIRTUAL_ENV` env var pointing at an unrelated venv that predates this project, while `uv run` (which correctly ignores that stale var, with a warning) resolves to the project's own `.venv`. Confirm which interpreter actually launches the app (`uv run linux-commander` always uses the right one) before assuming the extras-detection logic itself is broken -- `install_extras.py`'s `_import_name()`/`_IMPORT_NAME_OVERRIDES` were checked and are correct for `smbprotocol`/`keyring` (no dash-to-underscore mismatch, no override needed).

Separately, the Connections dialog's port field used to default to 21 (FTP's port) when SMB was selected fresh, rather than SMB's own 445 -- a real bug, now fixed. See [[ftp_dialog]]'s "Port-default bug fixed" section.

## Testing

No dedicated test file yet. Requires real SMB server for integration testing.