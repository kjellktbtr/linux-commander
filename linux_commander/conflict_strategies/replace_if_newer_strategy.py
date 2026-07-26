"""Replace if newer conflict resolution strategy."""

from __future__ import annotations

from linux_commander.conflict_strategies import ConflictInfo, ConflictStrategy
from linux_commander.vfs import WritableFileSystem


class _ReplaceIfNewerStrategy(ConflictStrategy):
    @property
    def name(self) -> str:
        return "replace_if_newer"

    @property
    def label(self) -> str:
        return "Replace if Newer"

    def should_delete(self, conflict: ConflictInfo, dest_fs: WritableFileSystem) -> bool:
        return conflict.source_mtime > conflict.dest_mtime


strategy_class = _ReplaceIfNewerStrategy
