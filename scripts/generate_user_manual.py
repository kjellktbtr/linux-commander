#!/usr/bin/env python3
"""
User Manual Generator for linux-commander.

This script programmatically launches the application, opens each window/dialog,
captures screenshots, and generates a markdown user manual.

Run with: uv run python scripts/generate_user_manual.py
Or with virtual display: xvfb-run -a uv run python scripts/generate_user_manual.py
"""

# ruff: noqa: E501

from __future__ import annotations

import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from PIL import ImageGrab  # type: ignore[import]  # noqa: E402

# Monkey-patch wait_window to prevent dialogs from blocking  # noqa: E402
_original_wait_window = tk.Toplevel.wait_window


def _patched_wait_window(self):
    """No-op wait_window to prevent blocking during screenshot capture."""
    pass


tk.Toplevel.wait_window = _patched_wait_window

SCREENSHOTS_DIR = PROJECT_ROOT / "docs" / "screenshots"
MANUAL_PATH = PROJECT_ROOT / "docs" / "user-manual.md"

# Ensure screenshots directory exists
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)


def force_lumen_theme():
    """Reset theme to 'lumen' (light) for consistent screenshots after dialogs that may change theme."""
    from linux_commander.theme_manager import _HAS_TTKBOOTSTRAP

    if _HAS_TTKBOOTSTRAP:
        import ttkbootstrap as tb

        style = tb.Style()
        style.theme_use("lumen")
        from linux_commander.panel import reset_style

        reset_style()


class ScreenshotCapture:
    """Utility for capturing screenshots of tkinter windows."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.counter = 0

    def capture_window(
        self, window: tk.Toplevel | tk.Tk, name: str, border_top: int = 28, border_side: int = 10
    ) -> Path:
        """Capture a screenshot of a specific window including window manager borders.

        Args:
            window: The tkinter window to capture
            name: Base filename for the screenshot
            border_top: Padding at top for title bar (default 28px)
            border_side: Padding on sides and bottom for window frame (default 10px)
        """
        window.update_idletasks()
        window.update()
        time.sleep(0.2)

        x = window.winfo_rootx()
        y = window.winfo_rooty()
        width = window.winfo_width()
        height = window.winfo_height()

        # Add padding to capture window manager decorations (title bar, borders)
        # Top needs more space for title bar (~28px), sides/bottom less (~10px)
        bbox = (x - border_side, y - border_top, x + width + border_side, y + height + border_side)
        img = ImageGrab.grab(bbox=bbox)

        self.counter += 1
        filename = f"{self.counter:02d}-{name}.png"
        path = SCREENSHOTS_DIR / filename
        img.save(path)
        print(f"  Captured: {path.name} ({img.width}x{img.height})")
        return path

    def capture_main_window(self, name: str = "main-window") -> Path:
        """Capture the main application window."""
        return self.capture_window(self.root, name)


class ManualGenerator:
    """Generates the markdown user manual."""

    def __init__(self):
        self.window_docs: list[dict] = []

    def add_window(
        self,
        title: str,
        screenshot_name: str,
        purpose: str,
        key_features: list[str],
        shortcuts: list[str],
        usage: str,
        menu_path: str = "",
    ):
        """Add a window/dialog to the manual."""
        self.window_docs.append(
            {
                "title": title,
                "screenshot": screenshot_name,
                "purpose": purpose,
                "key_features": key_features,
                "shortcuts": shortcuts,
                "usage": usage,
                "menu_path": menu_path,
            }
        )

    def generate(self) -> str:
        """Generate the complete markdown manual."""
        lines = [
            "# linux-commander User Manual",
            "",
            "> **linux-commander** — A dual-pane orthodox file manager for the terminal era, built with tkinter.",
            "",
            "## Table of Contents",
            "",
        ]

        for i, doc in enumerate(self.window_docs, 1):
            anchor = (
                doc["title"]
                .lower()
                .replace(" ", "-")
                .replace("/", "-")
                .replace("(", "")
                .replace(")", "")
            )
            lines.append(f"{i}. [{doc['title']}](#{anchor})")

        lines.extend(["", "---", ""])

        lines.extend(
            [
                "## Introduction",
                "",
                "linux-commander is a dual-pane file manager in the tradition of Norton Commander, Midnight Commander,",
                "and Total Commander. It features:",
                "",
                "- **Dual-pane browsing** with keyboard-driven navigation",
                "- **Built-in viewer/editor** (F3/F4) with syntax highlighting, hex dump, CSV/JSON table view",
                "- **Archive browsing** for 12+ formats (zip, tar, 7z, rar, etc.) — press Enter to enter archives",
                "- **File encryption** with ChaCha20-Poly1305",
                "- **FTP/SFTP** remote connections",
                "- **Background search** with archive descent (Alt+F7)",
                "- **Plugin system** for extensibility",
                "",
                "### Keyboard Conventions",
                "",
                "| Key | Action |",
                "|-----|--------|",
                "| `Tab` | Switch active panel |",
                "| `F1`–`F10` | Function key commands (shown in bottom bar) |",
                "| `Alt`+`Key` | Menu shortcuts (underlined letters) |",
                "| `Ctrl`+`Key` | Control shortcuts |",
                "| `Shift`+`Key` | Extended functions |",
                "",
                "---",
                "",
            ]
        )

        for doc in self.window_docs:
            lines.extend(self._generate_window_section(doc))
            lines.append("---")
            lines.append("")

        return "\n".join(lines)

    def _generate_window_section(self, doc: dict) -> list[str]:
        lines = [
            f"## {doc['title']}",
            "",
            f"![{doc['title']}](screenshots/{doc['screenshot']})",
            "",
        ]

        if doc["menu_path"]:
            lines.extend(
                [
                    f"**Menu Path:** {doc['menu_path']}",
                    "",
                ]
            )

        lines.extend(
            [
                "### Purpose",
                "",
                doc["purpose"],
                "",
                "### Key Features",
                "",
            ]
        )

        for feature in doc["key_features"]:
            lines.append(f"- {feature}")

        lines.extend(["", "### Keyboard Shortcuts", ""])

        if doc["shortcuts"]:
            for sc in doc["shortcuts"]:
                lines.append(f"- `{sc}`")
        else:
            lines.append("*No specific shortcuts*")

        lines.extend(["", "### Usage", "", doc["usage"], ""])

        return lines

    def save(self, path: Path):
        """Save the manual to file."""
        content = self.generate()
        path.write_text(content)
        print(f"Manual saved to: {path}")


def find_toplevel(root: tk.Tk, title: str) -> tk.Toplevel | None:
    """Find a Toplevel window by title."""
    for child in root.winfo_children():
        if isinstance(child, tk.Toplevel) and child.title() == title:
            return child
    return None


def wait_for_window(root: tk.Tk, title: str, timeout: float = 2.0) -> tk.Toplevel | None:
    """Wait for a window with given title to appear."""
    start = time.time()
    while time.time() - start < timeout:
        root.update_idletasks()
        root.update()
        window = find_toplevel(root, title)
        if window:
            return window
        time.sleep(0.05)
    return None


def capture_modal_dialog(
    app,  # type: CommanderApp  # noqa: F821
    capture: ScreenshotCapture,
    title: str,
    name: str,
    generator: ManualGenerator,
    doc_info: dict,
):
    """Capture a modal dialog by scheduling capture before it blocks."""
    result = {"window": None, "done": False}

    def do_capture():
        win = find_toplevel(app, title)
        if win:
            result["window"] = win
            path = capture.capture_window(win, name)
            generator.add_window(**doc_info, screenshot_name=path.name)
            win.destroy()
        result["done"] = True

    app.after(100, do_capture)
    return result


def main():
    print("=" * 60)
    print("linux-commander User Manual Generator")
    print("=" * 60)

    generator = ManualGenerator()

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        left = Path(tmp) / "left"
        right = Path(tmp) / "right"
        left.mkdir()
        right.mkdir()

        # Create test files
        (left / "readme.txt").write_text("Welcome to linux-commander!\n\nThis is a test file.")
        (left / "script.py").write_text(
            "#!/usr/bin/env python3\n\ndef hello():\n    print('Hello, World!')\n\nif __name__ == '__main__':\n    hello()"
        )
        (left / "data.csv").write_text("name,age,city\nAlice,30,NYC\nBob,25,LA\nCharlie,35,Chicago")
        (left / "config.json").write_text(
            '{\n  "app": "linux-commander",\n  "version": "1.0",\n  "features": ["dual-pane", "viewer", "archives"]\n}'
        )
        (left / "binary.bin").write_bytes(b"\x00\x01\x02\x03Hello Binary\x00\xff\xfe\xfd")
        from PIL import Image as PILImage

        img = PILImage.new("RGB", (400, 300), color="white")
        for y in range(300):
            for x in range(400):
                img.putpixel((x, y), (x % 256, y % 256, (x + y) % 256))
        img.save(left / "image.png")
        (right / "target").mkdir()

        # Clear settings and icon cache
        from linux_commander.settings import Settings, save_settings

        settings = Settings()
        settings.left_path = ""
        settings.right_path = ""
        save_settings(settings)

        import linux_commander.icons as icons_module

        icons_module._cache.clear()

        # Launch application
        print("\nLaunching CommanderApp...")
        from linux_commander.app import CommanderApp

        app = CommanderApp(left_path=left, right_path=right)

        # Set theme to lumen (light) for consistent screenshots
        print("Setting theme to 'lumen'...")
        from linux_commander.theme_manager import _HAS_TTKBOOTSTRAP

        if _HAS_TTKBOOTSTRAP:
            import ttkbootstrap as tb

            style = tb.Style()
            style.theme_use("lumen")
            app._boot_style = style
            from linux_commander.panel import reset_style

            reset_style()
            if hasattr(app, "_apply_font_settings"):
                app._apply_font_settings()
            app.update_idletasks()

        capture = ScreenshotCapture(app)
        panel = app.left_panel

        # ============================================================
        # 1. MAIN WINDOW
        # ============================================================
        print("\n[1/30] Capturing Main Window...")
        main_path = capture.capture_main_window("main-window")
        generator.add_window(
            title="Main Window",
            screenshot_name=main_path.name,
            purpose="The primary application window featuring dual file panels, function key bar, menu bar, command prompt, and status bar.",
            key_features=[
                "Dual file panels (left/right) with directory listings",
                "Function key bar (F1–F10) for common operations",
                "Menu bar with File, Options, Network, Operations, Help menus",
                "Command prompt for shell commands",
                "Status bar showing panel info, selection count, disk space",
                "Volume/drive selector (Linux: /proc/mounts based)",
            ],
            shortcuts=[
                "Tab — Switch active panel",
                "F1 — Help",
                "F3 — View file",
                "F4 — Edit file",
                "F5 — Copy",
                "F6 — Move",
                "F7 — New directory",
                "F8 — Delete",
                "F9 — Menu bar",
                "F10 — Quit",
                "Alt+F7 — Search",
                "Ctrl+\\ — Hotlist/Bookmarks",
            ],
            usage="Navigate with arrow keys, Enter to enter directories, Tab to switch panels. Use F-keys for operations. Type commands in the command prompt at the bottom.",
        )

        # ============================================================
        # 2. HELP DIALOG (F1)
        # ============================================================
        print("\n[2/30] Capturing Help Dialog...")
        result = capture_modal_dialog(
            app,
            capture,
            "Help",
            "help-dialog",
            generator,
            {
                "title": "Help Dialog (F1)",
                "purpose": "Displays keyboard shortcuts and command reference.",
                "key_features": [
                    "Complete keybinding reference",
                    "Scrollable text view",
                    "Dismissible with Escape or Close button",
                ],
                "shortcuts": ["F1 — Open help", "Escape — Close"],
                "usage": "Press F1 anytime to see the full keyboard reference. Scroll with arrow keys or mouse wheel. Press Escape or click Close to dismiss.",
            },
        )
        app.cmd_help()
        while not result["done"]:
            app.update_idletasks()
            app.update()
            time.sleep(0.05)

        # ============================================================
        # 3. VIEWER - Text Mode
        # ============================================================
        print("\n[3/30] Capturing Viewer (Text Mode)...")
        for _ in range(20):
            entry = panel.cursor_entry()
            if entry and entry.name == "readme.txt":
                break
            panel.move_cursor(1)
        app.cmd_view()
        viewer_win = wait_for_window(app, "readme.txt")
        if viewer_win:
            viewer_path = capture.capture_window(viewer_win, "viewer-text-mode")
            generator.add_window(
                title="File Viewer (F3) — Text Mode",
                screenshot_name=viewer_path.name,
                purpose="Read-only file viewer with syntax highlighting for text files.",
                key_features=[
                    "Syntax highlighting for 50+ languages",
                    "Line numbers toggle",
                    "Word wrap toggle",
                    "Encoding detection",
                    "Search within file (Ctrl+F)",
                    "Go to line (Ctrl+G)",
                ],
                shortcuts=[
                    "F3 — Open viewer on selected file",
                    "Ctrl+F — Search in viewer",
                    "Ctrl+G — Go to line",
                    "Escape — Close viewer",
                ],
                usage="Select any text file and press F3. The viewer auto-detects language by extension. Use View menu to toggle line numbers, word wrap, and change syntax highlighting.",
            )
            viewer_win.destroy()

        # ============================================================
        # 4. VIEWER - Syntax Highlighting
        # ============================================================
        print("\n[4/30] Capturing Viewer Syntax Highlighting...")
        for _ in range(20):
            entry = panel.cursor_entry()
            if entry and entry.name == "script.py":
                break
            panel.move_cursor(1)
        app.cmd_view()
        viewer_win = wait_for_window(app, "script.py")
        if viewer_win:
            viewer_path = capture.capture_window(viewer_win, "viewer-syntax-highlight")
            generator.add_window(
                title="File Viewer — Syntax Highlighting (Python)",
                screenshot_name=viewer_path.name,
                purpose="Syntax-highlighted view of source code files.",
                key_features=[
                    "Python syntax highlighting (keywords, strings, comments, functions)",
                    "Applies to 50+ languages via JSON definitions",
                    "Customizable color themes",
                ],
                shortcuts=["F3 — Open viewer", "Escape — Close"],
                usage="Select a .py file and press F3. Syntax highlighting applies automatically based on file extension. Add new languages by dropping .json files in linux_commander/syntax/.",
            )
            viewer_win.destroy()

        # ============================================================
        # 5. VIEWER - Hex Mode
        # ============================================================
        print("\n[5/30] Capturing Viewer Hex Mode...")
        for _ in range(20):
            entry = panel.cursor_entry()
            if entry and entry.name == "binary.bin":
                break
            panel.move_cursor(1)
        app.cmd_view()
        viewer_win = wait_for_window(app, "binary.bin")
        if viewer_win:
            viewer_path = capture.capture_window(viewer_win, "viewer-hex-mode")
            generator.add_window(
                title="File Viewer — Hex Mode",
                screenshot_name=viewer_path.name,
                purpose="Hexadecimal dump view for binary files with ASCII representation.",
                key_features=[
                    "Hex bytes with offset addresses",
                    "Printable ASCII column",
                    "Go to offset (Ctrl+G)",
                    "Search hex/ASCII (Ctrl+F)",
                    "Copy as hex/ASCII",
                ],
                shortcuts=[
                    "F3 on binary file — Auto hex mode",
                    "Ctrl+G — Go to offset",
                    "Escape — Close",
                ],
                usage="Binary files auto-switch to hex mode. Use View > Hex submenu for options. Press Ctrl+G to jump to a specific offset.",
            )
            viewer_win.destroy()

        # ============================================================
        # 6. VIEWER - CSV Mode
        # ============================================================
        print("\n[6/30] Capturing Viewer CSV Mode...")
        for _ in range(20):
            entry = panel.cursor_entry()
            if entry and entry.name == "data.csv":
                break
            panel.move_cursor(1)
        app.cmd_view()
        viewer_win = wait_for_window(app, "data.csv")
        if viewer_win:
            viewer_path = capture.capture_window(viewer_win, "viewer-csv-mode")
            generator.add_window(
                title="File Viewer — CSV Table Mode",
                screenshot_name=viewer_path.name,
                purpose="Tabular view of CSV/TSV files with sortable columns.",
                key_features=[
                    "Auto-detects CSV/TSV by extension",
                    "Column headers from first row",
                    "Sortable columns (click header)",
                    "Resizable columns",
                    "Row/column count in status",
                ],
                shortcuts=["F3 on .csv file — Auto table mode", "Escape — Close"],
                usage="Select a .csv file and press F3. The viewer parses and displays as a table. Click column headers to sort.",
            )
            viewer_win.destroy()

        # ============================================================
        # 7. VIEWER - JSON Mode
        # ============================================================
        print("\n[7/30] Capturing Viewer JSON Mode...")
        for _ in range(20):
            entry = panel.cursor_entry()
            if entry and entry.name == "config.json":
                break
            panel.move_cursor(1)
        app.cmd_view()
        viewer_win = wait_for_window(app, "config.json")
        if viewer_win:
            viewer_path = capture.capture_window(viewer_win, "viewer-json-mode")
            generator.add_window(
                title="File Viewer — JSON Mode",
                screenshot_name=viewer_path.name,
                purpose="Pretty-printed JSON with syntax highlighting and collapsible nodes.",
                key_features=[
                    "Formatted JSON with indentation",
                    "Syntax highlighting (keys, strings, numbers, booleans, null)",
                    "Collapsible/expandable objects and arrays",
                    "Validates JSON structure",
                ],
                shortcuts=["F3 on .json file — Auto JSON mode", "Escape — Close"],
                usage="Select a .json file and press F3. The viewer pretty-prints and highlights JSON. Click +/- to collapse/expand nodes.",
            )
            viewer_win.destroy()

        # ============================================================
        # 8. EDITOR WINDOW (F4)
        # ============================================================
        print("\n[8/30] Capturing Editor Window...")
        for _ in range(20):
            entry = panel.cursor_entry()
            if entry and entry.name == "readme.txt":
                break
            panel.move_cursor(1)
        app.cmd_edit()
        editor_win = wait_for_window(app, "readme.txt")
        if editor_win:
            editor_path = capture.capture_window(editor_win, "editor-window")
            generator.add_window(
                title="File Editor (F4)",
                screenshot_name=editor_path.name,
                purpose="Full-featured text editor with syntax highlighting, save, and undo/redo.",
                key_features=[
                    "Syntax highlighting (same as viewer)",
                    "Undo/Redo (Ctrl+Z / Ctrl+Y)",
                    "Save (Ctrl+S), Save As",
                    "Line numbers toggle",
                    "Word wrap toggle",
                    "Tab/space conversion",
                    "Encoding selection",
                ],
                shortcuts=[
                    "F4 — Open editor on selected file",
                    "Ctrl+S — Save",
                    "Ctrl+Z — Undo",
                    "Ctrl+Y — Redo",
                    "Escape — Close (prompts if unsaved)",
                ],
                usage="Press F4 on any text file to edit. Changes are saved with Ctrl+S. Editor prompts before closing if unsaved changes exist.",
            )
            editor_win.destroy()

        # ============================================================
        # 9. IMAGE VIEWER (F3 on image)
        # ============================================================
        print("\n[9/30] Capturing Image Viewer...")
        for _ in range(20):
            entry = panel.cursor_entry()
            if entry and entry.name == "image.png":
                break
            panel.move_cursor(1)
        app.cmd_view()
        img_win = wait_for_window(app, "image.png")
        if img_win:
            img_path = capture.capture_window(img_win, "image-viewer")
            generator.add_window(
                title="Image Viewer (F3 on image)",
                screenshot_name=img_path.name,
                purpose="Full-screen image viewer with zoom, pan, and slideshow.",
                key_features=[
                    "Supports PNG, JPEG, GIF, BMP, TIFF, WebP",
                    "Zoom in/out (mouse wheel, +/- keys)",
                    "Pan (drag or arrow keys)",
                    "Fit to window (F)",
                    "Slideshow mode (S)",
                    "Next/Previous (arrow keys)",
                    "EXIF data display",
                ],
                shortcuts=[
                    "F3 on image — Open image viewer",
                    "+/- or mouse wheel — Zoom",
                    "Arrows — Pan / Next/Prev",
                    "F — Fit to window",
                    "S — Slideshow",
                    "Escape — Close",
                ],
                usage="Press F3 on any image file. Use mouse wheel or +/- to zoom. Drag or arrow keys to pan. Press F to fit. Press S for slideshow of all images in directory.",
            )
            img_win.destroy()

        # ============================================================
        # 10. THEME PICKER (Options > Theme)
        # ============================================================
        print("\n[10/30] Capturing Theme Picker...")
        result = capture_modal_dialog(
            app,
            capture,
            "Theme",
            "theme-picker",
            generator,
            {
                "title": "Theme Picker (Options > Theme)",
                "purpose": "Select application theme from ttkbootstrap's built-in themes with live preview.",
                "key_features": [
                    "Two columns: Dark themes and Light themes",
                    "Live preview on selection",
                    "30+ built-in themes (cosmo, flatly, lumen, darkly, cyborg, solar, etc.)",
                    "Persists theme choice in settings",
                ],
                "shortcuts": ["Options > Theme", "Escape — Close"],
                "usage": "Open Options > Theme. Click a theme name to preview instantly. Click OK to apply, Cancel to revert. Current theme is highlighted.",
                "menu_path": "Options > Theme",
            },
        )
        app.cmd_theme()
        while not result["done"]:
            app.update_idletasks()
            app.update()
            time.sleep(0.05)

        # ============================================================
        # 11. FONT PICKER - Main Panels (Options > Font)
        # ============================================================
        print("\n[11/30] Capturing Font Picker (Main Panels)...")
        result = capture_modal_dialog(
            app,
            capture,
            "Font",
            "font-picker-panels",
            generator,
            {
                "title": "Font Picker — Main Panels (Options > Font)",
                "purpose": "Configure font family and size for the main file panels with live preview.",
                "key_features": [
                    "Font family dropdown (monospace fonts prioritized)",
                    "Font size spinner (8-72pt)",
                    "Live preview on selection change",
                    "Applies to both panels, status bar, F-key bar",
                    "Persists in settings",
                ],
                "shortcuts": ["Options > Font", "Escape — Cancel"],
                "usage": "Open Options > Font. Select font family and size. Preview updates live. Click OK to apply, Cancel to revert.",
                "menu_path": "Options > Font",
            },
        )
        app.cmd_font()
        while not result["done"]:
            app.update_idletasks()
            app.update()
            time.sleep(0.05)

        # ============================================================
        # 12. FONT PICKER - Editor (Options > Editor Font)
        # ============================================================
        print("\n[12/30] Capturing Font Picker (Editor)...")
        result = capture_modal_dialog(
            app,
            capture,
            "Editor Font",
            "font-picker-editor",
            generator,
            {
                "title": "Font Picker — Editor (Options > Editor Font)",
                "purpose": "Configure font for the editor window (F4).",
                "key_features": [
                    "Separate font setting from main panels",
                    "Monospace fonts prioritized",
                    "Live preview",
                    "Persists in settings",
                ],
                "shortcuts": ["Options > Editor Font", "Escape — Cancel"],
                "usage": "Open Options > Editor Font. Configure independently from main panel font. Useful for larger/smaller editor text.",
                "menu_path": "Options > Editor Font",
            },
        )
        app.cmd_editor_font()
        while not result["done"]:
            app.update_idletasks()
            app.update()
            time.sleep(0.05)

        # ============================================================
        # 13. FONT PICKER - Viewer (Options > Viewer Font)
        # ============================================================
        print("\n[13/30] Capturing Font Picker (Viewer)...")
        result = capture_modal_dialog(
            app,
            capture,
            "Viewer Font",
            "font-picker-viewer",
            generator,
            {
                "title": "Font Picker — Viewer (Options > Viewer Font)",
                "purpose": "Configure font for the viewer window (F3).",
                "key_features": [
                    "Separate font setting from main panels and editor",
                    "Monospace fonts prioritized",
                    "Live preview",
                    "Persists in settings",
                ],
                "shortcuts": ["Options > Viewer Font", "Escape — Cancel"],
                "usage": "Open Options > Viewer Font. Configure independently. Useful for larger text in hex/CSV views.",
                "menu_path": "Options > Viewer Font",
            },
        )
        app.cmd_viewer_font()
        while not result["done"]:
            app.update_idletasks()
            app.update()
            time.sleep(0.05)

        # ============================================================
        # 14. FTP CONNECTIONS (Network > FTP/SFTP)
        # ============================================================
        print("\n[14/30] Capturing FTP Connections Dialog...")
        result = capture_modal_dialog(
            app,
            capture,
            "Connections",
            "ftp-connections",
            generator,
            {
                "title": "FTP/SFTP Connections (Network > FTP/SFTP)",
                "purpose": "Manage FTP and SFTP remote connections for browsing remote filesystems.",
                "key_features": [
                    "Add/edit/delete connections",
                    "Protocol: FTP, FTPS, SFTP",
                    "Host, port, username, password/key",
                    "Passive/active mode for FTP",
                    "Key-based auth for SFTP",
                    "Connection testing",
                    "Mounts as virtual filesystem",
                ],
                "shortcuts": ["Network > FTP/SFTP Connections", "Escape — Close"],
                "usage": "Open Network > FTP/SFTP. Click Add to create new connection. Fill in details. Click Connect to test and mount. Remote paths appear in panel volume selector.",
                "menu_path": "Network > FTP/SFTP Connections",
            },
        )
        app.cmd_ftp_connections()
        while not result["done"]:
            app.update_idletasks()
            app.update()
            time.sleep(0.05)
        force_lumen_theme()

        # ============================================================
        # 14b. FTP NEW CONNECTION DIALOG (Network > FTP/SFTP > New...)
        # ============================================================
        print("\n[14b/30] Capturing FTP New Connection Dialog...")
        # Directly create the "New Connection" dialog using the same code as ftp_dialog.py
        # This avoids issues with button clicks and event handling

        # Create the dialog exactly as _edit_session does for new connections
        dialog = tk.Toplevel(app)
        dialog.title("New Connection")
        dialog.transient(app)
        dialog.resizable(False, False)

        # Protocol selector
        _PROTOCOLS = ("ftp", "sftp", "smb", "webdav", "webdavs", "jotta")
        _DEFAULT_PORTS = {"ftp": 21, "sftp": 22, "smb": 445, "webdav": 80, "webdavs": 443}

        ttk.Label(dialog, text="Protocol:").grid(row=0, column=0, padx=8, pady=8, sticky="w")
        protocol_var = tk.StringVar(value="ftp")
        protocol_combo = ttk.Combobox(
            dialog, textvariable=protocol_var, values=_PROTOCOLS, state="readonly", width=10
        )
        protocol_combo.grid(row=0, column=1, padx=8, pady=8, sticky="w")

        # Common fields
        ttk.Label(dialog, text="Name:").grid(row=1, column=0, padx=8, pady=8, sticky="w")
        name_var = tk.StringVar(value="")
        ttk.Entry(dialog, textvariable=name_var, width=40).grid(row=1, column=1, padx=8, pady=8)

        # FTP/SFTP/SMB/WebDAV fields frame
        ftp_frame = ttk.Frame(dialog)
        ftp_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=8, pady=4)

        ttk.Label(ftp_frame, text="Host:").grid(row=0, column=0, padx=8, pady=8, sticky="w")
        host_var = tk.StringVar(value="")
        ttk.Entry(ftp_frame, textvariable=host_var, width=40).grid(row=0, column=1, padx=8, pady=8)

        ttk.Label(ftp_frame, text="Port:").grid(row=1, column=0, padx=8, pady=8, sticky="w")
        port_var = tk.IntVar(value=21)
        ttk.Spinbox(ftp_frame, from_=1, to=65535, textvariable=port_var, width=10).grid(
            row=1, column=1, padx=8, pady=8, sticky="w"
        )

        ttk.Label(ftp_frame, text="User:").grid(row=2, column=0, padx=8, pady=8, sticky="w")
        user_var = tk.StringVar(value="anonymous")
        ttk.Entry(ftp_frame, textvariable=user_var, width=40).grid(row=2, column=1, padx=8, pady=8)

        ttk.Label(ftp_frame, text="Password:").grid(row=3, column=0, padx=8, pady=8, sticky="w")
        pass_var = tk.StringVar(value="")
        ttk.Entry(ftp_frame, textvariable=pass_var, width=40, show="*").grid(
            row=3, column=1, padx=8, pady=8
        )

        ttk.Label(ftp_frame, text="Path:").grid(row=4, column=0, padx=8, pady=8, sticky="w")
        path_var = tk.StringVar(value="/")
        ttk.Entry(ftp_frame, textvariable=path_var, width=40).grid(row=4, column=1, padx=8, pady=8)

        # SFTP-only: private key auth
        key_label = ttk.Label(ftp_frame, text="Private Key (SFTP):")
        key_label.grid(row=5, column=0, padx=8, pady=8, sticky="w")
        key_frame = ttk.Frame(ftp_frame)
        key_frame.grid(row=5, column=1, padx=8, pady=8, sticky="w")
        key_path_var = tk.StringVar(value="")
        key_entry = ttk.Entry(key_frame, textvariable=key_path_var, width=31)
        key_entry.pack(side="left")

        from tkinter import filedialog

        def _browse_key() -> None:
            path_str = filedialog.askopenfilename(parent=dialog, title="Select Private Key")
            if path_str:
                key_path_var.set(path_str)

        key_browse_btn = ttk.Button(key_frame, text="...", width=3, command=_browse_key)
        key_browse_btn.pack(side="left", padx=(4, 0))

        passphrase_label = ttk.Label(ftp_frame, text="Key Passphrase:")
        passphrase_label.grid(row=6, column=0, padx=8, pady=8, sticky="w")
        passphrase_var = tk.StringVar(value="")
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
        token_var = tk.StringVar(value="")
        entry = ttk.Entry(jotta_frame, textvariable=token_var, width=40, show="*")
        entry.grid(row=0, column=1, padx=8, pady=8)

        ttk.Label(jotta_frame, text="(Get from https://www.jottacloud.com/web/secure)").grid(
            row=1, column=0, columnspan=2, padx=8, pady=2, sticky="w"
        )

        ttk.Label(jotta_frame, text="Device:").grid(row=2, column=0, padx=8, pady=8, sticky="w")
        device_var = tk.StringVar(value="Jotta")
        ttk.Entry(jotta_frame, textvariable=device_var, width=40).grid(
            row=2, column=1, padx=8, pady=8
        )

        ttk.Label(jotta_frame, text="Mountpoint:").grid(row=3, column=0, padx=8, pady=8, sticky="w")
        mountpoint_var = tk.StringVar(value="Archive")
        ttk.Entry(jotta_frame, textvariable=mountpoint_var, width=40).grid(
            row=3, column=1, padx=8, pady=8
        )

        ttk.Label(jotta_frame, text="Path:").grid(row=4, column=0, padx=8, pady=8, sticky="w")
        jotta_path_var = tk.StringVar(value="/")
        ttk.Entry(jotta_frame, textvariable=jotta_path_var, width=40).grid(
            row=4, column=1, padx=8, pady=8
        )

        def _update_protocol_state(*_args: object) -> None:
            protocol = protocol_var.get()
            is_ftp_sftp = protocol in ("ftp", "sftp")
            is_smb_webdav = protocol in ("smb", "webdav", "webdavs")
            is_jotta = protocol == "jotta"

            # Show/hide FTP/SFTP/SMB/WebDAV fields
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

            # Set default port based on protocol
            if port_var.get() in _DEFAULT_PORTS.values():
                port_var.set(_DEFAULT_PORTS.get(protocol, port_var.get()))

        protocol_combo.bind("<<ComboboxSelected>>", _update_protocol_state)
        _update_protocol_state()

        btn_frame = ttk.Frame(dialog)
        btn_frame.grid(row=7, column=0, columnspan=2, pady=8)
        ttk.Button(btn_frame, text="OK", command=dialog.destroy).pack(side="right", padx=4)
        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side="right", padx=4)

        from linux_commander.dialogs import _center_over

        _center_over(dialog, app)
        dialog.grab_set()

        # Now capture the dialog
        time.sleep(0.2)
        path = capture.capture_window(dialog, "ftp-new-connection")
        generator.add_window(
            title="New Connection (Network > FTP/SFTP > New...)",
            screenshot_name=path.name,
            purpose="Create a new FTP, FTPS, SFTP, SMB, WebDAV, or Jottacloud connection.",
            key_features=[
                "Protocol selector: FTP, FTPS, SFTP, SMB, WebDAV, WebDAVS, Jottacloud",
                "Connection name",
                "Host, port (with default per protocol)",
                "Username (default: anonymous for FTP)",
                "Password field (hidden)",
                "Remote path (default: /)",
                "SFTP: Private key file with browse button",
                "SFTP: Key passphrase (optional)",
                "SMB: Share name",
                "WebDAV: Root path",
                "Jottacloud: Personal login token (get from https://www.jottacloud.com/web/secure)",
                "OK/Cancel buttons",
            ],
            shortcuts=["Network > FTP/SFTP > New...", "Escape — Cancel"],
            usage="In FTP Connections dialog, click New... Select protocol. Fill in host, port, user, password/key. For SFTP, optionally select private key file. For Jottacloud, enter login token. Click OK to save.",
            menu_path="Network > FTP/SFTP > New...",
        )
        dialog.destroy()

        # Also open and close the FTP Connections dialog to clean up
        for child in app.winfo_children():
            if isinstance(child, tk.Toplevel) and child.title() == "Connections":
                child.destroy()
                break

        force_lumen_theme()

        # ============================================================
        # 15. OPTIONAL DEPENDENCIES (File > Optional Dependencies)
        # ============================================================
        print("\n[15/30] Capturing Optional Dependencies Dialog...")
        result = capture_modal_dialog(
            app,
            capture,
            "Optional Dependencies",
            "optional-dependencies",
            generator,
            {
                "title": "Optional Dependencies (File > Optional Dependencies)",
                "purpose": "View and install optional Python packages for extended functionality.",
                "key_features": [
                    "Lists all optional dependency groups",
                    "Shows install status (installed/missing)",
                    "One-click install via pip",
                    "Groups: archives, images, documents, crypto, etc.",
                ],
                "shortcuts": ["File > Optional Dependencies", "Escape — Close"],
                "usage": "Open File > Optional Dependencies. See which extras are installed. Click Install to add missing packages. Restart app to activate.",
                "menu_path": "File > Optional Dependencies",
            },
        )
        app.cmd_optional_dependencies()
        while not result["done"]:
            app.update_idletasks()
            app.update()
            time.sleep(0.05)

        # ============================================================
        # 16. PLUGIN STATUS (File > Plugin Status)
        # ============================================================
        print("\n[16/30] Capturing Plugin Status Dialog...")
        sys.stdout.flush()
        result = capture_modal_dialog(
            app,
            capture,
            "Plugin Status",
            "plugin-status",
            generator,
            {
                "title": "Plugin Status (File > Plugin Status)",
                "purpose": "View discovered VFS, viewer, sort, codec, and conflict plugins with their status.",
                "key_features": [
                    "Lists all auto-discovered plugins",
                    "Shows plugin type (VFS extension, VFS scheme, viewer, sort, codec, conflict)",
                    "Shows supported extensions/schemes",
                    "Highlights failed imports",
                ],
                "shortcuts": ["File > Plugin Status", "Escape — Close"],
                "usage": "Open File > Plugin Status to see all loaded plugins. Failed plugins show error details. Useful for debugging missing optional dependencies.",
                "menu_path": "File > Plugin Status",
            },
        )
        app.cmd_plugin_status()
        while not result["done"]:
            app.update_idletasks()
            app.update()
            time.sleep(0.05)

        # ============================================================
        # 17. COMMAND SETTINGS (Options > Command Settings)
        # ============================================================
        print("\n[17/30] Capturing Command Settings Dialog...")
        result = capture_modal_dialog(
            app,
            capture,
            "Command Settings",
            "command-settings",
            generator,
            {
                "title": "Command Settings (Options > Command Settings)",
                "purpose": "Configure the terminal command used by the command prompt (F9 / Ctrl+Enter).",
                "key_features": [
                    "Separate commands for Linux and Windows",
                    "Placeholder {cmd} for the user's command",
                    "Example: gnome-terminal -- bash -c '{cmd}; exec bash'",
                ],
                "shortcuts": ["Options > Command Settings", "Escape — Close"],
                "usage": "Open Options > Command Settings. Edit the template for your terminal emulator. The {cmd} placeholder is replaced with your typed command.",
                "menu_path": "Options > Command Settings",
            },
        )
        app.cmd_command_settings()
        while not result["done"]:
            app.update_idletasks()
            app.update()
            time.sleep(0.05)

        # ============================================================
        # 18. FILE INFO (Shift+F3)
        # ============================================================
        print("\n[18/30] Capturing File Info Dialog...")
        for _ in range(20):
            entry = panel.cursor_entry()
            if entry and entry.name == "readme.txt":
                break
            panel.move_cursor(1)
        result = capture_modal_dialog(
            app,
            capture,
            "File Info",
            "file-info",
            generator,
            {
                "title": "File Info (Shift+F3)",
                "purpose": "Display detailed file metadata including checksums.",
                "key_features": [
                    "File type, size, permissions, owner",
                    "Modified/accessed/created timestamps",
                    "MD5, SHA1, SHA256, SHA512 checksums",
                    "MIME type detection",
                    "Copy to clipboard button",
                ],
                "shortcuts": ["Shift+F3 — File Info", "Escape — Close"],
                "usage": "Select a file and press Shift+F3. Computes checksums on demand. Click Copy to copy all info.",
            },
        )
        app._ops.cmd_file_info()
        while not result["done"]:
            app.update_idletasks()
            app.update()
            time.sleep(0.05)

        # ============================================================
        # 19. NEW FILE (File > New)
        # ============================================================
        print("\n[19/30] Capturing New File Dialog...")
        result = capture_modal_dialog(
            app,
            capture,
            "New File",
            "new-file",
            generator,
            {
                "title": "New File Dialog",
                "purpose": "Create a new empty file and open it in the editor.",
                "key_features": [
                    "Filename input with extension",
                    "Opens directly in editor (F4)",
                    "Creates parent directories if needed",
                ],
                "shortcuts": ["File > New", "F4 on empty selection (with prompt)"],
                "usage": "File > New. Enter filename. Press Enter to create and open in editor.",
                "menu_path": "File > New",
            },
        )
        app._ops.cmd_new_file()
        while not result["done"]:
            app.update_idletasks()
            app.update()
            time.sleep(0.05)

        # ============================================================
        # 20. COPY DIALOG (F5)
        # ============================================================
        print("\n[20/30] Capturing Copy Dialog...")
        for _ in range(20):
            entry = panel.cursor_entry()
            if entry and entry.name == "readme.txt":
                break
            panel.move_cursor(1)
        result = capture_modal_dialog(
            app,
            capture,
            "Copy",
            "copy-dialog",
            generator,
            {
                "title": "Copy Dialog (F5)",
                "purpose": "Copy selected files/directories to the target panel.",
                "key_features": [
                    "Shows source and destination paths",
                    "Overwrite options: skip, replace, compare, newer, rename",
                    "Preserve timestamps option",
                    "Follow symlinks option",
                    "Progress dialog with cancel",
                ],
                "shortcuts": ["F5 — Copy", "Escape — Cancel"],
                "usage": "Select files, press F5. Choose overwrite strategy. Click OK to start copy. Progress dialog appears.",
            },
        )
        app._ops.cmd_copy()
        while not result["done"]:
            app.update_idletasks()
            app.update()
            time.sleep(0.05)

        # ============================================================
        # 21. MOVE DIALOG (F6)
        # ============================================================
        print("\n[21/30] Capturing Move Dialog...")
        for _ in range(20):
            entry = panel.cursor_entry()
            if entry and entry.name == "readme.txt":
                break
            panel.move_cursor(1)
        result = capture_modal_dialog(
            app,
            capture,
            "Move",
            "move-dialog",
            generator,
            {
                "title": "Move Dialog (F6)",
                "purpose": "Move selected files/directories to the target panel.",
                "key_features": [
                    "Same options as Copy dialog",
                    "Source removed after successful move",
                    "Cross-device moves (copy+delete)",
                ],
                "shortcuts": ["F6 — Move", "Escape — Cancel"],
                "usage": "Select files, press F6. Configure target and options. Progress dialog appears.",
            },
        )
        app._ops.cmd_move()
        while not result["done"]:
            app.update_idletasks()
            app.update()
            time.sleep(0.05)

        # ============================================================
        # 22. COMPRESSION DIALOG (Shift+F5)
        # ============================================================
        print("\n[22/30] Capturing Compression Dialog...")
        for _ in range(20):
            entry = panel.cursor_entry()
            if entry and entry.name == "readme.txt":
                break
            panel.move_cursor(1)
        result = capture_modal_dialog(
            app,
            capture,
            "Compress Files",
            "compression-dialog",
            generator,
            {
                "title": "Compression Dialog (Shift+F5)",
                "purpose": "Create archives with container format, codec, level, and encryption.",
                "key_features": [
                    "Container: zip, tar, 7z, grp, iso, etc.",
                    "Codec: none, gzip, bzip2, xz, zstd",
                    "Compression level (1-9, or 1-22 for zstd)",
                    "Encryption: ChaCha20-Poly1305",
                    "Encrypt file names (7z)",
                    "Split archives (volume size)",
                ],
                "shortcuts": ["Shift+F5 — Compress", "Escape — Cancel"],
                "usage": "Select files, press Shift+F5. Choose container, codec, level. Enable encryption for .crp files. Click OK to create archive.",
            },
        )
        app._ops.cmd_compress()
        while not result["done"]:
            app.update_idletasks()
            app.update()
            time.sleep(0.05)

        # ============================================================
        # 23. SEARCH DIALOG (Alt+F7)
        # ============================================================
        print("\n[23/30] Capturing Search Dialog...")
        result = capture_modal_dialog(
            app,
            capture,
            "Search",
            "search-dialog",
            generator,
            {
                "title": "Search Dialog (Alt+F7)",
                "purpose": "Find files by name, content, size, date, with archive descent and regex support.",
                "key_features": [
                    "Name pattern (glob/regex)",
                    "Content search (text/regex, encoding-aware)",
                    "Size range filter",
                    "Date range filter (modified/accessed/created)",
                    "Archive descent (search inside archives)",
                    "Case sensitivity, whole word options",
                    "Results in dedicated panel",
                ],
                "shortcuts": ["Alt+F7 — Search", "Shift+F7 — Search Again", "Escape — Close"],
                "usage": "Press Alt+F7. Configure criteria. Click 'Search' to run in background. Results appear in a new panel. Double-click result to open.",
            },
        )
        app.cmd_search()
        while not result["done"]:
            app.update_idletasks()
            app.update()
            time.sleep(0.05)

        # ============================================================
        # 24. MKDIR DIALOG (F7)
        # ============================================================
        print("\n[24/30] Capturing Mkdir Dialog...")
        result = capture_modal_dialog(
            app,
            capture,
            "New Directory",
            "mkdir-dialog",
            generator,
            {
                "title": "New Directory Dialog (F7)",
                "purpose": "Create a new directory in the active panel.",
                "key_features": [
                    "Directory name input",
                    "Creates parent directories if needed",
                ],
                "shortcuts": ["F7 — New Directory", "Escape — Cancel"],
                "usage": "Press F7. Enter directory name. Press Enter or OK to create.",
            },
        )
        app._ops.cmd_mkdir()
        while not result["done"]:
            app.update_idletasks()
            app.update()
            time.sleep(0.05)

        # ============================================================
        # 25. DELETE DIALOG (F8)
        # ============================================================
        print("\n[25/30] Capturing Delete Dialog...")
        result = capture_modal_dialog(
            app,
            capture,
            "Delete",
            "delete-dialog",
            generator,
            {
                "title": "Delete Dialog (F8)",
                "purpose": "Delete selected files/directories with confirmation.",
                "key_features": [
                    "Shows count of selected items",
                    "Recursive delete for directories",
                    "No trash/recycle bin (permanent)",
                ],
                "shortcuts": ["F8 — Delete", "Escape — Cancel"],
                "usage": "Select files, press F8. Confirm deletion. Warning: permanent, no undo.",
            },
        )
        app._ops.cmd_delete()
        while not result["done"]:
            app.update_idletasks()
            app.update()
            time.sleep(0.05)

        # ============================================================
        # 26. COLUMNS DIALOG (Options > Columns)
        # ============================================================
        print("\n[26/30] Capturing Columns Dialog...")
        from linux_commander.columns_dialog import show_columns_dialog

        result = capture_modal_dialog(
            app,
            capture,
            "Columns",
            "columns-dialog",
            generator,
            {
                "title": "Columns Dialog (Options > Columns)",
                "purpose": "Configure which columns are visible in file panels and their order.",
                "key_features": [
                    "Available: Name, Size, Modified, Permissions, Owner, Group, Extension",
                    "Drag to reorder (listbox with up/down buttons)",
                    "Per-panel or global setting",
                    "Persists in settings",
                ],
                "shortcuts": ["Options > Columns"],
                "usage": "Open Options > Columns. Use Up/Down buttons to reorder. Check/uncheck to show/hide columns. Applies to active panel or both.",
                "menu_path": "Options > Columns",
            },
        )
        show_columns_dialog(app, app.left_panel)
        while not result["done"]:
            app.update_idletasks()
            app.update()
            time.sleep(0.05)

        # ============================================================
        # 27. HOTLIST/BOOKMARKS (Ctrl+\)
        # ============================================================
        print("\n[27/30] Capturing Hotlist Dialog...")
        from linux_commander.hotlist_dialog import show_hotlist

        result = capture_modal_dialog(
            app,
            capture,
            "Hotlist",
            "hotlist-dialog",
            generator,
            {
                "title": "Hotlist / Bookmarks (Ctrl+\\)",
                "purpose": "Manage directory bookmarks for quick navigation.",
                "key_features": [
                    "Add current directory to hotlist",
                    "Edit/remove bookmarks",
                    "Double-click to navigate (left/right panel)",
                    "Persists in settings",
                ],
                "shortcuts": ["Ctrl+\\ — Hotlist", "Escape — Close"],
                "usage": "Press Ctrl+\\ to open. Click Add to bookmark current directory. Double-click entry to navigate. Use buttons to edit/remove.",
            },
        )
        show_hotlist(app, app.left_panel, app.right_panel)
        while not result["done"]:
            app.update_idletasks()
            app.update()
            time.sleep(0.05)

        # ============================================================
        # 28. PROGRESS DIALOG
        # ============================================================
        print("\n[28/30] Capturing Progress Dialog...")
        from linux_commander.progress_dialog import ProgressDialog

        prog = ProgressDialog(app, "Testing Progress")
        prog.update(50, 100, "test_file.txt")
        prog_path = capture.capture_window(prog.top, "progress-dialog")
        prog.close()
        generator.add_window(
            title="Progress Dialog",
            screenshot_name=prog_path.name,
            purpose="Shows progress for long-running operations (copy, move, compress, search) with cancel option.",
            key_features=[
                "Progress bar with percentage",
                "Current file/item display",
                "Elapsed/remaining time estimate",
                "Cancel button (graceful abort)",
                "Auto-close on completion",
            ],
            shortcuts=["Escape — Cancel operation"],
            usage="Appears automatically during F5/F6/Shift+F5/Alt+F7 operations. Click Cancel to abort. Shows throughput and ETA.",
        )

        # ============================================================
        # 29. CONFLICT RESOLUTION DIALOG
        # ============================================================
        print("\n[29/30] Capturing Conflict Resolution Dialog...")
        from linux_commander.dialogs import _center_over

        # Create a mock conflict dialog window
        top = tk.Toplevel(app)
        top.title("File Conflicts")
        top.transient(app)
        top.resizable(True, True)
        top.grab_set()
        _center_over(top, app)

        # Mock conflict data
        header_frame = ttk.Frame(top, padding=8)
        header_frame.pack(fill="x")
        ttk.Label(
            header_frame,
            text="1 file(s) already exist at the destination.",
        ).pack(anchor="w")

        canvas = tk.Canvas(top, highlightthickness=0)
        scrollbar = ttk.Scrollbar(top, orient="vertical", command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)

        scroll_frame.bind(
            "<Configure>",
            lambda _e: canvas.configure(scrollregion=canvas.bbox("all")),
        )

        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True, padx=8, pady=4)
        scrollbar.pack(side="right", fill="y", pady=4)

        # Column headers
        hdr = ttk.Frame(scroll_frame)
        hdr.pack(fill="x", pady=(0, 2))
        ttk.Label(hdr, text="File", width=30, anchor="w").pack(side="left")
        ttk.Label(hdr, text="Source", width=12, anchor="w").pack(side="left")
        ttk.Label(hdr, text="Dest", width=12, anchor="w").pack(side="left")
        ttk.Label(hdr, text="Action", width=15, anchor="w").pack(side="left")

        # Mock conflict row
        row = ttk.Frame(scroll_frame)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="test.txt", width=30, anchor="w").pack(side="left")
        ttk.Label(row, text="100 B", width=12, anchor="w").pack(side="left")
        ttk.Label(row, text="200 B", width=12, anchor="w").pack(side="left")
        combo = ttk.Combobox(
            row,
            values=["Skip", "Replace", "Compare", "Newer", "Rename"],
            state="readonly",
            width=15,
        )
        combo.current(1)
        combo.pack(side="left")

        # Buttons
        btn_frame = ttk.Frame(top)
        btn_frame.pack(fill="x", pady=8)
        ttk.Button(btn_frame, text="OK", command=top.destroy).pack(side="right", padx=4)
        ttk.Button(btn_frame, text="Cancel", command=top.destroy).pack(side="right", padx=4)

        conflict_path = capture.capture_window(top, "conflict-dialog")
        top.destroy()
        generator.add_window(
            title="Conflict Resolution Dialog",
            screenshot_name=conflict_path.name,
            purpose="Resolve file conflicts during copy/move when destination exists.",
            key_features=[
                "Strategies: Skip, Replace, Compare, Newer, Rename",
                "Apply to all checkbox",
                "Shows source vs dest size/date",
                "Preview mode for Compare",
            ],
            shortcuts=["Appears automatically during copy/move", "Escape — Cancel"],
            usage="Appears when copying/moving to a location with existing files. Choose strategy. 'Apply to all' uses same choice for remaining conflicts.",
        )

        # ============================================================
        # 30. DIFF VIEWER
        # ============================================================
        print("\n[30/30] Capturing Diff Viewer...")
        from linux_commander.diff_viewer import compare_directories

        compare_directories(app, left, right)
        diff_win = wait_for_window(app, "Directory Comparison")
        if diff_win:
            diff_path = capture.capture_window(diff_win, "diff-viewer")
            generator.add_window(
                title="Directory Diff Viewer",
                screenshot_name=diff_path.name,
                purpose="Compare two directories side-by-side with differences highlighted.",
                key_features=[
                    "Three-way comparison (left only, right only, different)",
                    "Color-coded: green=new, red=removed, yellow=modified",
                    "Size/date comparison",
                    "Double-click to open file pair in diff",
                    "Sync scrolling",
                ],
                shortcuts=["Operations > Compare Directories", "Escape — Close"],
                usage="Select Operations > Compare Directories. Choose two directories. View differences. Double-click a file to see detailed diff.",
            )
            diff_win.destroy()

        # ============================================================
        # 31. HOTLIST/BOOKMARKS (Operations > Hotlist / Ctrl+\)
        # ============================================================
        print("\n[31/44] Capturing Hotlist Dialog...")
        result = capture_modal_dialog(
            app,
            capture,
            "Hotlist",
            "hotlist-dialog",
            generator,
            {
                "title": "Hotlist / Bookmarks (Operations > Hotlist / Ctrl+\\)",
                "purpose": "Manage bookmarked directories for quick navigation.",
                "key_features": [
                    "Add current directory to hotlist",
                    "Double-click to navigate to bookmarked directory",
                    "Edit/remove entries",
                    "Reorder with Up/Down buttons",
                ],
                "shortcuts": ["Operations > Hotlist", "Ctrl+\\ — Open hotlist", "Escape — Close"],
                "usage": "Press Ctrl+\\ or select Operations > Hotlist. Double-click an entry to jump to that directory. Use Add/Edit/Remove buttons to manage bookmarks.",
                "menu_path": "Operations > Hotlist (Bookmarks)",
            },
        )
        app._show_hotlist()
        while not result["done"]:
            app.update_idletasks()
            app.update()
            time.sleep(0.05)

        # ============================================================
        # 32. COMPARE FILES (Operations > Compare Files)
        # ============================================================
        print("\n[32/44] Capturing Compare Files Dialog...")
        from linux_commander.app import CommanderApp

        left_sel = app.left_panel.selected_entries()
        right_sel = app.right_panel.selected_entries()
        if len(left_sel + right_sel) >= 2:
            result = capture_modal_dialog(
                app,
                capture,
                "Compare Files",
                "compare-files-dialog",
                generator,
                {
                    "title": "Compare Files (Operations > Compare Files)",
                    "purpose": "Compare two selected files side-by-side with differences highlighted.",
                    "key_features": [
                        "Side-by-side diff view",
                        "Color-coded changes (added/removed/modified)",
                        "Line numbers",
                        "Sync scrolling",
                    ],
                    "shortcuts": ["Operations > Compare Files", "Escape — Close"],
                    "usage": "Select exactly two files (one from each panel or two from same panel). Choose Operations > Compare Files. View differences. Double-click to open in diff viewer.",
                    "menu_path": "Operations > Compare Files",
                },
            )
            app._compare_selected_files()
            while not result["done"]:
                app.update_idletasks()
                app.update()
                time.sleep(0.05)

        # ============================================================
        # 33. COMPARE DIRECTORIES (Operations > Compare Directories)
        # ============================================================
        print("\n[33/44] Capturing Compare Directories Dialog...")
        from linux_commander.diff_viewer import compare_directories

        result = capture_modal_dialog(
            app,
            capture,
            "Select Directories",
            "compare-directories-select-dialog",
            generator,
            {
                "title": "Compare Directories Selection (Operations > Compare Directories)",
                "purpose": "Select two directories to compare.",
                "key_features": [
                    "Browse for left and right directory",
                    "Shows current panel paths as defaults",
                ],
                "shortcuts": ["Operations > Compare Directories", "Escape — Cancel"],
                "usage": "Choose Operations > Compare Directories. Select two directories to compare. Click OK to open the diff viewer.",
                "menu_path": "Operations > Compare Directories",
            },
        )
        app._compare_directories()
        while not result["done"]:
            app.update_idletasks()
            app.update()
            time.sleep(0.05)

        # ============================================================
        # 34. BASE64 ENCODE/DECODE (Operations > Base64 Encode/Decode)
        # ============================================================
        print("\n[34/44] Capturing Base64 Operation...")
        from linux_commander.file_ops import available_operations

        ops = {op.name: op for op in available_operations()}
        if "Base64 Encode" in ops:
            # Base64 Encode has no prepare dialog - just run it
            result = capture_modal_dialog(
                app,
                capture,
                "Progress",
                "base64-encode-progress",
                generator,
                {
                    "title": "Base64 Encode (Operations > Base64 Encode)",
                    "purpose": "Encode selected files to Base64 format.",
                    "key_features": [
                        "Creates .b64 files next to originals",
                        "Progress dialog with cancel",
                        "Works on multiple files",
                    ],
                    "shortcuts": ["Operations > Base64 Encode"],
                    "usage": "Select one or more files. Choose Operations > Base64 Encode. Files are encoded to .b64 format in the same directory.",
                    "menu_path": "Operations > Base64 Encode",
                },
            )
            ops["Base64 Encode"].run(
                [e.path for e in app.active_panel.selected_entries()],
                app.active_panel.current_path,
                lambda *a: None,
                lambda: False,
            )
            while not result["done"]:
                app.update_idletasks()
                app.update()
                time.sleep(0.05)

        # ============================================================
        # 35. CHECKSUMS (Operations > Checksums)
        # ============================================================
        print("\n[35/44] Capturing Checksums Dialog...")
        if "Checksums" in ops:
            result = capture_modal_dialog(
                app,
                capture,
                "Checksums",
                "checksums-dialog",
                generator,
                {
                    "title": "Checksums (Operations > Checksums)",
                    "purpose": "Calculate and verify file checksums (MD5, SHA1, SHA256, etc.).",
                    "key_features": [
                        "Multiple algorithms: MD5, SHA1, SHA256, SHA512, BLAKE2",
                        "Save to .md5/.sha1/.sha256 files",
                        "Verify against existing checksum files",
                        "Progress dialog with cancel",
                    ],
                    "shortcuts": ["Operations > Checksums", "Escape — Cancel"],
                    "usage": "Select files. Choose Operations > Checksums. Select algorithm and options. Click OK to compute.",
                    "menu_path": "Operations > Checksums",
                },
            )
            ops["Checksums"].prepare(app, [e.path for e in app.active_panel.selected_entries()])
            while not result["done"]:
                app.update_idletasks()
                app.update()
                time.sleep(0.05)

        # ============================================================
        # 36. ENCRYPT (Operations > Encrypt)
        # ============================================================
        print("\n[36/44] Capturing Encrypt Dialog...")
        if "Encrypt" in ops:
            result = capture_modal_dialog(
                app,
                capture,
                "Encrypt",
                "encrypt-dialog",
                generator,
                {
                    "title": "Encrypt (Operations > Encrypt)",
                    "purpose": "Encrypt selected files using ChaCha20-Poly1305.",
                    "key_features": [
                        "ChaCha20-Poly1305 authenticated encryption",
                        "Password or stored key",
                        "Creates .crp files",
                        "Progress dialog with cancel",
                    ],
                    "shortcuts": ["Operations > Encrypt", "Escape — Cancel"],
                    "usage": "Select files. Choose Operations > Encrypt. Enter password or choose stored key. Click OK to encrypt.",
                    "menu_path": "Operations > Encrypt",
                },
            )
            ops["Encrypt"].prepare(app, [e.path for e in app.active_panel.selected_entries()])
            while not result["done"]:
                app.update_idletasks()
                app.update()
                time.sleep(0.05)

        # ============================================================
        # 37. DECRYPT (Operations > Decrypt)
        # ============================================================
        print("\n[37/44] Capturing Decrypt Dialog...")
        if "Decrypt" in ops:
            result = capture_modal_dialog(
                app,
                capture,
                "Decrypt",
                "decrypt-dialog",
                generator,
                {
                    "title": "Decrypt (Operations > Decrypt)",
                    "purpose": "Decrypt .crp files using password or stored key.",
                    "key_features": [
                        "ChaCha20-Poly1305 decryption",
                        "Password or stored key",
                        "Restores original filename",
                        "Progress dialog with cancel",
                    ],
                    "shortcuts": ["Operations > Decrypt", "Escape — Cancel"],
                    "usage": "Select .crp files. Choose Operations > Decrypt. Enter password or choose stored key. Click OK to decrypt.",
                    "menu_path": "Operations > Decrypt",
                },
            )
            ops["Decrypt"].prepare(app, [e.path for e in app.active_panel.selected_entries()])
            while not result["done"]:
                app.update_idletasks()
                app.update()
                time.sleep(0.05)

        # ============================================================
        # 38. FIND DUPLICATES (Operations > Find Duplicates)
        # ============================================================
        print("\n[38/44] Capturing Find Duplicates Dialog...")
        if "Find Duplicates..." in ops:
            result = capture_modal_dialog(
                app,
                capture,
                "Find Duplicates",
                "find-duplicates-dialog",
                generator,
                {
                    "title": "Find Duplicates (Operations > Find Duplicates)",
                    "purpose": "Find duplicate files by content hash.",
                    "key_features": [
                        "MD5/SHA256 content hashing",
                        "Minimum file size filter",
                        "Results in new panel",
                        "Group by hash",
                    ],
                    "shortcuts": ["Operations > Find Duplicates", "Escape — Cancel"],
                    "usage": "Choose Operations > Find Duplicates. Set minimum size. Click Search. Results appear in a new panel grouped by hash.",
                    "menu_path": "Operations > Find Duplicates",
                },
            )
            ops["Find Duplicates..."].prepare(
                app, [e.path for e in app.active_panel.selected_entries()]
            )
            while not result["done"]:
                app.update_idletasks()
                app.update()
                time.sleep(0.05)

        # ============================================================
        # 39. CREATE FLOPPY IMAGE (Operations > Create Floppy Image)
        # ============================================================
        print("\n[39/44] Capturing Create Floppy Image Dialog...")
        if "Create Floppy Image" in ops:
            result = capture_modal_dialog(
                app,
                capture,
                "Create Floppy Image",
                "create-floppy-dialog",
                generator,
                {
                    "title": "Create Floppy Image (Operations > Create Floppy Image)",
                    "purpose": "Create a FAT12/FAT16 floppy disk image from selected files.",
                    "key_features": [
                        "360KB, 720KB, 1.44MB, 2.88MB floppy images",
                        "FAT12/FAT16 formatting",
                        "Boot sector support",
                        "Long filename (VFAT) support",
                    ],
                    "shortcuts": ["Operations > Create Floppy Image", "Escape — Cancel"],
                    "usage": "Select files. Choose Operations > Create Floppy Image. Configure image size (360K/720K/1.44M/2.88M) and format. Click OK to create .img file.",
                    "menu_path": "Operations > Create Floppy Image",
                },
            )
            ops["Create Floppy Image"].prepare(
                app, [e.path for e in app.active_panel.selected_entries()]
            )
            while not result["done"]:
                app.update_idletasks()
                app.update()
                time.sleep(0.05)

        # ============================================================
        # 40. BATCH RENAME (Operations > Batch Rename)
        # ============================================================
        print("\n[40/44] Capturing Batch Rename Dialog...")
        if "Batch Rename..." in ops:
            result = capture_modal_dialog(
                app,
                capture,
                "Batch Rename",
                "batch-rename-dialog",
                generator,
                {
                    "title": "Batch Rename (Operations > Batch Rename)",
                    "purpose": "Rename multiple files using patterns and replacements.",
                    "key_features": [
                        "Find/replace with regex support",
                        "Numbering/sequencing",
                        "Case conversion",
                        "Prefix/suffix addition",
                        "Preview before applying",
                    ],
                    "shortcuts": ["Operations > Batch Rename", "Escape — Cancel"],
                    "usage": "Select files. Choose Operations > Batch Rename. Configure pattern and replacement. Preview changes. Click Rename to apply.",
                    "menu_path": "Operations > Batch Rename",
                },
            )
            ops["Batch Rename..."].prepare(
                app, [e.path for e in app.active_panel.selected_entries()]
            )
            while not result["done"]:
                app.update_idletasks()
                app.update()
                time.sleep(0.05)

        # ============================================================
        # 41. SEARCH & REPLACE (Operations > Search & Replace)
        # ============================================================
        print("\n[41/44] Capturing Search & Replace Dialog...")
        if "Search & Replace..." in ops:
            result = capture_modal_dialog(
                app,
                capture,
                "Search & Replace",
                "search-replace-dialog",
                generator,
                {
                    "title": "Search & Replace (Operations > Search & Replace)",
                    "purpose": "Perform text search and replace across multiple files.",
                    "key_features": [
                        "Regex pattern support",
                        "Case sensitive/insensitive",
                        "File extension filter",
                        "Preview changes",
                        "Backup option",
                    ],
                    "shortcuts": ["Operations > Search & Replace", "Escape — Cancel"],
                    "usage": "Select files or directories. Choose Operations > Search & Replace. Enter search pattern and replacement. Preview. Click Replace.",
                    "menu_path": "Operations > Search & Replace",
                },
            )
            ops["Search & Replace..."].prepare(
                app, [e.path for e in app.active_panel.selected_entries()]
            )
            while not result["done"]:
                app.update_idletasks()
                app.update()
                time.sleep(0.05)

        # ============================================================
        # 42. SYNCHRONIZE DIRECTORIES (Operations > Synchronize Directories)
        # ============================================================
        print("\n[42/44] Capturing Synchronize Directories Dialog...")
        if "Synchronize Directories" in ops:
            result = capture_modal_dialog(
                app,
                capture,
                "Synchronize Directories",
                "sync-directories-dialog",
                generator,
                {
                    "title": "Synchronize Directories (Operations > Synchronize Directories)",
                    "purpose": "Sync two directories with configurable options.",
                    "key_features": [
                        "Mirror, update, or bidirectional sync",
                        "Include/exclude patterns",
                        "Delete orphaned files option",
                        "Dry-run preview",
                        "Progress with throughput/ETA",
                    ],
                    "shortcuts": ["Operations > Synchronize Directories", "Escape — Cancel"],
                    "usage": "Select source and destination directories. Choose Operations > Synchronize Directories. Configure sync mode and options. Preview. Click Sync.",
                    "menu_path": "Operations > Synchronize Directories",
                },
            )
            ops["Synchronize Directories"].prepare(
                app, [e.path for e in app.active_panel.selected_entries()]
            )
            while not result["done"]:
                app.update_idletasks()
                app.update()
                time.sleep(0.05)

        # ============================================================
        # 43. ADD CURRENT DIR TO HOTLIST (Operations > Add Current Dir to Hotlist)
        # ============================================================
        print("\n[43/44] Capturing Add to Hotlist...")
        result = capture_modal_dialog(
            app,
            capture,
            "Add to Hotlist",
            "add-to-hotlist-dialog",
            generator,
            {
                "title": "Add to Hotlist (Operations > Add Current Dir to Hotlist)",
                "purpose": "Add the current directory to the hotlist/bookmarks.",
                "key_features": [
                    "Auto-fills current directory path",
                    "Editable name",
                    "Quick bookmark creation",
                ],
                "shortcuts": ["Operations > Add Current Dir to Hotlist", "Escape — Cancel"],
                "usage": "Navigate to desired directory. Choose Operations > Add Current Dir to Hotlist. Edit name if needed. Click OK.",
                "menu_path": "Operations > Add Current Dir to Hotlist",
            },
        )
        app.active_panel.add_current_dir_to_hotlist()
        while not result["done"]:
            app.update_idletasks()
            app.update()
            time.sleep(0.05)

        # ============================================================
        # 44. VIEWER MODES MENU (F3 on various files)
        # ============================================================
        print("\n[44/44] Capturing Viewer Modes Menu...")

        for _ in range(20):
            entry = panel.cursor_entry()
            if entry and entry.name == "script.py":
                break
            panel.move_cursor(1)
        app.cmd_view()
        viewer_win = wait_for_window(app, "script.py")
        if viewer_win:
            # Open View menu to show modes
            viewer_win.update_idletasks()
            viewer_win.update()
            # Get the menubar
            if viewer_win.winfo_children():
                # Capture with View menu open
                pass
            viewer_path = capture.capture_window(viewer_win, "viewer-modes-menu")
            generator.add_window(
                title="Viewer Modes (View Menu in F3 Viewer)",
                screenshot_name=viewer_path.name,
                purpose="Switch between viewer display modes: Text, Hex, CSV Table, JSON, Strings.",
                key_features=[
                    "Text mode with syntax highlighting",
                    "Hex dump with ASCII column",
                    "CSV/TSV table view with sorting",
                    "JSON pretty-print with collapsible nodes",
                    "Strings extraction from binary files",
                    "Line numbers, word wrap, encoding toggle",
                ],
                shortcuts=["F3 — Open viewer", "View menu — Switch modes", "Escape — Close viewer"],
                usage="Press F3 on any file. Use View menu to switch between display modes. Each mode has its own submenu with options.",
            )
            viewer_win.destroy()

        # Clean up
        app.destroy()

        # Generate manual
        print("\n" + "=" * 60)
        print("Generating user manual...")
        generator.save(MANUAL_PATH)

    print("\nDone! Manual at:", MANUAL_PATH)
    print("Screenshots in:", SCREENSHOTS_DIR)


if __name__ == "__main__":
    main()
