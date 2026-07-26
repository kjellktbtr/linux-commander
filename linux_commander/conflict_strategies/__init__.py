"""Conflict resolution strategy plugin discovery and contract.

Each plugin module in this package exposes a ``strategy_class`` attribute that
is a subclass of ``ConflictStrategy``.  Discovery uses ``pkgutil.iter_modules``
so broken modules are silently skipped.

A conflict strategy defines how to resolve a file conflict during copy/move
operations — skip, replace, replace if newer, etc.  New strategies can be
added by dropping a module into this package.

Public API:
    ConflictStrategy   — ABC that strategy plugins must subclass
    discover_strategies — auto-discover all strategy plugins in this package
    get_strategy       — get a strategy by name
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from abc import ABC, abstractmethod
from dataclasses import dataclass

from linux_commander.vfs import VfsPath, WritableFileSystem

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Conflict data
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConflictInfo:
    """Information about a single file conflict."""

    source: VfsPath
    dest: VfsPath
    source_size: int
    dest_size: int
    source_mtime: float
    dest_mtime: float


# ---------------------------------------------------------------------------
# Conflict strategy ABC
# ---------------------------------------------------------------------------


class ConflictStrategy(ABC):
    """Base class for conflict resolution strategies.

    Each subclass defines a single resolution strategy (skip, replace, etc.).
    The ``name`` is used to look up the strategy by string key.
    The ``label`` is displayed in the UI (e.g. in the conflict dialog).
    The ``should_delete(conflict, dest_fs)`` method returns whether to delete
    the destination file before the operation.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this strategy (e.g. 'skip', 'replace')."""

    @property
    @abstractmethod
    def label(self) -> str:
        """Display label for this strategy (e.g. 'Skip', 'Replace')."""

    @abstractmethod
    def should_delete(self, conflict: ConflictInfo, dest_fs: WritableFileSystem) -> bool:
        """Return True if the destination file should be deleted before the operation."""


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

_strategy_cache: dict[str, ConflictStrategy] | None = None


def discover_strategies() -> dict[str, ConflictStrategy]:
    """Auto-discover all conflict strategy plugins in this package.

    Returns a dict mapping strategy name -> ConflictStrategy instance.
    Results are cached after the first call.
    """
    global _strategy_cache
    if _strategy_cache is not None:
        return _strategy_cache

    strategies: dict[str, ConflictStrategy] = {}
    package = __package__ or "linux_commander.conflict_strategies"
    path = __path__

    for module_info in pkgutil.iter_modules(path, package + "."):
        try:
            mod = importlib.import_module(module_info.name)
        except ImportError:
            logger.warning("Failed to import conflict strategy module: %s", module_info.name)
            continue

        strategy_cls = getattr(mod, "strategy_class", None)
        if strategy_cls is None:
            continue

        if not isinstance(strategy_cls, type) or not issubclass(strategy_cls, ConflictStrategy):
            logger.warning(
                "Conflict strategy module %s has invalid strategy_class", module_info.name
            )
            continue

        instance = strategy_cls()
        strategies[instance.name] = instance

    _strategy_cache = strategies
    return strategies


def get_strategy(name: str) -> ConflictStrategy | None:
    """Get a conflict strategy by name."""
    return discover_strategies().get(name)


def reset_cache() -> None:
    """Clear the strategy cache. Useful for testing."""
    global _strategy_cache
    _strategy_cache = None
