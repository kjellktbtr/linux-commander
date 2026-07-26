"""Tests for the crypt VFS plugin (browsing encrypted .crp archives).

Skipped entirely when ``cryptography`` (the ``crypto`` extra) isn't
installed.
"""

from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from linux_commander.file_ops.crypt_op import _encrypt_bytes, _encrypt_file  # noqa: E402
from linux_commander.plugins import get_credential_provider, set_credential_provider  # noqa: E402
from linux_commander.plugins.crypt_plugin import CryptFileSystem, open_fs  # noqa: E402
from linux_commander.settings import StoredKey  # noqa: E402
from linux_commander.vfs import LocalFileSystem, VfsPath  # noqa: E402

_FS = LocalFileSystem()


def _local_vpath(path: Path) -> VfsPath:
    return _FS.from_path(path)


@pytest.fixture(autouse=True)
def _restore_credential_provider() -> None:
    """Each test sets its own provider; make sure none leaks between tests."""
    yield
    set_credential_provider(lambda name: None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# CryptFileSystem — single (non-archive) encrypted file, direct construction
# ---------------------------------------------------------------------------


def test_crypt_fs_presents_single_member(tmp_path: Path) -> None:
    host_vpath = _local_vpath(tmp_path / "notes.txt.crp")
    fs = CryptFileSystem(b"hello plaintext", host_vpath)
    root = VfsPath(fs=fs, parts=("",))

    entries = {e.name: e for e in fs.list_dir(root)}
    assert ".." in entries and entries[".."].is_parent
    assert "notes.txt" in entries
    assert entries["notes.txt"].size == len(b"hello plaintext")

    with fs.open_read(entries["notes.txt"].path) as f:
        assert f.read() == b"hello plaintext"


def test_crypt_fs_stat_and_missing_member(tmp_path: Path) -> None:
    host_vpath = _local_vpath(tmp_path / "notes.txt.crp")
    fs = CryptFileSystem(b"content", host_vpath)
    root_stat = fs.stat(VfsPath(fs=fs, parts=("",)))
    assert root_stat.is_dir

    member_stat = fs.stat(VfsPath(fs=fs, parts=("", "notes.txt")))
    assert not member_stat.is_dir
    assert member_stat.size == len(b"content")

    with pytest.raises(OSError):
        fs.stat(VfsPath(fs=fs, parts=("", "missing.txt")))


def test_crypt_fs_is_read_only() -> None:
    host_vpath = _local_vpath(Path("/tmp/notes.txt.crp"))
    fs = CryptFileSystem(b"x", host_vpath)
    assert fs.writable is False


# ---------------------------------------------------------------------------
# open_fs — single encrypted (non-archive) file, password mode
# ---------------------------------------------------------------------------


def test_open_fs_single_file_password_mode(tmp_path: Path) -> None:
    src = tmp_path / "notes.txt"
    src.write_text("secret notes")
    crp_path = tmp_path / "notes.txt.crp"
    _encrypt_file(_local_vpath(src), _local_vpath(crp_path), "hunter2", None)

    set_credential_provider(lambda name: ("hunter2", None))
    host_vpath = _local_vpath(crp_path)
    fs = open_fs(host_vpath.fs, host_vpath)

    assert isinstance(fs, CryptFileSystem)
    root = VfsPath(fs=fs, parts=("",))
    entries = {e.name for e in fs.list_dir(root)}
    assert "notes.txt" in entries

    inner_entry = next(e for e in fs.list_dir(root) if e.name == "notes.txt")
    with fs.open_read(inner_entry.path) as f:
        assert f.read() == b"secret notes"


def test_open_fs_wrong_password_raises(tmp_path: Path) -> None:
    src = tmp_path / "notes.txt"
    src.write_text("secret notes")
    crp_path = tmp_path / "notes.txt.crp"
    _encrypt_file(_local_vpath(src), _local_vpath(crp_path), "hunter2", None)

    set_credential_provider(lambda name: ("wrong-password", None))
    host_vpath = _local_vpath(crp_path)
    with pytest.raises(OSError):
        open_fs(host_vpath.fs, host_vpath)


def test_open_fs_cancelled_credential_prompt_raises(tmp_path: Path) -> None:
    src = tmp_path / "notes.txt"
    src.write_text("secret notes")
    crp_path = tmp_path / "notes.txt.crp"
    _encrypt_file(_local_vpath(src), _local_vpath(crp_path), "hunter2", None)

    set_credential_provider(lambda name: None)
    host_vpath = _local_vpath(crp_path)
    with pytest.raises(OSError):
        open_fs(host_vpath.fs, host_vpath)


def test_open_fs_no_provider_registered_raises(tmp_path: Path) -> None:
    src = tmp_path / "notes.txt"
    src.write_text("secret notes")
    crp_path = tmp_path / "notes.txt.crp"
    _encrypt_file(_local_vpath(src), _local_vpath(crp_path), "hunter2", None)

    import linux_commander.plugins as plugins_module

    plugins_module._credential_provider = None
    host_vpath = _local_vpath(crp_path)
    with pytest.raises(OSError):
        open_fs(host_vpath.fs, host_vpath)
    assert get_credential_provider() is None


# ---------------------------------------------------------------------------
# open_fs — stored-key mode
# ---------------------------------------------------------------------------


def test_open_fs_stored_key_mode(tmp_path: Path) -> None:
    key = StoredKey.generate("test-key")
    src = tmp_path / "notes.txt"
    src.write_text("key encrypted")
    crp_path = tmp_path / "notes.txt.crp"
    _encrypt_file(_local_vpath(src), _local_vpath(crp_path), None, key)

    set_credential_provider(lambda name: (None, key))
    host_vpath = _local_vpath(crp_path)
    fs = open_fs(host_vpath.fs, host_vpath)

    root = VfsPath(fs=fs, parts=("",))
    inner_entry = next(e for e in fs.list_dir(root) if e.name == "notes.txt")
    with fs.open_read(inner_entry.path) as f:
        assert f.read() == b"key encrypted"


def test_open_fs_wrong_key_raises(tmp_path: Path) -> None:
    key = StoredKey.generate("test-key")
    wrong_key = StoredKey.generate("wrong-key")
    src = tmp_path / "notes.txt"
    src.write_text("key encrypted")
    crp_path = tmp_path / "notes.txt.crp"
    _encrypt_file(_local_vpath(src), _local_vpath(crp_path), None, key)

    set_credential_provider(lambda name: (None, wrong_key))
    host_vpath = _local_vpath(crp_path)
    with pytest.raises(OSError):
        open_fs(host_vpath.fs, host_vpath)


# ---------------------------------------------------------------------------
# open_fs — delegates to the inner container plugin when the stripped name
# matches one (e.g. "backup.tar.gz.crp" -> tar plugin)
# ---------------------------------------------------------------------------


def test_open_fs_delegates_to_inner_tar_plugin(tmp_path: Path) -> None:
    inner_tar = tmp_path / "backup.tar.gz"
    with tarfile.open(inner_tar, "w:gz") as tf:
        member_path = tmp_path / "member.txt"
        member_path.write_text("inside the tar")
        tf.add(member_path, arcname="member.txt")

    crp_path = tmp_path / "backup.tar.gz.crp"
    blob = _encrypt_bytes(b"\x00" * 32, inner_tar.read_bytes())
    # Use the raw byte helper with an explicit all-zero key so this test
    # doesn't depend on password vs. key framing -- exercised separately.
    crp_path.write_bytes(blob)

    zero_key = StoredKey(name="zero", hex_value="00" * 32)
    set_credential_provider(lambda name: (None, zero_key))
    host_vpath = _local_vpath(crp_path)
    fs = open_fs(host_vpath.fs, host_vpath)

    # Should have delegated to TarFileSystem, not CryptFileSystem.
    assert not isinstance(fs, CryptFileSystem)
    root = VfsPath(fs=fs, parts=("",))
    names = {e.name for e in fs.list_dir(root) if not e.is_parent}
    assert names == {"member.txt"}

    member_entry = next(e for e in fs.list_dir(root) if e.name == "member.txt")
    with fs.open_read(member_entry.path) as f:
        assert f.read() == b"inside the tar"


def test_open_fs_no_inner_plugin_match_falls_back_to_single_file(tmp_path: Path) -> None:
    src = tmp_path / "readme.md"
    src.write_text("just a markdown file")
    crp_path = tmp_path / "readme.md.crp"
    _encrypt_file(_local_vpath(src), _local_vpath(crp_path), "pw", None)

    set_credential_provider(lambda name: ("pw", None))
    host_vpath = _local_vpath(crp_path)
    fs = open_fs(host_vpath.fs, host_vpath)

    assert isinstance(fs, CryptFileSystem)
    root = VfsPath(fs=fs, parts=("",))
    names = {e.name for e in fs.list_dir(root) if not e.is_parent}
    assert names == {"readme.md"}


# ---------------------------------------------------------------------------
# EXTENSIONS registration
# ---------------------------------------------------------------------------


def test_crp_extension_is_registered() -> None:
    from linux_commander.plugins import plugin_for_name

    assert plugin_for_name("archive.zip.crp") is not None


# ---------------------------------------------------------------------------
# Regression: "<name>.grp.zst.crp" must decrypt to a correctly (fully) named
# temp file, not a random basename that only keeps the last suffix -- else
# the second-level (.zst -> .grp) delegation can't find the right plugin.
# ---------------------------------------------------------------------------


def test_open_fs_double_nested_grp_zst_crp_preserves_full_inner_name(tmp_path: Path) -> None:
    pytest.importorskip("zstandard")
    from linux_commander import archiving
    from linux_commander.vfs import MountManager

    src_dir = tmp_path / "Downloads"
    src_dir.mkdir()
    src_file = src_dir / "readme.txt"
    src_file.write_text("inside the grp")

    grp_zst_path = tmp_path / "Downloads.grp.zst"
    errors = archiving.compress_sources(
        [_local_vpath(src_file)],
        _local_vpath(grp_zst_path),
        "grp",
        {
            "container": "grp",
            "codec": "zst",
            "compresslevel": 6,
            "password": None,
            "key_name": None,
        },
        _FS,
        lambda *a: None,
        lambda: False,
    )
    assert not errors
    assert grp_zst_path.exists()

    crp_path = tmp_path / "Downloads.grp.zst.crp"
    _encrypt_file(_local_vpath(grp_zst_path), _local_vpath(crp_path), "hunter2", None)

    set_credential_provider(lambda name: ("hunter2", None))
    host_vpath = _local_vpath(crp_path)
    zst_fs = open_fs(host_vpath.fs, host_vpath)

    # First hop (crypt_plugin -> compress_plugin): the .zst layer's
    # single-entry listing must be named "Downloads.grp", not a random
    # tempfile basename with the ".grp" part lost.
    root = VfsPath(fs=zst_fs, parts=("",))
    entries = {e.name: e for e in zst_fs.list_dir(root) if not e.is_parent}
    assert "Downloads.grp" in entries, f"expected 'Downloads.grp' entry, got {list(entries)}"

    # Second hop: pressing Enter on that entry must find the grp plugin.
    grp_entry = entries["Downloads.grp"]
    mgr = MountManager()
    grp_root = mgr.enter(grp_entry)
    assert grp_root is not None, "no plugin matched 'Downloads.grp' -- name was corrupted"

    inner_names = {e.name for e in grp_root.fs.list_dir(grp_root) if not e.is_parent}
    assert "readme.txt" in inner_names
