"""Sort by modification time."""

from __future__ import annotations

from linux_commander.sort_criteria import SortCriterion
from linux_commander.vfs import FileEntry


class _MtimeCriterion(SortCriterion):
    @property
    def name(self) -> str:
        return "mtime"

    @property
    def label(self) -> str:
        return "Date"

    def key(self, entry: FileEntry) -> float:
        return entry.mtime


criterion_class = _MtimeCriterion
