"""Menu bar builder for the CommanderApp.

Defines a ``MenuCallbacks`` protocol and a ``MenuBar`` class that builds
the entire menu bar (File, View, Operations) from callback references.
"""

from __future__ import annotations

import tkinter as tk
from typing import Protocol, cast

from linux_commander.panel import FilePanel


class MenuCallbacks(Protocol):
    """Protocol for menu bar callbacks implemented by CommanderApp."""

    def cmd_theme(self) -> None: ...
    def cmd_font(self) -> None: ...
    def cmd_editor_font(self) -> None: ...
    def cmd_viewer_font(self) -> None: ...
    def cmd_ftp_connections(self) -> None: ...
    def cmd_command_settings(self) -> None: ...
    def cmd_optional_dependencies(self) -> None: ...
    def cmd_plugin_status(self) -> None: ...
    def cmd_quit(self) -> None: ...
    def _show_command_prompt(self) -> None: ...
    def _show_hotlist(self) -> None: ...
    def _compare_selected_files(self) -> None: ...
    def _compare_directories(self) -> None: ...
    def _refresh_panel_preserving_position(self, panel: FilePanel) -> None: ...
    def _toggle_icons(self) -> None: ...
    def _toggle_extension_column(self) -> None: ...
    def _toggle_flat_view(self) -> None: ...
    def _run_file_operation(self, op: object) -> None: ...

    @property
    def active_panel(self) -> FilePanel: ...


class MenuBar:
    """Builds the application menu bar from a MenuCallbacks implementation."""

    @staticmethod
    def build(parent: tk.Misc, callbacks: MenuCallbacks) -> tk.Menu:
        """Build and return the complete menu bar."""

        menubar = tk.Menu(parent)

        MenuBar._build_file_menu(menubar, callbacks)
        MenuBar._build_view_menu(menubar, callbacks)
        MenuBar._build_operations_menu(menubar, callbacks)

        return menubar

    @staticmethod
    def _build_file_menu(menubar: tk.Menu, cb: MenuCallbacks) -> None:
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu, underline=0)

        file_menu.add_command(label="Theme...", command=cb.cmd_theme, underline=0)
        file_menu.add_separator()
        file_menu.add_command(label="Font...", command=cb.cmd_font, underline=0)
        file_menu.add_command(label="Editor Font...", command=cb.cmd_editor_font, underline=0)
        file_menu.add_command(label="Viewer Font...", command=cb.cmd_viewer_font, underline=0)
        file_menu.add_separator()
        file_menu.add_command(label="Connections...", command=cb.cmd_ftp_connections, underline=0)
        file_menu.add_command(
            label="Command Settings...", command=cb.cmd_command_settings, underline=8
        )
        file_menu.add_command(
            label="Optional Dependencies...",
            command=cb.cmd_optional_dependencies,
            underline=0,
        )
        file_menu.add_command(label="Plugin Status...", command=cb.cmd_plugin_status, underline=0)
        file_menu.add_separator()
        file_menu.add_command(
            label="Command Prompt",
            accelerator="Ctrl+X",
            command=cb._show_command_prompt,
            underline=8,
        )
        file_menu.add_separator()
        file_menu.add_command(label="Quit", accelerator="Ctrl+Q", command=cb.cmd_quit, underline=0)

    @staticmethod
    def _build_view_menu(menubar: tk.Menu, cb: MenuCallbacks) -> None:

        view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="View", menu=view_menu, underline=0)

        view_menu.add_command(
            label="Show Hidden Files",
            command=lambda: cb.active_panel.toggle_hidden(),
            underline=5,
        )
        view_menu.add_command(
            label="Refresh",
            command=lambda: cb._refresh_panel_preserving_position(cb.active_panel),
            underline=0,
        )
        view_menu.add_separator()
        view_menu.add_command(
            label="Sort by Name",
            command=lambda: cb.active_panel.set_sort("name"),
            underline=8,
        )
        view_menu.add_command(
            label="Sort by Date",
            command=lambda: cb.active_panel.set_sort("mtime"),
            underline=8,
        )
        view_menu.add_command(
            label="Sort by Size",
            command=lambda: cb.active_panel.set_sort("size"),
            underline=8,
        )
        view_menu.add_command(
            label="Sort by Extension",
            command=lambda: cb.active_panel.set_sort("extension"),
            underline=8,
        )
        view_menu.add_separator()
        view_menu.add_command(label="Show Icons", command=cb._toggle_icons, underline=5)
        view_menu.add_command(
            label="Show Extension Column", command=cb._toggle_extension_column, underline=5
        )
        view_menu.add_command(label="Flat View", command=cb._toggle_flat_view, underline=0)
        # Columns dialog needs access to both panels — use a lambda that
        # captures the callbacks object to get the other panel.
        view_menu.add_command(
            label="Columns…",
            command=lambda: _show_columns(cb),
            underline=0,
        )
        view_menu.add_separator()
        view_menu.add_command(
            label="Command Prompt",
            accelerator="Ctrl+X",
            command=cb._show_command_prompt,
            underline=8,
        )

    @staticmethod
    def _build_operations_menu(menubar: tk.Menu, cb: MenuCallbacks) -> None:
        from linux_commander.file_ops import available_operations

        ops = available_operations()
        ops_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Operations", menu=ops_menu, underline=0)

        # Hotlist commands
        ops_menu.add_command(
            label="Hotlist (Bookmarks)…",
            accelerator="Ctrl+\\",
            command=cb._show_hotlist,
            underline=0,
        )
        ops_menu.add_command(
            label="Add Current Dir to Hotlist",
            command=lambda: cb.active_panel.add_current_dir_to_hotlist(),
            underline=0,
        )
        ops_menu.add_separator()

        # Compare commands
        ops_menu.add_command(
            label="Compare Files…",
            command=cb._compare_selected_files,
            underline=0,
        )
        ops_menu.add_command(
            label="Compare Directories…",
            command=cb._compare_directories,
            underline=0,
        )
        ops_menu.add_separator()

        # Auto-discovered file operations
        for op in ops:
            ops_menu.add_command(
                label=op.name,
                command=lambda _op=op: cb._run_file_operation(_op),  # type: ignore[misc]
            )


def _show_columns(cb: MenuCallbacks) -> None:
    """Helper to show the columns dialog — needs both panels."""
    from linux_commander.columns_dialog import show_columns_dialog

    # The callbacks object is the CommanderApp (which is a tk.Misc).
    # Use cast to satisfy the type checker.
    app = cast(tk.Misc, cb)
    show_columns_dialog(app, cb.active_panel, getattr(app, "_other_panel", lambda: None)())
