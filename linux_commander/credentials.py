"""Credential management using system keyring.

Provides secure storage and retrieval of passwords for network connections
(SMB, WebDAV, FTP, SFTP) using the ``keyring`` library, which uses the
platform's native credential store (Windows Credential Manager, macOS Keychain,
GNOME Keyring, KWallet, etc.).
"""

from __future__ import annotations

import keyring
from keyring.errors import KeyringError

from linux_commander.settings import Settings

SERVICE_NAME = "linux-commander"


def _credential_key(scheme: str, host: str, share: str = "") -> str:
    """Generate a unique key for storing/retrieving credentials.

    Args:
        scheme: Protocol scheme (smb, webdav, webdavs, ftp, sftp, jotta)
        host: Hostname or IP address
        share: Share name or path component (for SMB, this is the share name;
               for WebDAV, this could be the path prefix)

    Returns:
        A string key suitable for keyring storage.
    """
    parts = [scheme, host]
    if share:
        parts.append(share)
    return "|".join(parts)


def store_credential(scheme: str, host: str, share: str, username: str, password: str) -> bool:
    """Store a credential in the system keyring.

    Args:
        scheme: Protocol scheme (smb, webdav, webdavs, ftp, sftp, jotta)
        host: Hostname or IP address
        share: Share name or path prefix
        username: Username
        password: Password to store

    Returns:
        True if stored successfully, False otherwise.
    """
    try:
        key = _credential_key(scheme, host, share)
        # Store as "username|password" to keep both together
        keyring.set_password(SERVICE_NAME, key, f"{username}|{password}")
        return True
    except KeyringError:
        return False


def get_credential(scheme: str, host: str, share: str) -> tuple[str, str] | None:
    """Retrieve a credential from the system keyring.

    Args:
        scheme: Protocol scheme (smb, webdav, webdavs, ftp, sftp, jotta)
        host: Hostname or IP address
        share: Share name or path prefix

    Returns:
        Tuple of (username, password) if found, None otherwise.
    """
    try:
        key = _credential_key(scheme, host, share)
        stored = keyring.get_password(SERVICE_NAME, key)
        if stored and "|" in stored:
            username, password = stored.split("|", 1)
            return username, password
        return None
    except KeyringError:
        return None


def delete_credential(scheme: str, host: str, share: str) -> bool:
    """Delete a credential from the system keyring.

    Args:
        scheme: Protocol scheme (smb, webdav, webdavs, ftp, sftp, jotta)
        host: Hostname or IP address
        share: Share name or path prefix

    Returns:
        True if deleted successfully, False otherwise.
    """
    try:
        key = _credential_key(scheme, host, share)
        keyring.delete_password(SERVICE_NAME, key)
        return True
    except KeyringError:
        return False


def get_saved_sessions_from_keyring(scheme: str) -> list[tuple[str, str, str, str]]:
    """Get all saved sessions for a given scheme from keyring.

    This is a best-effort function that may not work on all keyring backends
    (some don't support listing all keys).

    Returns:
        List of (host, share, username, password) tuples.
    """
    # keyring doesn't have a standard way to list all keys for a service
    # This is a placeholder for future enhancement if needed
    return []


def prompt_for_credentials(
    parent,
    scheme: str,
    host: str,
    share: str = "",
    default_user: str = "",
) -> tuple[str, str] | None:
    """Show a dialog to prompt for credentials.

    Args:
        parent: Parent tkinter widget
        scheme: Protocol scheme (displayed in dialog title)
        host: Hostname
        share: Share name or path
        default_user: Default username to pre-fill

    Returns:
        Tuple of (username, password) if user clicks OK, None if cancelled.
    """
    # Import here to avoid circular imports
    import tkinter as tk
    from tkinter import ttk

    from linux_commander import dialogs

    dialog = tk.Toplevel(parent)
    dialog.title(f"Connect to {scheme.upper()} Share")
    dialog.transient(parent)
    dialog.resizable(False, False)

    # Center over parent
    dialog.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() // 2) - (dialog.winfo_width() // 2)
    y = parent.winfo_rooty() + (parent.winfo_height() // 2) - (dialog.winfo_height() // 2)
    dialog.geometry(f"+{x}+{y}")

    main_frame = ttk.Frame(dialog, padding=12)
    main_frame.pack(fill="both", expand=True)

    # Host/share info
    info_text = f"Host: {host}"
    if share:
        info_text += f"\nShare: {share}"
    ttk.Label(main_frame, text=info_text, justify="left").grid(
        row=0, column=0, columnspan=2, sticky="w", pady=(0, 12)
    )

    # Username
    ttk.Label(main_frame, text="Username:").grid(row=1, column=0, sticky="w", pady=4)
    user_var = tk.StringVar(value=default_user)
    ttk.Entry(main_frame, textvariable=user_var, width=30).grid(
        row=1, column=1, sticky="ew", pady=4
    )

    # Password
    ttk.Label(main_frame, text="Password:").grid(row=2, column=0, sticky="w", pady=4)
    pass_var = tk.StringVar()
    ttk.Entry(main_frame, textvariable=pass_var, width=30, show="*").grid(
        row=2, column=1, sticky="ew", pady=4
    )

    # Remember checkbox
    remember_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(main_frame, text="Remember password", variable=remember_var).grid(
        row=3, column=0, columnspan=2, sticky="w", pady=(8, 0)
    )

    result: dict[str, tuple[str, str] | None] = {"value": None}

    def on_ok() -> None:
        username = user_var.get().strip()
        password = pass_var.get()
        if not username:
            dialogs.error(dialog, "Username is required.", title="Missing Username")
            return
        result["value"] = (username, password)
        if remember_var.get():
            store_credential(scheme, host, share, username, password)
        dialog.destroy()

    def on_cancel() -> None:
        result["value"] = None
        dialog.destroy()

    btn_frame = ttk.Frame(main_frame)
    btn_frame.grid(row=4, column=0, columnspan=2, pady=(12, 0), sticky="e")
    ttk.Button(btn_frame, text="Cancel", command=on_cancel).pack(side="right", padx=4)
    ttk.Button(btn_frame, text="Connect", command=on_ok).pack(side="right", padx=4)

    main_frame.columnconfigure(1, weight=1)
    dialog.grab_set()
    user_var.trace_add("write", lambda *_: None)  # ensure entry gets focus
    dialog.wait_window()

    return result["value"]


class CredentialManager:
    """High-level credential manager that integrates with Settings and keyring.

    This class provides a unified interface for getting credentials, first
    checking the keyring, then falling back to saved sessions in Settings,
    and finally prompting the user.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    def get_credential(
        self,
        parent,
        scheme: str,
        host: str,
        share: str,
        default_user: str = "",
    ) -> tuple[str, str] | None:
        """Get credential for a connection, with fallback chain.

        1. Try keyring
        2. Try saved sessions in settings
        3. Prompt user

        Args:
            parent: Parent tkinter widget for dialogs
            scheme: Protocol scheme
            host: Hostname
            share: Share/path
            default_user: Default username

        Returns:
            Tuple of (username, password) or None if cancelled.
        """
        # 1. Try keyring
        cred = get_credential(scheme, host, share)
        if cred:
            return cred

        # 2. Try saved sessions (for FTP/SFTP/Jotta which store in settings)
        if scheme in ("ftp", "sftp"):
            for session in self.settings.ftp_sessions:
                if session.host == host and session.path.rstrip("/") == share.rstrip("/"):
                    return session.user, session.password

        # 3. Prompt user
        return prompt_for_credentials(parent, scheme, host, share, default_user)

    def save_credential(
        self,
        scheme: str,
        host: str,
        share: str,
        username: str,
        password: str,
        remember: bool = True,
    ) -> None:
        """Save a credential, using keyring if available and remember is True."""
        if remember:
            store_credential(scheme, host, share, username, password)
