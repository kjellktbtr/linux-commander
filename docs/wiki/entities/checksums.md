---
title: Checksums — Generation & Verification
type: entity
sources:
  - linux_commander/file_ops/checksum_op.py
  - linux_commander/operations.py
  - linux_commander/app.py
related:
  - "[[operations]]"
  - "[[vfs]]"
  - "[[panel]]"
created: 2026-07-18
updated: 2026-07-18
confidence: high
---

# Checksums — Generation & Verification

`linux_commander/file_ops/checksum_op.py` implements **checksum generation and verification** as Operations menu items. Supports MD5, SHA1, SHA256, SHA512 with streaming (chunked) hashing for memory efficiency.

## Menu Structure

Operations → Checksums:
- **Generate MD5…**
- **Generate SHA256…**
- **Generate SHA512…**
- **Verify Checksums…** (reads `.md5`/`.sha256`/etc. and compares)

## Modes

| Mode | Trigger | Behavior |
|------|---------|----------|
| **Single file display** | 1 file selected | Shows hash in a dialog (copy button) |
| **Sidecar files** | Multiple files selected | Creates `<file>.md5`, `<file>.sha256`, etc. next to each source |
| **SUM file** | Multiple files, user chooses "SUM file" | Creates single `MD5SUMS`, `SHA256SUMS`, etc. with all hashes |
| **Verify** | Any selection + checksum file(s) | Reads checksum file(s), hashes actual files, reports mismatches |

## Algorithms

| Enum | `hashlib` name | Extension | Display |
|------|----------------|-----------|---------|
| `MD5` | `md5` | `.md5` | MD5 (128-bit) |
| `SHA1` | `sha1` | `.sha1` | SHA1 (160-bit) |
| `SHA256` | `sha256` | `.sha256` | SHA256 (256-bit) |
| `SHA512` | `sha512` | `.sha512` | SHA512 (512-bit) |

## Output Formats

| Format | Example Line | Notes |
|--------|--------------|-------|
| **Standard** | `d41d8cd98f00b204e9800998ecf8427e  file.txt` | Two spaces between hash and name (GNU coreutils default) |
| **BSD** | `MD5 (file.txt) = d41d8cd98f00b204e9800998ecf8427e` | `ALGO (filename) = hash` |
| **GNU** | `d41d8cd98f00b204e9800998ecf8427e *file.txt` | Asterisk prefix = binary mode |

Default: **Standard**.

## Verify Mode

When **Verify** is selected in the dialog:

1. User picks one or more checksum files (`.md5`, `.sha256`, `MD5SUMS`, etc.)
2. Operation parses each line (supports all three formats above)
3. For each entry, hashes the corresponding file (relative to checksum file's directory)
4. Results dialog shows: ✅ Match / ❌ Mismatch / ⚠️ Missing file

## Streaming Implementation

```python
def _hash_file(path: VfsPath, algorithm: ChecksumAlgorithm,
               on_progress: Callable[[int, int], None] | None = None) -> str:
    hasher = hashlib.new(algorithm.hashlib_name)
    total_size = path.fs.stat(path).size
    bytes_read = 0
    with path.fs.open_read(path) as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
            bytes_read += len(chunk)
            if on_progress:
                on_progress(bytes_read, total_size)
    return hasher.hexdigest()
```

- **Chunk size**: 64 KiB (matches `shutil.copyfileobj` default)
- **Progress callback**: Reports bytes read / total for ProgressDialog sub-bar
- **Memory**: O(1) — never loads full file into RAM

## Dialog: `ChecksumDialog`

Fields:
- **Algorithm**: Radio group (MD5, SHA1, SHA256, SHA512)
- **Mode**: Radio group (Single / Sidecar / SUM file / Verify)
- **Output format**: Combo (Standard / BSD / GNU) — enabled for Generate modes
- **Verify file picker**: File dialog (multi-select) — enabled for Verify mode
- **Preview**: For single-file mode, shows computed hash immediately

Returns a `dict` with keys: `algorithm`, `mode`, `format`, `verify_files` (list of VfsPath for verify mode).

## Run Functions

- `run_generate(sources, dest_dir, on_progress, should_cancel, algorithm, mode, format, verify_files)`
- `run_verify(sources, dest_dir, on_progress, should_cancel, algorithm, verify_files)`

Both return `list[OperationError]` (standard operations contract).

## Cross-Reference

- [[operations]] — FileOperation plugin registration, progress dialog integration
- [[vfs]] — Uses `VfsPath.fs.open_read()` for cross-backend hashing (works on archives, FTP, etc.)
- [[panel]] — Sources from active panel selection; destination = current directory