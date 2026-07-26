"""Replace if different size conflict resolution strategy."""

from __future__ import annotations

from linux_commander.conflict_strategies import ConflictInfo, ConflictStrategy
from linux_commander.vfs import WritableFileSystem


class _ReplaceIfDifferentSizeStrategy(ConflictStrategy):
    @property
    def name(self) -> str:
        return "replace_if_different_size"

    @property
    def label(self) -> str:
        return "Replace if Different Size"

    def should_delete(self, conflict: ConflictInfo, dest_fs: WritableFileSystem) -> bool:
        return conflict.source_size != conflict.dest_size


strategy_class = _ReplaceIfDifferentSizeStrategy
