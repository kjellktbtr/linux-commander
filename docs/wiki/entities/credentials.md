---
title: Credential Management
type: entity
sources:
  - linux_commander/credentials.py
  - linux_commander/ftp_dialog.py
  - linux_commander/settings.py
related:
  - "[[ftp_dialog]]"
  - "[[settings]]"
  - "[[smb_vfs]]"
  - "[[webdav_vfs]]"
  - "[[jotta_vfs]]"
created: 2026-07-18
updated: 2026-07-18
confidence: high
---

# Credential Management

`linux_commander/credentials.py` provides secure storage and retrieval of network credentials using the system keyring (Windows Credential Manager, macOS Keychain, GNOME Keyring, KWallet) with a fallback to in-memory session storage and user prompts.

## Architecture

### `CredentialManager`

Central class managing the credential lifecycle.

**Initialization**:
```python
manager = CredentialManager(keyring_module=None)
```
- `keyring_module`: Optional injected `keyring` module for testing. If `None`, attempts `import keyring`.

**Methods**:

| Method | Description |
|--------|-------------|
| `get_credentials(protocol, host, path, username)` | Get `(username, password)` tuple or `None` |
| `store_credentials(protocol, host, path, username, password)` | Save credentials to keyring |
| `clear_credentials(protocol, host, path, username)` | Remove stored credentials |
| `prompt_for_credentials(parent, protocol, host, path, username)` | Show dialog to prompt user |

### Credential Resolution Chain

When connecting to a network resource, credentials are resolved in order:

1. **URL credentials** — `user:pass@host` in the connection URL (highest priority)
2. **Keyring** — Platform-native secure storage via `keyring` library
3. **Settings (legacy)** — Stored `FtpSession` objects in `settings.json` (FTP/SFTP/Jotta only)
4. **User prompt** — Modal dialog asking for username/password

```python
# In ftp_dialog.py connect flow:
def _resolve_credentials(protocol, host, path, user):
    # 1. URL credentials already extracted
    # 2. Keyring
    creds = credential_manager.get_credentials(protocol, host, path, user)
    if creds:
        return creds
    # 3. Legacy settings (FTP/SFTP/Jotta)
    if protocol in ("ftp", "sftp", "jotta"):
        session = settings.get_session(host, user, protocol)
        if session and session.password:
            return (session.user, session.password)
    # 4. Prompt
    return credential_manager.prompt_for_credentials(parent, protocol, host, path, user)
```

## Keyring Integration

### Service Names

| Protocol | Keyring Service Name |
|----------|---------------------|
| FTP | `linux-commander-ftp` |
| SFTP | `linux-commander-sftp` |
| SMB | `linux-commander-smb` |
| WebDAV | `linux-commander-webdav` |
| WebDAVS | `linux-commander-webdavs` |
| Jotta | `linux-commander-jotta` |

### Key Format

Keys are composite strings to distinguish multiple accounts on the same host:

```
{protocol}://{username}@{host}{path}
```

Example: `smb://alice@fileserver/share/documents`

### Storage

- **Password only** — Username is part of the key, password is the secret
- **Encryption** — Handled by platform keyring backend
- **Permissions** — Inherited from keyring (user-only access on Unix)

## Prompt Dialog

`prompt_for_credentials(parent, protocol, host, path, username)` shows a modal dialog:

- **Title**: "Credentials for {protocol}://{host}{path}"
- **Fields**: Username (pre-filled), Password (hidden)
- **Buttons**: OK / Cancel
- **Returns**: `(username, password)` or `None` if cancelled

Uses `tkinter.simpledialog` with `ttkbootstrap` styling.

## Legacy Settings Storage

For backward compatibility, `FtpSession` objects in `settings.json` store credentials for FTP/SFTP/Jotta:

```python
@dataclass(slots=True)
class FtpSession:
    name: str
    host: str
    port: int
    user: str
    password: str  # plaintext in JSON (chmod 600)
    protocol: str = "ftp"  # "ftp", "sftp", "jotta"
    ...
```

**Security note**: Passwords in `settings.json` are plaintext. Keyring is preferred. The app migrates to keyring on first successful connection.

## Usage in VFS Plugins

Each protocol plugin's `connect_fs(url)` uses the credential chain:

```python
# smb_plugin.py / webdav_plugin.py / ftp_plugin.py / sftp_plugin.py / jotta_plugin.py
def connect_fs(url: str) -> FileSystem:
    parsed = urlparse(url)
    user = unquote(parsed.username) if parsed.username else ""
    password = unquote(parsed.password) if parsed.password else ""
    
    # If no password in URL, try keyring
    if not password:
        creds = credential_manager.get_credentials(
            parsed.scheme, parsed.hostname, parsed.path, user
        )
        if creds:
            user, password = creds
    
    # ... create connection with user/password ...
```

## Testing

No dedicated test file. Keyring behavior tested indirectly via FTP/SMB/WebDAV connection flows in integration tests (require live servers).

## Security Considerations

- Keyring is **preferred** over settings file
- Settings file is `chmod 0o600` on Unix
- No credentials logged or printed
- Prompt dialog uses `show='•'` for password field
- In-memory only during session (not persisted unless user saves session in Connections dialog)