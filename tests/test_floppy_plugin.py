"""Tests for the floppy VFS plugin."""

from __future__ import annotations

import pytest

from linux_commander.fatfs import FLOPPY_FORMATS, FATImageBuilder
from linux_commander.plugins import _discover, get_plugin_load_status
from linux_commander.plugins.floppy_plugin import FloppyFileSystem, open_fs
from linux_commander.vfs import LocalFileSystem, VfsPath


@pytest.fixture
def floppy_image(tmp_path):
    """Create a test floppy image with some files."""
    img_path = tmp_path / "test.img"
    fmt = FLOPPY_FORMATS["1.44M"]
    builder = FATImageBuilder(fmt, "TESTVOL")
    builder.add_file("hello.txt", b"Hello World!")
    builder.add_file("data.bin", b"\x00\x01\x02\x03")
    builder.add_dir("mydir")
    builder.add_file("mydir/nested.txt", b"nested content")
    img_path.write_bytes(builder.finalize())
    return img_path


@pytest.fixture
def floppy_fs(floppy_image):
    """Create a FloppyFileSystem from the test image."""
    host_fs = LocalFileSystem()
    host_path = VfsPath(fs=host_fs, parts=("", str(floppy_image.parent), floppy_image.name))
    return open_fs(host_fs, host_path)


class TestFloppyPluginDiscovery:
    def test_plugin_discovered(self) -> None:
        """The floppy plugin should be discovered."""
        _discover()
        status = get_plugin_load_status()
        names = [s.name for s in status if s.success]
        assert "floppy_plugin" in names

    def test_extensions_registered(self) -> None:
        """The floppy plugin should register .img, .ima, .floppy extensions."""
        _discover()
        status = get_plugin_load_status()
        floppy_status = next(s for s in status if s.name == "floppy_plugin")
        assert ".img" in floppy_status.extensions
        assert ".ima" in floppy_status.extensions
        assert ".floppy" in floppy_status.extensions


class TestFloppyFileSystem:
    def test_list_root_dir(self, floppy_fs: FloppyFileSystem) -> None:
        """Root directory should list files and dirs."""
        root = VfsPath(fs=floppy_fs, parts=("",))
        entries = floppy_fs.list_dir(root)
        names = [e.name for e in entries if not e.is_parent]
        assert "HELLO.TXT" in names
        assert "DATA.BIN" in names
        assert "MYDIR" in names

    def test_list_root_has_parent_entry(self, floppy_fs: FloppyFileSystem) -> None:
        """Root directory should have a '..' entry."""
        root = VfsPath(fs=floppy_fs, parts=("",))
        entries = floppy_fs.list_dir(root)
        parent_entries = [e for e in entries if e.is_parent]
        assert len(parent_entries) == 1
        assert parent_entries[0].name == ".."

    def test_stat_file(self, floppy_fs: FloppyFileSystem) -> None:
        """Stat should return correct metadata for a file."""
        path = VfsPath(fs=floppy_fs, parts=("", "HELLO.TXT"))
        stat = floppy_fs.stat(path)
        assert not stat.is_dir
        assert stat.size == 12

    def test_stat_dir(self, floppy_fs: FloppyFileSystem) -> None:
        """Stat should return correct metadata for a directory."""
        path = VfsPath(fs=floppy_fs, parts=("", "MYDIR"))
        stat = floppy_fs.stat(path)
        assert stat.is_dir

    def test_stat_root(self, floppy_fs: FloppyFileSystem) -> None:
        """Stat on root should return directory metadata."""
        root = VfsPath(fs=floppy_fs, parts=("",))
        stat = floppy_fs.stat(root)
        assert stat.is_dir

    def test_open_read(self, floppy_fs: FloppyFileSystem) -> None:
        """open_read should return file contents."""
        path = VfsPath(fs=floppy_fs, parts=("", "HELLO.TXT"))
        with floppy_fs.open_read(path) as f:
            data = f.read()
        assert data == b"Hello World!"

    def test_open_read_binary(self, floppy_fs: FloppyFileSystem) -> None:
        """open_read should return binary file contents."""
        path = VfsPath(fs=floppy_fs, parts=("", "DATA.BIN"))
        with floppy_fs.open_read(path) as f:
            data = f.read()
        assert data == b"\x00\x01\x02\x03"

    def test_display_prefix(self, floppy_fs: FloppyFileSystem) -> None:
        """display_prefix should contain the image path."""
        assert "floppy:" in floppy_fs.display_prefix
        assert "!" in floppy_fs.display_prefix

    def test_realpath_returns_none(self, floppy_fs: FloppyFileSystem) -> None:
        """realpath should return None for archive-internal paths."""
        path = VfsPath(fs=floppy_fs, parts=("", "HELLO.TXT"))
        assert floppy_fs.realpath(path) is None

    def test_writable_flag(self, floppy_fs: FloppyFileSystem) -> None:
        """FloppyFileSystem should be writable."""
        assert floppy_fs.writable is True

    def test_stat_not_found(self, floppy_fs: FloppyFileSystem) -> None:
        """stat should raise OSError for non-existent paths."""
        path = VfsPath(fs=floppy_fs, parts=("", "NONEXISTENT.TXT"))
        with pytest.raises(OSError):
            floppy_fs.stat(path)

    def test_open_read_not_found(self, floppy_fs: FloppyFileSystem) -> None:
        """open_read should raise OSError for non-existent paths."""
        path = VfsPath(fs=floppy_fs, parts=("", "NONEXISTENT.TXT"))
        with pytest.raises(OSError):
            floppy_fs.open_read(path)


class TestFloppyWriteOperations:
    def test_open_write(self, floppy_fs: FloppyFileSystem, floppy_image) -> None:
        """open_write should add a new file to the image."""
        path = VfsPath(fs=floppy_fs, parts=("", "newfile.txt"))
        with floppy_fs.open_write(path) as f:
            f.write(b"new content")

        # Re-open to verify
        host_fs = LocalFileSystem()
        host_path = VfsPath(fs=host_fs, parts=("", str(floppy_image.parent), floppy_image.name))
        new_fs = open_fs(host_fs, host_path)
        root = VfsPath(fs=new_fs, parts=("",))
        entries = new_fs.list_dir(root)
        names = [e.name for e in entries if not e.is_parent]
        assert "NEWFILE.TXT" in names

    def test_mkdir(self, floppy_fs: FloppyFileSystem, floppy_image) -> None:
        """mkdir should create a new directory."""
        path = VfsPath(fs=floppy_fs, parts=("", "newdir"))
        floppy_fs.mkdir(path)

        # Re-open to verify
        host_fs = LocalFileSystem()
        host_path = VfsPath(fs=host_fs, parts=("", str(floppy_image.parent), floppy_image.name))
        new_fs = open_fs(host_fs, host_path)
        root = VfsPath(fs=new_fs, parts=("",))
        entries = new_fs.list_dir(root)
        names = [e.name for e in entries if not e.is_parent]
        assert "NEWDIR" in names

    def test_delete(self, floppy_fs: FloppyFileSystem, floppy_image) -> None:
        """delete should remove a file from the image."""
        path = VfsPath(fs=floppy_fs, parts=("", "HELLO.TXT"))
        floppy_fs.delete(path)

        # Re-open to verify
        host_fs = LocalFileSystem()
        host_path = VfsPath(fs=host_fs, parts=("", str(floppy_image.parent), floppy_image.name))
        new_fs = open_fs(host_fs, host_path)
        root = VfsPath(fs=new_fs, parts=("",))
        entries = new_fs.list_dir(root)
        names = [e.name for e in entries if not e.is_parent]
        assert "HELLO.TXT" not in names

    def test_rename(self, floppy_fs: FloppyFileSystem, floppy_image) -> None:
        """rename should rename a file in the image."""
        src = VfsPath(fs=floppy_fs, parts=("", "HELLO.TXT"))
        dst = VfsPath(fs=floppy_fs, parts=("", "RENAMED.TXT"))
        floppy_fs.rename(src, dst)

        # Re-open to verify
        host_fs = LocalFileSystem()
        host_path = VfsPath(fs=host_fs, parts=("", str(floppy_image.parent), floppy_image.name))
        new_fs = open_fs(host_fs, host_path)
        root = VfsPath(fs=new_fs, parts=("",))
        entries = new_fs.list_dir(root)
        names = [e.name for e in entries if not e.is_parent]
        assert "RENAMED.TXT" in names
        assert "HELLO.TXT" not in names


class TestOpenFsValidation:
    def test_open_fs_rejects_invalid_image(self, tmp_path) -> None:
        """open_fs should reject non-floppy images."""
        bad_path = tmp_path / "bad.img"
        bad_path.write_bytes(b"not a floppy image" * 100)
        host_fs = LocalFileSystem()
        host_path = VfsPath(fs=host_fs, parts=("", str(tmp_path), "bad.img"))
        with pytest.raises(OSError, match="Not a valid floppy"):
            open_fs(host_fs, host_path)

    def test_open_fs_rejects_wrong_size(self, tmp_path) -> None:
        """open_fs should reject images with wrong size."""
        wrong_path = tmp_path / "wrong.img"
        # Write a valid boot sector but wrong total size
        wrong_path.write_bytes(b"\x00" * 1000)
        host_fs = LocalFileSystem()
        host_path = VfsPath(fs=host_fs, parts=("", str(tmp_path), "wrong.img"))
        with pytest.raises(OSError, match="Not a valid floppy"):
            open_fs(host_fs, host_path)
