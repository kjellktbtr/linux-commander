---
title: Cross-Platform Seams
type: concept
sources:
  - linux_commander/volumes.py
  - linux_commander/platform_util.py
  - linux_commander/app.py
  - linux_commander/panel.py
  - docs/raw/README.md
related:
  - "[[volumes]]"
  - "[[platform_util]]"
  - "[[app]]"
  - "[[panel]]"
  - "[[readme-summary]]"
created: 2026-07-14
updated: 2026-07-14
confidence: high
---

# Cross-Platform Seams

## Design Principle
All OS-specific logic is quarantined behind **two small modules**. The rest of the codebase (panels, operations, dialogs, viewer, app shell) uses only `pathlib.Path` and stdlib, making it portable by default.

## Seam 1: `volumes.py` — Drive/Volume Enumeration

### Purpose
Provides the selectable roots for the OFM "drive bar" (volume buttons + Alt+F1/F2 chooser).

### Interface
```python
@dataclass(frozen=True, slots=True)
class Volume:
    label: str      # display name
    path: Path      # filesystem root
    kind: str       # "local" | "mount" | future: "drive" | "network"

def list_volumes() -> list[Volume]:
    # Never raises; unimplemented platforms return []
```

### Current Implementations
| Platform | Function | Status |
|----------|----------|--------|
| Linux | `_list_volumes_linux()` | **Full** — parses `/proc/mounts`, filters pseudo-fs, adds `/` and `~` |
| Windows | `_list_volumes_windows()` | **Stub** — raises `NotImplementedError` |
| macOS | `_list_volumes_macos()` | **Stub** — raises `NotImplementedError` |

### Graceful Degradation
```python
def list_volumes() -> list[Volume]:
    if sys.platform.startswith("linux"):
        return _list_volumes_linux()
    if sys.platform == "win32":
        try:
            return _list_volumes_windows()
        except NotImplementedError:
            return []
    if sys.platform == "darwin":
        try:
            return _list_volumes_macos()
        except NotImplementedError:
            return []
    return []
```
- UI gets `[]` → volume bar has no buttons, chooser is empty, no crash
- App works with just the starting directory

### Linux Implementation Details
- Parses `/proc/mounts` directly (not `/media/$USER` assumptions)
- Filters `_PSEUDO_FSTYPES` (proc, sysfs, tmpfs, cgroup2, overlay, squashfs, fuse.* GVFS, etc.)
- Unescapes octal paths (`\040` → space)
- Always includes `/` (label `"/"`) and `~` (label `"Home"`) first with friendly labels
- Deduplicates by path (mount entries for `/` or home won't override)

### Windows Future Implementation
```python
def _list_volumes_windows() -> list[Volume]:
    # Python 3.12+: os.listdrives()
    # Older: ctypes.windll.kernel32.GetLogicalDrives() bitmask
    # Return [Volume(label="C:", path=Path("C:\\"), kind="drive"), ...]
```

### macOS Future Implementation
```python
def _list_volumes_macos() -> list[Volume]:
    # Enumerate /Volumes entries
    # Return [Volume(label=name, path=Path("/Volumes")/name, kind="mount"), ...]
```

## Seam 2: `platform_util.py` — Open With Default App

### Purpose
Opens a file with the OS's registered handler for its type (double-click behavior).

### Interface
```python
def open_with_default_app(path: Path) -> bool:
    # Returns True if opener launched, False otherwise (fallback to internal viewer)
```

### Current Implementations
| Platform | Command | Notes |
|----------|---------|-------|
| Linux | `xdg-open` | `subprocess.Popen`, detached, DEVNULL |
| Windows | `os.startfile` | Win32 API, fire-and-forget |
| macOS | `open` | `subprocess.Popen`, detached, DEVNULL |

### Error Handling
- All `OSError` caught → `False`
- Caller (panel → app → viewer) falls back to built-in viewer on `False`

## Root / Parent Detection
Used for ".." entry and "go up" logic. Works on both Unix and Windows because:
```python
path.parent == path  # True for "/" on Unix, "C:\\" on Windows
```
No platform-specific code needed.

## Path Handling
- All internal paths: `pathlib.Path`
- No string path manipulation with `/` or `\` directly
- `Path.iterdir()`, `Path.stat()`, `Path.mkdir()`, `Path.rename()`, `Path.unlink()`, `shutil.*` — all cross-platform

## Testing Strategy
- Unit tests for pure logic (fs, operations, viewer helpers, volumes parser) run on any platform
- Linux `volumes.py` tests use fixture `/proc/mounts` text (no real filesystem read)
- `platform_util` tests monkeypatch `subprocess.Popen` / `os.startfile`
- GUI tests require real Tk display (scripted drivers, not pytest)

## Adding a New Platform
1. Add branch in `volumes.py:list_volumes()` for `sys.platform`
2. Implement `_list_volumes_<platform>()` returning `list[Volume]`
3. Add branch in `platform_util.open_with_default_app()` for opener command
4. Add unit tests with mocks
5. Update `cross-platform-seams.md` and `README.md` status table