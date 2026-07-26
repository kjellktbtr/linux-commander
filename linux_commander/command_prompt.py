"""Command prompt widget for the CommanderApp.

Builds the command entry bar at the bottom of the window.  Supports
command history navigation (Up/Down), execution on Enter, and Escape
to clear and return focus to the panel.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

# Callback types
ExecuteCallback = Callable[[str], None]
FocusCallback = Callable[[], None]


class CommandPrompt:
    """Command entry bar widget.

    Builds a ``ttk.Frame`` containing a prompt label and an entry field.
    Manages command history and key bindings.
    """

    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_execute: ExecuteCallback,
        on_focus_return: FocusCallback,
    ) -> None:
        self._parent = parent
        self._on_execute = on_execute
        self._on_focus_return = on_focus_return

        self._frame = ttk.Frame(parent)
        self._frame.grid(row=2, column=0, columnspan=2, sticky="ew")
        self._frame.columnconfigure(1, weight=1)

        # Prompt label
        self._prompt_var = tk.StringVar(value="$")
        prompt_label = ttk.Label(
            self._frame,
            textvariable=self._prompt_var,
            anchor="w",
            padding=(4, 2),
            style="CmdPrompt.TLabel",
        )
        prompt_label.grid(row=0, column=0, sticky="w")

        # Command entry
        self._cmd_var = tk.StringVar()
        self._entry = ttk.Entry(self._frame, textvariable=self._cmd_var, style="Cmd.TEntry")
        self._entry.grid(row=0, column=1, sticky="ew", padx=(0, 4), pady=2)

        # Command history
        self._history: list[str] = []
        self._history_index: int = -1

        # Bind keys
        self._entry.bind("<Return>", self._on_enter)
        self._entry.bind("<KP_Enter>", self._on_enter)
        self._entry.bind("<Up>", self._on_history_up)
        self._entry.bind("<Down>", self._on_history_down)
        self._entry.bind("<Escape>", self._on_escape)

    @property
    def frame(self) -> ttk.Frame:
        """The underlying ``ttk.Frame``."""
        return self._frame

    @property
    def entry(self) -> ttk.Entry:
        """The entry widget (for focus management)."""
        return self._entry

    def focus_and_set(self, text: str) -> None:
        """Focus the entry and set its text."""
        self._entry.focus_set()
        self._cmd_var.set(text)
        self._entry.icursor("end")

    def focus_and_clear(self) -> None:
        """Focus the entry, clear its text, and reset history index."""
        self._entry.focus_set()
        self._cmd_var.set("")
        self._history_index = len(self._history)

    def _on_enter(self, event: tk.Event) -> str:
        cmd = self._cmd_var.get().strip()
        if not cmd:
            self._on_focus_return()
            return "break"

        # Add to history
        if self._history and self._history[-1] == cmd:
            self._history.pop()
        self._history.append(cmd)
        self._history_index = len(self._history)

        # Clear entry and return focus to panel
        self._cmd_var.set("")
        self._on_focus_return()

        # Execute command
        self._on_execute(cmd)
        return "break"

    def _on_escape(self, event: tk.Event) -> str:
        self._cmd_var.set("")
        self._on_focus_return()
        return "break"

    def _on_history_up(self, event: tk.Event) -> str:
        if not self._history:
            return "break"
        if self._history_index > 0:
            self._history_index -= 1
            self._cmd_var.set(self._history[self._history_index])
        return "break"

    def _on_history_down(self, event: tk.Event) -> str:
        if not self._history or self._history_index >= len(self._history) - 1:
            self._cmd_var.set("")
            self._history_index = len(self._history)
            return "break"
        self._history_index += 1
        self._cmd_var.set(self._history[self._history_index])
        return "break"
