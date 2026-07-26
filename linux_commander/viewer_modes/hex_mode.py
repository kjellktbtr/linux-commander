"""Hex dump viewer mode.

Displays file content as a classic hexdump with offset, hex bytes, and ASCII
columns.  Provides hex-specific tools: go-to-offset, find-in-hex, copy-as-hex,
and copy-as-C-array.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from linux_commander import dialogs
from linux_commander.viewer import _format_hexdump, _read_raw_capped
from linux_commander.viewer_modes import ViewerContext, ViewerMode


class HexMode(ViewerMode):
    """Hex dump display mode."""

    name = "Hex"
    exclusive_group = "display"

    # Tk state owned by this mode (created in build_menu, lives for window lifetime)
    _var: tk.BooleanVar | None = None
    _submenu: tk.Menu | None = None

    # ------------------------------------------------------------------
    # ViewerMode interface
    # ------------------------------------------------------------------

    def can_activate(self, ctx: ViewerContext) -> bool:
        return True

    def on_activate(self, ctx: ViewerContext) -> None:
        if ctx.path is None:
            # No file on disk: encode current buffer
            data = ctx.text_widget.get("1.0", "end-1c").encode("utf-8", errors="replace")
            truncated = False
        else:
            try:
                data, truncated = _read_raw_capped(ctx.path)
            except OSError as exc:
                ctx.show_error("Hexdump", f"Cannot read file for hexdump:\n{exc}")
                if self._var is not None:
                    self._var.set(False)
                return

        hex_text = _format_hexdump(data)
        if truncated:
            hex_text += "\n\n[... truncated - file exceeds the viewer's size limit ...]"

        ctx.clear_text()
        ctx.insert_text(hex_text)
        ctx.clear_syntax_tags()
        ctx.set_editable(False)
        ctx.set_title_suffix(self.title_suffix(ctx))
        self._populate_submenu(ctx)
        self._set_menu_item_state(ctx, "Hex Tools", "normal")

    def on_deactivate(self, ctx: ViewerContext) -> None:
        ctx.clear_text()
        ctx.insert_text(ctx.raw_text)
        ctx.clear_syntax_tags()
        ctx.apply_syntax_highlighting()
        ctx.set_editable(not ctx.read_only)
        ctx.set_modified(False)
        ctx.set_title_suffix("")
        # Clear hex submenu entries
        if self._submenu is not None:
            self._submenu.delete(0, "end")
        self._set_menu_item_state(ctx, "Hex Tools", "disabled")
        # Re-configure selection tag (may have been removed by menu rebuild)
        ctx.text_widget.tag_configure("hex_sel", background="#c65c00", foreground="white")

    def build_menu(self, ctx: ViewerContext, menu: tk.Menu) -> None:
        self._var = tk.BooleanVar(value=False)
        menu.add_checkbutton(
            label="Hexdump",
            variable=self._var,
            command=lambda: self._toggle(ctx),
            underline=0,
        )

        self._submenu = tk.Menu(menu, tearoff=0)
        menu.add_cascade(label="Hex Tools", menu=self._submenu, state="disabled", underline=0)

        # Wire up key bindings for hex tools
        ctx.top.bind("<Control-g>", lambda e: self._goto_offset(ctx))
        ctx.top.bind("<Control-Shift-f>", lambda e: self._find_in_hex(ctx))
        ctx.top.bind("<Control-Shift-c>", lambda e: self._copy_as_hex(ctx))

    def title_suffix(self, ctx: ViewerContext) -> str:
        return " [Hex]"

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

    # ------------------------------------------------------------------
    # Hex submenu population
    # ------------------------------------------------------------------

    def _populate_submenu(self, ctx: ViewerContext) -> None:
        if self._submenu is None:
            return
        self._submenu.delete(0, "end")
        self._submenu.add_command(
            label="Go to Offset...",
            accelerator="Ctrl+G",
            command=lambda: self._goto_offset(ctx),
            underline=0,
        )
        self._submenu.add_command(
            label="Find in Hex...",
            accelerator="Ctrl+Shift+F",
            command=lambda: self._find_in_hex(ctx),
            underline=0,
        )
        self._submenu.add_separator()
        self._submenu.add_command(
            label="Copy as Hex",
            accelerator="Ctrl+Shift+C",
            command=lambda: self._copy_as_hex(ctx),
            underline=0,
        )
        self._submenu.add_command(
            label="Copy as C Array",
            command=lambda: self._copy_as_c_array(ctx),
            underline=0,
        )

    # ------------------------------------------------------------------
    # Menu state helper — search menu tree by label
    # ------------------------------------------------------------------

    @staticmethod
    def _set_menu_item_state(ctx: ViewerContext, label: str, state: str) -> None:
        """Find a menu entry by *label* in the View menu and set its state."""
        try:
            menubar = ctx.top.nametowidget(ctx.top["menu"])
            for i in range(menubar.index("end") + 1):
                try:
                    if menubar.entrycget(i, "label") == "View":
                        view_menu = menubar.nametowidget(menubar.entrycget(i, "menu"))
                        for j in range(view_menu.index("end") + 1):
                            try:
                                if view_menu.entrycget(j, "label") == label:
                                    view_menu.entryconfigure(j, state=state)
                                    return
                            except tk.TclError:
                                continue
                        return
                except tk.TclError:
                    continue
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Go to offset
    # ------------------------------------------------------------------

    @staticmethod
    def _goto_offset(ctx: ViewerContext) -> str:
        dialog = tk.Toplevel(ctx.top)
        dialog.title("Go to Offset")
        dialog.transient(ctx.top)
        dialog.resizable(False, False)

        ttk.Label(dialog, text="Offset (hex):").grid(row=0, column=0, padx=8, pady=8, sticky="w")
        offset_var = tk.StringVar(value="0x0")
        entry = ttk.Entry(dialog, textvariable=offset_var, width=20)
        entry.grid(row=0, column=1, padx=8, pady=8)
        entry.select_range(0, "end")
        entry.focus_set()

        def _do_goto() -> None:
            val = offset_var.get().strip()
            try:
                if val.lower().startswith("0x"):
                    offset = int(val, 16)
                elif all(c in "0123456789abcdefABCDEF" for c in val):
                    offset = int(val, 16)
                else:
                    offset = int(val)
            except ValueError:
                dialogs.error(dialog, "Invalid offset format", title="Go to Offset")
                return
            if offset < 0:
                dialogs.error(dialog, "Offset must be non-negative", title="Go to Offset")
                return

            line_num = offset // 16
            ctx.text_widget.see(f"{line_num + 1}.0")
            HexMode._highlight_offset(ctx, offset)
            dialog.destroy()

        btn_frame = ttk.Frame(dialog)
        btn_frame.grid(row=1, column=0, columnspan=2, pady=8)
        ttk.Button(btn_frame, text="OK", command=_do_goto).pack(side="right", padx=4)
        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side="right", padx=4)

        dialog.bind("<Return>", lambda e: _do_goto())
        dialog.bind("<Escape>", lambda e: dialog.destroy())
        # Center dialog
        _center_over_dialog(dialog, ctx.top)
        dialog.grab_set()

        return "break"

    @staticmethod
    def _highlight_offset(ctx: ViewerContext, offset: int) -> None:
        line_num = offset // 16
        byte_in_line = offset % 16
        if byte_in_line < 8:
            col = 10 + byte_in_line * 3
        else:
            col = 10 + 8 * 3 + 1 + (byte_in_line - 8) * 3

        start_idx = f"{line_num + 1}.{col}"
        end_idx = f"{line_num + 1}.{col + 2}"
        ctx.text_widget.tag_remove("hex_sel", "1.0", "end")
        ctx.text_widget.tag_add("hex_sel", start_idx, end_idx)
        ctx.text_widget.see(start_idx)

    # ------------------------------------------------------------------
    # Find in hex
    # ------------------------------------------------------------------

    @staticmethod
    def _find_in_hex(ctx: ViewerContext) -> str:
        dialog = tk.Toplevel(ctx.top)
        dialog.title("Find in Hex")
        dialog.transient(ctx.top)
        dialog.resizable(False, False)

        ttk.Label(dialog, text="Search for:").grid(row=0, column=0, padx=8, pady=8, sticky="w")
        search_var = tk.StringVar()
        entry = ttk.Entry(dialog, textvariable=search_var, width=30)
        entry.grid(row=0, column=1, padx=8, pady=8)
        entry.focus_set()

        mode_var = tk.StringVar(value="hex")
        ttk.Radiobutton(
            dialog,
            text="Hex bytes (e.g. 48 65 6c 6c 6f)",
            variable=mode_var,
            value="hex",
        ).grid(row=1, column=0, columnspan=2, padx=8, sticky="w")
        ttk.Radiobutton(
            dialog, text="ASCII text (e.g. Hello)", variable=mode_var, value="ascii"
        ).grid(row=2, column=0, columnspan=2, padx=8, sticky="w")

        case_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(dialog, text="Case sensitive", variable=case_var).grid(
            row=3, column=0, columnspan=2, padx=8, sticky="w"
        )

        def _do_find() -> None:
            pattern = search_var.get().strip()
            if not pattern:
                return
            if mode_var.get() == "hex":
                try:
                    bytes_list = [int(b, 16) for b in pattern.split()]
                    search_bytes = bytes(bytes_list)
                except ValueError:
                    dialogs.error(
                        dialog,
                        "Invalid hex bytes (use space-separated hex pairs)",
                        title="Find in Hex",
                    )
                    return
                HexMode._search_bytes(ctx, search_bytes)
            else:
                HexMode._search_ascii(ctx, pattern, case_var.get())
            dialog.destroy()

        btn_frame = ttk.Frame(dialog)
        btn_frame.grid(row=4, column=0, columnspan=2, pady=8)
        ttk.Button(btn_frame, text="Find", command=_do_find).pack(side="right", padx=4)
        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side="right", padx=4)

        dialog.bind("<Return>", lambda e: _do_find())
        dialog.bind("<Escape>", lambda e: dialog.destroy())
        _center_over_dialog(dialog, ctx.top)
        dialog.grab_set()

        return "break"

    @staticmethod
    def _search_bytes(ctx: ViewerContext, search_bytes: bytes) -> None:
        data = HexMode._get_raw_data(ctx)
        try:
            idx = data.index(search_bytes)
        except ValueError:
            ctx.show_error("Find in Hex", "Byte sequence not found")
            return
        HexMode._highlight_offset(ctx, idx)

    @staticmethod
    def _search_ascii(ctx: ViewerContext, pattern: str, case_sensitive: bool) -> None:
        data = HexMode._get_raw_data(ctx)
        try:
            if case_sensitive:
                idx = data.index(pattern.encode("ascii", errors="ignore"))
            else:
                idx = data.lower().index(pattern.lower().encode("ascii", errors="ignore"))
        except ValueError:
            ctx.show_error("Find in Hex", "Text not found")
            return
        HexMode._highlight_offset(ctx, idx)

    @staticmethod
    def _get_raw_data(ctx: ViewerContext) -> bytes:
        if ctx.path is not None:
            data, _ = _read_raw_capped(ctx.path)
            return data
        return ctx.text_widget.get("1.0", "end-1c").encode("utf-8", errors="replace")

    # ------------------------------------------------------------------
    # Copy operations
    # ------------------------------------------------------------------

    @staticmethod
    def _text_index_to_offset(text_index: str) -> int | None:
        try:
            line_str, col_str = text_index.split(".")
            line = int(line_str) - 1
            col = int(col_str)
        except (ValueError, AttributeError):
            return None
        if line < 0:
            return None
        hex_start = 11
        if col < hex_start or col > hex_start + 47:
            return None
        byte_in_line = 0
        if col <= 34:
            byte_in_line = (col - hex_start) // 3
        elif col >= 36:
            byte_in_line = 8 + (col - 36) // 3
        else:
            return None
        if byte_in_line >= 16:
            return None
        return line * 16 + byte_in_line

    @staticmethod
    def _copy_as_hex(ctx: ViewerContext) -> str:
        try:
            sel_start = ctx.text_widget.index("sel.first")
            sel_end = ctx.text_widget.index("sel.last")
        except tk.TclError:
            return "break"

        start_offset = HexMode._text_index_to_offset(sel_start)
        end_offset = HexMode._text_index_to_offset(sel_end)
        if start_offset is None or end_offset is None:
            return "break"
        if end_offset < start_offset:
            start_offset, end_offset = end_offset, start_offset

        data = HexMode._get_raw_data(ctx)
        selected = data[start_offset : end_offset + 1]
        hex_str = " ".join(f"{b:02X}" for b in selected)
        ctx.top.clipboard_clear()
        ctx.top.clipboard_append(hex_str)

        return "break"

    @staticmethod
    def _copy_as_c_array(ctx: ViewerContext) -> str:
        try:
            sel_start = ctx.text_widget.index("sel.first")
            sel_end = ctx.text_widget.index("sel.last")
        except tk.TclError:
            return "break"

        start_offset = HexMode._text_index_to_offset(sel_start)
        end_offset = HexMode._text_index_to_offset(sel_end)
        if start_offset is None or end_offset is None:
            return "break"
        if end_offset < start_offset:
            start_offset, end_offset = end_offset, start_offset

        data = HexMode._get_raw_data(ctx)
        selected = data[start_offset : end_offset + 1]
        if not selected:
            return "break"

        lines: list[str] = []
        for i in range(0, len(selected), 16):
            chunk = selected[i : i + 16]
            line = ", ".join(f"0x{b:02X}" for b in chunk)
            lines.append(f"    {line},")

        c_array = "{\n" + "\n".join(lines) + "\n};"
        ctx.top.clipboard_clear()
        ctx.top.clipboard_append(c_array)

        return "break"


def _center_over_dialog(dialog: tk.Toplevel, parent: tk.Toplevel) -> None:
    """Center *dialog* over *parent*."""
    parent.update_idletasks()
    pw, ph = parent.winfo_width(), parent.winfo_height()
    dialog.update_idletasks()
    dw, dh = dialog.winfo_width(), dialog.winfo_height()
    x = parent.winfo_x() + (pw - dw) // 2
    y = parent.winfo_y() + (ph - dh) // 2
    dialog.geometry(f"+{x}+{y}")


mode_class = HexMode
