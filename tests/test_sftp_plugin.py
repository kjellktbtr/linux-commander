"""Tests for the SFTP VFS plugin (linux_commander.plugins.sftp_plugin).

Mirrors the FtpFileSystem test style in test_plugins.py: the underlying
paramiko client is mocked so these run fast and without a network connection.
Skipped entirely when ``paramiko`` (the ``ssh`` extra) isn't installed.
"""

from __future__ import annotations

import stat as stat_module
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("paramiko")

import linux_commander.plugins.sftp_plugin as sftp_plugin  # noqa: E402
from linux_commander.plugins import plugin_for_scheme  # noqa: E402
from linux_commander.plugins.sftp_plugin import SftpFileSystem  # noqa: E402
from linux_commander.vfs import VfsPath  # noqa: E402


def _attr(name: str, is_dir: bool = False, size: int = 0, mtime: float = 0.0) -> SimpleNamespace:
    """Build a fake ``paramiko.SFTPAttributes``-alike (only the fields we read)."""
    mode = (stat_module.S_IFDIR | 0o755) if is_dir else (stat_module.S_IFREG | 0o644)
    return SimpleNamespace(filename=name, st_mode=mode, st_size=size, st_mtime=mtime)


def _make_sftp_mock(entries: list[SimpleNamespace] | None = None) -> MagicMock:
    mock = MagicMock()
    mock.listdir_attr.return_value = entries or []
    return mock


# ---------------------------------------------------------------------------
# plugin discovery
# ---------------------------------------------------------------------------


def test_plugin_for_scheme_sftp() -> None:
    plugin = plugin_for_scheme("sftp")
    assert plugin is not None
    assert "sftp" in plugin.SCHEMES


# ---------------------------------------------------------------------------
# SftpFileSystem.list_dir
# ---------------------------------------------------------------------------


def test_sftp_list_dir_root_has_parent_entry() -> None:
    sftp = _make_sftp_mock([])
    fs = SftpFileSystem(MagicMock(), sftp, "/", "example.com")
    root = VfsPath(fs=fs, parts=("",))
    entries = fs.list_dir(root)
    assert entries[0].is_parent
    assert entries[0].name == ".."


def test_sftp_list_dir_file_and_dir() -> None:
    sftp = _make_sftp_mock([_attr("notes.txt", size=100), _attr("subdir", is_dir=True)])
    fs = SftpFileSystem(MagicMock(), sftp, "/", "example.com")
    root = VfsPath(fs=fs, parts=("",))
    entries = {e.name: e for e in fs.list_dir(root) if not e.is_parent}
    assert "notes.txt" in entries
    assert not entries["notes.txt"].is_dir
    assert entries["notes.txt"].size == 100
    assert "subdir" in entries
    assert entries["subdir"].is_dir
    assert entries["subdir"].size == 0


def test_sftp_list_dir_subpath_constructs_correct_remote_path() -> None:
    sftp = _make_sftp_mock([])
    fs = SftpFileSystem(MagicMock(), sftp, "/pub", "example.com")
    subdir = VfsPath(fs=fs, parts=("", "docs"))
    fs.list_dir(subdir)
    sftp.listdir_attr.assert_called_once_with("/pub/docs")


def test_sftp_list_dir_raises_oserror_wrapped() -> None:
    sftp = MagicMock()
    sftp.listdir_attr.side_effect = OSError("permission denied")
    fs = SftpFileSystem(MagicMock(), sftp, "/", "example.com")
    root = VfsPath(fs=fs, parts=("",))
    with pytest.raises(OSError):
        fs.list_dir(root)


# ---------------------------------------------------------------------------
# SftpFileSystem.stat
# ---------------------------------------------------------------------------


def test_sftp_stat_root_is_dir() -> None:
    fs = SftpFileSystem(MagicMock(), MagicMock(), "/", "example.com")
    root = VfsPath(fs=fs, parts=("",))
    result = fs.stat(root)
    assert result.is_dir


def test_sftp_stat_file() -> None:
    sftp = MagicMock()
    sftp.stat.return_value = _attr("file.txt", size=512)
    fs = SftpFileSystem(MagicMock(), sftp, "/", "example.com")
    vp = VfsPath(fs=fs, parts=("", "file.txt"))
    result = fs.stat(vp)
    assert not result.is_dir
    assert result.size == 512


def test_sftp_stat_not_found_raises() -> None:
    sftp = MagicMock()
    sftp.stat.side_effect = OSError("No such file")
    fs = SftpFileSystem(MagicMock(), sftp, "/", "example.com")
    vp = VfsPath(fs=fs, parts=("", "missing.txt"))
    with pytest.raises(OSError):
        fs.stat(vp)


# ---------------------------------------------------------------------------
# SftpFileSystem.open_read / open_write
# ---------------------------------------------------------------------------


def test_sftp_open_read_uses_correct_remote_path() -> None:
    sftp = MagicMock()
    fs = SftpFileSystem(MagicMock(), sftp, "/pub", "example.com")
    vp = VfsPath(fs=fs, parts=("", "docs", "readme.txt"))
    fs.open_read(vp)
    sftp.open.assert_called_once_with("/pub/docs/readme.txt", "rb")


def test_sftp_open_write_uses_correct_remote_path() -> None:
    sftp = MagicMock()
    fs = SftpFileSystem(MagicMock(), sftp, "/", "example.com")
    vp = VfsPath(fs=fs, parts=("", "file.txt"))
    fs.open_write(vp)
    sftp.open.assert_called_once_with("/file.txt", "wb")


def test_sftp_open_read_wraps_error() -> None:
    sftp = MagicMock()
    sftp.open.side_effect = OSError("no such file")
    fs = SftpFileSystem(MagicMock(), sftp, "/", "example.com")
    vp = VfsPath(fs=fs, parts=("", "missing.txt"))
    with pytest.raises(OSError):
        fs.open_read(vp)


# ---------------------------------------------------------------------------
# SftpFileSystem.mkdir / delete / rename
# ---------------------------------------------------------------------------


def test_sftp_mkdir() -> None:
    sftp = MagicMock()
    fs = SftpFileSystem(MagicMock(), sftp, "/", "example.com")
    vp = VfsPath(fs=fs, parts=("", "newdir"))
    fs.mkdir(vp)
    sftp.mkdir.assert_called_once_with("/newdir")


def test_sftp_delete_file_calls_remove_not_rmdir() -> None:
    sftp = MagicMock()
    sftp.stat.return_value = _attr("file.txt", size=1)
    fs = SftpFileSystem(MagicMock(), sftp, "/", "example.com")
    vp = VfsPath(fs=fs, parts=("", "file.txt"))
    fs.delete(vp)
    sftp.remove.assert_called_once_with("/file.txt")
    sftp.rmdir.assert_not_called()


def test_sftp_delete_dir_calls_rmdir_not_remove() -> None:
    sftp = MagicMock()
    sftp.stat.return_value = _attr("subdir", is_dir=True)
    fs = SftpFileSystem(MagicMock(), sftp, "/", "example.com")
    vp = VfsPath(fs=fs, parts=("", "subdir"))
    fs.delete(vp)
    sftp.rmdir.assert_called_once_with("/subdir")
    sftp.remove.assert_not_called()


def test_sftp_rename() -> None:
    sftp = MagicMock()
    fs = SftpFileSystem(MagicMock(), sftp, "/", "example.com")
    src = VfsPath(fs=fs, parts=("", "old.txt"))
    dst = VfsPath(fs=fs, parts=("", "new.txt"))
    fs.rename(src, dst)
    sftp.rename.assert_called_once_with("/old.txt", "/new.txt")


# ---------------------------------------------------------------------------
# SftpFileSystem.close / realpath / display_prefix
# ---------------------------------------------------------------------------


def test_sftp_close_closes_both_sftp_and_ssh() -> None:
    sftp, ssh = MagicMock(), MagicMock()
    fs = SftpFileSystem(ssh, sftp, "/", "example.com")
    fs.close()
    sftp.close.assert_called_once()
    ssh.close.assert_called_once()


def test_sftp_close_swallows_errors() -> None:
    sftp, ssh = MagicMock(), MagicMock()
    sftp.close.side_effect = Exception("already closed")
    fs = SftpFileSystem(ssh, sftp, "/", "example.com")
    fs.close()  # must not raise
    ssh.close.assert_called_once()


def test_sftp_realpath_is_none() -> None:
    fs = SftpFileSystem(MagicMock(), MagicMock(), "/", "example.com")
    vp = VfsPath(fs=fs, parts=("", "file.txt"))
    assert fs.realpath(vp) is None


def test_sftp_display_prefix_includes_host() -> None:
    fs = SftpFileSystem(MagicMock(), MagicMock(), "/", "example.com")
    assert fs.display_prefix == "sftp://example.com!"


# ---------------------------------------------------------------------------
# connect_fs — URL parsing (delegates to connect(), which is monkeypatched
# out here so no real network connection is attempted)
# ---------------------------------------------------------------------------


def test_connect_fs_parses_url_and_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_connect(
        host: str,
        port: int,
        user: str,
        password: str,
        key_path: str,
        key_passphrase: str,
        path: str,
    ) -> str:
        captured.update(
            host=host,
            port=port,
            user=user,
            password=password,
            key_path=key_path,
            key_passphrase=key_passphrase,
            path=path,
        )
        return "FAKE_FS"

    monkeypatch.setattr(sftp_plugin, "connect", fake_connect)
    result = sftp_plugin.connect_fs("sftp://alice:s3cr3t@example.com:2222/home/alice")
    assert result == "FAKE_FS"
    assert captured == {
        "host": "example.com",
        "port": 2222,
        "user": "alice",
        "password": "s3cr3t",
        "key_path": "",
        "key_passphrase": "",
        "path": "/home/alice",
    }


def test_connect_fs_defaults_port_and_empty_user_password(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_connect(
        host: str,
        port: int,
        user: str,
        password: str,
        key_path: str,
        key_passphrase: str,
        path: str,
    ) -> str:
        captured.update(host=host, port=port, user=user, password=password, path=path)
        return "FAKE_FS"

    monkeypatch.setattr(sftp_plugin, "connect", fake_connect)
    sftp_plugin.connect_fs("sftp://example.com")
    assert captured == {
        "host": "example.com",
        "port": 22,
        "user": "",
        "password": "",
        "path": "/",
    }
