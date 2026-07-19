"""VFS plugin for extra archive formats via libarchive (the ``archives`` extra).

Read-only browsing for formats not already covered by the dedicated zip/tar/
7z/rar/grp plugins: ISO 9660 disc images, cpio, ar (static-library) archives,
xar, and lha/lzh.

libarchive only supports sequential/streaming reads -- there is no seeking
to an arbitrary member by name -- so ``open_fs`` reads the whole archive once
up front, buffering every member's bytes in memory alongside a synthesized
directory index (cpio/ar have no explicit directory entries, so ancestor
directories are inferred from member paths, mirroring ``TarFileSystem`` /
``GrpFileSystem``). This is simple and matches the existing GRP plugin's
whole-archive-in-memory precedent, but means a very large archive (e.g. a
multi-gigabyte ISO) is fully read into memory at mount time -- an accepted
trade-off for a first, read-only cut of this format family.

Registers no extensions when the ``libarchive`` package (part of the
``archives`` extra) isn't installed, per the guard convention documented in
``plugins/__init__.py``.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import BinaryIO

from linux_commander.vfs import FileEntry, FileSystem, StatResult, VfsPath

try:
    import libarchive  # type: ignore[import-untyped]

    EXTENSIONS: tuple[str, ...] = (".iso", ".cpio", ".a", ".ar", ".xar", ".lha", ".lzh")
except ImportError:
    libarchive = None  # type: ignore[assignment]
    EXTENSIONS = ()


class LibArchiveFileSystem(FileSystem):
    """Read-only VFS backend for a libarchive-supported archive.

    ``parts`` encoding mirrors the other archive plugins:
    - Archive root:  ``("",)``
    - ``subdir/``:  ``("", "subdir")``
    - ``subdir/f``: ``("", "subdir", "f")``
    """

    writable: bool = False

    def __init__(self, archive_path: Path, host_vpath: VfsPath) -> None:
        self._archive_path = archive_path
        self.display_prefix = f"{host_vpath}!"
        self._files: dict[str, bytes] = {}
        self._mtimes: dict[str, float] = {}
        self._dirs: set[str] = set()
        self._build_index()

    # -- index --------------------------------------------------------------

    def _build_index(self) -> None:
        """Read the archive once, buffering members and synthesizing dirs."""
        try:
            with libarchive.file_reader(str(self._archive_path)) as archive:
                for entry in archive:
                    name = str(entry.pathname).strip("/")
                    if not name or name == ".":
                        continue
                    self._register_ancestor_dirs(name)
                    if entry.isdir:
                        self._dirs.add(name)
                    elif entry.isreg:
                        self._files[name] = b"".join(entry.get_blocks())
                        self._mtimes[name] = float(entry.mtime or 0.0)
                    # Other special types (symlinks, devices, ...) are skipped.
        except Exception as exc:
            raise OSError(f"Cannot open archive '{self._archive_path}': {exc}") from exc

    def _register_ancestor_dirs(self, name: str) -> None:
        parts = name.split("/")[:-1]
        prefix = ""
        for part in parts:
            prefix = f"{prefix}/{part}" if prefix else part
            self._dirs.add(prefix)

    def _member_prefix(self, path: VfsPath) -> str:
        """Convert a VfsPath to the internal archive path prefix."""
        if len(path.parts) <= 1:
            return ""
        return "/".join(path.parts[1:])

    # -- FileSystem API -------------------------------------------------------

    def list_dir(self, path: VfsPath) -> list[FileEntry]:
        prefix = self._member_prefix(path)
        entries: list[FileEntry] = []
        if path.parts != ("",):
            entries.append(
                FileEntry(
                    name="..", path=path.parent, is_dir=True, size=0, mtime=0.0, is_parent=True
                )
            )
        seen: set[str] = set()

        def _add(child: str, is_dir: bool, size: int, mtime: float) -> None:
            if child in seen:
                return
            seen.add(child)
            entries.append(
                FileEntry(name=child, path=path / child, is_dir=is_dir, size=size, mtime=mtime)
            )

        def _rest_of(full: str) -> str | None:
            if prefix:
                if not full.startswith(prefix + "/"):
                    return None
                return full[len(prefix) + 1 :]
            return full

        for dir_path in sorted(self._dirs):
            rest = _rest_of(dir_path)
            if not rest or "/" in rest:
                continue
            _add(rest, True, 0, 0.0)

        for name, data in self._files.items():
            rest = _rest_of(name)
            if not rest or "/" in rest:
                continue
            _add(rest, False, len(data), self._mtimes.get(name, 0.0))

        return entries

    def stat(self, path: VfsPath) -> StatResult:
        name = self._member_prefix(path)
        if not name:
            return StatResult(is_dir=True, size=0, mtime=0.0)
        if name in self._dirs:
            return StatResult(is_dir=True, size=0, mtime=0.0)
        if name in self._files:
            return StatResult(
                is_dir=False, size=len(self._files[name]), mtime=self._mtimes.get(name, 0.0)
            )
        raise OSError(f"Not found in archive: {name!r}")

    def open_read(self, path: VfsPath) -> BinaryIO:
        name = self._member_prefix(path)
        data = self._files.get(name)
        if data is None:
            raise OSError(f"Not found in archive: {name!r}")
        return io.BytesIO(data)


def open_fs(host_fs: FileSystem, host_path: VfsPath) -> FileSystem:
    """Mount ``host_path`` (materialized to a real file) as a LibArchiveFileSystem."""
    from linux_commander.plugins import materialize

    real_path = materialize(host_fs, host_path)
    return LibArchiveFileSystem(real_path, host_path)
