"""Sort by file size."""

from __future__ import annotations

from linux_commander.sort_criteria import SortCriterion
from linux_commander.vfs import FileEntry


class _SizeCriterion(SortCriterion):
    @property
    def name(self) -> str:
        return "size"

    @property
    def label(self) -> str:
        return "Size"

    def key(self, entry: FileEntry) -> int:
        return entry.size


criterion_class = _SizeCriterion
