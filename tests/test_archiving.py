"""Tests for linux_commander.archiving: the container x codec archive model.

Every container (zip, tar, grp, 7z) can be wrapped by every outer codec
(none, gz, bz2, xz, zst), independent of the container's own internal
encoding -- these tests build one of each combination and read it back
through the real VFS mount chain (nested mounts where there's no direct
compound-extension plugin, e.g. zip.zst; a single hop where one already
exists, e.g. tar.gz / tar.zst).
"""

from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path

import pytest

import linux_commander.settings as settings_module
from linux_commander.archiving import CODECS, CONTAINERS, compose_extension, compress_sources
from linux_commander.vfs import FileEntry, LocalFileSystem, MountManager, VfsPath

_FS = LocalFileSystem()


def _local_vpath(path: Path) -> VfsPath:
    return _FS.from_path(path)


@pytest.fixture
def sample_tree(tmp_path: Path) -> Path:
    (tmp_path / "a.txt").write_text("hello world")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.txt").write_text("nested file")
    return tmp_path


def _mount_and_list(dest_path: Path) -> tuple[VfsPath, set[str]]:
    """Mount dest_path, following one extra nested hop for a bare codec wrapper."""
    mgr = MountManager()
    entry = FileEntry(
        name=dest_path.name,
        path=_local_vpath(dest_path),
        is_dir=False,
        size=dest_path.stat().st_size,
        mtime=0.0,
    )
    root = mgr.enter(entry)
    assert root is not None, f"no plugin matched {dest_path.name}"
    names = {e.name for e in root.fs.list_dir(root) if not e.is_parent}
    if names != {"a.txt", "sub"}:
        # A single-member outer-codec wrapper (compress_plugin) -- descend once more.
        assert len(names) == 1, (dest_path.name, names)
        inner_entry = next(e for e in root.fs.list_dir(root) if not e.is_parent)
        root = mgr.enter(inner_entry)
        assert root is not None, f"no nested plugin for {dest_path.name}"
        names = {e.name for e in root.fs.list_dir(root) if not e.is_parent}
    return root, names


_ALL_COMBOS = [(c, k) for c in CONTAINERS for k in CODECS]


@pytest.mark.parametrize("container,codec", _ALL_COMBOS)
def test_compress_and_round_trip(sample_tree: Path, container: str, codec: str) -> None:
    sources = [_local_vpath(sample_tree / "a.txt"), _local_vpath(sample_tree / "sub")]
    dest_path = sample_tree / f"out{compose_extension(container, codec)}"
    dest = _local_vpath(dest_path)

    errors = compress_sources(
        sources,
        dest,
        container,
        {"container": container, "codec": codec, "compresslevel": 6},
        _FS,
        lambda *a: None,
        lambda: False,
    )
    assert not errors

    root, names = _mount_and_list(dest_path)
    assert names == {"a.txt", "sub"}

    a_entry = next(e for e in root.fs.list_dir(root) if e.name == "a.txt")
    with root.fs.open_read(a_entry.path) as f:
        assert f.read() == b"hello world"

    sub_entry = next(e for e in root.fs.list_dir(root) if e.name == "sub")
    b_entry = next(e for e in root.fs.list_dir(sub_entry.path) if e.name == "b.txt")
    with root.fs.open_read(b_entry.path) as f:
        assert f.read() == b"nested file"


def test_compress_sources_remote_dir_reports_per_file_progress(tmp_path: Path) -> None:
    """A remote (non-local) source directory must report real per-file
    progress and an accurate total, not "1/1" for the whole tree -- same bug
    pattern as copy/move/delete, fixed via count_progress_units and per-file
    ticking in _iter_sources's remote branch."""
    from linux_commander.plugins.zip_plugin import ZipFileSystem

    src_zip_path = tmp_path / "src.zip"
    with zipfile.ZipFile(src_zip_path, "w") as zf:
        zf.writestr("sub/a.txt", b"aaa")
        zf.writestr("sub/b.txt", b"bbb")
    src_zip_fs = ZipFileSystem(src_zip_path, _local_vpath(src_zip_path))
    remote_source = VfsPath(fs=src_zip_fs, parts=("", "sub"))

    dest_path = tmp_path / "out.zip"
    dest = _local_vpath(dest_path)

    calls: list[tuple[int, int, str]] = []
    errors = compress_sources(
        [remote_source],
        dest,
        "zip",
        {"container": "zip", "codec": "none", "compresslevel": 6},
        _FS,
        lambda c, t, n: calls.append((c, t, n)),
        lambda: False,
    )

    assert not errors
    assert [(c, t) for c, t, _ in calls] == [(1, 2), (2, 2)]
    assert {n for _, _, n in calls} == {"Compressing a.txt", "Compressing b.txt"}
    src_zip_fs.close()


def test_legacy_fused_fmt_still_works(sample_tree: Path) -> None:
    """options without container/codec falls back to splitting the legacy fmt string."""
    sources = [_local_vpath(sample_tree / "a.txt")]
    dest_path = sample_tree / "legacy.tar.gz"
    dest = _local_vpath(dest_path)

    errors = compress_sources(
        sources, dest, "tar.gz", {"compresslevel": 6}, _FS, lambda *a: None, lambda: False
    )
    assert not errors
    assert dest_path.exists()

    with tarfile.open(dest_path, "r:gz") as tf:
        assert tf.getnames() == ["a.txt"]


def test_compose_extension() -> None:
    assert compose_extension("zip", "none") == ".zip"
    assert compose_extension("tar", "gz") == ".tar.gz"
    assert compose_extension("grp", "zst") == ".grp.zst"
    assert compose_extension("7z", "bz2") == ".7z.bz2"


def test_compose_extension_encrypted() -> None:
    assert compose_extension("tar", "xz", encrypted=True) == ".tar.xz.crp"
    assert compose_extension("zip", "none", encrypted=True) == ".zip.crp"
    assert compose_extension("zip", "none", encrypted=False) == ".zip"


# ---------------------------------------------------------------------------
# Encryption stage (options["password"] / options["key_name"])
# ---------------------------------------------------------------------------


def test_compress_with_password_encryption_round_trip(sample_tree: Path) -> None:
    pytest.importorskip("cryptography")
    from linux_commander.file_ops.crypt_op import _decrypt_file

    sources = [_local_vpath(sample_tree / "a.txt")]
    dest_path = sample_tree / "secret.tar.gz.crp"
    dest = _local_vpath(dest_path)

    errors = compress_sources(
        sources,
        dest,
        "tar.gz",
        {
            "container": "tar",
            "codec": "gz",
            "compresslevel": 6,
            "password": "hunter2",
            "key_name": None,
        },
        _FS,
        lambda *a: None,
        lambda: False,
    )
    assert not errors
    assert dest_path.exists()

    plain_path = sample_tree / "secret.tar.gz"
    _decrypt_file(dest, _local_vpath(plain_path), "hunter2", None)
    assert plain_path.exists()

    with tarfile.open(plain_path, "r:gz") as tf:
        assert tf.getnames() == ["a.txt"]


def test_compress_with_wrong_password_fails_to_decrypt(sample_tree: Path) -> None:
    pytest.importorskip("cryptography")
    from linux_commander.file_ops.crypt_op import _decrypt_file

    sources = [_local_vpath(sample_tree / "a.txt")]
    dest_path = sample_tree / "secret.zip.crp"
    dest = _local_vpath(dest_path)

    errors = compress_sources(
        sources,
        dest,
        "zip",
        {
            "container": "zip",
            "codec": "none",
            "compresslevel": 6,
            "password": "correct-horse",
            "key_name": None,
        },
        _FS,
        lambda *a: None,
        lambda: False,
    )
    assert not errors

    with pytest.raises(Exception):  # noqa: B017 - cryptography raises InvalidTag
        _decrypt_file(dest, _local_vpath(sample_tree / "wrong.zip"), "wrong-password", None)


def test_compress_with_stored_key_encryption_round_trip(
    sample_tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("cryptography")
    from linux_commander.file_ops.crypt_op import _decrypt_file
    from linux_commander.settings import Settings, StoredKey

    key = StoredKey.generate("test-key")
    fake_settings = Settings(stored_keys=[key])
    monkeypatch.setattr(settings_module, "load_settings", lambda: fake_settings)

    sources = [_local_vpath(sample_tree / "a.txt")]
    dest_path = sample_tree / "secret2.zip.crp"
    dest = _local_vpath(dest_path)

    errors = compress_sources(
        sources,
        dest,
        "zip",
        {
            "container": "zip",
            "codec": "none",
            "compresslevel": 6,
            "password": None,
            "key_name": "test-key",
        },
        _FS,
        lambda *a: None,
        lambda: False,
    )
    assert not errors
    assert dest_path.exists()

    plain_path = sample_tree / "secret2.zip"
    _decrypt_file(dest, _local_vpath(plain_path), None, key)
    assert plain_path.exists()
    with zipfile.ZipFile(plain_path) as zf:
        assert zf.namelist() == ["a.txt"]


def test_compress_encryption_with_unknown_key_name_returns_error(
    sample_tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("cryptography")
    from linux_commander.settings import Settings

    monkeypatch.setattr(settings_module, "load_settings", lambda: Settings())

    sources = [_local_vpath(sample_tree / "a.txt")]
    dest_path = sample_tree / "bad.zip.crp"
    dest = _local_vpath(dest_path)

    errors = compress_sources(
        sources,
        dest,
        "zip",
        {
            "container": "zip",
            "codec": "none",
            "compresslevel": 6,
            "password": None,
            "key_name": "does-not-exist",
        },
        _FS,
        lambda *a: None,
        lambda: False,
    )
    assert errors
    assert not dest_path.exists()
