"""Compression dialog for creating compressed archives (Shift+F5).

Extracted from dialogs.py so that the core dialog helpers stay small.

Public API:
    class CompressionDialog
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Any

from linux_commander import archiving, dialogs


class CompressionDialog:
    """Dialog for creating compressed archives.

    The container (multi-file archive format) and the outer compression
    codec are chosen independently: any codec may wrap any container (e.g.
    ``grp.zst``, ``zip.gz``, ``7z.xz``), the same way ``tar.gz`` already
    works today -- the codec just wraps the finished container file.
    """

    def __init__(
        self,
        parent: tk.Misc,
        current_path: Any,
        sources: list[Any],
    ) -> None:
        self.parent = parent
        self.current_path = current_path
        self.sources = sources
        self.result: tuple[Any, str, dict[str, object]] | None = None

        self.top = tk.Toplevel(parent)
        self.top.title("Compress Files")
        self.top.transient(parent)  # type: ignore[call-overload]
        self.top.resizable(False, False)
        self.top.protocol("WM_DELETE_WINDOW", self._on_cancel)

        self._create_widgets()
        dialogs._center_over(self.top, parent)
        self.top.grab_set()
        self.top.wait_window()

    def _create_widgets(self) -> None:
        main_frame = ttk.Frame(self.top, padding=16)
        main_frame.pack(fill="both", expand=True)

        # Archive name
        ttk.Label(main_frame, text="Archive name:").grid(row=0, column=0, sticky="w", pady=4)
        # Default name based on current directory
        default_name = Path(str(self.current_path)).name or "archive"
        self.name_var = tk.StringVar(value=default_name)
        self.name_entry = ttk.Entry(main_frame, textvariable=self.name_var, width=40)
        self.name_entry.grid(row=0, column=1, sticky="ew", pady=4, padx=(8, 0))
        self.name_entry.select_range(0, "end")
        self.name_entry.icursor("end")

        # Container (multi-file archive format)
        ttk.Label(main_frame, text="Container:").grid(row=1, column=0, sticky="w", pady=4)
        self.container_var = tk.StringVar(value="zip")
        ttk.Combobox(
            main_frame,
            textvariable=self.container_var,
            values=list(archiving.CONTAINERS),
            state="readonly",
            width=10,
        ).grid(row=1, column=1, sticky="w", pady=4, padx=(8, 0))

        # Outer compression codec -- independent of the container.
        ttk.Label(main_frame, text="Compression:").grid(row=2, column=0, sticky="w", pady=4)
        self.codec_var = tk.StringVar(value="none")
        codec_combo = ttk.Combobox(
            main_frame,
            textvariable=self.codec_var,
            values=list(archiving.CODECS),
            state="readonly",
            width=10,
        )
        codec_combo.grid(row=2, column=1, sticky="w", pady=4, padx=(8, 0))
        codec_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_codec_change())

        # Options frame
        options_frame = ttk.LabelFrame(main_frame, text="Options", padding=8)
        options_frame.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 4))

        # Compression level
        ttk.Label(options_frame, text="Compression level:").grid(
            row=0, column=0, sticky="w", pady=4
        )
        self.compresslevel_var = tk.IntVar(value=6)
        self.level_spinbox = ttk.Spinbox(
            options_frame,
            from_=1,
            to=9,
            textvariable=self.compresslevel_var,
            width=5,
        )
        self.level_spinbox.grid(row=0, column=1, sticky="w", padx=8, pady=4)
        self._on_codec_change()  # set initial enabled/disabled state

        # Encryption -- an optional final stage reusing the same
        # ChaCha20-Poly1305 code as the Operations > Encrypt/Decrypt menu
        # item, producing a .crp file browsable the same way either was made.
        from linux_commander.file_ops import crypt_op

        self._has_crypto = crypt_op._HAS_CRYPTO
        self.encrypt_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            options_frame,
            text="Encrypt output (.crp)",
            variable=self.encrypt_var,
            command=self._on_encrypt_toggle,
            state="normal" if self._has_crypto else "disabled",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        if not self._has_crypto:
            ttk.Label(
                options_frame, text="(cryptography package not installed)", foreground="gray"
            ).grid(row=1, column=2, sticky="w", padx=4)

        self.crypt_mode_var = tk.StringVar(value="password")
        self._crypt_radio_frame = ttk.Frame(options_frame)
        self._crypt_radio_frame.grid(row=2, column=0, columnspan=3, sticky="w")
        ttk.Radiobutton(
            self._crypt_radio_frame,
            text="Password",
            variable=self.crypt_mode_var,
            value="password",
            command=self._on_crypt_mode_change,
        ).pack(side="left")
        ttk.Radiobutton(
            self._crypt_radio_frame,
            text="Stored Key",
            variable=self.crypt_mode_var,
            value="key",
            command=self._on_crypt_mode_change,
        ).pack(side="left", padx=(8, 0))

        self.password_var = tk.StringVar()
        self._password_frame = ttk.Frame(options_frame)
        self._password_frame.grid(row=3, column=0, columnspan=3, sticky="w", pady=4)
        ttk.Label(self._password_frame, text="Password:").pack(side="left")
        ttk.Entry(self._password_frame, textvariable=self.password_var, width=30, show="*").pack(
            side="left", padx=(8, 0)
        )

        from linux_commander.settings import load_settings

        self.key_var = tk.StringVar()
        self._key_frame = ttk.Frame(options_frame)
        self._key_frame.grid(row=4, column=0, columnspan=3, sticky="w", pady=4)
        key_names = [sk.name for sk in load_settings().stored_keys]
        self._key_combo = ttk.Combobox(
            self._key_frame, textvariable=self.key_var, values=key_names, state="readonly", width=28
        )
        self._key_combo.pack(side="left")
        if key_names:
            self._key_combo.set(key_names[0])
        ttk.Button(self._key_frame, text="Manage Keys...", command=self._open_manage_keys).pack(
            side="left", padx=(8, 0)
        )

        self._on_encrypt_toggle()  # set initial visibility

        # Buttons
        btn_frame = ttk.Frame(main_frame)
        btn_frame.grid(row=4, column=0, columnspan=2, pady=(16, 0))
        ttk.Button(btn_frame, text="Cancel", command=self._on_cancel).pack(side="right", padx=4)
        ttk.Button(btn_frame, text="Create", command=self._on_ok).pack(side="right")

        self.top.bind("<Return>", lambda e: self._on_ok())
        self.top.bind("<Escape>", lambda e: self._on_cancel())
        self.name_entry.focus_set()

        main_frame.columnconfigure(1, weight=1)

    def _on_codec_change(self) -> None:
        """Compression level is meaningless for an uncompressed ("none") wrap."""
        state = "disabled" if self.codec_var.get() == "none" else "normal"
        self.level_spinbox.configure(state=state)

    def _on_encrypt_toggle(self) -> None:
        """Show/hide the password-vs-key controls based on the Encrypt checkbox."""
        if self.encrypt_var.get() and self._has_crypto:
            self._crypt_radio_frame.grid()
            self._on_crypt_mode_change()
        else:
            self._crypt_radio_frame.grid_remove()
            self._password_frame.grid_remove()
            self._key_frame.grid_remove()

    def _on_crypt_mode_change(self) -> None:
        """Show the password entry or the stored-key picker, not both."""
        if not (self.encrypt_var.get() and self._has_crypto):
            return
        if self.crypt_mode_var.get() == "password":
            self._password_frame.grid()
            self._key_frame.grid_remove()
        else:
            self._password_frame.grid_remove()
            self._key_frame.grid()

    def _open_manage_keys(self) -> None:
        from linux_commander.file_ops.crypt_op import _manage_keys_dialog
        from linux_commander.settings import load_settings

        _manage_keys_dialog(self.top)
        # Keys may have been added/removed; reload and repopulate the
        # combobox instead of leaving it stuck with the original snapshot.
        names = [sk.name for sk in load_settings().stored_keys]
        current = self.key_var.get()
        self._key_combo["values"] = names
        if current in names:
            self._key_combo.set(current)
        elif names:
            self._key_combo.set(names[0])
        else:
            self._key_combo.set("")

    def _on_ok(self) -> None:
        name = self.name_var.get().strip()
        if not name:
            dialogs.error(self.top, "Please enter an archive name.", title="Compress")
            return

        container = self.container_var.get()
        codec = self.codec_var.get()

        password: str | None = None
        key_name: str | None = None
        if self.encrypt_var.get() and self._has_crypto:
            if self.crypt_mode_var.get() == "password":
                password = self.password_var.get()
                if not password:
                    dialogs.error(self.top, "Please enter a password.", title="Compress")
                    return
            else:
                key_name = self.key_var.get()
                if not key_name:
                    dialogs.error(self.top, "Please select a stored key.", title="Compress")
                    return

        # Add extension if not present
        ext = archiving.compose_extension(container, codec, encrypted=bool(password or key_name))
        if not name.endswith(ext):
            name += ext

        # Determine archive path (in current directory)
        archive_path = self.current_path / name

        options: dict[str, object] = {
            "container": container,
            "codec": codec,
            "compresslevel": self.compresslevel_var.get(),
            "password": password,
            "key_name": key_name,
        }

        fmt = container if codec == "none" else f"{container}.{codec}"
        self.result = (archive_path, fmt, options)
        self.top.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self.top.destroy()
