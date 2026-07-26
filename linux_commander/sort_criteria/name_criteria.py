"""Sort by file/directory name (case-insensitive)."""

from __future__ import annotations

from linux_commander.sort_criteria import SortCriterion
from linux_commander.vfs import FileEntry


class _NameCriterion(SortCriterion):
    @property
    def name(self) -> str:
        return "name"

    @property
    def label(self) -> str:
        return "Name"

    def key(self, entry: FileEntry) -> str:
        return entry.name.lower()


criterion_class = _NameCriterion
