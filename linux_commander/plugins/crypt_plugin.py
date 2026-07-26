"""VFS plugin for browsing password/key-encrypted archives (``.crp``).

Encrypted files are produced either by the Operations > Encrypt file
operation, or by the compression dialog's optional encryption stage
(``linux_commander.archiving._wrap_crypt``) -- both use the same
ChaCha20-Poly1305 blob format defined in ``linux_commander.file_ops.crypt_op``
(``MAGIC + salt + nonce + ciphertext``).

Pressing Enter on a ``.crp`` file prompts for the password/key via the
credential-provider hook (``plugins.set_credential_provider`` /
``get_credential_provider``, registered by the app at startup -- a VFS
plugin's ``open_fs(host_fs, path)`` has no UI parent to show a dialog from),
then decrypts the *whole* file once (the AEAD format is a single blob, not
seekable/streamable, so there is no way to decrypt only part of it). The
result is either:

- delegated to the inner container's own plugin, when stripping ``.crp``
  yields a name another plugin recognizes (e.g. ``backup.tar.gz.crp`` ->
  ``backup.tar.gz`` -> the tar plugin) -- the plaintext is spilled to a temp
  file and that plugin's ``open_fs`` is called on it, so the mounted
  filesystem is *that* plugin's, not this module's; or
- presented as a single-entry read-only archive (mirroring
  ``compress_plugin.CompressFileSystem``) when no inner plugin matches, i.e.
  a single encrypted (non-archive) file.

Registers no extension when the ``cryptography`` package (the ``crypto``
extra) isn't installed.
"""

from __future__ import annotations

import io
from typing import BinaryIO

from linux_commander.file_ops.crypt_op import (
    _HAS_CRYPTO,
    MAGIC,
    NONCE_LEN,
    SALT_LEN,
    _CipherClass,
    _decrypt_bytes,
    _derive_key,
)
from linux_commander.vfs import FileEntry, FileSystem, ReadableFileSystem, StatResult, VfsPath

EXTENSIONS: tuple[str, ...] = (".crp",) if _HAS_CRYPTO else ()


def _inner_name(name: str) -> str:
    """Return ``name`` with its ``.crp`` suffix stripped."""
    return name[: -len(".crp")] if name.lower().endswith(".crp") else name


def _decrypt_blob(blob: bytes, password: str | None, key_bytes: bytes | None) -> bytes:
    """Decrypt a ``CRP1`` blob, matching ``crypt_op``'s two AAD conventions.

    Key-mode encryption (``crypt_op._encrypt_bytes``) uses the salt as AAD;
    password-mode encryption (``crypt_op._encrypt_file``'s password branch)
    uses no AAD. Which convention applies isn't recorded in the blob itself,
    so the caller must supply the same credential kind (key vs. password)
    that was used to encrypt -- exactly what the credential prompt asks for.
    """
    if len(blob) < len(MAGIC) + SALT_LEN + NONCE_LEN:
        raise OSError("File too short or corrupted.")
    if blob[: len(MAGIC)] != MAGIC:
        raise OSError("Not a valid encrypted (.crp) file.")

    if key_bytes is not None:
        try:
            return _decrypt_bytes(key_bytes, blob)
        except Exception as exc:
            raise OSError(f"Decryption failed (wrong key?): {exc}") from exc

    if password is None:
        raise OSError("No password or key provided for decryption.")
    salt = blob[len(MAGIC) : len(MAGIC) + SALT_LEN]
    nonce = blob[len(MAGIC) + SALT_LEN : len(MAGIC) + SALT_LEN + NONCE_LEN]
    ciphertext = blob[len(MAGIC) + SALT_LEN + NONCE_LEN :]
    raw_key = _derive_key(password, salt)
    cipher = _CipherClass(raw_key)
    try:
        return cipher.decrypt(nonce, ciphertext, None)
    except Exception as exc:
        raise OSError(f"Decryption failed (wrong password?): {exc}") from exc


class CryptFileSystem(ReadableFileSystem):
    """Read-only VFS presenting a decrypted single (non-archive) file.

    Only used when the decrypted content's name doesn't match another
    archive plugin; ``parts`` encoding mirrors ``CompressFileSystem``:
    - Archive root:      ``("",)``
    - The single member: ``("", "<inner_name>")``
    """

    writable: bool = False

    def __init__(self, plaintext: bytes, host_vpath: VfsPath) -> None:
        self._plaintext = plaintext
        self._host_vpath = host_vpath
        self._inner = _inner_name(host_vpath.name)
        self.display_prefix = f"crp:{host_vpath.name}!"

    def _check_member(self, path: VfsPath) -> None:
        if len(path.parts) != 2 or path.parts[1] != self._inner:
            raise OSError(f"Not found in archive: {path.name!r}")

    def list_dir(self, path: VfsPath) -> list[FileEntry]:
        if path.parts != ("",):
            raise OSError(f"Not a directory: {path}")
        member_path = path / self._inner
        return [
            FileEntry(
                name="..",
                path=self._host_vpath.parent,
                is_dir=True,
                size=0,
                mtime=0.0,
                is_parent=True,
            ),
            FileEntry(
                name=self._inner,
                path=member_path,
                is_dir=False,
                size=len(self._plaintext),
                mtime=0.0,
            ),
        ]

    def stat(self, path: VfsPath) -> StatResult:
        if path.parts == ("",):
            return StatResult(is_dir=True, size=0, mtime=0.0)
        self._check_member(path)
        return StatResult(is_dir=False, size=len(self._plaintext), mtime=0.0)

    def open_read(self, path: VfsPath) -> BinaryIO:
        self._check_member(path)
        return io.BytesIO(self._plaintext)

    def close(self) -> None:
        pass  # plaintext lives only in memory; nothing to release


def open_fs(host_fs: FileSystem, host_path: VfsPath) -> ReadableFileSystem:
    """Prompt for credentials, decrypt ``host_path``, and mount the result."""
    from linux_commander.plugins import (
        cleanup_temp,
        get_credential_provider,
        materialize,
        plugin_for_name,
        spill_named_temp,
    )
    from linux_commander.vfs import LocalFileSystem

    provider = get_credential_provider()
    if provider is None:
        raise OSError("No credential prompt is registered for encrypted archives.")
    creds = provider(host_path.name)
    if creds is None:
        raise OSError("Decryption cancelled.")
    password, stored_key = creds
    key_bytes = stored_key.to_bytes() if stored_key is not None else None

    real_path = materialize(host_fs, host_path)
    try:
        blob = real_path.read_bytes()
    finally:
        # Nothing below needs the materialized file itself, only its bytes,
        # so (unlike archive plugins that keep a live handle open) we can
        # release it immediately rather than waiting for app quit.
        cleanup_temp(real_path)

    plaintext = _decrypt_blob(blob, password, key_bytes)

    inner_name = _inner_name(host_path.name)
    inner_plugin = plugin_for_name(inner_name)
    if inner_plugin is not None:
        local_fs = LocalFileSystem()
        # Preserve the *whole* inner name (not just its last suffix) -- a
        # plugin like compress_plugin determines its own inner member name
        # by stripping just its own suffix off the file's name, so e.g. a
        # "backup.grp.zst" temp file must really be named that, not a random
        # basename with ".zst" tacked on, or it computes a garbage member name.
        tmp_path = spill_named_temp(plaintext, inner_name)
        inner_vpath = local_fs.from_path(tmp_path)
        return inner_plugin.open_fs(local_fs, inner_vpath)

    return CryptFileSystem(plaintext, host_path)
