"""Compare conflict resolution strategy (opens diff viewer)."""

from __future__ import annotations

import tkinter as tk

from linux_commander.conflict_strategies import ConflictInfo, ConflictStrategy
from linux_commander.vfs import WritableFileSystem


class _CompareStrategy(ConflictStrategy):
    @property
    def name(self) -> str:
        return "compare"

    @property
    def label(self) -> str:
        return "Compare"

    def should_delete(self, conflict: ConflictInfo, dest_fs: WritableFileSystem) -> bool:
        # Don't delete - just open diff viewer
        return False

    def on_resolve(self, parent: tk.Misc, conflict: ConflictInfo) -> None:
        """Open diff viewer for source vs destination."""
        from linux_commander.diff_viewer import show_diff_viewer

        show_diff_viewer(parent, conflict.source, conflict.dest)


strategy_class = _CompareStrategy
