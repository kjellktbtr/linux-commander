"""File operations: Encrypt and decrypt files using ChaCha20-Poly1305.

Guards the ``cryptography`` import — if the library is not installed the
plugin silently registers no operations, keeping the menu clean.

Encryption writes ``<name>.crp`` (raw binary: magic + salt + nonce + ciphertext).
Decryption strips the ``.crp`` extension and writes the original file.
"""

from __future__ import annotations

import hashlib
import os
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk
from typing import Any, cast

from linux_commander.dialogs import _center_over, confirm, error, prompt
from linux_commander.file_ops import FileOperation
from linux_commander.operations import CancelPredicate, OperationError, ProgressCallback
from linux_commander.settings import StoredKey
from linux_commander.vfs import VfsPath, WritableFileSystem

# ── crypto guard ──────────────────────────────────────────────────────────

_CipherClass: Any = None
_HAS_CRYPTO = False
try:
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305  # type: ignore[import-not-found]  # noqa: I001

    _CipherClass = ChaCha20Poly1305
    _HAS_CRYPTO = True
except ImportError:
    pass
    _HAS_CRYPTO = False


# ── constants ─────────────────────────────────────────────────────────────

MAGIC = b"CRP1"
SALT_LEN = 16
NONCE_LEN = 12
KEY_LEN = 32
PBKDF2_ITERATIONS = 200_000


# ── core helpers ──────────────────────────────────────────────────────────


def _derive_key(password: str, salt: bytes) -> bytes:
    """Derive a 32-byte key from *password* and *salt* via PBKDF2-HMAC-SHA256."""
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS, dklen=KEY_LEN
    )


def _encrypt_bytes(key: bytes, plaintext: bytes) -> bytes:
    """Return raw ``MAGIC + salt + nonce + ciphertext``."""
    salt = os.urandom(SALT_LEN)
    nonce = os.urandom(NONCE_LEN)
    cipher = _CipherClass(key)
    ciphertext = cipher.encrypt(nonce, plaintext, salt)
    return MAGIC + salt + nonce + ciphertext


def _decrypt_bytes(key: bytes, blob: bytes) -> bytes:
    """Decrypt a ``CRP1`` blob; raises on wrong key or corruption."""
    if len(blob) < len(MAGIC) + SALT_LEN + NONCE_LEN:
        raise ValueError("File too short or corrupted.")
    if blob[: len(MAGIC)] != MAGIC:
        raise ValueError("Not a valid encrypted file.")
    salt = blob[len(MAGIC) : len(MAGIC) + SALT_LEN]
    nonce = blob[len(MAGIC) + SALT_LEN : len(MAGIC) + SALT_LEN + NONCE_LEN]
    ciphertext = blob[len(MAGIC) + SALT_LEN + NONCE_LEN :]
    cipher = _CipherClass(key)
    return cipher.decrypt(nonce, ciphertext, salt)


# ── per-file read / write helpers ─────────────────────────────────────────


def _encrypt_file(
    src: VfsPath,
    dest: VfsPath,
    password: str | None,
    key: StoredKey | None,
    file_size: int = 0,
    on_sub_progress: Callable[[int, int], None] | None = None,
) -> None:
    """Read *src*, encrypt with *password* or *key*, write to *dest*."""
    # Read plaintext in chunks with progress
    chunks: list[bytes] = []
    bytes_read = 0
    with src.fs.open_read(src) as inp:
        while chunk := inp.read(65536):
            chunks.append(chunk)
            bytes_read += len(chunk)
            if on_sub_progress:
                on_sub_progress(bytes_read, file_size)
    plaintext = b"".join(chunks)

    if key is not None:
        raw_key = key.to_bytes()
        blob = _encrypt_bytes(raw_key, plaintext)
        with cast(WritableFileSystem, dest.fs).open_write(dest) as out:
            out.write(blob)
        return

    assert password is not None
    salt = os.urandom(SALT_LEN)
    raw_key = _derive_key(password, salt)
    nonce = os.urandom(NONCE_LEN)
    cipher = _CipherClass(raw_key)
    ciphertext = cipher.encrypt(nonce, plaintext, None)
    blob = MAGIC + salt + nonce + ciphertext
    with cast(WritableFileSystem, dest.fs).open_write(dest) as out:
        out.write(blob)


def _decrypt_file(
    src: VfsPath,
    dest: VfsPath,
    password: str | None,
    key: StoredKey | None,
    file_size: int = 0,
    on_sub_progress: Callable[[int, int], None] | None = None,
) -> None:
    """Read *src*, decrypt with *password* or *key*, write to *dest*."""
    # Read encrypted blob in chunks with progress
    chunks: list[bytes] = []
    bytes_read = 0
    with src.fs.open_read(src) as inp:
        while chunk := inp.read(65536):
            chunks.append(chunk)
            bytes_read += len(chunk)
            if on_sub_progress:
                on_sub_progress(bytes_read, file_size)
    blob = b"".join(chunks)

    if key is not None:
        raw_key = key.to_bytes()
        plaintext = _decrypt_bytes(raw_key, blob)
        with cast(WritableFileSystem, dest.fs).open_write(dest) as out:
            out.write(plaintext)
        return

    assert password is not None
    if len(blob) < len(MAGIC) + SALT_LEN + NONCE_LEN:
        raise ValueError("File too short or corrupted.")
    if blob[: len(MAGIC)] != MAGIC:
        raise ValueError("Not a valid encrypted file.")
    salt = blob[len(MAGIC) : len(MAGIC) + SALT_LEN]
    nonce = blob[len(MAGIC) + SALT_LEN : len(MAGIC) + SALT_LEN + NONCE_LEN]
    ciphertext = blob[len(MAGIC) + SALT_LEN + NONCE_LEN :]
    raw_key = _derive_key(password, salt)
    cipher = _CipherClass(raw_key)
    plaintext = cipher.decrypt(nonce, ciphertext, None)
    with cast(WritableFileSystem, dest.fs).open_write(dest) as out:
        out.write(plaintext)


# ── run functions ─────────────────────────────────────────────────────────


def _encrypt_run(
    sources: list[VfsPath],
    dest_dir: VfsPath,
    on_progress: ProgressCallback,
    should_cancel: CancelPredicate,
    password: str | None = None,
    key_name: str | None = None,
) -> list[OperationError]:
    """Encrypt each source file to ``<name>.crp``."""
    from linux_commander.settings import load_settings

    settings = load_settings()
    stored_key: StoredKey | None = None
    if key_name is not None:
        for sk in settings.stored_keys:
            if sk.name == key_name:
                stored_key = sk
                break
        if stored_key is None:
            return [OperationError(path=sources[0], message=f"Stored key {key_name!r} not found.")]

    errors: list[OperationError] = []
    total = len(sources)
    for current, src in enumerate(sources, start=1):
        if should_cancel():
            break
        # Stat source for sub-progress
        st = src.fs.stat(src)
        sz = st.size
        on_progress(current, total, src.name, 0, sz)
        dest = dest_dir / (src.name + ".crp")
        try:
            _encrypt_file(
                src,
                dest,
                password,
                stored_key,
                file_size=sz,
                on_sub_progress=lambda d, t, c=current, s=src: on_progress(c, total, s.name, d, t),  # type: ignore[misc]
            )
        except Exception as exc:
            errors.append(OperationError(path=src, message=str(exc)))
    return errors


def _decrypt_run(
    sources: list[VfsPath],
    dest_dir: VfsPath,
    on_progress: ProgressCallback,
    should_cancel: CancelPredicate,
    password: str | None = None,
    key_name: str | None = None,
) -> list[OperationError]:
    """Decrypt each ``.crp`` source file, stripping the extension."""
    from linux_commander.settings import load_settings

    settings = load_settings()
    stored_key: StoredKey | None = None
    if key_name is not None:
        for sk in settings.stored_keys:
            if sk.name == key_name:
                stored_key = sk
                break
        if stored_key is None:
            return [OperationError(path=sources[0], message=f"Stored key {key_name!r} not found.")]

    errors: list[OperationError] = []
    total = len(sources)
    for current, src in enumerate(sources, start=1):
        if should_cancel():
            break
        # Stat source for sub-progress
        st = src.fs.stat(src)
        sz = st.size
        on_progress(current, total, src.name, 0, sz)
        if not src.name.lower().endswith(".crp"):
            errors.append(
                OperationError(
                    path=src,
                    message=f"'{src.name}' does not have a .crp extension; skipped.",
                )
            )
            continue
        out_name = src.name[: -len(".crp")] if len(src.name) > 4 else src.name + ".decrypted"
        dest = dest_dir / out_name
        try:
            _decrypt_file(
                src,
                dest,
                password,
                stored_key,
                file_size=sz,
                on_sub_progress=lambda d, t, c=current, s=src: on_progress(c, total, s.name, d, t),  # type: ignore[misc]
            )
        except Exception as exc:
            errors.append(OperationError(path=src, message=str(exc)))
    return errors


# ── key management dialog ─────────────────────────────────────────────────


def _manage_keys_dialog(parent: tk.Misc) -> None:
    """Modal dialog to view, add, and remove stored encryption keys."""
    from linux_commander.settings import load_settings, save_settings

    dialog = tk.Toplevel(parent)
    dialog.title("Manage Encryption Keys")
    dialog.transient(parent)  # type: ignore[call-overload]
    dialog.resizable(False, False)

    frame = ttk.Frame(dialog)
    frame.pack(padx=12, pady=(12, 4), fill="both", expand=True)

    listbox = tk.Listbox(frame, width=50, height=8, exportselection=False)
    scrollbar = ttk.Scrollbar(frame, orient="vertical", command=listbox.yview)
    listbox.configure(yscrollcommand=scrollbar.set)
    listbox.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    def _refresh() -> None:
        listbox.delete(0, "end")
        s = load_settings()
        for sk in s.stored_keys:
            listbox.insert("end", f"{sk.name}  ({sk.hex_value[:16]}...)")
        listbox._key_names = [sk.name for sk in s.stored_keys]  # type: ignore[attr-defined]

    _refresh()

    def _add_key() -> None:
        name = prompt(dialog, "Add Key", "Enter a name for the new key:")
        if not name:
            return
        s = load_settings()
        if any(sk.name == name for sk in s.stored_keys):
            error(dialog, f"A key named {name!r} already exists.", title="Duplicate Key")
            return
        s.stored_keys.append(StoredKey.generate(name))
        save_settings(s)
        _refresh()

    def _remove_key() -> None:
        sel = listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        key_name = listbox._key_names[idx]  # type: ignore[attr-defined]
        if not confirm(dialog, f"Remove key {key_name!r}?", title="Remove Key"):
            return
        s = load_settings()
        s.stored_keys = [sk for sk in s.stored_keys if sk.name != key_name]
        save_settings(s)
        _refresh()

    btn_frame = ttk.Frame(dialog)
    btn_frame.pack(fill="x", padx=12, pady=(4, 12))
    ttk.Button(btn_frame, text="Add Key", command=_add_key).pack(side="left", padx=4)
    ttk.Button(btn_frame, text="Remove Selected", command=_remove_key).pack(side="left", padx=4)
    ttk.Button(btn_frame, text="Close", command=dialog.destroy).pack(side="right", padx=4)

    dialog.bind("<Escape>", lambda e: dialog.destroy())
    _center_over(dialog, parent)
    dialog.grab_set()
    dialog.wait_window()


# ── prepare dialog ────────────────────────────────────────────────────────


def _prepare_encrypt(parent: tk.Misc, sources: list[VfsPath]) -> dict | None:
    """Show a dialog to choose password or stored key for encryption."""
    return _credential_dialog(parent, "Encrypt")


def _prepare_decrypt(parent: tk.Misc, sources: list[VfsPath]) -> dict | None:
    """Show a dialog to choose password or stored key for decryption."""
    return _credential_dialog(parent, "Decrypt")


def _credential_dialog(parent: tk.Misc, title: str) -> dict | None:
    """Modal dialog: choose password or stored key, return params dict or None."""
    from linux_commander.settings import load_settings

    settings = load_settings()
    result: dict | None = None

    dialog = tk.Toplevel(parent)
    dialog.title(title)
    dialog.transient(parent)  # type: ignore[call-overload]
    dialog.resizable(False, False)

    mode_var = tk.StringVar(value="password")
    password_var = tk.StringVar()
    key_var = tk.StringVar()

    # ── mode radio buttons ────────────────────────────────────────────
    radio_frame = ttk.Frame(dialog)
    radio_frame.pack(padx=12, pady=(12, 0), anchor="w")
    ttk.Radiobutton(radio_frame, text="Password", variable=mode_var, value="password").pack(
        anchor="w"
    )
    ttk.Radiobutton(radio_frame, text="Stored Key", variable=mode_var, value="key").pack(anchor="w")

    # ── password entry ────────────────────────────────────────────────
    pw_frame = ttk.Frame(dialog)
    pw_frame.pack(padx=12, pady=(4, 0), fill="x")
    ttk.Label(pw_frame, text="Password:").pack(side="left")
    pw_entry = ttk.Entry(pw_frame, textvariable=password_var, show="*", width=36)
    pw_entry.pack(side="left", padx=(8, 0))

    # ── key dropdown ──────────────────────────────────────────────────
    key_frame = ttk.Frame(dialog)
    key_frame.pack(padx=12, pady=(4, 0), fill="x")

    key_names = [sk.name for sk in settings.stored_keys]
    key_combo = ttk.Combobox(
        key_frame, textvariable=key_var, values=key_names, state="readonly", width=34
    )
    key_combo.pack(side="left")
    if key_names:
        key_combo.set(key_names[0])

    def _open_manage_keys() -> None:
        _manage_keys_dialog(dialog)
        # The manage-keys editor may have added/removed keys; reload and
        # repopulate the combobox instead of leaving it stuck with the
        # snapshot captured when this dialog was first built.
        fresh = load_settings()
        names = [sk.name for sk in fresh.stored_keys]
        current = key_var.get()
        key_combo["values"] = names
        if current in names:
            key_combo.set(current)
        elif names:
            key_combo.set(names[0])
        else:
            key_combo.set("")

    manage_btn = ttk.Button(key_frame, text="Manage Keys...", command=_open_manage_keys)
    manage_btn.pack(side="left", padx=(8, 0))

    # ── toggle visibility based on mode ───────────────────────────────
    def _toggle_mode() -> None:
        is_pw = mode_var.get() == "password"
        pw_frame.pack() if is_pw else pw_frame.pack_forget()
        key_frame.pack() if not is_pw else key_frame.pack_forget()

    mode_var.trace_add("write", lambda *_: _toggle_mode())
    _toggle_mode()

    # ── buttons ───────────────────────────────────────────────────────
    def on_ok() -> None:
        nonlocal result
        if mode_var.get() == "password":
            pw = password_var.get()
            if not pw:
                error(dialog, "Password cannot be empty.", title=title)
                return
            result = {"password": pw, "key_name": None}
        else:
            kn = key_var.get()
            if not kn:
                error(dialog, "No key selected.", title=title)
                return
            result = {"password": None, "key_name": kn}
        dialog.destroy()

    def on_cancel() -> None:
        dialog.destroy()

    btn_frame = ttk.Frame(dialog)
    btn_frame.pack(fill="x", padx=12, pady=(8, 12))
    ttk.Button(btn_frame, text="Cancel", command=on_cancel).pack(side="right", padx=4)
    ttk.Button(btn_frame, text="OK", command=on_ok).pack(side="right", padx=4)

    dialog.bind("<Return>", lambda e: on_ok())
    dialog.bind("<Escape>", lambda e: on_cancel())
    _center_over(dialog, parent)
    dialog.grab_set()
    dialog.wait_window()

    return result


# ── registration ─────────────────────────────────────────────────────────

OPERATIONS: list[FileOperation] = []
if _HAS_CRYPTO:
    OPERATIONS = [
        FileOperation(
            name="Encrypt",
            run=_encrypt_run,
            prepare=_prepare_encrypt,
            description="Encrypt selected files with a password or stored key (.crp).",
        ),
        FileOperation(
            name="Decrypt",
            run=_decrypt_run,
            prepare=_prepare_decrypt,
            description="Decrypt selected .crp files.",
        ),
    ]
