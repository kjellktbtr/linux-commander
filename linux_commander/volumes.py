"""Volume / drive enumeration (the OS-specific seam for the drive selector).

Linux: real (non-pseudo) mount points parsed from /proc/mounts, plus "/" and
the user's home directory. Windows/macOS: stubbed for now — see the "Done
when" note in build-orthodox-file-manager-plan.md about future platforms.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

_PSEUDO_FSTYPES = {
    "proc",
    "sysfs",
    "devtmpfs",
    "tmpfs",
    "devpts",
    "securityfs",
    "cgroup",
    "cgroup2",
    "pstore",
    "bpf",
    "autofs",
    "fusectl",
    "configfs",
    "mqueue",
    "hugetlbfs",
    "debugfs",
    "tracefs",
    "binfmt_misc",
    "overlay",
    "squashfs",
    "rpc_pipefs",
    "nsfs",
    "efivarfs",
    "selinuxfs",
    "ramfs",
}
"""Filesystem types that are virtual/kernel bookkeeping rather than something
a user would want to browse in a file manager."""


@dataclass(frozen=True, slots=True)
class Volume:
    """A selectable root in the volume/drive bar.

    `kind` is a free-form hint ("local" for "/" and home, "mount" for
    anything else found in /proc/mounts; future backends may use "drive" or
    "network").
    """

    label: str
    path: Path
    kind: str


def _unescape_mount_path(raw: str) -> str:
    """/proc/mounts encodes space/tab/backslash/newline in paths as octal
    escapes (e.g. `\\040` for a space); undo that."""
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), raw)


def parse_proc_mounts(mounts_text: str) -> list[Volume]:
    """Parse the contents of /proc/mounts into real (non-pseudo) `Volume`s.

    Takes the mounts text directly, rather than reading /proc/mounts itself,
    so this can be unit-tested with a fixture.
    """
    volumes: list[Volume] = []
    for line in mounts_text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        _device, mountpoint_raw, fstype = parts[0], parts[1], parts[2]
        if fstype in _PSEUDO_FSTYPES or fstype.startswith("fuse."):
            continue
        mountpoint = _unescape_mount_path(mountpoint_raw)
        volumes.append(Volume(label=mountpoint, path=Path(mountpoint), kind="mount"))
    return volumes


def _list_volumes_linux() -> list[Volume]:
    volumes: dict[Path, Volume] = {}
    # Always-present, friendly-labeled entries; inserted first so real mounts
    # discovered below never override their labels (via setdefault).
    volumes[Path("/")] = Volume(label="/", path=Path("/"), kind="local")
    volumes[Path.home()] = Volume(label="Home", path=Path.home(), kind="local")

    try:
        mounts_text = Path("/proc/mounts").read_text()
    except OSError:
        mounts_text = ""

    for volume in parse_proc_mounts(mounts_text):
        volumes.setdefault(volume.path, volume)

    return sorted(volumes.values(), key=lambda v: str(v.path))


def _drive_letters_from_bitmask(mask: int) -> list[str]:
    """Convert a GetLogicalDrives() bitmask to a sorted list of drive strings.

    Bit 0 = A:, bit 1 = B:, bit 2 = C:, etc.  Returns e.g. ``["A:", "C:", "D:"]``.
    """
    drives = []
    for i in range(26):
        if mask & (1 << i):
            drives.append(chr(ord("A") + i) + ":")
    return drives


def _list_volumes_windows() -> list[Volume]:  # pragma: no cover - Windows only
    """Enumerate logical drives (A:, C:, D:, ...) on Windows.

    Tries ``os.listdrives()`` (Python 3.12+) first; falls back to
    ``ctypes.windll.kernel32.GetLogicalDrives()`` bitmask on older versions.
    """
    try:
        import os as _os

        drive_strs: list[str] = [d.rstrip("\\") for d in _os.listdrives()]  # type: ignore[attr-defined]
    except AttributeError:
        # Python < 3.12: use the Win32 bitmask API
        import ctypes

        mask = ctypes.windll.kernel32.GetLogicalDrives()  # type: ignore[attr-defined]
        drive_strs = _drive_letters_from_bitmask(mask)

    return [Volume(label=d, path=Path(d + "\\"), kind="drive") for d in sorted(drive_strs)]


def _list_volumes_macos() -> list[Volume]:  # pragma: no cover - macOS only
    """Stub: enumerating /Volumes is not yet implemented."""
    raise NotImplementedError("macOS volume enumeration is not yet implemented")


def list_volumes() -> list[Volume]:
    """List the selectable volumes/drives for the current platform.

    Never raises: unimplemented platforms fall back to an empty list so the
    UI can simply show no volume bar / an empty chooser rather than crashing.
    """
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
