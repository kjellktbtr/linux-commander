"""UI-layer SearchCriteria — the user's search form state.

Extracted from search_dialog.py so the pure data-model class can be
imported and tested independently of Tkinter.

Public API:
    class SearchCriteria
"""

from __future__ import annotations

from datetime import datetime

from linux_commander.search_engine import SearchCriteria as EngineSearchCriteria
from linux_commander.vfs import VfsPath


class SearchCriteria:
    """Container for all search criteria (UI version).

    Holds the form values from ``SearchDialog`` and can convert them to
    the engine's ``SearchCriteria`` via ``to_engine_criteria()``.
    """

    def __init__(self) -> None:
        # Size
        self.size_enabled = False
        self.size_min: int | None = None  # bytes
        self.size_max: int | None = None  # bytes

        # Date
        self.date_enabled = False
        self.date_from: datetime | None = None
        self.date_to: datetime | None = None

        # Name pattern
        self.name_enabled = False
        self.name_pattern = ""
        self.name_case_sensitive = False
        self.name_regex = False

        # Content
        self.content_enabled = False
        self.content_pattern = ""
        self.content_mode = "string"  # "string", "regex", "hex"
        self.content_case_sensitive = False

        # Root path
        self.root_path: VfsPath | None = None

        # Archives
        self.search_archives: bool = False
        """When True, descend into archive files (zip, tar, …) during search."""

    def to_engine_criteria(self) -> EngineSearchCriteria:
        """Convert to the engine's SearchCriteria."""
        return EngineSearchCriteria(
            size_enabled=self.size_enabled,
            size_min=self.size_min,
            size_max=self.size_max,
            date_enabled=self.date_enabled,
            date_from=self.date_from,
            date_to=self.date_to,
            name_enabled=self.name_enabled,
            name_pattern=self.name_pattern,
            name_case_sensitive=self.name_case_sensitive,
            name_regex=self.name_regex,
            content_enabled=self.content_enabled,
            content_pattern=self.content_pattern,
            content_mode=self.content_mode,
            content_case_sensitive=self.content_case_sensitive,
            root_path=self.root_path,
            search_archives=self.search_archives,
        )

    def to_summary(self) -> str:
        parts = []
        if self.size_enabled:
            if self.size_min is not None and self.size_max is not None:
                parts.append(
                    f"size {self._fmt_size(self.size_min)}–{self._fmt_size(self.size_max)}"
                )
            elif self.size_min is not None:
                parts.append(f"size > {self._fmt_size(self.size_min)}")
            elif self.size_max is not None:
                parts.append(f"size < {self._fmt_size(self.size_max)}")
        if self.date_enabled:
            if self.date_from and self.date_to:
                start = self.date_from.strftime("%Y-%m-%d %H:%M")
                end = self.date_to.strftime("%Y-%m-%d %H:%M")
                parts.append(f"date {start}–{end}")
            elif self.date_from:
                parts.append(f"date newer {self.date_from.strftime('%Y-%m-%d %H:%M')}")
            elif self.date_to:
                parts.append(f"date older {self.date_to.strftime('%Y-%m-%d %H:%M')}")
        if self.name_enabled:
            mode = "regex" if self.name_regex else "glob"
            case = "cs" if self.name_case_sensitive else "ci"
            parts.append(f"name {mode}/{case}: {self.name_pattern}")
        if self.content_enabled:
            mode = self.content_mode
            case = "cs" if self.content_case_sensitive else "ci"
            parts.append(f"content {mode}/{case}: {self.content_pattern[:30]}")
        if not parts:
            return "No criteria"
        return " + ".join(parts)

    @staticmethod
    def _fmt_size(b: int | float) -> str:
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if b < 1024 or unit == "TB":
                return f"{b:.1f}{unit}" if unit != "B" else f"{int(b)}B"
            b /= 1024
        return f"{b:.1f}PB"
