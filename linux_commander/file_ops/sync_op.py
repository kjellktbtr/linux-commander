"""File operation: Directory synchronization with mirror/update/backup modes.

Supports three sync modes:
- Mirror: Make destination identical to source (delete extra, update changed, copy new)
- Update: Copy newer/missing files from source to destination (no deletions)
- Backup: Copy source to destination, preserving destination extras (no deletions,
  no overwrites of newer files)

Includes a dry-run preview dialog with a tree view of planned actions.
"""

from __future__ import annotations

import os
import tkinter as tk
from dataclasses import dataclass
from enum import Enum
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING, cast

from linux_commander.dialogs import _center_over
from linux_commander.file_ops import FileOperation
from linux_commander.operations import CancelPredicate, OperationError, ProgressCallback
from linux_commander.vfs import VfsPath, WritableFileSystem

if TYPE_CHECKING:
    from linux_commander.vfs import ReadableFileSystem, WritableFileSystem


class SyncMode(Enum):
    MIRROR = "Mirror (delete extra in destination)"
    UPDATE = "Update (copy newer/missing only)"
    BACKUP = "Backup (copy all, keep destination extras)"


@dataclass(frozen=True, slots=True)
class SyncAction:
    """A single action planned during sync preview."""

    action: str  # "copy", "update", "delete", "skip"
    source: VfsPath | None
    dest: VfsPath | None
    size: int
    reason: str


@dataclass
class SyncPlan:
    """Complete sync plan with all actions."""

    actions: list[SyncAction]
    total_copy_size: int
    files_to_copy: int
    files_to_update: int
    files_to_delete: int
    files_to_skip: int


def _stat_or_none(fs: ReadableFileSystem, path: VfsPath):
    """Stat a path, return None if not found."""
    try:
        return fs.stat(path)
    except OSError:
        return None


def _is_newer(source_stat, dest_stat) -> bool:
    """Check if source is newer than destination (mtime comparison)."""
    return source_stat.mtime > dest_stat.mtime


def _is_different(source_stat, dest_stat) -> bool:
    """Check if files differ (size or mtime)."""
    return source_stat.size != dest_stat.size or source_stat.mtime != dest_stat.mtime


def _matches_patterns(name: str, include_patterns: list[str], exclude_patterns: list[str]) -> bool:
    """Check if filename matches include/exclude patterns (simple glob-style)."""
    import fnmatch

    if include_patterns:
        if not any(fnmatch.fnmatch(name, pat) for pat in include_patterns):
            return False
    if exclude_patterns:
        if any(fnmatch.fnmatch(name, pat) for pat in exclude_patterns):
            return False
    return True


def _build_sync_plan(
    source_dir: VfsPath,
    dest_dir: VfsPath,
    mode: SyncMode,
    include_patterns: list[str],
    exclude_patterns: list[str],
    max_size: int | None,
    min_size: int | None,
    max_age_days: float | None,
    min_age_days: float | None,
) -> SyncPlan:
    """Build a sync plan by comparing source and destination trees."""
    import time

    now = time.time()

    actions: list[SyncAction] = []
    total_copy_size = 0
    files_to_copy = files_to_update = files_to_delete = files_to_skip = 0

    source_fs = source_dir.fs
    dest_fs = dest_dir.fs

    # Build maps of source and destination files
    source_files: dict[str, VfsPath] = {}  # relative_path -> VfsPath
    dest_files: dict[str, VfsPath] = {}

    def walk_dir(path: VfsPath, store: dict[str, VfsPath], prefix: str = ""):
        for entry in path.fs.list_dir(path):
            if entry.is_parent:
                continue
            rel_path = prefix + entry.name
            if entry.is_dir:
                walk_dir(entry.path, store, rel_path + "/")
            else:
                store[rel_path] = entry.path

    walk_dir(source_dir, source_files)
    walk_dir(dest_dir, dest_files)

    # Process source files
    for rel_path, src_path in source_files.items():
        name = os.path.basename(rel_path)
        if not _matches_patterns(name, include_patterns, exclude_patterns):
            continue

        src_stat = _stat_or_none(source_fs, src_path)
        if src_stat is None:
            continue

        # Check size filters
        if max_size is not None and src_stat.size > max_size:
            continue
        if min_size is not None and src_stat.size < min_size:
            continue

        # Check age filters
        if max_age_days is not None and (now - src_stat.mtime) / 86400 > max_age_days:
            continue
        if min_age_days is not None and (now - src_stat.mtime) / 86400 < min_age_days:
            continue

        if rel_path in dest_files:
            dest_path = dest_files[rel_path]
            dest_stat = _stat_or_none(dest_fs, dest_path)
            if dest_stat is None:
                actions.append(
                    SyncAction("copy", src_path, dest_path, src_stat.size, "Destination missing")
                )
                total_copy_size += src_stat.size
                files_to_copy += 1
            elif _is_different(src_stat, dest_stat):
                if _is_newer(src_stat, dest_stat) or mode == SyncMode.MIRROR:
                    actions.append(
                        SyncAction(
                            "update", src_path, dest_path, src_stat.size, "Source newer/different"
                        )
                    )
                    total_copy_size += src_stat.size
                    files_to_update += 1
                else:
                    actions.append(SyncAction("skip", src_path, dest_path, 0, "Destination newer"))
                    files_to_skip += 1
            else:
                actions.append(SyncAction("skip", src_path, dest_path, 0, "Identical"))
                files_to_skip += 1
        else:
            # New file in source
            dest_path = dest_dir
            for part in rel_path.split("/")[:-1]:
                dest_path = dest_path / part
            dest_path = dest_path / name
            actions.append(SyncAction("copy", src_path, dest_path, src_stat.size, "New file"))
            total_copy_size += src_stat.size
            files_to_copy += 1

    # Process destination files for mirror mode (delete extras)
    if mode == SyncMode.MIRROR:
        for rel_path, dest_path in dest_files.items():
            if rel_path not in source_files:
                dest_stat = _stat_or_none(dest_fs, dest_path)
                size = dest_stat.size if dest_stat else 0
                actions.append(SyncAction("delete", None, dest_path, size, "Not in source"))
                files_to_delete += 1

    return SyncPlan(
        actions=actions,
        total_copy_size=total_copy_size,
        files_to_copy=files_to_copy,
        files_to_update=files_to_update,
        files_to_delete=files_to_delete,
        files_to_skip=files_to_skip,
    )


def _format_size(size: int) -> str:
    """Format bytes as human-readable string."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size = size // 1024
    return f"{size:.1f} PB"


class SyncDialog(tk.Toplevel):
    """Dialog for configuring and previewing directory sync."""

    def __init__(self, parent: tk.Misc, source: VfsPath, dest: VfsPath) -> None:
        super().__init__(parent)
        self.title("Directory Synchronization")
        self.source = source
        self.dest = dest
        self.result: dict | None = None
        self._plan: SyncPlan | None = None
        self._build_ui()
        _center_over(self, parent)
        self.grab_set()
        self.wait_window()

    def _build_ui(self) -> None:
        self.geometry("1000x700")
        self.minsize(800, 600)

        # Paned window for split view
        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=8, pady=8)

        # Left panel - Options
        left_frame = ttk.Frame(paned, padding=8)
        paned.add(left_frame, weight=1)

        # Source/Dest paths
        path_frame = ttk.LabelFrame(left_frame, text="Directories", padding=8)
        path_frame.pack(fill="x", pady=(0, 8))

        ttk.Label(path_frame, text="Source:").grid(row=0, column=0, sticky="w")
        ttk.Label(path_frame, text=str(self.source), foreground="blue").grid(
            row=0, column=1, sticky="w", padx=(4, 0)
        )

        ttk.Label(path_frame, text="Destination:").grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Label(path_frame, text=str(self.dest), foreground="blue").grid(
            row=1, column=1, sticky="w", padx=(4, 0), pady=(4, 0)
        )

        ttk.Button(path_frame, text="Swap", command=self._swap_dirs).grid(
            row=0, column=2, rowspan=2, padx=(8, 0)
        )
        path_frame.columnconfigure(1, weight=1)

        # Mode selection
        mode_frame = ttk.LabelFrame(left_frame, text="Sync Mode", padding=8)
        mode_frame.pack(fill="x", pady=(0, 8))

        self.mode_var = tk.StringVar(value=SyncMode.MIRROR.value)
        for mode in SyncMode:
            ttk.Radiobutton(
                mode_frame,
                text=mode.value,
                variable=self.mode_var,
                value=mode.value,
                command=self._update_preview,
            ).pack(anchor="w")

        # Filters
        filter_frame = ttk.LabelFrame(left_frame, text="Filters", padding=8)
        filter_frame.pack(fill="x", pady=(0, 8))

        ttk.Label(filter_frame, text="Include patterns (glob):").grid(row=0, column=0, sticky="w")
        self.include_var = tk.StringVar()
        ttk.Entry(filter_frame, textvariable=self.include_var).grid(
            row=0, column=1, sticky="ew", padx=(4, 0)
        )

        ttk.Label(filter_frame, text="Exclude patterns (glob):").grid(
            row=1, column=0, sticky="w", pady=(4, 0)
        )
        self.exclude_var = tk.StringVar()
        ttk.Entry(filter_frame, textvariable=self.exclude_var).grid(
            row=1, column=1, sticky="ew", padx=(4, 0), pady=(4, 0)
        )

        filter_frame.columnconfigure(1, weight=1)

        # Size filters
        size_frame = ttk.LabelFrame(
            left_frame, text="Size Filters (bytes, empty = no limit)", padding=8
        )
        size_frame.pack(fill="x", pady=(0, 8))

        ttk.Label(size_frame, text="Min size:").grid(row=0, column=0, sticky="w")
        self.min_size_var = tk.StringVar()
        ttk.Entry(size_frame, textvariable=self.min_size_var, width=15).grid(
            row=0, column=1, sticky="w", padx=(4, 0)
        )

        ttk.Label(size_frame, text="Max size:").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.max_size_var = tk.StringVar()
        ttk.Entry(size_frame, textvariable=self.max_size_var, width=15).grid(
            row=1, column=1, sticky="w", padx=(4, 0), pady=(4, 0)
        )

        # Age filters
        age_frame = ttk.LabelFrame(
            left_frame, text="Age Filters (days, empty = no limit)", padding=8
        )
        age_frame.pack(fill="x", pady=(0, 8))

        ttk.Label(age_frame, text="Min age (older than):").grid(row=0, column=0, sticky="w")
        self.min_age_var = tk.StringVar()
        ttk.Entry(age_frame, textvariable=self.min_age_var, width=15).grid(
            row=0, column=1, sticky="w", padx=(4, 0)
        )

        ttk.Label(age_frame, text="Max age (newer than):").grid(
            row=1, column=0, sticky="w", pady=(4, 0)
        )
        self.max_age_var = tk.StringVar()
        ttk.Entry(age_frame, textvariable=self.max_age_var, width=15).grid(
            row=1, column=1, sticky="w", padx=(4, 0), pady=(4, 0)
        )

        # Dry run checkbox
        self.dry_run_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(left_frame, text="Dry run (preview only)", variable=self.dry_run_var).pack(
            anchor="w", pady=(0, 8)
        )

        # Summary
        summary_frame = ttk.LabelFrame(left_frame, text="Summary", padding=8)
        summary_frame.pack(fill="x", pady=(0, 8))

        self.summary_var = tk.StringVar(value="Click Preview to analyze")
        ttk.Label(summary_frame, textvariable=self.summary_var, justify="left").pack(anchor="w")

        # Preview button
        ttk.Button(
            left_frame, text="Preview", command=self._update_preview, style="Accent.TButton"
        ).pack(fill="x", pady=(0, 8))

        # Buttons
        btn_frame = ttk.Frame(left_frame)
        btn_frame.pack(fill="x")
        ttk.Button(btn_frame, text="Cancel", command=self._on_cancel).pack(side="right", padx=4)
        ttk.Button(btn_frame, text="OK", command=self._on_ok, style="Accent.TButton").pack(
            side="right", padx=4
        )

        # Right panel - Preview tree
        right_frame = ttk.Frame(paned, padding=8)
        paned.add(right_frame, weight=2)

        ttk.Label(right_frame, text="Sync Preview (double-click to toggle action)").pack(anchor="w")

        tree_frame = ttk.Frame(right_frame)
        tree_frame.pack(fill="both", expand=True, pady=(4, 0))

        columns = ("action", "path", "size", "reason")
        self.tree = ttk.Treeview(
            tree_frame, columns=columns, show="headings", selectmode="extended"
        )
        self.tree.heading("action", text="Action")
        self.tree.heading("path", text="Path")
        self.tree.heading("size", text="Size")
        self.tree.heading("reason", text="Reason")
        self.tree.column("action", width=100, anchor="center")
        self.tree.column("path", width=400, anchor="w")
        self.tree.column("size", width=80, anchor="e")
        self.tree.column("reason", width=200, anchor="w")

        self.tree.tag_configure("copy", foreground="blue")
        self.tree.tag_configure("update", foreground="orange")
        self.tree.tag_configure("delete", foreground="red")
        self.tree.tag_configure("skip", foreground="gray")

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        self.tree.bind("<Double-1>", self._on_toggle_action)
        self.tree.bind("<space>", self._on_toggle_action)

        # Initial preview
        self._update_preview()

    def _swap_dirs(self) -> None:
        self.source, self.dest = self.dest, self.source
        # Update labels
        for widget in self.winfo_children():
            if isinstance(widget, ttk.PanedWindow):
                for child in widget.winfo_children():
                    if isinstance(child, ttk.Frame):
                        for w in child.winfo_children():
                            if isinstance(w, ttk.LabelFrame) and w.cget("text") == "Directories":
                                for lbl in w.winfo_children():
                                    if (
                                        isinstance(lbl, ttk.Label)
                                        and lbl.cget("foreground") == "blue"
                                    ):
                                        if "Source" in str(lbl.grid_info()):
                                            lbl.config(text=str(self.source))
                                        else:
                                            lbl.config(text=str(self.dest))
        self._update_preview()

    def _parse_patterns(self, text: str) -> list[str]:
        return [p.strip() for p in text.split() if p.strip()]

    def _parse_size(self, text: str) -> int | None:
        text = text.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None

    def _parse_age(self, text: str) -> float | None:
        text = text.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    def _update_preview(self) -> None:
        """Build and display the sync plan."""
        include = self._parse_patterns(self.include_var.get())
        exclude = self._parse_patterns(self.exclude_var.get())
        min_size = self._parse_size(self.min_size_var.get())
        max_size = self._parse_size(self.max_size_var.get())
        min_age = self._parse_age(self.min_age_var.get())
        max_age = self._parse_age(self.max_age_var.get())

        mode_str = self.mode_var.get()
        mode = next(m for m in SyncMode if m.value == mode_str)

        try:
            self._plan = _build_sync_plan(
                self.source, self.dest, mode, include, exclude, max_size, min_size, max_age, min_age
            )
        except Exception as e:
            messagebox.showerror("Error", f"Failed to build sync plan: {e}", parent=self)
            self._plan = None
            return

        # Update tree
        self.tree.delete(*self.tree.get_children())
        for action in self._plan.actions:
            tag = action.action
            size_str = (
                _format_size(action.size) if action.action in ("copy", "update", "delete") else ""
            )
            path = action.source if action.source else action.dest
            self.tree.insert(
                "",
                "end",
                values=(action.action.capitalize(), str(path), size_str, action.reason),
                tags=(tag,),
            )

        # Update summary
        summary = (
            f"Files to copy: {self._plan.files_to_copy}\n"
            f"Files to update: {self._plan.files_to_update}\n"
            f"Files to delete: {self._plan.files_to_delete}\n"
            f"Files to skip: {self._plan.files_to_skip}\n"
            f"Total data to transfer: {_format_size(self._plan.total_copy_size)}"
        )
        self.summary_var.set(summary)

    def _on_toggle_action(self, event) -> None:
        """Toggle action on double-click/space (for manual override)."""
        # For simplicity, we don't implement manual toggle yet
        pass

    def _on_ok(self) -> None:
        if self._plan is None:
            self._update_preview()
            if self._plan is None:
                return

        mode_str = self.mode_var.get()
        mode = next(m for m in SyncMode if m.value == mode_str)

        self.result = {
            "source": self.source,
            "dest": self.dest,
            "mode": mode,
            "include_patterns": self._parse_patterns(self.include_var.get()),
            "exclude_patterns": self._parse_patterns(self.exclude_var.get()),
            "min_size": self._parse_size(self.min_size_var.get()),
            "max_size": self._parse_size(self.max_size_var.get()),
            "min_age_days": self._parse_age(self.min_age_var.get()),
            "max_age_days": self._parse_age(self.max_age_var.get()),
            "dry_run": self.dry_run_var.get(),
            "plan": self._plan,
        }
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self.destroy()


def _prepare_sync(parent: tk.Misc, sources: list[VfsPath]) -> dict | None:
    """Prepare dialog - uses the two panel directories as source/dest."""
    from linux_commander.app import CommanderApp

    # Find the app instance
    app: CommanderApp | None = None
    widget: tk.Misc | None = parent
    while widget:
        if isinstance(widget, CommanderApp):
            app = widget
            break
        widget = widget.master

    if app is None:
        from linux_commander.dialogs import error

        error(parent, "Error", "Could not find application instance")
        return None

    # Use the two panel directories
    left_panel = app.left_panel
    right_panel = app.right_panel
    active_panel = app.active_panel
    other_panel = right_panel if active_panel is left_panel else left_panel

    source_dir = active_panel.current_path
    dest_dir = other_panel.current_path

    dialog = SyncDialog(parent, source_dir, dest_dir)
    return dialog.result


def _run_sync(
    sources: list[VfsPath],
    dest_dir: VfsPath,
    on_progress: ProgressCallback,
    should_cancel: CancelPredicate,
    *,
    source: VfsPath,
    dest: VfsPath,
    mode: SyncMode,
    include_patterns: list[str],
    exclude_patterns: list[str],
    min_size: int | None,
    max_size: int | None,
    min_age_days: float | None,
    max_age_days: float | None,
    dry_run: bool,
    plan: SyncPlan,
) -> list[OperationError]:
    """Execute the sync plan."""
    errors: list[OperationError] = []

    if dry_run:
        # Dry run - just report what would happen
        on_progress(1, 1, "Dry run complete - no changes made")
        return errors

    # Execute actions
    total = len([a for a in plan.actions if a.action != "skip"])
    current = 0

    for action in plan.actions:
        if should_cancel():
            break
        if action.action == "skip":
            continue

        current += 1
        on_progress(current, total, str(action.source or action.dest))

        try:
            if action.action == "delete":
                if action.dest is not None:
                    cast(WritableFileSystem, action.dest.fs).delete(action.dest)
            elif action.action in ("copy", "update"):
                if action.source is not None and action.dest is not None:
                    # Ensure destination directory exists
                    dest_parent = action.dest.parent
                    real_dest_parent = dest_parent.fs.realpath(dest_parent)
                    if real_dest_parent is not None:
                        real_dest_parent.mkdir(parents=True, exist_ok=True)

                    # Copy the file
                    from linux_commander.operations import copy_entries

                    errs = copy_entries(
                        [action.source],
                        action.dest.parent,
                        on_progress=lambda *args: None,
                        should_cancel=lambda: False,
                    )
                    for err in errs:
                        errors.append(err)
        except Exception as exc:
            path = action.source or action.dest
            if path:
                errors.append(OperationError(path=path, message=str(exc)))

    return errors


OPERATIONS: list[FileOperation] = [
    FileOperation(
        name="Synchronize Directories",
        run=_run_sync,
        prepare=_prepare_sync,
        description="Synchronize two directories (Mirror/Update/Backup) with dry-run preview.",
    ),
]
