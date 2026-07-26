"""Sort criterion plugin discovery and contract.

Each plugin module in this package exposes a ``criterion_class`` attribute that
is a subclass of ``SortCriterion``.  Discovery uses ``pkgutil.iter_modules`` so
broken modules are silently skipped.

A sort criterion defines how file entries are sorted in the panel — by name,
size, modification time, extension, etc.  New criteria can be added by dropping
a module into this package.

Public API:
    SortCriterion      — ABC that criterion plugins must subclass
    discover_criteria  — auto-discover all criterion plugins in this package
    sort_entries       — sort entries using a criterion by name
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from abc import ABC, abstractmethod
from typing import Any

from linux_commander.vfs import FileEntry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sort criterion ABC
# ---------------------------------------------------------------------------


class SortCriterion(ABC):
    """Base class for sort criteria.

    Each subclass defines a single sort key (name, size, mtime, extension, etc.).
    The ``name`` is used to look up the criterion by string key.
    The ``label`` is displayed in the UI (e.g. in the panel header).
    The ``key(entry)`` method returns a sortable value for a file entry.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this criterion (e.g. 'name', 'size')."""

    @property
    @abstractmethod
    def label(self) -> str:
        """Display label for this criterion (e.g. 'Name', 'Size')."""

    @abstractmethod
    def key(self, entry: FileEntry) -> Any:
        """Return a sortable value for the given entry."""


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

_criteria_cache: dict[str, SortCriterion] | None = None


def discover_criteria() -> dict[str, SortCriterion]:
    """Auto-discover all sort criterion plugins in this package.

    Returns a dict mapping criterion name -> SortCriterion instance.
    Results are cached after the first call.
    """
    global _criteria_cache
    if _criteria_cache is not None:
        return _criteria_cache

    criteria: dict[str, SortCriterion] = {}
    package = __package__ or "linux_commander.sort_criteria"
    path = __path__

    for module_info in pkgutil.iter_modules(path, package + "."):
        try:
            mod = importlib.import_module(module_info.name)
        except ImportError:
            logger.warning("Failed to import sort criterion module: %s", module_info.name)
            continue

        criterion_cls = getattr(mod, "criterion_class", None)
        if criterion_cls is None:
            continue

        if not isinstance(criterion_cls, type) or not issubclass(criterion_cls, SortCriterion):
            logger.warning("Sort criterion module %s has invalid criterion_class", module_info.name)
            continue

        instance = criterion_cls()
        criteria[instance.name] = instance

    _criteria_cache = criteria
    return criteria


def get_criterion(name: str) -> SortCriterion | None:
    """Get a sort criterion by name."""
    return discover_criteria().get(name)


def reset_cache() -> None:
    """Clear the criterion cache. Useful for testing."""
    global _criteria_cache
    _criteria_cache = None


# ---------------------------------------------------------------------------
# Sort entries using a criterion
# ---------------------------------------------------------------------------


def sort_entries(
    entries: list[FileEntry],
    criterion_name: str = "name",
    reverse: bool = False,
) -> list[FileEntry]:
    """Sort entries using the named criterion.

    Directories are always sorted before files, and ``..`` is pinned at the top.
    """
    criterion = get_criterion(criterion_name)
    if criterion is None:
        # Fall back to name sorting if criterion not found
        criterion = get_criterion("name")
        assert criterion is not None, "name criterion must always be available"

    def sort_value(entry: FileEntry) -> Any:
        return criterion.key(entry)

    parent = [e for e in entries if e.is_parent]
    dirs = sorted(
        (e for e in entries if e.is_dir and not e.is_parent),
        key=sort_value,
        reverse=reverse,
    )
    files = sorted(
        (e for e in entries if not e.is_dir and not e.is_parent),
        key=sort_value,
        reverse=reverse,
    )
    return parent + dirs + files
