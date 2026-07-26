"""Compression codec plugin discovery and contract.

Each plugin module in this package exposes a ``codec_class`` attribute that is
a subclass of ``Codec``.  Discovery uses ``pkgutil.iter_modules`` so broken
modules are silently skipped.

A codec defines how to compress a file — gzip, bzip2, xz, zstd, etc.  New
codecs can be added by dropping a module into this package.

Public API:
    Codec              — ABC that codec plugins must subclass
    discover_codecs    — auto-discover all codec plugins in this package
    get_codec          — get a codec by name
    compress_file      — compress a file using a codec by name
"""

from __future__ import annotations

import importlib
import logging
import os
import pathlib
import pkgutil
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Codec ABC
# ---------------------------------------------------------------------------


class Codec(ABC):
    """Base class for compression codecs.

    Each subclass defines a single compression format (gzip, bzip2, xz, etc.).
    The ``name`` is used to look up the codec by string key.
    The ``compress(src, dst, level)`` method compresses a file.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this codec (e.g. 'gz', 'bz2', 'xz')."""

    @abstractmethod
    def compress(self, src: pathlib.Path, dst: pathlib.Path, level: int) -> None:
        """Compress ``src`` into ``dst`` using the given compression level."""


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

_codec_cache: dict[str, Codec] | None = None


def discover_codecs() -> dict[str, Codec]:
    """Auto-discover all codec plugins in this package.

    Returns a dict mapping codec name -> Codec instance.
    Results are cached after the first call.
    """
    global _codec_cache
    if _codec_cache is not None:
        return _codec_cache

    codecs: dict[str, Codec] = {}
    package = __package__ or "linux_commander.codecs"
    path = __path__

    for module_info in pkgutil.iter_modules(path, package + "."):
        try:
            mod = importlib.import_module(module_info.name)
        except ImportError:
            logger.warning("Failed to import codec module: %s", module_info.name)
            continue

        codec_cls = getattr(mod, "codec_class", None)
        if codec_cls is None:
            continue

        if not isinstance(codec_cls, type) or not issubclass(codec_cls, Codec):
            logger.warning("Codec module %s has invalid codec_class", module_info.name)
            continue

        instance = codec_cls()
        codecs[instance.name] = instance

    _codec_cache = codecs
    return codecs


def get_codec(name: str) -> Codec | None:
    """Get a codec by name."""
    return discover_codecs().get(name)


def reset_cache() -> None:
    """Clear the codec cache. Useful for testing."""
    global _codec_cache
    _codec_cache = None


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------


def compress_file(
    src: pathlib.Path,
    dst: pathlib.Path,
    codec_name: str,
    level: int,
) -> None:
    """Compress ``src`` into ``dst`` using the named codec.

    Deletes ``src`` after successful compression (it is always a temp file).
    Raises ``ValueError`` if the codec is not found.
    """
    codec = get_codec(codec_name)
    if codec is None:
        raise ValueError(f"Unsupported compression codec: {codec_name!r}")

    codec.compress(src, dst, level)
    # Only unlink if src still exists (none codec moves it with os.replace)
    if src.exists():
        os.unlink(src)
