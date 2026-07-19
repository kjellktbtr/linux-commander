"""Tests for linux_commander.fs (sort / format utilities) and LocalFileSystem listing."""

from __future__ import annotations

import os
import time
from pathlib import Path

from linux_commander.fs import (
    format_mtime,
    format_size,
    sort_entries,
    split_extension,
)
from linux_commander.vfs import LocalFileSystem

_FS = LocalFileSystem()


def _list(path: Path, show_hidden: bool = True):
    """List `path` via LocalFileSystem, optionally filtering dotfiles."""
    entries = _FS.list_dir(_FS.from_path(path))
    if not show_hidden:
        entries = [e for e in entries if e.is_parent or not e.name.startswith(".")]
    return entries


def test_list_directory_includes_parent_entry(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    entries = _list(tmp_path)
    assert entries[0].is_parent
    assert entries[0].name == ".."
    assert entries[0].path == _FS.from_path(tmp_path.parent)


def test_list_directory_lists_files_and_dirs(tmp_path: Path) -> None:
    (tmp_path / "a_file.txt").write_text("hello")
    (tmp_path / "a_dir").mkdir()
    entries = _list(tmp_path)
    names = {e.name for e in entries if not e.is_parent}
    assert names == {"a_file.txt", "a_dir"}

    file_entry = next(e for e in entries if e.name == "a_file.txt")
    assert not file_entry.is_dir
    assert file_entry.size == len("hello")

    dir_entry = next(e for e in entries if e.name == "a_dir")
    assert dir_entry.is_dir


def test_list_directory_hidden_filtering(tmp_path: Path) -> None:
    (tmp_path / ".hidden").write_text("x")
    (tmp_path / "visible.txt").write_text("x")

    shown = _list(tmp_path, show_hidden=True)
    names_shown = {e.name for e in shown if not e.is_parent}
    assert names_shown == {".hidden", "visible.txt"}

    hidden_filtered = _list(tmp_path, show_hidden=False)
    names_filtered = {e.name for e in hidden_filtered if not e.is_parent}
    assert names_filtered == {"visible.txt"}


def test_list_directory_at_filesystem_root_has_no_parent_entry() -> None:
    entries = _list(Path("/"))
    assert not any(e.is_parent for e in entries)


def test_list_directory_skips_unreadable_directory_without_raising(tmp_path: Path) -> None:
    unreadable = tmp_path / "no_access"
    unreadable.mkdir()
    (unreadable / "secret.txt").write_text("x")
    os.chmod(unreadable, 0o000)
    try:
        entries = _list(unreadable)
        assert all(e.is_parent for e in entries)
    finally:
        os.chmod(unreadable, 0o755)


def test_sort_entries_dirs_first_and_parent_pinned(tmp_path: Path) -> None:
    (tmp_path / "b_dir").mkdir()
    (tmp_path / "a_file.txt").write_text("x")
    (tmp_path / "a_dir").mkdir()
    entries = _list(tmp_path)

    sorted_entries = sort_entries(entries, key="name")
    assert sorted_entries[0].is_parent

    non_parent = [e for e in sorted_entries if not e.is_parent]
    dir_names = [e.name for e in non_parent if e.is_dir]
    file_names = [e.name for e in non_parent if not e.is_dir]
    assert dir_names == ["a_dir", "b_dir"]
    assert file_names == ["a_file.txt"]
    dir_positions = [i for i, e in enumerate(non_parent) if e.is_dir]
    file_positions = [i for i, e in enumerate(non_parent) if not e.is_dir]
    assert max(dir_positions) < min(file_positions)


def test_sort_entries_by_size(tmp_path: Path) -> None:
    (tmp_path / "small.txt").write_text("x")
    (tmp_path / "big.txt").write_text("x" * 100)
    entries = _list(tmp_path)
    sorted_entries = sort_entries(entries, key="size")
    non_parent = [e for e in sorted_entries if not e.is_parent]
    assert [e.name for e in non_parent] == ["small.txt", "big.txt"]


def test_sort_entries_by_mtime_reverse(tmp_path: Path) -> None:
    older = tmp_path / "older.txt"
    older.write_text("x")
    time.sleep(0.01)
    newer = tmp_path / "newer.txt"
    newer.write_text("x")
    entries = _list(tmp_path)
    sorted_entries = sort_entries(entries, key="mtime", reverse=True)
    non_parent = [e for e in sorted_entries if not e.is_parent]
    assert non_parent[0].name == "newer.txt"


def test_format_size() -> None:
    assert format_size(0) == "0B"
    assert format_size(512) == "512B"
    assert format_size(1024) == "1.0K"
    assert format_size(1536) == "1.5K"
    assert format_size(1024 * 1024) == "1.0M"
    assert format_size(1024 * 1024 * 1024) == "1.0G"


def test_format_mtime() -> None:
    import datetime as dt

    ts = dt.datetime(2024, 1, 15, 10, 30, 0).timestamp()
    assert format_mtime(ts) == "2024-01-15 10:30"


def test_format_mtime_blank_for_unknown_sentinel() -> None:
    """mtime<=0 is the codebase-wide "no known mtime" sentinel (VFS '..'
    entries, archive-internal directories, Jottacloud folders -- JFS never
    returns a <modified> timestamp for folders). It must render as blank,
    not a misleading 1970-01-01 epoch date."""
    assert format_mtime(0.0) == ""
    assert format_mtime(-1.0) == ""


def test_split_extension_simple() -> None:
    assert split_extension("archive.zip") == "zip"
    assert split_extension("photo.heic") == "heic"  # unknown -> last segment fallback


def test_split_extension_compound_container_codec() -> None:
    assert split_extension("backup.tar.gz") == "tar.gz"
    assert split_extension("backup.tar.bz2") == "tar.bz2"
    assert split_extension("backup.grp.zst") == "grp.zst"


def test_split_extension_compound_with_crypt_wrapper() -> None:
    assert split_extension("backup.tar.gz.crp") == "tar.gz.crp"


def test_split_extension_stops_at_unknown_segment() -> None:
    # "name" isn't a recognised token, so only the trailing ".txt" counts.
    assert split_extension("my.file.name.txt") == "txt"


def test_split_extension_dotfile_has_no_extension() -> None:
    assert split_extension(".gitignore") == ""


def test_split_extension_dotfile_with_real_extension() -> None:
    assert split_extension(".bashrc.bak") == "bak"


def test_split_extension_no_dot() -> None:
    assert split_extension("no_ext") == ""


def test_split_extension_caps_long_compound_chains() -> None:
    # Every segment is a known token ("tar"), so the naive chain would grow
    # unbounded; the length cap falls back to the last segment alone.
    long_name = "a." + ".".join(["tar"] * 10) + ".gz"
    ext = split_extension(long_name)
    assert ext == "gz"


def test_sort_entries_by_extension(tmp_path: Path) -> None:
    (tmp_path / "b.zip").write_text("x")
    (tmp_path / "a.tar.gz").write_text("x")
    (tmp_path / "c_no_ext").write_text("x")
    entries = _list(tmp_path)
    sorted_entries = sort_entries(entries, key="extension")
    non_parent = [e for e in sorted_entries if not e.is_parent]
    # "" (c_no_ext) sorts before "tar.gz" (a.tar.gz) before "zip" (b.zip).
    assert [e.name for e in non_parent] == ["c_no_ext", "a.tar.gz", "b.zip"]


def test_sort_entries_by_extension_ignores_directory_names(tmp_path: Path) -> None:
    (tmp_path / "some.dir.name").mkdir()
    (tmp_path / "z.txt").write_text("x")
    entries = _list(tmp_path)
    sorted_entries = sort_entries(entries, key="extension")
    non_parent = [e for e in sorted_entries if not e.is_parent]
    # Directories are grouped before files regardless of sort key (existing
    # behavior), and a directory's dotted name never counts as an extension.
    assert non_parent[0].name == "some.dir.name"
    assert non_parent[0].is_dir
