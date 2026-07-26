"""File operation: Create FAT12/FAT16 floppy disk images.

Appears in the Operations menu as ``"Create Floppy Image"``.  Shows a dialog
to pick format, output filename, and optional volume label, then builds the
image on a background thread.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import cast

from linux_commander.dialogs import _center_over
from linux_commander.fatfs import FLOPPY_FORMATS, FATImageBuilder
from linux_commander.file_ops import FileOperation
from linux_commander.operations import CancelPredicate, OperationError, ProgressCallback
from linux_commander.vfs import VfsPath, WritableFileSystem

# ---------------------------------------------------------------------------
# Prepare dialog
# ---------------------------------------------------------------------------

# Format choices presented to the user
_FORMAT_CHOICES = [
    ("360K (FAT12)", "360K"),
    ("720K (FAT12)", "720K"),
    ("1.2M (FAT12)", "1.2M"),
    ("1.44M (FAT12)", "1.44M"),
    ("2.88M (FAT16)", "2.88M"),
]


def _prepare_floppy(parent: tk.Misc, sources: list[VfsPath]) -> dict | None:
    """Show a dialog to collect floppy image creation parameters."""
    dialog = tk.Toplevel(parent)
    dialog.title("Create Floppy Image")
    dialog.transient(parent)  # type: ignore[call-overload]
    dialog.grab_set()
    _center_over(dialog, parent)

    # Prevent dialog close on WM_DELETE_WINDOW
    dialog.protocol("WM_DELETE_WINDOW", lambda: None)

    result: dict | None = None

    # -- Format dropdown --
    ttk.Label(dialog, text="Format:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
    fmt_var = tk.StringVar(value="1.44M")
    fmt_menu = ttk.Combobox(
        dialog,
        textvariable=fmt_var,
        values=[c[0] for c in _FORMAT_CHOICES],
        state="readonly",
        width=20,
    )
    fmt_menu.grid(row=0, column=1, padx=5, pady=5, sticky="w")
    fmt_menu.current(3)  # Default to 1.44M

    # -- Output filename --
    ttk.Label(dialog, text="Output:").grid(row=1, column=0, padx=5, pady=5, sticky="w")
    out_var = tk.StringVar(value="floppy.img")
    out_entry = ttk.Entry(dialog, textvariable=out_var, width=30)
    out_entry.grid(row=1, column=1, padx=5, pady=5, sticky="w")

    # -- Volume label --
    ttk.Label(dialog, text="Volume:").grid(row=2, column=0, padx=5, pady=5, sticky="w")
    vol_var = tk.StringVar(value="FLOPPY")
    vol_entry = ttk.Entry(dialog, textvariable=vol_var, width=30)
    vol_entry.grid(row=2, column=1, padx=5, pady=5, sticky="w")

    # -- Capacity warning --
    cap_label = ttk.Label(dialog, text="", foreground="red")
    cap_label.grid(row=3, column=0, columnspan=2, padx=5, pady=5, sticky="w")

    def _update_capacity() -> None:
        chosen = fmt_var.get()
        fmt_key = next((c[1] for c in _FORMAT_CHOICES if c[0] == chosen), "1.44M")
        fmt = FLOPPY_FORMATS[fmt_key]
        capacity = fmt.capacity_bytes
        # Rough estimate: subtract ~10% for FAT overhead
        usable = int(capacity * 0.9)
        cap_label.config(text=f"Usable space: ~{usable // 1024} KiB")

    fmt_menu.bind("<<ComboboxSelected>>", lambda _: _update_capacity())
    _update_capacity()

    # -- OK / Cancel --
    def _on_ok() -> None:
        nonlocal result
        chosen = fmt_var.get()
        fmt_key = next((c[1] for c in _FORMAT_CHOICES if c[0] == chosen), "1.44M")
        result = {
            "format": fmt_key,
            "output": out_var.get() or "floppy.img",
            "volume_label": vol_var.get()[:11],
        }
        dialog.destroy()

    def _on_cancel() -> None:
        nonlocal result
        result = None
        dialog.destroy()

    btn_frame = ttk.Frame(dialog)
    btn_frame.grid(row=4, column=0, columnspan=2, pady=10)
    ttk.Button(btn_frame, text="OK", command=_on_ok).grid(row=0, column=0, padx=5)
    ttk.Button(btn_frame, text="Cancel", command=_on_cancel).grid(row=0, column=1, padx=5)

    dialog.bind("<Return>", lambda _: _on_ok())
    dialog.bind("<Escape>", lambda _: _on_cancel())

    dialog.wait_window()
    return result


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


def _count_files(sources: list[VfsPath]) -> int:
    """Count total number of files in sources (recursively for directories)."""
    count = 0
    for src in sources:
        st = src.fs.stat(src)
        if st.is_dir:
            children = [e for e in src.fs.list_dir(src) if not e.is_parent]
            for child in children:
                count += _count_files([child.path])
        else:
            count += 1
    return count


def _create_floppy_run(
    sources: list[VfsPath],
    dest_dir: VfsPath,
    on_progress: ProgressCallback,
    should_cancel: CancelPredicate,
    format: str = "1.44M",
    output: str = "floppy.img",
    volume_label: str = "",
    **kwargs,
) -> list[OperationError]:
    """Build a floppy image from the selected files."""
    errors: list[OperationError] = []
    fmt = FLOPPY_FORMATS.get(format)
    if fmt is None:
        return [OperationError(path=dest_dir, message=f"Unknown format: {format}")]

    builder = FATImageBuilder(fmt, volume_label)

    # Count total files (including those in directories) for progress
    total_files = _count_files(sources)
    counter = [0]

    def _add_entry(src: VfsPath, prefix: str = "") -> None:
        """Recursively add a file or directory to the floppy image."""
        if should_cancel():
            return

        st = src.fs.stat(src)
        rel_path = f"{prefix}/{src.name}" if prefix else src.name

        if st.is_dir:
            children = [e for e in src.fs.list_dir(src) if not e.is_parent]
            for child in children:
                _add_entry(child.path, rel_path)
        else:
            try:
                data = src.fs.open_read(src).read()
                builder.add_file(rel_path, data)
                counter[0] += 1
                on_progress(counter[0], total_files, f"Added {rel_path}")
            except Exception as exc:
                errors.append(OperationError(path=src, message=str(exc)))

    for src in sources:
        if should_cancel():
            break
        _add_entry(src)

    if should_cancel():
        return [OperationError(path=dest_dir, message="Cancelled")]

    # Write the image to the active panel's directory
    try:
        image_data = builder.finalize()
        out_vpath = dest_dir / output
        with cast(WritableFileSystem, out_vpath.fs).open_write(out_vpath) as f:
            f.write(image_data)
    except Exception as exc:
        errors.append(OperationError(path=dest_dir, message=str(exc)))

    on_progress(total_files, total_files, "Done")
    return errors


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

OPERATIONS = [
    FileOperation(
        name="Create Floppy Image",
        run=_create_floppy_run,
        prepare=_prepare_floppy,
        description="Create a FAT12/FAT16 floppy disk image from selected files",
    ),
]
