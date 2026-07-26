"""File operations: Checksum generation and verification.

Supports MD5, SHA1, SHA256, SHA512 algorithms.
Modes:
- Generate: Create checksum file(s) for selected files
- Verify: Check existing checksum files against actual files
- Display: Show checksums in a dialog
"""

from __future__ import annotations

import hashlib
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from tkinter import ttk
from typing import TYPE_CHECKING, cast

from linux_commander.dialogs import _center_over, error
from linux_commander.file_ops import FileOperation
from linux_commander.operations import CancelPredicate, OperationError, ProgressCallback
from linux_commander.vfs import LocalFileSystem, VfsPath, WritableFileSystem

if TYPE_CHECKING:
    from linux_commander.vfs import ReadableFileSystem, WritableFileSystem


class ChecksumAlgorithm(Enum):
    MD5 = ("MD5", "md5", ".md5")
    SHA1 = ("SHA1", "sha1", ".sha1")
    SHA256 = ("SHA256", "sha256", ".sha256")
    SHA512 = ("SHA512", "sha512", ".sha512")

    def __init__(self, display: str, hashlib_name: str, ext: str):
        self.display = display
        self.hashlib_name = hashlib_name
        self.ext = ext


class ChecksumMode(Enum):
    GENERATE_SINGLE = "Single file — show hash"
    GENERATE_SIDECAR = "Multiple files — create .md5/.sha256 sidecar files"
    GENERATE_SUM_FILE = "Multiple files — create SUM file (one file with all hashes)"
    VERIFY = "Verify — check files against .md5/.sha256 or SUM file"


class ChecksumOutputFormat(Enum):
    STANDARD = "Standard (hash  filename)"
    BSD = "BSD (hash) = filename"
    GNU = "GNU (hash *filename)"


@dataclass
class ChecksumResult:
    """Result of a checksum operation."""

    path: VfsPath
    algorithm: ChecksumAlgorithm
    hash_value: str
    verified: bool | None = None  # None = not verified, True = match, False = mismatch
    expected_hash: str | None = None


def _hash_file(
    path: VfsPath,
    algorithm: ChecksumAlgorithm,
    on_progress: Callable[[int, int], None] | None = None,
) -> str:
    """Compute hash of a file, with optional progress callback."""
    hasher = hashlib.new(algorithm.hashlib_name)
    total_size = path.fs.stat(path).size
    bytes_read = 0

    with path.fs.open_read(path) as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
            bytes_read += len(chunk)
            if on_progress:
                on_progress(bytes_read, total_size)

    return hasher.hexdigest()


def _parse_checksum_line(line: str, algorithm: ChecksumAlgorithm) -> tuple[str, str] | None:
    """Parse a line from a checksum file.

    Supports multiple formats:
    - Standard: hash  filename
    - BSD: hash (filename) = hash
    - GNU: hash *filename
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    # Try standard format: hash<space><space>filename
    parts = line.split()
    if len(parts) >= 2:
        hash_val = parts[0].lower()
        # Validate hash length
        expected_len = {
            ChecksumAlgorithm.MD5: 32,
            ChecksumAlgorithm.SHA1: 40,
            ChecksumAlgorithm.SHA256: 64,
            ChecksumAlgorithm.SHA512: 128,
        }[algorithm]
        if len(hash_val) == expected_len:
            filename = " ".join(parts[1:])
            # Remove leading * for GNU format
            if filename.startswith("*"):
                filename = filename[1:]
            # Remove parentheses for BSD format
            if filename.startswith("(") and filename.endswith(")"):
                filename = filename[1:-1]
            return hash_val, filename

    # Try BSD format: MD5 (filename) = hash
    if "=" in line:
        left, right = line.split("=", 1)
        hash_val = right.strip().lower()
        expected_len = {
            ChecksumAlgorithm.MD5: 32,
            ChecksumAlgorithm.SHA1: 40,
            ChecksumAlgorithm.SHA256: 64,
            ChecksumAlgorithm.SHA512: 128,
        }[algorithm]
        if len(hash_val) == expected_len:
            # Extract filename from left side: ALGO (filename)
            if "(" in left and ")" in left:
                filename = left[left.index("(") + 1 : left.index(")")]
                return hash_val, filename

    return None


def _read_checksum_file(path: VfsPath, algorithm: ChecksumAlgorithm) -> dict[str, str]:
    """Read a checksum file and return dict of filename -> expected_hash."""
    result = {}
    try:
        with path.fs.open_read(path) as f:
            content = f.read().decode("utf-8", errors="replace")
        for line in content.splitlines():
            parsed = _parse_checksum_line(line, algorithm)
            if parsed:
                hash_val, filename = parsed
                result[filename] = hash_val
    except Exception:
        pass
    return result


def _write_checksum_file(
    path: VfsPath,
    entries: list[tuple[str, str]],  # (hash, filename)
    algorithm: ChecksumAlgorithm,
    format: ChecksumOutputFormat = ChecksumOutputFormat.STANDARD,
) -> None:
    """Write checksum entries to a file."""
    lines = []
    for hash_val, filename in entries:
        if format == ChecksumOutputFormat.STANDARD:
            lines.append(f"{hash_val}  {filename}")
        elif format == ChecksumOutputFormat.BSD:
            lines.append(f"{algorithm.display} ({filename}) = {hash_val}")
        elif format == ChecksumOutputFormat.GNU:
            lines.append(f"{hash_val} *{filename}")

    content = "\n".join(lines) + "\n"
    with cast(WritableFileSystem, path.fs).open_write(path) as f:
        f.write(content.encode("utf-8"))


class ChecksumDialog(tk.Toplevel):
    """Dialog for configuring checksum operation."""

    def __init__(self, parent: tk.Misc, sources: list[VfsPath]) -> None:
        super().__init__(parent)
        self.title("Checksums")
        self.sources = sources
        self.result: dict | None = None
        self._build_ui()
        _center_over(self, parent)
        self.grab_set()
        self.wait_window()

    def _build_ui(self) -> None:
        self.geometry("500x500")
        self.minsize(450, 450)

        # Algorithm selection
        algo_frame = ttk.LabelFrame(self, text="Algorithm", padding=8)
        algo_frame.pack(fill="x", padx=8, pady=8)

        self.algo_var = tk.StringVar(value=ChecksumAlgorithm.SHA256.display)
        for algo in ChecksumAlgorithm:
            ttk.Radiobutton(
                algo_frame, text=algo.display, variable=self.algo_var, value=algo.display
            ).pack(anchor="w")

        # Mode selection
        mode_frame = ttk.LabelFrame(self, text="Mode", padding=8)
        mode_frame.pack(fill="x", padx=8, pady=8)

        self.mode_var = tk.StringVar(value=ChecksumMode.GENERATE_SIDECAR.value)
        self.mode_combo = ttk.Combobox(
            mode_frame,
            textvariable=self.mode_var,
            state="readonly",
            values=[m.value for m in ChecksumMode],
        )
        self.mode_combo.pack(fill="x")
        self.mode_combo.bind("<<ComboboxSelected>>", self._on_mode_change)

        # Options frame (dynamic based on mode)
        self.options_frame = ttk.LabelFrame(self, text="Options", padding=8)
        self.options_frame.pack(fill="x", padx=8, pady=8)

        # Output format (for SUM file mode)
        self.format_frame = ttk.Frame(self.options_frame)
        ttk.Label(self.format_frame, text="Output format:").pack(side="left")
        self.format_var = tk.StringVar(value=ChecksumOutputFormat.STANDARD.value)
        format_combo = ttk.Combobox(
            self.format_frame,
            textvariable=self.format_var,
            state="readonly",
            width=25,
            values=[f.value for f in ChecksumOutputFormat],
        )
        format_combo.pack(side="left", padx=(4, 0))

        # Verify options
        self.verify_frame = ttk.Frame(self.options_frame)
        self.verify_checksum_file_var = tk.StringVar()
        ttk.Label(self.verify_frame, text="Checksum file:").pack(side="left")
        ttk.Entry(self.verify_frame, textvariable=self.verify_checksum_file_var, width=30).pack(
            side="left", padx=(4, 0)
        )
        ttk.Button(self.verify_frame, text="Browse...", command=self._browse_checksum_file).pack(
            side="left", padx=(4, 0)
        )

        # Single file display
        self.single_frame = ttk.Frame(self.options_frame)
        ttk.Label(self.single_frame, text="Single file mode: hash will be shown in a dialog.").pack(
            anchor="w"
        )

        # Summary
        self.summary_var = tk.StringVar(value=f"{len(self.sources)} file(s) selected")
        ttk.Label(self, textvariable=self.summary_var, foreground="blue").pack(pady=(0, 8))

        # Buttons
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x", padx=8, pady=8)
        ttk.Button(btn_frame, text="Cancel", command=self._on_cancel).pack(side="right", padx=4)
        ttk.Button(btn_frame, text="OK", command=self._on_ok, style="Accent.TButton").pack(
            side="right", padx=4
        )

        self._on_mode_change()

    def _on_mode_change(self, event=None) -> None:
        """Show/hide options based on selected mode."""
        # Hide all
        for frame in (self.format_frame, self.verify_frame, self.single_frame):
            frame.pack_forget()

        mode_str = self.mode_var.get()
        if mode_str == ChecksumMode.GENERATE_SINGLE.value:
            self.single_frame.pack(fill="x")
            self.summary_var.set(f"Will show hash for {len(self.sources)} file(s) individually")
        elif mode_str == ChecksumMode.GENERATE_SIDECAR.value:
            ext = self.algo_var.get().lower()
            self.summary_var.set(
                f"Will create .{ext} sidecar for each of {len(self.sources)} file(s)"
            )
        elif mode_str == ChecksumMode.GENERATE_SUM_FILE.value:
            self.format_frame.pack(fill="x")
            self.summary_var.set(
                f"Will create one SUM file with hashes for {len(self.sources)} file(s)"
            )
        elif mode_str == ChecksumMode.VERIFY.value:
            self.verify_frame.pack(fill="x")
            self.summary_var.set(f"Will verify {len(self.sources)} file(s) against checksum file")

    def _browse_checksum_file(self) -> None:
        from tkinter import filedialog

        filename = filedialog.askopenfilename(
            parent=self,
            title="Select Checksum File",
            filetypes=[
                ("Checksum files", "*.md5 *.sha1 *.sha256 *.sha512 *.sum *.txt"),
                ("All files", "*.*"),
            ],
        )
        if filename:
            self.verify_checksum_file_var.set(filename)

    def _on_ok(self) -> None:
        mode_str = self.mode_var.get()
        mode = next(m for m in ChecksumMode if m.value == mode_str)
        algo_display = self.algo_var.get()
        algo = next(a for a in ChecksumAlgorithm if a.display == algo_display)

        params: dict[str, object] = {
            "algorithm": algo,
            "mode": mode,
        }

        if mode == ChecksumMode.GENERATE_SUM_FILE:
            fmt_str = self.format_var.get()
            params["output_format"] = next(f for f in ChecksumOutputFormat if f.value == fmt_str)
        elif mode == ChecksumMode.VERIFY:
            checksum_file = self.verify_checksum_file_var.get()
            if not checksum_file:
                error(self, "Please select a checksum file to verify against.")
                return
            params["checksum_file"] = checksum_file

        self.result = params
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self.destroy()


def _prepare_checksum(parent: tk.Misc, sources: list[VfsPath]) -> dict | None:
    """Prepare dialog for checksum operation."""
    dialog = ChecksumDialog(parent, sources)
    return dialog.result


def _run_checksum(
    sources: list[VfsPath],
    dest_dir: VfsPath,
    on_progress: ProgressCallback,
    should_cancel: CancelPredicate,
    *,
    algorithm: ChecksumAlgorithm,
    mode: ChecksumMode,
    output_format: ChecksumOutputFormat = ChecksumOutputFormat.STANDARD,
    checksum_file: str | None = None,
) -> list[OperationError]:
    """Execute checksum operation."""
    errors: list[OperationError] = []
    total = len(sources)

    if mode == ChecksumMode.GENERATE_SINGLE:
        # Show each hash in a dialog
        results = []
        for current, src in enumerate(sources, start=1):
            if should_cancel():
                break
            on_progress(current, total, src.name)
            try:
                hash_val = _hash_file(src, algorithm)
                results.append(ChecksumResult(path=src, algorithm=algorithm, hash_value=hash_val))
            except Exception as exc:
                errors.append(OperationError(path=src, message=str(exc)))

        # Show results
        if results:
            _show_hash_results(sources[0].fs, results)

    elif mode == ChecksumMode.GENERATE_SIDECAR:
        # Create .md5/.sha256 file for each source
        for current, src in enumerate(sources, start=1):
            if should_cancel():
                break
            on_progress(current, total, src.name)
            try:
                hash_val = _hash_file(src, algorithm)
                sidecar_path = src.parent / (src.name + algorithm.ext)
                _write_checksum_file(sidecar_path, [(hash_val, src.name)], algorithm)
            except Exception as exc:
                errors.append(OperationError(path=src, message=str(exc)))

    elif mode == ChecksumMode.GENERATE_SUM_FILE:
        # Create single SUM file with all hashes
        entries = []
        for current, src in enumerate(sources, start=1):
            if should_cancel():
                break
            on_progress(current, total, src.name)
            try:
                hash_val = _hash_file(src, algorithm)
                entries.append((hash_val, src.name))
            except Exception as exc:
                errors.append(OperationError(path=src, message=str(exc)))

        if entries and not errors:
            # Write SUM file to destination directory
            sum_name = f"{algorithm.hashlib_name.upper()}SUMS"
            sum_path = dest_dir / sum_name
            _write_checksum_file(sum_path, entries, algorithm, output_format)

    elif mode == ChecksumMode.VERIFY:
        # Verify files against checksum file
        if not checksum_file:
            errors.append(
                OperationError(
                    path=sources[0] if sources else dest_dir, message="No checksum file specified"
                )
            )
            return errors

        # Read expected hashes
        checksum_path = VfsPath(LocalFileSystem(), Path(checksum_file).parts)
        expected = _read_checksum_file(checksum_path, algorithm)

        if not expected:
            errors.append(
                OperationError(
                    path=checksum_path, message=f"No valid checksums found in {checksum_file}"
                )
            )
            return errors

        for current, src in enumerate(sources, start=1):
            if should_cancel():
                break
            on_progress(current, total, src.name)

            # Find matching entry in checksum file
            expected_hash = expected.get(src.name)
            if expected_hash is None:
                errors.append(
                    OperationError(
                        path=src, message=f"No checksum entry for '{src.name}' in checksum file"
                    )
                )
                continue

            try:
                actual_hash = _hash_file(src, algorithm)
                if actual_hash.lower() != expected_hash.lower():
                    msg = f"Checksum mismatch: expected {expected_hash}, got {actual_hash}"
                    errors.append(OperationError(path=src, message=msg))
            except Exception as exc:
                errors.append(OperationError(path=src, message=str(exc)))

    return errors


def _show_hash_results(fs: ReadableFileSystem, results: list[ChecksumResult]) -> None:
    """Show hash results in a dialog."""
    # Create a simple results window
    root = None
    # Find a root widget
    for widget in fs.__dict__.values():
        if hasattr(widget, "winfo_toplevel"):
            root = widget.winfo_toplevel()
            break

    if root is None:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()

    if root is None:
        return

    dialog = tk.Toplevel(root)
    dialog.title("Checksum Results")
    dialog.geometry("600x400")
    _center_over(dialog, root)

    columns = ("file", "algorithm", "hash", "status")
    tree = ttk.Treeview(dialog, columns=columns, show="headings")
    tree.heading("file", text="File")
    tree.heading("algorithm", text="Algorithm")
    tree.heading("hash", text="Hash")
    tree.heading("status", text="Status")
    tree.column("file", width=200)
    tree.column("algorithm", width=80)
    tree.column("hash", width=250)
    tree.column("status", width=80)

    tree.tag_configure("ok", foreground="green")
    tree.tag_configure("mismatch", foreground="red")
    tree.tag_configure("info", foreground="black")

    for result in results:
        if result.verified is True:
            status = "OK"
            tag = "ok"
        elif result.verified is False:
            status = "MISMATCH"
            tag = "mismatch"
        else:
            status = "—"
            tag = "info"
        tree.insert(
            "",
            "end",
            values=(result.path.name, result.algorithm.display, result.hash_value, status),
            tags=(tag,),
        )

    tree.pack(fill="both", expand=True, padx=8, pady=8)

    ttk.Button(dialog, text="Close", command=dialog.destroy).pack(pady=8)
    dialog.grab_set()
    dialog.wait_window()


OPERATIONS: list[FileOperation] = [
    FileOperation(
        name="Checksums",
        run=_run_checksum,
        prepare=_prepare_checksum,
        description="Generate or verify MD5/SHA1/SHA256/SHA512 checksums.",
    ),
]
