"""Threaded progress dialog for long-running file operations.

Extracted from dialogs.py so that the core dialog helpers stay small
and ProgressDialog / run_with_progress can be imported independently.

Public API:
    class ProgressDialog
    WorkFunc = Callable[[ProgressCallback, CancelPredicate], list[OperationError]]
    run_with_progress(parent, title, work) -> list[OperationError]
"""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from linux_commander.operations import CancelPredicate, OperationError, ProgressCallback


def _center_over(top: tk.Toplevel, parent: tk.Misc) -> None:
    """Centre ``top`` over ``parent``."""
    top.update_idletasks()
    try:
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
    except tk.TclError:
        return
    w, h = top.winfo_width(), top.winfo_height()
    x = max(px + (pw - w) // 2, 0)
    y = max(py + (ph - h) // 2, 0)
    top.geometry(f"+{x}+{y}")


def _format_eta(seconds: float) -> str:
    if seconds >= 3600:
        return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m"
    if seconds >= 60:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    return f"{int(seconds)}s"


def _format_elapsed(seconds: float) -> str:
    if seconds >= 3600:
        return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m"
    if seconds >= 60:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    return f"{int(seconds)}s"


def _format_speed(bytes_per_sec: float) -> str:
    if bytes_per_sec >= 1024 * 1024:
        return f"{bytes_per_sec / (1024 * 1024):.1f} MB/s"
    if bytes_per_sec >= 1024:
        return f"{bytes_per_sec / 1024:.1f} KB/s"
    return f"{bytes_per_sec:.0f} B/s"


class ProgressDialog:
    """A non-modal progress dialog with a cancel button.

    Intended to be driven from the main thread only (via `update`), while a
    background worker thread does the actual file I/O and reports progress
    through a thread-safe channel — see `run_with_progress`.

    Deliberately does **not** grab input focus: the user can keep browsing
    panels, open other dialogs, or start another operation (F5/F6/F7/F8/
    Shift+F5 all funnel through `run_with_progress`, and Tk callbacks nest
    reentrantly, so a second operation gets its own independent worker
    thread and dialog) while this one runs in the background. Nothing here
    serializes two concurrent operations that happen to write to the same
    destination -- an accepted limitation, not a bug.

    Supports both file-count and byte-count based progress reporting.
    Shows transfer speed, ETA, and elapsed time.
    """

    def __init__(self, parent: tk.Misc, title: str = "Working...") -> None:
        self._cancelled = False
        self._start_time = time.time()
        self._last_update_time = self._start_time
        self._last_bytes = 0
        self._total_bytes = 0
        self._bytes_mode = False

        self.top = tk.Toplevel(parent)
        self.top.title(title)
        self.top.transient(parent)  # type: ignore[call-overload]
        self.top.resizable(False, False)
        self.top.protocol("WM_DELETE_WINDOW", self.cancel)

        # Current file name
        self._name_var = tk.StringVar(value="")
        ttk.Label(self.top, textvariable=self._name_var, width=50, anchor="w").pack(
            padx=16, pady=(16, 2), fill="x"
        )

        # Main progress bar (files or bytes)
        self._progress = ttk.Progressbar(self.top, mode="determinate", length=400)
        self._progress.pack(padx=16, pady=2, fill="x")

        # Sub/byte-progress bar (hidden until used)
        self._sub_progress = ttk.Progressbar(self.top, mode="determinate", length=400)
        self._sub_pack_info: dict[str, int] | None = None

        # Progress info: percentage, speed, ETA, elapsed
        self._info_var = tk.StringVar(value="")
        ttk.Label(self.top, textvariable=self._info_var, width=60, anchor="w").pack(
            padx=16, pady=(0, 4), fill="x"
        )

        # Cancel button
        ttk.Button(self.top, text="Cancel", command=self.cancel).pack(pady=(4, 16))

        _center_over(self.top, parent)

    def set_bytes_mode(self, total_bytes: int) -> None:
        """Switch to byte-based progress tracking."""
        self._bytes_mode = True
        self._total_bytes = total_bytes
        self._progress["maximum"] = max(total_bytes, 1)

    def _show_sub_bar(self) -> None:
        """Pack the sub-progress bar if not already visible."""
        if self._sub_pack_info is None:
            self._sub_progress.pack(padx=16, pady=0, fill="x", before=self._progress)
            # Store as non-None sentinel (the actual geometry is handled by tk)
            self._sub_pack_info = {}

    def update(
        self,
        current: int,
        total: int,
        name: str,
        bytes_transferred: int | None = None,
        sub_bytes_total: int | None = None,
    ) -> None:
        """Reflect progress; must be called from the main (Tk) thread.

        Args:
            current: Current item index (1-based) or bytes transferred.
            total: Total items or total bytes.
            name: Name of current file/operation.
            bytes_transferred: Optional bytes transferred so far (for speed/ETA calc
                and sub-progress bar value).
            sub_bytes_total: Optional total bytes for the current sub-operation.
        """
        now = time.time()
        self._name_var.set(name)

        if self._bytes_mode and bytes_transferred is not None:
            self._update_bytes_mode(now, bytes_transferred)
        else:
            self._update_file_mode(now, current, total, bytes_transferred, sub_bytes_total)

        self.top.update_idletasks()

    def _update_bytes_mode(self, now: float, bytes_transferred: int) -> None:
        """Byte-based progress: one bar showing overall transfer."""
        self._progress["maximum"] = max(self._total_bytes, 1)
        self._progress["value"] = bytes_transferred

        elapsed = self._elapsed_since_last_update(now)
        if elapsed > 0 and bytes_transferred > self._last_bytes:
            speed = (bytes_transferred - self._last_bytes) / elapsed
            self._last_bytes = bytes_transferred
            self._last_update_time = now

            remaining = self._total_bytes - bytes_transferred
            eta_str = _format_eta(remaining / speed) if speed > 0 else "calculating..."
            pct = (bytes_transferred / self._total_bytes * 100) if self._total_bytes > 0 else 0
            self._info_var.set(
                f"{pct:.1f}%  |  {_format_speed(speed)}  |  "
                f"ETA: {eta_str}  |  Elapsed: {_format_elapsed(now - self._start_time)}"
            )
        else:
            pct = (bytes_transferred / self._total_bytes * 100) if self._total_bytes > 0 else 0
            self._info_var.set(f"{pct:.1f}%")

    def _update_file_mode(
        self,
        now: float,
        current: int,
        total: int,
        bytes_done: int | None,
        sub_total: int | None,
    ) -> None:
        """File-count progress, optional per-file sub-bar."""
        self._progress["maximum"] = max(total, 1)
        self._progress["value"] = current

        # Sub-bar
        if sub_total is not None and sub_total > 0 and bytes_done is not None:
            self._show_sub_bar()
            self._sub_progress["maximum"] = sub_total
            self._sub_progress["value"] = bytes_done

            # Speed from sub-progress
            elapsed = self._elapsed_since_last_update(now)
            if elapsed > 0 and bytes_done > self._last_bytes:
                speed = (bytes_done - self._last_bytes) / elapsed
                self._last_bytes = bytes_done
                self._last_update_time = now
                speed_str = _format_speed(speed)
            else:
                speed_str = ""
        else:
            # Hide sub-bar when not in use
            self._hide_sub_bar()
            # Estimate speed from file completion rate
            elapsed_total = now - self._start_time
            if current > 0 and elapsed_total > 0:
                rate = current / elapsed_total
                speed_str = f"{rate:.1f} files/s"
            else:
                speed_str = ""

        pct = (current / total * 100) if total > 0 else 0
        elapsed_str = _format_elapsed(now - self._start_time)

        parts = [f"{pct:.1f}% ({current}/{total} files)", f"Elapsed: {elapsed_str}"]
        if speed_str:
            # Estimate ETA from file rate
            elapsed_total = now - self._start_time
            remaining_files = total - current
            if current > 0 and elapsed_total > 0 and remaining_files > 0:
                rate = current / elapsed_total
                eta_sec = remaining_files / rate
                parts.insert(1, f"ETA: {_format_eta(eta_sec)}")
            parts.insert(1, speed_str)
        self._info_var.set("  |  ".join(parts))

    def _hide_sub_bar(self) -> None:
        if self._sub_pack_info is not None:
            self._sub_progress.pack_forget()
            self._sub_pack_info = None

    def _elapsed_since_last_update(self, now: float) -> float:
        """Return seconds since the last ``_last_update_time`` reset."""
        return now - self._last_update_time

    def cancel(self) -> None:
        self._cancelled = True

    def should_cancel(self) -> bool:
        return self._cancelled

    def close(self) -> None:
        self.top.destroy()


WorkFunc = Callable[[ProgressCallback, CancelPredicate], list[OperationError]]


def run_with_progress(parent: tk.Misc, title: str, work: WorkFunc) -> list[OperationError]:
    """Run `work` on a background thread while showing a non-modal `ProgressDialog`.

    `work` is called as `work(on_progress, should_cancel)` on the worker
    thread — designed to wrap a call to `operations.copy_entries`,
    `move_entries`, or `delete_entries` with their fixed arguments already
    bound (e.g. via a small closure).

    Blocks the *calling function* (via `wait_window`) until its own worker
    finishes or is cancelled, then returns the collected `OperationError`s --
    but since the dialog no longer grabs input, `wait_window` keeps pumping
    Tk's event loop the whole time, so the rest of the app stays responsive.
    If the user triggers another F5/F6/F7/F8/Shift+F5 while this one is
    still running, that reentrant call gets its own thread, dialog, and
    nested `wait_window` -- both operations proceed concurrently regardless
    of which `run_with_progress` call returns first.
    """
    dialog = ProgressDialog(parent, title=title)
    progress_queue: queue.Queue[tuple[int, int, str, int | None, int | None] | None] = queue.Queue()
    result: dict[str, list[OperationError]] = {"errors": []}
    cancelled = threading.Event()

    def on_progress(
        current: int,
        total: int,
        name: str,
        bytes_done: int | None = None,
        sub_total: int | None = None,
    ) -> None:
        progress_queue.put((current, total, name, bytes_done, sub_total))

    def should_cancel() -> bool:
        return cancelled.is_set()

    def worker() -> None:
        result["errors"] = work(on_progress, should_cancel)
        progress_queue.put(None)  # sentinel: worker finished

    thread = threading.Thread(target=worker, daemon=True)

    def poll() -> None:
        if dialog.should_cancel():
            cancelled.set()
        try:
            while True:
                item = progress_queue.get_nowait()
                if item is None:
                    dialog.close()
                    return
                current, total, name, bytes_done, sub_total = item
                if bytes_done is not None or sub_total is not None:
                    dialog.update(current, total, name, bytes_done, sub_total)
                else:
                    dialog.update(current, total, name)
        except queue.Empty:
            pass
        parent.after(50, poll)

    thread.start()
    parent.after(50, poll)
    dialog.top.wait_window()
    thread.join(timeout=2.0)
    return result["errors"]
