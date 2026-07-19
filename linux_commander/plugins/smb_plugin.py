"""VFS plugin for browsing SMB/CIFS (Samba/Windows) shares.

Uses the ``smbprotocol`` library (modern SMB2/3 client) from the ``smb`` extra.
Registers the ``smb`` scheme. Falls back gracefully if the dependency is missing.

URL format: ``smb://[user:pass@]host/share[/path]``
"""

from __future__ import annotations

import os
import urllib.parse
from typing import TYPE_CHECKING, BinaryIO
from urllib.parse import unquote

from linux_commander.vfs import FileEntry, FileSystem, StatResult, VfsPath

if TYPE_CHECKING:
    from smbprotocol.connection import Connection
    from smbprotocol.exceptions import SMBException
    from smbprotocol.file import File
    from smbprotocol.open import (
        CreateDisposition,
        FileAttributes,
        ImpersonationLevel,
        Open,
        ShareAccess,
    )
    from smbprotocol.session import Session
    from smbprotocol.tree import TreeConnect

try:
    from smbprotocol.connection import Connection  # type: ignore[import-not-found]
    from smbprotocol.exceptions import SMBException  # type: ignore[import-not-found]
    from smbprotocol.file import File  # type: ignore[import-not-found]
    from smbprotocol.open import (
        CreateDisposition,
        CreateOptions,
        FileAttributes,
        FilePipePrinterAccessMask,
        ImpersonationLevel,
        Open,
        ShareAccess,
    )  # type: ignore[import-not-found]
    from smbprotocol.session import Session  # type: ignore[import-not-found]
    from smbprotocol.tree import TreeConnect  # type: ignore[import-not-found]

    HAS_SMB = True
except ImportError:
    HAS_SMB = False
    Connection = None  # type: ignore[misc,assignment]
    Session = None  # type: ignore[misc,assignment]
    TreeConnect = None  # type: ignore[misc,assignment]
    File = None  # type: ignore[misc,assignment]
    Open = None  # type: ignore[misc,assignment]
    CreateDisposition = None  # type: ignore[misc,assignment]
    FileAttributes = None  # type: ignore[misc,assignment]
    ImpersonationLevel = None  # type: ignore[misc,assignment]
    ShareAccess = None  # type: ignore[misc,assignment]
    FilePipePrinterAccessMask = None  # type: ignore[misc,assignment]
    CreateOptions = None  # type: ignore[misc,assignment]
    SMBException = Exception  # type: ignore[misc,assignment]
    CreateOptions = None  # type: ignore[misc,assignment]
    SMBException = Exception  # type: ignore[misc,assignment]

SCHEMES: tuple[str, ...] = ("smb",) if HAS_SMB else ()


class _SMBFile:
    """File-like wrapper for an SMB file handle."""

    def __init__(self, file_obj: File, mode: str) -> None:
        self._file = file_obj
        self._mode = mode
        self._closed = False

    def read(self, size: int | None = -1) -> bytes:
        if self._closed:
            raise ValueError("I/O operation on closed file")
        if "r" not in self._mode:
            raise OSError("File not open for reading")
        return self._file.read(size)

    def write(self, b: bytes | bytearray | memoryview) -> int:
        if self._closed:
            raise ValueError("I/O operation on closed file")
        if "w" not in self._mode and "a" not in self._mode:
            raise OSError("File not open for writing")
        return self._file.write(b)

    def flush(self) -> None:
        if self._closed:
            raise ValueError("I/O operation on closed file")
        if "w" in self._mode or "a" in self._mode:
            self._file.flush()

    def close(self) -> None:
        if not self._closed:
            self._file.close()
            self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed

    def __enter__(self) -> _SMBFile:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        if self._closed:
            raise ValueError("I/O operation on closed file")
        return self._file.seek(offset, whence)

    def tell(self) -> int:
        if self._closed:
            raise ValueError("I/O operation on closed file")
        return self._file.tell()

    def readable(self) -> bool:
        return "r" in self._mode

    def writable(self) -> bool:
        return "w" in self._mode or "a" in self._mode

    def seekable(self) -> bool:
        return True

    def fileno(self) -> int:
        raise OSError("Not a real file descriptor")

    def isatty(self) -> bool:
        return False


class SmbFileSystem(FileSystem):
    """Writable VFS backend backed by an SMB (CIFS) connection.

    Uses smbprotocol for SMB2/3 dialect negotiation, encryption (if supported),
    and modern authentication. Supports listing, reading, writing, mkdir, delete,
    and rename operations.

    The VFS root maps to the SMB share root. Sub-paths are relative to the share.
    """

    writable: bool = True

    def __init__(
        self,
        connection: Connection,
        session: Session,
        tree: TreeConnect,
        host: str,
        share: str,
        user: str,
        path: str = "/",
    ) -> None:
        self._connection = connection
        self._session = session
        self._tree = tree
        self._host = host
        self._share = share
        self._user = user
        self._root = path.rstrip("/") or "/"
        self.display_prefix = f"smb://{host}/{share}!"

    # -- helpers --------------------------------------------------------------

    def _to_smb_path(self, path: VfsPath) -> str:
        """Convert VfsPath to SMB relative path (backslash-separated, no leading slash)."""
        if len(path.parts) <= 1:
            return ""
        suffix = "/".join(path.parts[1:])
        base = self._root.lstrip("/") if self._root != "/" else ""
        if base:
            return f"{base}\\{suffix}".replace("/", "\\")
        return suffix.replace("/", "\\")

    def _to_vfs_path(self, smb_path: str, name: str) -> VfsPath:
        """Convert SMB path and name to VfsPath."""
        if not smb_path:
            return VfsPath(fs=self, parts=("", name))
        # smb_path uses backslashes, convert to forward slashes
        rel = smb_path.replace("\\", "/")
        parts = ("",) + tuple(p for p in rel.split("/") if p) + (name,)
        return VfsPath(fs=self, parts=parts)

    def _get_file_info(self, path: str):
        """Get file information for a path."""
        file_obj = File(self._tree, path)
        file_obj.create(
            ImpersonationLevel.Impersonation,
            FilePipePrinterAccessMask.FILE_READ_ATTRIBUTES,
            ShareAccess.FILE_SHARE_READ
            | ShareAccess.FILE_SHARE_WRITE
            | ShareAccess.FILE_SHARE_DELETE,
            CreateDisposition.FILE_OPEN,
            CreateOptions.FILE_SYNCHRONOUS_IO_NONALERT | CreateOptions.FILE_OPEN_FOR_BACKUP_INTENT,
            FileAttributes.FILE_ATTRIBUTE_NORMAL,
        )
        info = file_obj.query_info()
        file_obj.close()
        return info

    # -- FileSystem API -------------------------------------------------------

    def list_dir(self, path: VfsPath) -> list[FileEntry]:
        smb_path = self._to_smb_path(path)

        # Directory listing
        file_obj = File(self._tree, smb_path)
        file_obj.create(
            ImpersonationLevel.Impersonation,
            FilePipePrinterAccessMask.FILE_LIST_DIRECTORY
            | FilePipePrinterAccessMask.FILE_READ_ATTRIBUTES
            | FilePipePrinterAccessMask.SYNCHRONIZE,
            ShareAccess.FILE_SHARE_READ
            | ShareAccess.FILE_SHARE_WRITE
            | ShareAccess.FILE_SHARE_DELETE,
            CreateDisposition.FILE_OPEN,
            CreateOptions.FILE_DIRECTORY_FILE | CreateOptions.FILE_SYNCHRONOUS_IO_NONALERT,
            FileAttributes.FILE_ATTRIBUTE_NORMAL,
        )

        entries: list[FileEntry] = []

        # Add parent entry
        parent_entry = FileEntry(
            name="..",
            path=path.parent,
            is_dir=True,
            size=0,
            mtime=0.0,
            is_parent=True,
        )
        entries.append(parent_entry)

        try:
            for file_info in file_obj.query_directory():
                name = file_info.file_name.get_value()
                if name in (".", ".."):
                    continue

                is_dir = bool(file_info.file_attributes & FileAttributes.FILE_ATTRIBUTE_DIRECTORY)
                size = 0 if is_dir else int(file_info.end_of_file or 0)

                # Convert Windows FILETIME (100-ns intervals since 1601-01-01) to Unix timestamp
                mtime = 0.0
                if file_info.last_write_time:
                    # FILETIME is 100-nanosecond intervals since Jan 1, 1601
                    # Unix epoch is Jan 1, 1970
                    # Difference is 11644473600 seconds
                    filetime = file_info.last_write_time
                    mtime = (filetime / 10_000_000) - 11644473600

                vpath = self._to_vfs_path(smb_path, name)
                entries.append(
                    FileEntry(
                        name=name,
                        path=vpath,
                        is_dir=is_dir,
                        size=size,
                        mtime=mtime,
                    )
                )
        except SMBException as exc:
            raise OSError(f"Cannot list directory: {exc}") from exc
        finally:
            file_obj.close()

        return entries

    def stat(self, path: VfsPath) -> StatResult:
        if path.parts == ("",):
            return StatResult(is_dir=True, size=0, mtime=0.0)

        smb_path = self._to_smb_path(path)
        if not smb_path:
            return StatResult(is_dir=True, size=0, mtime=0.0)

        try:
            info = self._get_file_info(smb_path)
            is_dir = bool(info.file_attributes & FileAttributes.FILE_ATTRIBUTE_DIRECTORY)
            size = 0 if is_dir else int(info.end_of_file or 0)

            mtime = 0.0
            if info.last_write_time:
                filetime = info.last_write_time
                mtime = (filetime / 10_000_000) - 11644473600

            return StatResult(is_dir=is_dir, size=size, mtime=mtime)
        except SMBException as exc:
            raise OSError(f"Not found: {path}") from exc

    def open_read(self, path: VfsPath) -> BinaryIO:
        smb_path = self._to_smb_path(path)
        file_obj = File(self._tree, smb_path)
        file_obj.create(
            ImpersonationLevel.Impersonation,
            FilePipePrinterAccessMask.FILE_READ_DATA
            | FilePipePrinterAccessMask.FILE_READ_ATTRIBUTES
            | FilePipePrinterAccessMask.SYNCHRONIZE,
            ShareAccess.FILE_SHARE_READ
            | ShareAccess.FILE_SHARE_WRITE
            | ShareAccess.FILE_SHARE_DELETE,
            CreateDisposition.FILE_OPEN,
            CreateOptions.FILE_SYNCHRONOUS_IO_NONALERT | CreateOptions.FILE_SEQUENTIAL_ONLY,
            FileAttributes.FILE_ATTRIBUTE_NORMAL,
        )
        return _SMBFile(file_obj, "rb")  # type: ignore[return-value]

    def open_write(self, path: VfsPath) -> BinaryIO:
        smb_path = self._to_smb_path(path)
        file_obj = File(self._tree, smb_path)
        file_obj.create(
            ImpersonationLevel.Impersonation,
            FilePipePrinterAccessMask.FILE_WRITE_DATA
            | FilePipePrinterAccessMask.FILE_WRITE_ATTRIBUTES
            | FilePipePrinterAccessMask.SYNCHRONIZE,
            ShareAccess.FILE_SHARE_READ,
            CreateDisposition.FILE_OVERWRITE_IF,
            CreateOptions.FILE_SYNCHRONOUS_IO_NONALERT,
            FileAttributes.FILE_ATTRIBUTE_NORMAL,
        )
        return _SMBFile(file_obj, "wb")  # type: ignore[return-value]

    def mkdir(self, path: VfsPath) -> None:
        smb_path = self._to_smb_path(path)
        file_obj = File(self._tree, smb_path)
        file_obj.create(
            ImpersonationLevel.Impersonation,
            FilePipePrinterAccessMask.FILE_WRITE_DATA
            | FilePipePrinterAccessMask.FILE_WRITE_ATTRIBUTES
            | FilePipePrinterAccessMask.SYNCHRONIZE,
            ShareAccess.FILE_SHARE_READ,
            CreateDisposition.FILE_CREATE,
            CreateOptions.FILE_DIRECTORY_FILE | CreateOptions.FILE_SYNCHRONOUS_IO_NONALERT,
            FileAttributes.FILE_ATTRIBUTE_NORMAL,
        )
        file_obj.close()

    def delete(self, path: VfsPath) -> None:
        smb_path = self._to_smb_path(path)
        # First check if it's a directory
        try:
            info = self._get_file_info(smb_path)
            is_dir = bool(info.file_attributes & FileAttributes.FILE_ATTRIBUTE_DIRECTORY)
        except SMBException as exc:
            raise OSError(f"Cannot delete '{path}': not found") from exc

        file_obj = File(self._tree, smb_path)
        if is_dir:
            file_obj.create(
                ImpersonationLevel.Impersonation,
                FilePipePrinterAccessMask.DELETE | FilePipePrinterAccessMask.SYNCHRONIZE,
                ShareAccess.FILE_SHARE_READ
                | ShareAccess.FILE_SHARE_WRITE
                | ShareAccess.FILE_SHARE_DELETE,
                CreateDisposition.FILE_OPEN,
                CreateOptions.FILE_DIRECTORY_FILE
                | CreateOptions.FILE_SYNCHRONOUS_IO_NONALERT
                | CreateOptions.FILE_DELETE_ON_CLOSE,
                FileAttributes.FILE_ATTRIBUTE_NORMAL,
            )
        else:
            file_obj.create(
                ImpersonationLevel.Impersonation,
                FilePipePrinterAccessMask.DELETE | FilePipePrinterAccessMask.SYNCHRONIZE,
                ShareAccess.FILE_SHARE_READ
                | ShareAccess.FILE_SHARE_WRITE
                | ShareAccess.FILE_SHARE_DELETE,
                CreateDisposition.FILE_OPEN,
                CreateOptions.FILE_SYNCHRONOUS_IO_NONALERT | CreateOptions.FILE_DELETE_ON_CLOSE,
                FileAttributes.FILE_ATTRIBUTE_NORMAL,
            )
        file_obj.close()

    def rename(self, src: VfsPath, dst: VfsPath) -> None:
        src_path = self._to_smb_path(src)
        dst_path = self._to_smb_path(dst)

        # SMB rename: open source with FILE_OPEN and set rename info
        src_file = File(self._tree, src_path)
        src_file.create(
            ImpersonationLevel.Impersonation,
            FilePipePrinterAccessMask.DELETE | FilePipePrinterAccessMask.SYNCHRONIZE,
            ShareAccess.FILE_SHARE_READ
            | ShareAccess.FILE_SHARE_WRITE
            | ShareAccess.FILE_SHARE_DELETE,
            CreateDisposition.FILE_OPEN,
            CreateOptions.FILE_SYNCHRONOUS_IO_NONALERT,
            FileAttributes.FILE_ATTRIBUTE_NORMAL,
        )
        try:
            # Use the rename info class to rename
            from smbprotocol.file import FileRenameInfo

            rename_info = FileRenameInfo()
            rename_info.replace_if_exists = True
            rename_info.file_name = dst_path.replace(
                "\\", "/"
            )  # SMB expects forward slashes in rename
            src_file.set_info(rename_info)
        except SMBException as exc:
            raise OSError(f"Cannot rename '{src_path}' to '{dst_path}': {exc}") from exc
        finally:
            src_file.close()

    def close(self) -> None:
        try:
            self._tree.disconnect()
        except Exception:
            pass
        try:
            self._session.disconnect()
        except Exception:
            pass
        try:
            self._connection.disconnect()
        except Exception:
            pass


def connect_fs(url: str) -> SmbFileSystem:
    """Connect to an SMB share and return an SmbFileSystem.

    URL format: ``smb://[user:pass@]host/share[/path]``

    If credentials are not in the URL, they will be prompted via the
    registered credential provider (if any) or left blank for guest access.
    """
    if not HAS_SMB:
        raise RuntimeError(
            "smbprotocol not installed. Install with 'pip install linux-commander[smb]'"
        )

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "smb":
        raise ValueError(f"Expected smb:// URL, got {parsed.scheme}://")

    host = parsed.hostname or ""
    if not host:
        raise ValueError("Missing host in SMB URL")

    # Extract credentials from URL
    user = unquote(parsed.username) if parsed.username else ""
    password = unquote(parsed.password) if parsed.password else ""

    # Path format: /share/path -> share is first component, rest is path
    path_parts = [p for p in parsed.path.split("/") if p]
    if not path_parts:
        raise ValueError("SMB URL must include a share name (e.g., smb://host/share)")

    share = path_parts[0]
    sub_path = "/" + "/".join(path_parts[1:]) if len(path_parts) > 1 else "/"

    port = parsed.port or 445

    # Create connection
    connection = Connection(host, port)
    connection.connect()

    # Create session
    session = Session(connection, user, password)
    session.connect()

    # Connect to share
    tree = TreeConnect(session, f"\\\\{host}\\{share}")
    tree.connect()

    return SmbFileSystem(connection, session, tree, host, share, user, sub_path)
