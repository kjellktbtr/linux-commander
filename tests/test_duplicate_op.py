"""Tests for the duplicate-file-finder pipeline (linux_commander.file_ops.duplicate_op).

Covers the pure algorithmic pieces of the default comparison method (size ->
checksum -> conditional full-content compare) directly, since the top-level
`_run_duplicate_finder` only surfaces its results through a GUI dialog
(`DuplicateResultsDialog`) -- per CLAUDE.md, GUI behavior is verified with
scripted drivers against a real display, not under pytest. These tests also
cover the VFS-generalization of the directory walk (previously silently
returned nothing for any non-local backend) and a real bug in the old hash
grouping (singleton hash groups -- files that are NOT duplicates -- used to
be reported as duplicate groups anyway).
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from linux_commander.file_ops.duplicate_op import (
    DuplicateGroup,
    _compare_content_group,
    _content_equal,
    _find_duplicates_by_size,
    _hash_group,
    _walk_for_duplicates,
)
from linux_commander.plugins.zip_plugin import ZipFileSystem
from linux_commander.vfs import LocalFileSystem, VfsPath

_FS = LocalFileSystem()


def _vp(path: Path) -> VfsPath:
    return _FS.from_path(path)


# ---------------------------------------------------------------------------
# _content_equal
# ---------------------------------------------------------------------------


def test_content_equal_identical_files(tmp_path: Path) -> None:
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_bytes(b"hello world" * 1000)
    b.write_bytes(b"hello world" * 1000)
    assert _content_equal(_vp(a), _vp(b))


def test_content_equal_different_content_same_size(tmp_path: Path) -> None:
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_bytes(b"aaaa")
    b.write_bytes(b"bbbb")
    assert not _content_equal(_vp(a), _vp(b))


def test_content_equal_different_lengths(tmp_path: Path) -> None:
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_bytes(b"short")
    b.write_bytes(b"a much longer file than the other one")
    assert not _content_equal(_vp(a), _vp(b))


def test_content_equal_across_chunk_boundary(tmp_path: Path) -> None:
    """Regression guard: a mismatch exactly at a 64KB chunk boundary must
    still be caught (not masked by comparing truncated/misaligned reads)."""
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    chunk = 65536
    common = b"x" * chunk
    a.write_bytes(common + b"A")
    b.write_bytes(common + b"B")
    assert not _content_equal(_vp(a), _vp(b))


# ---------------------------------------------------------------------------
# _compare_content_group
# ---------------------------------------------------------------------------


def test_compare_content_group_keeps_genuinely_identical_files(tmp_path: Path) -> None:
    paths = []
    for name in ("a.txt", "b.txt", "c.txt"):
        p = tmp_path / name
        p.write_bytes(b"same content")
        paths.append(_vp(p))
    group = DuplicateGroup(files=paths, size=12, hash_value="deadbeef")

    result = _compare_content_group(group, should_cancel=lambda: False)

    assert len(result) == 1
    assert set(result[0].files) == set(paths)


def test_compare_content_group_splits_on_actual_mismatch(tmp_path: Path) -> None:
    """Simulates the (astronomically rare) checksum-collision case: same
    size, same recorded hash_value, but genuinely different bytes -- the
    content compare must NOT report these as duplicates."""
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_bytes(b"aaaaaaaaaaaa")
    b.write_bytes(b"bbbbbbbbbbbb")
    group = DuplicateGroup(files=[_vp(a), _vp(b)], size=12, hash_value="collided")

    result = _compare_content_group(group, should_cancel=lambda: False)

    assert result == []  # both clusters are singletons -> not duplicates


def test_compare_content_group_partial_match_within_group(tmp_path: Path) -> None:
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    c = tmp_path / "c.txt"
    a.write_bytes(b"twins content")
    b.write_bytes(b"twins content")
    c.write_bytes(b"odd one out!!")
    group = DuplicateGroup(files=[_vp(a), _vp(b), _vp(c)], size=13, hash_value="x")

    result = _compare_content_group(group, should_cancel=lambda: False)

    assert len(result) == 1
    assert set(result[0].files) == {_vp(a), _vp(b)}


def test_compare_content_group_respects_cancellation(tmp_path: Path) -> None:
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_bytes(b"same")
    b.write_bytes(b"same")
    group = DuplicateGroup(files=[_vp(a), _vp(b)], size=4, hash_value="x")

    result = _compare_content_group(group, should_cancel=lambda: True)

    assert result == []  # bailed before comparing anything


# ---------------------------------------------------------------------------
# _walk_for_duplicates -- VFS-generalization (used to be local-only)
# ---------------------------------------------------------------------------


def test_walk_for_duplicates_local_directory(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_bytes(b"aaa")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.txt").write_bytes(b"bb")

    files = _walk_for_duplicates(_vp(tmp_path), on_progress=None, should_cancel=lambda: False)

    names = {p.name for p, _size in files}
    assert names == {"a.txt", "b.txt"}


def test_walk_for_duplicates_respects_min_size(tmp_path: Path) -> None:
    (tmp_path / "small.txt").write_bytes(b"a")
    (tmp_path / "big.txt").write_bytes(b"a" * 100)

    files = _walk_for_duplicates(
        _vp(tmp_path), on_progress=None, should_cancel=lambda: False, min_size=10
    )

    names = {p.name for p, _size in files}
    assert names == {"big.txt"}


def test_walk_for_duplicates_works_on_non_local_backend(tmp_path: Path) -> None:
    """Regression test: the old os.walk-based implementation bailed out
    (returning an empty list) for any backend that wasn't LocalFileSystem --
    silently making duplicate search find nothing at all on archives or
    remote mounts (Jottacloud, SMB, WebDAV, SFTP). ZipFileSystem is a
    convenient non-local stand-in for that whole class of backend."""
    zp = tmp_path / "archive.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("a.txt", b"aaa")
        zf.writestr("sub/b.txt", b"bb")
    zip_fs = ZipFileSystem(zp, _vp(zp))
    root = VfsPath(fs=zip_fs, parts=("",))

    files = _walk_for_duplicates(root, on_progress=None, should_cancel=lambda: False)

    names = {p.name for p, _size in files}
    assert names == {"a.txt", "b.txt"}
    zip_fs.close()


# ---------------------------------------------------------------------------
# _find_duplicates_by_size / _hash_group
# ---------------------------------------------------------------------------


def test_find_duplicates_by_size_groups_matching_sizes_only(tmp_path: Path) -> None:
    a, b, c = _vp(tmp_path / "a"), _vp(tmp_path / "b"), _vp(tmp_path / "c")
    groups = _find_duplicates_by_size([(a, 10), (b, 10), (c, 20)])

    assert len(groups) == 1
    assert set(groups[0].files) == {a, b}
    assert groups[0].size == 10


def test_hash_group_drops_singleton_hashes(tmp_path: Path) -> None:
    """Regression test for a real bug: the old code appended a hash bucket
    to the result regardless of whether it had more than one file in it, so
    a file whose checksum didn't match anything else in its size-bucket
    (i.e. NOT a duplicate) was still reported as a "duplicate group" of one."""
    content = b"shared content"
    same_a = tmp_path / "same_a.txt"
    same_b = tmp_path / "same_b.txt"
    unique = tmp_path / "unique.txt"
    same_a.write_bytes(content)
    same_b.write_bytes(content)
    unique.write_bytes(b"x" * len(content))  # same size, different content/hash

    group = DuplicateGroup(
        files=[_vp(same_a), _vp(same_b), _vp(unique)],
        size=len(content),
    )

    result = _hash_group(group, on_progress=None, should_cancel=lambda: False)

    assert len(result) == 1
    assert set(result[0].files) == {_vp(same_a), _vp(same_b)}


# ---------------------------------------------------------------------------
# End-to-end pipeline (size -> checksum -> content), minus the GUI dialogs
# ---------------------------------------------------------------------------


def test_full_pipeline_finds_real_duplicates_and_excludes_near_misses(tmp_path: Path) -> None:
    # Two genuine duplicates.
    (tmp_path / "dup1.txt").write_bytes(b"duplicate content!")
    (tmp_path / "dup2.txt").write_bytes(b"duplicate content!")
    # Same size, different content -> must NOT be reported.
    (tmp_path / "near_miss.txt").write_bytes(b"totally different!")
    # Unique size -> never even grouped.
    (tmp_path / "unique.txt").write_bytes(b"x")

    files = _walk_for_duplicates(_vp(tmp_path), on_progress=None, should_cancel=lambda: False)
    size_groups = _find_duplicates_by_size(files)
    hash_groups: list[DuplicateGroup] = []
    for g in size_groups:
        hash_groups.extend(_hash_group(g, on_progress=None, should_cancel=lambda: False))
    final_groups: list[DuplicateGroup] = []
    for g in hash_groups:
        final_groups.extend(_compare_content_group(g, should_cancel=lambda: False))

    assert len(final_groups) == 1
    names = {p.name for p in final_groups[0].files}
    assert names == {"dup1.txt", "dup2.txt"}
