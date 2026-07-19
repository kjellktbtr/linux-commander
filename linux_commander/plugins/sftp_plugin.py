"""VFS plugin for browsing servers over SFTP (SSH File Transfer Protocol).

Connects using the third-party ``paramiko`` package (the ``ssh`` extra); the
plugin registers no scheme when it isn't installed, mirroring the guard
convention documented in ``plugins/__init__.py``.

Unlike the FTP plugin, ``open_read`` returns paramiko's own lazily-reading
``SFTPFile`` handle rather than buffering the whole file up front, so
``FileSystem.read_prefix``'s default implementation already reads only the
requested prefix over the wire -- no override needed here.

Host key verification is trust-on-first-use: known system/user host keys are
loaded and checked, but an unknown host is accepted and remembered for the
process lifetime (``paramiko.AutoAddPolicy``), matching common lightweight
SFTP client behavior. Pre-populate ``~/.ssh/known_hosts`` for stricter
verification.
"""

from __future__ import annotations

import stat as stat_module
import urllib.parse
from pathlib import Path
from typing import BinaryIO
from urllib.parse import unquote

from linux_commander.vfs import FileEntry, FileSystem, StatResult, VfsPath

try:
    import paramiko  # type: ignore[import-untyped]

    SCHEMES: tuple[str, ...] = ("sftp",)
except ImportError:
    paramiko = None  # type: ignore[assignment]
    SCHEMES = ()


class SftpFileSystem(FileSystem):
    """Writable VFS backend backed by an SFTP (SSH) connection.

    ``_root`` is the absolute remote path that maps to VfsPath root
    ``("",)``; sub-paths append to it, exactly like ``FtpFileSystem``.
    Connection params are kept only for display/reference -- unlike FTP,
    SFTP needs no control-channel reconnect logic since each request is a
    self-contained SSH channel operation.
    """

    writable: bool = True

    def __init__(
        self,
        ssh: paramiko.SSHClient,
        sftp: paramiko.SFTPClient,
        root_path: str,
        host: str,
        port: int = 22,
        user: str = "",
    ) -> None:
        self._ssh = ssh
        self._sftp = sftp
        self._root = root_path.rstrip("/") or "/"
        self._host = host
        self._port = port
        self._user = user
        self.display_prefix = f"sftp://{host}!"

    # -- helpers ----------------------------------------------------------

    def _to_remote_path(self, path: VfsPath) -> str:
        if len(path.parts) <= 1:
            return self._root
        suffix = "/".join(path.parts[1:])
        base = self._root if self._root != "/" else ""
        return f"{base}/{suffix}"

    # -- FileSystem API ---------------------------------------------------

    def list_dir(self, path: VfsPath) -> list[FileEntry]:
        remote_path = self._to_remote_path(path)
        # Always prepend ".."  -- matches FtpFileSystem's behavior for scheme
        # mounts, where MountManager.resolve_up() already no-ops at the root
        # instead of the archive-plugin convention of omitting it there.
        parent_entry = FileEntry(
            name="..", path=path.parent, is_dir=True, size=0, mtime=0.0, is_parent=True
        )
        entries: list[FileEntry] = []
        try:
            for attr in self._sftp.listdir_attr(remote_path):
                is_dir = attr.st_mode is not None and stat_module.S_ISDIR(attr.st_mode)
                entries.append(
                    FileEntry(
                        name=attr.filename,
                        path=path / attr.filename,
                        is_dir=is_dir,
                        size=0 if is_dir else int(attr.st_size or 0),
                        mtime=float(attr.st_mtime or 0.0),
                    )
                )
        except OSError as exc:
            raise OSError(f"Cannot list '{remote_path}': {exc}") from exc
        return [parent_entry] + entries

    def stat(self, path: VfsPath) -> StatResult:
        if path.parts == ("",):
            return StatResult(is_dir=True, size=0, mtime=0.0)
        remote_path = self._to_remote_path(path)
        try:
            attr = self._sftp.stat(remote_path)
        except OSError as exc:
            raise OSError(f"Not found on SFTP server: {path}") from exc
        is_dir = attr.st_mode is not None and stat_module.S_ISDIR(attr.st_mode)
        return StatResult(
            is_dir=is_dir,
            size=0 if is_dir else int(attr.st_size or 0),
            mtime=float(attr.st_mtime or 0.0),
        )

    def open_read(self, path: VfsPath) -> BinaryIO:
        remote_path = self._to_remote_path(path)
        try:
            return self._sftp.open(remote_path, "rb")  # type: ignore[return-value]
        except OSError as exc:
            raise OSError(f"Cannot open '{remote_path}': {exc}") from exc

    def open_write(self, path: VfsPath) -> BinaryIO:
        remote_path = self._to_remote_path(path)
        try:
            return self._sftp.open(remote_path, "wb")  # type: ignore[return-value]
        except OSError as exc:
            raise OSError(f"Cannot open '{remote_path}' for writing: {exc}") from exc

    def mkdir(self, path: VfsPath) -> None:
        remote_path = self._to_remote_path(path)
        try:
            self._sftp.mkdir(remote_path)
        except OSError as exc:
            raise OSError(f"Cannot create directory '{remote_path}': {exc}") from exc

    def delete(self, path: VfsPath) -> None:
        remote_path = self._to_remote_path(path)
        try:
            st = self.stat(path)
        except OSError:
            st = None
        try:
            if st is not None and st.is_dir:
                self._sftp.rmdir(remote_path)
            else:
                self._sftp.remove(remote_path)
        except OSError as exc:
            raise OSError(f"Cannot delete '{remote_path}': {exc}") from exc

    def rename(self, src: VfsPath, dst: VfsPath) -> None:
        src_path = self._to_remote_path(src)
        dst_path = self._to_remote_path(dst)
        try:
            self._sftp.rename(src_path, dst_path)
        except OSError as exc:
            raise OSError(f"Cannot rename '{src_path}' to '{dst_path}': {exc}") from exc

    def close(self) -> None:
        try:
            self._sftp.close()
        except Exception:
            pass
        try:
            self._ssh.close()
        except Exception:
            pass


def connect(
    host: str,
    port: int,
    user: str,
    password: str,
    key_path: str,
    key_passphrase: str,
    path: str,
) -> SftpFileSystem:
    """Connect to an SSH server and return an ``SftpFileSystem``.

    Uses private-key auth (with an optional passphrase) when ``key_path`` is
    given; otherwise password auth when ``password`` is given; otherwise
    falls back to the SSH agent / default key files paramiko knows about.
    """
    ssh = paramiko.SSHClient()
    ssh.load_system_host_keys()
    known_hosts = Path.home() / ".ssh" / "known_hosts"
    try:
        if known_hosts.exists():
            ssh.load_host_keys(str(known_hosts))
    except OSError:
        pass
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    connect_kwargs: dict[str, object] = {}
    if key_path:
        connect_kwargs["key_filename"] = key_path
        connect_kwargs["passphrase"] = key_passphrase or None
        connect_kwargs["look_for_keys"] = False
        connect_kwargs["allow_agent"] = False
    elif password:
        connect_kwargs["password"] = password
        connect_kwargs["look_for_keys"] = False
        connect_kwargs["allow_agent"] = False

    try:
        ssh.connect(host, port=port, username=user or None, timeout=30, **connect_kwargs)
    except Exception as exc:
        ssh.close()
        raise OSError(f"SFTP connection failed: {exc}") from exc

    try:
        sftp = ssh.open_sftp()
    except Exception as exc:
        ssh.close()
        raise OSError(f"Cannot open SFTP session: {exc}") from exc

    root = path or "/"
    if root != "/":
        try:
            sftp.stat(root)
        except OSError as exc:
            ssh.close()
            raise OSError(f"Cannot access remote path '{root}': {exc}") from exc

    return SftpFileSystem(ssh, sftp, root, host, port, user)


def connect_fs(url: str) -> SftpFileSystem:
    """Connect to an SFTP server at ``url`` and return an ``SftpFileSystem``.

    ``url`` must have the form ``sftp://[user:pass@]host[:port][/path]``.
    Private-key auth isn't representable in a URL; use ``connect()`` directly
    for that.
    """
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port or 22
    user = unquote(parsed.username) if parsed.username else ""
    password = unquote(parsed.password) if parsed.password else ""
    path = parsed.path or "/"
    return connect(host, port, user, password, "", "", path)
