"""FilePanel widget: a single directory-listing pane (Treeview-backed)."""

from __future__ import annotations

import fnmatch
import re
import tkinter as tk
import tkinter.font as tkfont
from collections.abc import Callable
from tkinter import ttk

from linux_commander import dialogs, viewer, volumes
from linux_commander.fs import SortKey, format_mtime, format_size, sort_entries, split_extension
from linux_commander.vfs import FileEntry, LocalFileSystem, MountManager, VfsPath

_SORT_LABELS: dict[SortKey, str] = {
    "name": "Name",
    "size": "Size",
    "mtime": "Date",
    "extension": "Ext",
}

PAGE_SIZE = 10
"""Number of rows PgUp/PgDn moves by. A fixed value is simple and good enough
for v1; a future refinement could compute it from the Treeview's rendered
height."""

_STYLE_INITIALIZED = False

# Selection colours for active / inactive panels
_SEL_ACTIVE_BG = "#2c5aa0"
_SEL_INACTIVE_BG = "#707070"
_SEL_FG = "white"


def _active_color() -> str:
    """Return the primary color from the active ttkbootstrap theme, or the
    default blue if ttkbootstrap is not available."""
    try:
        import ttkbootstrap as _tb

        return str(_tb.Style().colors.primary)
    except Exception:
        return _SEL_ACTIVE_BG


def _ensure_style() -> None:
    """Configure all shared ``FilePanel.*`` ttk styles once, using the
    platform's fixed-width font so listings look like a classic OFM.

    Two Treeview variants are registered here:
    - ``Active.FilePanel.Treeview``   — theme-primary selection highlight
    - ``Inactive.FilePanel.Treeview`` — grey selection highlight

    ``_apply_font_settings`` in app.py re-configures the font/rowheight on
    both variants whenever the user changes the font.
    """
    global _STYLE_INITIALIZED
    if _STYLE_INITIALIZED:
        return
    fixed_font = tkfont.nametofont("TkFixedFont")
    row_h = fixed_font.metrics("linespace") + 4
    style = ttk.Style()
    active_bg = _active_color()

    for prefix in ("Active.", "Inactive.", ""):
        style.configure(
            f"{prefix}FilePanel.Treeview",
            font=fixed_font,
            rowheight=row_h,
            # Minimise indent so the icon column doesn't push text too far right
            indent=2,
        )
        style.configure(f"{prefix}FilePanel.Treeview.Heading", font=fixed_font)

    style.map(
        "Active.FilePanel.Treeview",
        background=[("selected", active_bg)],
        foreground=[("selected", _SEL_FG)],
    )
    style.map(
        "Inactive.FilePanel.Treeview",
        background=[("selected", _SEL_INACTIVE_BG)],
        foreground=[("selected", _SEL_FG)],
    )

    style.configure("PanelHeader.TLabel", font=fixed_font)
    style.configure(
        "ActivePanelHeader.TLabel",
        font=fixed_font,
        background=active_bg,
        foreground="white",
    )
    style.configure("Volume.TButton", font=fixed_font)
    _STYLE_INITIALIZED = True


def reset_style() -> None:
    """Re-apply FilePanel styles after a ttkbootstrap theme change."""
    global _STYLE_INITIALIZED
    _STYLE_INITIALIZED = False
    _ensure_style()


class FilePanel(ttk.Frame):
    """A single dual-commander-style file listing pane.

    Wraps a `ttk.Treeview` (Name / Size / Modified columns) with a path header
    label, keyboard cursor navigation, and directory navigation (Enter to
    descend, Backspace / ".." to go up).
    """

    def __init__(
        self,
        master: tk.Misc,
        initial_path: VfsPath,
        show_hidden: bool = True,
        local_fs: LocalFileSystem | None = None,
        mount_manager: MountManager | None = None,
        on_activate_file: Callable[[FileEntry], None] | None = None,
        on_tab: Callable[[], None] | None = None,
        on_activate: Callable[[], None] | None = None,
        on_marks_changed: Callable[[], None] | None = None,
        on_directory_changed: Callable[[], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        image_extensions: list[str] | None = None,
        show_icons: bool = True,
        show_extension: bool = True,
        visible_columns: list[str] | None = None,
        on_network: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master)
        _ensure_style()

        self._local_fs = local_fs or LocalFileSystem()
        self._mount_manager = mount_manager or MountManager()
        self.show_hidden = show_hidden
        self.current_path: VfsPath = initial_path
        self.sort_key: SortKey = "name"
        self.sort_reverse = False
        self._entries: list[FileEntry] = []
        self._on_activate_file = on_activate_file
        self._on_tab = on_tab
        self._on_activate = on_activate
        self._on_marks_changed = on_marks_changed
        self._on_directory_changed = on_directory_changed
        self._on_error = on_error
        self.is_active = False
        self.marked: set[VfsPath] = set()
        self._pattern_history: list[str] = ["*"]
        self._image_extensions: set[str] = set(ext.lower() for ext in (image_extensions or []))
        self.show_icons: bool = show_icons
        self.show_extension: bool = show_extension
        self._visible_columns_list: list[str] = visible_columns or [
            "name",
            "extension",
            "size",
            "modified",
        ]
        # Quick search state
        self._quicksearch_buffer: str = ""
        self._quicksearch_timer: str | None = None
        self._quicksearch_label: ttk.Label | None = None
        # Search mode state
        self._search_mode: bool = False
        self._search_root_path: VfsPath | None = None
        self._search_results: list[FileEntry] = []
        # Right-drag state
        self._drag_selecting: bool = False
        self._drag_start_item: str | None = None
        self._drag_moved: bool = False
        self._drag_marking: bool = True  # True = mark, False = unmark
        # Directory history for back/forward navigation
        self._history: list[VfsPath] = [initial_path]
        self._history_index: int = 0
        # Flat view state
        self.flat_view: bool = False
        self._on_network = on_network

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        self._volume_bar = ttk.Frame(self)
        self._volume_bar.grid(row=0, column=0, sticky="ew", padx=2, pady=(2, 0))
        self._populate_volume_bar()

        self._path_var = tk.StringVar(value=str(initial_path))
        self._header = ttk.Label(
            self, textvariable=self._path_var, anchor="w", style="PanelHeader.TLabel"
        )
        self._header.grid(row=1, column=0, sticky="ew", padx=2, pady=(2, 0))

        _tree_show = "tree headings" if show_icons else "headings"
        self._tree = ttk.Treeview(
            self,
            columns=("name", "extension", "size", "modified"),
            show=_tree_show,  # type: ignore[arg-type]
            selectmode="browse",
            style="Inactive.FilePanel.Treeview",
        )
        if show_icons:
            self._tree.heading("#0", text="")
            self._tree.column("#0", width=26, minwidth=26, stretch=False)
        self._tree.heading("name", text="Name")
        self._tree.heading("extension", text="Ext")
        self._tree.heading("size", text="Size")
        self._tree.heading("modified", text="Modified")
        self._tree.column("name", anchor="w", stretch=True, width=220)
        self._tree.column("extension", anchor="w", stretch=False, width=90, minwidth=50)
        self._tree.column("size", anchor="e", stretch=False, width=90)
        self._tree.column("modified", anchor="e", stretch=False, width=140)
        self._tree.configure(displaycolumns=self._visible_columns())
        self._tree.grid(row=2, column=0, sticky="nsew")

        # Column header click to sort
        self._tree.heading("name", command=lambda: self._on_header_click("name"))
        self._tree.heading("extension", command=lambda: self._on_header_click("extension"))
        self._tree.heading("size", command=lambda: self._on_header_click("size"))
        self._tree.heading("modified", command=lambda: self._on_header_click("modified"))

        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=2, column=1, sticky="ns")

        # Marked (tagged) rows are shown bold in an amber color, classic-OFM style.
        # The Font object is kept as an attribute so Tk doesn't garbage-collect it.
        self._marked_font = tkfont.Font(font=tkfont.nametofont("TkFixedFont"))
        self._marked_font.configure(weight="bold")
        self._tree.tag_configure("marked", foreground="#d4a017", font=self._marked_font)

        self._bind_keys()
        self.load(initial_path)

    # -- key bindings ----------------------------------------------------

    def _bind_keys(self) -> None:
        bindings: dict[str, Callable[[tk.Event], object]] = {
            "<Up>": lambda e: self._handle(self.move_cursor, -1),
            "<Down>": lambda e: self._handle(self.move_cursor, 1),
            "<Prior>": lambda e: self._handle(self.move_cursor, -PAGE_SIZE),
            "<Next>": lambda e: self._handle(self.move_cursor, PAGE_SIZE),
            "<Home>": lambda e: self._handle(self.move_to_first),
            "<End>": lambda e: self._handle(self.move_to_last),
            "<Return>": lambda e: self._handle(self._activate_cursor),
            "<KP_Enter>": lambda e: self._handle(self._activate_cursor),
            "<Double-Button-1>": lambda e: self._handle(self._activate_cursor),
            "<BackSpace>": lambda e: self._handle(self.go_up),
            "<Insert>": lambda e: self._handle(self._on_insert_key),
            "<KeyPress-plus>": lambda e: self._handle(self._prompt_and_apply_pattern, True),
            "<KeyPress-minus>": lambda e: self._handle(self._prompt_and_apply_pattern, False),
            "<KeyPress-asterisk>": lambda e: self._handle(self.mark_all),
            # Navigation: Right = enter/execute, Left = go up
            "<Right>": lambda e: self._handle(self._activate_cursor),
            "<Left>": lambda e: self._handle(self.go_up),
            # Quick search: Alt+Shift+Key
            "<Alt-Shift-Key>": self._on_quicksearch_key,
            "<Alt-Shift-BackSpace>": self._on_quicksearch_backspace,
            "<Alt-Shift-Escape>": self._on_quicksearch_cancel,
            # Escape exits search mode
            "<Escape>": lambda e: self._handle(self._on_escape),
        }
        for sequence, callback in bindings.items():
            self._tree.bind(sequence, callback)

        if self._on_tab is not None:
            self._tree.bind("<Tab>", lambda e: self._handle(self._on_tab))  # type: ignore[arg-type]

        # Column header / row clicks (left button)
        self._tree.bind("<Button-1>", self._on_tree_click)

        # Right-button: press arms drag; release without movement = single toggle;
        # drag marks a contiguous range.
        self._tree.bind("<Button-3>", self._on_right_press)
        self._tree.bind("<B3-Motion>", self._on_drag_motion)
        self._tree.bind("<ButtonRelease-3>", self._on_drag_release)

    def _on_escape(self) -> None:
        """Handle Escape key: exit search mode if active."""
        if self._search_mode:
            self.exit_search_mode()

    def _on_tree_click(self, event: tk.Event) -> None:
        """Handle left-click: heading clicks are handled by the heading command=
        callbacks set in __init__ (which fire on button-release), so we only
        need to activate the panel when a row is clicked."""
        region = self._tree.identify("region", event.x, event.y)
        if region == "heading":
            # Sorting is handled by the heading command= callbacks; doing it
            # here too would cause double-fire (press + release = two toggles).
            return
        # Click on a row - activate this panel
        if self._on_activate is not None:
            self._on_activate()

    def _on_right_press(self, event: tk.Event) -> None:
        """Right-button press: move cursor to item and arm drag tracking."""
        item = self._tree.identify_row(event.y)
        if not item:
            return
        self._tree.selection_set(item)
        self._tree.focus(item)
        if self._on_activate is not None:
            self._on_activate()
        # Arm drag
        self._drag_start_item = item
        self._drag_selecting = True
        self._drag_moved = False
        # Decide whether this drag will mark or unmark (based on start item)
        try:
            idx = int(item)
            if 0 <= idx < len(self._entries):
                entry = self._entries[idx]
                self._drag_marking = entry.path not in self.marked
        except (ValueError, IndexError):
            self._drag_marking = True

    def _on_drag_motion(self, event: tk.Event) -> None:
        """Right-button drag: mark a contiguous range of entries."""
        if not self._drag_selecting:
            return
        self._drag_moved = True
        item = self._tree.identify_row(event.y)
        if not item or not self._drag_start_item:
            return
        # Move the cursor to track the mouse pointer visually
        self._tree.selection_set(item)
        self._tree.focus(item)
        self._tree.see(item)
        self._mark_range(self._drag_start_item, item)

    def _on_drag_release(self, event: tk.Event) -> None:
        """Right-button release: single toggle if no drag occurred."""
        if self._drag_selecting and not self._drag_moved:
            # Pure right-click: toggle the mark on the cursor entry
            self.toggle_mark()
        self._drag_selecting = False
        self._drag_start_item = None
        self._drag_moved = False

    def _mark_range(self, start_iid: str, end_iid: str) -> None:
        """Mark or unmark all entries from *start_iid* to *end_iid* inclusive.

        The direction (mark vs. unmark) is fixed by the state of the first
        item when the drag began (``self._drag_marking``).
        """
        try:
            start_idx = int(start_iid)
            end_idx = int(end_iid)
        except (ValueError, TypeError):
            return
        if start_idx > end_idx:
            start_idx, end_idx = end_idx, start_idx
        action = self._drag_marking
        for idx in range(start_idx, end_idx + 1):
            if 0 <= idx < len(self._entries):
                entry = self._entries[idx]
                if not entry.is_parent:
                    if action:
                        self.marked.add(entry.path)
                    else:
                        self.marked.discard(entry.path)
        self._refresh_row_tags()
        self._notify_marks_changed()

    def _handle(self, func: Callable[..., object], *args: object) -> str:
        func(*args)
        return "break"

    # -- volume bar -----------------------------------------------------------

    def _populate_volume_bar(self) -> None:
        """Build the row of volume buttons above the path header. Built once
        at construction time (a static snapshot of `volumes.list_volumes()`);
        it doesn't watch for media being plugged in/out during a session."""
        for volume in volumes.list_volumes():
            button = ttk.Button(
                self._volume_bar,
                text=volume.label,
                style="Volume.TButton",
                command=lambda v=volume: self.load(self._local_fs.from_path(v.path)),  # type: ignore[misc]
            )
            button.pack(side="left", padx=2, pady=2)

        # Network button
        if self._on_network is not None:
            net_btn = ttk.Button(
                self._volume_bar,
                text="Network",
                style="Volume.TButton",
                command=self._on_network,
            )
            net_btn.pack(side="left", padx=2, pady=2)

    # -- active/inactive appearance -----------------------------------------

    def set_active(self, active: bool) -> None:
        """Mark this panel as the active (focused) one, updating its visual style.

        Swaps the Treeview style between ``Active.FilePanel.Treeview`` (bright
        blue selection) and ``Inactive.FilePanel.Treeview`` (grey selection) so
        the inactive panel always shows a clearly dimmed cursor.
        """
        self.is_active = active
        header_style = "ActivePanelHeader.TLabel" if active else "PanelHeader.TLabel"
        self._header.configure(style=header_style)
        tree_style = ("Active" if active else "Inactive") + ".FilePanel.Treeview"
        self._tree.configure(style=tree_style)

        if active:
            self._tree.focus_set()
        else:
            # Clear focus indicator (dotted rectangle) but keep selection visible
            self._tree.focus("")

    # -- font update (called from app._apply_font_settings) ------------------

    def update_font(self, font: tkfont.Font) -> None:
        """Update the bold font used for marked-entry tags to match *font*."""
        self._marked_font.configure(
            family=font.actual("family"),
            size=font.actual("size"),
            weight="bold",
        )
        self._tree.tag_configure("marked", foreground="#d4a017", font=self._marked_font)

    # -- visible columns -----------------------------------------------------

    def _visible_columns(self) -> tuple[str, ...]:
        """The Treeview ``displaycolumns`` for the current column visibility."""
        return tuple(self._visible_columns_list)

    def toggle_extension_column(self) -> None:
        """Toggle the Extension column's visibility."""
        if "extension" in self._visible_columns_list:
            self._visible_columns_list = [c for c in self._visible_columns_list if c != "extension"]
        else:
            # Insert extension after name if present, otherwise at the end
            if "name" in self._visible_columns_list:
                idx = self._visible_columns_list.index("name") + 1
                self._visible_columns_list.insert(idx, "extension")
            else:
                self._visible_columns_list.append("extension")
        self._tree.configure(displaycolumns=self._visible_columns())
        # Reload to update name column (extension stripping)
        prev = self.current_index()
        self.load(self.current_path)
        if prev is not None:
            self.select_index(prev)

    def set_visible_columns(self, columns: list[str]) -> None:
        """Set the visible columns and persist the setting."""
        self._visible_columns_list = columns
        self._tree.configure(displaycolumns=self._visible_columns())
        # Reload to update name column (extension stripping)
        prev = self.current_index()
        self.load(self.current_path)
        if prev is not None:
            self.select_index(prev)

    # -- icons toggle --------------------------------------------------------

    def toggle_icons(self) -> None:
        """Toggle file-type icon display and reload the current directory."""
        self.show_icons = not self.show_icons
        if self.show_icons:
            self._tree.configure(show="tree headings")
            self._tree.heading("#0", text="")
            self._tree.column("#0", width=26, minwidth=26, stretch=False)
        else:
            self._tree.configure(show="headings")
        prev = self.current_index()
        self.load(self.current_path)
        if prev is not None:
            self.select_index(prev)

    def _split_name_ext(self, name: str) -> tuple[str, str]:
        """Split filename into (name_without_extension, extension).

        Returns (name, "") if no extension found.
        """
        ext = split_extension(name)
        if not ext:
            return name, ""
        # Remove the extension from the end
        if name.lower().endswith("." + ext.lower()):
            return name[: -(len(ext) + 1)], ext
        return name, ext

    # -- loading -----------------------------------------------------------

    def load(
        self,
        path: VfsPath,
        select_name: str | None = None,
        select_parent: bool = False,
        add_to_history: bool = True,
    ) -> None:
        """(Re)load `path` into the panel.

        If `select_name` is given, the cursor is placed on the entry with that
        name if present (used when navigating up, to re-select the directory
        we came from). If `select_parent` is True the cursor rests on the ``..``
        entry so a second Enter immediately returns to the parent (classic OFM
        behaviour when entering a directory). Otherwise the cursor defaults to
        the first non-parent entry, falling back to the ".." entry.

        Marks are always cleared on load — both when navigating to a new
        directory and when refreshing the current one (e.g. after a file
        operation), matching classic OFM behavior.

        Errors are reported via the `on_error` callback (rather than raised)
        so a bad path never crashes the app; the panel simply keeps showing
        its previous listing in that case. In practice `fs.list_directory`
        already swallows `PermissionError`/`OSError` internally, so this is a
        defensive belt-and-suspenders boundary rather than a commonly-hit path.

        If `add_to_history` is True (default), the path is added to the
        navigation history (used by go_back/go_forward). Internal navigation
        (back/forward) should pass False to avoid duplicate entries.
        """
        old_fs = self.current_path.fs
        try:
            if self.flat_view:
                raw = path.fs.list_dir_flat(path)
            else:
                raw = path.fs.list_dir(path)
        except OSError as exc:
            self._report_error(f"Could not open '{path}':\n{exc}")
            return
        if not self.show_hidden:
            raw = [e for e in raw if e.is_parent or not e.name.startswith(".")]
        entries = sort_entries(raw, key=self.sort_key, reverse=self.sort_reverse)

        self._tree.delete(*self._tree.get_children())
        self._entries = entries
        self.current_path = path
        self._update_header()
        if old_fs is not path.fs:
            self._mount_manager.release_if_leaving(old_fs, path.fs)
        if self.marked:
            self.marked = set()
            self._notify_marks_changed()

        if add_to_history:
            self._add_to_history(path)

        # Resolve icons lazily (None when PIL unavailable or icons disabled)
        if self.show_icons:
            try:
                from linux_commander import icons as _icons

                _icon_fn = _icons.icon_for_entry
            except Exception:
                _icon_fn = None
        else:
            _icon_fn = None

        for index, entry in enumerate(entries):
            # Leading space when icons are shown: creates a gap between the
            # 16-px icon and the first character of the name column text.
            _pfx = " " if _icon_fn is not None else ""
            # Strip extension from name column when extension column is visible
            show_ext_col = "extension" in self._visible_columns_list
            if entry.is_dir:
                display_name = f"{_pfx}[{entry.name}]"
                ext_text = ""
            elif show_ext_col:
                # Split name and extension
                name_part, ext_part = self._split_name_ext(entry.name)
                display_name = f"{_pfx}{name_part}"
                ext_text = ext_part
            else:
                display_name = f"{_pfx}{entry.name}"
                ext_text = split_extension(entry.name)
            size_text = "<DIR>" if entry.is_dir else format_size(entry.size)
            mtime_text = format_mtime(entry.mtime)
            icon = _icon_fn(entry) if _icon_fn is not None else None
            kw: dict = dict(
                parent="",
                index="end",
                iid=str(index),
                values=(display_name, ext_text, size_text, mtime_text),
            )
            if icon is not None:
                kw["image"] = icon
            self._tree.insert(**kw)

        target_index = self._default_cursor_index(select_name, select_parent)
        if target_index is not None:
            self._select_index(target_index)

        if self._on_directory_changed is not None:
            self._on_directory_changed()

    def _update_header(self) -> None:
        arrow = "v" if self.sort_reverse else "^"
        self._path_var.set(f"{self.current_path}   [{_SORT_LABELS[self.sort_key]} {arrow}]")

    def _report_error(self, message: str) -> None:
        if self._on_error is not None:
            self._on_error(message)

    def set_sort(self, key: SortKey) -> None:
        """Sort by `key`; pressing the same key again flips ascending/descending
        (classic Midnight Commander Ctrl+F3/F5/F6 behavior)."""
        if self.sort_key == key:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_key = key
            self.sort_reverse = False
        if self._search_mode:
            self._rerender_search_results()
        else:
            self.load(self.current_path)

    def _on_header_click(self, column: str) -> None:
        """Handle column header click for sorting.

        Maps column names to sort keys and toggles reverse if same column clicked.
        """
        sort_key_map = {
            "name": "name",
            "extension": "extension",
            "size": "size",
            "modified": "mtime",
        }
        sort_key = sort_key_map.get(column)
        if sort_key:
            self.set_sort(sort_key)  # type: ignore[arg-type]

    def toggle_hidden(self) -> None:
        """Toggle whether dotfiles are shown, then reload."""
        self.show_hidden = not self.show_hidden
        self.load(self.current_path)

    def _default_cursor_index(
        self, select_name: str | None, select_parent: bool = False
    ) -> int | None:
        if select_name is not None:
            for index, entry in enumerate(self._entries):
                if entry.name == select_name and not entry.is_parent:
                    return index
        if select_parent:
            # Place cursor on ".." so a second Enter returns to the parent
            for index, entry in enumerate(self._entries):
                if entry.is_parent:
                    return index
        for index, entry in enumerate(self._entries):
            if not entry.is_parent:
                return index
        for index, entry in enumerate(self._entries):
            if entry.is_parent:
                return index
        return None

    # -- cursor navigation ---------------------------------------------------

    def cursor_entry(self) -> FileEntry | None:
        """The `FileEntry` currently under the cursor, if any."""
        focus_iid = self._tree.focus()
        if not focus_iid:
            return None
        return self._entries[int(focus_iid)]

    def current_index(self) -> int | None:
        """The row index currently under the cursor, if any. Public wrapper
        around `_current_index`, used by callers that need to preserve cursor
        position across a `load()` refresh (e.g. after a file operation)."""
        return self._current_index()

    def select_index(self, index: int) -> None:
        """Move the cursor to `index`, clamped to the listing bounds. Public
        wrapper around `_select_index`."""
        self._select_index(index)

    def _current_index(self) -> int | None:
        focus_iid = self._tree.focus()
        if not focus_iid:
            return None
        return int(focus_iid)

    def _select_index(self, index: int) -> None:
        children = self._tree.get_children()
        if not children:
            return
        index = max(0, min(len(children) - 1, index))
        iid = children[index]
        self._tree.selection_set(iid)
        self._tree.focus(iid)
        self._tree.see(iid)

    def move_cursor(self, delta: int) -> None:
        """Move the cursor by `delta` rows, clamped to the listing bounds."""
        current = self._current_index() or 0
        self._select_index(current + delta)

    def move_to_first(self) -> None:
        self._select_index(0)

    def move_to_last(self) -> None:
        self._select_index(len(self._tree.get_children()) - 1)

    # -- directory navigation -----------------------------------------------

    def go_up(self) -> None:
        """Navigate to the parent directory, re-selecting the directory we came from."""
        if self._search_mode:
            self.exit_search_mode()
            return
        new_path, select_name = self._mount_manager.resolve_up(self.current_path)
        if new_path == self.current_path:
            return
        self.load(new_path, select_name=select_name or None)

    def _activate_cursor(self) -> None:
        entry = self.cursor_entry()
        if entry is None:
            return
        if entry.is_parent:
            if self._search_mode:
                self.exit_search_mode()
            else:
                self.go_up()
        elif entry.is_dir:
            self.load(entry.path, select_parent=True)
        else:
            # Check if it's an image file
            if self._image_extensions and entry.name.lower().endswith(
                tuple(self._image_extensions)
            ):
                # Collect all image files in current directory for navigation
                image_files = [
                    e.path
                    for e in self._entries
                    if not e.is_parent
                    and not e.is_dir
                    and e.name.lower().endswith(tuple(self._image_extensions))
                ]
                if image_files:
                    start_index = next((i for i, p in enumerate(image_files) if p == entry.path), 0)
                    viewer.view_image(
                        self.master,
                        entry.path,
                        image_files,
                        start_index,
                        list(self._image_extensions),
                    )
                    return
            try:
                target = self._mount_manager.enter(entry)
            except OSError as exc:
                self._report_error(f"Cannot open '{entry.name}':\n{exc}")
                return
            if target is not None:
                self.load(target, select_parent=True)
            elif self._on_activate_file is not None:
                self._on_activate_file(entry)

    # -- quick search (Alt+Shift+typing) ----------------------------------------

    def _on_quicksearch_key(self, event: tk.Event) -> str:
        """Handle Alt+Shift+character for quick search."""
        char = event.char
        if not char or len(char) != 1:
            return "break"
        # Ignore modifier keys that produce empty char
        if char in ("\x00", "\x1b"):  # Ctrl+Space, Escape
            return "break"

        self._quicksearch_buffer += char.lower()
        self._show_quicksearch_overlay()
        self._schedule_quicksearch_clear()
        self._quicksearch_find()
        return "break"

    def _on_quicksearch_backspace(self, event: tk.Event) -> str:
        """Handle Alt+Shift+Backspace to remove last character from quick search buffer."""
        if self._quicksearch_buffer:
            self._quicksearch_buffer = self._quicksearch_buffer[:-1]
            if self._quicksearch_buffer:
                self._show_quicksearch_overlay()
                self._schedule_quicksearch_clear()
                self._quicksearch_find()
            else:
                self._hide_quicksearch_overlay()
        return "break"

    def _on_quicksearch_cancel(self, event: tk.Event) -> str:
        """Handle Alt+Shift+Escape to cancel quick search."""
        self._quicksearch_buffer = ""
        self._cancel_quicksearch_timer()
        self._hide_quicksearch_overlay()
        return "break"

    def _show_quicksearch_overlay(self) -> None:
        """Show the quick search buffer in a small overlay label."""
        if self._quicksearch_label is None:
            self._quicksearch_label = ttk.Label(
                self._tree, text="", background="yellow", foreground="black", padding=(4, 2)
            )
        self._quicksearch_label.config(text=f"Find: {self._quicksearch_buffer}")
        self._quicksearch_label.place(relx=1.0, rely=0.0, anchor="ne", x=-4, y=4)

    def _hide_quicksearch_overlay(self) -> None:
        """Hide the quick search overlay label."""
        if self._quicksearch_label is not None:
            self._quicksearch_label.place_forget()

    def _schedule_quicksearch_clear(self) -> None:
        """Schedule clearing the quick search buffer after 1 second of inactivity."""
        self._cancel_quicksearch_timer()
        self._quicksearch_timer = self._tree.after(1000, self._clear_quicksearch_buffer)

    def _cancel_quicksearch_timer(self) -> None:
        """Cancel the quick search clear timer."""
        if self._quicksearch_timer is not None:
            self._tree.after_cancel(self._quicksearch_timer)
            self._quicksearch_timer = None

    def _clear_quicksearch_buffer(self) -> None:
        """Clear the quick search buffer and hide overlay."""
        self._quicksearch_buffer = ""
        self._quicksearch_timer = None
        self._hide_quicksearch_overlay()

    def _quicksearch_find(self) -> None:
        """Find and select the first entry matching the quick search buffer."""
        if not self._quicksearch_buffer:
            return
        buffer_lower = self._quicksearch_buffer.lower()
        for index, entry in enumerate(self._entries):
            if entry.is_parent:
                continue
            if entry.name.lower().startswith(buffer_lower):
                self._select_index(index)
                break

    # -- search mode -----------------------------------------------------

    def enter_search_mode(self, criteria_summary: str) -> None:
        """Switch panel to search results mode.

        Clears the current listing, sets header to show search criteria,
        and disables directory navigation (go_up, Enter on dirs).
        """
        self._search_mode = True
        self._search_root_path = self.current_path
        self._search_results = []

        # Clear tree
        self._tree.delete(*self._tree.get_children())
        self._entries = []

        # Update header to show search mode
        self._path_var.set(f"Search: {criteria_summary}  (Esc to exit)")

        # Disable volume bar and hide it
        for child in self._volume_bar.winfo_children():
            child.config(state="disabled")  # type: ignore[call-arg, call-overload]

    def exit_search_mode(self) -> None:
        """Exit search mode and restore previous directory."""
        if not self._search_mode:
            return
        self._search_mode = False
        # Re-enable volume bar
        for child in self._volume_bar.winfo_children():
            child.config(state="normal")  # type: ignore[call-arg, call-overload]
        # Restore previous directory
        if self._search_root_path is not None:
            self.load(self._search_root_path)
        self._search_root_path = None
        self._search_results = []

    def _search_row_values(self, entry: FileEntry) -> tuple[str, str, str, str]:
        """Build the (name, extension, size, modified) column values for a
        search result row."""
        show_ext_col = "extension" in self._visible_columns_list
        if entry.is_dir:
            display_name = f" {entry.name}"
            ext_text = ""
        elif show_ext_col:
            name_part, ext_part = self._split_name_ext(entry.name)
            display_name = f" {name_part}"
            ext_text = ext_part
        else:
            display_name = f" {entry.name}"
            ext_text = split_extension(entry.name)
        size_text = "<DIR>" if entry.is_dir else format_size(entry.size)
        mtime_text = format_mtime(entry.mtime)
        return (display_name, ext_text, size_text, mtime_text)

    def add_search_results(self, entries: list[FileEntry]) -> None:
        """Add a batch of search results to the panel (called from the Tk
        thread, e.g. by SearchController's poll loop)."""
        if not self._search_mode or not entries:
            return
        for entry in entries:
            self._search_results.append(entry)
            self._entries.append(entry)
            self._tree.insert(
                "",
                "end",
                iid=str(len(self._entries) - 1),
                values=self._search_row_values(entry),
            )

    def get_search_result_count(self) -> int:
        """Return the number of search results currently displayed."""
        return len(self._search_results)

    def _rerender_search_results(self) -> None:
        """Re-sort the accumulated search results and rebuild the tree.

        Unlike `load()`, this sorts the in-memory `_search_results`
        collection (which may span many directories) instead of re-listing
        a single directory from disk.
        """
        self._entries = sort_entries(
            self._search_results, key=self.sort_key, reverse=self.sort_reverse
        )
        self._tree.delete(*self._tree.get_children())
        for index, entry in enumerate(self._entries):
            self._tree.insert(
                "",
                "end",
                iid=str(index),
                values=self._search_row_values(entry),
            )

    # -- selection / tagging -------------------------------------------------

    def marked_entries(self) -> list[FileEntry]:
        """The `FileEntry` objects currently marked, in display order. Never
        includes the synthetic ".." entry."""
        return [e for e in self._entries if not e.is_parent and e.path in self.marked]

    def selected_entries(self) -> list[FileEntry]:
        """The entries an operation should act on: the marked set, or just the
        cursor entry if nothing is marked. Never includes ".."."""
        marked = self.marked_entries()
        if marked:
            return marked
        cursor = self.cursor_entry()
        if cursor is not None and not cursor.is_parent:
            return [cursor]
        return []

    def toggle_mark(self) -> None:
        """Toggle the mark on the entry under the cursor (no-op on ".." or empty)."""
        entry = self.cursor_entry()
        if entry is None or entry.is_parent:
            return
        if entry.path in self.marked:
            self.marked.discard(entry.path)
        else:
            self.marked.add(entry.path)
        self._refresh_row_tags()
        self._notify_marks_changed()

    def _on_insert_key(self) -> None:
        """Classic OFM Insert behavior: toggle the mark, then move down one row."""
        self.toggle_mark()
        self.move_cursor(1)

    def apply_pattern(
        self,
        pattern: str,
        mark: bool,
        case_sensitive: bool = False,
        is_regex: bool = False,
    ) -> None:
        """Mark (or unmark, if `mark` is False) every non-parent entry whose
        name matches `pattern`.

        If ``is_regex`` is True, treat ``pattern`` as a regular expression
        (compiled with ``re``). Otherwise use ``fnmatch`` glob matching.
        If ``case_sensitive`` is False, patterns are matched case-insensitively
        (via ``re.IGNORECASE`` or ``fnmatch`` on the lowered name)."""
        flags = 0 if case_sensitive else re.IGNORECASE

        def matches(name: str) -> bool:
            if is_regex:
                try:
                    return bool(re.match(pattern, name, flags))
                except re.error:
                    return False
            if not case_sensitive:
                return fnmatch.fnmatch(name.lower(), pattern.lower())
            return fnmatch.fnmatch(name, pattern)

        for entry in self._entries:
            if entry.is_parent:
                continue
            if matches(entry.name):
                if mark:
                    self.marked.add(entry.path)
                else:
                    self.marked.discard(entry.path)
        self._refresh_row_tags()
        self._notify_marks_changed()

    def mark_all(self) -> None:
        """Mark every non-parent entry in the panel (used by the * key)."""
        for entry in self._entries:
            if not entry.is_parent:
                self.marked.add(entry.path)
        self._refresh_row_tags()
        self._notify_marks_changed()

    def invert_selection(self) -> None:
        """Flip the marked state of every non-parent entry."""
        for entry in self._entries:
            if entry.is_parent:
                continue
            if entry.path in self.marked:
                self.marked.discard(entry.path)
            else:
                self.marked.add(entry.path)
        self._refresh_row_tags()
        self._notify_marks_changed()

    def _prompt_and_apply_pattern(self, mark: bool) -> None:
        title = "Select by pattern" if mark else "Deselect by pattern"
        prompt = "Mark files matching:" if mark else "Unmark files matching:"
        result = dialogs.pattern_dialog(self, title, prompt, history=self._pattern_history)
        if result and result["pattern"]:
            self.apply_pattern(
                pattern=str(result["pattern"]),
                mark=mark,
                case_sensitive=bool(result.get("case_sensitive", False)),
                is_regex=bool(result.get("regex", False)),
            )

    def _refresh_row_tags(self) -> None:
        for index, entry in enumerate(self._entries):
            iid = str(index)
            if not self._tree.exists(iid):
                continue
            self._tree.item(iid, tags=("marked",) if entry.path in self.marked else ())

    def _notify_marks_changed(self) -> None:
        if self._on_marks_changed is not None:
            self._on_marks_changed()

    # -- directory history (back/forward) -----------------------------------

    def _add_to_history(self, path: VfsPath) -> None:
        """Add a path to the navigation history."""
        if not hasattr(self, "_history"):
            self._history = [self.current_path]
            self._history_index = 0
        # If we're not at the end, truncate forward history
        if self._history_index < len(self._history) - 1:
            self._history = self._history[: self._history_index + 1]
        # Don't add duplicate consecutive entries
        if self._history and self._history[-1] == path:
            return
        self._history.append(path)
        self._history_index = len(self._history) - 1
        # Limit history size
        if len(self._history) > 50:
            self._history = self._history[-50:]
            self._history_index = len(self._history) - 1

    def go_back(self) -> bool:
        """Navigate back in history. Returns True if navigation occurred."""
        if not hasattr(self, "_history") or self._history_index <= 0:
            return False
        self._history_index -= 1
        self.load(self._history[self._history_index], add_to_history=False)
        return True

    def go_forward(self) -> bool:
        """Navigate forward in history. Returns True if navigation occurred."""
        if not hasattr(self, "_history") or self._history_index >= len(self._history) - 1:
            return False
        self._history_index += 1
        self.load(self._history[self._history_index], add_to_history=False)
        return True

    def can_go_back(self) -> bool:
        return hasattr(self, "_history") and self._history_index > 0

    def can_go_forward(self) -> bool:
        return hasattr(self, "_history") and self._history_index < len(self._history) - 1

    # -- flat view -----------------------------------------------------------

    def toggle_flat_view(self) -> None:
        """Toggle flat view (recursive directory listing) on/off."""
        self.flat_view = not self.flat_view
        prev = self.current_index()
        self.load(self.current_path)
        if prev is not None:
            self.select_index(prev)

    # -- hotlist integration -------------------------------------------------

    def add_current_dir_to_hotlist(self) -> None:
        """Add the current directory to the hotlist/bookmarks."""
        from linux_commander.hotlist import get_hotlist

        current = self.current_path
        name = current.name or str(current)
        path_str = str(current)
        get_hotlist().add(name, path_str)
        dialogs.show_text(self, "Hotlist", f"Added to hotlist:\n{name}\n{path_str}")
