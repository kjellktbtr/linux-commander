"""Operations controller extracted from CommanderApp (SRP).

Handles all file operations: copy, move, delete, mkdir, rename, compress,
new file, and file info. Also provides refresh helpers and error reporting.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from typing import cast

from linux_commander import dialogs, operations, platform_util, viewer
from linux_commander.operations import (
    CancelPredicate,
    ConflictResolution,
    OperationError,
    ProgressCallback,
)
from linux_commander.panel import FilePanel
from linux_commander.settings import Settings
from linux_commander.vfs import FileEntry, LocalFileSystem, MountManager, WritableFileSystem


class OperationsController:
    """Handle all file operations and refresh/error reporting."""

    def __init__(
        self,
        parent: tk.Misc,
        settings: Settings,
        local_fs: LocalFileSystem,
        mount_manager: MountManager,
        left_panel: FilePanel,
        right_panel: FilePanel,
        active_panel_getter: Callable[[], FilePanel],
        other_panel_getter: Callable[[], FilePanel],
        update_status: Callable[[], None],
    ) -> None:
        self._parent = parent
        self._settings = settings
        self._local_fs = local_fs
        self._mount_manager = mount_manager
        self._left_panel = left_panel
        self._right_panel = right_panel
        self._active_panel_getter = active_panel_getter
        self._other_panel_getter = other_panel_getter
        self._update_status = update_status

    @property
    def active_panel(self) -> FilePanel:
        return self._active_panel_getter()

    def _other_panel(self) -> FilePanel:
        return self._other_panel_getter()

    # -- refresh helpers -------------------------------------------------------

    def _refresh_panel_preserving_position(self, panel: FilePanel) -> None:
        """Reload `panel`'s current directory, keeping the cursor at the same
        row index if possible (a "sensible" row after copy/move/delete)."""
        previous_index = panel.current_index()
        panel.load(panel.current_path)
        if previous_index is not None:
            panel.select_index(previous_index)

    def _refresh_both_panels(self) -> None:
        """Refresh both panels, preserving cursor positions."""
        self._refresh_panel_preserving_position(self._left_panel)
        self._refresh_panel_preserving_position(self._right_panel)
        self._update_status()

    def _report_errors(self, errors: list[OperationError], verb: str = "Operation") -> None:
        """Show accumulated operation errors in a dialog."""
        if not errors:
            return
        lines = [f"{verb} errors:"]
        for err in errors:
            lines.append(f"  {err.path}: {err.message}")
        dialogs.error(self._parent, "\n".join(lines), title=f"{verb} failed")

    # -- file info -------------------------------------------------------------

    def cmd_file_info(self) -> None:
        """Show file type + checksums for the cursor file (Shift+F3)."""
        entry = self.active_panel.cursor_entry()
        if entry is None or entry.is_parent or entry.is_dir:
            return
        from linux_commander.file_info_dialog import show_file_info

        show_file_info(self._parent, entry.path, entry.size, entry.mtime)

    # -- new file --------------------------------------------------------------

    def cmd_new_file(self) -> None:
        """Shift+F4: create a new file in the active panel's directory and edit it."""
        panel = self.active_panel
        if not isinstance(panel.current_path.fs, WritableFileSystem):
            dialogs.error(
                self._parent,
                "Cannot create a file in a read-only filesystem.",
                title="New File failed",
            )
            return
        name = dialogs.prompt(self._parent, "New File", "New file name:")
        if not name:
            return
        target = panel.current_path / name
        real = panel.current_path.fs.realpath(target)
        already_exists = real is not None and real.exists()
        if not already_exists:
            try:
                operations.make_file(panel.current_path, name)
            except OSError as exc:
                dialogs.error(self._parent, str(exc), title="New File failed")
                return
            panel.load(panel.current_path, select_name=name)
            other = self._other_panel()
            if other.current_path == panel.current_path:
                self._refresh_panel_preserving_position(other)
            self._update_status()
        viewer.edit_file(
            self._parent,
            target,
            on_saved=lambda: self._refresh_panel_preserving_position(panel),
            settings=self._settings,
        )

    # -- copy/move -------------------------------------------------------------

    def cmd_copy(self) -> None:
        self._copy_or_move(is_move=False)

    def cmd_move(self) -> None:
        self._copy_or_move(is_move=True)

    def _copy_or_move(self, is_move: bool) -> None:
        panel = self.active_panel
        entries = panel.selected_entries()
        if not entries:
            return
        sources = [entry.path for entry in entries]
        verb = "Move" if is_move else "Copy"

        other_base = self._other_panel().current_path
        default_dest = str(other_base)
        dest_text = dialogs.prompt(
            self._parent, verb, f"{verb} {len(sources)} item(s) to:", initial=default_dest
        )
        if not dest_text:
            return

        # A single source and a bare filename (no directory component) typed
        # as the destination means "rename in place", not "move" — this never
        # touches the other panel's filesystem, so handle it before resolving
        # a cross-panel destination below.
        if is_move and len(sources) == 1 and "/" not in dest_text:
            source = sources[0]
            if not isinstance(source.fs, WritableFileSystem):
                dialogs.error(
                    self._parent,
                    "Cannot rename: source filesystem is read-only.",
                    title="Rename failed",
                )
                return
            try:
                operations.rename_entry(source, dest_text)
            except OSError as exc:
                dialogs.error(self._parent, str(exc), title="Rename failed")
            self._refresh_both_panels()
            return

        # Resolve against the OTHER panel's filesystem (which may be local,
        # remote, or an archive mount) so the destination keeps its own
        # backend instead of being coerced into a local path.
        dest_path = operations.resolve_dest_path(other_base, dest_text)

        if not isinstance(dest_path.fs, WritableFileSystem):
            dialogs.error(
                self._parent,
                "Destination filesystem is read-only.",
                title=f"{verb} failed",
            )
            return

        # If moving from a read-only source, warn that only a copy will happen.
        if is_move and any(not isinstance(s.fs, WritableFileSystem) for s in sources):
            if not dialogs.confirm(
                self._parent,
                "The source filesystem is read-only.\n"
                "Items will be copied but not removed from the source.\n\n"
                "Proceed with copy?",
                title="Move -> Copy only",
            ):
                return

        # Pre-scan for conflicts before starting the operation
        conflicts = operations.find_conflicts(sources, dest_path)
        if conflicts:
            from linux_commander.conflict_dialog import resolve_conflicts
            from linux_commander.conflict_strategies import ConflictInfo, get_strategy

            resolutions = resolve_conflicts(self._parent, conflicts)
            if resolutions is None:
                return  # user cancelled

            # Apply resolutions using plugin-based strategies
            writable_dest = cast(WritableFileSystem, dest_path.fs)
            for idx, resolution in resolutions.items():
                conflict = conflicts[idx]
                strategy = get_strategy(resolution.name.lower())
                if strategy is None:
                    continue  # unknown strategy, skip

                info = ConflictInfo(
                    source=conflict.source,
                    dest=conflict.dest,
                    source_size=conflict.source_size,
                    dest_size=conflict.dest_size,
                    source_mtime=conflict.source_mtime,
                    dest_mtime=conflict.dest_mtime,
                )

                # Handle compare strategy specially (opens diff viewer)
                if resolution == ConflictResolution.COMPARE:
                    if hasattr(strategy, "on_resolve"):
                        strategy.on_resolve(self._parent, info)
                    continue

                # Delete destination if strategy says so
                if strategy.should_delete(info, writable_dest):
                    try:
                        writable_dest.delete(conflict.dest)
                    except OSError:
                        pass  # will surface as error during operation

        op_func = operations.move_entries if is_move else operations.copy_entries

        def work(
            on_progress: ProgressCallback, should_cancel: CancelPredicate
        ) -> list[OperationError]:
            return op_func(
                sources,
                dest_path,
                on_progress=on_progress,
                should_cancel=should_cancel,
            )

        errors = dialogs.run_with_progress(self._parent, f"{verb}ing...", work)
        self._refresh_both_panels()
        self._report_errors(errors, verb)

    # -- compress --------------------------------------------------------------

    def cmd_compress(self) -> None:
        """Compress selected files to an archive (Shift+F5)."""
        from linux_commander.archiving import compress_sources
        from linux_commander.compression_dialog import CompressionDialog

        panel = self.active_panel
        entries = panel.selected_entries()
        if not entries:
            dialogs.error(self._parent, "No files selected for compression.", title="Compress")
            return

        sources = [entry.path for entry in entries]

        dialog = CompressionDialog(self._parent, panel.current_path, sources)
        if dialog.result:
            archive_path, fmt, options = dialog.result
            local_fs = self._local_fs

            def work(on_progress, should_cancel):
                return compress_sources(
                    sources, archive_path, fmt, options, local_fs, on_progress, should_cancel
                )

            errors = dialogs.run_with_progress(self._parent, "Compressing...", work)
            self._refresh_both_panels()
            self._report_errors(errors, "Compress")

    # -- mkdir -----------------------------------------------------------------

    def cmd_mkdir(self) -> None:
        panel = self.active_panel
        if not isinstance(panel.current_path.fs, WritableFileSystem):
            dialogs.error(
                self._parent,
                "Cannot create a directory in a read-only filesystem.",
                title="MkDir failed",
            )
            return
        name = dialogs.prompt(self._parent, "Make Directory", "New directory name:")
        if not name:
            return
        try:
            operations.make_directory(panel.current_path, name)
        except OSError as exc:
            dialogs.error(self._parent, str(exc), title="MkDir failed")
            return
        panel.load(panel.current_path, select_name=name)
        other = self._other_panel()
        if other.current_path == panel.current_path:
            self._refresh_panel_preserving_position(other)
        self._update_status()

    # -- delete ----------------------------------------------------------------

    def cmd_delete(self) -> None:
        panel = self.active_panel
        entries = panel.selected_entries()
        if not entries:
            return
        read_only = [e for e in entries if not isinstance(e.path.fs, WritableFileSystem)]
        if read_only:
            names = ", ".join(e.name for e in read_only[:5])
            dialogs.error(
                self._parent,
                f"Cannot delete from a read-only filesystem:\n{names}",
                title="Delete failed",
            )
            return
        preview = ", ".join(entry.name for entry in entries[:5])
        if len(entries) > 5:
            preview += f", and {len(entries) - 5} more"
        if not dialogs.confirm(
            self._parent,
            f"Delete {len(entries)} item(s)?\n\n{preview}",
            title="Confirm delete",
        ):
            return

        sources = [entry.path for entry in entries]

        def work(
            on_progress: ProgressCallback, should_cancel: CancelPredicate
        ) -> list[OperationError]:
            return operations.delete_entries(sources, on_progress, should_cancel)

        errors = dialogs.run_with_progress(self._parent, "Deleting...", work)
        self._refresh_both_panels()
        self._report_errors(errors, "Delete")

    # -- activate file ---------------------------------------------------------

    def _on_activate_file(self, entry: FileEntry) -> None:
        # Try the OS's default application first (requires a real OS path);
        # fall back to the built-in viewer if no opener is available or failed.
        real = entry.path.fs.realpath(entry.path)
        if real is not None and platform_util.open_with_default_app(real):
            return
        viewer.view_file(self._parent, entry.path)
