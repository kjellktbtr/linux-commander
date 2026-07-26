"""Virtual Filesystem abstraction.

Defines VfsPath, FileEntry, FileSystem (abstract base), LocalFileSystem, and
MountManager.  Together these decouple the UI from the host OS so that archive
files, FTP connections, and other backends can be browsed identically.
"""

from __future__ import annotations

import os
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

# ---------------------------------------------------------------------------
# Lightweight metadata type
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StatResult:
    """Portable stat-like metadata for a single VFS path."""

    is_dir: bool
    size: int
    mtime: float


# ---------------------------------------------------------------------------
# FileSystem abstract base classes — split into Readable + Writable mixins
# so that read-only backends (archives) aren't forced to inherit write methods
# they can never implement (ISP).  The combined `FileSystem` class provides
# backward compatibility for code that expects a single base.
# ---------------------------------------------------------------------------


class ReadableFileSystem(ABC):
    """Abstract base for read-only VFS backends (archives, remote read-only).

    Subclasses must implement ``list_dir``, ``stat``, and ``open_read``.
    ``display_prefix`` is prepended to the path string shown in panel headers
    and window titles.
    """

    display_prefix: str = ""

    @abstractmethod
    def list_dir(self, path: VfsPath) -> list[FileEntry]:
        """Return the contents of ``path`` as ``FileEntry`` rows.

        A synthetic ``..`` entry is prepended except at the absolute root of
        the filesystem (where there is nowhere to go up to).
        """

    def list_dir_flat(self, path: VfsPath) -> list[FileEntry]:
        """Return a flat list of all files under ``path`` recursively.

        The default implementation returns an empty list.  Subclasses that
        support flat view (e.g. LocalFileSystem) should override this.
        """
        return []

    @abstractmethod
    def stat(self, path: VfsPath) -> StatResult:
        """Return metadata for ``path``.  Raises ``OSError`` if not found."""

    @abstractmethod
    def open_read(self, path: VfsPath) -> BinaryIO:
        """Open ``path`` for binary reading.  May return a non-seekable stream."""

    def realpath(self, path: VfsPath) -> Path | None:
        """Return the OS-level ``pathlib.Path`` for ``path``, or ``None`` if the
        path is not backed by a real file (e.g. inside an archive or remote)."""
        return None

    def read_prefix(self, path: VfsPath, max_bytes: int) -> tuple[bytes, bool]:
        """Read at most ``max_bytes`` bytes from ``path``.

        Returns ``(data, truncated)`` where ``truncated`` is True when the file
        is larger than ``max_bytes``.  The default implementation uses
        ``open_read``; backends with eager full-file downloads (FTP, tar) should
        override this to stop reading early.
        """
        with self.open_read(path) as f:
            data = f.read(max_bytes + 1)
        truncated = len(data) > max_bytes
        if truncated:
            data = data[:max_bytes]
        return data, truncated

    def close(self) -> None:  # noqa: B027
        """Release held resources (open handles, temp files).  No-op by default."""


class WritableFileSystem(ABC):
    """Abstract base for writable VFS backends (local disk, network shares).

    Subclasses must implement ``open_write``, ``mkdir``, ``delete``, and
    ``rename``.  Combine with ``ReadableFileSystem`` for a full-featured
    backend.
    """

    @abstractmethod
    def open_write(self, path: VfsPath) -> BinaryIO:
        """Open ``path`` for binary writing."""

    @abstractmethod
    def mkdir(self, path: VfsPath) -> None:
        """Create ``path`` as a new directory."""

    @abstractmethod
    def delete(self, path: VfsPath) -> None:
        """Delete ``path``."""

    @abstractmethod
    def rename(self, src: VfsPath, dst: VfsPath) -> None:
        """Rename ``src`` to ``dst``."""

    def set_mtime(self, path: VfsPath, mtime: float) -> None:  # noqa: B027
        """Set the modification time of ``path`` to ``mtime`` (epoch seconds).

        No-op by default.  Writable backends that support preserving timestamps
        (SFTP, SMB, WebDAV, local disk) should override this.
        """
        pass


class FileSystem(ReadableFileSystem, WritableFileSystem):
    """Combined abstract base for full-featured VFS backends.

    Inherits both read and write capabilities.  Use this for backends that
    support both operations (e.g. LocalFileSystem, SFTP, SMB).  For
    read-only backends (archives), inherit ``ReadableFileSystem`` directly.
    """


# ---------------------------------------------------------------------------
# VfsPath value object
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VfsPath:
    """An immutable path that binds a location to its owning ``FileSystem``.

    ``parts`` uses a POSIX-like encoding:

    - Filesystem root:        ``("",)``
    - ``/home/user/foo``:   ``("", "home", "user", "foo")``

    Equality and hashing include the ``fs`` field so paths on different backends
    never compare equal even when their parts match.  This is critical for
    ``panel.marked: set[VfsPath]`` correctness across re-listings.
    """

    fs: ReadableFileSystem
    parts: tuple[str, ...]

    # -- path-like properties -------------------------------------------------

    @property
    def name(self) -> str:
        """The final path component, e.g. ``"foo.zip"``."""
        return self.parts[-1] if self.parts else ""

    @property
    def parent(self) -> VfsPath:
        """The parent path.  Returns ``self`` at the filesystem root."""
        if len(self.parts) <= 1:
            return self
        return VfsPath(fs=self.fs, parts=self.parts[:-1])

    @property
    def suffix(self) -> str:
        """The file extension including the leading dot, e.g. ``".zip"``.
        Returns ``""`` for dotfiles such as ``".gitignore"``."""
        name = self.name
        idx = name.rfind(".")
        if idx > 0:
            return name[idx:]
        return ""

    def __truediv__(self, child: str) -> VfsPath:
        """Append ``child`` as the next path component."""
        return VfsPath(fs=self.fs, parts=self.parts + (child,))

    def __str__(self) -> str:
        """Human-readable path string for panel headers and window titles."""
        raw = "/".join(self.parts)
        return self.fs.display_prefix + (raw if raw else "/")


# ---------------------------------------------------------------------------
# FileEntry — moved here from fs.py so VfsPath can be its `path` type without
# creating a circular import between vfs.py and fs.py.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FileEntry:
    """A single row in a directory listing.

    ``path`` is a ``VfsPath`` (was ``pathlib.Path``) so it carries its owning
    filesystem backend — required for operations and the viewer to dispatch to
    the correct implementation.
    """

    name: str
    path: VfsPath
    is_dir: bool
    size: int
    mtime: float
    is_parent: bool = False
    """True only for the synthetic ``..`` row used to navigate up."""


# ---------------------------------------------------------------------------
# LocalFileSystem — the real OS backend
# ---------------------------------------------------------------------------


class LocalFileSystem(FileSystem):
    """VFS backend backed by the host OS via ``pathlib`` / ``shutil``.

    ``__eq__`` and ``__hash__`` use class identity rather than object identity
    so that two independently-created ``LocalFileSystem()`` instances compare
    equal.  This is correct because there is only one host OS filesystem;
    it lets ``VfsPath`` objects from different instances compare equal and be
    looked up correctly in ``panel.marked``.
    """

    display_prefix: str = ""
    writable: bool = True

    def __eq__(self, other: object) -> bool:
        return isinstance(other, LocalFileSystem)

    def __hash__(self) -> int:
        return hash(LocalFileSystem)

    # -- internal helpers -----------------------------------------------------

    def _to_path(self, vpath: VfsPath) -> Path:
        """Convert a ``VfsPath`` to a ``pathlib.Path`` for OS calls."""
        raw = "/".join(vpath.parts)
        return Path(raw) if raw else Path("/")

    def from_path(self, path: Path) -> VfsPath:
        """Convert a ``pathlib.Path`` to a ``VfsPath`` on this filesystem."""
        pts = path.parts
        if pts and pts[0] == "/":
            parts: tuple[str, ...] = ("",) + pts[1:]
        else:
            parts = tuple(pts)
        return VfsPath(fs=self, parts=parts)

    # -- FileSystem API -------------------------------------------------------

    def list_dir(self, path: VfsPath) -> list[FileEntry]:
        """List ``path``, prepending ``..`` unless at the local root ``/``."""
        real = self._to_path(path)
        entries: list[FileEntry] = []

        if path.parent != path:
            parent_real = self._to_path(path.parent)
            try:
                parent_mtime = parent_real.stat().st_mtime
            except OSError:
                parent_mtime = 0.0
            entries.append(
                FileEntry(
                    name="..",
                    path=path.parent,
                    is_dir=True,
                    size=0,
                    mtime=parent_mtime,
                    is_parent=True,
                )
            )

        try:
            children = list(real.iterdir())
        except (PermissionError, OSError):
            return entries

        for child in children:
            try:
                st = child.stat()
            except (PermissionError, OSError):
                continue
            entries.append(
                FileEntry(
                    name=child.name,
                    path=self.from_path(child),
                    is_dir=child.is_dir(),
                    size=0 if child.is_dir() else st.st_size,
                    mtime=st.st_mtime,
                )
            )

        return entries

    def list_dir_flat(self, path: VfsPath) -> list[FileEntry]:
        """List ``path`` recursively (flat view), returning all files and directories.

        Unlike ``list_dir``, this does not prepend a ``..`` entry and returns
        a flat list of all descendants with relative paths in ``name``.
        """
        real = self._to_path(path)
        entries: list[FileEntry] = []

        try:
            for root, dirs, files in os.walk(real):
                root_path = Path(root)
                # Calculate relative path from the base
                try:
                    rel = root_path.relative_to(real)
                except ValueError:
                    rel = Path(".")
                rel_str = str(rel) if rel != Path(".") else ""

                for d in dirs:
                    child = root_path / d
                    try:
                        st = child.stat()
                    except (PermissionError, OSError):
                        continue
                    name = os.path.join(rel_str, d) if rel_str else d
                    entries.append(
                        FileEntry(
                            name=name,
                            path=self.from_path(child),
                            is_dir=True,
                            size=0,
                            mtime=st.st_mtime,
                        )
                    )

                for f in files:
                    child = root_path / f
                    try:
                        st = child.stat()
                    except (PermissionError, OSError):
                        continue
                    name = os.path.join(rel_str, f) if rel_str else f
                    entries.append(
                        FileEntry(
                            name=name,
                            path=self.from_path(child),
                            is_dir=False,
                            size=st.st_size,
                            mtime=st.st_mtime,
                        )
                    )
        except (PermissionError, OSError):
            pass

        return entries

    def stat(self, path: VfsPath) -> StatResult:
        real = self._to_path(path)
        st = real.stat()
        return StatResult(is_dir=real.is_dir(), size=st.st_size, mtime=st.st_mtime)

    def open_read(self, path: VfsPath) -> BinaryIO:  # type: ignore[override]
        return self._to_path(path).open("rb")

    def open_write(self, path: VfsPath) -> BinaryIO:  # type: ignore[override]
        return self._to_path(path).open("wb")

    def realpath(self, path: VfsPath) -> Path:  # type: ignore[override]
        return self._to_path(path)

    def mkdir(self, path: VfsPath) -> None:
        self._to_path(path).mkdir(parents=False)

    def delete(self, path: VfsPath) -> None:
        real = self._to_path(path)
        if real.is_dir() and not real.is_symlink():
            shutil.rmtree(real)
        else:
            real.unlink()

    def rename(self, src: VfsPath, dst: VfsPath) -> None:
        self._to_path(src).rename(self._to_path(dst))

    def set_mtime(self, path: VfsPath, mtime: float) -> None:
        real = self._to_path(path)
        try:
            os.utime(real, (mtime, mtime))
        except OSError:
            pass

    def close(self) -> None:
        pass  # the host OS filesystem has no resources to release


# ---------------------------------------------------------------------------
# MountManager — plugin dispatch + refcounted mount lifecycle
# ---------------------------------------------------------------------------


class MountManager:
    """Manages VFS mounts (archive files, network connections).

    Tracks a refcount per mounted ``FileSystem`` so that two panels can
    independently browse inside the same archive without closing each
    other's handle.  ``close_all()`` is called on app quit to ensure
    every handle and temp file is released.
    """

    def __init__(self) -> None:
        # host_path → (fs, refcount)  — for extension mounts (zip, tar)
        self._mounts: dict[VfsPath, tuple[ReadableFileSystem, int]] = {}
        # id(fs) → host_path  (reverse lookup for resolve_up / release)
        self._fs_to_host: dict[int, VfsPath] = {}
        # id(fs) → (fs, refcount)  — for scheme mounts (ftp, …) with no host path
        self._scheme_fses: dict[int, tuple[ReadableFileSystem, int]] = {}

    def mount_scheme_fs(self, fs: ReadableFileSystem) -> VfsPath:
        """Register a scheme-based FS (FTP, SFTP, …) and return its root.

        Unlike ``enter()``, scheme FSes have no host path in ``_fs_to_host``:
        going up from their root stays at the root (like the local ``/``).
        Refcounted the same way so two panels on the same connection share one
        handle.
        """
        key = id(fs)
        if key in self._scheme_fses:
            fs_obj, count = self._scheme_fses[key]
            self._scheme_fses[key] = (fs_obj, count + 1)
        else:
            self._scheme_fses[key] = (fs, 1)
        return VfsPath(fs=fs, parts=("",))

    def enter(self, entry: FileEntry) -> VfsPath | None:
        """If ``entry``'s extension matches a plugin, mount and return its root.

        If the same archive is already mounted, the existing backend is reused
        and its refcount incremented.  Returns ``None`` when no plugin matches.
        """
        # Lazy import to avoid circular dependency (plugins imports vfs).
        # Use plugin_for_name so compound extensions (.tar.gz) match correctly.
        from linux_commander.plugins import plugin_for_name

        plugin = plugin_for_name(entry.name)
        if plugin is None:
            return None

        host_path = entry.path
        if host_path in self._mounts:
            fs, count = self._mounts[host_path]
            self._mounts[host_path] = (fs, count + 1)
        else:
            fs = plugin.open_fs(entry.path.fs, entry.path)
            self._mounts[host_path] = (fs, 1)
            self._fs_to_host[id(fs)] = host_path

        return VfsPath(fs=fs, parts=("",))

    def resolve_up(self, path: VfsPath) -> tuple[VfsPath, str]:
        """Navigate up from ``path``, crossing mount boundaries when necessary.

        Returns ``(new_path, select_name)`` where ``select_name`` is the name
        the cursor should land on after navigating up:

        - At a mounted-fs root: ``(host_directory, archive_filename)``
        - Otherwise: ``(path.parent, path.name)``
        - At the local root (``path.parent == path``): ``(path, "")`` — no move
        """
        if path.parts == ("",) and id(path.fs) in self._fs_to_host:
            host_path = self._fs_to_host[id(path.fs)]
            return host_path.parent, host_path.name
        if path.parent == path:
            return path, ""
        return path.parent, path.name

    def release(self, fs: ReadableFileSystem) -> None:
        """Decrement the refcount for ``fs``; close and remove at zero."""
        key = id(fs)
        # Scheme mounts (FTP, …) are tracked separately.
        if key in self._scheme_fses:
            fs_obj, count = self._scheme_fses[key]
            if count <= 1:
                del self._scheme_fses[key]
                try:
                    fs_obj.close()
                except Exception:
                    pass
            else:
                self._scheme_fses[key] = (fs_obj, count - 1)
            return
        host_path = self._fs_to_host.get(key)
        if host_path is None:
            return  # not mounted (e.g. LocalFileSystem) — ignore
        entry = self._mounts.get(host_path)
        if entry is None:
            return
        fs_obj, count = entry
        if count <= 1:
            del self._mounts[host_path]
            del self._fs_to_host[key]
            try:
                fs_obj.close()
            except Exception:
                pass
        else:
            self._mounts[host_path] = (fs_obj, count - 1)

    def release_if_leaving(self, old_fs: ReadableFileSystem, new_fs: ReadableFileSystem) -> None:
        """Release ``old_fs`` unless ``new_fs`` is mounted *on top of* ``old_fs``.

        This prevents premature close when a panel enters a nested archive
        (zip-inside-tar): at that point ``old_fs`` is TarFS and ``new_fs`` is
        ZipFS whose host path lives on TarFS, so we must NOT release TarFS yet.
        """
        if old_fs is new_fs:
            return
        host_path = self._fs_to_host.get(id(new_fs))
        if host_path is not None and host_path.fs is old_fs:
            return  # entering a mount nested inside old_fs — keep it alive
        self.release(old_fs)

    def close_all(self) -> None:
        """Close all mounted filesystems; called on app quit.

        Iterates extension mounts in reverse insertion order so innermost mounts
        (e.g. a zip inside a tar) are closed before the outer ones that host them.
        Scheme mounts (FTP, …) are closed afterwards.
        """
        for fs, _ in reversed(list(self._mounts.values())):
            try:
                fs.close()
            except Exception:
                pass
        self._mounts.clear()
        self._fs_to_host.clear()
        for fs, _ in self._scheme_fses.values():
            try:
                fs.close()
            except Exception:
                pass
        self._scheme_fses.clear()
