"""Threaded search controller extracted from app.py.

Manages the lifecycle of a background file search: starting the worker
thread, routing results back to the panel in small batches via
``app.after()``, and keeping the panel header/status bar up to date.

Public API:
    class SearchController
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from linux_commander.search_engine import SearchCriteria, SearchResult, search_files
from linux_commander.vfs import FileEntry

if TYPE_CHECKING:
    from linux_commander.panel import FilePanel

# How often the Tk thread drains queued results and refreshes the status
# bar/header, in milliseconds. A few times a second is enough to feel live
# without flooding the Tk event loop with a callback per result.
_POLL_INTERVAL_MS = 300


class SearchController:
    """Drives a single background search run.

    Args:
        app: The root ``CommanderApp`` (a ``tk.Tk`` subclass).  Used for
            ``app.after()`` to marshal callbacks onto the Tk thread, and
            for ``app.status_var`` to update the status bar.
        panel: The ``FilePanel`` that will receive the results.
    """

    def __init__(self, app: Any, panel: FilePanel) -> None:
        self._app = app
        self._panel = panel
        self._cancelled = threading.Event()
        self._done = threading.Event()
        self._thread: threading.Thread | None = None
        self._results_queue: queue.Queue[FileEntry] = queue.Queue()
        self._found_count = 0
        self._scanned_count = 0
        self._summary = ""

    def start(
        self,
        criteria: SearchCriteria,
        summary: str,
        on_done: Callable[[], None],
    ) -> None:
        """Enter search mode on the panel and start the background search."""
        self._cancelled.clear()
        self._done.clear()
        self._found_count = 0
        self._scanned_count = 0
        self._summary = summary
        self._panel.enter_search_mode(summary)
        self._app.status_var.set("Searching...")

        self._thread = threading.Thread(
            target=self._worker,
            args=(criteria,),
            daemon=True,
        )
        self._thread.start()
        self._poll(on_done)

    def cancel(self) -> None:
        """Signal the background worker to stop."""
        self._cancelled.set()

    def _worker(self, criteria: SearchCriteria) -> None:
        """Background worker — runs on a daemon thread.

        Only touches thread-safe primitives (the results queue and plain
        int counters); all Tk/widget access happens on the poll loop below.
        """

        def on_found(result: SearchResult) -> None:
            self._found_count += 1
            self._results_queue.put(result.entry)

        def on_progress(current: int, total: int, name: str) -> None:
            self._scanned_count = current

        def should_cancel() -> bool:
            return self._cancelled.is_set()

        search_files(criteria, on_found, lambda f, s: None, should_cancel, on_progress)

        self._done.set()

    def _drain(self) -> None:
        """Flush any queued results into the panel in one batch insert."""
        batch: list[FileEntry] = []
        while True:
            try:
                batch.append(self._results_queue.get_nowait())
            except queue.Empty:
                break
        if batch:
            self._panel.add_search_results(batch)

    def _poll(self, on_done: Callable[[], None]) -> None:
        """Tk-thread poll: drain queued results and refresh status/header."""
        self._drain()
        is_done = self._done.is_set()

        if is_done:
            self._app.status_var.set(
                f"Search complete: {self._found_count} found, {self._scanned_count} scanned"
            )
        else:
            self._app.status_var.set(
                f"Searching... {self._found_count} found, {self._scanned_count} scanned"
            )

        count = self._panel.get_search_result_count()
        current_text = self._panel._path_var.get()
        if current_text.startswith("Search:") and "(Esc" in current_text:
            self._panel._path_var.set(f"Search: {self._summary}  [{count} results]  (Esc to exit)")

        if is_done:
            on_done()
        else:
            self._app.after(_POLL_INTERVAL_MS, lambda: self._poll(on_done))
