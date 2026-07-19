"""Tests for VFS plugins: discovery, ZipFileSystem, TarFileSystem, FtpFileSystem."""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from linux_commander.plugins import (
    cleanup_all_temps,
    materialize,
    plugin_for_extension,
    plugin_for_name,
    plugin_for_scheme,
    viewer_plugin_for_name,
)
from linux_commander.plugins.ftp_plugin import FtpFileSystem, connect_fs
from linux_commander.plugins.grp_plugin import GrpFileSystem
from linux_commander.plugins.tar_plugin import TarFileSystem
from linux_commander.plugins.zip_plugin import ZipFileSystem
from linux_commander.vfs import FileEntry, LocalFileSystem, MountManager, VfsPath

_FS = LocalFileSystem()


def _make_zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return path


def _make_grp(path: Path, members: dict[str, bytes]) -> Path:
    """Create a GRP archive for testing using the GRPFile logic."""
    import struct

    MAGIC = b"KenSilverman"
    ENTRY = struct.Struct("<12sI")
    COUNT = struct.Struct("<I")

    parts: list[bytes] = [MAGIC, COUNT.pack(len(members))]
    name_bytes = {name: name.encode("ascii").ljust(12, b"\x00") for name in members}
    parts.extend(ENTRY.pack(name_bytes[name], len(data)) for name, data in members.items())
    parts.extend(members.values())
    path.write_bytes(b"".join(parts))
    return path


# ---------------------------------------------------------------------------
# Plugin discovery
# ---------------------------------------------------------------------------


def test_plugin_discovery_finds_zip() -> None:
    plugin = plugin_for_extension(".zip")
    assert plugin is not None
    assert hasattr(plugin, "open_fs")
    assert ".zip" in plugin.EXTENSIONS


def test_plugin_discovery_case_insensitive() -> None:
    assert plugin_for_extension(".ZIP") is plugin_for_extension(".zip")


def test_plugin_for_unknown_extension_is_none() -> None:
    assert plugin_for_extension(".xyz_unknown_999") is None


def test_plugin_for_unknown_scheme_is_none() -> None:
    assert plugin_for_scheme("xyz_unknown_999") is None


def test_viewer_plugin_for_unknown_extension_is_none() -> None:
    assert viewer_plugin_for_name("file.xyz_unknown_999") is None


def test_viewer_plugin_for_dotfile_returns_none() -> None:
    assert viewer_plugin_for_name(".gitignore") is None


def test_viewer_plugin_for_name_does_not_shadow_vfs_extension_plugins() -> None:
    # .zip has a VFS mount plugin but no VIEW_EXTENSIONS reader -- the two
    # maps are independent, so it must not spuriously match a reader plugin.
    assert viewer_plugin_for_name("archive.zip") is None


# ---------------------------------------------------------------------------
# ZipFileSystem.list_dir
# ---------------------------------------------------------------------------


def test_zip_list_dir_root_has_parent_entry(tmp_path: Path) -> None:
    zp = _make_zip(tmp_path / "test.zip", {"hello.txt": b"hello"})
    fs = ZipFileSystem(zp, _FS.from_path(zp))
    root = VfsPath(fs=fs, parts=("",))
    entries = fs.list_dir(root)
    assert entries[0].is_parent
    assert entries[0].name == ".."
    fs.close()


def test_zip_list_dir_root_members(tmp_path: Path) -> None:
    zp = _make_zip(
        tmp_path / "test.zip",
        {"hello.txt": b"hello", "sub/world.txt": b"world"},
    )
    fs = ZipFileSystem(zp, _FS.from_path(zp))
    root = VfsPath(fs=fs, parts=("",))
    names = {e.name for e in fs.list_dir(root) if not e.is_parent}
    assert "hello.txt" in names
    assert "sub" in names  # synthetic directory
    fs.close()


def test_zip_list_dir_subdirectory(tmp_path: Path) -> None:
    zp = _make_zip(tmp_path / "test.zip", {"sub/a.txt": b"a", "sub/b.txt": b"b"})
    fs = ZipFileSystem(zp, _FS.from_path(zp))
    subdir = VfsPath(fs=fs, parts=("", "sub"))
    entries = fs.list_dir(subdir)
    names = {e.name for e in entries if not e.is_parent}
    assert names == {"a.txt", "b.txt"}
    fs.close()


def test_zip_list_dir_nested_dirs_not_flattened(tmp_path: Path) -> None:
    zp = _make_zip(tmp_path / "test.zip", {"a/b/c.txt": b"deep"})
    fs = ZipFileSystem(zp, _FS.from_path(zp))
    root = VfsPath(fs=fs, parts=("",))
    names = {e.name for e in fs.list_dir(root) if not e.is_parent}
    assert names == {"a"}  # only top-level; "b" not shown at root
    level_a = VfsPath(fs=fs, parts=("", "a"))
    names_a = {e.name for e in fs.list_dir(level_a) if not e.is_parent}
    assert names_a == {"b"}
    fs.close()


def test_zip_subdir_entry_is_dir(tmp_path: Path) -> None:
    zp = _make_zip(tmp_path / "test.zip", {"sub/file.txt": b"x"})
    fs = ZipFileSystem(zp, _FS.from_path(zp))
    root = VfsPath(fs=fs, parts=("",))
    sub = next(e for e in fs.list_dir(root) if e.name == "sub")
    assert sub.is_dir
    fs.close()


def test_zip_file_size_reported(tmp_path: Path) -> None:
    content = b"hello world"
    zp = _make_zip(tmp_path / "test.zip", {"f.txt": content})
    fs = ZipFileSystem(zp, _FS.from_path(zp))
    root = VfsPath(fs=fs, parts=("",))
    entry = next(e for e in fs.list_dir(root) if e.name == "f.txt")
    assert entry.size == len(content)
    fs.close()


# ---------------------------------------------------------------------------
# ZipFileSystem.open_read
# ---------------------------------------------------------------------------


def test_zip_open_read_correct_bytes(tmp_path: Path) -> None:
    content = b"binary content \x00\x01\x02"
    zp = _make_zip(tmp_path / "test.zip", {"data.bin": content})
    fs = ZipFileSystem(zp, _FS.from_path(zp))
    vp = VfsPath(fs=fs, parts=("", "data.bin"))
    with fs.open_read(vp) as fh:
        assert fh.read() == content
    fs.close()


def test_zip_open_read_nested_file(tmp_path: Path) -> None:
    zp = _make_zip(tmp_path / "test.zip", {"dir/nested.txt": b"nested"})
    fs = ZipFileSystem(zp, _FS.from_path(zp))
    vp = VfsPath(fs=fs, parts=("", "dir", "nested.txt"))
    with fs.open_read(vp) as fh:
        assert fh.read() == b"nested"
    fs.close()


# ---------------------------------------------------------------------------
# ZipFileSystem error handling
# ---------------------------------------------------------------------------


def test_zip_bad_file_raises_oserror(tmp_path: Path) -> None:
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip file at all")
    with pytest.raises(OSError):
        ZipFileSystem(bad, _FS.from_path(bad))


# ---------------------------------------------------------------------------
# MountManager + zip integration
# ---------------------------------------------------------------------------


def test_mount_manager_enter_returns_zip_root(tmp_path: Path) -> None:
    zp = _make_zip(tmp_path / "archive.zip", {"file.txt": b"content"})
    entry = FileEntry(name="archive.zip", path=_FS.from_path(zp), is_dir=False, size=0, mtime=0.0)
    mm = MountManager()
    result = mm.enter(entry)
    assert result is not None
    assert result.parts == ("",)
    assert isinstance(result.fs, ZipFileSystem)
    mm.close_all()


def test_mount_manager_enter_non_archive_returns_none(tmp_path: Path) -> None:
    f = tmp_path / "plain.txt"
    f.write_text("hello")
    entry = FileEntry(name="plain.txt", path=_FS.from_path(f), is_dir=False, size=0, mtime=0.0)
    mm = MountManager()
    assert mm.enter(entry) is None


def test_mount_manager_enter_reuses_backend(tmp_path: Path) -> None:
    zp = _make_zip(tmp_path / "archive.zip", {"file.txt": b"content"})
    entry = FileEntry(name="archive.zip", path=_FS.from_path(zp), is_dir=False, size=0, mtime=0.0)
    mm = MountManager()
    root1 = mm.enter(entry)
    root2 = mm.enter(entry)
    assert root1 is not None and root2 is not None
    assert root1.fs is root2.fs  # same backend instance
    mm.close_all()


def test_mount_manager_resolve_up_crosses_boundary(tmp_path: Path) -> None:
    zp = _make_zip(tmp_path / "archive.zip", {"file.txt": b"content"})
    host_vp = _FS.from_path(zp)
    entry = FileEntry(name="archive.zip", path=host_vp, is_dir=False, size=0, mtime=0.0)
    mm = MountManager()
    zip_root = mm.enter(entry)
    assert zip_root is not None
    new_path, select_name = mm.resolve_up(zip_root)
    assert new_path == _FS.from_path(tmp_path)
    assert select_name == "archive.zip"
    mm.close_all()


def test_mount_manager_resolve_up_inside_zip(tmp_path: Path) -> None:
    zp = _make_zip(tmp_path / "archive.zip", {"sub/file.txt": b"x"})
    entry = FileEntry(name="archive.zip", path=_FS.from_path(zp), is_dir=False, size=0, mtime=0.0)
    mm = MountManager()
    zip_root = mm.enter(entry)
    assert zip_root is not None
    subdir = zip_root / "sub"
    new_path, select_name = mm.resolve_up(subdir)
    assert new_path == zip_root
    assert select_name == "sub"
    mm.close_all()


def test_mount_manager_release_closes_at_zero(tmp_path: Path) -> None:
    zp = _make_zip(tmp_path / "archive.zip", {"file.txt": b"content"})
    entry = FileEntry(name="archive.zip", path=_FS.from_path(zp), is_dir=False, size=0, mtime=0.0)
    mm = MountManager()
    root = mm.enter(entry)
    assert root is not None
    fs = root.fs
    mm.release(fs)
    # After release the zipfile handle is closed; open_read needs the handle
    with pytest.raises((ValueError, zipfile.BadZipFile)):
        vp = VfsPath(fs=fs, parts=("", "file.txt"))
        fs.open_read(vp)


def test_mount_manager_refcount_release_twice(tmp_path: Path) -> None:
    zp = _make_zip(tmp_path / "archive.zip", {"file.txt": b"content"})
    entry = FileEntry(name="archive.zip", path=_FS.from_path(zp), is_dir=False, size=0, mtime=0.0)
    mm = MountManager()
    root1 = mm.enter(entry)
    root2 = mm.enter(entry)
    assert root1 is not None and root2 is not None
    fs = root1.fs
    mm.release(fs)  # refcount 2 → 1; still open
    # Should still be readable
    entries = fs.list_dir(root1)
    assert any(e.name == "file.txt" for e in entries)
    mm.release(fs)  # refcount 1 → 0; now closed


# ---------------------------------------------------------------------------
# plugin_for_name — compound-extension lookup
# ---------------------------------------------------------------------------


def test_plugin_for_name_zip() -> None:
    assert plugin_for_name("archive.zip") is plugin_for_extension(".zip")


def test_plugin_for_name_tar_gz_compound() -> None:
    plugin = plugin_for_name("archive.tar.gz")
    assert plugin is not None
    assert ".tar.gz" in plugin.EXTENSIONS


def test_plugin_for_name_tgz() -> None:
    plugin = plugin_for_name("archive.tgz")
    assert plugin is not None
    assert ".tgz" in plugin.EXTENSIONS


def test_plugin_for_name_tar() -> None:
    plugin = plugin_for_name("archive.tar")
    assert plugin is not None
    assert ".tar" in plugin.EXTENSIONS


def test_plugin_for_name_plain_gz_returns_compress_plugin() -> None:
    # A plain .gz file (not a tar.gz) is handled by compress_plugin
    from linux_commander.plugins import compress_plugin

    assert plugin_for_name("image.svg.gz") is compress_plugin


def test_plugin_for_name_dotfile_returns_none() -> None:
    assert plugin_for_name(".gitignore") is None


def test_plugin_for_name_unknown_returns_none() -> None:
    assert plugin_for_name("file.xyz_unknown_999") is None


# ---------------------------------------------------------------------------
# TarFileSystem helpers
# ---------------------------------------------------------------------------


def _make_tar(path: Path, members: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w") as tf:
        for name, content in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
    return path


def _make_tar_gz(path: Path, members: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w:gz") as tf:
        for name, content in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
    return path


def _make_grp(path: Path, members: dict[str, bytes]) -> Path:
    """Create a GRP archive using the plugin's write logic."""
    from linux_commander.plugins.grp_plugin import GrpFileSystem

    GrpFileSystem.create(path, members)
    return path


# ---------------------------------------------------------------------------
# TarFileSystem.list_dir
# ---------------------------------------------------------------------------


def test_tar_list_dir_root_has_parent_entry(tmp_path: Path) -> None:
    tp = _make_tar(tmp_path / "test.tar", {"hello.txt": b"hello"})
    fs = TarFileSystem(tp, _FS.from_path(tp))
    root = VfsPath(fs=fs, parts=("",))
    entries = fs.list_dir(root)
    assert entries[0].is_parent
    assert entries[0].name == ".."
    fs.close()


def test_tar_list_dir_root_members(tmp_path: Path) -> None:
    tp = _make_tar(
        tmp_path / "test.tar",
        {"hello.txt": b"hello", "sub/world.txt": b"world"},
    )
    fs = TarFileSystem(tp, _FS.from_path(tp))
    root = VfsPath(fs=fs, parts=("",))
    names = {e.name for e in fs.list_dir(root) if not e.is_parent}
    assert "hello.txt" in names
    assert "sub" in names
    fs.close()


def test_tar_list_dir_subdirectory(tmp_path: Path) -> None:
    tp = _make_tar(tmp_path / "test.tar", {"sub/a.txt": b"a", "sub/b.txt": b"b"})
    fs = TarFileSystem(tp, _FS.from_path(tp))
    subdir = VfsPath(fs=fs, parts=("", "sub"))
    names = {e.name for e in fs.list_dir(subdir) if not e.is_parent}
    assert names == {"a.txt", "b.txt"}
    fs.close()


def test_tar_gz_list_dir_root_members(tmp_path: Path) -> None:
    tp = _make_tar_gz(
        tmp_path / "test.tar.gz",
        {"readme.txt": b"readme", "lib/util.py": b"util"},
    )
    fs = TarFileSystem(tp, _FS.from_path(tp))
    root = VfsPath(fs=fs, parts=("",))
    names = {e.name for e in fs.list_dir(root) if not e.is_parent}
    assert "readme.txt" in names
    assert "lib" in names
    fs.close()


# ---------------------------------------------------------------------------
# TarFileSystem.open_read
# ---------------------------------------------------------------------------


def test_tar_open_read_correct_bytes(tmp_path: Path) -> None:
    content = b"tar file content \x00\xff"
    tp = _make_tar(tmp_path / "test.tar", {"data.bin": content})
    fs = TarFileSystem(tp, _FS.from_path(tp))
    vp = VfsPath(fs=fs, parts=("", "data.bin"))
    with fs.open_read(vp) as fh:
        assert fh.read() == content
    fs.close()


def test_tar_gz_open_read_correct_bytes(tmp_path: Path) -> None:
    content = b"compressed content"
    tp = _make_tar_gz(tmp_path / "test.tar.gz", {"readme.txt": content})
    fs = TarFileSystem(tp, _FS.from_path(tp))
    vp = VfsPath(fs=fs, parts=("", "readme.txt"))
    with fs.open_read(vp) as fh:
        assert fh.read() == content
    fs.close()


def test_tar_open_read_nested_file(tmp_path: Path) -> None:
    tp = _make_tar(tmp_path / "test.tar", {"dir/nested.txt": b"nested"})
    fs = TarFileSystem(tp, _FS.from_path(tp))
    vp = VfsPath(fs=fs, parts=("", "dir", "nested.txt"))
    with fs.open_read(vp) as fh:
        assert fh.read() == b"nested"
    fs.close()


# ---------------------------------------------------------------------------
# TarFileSystem error handling
# ---------------------------------------------------------------------------


def test_tar_bad_file_raises_oserror(tmp_path: Path) -> None:
    bad = tmp_path / "bad.tar"
    bad.write_bytes(b"not a tar file at all")
    with pytest.raises(OSError):
        TarFileSystem(bad, _FS.from_path(bad))


# ---------------------------------------------------------------------------
# MountManager + tar integration (compound extension)
# ---------------------------------------------------------------------------


def test_mount_manager_enter_tar_gz(tmp_path: Path) -> None:
    tp = _make_tar_gz(tmp_path / "archive.tar.gz", {"file.txt": b"content"})
    entry = FileEntry(
        name="archive.tar.gz", path=_FS.from_path(tp), is_dir=False, size=0, mtime=0.0
    )
    mm = MountManager()
    result = mm.enter(entry)
    assert result is not None
    assert result.parts == ("",)
    assert isinstance(result.fs, TarFileSystem)
    mm.close_all()


def test_mount_manager_enter_tgz(tmp_path: Path) -> None:
    tp = _make_tar_gz(tmp_path / "archive.tgz", {"file.txt": b"content"})
    entry = FileEntry(name="archive.tgz", path=_FS.from_path(tp), is_dir=False, size=0, mtime=0.0)
    mm = MountManager()
    result = mm.enter(entry)
    assert result is not None
    assert isinstance(result.fs, TarFileSystem)
    mm.close_all()


def test_mount_manager_resolve_up_tar_gz(tmp_path: Path) -> None:
    tp = _make_tar_gz(tmp_path / "archive.tar.gz", {"file.txt": b"x"})
    entry = FileEntry(
        name="archive.tar.gz", path=_FS.from_path(tp), is_dir=False, size=0, mtime=0.0
    )
    mm = MountManager()
    tar_root = mm.enter(entry)
    assert tar_root is not None
    new_path, select_name = mm.resolve_up(tar_root)
    assert new_path == _FS.from_path(tmp_path)
    assert select_name == "archive.tar.gz"
    mm.close_all()


# ---------------------------------------------------------------------------
# cleanup_all_temps
# ---------------------------------------------------------------------------


def test_cleanup_all_temps_removes_files(tmp_path: Path) -> None:
    """materialize() on a non-local path creates a temp file; cleanup_all_temps removes it."""
    zp = _make_zip(tmp_path / "arch.zip", {"f.txt": b"hello"})
    zfs = ZipFileSystem(zp, _FS.from_path(zp))
    inner_vp = VfsPath(fs=zfs, parts=("", "f.txt"))

    # materialize spills the inner file to a temp
    tmp_real = materialize(zfs, inner_vp)
    assert tmp_real.exists()

    cleanup_all_temps()

    assert not tmp_real.exists()
    zfs.close()


def test_cleanup_all_temps_is_idempotent(tmp_path: Path) -> None:
    """Calling cleanup_all_temps twice must not raise."""
    zp = _make_zip(tmp_path / "arch.zip", {"f.txt": b"hi"})
    zfs = ZipFileSystem(zp, _FS.from_path(zp))
    inner_vp = VfsPath(fs=zfs, parts=("", "f.txt"))
    materialize(zfs, inner_vp)

    cleanup_all_temps()
    cleanup_all_temps()  # second call must be a no-op, not raise
    zfs.close()


# ---------------------------------------------------------------------------
# plugin discovery — scheme lookup
# ---------------------------------------------------------------------------


def test_plugin_for_scheme_ftp() -> None:
    plugin = plugin_for_scheme("ftp")
    assert plugin is not None
    assert "ftp" in plugin.SCHEMES


def test_plugin_for_scheme_unknown_returns_none() -> None:
    assert plugin_for_scheme("sftp_unknown_999") is None


# ---------------------------------------------------------------------------
# FtpFileSystem helpers
# ---------------------------------------------------------------------------


def _make_ftp_mock(
    mlsd_entries: list[tuple[str, dict[str, str]]] | None = None,
) -> MagicMock:
    """Build a minimal mock ftplib.FTP object."""
    mock = MagicMock()
    mock.host = "example.com"
    mock.mlsd.return_value = iter(mlsd_entries or [])
    return mock


# ---------------------------------------------------------------------------
# FtpFileSystem.list_dir
# ---------------------------------------------------------------------------


def test_ftp_list_dir_root_has_parent_entry() -> None:
    ftp = _make_ftp_mock([])
    fs = FtpFileSystem(ftp, "/")
    root = VfsPath(fs=fs, parts=("",))
    entries = fs.list_dir(root)
    assert entries[0].is_parent
    assert entries[0].name == ".."


def test_ftp_list_dir_skips_dot_entries() -> None:
    ftp = _make_ftp_mock(
        [
            (".", {"type": "cdir"}),
            ("..", {"type": "pdir"}),
            ("file.txt", {"type": "file", "size": "42", "modify": "20240101120000"}),
        ]
    )
    fs = FtpFileSystem(ftp, "/")
    root = VfsPath(fs=fs, parts=("",))
    names = {e.name for e in fs.list_dir(root)}
    assert "." not in names
    assert ".." not in names or all(e.is_parent for e in fs.list_dir(root) if e.name == "..")


def test_ftp_list_dir_file_and_dir() -> None:
    ftp = _make_ftp_mock(
        [
            ("notes.txt", {"type": "file", "size": "100", "modify": "20240601000000"}),
            ("subdir", {"type": "dir", "modify": "20240601000000"}),
        ]
    )
    fs = FtpFileSystem(ftp, "/")
    root = VfsPath(fs=fs, parts=("",))
    entries = {e.name: e for e in fs.list_dir(root) if not e.is_parent}
    assert "notes.txt" in entries
    assert not entries["notes.txt"].is_dir
    assert entries["notes.txt"].size == 100
    assert "subdir" in entries
    assert entries["subdir"].is_dir


def test_ftp_list_dir_subpath_constructs_correct_ftp_path() -> None:
    ftp = _make_ftp_mock([("a.txt", {"type": "file", "size": "1", "modify": "20240101000000"})])
    fs = FtpFileSystem(ftp, "/pub")
    subdir = VfsPath(fs=fs, parts=("", "docs"))
    fs.list_dir(subdir)
    ftp.mlsd.assert_called_once_with("/pub/docs")


# ---------------------------------------------------------------------------
# FtpFileSystem.stat
# ---------------------------------------------------------------------------


def test_ftp_stat_root_is_dir() -> None:
    ftp = _make_ftp_mock()
    fs = FtpFileSystem(ftp, "/")
    root = VfsPath(fs=fs, parts=("",))
    result = fs.stat(root)
    assert result.is_dir


def test_ftp_stat_file() -> None:
    ftp = _make_ftp_mock(
        [("file.txt", {"type": "file", "size": "512", "modify": "20240101000000"})]
    )
    fs = FtpFileSystem(ftp, "/")
    vp = VfsPath(fs=fs, parts=("", "file.txt"))
    result = fs.stat(vp)
    assert not result.is_dir
    assert result.size == 512


def test_ftp_stat_not_found_raises() -> None:
    ftp = _make_ftp_mock([])
    fs = FtpFileSystem(ftp, "/")
    vp = VfsPath(fs=fs, parts=("", "missing.txt"))
    with pytest.raises(OSError):
        fs.stat(vp)


# ---------------------------------------------------------------------------
# FtpFileSystem.open_read
# ---------------------------------------------------------------------------


def test_ftp_open_read_returns_correct_bytes() -> None:
    ftp = _make_ftp_mock()

    def fake_retrbinary(cmd: str, callback: object) -> None:
        assert cmd == "RETR /file.txt"
        assert callable(callback)
        callback(b"hello ")  # type: ignore[operator]
        callback(b"world")  # type: ignore[operator]

    ftp.retrbinary = fake_retrbinary
    fs = FtpFileSystem(ftp, "/")
    vp = VfsPath(fs=fs, parts=("", "file.txt"))
    with fs.open_read(vp) as fh:
        assert fh.read() == b"hello world"


def test_ftp_open_read_subdirectory_path() -> None:
    ftp = _make_ftp_mock()

    captured: list[str] = []

    def fake_retrbinary(cmd: str, callback: object) -> None:
        captured.append(cmd)
        callback(b"data")  # type: ignore[operator]

    ftp.retrbinary = fake_retrbinary
    fs = FtpFileSystem(ftp, "/pub")
    vp = VfsPath(fs=fs, parts=("", "docs", "readme.txt"))
    fs.open_read(vp)
    assert captured == ["RETR /pub/docs/readme.txt"]


# ---------------------------------------------------------------------------
# FtpFileSystem.close
# ---------------------------------------------------------------------------


def test_ftp_close_calls_quit() -> None:
    ftp = _make_ftp_mock()
    fs = FtpFileSystem(ftp, "/")
    fs.close()
    ftp.quit.assert_called_once()


def test_ftp_close_falls_back_to_close_on_quit_error() -> None:
    import ftplib

    ftp = _make_ftp_mock()
    ftp.quit.side_effect = ftplib.Error("already closed")
    fs = FtpFileSystem(ftp, "/")
    fs.close()  # must not raise
    ftp.close.assert_called_once()


# ---------------------------------------------------------------------------
# connect_fs URL parsing
# ---------------------------------------------------------------------------


def test_connect_fs_parses_url() -> None:
    with patch("linux_commander.plugins.ftp_plugin.ftplib.FTP") as MockFTP:
        mock_ftp = MagicMock()
        mock_ftp.host = "example.com"
        MockFTP.return_value = mock_ftp

        fs = connect_fs("ftp://user:secret@example.com:2121/data")

        mock_ftp.connect.assert_called_once_with("example.com", 2121, timeout=30)
        mock_ftp.login.assert_called_once_with("user", "secret")
        mock_ftp.cwd.assert_called_once_with("/data")
        assert isinstance(fs, FtpFileSystem)


def test_connect_fs_anonymous_defaults() -> None:
    with patch("linux_commander.plugins.ftp_plugin.ftplib.FTP") as MockFTP:
        mock_ftp = MagicMock()
        mock_ftp.host = "ftp.example.org"
        MockFTP.return_value = mock_ftp

        connect_fs("ftp://ftp.example.org")

        mock_ftp.login.assert_called_once_with("anonymous", "")
        mock_ftp.cwd.assert_not_called()


# ---------------------------------------------------------------------------
# MountManager.mount_scheme_fs + release
# ---------------------------------------------------------------------------


def test_mount_scheme_fs_returns_root_vfspath() -> None:
    from linux_commander.vfs import MountManager

    ftp = _make_ftp_mock()
    fs = FtpFileSystem(ftp, "/")
    mm = MountManager()
    root = mm.mount_scheme_fs(fs)
    assert root.parts == ("",)
    assert root.fs is fs


def test_mount_scheme_fs_release_closes_at_zero() -> None:
    from linux_commander.vfs import MountManager

    ftp = _make_ftp_mock()
    fs = FtpFileSystem(ftp, "/")
    mm = MountManager()
    mm.mount_scheme_fs(fs)
    mm.release(fs)
    ftp.quit.assert_called_once()


def test_mount_scheme_fs_refcount() -> None:
    from linux_commander.vfs import MountManager

    ftp = _make_ftp_mock()
    fs = FtpFileSystem(ftp, "/")
    mm = MountManager()
    mm.mount_scheme_fs(fs)
    mm.mount_scheme_fs(fs)  # second enter (two panels)
    mm.release(fs)  # refcount 2 → 1
    ftp.quit.assert_not_called()
    mm.release(fs)  # refcount 1 → 0
    ftp.quit.assert_called_once()


def test_mount_scheme_fs_close_all() -> None:
    from linux_commander.vfs import MountManager

    ftp = _make_ftp_mock()
    fs = FtpFileSystem(ftp, "/")
    mm = MountManager()
    mm.mount_scheme_fs(fs)
    mm.close_all()
    ftp.quit.assert_called_once()
    assert mm._scheme_fses == {}


# ---------------------------------------------------------------------------
# ZipFileSystem write operations
# ---------------------------------------------------------------------------


def test_zip_open_write_appends_member(tmp_path: Path) -> None:
    zp = _make_zip(tmp_path / "archive.zip", {"existing.txt": b"old"})
    fs = ZipFileSystem(zp, _FS.from_path(zp))
    new_path = VfsPath(fs=fs, parts=("", "new.txt"))
    with fs.open_write(new_path) as f:
        f.write(b"hello archive")
    root = VfsPath(fs=fs, parts=("",))
    names = {e.name for e in fs.list_dir(root) if not e.is_parent}
    assert "new.txt" in names
    assert "existing.txt" in names
    assert fs.open_read(new_path).read() == b"hello archive"
    fs.close()


def test_zip_delete_removes_member(tmp_path: Path) -> None:
    zp = _make_zip(tmp_path / "archive.zip", {"keep.txt": b"keep", "drop.txt": b"drop"})
    fs = ZipFileSystem(zp, _FS.from_path(zp))
    drop_path = VfsPath(fs=fs, parts=("", "drop.txt"))
    fs.delete(drop_path)
    root = VfsPath(fs=fs, parts=("",))
    names = {e.name for e in fs.list_dir(root) if not e.is_parent}
    assert "drop.txt" not in names
    assert "keep.txt" in names
    fs.close()


def test_zip_rename_member(tmp_path: Path) -> None:
    zp = _make_zip(tmp_path / "archive.zip", {"old.txt": b"data"})
    fs = ZipFileSystem(zp, _FS.from_path(zp))
    old_path = VfsPath(fs=fs, parts=("", "old.txt"))
    new_path = VfsPath(fs=fs, parts=("", "new.txt"))
    fs.rename(old_path, new_path)
    root = VfsPath(fs=fs, parts=("",))
    names = {e.name for e in fs.list_dir(root) if not e.is_parent}
    assert "old.txt" not in names
    assert "new.txt" in names
    assert fs.open_read(new_path).read() == b"data"
    fs.close()


def test_zip_mkdir_creates_dir_entry(tmp_path: Path) -> None:
    zp = _make_zip(tmp_path / "archive.zip", {"file.txt": b"x"})
    fs = ZipFileSystem(zp, _FS.from_path(zp))
    dir_path = VfsPath(fs=fs, parts=("", "mydir"))
    fs.mkdir(dir_path)
    root = VfsPath(fs=fs, parts=("",))
    names = {e.name for e in fs.list_dir(root) if not e.is_parent}
    assert "mydir" in names
    fs.close()


# ---------------------------------------------------------------------------
# TarFileSystem write operations
# ---------------------------------------------------------------------------


def test_tar_open_write_appends_member(tmp_path: Path) -> None:
    tp = _make_tar(tmp_path / "archive.tar", {"existing.txt": b"old"})
    fs = TarFileSystem(tp, _FS.from_path(tp))
    new_path = VfsPath(fs=fs, parts=("", "new.txt"))
    with fs.open_write(new_path) as f:
        f.write(b"hello tar")
    root = VfsPath(fs=fs, parts=("",))
    names = {e.name for e in fs.list_dir(root) if not e.is_parent}
    assert "new.txt" in names
    assert "existing.txt" in names
    assert fs.open_read(new_path).read() == b"hello tar"
    fs.close()


def test_tar_gz_open_write_rewrites_and_appends(tmp_path: Path) -> None:
    tp = _make_tar_gz(tmp_path / "archive.tar.gz", {"existing.txt": b"old"})
    fs = TarFileSystem(tp, _FS.from_path(tp))
    new_path = VfsPath(fs=fs, parts=("", "new.txt"))
    with fs.open_write(new_path) as f:
        f.write(b"hello gz")
    root = VfsPath(fs=fs, parts=("",))
    names = {e.name for e in fs.list_dir(root) if not e.is_parent}
    assert "new.txt" in names
    assert "existing.txt" in names
    assert fs.open_read(new_path).read() == b"hello gz"
    fs.close()


def test_tar_delete_removes_member(tmp_path: Path) -> None:
    tp = _make_tar(tmp_path / "archive.tar", {"keep.txt": b"keep", "drop.txt": b"drop"})
    fs = TarFileSystem(tp, _FS.from_path(tp))
    drop_path = VfsPath(fs=fs, parts=("", "drop.txt"))
    fs.delete(drop_path)
    root = VfsPath(fs=fs, parts=("",))
    names = {e.name for e in fs.list_dir(root) if not e.is_parent}
    assert "drop.txt" not in names
    assert "keep.txt" in names
    fs.close()


def test_tar_rename_member(tmp_path: Path) -> None:
    tp = _make_tar(tmp_path / "archive.tar", {"old.txt": b"data"})
    fs = TarFileSystem(tp, _FS.from_path(tp))
    old_path = VfsPath(fs=fs, parts=("", "old.txt"))
    new_path = VfsPath(fs=fs, parts=("", "new.txt"))
    fs.rename(old_path, new_path)
    root = VfsPath(fs=fs, parts=("",))
    names = {e.name for e in fs.list_dir(root) if not e.is_parent}
    assert "old.txt" not in names
    assert "new.txt" in names
    assert fs.open_read(new_path).read() == b"data"
    fs.close()


# ---------------------------------------------------------------------------
# Archive search tests
# ---------------------------------------------------------------------------


def test_search_archives_name_in_zip(tmp_path: Path) -> None:
    """search_files with search_archives=True finds a file nested inside a zip."""
    from linux_commander.search_engine import SearchCriteria, SearchResult, search_files
    from linux_commander.vfs import LocalFileSystem

    local = LocalFileSystem()
    # Create a zip with one nested file.
    zp = tmp_path / "archive.zip"
    _make_zip(zp, {"inner/secret.txt": b"some content"})

    results: list[SearchResult] = []
    done: list[tuple[int, int]] = []

    root_vp = local.from_path(tmp_path)
    criteria = SearchCriteria(
        root_path=root_vp,
        name_enabled=True,
        name_pattern="secret.txt",
        search_archives=True,
    )
    search_files(
        criteria,
        on_found=results.append,
        on_done=lambda f, s: done.append((f, s)),
        should_cancel=lambda: False,
    )

    assert done == [(1, done[0][1])]
    assert len(results) == 1
    assert results[0].entry.name == "secret.txt"


def test_search_archives_name_in_tar(tmp_path: Path) -> None:
    """search_files with search_archives=True finds a file nested inside a tar."""
    from linux_commander.search_engine import SearchCriteria, SearchResult, search_files
    from linux_commander.vfs import LocalFileSystem

    local = LocalFileSystem()
    tp = tmp_path / "archive.tar"
    _make_tar(tp, {"subdir/found.log": b"needle"})

    results: list[SearchResult] = []
    done: list[tuple[int, int]] = []

    root_vp = local.from_path(tmp_path)
    criteria = SearchCriteria(
        root_path=root_vp,
        name_enabled=True,
        name_pattern="found.log",
        search_archives=True,
    )
    search_files(
        criteria,
        on_found=results.append,
        on_done=lambda f, s: done.append((f, s)),
        should_cancel=lambda: False,
    )

    assert len(results) == 1
    assert results[0].entry.name == "found.log"


def test_search_archives_content_in_zip(tmp_path: Path) -> None:
    """Content search inside a zip archive returns the correct file."""
    from linux_commander.search_engine import SearchCriteria, SearchResult, search_files
    from linux_commander.vfs import LocalFileSystem

    local = LocalFileSystem()
    zp = tmp_path / "pack.zip"
    _make_zip(
        zp,
        {
            "match.txt": b"the quick brown fox",
            "no_match.txt": b"nothing here",
        },
    )

    results: list[SearchResult] = []
    done: list[tuple[int, int]] = []

    root_vp = local.from_path(tmp_path)
    criteria = SearchCriteria(
        root_path=root_vp,
        content_enabled=True,
        content_pattern="quick brown",
        search_archives=True,
    )
    search_files(
        criteria,
        on_found=results.append,
        on_done=lambda f, s: done.append((f, s)),
        should_cancel=lambda: False,
    )

    result_names = {r.entry.name for r in results}
    # match.txt must be found via archive descent; no_match.txt must not be.
    assert "match.txt" in result_names
    assert "no_match.txt" not in result_names


def test_search_archives_disabled_does_not_descend(tmp_path: Path) -> None:
    """Without search_archives, files inside archives are NOT found."""
    from linux_commander.search_engine import SearchCriteria, SearchResult, search_files
    from linux_commander.vfs import LocalFileSystem

    local = LocalFileSystem()
    zp = tmp_path / "archive.zip"
    _make_zip(zp, {"inner.txt": b"content"})

    results: list[SearchResult] = []
    done: list[tuple[int, int]] = []

    root_vp = local.from_path(tmp_path)
    criteria = SearchCriteria(
        root_path=root_vp,
        name_enabled=True,
        name_pattern="inner.txt",
        search_archives=False,  # default
    )
    search_files(
        criteria,
        on_found=results.append,
        on_done=lambda f, s: done.append((f, s)),
        should_cancel=lambda: False,
    )

    # inner.txt is inside the zip — should not be found.
    assert results == []


# ---------------------------------------------------------------------------
# GRP Plugin Tests
# ---------------------------------------------------------------------------


def test_plugin_discovery_finds_grp() -> None:
    plugin = plugin_for_extension(".grp")
    assert plugin is not None
    assert hasattr(plugin, "open_fs")
    assert ".grp" in plugin.EXTENSIONS


def test_plugin_discovery_grp_case_insensitive() -> None:
    assert plugin_for_extension(".GRP") is plugin_for_extension(".grp")


def test_grp_list_dir_root_has_parent_entry(tmp_path: Path) -> None:
    grp = _make_grp(tmp_path / "test.grp", {"hello.txt": b"hello"})
    fs = GrpFileSystem(grp, _FS.from_path(grp))
    root = VfsPath(fs=fs, parts=("",))
    entries = fs.list_dir(root)
    assert entries[0].is_parent
    assert entries[0].name == ".."
    fs.close()


def test_grp_list_dir_root_members(tmp_path: Path) -> None:
    grp = _make_grp(
        tmp_path / "test.grp",
        {"hello.txt": b"hello", "world.dat": b"world"},
    )
    fs = GrpFileSystem(grp, _FS.from_path(grp))
    root = VfsPath(fs=fs, parts=("",))
    names = {e.name for e in fs.list_dir(root) if not e.is_parent}
    assert "hello.txt" in names
    assert "world.dat" in names
    fs.close()


def test_grp_file_size_reported(tmp_path: Path) -> None:
    content = b"hello world"
    grp = _make_grp(tmp_path / "test.grp", {"f.txt": content})
    fs = GrpFileSystem(grp, _FS.from_path(grp))
    root = VfsPath(fs=fs, parts=("",))
    entry = next(e for e in fs.list_dir(root) if e.name == "f.txt")
    assert entry.size == len(content)
    fs.close()


def test_grp_open_read_correct_bytes(tmp_path: Path) -> None:
    content = b"binary content \x00\x01\x02"
    grp = _make_grp(tmp_path / "test.grp", {"data.bin": content})
    fs = GrpFileSystem(grp, _FS.from_path(grp))
    vp = VfsPath(fs=fs, parts=("", "data.bin"))
    with fs.open_read(vp) as fh:
        assert fh.read() == content
    fs.close()


def test_grp_case_insensitive_lookup(tmp_path: Path) -> None:
    grp = _make_grp(tmp_path / "test.grp", {"PALETTE.DAT": b"\xab\xcd"})
    fs = GrpFileSystem(grp, _FS.from_path(grp))
    # All case variations should work
    for name in ["palette.dat", "PALETTE.DAT", "Palette.Dat"]:
        vp = VfsPath(fs=fs, parts=("", name))
        with fs.open_read(vp) as fh:
            assert fh.read() == b"\xab\xcd"
    # Contains check
    assert "palette.dat" in fs
    assert "PALETTE.DAT" in fs
    assert "missing" not in fs
    fs.close()


def test_grp_stat_file(tmp_path: Path) -> None:
    content = b"file content"
    grp = _make_grp(tmp_path / "test.grp", {"file.txt": content})
    fs = GrpFileSystem(grp, _FS.from_path(grp))
    vp = VfsPath(fs=fs, parts=("", "file.txt"))
    st = fs.stat(vp)
    assert st.is_dir is False
    assert st.size == len(content)
    fs.close()


def test_grp_stat_dir(tmp_path: Path) -> None:
    grp = _make_grp(tmp_path / "test.grp", {"file.txt": b"x"})
    fs = GrpFileSystem(grp, _FS.from_path(grp))
    root = VfsPath(fs=fs, parts=("",))
    st = fs.stat(root)
    assert st.is_dir is True
    assert st.size == 0
    fs.close()


def test_grp_mount_manager_enter(tmp_path: Path) -> None:
    from linux_commander.vfs import FileEntry, MountManager

    grp = _make_grp(tmp_path / "test.grp", {"README.TXT": b"hello"})
    mgr = MountManager()
    entry = FileEntry(
        name="test.grp",
        path=_FS.from_path(grp),
        is_dir=False,
        size=grp.stat().st_size,
        mtime=grp.stat().st_mtime,
    )
    root = mgr.enter(entry)
    assert root is not None
    assert root.parts == ("",)
    # List contents
    entries = root.fs.list_dir(root)
    names = {e.name for e in entries if not e.is_parent}
    assert "README.TXT" in names
    mgr.release(entry)


def test_grp_invalid_magic_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.grp"
    bad.write_bytes(b"NotKenSilverman" + b"\x00" * 20)
    with pytest.raises(OSError, match="bad GRP magic"):
        GrpFileSystem(bad, _FS.from_path(bad))


def test_grp_too_short_raises(tmp_path: Path) -> None:
    bad = tmp_path / "short.grp"
    bad.write_bytes(b"Ken")
    with pytest.raises(OSError, match="too short"):
        GrpFileSystem(bad, _FS.from_path(bad))


def test_grp_write_file(tmp_path: Path) -> None:
    grp = _make_grp(tmp_path / "test.grp", {"ORIGINAL.TXT": b"original"})
    fs = GrpFileSystem(grp, _FS.from_path(grp))

    # Write new file
    vp = VfsPath(fs=fs, parts=("", "NEWFILE.TXT"))
    with fs.open_write(vp) as fh:
        fh.write(b"new content")
    fs.close()

    # Reopen and verify
    fs2 = GrpFileSystem(grp, _FS.from_path(grp))
    with fs2.open_read(vp) as fh:
        assert fh.read() == b"new content"
    fs2.close()


def test_grp_overwrite_file(tmp_path: Path) -> None:
    grp = _make_grp(tmp_path / "test.grp", {"FILE.TXT": b"old"})
    fs = GrpFileSystem(grp, _FS.from_path(grp))

    # Overwrite
    vp = VfsPath(fs=fs, parts=("", "FILE.TXT"))
    with fs.open_write(vp) as fh:
        fh.write(b"new")
    fs.close()

    # Reopen and verify
    fs2 = GrpFileSystem(grp, _FS.from_path(grp))
    with fs2.open_read(vp) as fh:
        assert fh.read() == b"new"
    fs2.close()


def test_grp_delete_file(tmp_path: Path) -> None:
    grp = _make_grp(tmp_path / "test.grp", {"TODELETE.TXT": b"x", "KEEP.TXT": b"y"})
    fs = GrpFileSystem(grp, _FS.from_path(grp))

    fs.delete(VfsPath(fs=fs, parts=("", "TODELETE.TXT")))
    fs.close()

    # Reopen and verify
    fs2 = GrpFileSystem(grp, _FS.from_path(grp))
    root = VfsPath(fs=fs2, parts=("",))
    names = {e.name for e in fs2.list_dir(root) if not e.is_parent}
    assert "TODELETE.TXT" not in names
    assert "KEEP.TXT" in names
    fs2.close()


def test_grp_rename_file(tmp_path: Path) -> None:
    grp = _make_grp(tmp_path / "test.grp", {"OLDNAME.TXT": b"content"})
    fs = GrpFileSystem(grp, _FS.from_path(grp))

    fs.rename(
        VfsPath(fs=fs, parts=("", "OLDNAME.TXT")),
        VfsPath(fs=fs, parts=("", "NEWNAME.TXT")),
    )
    fs.close()

    # Reopen and verify
    fs2 = GrpFileSystem(grp, _FS.from_path(grp))
    root = VfsPath(fs=fs2, parts=("",))
    names = {e.name for e in fs2.list_dir(root) if not e.is_parent}
    assert "OLDNAME.TXT" not in names
    assert "NEWNAME.TXT" in names
    with fs2.open_read(VfsPath(fs=fs2, parts=("", "NEWNAME.TXT"))) as fh:
        assert fh.read() == b"content"
    fs2.close()


def test_grp_mkdir(tmp_path: Path) -> None:
    grp = _make_grp(tmp_path / "test.grp", {"FILE.TXT": b"x"})
    fs = GrpFileSystem(grp, _FS.from_path(grp))

    # Create directory (GRP is flat, so this creates a synthetic dir entry)
    fs.mkdir(VfsPath(fs=fs, parts=("", "SUBDIR")))

    # Verify it appears in listing within same session
    root = VfsPath(fs=fs, parts=("",))
    names = {e.name for e in fs.list_dir(root) if not e.is_parent}
    assert "SUBDIR" in names
    fs.close()


def test_grp_persist_on_close(tmp_path: Path) -> None:
    """Modifications are persisted when close() is called."""
    grp = _make_grp(tmp_path / "test.grp", {"ORIG.TXT": b"orig"})

    fs = GrpFileSystem(grp, _FS.from_path(grp))
    with fs.open_write(VfsPath(fs=fs, parts=("", "NEW.TXT"))) as fh:
        fh.write(b"new")
    fs.delete(VfsPath(fs=fs, parts=("", "ORIG.TXT")))
    fs.close()  # Should trigger rewrite

    # Reopen fresh and verify
    fs2 = GrpFileSystem(grp, _FS.from_path(grp))
    root = VfsPath(fs=fs2, parts=("",))
    names = {e.name for e in fs2.list_dir(root) if not e.is_parent}
    assert "ORIG.TXT" not in names
    assert "NEW.TXT" in names
    with fs2.open_read(VfsPath(fs=fs2, parts=("", "NEW.TXT"))) as fh:
        assert fh.read() == b"new"
    fs2.close()


def test_grp_read_prefix(tmp_path: Path) -> None:
    content = b"0123456789" * 100  # 1000 bytes
    grp = _make_grp(tmp_path / "test.grp", {"BIG.BIN": content})
    fs = GrpFileSystem(grp, _FS.from_path(grp))
    vp = VfsPath(fs=fs, parts=("", "BIG.BIN"))

    data, truncated = fs.read_prefix(vp, 50)
    assert data == content[:50]
    assert truncated is True

    data, truncated = fs.read_prefix(vp, 2000)
    assert data == content
    assert truncated is False
    fs.close()
