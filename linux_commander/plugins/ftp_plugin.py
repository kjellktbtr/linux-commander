"""VFS plugin for browsing FTP servers.

Connects to FTP using stdlib ``ftplib``.  Directory listing tries ``MLSD``
(RFC 3659) first; if the server rejects it the plugin falls back to ``LIST``
(Unix ``ls -l`` format) so that older or restricted servers still work.
Files are downloaded fully into ``BytesIO`` so the rest of the app can treat
them as seekable.

MLSD support is probed once via ``FEAT`` at connect time to avoid the
``OPTS MLST`` command that some servers reject with a stale-response side
effect that desyncs the control channel.  When the control channel does get
desynced (``error_reply``, ``error_proto``), the plugin transparently
reconnects before falling back to ``LIST``.
"""

from __future__ import annotations

import datetime
import ftplib
import io
import re
import urllib.parse
from typing import BinaryIO
from urllib.parse import unquote

from linux_commander.vfs import FileEntry, FileSystem, StatResult, VfsPath

SCHEMES: tuple[str, ...] = ("ftp",)

# Matches a Unix-style LIST line produced by most FTP servers:
#   drwxr-xr-x  2 user group    4096 Jan 15 10:00 name
# Group 1: 'd' for directory, '-' for file, 'l' for symlink
# Group 2: file size in bytes
# Group 3: entry name (everything after the 8th whitespace-separated field)
_LIST_RE = re.compile(r"^([dl-])[rwxst-]{9}\s+\d+\s+\S+\s+\S+\s+(\d+)\s+\S+\s+\S+\s+\S+\s+(.+)$")


def _parse_mtime(modify: str) -> float:
    """Parse an MLSD ``modify`` fact (``YYYYMMDDHHMMSS``) to a POSIX timestamp."""
    try:
        return datetime.datetime.strptime(modify, "%Y%m%d%H%M%S").timestamp()
    except (ValueError, TypeError):
        return 0.0


def _probe_mlsd_support(ftp: ftplib.FTP) -> bool:
    """Return True if the server advertises MLSD support via FEAT."""
    try:
        feat = ftp.sendcmd("FEAT")
    except (ftplib.error_perm, ftplib.error_temp, OSError):
        return False
    feat_upper = feat.upper()
    return "MLSD" in feat_upper or "MLST" in feat_upper


def _list_via_list(ftp: ftplib.FTP, ftp_path: str, vpath: VfsPath) -> list[FileEntry]:
    """Fallback directory listing using the FTP ``LIST`` command.

    Parses standard Unix ``ls -l`` output lines.  Entries ``'.'`` and ``'..'``
    are skipped; symlinks are treated as files.
    """
    lines: list[str] = []
    try:
        ftp.retrlines(f"LIST {ftp_path}", lines.append)
    except (ftplib.error_perm, ftplib.error_reply, ftplib.error_temp):
        return []

    entries: list[FileEntry] = []
    for line in lines:
        m = _LIST_RE.match(line.rstrip())
        if not m:
            continue
        type_char, size_str, name = m.group(1), m.group(2), m.group(3)
        name = name.strip()
        if name in (".", ".."):
            continue
        is_dir = type_char == "d"
        size = 0 if is_dir else int(size_str)
        entries.append(
            FileEntry(
                name=name,
                path=vpath / name,
                is_dir=is_dir,
                size=size,
                mtime=0.0,
            )
        )
    return entries


class _FtpWriteBuffer(io.RawIOBase):
    """Write-buffer that uploads to FTP via STOR when closed.

    Used as the return value of ``FtpFileSystem.open_write``.  Data written
    to this buffer is accumulated in memory and flushed to the server in a
    single ``STOR`` transfer on ``close()``.
    """

    def __init__(self, ftp: ftplib.FTP, ftp_path: str) -> None:
        self._ftp = ftp
        self._ftp_path = ftp_path
        self._buf = io.BytesIO()

    def write(self, b: bytes | bytearray) -> int:  # type: ignore[override]
        return self._buf.write(b)

    def close(self) -> None:
        if not self.closed:
            self._buf.seek(0)
            try:
                self._ftp.storbinary(f"STOR {self._ftp_path}", self._buf)
            except ftplib.error_perm as exc:
                super().close()
                raise OSError(f"Cannot upload to '{self._ftp_path}': {exc}") from exc
            super().close()


class FtpFileSystem(FileSystem):
    """Writable VFS backend backed by an FTP connection.

    ``_root`` is the absolute FTP path that maps to VfsPath root ``("",)``.
    Sub-paths append to it: ``("", "dir", "f")`` → ``{_root}/dir/f``.

    Connection params (host, port, user, password) are stored so the
    plugin can transparently reconnect when the control channel desyncs.
    ``_supports_mlsd`` is probed once at connect time via ``FEAT``; if the
    server does not advertise MLSD, the plugin skips straight to ``LIST`` on
    every listing and avoids the ``OPTS MLST`` command that some servers
    handle in a way that leaves a stale response on the control channel.
    """

    writable: bool = True

    def __init__(
        self,
        ftp: ftplib.FTP,
        root_path: str,
        host: str = "",
        port: int = 21,
        user: str = "anonymous",
        password: str = "",
        supports_mlsd: bool = True,
    ) -> None:
        self._ftp = ftp
        self._root = root_path.rstrip("/") or "/"
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._supports_mlsd = supports_mlsd
        self.display_prefix = f"ftp://{ftp.host}!"

    def _reconnect(self) -> None:
        """Re-establish the FTP connection after a control-channel desync."""
        try:
            self._ftp.close()
        except Exception:
            pass
        ftp = ftplib.FTP()
        ftp.connect(self._host, self._port, timeout=30)
        ftp.sock.settimeout(30)  # type: ignore[union-attr]
        ftp.login(self._user, self._password)
        ftp.set_pasv(True)
        if self._root and self._root != "/":
            try:
                ftp.cwd(self._root)
            except ftplib.error_perm:
                pass
        self._ftp = ftp

    # -- helpers ----------------------------------------------------------

    def _to_ftp_path(self, path: VfsPath) -> str:
        if len(path.parts) <= 1:
            return self._root
        suffix = "/".join(path.parts[1:])
        base = self._root if self._root != "/" else ""
        return f"{base}/{suffix}"

    # -- FileSystem API ---------------------------------------------------

    def list_dir(self, path: VfsPath) -> list[FileEntry]:
        ftp_path = self._to_ftp_path(path)
        parent_entry = FileEntry(
            name="..",
            path=path.parent,
            is_dir=True,
            size=0,
            mtime=0.0,
            is_parent=True,
        )
        entries: list[FileEntry] = []
        if self._supports_mlsd:
            try:
                # Call mlsd without facts= to avoid OPTS MLST, which some servers
                # respond to in a way that leaves a stale reply on the control channel.
                for name, facts in self._ftp.mlsd(ftp_path):
                    if name in (".", ".."):
                        continue
                    is_dir = facts.get("type") in ("dir", "cdir", "pdir")
                    size = 0 if is_dir else int(facts.get("size", 0))
                    mtime = _parse_mtime(facts.get("modify", ""))
                    entries.append(
                        FileEntry(
                            name=name,
                            path=path / name,
                            is_dir=is_dir,
                            size=size,
                            mtime=mtime,
                        )
                    )
                return [parent_entry] + entries
            except ftplib.error_perm:
                # Server rejected MLSD (permission or no support); fall through to LIST.
                self._supports_mlsd = False
            except (ftplib.error_reply, ftplib.error_proto, ftplib.error_temp, OSError):
                # Control channel desynced — reconnect, then fall through to LIST.
                try:
                    self._reconnect()
                except Exception:
                    pass
        entries = _list_via_list(self._ftp, ftp_path, path)
        return [parent_entry] + entries

    def stat(self, path: VfsPath) -> StatResult:
        if path.parts == ("",):
            return StatResult(is_dir=True, size=0, mtime=0.0)
        parent_path = self._to_ftp_path(path.parent)
        if self._supports_mlsd:
            try:
                for name, facts in self._ftp.mlsd(parent_path):
                    if name == path.name:
                        is_dir = facts.get("type") in ("dir", "cdir", "pdir")
                        size = 0 if is_dir else int(facts.get("size", 0))
                        mtime = _parse_mtime(facts.get("modify", ""))
                        return StatResult(is_dir=is_dir, size=size, mtime=mtime)
            except ftplib.error_perm:
                self._supports_mlsd = False
            except (ftplib.error_reply, ftplib.error_proto, ftplib.error_temp, OSError):
                try:
                    self._reconnect()
                except Exception:
                    pass
        # MLSD unavailable or entry not found — fall back to LIST
        for entry in _list_via_list(self._ftp, parent_path, path.parent):
            if entry.name == path.name:
                return StatResult(is_dir=entry.is_dir, size=entry.size, mtime=entry.mtime)
        raise OSError(f"Not found on FTP server: {path}")

    def open_read(self, path: VfsPath) -> BinaryIO:
        ftp_path = self._to_ftp_path(path)
        buf = io.BytesIO()
        try:
            self._ftp.retrbinary(f"RETR {ftp_path}", buf.write)
        except ftplib.error_perm as exc:
            raise OSError(f"Cannot retrieve '{ftp_path}': {exc}") from exc
        buf.seek(0)
        return buf

    def read_prefix(self, path: VfsPath, max_bytes: int) -> tuple[bytes, bool]:
        """Download at most ``max_bytes`` bytes, aborting the transfer early."""
        ftp_path = self._to_ftp_path(path)
        buf = io.BytesIO()
        truncated = False

        class _Enough(Exception):
            pass

        def _callback(chunk: bytes) -> None:
            nonlocal truncated
            remaining = max_bytes + 1 - buf.tell()
            if remaining <= 0:
                raise _Enough()
            buf.write(chunk[:remaining])
            if len(chunk) > remaining:
                truncated = True
                raise _Enough()

        try:
            self._ftp.retrbinary(f"RETR {ftp_path}", _callback)
        except _Enough:
            try:
                self._ftp.abort()
            except Exception:
                try:
                    self._reconnect()
                except Exception:
                    pass
        except ftplib.error_perm as exc:
            raise OSError(f"Cannot retrieve '{ftp_path}': {exc}") from exc

        data = buf.getvalue()
        if len(data) > max_bytes:
            data = data[:max_bytes]
            truncated = True
        return data, truncated

    def open_write(self, path: VfsPath) -> BinaryIO:
        """Return a buffer that uploads its contents via STOR on close."""
        ftp_path = self._to_ftp_path(path)
        return _FtpWriteBuffer(self._ftp, ftp_path)  # type: ignore[return-value]

    def delete(self, path: VfsPath) -> None:
        ftp_path = self._to_ftp_path(path)
        try:
            # Try stat to decide between DELE (file) and RMD (dir).
            st = self.stat(path)
        except OSError:
            st = None
        try:
            if st is not None and st.is_dir:
                self._ftp.rmd(ftp_path)
            else:
                self._ftp.delete(ftp_path)
        except ftplib.error_perm as exc:
            raise OSError(f"Cannot delete '{ftp_path}': {exc}") from exc

    def rename(self, src: VfsPath, dst: VfsPath) -> None:
        src_path = self._to_ftp_path(src)
        dst_path = self._to_ftp_path(dst)
        try:
            self._ftp.rename(src_path, dst_path)
        except ftplib.error_perm as exc:
            raise OSError(f"Cannot rename '{src_path}' to '{dst_path}': {exc}") from exc

    def mkdir(self, path: VfsPath) -> None:
        ftp_path = self._to_ftp_path(path)
        try:
            self._ftp.mkd(ftp_path)
        except ftplib.error_perm as exc:
            raise OSError(f"Cannot create directory '{ftp_path}': {exc}") from exc

    def set_mtime(self, path: VfsPath, mtime: float) -> None:
        import datetime

        ftp_path = self._to_ftp_path(path)
        try:
            dt = datetime.datetime.fromtimestamp(mtime)
            fmt = "%Y%m%d%H%M%S"
            self._ftp.sendcmd(f"MDTM {dt.strftime(fmt)} {ftp_path}")
        except (ftplib.error_perm, ftplib.error_temp, OSError):
            pass

    def realpath(self, path: VfsPath) -> None:  # type: ignore[override]
        return None

    def close(self) -> None:
        try:
            self._ftp.quit()
        except Exception:
            try:
                self._ftp.close()
            except Exception:
                pass


def connect_fs(url: str) -> FtpFileSystem:
    """Connect to an FTP server at ``url`` and return an ``FtpFileSystem``.

    ``url`` must have the form ``ftp://[user:pass@]host[:port][/path]``.
    Omitting credentials uses anonymous login.
    """
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port or 21
    user = unquote(parsed.username) if parsed.username else "anonymous"
    password = unquote(parsed.password) if parsed.password else ""
    path = parsed.path or "/"

    ftp = ftplib.FTP()
    ftp.connect(host, port, timeout=30)
    ftp.sock.settimeout(30)  # type: ignore[union-attr]
    try:
        ftp.login(user, password)
    except ftplib.error_perm as exc:
        ftp.close()
        raise OSError(f"FTP login failed (check username/password): {exc}") from exc
    except Exception as exc:
        ftp.close()
        raise OSError(f"FTP connection failed: {exc}") from exc

    if path and path != "/":
        try:
            ftp.cwd(path)
        except ftplib.error_perm as exc:
            ftp.close()
            raise OSError(f"Cannot change to directory '{path}': {exc}") from exc

    supports_mlsd = _probe_mlsd_support(ftp)
    return FtpFileSystem(ftp, path, host, port, user, password, supports_mlsd)
