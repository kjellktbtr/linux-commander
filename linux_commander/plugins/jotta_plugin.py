"""VFS plugin for browsing and managing Jottacloud (JFS API).

Supports listing directories, downloading/uploading files, creating folders,
deleting (to the Jottacloud trash), and renaming/moving. Uses the JottaAPI
client from linux_commander.jotta_api.

Authentication uses a Personal Login Token from Jottacloud web UI
(https://www.jottacloud.com/web/secure), which is exchanged for
OAuth2 access/refresh tokens. Tokens are persisted and auto-refreshed.
"""

from __future__ import annotations

import io
import urllib.parse
from collections.abc import Iterable
from typing import TYPE_CHECKING, BinaryIO

from linux_commander.vfs import FileEntry, FileSystem, StatResult, VfsPath

if TYPE_CHECKING:
    from linux_commander.jotta_api import SyncJottaAPI


try:
    from linux_commander.jotta_api import JottaAPIError, JottaAuthError, SyncJottaAPI

    HAS_JOTTA_API = True
except ImportError:
    SyncJottaAPI = None  # type: ignore[misc,assignment]
    JottaAuthError = Exception  # type: ignore[misc,assignment]
    JottaAPIError = Exception  # type: ignore[misc,assignment]
    HAS_JOTTA_API = False


SCHEMES: tuple[str, ...] = ("jotta",) if HAS_JOTTA_API else ()


class _JottaUploadFile:
    """Write-mode file handle for Jottacloud uploads.

    JFS uploads are a single POST carrying the full content (with md5/size
    known up front), so writes buffer to a ``BytesIO`` and only hit the
    network on :meth:`close` -- same approach as ``_WebDAVFile`` in
    ``webdav_plugin.py``.
    """

    def __init__(
        self,
        api: SyncJottaAPI,
        jfs_path: str,
        device: str,
        mountpoint: str,
    ) -> None:
        self._api = api
        self._jfs_path = jfs_path
        self._device = device
        self._mountpoint = mountpoint
        self._buffer = io.BytesIO()
        self._closed = False

    def write(self, b: bytes | bytearray | memoryview) -> int:
        if self._closed:
            raise ValueError("I/O operation on closed file")
        return self._buffer.write(b)

    def writelines(self, lines: Iterable[bytes]) -> None:
        for line in lines:
            self.write(line)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._api.upload_file(
                self._jfs_path,
                self._buffer.getvalue(),
                device=self._device,
                mountpoint=self._mountpoint,
            )
        except JottaAuthError as e:
            raise OSError(f"Authentication failed: {e}") from e
        except JottaAPIError as e:
            raise OSError(f"Cannot upload '{self._jfs_path}': {e}") from e
        finally:
            self._buffer.close()

    def flush(self) -> None:
        pass

    @property
    def closed(self) -> bool:
        return self._closed

    def readable(self) -> bool:
        return False

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def fileno(self) -> int:
        raise OSError("Not a real file descriptor")

    def isatty(self) -> bool:
        return False

    def __enter__(self) -> _JottaUploadFile:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


class JottaFileSystem(FileSystem):
    """Read/write VFS backend backed by Jottacloud JFS API.

    The VFS root maps to a specific device/mountpoint combination.
    Path structure: /username/device/mountpoint/path...
    """

    writable: bool = True

    def __init__(
        self,
        api: SyncJottaAPI,
        username: str,
        device: str,
        mountpoint: str,
        root_path: str = "/",
    ) -> None:
        self._api = api
        self._username = username
        self._device = device
        self._mountpoint = mountpoint
        self._root = root_path.rstrip("/") or "/"
        self.display_prefix = f"jotta://{username}@{device}/{mountpoint}!"

    # -- helpers --------------------------------------------------------------

    def _to_jfs_path(self, path: VfsPath) -> str:
        """Convert VfsPath to JFS relative path (without username/device/mountpoint)."""
        if len(path.parts) <= 1:
            return self._root
        suffix = "/".join(path.parts[1:])
        base = self._root if self._root != "/" else ""
        return f"{base}/{suffix}".lstrip("/")

    def _to_vfs_path(self, jfs_path: str, name: str) -> VfsPath:
        """Convert JFS path to VfsPath."""
        if jfs_path == self._root or jfs_path == "/":
            return VfsPath(fs=self, parts=("", name))
        # Remove root prefix
        if jfs_path.startswith(self._root + "/"):
            rel = jfs_path[len(self._root) + 1 :]
        elif jfs_path.startswith("/"):
            rel = jfs_path[1:]
        else:
            rel = jfs_path
        parts = ("",) + tuple(p for p in rel.split("/") if p)
        return VfsPath(fs=self, parts=parts)

    # -- FileSystem API -------------------------------------------------------

    def list_dir(self, path: VfsPath) -> list[FileEntry]:
        jfs_path = self._to_jfs_path(path)
        try:
            folders, files = self._api.list_files(
                path=jfs_path, device=self._device, mountpoint=self._mountpoint
            )
        except JottaAuthError as e:
            raise OSError(f"Authentication failed: {e}") from e
        except JottaAPIError as e:
            raise OSError(f"Cannot list '{jfs_path}': {e}") from e

        # Always prepend ".." entry for navigation (MountManager handles root)
        parent_entry = FileEntry(
            name="..",
            path=path.parent,
            is_dir=True,
            size=0,
            mtime=0.0,
            is_parent=True,
        )

        entries = [parent_entry]

        for folder in folders:
            if folder.deleted:
                # JFS keeps trashed items in listings (marked via a `deleted`
                # attribute) rather than dropping them -- without this check
                # a successfully deleted folder just keeps showing up.
                continue
            folder_path = folder.path or ""
            vpath = self._to_vfs_path(folder_path, folder.name)
            entries.append(
                FileEntry(
                    name=folder.name,
                    path=vpath,
                    is_dir=True,
                    size=0,
                    mtime=folder.modified.timestamp() if folder.modified else 0.0,
                )
            )

        for file in files:
            if file.deleted:
                continue
            vpath = self._to_vfs_path(file.path, file.name)
            entries.append(
                FileEntry(
                    name=file.name,
                    path=vpath,
                    is_dir=False,
                    size=file.size,
                    mtime=file.modified.timestamp() if file.modified else 0.0,
                )
            )

        return entries

    def stat(self, path: VfsPath) -> StatResult:
        if path.parts == ("",):
            return StatResult(is_dir=True, size=0, mtime=0.0)

        # A GET on `path`'s own JFS path lists *its children*, not `path`
        # itself -- there is no lightweight single-item JFS endpoint, so the
        # only correct way to learn `path`'s own type/size/mtime is to list
        # its *parent* and find the matching child by name (same as
        # list_dir() already does correctly for every entry it shows).
        parent = path.parent
        parent_jfs_path = self._to_jfs_path(parent)
        try:
            folders, files = self._api.list_files(
                path=parent_jfs_path, device=self._device, mountpoint=self._mountpoint
            )
        except JottaAuthError as e:
            raise OSError(f"Authentication failed: {e}") from e
        except JottaAPIError as e:
            raise OSError(f"Cannot stat '{path.name}': {e}") from e

        name = path.name
        for f in files:
            if f.name == name and not f.deleted:
                return StatResult(
                    is_dir=False,
                    size=f.size,
                    mtime=f.modified.timestamp() if f.modified else 0.0,
                )
        for folder in folders:
            if folder.name == name and not folder.deleted:
                return StatResult(
                    is_dir=True,
                    size=0,
                    mtime=folder.modified.timestamp() if folder.modified else 0.0,
                )

        raise OSError(f"Not found: {path.name}")

    def open_read(self, path: VfsPath) -> BinaryIO:
        jfs_path = self._to_jfs_path(path)
        try:
            resp = self._api.get_file(
                path=jfs_path, device=self._device, mountpoint=self._mountpoint
            )
        except JottaAuthError as e:
            raise OSError(f"Authentication failed: {e}") from e
        except JottaAPIError as e:
            raise OSError(f"Cannot open '{jfs_path}': {e}") from e

        # Return a BytesIO with the full content for seekability
        return io.BytesIO(resp.content)

    def open_write(self, path: VfsPath) -> BinaryIO:
        jfs_path = self._to_jfs_path(path)
        return _JottaUploadFile(  # type: ignore[return-value]
            self._api, jfs_path, self._device, self._mountpoint
        )

    def mkdir(self, path: VfsPath) -> None:
        jfs_path = self._to_jfs_path(path)
        try:
            self._api.create_folder(jfs_path, device=self._device, mountpoint=self._mountpoint)
        except JottaAuthError as e:
            raise OSError(f"Authentication failed: {e}") from e
        except JottaAPIError as e:
            raise OSError(f"Cannot create directory '{jfs_path}': {e}") from e

    def delete(self, path: VfsPath) -> None:
        jfs_path = self._to_jfs_path(path)
        is_dir = self.stat(path).is_dir
        try:
            self._api.delete_path(
                jfs_path, is_dir=is_dir, device=self._device, mountpoint=self._mountpoint
            )
        except JottaAuthError as e:
            raise OSError(f"Authentication failed: {e}") from e
        except JottaAPIError as e:
            raise OSError(f"Cannot delete '{jfs_path}': {e}") from e

    def rename(self, src: VfsPath, dst: VfsPath) -> None:
        src_path = self._to_jfs_path(src)
        dst_path = self._to_jfs_path(dst)
        is_dir = self.stat(src).is_dir
        try:
            self._api.move_path(
                src_path,
                dst_path,
                is_dir=is_dir,
                device=self._device,
                mountpoint=self._mountpoint,
            )
        except JottaAuthError as e:
            raise OSError(f"Authentication failed: {e}") from e
        except JottaAPIError as e:
            raise OSError(f"Cannot rename '{src_path}' to '{dst_path}': {e}") from e

    def close(self) -> None:
        """Release API client resources."""
        self._api.close()


def connect(
    host: str,
    port: int,
    user: str,
    password: str,
    key_path: str,
    key_passphrase: str,
    path: str,
) -> JottaFileSystem:
    """Legacy connect fallback - not used for Jottacloud."""
    raise NotImplementedError(
        "Jottacloud connections use Personal Login Token, not host/port/user/password. "
        "Use connect_fs() with a jotta:// URL containing the token."
    )


def connect_fs(url: str) -> JottaFileSystem:
    """Create a JottaFileSystem from a jotta:// URL.

    URL format: jotta://<token>@<device>/<mountpoint>[/<path>]
    Where <token> is a Personal Login Token from https://www.jottacloud.com/web/secure

    Example:
        jotta://ABC123...@Jotta/Archive/Documents

    Note: this always performs a fresh Personal Login Token exchange, and that
    token is single-use (Jottacloud invalidates it after one exchange). This
    entry point is only suitable for one-off/bootstrap connections. The Remote
    Connections dialog (ftp_dialog.py) does NOT use this function for saved
    Jottacloud sessions — it exchanges the login token once, then persists and
    reuses the resulting OAuth refresh token via ``SyncJottaAPI.restore()``.
    """
    if not HAS_JOTTA_API:
        raise RuntimeError("Jottacloud support not available (jotta_api import failed)")

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "jotta":
        raise ValueError(f"Expected jotta:// URL, got {parsed.scheme}://")

    # Extract token from userinfo
    token = urllib.parse.unquote(parsed.username) if parsed.username else ""
    if not token:
        raise ValueError("Personal Login Token required in URL (jotta://TOKEN@...)")

    # Path: /device/mountpoint/path...
    path_parts = [p for p in parsed.path.split("/") if p]
    if len(path_parts) < 2:
        raise ValueError(
            "URL must include at least device and mountpoint: jotta://TOKEN@device/mountpoint"
        )

    device = path_parts[0]
    mountpoint = path_parts[1]
    root_path = "/" + "/".join(path_parts[2:]) if len(path_parts) > 2 else "/"

    # Authenticate and get username
    api = SyncJottaAPI()
    try:
        api.authenticate(token)
    except (JottaAuthError, JottaAPIError) as e:
        api.close()
        raise OSError(f"Failed to authenticate with Jottacloud: {e}") from e

    username = api.username
    if not username:
        api.close()
        raise OSError("Failed to get username from token")

    return JottaFileSystem(
        api=api,
        username=username,
        device=device,
        mountpoint=mountpoint,
        root_path=root_path,
    )
