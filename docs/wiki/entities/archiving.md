---
title: Archiving — Compression Dialog Logic
type: entity
sources:
  - linux_commander/archiving.py
  - linux_commander/compression_dialog.py
  - CONTRIBUTING.md
related:
  - "[[vfs]]"
  - "[[plugins]]"
  - "[[operations]]"
  - "[[compression_dialog]]"
  - "[[settings]]"
created: 2026-07-17
updated: 2026-07-18
confidence: high
---

# Archiving — Compression Dialog Logic

`linux_commander/archiving.py` implements the **container × codec matrix** and **encryption wrapping** used by the Shift+F5 compression dialog. It builds archives from a list of source files (any mix of local, archive-mounted, or remote).

## Container × Codec Matrix

| Container | Extensions | Writable | Codecs (compression) |
|-----------|------------|----------|---------------------|
| zip | `.zip` | ✅ | none, gz, bz2, xz, zst¹ |
| tar | `.tar` | ✅ | none, gz, bz2, xz, zst¹ |
| grp | `.grp` | ✅ | none, gz, bz2, xz, zst¹ |
| 7z | `.7z` | ✅² | none, gz, bz2, xz, zst¹ |
| iso | `.iso` | ✅² | none, gz, bz2, xz, zst¹ |

¹ `zstd` codec requires Python 3.14+ (stdlib `compression.zstd`).
² Needs `archives` extra (`py7zr`, `libarchive-c`).

**Every container × codec combination is valid**, including double-compression like `.7z.xz` (7z container + xz codec). The codec compresses the *already-compressed* container stream.

## Archive Name Composition

```
<basename>.<container_ext>[.<codec_ext>][.crp]
```

Examples:
- `backup.tar.gz` — tar container, gz codec
- `data.grp.zst` — grp container, zstd codec
- `archive.7z.xz.crp` — 7z container, xz codec, encrypted

## Encryption Wrap (`.crp`)

If **Encrypt output** is checked in the dialog:
1. Build the archive (container + codec) to a temp file
2. Encrypt that temp file with ChaCha20-Poly1305 (`.crp` format)
3. Output is `<name>.<container>.<codec>.crp`

Credential modes (must match on decrypt):
- **Password** — PBKDF2-HMAC-SHA256 (200k iterations) from password + stored salt
- **Stored key** — named 256-bit key from config; salt used as AAD

All three paths (Operations menu Encrypt/Decrypt, compression dialog, Enter on `.crp`) produce **byte-identical** `.crp` files.

## Core Functions

```python
# Build archive from source paths (list[VfsPath]) to output path (VfsPath)
def build_archive(
    sources: list[VfsPath],
    out_path: VfsPath,
    container: str,      # "zip", "tar", "grp", "7z", "iso"
    codec: str,          # "none", "gz", "bz2", "xz", "zst"
    level: int,          # compression level (1-9, 0=default)
    encrypt: bool,
    credential: Credential,  # Password or StoredKey
    progress_cb: ProgressCallback,
    cancel_event: threading.Event,
) -> OperationResult: ...

# Credential types
@dataclass
class Password: passphrase: str
@dataclass
class StoredKey: name: str  # looks up key in settings
Credential = Password | StoredKey
```

## Background Thread Pattern

Same as `operations.py` — `compression_dialog.py` calls `run_with_progress` with a worker that:
1. Materializes all sources to local temp files (via `plugins.materialize`)
2. Streams into container writer (zipfile, tarfile, py7zr, libarchive)
3. Pipes through codec if not "none" (gzip, bz2, lzma, zstd)
4. Optionally encrypts final stream to `.crp`
5. Moves result to final destination

## Per-file progress for remote sources (2026-07-18)

`_iter_sources()` (the shared driver behind every container builder) now precomputes an accurate `total` via `operations.count_progress_units()` instead of `len(sources)` — a single selected directory no longer reports "1/1" while compressing. For **remote** (non-`local_fs`) sources it also reports one genuine progress tick per file as `_iter_vfs()` streams each member's bytes into the archive (`should_cancel()` is checked between files too). Local sources still advance the running total in one jump per top-level item when their container-specific `add_local_dir` finishes — the various container libraries (zipfile, tarfile, py7zr, libarchive) don't expose a shared per-file hook the way `shutil.copytree`'s `copy_function` does (see [[operations]]), so per-file granularity there wasn't pursued.

Note: `_create_grp_archive()` has its own second "packing" phase (turning collected bytes into the flat GRP structure) with its own pre-existing per-entry progress reporting, independent of `_iter_sources()`'s collection-phase progress — the overall progress bar resets between the two phases for GRP specifically; this is a pre-existing characteristic of GRP's two-phase build, not something introduced or fixed here.

## Cross-Reference

- [[vfs]] — sources can be any VfsPath (local, archive-mounted, FTP/SFTP)
- [[plugins]] — materialize helpers spill nested/remote sources to temp files
- [[operations]] — same progress/cancel pattern, OperationResult return type
- [[compression_dialog]] — Shift+F5 UI, collects params, calls build_archive
- [[settings]] — stored encryption keys live in settings.json