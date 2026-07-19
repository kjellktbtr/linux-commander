"""VFS plugin for browsing and writing 7-Zip archives (.7z).

Requires the ``py7zr`` third-party package.  If it is not installed the
plugin silently registers no extensions and the app starts normally.

Write operations perform a full archive rewrite (py7zr cannot append to
an existing 7z file), using the same atomic temp-file + os.replace pattern
as the tar/zip plugins.
"""

from __future__ import annotations

import io
import os
import pathlib
import shutil
import tempfile
from typing import BinaryIO

from linux_commander.vfs import FileEntry, FileSystem, StatResult, VfsPath

try:
    import py7zr as _py7zr_mod

    EXTENSIONS: tuple[str, ...] = (".7z",)
except ImportError:
    _py7zr_mod = None  # type: ignore[assignment]
    EXTENSIONS = ()


class SevenZipFileSystem(FileSystem):
    """Read-write VFS backend backed by a 7-Zip archive.

    ``parts`` encoding inside the archive:
    - Archive root:         ``("",)``
    - ``subdir/``:          ``("", "subdir")``
    - ``subdir/file.txt``:  ``("", "subdir", "file.txt")``

    Write operations (open_write, mkdir, delete, rename) always rewrite the
    full archive to a sibling temp file and atomically replace the original.
    """

    writable: bool = True

    def __init__(self, archive_path: pathlib.Path, host_vpath: VfsPath) -> None:
        if _py7zr_mod is None:
            raise OSError("py7zr package is not installed")
        self._archive_path = archive_path
        self._host_vpath = host_vpath
        self.display_prefix = f"7z:{archive_path.name}!"
        self._build_index()

    # -- index -----------------------------------------------------------------

    def _build_index(self) -> None:
        """Build lookup tables from the archive's file listing."""
        self._members: dict[str, object] = {}  # name → FileInfo
        self._dirs: set[str] = set()
        try:
            with _py7zr_mod.SevenZipFile(self._archive_path, "r") as sz:  # type: ignore[union-attr]
                for info in sz.list():
                    name = info.filename.replace("\\", "/").rstrip("/")
                    if not name:
                        continue
                    if info.is_directory:
                        self._dirs.add(name)
                    else:
                        self._members[name] = info
                    # Synthesize ancestor directories
                    parts = name.split("/")
                    for i in range(1, len(parts)):
                        self._dirs.add("/".join(parts[:i]))
        except Exception as exc:
            raise OSError(f"Cannot open 7z archive '{self._archive_path}': {exc}") from exc

    # -- path helpers ----------------------------------------------------------

    def _to_7z_path(self, path: VfsPath) -> str:
        if len(path.parts) <= 1:
            return ""
        return "/".join(path.parts[1:])

    # -- internal read helper --------------------------------------------------

    def _extract_to_bytes(self, member_name: str) -> bytes:
        """Extract a single member and return its bytes."""
        tmpdir = tempfile.mkdtemp()
        try:
            with _py7zr_mod.SevenZipFile(self._archive_path, "r") as sz:  # type: ignore[union-attr]
                sz.extract(path=tmpdir, targets=[member_name])
            extracted = pathlib.Path(tmpdir, member_name)
            if not extracted.exists():
                raise OSError(f"Extraction produced no file for '{member_name}'")
            return extracted.read_bytes()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # -- rewrite helpers -------------------------------------------------------

    def _rewrite(
        self,
        *,
        keep: set[str] | None = None,
        rename: dict[str, str] | None = None,
        extra: dict[str, bytes] | None = None,
    ) -> None:
        """Full-rewrite the archive, applying structural changes atomically.

        Args:
            keep: If given, only keep members whose names are in this set.
                  Pass ``None`` to keep everything.
            rename: Map of old_name → new_name for member renames.
            extra: New members to add/overwrite: member_name → bytes.
        """
        fd, tmp_path_str = tempfile.mkstemp(suffix=".7z", dir=str(self._archive_path.parent))
        tmp_path = pathlib.Path(tmp_path_str)
        os.close(fd)
        try:
            with _py7zr_mod.SevenZipFile(tmp_path, "w") as out_sz:  # type: ignore[union-attr]
                # Copy existing members (possibly renamed or filtered)
                for name in list(self._members.keys()):
                    new_name = (rename or {}).get(name, name)
                    if keep is not None and name not in keep:
                        continue
                    data = self._extract_to_bytes(name)
                    out_sz.writestr(data, new_name)

                # Directories emerge implicitly from file paths in py7zr, so
                # there is nothing to emit here; we only iterate to filter them.

                # Extra new / overwrite members
                for new_name, data in (extra or {}).items():
                    out_sz.writestr(data, new_name)

                # extra_dirs are created implicitly when file paths contain them;
                # no explicit directory-entry API exists in py7zr.

            os.replace(tmp_path_str, str(self._archive_path))
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        self._build_index()

    # -- FileSystem API --------------------------------------------------------

    def list_dir(self, path: VfsPath) -> list[FileEntry]:
        sz_dir = self._to_7z_path(path)
        parent_path = self._host_vpath.parent if path.parts == ("",) else path.parent
        entries: list[FileEntry] = [
            FileEntry(
                name="..",
                path=parent_path,
                is_dir=True,
                size=0,
                mtime=0.0,
                is_parent=True,
            )
        ]
        prefix = f"{sz_dir}/" if sz_dir else ""
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
            size = 0 if is_dir or info is None else getattr(info, "uncompressed", 0) or 0
            mtime = 0.0
            if info is not None:
                dt = getattr(info, "creationtime", None)
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
        name = self._to_7z_path(path)
        if name in self._dirs:
            return StatResult(is_dir=True, size=0, mtime=0.0)
        info = self._members.get(name)
        if info is not None:
            size = getattr(info, "uncompressed", 0) or 0
            mtime = 0.0
            dt = getattr(info, "creationtime", None)
            if dt is not None:
                try:
                    mtime = dt.timestamp()
                except Exception:
                    mtime = 0.0
            return StatResult(is_dir=False, size=size, mtime=mtime)
        raise OSError(f"Not found in archive: {name!r}")

    def open_read(self, path: VfsPath) -> BinaryIO:
        name = self._to_7z_path(path)
        if name not in self._members:
            raise OSError(f"Not found in archive: {name!r}")
        return io.BytesIO(self._extract_to_bytes(name))

    def read_prefix(self, path: VfsPath, max_bytes: int) -> tuple[bytes, bool]:
        data = self.open_read(path).read()
        truncated = len(data) > max_bytes
        if truncated:
            data = data[:max_bytes]
        return data, truncated

    def open_write(self, path: VfsPath) -> BinaryIO:
        """Return a buffer; its contents are written to the archive on close."""
        name = self._to_7z_path(path)
        fs = self

        class _WriteBuffer(io.RawIOBase):
            def __init__(self) -> None:
                self._buf = io.BytesIO()

            def write(self, b: bytes | bytearray) -> int:  # type: ignore[override]
                return self._buf.write(b)

            def close(self) -> None:
                if not self.closed:
                    data = self._buf.getvalue()
                    fs._rewrite(
                        keep=set(fs._members.keys()),
                        extra={name: data},
                    )
                    super().close()

        return _WriteBuffer()  # type: ignore[return-value]

    def mkdir(self, path: VfsPath) -> None:
        name = self._to_7z_path(path)
        if name in self._dirs:
            raise OSError(f"Directory already exists: {name!r}")
        # Add an empty directory by synthesizing it in the index;
        # py7zr stores dirs implicitly so just update our index.
        self._dirs.add(name)
        # Also trigger a rewrite to persist (write a zero-byte placeholder).
        self._rewrite(
            keep=set(self._members.keys()),
            extra={f"{name}/.keep": b""},
        )

    def delete(self, path: VfsPath) -> None:
        name = self._to_7z_path(path)
        if name in self._dirs:
            # Remove all members under this directory
            keep = {k for k in self._members if not k.startswith(f"{name}/")}
        elif name in self._members:
            keep = set(self._members.keys()) - {name}
        else:
            raise OSError(f"Not found in archive: {name!r}")
        self._rewrite(keep=keep)

    def rename(self, src: VfsPath, dst: VfsPath) -> None:
        src_name = self._to_7z_path(src)
        dst_name = self._to_7z_path(dst)
        if src_name not in self._members and src_name not in self._dirs:
            raise OSError(f"Not found in archive: {src_name!r}")
        rename_map = {src_name: dst_name}
        self._rewrite(keep=set(self._members.keys()), rename=rename_map)

    def realpath(self, path: VfsPath) -> None:  # type: ignore[override]
        return None

    def close(self) -> None:
        pass  # no persistent file handle (opened fresh per operation)


def open_fs(host_fs: FileSystem, host_path: VfsPath) -> SevenZipFileSystem:
    """Create a SevenZipFileSystem for the archive at *host_path*."""
    from linux_commander.plugins import materialize

    real = materialize(host_fs, host_path)
    return SevenZipFileSystem(real, host_path)
