---
title: platform_util — Cross-Platform "Open With Default App"
type: entity
sources:
  - linux_commander/platform_util.py
related:
  - "[[app]]"
  - "[[panel]]"
  - "[[viewer]]"
  - "[[cross-platform-seams]]"
  - "[[readme-summary]]"
created: 2026-07-14
updated: 2026-07-14
confidence: high
---

# platform_util — Cross-Platform "Open With Default App"

## Purpose
Tiny OS-specific seam for opening a file with the user's default application. Called when Enter is pressed on a non-directory row in the file panel.

## Public API

### `open_with_default_app(path: Path) -> bool`
Dispatches on `sys.platform`:
- **Linux** (`sys.platform.startswith("linux")`): `subprocess.Popen(["xdg-open", str(path)])` → returns `True`
- **Windows** (`sys.platform == "win32"`): `os.startfile(path)` → returns `True`
- **macOS** (`sys.platform == "darwin"`): `subprocess.Popen(["open", str(path)])` → returns `True`
- **Other / error**: returns `False`

On any `OSError` (opener not found, launch failed): returns `False`.

## Usage in App
1. Panel `_activate_cursor()` on a file (not dir) → calls `platform_util.open_with_default_app(entry.path)`
2. If returns `True` → done (external app launched)
3. If returns `False` → falls back to built-in `viewer.view_file()`

This is the "try system opener first, fall back to internal viewer" behavior documented in README.

## Implementation Notes
- `subprocess.Popen` with `stdout=DEVNULL, stderr=DEVNULL` — fire-and-forget, no waiting
- `os.startfile` is Windows-only; accessed via `import os` inside the `win32` branch
- No blocking, no result capture — if it fails silently, fallback handles it
- Designed to be a pure seam: all OS logic here, callers just check `bool`

## Unit Tests (`tests/test_platform_util.py`)
Monkeypatches `subprocess.Popen` (and `os.startfile` on Windows) to verify:
- Correct command dispatched per platform
- `FileNotFoundError` → `False`
- Unknown platform → `False`
- No real app ever launched during tests

## Related
- [[app]] — `CommanderApp._on_activate_file` calls this
- [[panel]] — `_activate_cursor` triggers the callback
- [[viewer]] — fallback viewer when this returns `False`
- [[cross-platform-seams]] — one of two OS-specific seams (other is `volumes.py`)