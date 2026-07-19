"""F-key table shared by the F-key bar buttons and the global keyboard bindings."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FKeySpec:
    """Describes one function-key slot in the bottom key bar.

    `key` is the Tk keysym (e.g. "F1"); `label` is the short caption shown on
    the key-bar button; `handler_name` is the name of the `CommanderApp`
    method invoked when the key is pressed or the button is clicked.
    """

    key: str
    label: str
    handler_name: str


F_KEY_SPECS: tuple[FKeySpec | None, ...] = (
    FKeySpec("F1", "Help", "cmd_help"),
    None,  # F2 — unused in v1 (classic NC "User menu"); reserved slot.
    FKeySpec("F3", "View", "cmd_view"),
    FKeySpec("F4", "Edit", "cmd_edit"),
    FKeySpec("F5", "Copy", "cmd_copy"),
    FKeySpec("F6", "Move", "cmd_move"),
    FKeySpec("F7", "MkDir", "cmd_mkdir"),
    FKeySpec("F8", "Delete", "cmd_delete"),
    FKeySpec("F9", "Menu", "cmd_menu"),
    FKeySpec("F10", "Quit", "cmd_quit"),
)
"""Fixed 10-slot F-key bar, in order F1..F10. `None` marks an unused slot."""
