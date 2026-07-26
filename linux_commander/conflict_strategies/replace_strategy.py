"""Replace conflict resolution strategy."""

from __future__ import annotations

from linux_commander.conflict_strategies import ConflictInfo, ConflictStrategy
from linux_commander.vfs import WritableFileSystem


class _ReplaceStrategy(ConflictStrategy):
    @property
    def name(self) -> str:
        return "replace"

    @property
    def label(self) -> str:
        return "Replace"

    def should_delete(self, conflict: ConflictInfo, dest_fs: WritableFileSystem) -> bool:
        return True


strategy_class = _ReplaceStrategy
