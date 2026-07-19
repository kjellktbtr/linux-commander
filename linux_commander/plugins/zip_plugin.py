"""VFS plugin for browsing and writing ZIP archives."""

from __future__ import annotations

import datetime
import io
import os
import tempfile
import zipfile
from pathlib import Path
from typing import BinaryIO

from linux_commander.vfs import FileEntry, FileSystem, StatResult, VfsPath

EXTENSIONS: tuple[str, ...] = (".zip",)


class ZipFileSystem(FileSystem):
    """Writable VFS backend backed by a ZIP archive.

    ``parts`` encoding inside the zip:
    - Archive root:   ``("",)``
    - ``subdir/``:   ``("", "subdir")``
    - ``subdir/f``:  ``("", "subdir", "f")``

    Write operations:
    - ``open_write`` / ``mkdir``: uses ``zipfile.ZipFile`` in append mode
      (``"a"``) so no full rewrite is needed.
    - ``delete`` / ``rename``: cannot be done in-place; the archive is
      rewritten to a temp file and atomically replaced.
    """

    writable: bool = True

    def __init__(self, archive_path: Path, host_vpath: VfsPath) -> None:
        self._archive_path = archive_path
        try:
            self._zf = zipfile.ZipFile(archive_path, "r")
        except (zipfile.BadZipFile, OSError) as exc:
            raise OSError(f"Cannot open ZIP archive '{archive_path}': {exc}") from exc
        self._host_vpath = host_vpath
        self.display_prefix = f"zip:{archive_path}!"
        self._build_index()

    # -- index ----------------------------------------------------------------

    def _build_index(self) -> None:
        """Build lookup tables for members and synthetic directory entries."""
        self._members: dict[str, zipfile.ZipInfo] = {}
        self._dirs: set[str] = set()
        for info in self._zf.infolist():
            name = info.filename.rstrip("/")
            if not name:
                continue
            if info.is_dir():
                self._dirs.add(name)
            else:
                self._members[name] = info
            # Synthesise every ancestor directory path
            parts = name.split("/")
            for i in range(1, len(parts)):
                self._dirs.add("/".join(parts[:i]))

    @staticmethod
    def _mtime(info: zipfile.ZipInfo) -> float:
        try:
            return datetime.datetime(*info.date_time).timestamp()
        except (ValueError, OSError):
            return 0.0

    def _zip_prefix(self, path: VfsPath) -> str:
        """Convert a ``VfsPath`` to the internal zip path prefix.

        Root ``("",)`` → ``""``; ``("", "a", "b")`` → ``"a/b"``.
        """
        if len(path.parts) <= 1:
            return ""
        return "/".join(path.parts[1:])

    def _reopen_read(self) -> None:
        """Close and reopen the archive in read mode, refreshing the index."""
        self._zf.close()
        self._zf = zipfile.ZipFile(self._archive_path, "r")
        self._build_index()

    # -- FileSystem API -------------------------------------------------------

    def list_dir(self, path: VfsPath) -> list[FileEntry]:
        prefix = self._zip_prefix(path)
        entries: list[FileEntry] = []

        # Always include ".." — even at the archive root.  Navigation across
        # the mount boundary (back to the host directory) is handled by
        # MountManager.resolve_up, not by this entry's path field.
        entries.append(
            FileEntry(
                name="..",
                path=path.parent,
                is_dir=True,
                size=0,
                mtime=0.0,
                is_parent=True,
            )
        )

        seen: set[str] = set()

        def _add(child: str, is_dir: bool, size: int, mtime: float) -> None:
            if child in seen:
                return
            seen.add(child)
            entries.append(
                FileEntry(
                    name=child,
                    path=path / child,
                    is_dir=is_dir,
                    size=size,
                    mtime=mtime,
                )
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
            _add(rest, False, info.file_size, self._mtime(info))

        return entries

    def stat(self, path: VfsPath) -> StatResult:
        zip_name = self._zip_prefix(path)
        if not zip_name:
            return StatResult(is_dir=True, size=0, mtime=0.0)
        if zip_name in self._dirs:
            return StatResult(is_dir=True, size=0, mtime=0.0)
        if zip_name in self._members:
            info = self._members[zip_name]
            return StatResult(is_dir=False, size=info.file_size, mtime=self._mtime(info))
        raise OSError(f"Not found in archive: {zip_name!r}")

    def open_read(self, path: VfsPath) -> BinaryIO:  # type: ignore[override]
        zip_name = self._zip_prefix(path)
        return self._zf.open(zip_name)  # type: ignore[return-value]

    def read_prefix(self, path: VfsPath, max_bytes: int) -> tuple[bytes, bool]:
        zip_name = self._zip_prefix(path)
        with self._zf.open(zip_name) as f:
            data = f.read(max_bytes + 1)
        truncated = len(data) > max_bytes
        if truncated:
            data = data[:max_bytes]
        return data, truncated

    def open_write(self, path: VfsPath) -> BinaryIO:
        """Return a buffer; on close its contents are appended to the archive."""
        zip_name = self._zip_prefix(path)
        fs = self

        class _ZipWriteBuffer(io.RawIOBase):
            def __init__(self) -> None:
                self._buf = io.BytesIO()

            def write(self, b: bytes | bytearray) -> int:  # type: ignore[override]
                return self._buf.write(b)

            def close(self) -> None:
                if not self.closed:
                    data = self._buf.getvalue()
                    # Append to the archive; reopen read handle afterward.
                    fs._zf.close()
                    with zipfile.ZipFile(fs._archive_path, "a") as za:
                        za.writestr(zip_name, data)
                    fs._reopen_read()
                    super().close()

        return _ZipWriteBuffer()  # type: ignore[return-value]

    def mkdir(self, path: VfsPath) -> None:
        """Add a directory entry to the archive."""
        zip_name = self._zip_prefix(path) + "/"
        self._zf.close()
        with zipfile.ZipFile(self._archive_path, "a") as za:
            info = zipfile.ZipInfo(zip_name)
            za.writestr(info, "")
        self._reopen_read()

    def delete(self, path: VfsPath) -> None:
        """Remove a member (or directory tree) from the archive via full rewrite."""
        zip_prefix = self._zip_prefix(path)
        # Collect names to drop: the entry itself and everything under it.
        to_drop: set[str] = set()
        for name in list(self._members.keys()):
            if name == zip_prefix or name.startswith(zip_prefix + "/"):
                to_drop.add(name)
        for d in list(self._dirs):
            if d == zip_prefix or d.startswith(zip_prefix + "/"):
                to_drop.add(d + "/")
        if not to_drop:
            raise OSError(f"Not found in archive: {zip_prefix!r}")
        self._rewrite(lambda n: n not in to_drop and (n.rstrip("/") + "/") not in to_drop)

    def rename(self, src: VfsPath, dst: VfsPath) -> None:
        """Rename a member in the archive via full rewrite."""
        old_name = self._zip_prefix(src)
        new_name = self._zip_prefix(dst)

        def transform(name: str) -> str:
            if name == old_name:
                return new_name
            if name.startswith(old_name + "/"):
                return new_name + name[len(old_name) :]
            return name

        self._rewrite(keep=None, rename=transform)

    def _rewrite(
        self,
        keep: object = None,  # callable(str) -> bool | None
        rename: object = None,  # callable(str) -> str | None
    ) -> None:
        """Rewrite the archive, optionally filtering or renaming members."""
        # Read all members into memory while the file is still open.
        member_data: list[tuple[zipfile.ZipInfo, bytes]] = []
        for info in self._zf.infolist():
            raw_name = info.filename
            norm_name = raw_name.rstrip("/")
            if keep is not None and not keep(raw_name):  # type: ignore[operator]
                continue
            new_name = rename(norm_name) if rename is not None else norm_name  # type: ignore[operator]
            if info.is_dir():
                new_name += "/"
            if new_name != raw_name:
                info = zipfile.ZipInfo(new_name)
            try:
                data = self._zf.read(raw_name.rstrip("/")) if not info.is_dir() else b""
            except (KeyError, zipfile.BadZipFile):
                data = b""
            member_data.append((info, data))

        self._zf.close()

        # Write to a temp file, then atomically replace the archive.
        fd, tmp = tempfile.mkstemp(dir=self._archive_path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                with zipfile.ZipFile(fh, "w", zipfile.ZIP_DEFLATED) as zf_out:
                    for info, data in member_data:
                        zf_out.writestr(info, data)
            os.replace(tmp, self._archive_path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

        self._reopen_read()

    def realpath(self, path: VfsPath) -> Path | None:
        return None

    def close(self) -> None:
        self._zf.close()


def open_fs(host_fs: FileSystem, host_path: VfsPath) -> ZipFileSystem:
    """Open the ZIP at ``host_path`` and return a ``ZipFileSystem``."""
    from linux_commander.plugins import materialize

    real = materialize(host_fs, host_path)
    return ZipFileSystem(real, host_path)
