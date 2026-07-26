"""Archive container plugin discovery and contract.

Each plugin module in this package exposes a ``container_class`` attribute that
is a subclass of ``Container``.  Discovery uses ``pkgutil.iter_modules`` so
broken modules are silently skipped.

A container defines how to build an archive — zip, tar, grp, 7z, iso, etc.
New containers can be added by dropping a module into this package.

Public API:
    Container            — ABC that container plugins must subclass
    discover_containers  — auto-discover all container plugins in this package
    get_container        — get a container by name
"""

from __future__ import annotations

import importlib
import logging
import pathlib
import pkgutil
from abc import ABC, abstractmethod

from linux_commander.operations import CancelPredicate, OperationError, ProgressCallback
from linux_commander.vfs import FileSystem, VfsPath

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Container ABC
# ---------------------------------------------------------------------------


class Container(ABC):
    """Base class for archive container builders.

    Each subclass defines a single archive format (zip, tar, grp, etc.).
    The ``name`` is used to look up the container by string key.
    The ``extension`` is the file extension for this format.
    The ``build()`` method creates the archive.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this container (e.g. 'zip', 'tar', 'grp')."""

    @property
    @abstractmethod
    def extension(self) -> str:
        """File extension for this container (e.g. '.zip', '.tar')."""

    @abstractmethod
    def build(
        self,
        sources: list[VfsPath],
        dest: pathlib.Path,
        local_fs: FileSystem,
        on_progress: ProgressCallback,
        should_cancel: CancelPredicate,
    ) -> list[OperationError]:
        """Build an archive at ``dest`` from ``sources``."""

    @property
    def available(self) -> bool:
        """Whether this container is available (True by default, override for optional deps)."""
        return True


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

_container_cache: dict[str, Container] | None = None


def discover_containers() -> dict[str, Container]:
    """Auto-discover all container plugins in this package.

    Returns a dict mapping container name -> Container instance.
    Results are cached after the first call.
    """
    global _container_cache
    if _container_cache is not None:
        return _container_cache

    containers: dict[str, Container] = {}
    package = __package__ or "linux_commander.containers"
    path = __path__

    for module_info in pkgutil.iter_modules(path, package + "."):
        try:
            mod = importlib.import_module(module_info.name)
        except ImportError:
            logger.warning("Failed to import container module: %s", module_info.name)
            continue

        container_cls = getattr(mod, "container_class", None)
        if container_cls is None:
            continue

        if not isinstance(container_cls, type) or not issubclass(container_cls, Container):
            logger.warning("Container module %s has invalid container_class", module_info.name)
            continue

        instance = container_cls()
        if instance.available:
            containers[instance.name] = instance

    _container_cache = containers
    return containers


def get_container(name: str) -> Container | None:
    """Get a container by name."""
    return discover_containers().get(name)


def reset_cache() -> None:
    """Clear the container cache. Useful for testing."""
    global _container_cache
    _container_cache = None
