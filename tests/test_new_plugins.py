"""Tests for new archive plugins (compress, rar, sevenzip) and file_ops registry."""

from __future__ import annotations

import base64
import gzip
from pathlib import Path

import pytest

from linux_commander.vfs import LocalFileSystem, VfsPath

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _local_vpath(path: Path) -> VfsPath:
    return LocalFileSystem().from_path(path)


# ---------------------------------------------------------------------------
# compress_plugin — single-file gz/bz2/xz
# ---------------------------------------------------------------------------


def test_compress_plugin_registered_gz() -> None:
    from linux_commander.plugins import plugin_for_name

    assert plugin_for_name("data.csv.gz") is not None


def test_compress_plugin_registered_bz2() -> None:
    from linux_commander.plugins import plugin_for_name, tar_plugin

    assert plugin_for_name("archive.tar.bz2") is tar_plugin  # handled by tar plugin, not compress
    assert plugin_for_name("data.csv.bz2") is not None


def test_compress_plugin_registered_xz() -> None:
    from linux_commander.plugins import plugin_for_name

    assert plugin_for_name("file.xz") is not None


def test_compress_gz_list_and_read(tmp_path: Path) -> None:
    from linux_commander.plugins.compress_plugin import CompressFileSystem

    content = b"hello from gzip " * 100
    gz_path = tmp_path / "data.csv.gz"
    with gzip.open(gz_path, "wb") as f:
        f.write(content)

    host_vpath = _local_vpath(gz_path)
    fs = CompressFileSystem(str(gz_path), host_vpath)
    root = VfsPath(fs=fs, parts=("",))

    entries = fs.list_dir(root)
    assert len(entries) == 2  # ".." + "data.csv"
    names = {e.name for e in entries}
    assert ".." in names
    assert "data.csv" in names

    member = root / "data.csv"
    with fs.open_read(member) as f:
        assert f.read() == content


def test_compress_plugin_open_fs_preserves_compound_name_from_non_local_host(
    tmp_path: Path,
) -> None:
    """Regression test: open_fs() used to call materialize(), which spills to
    a *randomly named* temp file (only keeping the last suffix, e.g. ".gz")
    when the host has no real local file (a remote backend like Jottacloud,
    or a nested archive member). Since this format derives its member name
    by stripping just the outer suffix off the archive's OWN filename (e.g.
    "backup.grp.gz" -> "backup.grp"), a random basename surfaced as the
    member name -- e.g. "tmpAbC123" instead of "backup.grp". Fixed to use
    spill_named_temp(), which preserves the whole filename. Uses a
    ZipFileSystem as a convenient non-local (realpath()=None) host, standing
    in for the whole class of such backends."""
    import gzip
    import zipfile

    from linux_commander.plugins.compress_plugin import open_fs
    from linux_commander.plugins.zip_plugin import ZipFileSystem

    content = gzip.compress(b"grp archive bytes")
    zp = tmp_path / "container.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("backup.grp.gz", content)
    zip_fs = ZipFileSystem(zp, _local_vpath(zp))
    host_path = VfsPath(fs=zip_fs, parts=("", "backup.grp.gz"))
    assert zip_fs.realpath(host_path) is None  # this is the case that used to break

    fs = open_fs(zip_fs, host_path)
    root = VfsPath(fs=fs, parts=("",))
    names = {e.name for e in fs.list_dir(root) if not e.is_parent}

    assert names == {"backup.grp"}
    zip_fs.close()


def test_compress_gz_read_prefix(tmp_path: Path) -> None:
    import gzip

    from linux_commander.plugins.compress_plugin import CompressFileSystem

    content = b"x" * 2000
    gz_path = tmp_path / "big.bin.gz"
    with gzip.open(gz_path, "wb") as f:
        f.write(content)

    host_vpath = _local_vpath(gz_path)
    fs = CompressFileSystem(str(gz_path), host_vpath)
    root = VfsPath(fs=fs, parts=("",))
    member = root / "big.bin"

    data, truncated = fs.read_prefix(member, 100)
    assert len(data) == 100
    assert truncated is True
    assert data == content[:100]


def test_compress_bz2_round_trip(tmp_path: Path) -> None:
    import bz2

    from linux_commander.plugins.compress_plugin import CompressFileSystem

    content = b"bz2 content here"
    bz2_path = tmp_path / "notes.txt.bz2"
    with bz2.open(bz2_path, "wb") as f:
        f.write(content)

    host_vpath = _local_vpath(bz2_path)
    fs = CompressFileSystem(str(bz2_path), host_vpath)
    root = VfsPath(fs=fs, parts=("",))

    entries = fs.list_dir(root)
    member_names = {e.name for e in entries if not e.is_parent}
    assert "notes.txt" in member_names

    member = root / "notes.txt"
    with fs.open_read(member) as f:
        assert f.read() == content


def test_compress_xz_round_trip(tmp_path: Path) -> None:
    import lzma

    from linux_commander.plugins.compress_plugin import CompressFileSystem

    content = b"lzma content here"
    xz_path = tmp_path / "data.xz"
    with lzma.open(xz_path, "wb") as f:
        f.write(content)

    host_vpath = _local_vpath(xz_path)
    fs = CompressFileSystem(str(xz_path), host_vpath)
    root = VfsPath(fs=fs, parts=("",))

    entries = fs.list_dir(root)
    member_names = {e.name for e in entries if not e.is_parent}
    assert "data" in member_names  # ".xz" stripped


def test_compress_not_registered_for_tar_gz() -> None:
    """tar.gz should be handled by tar_plugin, not compress_plugin."""
    from linux_commander.plugins import plugin_for_name, tar_plugin

    plugin = plugin_for_name("archive.tar.gz")
    assert plugin is tar_plugin


# ---------------------------------------------------------------------------
# rar_plugin — read-only (guarded)
# ---------------------------------------------------------------------------


def test_rar_plugin_registered_when_rarfile_installed() -> None:
    pytest.importorskip("rarfile")
    from linux_commander.plugins import plugin_for_name

    plugin = plugin_for_name("archive.rar")
    assert plugin is not None


def test_rar_plugin_not_registered_without_rarfile(monkeypatch: pytest.MonkeyPatch) -> None:
    """If rarfile is absent EXTENSIONS must be empty — simulate via patching."""
    import linux_commander.plugins.rar_plugin as rar_mod

    original = rar_mod.EXTENSIONS
    try:
        rar_mod.EXTENSIONS = ()
        # Reset discovery cache so we can re-discover
        import linux_commander.plugins as pkg

        pkg._ext_map = None
        from linux_commander.plugins import plugin_for_name

        result = plugin_for_name("test.rar")
        assert result is None
    finally:
        rar_mod.EXTENSIONS = original
        import linux_commander.plugins as pkg2

        pkg2._ext_map = None


# ---------------------------------------------------------------------------
# sevenzip_plugin — read+write (guarded)
# ---------------------------------------------------------------------------


def test_sevenzip_plugin_registered_when_py7zr_installed() -> None:
    pytest.importorskip("py7zr")
    from linux_commander.plugins import plugin_for_name

    plugin = plugin_for_name("archive.7z")
    assert plugin is not None


def test_sevenzip_list_and_read(tmp_path: Path) -> None:
    py7zr = pytest.importorskip("py7zr")
    from linux_commander.plugins.sevenzip_plugin import SevenZipFileSystem

    sz_path = tmp_path / "test.7z"
    with py7zr.SevenZipFile(sz_path, "w") as sz:
        sz.writestr(b"hello 7z", "hello.txt")
        sz.writestr(b"sub content", "subdir/file.txt")

    host_vpath = _local_vpath(sz_path)
    fs = SevenZipFileSystem(sz_path, host_vpath)
    root = VfsPath(fs=fs, parts=("",))

    entries = fs.list_dir(root)
    names = {e.name for e in entries}
    assert "hello.txt" in names
    assert "subdir" in names

    member = root / "hello.txt"
    with fs.open_read(member) as f:
        assert f.read() == b"hello 7z"


def test_sevenzip_read_prefix(tmp_path: Path) -> None:
    py7zr = pytest.importorskip("py7zr")
    from linux_commander.plugins.sevenzip_plugin import SevenZipFileSystem

    sz_path = tmp_path / "test.7z"
    content = b"A" * 500
    with py7zr.SevenZipFile(sz_path, "w") as sz:
        sz.writestr(content, "bigfile.bin")

    host_vpath = _local_vpath(sz_path)
    fs = SevenZipFileSystem(sz_path, host_vpath)
    root = VfsPath(fs=fs, parts=("",))
    member = root / "bigfile.bin"

    data, truncated = fs.read_prefix(member, 100)
    assert len(data) == 100
    assert truncated is True


def test_sevenzip_write_new_file(tmp_path: Path) -> None:
    py7zr = pytest.importorskip("py7zr")
    from linux_commander.plugins.sevenzip_plugin import SevenZipFileSystem

    sz_path = tmp_path / "test.7z"
    with py7zr.SevenZipFile(sz_path, "w") as sz:
        sz.writestr(b"original", "orig.txt")

    host_vpath = _local_vpath(sz_path)
    fs = SevenZipFileSystem(sz_path, host_vpath)
    root = VfsPath(fs=fs, parts=("",))

    dest = root / "new.txt"
    with fs.open_write(dest) as out:
        out.write(b"new content")

    # Re-read after rewrite
    with fs.open_read(root / "orig.txt") as f:
        assert f.read() == b"original"
    with fs.open_read(root / "new.txt") as f:
        assert f.read() == b"new content"


def test_sevenzip_delete(tmp_path: Path) -> None:
    py7zr = pytest.importorskip("py7zr")
    from linux_commander.plugins.sevenzip_plugin import SevenZipFileSystem

    sz_path = tmp_path / "test.7z"
    with py7zr.SevenZipFile(sz_path, "w") as sz:
        sz.writestr(b"keep me", "keep.txt")
        sz.writestr(b"delete me", "gone.txt")

    host_vpath = _local_vpath(sz_path)
    fs = SevenZipFileSystem(sz_path, host_vpath)
    root = VfsPath(fs=fs, parts=("",))

    fs.delete(root / "gone.txt")

    names = {e.name for e in fs.list_dir(root) if not e.is_parent}
    assert "keep.txt" in names
    assert "gone.txt" not in names


def test_sevenzip_stat(tmp_path: Path) -> None:
    py7zr = pytest.importorskip("py7zr")
    from linux_commander.plugins.sevenzip_plugin import SevenZipFileSystem

    sz_path = tmp_path / "test.7z"
    with py7zr.SevenZipFile(sz_path, "w") as sz:
        sz.writestr(b"12345", "a.txt")

    host_vpath = _local_vpath(sz_path)
    fs = SevenZipFileSystem(sz_path, host_vpath)
    root = VfsPath(fs=fs, parts=("",))

    st = fs.stat(root / "a.txt")
    assert not st.is_dir
    assert st.size == 5

    root_st = fs.stat(root)
    assert root_st.is_dir


# ---------------------------------------------------------------------------
# tar_plugin — zstd extension registration probe
# ---------------------------------------------------------------------------


def test_tar_zstd_extensions_present_on_314() -> None:
    """On Python 3.14+ tarfile supports zstd; verify extensions are registered."""
    from linux_commander.plugins.tar_plugin import _TARFILE_HAS_ZSTD, EXTENSIONS

    if _TARFILE_HAS_ZSTD:
        assert ".tar.zst" in EXTENSIONS
        assert ".tzst" in EXTENSIONS
    else:
        assert ".tar.zst" not in EXTENSIONS
        assert ".tzst" not in EXTENSIONS


# ---------------------------------------------------------------------------
# file_ops — registry and base64 operations
# ---------------------------------------------------------------------------


def test_file_ops_available_operations_returns_base64() -> None:
    from linux_commander.file_ops import available_operations

    ops = available_operations()
    names = [op.name for op in ops]
    assert "Base64 Encode" in names
    assert "Base64 Decode" in names


def test_base64_encode_round_trip(tmp_path: Path) -> None:
    from linux_commander.file_ops.base64_op import _encode
    from linux_commander.vfs import LocalFileSystem

    local = LocalFileSystem()
    content = b"Hello, World! This is test data.\n" * 10
    src_path = tmp_path / "hello.txt"
    src_path.write_bytes(content)

    source = local.from_path(src_path)
    dest_dir = local.from_path(tmp_path)

    errors = _encode([source], dest_dir, lambda c, t, n: None, lambda: False)
    assert errors == []

    encoded_path = tmp_path / "hello.txt.b64"
    assert encoded_path.exists()
    assert base64.b64decode(encoded_path.read_bytes()) == content


def test_base64_decode_round_trip(tmp_path: Path) -> None:
    from linux_commander.file_ops.base64_op import _decode, _encode
    from linux_commander.vfs import LocalFileSystem

    local = LocalFileSystem()
    original = b"Round-trip test data " * 5

    # First encode
    src_path = tmp_path / "data.bin"
    src_path.write_bytes(original)
    source = local.from_path(src_path)
    dest_dir = local.from_path(tmp_path)
    _encode([source], dest_dir, lambda c, t, n: None, lambda: False)

    # Then decode the .b64 file
    b64_path = tmp_path / "data.bin.b64"
    b64_source = local.from_path(b64_path)
    errors = _decode([b64_source], dest_dir, lambda c, t, n: None, lambda: False)
    assert errors == []

    decoded_path = tmp_path / "data.bin"
    assert decoded_path.read_bytes() == original


def test_base64_decode_non_b64_file_returns_error(tmp_path: Path) -> None:
    from linux_commander.file_ops.base64_op import _decode
    from linux_commander.vfs import LocalFileSystem

    local = LocalFileSystem()
    bad_path = tmp_path / "notbase64.txt"
    bad_path.write_bytes(b"plain text")
    source = local.from_path(bad_path)
    dest_dir = local.from_path(tmp_path)

    errors = _decode([source], dest_dir, lambda c, t, n: None, lambda: False)
    assert len(errors) == 1
    assert "notbase64.txt" in errors[0].message or ".b64" in errors[0].message


def test_base64_encode_cancel_mid_batch(tmp_path: Path) -> None:
    from linux_commander.file_ops.base64_op import _encode
    from linux_commander.vfs import LocalFileSystem

    local = LocalFileSystem()
    files = []
    for i in range(5):
        p = tmp_path / f"file{i}.txt"
        p.write_bytes(b"data")
        files.append(local.from_path(p))

    dest_dir = local.from_path(tmp_path)
    call_count = [0]

    def should_cancel() -> bool:
        call_count[0] += 1
        return call_count[0] >= 3  # cancel after 2 files

    errors = _encode(files, dest_dir, lambda c, t, n: None, should_cancel)
    # No errors — just fewer outputs than 5
    assert errors == []
    outputs = list(tmp_path.glob("*.b64"))
    assert len(outputs) < 5
