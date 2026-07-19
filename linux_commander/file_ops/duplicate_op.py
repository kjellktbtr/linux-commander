"""File operation: Duplicate file finder.

Default comparison method (applied in order, cheapest check first):
1. Size differs -> not duplicates.
2. Size matches -> compare SHA256 checksums; differs -> not duplicates.
3. Checksum matches -> full byte-for-byte content comparison, since a
   checksum match is strong evidence but not proof. Files at or below the
   "large file" threshold (``Settings.duplicate_large_file_mb``, default
   10MB, adjustable per-scan) are compared immediately; above it, the user
   is asked once per group whether to assume similar, assume different, or
   actually compare the content -- with a checkbox to apply that choice to
   every later large-file group in the same scan, so a directory full of
   large same-checksum files doesn't prompt repeatedly.

Works across any VFS backend (local, archive, remote), not just local disk.
Results shown grouped with selection for delete/move.
"""

from __future__ import annotations

import hashlib
import threading
import tkinter as tk
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk
from typing import TYPE_CHECKING

from linux_commander.dialogs import _center_over, confirm
from linux_commander.file_ops import FileOperation
from linux_commander.operations import CancelPredicate, OperationError, ProgressCallback
from linux_commander.settings import load_settings
from linux_commander.vfs import LocalFileSystem, VfsPath

if TYPE_CHECKING:
    from linux_commander.app import CommanderApp

_DEFAULT_LARGE_FILE_MB = 10.0
_COMPARE_CHUNK_SIZE = 65536


@dataclass
class DuplicateGroup:
    """A group of duplicate files."""

    files: list[VfsPath]
    size: int
    hash_value: str | None = None


@dataclass
class ScanResult:
    """Result of a duplicate scan."""

    groups: list[DuplicateGroup]
    total_files: int
    total_size: int
    wasted_size: int


def _format_size(size: int) -> str:
    size_float = float(size)
    for unit in ["B", "KB", "MB", "GB"]:
        if size_float < 1024:
            return f"{size_float:.1f} {unit}"
        size_float /= 1024
    return f"{size_float:.1f} TB"


def _hash_file(path: VfsPath, algorithm: str = "sha256") -> str:
    """Compute hash of a file."""
    hasher = hashlib.new(algorithm)
    with path.fs.open_read(path) as f:
        while chunk := f.read(_COMPARE_CHUNK_SIZE):
            hasher.update(chunk)
    return hasher.hexdigest()


def _content_equal(a: VfsPath, b: VfsPath) -> bool:
    """Byte-for-byte comparison via the VFS read API (works across backends,
    e.g. comparing a local file against one inside a Jottacloud mount)."""
    with a.fs.open_read(a) as fa, b.fs.open_read(b) as fb:
        while True:
            chunk_a = fa.read(_COMPARE_CHUNK_SIZE)
            chunk_b = fb.read(_COMPARE_CHUNK_SIZE)
            if chunk_a != chunk_b:
                return False
            if not chunk_a:
                return True


def _compare_content_group(
    group: DuplicateGroup, should_cancel: CancelPredicate
) -> list[DuplicateGroup]:
    """Split a same-size/same-checksum group by actual byte-for-byte content
    equality. A checksum match makes an actual mismatch astronomically
    unlikely, but this is the final word before files get reported as
    duplicates (and potentially deleted) -- so it's still verified, not
    assumed. Files that turn out to differ end up in their own
    (dropped-if-singleton) cluster rather than staying grouped."""
    clusters: list[list[VfsPath]] = []
    for path in group.files:
        if should_cancel():
            break
        placed = False
        for cluster in clusters:
            try:
                if _content_equal(cluster[0], path):
                    cluster.append(path)
                    placed = True
                    break
            except OSError:
                continue
        if not placed:
            clusters.append([path])
    return [
        DuplicateGroup(files=c, size=group.size, hash_value=group.hash_value)
        for c in clusters
        if len(c) > 1
    ]


def _walk_for_duplicates(
    root: VfsPath,
    on_progress: ProgressCallback | None,
    should_cancel: CancelPredicate,
    min_size: int = 0,
) -> list[tuple[VfsPath, int]]:
    """Recursively walk ``root`` via the VFS ``list_dir`` API, returning
    ``(path, size)`` for every file at or under it meeting ``min_size``.

    Works for any backend (local disk, archives, remote mounts like
    Jottacloud/SMB/WebDAV) since it never touches the filesystem directly --
    unlike the old ``os.walk``-based version, which silently returned no
    results at all for anything that wasn't a ``LocalFileSystem``.
    """
    files: list[tuple[VfsPath, int]] = []
    file_count = 0

    def _recurse(vpath: VfsPath) -> None:
        nonlocal file_count
        if should_cancel():
            return
        try:
            entries = vpath.fs.list_dir(vpath)
        except OSError:
            return
        for entry in entries:
            if entry.is_parent:
                continue
            if entry.is_dir:
                _recurse(entry.path)
            elif entry.size >= min_size:
                files.append((entry.path, entry.size))
                file_count += 1
        if on_progress:
            on_progress(file_count, 0, str(vpath))

    try:
        st = root.fs.stat(root)
    except OSError:
        return files
    if not st.is_dir:
        if st.size >= min_size:
            files.append((root, st.size))
        return files

    _recurse(root)
    return files


def _find_duplicates_by_size(
    files: list[tuple[VfsPath, int]],
) -> list[DuplicateGroup]:
    """Group files by size, return groups with more than 1 file."""
    size_map: dict[int, list[VfsPath]] = defaultdict(list)
    for path, size in files:
        size_map[size].append(path)

    groups = []
    for size, paths in size_map.items():
        if len(paths) > 1:
            groups.append(DuplicateGroup(files=paths, size=size))
    return groups


def _hash_group(
    group: DuplicateGroup,
    on_progress: ProgressCallback | None,
    should_cancel: CancelPredicate,
) -> list[DuplicateGroup]:
    """Hash files in a same-size group and split by hash.

    Only hash values shared by more than one file are kept -- a file whose
    checksum doesn't match anything else in the size-bucket is not a
    duplicate candidate and must not be reported as one.
    """
    hash_map: dict[str, list[VfsPath]] = defaultdict(list)
    for i, path in enumerate(group.files):
        if should_cancel():
            break
        if on_progress:
            on_progress(i + 1, len(group.files), path.name, None, None)
        try:
            h = _hash_file(path)
            hash_map[h].append(path)
        except OSError:
            continue

    return [
        DuplicateGroup(files=paths, size=group.size, hash_value=h)
        for h, paths in hash_map.items()
        if len(paths) > 1
    ]


def _prepare_duplicate_finder(parent: tk.Misc, sources: list[VfsPath]) -> dict | None:
    """Show duplicate finder dialog and return parameters."""
    dialog = DuplicateFinderDialog(parent, sources)
    return dialog.result


class DuplicateFinderDialog(tk.Toplevel):
    """Dialog for configuring duplicate scan."""

    def __init__(self, parent: tk.Misc, sources: list[VfsPath]) -> None:
        super().__init__(parent)
        self.title("Find Duplicates")
        self.sources = sources
        self.result: dict | None = None
        self._build_ui()
        _center_over(self, parent)
        self.grab_set()
        self.wait_window()

    def _build_ui(self) -> None:
        self.geometry("500x420")
        self.minsize(450, 380)

        # Source directories
        src_frame = ttk.LabelFrame(self, text="Scan Directories", padding=8)
        src_frame.pack(fill="x", padx=8, pady=8)

        self.listbox = tk.Listbox(src_frame, height=6, exportselection=False)
        scrollbar = ttk.Scrollbar(src_frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scrollbar.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        for src in self.sources:
            self.listbox.insert("end", str(src))

        btn_frame = ttk.Frame(src_frame)
        btn_frame.pack(fill="x", pady=(4, 0))
        ttk.Button(btn_frame, text="Add Panel Dir", command=self._add_panel_dir).pack(
            side="left", padx=2
        )
        ttk.Button(btn_frame, text="Remove", command=self._remove_dir).pack(side="left", padx=2)

        # Options
        opts_frame = ttk.LabelFrame(self, text="Options", padding=8)
        opts_frame.pack(fill="x", padx=8, pady=8)
        ttk.Label(
            opts_frame,
            text="Files are compared by size, then checksum, then full content.",
            wraplength=440,
            justify="left",
        ).pack(anchor="w")

        self.min_size_var = tk.StringVar(value="0")
        ttk.Label(opts_frame, text="Minimum file size to consider (bytes):").pack(
            anchor="w", pady=(8, 0)
        )
        ttk.Entry(opts_frame, textvariable=self.min_size_var, width=16).pack(anchor="w")

        settings = load_settings()
        self.large_file_mb_var = tk.StringVar(value=str(settings.duplicate_large_file_mb))
        ttk.Label(
            opts_frame,
            text=(
                "Ask before comparing full content of files larger than\n"
                "this size (MB) -- smaller matches are compared right away:"
            ),
            justify="left",
        ).pack(anchor="w", pady=(8, 0))
        ttk.Entry(opts_frame, textvariable=self.large_file_mb_var, width=16).pack(anchor="w")

        # Buttons
        btn_frame2 = ttk.Frame(self)
        btn_frame2.pack(fill="x", padx=8, pady=(0, 8))

        ttk.Button(btn_frame2, text="Cancel", command=self._on_cancel).pack(side="right", padx=4)
        ttk.Button(btn_frame2, text="Scan", command=self._on_scan).pack(side="right", padx=4)

        self.bind("<Escape>", lambda e: self._on_cancel())

    def _add_panel_dir(self) -> None:
        pass  # Would need access to panels

    def _remove_dir(self) -> None:
        sel = self.listbox.curselection()
        if sel:
            self.listbox.delete(sel[0])

    def _on_cancel(self) -> None:
        self.result = None
        self.destroy()

    def _on_scan(self) -> None:
        dirs = list(self.listbox.get(0, "end"))
        if not dirs:
            return
        try:
            large_file_mb = float(self.large_file_mb_var.get())
        except ValueError:
            large_file_mb = _DEFAULT_LARGE_FILE_MB
        self.result = {
            "directories": dirs,
            "min_size": int(self.min_size_var.get()) if self.min_size_var.get().isdigit() else 0,
            "large_file_mb": large_file_mb,
        }
        self.destroy()


class LargeFileChoiceDialog(tk.Toplevel):
    """Modal prompt shown when a same-checksum group's files exceed the
    content-compare size threshold, before doing a full byte-for-byte read.

    Offers three choices (assume similar / assume different / compare
    content) plus a checkbox to apply the choice to every later large-file
    group in the same scan, so a whole directory of large matches doesn't
    prompt once per group.
    """

    def __init__(self, parent: tk.Misc, group: DuplicateGroup, threshold_mb: float) -> None:
        super().__init__(parent)
        self.title("Large File Comparison")
        self.resizable(False, False)
        self.action: str | None = None
        self.apply_to_all = False
        self._build_ui(group, threshold_mb)
        _center_over(self, parent)
        self.grab_set()
        self.wait_window()

    def _build_ui(self, group: DuplicateGroup, threshold_mb: float) -> None:
        count = len(group.files)
        message = (
            f"{count} files of size {_format_size(group.size)} have matching checksums "
            f"and exceed the {threshold_mb:g} MB content-compare threshold.\n\n"
            "Comparing their full content can take a while for large files. "
            "How should these be treated?"
        )
        ttk.Label(self, text=message, padding=16, wraplength=380, justify="left").pack()

        self._apply_all_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            self,
            text="Do this for all remaining large files in this scan",
            variable=self._apply_all_var,
        ).pack(padx=16, pady=(0, 8), anchor="w")

        button_frame = ttk.Frame(self)
        button_frame.pack(fill="x", padx=8, pady=(0, 12))
        ttk.Button(
            button_frame, text="Assume Similar", command=lambda: self._choose("similar")
        ).pack(side="left", padx=4)
        ttk.Button(
            button_frame, text="Assume Different", command=lambda: self._choose("different")
        ).pack(side="left", padx=4)
        ttk.Button(
            button_frame, text="Compare Content", command=lambda: self._choose("compare")
        ).pack(side="left", padx=4)

        self.protocol("WM_DELETE_WINDOW", lambda: self._choose("compare"))
        self.bind("<Escape>", lambda e: self._choose("compare"))

    def _choose(self, action: str) -> None:
        self.action = action
        self.apply_to_all = self._apply_all_var.get()
        self.destroy()


class DuplicateResultsDialog(tk.Toplevel):
    """Dialog showing duplicate groups with selection for action."""

    def __init__(
        self,
        parent: tk.Misc,
        result: ScanResult,
        on_delete: Callable[[list[VfsPath]], None],
        on_move: Callable[[list[VfsPath], VfsPath], None],
    ) -> None:
        super().__init__(parent)
        self.title("Duplicate Files Found")
        self.result = result
        self.on_delete = on_delete
        self.on_move = on_move
        self._selected_files: set[str] = set()
        self._item_to_path: dict[str, VfsPath] = {}
        self._build_ui()
        _center_over(self, parent)
        self.grab_set()
        self.wait_window()

    def _build_ui(self) -> None:
        self.geometry("900x500")
        self.minsize(700, 400)

        # Summary
        summary = (
            f"Found {len(self.result.groups)} duplicate groups, "
            f"{self._total_dupes()} duplicate files, "
            f"{_format_size(self.result.wasted_size)} wasted"
        )
        ttk.Label(self, text=summary, padding=8).pack(fill="x")

        # Tree with groups
        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        columns = ("select", "file", "size", "hash")
        self.tree = ttk.Treeview(
            tree_frame, columns=columns, show="tree headings", selectmode="extended"
        )
        self.tree.heading("#0", text="Group")
        self.tree.heading("select", text="☐", anchor="center")
        self.tree.heading("file", text="File Path")
        self.tree.heading("size", text="Size", anchor="center")
        self.tree.heading("hash", text="SHA256", anchor="w")
        self.tree.column("#0", width=80, anchor="center")
        self.tree.column("select", width=40, anchor="center")
        self.tree.column("file", width=400, anchor="w")
        self.tree.column("size", width=80, anchor="center")
        self.tree.column("hash", width=300, anchor="w")
        self.tree.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")

        self.tree.tag_configure("group", font=("TkDefaultFont", 9, "bold"))
        self.tree.tag_configure("file", font=("TkDefaultFont", 9))

        self._populate_tree()

        # Checkbox handling
        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<space>", self._on_space)

        # Buttons
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x", padx=8, pady=(0, 8))

        ttk.Button(btn_frame, text="Select All", command=self._select_all).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Select None", command=self._select_none).pack(
            side="left", padx=4
        )
        ttk.Separator(btn_frame, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(btn_frame, text="Delete Selected", command=self._delete_selected).pack(
            side="left", padx=4
        )
        ttk.Button(btn_frame, text="Move Selected...", command=self._move_selected).pack(
            side="left", padx=4
        )
        ttk.Button(btn_frame, text="Close", command=self.destroy).pack(side="right", padx=4)

    def _populate_tree(self) -> None:
        for i, group in enumerate(self.result.groups):
            hash_str = group.hash_value[:16] + "..." if group.hash_value else ""
            gid = self.tree.insert(
                "",
                "end",
                text=f"Group {i + 1} ({len(group.files)} files)",
                values=("", "", f"{group.size} bytes", hash_str),
                tags=("group",),
            )
            for f in group.files:
                item_id = f"file_{id(group)}_{id(f)}"
                self.tree.insert(
                    gid,
                    "end",
                    text="",
                    values=("☐", str(f), f"{group.size} bytes", hash_str),
                    tags=("file",),
                    iid=item_id,
                )
                self._item_to_path[item_id] = f

    def _total_dupes(self) -> int:
        return sum(len(g.files) - 1 for g in self.result.groups)

    def _on_click(self, event) -> None:
        region = self.tree.identify("region", event.x, event.y)
        if region == "cell":
            col = self.tree.identify_column(event.x)
            if col == "#1":
                item = self.tree.identify_row(event.y)
                if item and item in self._item_to_path:
                    self._toggle_item(item)

    def _on_space(self, event) -> None:
        for item in self.tree.selection():
            if item in self._item_to_path:
                self._toggle_item(item)

    def _toggle_item(self, item: str) -> None:
        if item in self._selected_files:
            self._selected_files.remove(item)
            self.tree.set(item, "select", "☐")
        else:
            self._selected_files.add(item)
            self.tree.set(item, "select", "☑")

    def _select_all(self) -> None:
        for item in self._item_to_path:
            if item not in self._selected_files:
                self._selected_files.add(item)
                self.tree.set(item, "select", "☑")

    def _select_none(self) -> None:
        self._selected_files.clear()
        for item in self._item_to_path:
            self.tree.set(item, "select", "☐")

    def _get_selected_vfspaths(self) -> list[VfsPath]:
        return [
            self._item_to_path[item] for item in self._selected_files if item in self._item_to_path
        ]

    def _delete_selected(self) -> None:
        selected = self._get_selected_vfspaths()
        if not selected:
            return
        if confirm(self, f"Delete {len(selected)} selected file(s)?", "Confirm Delete"):
            self.on_delete(selected)
            for item in list(self._selected_files):
                self.tree.delete(item)
            self._selected_files.clear()

    def _move_selected(self) -> None:
        # Would need destination dialog - simplified for now
        pass


class _ScanResultHolder:
    """Thread-safe holder for scan result and errors."""

    def __init__(self) -> None:
        self.result: ScanResult | None = None
        self.errors: list[OperationError] = []
        self.event = threading.Event()

    def set_result(self, result: ScanResult, errors: list[OperationError]) -> None:
        self.result = result
        self.errors = errors
        self.event.set()


def _ask_large_file_action(
    app: CommanderApp, group: DuplicateGroup, threshold_mb: float
) -> tuple[str, bool]:
    """Marshal ``LargeFileChoiceDialog`` onto the Tk main thread and block
    the calling (scan worker) thread until the user responds.

    Returns ``(action, apply_to_all)``; if the dialog is dismissed without an
    explicit choice, defaults to ``"compare"`` (do the real comparison rather
    than silently assuming anything) with ``apply_to_all=False``.
    """
    done = threading.Event()
    response: dict[str, object] = {"action": "compare", "apply_to_all": False}

    def show() -> None:
        dialog = LargeFileChoiceDialog(app, group, threshold_mb)
        if dialog.action is not None:
            response["action"] = dialog.action
            response["apply_to_all"] = dialog.apply_to_all
        done.set()

    app.after(0, show)
    done.wait()
    return response["action"], bool(response["apply_to_all"])  # type: ignore[return-value]


def _run_duplicate_finder(
    sources: list[VfsPath],
    dest_dir: VfsPath,
    on_progress: ProgressCallback,
    should_cancel: CancelPredicate,
    directories: list[str],
    min_size: int,
    large_file_mb: float = _DEFAULT_LARGE_FILE_MB,
) -> list[OperationError]:
    """Run duplicate finder scan in background, then show results dialog on main thread."""
    errors: list[OperationError] = []
    holder = _ScanResultHolder()
    large_file_bytes = int(large_file_mb * 1024 * 1024)

    # Parse directory strings to VfsPath objects
    scan_roots: list[VfsPath] = []
    for dir_str in directories:
        found = None
        for src in sources:
            if str(src).rstrip("/") == dir_str.rstrip("/"):
                found = src
                break
        if found:
            scan_roots.append(found)
        else:
            fs = LocalFileSystem()
            path = fs.from_path(Path(dir_str))
            if path and fs.realpath(path):
                scan_roots.append(path)
            else:
                errors.append(
                    OperationError(
                        path=VfsPath(fs=LocalFileSystem(), parts=("", dir_str)),
                        message=f"Directory not found: {dir_str}",
                    )
                )

    if not scan_roots:
        errors.append(
            OperationError(
                path=sources[0] if sources else VfsPath(fs=LocalFileSystem(), parts=("",)),
                message="No valid directories to scan",
            )
        )
        return errors

    # Background scan function
    def do_scan() -> None:
        all_files: list[tuple[VfsPath, int]] = []
        total_files = 0

        for root in scan_roots:
            if should_cancel():
                break
            on_progress(0, 0, f"Scanning {root}...", None, None)
            files = _walk_for_duplicates(root, on_progress, should_cancel, min_size)
            all_files.extend(files)
            total_files += len(files)

        if should_cancel():
            holder.set_result(ScanResult([], 0, 0, 0), errors)
            return

        on_progress(0, 0, f"Found {total_files} files, grouping by size...", None, None)
        size_groups = _find_duplicates_by_size(all_files)

        on_progress(0, 0, f"Comparing checksums of {len(size_groups)} size groups...", None, None)
        hash_groups: list[DuplicateGroup] = []
        for group in size_groups:
            if should_cancel():
                break
            hash_groups.extend(_hash_group(group, on_progress, should_cancel))

        # Step 3: same size + same checksum -> verify with a real content
        # compare, prompting first for large files (unless the user already
        # chose to apply one action to all of them in this scan).
        app = CommanderApp.get_app()
        remembered_action: str | None = None
        final_groups: list[DuplicateGroup] = []
        for group in hash_groups:
            if should_cancel():
                break
            if group.size > large_file_bytes:
                if remembered_action is not None:
                    action = remembered_action
                elif app is None:
                    action = "compare"  # no UI context available -- do the real check
                else:
                    action, apply_to_all = _ask_large_file_action(app, group, large_file_mb)
                    if apply_to_all:
                        remembered_action = action
                if action == "similar":
                    final_groups.append(group)
                    continue
                if action == "different":
                    continue
                # action == "compare" falls through to the real comparison below.
            final_groups.extend(_compare_content_group(group, should_cancel))

        wasted = sum((len(g.files) - 1) * g.size for g in final_groups)
        result = ScanResult(
            groups=final_groups,
            total_files=total_files,
            total_size=sum(s for _, s in all_files),
            wasted_size=wasted,
        )
        holder.set_result(result, errors)

    # Start scan in background thread
    scan_thread = threading.Thread(target=do_scan, daemon=True)
    scan_thread.start()

    # Wait for scan to complete while pumping progress updates
    # The on_progress callback is called from the scan thread and queues updates
    # to the main thread via the progress dialog mechanism
    while scan_thread.is_alive():
        if should_cancel():
            break
        scan_thread.join(timeout=0.1)

    # Check if scan completed with result
    if holder.result is None:
        return errors

    # Now show results dialog on main thread
    app = CommanderApp.get_app()
    if app and holder.result is not None:
        result = holder.result
        # We need to run the dialog on the main thread and wait for it
        # Use a simple event to synchronize
        dialog_done = threading.Event()

        def show_dialog() -> None:
            def on_delete(files: list[VfsPath]) -> None:
                for f in files:
                    try:
                        f.fs.delete(f)
                    except OSError as e:
                        errors.append(OperationError(path=f, message=f"Delete failed: {e}"))

            def on_move(files: list[VfsPath], dest: VfsPath) -> None:
                for f in files:
                    try:
                        f.fs.rename(f, dest / f.name)
                    except OSError as e:
                        errors.append(OperationError(path=f, message=f"Move failed: {e}"))

            DuplicateResultsDialog(app, result, on_delete, on_move)
            dialog_done.set()

        app.after(0, show_dialog)
        dialog_done.wait()

    return errors


OPERATIONS: list[FileOperation] = [
    FileOperation(
        name="Find Duplicates…",
        run=_run_duplicate_finder,
        prepare=_prepare_duplicate_finder,
        description="Scan for duplicate files by size, checksum, and content",
    ),
]
