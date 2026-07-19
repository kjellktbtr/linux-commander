"""Remote Connections manager dialog (FTP, SFTP, SMB, WebDAV, Jottacloud sessions)."""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, ttk
from typing import Any

from linux_commander import dialogs
from linux_commander.plugins import plugin_for_scheme
from linux_commander.settings import FtpSession, JottaSession, Settings
from linux_commander.vfs import MountManager

_PROTOCOLS = ("ftp", "sftp", "smb", "webdav", "webdavs", "jotta")
_DEFAULT_PORTS = {"ftp": 21, "sftp": 22, "smb": 445, "webdav": 80, "webdavs": 443}


class RemoteConnectionDialog:
    """Dialog for managing saved remote connections (FTP/SFTP/Jottacloud)."""

    def __init__(
        self,
        parent: tk.Misc,
        settings: Settings,
        mount_manager: MountManager,
        target_panel_getter,
    ) -> None:
        self.parent = parent
        self.settings = settings
        self.mount_manager = mount_manager
        self.target_panel_getter = target_panel_getter

        self.top = tk.Toplevel(parent)
        self.top.title("Connections")
        self.top.transient(parent)  # type: ignore[call-overload]
        self.top.resizable(True, True)
        self.top.geometry("600x400")
        self.top.minsize(500, 300)

        self._create_widgets()
        self._load_sessions()

        dialogs._center_over(self.top, parent)
        self.top.grab_set()
        self.listbox.focus_set()
        self.top.wait_window()

    def _create_widgets(self) -> None:
        # Main frame
        main_frame = ttk.Frame(self.top)
        main_frame.pack(fill="both", expand=True, padx=8, pady=8)

        # Listbox with sessions
        list_frame = ttk.Frame(main_frame)
        list_frame.pack(fill="both", expand=True)

        self.listbox = tk.Listbox(list_frame, selectmode="single", exportselection=False)
        self.listbox.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        scrollbar.pack(side="right", fill="y")
        self.listbox.configure(yscrollcommand=scrollbar.set)

        self.listbox.bind("<Double-Button-1>", lambda e: self._on_connect())
        self.listbox.bind("<Return>", lambda e: self._on_connect())
        self.listbox.bind("<Delete>", lambda e: self._on_delete())

        # Button frame
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill="x", pady=(8, 0))

        ttk.Button(btn_frame, text="New...", command=self._on_new).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Edit...", command=self._on_edit).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Delete", command=self._on_delete).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Connect", command=self._on_connect).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Close", command=self.top.destroy).pack(side="right", padx=4)

    def _load_sessions(self) -> None:
        self.listbox.delete(0, "end")
        # FTP/SFTP/SMB/WebDAV sessions
        for ftp_session in self.settings.ftp_sessions:
            user_host = f"{ftp_session.user}@{ftp_session.host}:{ftp_session.port}"
            display = (
                f"[{ftp_session.protocol}] {ftp_session.name}  ({user_host}{ftp_session.path})"
            )
            self.listbox.insert("end", display)
        # Jottacloud sessions
        for jotta_session in self.settings.jotta_sessions:
            dev_mp = f"{jotta_session.device}/{jotta_session.mountpoint}{jotta_session.path}"
            display = f"[jotta] {jotta_session.name}  ({dev_mp})"
            self.listbox.insert("end", display)

    def _get_selected_index(self) -> int | None:
        sel = self.listbox.curselection()
        return sel[0] if sel else None

    def _get_selected_session(self) -> FtpSession | JottaSession | None:
        """Get the selected session (FTP/SFTP or Jottacloud)."""
        idx = self._get_selected_index()
        if idx is None:
            return None
        ftp_count = len(self.settings.ftp_sessions)
        if idx < ftp_count:
            return self.settings.ftp_sessions[idx]
        return self.settings.jotta_sessions[idx - ftp_count]

    def _on_new(self) -> None:
        self._edit_session(None)

    def _on_edit(self) -> None:
        idx = self._get_selected_index()
        if idx is not None:
            self._edit_session(idx)

    def _on_delete(self) -> None:
        idx = self._get_selected_index()
        if idx is not None:
            ftp_count = len(self.settings.ftp_sessions)
            if idx < ftp_count:
                session: FtpSession | JottaSession = self.settings.ftp_sessions[idx]
                session_type = "FTP/SFTP"
            else:
                session = self.settings.jotta_sessions[idx - ftp_count]
                session_type = "Jottacloud"
            if dialogs.confirm(
                self.top,
                f"Delete {session_type} connection '{session.name}'?",
                title="Delete",
            ):
                if idx < ftp_count:
                    del self.settings.ftp_sessions[idx]
                else:
                    del self.settings.jotta_sessions[idx - ftp_count]
                self._load_sessions()

    def _on_connect(self) -> None:
        idx = self._get_selected_index()
        if idx is None:
            return
        ftp_count = len(self.settings.ftp_sessions)
        if idx < ftp_count:
            session_ftp: FtpSession = self.settings.ftp_sessions[idx]
            self._connect_ftp_session(session_ftp)
        else:
            session_jotta: JottaSession = self.settings.jotta_sessions[idx - ftp_count]
            self._connect_jotta_session(session_jotta)

    def _edit_session(self, index: int | None) -> None:
        """Open dialog to add/edit a connection."""
        if index is not None:
            ftp_count = len(self.settings.ftp_sessions)
            if index < ftp_count:
                session_ftp: FtpSession = self.settings.ftp_sessions[index]
                session: FtpSession | JottaSession = session_ftp
                is_jotta = False
            else:
                session_jotta: JottaSession = self.settings.jotta_sessions[index - ftp_count]
                session = session_jotta
                is_jotta = True
            title = "Edit Connection"
        else:
            # Default to FTP for new connections
            session = FtpSession(name="", host="", port=21, user="anonymous", password="", path="/")
            is_jotta = False
            title = "New Connection"

        dialog = tk.Toplevel(self.top)
        dialog.title(title)
        dialog.transient(self.top)
        dialog.resizable(False, False)

        # Protocol selector
        ttk.Label(dialog, text="Protocol:").grid(row=0, column=0, padx=8, pady=8, sticky="w")
        protocol_var = tk.StringVar(
            value="jotta"
            if is_jotta
            else session.protocol
            if hasattr(session, "protocol")
            else "ftp"
        )
        protocol_combo = ttk.Combobox(
            dialog, textvariable=protocol_var, values=_PROTOCOLS, state="readonly", width=10
        )
        protocol_combo.grid(row=0, column=1, padx=8, pady=8, sticky="w")

        # Common fields
        ttk.Label(dialog, text="Name:").grid(row=1, column=0, padx=8, pady=8, sticky="w")
        name_var = tk.StringVar(value=session.name)
        ttk.Entry(dialog, textvariable=name_var, width=40).grid(row=1, column=1, padx=8, pady=8)

        # FTP/SFTP/SMB/WebDAV fields frame
        ftp_frame = ttk.Frame(dialog)
        ftp_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=8, pady=4)

        ttk.Label(ftp_frame, text="Host:").grid(row=0, column=0, padx=8, pady=8, sticky="w")
        host_var = tk.StringVar(value=getattr(session, "host", ""))
        ttk.Entry(ftp_frame, textvariable=host_var, width=40).grid(row=0, column=1, padx=8, pady=8)

        ttk.Label(ftp_frame, text="Port:").grid(row=1, column=0, padx=8, pady=8, sticky="w")
        port_var = tk.IntVar(value=getattr(session, "port", 21))
        ttk.Spinbox(ftp_frame, from_=1, to=65535, textvariable=port_var, width=10).grid(
            row=1, column=1, padx=8, pady=8, sticky="w"
        )

        ttk.Label(ftp_frame, text="User:").grid(row=2, column=0, padx=8, pady=8, sticky="w")
        user_var = tk.StringVar(value=getattr(session, "user", "anonymous"))
        ttk.Entry(ftp_frame, textvariable=user_var, width=40).grid(row=2, column=1, padx=8, pady=8)

        ttk.Label(ftp_frame, text="Password:").grid(row=3, column=0, padx=8, pady=8, sticky="w")
        pass_var = tk.StringVar(value=getattr(session, "password", ""))
        ttk.Entry(ftp_frame, textvariable=pass_var, width=40, show="*").grid(
            row=3, column=1, padx=8, pady=8
        )

        ttk.Label(ftp_frame, text="Path:").grid(row=4, column=0, padx=8, pady=8, sticky="w")
        path_var = tk.StringVar(value=getattr(session, "path", "/"))
        ttk.Entry(ftp_frame, textvariable=path_var, width=40).grid(row=4, column=1, padx=8, pady=8)

        # SFTP-only: private key auth
        key_label = ttk.Label(ftp_frame, text="Private Key (SFTP):")
        key_label.grid(row=5, column=0, padx=8, pady=8, sticky="w")
        key_frame = ttk.Frame(ftp_frame)
        key_frame.grid(row=5, column=1, padx=8, pady=8, sticky="w")
        key_path_var = tk.StringVar(value=getattr(session, "key_path", ""))
        key_entry = ttk.Entry(key_frame, textvariable=key_path_var, width=31)
        key_entry.pack(side="left")

        def _browse_key() -> None:
            path_str = filedialog.askopenfilename(parent=dialog, title="Select Private Key")
            if path_str:
                key_path_var.set(path_str)

        key_browse_btn = ttk.Button(key_frame, text="...", width=3, command=_browse_key)
        key_browse_btn.pack(side="left", padx=(4, 0))

        passphrase_label = ttk.Label(ftp_frame, text="Key Passphrase:")
        passphrase_label.grid(row=6, column=0, padx=8, pady=8, sticky="w")
        passphrase_var = tk.StringVar(value=getattr(session, "key_passphrase", ""))
        passphrase_entry = ttk.Entry(ftp_frame, textvariable=passphrase_var, width=40, show="*")
        passphrase_entry.grid(row=6, column=1, padx=8, pady=8)

        # SMB-specific: Share name field
        smb_share_label = ttk.Label(ftp_frame, text="Share (SMB):")
        smb_share_label.grid(row=7, column=0, padx=8, pady=8, sticky="w")
        smb_share_var = tk.StringVar(value="")
        ttk.Entry(ftp_frame, textvariable=smb_share_var, width=40).grid(
            row=7, column=1, padx=8, pady=8
        )

        # WebDAV-specific: Root path field
        webdav_root_label = ttk.Label(ftp_frame, text="Root Path (WebDAV):")
        webdav_root_label.grid(row=8, column=0, padx=8, pady=8, sticky="w")
        webdav_root_var = tk.StringVar(value="")
        ttk.Entry(ftp_frame, textvariable=webdav_root_var, width=40).grid(
            row=8, column=1, padx=8, pady=8
        )

        # Jottacloud fields frame
        jotta_frame = ttk.Frame(dialog)
        # Will be shown/hidden based on protocol
        lbl = ttk.Label(jotta_frame, text="Personal Login Token:")
        lbl.grid(row=0, column=0, padx=8, pady=8, sticky="w")
        token_var = tk.StringVar(value=getattr(session, "token", ""))
        entry = ttk.Entry(jotta_frame, textvariable=token_var, width=40, show="*")
        entry.grid(row=0, column=1, padx=8, pady=8)

        ttk.Label(jotta_frame, text="(Get from https://www.jottacloud.com/web/secure)").grid(
            row=1, column=0, columnspan=2, padx=8, pady=2, sticky="w"
        )

        ttk.Label(jotta_frame, text="Device:").grid(row=2, column=0, padx=8, pady=8, sticky="w")
        device_var = tk.StringVar(value=getattr(session, "device", "Jotta"))
        ttk.Entry(jotta_frame, textvariable=device_var, width=40).grid(
            row=2, column=1, padx=8, pady=8
        )

        ttk.Label(jotta_frame, text="Mountpoint:").grid(row=3, column=0, padx=8, pady=8, sticky="w")
        mountpoint_var = tk.StringVar(value=getattr(session, "mountpoint", "Archive"))
        ttk.Entry(jotta_frame, textvariable=mountpoint_var, width=40).grid(
            row=3, column=1, padx=8, pady=8
        )

        ttk.Label(jotta_frame, text="Path:").grid(row=4, column=0, padx=8, pady=8, sticky="w")
        jotta_path_var = tk.StringVar(value=getattr(session, "path", "/"))
        ttk.Entry(jotta_frame, textvariable=jotta_path_var, width=40).grid(
            row=4, column=1, padx=8, pady=8
        )

        def _update_protocol_state(*_args: object) -> None:
            protocol = protocol_var.get()
            is_ftp_sftp = protocol in ("ftp", "sftp")
            is_smb_webdav = protocol in ("smb", "webdav", "webdavs")
            is_jotta = protocol == "jotta"

            # Show/hide FTP/SFTP/SMB/WebDAV fields (they all use the same field frame)
            if is_ftp_sftp or is_smb_webdav:
                ftp_frame.grid()
            else:
                ftp_frame.grid_remove()

            # Show/hide Jottacloud fields
            if is_jotta:
                jotta_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=8, pady=4)
            else:
                jotta_frame.grid_remove()

            # SFTP-only fields
            state = "normal" if protocol == "sftp" else "disabled"
            key_entry.configure(state=state)
            key_browse_btn.configure(state=state)
            passphrase_entry.configure(state=state)

            # Set default port based on protocol -- swap whenever the field
            # still holds *any* known protocol's default (not just ftp/sftp,
            # which this used to be hardcoded to -- so picking SMB from a
            # fresh dialog, which starts at ftp's port 21, never switched to
            # 445). A manually-typed custom port never matches any default
            # here, so it's left untouched.
            if port_var.get() in _DEFAULT_PORTS.values():
                port_var.set(_DEFAULT_PORTS.get(protocol, port_var.get()))

        protocol_combo.bind("<<ComboboxSelected>>", _update_protocol_state)
        _update_protocol_state()

        def on_ok() -> None:
            name = name_var.get().strip()
            protocol = protocol_var.get()
            if not name:
                dialogs.error(dialog, "Name is required.", title="Invalid input")
                return

            if protocol in ("ftp", "sftp", "smb", "webdav", "webdavs"):
                host = host_var.get().strip()
                if not host:
                    dialogs.error(dialog, "Host is required.", title="Invalid input")
                    return
                ftp_session = FtpSession(
                    name=name,
                    host=host,
                    port=port_var.get(),
                    user=user_var.get(),
                    password=pass_var.get(),
                    path=path_var.get() or "/",
                    protocol=protocol,
                    key_path=key_path_var.get().strip(),
                    key_passphrase=passphrase_var.get(),
                )
                if index is not None:
                    if index < len(self.settings.ftp_sessions):
                        self.settings.ftp_sessions[index] = ftp_session
                    else:
                        # Index was Jotta, but now FTP/SMB/WebDAV - remove old and add new
                        del self.settings.jotta_sessions[index - len(self.settings.ftp_sessions)]
                        self.settings.ftp_sessions.append(ftp_session)
                else:
                    self.settings.ftp_sessions.append(ftp_session)
            else:  # jotta
                token = token_var.get().strip()
                if not token:
                    msg = "Personal Login Token is required for Jottacloud."
                    dialogs.error(dialog, msg, title="Invalid input")
                    return
                jotta_session = JottaSession(
                    name=name,
                    token=token,
                    device=device_var.get().strip() or "Jotta",
                    mountpoint=mountpoint_var.get().strip() or "Archive",
                    path=jotta_path_var.get().strip() or "/",
                )
                if index is not None:
                    if index < len(self.settings.ftp_sessions):
                        # Index was FTP, but now Jotta - remove old and add new
                        del self.settings.ftp_sessions[index]
                        self.settings.jotta_sessions.append(jotta_session)
                    else:
                        jotta_idx = index - len(self.settings.ftp_sessions)
                        self.settings.jotta_sessions[jotta_idx] = jotta_session
                else:
                    self.settings.jotta_sessions.append(jotta_session)

            self._load_sessions()
            dialog.destroy()

        btn_frame = ttk.Frame(dialog)
        btn_frame.grid(row=7, column=0, columnspan=2, pady=8)
        ttk.Button(btn_frame, text="OK", command=on_ok).pack(side="right", padx=4)
        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side="right", padx=4)

        dialogs._center_over(dialog, self.top)
        dialog.grab_set()
        dialog.wait_window()

    def _connect_ftp_session(self, session: FtpSession) -> None:
        """Connect to an FTP/SFTP session and load into target panel."""
        try:
            plugin = plugin_for_scheme(session.protocol)
            if plugin is None:
                dialogs.error(
                    self.top,
                    f"{session.protocol.upper()} support is not available "
                    f"(missing optional dependency).",
                    title="Connect",
                )
                return
            if session.protocol == "sftp" and session.key_path:
                fs = plugin.connect(
                    session.host,
                    session.port,
                    session.user,
                    session.password,
                    session.key_path,
                    session.key_passphrase,
                    session.path,
                )
            else:
                fs = plugin.connect_fs(session.to_url())
        except OSError as exc:
            dialogs.error(self.top, f"Cannot connect:\n{exc}", title="Connection Error")
            return
        except Exception as exc:
            dialogs.error(
                self.top, f"Unexpected error connecting:\n{exc}", title="Connection Error"
            )
            return

        root = self.mount_manager.mount_scheme_fs(fs)
        panel = self.target_panel_getter()
        if panel:
            panel.load(root)
        self.top.destroy()

    def _connect_jotta_session(self, session: JottaSession) -> None:
        """Connect to a Jottacloud session and load into target panel.

        A Personal Login Token can only be exchanged once — Jottacloud
        invalidates it after the first use. So on first connect we exchange
        it and persist the resulting OAuth refresh token onto the session;
        on every later connect we restore from those persisted tokens
        instead of re-exchanging the (by-then-dead) login token.
        """
        try:
            from linux_commander.jotta_api import AuthToken, JottaAuthError, SyncJottaAPI
            from linux_commander.plugins.jotta_plugin import JottaFileSystem
        except ImportError:
            dialogs.error(
                self.top,
                "Jottacloud support is not installed.\n"
                "Install the 'jotta' extra (see File > Optional Dependencies...).",
                title="Connection Error",
            )
            return

        # Wired so any *mid-session* proactive refresh (triggered later, while
        # browsing, by _ensure_valid_token) is persisted immediately too.
        async def _on_token_update(token: AuthToken) -> None:
            self._persist_jotta_token(session, token)

        api = SyncJottaAPI(on_token_update=_on_token_update)
        try:
            if session.is_provisioned:
                api.restore(
                    AuthToken(
                        access_token=session.access_token,
                        refresh_token=session.refresh_token,
                        expires_in=0,
                        expires_at=session.token_expiry,
                    ),
                    session.username,
                )
                if api.token is not None and api.token.is_expired:
                    # Persisted via _on_token_update above.
                    api.refresh_token()
            else:
                # One-time login-token exchange. _on_token_update already
                # fired here with the correct tokens, but session.username
                # was still empty at that point, so persist once more now
                # that it's known.
                api.authenticate(session.token)
                username = api.username
                if not username:
                    raise JottaAuthError("Failed to get username from token")
                session.username = username
                if api.token is not None:
                    self._persist_jotta_token(session, api.token)

            username = api.username
            if not username:
                raise JottaAuthError("Not authenticated - username unknown")

            fs = JottaFileSystem(
                api=api,
                username=username,
                device=session.device,
                mountpoint=session.mountpoint,
                root_path=session.path,
            )
        except JottaAuthError as exc:
            dialogs.error(self.top, f"Authentication failed:\n{exc}", title="Connection Error")
            return
        except Exception as exc:
            dialogs.error(
                self.top, f"Unexpected error connecting:\n{exc}", title="Connection Error"
            )
            return

        root = self.mount_manager.mount_scheme_fs(fs)
        panel = self.target_panel_getter()
        if panel:
            panel.load(root)
        self.top.destroy()

    def _persist_jotta_token(self, session: JottaSession, token: Any) -> None:
        """Save a Jottacloud OAuth token (fresh or refreshed) back to settings.

        ``token`` is a ``linux_commander.jotta_api.AuthToken``; typed as ``Any``
        here since that module is an optional-dependency import, not a
        module-level import of this file.
        """
        from linux_commander.settings import save_settings

        session.access_token = token.access_token
        session.refresh_token = token.refresh_token
        session.token_expiry = token.expires_at
        save_settings(self.settings)


def show_remote_connections(
    parent: tk.Misc,
    settings: Settings,
    mount_manager: MountManager,
    target_panel_getter,
) -> None:
    """Show the remote connections manager dialog (FTP/SFTP/Jottacloud)."""
    RemoteConnectionDialog(parent, settings, mount_manager, target_panel_getter)
