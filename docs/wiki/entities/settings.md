---
title: Settings
type: entity
sources:
  - linux_commander/settings.py
  - CONTRIBUTING.md
related:
  - "[[app]]"
  - "[[ftp_dialog]]"
  - "[[operations]]"
created: 2026-07-17
updated: 2026-07-22
confidence: high
---

# Settings — Configuration Persistence

`linux_commander/settings.py` defines the `Settings` dataclass and handles load/save to `settings.json` in the platform config directory (XDG/`%APPDATA%`/`~/Library/Application Support`). File is `chmod 0o600` on Unix.

## Settings Dataclass

```python
@dataclass
class Settings:
    # UI
    panel_font: FontSpec = FontSpec("TkDefaultFont", 10)
    editor_font: FontSpec = FontSpec("TkFixedFont", 10)
    viewer_font: FontSpec = FontSpec("TkFixedFont", 10)
    theme: str = "flatly"           # ttkbootstrap theme name
    show_icons: bool = True
    show_ext_column: bool = True
    image_extensions: tuple[str, ...] = (".png", ".jpg", ...)

    # Viewer/Editor
    json_indent: int = 2

    # Terminal commands (per-platform templates)
    terminal_linux: str = "x-terminal-emulator -e {cmd}"
    terminal_windows: str = 'start "" cmd /k {cmd}'

    # Security
    stored_keys: dict[str, StoredKey] = field(default_factory=dict)  # name -> StoredKey
    ftp_sessions: dict[str, FtpSession] = field(default_factory=dict)  # name -> FtpSession

    # Per-panel state (restored on launch)
    panel_states: list[PanelState] = field(default_factory=list)  # [left, right]
```

### Nested Types

| Type | Fields |
|------|--------|
| `FontSpec` | `family: str`, `size: int` |
| `StoredKey` | `name: str`, `key_b64: str` (base64-encoded 256-bit key) |
| `FtpSession` | `name`, `protocol` ("ftp"/"sftp"), `host`, `port`, `user`, `password`, `path`, `private_key`, `key_passphrase` |
| `PanelState` | `path`, `tagged`, `sort_by`, `sort_reverse`, `show_hidden` |

## Load / Save

```python
def load_settings() -> Settings: ...
def save_settings(settings: Settings) -> None: ...
```

- Load: reads `settings.json`, merges with defaults (missing keys get defaults)
- Save: atomic write via temp file + rename; `0o600` on Unix
- Called on app startup (load) and on any settings change (save) — debounced in `app.py`

### SOLID Refactoring — Auto-serialization (2026-07-22)

Manual field-by-field `to_dict()`/`from_dict()` mapping was replaced with `dataclasses.asdict()` for serialization and `**kwargs` unpacking for deserialization. This eliminates boilerplate and prevents drift when new fields are added to the `Settings` dataclass.

## Key Management

- **StoredKey**: 256-bit ChaCha20-Poly1305 key, generated via `secrets.token_bytes(32)`, stored base64
- UI: **File > Manage Keys...** — add/remove/list named keys
- Used by encryption (Operations menu, compression dialog, `.crp` browsing) as alternative to password

## Session Management

- **FtpSession**: persisted FTP/SFTP connections (name, protocol, host, port, user, password, path, private key path, key passphrase)
- UI: **File > Connections...** — add/edit/delete/connect
- SFTP private-key auth: key path + optional passphrase; tried before password, then SSH agent

## Duplicate Finder Threshold (2026-07-18)

`duplicate_large_file_mb: float = 10.0` — persisted default for the duplicate-finder's "large file" content-compare threshold (see [[duplicate_op]]). Files whose checksums already match get a full byte-for-byte content comparison automatically if they're at or below this size; above it, the user is prompted per scan (with an "apply to all" option) before that comparison runs, since a full read of many large files can be slow. Adjustable per-scan in the Find Duplicates dialog; that per-scan value is not itself persisted, only the dialog's pre-filled default is.

## Cross-Reference

- [[app]] — CommanderApp loads/saves settings, applies theme/fonts
- [[ftp_dialog]] — Connections manager UI
- [[operations]] — Encrypt/Decrypt operations use stored keys
- [[archiving]] — Compression dialog uses stored keys for encryption stage
- [[duplicate_op]] — duplicate finder's large-file content-compare threshold