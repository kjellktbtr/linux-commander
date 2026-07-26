"""Panel loading helpers — tree population and entry formatting.

Extracted from ``FilePanel.load()`` to reduce the panel module size.
Functions here take a ``FilePanel`` reference to access shared state
(tree widget, entries list, settings, mount manager, callbacks).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from linux_commander.fs import format_mtime, format_size, sort_entries, split_extension
from linux_commander.vfs import ReadableFileSystem

if TYPE_CHECKING:
    from linux_commander.panel import FilePanel
    from linux_commander.vfs import FileEntry, VfsPath


def list_and_sort(panel: FilePanel, path: VfsPath) -> list[FileEntry] | None:
    """List directory entries, filter hidden files, and sort.

    Returns ``None`` on error (error already reported via ``panel._on_error``).
    """
    try:
        if panel.flat_view:
            raw = path.fs.list_dir_flat(path)
        else:
            raw = path.fs.list_dir(path)
    except OSError as exc:
        panel._report_error(f"Could not open '{path}':\n{exc}")
        return None

    if not panel.show_hidden:
        raw = [e for e in raw if e.is_parent or not e.name.startswith(".")]

    return sort_entries(raw, key=panel.sort_key, reverse=panel.sort_reverse)


def populate_tree(panel: FilePanel, entries: list[FileEntry]) -> None:
    """Clear and repopulate the panel's Treeview with *entries*.

    Respects ``show_icons``, ``show_extension``, and ``_visible_columns_list``.
    """
    from linux_commander import icons as _icons

    panel._tree.delete(*panel._tree.get_children())

    # Resolve icons lazily (None when PIL unavailable or icons disabled)
    if panel.show_icons:
        try:
            _icon_fn = _icons.icon_for_entry
        except Exception:
            _icon_fn = None
    else:
        _icon_fn = None

    show_ext_col = "extension" in panel._visible_columns_list

    for index, entry in enumerate(entries):
        _pfx = " " if _icon_fn is not None else ""

        if entry.is_dir:
            display_name = f"{_pfx}[{entry.name}]"
            ext_text = ""
        elif show_ext_col:
            name_part, ext_part = panel._split_name_ext(entry.name)
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
        panel._tree.insert(**kw)


def post_load_housekeeping(
    panel: FilePanel,
    old_fs: ReadableFileSystem,
    path: VfsPath,
    select_name: str | None,
    select_parent: bool,
    add_to_history: bool,
) -> None:
    """Run post-load tasks: update header, manage mounts, clear marks,
    update history, select cursor, and notify directory changed.

    ``old_fs`` is the filesystem of the path *before* this load call
    (needed to detect cross-filesystem navigation for mount cleanup).
    """
    panel.current_path = path
    panel._update_header()

    # Release mount refs when leaving a filesystem
    if old_fs is not path.fs:
        panel._mount_manager.release_if_leaving(old_fs, path.fs)

    # Clear marks on load
    if panel.marked:
        panel.marked = set()
        panel._notify_marks_changed()

    if add_to_history:
        panel._add_to_history(path)

    # Select cursor
    target_index = _default_cursor_index(panel, select_name, select_parent)
    if target_index is not None:
        panel._select_index(target_index)

    if panel._on_directory_changed is not None:
        panel._on_directory_changed()


def _default_cursor_index(
    panel: FilePanel,
    select_name: str | None,
    select_parent: bool,
) -> int | None:
    """Find the default cursor index after loading.

    Priority: explicit ``select_name`` (non-parent) > parent entry
    (if ``select_parent``) > first non-parent entry > parent entry.
    """
    entries = panel._entries
    if not entries:
        return None

    if select_name is not None:
        for index, entry in enumerate(entries):
            if entry.name == select_name and not entry.is_parent:
                return index

    if select_parent:
        for index, entry in enumerate(entries):
            if entry.is_parent:
                return index

    for index, entry in enumerate(entries):
        if not entry.is_parent:
            return index

    for index, entry in enumerate(entries):
        if entry.is_parent:
            return index

    return None
