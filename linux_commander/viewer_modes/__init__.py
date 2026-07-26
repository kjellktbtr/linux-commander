"""Viewer mode plugin discovery and contract.

Each plugin module in this package exposes a ``mode_class`` attribute that is a
subclass of ``ViewerMode``.  Discovery uses ``pkgutil.iter_modules`` so broken
modules are silently skipped.

A viewer mode controls how file content is displayed in the TextWindow — hex
dump, JSON pretty-print, CSV table, strings scan, etc.  Modes in the same
``exclusive_group`` are mutually exclusive (only one can be active at a time).

Public API:
    ViewerMode          — ABC that mode plugins must subclass
    ViewerContext       — Protocol describing the minimal surface TextWindow exposes
    discover_modes()    — auto-discover all mode plugins in this package
"""

from __future__ import annotations

import importlib
import pkgutil
import tkinter as tk
from abc import ABC, abstractmethod
from tkinter import ttk
from typing import Protocol, runtime_checkable

from linux_commander.settings import Settings
from linux_commander.vfs import VfsPath

# ---------------------------------------------------------------------------
# Context protocol — the minimal surface TextWindow exposes to modes
# ---------------------------------------------------------------------------


@runtime_checkable
class ViewerContext(Protocol):
    """Minimal interface a viewer window must provide to a ViewerMode.

    Modes should *only* interact with the window through this protocol —
    never reach into TextWindow internals directly.
    """

    text_widget: tk.Text
    """The main text widget."""

    top: tk.Toplevel
    """The Toplevel window."""

    settings: Settings
    """Application settings."""

    path: VfsPath | None
    """Current file path, or None for a new buffer."""

    raw_text: str
    """Original file content (before any mode transformation)."""

    read_only: bool
    """Whether editing is disabled."""

    is_active: bool
    """Whether this particular mode is currently the active display mode."""

    csv_frame: tk.Frame
    """Frame that holds the CSV Treeview (shared infrastructure)."""

    csv_tree: ttk.Treeview
    """The CSV Treeview widget inside ``csv_frame``."""

    def clear_text(self) -> None:
        """Clear the text widget."""

    def insert_text(self, text: str) -> None:
        """Insert ``text`` at the beginning of the text widget."""

    def set_title_suffix(self, suffix: str) -> None:
        """Set the mode-specific title suffix (e.g. ' [Hex]')."""

    def apply_syntax_highlighting(self) -> None:
        """Re-apply syntax highlighting based on current file extension."""

    def clear_syntax_tags(self) -> None:
        """Remove all syntax-highlighting tags from the text widget."""

    def set_editable(self, enabled: bool) -> None:
        """Enable or disable text widget editing."""

    def show_error(self, title: str, message: str) -> None:
        """Show an error dialog."""

    def set_modified(self, modified: bool) -> None:
        """Mark the buffer as modified or unmodified."""

    def show_csv_area(self) -> None:
        """Show the CSV Treeview area, hide the text widget."""

    def show_text_area(self) -> None:
        """Show the text widget, hide the CSV Treeview area."""

    def deactivate_other_modes(self, group: str) -> None:
        """Deactivate all other modes in the same exclusive group."""

    def reactivate_group(self, group: str) -> None:
        """Re-enable menu items for modes in *group* after one is deactivated."""


# ---------------------------------------------------------------------------
# ViewerMode ABC
# ---------------------------------------------------------------------------


class ViewerMode(ABC):
    """Base class for viewer display modes.

    Subclasses implement enter/exit logic, menu contributions, and title
    suffixes.  Modes in the same ``exclusive_group`` are mutually exclusive.
    """

    name: str
    """Display name shown in menus and title bar."""

    exclusive_group: str
    """Group key — modes sharing a group cannot be active simultaneously.

    Example: ``"display"`` for hex/json/csv/strings so only one shows at a time.
    """

    @abstractmethod
    def can_activate(self, ctx: ViewerContext) -> bool:
        """Return True if this mode can be activated given the current context."""

    @abstractmethod
    def on_activate(self, ctx: ViewerContext) -> None:
        """Enter this mode — transform the text widget, update state."""

    @abstractmethod
    def on_deactivate(self, ctx: ViewerContext) -> None:
        """Leave this mode — restore original content, reset state."""

    @abstractmethod
    def build_menu(self, ctx: ViewerContext, menu: tk.Menu) -> None:
        """Add menu entries (checkbuttons, submenus) to the View menu.

        The mode is responsible for wiring its own Tk variables and callbacks.
        """

    def title_suffix(self, ctx: ViewerContext) -> str:
        """Return the title-bar suffix when this mode is active.

        Override to provide a custom suffix; default returns ``" [<name>]"``.
        """
        return f" [{self.name}]"


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

_mode_cache: list[type[ViewerMode]] | None = None


def discover_modes() -> list[type[ViewerMode]]:
    """Auto-discover all ViewerMode classes in this package.

    Scans ``linux_commander.viewer_modes`` for modules that expose a
    ``mode_class`` attribute (a ``ViewerMode`` subclass).  Returns the
    classes so each ``TextWindow`` can instantiate its own copies with
    independent Tk state.  Broken modules are silently skipped.

    Results are cached after the first call.
    """
    global _mode_cache
    if _mode_cache is not None:
        return _mode_cache

    modes: list[type[ViewerMode]] = []
    for info in pkgutil.iter_modules(__path__):  # type: ignore[name-defined]
        mod_name = info.name
        # Skip __init__ itself
        if mod_name == "__init__":
            continue
        try:
            mod = importlib.import_module(f"{__name__}.{mod_name}")
        except Exception:
            continue  # broken plugin — skip silently

        cls = getattr(mod, "mode_class", None)
        if cls is None:
            continue
        if not isinstance(cls, type) or not issubclass(cls, ViewerMode):
            continue
        modes.append(cls)

    _mode_cache = modes
    return modes


def reset_mode_cache() -> None:
    """Clear the discovered-modes cache.  Useful for tests."""
    global _mode_cache
    _mode_cache = None
