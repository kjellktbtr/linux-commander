"""Session save/restore logic extracted from CommanderApp (SRP).

Handles persisting and restoring per-panel state (paths, marks, sort,
show_hidden) and the active-side selection.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from linux_commander.panel import FilePanel
from linux_commander.settings import Settings, save_settings
from linux_commander.vfs import LocalFileSystem


class SessionManager:
    """Save and restore per-panel session state."""

    def __init__(self, settings: Settings, local_fs: LocalFileSystem) -> None:
        self._settings = settings
        self._local_fs = local_fs

    # -- save ----------------------------------------------------------------

    def save(self, left_panel: FilePanel, right_panel: FilePanel, active_panel: FilePanel) -> None:
        """Persist current panel state to settings file."""
        s = self._settings

        s.active_side = "right" if active_panel is right_panel else "left"

        for panel, path_attr, marks_attr, sk_attr, sr_attr, sh_attr in [
            (
                left_panel,
                "left_path",
                "left_marks",
                "left_sort_key",
                "left_sort_reverse",
                "left_show_hidden",
            ),
            (
                right_panel,
                "right_path",
                "right_marks",
                "right_sort_key",
                "right_sort_reverse",
                "right_show_hidden",
            ),
        ]:
            real_path = panel.current_path.fs.realpath(panel.current_path)
            if real_path is not None:
                setattr(s, path_attr, str(real_path))
                setattr(s, marks_attr, [e.name for e in panel.marked_entries()])
            else:
                setattr(s, path_attr, "")
                setattr(s, marks_attr, [])
            setattr(s, sk_attr, panel.sort_key)
            setattr(s, sr_attr, panel.sort_reverse)
            setattr(s, sh_attr, panel.show_hidden)

        # Legacy single-panel fields (forward-compat)
        s.show_hidden = active_panel.show_hidden
        s.sort_key = active_panel.sort_key
        s.sort_reverse = active_panel.sort_reverse
        s.selection_patterns = active_panel._pattern_history  # type: ignore[attr-defined]

        save_settings(s)

    # -- restore -------------------------------------------------------------

    def restore(
        self,
        left_panel: FilePanel,
        right_panel: FilePanel,
        set_active_panel: Callable[[FilePanel], None],
        update_active_panel_style: Callable[[], None],
    ) -> None:
        """Restore per-panel paths, marks, sort, and active side from settings."""
        s = self._settings

        for panel, path_str, marks, sk, sr, sh in [
            (
                left_panel,
                s.left_path,
                s.left_marks,
                s.left_sort_key,
                s.left_sort_reverse,
                s.left_show_hidden,
            ),
            (
                right_panel,
                s.right_path,
                s.right_marks,
                s.right_sort_key,
                s.right_sort_reverse,
                s.right_show_hidden,
            ),
        ]:
            if path_str:
                p = Path(path_str)
                if p.is_dir():
                    panel.sort_key = sk  # type: ignore[assignment]
                    panel.sort_reverse = sr
                    panel.show_hidden = sh
                    panel.load(self._local_fs.from_path(p))
                    if marks:
                        mark_set = set(marks)
                        for entry in panel._entries:
                            if not entry.is_parent and entry.name in mark_set:
                                panel.marked.add(entry.path)
                        panel._refresh_row_tags()
                        panel._notify_marks_changed()

        if s.active_side == "right":
            set_active_panel(right_panel)
            update_active_panel_style()
