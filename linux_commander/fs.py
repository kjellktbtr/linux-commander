"""Filesystem utilities: sorting and formatting for directory listings.

``FileEntry`` and the listing logic have moved to ``linux_commander.vfs``
(``LocalFileSystem.list_dir``).  This module retains the pure sorting and
display-formatting functions that have no OS dependency.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from linux_commander.vfs import FileEntry  # re-exported for import compatibility

SortKey = Literal["name", "size", "mtime", "extension"]

# Container/codec/wrapper tokens this app treats as chainable extension
# segments, so compound extensions like "tar.gz" and "tar.gz.crp" display and
# sort as one unit.  Deliberately duplicated (rather than imported) from
# archiving.CONTAINER_EXTENSIONS / CODEC_EXTENSIONS and the plugins' own
# EXTENSIONS so this module stays free of the plugin/archiving import graph;
# these tokens change rarely.
_KNOWN_EXTENSION_TOKENS = frozenset(
    {
        # containers
        "tar",
        "zip",
        "grp",
        "7z",
        "iso",
        # codecs
        "gz",
        "bz2",
        "xz",
        "zst",
        # fused container+codec shorthands
        "tgz",
        "tbz2",
        "txz",
        "tzst",
        # this app's encryption wrapper
        "crp",
        # other archive formats the app can open
        "rar",
        "cpio",
        "a",
        "ar",
        "xar",
        "lha",
        "lzh",
    }
)

_MAX_EXTENSION_LEN = 20
"""Keeps the Extension column narrow: a compound match longer than this
falls back to the last segment alone rather than displaying a long string."""


def split_extension(name: str) -> str:
    """Return the display extension for `name`, without a leading dot,
    lowercased. Returns ``""`` if `name` has no extension (including
    dotfiles such as ``.gitignore``, where the leading dot is never treated
    as an extension separator).

    Chains trailing dot-segments together while each one is a recognised
    container/codec/wrapper token (see `_KNOWN_EXTENSION_TOKENS`), so
    ``archive.tar.gz`` -> ``"tar.gz"`` and ``backup.tar.gz.crp`` ->
    ``"tar.gz.crp"``. For an unrecognised extension, falls back to the last
    dot-segment alone (correct in the common case, e.g. ``photo.heic`` ->
    ``"heic"``, and cheap to get wrong when it isn't).
    """
    # A dot at index 0 marks a dotfile, not an extension separator.
    dot = name.find(".", 1)
    if dot < 0:
        return ""
    segments = name[dot + 1 :].split(".")
    last = segments[-1].lower()
    if last not in _KNOWN_EXTENSION_TOKENS:
        return last
    chain = [last]
    for seg in reversed(segments[:-1]):
        lowered = seg.lower()
        if lowered not in _KNOWN_EXTENSION_TOKENS:
            break
        chain.append(lowered)
    chain.reverse()
    ext = ".".join(chain)
    return ext if len(ext) <= _MAX_EXTENSION_LEN else last


def sort_entries(
    entries: list[FileEntry],
    key: SortKey = "name",
    reverse: bool = False,
) -> list[FileEntry]:
    """Sort entries with directories first and ``..`` always pinned at the top."""

    def sort_value(entry: FileEntry) -> tuple:
        primary: int | float | str
        match key:
            case "size":
                primary = entry.size
            case "mtime":
                primary = entry.mtime
            case "extension":
                primary = "" if entry.is_dir else split_extension(entry.name)
            case _:
                primary = entry.name.lower()
        return (primary,)

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


def format_size(num_bytes: int) -> str:
    """Format a byte count as a short human-readable string, e.g. ``'12.3K'``."""
    size = float(num_bytes)
    for unit in ("B", "K", "M", "G", "T", "P"):
        if size < 1024 or unit == "P":
            if unit == "B":
                return f"{int(size)}{unit}"
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}P"


def format_mtime(timestamp: float) -> str:
    """Format a Unix timestamp as ``'YYYY-MM-DD HH:MM'``.

    ``timestamp <= 0`` is the codebase-wide sentinel for "this backend has no
    modification time for this entry" (every VFS plugin's synthetic ``..``
    entry, archive-internal directories, and Jottacloud folders -- JFS's
    listing XML never includes a ``<modified>`` element on ``<folder>``
    elements -- all use ``mtime=0.0``). Rendering that as a formatted epoch
    date ("1970-01-01") reads as a real, if very old, timestamp, which is
    misleading; blank is the honest rendering of "unknown".
    """
    if timestamp <= 0:
        return ""
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
