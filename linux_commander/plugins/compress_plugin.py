"""VFS plugin for single-file compression formats (.gz, .bz2, .xz, .zst).

Each compressed file is presented as a one-entry read-only archive whose sole
member is the filename with the compression suffix stripped:

    data.csv.gz  →  member "data.csv"
    photo.png.xz →  member "photo.png"

Supported codecs:
    .gz   — stdlib gzip (always available)
    .bz2  — stdlib bz2  (always available)
    .xz   — stdlib lzma (always available)
    .zst  — compression.zstd (Python 3.14+, probed at import time)

The .zst extension only registers if compression.zstd is importable.
"""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
from typing import IO, BinaryIO

from linux_commander.vfs import FileEntry, FileSystem, ReadableFileSystem, StatResult, VfsPath

# Probe for stdlib zstd support (Python 3.14+)
try:
    import compression.zstd as _zstd_mod  # type: ignore[import-not-found]

    _HAS_ZSTD = True
except ImportError:
    _zstd_mod = None  # type: ignore[assignment]
    _HAS_ZSTD = False

_ZSTD_EXT: tuple[str, ...] = (".zst",) if _HAS_ZSTD else ()
EXTENSIONS: tuple[str, ...] = (".gz", ".bz2", ".xz") + _ZSTD_EXT

_SUFFIX_TO_OPENER = {
    ".gz": gzip.open,
    ".bz2": bz2.open,
    ".xz": lzma.open,
}


def _open_compressed(archive_path_str: str, suffix: str) -> IO[bytes]:
    """Open the compressed file and return a decompressing stream."""
    if suffix == ".zst" and _HAS_ZSTD:
        raw = open(archive_path_str, "rb")  # noqa: SIM115
        return _zstd_mod.open(raw, "rb")  # type: ignore[union-attr, return-value]
    opener = _SUFFIX_TO_OPENER.get(suffix)
    if opener is None:
        raise OSError(f"Unsupported compression suffix: {suffix!r}")
    return opener(archive_path_str, "rb")  # type: ignore[operator, return-value]


def _inner_name(archive_name: str) -> str:
    """Return the member filename by stripping the outermost compression suffix."""
    for suffix in (".gz", ".bz2", ".xz", ".zst"):
        if archive_name.lower().endswith(suffix):
            return archive_name[: -len(suffix)]
    return archive_name


class CompressFileSystem(ReadableFileSystem):
    """Read-only VFS presenting a single-file compressed archive as one-entry dir.

    ``parts`` encoding:
    - Archive root:          ``("",)``
    - The single member:     ``("", "<inner_name>")``
    """

    writable: bool = False

    def __init__(self, archive_path_str: str, host_vpath: VfsPath) -> None:
        from pathlib import Path

        self._archive_path = archive_path_str
        self._host_vpath = host_vpath
        archive_name = Path(archive_path_str).name
        self._suffix = Path(archive_name.lower()).suffix
        self._inner = _inner_name(archive_name)
        self.display_prefix = f"gz:{archive_name}!"

    # -- helpers ------------------------------------------------------------------

    def _check_member(self, path: VfsPath) -> None:
        """Raise OSError if *path* is not the single valid member."""
        if len(path.parts) != 2 or path.parts[1] != self._inner:
            raise OSError(f"Not found in archive: {path.name!r}")

    # -- FileSystem API -----------------------------------------------------------

    def list_dir(self, path: VfsPath) -> list[FileEntry]:
        if path.parts == ("",):
            member_path = path / self._inner
            try:
                with _open_compressed(self._archive_path, self._suffix) as f:
                    size = len(f.read())
            except OSError:
                size = 0
            entries = [
                FileEntry(
                    name="..",
                    path=self._host_vpath.parent,
                    is_dir=True,
                    size=0,
                    mtime=0.0,
                    is_parent=True,
                ),
                FileEntry(
                    name=self._inner,
                    path=member_path,
                    is_dir=False,
                    size=size,
                    mtime=0.0,
                ),
            ]
            return entries
        raise OSError(f"Not a directory: {path}")

    def stat(self, path: VfsPath) -> StatResult:
        if path.parts == ("",):
            return StatResult(is_dir=True, size=0, mtime=0.0)
        self._check_member(path)
        try:
            with _open_compressed(self._archive_path, self._suffix) as f:
                size = len(f.read())
        except OSError:
            size = 0
        return StatResult(is_dir=False, size=size, mtime=0.0)

    def open_read(self, path: VfsPath) -> BinaryIO:
        self._check_member(path)
        stream = _open_compressed(self._archive_path, self._suffix)
        return io.BytesIO(stream.read())  # type: ignore[union-attr]

    def read_prefix(self, path: VfsPath, max_bytes: int) -> tuple[bytes, bool]:
        self._check_member(path)
        with _open_compressed(self._archive_path, self._suffix) as f:
            data = f.read(max_bytes + 1)  # type: ignore[union-attr]
        truncated = len(data) > max_bytes
        if truncated:
            data = data[:max_bytes]
        return data, truncated

    def close(self) -> None:
        pass  # no persistent handle


def open_fs(host_fs: FileSystem, host_path: VfsPath) -> CompressFileSystem:
    """Create a CompressFileSystem for the compressed file at *host_path*."""
    real = host_fs.realpath(host_path)
    if real is not None:
        return CompressFileSystem(str(real), host_path)

    # No real local file (host is a remote backend like Jottacloud/SMB/SFTP,
    # or a nested archive member) -- materialize() would spill to a
    # *randomly named* temp file (it only preserves the last suffix, e.g.
    # ".zst"), but this format's inner member name is derived by stripping
    # just the outer suffix off the archive's own compound filename (e.g.
    # "backup.grp.zst" -> "backup.grp"). A random basename there surfaces
    # as the member name too -- use spill_named_temp() instead, which
    # preserves the whole filename.
    from linux_commander.plugins import spill_named_temp

    with host_fs.open_read(host_path) as f:
        data = f.read()
    tmp_path = spill_named_temp(data, host_path.name)
    return CompressFileSystem(str(tmp_path), host_path)
