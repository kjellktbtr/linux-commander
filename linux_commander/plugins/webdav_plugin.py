"""VFS plugin for browsing WebDAV servers.

Supports WebDAV (HTTP) and WebDAVS (HTTPS) protocols using the `webdavclient3`
library. Handles PROPFIND, GET, PUT, DELETE, MKCOL, COPY, MOVE operations.

Authentication: Basic auth via URL credentials or keyring integration.
"""

from __future__ import annotations

import io
from collections.abc import Iterable, Iterator
from typing import BinaryIO
from urllib.parse import unquote, urlparse

from linux_commander.vfs import FileEntry, FileSystem, StatResult, VfsPath

try:
    from webdav3.client import Client as WebDAVClient  # type: ignore[import-not-found]
    from webdav3.exceptions import WebDavException  # type: ignore[import-not-found]

    HAS_WEBDAV = True
except ImportError:
    WebDAVClient = None  # type: ignore[assignment,misc]
    WebDavException = Exception  # type: ignore[assignment,misc]
    HAS_WEBDAV = False

SCHEMES: tuple[str, ...] = ("webdav", "webdavs") if HAS_WEBDAV else ()


class _WebDAVFile:
    """File-like wrapper for WebDAV file handles."""

    def __init__(self, client: WebDAVClient, remote_path: str, mode: str) -> None:
        self._client = client
        self._remote_path = remote_path
        self._mode = mode
        self._buffer: io.BytesIO | None = None
        self._closed = False
        self._position = 0

        if "r" in mode:
            # For reading, download the whole file to a BytesIO buffer
            # (WebDAV doesn't support random access reads easily without Range header)
            self._buffer = io.BytesIO()
            self._client.download_sync(
                remote_path=self._remote_path, local_path=None, buff=self._buffer
            )
            self._buffer.seek(0)
        elif "w" in mode:
            self._buffer = io.BytesIO()

    def read(self, size: int | None = -1) -> bytes:
        if self._closed:
            raise ValueError("I/O operation on closed file")
        if self._buffer is None:
            return b""
        data = self._buffer.read(size)
        self._position += len(data)
        return data

    def write(self, b: bytes | bytearray | memoryview) -> int:
        if self._closed:
            raise ValueError("I/O operation on closed file")
        if self._buffer is None:
            return 0
        n = self._buffer.write(b)
        self._position += n
        return n

    def seek(self, offset: int, whence: int = 0) -> int:
        if self._closed:
            raise ValueError("I/O operation on closed file")
        if self._buffer is None:
            return 0
        self._position = self._buffer.seek(offset, whence)
        return self._position

    def tell(self) -> int:
        return self._position

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if "w" in self._mode and self._buffer is not None:
            # Upload the buffer content to WebDAV
            self._buffer.seek(0)
            try:
                self._client.upload_sync(
                    remote_path=self._remote_path, local_path=None, buff=self._buffer
                )
            except WebDavException as exc:
                raise OSError(f"Failed to upload '{self._remote_path}': {exc}") from exc
        if self._buffer is not None:
            self._buffer.close()
            self._buffer = None

    def flush(self) -> None:
        if self._buffer is not None:
            self._buffer.flush()

    @property
    def closed(self) -> bool:
        return self._closed

    def readable(self) -> bool:
        return "r" in self._mode

    def writable(self) -> bool:
        return "w" in self._mode

    def seekable(self) -> bool:
        return True

    def fileno(self) -> int:
        raise OSError("Not a real file descriptor")

    def isatty(self) -> bool:
        return False

    def __enter__(self) -> _WebDAVFile:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def __iter__(self) -> Iterator[bytes]:
        if self._buffer is None:
            return iter([])
        return iter(self._buffer)

    def __next__(self) -> bytes:
        if self._buffer is None:
            raise StopIteration
        line = self._buffer.readline()
        if not line:
            raise StopIteration
        return line

    def writelines(self, lines: Iterable[bytes]) -> None:
        if self._buffer is None:
            return
        for line in lines:
            self._buffer.write(line)


class WebDAVFileSystem(FileSystem):
    """Writable VFS backend backed by a WebDAV connection.

    The VFS root maps to the remote base path. Sub-paths append to it.
    """

    writable: bool = True

    def __init__(
        self,
        client: WebDAVClient,
        host: str,
        base_path: str,
        user: str = "",
    ) -> None:
        self._client = client
        self._host = host
        self._base_path = base_path.rstrip("/") or "/"
        self._user = user
        self.display_prefix = f"webdav://{host}!"

    def _to_remote_path(self, path: VfsPath) -> str:
        """Convert VfsPath to remote WebDAV path."""
        if len(path.parts) <= 1:
            return self._base_path
        suffix = "/".join(path.parts[1:])
        base = self._base_path if self._base_path != "/" else ""
        return f"{base}/{suffix}".replace("//", "/")

    def _to_vfs_path(self, remote_path: str, name: str) -> VfsPath:
        """Convert remote path to VfsPath."""
        # Remove base path prefix
        if remote_path.startswith(self._base_path):
            rel = remote_path[len(self._base_path) :].lstrip("/")
        else:
            rel = remote_path.lstrip("/")
        if not rel:
            return VfsPath(fs=self, parts=("", name))
        parts = ("",) + tuple(p for p in rel.split("/") if p) + (name,)
        return VfsPath(fs=self, parts=parts)

    def list_dir(self, path: VfsPath) -> list[FileEntry]:
        remote_path = self._to_remote_path(path)

        # Ensure path ends with / for directory listing
        list_path = remote_path if remote_path.endswith("/") else remote_path + "/"

        parent_entry = FileEntry(
            name="..",
            path=path.parent,
            is_dir=True,
            size=0,
            mtime=0.0,
            is_parent=True,
        )
        entries: list[FileEntry] = [parent_entry]

        try:
            # Use client.list() which returns a list of dicts with file info
            items = self._client.list(list_path, get_info=True)
            for item in items:
                name = item.get("name", "").rstrip("/")
                if not name or name in (".", ".."):
                    continue

                is_dir = item.get("isdir", False)
                size = 0 if is_dir else int(item.get("size", 0))

                # Parse modification time
                mtime = 0.0
                modified_str = item.get("modified") or item.get("mtime")
                if modified_str:
                    try:
                        import datetime

                        # WebDAV dates are typically RFC 1123 or ISO 8601
                        for fmt in (
                            "%a, %d %b %Y %H:%M:%S %Z",
                            "%Y-%m-%dT%H:%M:%SZ",
                            "%Y-%m-%d %H:%M:%S",
                        ):
                            try:
                                mtime = datetime.datetime.strptime(modified_str, fmt).timestamp()
                                break
                            except ValueError:
                                continue
                    except Exception:
                        pass

                # The item path from list() is relative to the list_path
                full_remote = f"{list_path}{name}".replace("//", "/")
                vpath = self._to_vfs_path(full_remote, name)
                entries.append(
                    FileEntry(
                        name=name,
                        path=vpath,
                        is_dir=is_dir,
                        size=size,
                        mtime=mtime,
                    )
                )
        except WebDavException as exc:
            raise OSError(f"Cannot list '{remote_path}': {exc}") from exc

        return entries

    def stat(self, path: VfsPath) -> StatResult:
        if path.parts == ("",):
            return StatResult(is_dir=True, size=0, mtime=0.0)

        remote_path = self._to_remote_path(path)
        try:
            info = self._client.info(remote_path)
            is_dir = info.get("isdir", False)
            size = 0 if is_dir else int(info.get("size", 0))
            mtime = 0.0
            modified_str = info.get("modified") or info.get("mtime")
            if modified_str:
                try:
                    import datetime

                    for fmt in (
                        "%a, %d %b %Y %H:%M:%S %Z",
                        "%Y-%m-%dT%H:%M:%SZ",
                        "%Y-%m-%d %H:%M:%S",
                    ):
                        try:
                            mtime = datetime.datetime.strptime(modified_str, fmt).timestamp()
                            break
                        except ValueError:
                            continue
                except Exception:
                    pass
            return StatResult(is_dir=is_dir, size=size, mtime=mtime)
        except WebDavException as exc:
            raise OSError(f"Not found: {path}") from exc

    def open_read(self, path: VfsPath) -> BinaryIO:
        remote_path = self._to_remote_path(path)
        return _WebDAVFile(self._client, remote_path, "rb")  # type: ignore[return-value]

    def open_write(self, path: VfsPath) -> BinaryIO:
        remote_path = self._to_remote_path(path)
        return _WebDAVFile(self._client, remote_path, "wb")  # type: ignore[return-value]

    def mkdir(self, path: VfsPath) -> None:
        remote_path = self._to_remote_path(path)
        try:
            self._client.mkdir(remote_path)
        except WebDavException as exc:
            raise OSError(f"Cannot create directory '{remote_path}': {exc}") from exc

    def delete(self, path: VfsPath) -> None:
        remote_path = self._to_remote_path(path)
        try:
            # Check if it's a directory first
            info = self._client.info(remote_path)
            if info.get("isdir", False):
                # WebDAV delete on non-empty directory may fail; try anyway
                self._client.clean(remote_path)
            else:
                self._client.clean(remote_path)
        except WebDavException as exc:
            raise OSError(f"Cannot delete '{remote_path}': {exc}") from exc

    def rename(self, src: VfsPath, dst: VfsPath) -> None:
        src_path = self._to_remote_path(src)
        dst_path = self._to_remote_path(dst)
        try:
            # WebDAV uses COPY + DELETE for move (MOVE not universally supported)
            self._client.move(src_path, dst_path)
        except WebDavException as exc:
            raise OSError(f"Cannot rename '{src_path}' to '{dst_path}': {exc}") from exc

    def set_mtime(self, path: VfsPath, mtime: float) -> None:
        import datetime

        remote_path = self._to_remote_path(path)
        try:
            dt = datetime.datetime.fromtimestamp(mtime)
            self._client.set_properties(remote_path, {"lastmod": dt})
        except (WebDavException, OSError):
            pass

    def close(self) -> None:
        # webdav3 client doesn't have explicit close; connections are per-request
        pass


def connect_fs(url: str) -> WebDAVFileSystem:
    """Connect to a WebDAV server and return a WebDAVFileSystem.

    URL format: ``webdav://[user:pass@]host[:port][/path]`` or
    ``webdavs://[user:pass@]host[:port][/path]`` (HTTPS)

    If credentials are not in the URL, they will be prompted via the
    registered credential provider (if any) or left blank for anonymous access.
    """
    if not HAS_WEBDAV:
        raise RuntimeError(
            "webdavclient3 not installed. Install with 'pip install linux-commander[webdav]'"
        )

    parsed = urlparse(url)
    if parsed.scheme not in ("webdav", "webdavs"):
        raise ValueError(f"Expected webdav:// or webdavs:// URL, got {parsed.scheme}://")

    host = parsed.hostname or ""
    if not host:
        raise ValueError("Missing host in WebDAV URL")

    user = unquote(parsed.username) if parsed.username else ""
    password = unquote(parsed.password) if parsed.password else ""

    # Port handling
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "webdavs" else 80

    # Build the base URL for the client
    scheme = "https" if parsed.scheme == "webdavs" else "http"
    if port and port not in (80, 443):
        base_url = f"{scheme}://{host}:{port}"
    else:
        base_url = f"{scheme}://{host}"

    # Base path (the path component of the URL)
    base_path = parsed.path or "/"

    # Configure webdav3 client
    options = {
        "webdav_hostname": base_url,
        "webdav_login": user,
        "webdav_password": password,
        "webdav_root": base_path,  # This sets the root path for operations
    }

    # Create client
    client = WebDAVClient(options)

    # Verify connection by listing root
    try:
        client.list(base_path)
    except WebDavException as exc:
        raise OSError(f"Cannot connect to WebDAV server: {exc}") from exc

    return WebDAVFileSystem(client, host, base_path, user)
