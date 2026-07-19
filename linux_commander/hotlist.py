"""Directory hotlist/bookmarks for quick navigation.

Stores bookmarks in ~/.config/linux-commander/hotlist.json (XDG config).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "linux-commander"
HOTLIST_FILE = CONFIG_DIR / "hotlist.json"


@dataclass(frozen=True, slots=True)
class HotlistEntry:
    """A single bookmark entry."""

    name: str
    path: str  # Stored as display string (e.g. "file:///home/user" or "sftp://host/path")


class Hotlist:
    """Manages the directory hotlist (bookmarks)."""

    def __init__(self) -> None:
        self._entries: list[HotlistEntry] = []
        self._load()

    def _load(self) -> None:
        """Load hotlist from JSON file."""
        if HOTLIST_FILE.exists():
            try:
                data = json.loads(HOTLIST_FILE.read_text(encoding="utf-8"))
                self._entries = [HotlistEntry(**item) for item in data]
            except (json.JSONDecodeError, OSError, TypeError):
                self._entries = []
        else:
            self._entries = []

    def _save(self) -> None:
        """Save hotlist to JSON file."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = [asdict(entry) for entry in self._entries]
        HOTLIST_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def all(self) -> list[HotlistEntry]:
        """Return all entries (copy)."""
        return list(self._entries)

    def add(self, name: str, path: str) -> None:
        """Add or update an entry."""
        # Remove existing entry with same path
        self._entries = [e for e in self._entries if e.path != path]
        # Add new at top
        self._entries.insert(0, HotlistEntry(name=name, path=path))
        # Limit to 100 entries
        if len(self._entries) > 100:
            self._entries = self._entries[:100]
        self._save()

    def remove(self, path: str) -> None:
        """Remove entry by path."""
        self._entries = [e for e in self._entries if e.path != path]
        self._save()

    def clear(self) -> None:
        """Clear all entries."""
        self._entries.clear()
        self._save()


# Global singleton
_hotlist: Hotlist | None = None


def get_hotlist() -> Hotlist:
    """Get the global hotlist instance."""
    global _hotlist
    if _hotlist is None:
        _hotlist = Hotlist()
    return _hotlist
