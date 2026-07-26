"""VFS plugin for browsing FAT12/FAT16 floppy disk images.

Registers extensions `.img`, `.ima`, and `.floppy`.  Validates by file size
against known floppy formats plus the 0x55AA boot-sector signature.
"""

from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path
from typing import BinaryIO

from linux_commander.fatfs import (
    FATDirEntry,
    FATImage,
    FATImageBuilder,
    detect_floppy_format,
)
from linux_commander.vfs import FileEntry, FileSystem, StatResult, VfsPath

EXTENSIONS: tuple[str, ...] = (".img", ".ima", ".floppy")


class FloppyFileSystem(FileSystem):
    """Writable VFS backend backed by a FAT12/FAT16 floppy disk image."""

    writable: bool = True

    def __init__(self, image_path: Path, host_vpath: VfsPath) -> None:
        self._image_path = image_path
        self._host_vpath = host_vpath
        self.display_prefix = f"floppy:{image_path}!"
        self._reload()

    # -- internal reload -----------------------------------------------------

    def _reload(self) -> None:
        """Re-read the image into a fresh FATImage."""
        raw = self._image_path.read_bytes()
        self._img = FATImage(raw)
        self._fmt = detect_floppy_format(len(raw), raw[:512])

    # -- VfsPath helpers -----------------------------------------------------

    @staticmethod
    def _vpath_prefix(path: VfsPath) -> str:
        """Convert a VfsPath to an internal path string.

        Root ``("",)`` -> ``""``; ``("", "a", "b")`` -> ``"a/b"``.
        """
        if len(path.parts) <= 1:
            return ""
        return "/".join(path.parts[1:])

    # -- FileSystem API ------------------------------------------------------

    def list_dir(self, path: VfsPath) -> list[FileEntry]:
        prefix = self._vpath_prefix(path)
        entries: list[FileEntry] = []

        # Synthetic ".." entry
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

        if not prefix:
            # Root directory
            for de in self._img.list_root_dir():
                entries.append(
                    FileEntry(
                        name=de.display_name,
                        path=path / de.display_name,
                        is_dir=de.is_dir,
                        size=de.size,
                        mtime=de.mtime,
                    )
                )
        else:
            # Subdirectory: find the dir entry, list its cluster chain
            parent_prefix = "/".join(path.parts[1:-1]) if len(path.parts) > 2 else ""
            dir_name = path.name
            parent_entries = (
                self._img.list_root_dir()
                if not parent_prefix
                else self._img.list_cluster_dir(self._resolve_cluster(parent_prefix))
            )
            dir_entry = None
            for de in parent_entries:
                if de.is_dir and de.display_name.upper() == dir_name.upper():
                    dir_entry = de
                    break
            if dir_entry is None:
                return entries
            for de in self._img.list_cluster_dir(dir_entry.first_cluster):
                entries.append(
                    FileEntry(
                        name=de.display_name,
                        path=path / de.display_name,
                        is_dir=de.is_dir,
                        size=de.size,
                        mtime=de.mtime,
                    )
                )

        return entries

    def stat(self, path: VfsPath) -> StatResult:
        prefix = self._vpath_prefix(path)
        if not prefix:
            return StatResult(is_dir=True, size=0, mtime=0.0)
        entry = self._find_entry(prefix)
        if entry is None:
            raise OSError(f"Not found in floppy image: {prefix!r}")
        return StatResult(is_dir=entry.is_dir, size=entry.size, mtime=entry.mtime)

    def open_read(self, path: VfsPath) -> BinaryIO:
        prefix = self._vpath_prefix(path)
        entry = self._find_entry(prefix)
        if entry is None:
            raise OSError(f"Not found in floppy image: {prefix!r}")
        if entry.is_dir:
            raise IsADirectoryError(prefix)
        data = self._img.read_file(entry)
        return io.BytesIO(data)

    def realpath(self, path: VfsPath) -> Path | None:
        return None

    # -- write operations ----------------------------------------------------

    def open_write(self, path: VfsPath) -> BinaryIO:
        """Return a buffer; on close its contents are written to the image."""
        prefix = self._vpath_prefix(path)
        fs = self

        class _FloppyWriteBuffer(io.RawIOBase):
            def __init__(self) -> None:
                self._buf = io.BytesIO()

            def write(self, b: bytes | bytearray) -> int:  # type: ignore[override]
                return self._buf.write(b)

            def close(self) -> None:
                if not self.closed:
                    data = self._buf.getvalue()
                    fs._write_file(prefix, data)
                    super().close()

        return _FloppyWriteBuffer()  # type: ignore[return-value]

    def mkdir(self, path: VfsPath) -> None:
        prefix = self._vpath_prefix(path)
        if not prefix:
            return
        self._write_dir(prefix)

    def delete(self, path: VfsPath) -> None:
        prefix = self._vpath_prefix(path)
        if not prefix:
            raise OSError("Cannot delete floppy root")
        self._delete_path(prefix)

    def rename(self, src: VfsPath, dst: VfsPath) -> None:
        old_prefix = self._vpath_prefix(src)
        new_prefix = self._vpath_prefix(dst)
        if not old_prefix or not new_prefix:
            raise OSError("Cannot rename floppy root")
        self._rename_path(old_prefix, new_prefix)

    def close(self) -> None:
        pass

    # -- internal write helpers ----------------------------------------------

    def _resolve_cluster(self, prefix: str) -> int:
        """Resolve a path prefix to a directory's first cluster."""
        entry = self._find_entry(prefix)
        if entry is None or not entry.is_dir:
            raise NotADirectoryError(prefix)
        return entry.first_cluster

    def _find_entry(self, prefix: str) -> FATDirEntry | None:
        """Find a directory entry by path prefix."""
        if not prefix:
            return None
        parts = prefix.strip("/").split("/")
        current_entries = self._img.list_root_dir()
        for i, part in enumerate(parts):
            found = None
            for de in current_entries:
                if de.display_name.upper() == part.upper():
                    found = de
                    break
            if found is None:
                return None
            if i == len(parts) - 1:
                return found
            current_entries = self._img.list_cluster_dir(found.first_cluster)
        return None

    def _rewrite_image(self, callback) -> None:
        """Rewrite the entire floppy image via callback, atomic replace."""
        if self._fmt is None:
            raise OSError("Unknown floppy format")
        builder = FATImageBuilder(self._fmt, "LINUXCMD")
        callback(builder)
        new_data = builder.finalize()
        fd, tmp = tempfile.mkstemp(dir=self._image_path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(new_data)
            os.replace(tmp, self._image_path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self._reload()

    def _write_file(self, prefix: str, data: bytes) -> None:
        def _do(builder: FATImageBuilder) -> None:
            # First copy all existing entries
            for de in self._img.list_root_dir():
                if de.is_dir:
                    builder.add_dir(de.display_name)
                else:
                    file_data = self._img.read_file(de)
                    builder.add_file(de.display_name, file_data)
            # Add/overwrite the target file
            builder.add_file(prefix, data)

        self._rewrite_image(_do)

    def _write_dir(self, prefix: str) -> None:
        def _do(builder: FATImageBuilder) -> None:
            for de in self._img.list_root_dir():
                if de.is_dir:
                    builder.add_dir(de.display_name)
                else:
                    file_data = self._img.read_file(de)
                    builder.add_file(de.display_name, file_data)
            builder.add_dir(prefix)

        self._rewrite_image(_do)

    def _delete_path(self, prefix: str) -> None:
        def _do(builder: FATImageBuilder) -> None:
            to_drop = prefix.upper()
            for de in self._img.list_root_dir():
                if de.display_name.upper() == to_drop:
                    continue
                if de.is_dir:
                    builder.add_dir(de.display_name)
                else:
                    file_data = self._img.read_file(de)
                    builder.add_file(de.display_name, file_data)

        self._rewrite_image(_do)

    def _rename_path(self, old_prefix: str, new_prefix: str) -> None:
        def _do(builder: FATImageBuilder) -> None:
            old_upper = old_prefix.upper()
            for de in self._img.list_root_dir():
                name = de.display_name
                if name.upper() == old_upper:
                    name = new_prefix
                if de.is_dir:
                    builder.add_dir(name)
                else:
                    file_data = self._img.read_file(de)
                    builder.add_file(name, file_data)

        self._rewrite_image(_do)


def open_fs(host_fs: FileSystem, host_path: VfsPath) -> FloppyFileSystem:
    """Open a floppy image and return a FloppyFileSystem."""
    from linux_commander.plugins import materialize

    real = materialize(host_fs, host_path)
    # Validate: check size + boot signature
    raw = real.read_bytes()
    fmt = detect_floppy_format(len(raw), raw[:512])
    if fmt is None:
        raise OSError(f"Not a valid floppy disk image: {real}")
    return FloppyFileSystem(real, host_path)
