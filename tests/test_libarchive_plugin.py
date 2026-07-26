"""Tests for the libarchive VFS plugin (extra read-only archive formats).

Fixtures are built with libarchive's own write API (no external CLI tools
needed), so these run anywhere libarchive-c + system libarchive are
installed. Skipped entirely when ``libarchive`` (the ``archives`` extra)
isn't installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("libarchive")

import libarchive  # noqa: E402

from linux_commander.plugins import plugin_for_name  # noqa: E402
from linux_commander.plugins.libarchive_plugin import LibArchiveFileSystem, open_fs  # noqa: E402
from linux_commander.vfs import LocalFileSystem, VfsPath  # noqa: E402

_FS = LocalFileSystem()


def _local_vpath(path: Path) -> VfsPath:
    return _FS.from_path(path)


def _make_cpio(path: Path) -> Path:
    with libarchive.file_writer(str(path), "cpio_newc") as archive:
        archive.add_file_from_memory("a.txt", 5, b"hello")
        archive.add_file_from_memory("sub/b.txt", 6, b"nested")
    return path


def _make_ar(path: Path) -> Path:
    with libarchive.file_writer(str(path), "ar_bsd") as archive:
        archive.add_file_from_memory("one.txt", 3, b"abc")
    return path


def _make_iso(path: Path) -> Path:
    with libarchive.file_writer(str(path), "iso9660") as archive:
        archive.add_file_from_memory("a.txt", 5, b"hello")
        archive.add_file_from_memory("sub/b.txt", 6, b"nested")
    return path


# ---------------------------------------------------------------------------
# plugin discovery
# ---------------------------------------------------------------------------


def test_plugin_registered_for_iso() -> None:
    assert plugin_for_name("disk.iso") is not None


def test_plugin_registered_for_cpio_a_ar_xar() -> None:
    for name in ("archive.cpio", "lib.a", "lib.ar", "archive.xar"):
        assert plugin_for_name(name) is not None, name


# ---------------------------------------------------------------------------
# cpio — no explicit directory entries, so "sub" must be synthesized
# ---------------------------------------------------------------------------


def test_cpio_list_dir_and_synthesized_subdir(tmp_path: Path) -> None:
    archive_path = _make_cpio(tmp_path / "test.cpio")
    fs = LibArchiveFileSystem(archive_path, _local_vpath(archive_path))
    root = VfsPath(fs=fs, parts=("",))

    names = {e.name: e for e in fs.list_dir(root)}
    assert ".." not in names  # root has no synthetic parent entry
    assert "a.txt" in names and not names["a.txt"].is_dir
    assert names["a.txt"].size == 5
    assert "sub" in names and names["sub"].is_dir


def test_cpio_list_dir_nested_file(tmp_path: Path) -> None:
    archive_path = _make_cpio(tmp_path / "test.cpio")
    fs = LibArchiveFileSystem(archive_path, _local_vpath(archive_path))
    root = VfsPath(fs=fs, parts=("",))
    sub_entry = next(e for e in fs.list_dir(root) if e.name == "sub")

    sub_names = {e.name: e for e in fs.list_dir(sub_entry.path)}
    assert ".." in sub_names and sub_names[".."].is_parent
    assert "b.txt" in sub_names
    assert sub_names["b.txt"].size == 6


def test_cpio_open_read_returns_correct_bytes(tmp_path: Path) -> None:
    archive_path = _make_cpio(tmp_path / "test.cpio")
    fs = LibArchiveFileSystem(archive_path, _local_vpath(archive_path))
    vp = VfsPath(fs=fs, parts=("", "sub", "b.txt"))
    with fs.open_read(vp) as f:
        assert f.read() == b"nested"


def test_cpio_stat_file_and_dir(tmp_path: Path) -> None:
    archive_path = _make_cpio(tmp_path / "test.cpio")
    fs = LibArchiveFileSystem(archive_path, _local_vpath(archive_path))
    file_stat = fs.stat(VfsPath(fs=fs, parts=("", "a.txt")))
    assert not file_stat.is_dir
    assert file_stat.size == 5

    dir_stat = fs.stat(VfsPath(fs=fs, parts=("", "sub")))
    assert dir_stat.is_dir


def test_cpio_stat_missing_raises(tmp_path: Path) -> None:
    archive_path = _make_cpio(tmp_path / "test.cpio")
    fs = LibArchiveFileSystem(archive_path, _local_vpath(archive_path))
    with pytest.raises(OSError):
        fs.stat(VfsPath(fs=fs, parts=("", "missing.txt")))


def test_cpio_open_read_missing_raises(tmp_path: Path) -> None:
    archive_path = _make_cpio(tmp_path / "test.cpio")
    fs = LibArchiveFileSystem(archive_path, _local_vpath(archive_path))
    with pytest.raises(OSError):
        fs.open_read(VfsPath(fs=fs, parts=("", "missing.txt")))


# ---------------------------------------------------------------------------
# ar — flat, no directories at all
# ---------------------------------------------------------------------------


def test_ar_flat_archive_has_no_directories(tmp_path: Path) -> None:
    archive_path = _make_ar(tmp_path / "test.a")
    fs = LibArchiveFileSystem(archive_path, _local_vpath(archive_path))
    root = VfsPath(fs=fs, parts=("",))
    entries = {e.name: e for e in fs.list_dir(root)}
    assert "one.txt" in entries
    assert not entries["one.txt"].is_dir
    with fs.open_read(entries["one.txt"].path) as f:
        assert f.read() == b"abc"


# ---------------------------------------------------------------------------
# writable is False; write methods keep the FileSystem base-class defaults
# ---------------------------------------------------------------------------


def test_libarchive_fs_is_read_only(tmp_path: Path) -> None:
    from linux_commander.vfs import WritableFileSystem

    archive_path = _make_iso(tmp_path / "test.iso")
    fs = LibArchiveFileSystem(archive_path, _local_vpath(archive_path))
    assert not isinstance(fs, WritableFileSystem)


def test_libarchive_fs_realpath_is_none(tmp_path: Path) -> None:
    archive_path = _make_iso(tmp_path / "test.iso")
    fs = LibArchiveFileSystem(archive_path, _local_vpath(archive_path))
    assert fs.realpath(VfsPath(fs=fs, parts=("", "a.txt"))) is None


def test_build_index_wraps_corrupt_archive_error(tmp_path: Path) -> None:
    bogus = tmp_path / "not_an_archive.iso"
    bogus.write_bytes(b"this is not a valid archive of any kind, just junk bytes" * 10)
    with pytest.raises(OSError):
        LibArchiveFileSystem(bogus, _local_vpath(bogus))


# ---------------------------------------------------------------------------
# open_fs — the plugin entry point used by MountManager.enter()
# ---------------------------------------------------------------------------


def test_open_fs_mounts_local_iso(tmp_path: Path) -> None:
    archive_path = _make_iso(tmp_path / "test.iso")
    host_vpath = _local_vpath(archive_path)
    fs = open_fs(host_vpath.fs, host_vpath)
    root = VfsPath(fs=fs, parts=("",))
    names = {e.name for e in fs.list_dir(root)}
    assert "a.txt" in names and "sub" in names
