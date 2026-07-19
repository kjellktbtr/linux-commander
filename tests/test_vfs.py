"""Tests for linux_commander.vfs: VfsPath, LocalFileSystem, MountManager."""

from __future__ import annotations

import os
from pathlib import Path

from linux_commander.vfs import (
    FileEntry,
    LocalFileSystem,
    MountManager,
    StatResult,
    VfsPath,
)

_FS = LocalFileSystem()


# ---------------------------------------------------------------------------
# VfsPath properties
# ---------------------------------------------------------------------------


def test_vfspath_name() -> None:
    p = VfsPath(fs=_FS, parts=("", "home", "user", "foo.zip"))
    assert p.name == "foo.zip"


def test_vfspath_name_root() -> None:
    p = VfsPath(fs=_FS, parts=("",))
    assert p.name == ""


def test_vfspath_parent_normal() -> None:
    p = VfsPath(fs=_FS, parts=("", "home", "user"))
    assert p.parent == VfsPath(fs=_FS, parts=("", "home"))


def test_vfspath_parent_at_root_returns_self() -> None:
    root = VfsPath(fs=_FS, parts=("",))
    assert root.parent is root


def test_vfspath_suffix_with_extension() -> None:
    p = VfsPath(fs=_FS, parts=("", "archive.tar.gz"))
    assert p.suffix == ".gz"


def test_vfspath_suffix_no_extension() -> None:
    p = VfsPath(fs=_FS, parts=("", "Makefile"))
    assert p.suffix == ""


def test_vfspath_suffix_dotfile_is_empty() -> None:
    p = VfsPath(fs=_FS, parts=("", ".gitignore"))
    assert p.suffix == ""


def test_vfspath_truediv_appends_child() -> None:
    p = VfsPath(fs=_FS, parts=("", "home"))
    child = p / "user"
    assert child == VfsPath(fs=_FS, parts=("", "home", "user"))


def test_vfspath_str_root() -> None:
    root = VfsPath(fs=_FS, parts=("",))
    assert str(root) == "/"


def test_vfspath_str_absolute() -> None:
    p = VfsPath(fs=_FS, parts=("", "home", "user"))
    assert str(p) == "/home/user"


# ---------------------------------------------------------------------------
# VfsPath equality and hashing
# ---------------------------------------------------------------------------


def test_vfspath_equal_same_fs_same_parts() -> None:
    a = VfsPath(fs=_FS, parts=("", "home"))
    b = VfsPath(fs=_FS, parts=("", "home"))
    assert a == b


def test_vfspath_unequal_different_parts() -> None:
    a = VfsPath(fs=_FS, parts=("", "home"))
    b = VfsPath(fs=_FS, parts=("", "tmp"))
    assert a != b


def test_vfspath_hashable_usable_in_set() -> None:
    a = VfsPath(fs=_FS, parts=("", "home"))
    b = VfsPath(fs=_FS, parts=("", "home"))
    c = VfsPath(fs=_FS, parts=("", "tmp"))
    s: set[VfsPath] = {a, b, c}
    assert len(s) == 2  # a and b are the same


# ---------------------------------------------------------------------------
# LocalFileSystem class-equality
# ---------------------------------------------------------------------------


def test_local_fs_instances_are_equal() -> None:
    fs1 = LocalFileSystem()
    fs2 = LocalFileSystem()
    assert fs1 == fs2


def test_local_fs_hash_is_stable() -> None:
    fs1 = LocalFileSystem()
    fs2 = LocalFileSystem()
    assert hash(fs1) == hash(fs2)


def test_vfspath_from_different_local_fs_instances_are_equal() -> None:
    fs1 = LocalFileSystem()
    fs2 = LocalFileSystem()
    p1 = VfsPath(fs=fs1, parts=("", "home"))
    p2 = VfsPath(fs=fs2, parts=("", "home"))
    assert p1 == p2


# ---------------------------------------------------------------------------
# LocalFileSystem.from_path / _to_path round-trip
# ---------------------------------------------------------------------------


def test_from_path_absolute() -> None:
    fs = LocalFileSystem()
    vp = fs.from_path(Path("/home/user/foo"))
    assert vp.parts == ("", "home", "user", "foo")
    assert fs.realpath(vp) == Path("/home/user/foo")


def test_from_path_root() -> None:
    fs = LocalFileSystem()
    vp = fs.from_path(Path("/"))
    assert vp.parts == ("",)
    assert fs.realpath(vp) == Path("/")


def test_from_path_relative() -> None:
    fs = LocalFileSystem()
    vp = fs.from_path(Path("foo/bar"))
    assert vp.parts == ("foo", "bar")


# ---------------------------------------------------------------------------
# LocalFileSystem.list_dir — mirrors test_fs.py coverage
# ---------------------------------------------------------------------------


def test_list_dir_includes_parent_entry(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    fs = LocalFileSystem()
    entries = fs.list_dir(fs.from_path(tmp_path))
    assert entries[0].is_parent
    assert entries[0].name == ".."
    assert entries[0].path == fs.from_path(tmp_path.parent)


def test_list_dir_lists_files_and_dirs(tmp_path: Path) -> None:
    (tmp_path / "a_file.txt").write_text("hello")
    (tmp_path / "a_dir").mkdir()
    fs = LocalFileSystem()
    entries = fs.list_dir(fs.from_path(tmp_path))
    names = {e.name for e in entries if not e.is_parent}
    assert names == {"a_file.txt", "a_dir"}

    file_entry = next(e for e in entries if e.name == "a_file.txt")
    assert not file_entry.is_dir
    assert file_entry.size == len("hello")

    dir_entry = next(e for e in entries if e.name == "a_dir")
    assert dir_entry.is_dir


def test_list_dir_at_filesystem_root_has_no_parent_entry() -> None:
    fs = LocalFileSystem()
    entries = fs.list_dir(fs.from_path(Path("/")))
    assert not any(e.is_parent for e in entries)


def test_list_dir_skips_unreadable_directory_without_raising(tmp_path: Path) -> None:
    unreadable = tmp_path / "no_access"
    unreadable.mkdir()
    (unreadable / "secret.txt").write_text("x")
    os.chmod(unreadable, 0o000)
    try:
        fs = LocalFileSystem()
        entries = fs.list_dir(fs.from_path(unreadable))
        assert all(e.is_parent for e in entries)
    finally:
        os.chmod(unreadable, 0o755)


# ---------------------------------------------------------------------------
# LocalFileSystem.stat and open_read
# ---------------------------------------------------------------------------


def test_stat_file(tmp_path: Path) -> None:
    f = tmp_path / "file.txt"
    f.write_text("hello")
    fs = LocalFileSystem()
    result = fs.stat(fs.from_path(f))
    assert isinstance(result, StatResult)
    assert not result.is_dir
    assert result.size == len("hello")
    assert result.mtime > 0


def test_stat_dir(tmp_path: Path) -> None:
    fs = LocalFileSystem()
    result = fs.stat(fs.from_path(tmp_path))
    assert result.is_dir


def test_open_read(tmp_path: Path) -> None:
    f = tmp_path / "data.bin"
    f.write_bytes(b"\x00\x01\x02")
    fs = LocalFileSystem()
    with fs.open_read(fs.from_path(f)) as fh:
        data = fh.read()
    assert data == b"\x00\x01\x02"


# ---------------------------------------------------------------------------
# MountManager Phase-1 stub behaviour
# ---------------------------------------------------------------------------


def test_mount_manager_enter_returns_none_for_unknown_extension() -> None:
    fs = LocalFileSystem()
    vp = fs.from_path(Path("/tmp/readme.txt"))
    entry = FileEntry(name="readme.txt", path=vp, is_dir=False, size=0, mtime=0.0)
    mm = MountManager()
    assert mm.enter(entry) is None


def test_mount_manager_resolve_up_normal() -> None:
    fs = LocalFileSystem()
    p = fs.from_path(Path("/home/user/docs"))
    mm = MountManager()
    new_path, select_name = mm.resolve_up(p)
    assert new_path == fs.from_path(Path("/home/user"))
    assert select_name == "docs"


def test_mount_manager_resolve_up_at_root() -> None:
    fs = LocalFileSystem()
    root = fs.from_path(Path("/"))
    mm = MountManager()
    new_path, select_name = mm.resolve_up(root)
    assert new_path == root  # no movement
    assert select_name == ""


# ---------------------------------------------------------------------------
# MountManager.release_if_leaving
# ---------------------------------------------------------------------------


class _FakeFS:
    """Minimal FileSystem stand-in that records close() calls."""

    writable: bool = False
    display_prefix: str = ""

    def __init__(self, name: str) -> None:
        self.name = name
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def list_dir(self, path: object) -> list:  # type: ignore[override]
        return []

    def stat(self, path: object) -> object:  # type: ignore[override]
        raise OSError

    def open_read(self, path: object) -> object:  # type: ignore[override]
        raise OSError


def _inject_mount(mm: MountManager, host_path: VfsPath, fs: _FakeFS) -> None:
    """Insert a fake mount directly into MountManager internals for testing."""
    mm._mounts[host_path] = (fs, 1)  # type: ignore[arg-type]
    mm._fs_to_host[id(fs)] = host_path


def test_release_if_leaving_same_fs_is_noop() -> None:
    mm = MountManager()
    local = LocalFileSystem()
    path = local.from_path(Path("/tmp"))
    fake = _FakeFS("outer")
    _inject_mount(mm, path, fake)
    mm.release_if_leaving(fake, fake)  # type: ignore[arg-type]
    assert not fake.closed


def test_release_if_leaving_unrelated_fs_releases() -> None:
    """Going to an unrelated fs (e.g. volume button from archive) should release."""
    mm = MountManager()
    local = LocalFileSystem()
    host_path = local.from_path(Path("/tmp/archive.zip"))
    outer = _FakeFS("outer")
    _inject_mount(mm, host_path, outer)

    local_target_path = local.from_path(Path("/home"))
    mm.release_if_leaving(outer, local_target_path.fs)  # type: ignore[arg-type]
    assert outer.closed


def test_release_if_leaving_entering_nested_does_not_release() -> None:
    """Entering an archive that is mounted on top of old_fs must NOT release old_fs."""
    mm = MountManager()
    local = LocalFileSystem()

    # outer mount: tar at /tmp/arch.tar (on local)
    outer_host = local.from_path(Path("/tmp/arch.tar"))
    outer_fs = _FakeFS("tar")
    _inject_mount(mm, outer_host, outer_fs)

    # inner mount: zip at inner.zip inside the tar (on outer_fs)
    inner_host = VfsPath(fs=outer_fs, parts=("", "inner.zip"))  # type: ignore[arg-type]
    inner_fs = _FakeFS("zip")
    _inject_mount(mm, inner_host, inner_fs)

    # Entering inner_fs FROM outer_fs — must not release outer_fs
    mm.release_if_leaving(outer_fs, inner_fs)  # type: ignore[arg-type]
    assert not outer_fs.closed


# ---------------------------------------------------------------------------
# MountManager.close_all — reverse insertion order
# ---------------------------------------------------------------------------


def test_close_all_closes_innermost_first() -> None:
    """close_all should close the last-registered fs before earlier ones."""
    mm = MountManager()
    local = LocalFileSystem()

    closed_order: list[str] = []

    class _OrderedFakeFS(_FakeFS):
        def close(self) -> None:
            closed_order.append(self.name)
            self.closed = True

    outer = _OrderedFakeFS("outer")
    inner = _OrderedFakeFS("inner")
    _inject_mount(mm, local.from_path(Path("/tmp/arch.tar")), outer)
    _inject_mount(mm, local.from_path(Path("/tmp/inner.zip")), inner)

    mm.close_all()

    assert closed_order == ["inner", "outer"]
    assert mm._mounts == {}
    assert mm._fs_to_host == {}
