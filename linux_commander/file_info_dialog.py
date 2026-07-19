"""File Info dialog (Shift+F3): file type + checksums for the cursor file.

On Linux/macOS, runs the ``file`` command to describe the file's type. On
Windows (no ``file`` command), falls back to what Python itself can
determine (a ``mimetypes`` guess). Either way, MD5/SHA1/SHA256 checksums and
extended POSIX metadata (permissions, owner, ...) are gathered on a
background thread so a large or remote (FTP/archive) file never blocks the
UI; the window opens immediately and fills in each section as it completes.

Public API:
    show_file_info(parent, path, size, mtime) -> None
"""

from __future__ import annotations

import hashlib
import mimetypes
import queue
import stat as stat_module
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk

from linux_commander.dialogs import _center_over
from linux_commander.fs import format_mtime, format_size
from linux_commander.plugins import cleanup_temp, materialize
from linux_commander.vfs import VfsPath

_HASH_NAMES = ("md5", "sha1", "sha256")
_CHUNK_SIZE = 1024 * 1024  # 1 MiB

_MsgKind = str  # queue message kinds: "filetype", "posix", "hash_total", "hash_progress",
#                 "hashes", "error", "done"


def _extended_posix_info(real_path_str: str) -> list[tuple[str, str]]:
    """Return (label, value) pairs from a real OS stat, when available.

    Owner/group are omitted if ``pwd``/``grp`` aren't importable (Windows).
    """
    import os

    try:
        st = os.stat(real_path_str)
    except OSError as exc:
        return [("Stat error", str(exc))]

    rows: list[tuple[str, str]] = [("Permissions", stat_module.filemode(st.st_mode))]
    try:
        import grp
        import pwd

        owner = pwd.getpwuid(st.st_uid).pw_name
        group = grp.getgrgid(st.st_gid).gr_name
        rows.append(("Owner", f"{owner} ({st.st_uid})"))
        rows.append(("Group", f"{group} ({st.st_gid})"))
    except (ImportError, KeyError):
        pass  # Windows, or uid/gid with no passwd/group entry
    rows.append(("Links", str(st.st_nlink)))
    return rows


def _describe_file_type(real_path_str: str, name: str) -> str:
    """Run ``file`` on Linux/macOS; fall back to a ``mimetypes`` guess elsewhere."""
    if sys.platform.startswith("linux") or sys.platform == "darwin":
        try:
            proc = subprocess.run(
                ["file", "-b", real_path_str],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    guessed, encoding = mimetypes.guess_type(name)
    if guessed:
        suffix = f" ({encoding})" if encoding else ""
        return f"{guessed}{suffix} [Python guess; 'file' unavailable]"
    return "Unknown [Python guess; 'file' unavailable]"


def _compute(path: VfsPath, name: str, out: queue.Queue[tuple[_MsgKind, object]]) -> None:
    """Background worker: materialize, describe type, gather stat, hash.

    Runs off the Tk thread; every result is handed back through ``out``.
    """
    try:
        real_path = materialize(path.fs, path)
    except OSError as exc:
        out.put(("error", f"Could not read file: {exc}"))
        out.put(("done", None))
        return

    try:
        real_path_str = str(real_path)
        out.put(("filetype", _describe_file_type(real_path_str, name)))
        out.put(("posix", _extended_posix_info(real_path_str)))

        total = real_path.stat().st_size
        out.put(("hash_total", total))
        hashers = {alg: hashlib.new(alg) for alg in _HASH_NAMES}
        done = 0
        with open(real_path, "rb") as f:
            while chunk := f.read(_CHUNK_SIZE):
                for h in hashers.values():
                    h.update(chunk)
                done += len(chunk)
                out.put(("hash_progress", done))
        out.put(("hashes", {alg: h.hexdigest() for alg, h in hashers.items()}))
    except OSError as exc:
        out.put(("error", f"Error reading file: {exc}"))
    finally:
        cleanup_temp(real_path)
        out.put(("done", None))


class FileInfoDialog:
    """Non-modal window showing file-type, checksum, and metadata info."""

    def __init__(self, parent: tk.Misc, path: VfsPath, size: int, mtime: float) -> None:
        self.top = tk.Toplevel(parent)
        self.top.title(f"File Info — {path.name}")
        self.top.transient(parent)  # type: ignore[call-overload]
        self.top.resizable(False, False)
        self.top.protocol("WM_DELETE_WINDOW", self._on_close)

        self._queue: queue.Queue[tuple[_MsgKind, object]] = queue.Queue()
        self._hash_vars: dict[str, tk.StringVar] = {}
        self._closed = False

        main = ttk.Frame(self.top, padding=16)
        main.pack(fill="both", expand=True)

        info_frame = ttk.Frame(main)
        info_frame.pack(fill="x")
        self._add_row(info_frame, "Name:", path.name)
        self._add_row(info_frame, "Path:", str(path))
        self._add_row(info_frame, "Size:", f"{format_size(size)} ({size} bytes)")
        self._add_row(info_frame, "Modified:", format_mtime(mtime))

        ttk.Separator(main, orient="horizontal").pack(fill="x", pady=8)

        self._filetype_var = tk.StringVar(value="Calculating…")
        filetype_frame = ttk.Frame(main)
        filetype_frame.pack(fill="x")
        self._add_row(filetype_frame, "File type:", None, self._filetype_var)

        # POSIX metadata rows are appended here once the background thread
        # reports them (empty until then, e.g. for Windows/archive sources).
        self._posix_frame = ttk.Frame(main)
        self._posix_frame.pack(fill="x")

        ttk.Separator(main, orient="horizontal").pack(fill="x", pady=8)

        self._progress = ttk.Progressbar(main, mode="determinate", length=360)
        self._progress.pack(fill="x", pady=(0, 8))

        hash_frame = ttk.Frame(main)
        hash_frame.pack(fill="x")
        for alg in _HASH_NAMES:
            var = tk.StringVar(value="Calculating…")
            self._hash_vars[alg] = var
            row = ttk.Frame(hash_frame)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=f"{alg.upper()}:", width=8, anchor="w").pack(side="left")
            ttk.Entry(row, textvariable=var, width=48, state="readonly").pack(
                side="left", fill="x", expand=True
            )

        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill="x", pady=(12, 0))
        ttk.Button(btn_frame, text="Close", command=self._on_close).pack(side="right")

        self.top.bind("<Escape>", lambda e: self._on_close())
        _center_over(self.top, parent)

        self._thread = threading.Thread(
            target=_compute, args=(path, path.name, self._queue), daemon=True
        )
        self._thread.start()
        self.top.after(50, self._poll)

    def _add_row(
        self,
        parent: ttk.Frame,
        label: str,
        value: str | None,
        var: tk.StringVar | None = None,
    ) -> None:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text=label, width=12, anchor="w").pack(side="left")
        if var is not None:
            ttk.Label(row, textvariable=var, anchor="w", wraplength=420).pack(
                side="left", fill="x", expand=True
            )
        else:
            ttk.Label(row, text=value or "", anchor="w", wraplength=420).pack(
                side="left", fill="x", expand=True
            )

    def _poll(self) -> None:
        if self._closed:
            return
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                self._handle_message(kind, payload)
        except queue.Empty:
            pass
        if not self._closed:
            self.top.after(50, self._poll)

    def _handle_message(self, kind: _MsgKind, payload: object) -> None:
        if kind == "filetype":
            self._filetype_var.set(str(payload))
        elif kind == "posix":
            rows = payload if isinstance(payload, list) else []
            for label, value in rows:  # type: ignore[misc]
                self._add_row(self._posix_frame, f"{label}:", value)
        elif kind == "hash_total":
            total = int(payload)  # type: ignore[call-overload]
            self._progress["maximum"] = max(total, 1)
        elif kind == "hash_progress":
            self._progress["value"] = int(payload)  # type: ignore[call-overload]
        elif kind == "hashes":
            hashes = payload if isinstance(payload, dict) else {}
            for alg, value in hashes.items():
                if alg in self._hash_vars:
                    self._hash_vars[alg].set(value)
        elif kind == "error":
            for var in self._hash_vars.values():
                var.set(str(payload))

    def _on_close(self) -> None:
        self._closed = True
        self.top.destroy()


def show_file_info(parent: tk.Misc, path: VfsPath, size: int, mtime: float) -> None:
    """Open a non-modal File Info window for ``path`` (Shift+F3)."""
    FileInfoDialog(parent, path, size, mtime)
