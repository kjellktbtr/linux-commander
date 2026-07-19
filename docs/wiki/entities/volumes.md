---
title: volumes — Volume/Drive Enumeration
type: entity
sources:
  - linux_commander/volumes.py
related:
  - "[[panel]]"
  - "[[app]]"
  - "[[cross-platform-seams]]"
  - "[[readme-summary]]"
created: 2026-07-14
updated: 2026-07-15
confidence: high
---

# volumes — Volume/Drive Enumeration

## Purpose
Provides the "drive selector" (classic OFM volume bar) by enumerating selectable filesystem roots. Linux backend is fully implemented; Windows/macOS are stubbed with graceful fallback to empty list.

## Public API

### `Volume` (dataclass)
- `label: str` — display name for the volume button / chooser entry
- `path: Path` — filesystem root path
- `kind: str` — `"local"` for `/` and home, `"mount"` for other Linux mounts, `"drive"` for Windows drive letters

### `list_volumes() -> list[Volume]`
Dispatches on `sys.platform`:
- **Linux**: `_list_volumes_linux()`
- **Windows**: `_list_volumes_windows()` (implemented — returns drive letters A:, C:, ...)
- **macOS**: `_list_volumes_macos()` (stub → `NotImplementedError` → caught → `[]`)
- **Other**: `[]`

Never raises; unimplemented platforms return empty list so UI degrades gracefully (no volume bar / empty chooser).

## Windows Implementation (`_list_volumes_windows`)

Enumerates logical drives and returns one `Volume` per drive:
`Volume(label="C:", path=Path("C:\\"), kind="drive")`.

- Tries `os.listdrives()` (Python 3.12+) first.
- Falls back to `ctypes.windll.kernel32.GetLogicalDrives()` bitmask on older Python.
- Pure helper `_drive_letters_from_bitmask(mask)` converts the bitmask to `["A:", "C:", ...]`
  (testable without a real Windows host — see unit tests below).
- No display-side changes needed: panel volume bar and Alt+F1/F2 chooser already
  read `.label`/`.path` generically.

## Linux Implementation (`_list_volumes_linux`)

### Always-present entries (inserted first, labels never overridden)
1. `/` — label `"/"`, `kind="local"`
2. `~` (home) — label `"Home"`, `kind="local"`

### Discovered from `/proc/mounts`
- Parses `/proc/mounts` line-by-line (fields: device, mountpoint, fstype, ...)
- Skips pseudo/virtual filesystems (`_PSEUDO_FSTYPES` set + any `fuse.*`)
- Unescapes octal escapes in mountpoints (`\040` → space, etc.) via `_unescape_mount_path`
- Adds as `Volume(label=mountpoint, path=Path(mountpoint), kind="mount")`
- Deduplicates by path (home/root from mounts won't override friendly labels)

### Why `/proc/mounts` instead of `/media/$USER`?
- Captures **whatever is actually mounted**, wherever it's mounted
- Works with non-standard mount points (e.g., VM shared folders at `/media/sf_...`, fstype `vboxsf`)
- No desktop-environment assumptions

## Octal Escape Unescaping (`_unescape_mount_path`)
`/proc/mounts` encodes space/tab/backslash/newline in paths as `\NNN` (octal). Regex `\\([0-7]{3})` → `chr(int(match, 8))`.

## Unit Tests (`tests/test_volumes.py`)
10 tests:

Linux (6): fixture `/proc/mounts` text (no real file read):
- Octal-escape unescaping (`\040` -> space)
- Pseudo-fs filtering (proc, sysfs, tmpfs, cgroup2, autofs, fuse.gvfsd-fuse, fuse.portal all excluded)
- Real mounts kept (`/`, `/home`, `/var/cache`, vboxsf shared folder)
- Paths-with-spaces unescaping
- Blank/malformed-line handling

Windows bitmask helper (4): no OS required:
- Known mask -> `["C:", "D:", "E:"]`
- Bit 0 + 2 -> `["A:", "C:"]`
- Mask 0 -> `[]`
- All 26 bits -> `["A:", ..., "Z:"]`

## Verification
- `uv run pytest tests/test_volumes.py` passes
- Manual: Alt+F1/Alt+F2 and volume bar switch panel between `/`, home, and mounted media
- Scripted driver (`verify_volumes.py`, 7 assertions) against real Tk:
  - Volume bar shows `/` and `Home` buttons
  - Clicking "Home" loads `Path.home()` into that panel
  - Alt+F1 (with right panel active) loads chosen volume into LEFT panel only
  - Alt+F2 Cancel leaves right panel unchanged

## Related
- [[panel]] — `_populate_volume_bar()` builds buttons from `list_volumes()` at construction
- [[app]] — Alt+F1/F2 handlers call `dialogs.choose_from_list` with `list_volumes()`
- [[cross-platform-seams]] — OS-specific seam design