"""Search mode controller extracted from FilePanel (SRP).

Manages search mode state: entering/exiting search mode, accumulating results,
and re-rendering sorted results. Composed with FilePanel rather than inheriting
from it, keeping the panel focused on directory listing.
"""

from __future__ import annotations

from linux_commander.fs import format_mtime, format_size, sort_entries, split_extension
from linux_commander.vfs import FileEntry, VfsPath


class SearchModeController:
    """Manages search mode state for a FilePanel.

    Composed with FilePanel to keep search concerns separate from
    directory listing concerns.
    """

    def __init__(self, panel: object) -> None:
        """Initialize with a reference to the FilePanel.

        The panel reference is stored as ``object`` to avoid circular imports.
        The controller uses duck typing to access panel attributes.
        """
        self._panel = panel
        self._active: bool = False
        self._root_path: VfsPath | None = None
        self._results: list[FileEntry] = []

    @property
    def active(self) -> bool:
        """Whether search mode is currently active."""
        return self._active

    @property
    def root_path(self) -> VfsPath | None:
        """The directory path that was active when search mode started."""
        return self._root_path

    @property
    def results(self) -> list[FileEntry]:
        """The accumulated search results."""
        return self._results

    def count(self) -> int:
        """Return the number of search results currently accumulated."""
        return len(self._results)

    def enter(self, criteria_summary: str) -> None:
        """Switch panel to search results mode.

        Clears the current listing, sets header to show search criteria,
        and disables directory navigation.
        """
        self._active = True
        self._root_path = self._panel.current_path  # type: ignore[attr-defined]
        self._results = []

        # Clear tree
        self._panel._tree.delete(*self._panel._tree.get_children())  # type: ignore[attr-defined]
        self._panel._entries = []  # type: ignore[attr-defined]

        # Update header to show search mode
        self._panel._path_var.set(f"Search: {criteria_summary}  (Esc to exit)")  # type: ignore[attr-defined]

        # Disable volume bar
        for child in self._panel._volume_bar.winfo_children():  # type: ignore[attr-defined]
            child.config(state="disabled")  # type: ignore[union-attr]

    def exit(self) -> None:
        """Exit search mode and restore previous directory."""
        if not self._active:
            return
        self._active = False
        # Re-enable volume bar
        for child in self._panel._volume_bar.winfo_children():  # type: ignore[attr-defined]
            child.config(state="normal")  # type: ignore[union-attr]
        # Restore previous directory
        if self._root_path is not None:
            self._panel.load(self._root_path)  # type: ignore[attr-defined]
        self._root_path = None
        self._results = []

    def row_values(self, entry: FileEntry) -> tuple[str, str, str, str]:
        """Build the (name, extension, size, modified) column values for a
        search result row."""
        show_ext_col = "extension" in self._panel._visible_columns_list  # type: ignore[attr-defined]
        if entry.is_dir:
            display_name = f" {entry.name}"
            ext_text = ""
        elif show_ext_col:
            name_part, ext_part = self._panel._split_name_ext(entry.name)  # type: ignore[attr-defined]
            display_name = f" {name_part}"
            ext_text = ext_part
        else:
            display_name = f" {entry.name}"
            ext_text = split_extension(entry.name)
        size_text = "<DIR>" if entry.is_dir else format_size(entry.size)
        mtime_text = format_mtime(entry.mtime)
        return (display_name, ext_text, size_text, mtime_text)

    def add_results(self, entries: list[FileEntry]) -> None:
        """Add a batch of search results to the panel."""
        if not self._active or not entries:
            return
        for entry in entries:
            self._results.append(entry)
            self._panel._entries.append(entry)  # type: ignore[attr-defined]
            self._panel._tree.insert(  # type: ignore[attr-defined]
                "",
                "end",
                iid=str(len(self._panel._entries) - 1),  # type: ignore[attr-defined]
                values=self.row_values(entry),
            )

    def rerender(self) -> None:
        """Re-sort the accumulated search results and rebuild the tree."""
        self._panel._entries = sort_entries(  # type: ignore[attr-defined]
            self._results,
            key=self._panel.sort_key,  # type: ignore[attr-defined]
            reverse=self._panel.sort_reverse,  # type: ignore[attr-defined]
        )
        self._panel._tree.delete(*self._panel._tree.get_children())  # type: ignore[attr-defined]
        for index, entry in enumerate(self._panel._entries):  # type: ignore[attr-defined]
            self._panel._tree.insert(  # type: ignore[attr-defined]
                "",
                "end",
                iid=str(index),
                values=self.row_values(entry),
            )
