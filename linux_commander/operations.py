"""File operations: copy, move, delete, mkdir, rename.

Every batch operation (copy/move/delete) reports progress via an
``on_progress(current, total, name)`` callback, can be aborted early via a
``should_cancel()`` predicate, and collects per-item failures instead of
aborting the whole batch on the first error.

``total`` is a genuine recursive file count across the whole batch (a single
selected directory with 500 files inside contributes 500 to the total, not
1), and ``on_progress`` fires once per file actually transferred/deleted --
including files inside a directory copied via the local ``shutil`` fast
path, via its ``copy_function`` hook -- so a whole-tree operation shows real
per-file progress rather than a single tick for the entire tree.
Cancellation is checked between individual files too, not just between
top-level selected items.

All path arguments are ``VfsPath`` objects carrying their owning filesystem
backend.  Operations use the shutil fast path when source and destination
are both OS-backed; otherwise a chunk-level stream copy is used so that
archive members (or remote backends) can be copied to local disk.

Move from a read-only backend (e.g. an archive) degrades to copy-only:
the data is transferred but the source is not deleted.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import cast

from linux_commander.vfs import VfsPath, WritableFileSystem

ProgressCallback = Callable[..., None]  # (current, total, name, [bytes_done, bytes_total])
CancelPredicate = Callable[[], bool]


class _Cancelled(Exception):
    """Internal sentinel: a should_cancel() check tripped mid-batch."""


def call_progress(
    on_progress: ProgressCallback,
    current: int,
    total: int,
    name: str,
    bytes_done: int | None = None,
    bytes_total: int | None = None,
) -> None:
    """Call on_progress, supporting both old (3-arg) and new (5-arg) signatures."""
    try:
        on_progress(current, total, name, bytes_done, bytes_total)
    except TypeError:
        on_progress(current, total, name)


@dataclass(frozen=True, slots=True)
class OperationError:
    """A single item that failed during a batch operation."""

    path: VfsPath
    message: str


def _noop_progress(*args: object) -> None:
    pass


def _never_cancel() -> bool:
    return False


# ---------------------------------------------------------------------------
# Conflict detection (pre-scan before copy/move)
# ---------------------------------------------------------------------------


class ConflictResolution(
    Enum,
):
    """How to resolve a file conflict during copy/move."""

    REPLACE = auto()
    SKIP = auto()
    REPLACE_IF_NEWER = auto()
    REPLACE_IF_DIFFERENT_SIZE = auto()
    COMPARE = auto()


@dataclass(frozen=True, slots=True)
class ConflictInfo:
    """A single conflict: source file already exists at the destination."""

    source: VfsPath
    dest: VfsPath
    source_size: int
    source_mtime: float
    dest_size: int
    dest_mtime: float


def find_conflicts(sources: list[VfsPath], dest_dir: VfsPath) -> list[ConflictInfo]:
    """Pre-scan ``sources`` against ``dest_dir`` and return all conflicts.

    For each source, checks whether a file or directory with the same name
    already exists at the destination.  For directories, recurses into
    children to find nested conflicts.  Returns an empty list when no
    conflicts exist.
    """
    conflicts: list[ConflictInfo] = []
    for source in sources:
        _find_conflicts_recursive(source, dest_dir, conflicts)
    return conflicts


def _find_conflicts_recursive(
    source: VfsPath, dest_dir: VfsPath, conflicts: list[ConflictInfo]
) -> None:
    """Recursively find conflicts for a single source (file or directory)."""
    try:
        src_st = source.fs.stat(source)
    except OSError:
        return

    dest = dest_dir / source.name

    try:
        dest_st = dest.fs.stat(dest)
    except OSError:
        # Destination doesn't exist -- no conflict
        if src_st.is_dir:
            children = [e for e in source.fs.list_dir(source) if not e.is_parent]
            for child in children:
                _find_conflicts_recursive(child.path, dest_dir, conflicts)
        return

    # Both exist -- check type compatibility
    if src_st.is_dir and dest_st.is_dir:
        # Directory conflict -- recurse into children
        src_children = [e for e in source.fs.list_dir(source) if not e.is_parent]
        dest_children = [e for e in dest.fs.list_dir(dest) if not e.is_parent]
        dest_names = {e.name for e in dest_children}
        for child in src_children:
            if child.name in dest_names:
                _find_conflicts_recursive(child.path, dest, conflicts)
            else:
                _find_conflicts_recursive(child.path, dest, conflicts)
    elif not src_st.is_dir and not dest_st.is_dir:
        # File conflict
        conflicts.append(
            ConflictInfo(
                source=source,
                dest=dest,
                source_size=src_st.size,
                source_mtime=src_st.mtime,
                dest_size=dest_st.size,
                dest_mtime=dest_st.mtime,
            )
        )
    # else: type mismatch (file vs dir) -- let the operation handle it


# ---------------------------------------------------------------------------
# Progress-unit counting (used to compute an accurate `total` up front)
# ---------------------------------------------------------------------------


def count_progress_units(path: VfsPath) -> int:
    """Recursively count progress units under ``path`` via the VFS API.

    A file is 1 unit. A directory's unit count is the sum of its children's
    units, or 1 if it has none (an empty directory is still one unit of
    work -- creating/deleting it). Works for any backend (local, archive,
    remote) since it goes through ``list_dir``/``stat``, never raw OS calls.
    """
    st = path.fs.stat(path)
    if not st.is_dir:
        return 1
    children = [e for e in path.fs.list_dir(path) if not e.is_parent]
    if not children:
        return 1
    return sum(count_progress_units(child.path) for child in children)


def _count_all(paths: list[VfsPath]) -> tuple[list[int], list[OperationError]]:
    """Count units for each of ``paths``, collecting a 0 + an error for any
    that fail to stat (e.g. a stale/missing selection) rather than aborting
    the whole batch."""
    units: list[int] = []
    errors: list[OperationError] = []
    for path in paths:
        try:
            units.append(count_progress_units(path))
        except OSError as exc:
            errors.append(OperationError(path, str(exc)))
            units.append(0)
    return units, errors


def _catch_up_progress(
    counter: list[int],
    target: int,
    name: str,
    total: int,
    on_progress: ProgressCallback,
) -> None:
    """Advance ``counter`` the rest of the way to ``target`` in one final
    tick, for shutil operations that may not have ticked per-file for
    everything they did -- an empty subdirectory (``copytree`` never calls
    ``copy_function`` for directories, only files), or a same-filesystem
    ``os.rename`` shortcut inside ``shutil.move`` that skips
    ``copy_function`` entirely because it's a single atomic op."""
    if counter[0] < target:
        counter[0] = target
        call_progress(on_progress, counter[0], total, name)


def _make_local_copy_function(
    counter: list[int],
    total: int,
    on_progress: ProgressCallback,
    should_cancel: CancelPredicate,
) -> Callable[[str, str], None]:
    """Build a ``copy_function`` for ``shutil.copytree``/``shutil.move``, so
    the local (same-filesystem) fast path reports genuine per-file progress
    too, instead of only being caught up to the tree's total unit count
    after the fact."""

    def _copy_one(src: str, dst: str) -> None:
        if should_cancel():
            raise _Cancelled()
        shutil.copy2(src, dst)
        counter[0] += 1
        call_progress(on_progress, counter[0], total, Path(dst).name)

    return _copy_one


# ---------------------------------------------------------------------------
# Stream-copy helpers (used when shutil fast path is unavailable)
# ---------------------------------------------------------------------------


def _stream_copy(
    src: VfsPath,
    dest_dir: VfsPath,
    name: str,
    total_size: int = 0,
    on_sub_progress: Callable[[int, int], None] | None = None,
) -> None:
    """Copy one file from ``src`` into ``dest_dir`` using ``open_read``/``open_write``.

    Preserves the source modification time on the destination if the backend
    supports ``set_mtime``.
    """
    dest = dest_dir / name
    src_mtime = src.fs.stat(src).mtime
    bytes_written = 0
    writable_dest = cast(WritableFileSystem, dest_dir.fs)
    with src.fs.open_read(src) as inp:
        with writable_dest.open_write(dest) as out:
            while chunk := inp.read(65536):
                out.write(chunk)
                if on_sub_progress:
                    bytes_written += len(chunk)
                    on_sub_progress(bytes_written, total_size)
    writable_dest.set_mtime(dest, src_mtime)


def _copy_file_with_progress(
    source: VfsPath,
    dest_dir: VfsPath,
    counter: list[int],
    total: int,
    on_progress: ProgressCallback,
) -> None:
    """Copy one file with per-byte progress reporting, advancing the shared
    running ``counter`` by one file when done."""
    sz = source.fs.stat(source).size
    current = counter[0] + 1
    call_progress(on_progress, current, total, source.name, 0, sz)

    def _sub(done: int, tot: int) -> None:
        call_progress(on_progress, current, total, source.name, done, tot)

    _stream_copy(source, dest_dir, source.name, total_size=sz, on_sub_progress=_sub)
    counter[0] = current


def _stream_copy_tree(
    src: VfsPath,
    dest_dir: VfsPath,
    on_progress: ProgressCallback,
    should_cancel: CancelPredicate,
    counter: list[int],
    total: int,
) -> None:
    """Recursively copy the directory ``src`` into ``dest_dir`` via stream
    copy, advancing ``counter``/reporting ``on_progress`` per actual file
    copied (or once for the directory itself, if it's empty) -- so a
    whole-tree cross-backend transfer (e.g. uploading a folder to
    Jottacloud) shows real per-file progress instead of a single tick for
    the entire tree."""
    if should_cancel():
        raise _Cancelled()
    new_dir = dest_dir / src.name
    writable = cast(WritableFileSystem, dest_dir.fs)
    writable.mkdir(new_dir)
    # Preserve directory mtime after copying children
    src_dir_mtime = src.fs.stat(src).mtime
    children = [e for e in src.fs.list_dir(src) if not e.is_parent]
    if not children:
        counter[0] += 1
        call_progress(on_progress, counter[0], total, src.name)
        writable.set_mtime(new_dir, src_dir_mtime)
        return
    for entry in children:
        if should_cancel():
            raise _Cancelled()
        if entry.is_dir:
            _stream_copy_tree(entry.path, new_dir, on_progress, should_cancel, counter, total)
        else:
            _copy_file_with_progress(entry.path, new_dir, counter, total, on_progress)
    writable.set_mtime(new_dir, src_dir_mtime)


# ---------------------------------------------------------------------------
# VFS-only delete (used when there's no real local file backing an entry)
# ---------------------------------------------------------------------------


def _delete_via_vfs(
    entry: VfsPath,
    entry_units: int,
    on_progress: ProgressCallback,
    should_cancel: CancelPredicate,
    counter: list[int],
    total: int,
) -> None:
    """Delete ``entry`` through the VFS ``delete()``/``list_dir()`` API, for
    backends with no real local file backing it (e.g. Jottacloud, SMB,
    WebDAV) -- these must never fall through to raw ``pathlib``/``shutil``
    calls, only ``entry.fs.delete()``.

    Tries one recursive ``fs.delete()`` call first -- most remote backends
    implement directory delete as a single recursive server-side operation
    (Jottacloud's ``dlDir``, WebDAV's ``DELETE`` on a collection, this app's
    own zip writer) -- advancing the counter by the whole precomputed
    ``entry_units`` in one jump since there's no per-file hook into that.
    Falls back to deleting children first, one genuine per-file tick each,
    for backends that only support removing empty directories (e.g. SFTP's
    ``rmdir``).
    """
    if should_cancel():
        raise _Cancelled()
    writable = cast(WritableFileSystem, entry.fs)
    st = entry.fs.stat(entry)
    if not st.is_dir:
        writable.delete(entry)
        counter[0] += 1
        call_progress(on_progress, counter[0], total, entry.name)
        return
    try:
        writable.delete(entry)
        counter[0] += entry_units
        call_progress(on_progress, counter[0], total, entry.name)
        return
    except OSError:
        pass
    children = [e for e in entry.fs.list_dir(entry) if not e.is_parent]
    if not children:
        writable.delete(entry)
        counter[0] += 1
        call_progress(on_progress, counter[0], total, entry.name)
        return
    for child in children:
        _delete_via_vfs(
            child.path, count_progress_units(child.path), on_progress, should_cancel, counter, total
        )
    writable.delete(entry)


# ---------------------------------------------------------------------------
# Destination text resolution (used by the copy/move dialog)
# ---------------------------------------------------------------------------


def resolve_dest_path(base: VfsPath, text: str) -> VfsPath:
    """Resolve a typed destination string against ``base``'s filesystem.

    ``text`` is normally the (possibly user-edited) display string of a
    ``VfsPath`` -- the copy/move dialog pre-fills it with
    ``str(other_panel.current_path)``.  This strips ``base.fs.display_prefix``
    if present, then builds an absolute or ``base``-relative ``VfsPath`` **on
    ``base.fs``**, so a remote or archive-mounted destination stays on its
    own backend instead of being silently reinterpreted as a local path
    (e.g. ``sftp://host!/tmp`` turning into a local directory literally
    named ``sftp:/host!/tmp``).
    """
    prefix = base.fs.display_prefix
    remainder = text[len(prefix) :] if prefix and text.startswith(prefix) else text
    segments = [seg for seg in remainder.split("/") if seg]
    if remainder.startswith("/"):
        parts: tuple[str, ...] = ("",) + tuple(segments)
    else:
        parts = base.parts + tuple(segments)
    return VfsPath(fs=base.fs, parts=parts)


# ---------------------------------------------------------------------------
# Public batch operations
# ---------------------------------------------------------------------------


def _copy_entries(
    sources: list[VfsPath],
    dest_dir: VfsPath,
    on_progress: ProgressCallback | None = None,
    should_cancel: CancelPredicate | None = None,
    conflict_resolutions: dict[int, ConflictResolution] | None = None,
) -> list[OperationError]:
    """Copy ``sources`` (files or directories) into ``dest_dir``.

    Uses the shutil fast path when both source and destination are OS-backed.
    Falls back to a chunk-level stream copy for cross-backend transfers (e.g.
    extracting a member from a ZIP archive to a local directory, or
    uploading to a remote backend).

    Returns a list of items that failed; stops early if ``should_cancel()``
    becomes true (checked between files, not just between top-level items).

    ``conflict_resolutions`` maps conflict indices (from ``find_conflicts()``)
    to the chosen resolution strategy for each conflicting file.
    """
    on_progress = on_progress or _noop_progress
    should_cancel = should_cancel or _never_cancel

    if not isinstance(dest_dir.fs, WritableFileSystem):
        msg = "Destination filesystem does not support writing"
        return [OperationError(s, msg) for s in sources]
    real_dest = dest_dir.fs.realpath(dest_dir)
    if real_dest is not None:
        real_dest.mkdir(parents=True, exist_ok=True)
    # else: remote/archive destination — assumed to already exist (it's the
    # directory currently browsed in the other panel); a missing directory
    # surfaces as a clear per-item OSError from the stream copy below.

    units, errors = _count_all(sources)
    total = sum(units)
    counter = [0]

    for source, source_units in zip(sources, units, strict=True):
        if should_cancel():
            break
        if source_units == 0:
            continue  # counting already failed and was recorded above
        try:
            real_src = source.fs.realpath(source)
            if real_src is not None and real_dest is not None:
                # Fast path: both OS-backed — delegate to shutil, with a
                # copy_function hook so per-file progress still fires.
                dest = real_dest / source.name
                target = counter[0] + source_units
                if real_src.is_dir() and not real_src.is_symlink():
                    if dest.exists():
                        raise FileExistsError(f"'{dest.name}' already exists in {real_dest}")
                    shutil.copytree(
                        real_src,
                        dest,
                        copy_function=_make_local_copy_function(
                            counter, total, on_progress, should_cancel
                        ),
                    )
                else:
                    shutil.copy2(real_src, dest)
                _catch_up_progress(counter, target, source.name, total, on_progress)
            else:
                # Stream path: cross-backend (e.g. archive/remote → local,
                # local → remote, or remote → remote)
                st = source.fs.stat(source)
                if st.is_dir:
                    _stream_copy_tree(source, dest_dir, on_progress, should_cancel, counter, total)
                else:
                    _copy_file_with_progress(source, dest_dir, counter, total, on_progress)
        except _Cancelled:
            break
        except OSError as exc:
            errors.append(OperationError(source, str(exc)))

    return errors


def copy_entries(
    sources: list[VfsPath],
    dest_dir: VfsPath,
    on_progress: ProgressCallback | None = None,
    should_cancel: CancelPredicate | None = None,
    conflict_resolutions: dict[int, ConflictResolution] | None = None,
) -> list[OperationError]:
    """Copy ``sources`` (files or directories) into ``dest_dir``.

    Uses the shutil fast path when both source and destination are OS-backed.
    Falls back to a chunk-level stream copy for cross-backend transfers (e.g.
    extracting a member from a ZIP archive to a local directory, or
    uploading to a remote backend).

    Returns a list of items that failed; stops early if ``should_cancel()``
    becomes true (checked between files, not just between top-level items).

    ``conflict_resolutions`` maps conflict indices (from ``find_conflicts()``)
    to the chosen resolution strategy for each conflicting file.
    """
    return _copy_entries(sources, dest_dir, on_progress, should_cancel, conflict_resolutions)


def move_entries(
    sources: list[VfsPath],
    dest_dir: VfsPath,
    on_progress: ProgressCallback | None = None,
    should_cancel: CancelPredicate | None = None,
    conflict_resolutions: dict[int, ConflictResolution] | None = None,
) -> list[OperationError]:
    """Move ``sources`` (files or directories) into ``dest_dir``.

    Uses ``shutil.move`` for local-to-local transfers.  For cross-backend
    moves (e.g. from an archive to local disk), stream-copies then deletes
    the source — but skips the delete when the source filesystem is read-only,
    producing copy-only semantics.  Callers should warn the user before
    invoking this on a read-only source.

    Same progress/cancel/error-collection contract as ``copy_entries``.
    """
    on_progress = on_progress or _noop_progress
    should_cancel = should_cancel or _never_cancel

    if not isinstance(dest_dir.fs, WritableFileSystem):
        msg = "Destination filesystem does not support writing"
        return [OperationError(s, msg) for s in sources]
    real_dest = dest_dir.fs.realpath(dest_dir)
    if real_dest is not None:
        real_dest.mkdir(parents=True, exist_ok=True)
    # else: remote/archive destination — see copy_entries for rationale.

    units, errors = _count_all(sources)
    total = sum(units)
    counter = [0]

    for source, source_units in zip(sources, units, strict=True):
        if should_cancel():
            break
        if source_units == 0:
            continue
        try:
            real_src = source.fs.realpath(source)
            if real_src is not None and real_dest is not None:
                # Fast path: local→local; shutil.move handles copy+delete,
                # using os.rename (instant, no per-file ticks needed) when
                # possible, falling back to copy_function-driven copy+delete
                # across filesystems.
                dest = real_dest / source.name
                if dest.exists():
                    raise FileExistsError(f"'{dest.name}' already exists in {real_dest}")
                target = counter[0] + source_units
                shutil.move(
                    str(real_src),
                    str(dest),
                    copy_function=_make_local_copy_function(
                        counter, total, on_progress, should_cancel
                    ),
                )
                _catch_up_progress(counter, target, source.name, total, on_progress)
            else:
                # Stream path: cross-backend
                st = source.fs.stat(source)
                if st.is_dir:
                    _stream_copy_tree(source, dest_dir, on_progress, should_cancel, counter, total)
                else:
                    _copy_file_with_progress(source, dest_dir, counter, total, on_progress)
                # Only delete the source if the backend supports writes
                if isinstance(source.fs, WritableFileSystem):
                    cast(WritableFileSystem, source.fs).delete(source)
        except _Cancelled:
            break
        except OSError as exc:
            errors.append(OperationError(source, str(exc)))

    return errors


def delete_entries(
    entries: list[VfsPath],
    on_progress: ProgressCallback | None = None,
    should_cancel: CancelPredicate | None = None,
) -> list[OperationError]:
    """Permanently delete ``entries`` (files or directories).

    Uses the shutil fast path for OS-backed entries, and the VFS
    ``delete()`` API (via :func:`_delete_via_vfs`) for backends with no real
    local file -- e.g. Jottacloud, SMB, WebDAV, SFTP -- so deleting from a
    writable remote backend actually works instead of unconditionally
    failing with "read-only filesystem".

    Same progress/cancel/error-collection contract as ``copy_entries``.
    """
    on_progress = on_progress or _noop_progress
    should_cancel = should_cancel or _never_cancel

    units, errors = _count_all(entries)
    total = sum(units)
    counter = [0]

    for entry, entry_units in zip(entries, units, strict=True):
        if should_cancel():
            break
        if entry_units == 0:
            continue
        real = entry.fs.realpath(entry)
        try:
            if real is not None:
                target = counter[0] + entry_units
                if real.is_dir() and not real.is_symlink():
                    shutil.rmtree(real)
                else:
                    real.unlink()
                _catch_up_progress(counter, target, entry.name, total, on_progress)
            elif isinstance(entry.fs, WritableFileSystem):
                _delete_via_vfs(entry, entry_units, on_progress, should_cancel, counter, total)
            else:
                raise OSError("Filesystem does not support deleting")
        except _Cancelled:
            break
        except OSError as exc:
            errors.append(OperationError(entry, str(exc)))

    return errors


def make_directory(parent: VfsPath, name: str) -> VfsPath:
    """Create ``name`` as a new directory inside ``parent``.

    Raises ``FileExistsError`` with a clear message if the target already exists.
    """
    target = parent / name
    real = parent.fs.realpath(target)
    if real is not None and real.exists():
        raise FileExistsError(f"'{name}' already exists in {parent}")
    try:
        cast(WritableFileSystem, parent.fs).mkdir(target)
    except FileExistsError as err:
        raise FileExistsError(f"'{name}' already exists in {parent}") from err
    return target


def make_file(parent: VfsPath, name: str) -> VfsPath:
    """Create ``name`` as a new empty file inside ``parent``.

    Raises ``FileExistsError`` with a clear message if the target already exists.
    """
    target = parent / name
    real = parent.fs.realpath(target)
    if real is not None and real.exists():
        raise FileExistsError(f"'{name}' already exists in {parent}")
    try:
        with cast(WritableFileSystem, parent.fs).open_write(target):
            pass
    except FileExistsError as err:
        raise FileExistsError(f"'{name}' already exists in {parent}") from err
    return target


def rename_entry(entry: VfsPath, new_name: str) -> VfsPath:
    """Rename ``entry`` to ``new_name`` within its current parent directory.

    Raises ``FileExistsError`` with a clear message if the target already exists.
    """
    target = entry.parent / new_name
    real_target = entry.fs.realpath(target)
    if real_target is not None and real_target.exists():
        raise FileExistsError(f"'{new_name}' already exists in {entry.parent}")
    cast(WritableFileSystem, entry.fs).rename(entry, target)
    return target
