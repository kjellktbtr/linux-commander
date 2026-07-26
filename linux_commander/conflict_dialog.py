"""Conflict resolution dialog for copy/move operations.

Shows a modal dialog listing all conflicting files with per-file resolution
options (Replace, Skip, Replace if newer, Replace if different size, Compare)
and an Apply to All checkbox.
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from tkinter import ttk

from linux_commander.dialogs import _center_over
from linux_commander.operations import ConflictInfo, ConflictResolution


def _format_size(size: int) -> str:
    """Format byte count as human-readable size string."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / (1024 * 1024 * 1024):.1f} GB"


def _format_mtime(mtime: float) -> str:
    """Format modification time as readable date string."""
    try:
        dt = datetime.fromtimestamp(mtime)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (OSError, ValueError):
        return "unknown"


def resolve_conflicts(
    parent: tk.Misc, conflicts: list[ConflictInfo]
) -> dict[int, ConflictResolution] | None:
    """Show a modal dialog to resolve file conflicts.

    Returns a dict mapping conflict index to chosen resolution, or ``None``
    if the user cancelled the dialog.
    """
    top = tk.Toplevel(parent)
    top.title("File Conflicts")
    top.transient(parent)  # type: ignore[call-overload]
    top.resizable(True, True)
    top.grab_set()
    _center_over(top, parent)

    result: dict[int, ConflictResolution] | None = None

    # -- Header --
    header_frame = ttk.Frame(top, padding=8)
    header_frame.pack(fill="x")
    ttk.Label(
        header_frame,
        text=f"{len(conflicts)} file(s) already exist at the destination.",
    ).pack(anchor="w")

    # -- Scrollable frame for conflict rows --
    canvas = tk.Canvas(top, highlightthickness=0)
    scrollbar = ttk.Scrollbar(top, orient="vertical", command=canvas.yview)
    scroll_frame = ttk.Frame(canvas)

    scroll_frame.bind(
        "<Configure>",
        lambda _e: canvas.configure(scrollregion=canvas.bbox("all")),
    )

    canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)

    canvas.pack(side="left", fill="both", expand=True, padx=8, pady=4)
    scrollbar.pack(side="right", fill="y", pady=4)

    # Bind mouse wheel to scroll
    def _on_mousewheel(event: tk.Event) -> None:
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    canvas.bind_all("<MouseWheel>", _on_mousewheel, add="+")

    # -- Column headers --
    hdr = ttk.Frame(scroll_frame)
    hdr.pack(fill="x", pady=(0, 2))
    for text, width in [
        ("Source", 20),
        ("Destination", 20),
        ("Src Size", 10),
        ("Dst Size", 10),
        ("Action", 22),
    ]:
        ttk.Label(hdr, text=text, font=tkfont.nametofont("TkDefaultFont"), width=width).pack(
            side="left", padx=2
        )

    # -- Build one row per conflict --
    res_vars: list[tk.StringVar] = []

    for _i, conflict in enumerate(conflicts):
        row = ttk.Frame(scroll_frame)
        row.pack(fill="x", pady=1)

        # Source name
        ttk.Label(row, text=conflict.source.name, width=20).pack(side="left", padx=2)
        # Dest name
        ttk.Label(row, text=conflict.dest.name, width=20).pack(side="left", padx=2)
        # Source size
        ttk.Label(row, text=_format_size(conflict.source_size), width=10).pack(side="left", padx=2)
        # Dest size
        ttk.Label(row, text=_format_size(conflict.dest_size), width=10).pack(side="left", padx=2)
        # Action combobox
        var = tk.StringVar(value=ConflictResolution.REPLACE.name)
        res_vars.append(var)
        cb = ttk.Combobox(
            row,
            textvariable=var,
            values=[r.name for r in ConflictResolution],
            state="readonly",
            width=20,
        )
        cb.pack(side="left", padx=2)

        # Double-click to show details
        def _on_dblclick(_event: tk.Event, c: ConflictInfo = conflict) -> None:
            info_var.set(
                f"Source: {c.source.name} "
                f"({_format_size(c.source_size)}, {_format_mtime(c.source_mtime)})\n"
                f"Dest:   {c.dest.name} "
                f"({_format_size(c.dest_size)}, {_format_mtime(c.dest_mtime)})"
            )

        row.bind("<Double-Button-1>", _on_dblclick)

    # -- Info frame (shows details for double-clicked row) --
    info_frame = ttk.Frame(top)
    info_frame.pack(fill="x", padx=8, pady=4)
    info_var = tk.StringVar(value="Double-click a row for details")
    ttk.Label(info_frame, textvariable=info_var, wraplength=600, justify="left").pack(anchor="w")

    # -- Apply to All checkbox --
    apply_all_var = tk.BooleanVar(value=False)

    def _apply_to_all() -> None:
        if apply_all_var.get():
            chosen = res_vars[0].get()
            for v in res_vars:
                v.set(chosen)

    apply_frame = ttk.Frame(top)
    apply_frame.pack(fill="x", padx=8, pady=2)
    ttk.Checkbutton(
        apply_frame,
        text="Apply to All (use first file's action for all conflicts)",
        variable=apply_all_var,
        command=_apply_to_all,
    ).pack(anchor="w")

    # -- Buttons --
    btn_frame = ttk.Frame(top, padding=8)
    btn_frame.pack(fill="x")

    def _on_ok() -> None:
        nonlocal result
        result = {}
        for i, var in enumerate(res_vars):
            result[i] = ConflictResolution[var.get()]
        top.destroy()

    def _on_cancel() -> None:
        nonlocal result
        result = None
        top.destroy()

    ok_btn = ttk.Button(btn_frame, text="OK", command=_on_ok)
    ok_btn.pack(side="right", padx=4)
    ttk.Button(btn_frame, text="Cancel", command=_on_cancel).pack(side="right")

    top.bind("<Return>", lambda _: _on_ok())
    top.bind("<KP_Enter>", lambda _: _on_ok())
    top.bind("<Escape>", lambda _: _on_cancel())
    top.protocol("WM_DELETE_WINDOW", _on_cancel)

    ok_btn.focus_set()
    top.wait_window()
    return result
