"""Tests for the Jottacloud VFS plugin (linux_commander.plugins.jotta_plugin).

Mocks the SyncJottaAPI client (mirrors test_sftp_plugin.py's MagicMock style)
so these run fast and without a network connection. Skipped entirely when the
``jotta`` extra (httpx/pydantic) isn't installed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

pytest.importorskip("httpx")
pytest.importorskip("pydantic")

from linux_commander.jotta_api import JottaAuthError, JottaFile, JottaFolder  # noqa: E402
from linux_commander.plugins.jotta_plugin import JottaFileSystem  # noqa: E402
from linux_commander.vfs import VfsPath  # noqa: E402

_NOW = datetime.now(UTC)


def _file(name: str) -> JottaFile:
    return JottaFile(
        name=name,
        path=name,
        uuid="uuid",
        size=1,
        md5="md5",
        mime="application/octet-stream",
        state="COMPLETED",
        created=_NOW,
        modified=_NOW,
        updated=_NOW,
    )


def _folder(name: str) -> JottaFolder:
    return JottaFolder(name=name, path=name, modified=_NOW)


def _make_fs(api: MagicMock | None = None) -> JottaFileSystem:
    return JottaFileSystem(
        api=api or MagicMock(),
        username="user",
        device="Jotta",
        mountpoint="Archive",
    )


def test_jotta_filesystem_is_writable() -> None:
    assert _make_fs().writable is True


# ---------------------------------------------------------------------------
# open_write / upload
# ---------------------------------------------------------------------------


def test_jotta_open_write_buffers_then_uploads_on_close() -> None:
    api = MagicMock()
    fs = _make_fs(api)
    vp = VfsPath(fs=fs, parts=("", "file.txt"))

    handle = fs.open_write(vp)
    handle.write(b"hello ")
    handle.write(b"world")
    api.upload_file.assert_not_called()  # nothing sent until close()
    handle.close()

    api.upload_file.assert_called_once_with(
        "file.txt", b"hello world", device="Jotta", mountpoint="Archive"
    )


def test_jotta_open_write_close_is_idempotent() -> None:
    api = MagicMock()
    fs = _make_fs(api)
    vp = VfsPath(fs=fs, parts=("", "file.txt"))
    handle = fs.open_write(vp)
    handle.write(b"x")
    handle.close()
    handle.close()  # must not upload a second time
    api.upload_file.assert_called_once()


def test_jotta_open_write_wraps_auth_error() -> None:
    api = MagicMock()
    api.upload_file.side_effect = JottaAuthError("expired")
    fs = _make_fs(api)
    vp = VfsPath(fs=fs, parts=("", "file.txt"))
    handle = fs.open_write(vp)
    handle.write(b"x")
    with pytest.raises(OSError):
        handle.close()


# ---------------------------------------------------------------------------
# mkdir
# ---------------------------------------------------------------------------


def test_jotta_mkdir() -> None:
    api = MagicMock()
    fs = _make_fs(api)
    vp = VfsPath(fs=fs, parts=("", "newdir"))
    fs.mkdir(vp)
    api.create_folder.assert_called_once_with("newdir", device="Jotta", mountpoint="Archive")


def test_jotta_mkdir_wraps_api_error() -> None:
    from linux_commander.jotta_api import JottaAPIError

    api = MagicMock()
    api.create_folder.side_effect = JottaAPIError("bad request", 400)
    fs = _make_fs(api)
    vp = VfsPath(fs=fs, parts=("", "newdir"))
    with pytest.raises(OSError):
        fs.mkdir(vp)


# ---------------------------------------------------------------------------
# delete -- must resolve is_dir via stat() and pick dl vs dlDir accordingly
# ---------------------------------------------------------------------------


def test_jotta_delete_file_passes_is_dir_false() -> None:
    api = MagicMock()
    api.list_files.return_value = ([], [_file("file.txt")])
    fs = _make_fs(api)
    vp = VfsPath(fs=fs, parts=("", "file.txt"))
    fs.delete(vp)
    api.delete_path.assert_called_once_with(
        "file.txt", is_dir=False, device="Jotta", mountpoint="Archive"
    )


def test_jotta_delete_folder_passes_is_dir_true() -> None:
    api = MagicMock()
    api.list_files.return_value = ([_folder("subdir")], [])
    fs = _make_fs(api)
    vp = VfsPath(fs=fs, parts=("", "subdir"))
    fs.delete(vp)
    api.delete_path.assert_called_once_with(
        "subdir", is_dir=True, device="Jotta", mountpoint="Archive"
    )


# ---------------------------------------------------------------------------
# rename -- same is_dir resolution as delete
# ---------------------------------------------------------------------------


def test_jotta_rename_file() -> None:
    api = MagicMock()
    api.list_files.return_value = ([], [_file("old.txt")])
    fs = _make_fs(api)
    src = VfsPath(fs=fs, parts=("", "old.txt"))
    dst = VfsPath(fs=fs, parts=("", "new.txt"))
    fs.rename(src, dst)
    api.move_path.assert_called_once_with(
        "old.txt", "new.txt", is_dir=False, device="Jotta", mountpoint="Archive"
    )


def test_jotta_rename_folder() -> None:
    api = MagicMock()
    api.list_files.return_value = ([_folder("olddir")], [])
    fs = _make_fs(api)
    src = VfsPath(fs=fs, parts=("", "olddir"))
    dst = VfsPath(fs=fs, parts=("", "newdir"))
    fs.rename(src, dst)
    api.move_path.assert_called_once_with(
        "olddir", "newdir", is_dir=True, device="Jotta", mountpoint="Archive"
    )


# ---------------------------------------------------------------------------
# stat() -- must list the PARENT and search for a matching child, not GET
# the item's own path and search among *its* children (a folder's own JFS
# listing is its contents, never itself, so that always failed).
# ---------------------------------------------------------------------------


def test_jotta_stat_lists_parent_not_self_for_nested_folder() -> None:
    """Regression test for "deleting a sub folder on Jottacloud says it
    doesn't exist": stat() used to GET the item's own jfs path and search
    its *children* for a name match, which for a folder is never itself.
    delete()/rename() call stat() internally, so every folder delete/rename
    raised OSError("Not found") before ever reaching the actual API call."""
    api = MagicMock()
    api.list_files.return_value = ([_folder("SubFolder")], [])
    fs = _make_fs(api)
    vp = VfsPath(fs=fs, parts=("", "TopFolder", "SubFolder"))

    result = fs.stat(vp)

    assert result.is_dir
    # Must have listed the PARENT ("TopFolder"), not the item's own path
    # ("TopFolder/SubFolder").
    api.list_files.assert_called_once_with(path="TopFolder", device="Jotta", mountpoint="Archive")


def test_jotta_delete_nested_folder_succeeds() -> None:
    """End-to-end version of the same regression: before the stat() fix this
    raised OSError("Not found: ...") before ever calling delete_path()."""
    api = MagicMock()
    api.list_files.return_value = ([_folder("SubFolder")], [])
    fs = _make_fs(api)
    vp = VfsPath(fs=fs, parts=("", "TopFolder", "SubFolder"))

    fs.delete(vp)  # must not raise

    api.delete_path.assert_called_once_with(
        "TopFolder/SubFolder", is_dir=True, device="Jotta", mountpoint="Archive"
    )


# ---------------------------------------------------------------------------
# Deleted-item filtering -- JFS keeps trashed items in listings (marked via
# a `deleted` attribute) instead of dropping them.
# ---------------------------------------------------------------------------


def _deleted_file(name: str) -> JottaFile:
    return JottaFile(
        name=name,
        path=name,
        uuid="uuid",
        size=1,
        md5="md5",
        mime="application/octet-stream",
        state="COMPLETED",
        created=_NOW,
        modified=_NOW,
        updated=_NOW,
        deleted=True,
    )


def test_jotta_list_dir_filters_out_deleted_entries() -> None:
    """Regression test for "delete doesn't say it fails, but the file
    doesn't disappear": a trashed file/folder keeps appearing in JFS
    listings (with `deleted="true"`) rather than vanishing -- list_dir()
    must filter these out itself."""
    api = MagicMock()
    live_folder = _folder("keepdir")
    dead_folder = JottaFolder(name="gonedir", path="gonedir", modified=_NOW, deleted=True)
    api.list_files.return_value = (
        [live_folder, dead_folder],
        [_file("keep.txt"), _deleted_file("gone.txt")],
    )
    fs = _make_fs(api)
    root = VfsPath(fs=fs, parts=("",))

    entries = fs.list_dir(root)

    names = {e.name for e in entries if not e.is_parent}
    assert names == {"keep.txt", "keepdir"}


def test_jotta_stat_treats_deleted_entry_as_not_found() -> None:
    api = MagicMock()
    api.list_files.return_value = ([], [_deleted_file("gone.txt")])
    fs = _make_fs(api)
    vp = VfsPath(fs=fs, parts=("", "gone.txt"))

    with pytest.raises(OSError):
        fs.stat(vp)
