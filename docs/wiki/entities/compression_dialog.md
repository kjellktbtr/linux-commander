---
title: Compression Dialog — Shift+F5 Archive Builder
type: entity
sources:
  - linux_commander/compression_dialog.py
  - linux_commander/archiving.py
  - CONTRIBUTING.md
related:
  - "[[archiving]]"
  - "[[operations]]"
  - "[[settings]]"
  - "[[vfs]]"
created: 2026-07-17
updated: 2026-07-17
confidence: high
---

# Compression Dialog — Shift+F5 Archive Builder

`linux_commander/compression_dialog.py` is the **Shift+F5** dialog for creating new archives from tagged files (or cursor file).

## UI Layout

| Control | Purpose |
|---------|---------|
| Archive name | Output filename (path relative to target panel) |
| Container | Combobox: zip / tar / grp / 7z¹ / iso¹ |
| Codec | Combobox: none / gz / bz2 / xz / zst² |
| Level | Spinbox 0–9 (codec-dependent) |
| Encrypt output | Checkbox — enables credential selector |
| Credential | Password or stored key (from Settings) |

¹ Needs `archives` extra. ² Needs Python 3.14+.

## Validation

- Disables unavailable container/codec combos (missing extras)
- Validates output name doesn't conflict (unless overwriting)
- Shows estimated extension: `name.container.codec.crp` (e.g., `backup.tar.gz.crp`)

## Execution Flow

1. User clicks **Compress** → `run_with_progress()` (background thread)
2. Collects tagged files → list of `(src_fs, src_path, arcname)`
3. Calls `archiving.build_archive(sources, dest, container, codec, level, encrypt, credential)`
4. `build_archive`:
   - Creates container with codec (streaming where possible)
   - If `encrypt` → wraps with `encrypt_stream()` (ChaCha20-Poly1305, `.crp`)
   - Writes to destination `FileSystem` via `open_write()`
5. Progress reported via `ProgressCallback`
6. On success: target panel refreshes, cursor on new archive

## Encryption Integration

- Credential modes: **Password** (PBKDF2 200k iter) or **Stored Key** (from Settings → Manage Keys)
- `.crp` appended as final extension regardless of container/codec
- Same format as Operations → Encrypt / Enter on `.crp`

## Cross-Reference

- [[archiving]] — `build_archive`, `encrypt_stream`, container×codec matrix
- [[operations]] — same progress pattern (ProgressDialog + background thread)
- [[settings]] — stored encryption keys, default terminal templates
- [[vfs]] — writes via `FileSystem.open_write`, works on any writable FS (local, archive, FTP)