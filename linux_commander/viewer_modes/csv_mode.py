"""CSV table viewer mode.

Displays delimited text (CSV/TSV) as a sortable Treeview table.  Supports
auto-detection of the delimiter or manual selection (comma, semicolon, tab).
"""

from __future__ import annotations

import csv
import io
import tkinter as tk

from linux_commander.viewer import CSV_EXTENSIONS, detect_delimiter
from linux_commander.viewer_modes import ViewerContext, ViewerMode


class CsvMode(ViewerMode):
    """CSV table display mode."""

    name = "CSV"
    exclusive_group = "display"

    # Tk state owned by this mode
    _var: tk.BooleanVar | None = None
    _delim_var: tk.StringVar | None = None
    _delim: str | None = None

    # ------------------------------------------------------------------
    # ViewerMode interface
    # ------------------------------------------------------------------

    def can_activate(self, ctx: ViewerContext) -> bool:
        return True

    def on_activate(self, ctx: ViewerContext) -> None:
        ctx.show_csv_area()
        self._render_table(ctx)
        suffix = " [Table]" if ctx.path and ctx.path.suffix.lower() in CSV_EXTENSIONS else " [CSV]"
        ctx.set_title_suffix(suffix)

    def on_deactivate(self, ctx: ViewerContext) -> None:
        ctx.show_text_area()
        ctx.set_title_suffix("")

    def build_menu(self, ctx: ViewerContext, menu: tk.Menu) -> None:
        self._var = tk.BooleanVar(value=False)
        menu.add_checkbutton(
            label="CSV Table",
            variable=self._var,
            command=lambda: self._toggle(ctx),
            underline=0,
        )

        # CSV Separator submenu
        delim_menu = tk.Menu(menu, tearoff=0)
        menu.add_cascade(label="CSV Separator", menu=delim_menu, underline=4)

        self._delim_var = tk.StringVar(value="")
        for label, value in (
            ("Auto", ""),
            ("Comma", ","),
            ("Semicolon", ";"),
            ("Tab", "\t"),
        ):
            delim_menu.add_radiobutton(
                label=label,
                value=value,
                variable=self._delim_var,
                command=lambda: self._on_delim_pick(ctx),
            )

    def title_suffix(self, ctx: ViewerContext) -> str:
        if ctx.path and ctx.path.suffix.lower() in CSV_EXTENSIONS:
            return " [Table]"
        return " [CSV]"

    # ------------------------------------------------------------------
    # Toggle handler
    # ------------------------------------------------------------------

    def _toggle(self, ctx: ViewerContext) -> None:
        if self._var is None:
            return
        entering = self._var.get()
        if entering:
            ctx.deactivate_other_modes(self.exclusive_group)
            self.on_activate(ctx)
        else:
            self.on_deactivate(ctx)
            ctx.reactivate_group(self.exclusive_group)

    def _on_delim_pick(self, ctx: ViewerContext) -> None:
        if self._delim_var is None:
            return
        self._delim = self._delim_var.get() or None
        if self._var is not None and self._var.get():
            self._render_table(ctx)

    # ------------------------------------------------------------------
    # Table rendering
    # ------------------------------------------------------------------

    def _render_table(self, ctx: ViewerContext) -> None:
        delim = (
            self._delim if self._delim and len(self._delim) == 1 else detect_delimiter(ctx.raw_text)
        )
        rows = list(csv.reader(io.StringIO(ctx.raw_text), delimiter=delim))
        self._populate_table(ctx, rows)

    @staticmethod
    def _populate_table(ctx: ViewerContext, rows: list[list[str]]) -> None:
        tree = ctx.csv_tree
        tree.delete(*tree.get_children())

        if not rows:
            return

        header = rows[0] if rows else []
        width = max(len(row) for row in rows) if rows else 0
        # Pad header if some rows have more columns
        while len(header) < width:
            header.append(f"Col {len(header) + 1}")

        columns = []
        for col_id, _title in enumerate(header, 1):
            columns.append(f"col{col_id}")

        # Define columns before setting headings
        tree["columns"] = columns

        for col_id, title in enumerate(header, 1):
            tree.heading(f"col{col_id}", text=title)
            tree.column(f"col{col_id}", width=80, anchor="w")

        for row in rows[1:]:
            # Pad row if it has fewer columns than the header
            padded = row + [""] * (len(header) - len(row))
            tree.insert("", "end", values=padded[: len(header)])


mode_class = CsvMode
