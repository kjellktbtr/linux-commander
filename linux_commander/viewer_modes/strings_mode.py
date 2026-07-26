"""Strings scan viewer mode.

Scans a binary file in the background for printable ASCII strings and
displays them incrementally in the text widget.  Supports configurable
minimum string length.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk

from linux_commander.viewer import (
    _STRINGS_POLL_MS,
    _strings_worker,
)
from linux_commander.viewer_modes import ViewerContext, ViewerMode


class StringsMode(ViewerMode):
    """Strings scan display mode."""

    name = "Strings"
    exclusive_group = "display"

    # Tk state owned by this mode
    _var: tk.BooleanVar | None = None
    _min_len_var: tk.IntVar | None = None
    _min_len: int = 4

    # Background scan state
    _cancel_event: threading.Event | None = None
    _queue: queue.Queue[str | None] | None = None

    # ------------------------------------------------------------------
    # ViewerMode interface
    # ------------------------------------------------------------------

    def can_activate(self, ctx: ViewerContext) -> bool:
        return ctx.path is not None

    def on_activate(self, ctx: ViewerContext) -> None:
        if ctx.path is None:
            ctx.show_error("Strings", "There is no file on disk to scan.")
            if self._var is not None:
                self._var.set(False)
            return

        ctx.clear_text()
        ctx.clear_syntax_tags()
        ctx.set_editable(False)
        ctx.set_title_suffix(self.title_suffix(ctx))
        self._start_scan(ctx)

    def on_deactivate(self, ctx: ViewerContext) -> None:
        self._cancel_scan()
        ctx.clear_text()
        ctx.insert_text(ctx.raw_text)
        ctx.set_editable(not ctx.read_only)
        ctx.set_title_suffix("")
        ctx.apply_syntax_highlighting()

    def build_menu(self, ctx: ViewerContext, menu: tk.Menu) -> None:
        self._var = tk.BooleanVar(value=False)
        menu.add_checkbutton(
            label="Strings",
            variable=self._var,
            command=lambda: self._toggle(ctx),
            underline=0,
        )

        # Min Length submenu
        len_menu = tk.Menu(menu, tearoff=0)
        menu.add_cascade(label="Strings Min Length", menu=len_menu, underline=8)

        self._min_len_var = tk.IntVar(value=self._min_len)
        for length in (3, 4, 6, 8, 16):
            len_menu.add_radiobutton(
                label=str(length),
                value=length,
                variable=self._min_len_var,
                command=lambda: self._on_min_len_pick(ctx),
            )

    def title_suffix(self, ctx: ViewerContext) -> str:
        return " [Strings]"

    # ------------------------------------------------------------------
    # Toggle handler
    # ------------------------------------------------------------------

    def _toggle(self, ctx: ViewerContext) -> None:
        if self._var is None:
            return
        entering = self._var.get()
        if entering:
            if not self.can_activate(ctx):
                ctx.show_error("Strings", "There is no file on disk to scan.")
                self._var.set(False)
                return
            ctx.deactivate_other_modes(self.exclusive_group)
            self.on_activate(ctx)
        else:
            self.on_deactivate(ctx)
            ctx.reactivate_group(self.exclusive_group)

    def _on_min_len_pick(self, ctx: ViewerContext) -> None:
        if self._min_len_var is None:
            return
        self._min_len = self._min_len_var.get()
        if self._var is not None and self._var.get():
            self._cancel_scan()
            ctx.clear_text()
            self._start_scan(ctx)

    # ------------------------------------------------------------------
    # Background scan
    # ------------------------------------------------------------------

    def _start_scan(self, ctx: ViewerContext) -> None:
        assert ctx.path is not None
        self._cancel_event = threading.Event()
        q: queue.Queue[str | None] = queue.Queue()
        self._queue = q

        thread = threading.Thread(
            target=_strings_worker,
            args=(ctx.path, self._min_len, q, self._cancel_event),
            daemon=True,
        )
        thread.start()
        ctx.top.after(_STRINGS_POLL_MS, lambda: self._poll(ctx, q))

    def _cancel_scan(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()
        self._queue = None

    def _poll(self, ctx: ViewerContext, q: queue.Queue[str | None]) -> None:
        if self._var is None or not self._var.get():
            return
        if self._queue is not q:
            return

        batch: list[str] = []
        done = False
        try:
            while True:
                item = q.get_nowait()
                if item is None:
                    done = True
                    break
                batch.append(item)
        except queue.Empty:
            pass

        if batch:
            ctx.text_widget.configure(state="normal")
            ctx.text_widget.insert("end", "\n".join(batch) + "\n")
            ctx.text_widget.configure(state="disabled")

        if not done:
            ctx.top.after(_STRINGS_POLL_MS, lambda: self._poll(ctx, q))


mode_class = StringsMode
