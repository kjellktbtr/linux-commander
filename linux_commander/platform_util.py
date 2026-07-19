"""OS-specific "open with default app" seam (xdg-open / os.startfile / open)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def open_with_default_app(path: Path) -> bool:
    """Open `path` with the OS's default application for its type.

    Dispatches on `sys.platform`: `xdg-open` on Linux, `os.startfile` on
    Windows, `open` on macOS. Returns True if an opener was launched, False
    if no opener is available for this platform or launching failed — the
    caller should fall back to the built-in viewer in that case.
    """
    try:
        if sys.platform.startswith("linux"):
            subprocess.Popen(
                ["xdg-open", str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        if sys.platform == "win32":  # pragma: no cover - exercised on Windows only
            import os

            os.startfile(path)  # type: ignore[attr-defined]
            return True
        if sys.platform == "darwin":  # pragma: no cover - exercised on macOS only
            subprocess.Popen(
                ["open", str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
    except OSError:
        return False
    return False
