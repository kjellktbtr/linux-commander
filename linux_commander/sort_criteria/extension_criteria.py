"""Sort by file extension."""

from __future__ import annotations

from linux_commander.sort_criteria import SortCriterion
from linux_commander.vfs import FileEntry


class _ExtensionCriterion(SortCriterion):
    @property
    def name(self) -> str:
        return "extension"

    @property
    def label(self) -> str:
        return "Ext"

    def key(self, entry: FileEntry) -> str:
        if entry.is_dir:
            return ""
        # Extract extension (including dot) for sorting
        dot_idx = entry.name.rfind(".")
        if dot_idx < 0:
            return ""
        return entry.name[dot_idx:].lower()


criterion_class = _ExtensionCriterion
