"""Compression worker extracted from app.py.

Performs the actual archive creation from a list of VfsPath sources.
Designed to be called from a ``run_with_progress`` work-function; it
is I/O-bound and intended to run on a background thread.

Archives are built along two independent axes:

    container: zip, tar, grp, 7z (7z only when py7zr is installed),
               iso (only when libarchive is installed)
    codec:     none, gz, bz2, xz, zst (zst only when compression.zstd is available)

The container is always built uncompressed to a temp file (each format keeps
its own natural internal encoding -- zip's deflate, 7z's lzma2, tar/grp
uncompressed); the codec then wraps that whole file, exactly like ``tar`` +
gzip already does today. Every combination is valid, e.g. ``grp.zst``,
``zip.gz``, ``7z.xz`` -- even where double-compression rarely helps.

Public API:
    compress_sources(sources, archive_path, fmt, options, local_fs, on_progress, should_cancel)
        -> list[OperationError]
    CONTAINERS, CODECS: identifiers available on this install
    CONTAINER_EXTENSIONS, CODEC_EXTENSIONS: per-identifier file extension
    compose_extension(container, codec) -> str
"""

from __future__ import annotations

import os
import pathlib
import tempfile
from collections.abc import Callable
from typing import cast

from linux_commander.codecs import discover_codecs
from linux_commander.containers import discover_containers, get_container
from linux_commander.operations import (
    CancelPredicate,
    OperationError,
    ProgressCallback,
    call_progress,
    count_progress_units,
)
from linux_commander.vfs import FileSystem, VfsPath, WritableFileSystem

CODEC_EXTENSIONS: dict[str, str] = {
    "none": "",
    "gz": ".gz",
    "bz2": ".bz2",
    "xz": ".xz",
    "zst": ".zst",
}


def _build_containers() -> tuple[str, ...]:
    return tuple(c.name for c in discover_containers().values())


def _build_container_extensions() -> dict[str, str]:
    return {c.name: c.extension for c in discover_containers().values()}


CONTAINERS = _build_containers()
CONTAINER_EXTENSIONS = _build_container_extensions()
CODECS = tuple(c.name for c in discover_codecs().values())

_FUSED_FMT_MAP: dict[str, tuple[str, str]] = {
    "zip": ("zip", "none"),
    "tar": ("tar", "none"),
    "tar.gz": ("tar", "gz"),
    "tgz": ("tar", "gz"),
    "tar.bz2": ("tar", "bz2"),
    "tbz2": ("tar", "bz2"),
    "tar.xz": ("tar", "xz"),
    "txz": ("tar", "xz"),
    "grp": ("grp", "none"),
    "7z": ("7z", "none"),
    "iso": ("iso", "none"),
}


def compose_extension(container: str, codec: str, encrypted: bool = False) -> str:
    """Return the combined file extension for a container+codec(+crypt) triple.

    E.g. ``("tar", "gz") -> ".tar.gz"``, ``("grp", "zst") -> ".grp.zst"``,
    ``("zip", "none") -> ".zip"``; with ``encrypted=True``, appends ``.crp``
    (e.g. ``("tar", "xz", True) -> ".tar.xz.crp"``).
    """
    ext = CONTAINER_EXTENSIONS[container] + CODEC_EXTENSIONS[codec]
    return ext + ".crp" if encrypted else ext


def _split_fmt(fmt: str) -> tuple[str, str]:
    """Map a legacy fused format string (e.g. ``"tar.gz"``) to (container, codec)."""
    try:
        return _FUSED_FMT_MAP[fmt]
    except KeyError:
        raise ValueError(f"Unknown archive format: {fmt!r}") from None


# Lazy import of _iter_sources to avoid circular imports at module load time


def _iter_vfs(path: VfsPath) -> list[tuple[VfsPath, str]]:
    """Recursively iterate a VFS path, yielding (file_vpath, arcname) pairs.

    If ``path`` is a file it yields itself.  If it is a directory it recurses
    via ``list_dir``, building a relative archive name from the contents
    (not including the top-level directory name).
    """
    try:
        st = path.fs.stat(path)
    except OSError:
        return [(path, path.name)]
    if not st.is_dir:
        return [(path, path.name)]

    result: list[tuple[VfsPath, str]] = []

    def _recurse(vpath: VfsPath, prefix: str) -> None:
        try:
            entries = vpath.fs.list_dir(vpath)
        except OSError:
            return
        for entry in entries:
            if entry.is_parent:
                continue
            child = entry.path
            child_arc = f"{prefix}/{entry.name}" if prefix else entry.name
            if entry.is_dir:
                _recurse(child, child_arc)
            else:
                result.append((child, child_arc))

    # Start with empty prefix so top-level directory name is not included
    _recurse(path, "")
    return result


def _iter_sources(
    sources: list[VfsPath],
    local_fs: FileSystem,
    should_cancel: CancelPredicate,
    on_progress: ProgressCallback,
    add_local_file: Callable[[pathlib.Path, str], None],
    add_local_dir: Callable[[pathlib.Path, str], None],
    add_bytes: Callable[[str, bytes], None],
) -> list[OperationError]:
    """Drive each of ``sources`` through the container-specific ``add_*`` callbacks.

    Local sources are handed to the container writer as real paths (so it can
    stream them, or recurse a directory, itself) -- the container library has
    no per-file progress hook for that, so a whole local directory tree
    advances the running total in one jump when it finishes. Remote sources
    are flattened via ``_iter_vfs`` (which already recurses to individual
    files) and handed over as in-memory bytes one file at a time, so those
    report genuine per-file progress. ``total`` is a real recursive file
    count across the whole batch (see ``count_progress_units``), not
    ``len(sources)`` -- a single selected directory no longer shows "1/1".
    Shared by the zip/tar/7z builders so each only needs to say how to add
    one file/directory/bytes-blob to its own archive.
    """
    errors: list[OperationError] = []

    units: list[int] = []
    for source in sources:
        try:
            units.append(count_progress_units(source))
        except OSError as exc:
            errors.append(OperationError(source, str(exc)))
            units.append(0)
    total = sum(units)
    counter = [0]

    for source, source_units in zip(sources, units, strict=True):
        if should_cancel():
            errors.append(OperationError(source, "Cancelled by user"))
            break
        if source_units == 0:
            continue
        try:
            if source.fs == local_fs:
                real_path = source.fs.realpath(source)
                if real_path is None:
                    errors.append(OperationError(source, "Cannot determine real path"))
                    continue
                if real_path.is_dir():
                    add_local_dir(real_path, os.path.basename(source.name))
                else:
                    add_local_file(real_path, os.path.basename(source.name))
                counter[0] += source_units
                call_progress(
                    on_progress, counter[0], total, f"Compressing {source.name}", None, None
                )
            else:
                # Remote source — recurse via VFS (handles dirs too), one
                # genuine per-file progress tick as each file is read+added.
                cancelled = False
                for vpath, arcname in _iter_vfs(source):
                    if should_cancel():
                        cancelled = True
                        break
                    with vpath.fs.open_read(vpath) as f:
                        data = f.read()
                    add_bytes(arcname, data)
                    counter[0] += 1
                    call_progress(
                        on_progress, counter[0], total, f"Compressing {arcname}", None, None
                    )
                if cancelled:
                    errors.append(OperationError(source, "Cancelled by user"))
                    break
        except OSError as exc:
            errors.append(OperationError(source, str(exc)))
    return errors


def _wrap_codec(
    container_path: pathlib.Path, dest_path: pathlib.Path, codec: str, level: int
) -> None:
    """Compress ``container_path`` into ``dest_path`` as a whole-file wrap.

    ``codec == "none"`` just moves the file into place unchanged. Deletes
    ``container_path`` in all cases (it is always a private temp file).
    """
    from linux_commander.codecs import compress_file

    compress_file(container_path, dest_path, codec, level)


def _wrap_crypt(
    src_path: pathlib.Path, dest_path: pathlib.Path, password: str | None, key_name: str | None
) -> None:
    """Encrypt ``src_path`` into ``dest_path`` as a ``.crp`` blob.

    Reuses ``linux_commander.file_ops.crypt_op``'s ChaCha20-Poly1305 helpers
    directly (the same ones behind the Operations > Encrypt/Decrypt menu
    item), so a ``.crp`` produced here decrypts identically via either path.
    Deletes ``src_path`` on success (it is always a private temp file).
    """
    from linux_commander.file_ops import crypt_op
    from linux_commander.settings import StoredKey, load_settings
    from linux_commander.vfs import LocalFileSystem

    if not crypt_op._HAS_CRYPTO:
        raise OSError("cryptography package is not installed (the 'crypto' extra)")

    stored_key: StoredKey | None = None
    if key_name:
        settings = load_settings()
        stored_key = next((sk for sk in settings.stored_keys if sk.name == key_name), None)
        if stored_key is None:
            raise OSError(f"Stored key {key_name!r} not found.")
    if stored_key is None and not password:
        raise OSError("No password or stored key provided for encryption.")

    local_fs = LocalFileSystem()
    src_vpath = local_fs.from_path(src_path)
    dest_vpath = local_fs.from_path(dest_path)
    crypt_op._encrypt_file(src_vpath, dest_vpath, password, stored_key)
    os.unlink(src_path)


def compress_sources(
    sources: list[VfsPath],
    archive_path: VfsPath,
    fmt: str,
    options: dict[str, object],
    local_fs: FileSystem,
    on_progress: ProgressCallback,
    should_cancel: CancelPredicate,
) -> list[OperationError]:
    """Build an archive at ``archive_path`` from ``sources``.

    Args:
        sources: VfsPaths to include.  May span local and remote filesystems.
        archive_path: Where to write the archive.  The destination filesystem
            must be writable and its ``realpath()`` must return a real path
            (i.e. be a ``LocalFileSystem``).
        fmt: Legacy fused format string (``"zip"``, ``"tar.gz"``, ``"grp"``,
            ...), used only as a fallback when ``options`` doesn't specify
            ``container``/``codec`` directly.
        options: ``{"container": str, "codec": str, "compresslevel": int,
            "password": str | None, "key_name": str | None}``. ``container``
            is one of ``CONTAINERS``; ``codec`` is one of ``CODECS``. Falls
            back to splitting ``fmt`` when either is absent. When
            ``password`` or ``key_name`` (a name in ``Settings.stored_keys``)
            is set, the finished container+codec file is additionally
            encrypted to a ``.crp`` blob (see ``_wrap_crypt``); ``key_name``
            takes precedence if both are set.
        local_fs: The app's shared ``LocalFileSystem`` instance; used to
            distinguish local sources from remote ones.
        on_progress: Callback ``(current, total, name) -> None``.
        should_cancel: Predicate; returns ``True`` when the user cancelled.

    Returns:
        A (possibly empty) list of ``OperationError`` for sources that failed.
    """
    container = options.get("container")
    codec = options.get("codec")
    if not isinstance(container, str) or not isinstance(codec, str):
        container, codec = _split_fmt(fmt)

    level = int(options.get("compresslevel", 6))  # type: ignore[call-overload]

    password_opt = options.get("password")
    password = password_opt if isinstance(password_opt, str) and password_opt else None
    key_name_opt = options.get("key_name")
    key_name = key_name_opt if isinstance(key_name_opt, str) and key_name_opt else None
    encrypted = password is not None or key_name is not None

    real_archive = archive_path.fs.realpath(archive_path)
    # For writable-but-no-realpath filesystems (e.g. FTP) we build into a
    # local temp file and stream it up to the destination afterward.
    tmp_path: str | None = None
    if real_archive is None:
        if not isinstance(archive_path.fs, WritableFileSystem):
            return [
                OperationError(  # type: ignore[list-item]
                    archive_path, "Destination filesystem does not support writing"
                )
            ]
        suffix = compose_extension(container, codec, encrypted).replace(".", "_")
        fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        real_archive = pathlib.Path(tmp_path)

    errors: list[OperationError] = []
    build_fd, build_name = tempfile.mkstemp(
        dir=real_archive.parent, suffix=CONTAINER_EXTENSIONS[container]
    )
    os.close(build_fd)
    build_path = pathlib.Path(build_name)

    # When encrypting, the codec wrap can't write straight to real_archive --
    # it needs to land in one more private temp file that _wrap_crypt then
    # encrypts into real_archive.
    codec_tmp_path: pathlib.Path | None = None
    if encrypted:
        codec_fd, codec_name = tempfile.mkstemp(dir=real_archive.parent, suffix=".tmp")
        os.close(codec_fd)
        codec_tmp_path = pathlib.Path(codec_name)

    try:
        ctr = get_container(container)
        if ctr is None:
            raise ValueError(f"Unknown container format: {container!r}")
        errors = ctr.build(sources, build_path, local_fs, on_progress, should_cancel)
        if not errors:
            if encrypted:
                assert codec_tmp_path is not None
                _wrap_codec(build_path, codec_tmp_path, codec, level)
                _wrap_crypt(codec_tmp_path, real_archive, password, key_name)
            else:
                _wrap_codec(build_path, real_archive, codec, level)
    except Exception as exc:
        first = sources[0] if sources else None
        if first is not None:
            errors.append(OperationError(first, f"Compression failed: {exc}"))  # type: ignore[arg-type]
    finally:
        if build_path.exists():
            try:
                os.unlink(build_path)
            except OSError:
                pass
        if codec_tmp_path is not None and codec_tmp_path.exists():
            try:
                os.unlink(codec_tmp_path)
            except OSError:
                pass

    # Flush temp archive to the remote destination (e.g. FTP).
    if tmp_path is not None and not errors:
        try:
            with open(real_archive, "rb") as f_in:
                with cast(WritableFileSystem, archive_path.fs).open_write(archive_path) as f_out:
                    while chunk := f_in.read(65536):
                        f_out.write(chunk)
        except OSError as exc:
            first = sources[0] if sources else None
            if first is not None:
                errors.append(OperationError(first, f"Upload failed: {exc}"))  # type: ignore[arg-type]
        finally:
            try:
                os.unlink(real_archive)
            except OSError:
                pass

    return errors
