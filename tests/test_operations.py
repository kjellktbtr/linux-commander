"""Tests for linux_commander.operations."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from linux_commander.operations import (
    copy_entries,
    delete_entries,
    make_directory,
    move_entries,
    rename_entry,
    resolve_dest_path,
)
from linux_commander.plugins.zip_plugin import ZipFileSystem
from linux_commander.vfs import LocalFileSystem, VfsPath

_FS = LocalFileSystem()


def _vp(path: Path):
    """Shorthand: convert a pathlib.Path to a VfsPath on the local filesystem."""
    return _FS.from_path(path)


def _make_tree(root: Path) -> Path:
    """Build a small nested directory tree under `root` and return its path."""
    tree = root / "tree"
    (tree / "sub").mkdir(parents=True)
    (tree / "top.txt").write_text("top")
    (tree / "sub" / "nested.txt").write_text("nested")
    return tree


def test_copy_entries_copies_files_and_nested_dirs(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    file1 = src_dir / "file1.txt"
    file1.write_text("hello")
    tree = _make_tree(src_dir)

    dest_dir = tmp_path / "dest"
    errors = copy_entries([_vp(file1), _vp(tree)], _vp(dest_dir))

    assert errors == []
    assert (dest_dir / "file1.txt").read_text() == "hello"
    assert (dest_dir / "tree" / "top.txt").read_text() == "top"
    assert (dest_dir / "tree" / "sub" / "nested.txt").read_text() == "nested"
    assert file1.exists()
    assert tree.exists()


def test_copy_entries_reports_progress(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    f1 = src_dir / "a.txt"
    f1.write_text("a")
    f2 = src_dir / "b.txt"
    f2.write_text("b")
    dest_dir = tmp_path / "dest"

    calls: list[tuple[int, int, str]] = []
    copy_entries(
        [_vp(f1), _vp(f2)],
        _vp(dest_dir),
        on_progress=lambda c, t, n: calls.append((c, t, n)),
    )

    assert calls == [(1, 2, "a.txt"), (2, 2, "b.txt")]


def test_copy_entries_cancel_stops_processing(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    f1 = src_dir / "a.txt"
    f1.write_text("a")
    f2 = src_dir / "b.txt"
    f2.write_text("b")
    dest_dir = tmp_path / "dest"

    errors = copy_entries([_vp(f1), _vp(f2)], _vp(dest_dir), should_cancel=lambda: True)

    assert errors == []
    assert not (dest_dir / "a.txt").exists()
    assert not (dest_dir / "b.txt").exists()


def test_copy_entries_file_overwrites_existing_destination_file(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    f1 = src_dir / "a.txt"
    f1.write_text("a")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()
    (dest_dir / "a.txt").write_text("already here")

    errors = copy_entries([_vp(f1)], _vp(dest_dir))

    assert errors == []
    assert (dest_dir / "a.txt").read_text() == "a"


def test_copy_entries_dir_collision_is_an_error(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    tree = _make_tree(src_dir)
    dest_dir = tmp_path / "dest"
    (dest_dir / "tree").mkdir(parents=True)

    errors = copy_entries([_vp(tree)], _vp(dest_dir))

    assert len(errors) == 1
    assert errors[0].path == _vp(tree)
    assert "already exists" in errors[0].message


def test_move_entries_moves_files_and_dirs(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    f1 = src_dir / "file1.txt"
    f1.write_text("hello")
    tree = _make_tree(src_dir)
    dest_dir = tmp_path / "dest"

    errors = move_entries([_vp(f1), _vp(tree)], _vp(dest_dir))

    assert errors == []
    assert (dest_dir / "file1.txt").read_text() == "hello"
    assert (dest_dir / "tree" / "top.txt").read_text() == "top"
    assert not f1.exists()
    assert not tree.exists()


def test_delete_entries_removes_files_and_dirs(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    f1 = src_dir / "file1.txt"
    f1.write_text("hello")
    tree = _make_tree(src_dir)

    errors = delete_entries([_vp(f1), _vp(tree)])

    assert errors == []
    assert not f1.exists()
    assert not tree.exists()


def test_delete_entries_collects_error_for_missing_path(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.txt"
    errors = delete_entries([_vp(missing)])
    assert len(errors) == 1
    assert errors[0].path == _vp(missing)


def test_make_directory_creates_dir(tmp_path: Path) -> None:
    new_dir_vp = make_directory(_vp(tmp_path), "newdir")
    assert _FS.realpath(new_dir_vp).is_dir()
    assert new_dir_vp == _vp(tmp_path / "newdir")


def test_make_directory_collision_raises(tmp_path: Path) -> None:
    (tmp_path / "existing").mkdir()
    with pytest.raises(FileExistsError, match="already exists"):
        make_directory(_vp(tmp_path), "existing")


def test_rename_entry_renames_within_parent(tmp_path: Path) -> None:
    original = tmp_path / "old.txt"
    original.write_text("content")
    renamed = rename_entry(_vp(original), "new.txt")
    assert renamed == _vp(tmp_path / "new.txt")
    assert _FS.realpath(renamed).read_text() == "content"
    assert not original.exists()


def test_rename_entry_collision_raises(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    with pytest.raises(FileExistsError, match="already exists"):
        rename_entry(_vp(tmp_path / "a.txt"), "b.txt")


# ---------------------------------------------------------------------------
# Cross-backend (zip → local) operations
# ---------------------------------------------------------------------------


def _make_zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return path


def test_copy_entries_file_from_zip_to_local(tmp_path: Path) -> None:
    content = b"hello from archive"
    zp = _make_zip(tmp_path / "archive.zip", {"readme.txt": content})
    zip_fs = ZipFileSystem(zp, _FS.from_path(zp))
    src = VfsPath(fs=zip_fs, parts=("", "readme.txt"))
    dest_dir = tmp_path / "out"
    errors = copy_entries([src], _vp(dest_dir))
    assert errors == []
    assert (dest_dir / "readme.txt").read_bytes() == content
    zip_fs.close()


def test_copy_entries_dir_from_zip_to_local(tmp_path: Path) -> None:
    zp = _make_zip(
        tmp_path / "archive.zip",
        {"sub/a.txt": b"aaa", "sub/b.txt": b"bbb"},
    )
    zip_fs = ZipFileSystem(zp, _FS.from_path(zp))
    src = VfsPath(fs=zip_fs, parts=("", "sub"))
    dest_dir = tmp_path / "out"
    errors = copy_entries([src], _vp(dest_dir))
    assert errors == []
    assert (dest_dir / "sub" / "a.txt").read_bytes() == b"aaa"
    assert (dest_dir / "sub" / "b.txt").read_bytes() == b"bbb"
    zip_fs.close()


def _empty_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w"):
        pass
    return path


def test_copy_entries_file_from_local_to_zip(tmp_path: Path) -> None:
    """local -> non-realpath destination (stands in for local -> remote)."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    f1 = src_dir / "file1.txt"
    f1.write_text("hello")

    dest_zip = ZipFileSystem(_empty_zip(tmp_path / "dest.zip"), _vp(tmp_path / "dest.zip"))
    dest_root = VfsPath(fs=dest_zip, parts=("",))

    errors = copy_entries([_vp(f1)], dest_root)

    assert errors == []
    names = {e.name for e in dest_zip.list_dir(dest_root) if not e.is_parent}
    assert "file1.txt" in names
    with dest_zip.open_read(dest_root / "file1.txt") as fh:
        assert fh.read() == b"hello"
    dest_zip.close()


def test_copy_entries_zip_to_zip(tmp_path: Path) -> None:
    """Neither side is OS-backed (realpath is None for both) -- stands in
    for a remote -> remote copy: it must stream via open_read/open_write
    rather than requiring a real local destination path."""
    content = b"cross-backend"
    src_zp = _make_zip(tmp_path / "src.zip", {"a.txt": content})
    src_zip = ZipFileSystem(src_zp, _vp(src_zp))
    src = VfsPath(fs=src_zip, parts=("", "a.txt"))

    dest_zip = ZipFileSystem(_empty_zip(tmp_path / "dest.zip"), _vp(tmp_path / "dest.zip"))
    dest_root = VfsPath(fs=dest_zip, parts=("",))

    errors = copy_entries([src], dest_root)

    assert errors == []
    with dest_zip.open_read(dest_root / "a.txt") as fh:
        assert fh.read() == content
    src_zip.close()
    dest_zip.close()


def test_resolve_dest_path_absolute_on_base_fs(tmp_path: Path) -> None:
    zp = _empty_zip(tmp_path / "archive.zip")
    zip_fs = ZipFileSystem(zp, _vp(zp))
    base = VfsPath(fs=zip_fs, parts=("", "some", "dir"))

    dest = resolve_dest_path(base, f"{zip_fs.display_prefix}/other/place")

    assert dest.fs is zip_fs
    assert dest.parts == ("", "other", "place")
    zip_fs.close()


def test_resolve_dest_path_relative_joins_onto_base(tmp_path: Path) -> None:
    zp = _empty_zip(tmp_path / "archive.zip")
    zip_fs = ZipFileSystem(zp, _vp(zp))
    base = VfsPath(fs=zip_fs, parts=("", "some", "dir"))

    dest = resolve_dest_path(base, "sub")

    assert dest.fs is zip_fs
    assert dest.parts == ("", "some", "dir", "sub")
    zip_fs.close()


def test_resolve_dest_path_local_unaffected() -> None:
    base = _vp(Path("/tmp/somewhere"))
    dest = resolve_dest_path(base, "/tmp/elsewhere")
    assert dest.fs is _FS
    assert dest.parts == ("", "tmp", "elsewhere")


def test_move_entries_from_zip(tmp_path: Path) -> None:
    """Moving from a zip archive: file is copied to destination and deleted from archive."""
    content = b"zip content"
    zp = _make_zip(tmp_path / "archive.zip", {"file.txt": content})
    zip_fs = ZipFileSystem(zp, _FS.from_path(zp))
    src = VfsPath(fs=zip_fs, parts=("", "file.txt"))
    dest_dir = tmp_path / "out"
    errors = move_entries([src], _vp(dest_dir))
    assert errors == []
    # Destination has the file.
    assert (dest_dir / "file.txt").read_bytes() == content
    # Source is deleted from the archive (zip is now writable).
    root = VfsPath(fs=zip_fs, parts=("",))
    names = {e.name for e in zip_fs.list_dir(root) if not e.is_parent}
    assert "file.txt" not in names
    zip_fs.close()


# ---------------------------------------------------------------------------
# Per-file progress across a whole directory tree (regression coverage for
# "copying a directory only shows 1/1 instead of per-file progress") and the
# VFS-delete fix (regression coverage for "F8 delete always fails against
# any backend with no real local file, e.g. Jottacloud/SMB/WebDAV/SFTP").
# ---------------------------------------------------------------------------


def test_copy_entries_local_dir_reports_per_file_progress(tmp_path: Path) -> None:
    """A single selected directory (2 files inside) must report a real 2-file
    total via the local shutil fast path's copy_function hook, not "1/1" for
    the whole tree."""
    src_dir = tmp_path / "src"
    tree = _make_tree(src_dir)  # tree/top.txt, tree/sub/nested.txt
    dest_dir = tmp_path / "dest"

    calls: list[tuple[int, int, str]] = []
    copy_entries(
        [_vp(tree)],
        _vp(dest_dir),
        on_progress=lambda c, t, n: calls.append((c, t, n)),
    )

    assert [(c, t) for c, t, _ in calls] == [(1, 2), (2, 2)]
    assert {n for _, _, n in calls} == {"top.txt", "nested.txt"}


def test_copy_entries_dir_from_zip_reports_per_file_progress(tmp_path: Path) -> None:
    """Same as above but via the cross-backend stream path -- this is the
    exact shape of "uploading a directory to Jottacloud": a directory member
    on a non-realpath backend copied to a real destination."""
    zp = _make_zip(tmp_path / "archive.zip", {"sub/a.txt": b"aaa", "sub/b.txt": b"bbb"})
    zip_fs = ZipFileSystem(zp, _FS.from_path(zp))
    src = VfsPath(fs=zip_fs, parts=("", "sub"))
    dest_dir = tmp_path / "out"

    calls: list[tuple[int, int, str]] = []
    copy_entries([src], _vp(dest_dir), on_progress=lambda c, t, n: calls.append((c, t, n)))

    # The stream path's byte-sub-progress fires an initial 0-byte tick plus a
    # completion tick per file (collapsed to identical (current, total, name)
    # tuples by the 3-arg on_progress fallback), so the *set* of distinct
    # (current, total) pairs reached is what demonstrates the per-file total
    # -- (1, 2) then (2, 2), never "1/1" for the whole directory.
    assert {(c, t) for c, t, _ in calls} == {(1, 2), (2, 2)}
    assert {n for _, _, n in calls} == {"a.txt", "b.txt"}
    zip_fs.close()


def test_copy_entries_cancel_mid_local_dir_stops_after_first_file(tmp_path: Path) -> None:
    """Cancellation is now checked between individual files inside a
    directory, not just between top-level selected items."""
    src_dir = tmp_path / "src"
    tree = _make_tree(src_dir)
    dest_dir = tmp_path / "dest"

    calls: list[tuple[int, int, str]] = []

    def cancel_after_one() -> bool:
        return len(calls) >= 1

    errors = copy_entries(
        [_vp(tree)],
        _vp(dest_dir),
        on_progress=lambda c, t, n: calls.append((c, t, n)),
        should_cancel=cancel_after_one,
    )

    assert errors == []
    assert len(calls) == 1


def test_copy_entries_cancel_mid_stream_tree_stops_after_first_file(tmp_path: Path) -> None:
    """Same cancel-mid-tree contract on the cross-backend stream path."""
    zp = _make_zip(tmp_path / "archive.zip", {"sub/a.txt": b"aaa", "sub/b.txt": b"bbb"})
    zip_fs = ZipFileSystem(zp, _FS.from_path(zp))
    src = VfsPath(fs=zip_fs, parts=("", "sub"))
    dest_dir = tmp_path / "out"

    calls: list[tuple[int, int, str]] = []

    def cancel_after_first_file() -> bool:
        # should_cancel is only checked between whole files, and one file's
        # byte-sub-progress fires two raw callbacks (initial 0-byte tick +
        # completion tick) -- wait for both before cancelling.
        return len(calls) >= 2

    errors = copy_entries(
        [src],
        _vp(dest_dir),
        on_progress=lambda c, t, n: calls.append((c, t, n)),
        should_cancel=cancel_after_first_file,
    )

    assert errors == []
    assert len(calls) == 2
    assert {n for _, _, n in calls} == {"a.txt"}
    zip_fs.close()


def test_move_entries_local_dir_reports_real_file_count(tmp_path: Path) -> None:
    """A same-filesystem move uses os.rename (atomic, no copy_function
    calls), so the running total must still be caught up to the real file
    count in one final tick instead of staying stuck at 0/1."""
    src_dir = tmp_path / "src"
    tree = _make_tree(src_dir)
    dest_dir = tmp_path / "dest"

    calls: list[tuple[int, int, str]] = []
    move_entries([_vp(tree)], _vp(dest_dir), on_progress=lambda c, t, n: calls.append((c, t, n)))

    assert calls == [(2, 2, "tree")]


def test_delete_entries_reports_real_file_count_for_dir(tmp_path: Path) -> None:
    """Deleting one selected directory (2 files inside) must report a real
    2-file total, not "1/1" for the whole tree."""
    tree = _make_tree(tmp_path)

    calls: list[tuple[int, int, str]] = []
    delete_entries([_vp(tree)], on_progress=lambda c, t, n: calls.append((c, t, n)))

    assert calls == [(2, 2, "tree")]
    assert not tree.exists()


def test_delete_entries_deletes_via_vfs_when_backend_has_no_realpath(tmp_path: Path) -> None:
    """Regression test: delete_entries used to require realpath() on every
    entry, so F8 delete against any backend with no real local file (SMB,
    WebDAV, SFTP, Jottacloud) always failed with "Cannot delete from a
    read-only filesystem" even though cmd_delete's own fs.writable check had
    already passed and those backends implement fs.delete(). ZipFileSystem
    is a convenient writable, realpath()-less stand-in for that whole class
    of backend."""
    zp = _make_zip(tmp_path / "archive.zip", {"a.txt": b"aaa", "sub/b.txt": b"bbb"})
    zip_fs = ZipFileSystem(zp, _vp(zp))
    root = VfsPath(fs=zip_fs, parts=("",))
    file_entry = VfsPath(fs=zip_fs, parts=("", "a.txt"))
    dir_entry = VfsPath(fs=zip_fs, parts=("", "sub"))

    assert zip_fs.realpath(file_entry) is None  # this is the case that used to always fail
    assert zip_fs.writable

    errors = delete_entries([file_entry, dir_entry])

    assert errors == []
    names = {e.name for e in zip_fs.list_dir(root) if not e.is_parent}
    assert names == set()
    zip_fs.close()


def test_delete_entries_still_errors_for_readonly_backend_with_no_realpath(tmp_path: Path) -> None:
    """A genuinely read-only, non-realpath backend must still produce a
    clear per-item error rather than silently no-op-ing or crashing."""
    zp = _make_zip(tmp_path / "archive.zip", {"a.txt": b"aaa"})
    zip_fs = ZipFileSystem(zp, _vp(zp))
    zip_fs.writable = False  # simulate a read-only archive format (e.g. tar/7z/rar)
    file_entry = VfsPath(fs=zip_fs, parts=("", "a.txt"))

    errors = delete_entries([file_entry])

    assert len(errors) == 1
    assert errors[0].path == file_entry
    zip_fs.close()
