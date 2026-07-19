---
title: File Info — Shift+F3 File Properties
type: entity
sources:
  - linux_commander/file_info_dialog.py
  - CONTRIBUTING.md
related:
  - "[[vfs]]"
  - "[[operations]]"
  - "[[platform_util]]"
created: 2026-07-17
updated: 2026-07-17
confidence: high
---

# File Info — Shift+F3 File Properties

`linux_commander/file_info_dialog.py` shows detailed file metadata on a **background thread** with progress bar.

## Displayed Information

| Category | Fields |
|----------|--------|
| **Basic** | Name, Path, Size (bytes + human), Modified time |
| **Type** | `file` command output (Linux/macOS) or `mimetypes` guess (Windows) |
| **POSIX** | Permissions (rwx), Owner, Group, Link count |
| **Checksums** | MD5, SHA-1, SHA-256 (hex) |

## Background Computation

- Runs on worker thread via `progress_dialog.run_with_progress()`
- Checksums computed in chunks (streaming, not memory-bound)
- **Works on any VFS path** — remote (FTP/SFTP) or archive-mounted files are materialized to temp file first via `plugins.materialize()`
- Progress bar shows bytes processed / total size
- Cancel button stops computation

## POSIX Fields

- Owner/Group omitted on Windows (N/A)
- Permissions shown as `rwxr-xr-x` + octal (e.g., `0755`)
- Link count for hardlinks

## File Type Detection

- Linux/macOS: `subprocess.run(["file", "--brief", path])`
- Windows: `mimetypes.guess_type()` fallback

## Cross-Reference

- [[vfs]] — works on any `VfsPath` (local, archive, remote)
- [[operations]] — same progress dialog pattern
- [[platform_util]] — OS-specific type detection