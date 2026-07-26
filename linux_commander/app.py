"""CommanderApp: the dual-panel application shell and entry point."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import ttk
from typing import TYPE_CHECKING

try:
    import ttkbootstrap as _ttkbs  # noqa: F401

    _HAS_TTKBOOTSTRAP = True
except ImportError:
    _HAS_TTKBOOTSTRAP = False

from linux_commander import dialogs, operations, viewer, volumes
from linux_commander.command_prompt import CommandPrompt
from linux_commander.diff_viewer import compare_directories, show_diff_viewer
from linux_commander.fkey_bar import FKeyBar
from linux_commander.font_manager import (
    apply_font_settings as _apply_font_settings_fn,
)
from linux_commander.font_manager import (
    show_font_dialog as _show_font_dialog_fn,
)
from linux_commander.font_manager import (
    show_font_picker as _show_font_picker_fn,
)
from linux_commander.fs import format_size
from linux_commander.ftp_dialog import show_remote_connections
from linux_commander.keys import F_KEY_SPECS, FKeySpec
from linux_commander.operations_controller import OperationsController
from linux_commander.panel import FilePanel
from linux_commander.search_dialog import SearchDialog
from linux_commander.search_engine import SearchCriteria
from linux_commander.session_manager import SessionManager
from linux_commander.settings import StoredKey, load_settings
from linux_commander.theme_manager import (
    apply_theme as _apply_theme_fn,
)
from linux_commander.theme_manager import (
    init_ttkbootstrap as _init_ttkbootstrap_fn,
)
from linux_commander.theme_manager import (
    set_ttkbootstrap_available,
)
from linux_commander.theme_manager import (
    show_theme_picker as _show_theme_picker_fn,
)
from linux_commander.vfs import FileEntry, LocalFileSystem, MountManager, WritableFileSystem

if TYPE_CHECKING:
    from linux_commander.search_controller import SearchController

HELP_TEXT = """\
linux-commander — keybindings

Navigation
  Up/Down/PgUp/PgDn/Home/End   move the cursor
  Enter / Right                open a directory, or a file (default app,
                                falling back to the built-in viewer)
  Backspace / Left             go to the parent directory
  Tab                          switch the active panel
  Alt+F1 / Alt+F2               choose a volume for the left / right panel

Selection
  Insert                       tag/untag the current file, move down
  Right-click                  tag/untag a single file
  Right-drag                   tag/untag a range of files
  +                            tag files matching a glob pattern
  -                            untag files matching a glob pattern
  *                            invert the tag selection

Quick Search (in active panel)
  Alt+Shift+<char>             append character to quick-search; timer clears after 1s
  Alt+Shift+Backspace          delete last character from search buffer
  Alt+Shift+Escape             clear search buffer

File Search (panelize results)
  Alt+F7 / Shift+F7            open file search dialog (Alt+F7 again to reopen)
  F3 (in search results)       view file under cursor
  Enter (in search results)    open file/directory, exit search mode
  Escape (in search results)   exit search mode, return to panel

View options (apply to the active panel)
  Ctrl+H                       toggle hidden (dotfile) visibility
  Ctrl+R                       refresh the listing
  Ctrl+F3 / Ctrl+F5 / Ctrl+F6   sort by name / date / size (press again to
                                reverse the order)

File operations (act on tagged files, or the cursor if none are tagged)
  F1   Help           show this cheat-sheet
  F3   View           read-only built-in viewer
  F4   Edit           built-in editor (Ctrl+S/F2 saves)
  F5   Copy           copy to the other panel's directory (or a typed path)
  F6   Move/Rename     move, or rename in place if you type just a new name
  F7   MkDir          create a new directory
  F8   Delete         permanently delete (with confirmation)
  F9   Menu           placeholder menu (includes Command Prompt)
  F10  Quit           quit
  Shift+F3  File Info  file type ('file' command, or a Python guess on
                       Windows) + MD5/SHA1/SHA256 checksums, computed in
                       the background
  Shift+F4  New File   create a new file in the current directory and edit it
  Shift+F5  Compress   create a zip/tar/grp/7z archive, any outer codec

Command line (always visible at the bottom)
  Typing any letter/digit      focuses the command line and starts typing
  Ctrl+X                       focus the command line (clears current text)
  Enter                        run the command in a terminal
  Up/Down                      navigate command history
  Escape                       clear the command line and return focus to panel
"""


class CommanderApp(tk.Tk):
    """The top-level dual-panel orthodox-file-manager window.

    Two `FilePanel`s sit side by side; exactly one is "active" at a time
    (switched with Tab). A bottom F-key bar exposes the classic Norton/
    Midnight Commander command set and is wired to the same handler methods
    as the global F-key bindings, via the `keys.F_KEY_SPECS` table.
    """

    _instance: CommanderApp | None = None

    def __init__(self, left_path: Path | None = None, right_path: Path | None = None) -> None:
        super().__init__()
        CommanderApp._instance = self
        self.title("linux-commander")
        self.geometry("1000x600")

        # Register this root as the master for icon PhotoImage creation,
        # so icons are tied to the correct Tk instance after destroy/recreate.
        from linux_commander.icons import set_tk_master

        set_tk_master(self)

        # Load settings early (font and icons come from settings)
        self._settings = load_settings()

        # Tell theme_manager whether ttkbootstrap is available
        set_ttkbootstrap_available(_HAS_TTKBOOTSTRAP)

        # Apply ttkbootstrap theme before any widgets are created
        self._boot_style = _init_ttkbootstrap_fn(self, self._settings)

        self._local_fs = LocalFileSystem()
        self._mount_manager = MountManager()
        self._session_manager = SessionManager(self._settings, self._local_fs)
        self._search_controller: SearchController | None = None

        # Let the .crp VFS plugin prompt for a password/key when Enter is
        # pressed on an encrypted file -- open_fs(host_fs, path) has no UI
        # parent of its own to show a dialog from.
        from linux_commander.plugins import set_credential_provider

        set_credential_provider(self._crypt_credential_provider)

        left_vpath = self._local_fs.from_path(left_path or Path.cwd())
        right_vpath = self._local_fs.from_path(right_path or Path.cwd())

        self.columnconfigure(0, weight=1, uniform="panels")
        self.columnconfigure(1, weight=1, uniform="panels")
        self.rowconfigure(0, weight=1)

        self.left_panel = FilePanel(
            self,
            left_vpath,
            local_fs=self._local_fs,
            mount_manager=self._mount_manager,
            on_activate_file=self._on_activate_file,
            on_tab=self._switch_active_panel,
            on_activate=lambda: self.activate_panel(self.left_panel),
            on_marks_changed=self._update_status,
            on_directory_changed=self._update_title,
            on_error=self._show_error,
            image_extensions=self._settings.image_extensions,
            show_icons=self._settings.show_icons,
            show_extension=self._settings.show_extension,
            on_network=self._connect_network,
        )
        self.left_panel.grid(row=0, column=0, sticky="nsew")

        self.right_panel = FilePanel(
            self,
            right_vpath,
            local_fs=self._local_fs,
            mount_manager=self._mount_manager,
            on_activate_file=self._on_activate_file,
            on_tab=self._switch_active_panel,
            on_activate=lambda: self.activate_panel(self.right_panel),
            on_marks_changed=self._update_status,
            on_directory_changed=self._update_title,
            on_error=self._show_error,
            image_extensions=self._settings.image_extensions,
            show_icons=self._settings.show_icons,
            show_extension=self._settings.show_extension,
            on_network=self._connect_network,
        )
        self.right_panel.grid(row=0, column=1, sticky="nsew")

        # Initialize operations controller after panels are created
        self._ops = OperationsController(
            self,
            self._settings,
            self._local_fs,
            self._mount_manager,
            self.left_panel,
            self.right_panel,
            lambda: self.active_panel,
            self._other_panel,
            self._update_status,
        )

        self.status_var = tk.StringVar(value="")
        status_label = ttk.Label(
            self,
            textvariable=self.status_var,
            anchor="w",
            relief="sunken",
            padding=(4, 1),
            style="Status.TLabel",
        )
        status_label.grid(row=1, column=0, columnspan=2, sticky="ew")

        self._build_fkey_bar()
        self._build_command_prompt()
        self._build_menu_bar()

        # Apply font AFTER panels are created so it overrides _ensure_style's
        # initial TkFixedFont defaults.
        self._apply_font_settings()

        self.active_panel = self.left_panel
        self._update_active_panel_style()

        # Restore per-panel session state (paths, marks, sort, active side)
        self._restore_session_state()

        self._update_status()
        self._update_title()

        self._bind_global_keys()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    @classmethod
    def get_app(cls) -> CommanderApp | None:
        """Return the singleton CommanderApp instance, if created."""
        return cls._instance

    # -- ttkbootstrap theme --------------------------------------------------

    def _init_ttkbootstrap(self):
        return _init_ttkbootstrap_fn(self, self._settings)

    def _apply_theme(self, theme_name: str) -> None:
        """Switch to *theme_name*, re-apply custom panel styles, then fonts."""
        _apply_theme_fn(self._boot_style, theme_name, self._settings, self._apply_font_settings)

    def cmd_theme(self) -> None:
        """Theme picker: two columns (dark / light) with live preview."""
        _show_theme_picker_fn(self, self._settings, self._apply_theme)

    # -- F-key bar -----------------------------------------------------------

    def _build_fkey_bar(self) -> None:
        self._fkey_bar = FKeyBar(self, F_KEY_SPECS, self._dispatch)

    def _build_command_prompt(self) -> None:
        self._cmd_prompt = CommandPrompt(
            self,
            on_execute=self._execute_command,
            on_focus_return=self._return_focus_to_panel,
        )

    def _execute_command(self, cmd: str) -> None:
        """Execute a shell command in a terminal, in the active panel's directory."""
        import subprocess
        import sys

        if sys.platform == "win32":
            terminal_cmd = self._settings.terminal_command_windows
        else:
            terminal_cmd = self._settings.terminal_command_linux

        # Replace {cmd} placeholder
        full_cmd = terminal_cmd.format(cmd=cmd)

        # Run in the active panel's real directory, when it has one (local
        # filesystems only — FTP/archive panels fall back to the default cwd).
        cwd: str | None = None
        current = self.active_panel.current_path
        real = current.fs.realpath(current)
        if real is not None and real.is_dir():
            cwd = str(real)

        try:
            subprocess.Popen(full_cmd, shell=True, cwd=cwd)
        except OSError as exc:
            self._show_error(f"Failed to execute command: {exc}")

    def _apply_font_settings(self) -> None:
        """Apply font settings from self._settings to all styled widgets."""
        _apply_font_settings_fn(self, self._settings, self.left_panel, self.right_panel)

    def _on_close(self) -> None:
        """Handle window close: save settings and exit (no confirmation)."""
        self._tear_down()

    def _tear_down(self) -> None:
        """Save state, clean up mounts/temps, and destroy the window."""
        self._save_settings()
        from linux_commander.plugins import cleanup_all_temps

        self._mount_manager.close_all()
        cleanup_all_temps()
        self.destroy()

    def _save_settings(self) -> None:
        """Persist current settings to disk."""
        self._session_manager.save(self.left_panel, self.right_panel, self.active_panel)

    def _restore_session_state(self) -> None:
        """Restore per-panel paths, marks, sort, and active side from settings."""
        self._session_manager.restore(
            self.left_panel,
            self.right_panel,
            lambda panel: setattr(self, "active_panel", panel),
            self._update_active_panel_style,
        )

    def _build_menu_bar(self) -> None:
        from linux_commander.menu_bar import MenuBar

        self.config(menu=MenuBar.build(self, self))

    def _run_file_operation(self, op: object) -> None:
        """Run a ``FileOperation`` on the active panel's selected files."""
        from linux_commander.file_ops import FileOperation

        assert isinstance(op, FileOperation)
        panel = self.active_panel
        entries = panel.selected_entries()
        if not entries:
            dialogs.error(self, "No files selected.", title=op.name)
            return
        sources = [e.path for e in entries]
        dest_dir = panel.current_path
        if not isinstance(dest_dir.fs, WritableFileSystem):
            dialogs.error(
                self,
                "The current directory is on a read-only filesystem.",
                title=op.name,
            )
            return
        params: dict = {}
        if op.prepare is not None:
            result = op.prepare(self, sources)
            if result is None:
                return  # user cancelled
            params = result

        def work(on_progress, should_cancel):
            return op.run(sources, dest_dir, on_progress, should_cancel, **params)

        errors = dialogs.run_with_progress(self, f"{op.name}…", work)
        self._refresh_both_panels()
        self._report_errors(errors, op.name)

    def _compare_selected_files(self) -> None:
        """Compare two selected files (one from each panel or two from same panel)."""
        # Collect selected files from both panels
        left_sel = self.left_panel.selected_entries()
        right_sel = self.right_panel.selected_entries()

        all_selected = left_sel + right_sel
        if len(all_selected) < 2:
            dialogs.error(self, "Select exactly two files to compare.", title="Compare Files")
            return
        if len(all_selected) > 2:
            dialogs.error(
                self,
                f"Select exactly two files to compare (found {len(all_selected)}).",
                title="Compare Files",
            )
            return

        path_a = all_selected[0].path
        path_b = all_selected[1].path

        # Check both are files (not directories)
        try:
            stat_a = path_a.fs.stat(path_a)
            stat_b = path_b.fs.stat(path_b)
            if stat_a.is_dir or stat_b.is_dir:
                dialogs.error(
                    self, "Both selections must be files, not directories.", title="Compare Files"
                )
                return
        except OSError as e:
            dialogs.error(self, f"Cannot access selected files: {e}", title="Compare Files")
            return

        show_diff_viewer(self, path_a, path_b)

    def _compare_directories(self) -> None:
        """Compare the two panel directories."""
        compare_directories(self, self.left_panel.current_path, self.right_panel.current_path)

    def _toggle_icons(self) -> None:
        """Toggle file-type icons on both panels in sync and persist the setting."""
        for panel in (self.left_panel, self.right_panel):
            panel.toggle_icons()
        # Panels are now in sync; read the state from one of them
        self._settings.show_icons = self.left_panel.show_icons

    def _toggle_extension_column(self) -> None:
        """Toggle the Extension column on both panels in sync and persist it."""
        for panel in (self.left_panel, self.right_panel):
            panel.toggle_extension_column()
        # Panels are now in sync; read the state from one of them
        self._settings.show_extension = self.left_panel.show_extension

    def _toggle_flat_view(self) -> None:
        """Toggle flat view on both panels in sync."""
        for panel in (self.left_panel, self.right_panel):
            panel.toggle_flat_view()

    def cmd_ftp_connections(self) -> None:
        """Open the remote connections manager dialog (FTP/SFTP/Jottacloud)."""
        show_remote_connections(
            self,
            self._settings,
            self._mount_manager,
            lambda: self.active_panel,
        )

    def cmd_optional_dependencies(self) -> None:
        """Report which optional-dependency extras are installed."""
        from linux_commander.install_extras import (
            format_report,
            is_zstd_available,
            load_extras,
            probe_extras,
        )

        probed = probe_extras(load_extras())
        report = format_report(probed, is_zstd_available())
        report += (
            "\n\nRun 'uv run linux-commander-install-extras --install' in a terminal "
            "to install missing extras."
        )
        dialogs.show_text(self, "Optional Dependencies", report)

    def cmd_plugin_status(self) -> None:
        """Show the plugin load status dialog."""
        from linux_commander.dialogs import show_plugin_status

        show_plugin_status(self)

    def _crypt_credential_provider(self, name: str) -> tuple[str | None, StoredKey | None] | None:
        """Prompt for a password or stored key to decrypt an encrypted (.crp) file.

        Registered with ``plugins.set_credential_provider`` in ``__init__``;
        called by ``plugins/crypt_plugin.py``'s ``open_fs`` when Enter is
        pressed on a ``.crp`` file (a VFS plugin has no UI parent of its own
        to show a dialog from). Reuses the same modal as the
        Operations > Encrypt/Decrypt menu item.
        """
        from linux_commander.file_ops.crypt_op import _credential_dialog
        from linux_commander.settings import load_settings

        result = _credential_dialog(self, f"Decrypt '{name}'")
        if result is None:
            return None
        key_name = result.get("key_name")
        if key_name:
            settings = load_settings()
            stored_key = next((sk for sk in settings.stored_keys if sk.name == key_name), None)
            if stored_key is None:
                dialogs.error(self, f"Stored key {key_name!r} not found.", title="Decrypt")
                return None
            return None, stored_key
        return result.get("password"), None

    def _bind_global_keys(self) -> None:
        for spec in F_KEY_SPECS:
            if spec is None:
                continue
            self.bind_all(f"<{spec.key}>", lambda event, s=spec: self._dispatch(s))  # type: ignore[misc]
        self.bind_all("<Alt-F1>", lambda event: self._choose_volume(self.left_panel))
        self.bind_all("<Alt-F2>", lambda event: self._choose_volume(self.right_panel))
        self.bind_all("<Control-h>", lambda event: self.active_panel.toggle_hidden())
        self.bind_all(
            "<Control-r>", lambda event: self._refresh_panel_preserving_position(self.active_panel)
        )
        self.bind_all("<Control-F3>", lambda event: self.active_panel.set_sort("name"))
        self.bind_all("<Control-F5>", lambda event: self.active_panel.set_sort("mtime"))
        self.bind_all("<Control-F6>", lambda event: self.active_panel.set_sort("size"))
        self.bind_all("<Control-q>", lambda event: self.cmd_quit())
        # Hotlist: Ctrl+Backslash (like Midnight Commander)
        self.bind_all("<Control-backslash>", lambda event: self._show_hotlist())
        # Directory history: Alt+Left / Alt+Right for back/forward
        self.bind_all("<Alt-Left>", lambda event: self._navigate_history("back"))
        self.bind_all("<Alt-Right>", lambda event: self._navigate_history("forward"))
        # Flat view toggle: Ctrl+Shift+F
        self.bind_all("<Control-Shift-f>", lambda event: self.active_panel.toggle_flat_view())
        # Command prompt: Ctrl+X
        self.bind_all("<Control-x>", lambda event: self._show_command_prompt())
        # File Info: Shift+F3
        self.bind_all("<Shift-F3>", lambda event: self.cmd_file_info())
        # New file: Shift+F4
        self.bind_all("<Shift-F4>", lambda event: self.cmd_new_file())
        # Compression: Shift+F5
        self.bind_all("<Shift-F5>", lambda event: self.cmd_compress())
        # Search: Alt+F7 / Shift+F7 (Total Commander convention)
        self.bind_all("<Alt-F7>", lambda event: self.cmd_search())
        self.bind_all("<Shift-F7>", lambda event: self.cmd_search())
        # Escape exits search mode if in search mode
        self.bind_all("<Escape>", lambda event: self._maybe_exit_search_mode())
        # Typing a bare character in either panel starts a new command
        for panel in (self.left_panel, self.right_panel):
            panel._tree.bind("<Key>", self._on_panel_key_to_cmd, add="+")

    def _maybe_exit_search_mode(self) -> None:
        """Exit search mode if the active panel is in search mode."""
        if self.active_panel._search_mode:
            self.stop_search()
            self.active_panel.exit_search_mode()

    def _navigate_history(self, direction: str) -> None:
        """Navigate directory history (back/forward) in the active panel."""
        panel = self.active_panel
        if direction == "back":
            panel.go_back()
        elif direction == "forward":
            panel.go_forward()

    def _on_panel_key_to_cmd(self, event: tk.Event) -> str | None:
        """Route unmodified printable keys typed in a panel to the command entry."""
        char = event.char
        if not char or not char.isprintable():
            return None
        # +, -, * are handled by panel-level bindings for mark/unmark
        if char in ("+", "-", "*"):
            return None
        # Ctrl or Alt modifier — leave to other handlers (quick-search uses Alt+Shift)
        state = int(event.state)  # type: ignore[arg-type]
        if state & 0x4 or state & 0x8:
            return None
        self._cmd_prompt.focus_and_set(char)
        return "break"

    def _show_command_prompt(self) -> None:
        """Focus the always-visible command prompt at the bottom."""
        self._cmd_prompt.focus_and_clear()

    def _return_focus_to_panel(self) -> None:
        """Return focus to the active panel's tree."""
        self.active_panel._tree.focus_set()

    def _dispatch(self, spec: FKeySpec) -> None:
        handler = getattr(self, spec.handler_name, None)
        if handler is not None:
            handler()

    def _choose_volume(self, panel: FilePanel) -> None:
        """Alt+F1/Alt+F2: pop a volume chooser and load the selection into
        `panel` (always the same fixed panel, regardless of which is active —
        classic Norton/Total Commander convention)."""
        volume_list = volumes.list_volumes()
        labels = [f"{v.label}  ({v.path})" for v in volume_list]
        labels.append("Network...")
        labels.append("Connect to Server...")
        index = dialogs.choose_from_list(self, "Choose volume", labels)
        if index is None:
            return
        if index == len(volume_list):  # Network...
            self._connect_network()
        elif index == len(volume_list) + 1:  # Connect to Server...
            self._connect_server()
        else:
            panel.load(self._local_fs.from_path(volume_list[index].path))

    def _connect_network(self) -> None:
        """Open the saved connections dialog (Network...)."""
        from linux_commander.ftp_dialog import show_remote_connections

        show_remote_connections(
            self, self._settings, self._mount_manager, lambda: self.active_panel
        )

    def _connect_server(self) -> None:
        """Prompt for a network URL and connect the panel to it (Connect to Server...)."""
        protocols = ["ftp", "sftp", "smb", "webdav", "webdavs", "jotta"]
        protocol_idx = dialogs.choose_from_list(
            self, "Select Protocol", [p.upper() for p in protocols]
        )
        if protocol_idx is None:
            return
        protocol = protocols[protocol_idx].lower()

        # Build initial URL based on protocol
        initial = f"{protocol}://"
        if protocol in ("smb",):
            initial += "host/share"
        elif protocol in ("webdav", "webdavs"):
            initial += "host[:port]/path"
        else:
            initial += "host[:port]/path"

        url = dialogs.prompt(
            self,
            title=f"Connect to {protocol.upper()}",
            message=f"{protocol.upper()} URL ({protocol}://[user:pass@]host[:port][/path]):",
            initial=initial,
        )
        if not url:
            return
        try:
            from linux_commander.plugins import plugin_for_scheme

            plugin = plugin_for_scheme(protocol)
            if plugin is None:
                dialogs.error(
                    self,
                    f"{protocol.upper()} plugin not available.",
                    title=f"{protocol.upper()} Error",
                )
                return
            fs = plugin.connect_fs(url)
        except OSError as exc:
            dialogs.error(
                self,
                f"Cannot connect to {protocol.upper()} server:\n{exc}",
                title="Connection Error",
            )
            return
        root = self._mount_manager.mount_scheme_fs(fs)
        self.active_panel.load(root)

    # -- active panel switching ----------------------------------------------

    def _switch_active_panel(self) -> None:
        self.active_panel = (
            self.right_panel if self.active_panel is self.left_panel else self.left_panel
        )
        self._update_active_panel_style()
        self._update_status()
        self._update_title()

    def activate_panel(self, panel: FilePanel) -> None:
        """Activate a specific panel (called when clicking on it)."""
        if self.active_panel is not panel:
            self.active_panel = panel
            self._update_active_panel_style()
            self._update_status()
            self._update_title()

    def _update_active_panel_style(self) -> None:
        self.left_panel.set_active(self.active_panel is self.left_panel)
        self.right_panel.set_active(self.active_panel is self.right_panel)

    def _update_title(self) -> None:
        """Show the active panel's current directory in the window title.

        Guarded with getattr because FilePanel.load() (called during each
        panel's own construction) fires this callback before self.active_panel
        is assigned; it's a no-op until then, and set explicitly once both
        panels exist (see __init__).
        """
        panel = getattr(self, "active_panel", None)
        if panel is None:
            return
        self.title(f"linux-commander - {panel.current_path}")

    def _show_error(self, message: str) -> None:
        dialogs.error(self, message, title="Error")

    def _other_panel(self) -> FilePanel:
        return self.right_panel if self.active_panel is self.left_panel else self.left_panel

    def _update_status(self) -> None:
        """Show the active panel's tagged count/size in the status line, if any
        files are marked; otherwise clear it."""
        marked = self.active_panel.marked_entries()
        if marked:
            total_size = sum(entry.size for entry in marked)
            self.status_var.set(f"{len(marked)} marked ({format_size(total_size)})")
        else:
            self.status_var.set("")

    def _on_activate_file(self, entry: FileEntry) -> None:
        self._ops._on_activate_file(entry)

    # -- refresh helpers -------------------------------------------------------

    def _refresh_panel_preserving_position(self, panel: FilePanel) -> None:
        """Reload `panel`'s current directory, keeping the cursor at the same
        row index if possible (a "sensible" row after copy/move/delete)."""
        self._ops._refresh_panel_preserving_position(panel)

    def _refresh_both_panels(self) -> None:
        self._ops._refresh_both_panels()

    def _report_errors(
        self, errors: list[operations.OperationError], verb: str = "Operation"
    ) -> None:
        self._ops._report_errors(errors, verb)

    # -- F-key command handlers -----------------------------------------------

    def _show_hotlist(self) -> None:
        """Show the hotlist/bookmarks dialog (Ctrl+Backslash)."""
        from linux_commander.hotlist_dialog import show_hotlist

        show_hotlist(self, self.active_panel, self._other_panel())

    def cmd_help(self) -> None:
        dialogs.show_text(self, "Help", HELP_TEXT)

    def cmd_view(self) -> None:
        entry = self.active_panel.cursor_entry()
        if entry is None or entry.is_parent:
            return
        if entry.is_dir:
            self.active_panel.load(entry.path)
            return
        # Check if it's an image file
        panel = self.active_panel
        if panel._image_extensions and entry.name.lower().endswith(tuple(panel._image_extensions)):
            # Collect all image files in current directory for navigation
            image_files = [
                e.path
                for e in panel._entries
                if not e.is_parent
                and not e.is_dir
                and e.name.lower().endswith(tuple(panel._image_extensions))
            ]
            if image_files:
                start_index = next((i for i, p in enumerate(image_files) if p == entry.path), 0)
                viewer.view_image(
                    self,
                    entry.path,
                    image_files,
                    start_index,
                    list(panel._image_extensions),
                    self._settings,
                )
                return
        viewer.view_file(self, entry.path, self._settings)

    def cmd_edit(self) -> None:
        entry = self.active_panel.cursor_entry()
        if entry is None or entry.is_parent or entry.is_dir:
            return
        panel = self.active_panel
        if not isinstance(entry.path.fs, WritableFileSystem):
            viewer.view_file(self, entry.path, self._settings)
            return
        viewer.edit_file(
            self,
            entry.path,
            on_saved=lambda: self._refresh_panel_preserving_position(panel),
            settings=self._settings,
        )

    def cmd_file_info(self) -> None:
        """Show file type + checksums for the cursor file (Shift+F3)."""
        self._ops.cmd_file_info()

    def cmd_new_file(self) -> None:
        """Shift+F4: create a new file in the active panel's directory and edit it."""
        self._ops.cmd_new_file()

    def cmd_copy(self) -> None:
        self._ops.cmd_copy()

    def cmd_move(self) -> None:
        self._ops.cmd_move()

    def cmd_compress(self) -> None:
        """Compress selected files to an archive (Shift+F5)."""
        self._ops.cmd_compress()

    # -- Search ---------------------------------------------------------------

    def cmd_search(self) -> None:
        """Open the search dialog (Alt+F7 / Shift+F7)."""
        panel = self.active_panel
        dialog = SearchDialog(self, self, panel.current_path)
        # Dialog is non-modal; SearchDialog calls start_search when user clicks Search.
        # Store a reference to prevent GC.
        self._search_dialog = dialog

    def start_search(
        self, criteria: SearchCriteria, summary: str, on_done: Callable[[], None]
    ) -> None:
        """Start a background search with the given criteria."""
        from linux_commander.search_controller import SearchController

        panel = self.active_panel
        self._search_controller = SearchController(self, panel)
        self._search_controller.start(criteria, summary, on_done)

    def stop_search(self) -> None:
        """Signal the active background search (if any) to stop."""
        if self._search_controller is not None:
            self._search_controller.cancel()

    # -- copy/move ------------------------------------------------------------

    def cmd_mkdir(self) -> None:
        self._ops.cmd_mkdir()

    def cmd_delete(self) -> None:
        self._ops.cmd_delete()

    def cmd_menu(self) -> None:
        menu = tk.Menu(self, tearoff=False)
        for label in ("Left", "Files", "Commands", "Options", "Right"):
            menu.add_command(label=label, state="disabled")
        menu.add_separator()
        menu.add_command(label="Search...", accelerator="Alt+F7", command=self.cmd_search)
        menu.add_separator()
        menu.add_command(
            label="Command Prompt", accelerator="Ctrl+X", command=self._show_command_prompt
        )
        menu.add_command(
            label="(placeholder - full menu system not yet implemented)", state="disabled"
        )
        try:
            menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())
        finally:
            menu.grab_release()

    # -- font dialogs --------------------------------------------------------

    def cmd_font(self) -> None:
        """Font selection dialog for the main application panels (live preview)."""
        _show_font_picker_fn(self, self._settings, self._apply_font_settings)

    def cmd_editor_font(self) -> None:
        """Font selection dialog for the editor."""
        _show_font_dialog_fn(
            self, self._settings, "Editor Font", "editor_font_family", "editor_font_size"
        )

    def cmd_viewer_font(self) -> None:
        """Font selection dialog for the viewer."""
        _show_font_dialog_fn(
            self, self._settings, "Viewer Font", "viewer_font_family", "viewer_font_size"
        )

    # -- command settings dialog ---------------------------------------------

    def cmd_command_settings(self) -> None:
        """Dialog to configure the terminal command templates used by Ctrl+X."""
        dialog = tk.Toplevel(self)
        dialog.title("Command Settings")
        dialog.transient(self)
        dialog.resizable(True, False)

        info = ttk.Label(
            dialog,
            text="Use {cmd} as a placeholder for the command to run.",
            padding=(8, 4),
        )
        info.grid(row=0, column=0, columnspan=2, padx=8, pady=(8, 0), sticky="w")

        ttk.Label(dialog, text="Linux:").grid(row=1, column=0, padx=8, pady=6, sticky="w")
        linux_var = tk.StringVar(value=self._settings.terminal_command_linux)
        linux_entry = ttk.Entry(dialog, textvariable=linux_var, width=55)
        linux_entry.grid(row=1, column=1, padx=8, pady=6, sticky="ew")

        ttk.Label(dialog, text="Windows:").grid(row=2, column=0, padx=8, pady=6, sticky="w")
        win_var = tk.StringVar(value=self._settings.terminal_command_windows)
        win_entry = ttk.Entry(dialog, textvariable=win_var, width=55)
        win_entry.grid(row=2, column=1, padx=8, pady=6, sticky="ew")

        dialog.columnconfigure(1, weight=1)

        def apply() -> None:
            self._settings.terminal_command_linux = linux_var.get()
            self._settings.terminal_command_windows = win_var.get()
            dialog.destroy()

        btn_frame = ttk.Frame(dialog)
        btn_frame.grid(row=3, column=0, columnspan=2, pady=8)
        ttk.Button(btn_frame, text="OK", command=apply).pack(side="right", padx=4)
        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side="right", padx=4)

        dialogs._center_over(dialog, self)
        dialog.grab_set()
        linux_entry.focus_set()
        dialog.wait_window()

    # -- quit (no confirmation) ----------------------------------------------

    def cmd_quit(self) -> None:
        """Save state and exit immediately — no confirmation dialog."""
        self._tear_down()


def main() -> None:
    """Entry point for the `linux-commander` script and `python -m linux_commander`."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="linux-commander",
        description="A dual-pane orthodox file manager.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        metavar="PATH",
        help="Initial directory for the panels (left, right). "
        "If only one path is given, the right panel uses the current directory.",
    )
    args = parser.parse_args()

    left_path = Path(args.paths[0]) if len(args.paths) >= 1 else None
    right_path = Path(args.paths[1]) if len(args.paths) >= 2 else None

    app = CommanderApp(left_path=left_path, right_path=right_path)
    app.mainloop()
