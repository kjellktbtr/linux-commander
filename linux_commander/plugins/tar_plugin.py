"""VFS plugin for browsing and writing tar archives.

Supports .tar, .tar.gz / .tgz, .tar.bz2, .tar.xz, and (on Python 3.14+)
.tar.zst / .tzst via Python's standard ``tarfile`` module.  The archive is
opened in ``r:*`` mode (transparent decompression) on a real local file
produced by ``materialize()``, so seekable access always works.

Write operations:
- ``open_write`` / ``mkdir``: For uncompressed ``.tar``, uses ``tarfile``
  append mode (``"a"``).  For compressed formats, the whole archive is
  rewritten (stdlib cannot append to a compressed stream).
- ``delete`` / ``rename``: always require a full rewrite.
"""

from __future__ import annotations

import io
import os
import tarfile
import tempfile
from pathlib import Path
from typing import BinaryIO

from linux_commander.vfs import FileEntry, FileSystem, StatResult, VfsPath

# Probe for tarfile zstd support (added in Python 3.14).
_TARFILE_HAS_ZSTD = "zst" in tarfile.ENCODING_MAP if hasattr(tarfile, "ENCODING_MAP") else False
try:
    # Trying to open a non-existent file in w:zst mode is the most reliable probe.
    _tf_test = tarfile.open("/dev/null", "w:zst")  # type: ignore[call-overload]
    _tf_test.close()
    _TARFILE_HAS_ZSTD = True
except Exception:
    _TARFILE_HAS_ZSTD = False

_ZSTD_EXTENSIONS: tuple[str, ...] = (".tar.zst", ".tzst") if _TARFILE_HAS_ZSTD else ()

EXTENSIONS: tuple[str, ...] = (
    ".tar",
    ".tar.gz",
    ".tgz",
    ".tar.bz2",
    ".tar.xz",
    *_ZSTD_EXTENSIONS,
)

# Format → write-mode for creating/rewriting a compressed archive.
_WRITE_MODE: dict[str, str] = {
    ".tar": "w",
    ".tar.gz": "w:gz",
    ".tgz": "w:gz",
    ".tar.bz2": "w:bz2",
    ".tar.xz": "w:xz",
    ".tar.zst": "w:zst",
    ".tzst": "w:zst",
}

_COMPOUND_SUFFIXES = (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst")


def _archive_suffix(path: Path) -> str:
    """Return the compound extension for a tar archive path, e.g. ``.tar.gz``."""
    name = path.name.lower()
    for ext in _COMPOUND_SUFFIXES:
        if name.endswith(ext):
            return ext
    if name.endswith(".tgz"):
        return ".tgz"
    if name.endswith(".tzst"):
        return ".tzst"
    return path.suffix.lower()


class TarFileSystem(FileSystem):
    """Writable VFS backend backed by a tar archive.

    ``parts`` encoding mirrors ZipFileSystem:
    - Archive root:  ``("",)``
    - ``subdir/``:  ``("", "subdir")``
    - ``subdir/f``: ``("", "subdir", "f")``
    """

    writable: bool = True

    def __init__(self, archive_path: Path, host_vpath: VfsPath) -> None:
        self._archive_path = archive_path
        self._suffix = _archive_suffix(archive_path)
        try:
            self._tf = tarfile.open(archive_path, "r:*")
        except (tarfile.TarError, OSError) as exc:
            raise OSError(f"Cannot open tar archive '{archive_path}': {exc}") from exc
        self._host_vpath = host_vpath
        self.display_prefix = f"tar:{archive_path}!"
        self._build_index()

    # -- index ----------------------------------------------------------------

    def _build_index(self) -> None:
        """Build lookup tables for regular members and synthetic directories."""
        self._members: dict[str, tarfile.TarInfo] = {}
        self._dirs: set[str] = set()
        for info in self._tf.getmembers():
            name = info.name.lstrip("/").rstrip("/")
            if name.startswith("./"):
                name = name[2:]
            if not name or name == ".":
                continue
            if info.isdir():
                self._dirs.add(name)
            elif info.isfile():
                self._members[name] = info
            # Synthesise every ancestor directory path
            parts = name.split("/")
            for i in range(1, len(parts)):
                self._dirs.add("/".join(parts[:i]))

    def _tar_prefix(self, path: VfsPath) -> str:
        """Convert a VfsPath to the internal tar path prefix."""
        if len(path.parts) <= 1:
            return ""
        return "/".join(path.parts[1:])

    def _reopen_read(self) -> None:
        """Close and reopen the archive in read mode, refreshing the index."""
        self._tf.close()
        self._tf = tarfile.open(self._archive_path, "r:*")
        self._build_index()

    def _write_mode(self) -> str:
        return _WRITE_MODE.get(self._suffix, "w")

    def _is_compressed(self) -> bool:
        return self._suffix not in (".tar", "")

    # -- FileSystem API -------------------------------------------------------

    def list_dir(self, path: VfsPath) -> list[FileEntry]:
        prefix = self._tar_prefix(path)
        entries: list[FileEntry] = [
            FileEntry(
                name="..",
                path=path.parent,
                is_dir=True,
                size=0,
                mtime=0.0,
                is_parent=True,
            )
        ]
        seen: set[str] = set()

        def _add(child: str, is_dir: bool, size: int, mtime: float) -> None:
            if child in seen:
                return
            seen.add(child)
            entries.append(
                FileEntry(name=child, path=path / child, is_dir=is_dir, size=size, mtime=mtime)
            )

        for dir_path in sorted(self._dirs):
            if prefix:
                if not dir_path.startswith(prefix + "/"):
                    continue
                rest = dir_path[len(prefix) + 1 :]
            else:
                rest = dir_path
            if not rest or "/" in rest:
                continue
            _add(rest, True, 0, 0.0)

        for member_name, info in self._members.items():
            if prefix:
                if not member_name.startswith(prefix + "/"):
                    continue
                rest = member_name[len(prefix) + 1 :]
            else:
                rest = member_name
            if not rest or "/" in rest:
                continue
            _add(rest, False, info.size, float(info.mtime))

        return entries

    def stat(self, path: VfsPath) -> StatResult:
        tar_name = self._tar_prefix(path)
        if not tar_name:
            return StatResult(is_dir=True, size=0, mtime=0.0)
        if tar_name in self._dirs:
            return StatResult(is_dir=True, size=0, mtime=0.0)
        if tar_name in self._members:
            info = self._members[tar_name]
            return StatResult(is_dir=False, size=info.size, mtime=float(info.mtime))
        raise OSError(f"Not found in archive: {tar_name!r}")

    def open_read(self, path: VfsPath) -> BinaryIO:
        tar_name = self._tar_prefix(path)
        info = self._members.get(tar_name)
        if info is None:
            raise OSError(f"Not found in archive: {tar_name!r}")
        f = self._tf.extractfile(info)
        if f is None:
            raise OSError(f"Cannot read '{tar_name}': not a regular file")
        # Buffer fully into BytesIO to avoid stream-position issues with
        # compressed archives where seeking requires re-reading from the start.
        return io.BytesIO(f.read())

    def read_prefix(self, path: VfsPath, max_bytes: int) -> tuple[bytes, bool]:
        tar_name = self._tar_prefix(path)
        info = self._members.get(tar_name)
        if info is None:
            raise OSError(f"Not found in archive: {tar_name!r}")
        f = self._tf.extractfile(info)
        if f is None:
            raise OSError(f"Cannot read '{tar_name}': not a regular file")
        data = f.read(max_bytes + 1)
        truncated = len(data) > max_bytes
        if truncated:
            data = data[:max_bytes]
        return data, truncated

    def open_write(self, path: VfsPath) -> BinaryIO:
        """Return a buffer; on close its contents are added to the archive."""
        tar_name = self._tar_prefix(path)
        fs = self

        class _TarWriteBuffer(io.RawIOBase):
            def __init__(self) -> None:
                self._buf = io.BytesIO()

            def write(self, b: bytes | bytearray) -> int:  # type: ignore[override]
                return self._buf.write(b)

            def close(self) -> None:
                if not self.closed:
                    data = self._buf.getvalue()
                    ti = tarfile.TarInfo(name=tar_name)
                    ti.size = len(data)
                    if fs._is_compressed():
                        # Compressed tar: cannot append → full rewrite with the new member.
                        fs._rewrite_add(tar_name, data)
                    else:
                        # Uncompressed tar: cheap append.
                        fs._tf.close()
                        with tarfile.open(fs._archive_path, "a") as tf_out:
                            tf_out.addfile(ti, io.BytesIO(data))
                        fs._reopen_read()
                    super().close()

        return _TarWriteBuffer()  # type: ignore[return-value]

    def mkdir(self, path: VfsPath) -> None:
        """Add a directory entry to the archive."""
        tar_name = self._tar_prefix(path)
        ti = tarfile.TarInfo(name=tar_name)
        ti.type = tarfile.DIRTYPE
        ti.mode = 0o755
        if self._is_compressed():
            self._rewrite_add_dir(tar_name)
        else:
            self._tf.close()
            with tarfile.open(self._archive_path, "a") as tf_out:
                tf_out.addfile(ti)
            self._reopen_read()

    def delete(self, path: VfsPath) -> None:
        """Remove a member (or directory tree) from the archive via full rewrite."""
        tar_prefix = self._tar_prefix(path)

        def keep(name: str) -> bool:
            return name != tar_prefix and not name.startswith(tar_prefix + "/")

        self._rewrite(keep=keep)

    def rename(self, src: VfsPath, dst: VfsPath) -> None:
        """Rename a member in the archive via full rewrite."""
        old_name = self._tar_prefix(src)
        new_name = self._tar_prefix(dst)

        def transform(name: str) -> str:
            if name == old_name:
                return new_name
            if name.startswith(old_name + "/"):
                return new_name + name[len(old_name) :]
            return name

        self._rewrite(rename=transform)

    def _rewrite(
        self,
        keep: object = None,  # callable(str) -> bool | None
        rename: object = None,  # callable(str) -> str | None
        extra: list[tuple[tarfile.TarInfo, bytes]] | None = None,
    ) -> None:
        """Rewrite the archive, optionally filtering/renaming members or adding extras."""
        # Read all members while the file is open.
        member_data: list[tuple[tarfile.TarInfo, bytes]] = []
        for info in self._tf.getmembers():
            norm = info.name.lstrip("/").rstrip("/")
            if norm.startswith("./"):
                norm = norm[2:]
            if keep is not None and not keep(norm):  # type: ignore[operator]
                continue
            new_name = rename(norm) if rename is not None else norm  # type: ignore[operator]
            # Clone TarInfo with potentially new name.
            new_info = tarfile.TarInfo.fromtarfile(self._tf) if False else info  # copy trick
            new_info = tarfile.TarInfo(name=new_name)
            new_info.mode = info.mode
            new_info.type = info.type
            new_info.mtime = info.mtime
            new_info.uid = info.uid
            new_info.gid = info.gid
            if info.isfile():
                f = self._tf.extractfile(info)
                data = f.read() if f is not None else b""
                new_info.size = len(data)
            else:
                data = b""
            member_data.append((new_info, data))

        if extra:
            member_data.extend(extra)

        self._tf.close()

        fd, tmp = tempfile.mkstemp(dir=self._archive_path.parent, suffix=".tmp")
        os.close(fd)
        try:
            with tarfile.open(tmp, mode=self._write_mode()) as tf_out:  # type: ignore[call-overload]
                for info, data in member_data:
                    tf_out.addfile(info, io.BytesIO(data))
            os.replace(tmp, self._archive_path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

        self._reopen_read()

    def _rewrite_add(self, tar_name: str, data: bytes) -> None:
        """Full rewrite adding a new regular-file member."""
        ti = tarfile.TarInfo(name=tar_name)
        ti.size = len(data)
        self._rewrite(extra=[(ti, data)])

    def _rewrite_add_dir(self, tar_name: str) -> None:
        """Full rewrite adding a new directory member."""
        ti = tarfile.TarInfo(name=tar_name)
        ti.type = tarfile.DIRTYPE
        ti.mode = 0o755
        self._rewrite(extra=[(ti, b"")])

    def realpath(self, path: VfsPath) -> Path | None:
        return None

    def close(self) -> None:
        self._tf.close()


def open_fs(host_fs: FileSystem, host_path: VfsPath) -> TarFileSystem:
    """Open the tar archive at ``host_path`` and return a ``TarFileSystem``."""
    from linux_commander.plugins import materialize

    real = materialize(host_fs, host_path)
    return TarFileSystem(real, host_path)
