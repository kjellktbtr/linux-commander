"""Skip conflict resolution strategy."""

from __future__ import annotations

from linux_commander.conflict_strategies import ConflictInfo, ConflictStrategy
from linux_commander.vfs import WritableFileSystem


class _SkipStrategy(ConflictStrategy):
    @property
    def name(self) -> str:
        return "skip"

    @property
    def label(self) -> str:
        return "Skip"

    def should_delete(self, conflict: ConflictInfo, dest_fs: WritableFileSystem) -> bool:
        return False


strategy_class = _SkipStrategy
