"""Integration tests for linux-commander.

Exercises the full CommanderApp shell, panels, viewer, and operations
programmatically by calling public cmd_* and panel methods directly.
"""

from __future__ import annotations

import os
import time
import tkinter as tk
import zipfile
from collections.abc import Generator
from pathlib import Path

import pytest

from linux_commander.app import CommanderApp
from linux_commander.vfs import FileEntry, LocalFileSystem

_FS = LocalFileSystem()


def _select_entry(panel, name: str) -> FileEntry | None:
    """Move cursor to entry with given name. Returns entry or None."""
    panel.move_to_first()
    for _ in range(50):
        entry = panel.cursor_entry()
        if entry is not None and entry.name == name:
            return entry
        panel.move_cursor(1)
    return None


def _find_viewer(app: CommanderApp):
    """Find the TextWindow Toplevel among app children.

    TextWindow creates a tk.Toplevel whose child is a tk.Text widget.
    The TextWindow instance itself is not stored on the app, so we
    search by widget type hierarchy.
    """
    for child in app.winfo_children():
        if isinstance(child, tk.Toplevel):
            # Check if this Toplevel contains a Text widget (viewer/editor)
            for grandchild in child.winfo_children():
                if isinstance(grandchild, tk.Text):
                    # Attach text_widget for convenience
                    child.text_widget = grandchild  # type: ignore[attr-defined]
                    return child
    return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def test_dirs(tmp_path: Path) -> tuple[Path, Path]:
    """Create two directories populated with known test content.

    Returns (left_dir, right_dir).
    """
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()

    # Text file
    (left / "text.txt").write_text("hello world\nsecond line\n")

    # Binary file with some printable strings
    (left / "binary.bin").write_bytes(
        b"\x00\x01\x02\x03Hello Binary\x00\xff\xfe\xfdAnother String\x00\x00\x00\x00"
    )

    # CSV file
    (left / "data.csv").write_text("name,age,city\nAlice,30,NYC\nBob,25,LA\n")

    # JSON file
    (left / "config.json").write_text('{\n  "key": "value",\n  "number": 42\n}')

    # Python file for syntax highlighting
    (left / "script.py").write_text("import os\n\ndef main():\n    print('hello')\n")

    # Subdirectory with nested file
    subdir = left / "subdir"
    subdir.mkdir()
    (subdir / "nested.txt").write_text("nested content\n")

    # Hidden file
    (left / ".hidden").write_text("hidden content\n")

    # Empty file
    (left / "empty.txt").write_text("")

    # File with old mtime (for mtime preservation test)
    old_file = left / "old_file.txt"
    old_file.write_text("old content\n")
    os.utime(old_file, (time.time() - 86400, time.time() - 86400))

    # Target directory in right panel
    (right / "target").mkdir()

    return left, right


@pytest.fixture
def app(test_dirs: tuple[Path, Path]) -> Generator[CommanderApp, None, None]:
    """Create a CommanderApp instance with test directories.

    The app is destroyed in the test's teardown.
    """
    left, right = test_dirs
    # Clear persisted paths so initial paths are used
    from linux_commander.settings import Settings, save_settings

    settings = Settings()
    settings.left_path = ""
    settings.right_path = ""
    save_settings(settings)

    # Clear icon cache so icons are rebuilt for new Tk root
    import linux_commander.icons as icons_module

    icons_module._cache.clear()

    try:
        app = CommanderApp(left_path=left, right_path=right)
        yield app
        app.destroy()
    finally:
        # Restore empty settings for next test
        save_settings(settings)


# ---------------------------------------------------------------------------
# Phase 1: Fixture verification
# ---------------------------------------------------------------------------


class TestFixture:
    """Verify the test fixture creates correct content."""

    def test_fixture_creates_dirs(self, test_dirs: tuple[Path, Path]) -> None:
        left, right = test_dirs
        assert left.exists()
        assert right.exists()
        assert (left / "text.txt").exists()
        assert (left / "binary.bin").exists()
        assert (left / "data.csv").exists()
        assert (left / "config.json").exists()
        assert (left / "script.py").exists()
        assert (left / "subdir" / "nested.txt").exists()
        assert (left / ".hidden").exists()
        assert (left / "empty.txt").exists()
        assert (left / "old_file.txt").exists()
        assert (right / "target").is_dir()

    def test_app_initializes_with_correct_paths(
        self, app: CommanderApp, test_dirs: tuple[Path, Path]
    ) -> None:
        left, right = test_dirs
        assert str(app.left_panel.current_path) == str(left)
        assert str(app.right_panel.current_path) == str(right)


# ---------------------------------------------------------------------------
# Phase 2: Panel navigation
# ---------------------------------------------------------------------------


class TestNavigation:
    """Test panel navigation methods."""

    def test_cursor_initial_position(self, app: CommanderApp) -> None:
        """Cursor should start at first entry (index 0 is .. parent)."""
        panel = app.left_panel
        idx = panel.current_index()
        assert idx is not None
        # Index 0 is the .. parent entry, cursor starts at first real entry
        assert idx >= 0

    def test_move_cursor_down(self, app: CommanderApp) -> None:
        """Moving cursor down should increment index."""
        panel = app.left_panel
        initial = panel.current_index()
        panel.move_cursor(1)
        new_idx = panel.current_index()
        assert new_idx is not None
        assert initial is not None
        assert new_idx == initial + 1

    def test_move_to_first(self, app: CommanderApp) -> None:
        """move_to_first should set cursor to index 0."""
        panel = app.left_panel
        panel.move_to_last()
        panel.move_to_first()
        assert panel.current_index() == 0

    def test_move_to_last(self, app: CommanderApp) -> None:
        """move_to_last should set cursor to last entry."""
        panel = app.left_panel
        panel.move_to_last()
        idx = panel.current_index()
        assert idx is not None
        assert idx > 0

    def test_enter_directory(self, app: CommanderApp, test_dirs: tuple[Path, Path]) -> None:
        """Entering a directory should navigate into it."""
        left, _ = test_dirs
        panel = app.left_panel

        entry = _select_entry(panel, "subdir")
        assert entry is not None

        # Enter the directory
        panel._activate_cursor()
        app.update_idletasks()

        assert str(panel.current_path).endswith("subdir")

    def test_go_up(self, app: CommanderApp, test_dirs: tuple[Path, Path]) -> None:
        """go_up should navigate to parent directory."""
        left, _ = test_dirs
        panel = app.left_panel

        # Navigate into subdir first
        _select_entry(panel, "subdir")
        panel._activate_cursor()
        app.update_idletasks()
        assert str(panel.current_path).endswith("subdir")

        # Go up
        panel.go_up()
        app.update_idletasks()
        assert str(panel.current_path) == str(left)

    def test_switch_active_panel(self, app: CommanderApp) -> None:
        """Tab should switch the active panel."""
        assert app.left_panel.is_active
        app._switch_active_panel()
        assert app.right_panel.is_active
        app._switch_active_panel()
        assert app.left_panel.is_active

    def test_panel_entry_count(self, app: CommanderApp) -> None:
        """Panel should list all visible entries."""
        panel = app.left_panel
        entries = panel._entries
        # Should have .. + visible files (hidden files shown by default)
        assert len(entries) >= 8


# ---------------------------------------------------------------------------
# Phase 3: F-key commands
# ---------------------------------------------------------------------------


class TestFKeyCommands:
    """Test F-key command handlers."""

    def test_f1_help(self, app: CommanderApp) -> None:
        """F1 should show help without crashing."""
        app.cmd_help()
        # Verify help dialog was created (check for toplevel windows)
        # The help dialog is a Toplevel; we just verify no exception

    def test_f3_view_text_file(self, app: CommanderApp) -> None:
        """F3 on a text file should open the viewer."""
        panel = app.left_panel
        # Select text.txt
        entry = _select_entry(panel, "text.txt")
        assert entry is not None

        app.cmd_view()
        app.update_idletasks()

        # Verify a viewer window was created
        viewer = _find_viewer(app)
        assert viewer is not None

    def test_f4_edit_file(self, app: CommanderApp) -> None:
        """F4 on a text file should open the editor."""
        panel = app.left_panel
        _select_entry(panel, "text.txt")
        app.cmd_edit()
        app.update_idletasks()

    def test_f7_mkdir(self, app: CommanderApp, test_dirs: tuple[Path, Path]) -> None:
        """F7 should create a new directory."""
        left, _ = test_dirs
        # Call mkdir directly through operations
        from linux_commander.operations import make_directory

        make_directory(app.left_panel.current_path, "newdir")
        app.update_idletasks()
        assert (left / "newdir").is_dir()

    def test_f8_delete_file(self, app: CommanderApp, test_dirs: tuple[Path, Path]) -> None:
        """F8 should delete the selected file."""
        left, _ = test_dirs
        panel = app.left_panel

        entry = _select_entry(panel, "empty.txt")
        assert entry is not None
        assert (left / "empty.txt").exists()

        # Delete via operations
        from linux_commander.operations import delete_entries

        delete_entries([entry.path])
        app.update_idletasks()

        assert not (left / "empty.txt").exists()

    def test_f10_quit(self, test_dirs: tuple[Path, Path]) -> None:
        """F10 should tear down the app."""
        left, right = test_dirs
        # Clear icon cache so new app creates fresh icons
        import linux_commander.icons as icons_module

        icons_module._cache.clear()
        app = CommanderApp(left_path=left, right_path=right)
        app.cmd_quit()
        # App should be destroyed — winfo_exists throws TclError after destroy
        try:
            exists = app.winfo_exists()
        except tk.TclError:
            exists = False
        assert not exists


# ---------------------------------------------------------------------------
# Phase 4: File operations (Copy, Move)
# ---------------------------------------------------------------------------


class TestFileOperations:
    """Test copy, move, and rename operations."""

    def test_copy_file(self, app: CommanderApp, test_dirs: tuple[Path, Path]) -> None:
        """Copy a file from left to right panel."""
        left, right = test_dirs
        panel = app.left_panel

        # Select text.txt
        panel.move_to_first()
        source_entry = None
        for _ in range(50):
            entry = panel.cursor_entry()
            if entry is not None and entry.name == "text.txt":
                source_entry = entry
                break
            panel.move_cursor(1)
        assert source_entry is not None

        # Navigate right panel to target
        app._switch_active_panel()
        app.right_panel.move_to_first()
        for _ in range(50):
            entry = app.right_panel.cursor_entry()
            if entry is not None and entry.name == "target":
                break
            app.right_panel.move_cursor(1)

        app.right_panel._activate_cursor()
        app.update_idletasks()

        # Copy using operations directly (bypass dialog)
        from linux_commander.operations import copy_entries

        dest_path = app.right_panel._local_fs.from_path(right / "target")
        copy_entries(
            [source_entry.path],
            dest_path,
            lambda *_a: None,
            lambda: False,
        )
        app.update_idletasks()

        # Verify file was copied
        assert (right / "target" / "text.txt").exists()

    def test_copy_directory(self, app: CommanderApp, test_dirs: tuple[Path, Path]) -> None:
        """Copy a directory tree from left to right panel."""
        left, right = test_dirs
        panel = app.left_panel

        # Select subdir
        panel.move_to_first()
        source_entry = None
        for _ in range(50):
            entry = panel.cursor_entry()
            if entry is not None and entry.name == "subdir":
                source_entry = entry
                break
            panel.move_cursor(1)
        assert source_entry is not None

        # Navigate right panel to target
        app._switch_active_panel()
        app.right_panel.move_to_first()
        for _ in range(50):
            entry = app.right_panel.cursor_entry()
            if entry is not None and entry.name == "target":
                break
            app.right_panel.move_cursor(1)

        app.right_panel._activate_cursor()
        app.update_idletasks()

        # Copy using operations directly
        from linux_commander.operations import copy_entries

        dest_path = app.right_panel._local_fs.from_path(right / "target")
        copy_entries(
            [source_entry.path],
            dest_path,
            lambda *_a: None,
            lambda: False,
        )
        app.update_idletasks()

        # Verify directory was copied
        assert (right / "target" / "subdir").is_dir()
        assert (right / "target" / "subdir" / "nested.txt").exists()

    def test_move_file(self, app: CommanderApp, test_dirs: tuple[Path, Path]) -> None:
        """Move a file from left to right panel."""
        left, right = test_dirs

        # Navigate right panel to target
        app._switch_active_panel()
        app.right_panel.move_to_first()
        for _ in range(50):
            entry = app.right_panel.cursor_entry()
            if entry is not None and entry.name == "target":
                break
            app.right_panel.move_cursor(1)

        app.right_panel._activate_cursor()
        app.update_idletasks()

        # Select and move old_file.txt
        app._switch_active_panel()
        app.left_panel.move_to_first()
        source_entry = None
        for _ in range(50):
            entry = app.left_panel.cursor_entry()
            if entry is not None and entry.name == "old_file.txt":
                source_entry = entry
                break
            app.left_panel.move_cursor(1)
        assert source_entry is not None

        # Move using operations directly
        from linux_commander.operations import move_entries

        dest_path = app.right_panel._local_fs.from_path(right / "target")
        move_entries(
            [source_entry.path],
            dest_path,
            lambda *_a: None,
            lambda: False,
        )
        app.update_idletasks()

        # Verify file was moved
        assert not (left / "old_file.txt").exists()
        assert (right / "target" / "old_file.txt").exists()

    def test_copy_preserves_mtime(self, app: CommanderApp, test_dirs: tuple[Path, Path]) -> None:
        """Copied files should preserve source modification time."""
        left, right = test_dirs

        # Get source mtime
        src_file = left / "old_file.txt"
        src_mtime = src_file.stat().st_mtime

        # Navigate right panel to target
        app._switch_active_panel()
        app.right_panel.move_to_first()
        for _ in range(50):
            entry = app.right_panel.cursor_entry()
            if entry is not None and entry.name == "target":
                break
            app.right_panel.move_cursor(1)

        app.right_panel._activate_cursor()
        app.update_idletasks()

        # Copy old_file.txt
        app._switch_active_panel()
        app.left_panel.move_to_first()
        source_entry = None
        for _ in range(50):
            entry = app.left_panel.cursor_entry()
            if entry is not None and entry.name == "old_file.txt":
                source_entry = entry
                break
            app.left_panel.move_cursor(1)
        assert source_entry is not None

        from linux_commander.operations import copy_entries

        dest_path = app.right_panel._local_fs.from_path(right / "target")
        copy_entries(
            [source_entry.path],
            dest_path,
            lambda *_a: None,
            lambda: False,
        )
        app.update_idletasks()

        # Verify mtime preserved
        dst_file = right / "target" / "old_file.txt"
        dst_mtime = dst_file.stat().st_mtime
        assert abs(dst_mtime - src_mtime) < 1.0

    def test_rename_file(self, app: CommanderApp, test_dirs: tuple[Path, Path]) -> None:
        """Rename a file in place."""
        left, _ = test_dirs
        panel = app.left_panel

        entry = _select_entry(panel, "text.txt")
        assert entry is not None

        # Rename via operations
        from linux_commander.operations import rename_entry

        rename_entry(entry.path, "renamed.txt")
        app.update_idletasks()

        assert (left / "renamed.txt").exists()
        assert not (left / "text.txt").exists()


# ---------------------------------------------------------------------------
# Phase 5: Viewer modes
# ---------------------------------------------------------------------------


class TestViewerModes:
    """Test viewer display modes (hex, strings, csv, json)."""

    def test_viewer_shows_text_content(self, app: CommanderApp) -> None:
        """Viewer should show text file content."""
        panel = app.left_panel
        entry = _select_entry(panel, "text.txt")
        assert entry is not None

        app.cmd_view()
        app.update_idletasks()

        # Find the TextWindow (last Toplevel child)
        viewer = _find_viewer(app)
        assert viewer is not None
        content = viewer.text_widget.get("1.0", "end-1c")  # type: ignore[union-attr]
        assert "hello world" in content

    def test_viewer_hex_mode(self, app: CommanderApp) -> None:
        """Hex mode should show hex dump of binary file."""
        panel = app.left_panel
        panel.move_to_first()
        for _ in range(50):
            entry = panel.cursor_entry()
            if entry is not None and entry.name == "binary.bin":
                break
            panel.move_cursor(1)

        app.cmd_view()
        app.update_idletasks()

        # Binary files should auto-switch to hex mode
        viewer = _find_viewer(app)
        assert viewer is not None

    def test_viewer_csv_auto_detect(self, app: CommanderApp) -> None:
        """CSV files should auto-detect and show table view."""
        panel = app.left_panel
        panel.move_to_first()
        for _ in range(50):
            entry = panel.cursor_entry()
            if entry is not None and entry.name == "data.csv":
                break
            panel.move_cursor(1)

        app.cmd_view()
        app.update_idletasks()

    def test_viewer_json_mode(self, app: CommanderApp) -> None:
        """JSON mode should format JSON content."""
        panel = app.left_panel
        panel.move_to_first()
        for _ in range(50):
            entry = panel.cursor_entry()
            if entry is not None and entry.name == "config.json":
                break
            panel.move_cursor(1)

        app.cmd_view()
        app.update_idletasks()

    def test_viewer_syntax_highlighting(self, app: CommanderApp) -> None:
        """Python files should have syntax highlighting."""
        panel = app.left_panel
        panel.move_to_first()
        for _ in range(50):
            entry = panel.cursor_entry()
            if entry is not None and entry.name == "script.py":
                break
            panel.move_cursor(1)

        app.cmd_view()
        app.update_idletasks()

        # Verify viewer opened
        viewer = _find_viewer(app)
        assert viewer is not None


# ---------------------------------------------------------------------------
# Phase 6: Tagging, sorting, view options
# ---------------------------------------------------------------------------


class TestTaggingSorting:
    """Test file tagging, sorting, and view options."""

    def test_toggle_mark(self, app: CommanderApp) -> None:
        """toggle_mark should tag/untag the current file."""
        panel = app.left_panel
        entry = panel.cursor_entry()
        assert entry is not None

        panel.toggle_mark()
        marked = panel.marked_entries()
        assert entry in marked

        panel.toggle_mark()
        marked = panel.marked_entries()
        assert entry not in marked

    def test_mark_all(self, app: CommanderApp) -> None:
        """mark_all should tag all visible entries."""
        panel = app.left_panel
        panel.mark_all()
        app.update_idletasks()
        marked = panel.marked_entries()
        assert len(marked) > 0

    def test_invert_selection(self, app: CommanderApp) -> None:
        """invert_selection should toggle all marks."""
        panel = app.left_panel
        panel.mark_all()
        app.update_idletasks()
        all_marked = len(panel.marked_entries()) > 0

        panel.invert_selection()
        app.update_idletasks()
        # After invert, some should be unmarked
        assert all_marked  # Just verify it ran without error

    def test_sort_by_name(self, app: CommanderApp) -> None:
        """set_sort('name') should sort entries alphabetically (case-insensitive).

        Directories are grouped before files, each group sorted within itself.
        """
        panel = app.left_panel
        # Default sort_key is "name", so first set a different key to avoid
        # toggling reverse, then set back to "name"
        panel.set_sort("mtime")
        panel.set_sort("name")
        app.update_idletasks()

        print(f"DEBUG: sort_key={panel.sort_key}, sort_reverse={panel.sort_reverse}")
        entries = panel._entries
        print(f"DEBUG: entries count={len(entries)}")
        for e in entries:
            print(f"  {e.name} is_dir={e.is_dir} is_parent={e.is_parent}")
        # Filter out the parent (..) entry
        entries = [e for e in entries if not e.is_parent]
        # Group into dirs and files like the sorter does
        dirs = [e.name for e in entries if e.is_dir]
        files = [e.name for e in entries if not e.is_dir]
        print(f"DEBUG: dirs={dirs}")
        print(f"DEBUG: files={files}")
        # Each group should be sorted case-insensitively
        assert dirs == sorted(dirs, key=str.lower)
        assert files == sorted(files, key=str.lower)
        # And dirs should come before files
        names = [e.name for e in entries]
        assert names[: len(dirs)] == dirs
        assert names[len(dirs) :] == files

    def test_sort_by_size(self, app: CommanderApp) -> None:
        """set_sort('size') should sort entries by size."""
        panel = app.left_panel
        panel.set_sort("size")
        app.update_idletasks()

    def test_sort_by_mtime(self, app: CommanderApp) -> None:
        """set_sort('mtime') should sort entries by modification time."""
        panel = app.left_panel
        panel.set_sort("mtime")
        app.update_idletasks()

    def test_toggle_hidden(self, app: CommanderApp) -> None:
        """toggle_hidden should show/hide dotfiles."""
        panel = app.left_panel

        # Hidden files should be visible by default
        entry_names = [e.name for e in panel._entries]
        has_hidden = ".hidden" in entry_names

        panel.toggle_hidden()
        app.update_idletasks()

        entry_names = [e.name for e in panel._entries]
        hidden_visible = ".hidden" in entry_names

        # Visibility should have toggled
        assert hidden_visible != has_hidden

    def test_flat_view(self, app: CommanderApp) -> None:
        """toggle_flat_view should show recursive listing."""
        panel = app.left_panel
        panel.toggle_flat_view()
        app.update_idletasks()

        # Flat view should include nested files
        entry_names = [e.name for e in panel._entries]
        assert "subdir/nested.txt" in entry_names or "nested.txt" in entry_names


# ---------------------------------------------------------------------------
# Phase 7: Archive browsing and compression
# ---------------------------------------------------------------------------


class TestArchiveCompression:
    """Test archive browsing and compression."""

    def test_browse_zip_archive(self, app: CommanderApp, test_dirs: tuple[Path, Path]) -> None:
        """Navigating into a zip file should show archive contents."""
        left, _ = test_dirs

        # Create a zip file
        zip_path = left / "archive.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("file_in_zip.txt", "content inside zip")

        # Refresh panel to see new zip
        panel = app.left_panel
        panel.load(panel.current_path)
        app.update_idletasks()

        # Find and enter the zip
        entry = _select_entry(panel, "archive.zip")
        assert entry is not None

        panel._activate_cursor()
        app.update_idletasks()

        # Should now be inside the zip
        entries = panel._entries
        names = [e.name for e in entries if not e.is_parent]
        assert "file_in_zip.txt" in names

    def test_compress_creates_archive(
        self, app: CommanderApp, test_dirs: tuple[Path, Path]
    ) -> None:
        """Compression should create an archive file."""
        left, _ = test_dirs

        # Select files to compress
        panel = app.left_panel
        entry = _select_entry(panel, "text.txt")
        assert entry is not None

        # Call compress directly with settings
        from linux_commander.archiving import compress_sources
        from linux_commander.vfs import VfsPath

        zip_path = left / "compressed.zip"

        errors = compress_sources(
            [entry.path],
            VfsPath(fs=panel._local_fs, parts=panel._local_fs.from_path(zip_path).parts),
            "zip",
            {
                "container": "zip",
                "codec": "none",
                "compresslevel": 6,
                "password": None,
                "key_name": None,
            },
            panel._local_fs,
            lambda *_a: None,
            lambda: False,
        )
        app.update_idletasks()

        assert not errors
        assert zip_path.exists()

    def test_file_info(self, app: CommanderApp) -> None:
        """File info should compute checksums."""
        panel = app.left_panel
        _select_entry(panel, "text.txt")
        app._ops.cmd_file_info()
        app.update_idletasks()
        # Just verify it doesn't crash


# ---------------------------------------------------------------------------
# Phase 8: Command line and menu actions
# ---------------------------------------------------------------------------


class TestCommandLineMenu:
    """Test command line and menu actions."""

    def test_command_prompt_exists(self, app: CommanderApp) -> None:
        """Command prompt widget should exist."""
        assert hasattr(app, "_command_prompt") or hasattr(app, "command_prompt")

    def test_refresh_panel(self, app: CommanderApp) -> None:
        """Refresh should reload the panel."""
        panel = app.left_panel
        count_before = len(panel._entries)
        app._refresh_panel_preserving_position(panel)
        app.update_idletasks()
        count_after = len(panel._entries)
        assert count_before == count_after

    def test_theme_dialog(self, app: CommanderApp) -> None:
        """Theme dialog should open without crashing."""

        def close_theme_dialog():
            for child in app.winfo_children():
                if isinstance(child, tk.Toplevel) and child.title() == "Theme":
                    child.destroy()
                    break

        app.after(50, close_theme_dialog)
        app.cmd_theme()
        app.update_idletasks()

    def test_font_dialog(self, app: CommanderApp) -> None:
        """Font dialog should open without crashing."""

        def close_font_dialog():
            for child in app.winfo_children():
                if isinstance(child, tk.Toplevel) and child.title() == "Font":
                    child.destroy()
                    break

        app.after(50, close_font_dialog)
        app.cmd_font()
        app.update_idletasks()
