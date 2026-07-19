"""Background file search engine with incremental result callbacks."""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from linux_commander.vfs import FileEntry, LocalFileSystem, VfsPath


def _get_plugin_for_name(name: str) -> object:
    """Lazy import of plugin_for_name to avoid circular imports."""
    from linux_commander.plugins import plugin_for_name

    return plugin_for_name(name)


@dataclass(frozen=True, slots=True)
class SearchCriteria:
    """Immutable search criteria passed to the background worker."""

    # Size
    size_enabled: bool = False
    size_min: int | None = None
    size_max: int | None = None

    # Date (modification time)
    date_enabled: bool = False
    date_from: datetime | None = None
    date_to: datetime | None = None

    # Name pattern
    name_enabled: bool = False
    name_pattern: str = ""
    name_case_sensitive: bool = False
    name_regex: bool = False

    # Content
    content_enabled: bool = False
    content_pattern: str = ""
    content_mode: str = "string"  # "string", "regex", "hex"
    content_case_sensitive: bool = False

    # Root path
    root_path: VfsPath | None = None

    # Limits
    max_content_file_size: int = 10 * 1024 * 1024  # 10 MB default

    # Archive descent
    search_archives: bool = False
    """When True, descend into archive files (zip, tar, …) during search."""


@dataclass(frozen=True, slots=True)
class SearchResult:
    """A single search result with the matching FileEntry and match info."""

    entry: FileEntry
    match_info: str = ""  # e.g., "content", "name", "size", "date"


# Callback types
OnFoundCallback = Callable[[SearchResult], None]
OnDoneCallback = Callable[[int, int], None]  # (total_found, total_scanned)
OnProgressCallback = Callable[[int, int, str], None]  # (current, total, name)


def search_files(
    criteria: SearchCriteria,
    on_found: OnFoundCallback,
    on_done: OnDoneCallback,
    should_cancel: Callable[[], bool],
    on_progress: OnProgressCallback | None = None,
) -> None:
    """Walk the filesystem and report matching files incrementally.

    Runs in a background thread. Calls `on_found` for each match via
    `root.after()` (must be called from main thread). Calls `on_done`
    when finished or cancelled.

    Args:
        criteria: SearchCriteria with all filters
        on_found: Called for each matching file
        on_done: Called with (total_found, total_scanned) when complete
        should_cancel: Callable returning True if search should stop
        on_progress: Optional progress callback (scanned, total_estimate, current_path)
    """
    if criteria.root_path is None:
        on_done(0, 0)
        return

    fs = criteria.root_path.fs
    if not isinstance(fs, LocalFileSystem):
        # For now, only support LocalFileSystem
        on_done(0, 0)
        return

    root = fs._to_path(criteria.root_path)
    if not root.is_dir():
        on_done(0, 0)
        return

    # Pre-compile patterns for efficiency
    name_regex = None
    if criteria.name_enabled and criteria.name_regex:
        flags = 0 if criteria.name_case_sensitive else re.IGNORECASE
        name_regex = re.compile(criteria.name_pattern, flags)

    content_regex = None
    content_bytes = None
    if criteria.content_enabled:
        if criteria.content_mode == "regex":
            flags = 0 if criteria.content_case_sensitive else re.IGNORECASE
            content_regex = re.compile(criteria.content_pattern, flags)
        elif criteria.content_mode == "hex":
            content_bytes = _parse_hex_pattern(criteria.content_pattern)

    total_found_ref = [0]
    total_scanned_ref = [0]

    def walk_dir(dir_path: Path) -> None:
        if should_cancel():
            return

        try:
            entries = list(dir_path.iterdir())
        except (OSError, PermissionError):
            return

        for entry in entries:
            if should_cancel():
                return

            total_scanned_ref[0] += 1

            if on_progress and total_scanned_ref[0] % 50 == 0:
                on_progress(total_scanned_ref[0], 0, str(entry))

            try:
                if entry.is_dir():
                    walk_dir(entry)
                else:
                    if _matches_criteria(
                        entry,
                        criteria,
                        name_regex,
                        content_regex,
                        content_bytes,
                    ):
                        total_found_ref[0] += 1
                        # Create VfsPath and FileEntry for the result
                        vfs_path = fs.from_path(entry)
                        stat = entry.stat()
                        file_entry = FileEntry(
                            path=vfs_path,
                            name=entry.name,
                            is_dir=False,
                            is_parent=False,
                            size=stat.st_size,
                            mtime=stat.st_mtime,
                        )
                        on_found(
                            SearchResult(
                                entry=file_entry,
                                match_info="content"
                                if criteria.content_enabled
                                else "name/size/date",
                            )
                        )

                    # Optionally descend into archive files.
                    if criteria.search_archives and not should_cancel():
                        plugin = _get_plugin_for_name(entry.name)
                        if plugin is not None and hasattr(plugin, "open_fs"):
                            vfs_path = fs.from_path(entry)
                            try:
                                archive_fs = plugin.open_fs(fs, vfs_path)  # type: ignore[union-attr]
                                try:
                                    arc_root = VfsPath(fs=archive_fs, parts=("",))
                                    _walk_vfs(
                                        arc_root,
                                        criteria,
                                        name_regex,
                                        content_regex,
                                        content_bytes,
                                        on_found,
                                        should_cancel,
                                        on_progress,
                                        total_found_ref,
                                        total_scanned_ref,
                                    )
                                finally:
                                    archive_fs.close()
                            except OSError:
                                pass
            except (OSError, PermissionError):
                continue

    walk_dir(root)
    on_done(total_found_ref[0], total_scanned_ref[0])


def _walk_vfs(
    vpath: VfsPath,
    criteria: SearchCriteria,
    name_regex: re.Pattern | None,
    content_regex: re.Pattern | None,
    content_bytes: bytes | None,
    on_found: OnFoundCallback,
    should_cancel: Callable[[], bool],
    on_progress: OnProgressCallback | None,
    total_found_ref: list[int],
    total_scanned_ref: list[int],
) -> None:
    """Recursively search inside a VFS (e.g. an archive backend).

    ``total_found_ref`` and ``total_scanned_ref`` are single-element lists
    used as mutable int references so nested calls accumulate into the same
    counters.
    """
    try:
        entries = vpath.fs.list_dir(vpath)
    except OSError:
        return

    for entry in entries:
        if should_cancel():
            return
        if entry.is_parent:
            continue
        total_scanned_ref[0] += 1
        if on_progress and total_scanned_ref[0] % 50 == 0:
            on_progress(total_scanned_ref[0], 0, str(entry.path))
        if entry.is_dir:
            _walk_vfs(
                entry.path,
                criteria,
                name_regex,
                content_regex,
                content_bytes,
                on_found,
                should_cancel,
                on_progress,
                total_found_ref,
                total_scanned_ref,
            )
        else:
            if _matches_criteria_vfs(entry, criteria, name_regex, content_regex, content_bytes):
                total_found_ref[0] += 1
                on_found(
                    SearchResult(
                        entry=entry,
                        match_info="content" if criteria.content_enabled else "name/size/date",
                    )
                )


def _matches_criteria_vfs(
    entry: FileEntry,
    criteria: SearchCriteria,
    name_regex: re.Pattern | None,
    content_regex: re.Pattern | None,
    content_bytes: bytes | None,
) -> bool:
    """Check if a VFS FileEntry matches all enabled criteria."""
    # Size
    if criteria.size_enabled:
        size = entry.size
        if criteria.size_min is not None and size < criteria.size_min:
            return False
        if criteria.size_max is not None and size > criteria.size_max:
            return False

    # Date (modification time)
    if criteria.date_enabled:
        mtime = datetime.fromtimestamp(entry.mtime)
        if criteria.date_from is not None and mtime < criteria.date_from:
            return False
        if criteria.date_to is not None and mtime > criteria.date_to:
            return False

    # Name
    if criteria.name_enabled:
        name = entry.name
        if criteria.name_regex and name_regex:
            if not name_regex.search(name):
                return False
        elif not criteria.name_regex:
            if criteria.name_case_sensitive:
                if not fnmatch.fnmatch(name, criteria.name_pattern):
                    return False
            else:
                if not fnmatch.fnmatch(name.lower(), criteria.name_pattern.lower()):
                    return False

    # Content
    if criteria.content_enabled:
        if not _match_content_vfs(entry.path, criteria, content_regex, content_bytes):
            return False

    return True


def _match_content_vfs(
    vpath: VfsPath,
    criteria: SearchCriteria,
    content_regex: re.Pattern | None,
    content_bytes: bytes | None,
) -> bool:
    """Check VFS file content against pattern."""
    try:
        with vpath.fs.open_read(vpath) as f:
            data = f.read(criteria.max_content_file_size)

        if criteria.content_mode == "hex" and content_bytes:
            return content_bytes in data

        # Text search
        try:
            content = data.decode("utf-8", errors="ignore")
        except Exception:
            return False

        if content_regex:
            return content_regex.search(content) is not None

        pattern = criteria.content_pattern
        if not criteria.content_case_sensitive:
            pattern = pattern.lower()
            content = content.lower()
        return pattern in content
    except OSError:
        return False


def _matches_criteria(
    path: Path,
    criteria: SearchCriteria,
    name_regex: re.Pattern | None,
    content_regex: re.Pattern | None,
    content_bytes: bytes | None,
) -> bool:
    """Check if a file matches all enabled criteria."""
    try:
        stat = path.stat()
    except (OSError, PermissionError):
        return False

    # Size
    if criteria.size_enabled:
        size = stat.st_size
        if criteria.size_min is not None and size < criteria.size_min:
            return False
        if criteria.size_max is not None and size > criteria.size_max:
            return False

    # Date (modification time)
    if criteria.date_enabled:
        mtime = datetime.fromtimestamp(stat.st_mtime)
        if criteria.date_from is not None and mtime < criteria.date_from:
            return False
        if criteria.date_to is not None and mtime > criteria.date_to:
            return False

    # Name
    if criteria.name_enabled:
        if criteria.name_regex and name_regex:
            if not name_regex.search(path.name):
                return False
        elif criteria.name_regex is False:
            # Glob match
            if criteria.name_case_sensitive:
                if not fnmatch.fnmatch(path.name, criteria.name_pattern):
                    return False
            else:
                if not fnmatch.fnmatch(path.name.lower(), criteria.name_pattern.lower()):
                    return False

    # Content - only check if all other criteria passed
    if criteria.content_enabled:
        if not _match_content(path, criteria, content_regex, content_bytes):
            return False

    return True


def _match_content(
    path: Path,
    criteria: SearchCriteria,
    content_regex: re.Pattern | None,
    content_bytes: bytes | None,
) -> bool:
    """Check file content against pattern."""
    try:
        # Skip large files for content search
        stat = path.stat()
        if stat.st_size > criteria.max_content_file_size:
            return False

        if criteria.content_mode == "hex" and content_bytes:
            with open(path, "rb") as f:
                data = f.read(criteria.max_content_file_size)
            return content_bytes in data
        else:
            # Text search
            try:
                with open(path, encoding="utf-8", errors="ignore") as f:
                    content = f.read(criteria.max_content_file_size)
            except (OSError, UnicodeDecodeError):
                return False

            if content_regex:
                return content_regex.search(content) is not None
            else:
                # Plain string search
                pattern = criteria.content_pattern
                if not criteria.content_case_sensitive:
                    pattern = pattern.lower()
                    content = content.lower()
                return pattern in content
    except (OSError, PermissionError):
        return False


def _parse_hex_pattern(pattern: str) -> bytes:
    """Parse space-separated hex bytes (e.g., 'FF D8 FF E0')."""
    parts = pattern.strip().split()
    out = bytearray()
    for part in parts:
        if not part:
            continue
        # Allow 1 or 2 hex digits per byte
        try:
            val = int(part, 16)
            if val > 255:
                raise ValueError
            out.append(val)
        except ValueError:
            # Invalid hex, ignore
            pass
    return bytes(out)
