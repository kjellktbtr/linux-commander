"""Hotlist (bookmarks) dialog for linux-commander."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from typing import TYPE_CHECKING

from linux_commander import dialogs
from linux_commander.hotlist import get_hotlist
from linux_commander.vfs import LocalFileSystem, VfsPath

if TYPE_CHECKING:
    from linux_commander.panel import FilePanel


def _parse_hotlist_path(path_str: str) -> VfsPath:
    """Parse a hotlist path string back into a VfsPath.

    The string is stored as the display string from VfsPath.__str__,
    which includes the filesystem's display_prefix.

    Supported formats:
    - Local: "/home/user/path"
    - FTP: "ftp://user:pass@host/path"
    - SFTP: "sftp://user:pass@host/path"
    - Other scheme-based: "scheme://path"
    """
    # Check for scheme-based paths
    if "://" in path_str:
        scheme = path_str.split("://")[0].lower()
        from linux_commander.plugins import plugin_for_scheme

        plugin = plugin_for_scheme(scheme)
        if plugin is None:
            raise ValueError(f"No plugin for scheme '{scheme}'")
        # The plugin's connect_fs should handle the full URL
        fs = plugin.connect_fs(path_str)
        return VfsPath(fs=fs, parts=("",))
    # Local path - use LocalFileSystem
    from pathlib import Path

    return LocalFileSystem().from_path(Path(path_str))


class HotlistDialog(tk.Toplevel):
    """Dialog for managing and navigating hotlist bookmarks."""

    def __init__(
        self,
        parent: tk.Misc,
        panel: FilePanel,
        other_panel: FilePanel | None = None,
    ) -> None:
        super().__init__(parent)
        self.title("Hotlist (Bookmarks)")
        self.panel = panel
        self.other_panel = other_panel
        self.hotlist = get_hotlist()
        self._build_ui()
        self._populate()
        dialogs._center_over(self, parent)
        self.grab_set()
        self.wait_window()

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        # Treeview for bookmarks
        columns = ("name", "path")
        self.tree = ttk.Treeview(
            self,
            columns=columns,
            show="headings",
            selectmode="browse",
            height=15,
        )
        self.tree.heading("name", text="Name")
        self.tree.heading("path", text="Path")
        self.tree.column("name", width=180, anchor="w")
        self.tree.column("path", width=400, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)

        # Scrollbar
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=0, column=1, sticky="ns", pady=8)

        # Buttons
        btn_frame = ttk.Frame(self)
        btn_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 8))
        btn_frame.columnconfigure(0, weight=1)

        ttk.Button(btn_frame, text="Go To", command=self._on_goto).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Go To (Other Panel)", command=self._on_goto_other).pack(
            side="left", padx=4
        )
        ttk.Button(btn_frame, text="Add Current Dir", command=self._on_add_current).pack(
            side="left", padx=4
        )
        ttk.Button(btn_frame, text="Rename", command=self._on_rename).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Remove", command=self._on_remove).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Close", command=self.destroy).pack(side="right", padx=4)

        # Double-click to go
        self.tree.bind("<Double-1>", lambda e: self._on_goto())
        # Enter key to go
        self.tree.bind("<Return>", lambda e: self._on_goto())
        # Delete key to remove
        self.tree.bind("<Delete>", lambda e: self._on_remove())

    def _populate(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        for entry in self.hotlist.all():
            self.tree.insert("", "end", values=(entry.name, entry.path), iid=entry.path)

    def _get_selected_path(self) -> str | None:
        sel = self.tree.selection()
        if not sel:
            return None
        return sel[0]

    def _on_goto(self) -> None:
        path = self._get_selected_path()
        if path:
            self._navigate_to(self.panel, path)

    def _on_goto_other(self) -> None:
        path = self._get_selected_path()
        if path and self.other_panel:
            self._navigate_to(self.other_panel, path)

    def _navigate_to(self, panel: FilePanel, path_str: str) -> None:
        """Navigate panel to the given path string."""
        try:
            vpath = _parse_hotlist_path(path_str)
            panel.load(vpath)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to navigate: {e}", parent=self)

    def _on_add_current(self) -> None:
        current = self.panel.current_path
        name = current.name or str(current)
        path_str = str(current)
        self.hotlist.add(name, path_str)
        self._populate()

    def _on_rename(self) -> None:
        path = self._get_selected_path()
        if not path:
            return
        # Find current name
        current_name = next((e.name for e in self.hotlist.all() if e.path == path), path)
        new_name = simpledialog.askstring(
            "Rename Bookmark", "New name:", initialvalue=current_name, parent=self
        )
        if new_name and new_name != current_name:
            # Remove old, add new with same path
            self.hotlist.remove(path)
            self.hotlist.add(new_name, path)
            self._populate()

    def _on_remove(self) -> None:
        path = self._get_selected_path()
        if not path:
            return
        if messagebox.askyesno("Remove Bookmark", f"Remove '{path}' from hotlist?", parent=self):
            self.hotlist.remove(path)
            self._populate()


def show_hotlist(parent: tk.Misc, panel: FilePanel, other_panel: FilePanel | None = None) -> None:
    """Show the hotlist dialog."""
    HotlistDialog(parent, panel, other_panel)
