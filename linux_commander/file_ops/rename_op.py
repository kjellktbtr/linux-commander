"""File operation: Batch rename with regex preview."""

from __future__ import annotations

import re
import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk
from typing import TYPE_CHECKING, cast

from linux_commander.dialogs import _center_over, confirm
from linux_commander.file_ops import FileOperation
from linux_commander.operations import (
    CancelPredicate,
    OperationError,
    ProgressCallback,
    call_progress,
)
from linux_commander.vfs import VfsPath, WritableFileSystem

if TYPE_CHECKING:
    pass


@dataclass
class RenameItem:
    """A single file rename preview item."""

    old_name: str
    new_name: str
    src: VfsPath
    dest: VfsPath
    conflict: bool = False
    error: str | None = None


def _apply_rename_pattern(
    name: str,
    find_pattern: str,
    replace_pattern: str,
    use_regex: bool,
    counter_start: int,
    counter_format: str,
    preserve_ext: bool,
    case_sensitive: bool,
) -> str:
    """Apply rename pattern to a single filename."""
    if preserve_ext:
        # Split extension
        dot_idx = name.rfind(".")
        if dot_idx > 0:
            base = name[:dot_idx]
            ext = name[dot_idx:]
        else:
            base = name
            ext = ""
    else:
        base = name
        ext = ""

    if use_regex:
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            new_base = re.sub(find_pattern, replace_pattern, base, flags=flags)
        except re.error:
            return name  # Invalid regex, return unchanged
    else:
        # Literal find/replace
        if case_sensitive:
            new_base = base.replace(find_pattern, replace_pattern)
        else:
            # Case-insensitive literal replace
            idx = base.lower().find(find_pattern.lower())
            if idx >= 0:
                new_base = base[:idx] + replace_pattern + base[idx + len(find_pattern) :]
            else:
                new_base = base

    # Handle counter placeholder
    if "{n" in counter_format:
        # Counter is handled at the batch level
        pass

    return new_base + ext


def _prepare_batch_rename(parent: tk.Misc, sources: list[VfsPath]) -> dict | None:
    """Show batch rename dialog and return parameters."""
    dialog = BatchRenameDialog(parent, sources)
    return dialog.result


class BatchRenameDialog(tk.Toplevel):
    """Dialog for batch rename with live preview."""

    def __init__(self, parent: tk.Misc, sources: list[VfsPath]) -> None:
        super().__init__(parent)
        self.title("Batch Rename")
        self.sources = sources
        self.result: dict | None = None
        self._preview_items: list[RenameItem] = []
        self._build_ui()
        self._update_preview()
        _center_over(self, parent)
        self.grab_set()
        self.wait_window()

    def _build_ui(self) -> None:
        self.geometry("800x500")
        self.minsize(600, 400)

        # Top controls
        top_frame = ttk.Frame(self)
        top_frame.pack(fill="x", padx=8, pady=8)

        # Find/Replace
        ttk.Label(top_frame, text="Find:").grid(row=0, column=0, sticky="w", padx=(0, 4))
        self.find_var = tk.StringVar()
        ttk.Entry(top_frame, textvariable=self.find_var, width=30).grid(
            row=0, column=1, sticky="ew", padx=(0, 8)
        )

        ttk.Label(top_frame, text="Replace:").grid(row=0, column=2, sticky="w", padx=(0, 4))
        self.replace_var = tk.StringVar()
        ttk.Entry(top_frame, textvariable=self.replace_var, width=30).grid(
            row=0, column=3, sticky="ew", padx=(0, 8)
        )

        top_frame.columnconfigure(1, weight=1)
        top_frame.columnconfigure(3, weight=1)

        # Options row
        opts_frame = ttk.Frame(self)
        opts_frame.pack(fill="x", padx=8, pady=(0, 8))

        self.use_regex_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            opts_frame,
            text="Regular Expression",
            variable=self.use_regex_var,
            command=self._update_preview,
        ).pack(side="left", padx=(0, 8))

        self.preserve_ext_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            opts_frame,
            text="Preserve Extension",
            variable=self.preserve_ext_var,
            command=self._update_preview,
        ).pack(side="left", padx=(0, 8))

        self.case_sensitive_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            opts_frame,
            text="Case Sensitive",
            variable=self.case_sensitive_var,
            command=self._update_preview,
        ).pack(side="left", padx=(0, 8))

        # Counter
        ttk.Label(opts_frame, text="Counter:").pack(side="left", padx=(16, 4))
        self.counter_var = tk.StringVar(value="{n:03d}")
        ttk.Entry(opts_frame, textvariable=self.counter_var, width=12).pack(
            side="left", padx=(0, 4)
        )
        ttk.Label(opts_frame, text="(start)").pack(side="left", padx=(0, 4))
        self.counter_start_var = tk.IntVar(value=1)
        ttk.Spinbox(
            opts_frame, from_=1, to=9999, textvariable=self.counter_start_var, width=6
        ).pack(side="left", padx=(0, 4))

        # Preview table
        table_frame = ttk.Frame(self)
        table_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        columns = ("old", "new", "status")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("old", text="Old Name")
        self.tree.heading("new", text="New Name")
        self.tree.heading("status", text="Status")
        self.tree.column("old", width=250, anchor="w")
        self.tree.column("new", width=250, anchor="w")
        self.tree.column("status", width=100, anchor="center")
        self.tree.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")

        self.tree.tag_configure("conflict", foreground="red")
        self.tree.tag_configure("ok", foreground="green")
        self.tree.tag_configure("skip", foreground="gray")

        # Bottom buttons
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x", padx=8, pady=(0, 8))

        ttk.Button(btn_frame, text="Preview", command=self._update_preview).pack(
            side="left", padx=4
        )
        ttk.Button(btn_frame, text="Select All", command=self._select_all).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Deselect All", command=self._deselect_all).pack(
            side="left", padx=4
        )
        ttk.Button(btn_frame, text="Cancel", command=self._on_cancel).pack(side="right", padx=4)
        ttk.Button(btn_frame, text="OK", command=self._on_ok).pack(side="right", padx=4)

        # Bind events
        self.find_var.trace_add("write", lambda *_: self._update_preview())
        self.replace_var.trace_add("write", lambda *_: self._update_preview())
        self.counter_var.trace_add("write", lambda *_: self._update_preview())
        self.counter_start_var.trace_add("write", lambda *_: self._update_preview())

    def _select_all(self) -> None:
        for item in self.tree.get_children():
            self.tree.selection_add(item)

    def _deselect_all(self) -> None:
        self.tree.selection_remove(*self.tree.get_children())

    def _update_preview(self) -> None:
        """Update the preview table based on current settings."""
        self.tree.delete(*self.tree.get_children())
        self._preview_items = []

        find = self.find_var.get()
        replace = self.replace_var.get()
        use_regex = self.use_regex_var.get()
        preserve_ext = self.preserve_ext_var.get()
        case_sensitive = self.case_sensitive_var.get()
        counter_format = self.counter_var.get()
        counter_start = self.counter_start_var.get()

        # Extract counter placeholder
        counter_re = re.compile(r"\{n(:[^}]*)?\}")
        counter_match = counter_re.search(counter_format)
        counter_placeholder = counter_match.group(0) if counter_match else "{n:03d}"

        # Check for conflicts
        new_names: dict[str, int] = {}

        for idx, src in enumerate(self.sources):
            old_name = src.name
            new_name = old_name

            if use_regex and find:
                flags = 0 if case_sensitive else re.IGNORECASE
                try:
                    new_name = re.sub(find, replace, old_name, flags=flags)
                except re.error:
                    new_name = old_name  # Invalid regex
            elif find:
                if case_sensitive:
                    new_name = old_name.replace(find, replace)
                else:
                    # Case-insensitive
                    idx_find = old_name.lower().find(find.lower())
                    if idx_find >= 0:
                        new_name = old_name[:idx_find] + replace + old_name[idx_find + len(find) :]

            # Handle counter
            if counter_placeholder in new_name:
                counter_val = counter_start
                # We need to apply counter per-item
                # Find all items that would get this counter
                pass

            # Apply counter per-item
            counter_val = counter_start + idx
            new_name = new_name.replace(counter_placeholder, f"{counter_val:03d}")

            # Handle extension preservation
            if preserve_ext:
                old_dot = old_name.rfind(".")
                new_dot = new_name.rfind(".")
                if old_dot > 0 and new_dot > 0:
                    new_name = new_name[:new_dot] + old_name[old_dot:]

            conflict = False
            if new_name in new_names:
                conflict = True
            new_names[new_name] = new_names.get(new_name, 0) + 1

            dest = src.parent / new_name

            item = RenameItem(
                old_name=old_name,
                new_name=new_name,
                src=src,
                dest=dest,
                conflict=conflict,
            )
            self._preview_items.append(item)

        # Update tree
        for item in self._preview_items:
            tag = (
                "conflict"
                if item.conflict
                else ("ok" if item.old_name != item.new_name else "skip")
            )
            status = (
                "CONFLICT"
                if item.conflict
                else ("Renamed" if item.old_name != item.new_name else "Skipped")
            )
            self.tree.insert("", "end", values=(item.old_name, item.new_name, status), tags=(tag,))

    def _on_ok(self) -> None:
        # Check for conflicts
        conflicts = [item for item in self._preview_items if item.conflict]
        if conflicts and not confirm(
            self, f"{len(conflicts)} file(s) have name conflicts. Continue anyway?"
        ):
            return

        # Build result dict
        self.result = {
            "items": self._preview_items,
            "find": self.find_var.get(),
            "replace": self.replace_var.get(),
            "use_regex": self.use_regex_var.get(),
            "preserve_ext": self.preserve_ext_var.get(),
            "case_sensitive": self.case_sensitive_var.get(),
        }
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self.destroy()


def _batch_rename_run(
    sources: list[VfsPath],
    dest_dir: VfsPath,
    on_progress: ProgressCallback,
    should_cancel: CancelPredicate,
    items: list[RenameItem],
) -> list[OperationError]:
    """Execute batch rename using precomputed items."""
    errors: list[OperationError] = []
    total = len(items)

    for current, item in enumerate(items, start=1):
        if should_cancel():
            break
        if item.old_name == item.new_name:
            continue  # Skip unchanged

        call_progress(on_progress, current, total, item.old_name, None, None)

        try:
            # Check if dest already exists
            try:
                item.dest.fs.stat(item.dest)
                errors.append(
                    OperationError(item.src, f"Destination '{item.new_name}' already exists")
                )
                continue
            except OSError:
                pass  # Good, doesn't exist

            cast(WritableFileSystem, item.src.fs).rename(item.src, item.dest)
        except OSError as exc:
            errors.append(OperationError(item.src, str(exc)))

    return errors


OPERATIONS: list[FileOperation] = [
    FileOperation(
        name="Batch Rename…",
        run=_batch_rename_run,
        prepare=_prepare_batch_rename,
        description="Rename multiple files with find/replace, regex, counter, and preview.",
    ),
]
