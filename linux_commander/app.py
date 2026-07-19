"""CommanderApp: the dual-panel application shell and entry point."""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from collections.abc import Callable
from pathlib import Path
from tkinter import ttk
from typing import TYPE_CHECKING

try:
    import ttkbootstrap as _ttkbs  # noqa: F401

    _HAS_TTKBOOTSTRAP = True
except ImportError:
    _HAS_TTKBOOTSTRAP = False

from linux_commander import dialogs, operations, platform_util, viewer, volumes
from linux_commander.columns_dialog import show_columns_dialog
from linux_commander.diff_viewer import compare_directories, show_diff_viewer
from linux_commander.fs import format_size
from linux_commander.ftp_dialog import show_remote_connections
from linux_commander.keys import F_KEY_SPECS, FKeySpec
from linux_commander.operations import CancelPredicate, OperationError, ProgressCallback
from linux_commander.panel import FilePanel
from linux_commander.search_dialog import SearchDialog
from linux_commander.search_engine import SearchCriteria
from linux_commander.settings import StoredKey, load_settings, save_settings
from linux_commander.vfs import FileEntry, LocalFileSystem, MountManager

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

        # Load settings early (font and icons come from settings)
        self._settings = load_settings()

        # Apply ttkbootstrap theme before any widgets are created
        self._boot_style = self._init_ttkbootstrap()

        self._local_fs = LocalFileSystem()
        self._mount_manager = MountManager()
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
        if not _HAS_TTKBOOTSTRAP:
            return None
        try:
            import ttkbootstrap as tb
            from ttkbootstrap.window import apply_all_bindings

            # ttkbootstrap.Style.theme_use() restyles every live widget it
            # knows about via a global Publisher/subscriber registry that
            # every ttk.Combobox auto-joins. Widgets are normally removed
            # from that registry by a <Destroy> binding that
            # ttkbootstrap.window.Window/Toplevel install automatically --
            # but this app subclasses plain tk.Tk (not ttkbootstrap's
            # Window), so that cleanup binding was never wired up. Every
            # closed dialog containing a Combobox (compression dialog,
            # connections manager, search dialog, the viewer's font
            # picker, ...) left a stale widget reference behind, and the
            # next theme_use() call crashed with `_tkinter.TclError: bad
            # window path name` the moment it reached one. apply_all_bindings
            # installs the same <Destroy>-triggered unsubscribe (plus a
            # <Map> convenience binding) that Window's own __init__ sets up.
            apply_all_bindings(self)

            theme = self._settings.theme or "darkly"
            return tb.Style(theme=theme)
        except Exception:
            return None

    def _apply_theme(self, theme_name: str) -> None:
        """Switch to *theme_name*, re-apply custom panel styles, then fonts."""
        if self._boot_style is None:
            return
        self._settings.theme = theme_name
        self._boot_style.theme_use(theme_name)
        from linux_commander.panel import reset_style

        reset_style()
        self._apply_font_settings()

    def cmd_theme(self) -> None:
        """Theme picker: two columns (dark / light) with live preview."""
        if self._boot_style is None:
            dialogs.error(
                self,
                "ttkbootstrap is not installed.\n\nRun: pip install ttkbootstrap",
                title="Theme",
            )
            return

        # Classify themes as dark or light using ttkbootstrap's own metadata
        all_themes = sorted(self._boot_style.theme_names())
        dark: list[str] = []
        light: list[str] = []
        for name in all_themes:
            try:
                self._boot_style.theme_use(name)
                t = self._boot_style.theme.type
            except Exception:
                t = "light"
            (dark if t == "dark" else light).append(name)
        # Restore current theme after the classification loop
        self._boot_style.theme_use(self._settings.theme)

        saved_theme = self._settings.theme

        dialog = tk.Toplevel(self)
        dialog.title("Theme")
        dialog.transient(self)
        dialog.resizable(False, False)

        ttk.Label(dialog, text="Dark themes").grid(
            row=0, column=0, padx=(12, 6), pady=(10, 2), sticky="w"
        )
        ttk.Label(dialog, text="Light themes").grid(
            row=0, column=1, padx=(6, 12), pady=(10, 2), sticky="w"
        )

        dark_lb = tk.Listbox(
            dialog,
            selectmode="single",
            activestyle="dotbox",
            exportselection=False,
            width=16,
            height=max(len(dark), len(light)),
        )
        for t in dark:
            dark_lb.insert(tk.END, t)
        dark_lb.grid(row=1, column=0, padx=(12, 6), pady=(0, 8), sticky="ns")

        light_lb = tk.Listbox(
            dialog,
            selectmode="single",
            activestyle="dotbox",
            exportselection=False,
            width=16,
            height=max(len(dark), len(light)),
        )
        for t in light:
            light_lb.insert(tk.END, t)
        light_lb.grid(row=1, column=1, padx=(6, 12), pady=(0, 8), sticky="ns")

        def _pick_dark(event=None):
            sel = dark_lb.curselection()
            if not sel:
                return
            light_lb.selection_clear(0, tk.END)
            self._apply_theme(dark[sel[0]])

        def _pick_light(event=None):
            sel = light_lb.curselection()
            if not sel:
                return
            dark_lb.selection_clear(0, tk.END)
            self._apply_theme(light[sel[0]])

        dark_lb.bind("<<ListboxSelect>>", _pick_dark)
        light_lb.bind("<<ListboxSelect>>", _pick_light)

        # Pre-select the active theme
        cur = self._settings.theme
        if cur in dark:
            idx = dark.index(cur)
            dark_lb.selection_set(idx)
            dark_lb.see(idx)
        elif cur in light:
            idx = light.index(cur)
            light_lb.selection_set(idx)
            light_lb.see(idx)

        def _apply():
            dialog.destroy()

        def _cancel():
            self._apply_theme(saved_theme)
            dialog.destroy()

        btn_frame = ttk.Frame(dialog)
        btn_frame.grid(row=2, column=0, columnspan=2, pady=8)
        ttk.Button(btn_frame, text="OK", command=_apply).pack(side="right", padx=4)
        ttk.Button(btn_frame, text="Cancel", command=_cancel).pack(side="right", padx=4)

        dialog.protocol("WM_DELETE_WINDOW", _cancel)
        dialogs._center_over(dialog, self)
        dialog.grab_set()
        dialog.wait_window()

    # -- F-key bar -----------------------------------------------------------

    def _build_fkey_bar(self) -> None:
        bar = ttk.Frame(self)
        bar.grid(row=3, column=0, columnspan=2, sticky="ew")
        for index, spec in enumerate(F_KEY_SPECS):
            bar.columnconfigure(index, weight=1)
            if spec is None:
                ttk.Label(bar, text="").grid(row=0, column=index, sticky="ew")
                continue
            text = f"{spec.key} {spec.label}"
            button = ttk.Button(
                bar,
                text=text,
                style="FKey.TButton",
                command=lambda s=spec: self._dispatch(s),  # type: ignore[misc]
            )
            button.grid(row=0, column=index, sticky="ew", padx=2, pady=2)
        self._fkey_bar = bar

    def _build_command_prompt(self) -> None:
        """Build the command prompt bar at the bottom of the window."""
        self._cmd_frame = ttk.Frame(self)
        self._cmd_frame.grid(row=2, column=0, columnspan=2, sticky="ew")
        self._cmd_frame.columnconfigure(1, weight=1)

        # Prompt label
        self._cmd_prompt_var = tk.StringVar(value="$")
        prompt_label = ttk.Label(
            self._cmd_frame,
            textvariable=self._cmd_prompt_var,
            anchor="w",
            padding=(4, 2),
            style="CmdPrompt.TLabel",
        )
        prompt_label.grid(row=0, column=0, sticky="w")

        # Command entry
        self._cmd_var = tk.StringVar()
        self._cmd_entry = ttk.Entry(self._cmd_frame, textvariable=self._cmd_var, style="Cmd.TEntry")
        self._cmd_entry.grid(row=0, column=1, sticky="ew", padx=(0, 4), pady=2)

        # Command history
        self._cmd_history: list[str] = []
        self._cmd_history_index: int = -1

        # Bind keys
        self._cmd_entry.bind("<Return>", self._on_command_enter)
        self._cmd_entry.bind("<Up>", self._on_command_history_up)
        self._cmd_entry.bind("<Down>", self._on_command_history_down)
        self._cmd_entry.bind("<Escape>", self._on_command_escape)

    def _on_command_enter(self, event: tk.Event) -> str:
        """Execute the command entered in the command prompt."""
        cmd = self._cmd_var.get().strip()
        if not cmd:
            self.active_panel._tree.focus_set()
            return "break"

        # Add to history
        if self._cmd_history and self._cmd_history[-1] == cmd:
            self._cmd_history.pop()
        self._cmd_history.append(cmd)
        self._cmd_history_index = len(self._cmd_history)

        # Clear entry and return focus to panel
        self._cmd_var.set("")
        self.active_panel._tree.focus_set()

        # Execute command
        self._execute_command(cmd)
        return "break"

    def _on_command_escape(self, event: tk.Event) -> str:
        """Clear the command entry and return focus to the active panel."""
        self._cmd_var.set("")
        self.active_panel._tree.focus_set()
        return "break"

    def _on_command_history_up(self, event: tk.Event) -> str:
        """Navigate up in command history."""
        if not self._cmd_history:
            return "break"
        if self._cmd_history_index > 0:
            self._cmd_history_index -= 1
            self._cmd_var.set(self._cmd_history[self._cmd_history_index])
        return "break"

    def _on_command_history_down(self, event: tk.Event) -> str:
        """Navigate down in command history."""
        if not self._cmd_history or self._cmd_history_index >= len(self._cmd_history) - 1:
            self._cmd_var.set("")
            self._cmd_history_index = len(self._cmd_history)
            return "break"
        self._cmd_history_index += 1
        self._cmd_var.set(self._cmd_history[self._cmd_history_index])
        return "break"

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
        """Apply font settings from self._settings to all styled widgets.

        Everything goes through ``ttk.Style`` — direct ``.configure(font=...)``
        on ttk widgets raises ``TclError: unknown option "-font"``.

        Named Tk aliases such as "TkFixedFont" are NOT real family names; passing
        them to ``tkfont.Font(family=...)`` silently resolves to the wrong family
        (e.g. "Noto Sans" instead of "Noto Sans Mono").  We resolve them via
        ``nametofont`` first.  The Font object is stored as ``self._panel_font``
        to prevent Python from garbage-collecting it while Tk still references it.
        """
        family = self._settings.font_family
        size = self._settings.font_size
        _tk_aliases = (
            "TkFixedFont",
            "TkDefaultFont",
            "TkTextFont",
            "TkHeadingFont",
            "TkCaptionFont",
            "TkSmallCaptionFont",
            "TkIconFont",
            "TkTooltipFont",
        )
        if family in _tk_aliases:
            base = tkfont.nametofont(family)
            actual = base.actual()
            family = actual["family"]
            weight = actual.get("weight", "normal")
        else:
            weight = "normal"
        # Store as instance attribute — tkfont.Font.__del__ deletes the Tk font
        # resource, so a local variable that gets GC'd would break the style.
        self._panel_font = tkfont.Font(family=family, size=size, weight=weight)
        font = self._panel_font
        row_h = font.metrics("linespace") + 4
        style = ttk.Style()

        # Panel Treeview — both active and inactive variants
        for prefix in ("", "Active.", "Inactive."):
            style.configure(f"{prefix}FilePanel.Treeview", font=font, rowheight=row_h, indent=2)
            style.configure(f"{prefix}FilePanel.Treeview.Heading", font=font)

        # Panel header labels
        style.configure("PanelHeader.TLabel", font=font)
        style.configure("ActivePanelHeader.TLabel", font=font)

        # F-key bar and volume-bar buttons
        style.configure("FKey.TButton", font=font)
        style.configure("Volume.TButton", font=font)

        # Status bar and command-prompt label
        style.configure("Status.TLabel", font=font)
        style.configure("CmdPrompt.TLabel", font=font)

        # Command entry (keep monospace regardless of panel font)
        fixed = tkfont.nametofont("TkFixedFont")
        style.configure("Cmd.TEntry", font=fixed)

        # Update the marked-tag bold font and reload each panel so that existing
        # rows are re-inserted with the new rowheight (Treeview only applies
        # rowheight to newly inserted rows, not to already-rendered ones).
        for panel in (getattr(self, "left_panel", None), getattr(self, "right_panel", None)):
            if panel is not None:
                panel.update_font(font)
                prev = panel.current_index()
                panel.load(panel.current_path)
                if prev is not None:
                    panel.select_index(prev)

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
        s = self._settings
        # Active side
        s.active_side = "right" if self.active_panel is self.right_panel else "left"
        # Per-panel state
        for panel, path_attr, marks_attr, sk_attr, sr_attr, sh_attr in [
            (
                self.left_panel,
                "left_path",
                "left_marks",
                "left_sort_key",
                "left_sort_reverse",
                "left_show_hidden",
            ),
            (
                self.right_panel,
                "right_path",
                "right_marks",
                "right_sort_key",
                "right_sort_reverse",
                "right_show_hidden",
            ),
        ]:
            if isinstance(panel.current_path.fs, LocalFileSystem):
                real = panel.current_path.fs._to_path(panel.current_path)
                setattr(s, path_attr, str(real))
                setattr(s, marks_attr, [e.name for e in panel.marked_entries()])
            else:
                # FTP / archive — not reliably restorable, skip
                setattr(s, path_attr, "")
                setattr(s, marks_attr, [])
            setattr(s, sk_attr, panel.sort_key)
            setattr(s, sr_attr, panel.sort_reverse)
            setattr(s, sh_attr, panel.show_hidden)
        # Legacy single-panel fields (kept for forward-compat)
        s.show_hidden = self.active_panel.show_hidden
        s.sort_key = self.active_panel.sort_key
        s.sort_reverse = self.active_panel.sort_reverse
        s.selection_patterns = self.active_panel._pattern_history
        save_settings(s)

    def _restore_session_state(self) -> None:
        """Restore per-panel paths, marks, sort, and active side from settings."""
        s = self._settings
        for panel, path_str, marks, sk, sr, sh in [
            (
                self.left_panel,
                s.left_path,
                s.left_marks,
                s.left_sort_key,
                s.left_sort_reverse,
                s.left_show_hidden,
            ),
            (
                self.right_panel,
                s.right_path,
                s.right_marks,
                s.right_sort_key,
                s.right_sort_reverse,
                s.right_show_hidden,
            ),
        ]:
            if path_str:
                p = Path(path_str)
                if p.is_dir():
                    panel.sort_key = sk
                    panel.sort_reverse = sr
                    panel.show_hidden = sh
                    panel.load(self._local_fs.from_path(p))
                    # Re-mark saved entry names
                    if marks:
                        mark_set = set(marks)
                        for entry in panel._entries:
                            if not entry.is_parent and entry.name in mark_set:
                                panel.marked.add(entry.path)
                        panel._refresh_row_tags()
                        panel._notify_marks_changed()
        # Active side
        if s.active_side == "right":
            self.active_panel = self.right_panel
            self._update_active_panel_style()

    def _build_menu_bar(self) -> None:
        menubar = tk.Menu(self)
        self.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu, underline=0)
        file_menu.add_command(label="Theme...", command=self.cmd_theme, underline=0)
        file_menu.add_separator()
        file_menu.add_command(label="Font...", command=self.cmd_font, underline=0)
        file_menu.add_command(label="Editor Font...", command=self.cmd_editor_font, underline=0)
        file_menu.add_command(label="Viewer Font...", command=self.cmd_viewer_font, underline=0)
        file_menu.add_separator()
        file_menu.add_command(label="Connections...", command=self.cmd_ftp_connections, underline=0)
        file_menu.add_command(
            label="Command Settings...", command=self.cmd_command_settings, underline=8
        )
        file_menu.add_command(
            label="Optional Dependencies...",
            command=self.cmd_optional_dependencies,
            underline=0,
        )
        file_menu.add_command(label="Plugin Status...", command=self.cmd_plugin_status, underline=0)
        file_menu.add_separator()
        file_menu.add_command(
            label="Command Prompt",
            accelerator="Ctrl+X",
            command=self._show_command_prompt,
            underline=8,
        )
        file_menu.add_separator()
        file_menu.add_command(
            label="Quit", accelerator="Ctrl+Q", command=self.cmd_quit, underline=0
        )

        view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="View", menu=view_menu, underline=0)
        view_menu.add_command(
            label="Show Hidden Files",
            command=lambda: self.active_panel.toggle_hidden(),
            underline=5,
        )
        view_menu.add_command(
            label="Refresh",
            command=lambda: self._refresh_panel_preserving_position(self.active_panel),
            underline=0,
        )
        view_menu.add_separator()
        view_menu.add_command(
            label="Sort by Name",
            command=lambda: self.active_panel.set_sort("name"),
            underline=8,
        )
        view_menu.add_command(
            label="Sort by Date",
            command=lambda: self.active_panel.set_sort("mtime"),
            underline=8,
        )
        view_menu.add_command(
            label="Sort by Size",
            command=lambda: self.active_panel.set_sort("size"),
            underline=8,
        )
        view_menu.add_command(
            label="Sort by Extension",
            command=lambda: self.active_panel.set_sort("extension"),
            underline=8,
        )
        view_menu.add_separator()
        view_menu.add_command(label="Show Icons", command=self._toggle_icons, underline=5)
        view_menu.add_command(
            label="Show Extension Column", command=self._toggle_extension_column, underline=5
        )
        view_menu.add_command(label="Flat View", command=self._toggle_flat_view, underline=0)
        view_menu.add_command(
            label="Columns…",
            command=lambda: show_columns_dialog(self, self.active_panel, self._other_panel()),
            underline=0,
        )
        view_menu.add_separator()
        view_menu.add_command(
            label="Command Prompt",
            accelerator="Ctrl+X",
            command=self._show_command_prompt,
            underline=8,
        )

        # Operations menu — populated from the self-registering file_ops registry
        from linux_commander.file_ops import available_operations

        ops = available_operations()
        ops_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Operations", menu=ops_menu, underline=0)
        # Hotlist commands
        ops_menu.add_command(
            label="Hotlist (Bookmarks)…",
            accelerator="Ctrl+\\",
            command=self._show_hotlist,
            underline=0,
        )
        ops_menu.add_command(
            label="Add Current Dir to Hotlist",
            command=lambda: self.active_panel.add_current_dir_to_hotlist(),
            underline=0,
        )
        ops_menu.add_separator()
        # Compare commands
        ops_menu.add_command(
            label="Compare Files…",
            command=self._compare_selected_files,
            underline=0,
        )
        ops_menu.add_command(
            label="Compare Directories…",
            command=self._compare_directories,
            underline=0,
        )
        ops_menu.add_separator()
        for op in ops:
            ops_menu.add_command(
                label=op.name,
                command=lambda _op=op: self._run_file_operation(_op),  # type: ignore[misc]
            )

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
        if not dest_dir.fs.writable:
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
        self._cmd_entry.focus_set()
        self._cmd_var.set(char)
        self._cmd_entry.icursor(tk.END)
        return "break"

    def _show_command_prompt(self) -> None:
        """Focus the always-visible command prompt at the bottom."""
        self._cmd_entry.focus_set()
        self._cmd_var.set("")
        self._cmd_history_index = len(self._cmd_history)

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
        # Try the OS's default application first (requires a real OS path);
        # fall back to the built-in viewer if no opener is available or failed.
        real = entry.path.fs.realpath(entry.path)
        if real is not None and platform_util.open_with_default_app(real):
            return
        viewer.view_file(self, entry.path)

    # -- refresh helpers -------------------------------------------------------

    def _refresh_panel_preserving_position(self, panel: FilePanel) -> None:
        """Reload `panel`'s current directory, keeping the cursor at the same
        row index if possible (a "sensible" row after copy/move/delete)."""
        previous_index = panel.current_index()
        panel.load(panel.current_path)
        if previous_index is not None:
            panel.select_index(previous_index)

    def _refresh_both_panels(self) -> None:
        self._refresh_panel_preserving_position(self.left_panel)
        self._refresh_panel_preserving_position(self.right_panel)
        self._update_status()

    def _report_errors(
        self, errors: list[operations.OperationError], verb: str = "Operation"
    ) -> None:
        if not errors:
            return
        lines = [f"{err.path.name}: {err.message}" for err in errors]
        message = f"{verb} completed with {len(errors)} error(s):\n\n" + "\n".join(lines)
        dialogs.error(self, message, title=f"{verb} errors")

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
        if not entry.path.fs.writable:
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
        entry = self.active_panel.cursor_entry()
        if entry is None or entry.is_parent or entry.is_dir:
            return
        from linux_commander.file_info_dialog import show_file_info

        show_file_info(self, entry.path, entry.size, entry.mtime)

    def cmd_new_file(self) -> None:
        """Shift+F4: create a new file in the active panel's directory and edit it."""
        panel = self.active_panel
        if not panel.current_path.fs.writable:
            dialogs.error(
                self, "Cannot create a file in a read-only filesystem.", title="New File failed"
            )
            return
        name = dialogs.prompt(self, "New File", "New file name:")
        if not name:
            return
        target = panel.current_path / name
        real = panel.current_path.fs.realpath(target)
        already_exists = real is not None and real.exists()
        if not already_exists:
            try:
                operations.make_file(panel.current_path, name)
            except OSError as exc:
                dialogs.error(self, str(exc), title="New File failed")
                return
            panel.load(panel.current_path, select_name=name)
            other = self._other_panel()
            if other.current_path == panel.current_path:
                self._refresh_panel_preserving_position(other)
            self._update_status()
        viewer.edit_file(
            self,
            target,
            on_saved=lambda: self._refresh_panel_preserving_position(panel),
            settings=self._settings,
        )

    def cmd_copy(self) -> None:
        self._copy_or_move(is_move=False)

    def cmd_move(self) -> None:
        self._copy_or_move(is_move=True)

    def cmd_compress(self) -> None:
        """Compress selected files to an archive (Shift+F5)."""
        from linux_commander.archiving import compress_sources
        from linux_commander.compression_dialog import CompressionDialog

        panel = self.active_panel
        entries = panel.selected_entries()
        if not entries:
            dialogs.error(self, "No files selected for compression.", title="Compress")
            return
        sources = [entry.path for entry in entries]

        dialog = CompressionDialog(self, panel.current_path, sources)
        if dialog.result:
            archive_path, fmt, options = dialog.result
            local_fs = self._local_fs

            def work(on_progress, should_cancel):
                return compress_sources(
                    sources, archive_path, fmt, options, local_fs, on_progress, should_cancel
                )

            errors = dialogs.run_with_progress(self, "Compressing...", work)
            self._refresh_both_panels()
            self._report_errors(errors, "Compress")

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

    def _copy_or_move(self, is_move: bool) -> None:
        panel = self.active_panel
        entries = panel.selected_entries()
        if not entries:
            return
        sources = [entry.path for entry in entries]
        verb = "Move" if is_move else "Copy"

        other_base = self._other_panel().current_path
        default_dest = str(other_base)
        dest_text = dialogs.prompt(
            self, verb, f"{verb} {len(sources)} item(s) to:", initial=default_dest
        )
        if not dest_text:
            return

        # A single source and a bare filename (no directory component) typed
        # as the destination means "rename in place", not "move" — this never
        # touches the other panel's filesystem, so handle it before resolving
        # a cross-panel destination below.
        if is_move and len(sources) == 1 and "/" not in dest_text:
            source = sources[0]
            if not source.fs.writable:
                dialogs.error(
                    self, "Cannot rename: source filesystem is read-only.", title="Rename failed"
                )
                return
            try:
                operations.rename_entry(source, dest_text)
            except OSError as exc:
                dialogs.error(self, str(exc), title="Rename failed")
            self._refresh_both_panels()
            return

        # Resolve against the OTHER panel's filesystem (which may be local,
        # remote, or an archive mount) so the destination keeps its own
        # backend instead of being coerced into a local path.
        dest_path = operations.resolve_dest_path(other_base, dest_text)

        if not dest_path.fs.writable:
            dialogs.error(self, "Destination filesystem is read-only.", title=f"{verb} failed")
            return

        # If moving from a read-only source, warn that only a copy will happen.
        if is_move and any(not s.fs.writable for s in sources):
            if not dialogs.confirm(
                self,
                "The source filesystem is read-only.\n"
                "Items will be copied but not removed from the source.\n\n"
                "Proceed with copy?",
                title="Move -> Copy only",
            ):
                return

        op_func = operations.move_entries if is_move else operations.copy_entries

        def work(
            on_progress: ProgressCallback, should_cancel: CancelPredicate
        ) -> list[OperationError]:
            return op_func(sources, dest_path, on_progress=on_progress, should_cancel=should_cancel)

        errors = dialogs.run_with_progress(self, f"{verb}ing...", work)
        self._refresh_both_panels()
        self._report_errors(errors, verb)

    def cmd_mkdir(self) -> None:
        panel = self.active_panel
        if not panel.current_path.fs.writable:
            dialogs.error(
                self, "Cannot create a directory in a read-only filesystem.", title="MkDir failed"
            )
            return
        name = dialogs.prompt(self, "Make Directory", "New directory name:")
        if not name:
            return
        try:
            operations.make_directory(panel.current_path, name)
        except OSError as exc:
            dialogs.error(self, str(exc), title="MkDir failed")
            return
        panel.load(panel.current_path, select_name=name)
        other = self._other_panel()
        if other.current_path == panel.current_path:
            self._refresh_panel_preserving_position(other)
        self._update_status()

    def cmd_delete(self) -> None:
        panel = self.active_panel
        entries = panel.selected_entries()
        if not entries:
            return
        read_only = [e for e in entries if not e.path.fs.writable]
        if read_only:
            names = ", ".join(e.name for e in read_only[:5])
            dialogs.error(
                self,
                f"Cannot delete from a read-only filesystem:\n{names}",
                title="Delete failed",
            )
            return
        preview = ", ".join(entry.name for entry in entries[:5])
        if len(entries) > 5:
            preview += f", and {len(entries) - 5} more"
        if not dialogs.confirm(
            self, f"Delete {len(entries)} item(s)?\n\n{preview}", title="Confirm delete"
        ):
            return
        paths = [entry.path for entry in entries]

        def work(
            on_progress: ProgressCallback, should_cancel: CancelPredicate
        ) -> list[OperationError]:
            return operations.delete_entries(
                paths, on_progress=on_progress, should_cancel=should_cancel
            )

        errors = dialogs.run_with_progress(self, "Deleting...", work)
        self._refresh_both_panels()
        self._report_errors(errors, "Delete")

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
        families = sorted(tkfont.families())
        mono_families = [
            f
            for f in families
            if "mono" in f.lower() or "courier" in f.lower() or "console" in f.lower()
        ]
        if mono_families:
            families = mono_families + [f for f in families if f not in mono_families]

        dialog = tk.Toplevel(self)
        dialog.title("Font")
        dialog.transient(self)
        dialog.resizable(False, False)

        ttk.Label(dialog, text="Font:").grid(row=0, column=0, padx=8, pady=8, sticky="w")
        font_var = tk.StringVar(value=self._settings.font_family)
        font_combo = ttk.Combobox(
            dialog, textvariable=font_var, values=families, state="readonly", width=30
        )
        font_combo.grid(row=0, column=1, padx=8, pady=8)

        ttk.Label(dialog, text="Size:").grid(row=1, column=0, padx=8, pady=8, sticky="w")
        size_var = tk.IntVar(value=self._settings.font_size)
        size_spin = ttk.Spinbox(dialog, from_=8, to=72, textvariable=size_var, width=5)
        size_spin.grid(row=1, column=1, padx=8, pady=8, sticky="w")

        # Snapshot for Cancel restore
        _saved_family = self._settings.font_family
        _saved_size = self._settings.font_size

        def preview(*_) -> None:
            fam = font_var.get()
            if not fam:
                return
            try:
                sz = int(size_var.get())
            except (ValueError, tk.TclError):
                return
            self._settings.font_family = fam
            self._settings.font_size = sz
            self._apply_font_settings()

        def apply_font() -> None:
            preview()
            dialog.destroy()

        def cancel_font() -> None:
            self._settings.font_family = _saved_family
            self._settings.font_size = _saved_size
            self._apply_font_settings()
            dialog.destroy()

        font_combo.bind("<<ComboboxSelected>>", preview)
        size_spin.configure(command=preview)
        size_spin.bind("<KeyRelease>", preview)

        btn_frame = ttk.Frame(dialog)
        btn_frame.grid(row=2, column=0, columnspan=2, pady=8)
        ttk.Button(btn_frame, text="OK", command=apply_font).pack(side="right", padx=4)
        ttk.Button(btn_frame, text="Cancel", command=cancel_font).pack(side="right", padx=4)

        dialog.protocol("WM_DELETE_WINDOW", cancel_font)
        dialogs._center_over(dialog, self)
        dialog.grab_set()
        font_combo.focus_set()
        dialog.wait_window()

    def _font_dialog(self, title: str, family_attr: str, size_attr: str) -> None:
        """Generic font selection dialog for editor/viewer (apply-on-OK, cancel restores)."""
        families = sorted(tkfont.families())
        mono_families = [
            f
            for f in families
            if "mono" in f.lower() or "courier" in f.lower() or "console" in f.lower()
        ]
        if mono_families:
            families = mono_families + [f for f in families if f not in mono_families]

        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.transient(self)
        dialog.resizable(False, False)

        ttk.Label(dialog, text="Font:").grid(row=0, column=0, padx=8, pady=8, sticky="w")
        font_var = tk.StringVar(value=getattr(self._settings, family_attr))
        font_combo = ttk.Combobox(
            dialog, textvariable=font_var, values=families, state="readonly", width=30
        )
        font_combo.grid(row=0, column=1, padx=8, pady=8)

        ttk.Label(dialog, text="Size:").grid(row=1, column=0, padx=8, pady=8, sticky="w")
        size_var = tk.IntVar(value=getattr(self._settings, size_attr))
        size_spin = ttk.Spinbox(dialog, from_=8, to=72, textvariable=size_var, width=5)
        size_spin.grid(row=1, column=1, padx=8, pady=8, sticky="w")

        _saved_family = getattr(self._settings, family_attr)
        _saved_size = getattr(self._settings, size_attr)

        def apply_font() -> None:
            setattr(self._settings, family_attr, font_var.get())
            setattr(self._settings, size_attr, size_var.get())
            dialog.destroy()

        def cancel_font() -> None:
            setattr(self._settings, family_attr, _saved_family)
            setattr(self._settings, size_attr, _saved_size)
            dialog.destroy()

        btn_frame = ttk.Frame(dialog)
        btn_frame.grid(row=2, column=0, columnspan=2, pady=8)
        ttk.Button(btn_frame, text="OK", command=apply_font).pack(side="right", padx=4)
        ttk.Button(btn_frame, text="Cancel", command=cancel_font).pack(side="right", padx=4)

        dialog.protocol("WM_DELETE_WINDOW", cancel_font)
        dialogs._center_over(dialog, self)
        dialog.grab_set()
        font_combo.focus_set()
        dialog.wait_window()

    def cmd_editor_font(self) -> None:
        """Font selection dialog for the editor."""
        self._font_dialog("Editor Font", "editor_font_family", "editor_font_size")

    def cmd_viewer_font(self) -> None:
        """Font selection dialog for the viewer."""
        self._font_dialog("Viewer Font", "viewer_font_family", "viewer_font_size")

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
    app = CommanderApp()
    app.mainloop()
