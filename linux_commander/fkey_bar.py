"""F-key bar widget for the CommanderApp.

Builds the row of F-key buttons at the bottom of the window.  Each button
displays ``F<n> Label`` and calls the dispatch callback when pressed.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Protocol

from linux_commander.keys import FKeySpec


class _DispatchProto(Protocol):
    def __call__(self, spec: FKeySpec) -> None: ...


class FKeyBar:
    """F-key button row widget.

    Builds a ``ttk.Frame`` containing one button per F-key spec.  Empty
    slots (``None`` in the spec list) render as invisible spacers.
    """

    def __init__(
        self,
        parent: tk.Misc,
        specs: tuple[FKeySpec | None, ...] | list[FKeySpec | None],
        dispatch: _DispatchProto,
    ) -> None:
        self._frame = ttk.Frame(parent)
        self._frame.grid(row=3, column=0, columnspan=2, sticky="ew")

        for index, spec in enumerate(specs):
            self._frame.columnconfigure(index, weight=1)
            if spec is None:
                ttk.Label(self._frame, text="").grid(row=0, column=index, sticky="ew")
                continue
            text = f"{spec.key} {spec.label}"
            button = ttk.Button(
                self._frame,
                text=text,
                style="FKey.TButton",
                command=lambda s=spec: dispatch(s),  # type: ignore[misc]
            )
            button.grid(row=0, column=index, sticky="ew", padx=2, pady=2)

    @property
    def frame(self) -> ttk.Frame:
        """The underlying ``ttk.Frame``."""
        return self._frame
