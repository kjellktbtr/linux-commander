"""Columns configuration dialog for linux-commander."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

from linux_commander import dialogs

if TYPE_CHECKING:
    from linux_commander.panel import FilePanel


# All available columns with their display names and default order
ALL_COLUMNS: list[tuple[str, str]] = [
    ("name", "Name"),
    ("extension", "Extension"),
    ("size", "Size"),
    ("modified", "Modified"),
    ("owner", "Owner"),
    ("group", "Group"),
    ("permissions", "Permissions"),
]

DEFAULT_COLUMNS: list[str] = ["name", "extension", "size", "modified"]


class ColumnsDialog(tk.Toplevel):
    """Dialog for configuring visible columns in the file panel."""

    def __init__(
        self,
        parent: tk.Misc,
        panel: FilePanel,
        other_panel: FilePanel | None = None,
    ) -> None:
        super().__init__(parent)
        self.title("Columns")
        self.panel = panel
        self.other_panel = other_panel
        self._build_ui()
        self._populate()
        dialogs._center_over(self, parent)
        self.grab_set()
        self.wait_window()

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        # Main frame with list of columns
        main_frame = ttk.Frame(self)
        main_frame.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(0, weight=1)

        # Label
        ttk.Label(
            main_frame, text="Visible columns (drag to reorder, check/uncheck to show/hide):"
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))

        # Treeview for columns
        self.tree = ttk.Treeview(
            main_frame,
            columns=("visible", "name"),
            show="headings",
            selectmode="browse",
            height=12,
        )
        self.tree.heading("visible", text="Visible")
        self.tree.heading("name", text="Column")
        self.tree.column("visible", width=60, anchor="center", stretch=False)
        self.tree.column("name", width=200, anchor="w")
        self.tree.grid(row=1, column=0, sticky="nsew")

        # Scrollbar
        scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=1, column=1, sticky="ns")

        # Buttons
        btn_frame = ttk.Frame(self)
        btn_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))
        btn_frame.columnconfigure(0, weight=1)

        ttk.Button(btn_frame, text="Move Up", command=self._move_up).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Move Down", command=self._move_down).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Reset to Defaults", command=self._reset_defaults).pack(
            side="left", padx=4
        )
        ttk.Button(btn_frame, text="OK", command=self._on_ok).pack(side="right", padx=4)
        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(side="right", padx=4)

        # Bind double-click to toggle visibility
        self.tree.bind("<Double-1>", self._on_double_click)
        # Bind space to toggle
        self.tree.bind("<space>", self._on_space)

    def _populate(self) -> None:
        self.tree.delete(*self.tree.get_children())
        visible_set = set(self.panel._visible_columns())
        for _idx, (col_id, col_name) in enumerate(ALL_COLUMNS):
            visible = col_id in visible_set
            self.tree.insert(
                "",
                "end",
                iid=col_id,
                values=("✓" if visible else "", col_name),
            )

    def _on_double_click(self, event: tk.Event) -> None:
        item = self.tree.identify_row(event.y)
        if item:
            self._toggle_visibility(item)

    def _on_space(self, event: tk.Event) -> None:
        item = self.tree.focus()
        if item:
            self._toggle_visibility(item)

    def _toggle_visibility(self, item: str) -> None:
        values = self.tree.item(item, "values")
        if not values:
            return
        visible = values[0] == ""
        new_visible = "✓" if not visible else ""
        self.tree.item(item, values=(new_visible, values[1]))

    def _move_up(self) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        item = selection[0]
        index = self.tree.index(item)
        if index > 0:
            self.tree.move(item, "", index - 1)
            self.tree.selection_set(item)

    def _move_down(self) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        item = selection[0]
        index = self.tree.index(item)
        if index < len(self.tree.get_children()) - 1:
            self.tree.move(item, "", index + 1)
            self.tree.selection_set(item)

    def _reset_defaults(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for col_id, col_name in ALL_COLUMNS:
            visible = col_id in DEFAULT_COLUMNS
            self.tree.insert(
                "",
                "end",
                iid=col_id,
                values=("✓" if visible else "", col_name),
            )

    def _on_ok(self) -> None:
        # Collect visible columns in order
        visible_columns: list[str] = []
        for item in self.tree.get_children():
            values = self.tree.item(item, "values")
            if values and values[0] == "✓":
                visible_columns.append(item)

        if not visible_columns:
            dialogs.error(self, "At least one column must be visible.", title="Columns")
            return

        # Apply to both panels
        self.panel.set_visible_columns(visible_columns)
        if self.other_panel:
            self.other_panel.set_visible_columns(visible_columns)

        # Persist to settings
        # The parent passed to this dialog is the CommanderApp instance
        app = self.master
        if hasattr(app, "_settings"):
            app._settings.visible_columns = visible_columns
            if hasattr(app, "_save_settings"):
                app._save_settings()

        self.destroy()


def show_columns_dialog(
    parent: tk.Misc, panel: FilePanel, other_panel: FilePanel | None = None
) -> None:
    """Show the columns configuration dialog."""
    ColumnsDialog(parent, panel, other_panel)
