"""Round-trip tests for the GRP virtual-folder hierarchy and __GRPMAP.J persistence.

Complements the flat-archive GRP tests in test_plugins.py by covering nested/
long original names: does the name mapping survive an edit + close + reopen
cycle, and does browsing correctly reconstruct the original directory
structure from a mix of GRP-truncated and original names.
"""

from __future__ import annotations

from pathlib import Path

from linux_commander.archiving import compress_sources
from linux_commander.plugins.grp_plugin import GrpFileSystem
from linux_commander.vfs import LocalFileSystem, VfsPath

_FS = LocalFileSystem()


def _local_vpath(path: Path) -> VfsPath:
    return _FS.from_path(path)


def _build_nested_grp(tmp_path: Path) -> Path:
    """Build a GRP archive with a nested directory and a long filename."""
    (tmp_path / "top.txt").write_text("top level")
    sub = tmp_path / "level2"
    sub.mkdir()
    (sub / "nested-long-filename.txt").write_text("nested content")

    dest_path = tmp_path / "archive.grp"
    dest = _local_vpath(dest_path)
    sources = [_local_vpath(tmp_path / "top.txt"), _local_vpath(sub)]
    errors = compress_sources(
        sources,
        dest,
        "grp",
        {"container": "grp", "codec": "none"},
        _FS,
        lambda *a: None,
        lambda: False,
    )
    assert not errors
    return dest_path


def test_nested_archive_reconstructs_hierarchy(tmp_path: Path) -> None:
    grp = _build_nested_grp(tmp_path)
    fs = GrpFileSystem(grp, _local_vpath(grp))
    root = VfsPath(fs=fs, parts=("",))
    names = {e.name for e in fs.list_dir(root) if not e.is_parent}
    assert names == {"top.txt", "level2"}

    level2_entry = next(e for e in fs.list_dir(root) if e.name == "level2")
    assert level2_entry.is_dir
    inner_names = {e.name for e in fs.list_dir(level2_entry.path) if not e.is_parent}
    assert inner_names == {"nested-long-filename.txt"}
    fs.close()


def test_nested_archive_file_content_readable(tmp_path: Path) -> None:
    grp = _build_nested_grp(tmp_path)
    fs = GrpFileSystem(grp, _local_vpath(grp))
    root = VfsPath(fs=fs, parts=("",))
    level2_entry = next(e for e in fs.list_dir(root) if e.name == "level2")
    file_entry = next(
        e for e in fs.list_dir(level2_entry.path) if e.name == "nested-long-filename.txt"
    )
    with fs.open_read(file_entry.path) as fh:
        assert fh.read() == b"nested content"
    fs.close()


def test_grpmap_persists_through_edit_close_reopen(tmp_path: Path) -> None:
    """The critical round-trip fix: editing a mapped archive must not drop __GRPMAP.J."""
    grp = _build_nested_grp(tmp_path)

    fs = GrpFileSystem(grp, _local_vpath(grp))
    root = VfsPath(fs=fs, parts=("",))
    level2_entry = next(e for e in fs.list_dir(root) if e.name == "level2")

    # Edit an existing nested file.
    file_entry = next(
        e for e in fs.list_dir(level2_entry.path) if e.name == "nested-long-filename.txt"
    )
    with fs.open_write(file_entry.path) as fh:
        fh.write(b"edited content")

    # Add a brand-new nested file with a long name.
    new_vp = level2_entry.path / "another-long-new-filename.txt"
    with fs.open_write(new_vp) as fh:
        fh.write(b"brand new")

    fs.close()  # triggers _rewrite -- must re-emit __GRPMAP.J

    # Reopen fresh and verify everything survived.
    fs2 = GrpFileSystem(grp, _local_vpath(grp))
    root2 = VfsPath(fs=fs2, parts=("",))
    names2 = {e.name for e in fs2.list_dir(root2) if not e.is_parent}
    assert names2 == {"top.txt", "level2"}

    level2_entry2 = next(e for e in fs2.list_dir(root2) if e.name == "level2")
    inner_names2 = {e.name for e in fs2.list_dir(level2_entry2.path) if not e.is_parent}
    assert inner_names2 == {"nested-long-filename.txt", "another-long-new-filename.txt"}

    edited_entry = next(
        e for e in fs2.list_dir(level2_entry2.path) if e.name == "nested-long-filename.txt"
    )
    with fs2.open_read(edited_entry.path) as fh:
        assert fh.read() == b"edited content"

    new_entry = next(
        e for e in fs2.list_dir(level2_entry2.path) if e.name == "another-long-new-filename.txt"
    )
    with fs2.open_read(new_entry.path) as fh:
        assert fh.read() == b"brand new"

    # The mapping file itself must never leak into a listing.
    assert "__GRPMAP.J" not in names2
    assert "__GRPMAP.J" not in inner_names2
    fs2.close()


def test_delete_removes_stale_name_mapping(tmp_path: Path) -> None:
    """Deleting a mapped file must not leave a stale entry pointing at nothing."""
    grp = _build_nested_grp(tmp_path)
    fs = GrpFileSystem(grp, _local_vpath(grp))
    root = VfsPath(fs=fs, parts=("",))
    level2_entry = next(e for e in fs.list_dir(root) if e.name == "level2")
    file_entry = next(
        e for e in fs.list_dir(level2_entry.path) if e.name == "nested-long-filename.txt"
    )
    fs.delete(file_entry.path)
    fs.close()

    fs2 = GrpFileSystem(grp, _local_vpath(grp))
    root2 = VfsPath(fs=fs2, parts=("",))
    names2 = {e.name for e in fs2.list_dir(root2) if not e.is_parent}
    # level2 had exactly one file, now deleted -- it disappears entirely (GRP
    # has no persisted empty-directory concept).
    assert names2 == {"top.txt"}
    fs2.close()


def test_rename_moves_mapping_to_new_name(tmp_path: Path) -> None:
    grp = _build_nested_grp(tmp_path)
    fs = GrpFileSystem(grp, _local_vpath(grp))
    root = VfsPath(fs=fs, parts=("",))
    level2_entry = next(e for e in fs.list_dir(root) if e.name == "level2")
    file_entry = next(
        e for e in fs.list_dir(level2_entry.path) if e.name == "nested-long-filename.txt"
    )
    dst_vp = level2_entry.path / "renamed-long-filename.txt"
    fs.rename(file_entry.path, dst_vp)
    fs.close()

    fs2 = GrpFileSystem(grp, _local_vpath(grp))
    root2 = VfsPath(fs=fs2, parts=("",))
    level2_entry2 = next(e for e in fs2.list_dir(root2) if e.name == "level2")
    inner_names2 = {e.name for e in fs2.list_dir(level2_entry2.path) if not e.is_parent}
    assert inner_names2 == {"renamed-long-filename.txt"}

    renamed_entry = next(
        e for e in fs2.list_dir(level2_entry2.path) if e.name == "renamed-long-filename.txt"
    )
    with fs2.open_read(renamed_entry.path) as fh:
        assert fh.read() == b"nested content"
    fs2.close()


def _build_deep_identical_names_grp(tmp_path: Path, depth: int = 3, breadth: int = 3) -> Path:
    """Build a directory with an identically-named file at every depth level.

    Replicates the real-world structure (many nested dirs, each containing a
    file with the same name) that once caused a subdirectory several levels
    deep to be misclassified as a file during GRP virtual-folder browsing.
    """
    src_root = tmp_path / "src"
    src_root.mkdir()
    (src_root / "hello.txt").write_text("hello world")

    def _populate(d: Path, level: int) -> None:
        if level >= depth:
            return
        for i in range(breadth):
            child = d / str(i)
            child.mkdir()
            (child / "hello.txt").write_text("hello world")
            _populate(child, level + 1)

    _populate(src_root, 0)

    dest_path = tmp_path / "deep.grp"
    dest = _local_vpath(dest_path)
    sources = [_local_vpath(src_root)]
    errors = compress_sources(
        sources,
        dest,
        "grp",
        {"container": "grp", "codec": "none"},
        _FS,
        lambda *a: None,
        lambda: False,
    )
    assert not errors
    return dest_path


def _extract_grp_to_disk(fs: GrpFileSystem, out_root: Path) -> None:
    def _walk(vpath: VfsPath, disk_dir: Path) -> None:
        for entry in fs.list_dir(vpath):
            if entry.is_parent:
                continue
            target = disk_dir / entry.name
            if entry.is_dir:
                target.mkdir(exist_ok=True)
                _walk(entry.path, target)
            else:
                with fs.open_read(entry.path) as fh:
                    target.write_bytes(fh.read())

    _walk(VfsPath(fs=fs, parts=("",)), out_root)


def test_deep_nesting_with_identically_named_files_round_trips(tmp_path: Path) -> None:
    """Regression test for a real bug: a directory several levels deep, full of
    same-named "hello.txt" files, was misclassified as a file (list_dir wrongly
    reported is_dir=False) partway through browsing, losing everything below it
    on extraction. Verified by extracting the whole tree and diffing it against
    the original directory, byte for byte.
    """
    grp = _build_deep_identical_names_grp(tmp_path)
    src_root = tmp_path / "src"

    fs = GrpFileSystem(grp, _local_vpath(grp))
    out_root = tmp_path / "extracted"
    out_root.mkdir()
    _extract_grp_to_disk(fs, out_root)
    fs.close()

    extracted_root = out_root / "src"
    assert extracted_root.is_dir()

    src_files = {
        p.relative_to(src_root): p.read_bytes() for p in src_root.rglob("*") if p.is_file()
    }
    extracted_files = {
        p.relative_to(extracted_root): p.read_bytes()
        for p in extracted_root.rglob("*")
        if p.is_file()
    }
    assert extracted_files == src_files


def test_flat_archive_no_map_needed_no_grpmap_file(tmp_path: Path) -> None:
    """Short, already-canonical names need no mapping file at all."""
    dest_path = tmp_path / "flat.grp"
    src = tmp_path / "SHORT.TXT"
    src.write_text("flat content")
    dest = _local_vpath(dest_path)
    sources = [_local_vpath(src)]
    errors = compress_sources(
        sources,
        dest,
        "grp",
        {"container": "grp", "codec": "none"},
        _FS,
        lambda *a: None,
        lambda: False,
    )
    assert not errors

    raw = dest_path.read_bytes()
    assert b"__GRPMAP.J" not in raw  # no mapping needed -> no hidden member emitted

    fs = GrpFileSystem(dest_path, _local_vpath(dest_path))
    names = {e.name for e in fs.list_dir(VfsPath(fs=fs, parts=("",))) if not e.is_parent}
    assert names == {"SHORT.TXT"}
    fs.close()
