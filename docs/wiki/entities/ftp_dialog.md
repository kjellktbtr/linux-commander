---
title: FTP Dialog — Connections Manager (FTP/SFTP)
type: entity
sources:
  - linux_commander/ftp_dialog.py
  - linux_commander/plugins/ftp_plugin.py
  - linux_commander/plugins/sftp_plugin.py
  - CONTRIBUTING.md
related:
  - "[[vfs]]"
  - "[[plugins]]"
  - "[[settings]]"
  - "[[volumes]]"
  - "[[platform_util]]"
created: 2026-07-17
updated: 2026-07-18
confidence: high
---

# FTP Dialog — Connections Manager

`linux_commander/ftp_dialog.py` implements **File > Connections...** — a modal manager for saved FTP/SFTP sessions.

**Note**: this page predates SMB/WebDAV/WebDAVS/Jottacloud support being added to the same dialog (see [[smb_vfs]], [[webdav_vfs]], [[jotta_vfs]]) — the tables below only cover the original FTP/SFTP fields and haven't been fully updated for the later protocols.

## Port-default bug fixed (2026-07-18)

`_update_protocol_state()`'s port-default logic used to be hardcoded to only ever toggle between ftp(21) and sftp(22):
```python
other_default = _DEFAULT_PORTS["ftp" if protocol == "sftp" else "sftp"]
if port_var.get() == other_default:
    port_var.set(_DEFAULT_PORTS[protocol])
```
`_DEFAULT_PORTS` already correctly had `"smb": 445, "webdav": 80, "webdavs": 443` — but selecting SMB from a fresh dialog (which starts at ftp's port 21) never actually switched the port field to 445, since 21 only matched the `other_default` check when toggling with sftp. Fixed to swap whenever the field holds *any* known protocol's default, not just ftp/sftp:
```python
if port_var.get() in _DEFAULT_PORTS.values():
    port_var.set(_DEFAULT_PORTS.get(protocol, port_var.get()))
```
A manually-typed custom port never matches any default, so it's left untouched; editing an existing session whose port already matches its own protocol's default is a no-op (idempotent). Verified via a headless Tk script exercising the exact `IntVar` transitions (fresh→smb→webdav→webdavs→sftp, custom-port preservation, idempotent re-entry, jotta's no-port case) rather than driving the full dialog UI.

## Session Fields

| Field | FTP | SFTP |
|-------|-----|------|
| Name | ✅ | ✅ |
| Protocol | `ftp` | `sftp` |
| Host | ✅ | ✅ |
| Port | 21 (default) | 22 (default) |
| User | ✅ | ✅ |
| Password | ✅ | ✅ |
| Initial Path | ✅ | ✅ |
| Private Key Path | ❌ | ✅ |
| Key Passphrase | ❌ | ✅ |

## Authentication Order (SFTP)

1. Private key (if set) — key file + optional passphrase
2. Password (if set)
3. SSH agent / default keys (`~/.ssh/id_rsa`, etc.)

Host key verification: **Trust On First Use** — saved to `~/.ssh/known_hosts`.

## URL Support

Volume chooser (Alt+F1/F2) accepts URLs:
```
ftp://[user:pass@]host[:port][/path]
sftp://[user:pass@]host[:port][/path]
```
- Anonymous login if no credentials
- **Private key auth cannot be expressed as URL** — must use Connections dialog

## Persistence

Sessions saved in `settings.json` → `Settings.ftp_sessions: dict[name, FtpSession]`.

## Connection Flow

1. User selects session → **Connect**
2. `connect_fs(url)` called (via `plugins.SCHEME_MAP`)
3. `FtpFileSystem` / `SftpFileSystem` returned
4. Panel loads root of connection (`/`)
5. Navigation works like local: F3 view, F5 download, Enter on dir
6. Closing panel or navigating away from root → clean disconnect

## Cross-Reference

- [[vfs]] — `FtpFileSystem`, `SftpFileSystem` implement `FileSystem`
- [[plugins]] — `ftp_plugin.py` / `sftp_plugin.py` register `connect_fs`
- [[settings]] — `FtpSession` dataclass, persisted in settings
- [[volumes]] — "Connect to FTP..." entry in volume chooser
- [[platform_util]] — not used here (network only)