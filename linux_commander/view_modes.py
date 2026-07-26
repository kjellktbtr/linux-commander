"""View mode strategies extracted from TextWindow (SRP).

Each view mode encapsulates its own rendering logic, toggle behavior,
and whether it blocks editing. Composed with TextWindow rather than
inheriting from it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class ViewMode(ABC):
    """Base class for viewer display modes.

    Each mode controls how content is rendered, whether it can be toggled,
    and whether it forces read-only.
    """

    name: str

    @property
    @abstractmethod
    def is_active(self) -> bool:
        """Whether this mode is currently on."""

    @abstractmethod
    def toggle(self, window: object) -> None:
        """Enter or exit this mode on *window*."""

    @abstractmethod
    def render(self, window: object) -> None:
        """Render content in this mode."""

    @property
    @abstractmethod
    def blocks_edit(self) -> bool:
        """Whether this mode forces read-only."""


class CsvMode(ViewMode):
    """CSV/table view mode."""

    name = "CSV"

    def __init__(self) -> None:
        self._active = False

    @property
    def is_active(self) -> bool:
        return self._active

    def toggle(self, window: object) -> None:
        self._active = not self._active
        window._apply_view_mode()  # type: ignore[attr-defined]

    def render(self, window: object) -> None:
        if self._active:
            window._render_table()  # type: ignore[attr-defined]

    @property
    def blocks_edit(self) -> bool:
        return False


class HexMode(ViewMode):
    """Hex dump view mode."""

    name = "Hex"

    def __init__(self) -> None:
        self._active = False

    @property
    def is_active(self) -> bool:
        return self._active

    def toggle(self, window: object) -> None:
        self._active = not self._active
        window._apply_view_mode()  # type: ignore[attr-defined]

    def render(self, window: object) -> None:
        if self._active:
            window._render_hex()  # type: ignore[attr-defined]

    @property
    def blocks_edit(self) -> bool:
        return True


class StringsMode(ViewMode):
    """Strings scan view mode."""

    name = "Strings"

    def __init__(self) -> None:
        self._active = False

    @property
    def is_active(self) -> bool:
        return self._active

    def toggle(self, window: object) -> None:
        self._active = not self._active
        if self._active:
            window._start_strings_scan()  # type: ignore[attr-defined]
        else:
            window._stop_strings_scan()  # type: ignore[attr-defined]

    def render(self, window: object) -> None:
        if self._active:
            window._render_strings()  # type: ignore[attr-defined]

    @property
    def blocks_edit(self) -> bool:
        return True


class JsonMode(ViewMode):
    """JSON pretty-print view mode."""

    name = "JSON"

    def __init__(self) -> None:
        self._active = False

    @property
    def is_active(self) -> bool:
        return self._active

    def toggle(self, window: object) -> None:
        self._active = not self._active
        window._apply_view_mode()  # type: ignore[attr-defined]

    def render(self, window: object) -> None:
        if self._active:
            window._render_json()  # type: ignore[attr-defined]

    @property
    def blocks_edit(self) -> bool:
        return False
