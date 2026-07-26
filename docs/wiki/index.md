---
title: Wiki Index
type: index
sources:
  - docs/raw/README.md
  - CONTRIBUTING.md
related: []
created: 2026-07-14
updated: 2026-07-22
confidence: high
---

# linux-commander Wiki Index

## Project Overview

A dual-pane "orthodox file manager" in the tradition of Norton Commander, Midnight Commander, and Total Commander — built with plain tkinter (no third-party GUI libraries).

## Entities (Modules)

### Core Application
- [[app]] — CommanderApp: dual-panel window, F-key bar, key routing, app shell
- [[panel]] — FilePanel: single directory-listing pane (Treeview-backed)
- [[keys]] — F-key table shared by key bar and global bindings
- [[fkey_bar]] — FKeyBar widget: F-key button row (extracted from app.py)
- [[command_prompt]] — CommandPrompt widget: command entry bar with history
- [[menu_bar]] — MenuBar builder with MenuCallbacks protocol
- [[panel_loading]] — Panel loading helpers: tree population, entry formatting

### Filesystem & VFS
- [[fs]] — Directory listing, sorting, formatting helpers
- [[vfs]] — FileSystem ABC, VfsPath, LocalFileSystem, MountManager, plugin mount lifecycle
- [[plugins]] — Auto-discovered VFS archive/protocol plugins + viewer document-reader plugins
- [[volumes]] — Volume/drive enumeration (Linux /proc/mounts backend, Windows drive letters)
- [[platform_util]] — Cross-platform "open with default app" seam

### Plugin Systems
- [[sort_criteria]] — Plugin-based sort criteria (name, size, mtime, extension)
- [[codecs]] — Plugin-based compression codecs (none, gz, bz2, xz, zstd)
- [[containers]] — Plugin-based archive container builders (zip, tar, grp, 7z, iso)
- [[conflict_strategies]] — Plugin-based conflict resolution (skip, replace, compare, etc.)

### Network VFS Backends
- [[smb_vfs]] — SMB/CIFS backend (`smbprotocol`), read/write
- [[webdav_vfs]] — WebDAV/WebDAVS backend (`webdavclient3`), read/write
- [[jotta_vfs]] — Jottacloud backend (hand-rolled JFS REST client), read/write
- [[credentials]] — Keyring-backed credential storage shared by all network plugins

### Operations
- [[operations]] — Copy/move/delete/mkdir/rename with progress callbacks
- [[archiving]] — Compression dialog logic, container×codec matrix, encryption wrapping
- [[file_info]] — Shift+F3 file info (type, permissions, checksums)
- [[search_engine]] — Background search worker, criteria model, archive descent
- [[duplicate_op]] — Duplicate file finder (size → checksum → content), VFS-generic

### UI Components
- [[dialogs]] — Modal dialogs (confirm, prompt, error, progress, choose-from-list)
- [[viewer]] — Built-in read-only viewer (F3) and editor (F4)
- [[image_viewer]] — Standalone image viewer (F3 on images)
- [[compression_dialog]] — Shift+F5 dialog (container/codec/level/encrypt-output)
- [[search_dialog]] — Search UI (Alt+F7 / Shift+F7)
- [[ftp_dialog]] — Connections manager (FTP/SFTP sessions)
- [[settings]] — Settings dataclass, load/save (settings.json), StoredKey, FtpSession

### Syntax Highlighting
- [[syntax]] — Syntax highlighting engine + JSON language definitions

### Concepts
- [[orthodox-file-manager]] — OFM design patterns and keybindings
- [[cross-platform-seams]] — OS-specific seams for future Windows/macOS support

## Sources
- [[readme-summary]] — README.md ingested as source document
- [[contributing-summary]] — CONTRIBUTING.md ingested as source document