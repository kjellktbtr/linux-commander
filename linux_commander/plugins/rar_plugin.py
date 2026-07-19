"""VFS plugin for browsing RAR archives (read-only).

Requires the ``rarfile`` third-party package.  If it is not installed the
plugin silently registers no extensions and the app starts normally.

Write operations are not supported — rarfile provides no public write API.
"""

from __future__ import annotations

import io
from typing import BinaryIO

from linux_commander.vfs import FileEntry, FileSystem, StatResult, VfsPath

try:
    import rarfile as _rarfile_mod  # type: ignore[import-untyped]

    EXTENSIONS: tuple[str, ...] = (".rar",)
except ImportError:
    _rarfile_mod = None  # type: ignore[assignment]
    EXTENSIONS = ()


class RarFileSystem(FileSystem):
    """Read-only VFS backend backed by a RAR archive.

    ``parts`` encoding inside the RAR:
    - Archive root:         ``("",)``
    - ``subdir/``:          ``("", "subdir")``
    - ``subdir/file.txt``:  ``("", "subdir", "file.txt")``
    """

    writable: bool = False

    def __init__(self, archive_path: str, host_vpath: VfsPath) -> None:
        from pathlib import Path

        if _rarfile_mod is None:
            raise OSError("rarfile package is not installed")
        self._archive_path = archive_path
        self._host_vpath = host_vpath
        self.display_prefix = f"rar:{Path(archive_path).name}!"
        try:
            self._rf = _rarfile_mod.RarFile(archive_path)
        except Exception as exc:
            raise OSError(f"Cannot open RAR archive '{archive_path}': {exc}") from exc
        self._build_index()

    # -- index -----------------------------------------------------------------

    def _build_index(self) -> None:
        self._members: dict[str, object] = {}  # member_path_str → RarInfo
        self._dirs: set[str] = set()
        for info in self._rf.infolist():  # type: ignore[union-attr]
            name = info.filename.replace("\\", "/").rstrip("/")
            if not name:
                continue
            if info.is_dir():
                self._dirs.add(name)
            else:
                self._members[name] = info
            # Synthesize every ancestor directory
            parts = name.split("/")
            for i in range(1, len(parts)):
                self._dirs.add("/".join(parts[:i]))

    # -- path helpers ----------------------------------------------------------

    def _to_rar_path(self, path: VfsPath) -> str:
        if len(path.parts) <= 1:
            return ""
        return "/".join(path.parts[1:])

    def _from_rar_path(self, rar_name: str, parent: VfsPath) -> VfsPath:
        return VfsPath(fs=self, parts=("",) + tuple(rar_name.split("/")))

    # -- FileSystem API --------------------------------------------------------

    def list_dir(self, path: VfsPath) -> list[FileEntry]:
        rar_dir = self._to_rar_path(path)
        parent_entry = FileEntry(
            name="..",
            path=self._host_vpath.parent if path.parts == ("",) else path.parent,
            is_dir=True,
            size=0,
            mtime=0.0,
            is_parent=True,
        )
        entries: list[FileEntry] = [parent_entry]
        prefix = f"{rar_dir}/" if rar_dir else ""
        seen: set[str] = set()
        for name in list(self._dirs) + list(self._members.keys()):
            if not name.startswith(prefix):
                continue
            rest = name[len(prefix) :]
            if not rest or "/" in rest:
                continue
            if rest in seen:
                continue
            seen.add(rest)
            child_path = path / rest
            is_dir = name in self._dirs
            info = self._members.get(name)
            size = 0 if is_dir or info is None else getattr(info, "file_size", 0)
            mtime = 0.0
            if info is not None:
                dt = getattr(info, "mtime", None)
                if dt is not None:
                    try:
                        mtime = dt.timestamp()
                    except Exception:
                        mtime = 0.0
            entries.append(
                FileEntry(name=rest, path=child_path, is_dir=is_dir, size=size, mtime=mtime)
            )
        return entries

    def stat(self, path: VfsPath) -> StatResult:
        if path.parts == ("",):
            return StatResult(is_dir=True, size=0, mtime=0.0)
        rar_name = self._to_rar_path(path)
        if rar_name in self._dirs:
            return StatResult(is_dir=True, size=0, mtime=0.0)
        info = self._members.get(rar_name)
        if info is not None:
            size = getattr(info, "file_size", 0)
            mtime = 0.0
            dt = getattr(info, "mtime", None)
            if dt is not None:
                try:
                    mtime = dt.timestamp()
                except Exception:
                    mtime = 0.0
            return StatResult(is_dir=False, size=size, mtime=mtime)
        raise OSError(f"Not found in archive: {rar_name!r}")

    def open_read(self, path: VfsPath) -> BinaryIO:
        rar_name = self._to_rar_path(path)
        if rar_name not in self._members:
            raise OSError(f"Not found in archive: {rar_name!r}")
        try:
            with self._rf.open(rar_name) as f:  # type: ignore[union-attr]
                return io.BytesIO(f.read())
        except Exception as exc:
            raise OSError(f"Cannot read '{rar_name}': {exc}") from exc

    def read_prefix(self, path: VfsPath, max_bytes: int) -> tuple[bytes, bool]:
        rar_name = self._to_rar_path(path)
        if rar_name not in self._members:
            raise OSError(f"Not found in archive: {rar_name!r}")
        try:
            with self._rf.open(rar_name) as f:  # type: ignore[union-attr]
                data = f.read(max_bytes + 1)
        except Exception as exc:
            raise OSError(f"Cannot read '{rar_name}': {exc}") from exc
        truncated = len(data) > max_bytes
        if truncated:
            data = data[:max_bytes]
        return data, truncated

    def realpath(self, path: VfsPath) -> None:  # type: ignore[override]
        return None

    def close(self) -> None:
        try:
            self._rf.close()  # type: ignore[union-attr]
        except Exception:
            pass


def open_fs(host_fs: FileSystem, host_path: VfsPath) -> RarFileSystem:
    """Create a RarFileSystem for the archive at *host_path*."""
    from linux_commander.plugins import materialize

    real = materialize(host_fs, host_path)
    return RarFileSystem(str(real), host_path)
