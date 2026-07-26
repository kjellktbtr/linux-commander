"""Modal dialog helpers: confirm, prompt, error, static text, and a threaded
progress runner for long-running file operations.

``ProgressDialog`` and ``run_with_progress`` live in
``linux_commander.progress_dialog`` but are re-exported here for
backwards compatibility.

``CompressionDialog`` lives in ``linux_commander.compression_dialog`` but
is also re-exported here for backwards compatibility.
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from tkinter import ttk

# Re-exports — keep these so all existing `from linux_commander.dialogs import …`
# callers continue to work without change.
from linux_commander.compression_dialog import CompressionDialog as CompressionDialog
from linux_commander.progress_dialog import ProgressDialog as ProgressDialog
from linux_commander.progress_dialog import WorkFunc as WorkFunc
from linux_commander.progress_dialog import run_with_progress as run_with_progress


def _center_over(top: tk.Toplevel, parent: tk.Misc) -> None:
    top.update_idletasks()
    try:
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
    except tk.TclError:
        return
    w, h = top.winfo_width(), top.winfo_height()
    x = max(px + (pw - w) // 2, 0)
    y = max(py + (ph - h) // 2, 0)
    top.geometry(f"+{x}+{y}")


def confirm(parent: tk.Misc, message: str, title: str = "Confirm") -> bool:
    """Show a modal Yes/No confirmation dialog; blocks and returns True if confirmed."""
    result = {"value": False}
    top = tk.Toplevel(parent)
    top.title(title)
    top.transient(parent)  # type: ignore[call-overload]
    top.resizable(False, False)

    def on_yes() -> None:
        result["value"] = True
        top.destroy()

    def on_no() -> None:
        top.destroy()

    ttk.Label(top, text=message, padding=16, wraplength=360, justify="left").pack()
    button_frame = ttk.Frame(top)
    button_frame.pack(fill="x", padx=8, pady=(0, 8))
    no_btn = ttk.Button(button_frame, text="No", command=on_no)
    yes_btn = ttk.Button(button_frame, text="Yes", command=on_yes)
    no_btn.pack(side="right")
    yes_btn.pack(side="right", padx=8)

    top.bind("<Return>", lambda e: on_yes())
    top.bind("<KP_Enter>", lambda e: on_yes())
    top.bind("<Escape>", lambda e: on_no())
    top.protocol("WM_DELETE_WINDOW", on_no)

    _center_over(top, parent)
    top.grab_set()
    yes_btn.focus_set()
    top.wait_window()
    return result["value"]


def prompt(parent: tk.Misc, title: str, message: str, initial: str = "") -> str | None:
    """Show a modal single-line text prompt; blocks and returns the entered
    text, or None if cancelled."""
    result: dict[str, str | None] = {"value": None}
    top = tk.Toplevel(parent)
    top.title(title)
    top.transient(parent)  # type: ignore[call-overload]
    top.resizable(False, False)

    def on_ok() -> None:
        result["value"] = entry_var.get()
        top.destroy()

    def on_cancel() -> None:
        top.destroy()

    ttk.Label(top, text=message, padding=(16, 16, 16, 4)).pack(anchor="w")
    entry_var = tk.StringVar(value=initial)
    entry = ttk.Entry(top, textvariable=entry_var, width=44)
    entry.pack(padx=16, pady=(0, 8), fill="x")
    entry.select_range(0, "end")
    entry.icursor("end")

    button_frame = ttk.Frame(top)
    button_frame.pack(fill="x", padx=8, pady=8)
    cancel_btn = ttk.Button(button_frame, text="Cancel", command=on_cancel)
    ok_btn = ttk.Button(button_frame, text="OK", command=on_ok)
    cancel_btn.pack(side="right")
    ok_btn.pack(side="right", padx=8)

    top.bind("<Return>", lambda e: on_ok())
    top.bind("<KP_Enter>", lambda e: on_ok())
    top.bind("<Escape>", lambda e: on_cancel())
    top.protocol("WM_DELETE_WINDOW", on_cancel)

    _center_over(top, parent)
    top.grab_set()
    entry.focus_set()
    top.wait_window()
    return result["value"]


def error(parent: tk.Misc, message: str, title: str = "Error") -> None:
    """Show a modal error/info message with a single OK button; blocks until dismissed."""
    top = tk.Toplevel(parent)
    top.title(title)
    top.transient(parent)  # type: ignore[call-overload]
    top.resizable(False, False)

    def on_ok() -> None:
        top.destroy()

    ttk.Label(top, text=message, padding=16, wraplength=400, justify="left").pack()
    ok_btn = ttk.Button(top, text="OK", command=on_ok)
    ok_btn.pack(pady=(0, 12))

    top.bind("<Return>", lambda e: on_ok())
    top.bind("<KP_Enter>", lambda e: on_ok())
    top.bind("<Escape>", lambda e: on_ok())
    top.protocol("WM_DELETE_WINDOW", on_ok)

    _center_over(top, parent)
    top.grab_set()
    ok_btn.focus_set()
    top.wait_window()


def choose_from_list(parent: tk.Misc, title: str, items: list[str]) -> int | None:
    """Show a modal single-selection list dialog (used for the volume
    chooser); blocks and returns the selected index, or None if cancelled."""
    result: dict[str, int | None] = {"value": None}
    top = tk.Toplevel(parent)
    top.title(title)
    top.transient(parent)  # type: ignore[call-overload]
    top.resizable(False, False)

    listbox = tk.Listbox(top, height=min(len(items), 10), width=44, exportselection=False)
    for item in items:
        listbox.insert("end", item)
    if items:
        listbox.selection_set(0)
    listbox.pack(padx=16, pady=16, fill="both", expand=True)

    def on_ok() -> None:
        selection = listbox.curselection()
        if selection:
            result["value"] = selection[0]
        top.destroy()

    def on_cancel() -> None:
        top.destroy()

    button_frame = ttk.Frame(top)
    button_frame.pack(fill="x", padx=8, pady=(0, 8))
    cancel_btn = ttk.Button(button_frame, text="Cancel", command=on_cancel)
    ok_btn = ttk.Button(button_frame, text="OK", command=on_ok)
    cancel_btn.pack(side="right")
    ok_btn.pack(side="right", padx=8)

    listbox.bind("<Double-Button-1>", lambda e: on_ok())
    top.bind("<Return>", lambda e: on_ok())
    top.bind("<KP_Enter>", lambda e: on_ok())
    top.bind("<Escape>", lambda e: on_cancel())
    top.protocol("WM_DELETE_WINDOW", on_cancel)

    _center_over(top, parent)
    top.grab_set()
    listbox.focus_set()
    top.wait_window()
    return result["value"]


def show_text(parent: tk.Misc, title: str, text: str) -> None:
    """Show a read-only, scrollable block of text in a dismissible window
    (used for the F1 Help cheat-sheet). Non-blocking."""
    top = tk.Toplevel(parent)
    top.title(title)
    top.transient(parent)  # type: ignore[call-overload]

    text_widget = tk.Text(
        top, wrap="word", width=64, height=24, font=tkfont.nametofont("TkFixedFont")
    )
    text_widget.insert("1.0", text)
    text_widget.configure(state="disabled")
    text_widget.pack(padx=8, pady=8, fill="both", expand=True)

    close_btn = ttk.Button(top, text="Close", command=top.destroy)
    close_btn.pack(pady=(0, 8))

    top.bind("<Escape>", lambda e: top.destroy())
    top.protocol("WM_DELETE_WINDOW", top.destroy)

    _center_over(top, parent)
    top.grab_set()
    close_btn.focus_set()


def pattern_dialog(
    parent: tk.Misc,
    title: str,
    message: str,
    history: list[str] | None = None,
) -> dict[str, object] | None:
    """Show a modal pattern dialog with history combobox, case-sensitive and
    regex checkboxes. Returns ``{'pattern': str, 'case_sensitive': bool,
    'regex': bool}`` or ``None`` if cancelled.

    ``history`` is a list of previous patterns displayed in the combobox;
    entries are updated in-place if modified by this call.
    """
    if history is None:
        history = []
    result: dict[str, object] | None = None
    top = tk.Toplevel(parent)
    top.title(title)
    top.transient(parent)  # type: ignore[call-overload]
    top.resizable(False, False)

    ttk.Label(top, text=message, padding=(16, 16, 16, 4)).pack(anchor="w")

    pattern_var = tk.StringVar(value=history[0] if history else "*")
    combo = ttk.Combobox(top, textvariable=pattern_var, values=history, width=40)
    combo.pack(padx=16, pady=(0, 8), fill="x")
    combo.select_range(0, "end")
    combo.icursor("end")

    case_var = tk.BooleanVar(value=False)
    case_cb = ttk.Checkbutton(top, text="Case sensitive", variable=case_var)
    case_cb.pack(padx=16, pady=2, anchor="w")

    regex_var = tk.BooleanVar(value=False)
    regex_cb = ttk.Checkbutton(top, text="Regular expression", variable=regex_var)
    regex_cb.pack(padx=16, pady=2, anchor="w")

    def on_ok() -> None:
        nonlocal result
        pattern = pattern_var.get()
        if not pattern:
            return
        # Update history: add or move to front
        if pattern in history:
            history.remove(pattern)
        history.insert(0, pattern)
        result = {
            "pattern": pattern,
            "case_sensitive": case_var.get(),
            "regex": regex_var.get(),
        }
        top.destroy()

    def on_cancel() -> None:
        top.destroy()

    button_frame = ttk.Frame(top)
    button_frame.pack(fill="x", padx=8, pady=8)
    cancel_btn = ttk.Button(button_frame, text="Cancel", command=on_cancel)
    ok_btn = ttk.Button(button_frame, text="OK", command=on_ok)
    cancel_btn.pack(side="right")
    ok_btn.pack(side="right", padx=8)

    top.bind("<Return>", lambda e: on_ok())
    top.bind("<KP_Enter>", lambda e: on_ok())
    top.bind("<Escape>", lambda e: on_cancel())
    top.protocol("WM_DELETE_WINDOW", on_cancel)

    _center_over(top, parent)
    top.grab_set()
    combo.focus_set()
    top.wait_window()
    return result


def date_time_picker(
    parent: tk.Misc,
    title: str,
    initial: datetime | None = None,
) -> datetime | None:
    """Show a modal date/time picker dialog; returns selected datetime or None if cancelled.

    The dialog presents spinboxes for year, month, day, hour, minute, second.
    """
    from datetime import datetime

    if initial is None:
        initial = datetime.now()

    result: dict[str, datetime | None] = {"value": None}
    top = tk.Toplevel(parent)
    top.title(title)
    top.transient(parent)  # type: ignore[call-overload]
    top.resizable(False, False)

    # Date row
    date_frame = ttk.Frame(top, padding=(16, 16, 16, 4))
    date_frame.pack(anchor="w")
    ttk.Label(date_frame, text="Date:").pack(side="left")

    year_var = tk.IntVar(value=initial.year)
    month_var = tk.IntVar(value=initial.month)
    day_var = tk.IntVar(value=initial.day)

    ttk.Spinbox(date_frame, from_=1970, to=2100, textvariable=year_var, width=6).pack(
        side="left", padx=2
    )
    ttk.Label(date_frame, text="-").pack(side="left")
    ttk.Spinbox(date_frame, from_=1, to=12, textvariable=month_var, width=4).pack(
        side="left", padx=2
    )
    ttk.Label(date_frame, text="-").pack(side="left")
    ttk.Spinbox(date_frame, from_=1, to=31, textvariable=day_var, width=4).pack(side="left", padx=2)

    # Time row
    time_frame = ttk.Frame(top, padding=(16, 0, 16, 4))
    time_frame.pack(anchor="w")
    ttk.Label(time_frame, text="Time:").pack(side="left")

    hour_var = tk.IntVar(value=initial.hour)
    minute_var = tk.IntVar(value=initial.minute)
    second_var = tk.IntVar(value=initial.second)

    ttk.Spinbox(time_frame, from_=0, to=23, textvariable=hour_var, width=4).pack(
        side="left", padx=2
    )
    ttk.Label(time_frame, text=":").pack(side="left")
    ttk.Spinbox(time_frame, from_=0, to=59, textvariable=minute_var, width=4).pack(
        side="left", padx=2
    )
    ttk.Label(time_frame, text=":").pack(side="left")
    ttk.Spinbox(time_frame, from_=0, to=59, textvariable=second_var, width=4).pack(
        side="left", padx=2
    )

    # Quick preset buttons
    preset_frame = ttk.Frame(top, padding=(16, 4, 16, 4))
    preset_frame.pack(fill="x")

    def set_preset(days_ago: int) -> None:
        from datetime import timedelta

        dt = datetime.now() - timedelta(days=days_ago)
        year_var.set(dt.year)
        month_var.set(dt.month)
        day_var.set(dt.day)
        hour_var.set(0)
        minute_var.set(0)
        second_var.set(0)

    ttk.Button(preset_frame, text="Today", command=lambda: set_preset(0)).pack(side="left", padx=2)
    ttk.Button(preset_frame, text="Yesterday", command=lambda: set_preset(1)).pack(
        side="left", padx=2
    )
    ttk.Button(preset_frame, text="Last 7 days", command=lambda: set_preset(7)).pack(
        side="left", padx=2
    )
    ttk.Button(preset_frame, text="Last 30 days", command=lambda: set_preset(30)).pack(
        side="left", padx=2
    )

    # Buttons
    button_frame = ttk.Frame(top)
    button_frame.pack(fill="x", padx=8, pady=8)
    cancel_btn = ttk.Button(button_frame, text="Cancel", command=lambda: top.destroy())
    ok_btn = ttk.Button(
        button_frame,
        text="OK",
        command=lambda: (
            result.update(
                {
                    "value": datetime(
                        year_var.get(),
                        month_var.get(),
                        day_var.get(),
                        hour_var.get(),
                        minute_var.get(),
                        second_var.get(),
                    )
                }
            ),
            top.destroy(),  # type: ignore[func-returns-value]
            None,
        ),
    )
    cancel_btn.pack(side="right")
    ok_btn.pack(side="right", padx=8)

    top.bind("<Return>", lambda e: ok_btn.invoke())
    top.bind("<KP_Enter>", lambda e: ok_btn.invoke())
    top.bind("<Escape>", lambda e: cancel_btn.invoke())
    top.protocol("WM_DELETE_WINDOW", cancel_btn.invoke)

    _center_over(top, parent)
    top.grab_set()
    top.wait_window()
    return result["value"]


def show_plugin_status(parent: tk.Misc) -> None:
    """Show a modal dialog displaying the load status of all plugins."""
    from linux_commander.file_ops import OperationPluginLoadStatus, get_operation_plugin_load_status
    from linux_commander.plugins import PluginLoadStatus, get_plugin_load_status

    vfs_status: list[PluginLoadStatus] = get_plugin_load_status()
    ops_status: list[OperationPluginLoadStatus] = get_operation_plugin_load_status()

    top = tk.Toplevel(parent)
    top.title("Plugin Status")
    top.transient(parent)  # type: ignore[call-overload]
    top.resizable(True, True)

    # Create main frame with padding
    main_frame = ttk.Frame(top, padding=12)
    main_frame.pack(fill="both", expand=True)

    # Create notebook for VFS plugins and Operation plugins
    notebook = ttk.Notebook(main_frame)
    notebook.pack(fill="both", expand=True)

    # VFS Plugins tab
    vfs_frame = ttk.Frame(notebook, padding=8)
    notebook.add(vfs_frame, text="VFS Plugins")

    # Operation Plugins tab
    ops_frame = ttk.Frame(notebook, padding=8)
    notebook.add(ops_frame, text="Operation Plugins")

    # Create treeview for VFS plugins
    vfs_columns = ("name", "status", "extensions", "schemes", "view_extensions", "error")
    vfs_tree = ttk.Treeview(vfs_frame, columns=vfs_columns, show="headings", height=15)
    vfs_tree.heading("name", text="Plugin")
    vfs_tree.heading("status", text="Status")
    vfs_tree.heading("extensions", text="Extensions")
    vfs_tree.heading("schemes", text="Schemes")
    vfs_tree.heading("view_extensions", text="Viewer Ext.")
    vfs_tree.heading("error", text="Error")
    vfs_tree.column("name", width=150, anchor="w")
    vfs_tree.column("status", width=80, anchor="center")
    vfs_tree.column("extensions", width=150, anchor="w")
    vfs_tree.column("schemes", width=100, anchor="w")
    vfs_tree.column("view_extensions", width=120, anchor="w")
    vfs_tree.column("error", width=300, anchor="w")

    vfs_scroll = ttk.Scrollbar(vfs_frame, orient="vertical", command=vfs_tree.yview)
    vfs_tree.configure(yscrollcommand=vfs_scroll.set)
    vfs_tree.pack(side="left", fill="both", expand=True)
    vfs_scroll.pack(side="right", fill="y")

    for status in vfs_status:
        status_text = "✓ Loaded" if status.success else "✗ Failed"
        vfs_tree.insert(
            "",
            "end",
            values=(
                status.name,
                status_text,
                ", ".join(status.extensions) if status.extensions else "—",
                ", ".join(status.schemes) if status.schemes else "—",
                ", ".join(status.view_extensions) if status.view_extensions else "—",
                status.error or "",
            ),
        )

    # Create treeview for operation plugins
    ops_columns = ("name", "status", "operations", "error")
    ops_tree = ttk.Treeview(ops_frame, columns=ops_columns, show="headings", height=15)
    ops_tree.heading("name", text="Plugin")
    ops_tree.heading("status", text="Status")
    ops_tree.heading("operations", text="Operations")
    ops_tree.heading("error", text="Error")
    ops_tree.column("name", width=150, anchor="w")
    ops_tree.column("status", width=80, anchor="center")
    ops_tree.column("operations", width=250, anchor="w")
    ops_tree.column("error", width=300, anchor="w")

    ops_scroll = ttk.Scrollbar(ops_frame, orient="vertical", command=ops_tree.yview)
    ops_tree.configure(yscrollcommand=ops_scroll.set)
    ops_tree.pack(side="left", fill="both", expand=True)
    ops_scroll.pack(side="right", fill="y")

    for status in ops_status:  # type: ignore[assignment]
        status_text = "✓ Loaded" if status.success else "✗ Failed"
        ops_tree.insert(
            "",
            "end",
            values=(
                status.name,
                status_text,
                ", ".join(status.operations) if status.operations else "—",  # type: ignore[attr-defined]
                status.error or "",
            ),
        )

    # Summary label
    summary_frame = ttk.Frame(main_frame)
    summary_frame.pack(fill="x", pady=(8, 0))

    vfs_loaded = sum(1 for s in vfs_status if s.success)
    vfs_failed = len(vfs_status) - vfs_loaded
    ops_loaded = sum(1 for s in ops_status if s.success)
    ops_failed = len(ops_status) - ops_loaded

    summary_text = (
        f"VFS Plugins: {vfs_loaded} loaded, {vfs_failed} failed  |  "
        f"Operation Plugins: {ops_loaded} loaded, {ops_failed} failed"
    )
    ttk.Label(summary_frame, text=summary_text).pack(side="left")

    # Close button
    button_frame = ttk.Frame(main_frame)
    button_frame.pack(fill="x", pady=(12, 0))
    close_btn = ttk.Button(button_frame, text="Close", command=top.destroy)
    close_btn.pack(side="right")

    top.bind("<Escape>", lambda e: top.destroy())
    top.protocol("WM_DELETE_WINDOW", top.destroy)

    _center_over(top, parent)
    top.grab_set()
    close_btn.focus_set()
    top.wait_window()
