"""File Compare (Diff) Viewer — side-by-side or unified diff with syntax highlighting.

Uses difflib for text diffing. Supports:
- Side-by-side view (two panels with synchronized scrolling)
- Unified diff view (single panel with +/- lines)
- Syntax highlighting of diff hunks (red/green for changes)
- Navigation between changes (Prev/Next buttons)
- Line numbers, word wrap toggle
- External diff tool integration (meld, vimdiff, etc.)
"""

from __future__ import annotations

import difflib
import queue
import subprocess
import tempfile
import threading
import tkinter as tk
from dataclasses import dataclass
from enum import Enum
from tkinter import messagebox, ttk

from linux_commander.dialogs import _center_over
from linux_commander.vfs import VfsPath


class DiffMode(Enum):
    SIDE_BY_SIDE = "Side by Side"
    UNIFIED = "Unified"


class DiffViewMode(Enum):
    TEXT = "Text"
    SYNTAX_HIGHLIGHTED = "Syntax Highlighted"


@dataclass
class DiffHunk:
    """A single diff hunk with line ranges."""

    a_start: int
    a_len: int
    b_start: int
    b_len: int
    lines: list[tuple[str, str]]  # (type, text) where type is ' ', '-', '+', '?'


@dataclass
class DiffResult:
    """Complete diff result between two texts."""

    hunks: list[DiffHunk]
    a_lines: list[str]
    b_lines: list[str]


def _read_text(path: VfsPath, max_bytes: int = 2 * 1024 * 1024) -> str:
    """Read file as text with UTF-8 fallback."""
    try:
        raw = path.fs.read_prefix(path, max_bytes)[0]
        return raw.decode("utf-8", errors="replace")
    except Exception as e:
        return f"[Error reading file: {e}]"


def _split_lines(text: str) -> list[str]:
    """Split text into lines, keeping trailing newlines."""
    return text.splitlines(keepends=True)


def compute_diff(text_a: str, text_b: str) -> DiffResult:
    """Compute diff between two texts using difflib.SequenceMatcher."""
    a_lines = _split_lines(text_a)
    b_lines = _split_lines(text_b)

    matcher = difflib.SequenceMatcher(None, a_lines, b_lines)
    hunks = []

    for tag, a_start, a_end, b_start, b_end in matcher.get_opcodes():
        if tag == "equal":
            # Just add as a single hunk with context
            lines = [(" ", line) for line in a_lines[a_start:a_end]]
            hunks.append(DiffHunk(a_start, a_end - a_start, b_start, b_end - b_start, lines))
        elif tag == "replace":
            a_chunk = a_lines[a_start:a_end]
            b_chunk = b_lines[b_start:b_end]
            # Use difflib.ndiff for intra-line diff within the chunk
            diff = list(difflib.ndiff(a_chunk, b_chunk))
            lines = []
            for d in diff:
                if d.startswith("  "):
                    lines.append((" ", d[2:]))
                elif d.startswith("- "):
                    lines.append(("-", d[2:]))
                elif d.startswith("+ "):
                    lines.append(("+", d[2:]))
                elif d.startswith("? "):
                    # Intra-line diff marker - skip for now
                    pass
            hunks.append(DiffHunk(a_start, a_end - a_start, b_start, b_end - b_start, lines))
        elif tag == "delete":
            lines = [("-", line) for line in a_lines[a_start:a_end]]
            hunks.append(DiffHunk(a_start, a_end - a_start, b_start, 0, lines))
        elif tag == "insert":
            lines = [("+", line) for line in b_lines[b_start:b_end]]
            hunks.append(DiffHunk(a_start, 0, b_start, b_end - b_start, lines))

    return DiffResult(hunks=hunks, a_lines=a_lines, b_lines=b_lines)


class DiffViewer(tk.Toplevel):
    """Main diff viewer window with side-by-side or unified view."""

    def __init__(
        self,
        parent: tk.Misc,
        path_a: VfsPath,
        path_b: VfsPath,
        title: str = "File Compare",
    ) -> None:
        super().__init__(parent)
        self.title(title)
        self.path_a = path_a
        self.path_b = path_b
        self.diff_result: DiffResult | None = None
        self.current_hunk_index = 0
        self._build_ui()
        _center_over(self, parent)
        self._load_and_diff()
        self.grab_set()
        self.wait_window()

    def _build_ui(self) -> None:
        self.geometry("1200x800")
        self.minsize(800, 600)

        # Toolbar
        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", padx=4, pady=4)

        ttk.Button(toolbar, text="Prev Change", command=self._prev_change).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Next Change", command=self._next_change).pack(side="left", padx=2)
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=8)

        ttk.Label(toolbar, text="View:").pack(side="left", padx=(8, 2))
        self.mode_var = tk.StringVar(value=DiffMode.SIDE_BY_SIDE.value)
        mode_combo = ttk.Combobox(
            toolbar,
            textvariable=self.mode_var,
            width=15,
            state="readonly",
            values=[m.value for m in DiffMode],
        )
        mode_combo.pack(side="left", padx=2)
        mode_combo.bind("<<ComboboxSelected>>", lambda e: self._rebuild_view())

        ttk.Label(toolbar, text="Highlight:").pack(side="left", padx=(8, 2))
        self.highlight_var = tk.StringVar(value=DiffViewMode.SYNTAX_HIGHLIGHTED.value)
        hl_combo = ttk.Combobox(
            toolbar,
            textvariable=self.highlight_var,
            width=20,
            state="readonly",
            values=[m.value for m in DiffViewMode],
        )
        hl_combo.pack(side="left", padx=2)
        hl_combo.bind("<<ComboboxSelected>>", lambda e: self._rebuild_view())

        self.wrap_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            toolbar, text="Wrap Lines", variable=self.wrap_var, command=self._toggle_wrap
        ).pack(side="left", padx=(8, 2))

        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=8)

        ttk.Button(toolbar, text="Open in Meld", command=self._open_meld).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Open in Vimdiff", command=self._open_vimdiff).pack(
            side="left", padx=2
        )
        ttk.Button(toolbar, text="Save Patch...", command=self._save_patch).pack(
            side="left", padx=2
        )

        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=8)

        self.hunk_label = ttk.Label(toolbar, text="Hunk 0 / 0")
        self.hunk_label.pack(side="right", padx=8)

        # Main content area
        self.content_frame = ttk.Frame(self)
        self.content_frame.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        # Status bar
        self.status_var = tk.StringVar(value="")
        status_bar = ttk.Label(self, textvariable=self.status_var, relief="sunken", anchor="w")
        status_bar.pack(fill="x", side="bottom", padx=4, pady=(0, 4))

        # Bind keys
        self.bind("<Prior>", lambda e: self._prev_change())  # Page Up
        self.bind("<Next>", lambda e: self._next_change())  # Page Down
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Left>", lambda e: self._prev_change())
        self.bind("<Right>", lambda e: self._next_change())

    def _load_and_diff(self) -> None:
        """Load both files and compute diff."""
        self.status_var.set("Loading files...")
        self.update_idletasks()

        text_a = _read_text(self.path_a)
        text_b = _read_text(self.path_b)

        self.status_var.set("Computing diff...")
        self.update_idletasks()

        # Run diff in background to keep UI responsive
        import queue

        diff_queue: queue.Queue[DiffResult | Exception] = queue.Queue()

        def compute_in_background():
            try:
                result = compute_diff(text_a, text_b)
                diff_queue.put(result)
            except Exception as e:
                diff_queue.put(e)

        thread = threading.Thread(target=compute_in_background, daemon=True)
        thread.start()

        def check_diff():
            try:
                result = diff_queue.get_nowait()
                if isinstance(result, Exception):
                    self.status_var.set(f"Error: {result}")
                    messagebox.showerror("Diff Error", str(result), parent=self)
                    return
                self.diff_result = result
                self.current_hunk_index = 0
                self._rebuild_view()
                self._update_hunk_label()
                self.status_var.set(f"Done — {len(self.diff_result.hunks)} hunks")
            except queue.Empty:
                # Still computing, check again
                self.after(50, check_diff)

        check_diff()

    def _rebuild_view(self) -> None:
        """Rebuild the view based on current mode."""
        # Clear existing widgets
        for widget in self.content_frame.winfo_children():
            widget.destroy()

        if self.diff_result is None:
            return

        if self.mode_var.get() == DiffMode.SIDE_BY_SIDE.value:
            self._build_side_by_side()
        else:
            self._build_unified()

    def _build_side_by_side(self) -> None:
        """Build side-by-side diff view."""
        # Paned window for two text areas
        paned = ttk.PanedWindow(self.content_frame, orient="horizontal")
        paned.pack(fill="both", expand=True)

        # Left panel (File A)
        left_frame = ttk.Frame(paned)
        paned.add(left_frame, weight=1)

        ttk.Label(left_frame, text=f"← {self.path_a.name}", foreground="blue").pack(anchor="w")

        left_text_frame = ttk.Frame(left_frame)
        left_text_frame.pack(fill="both", expand=True)

        self.text_a = tk.Text(
            left_text_frame, wrap="none", font=("Monospace", 10), undo=False, maxundo=0
        )
        self.text_a.pack(side="left", fill="both", expand=True)

        left_vsb = ttk.Scrollbar(left_text_frame, orient="vertical", command=self._on_scroll_a)
        left_vsb.pack(side="right", fill="y")
        self.text_a.configure(yscrollcommand=lambda *args: self._on_text_a_scroll(left_vsb, *args))

        left_hsb = ttk.Scrollbar(left_frame, orient="horizontal", command=self.text_a.xview)
        left_hsb.pack(fill="x")
        self.text_a.configure(xscrollcommand=left_hsb.set)

        # Right panel (File B)
        right_frame = ttk.Frame(paned)
        paned.add(right_frame, weight=1)

        ttk.Label(right_frame, text=f"{self.path_b.name} →", foreground="blue").pack(anchor="e")

        right_text_frame = ttk.Frame(right_frame)
        right_text_frame.pack(fill="both", expand=True)

        self.text_b = tk.Text(
            right_text_frame, wrap="none", font=("Monospace", 10), undo=False, maxundo=0
        )
        self.text_b.pack(side="left", fill="both", expand=True)

        right_vsb = ttk.Scrollbar(right_text_frame, orient="vertical", command=self._on_scroll_b)
        right_vsb.pack(side="right", fill="y")
        self.text_b.configure(yscrollcommand=lambda *args: self._on_text_b_scroll(right_vsb, *args))

        right_hsb = ttk.Scrollbar(right_frame, orient="horizontal", command=self.text_b.xview)
        right_hsb.pack(fill="x")
        self.text_b.configure(xscrollcommand=right_hsb.set)

        # Configure tags for diff highlighting
        self._configure_diff_tags(self.text_a)
        self._configure_diff_tags(self.text_b)

        # Fill content
        self._fill_side_by_side()

        # Scroll to first change
        self._scroll_to_hunk(0)

    def _build_unified(self) -> None:
        """Build unified diff view."""
        frame = ttk.Frame(self.content_frame)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame, text=f"Unified Diff: {self.path_a.name} ↔ {self.path_b.name}", foreground="blue"
        ).pack(anchor="w")

        text_frame = ttk.Frame(frame)
        text_frame.pack(fill="both", expand=True)

        self.text_unified = tk.Text(
            text_frame, wrap="none", font=("Monospace", 10), undo=False, maxundo=0
        )
        self.text_unified.pack(side="left", fill="both", expand=True)

        vsb = ttk.Scrollbar(text_frame, orient="vertical", command=self.text_unified.yview)
        vsb.pack(side="right", fill="y")
        self.text_unified.configure(yscrollcommand=vsb.set)

        hsb = ttk.Scrollbar(frame, orient="horizontal", command=self.text_unified.xview)
        hsb.pack(fill="x")
        self.text_unified.configure(xscrollcommand=hsb.set)

        # Configure tags
        self._configure_diff_tags(self.text_unified)

        # Fill content
        self._fill_unified()

        # Scroll to first change
        self._scroll_to_hunk(0)

    def _configure_diff_tags(self, text_widget: tk.Text) -> None:
        """Configure tags for diff highlighting."""
        text_widget.tag_configure("diff_add", background="#d4edda", foreground="#155724")
        text_widget.tag_configure("diff_remove", background="#f8d7da", foreground="#721c24")
        text_widget.tag_configure("diff_change", background="#fff3cd", foreground="#856404")
        text_widget.tag_configure("diff_context", foreground="#6c757d")
        text_widget.tag_configure(
            "diff_hunk_header", background="#e9ecef", font=("Monospace", 10, "bold")
        )
        text_widget.tag_configure("diff_linenum", foreground="#6c757d")

        # Syntax highlighting tags (will be applied per-language)
        # These will be configured by apply_highlighting

    def _fill_side_by_side(self) -> None:
        """Fill side-by-side text widgets with diff content."""
        if self.diff_result is None:
            return

        self.text_a.delete("1.0", "end")
        self.text_b.delete("1.0", "end")

        a_line_num = 1
        b_line_num = 1

        for hunk in self.diff_result.hunks:
            for tag, line in hunk.lines:
                if tag == " ":
                    # Context line - appears in both
                    self.text_a.insert("end", f"{a_line_num:6d} ", "diff_linenum")
                    self.text_a.insert("end", line, "diff_context")
                    self.text_b.insert("end", f"{b_line_num:6d} ", "diff_linenum")
                    self.text_b.insert("end", line, "diff_context")
                    a_line_num += 1
                    b_line_num += 1
                elif tag == "-":
                    # Removed line - only in A
                    self.text_a.insert("end", f"{a_line_num:6d} ", "diff_linenum")
                    self.text_a.insert("end", line, "diff_remove")
                    self.text_b.insert("end", "      ", "diff_linenum")
                    self.text_b.insert("end", "\n", "diff_context")
                    a_line_num += 1
                elif tag == "+":
                    # Added line - only in B
                    self.text_a.insert("end", "      ", "diff_linenum")
                    self.text_a.insert("end", "\n", "diff_context")
                    self.text_b.insert("end", f"{b_line_num:6d} ", "diff_linenum")
                    self.text_b.insert("end", line, "diff_add")
                    b_line_num += 1

        # Apply syntax highlighting if enabled
        if self.highlight_var.get() == DiffViewMode.SYNTAX_HIGHLIGHTED.value:
            self._apply_syntax_highlighting()

        # Make read-only
        self.text_a.config(state="disabled")
        self.text_b.config(state="disabled")

    def _fill_unified(self) -> None:
        """Fill unified diff text widget."""
        if self.diff_result is None:
            return

        self.text_unified.delete("1.0", "end")

        # Header
        self.text_unified.insert("end", f"--- {self.path_a}\n", "diff_remove")
        self.text_unified.insert("end", f"+++ {self.path_b}\n", "diff_add")

        for _i, hunk in enumerate(self.diff_result.hunks):
            # Hunk header
            a_start = hunk.a_start + 1
            b_start = hunk.b_start + 1

            self.text_unified.insert(
                "end", f"@@ -{a_start},{hunk.a_len} +{b_start},{hunk.b_len} @@", "diff_hunk_header"
            )

            # Context before first change
            for tag, line in hunk.lines:
                if tag == " ":
                    self.text_unified.insert("end", f" {line}", "diff_context")
                elif tag == "-":
                    self.text_unified.insert("end", f"-{line}", "diff_remove")
                elif tag == "+":
                    self.text_unified.insert("end", f"+{line}", "diff_add")

        # Apply syntax highlighting if enabled
        if self.highlight_var.get() == DiffViewMode.SYNTAX_HIGHLIGHTED.value:
            self._apply_syntax_highlighting_unified()

        self.text_unified.config(state="disabled")

    def _apply_syntax_highlighting(self) -> None:
        """Apply syntax highlighting to side-by-side view."""
        # We need to temporarily enable the widgets
        self.text_a.config(state="normal")
        self.text_b.config(state="normal")

        # For side-by-side, we highlight only the actual content (without line numbers)
        # This is complex because we'd need to strip line numbers first.
        # For simplicity, we'll apply highlighting to the raw content and rely on
        # the diff tags for change highlighting.

        self.text_a.config(state="disabled")
        self.text_b.config(state="disabled")

    def _apply_syntax_highlighting_unified(self) -> None:
        """Apply syntax highlighting to unified view."""
        self.text_unified.config(state="normal")

        # We can't easily apply syntax highlighting on top of diff tags
        # without interfering. For now, skip syntax highlighting in unified mode.
        # The diff tags provide sufficient visual distinction.

        self.text_unified.config(state="disabled")

    def _on_text_a_scroll(self, scrollbar: ttk.Scrollbar, *args) -> None:
        """Handle scroll on text A - sync with text B."""
        scrollbar.set(*args)
        if hasattr(self, "text_b") and self.text_b.winfo_exists():
            self.text_b.yview_moveto(args[0])

    def _on_text_b_scroll(self, scrollbar: ttk.Scrollbar, *args) -> None:
        """Handle scroll on text B - sync with text A."""
        scrollbar.set(*args)
        if hasattr(self, "text_a") and self.text_a.winfo_exists():
            self.text_a.yview_moveto(args[0])

    def _on_scroll_a(self, *args) -> None:
        """Scroll command for text A."""
        self.text_a.yview(*args)
        if hasattr(self, "text_b") and self.text_b.winfo_exists():
            self.text_b.yview(*args)

    def _on_scroll_b(self, *args) -> None:
        """Scroll command for text B."""
        self.text_b.yview(*args)
        if hasattr(self, "text_a") and self.text_a.winfo_exists():
            self.text_a.yview(*args)

    def _toggle_wrap(self) -> None:
        """Toggle word wrap."""
        wrap = "word" if self.wrap_var.get() else "none"
        for attr in ("text_a", "text_b", "text_unified"):
            if hasattr(self, attr):
                getattr(self, attr).config(wrap=wrap)

    def _scroll_to_hunk(self, index: int) -> None:
        """Scroll to show the given hunk."""
        if self.diff_result is None or index >= len(self.diff_result.hunks):
            return

        if self.mode_var.get() == DiffMode.SIDE_BY_SIDE.value:
            # Approximate line number in text widget
            hunk = self.diff_result.hunks[index]
            line = hunk.a_start + 1
            for attr in ("text_a", "text_b"):
                if hasattr(self, attr):
                    text = getattr(self, attr)
                    text.see(f"{line}.0")
        else:
            hunk = self.diff_result.hunks[index]
            # In unified mode, find the hunk header
            # Approximate: each hunk has header + lines
            line = sum(len(h.lines) + 1 for h in self.diff_result.hunks[:index]) + 3
            if hasattr(self, "text_unified"):
                self.text_unified.see(f"{line}.0")

    def _update_hunk_label(self) -> None:
        """Update the hunk counter label."""
        if self.diff_result:
            total = len(self.diff_result.hunks)
            current = self.current_hunk_index + 1 if total > 0 else 0
            self.hunk_label.config(text=f"Hunk {current} / {total}")

    def _next_change(self) -> None:
        """Navigate to next change hunk."""
        if self.diff_result and self.current_hunk_index < len(self.diff_result.hunks) - 1:
            self.current_hunk_index += 1
            self._scroll_to_hunk(self.current_hunk_index)
            self._update_hunk_label()

    def _prev_change(self) -> None:
        """Navigate to previous change hunk."""
        if self.diff_result and self.current_hunk_index > 0:
            self.current_hunk_index -= 1
            self._scroll_to_hunk(self.current_hunk_index)
            self._update_hunk_label()

    def _open_meld(self) -> None:
        """Open files in Meld external diff tool."""
        try:
            # Write temp files for VFS paths
            path_a = self._get_local_path(self.path_a)
            path_b = self._get_local_path(self.path_b)
            subprocess.Popen(["meld", path_a, path_b])
        except FileNotFoundError:
            messagebox.showwarning(
                "Meld not found", "Meld is not installed or not in PATH.", parent=self
            )
        except Exception as e:
            messagebox.showerror("Error", f"Failed to launch Meld: {e}", parent=self)

    def _open_vimdiff(self) -> None:
        """Open files in vimdiff."""
        try:
            path_a = self._get_local_path(self.path_a)
            path_b = self._get_local_path(self.path_b)
            subprocess.Popen(["vimdiff", path_a, path_b])
        except FileNotFoundError:
            messagebox.showwarning(
                "Vim not found", "Vim is not installed or not in PATH.", parent=self
            )
        except Exception as e:
            messagebox.showerror("Error", f"Failed to launch vimdiff: {e}", parent=self)

    def _get_local_path(self, vpath: VfsPath) -> str:
        """Get local filesystem path, extracting to temp if needed."""
        real = vpath.fs.realpath(vpath)
        if real is not None:
            return str(real)
        # Extract to temp file

        with tempfile.NamedTemporaryFile(delete=False, suffix=vpath.suffix) as tmp:
            with vpath.fs.open_read(vpath) as src:
                tmp.write(src.read())
            return tmp.name

    def _save_patch(self) -> None:
        """Save unified diff as a patch file."""
        from tkinter import filedialog

        if self.diff_result is None:
            return

        path = filedialog.asksaveasfilename(
            parent=self,
            title="Save Patch",
            defaultextension=".patch",
            filetypes=[("Patch files", "*.patch"), ("Diff files", "*.diff"), ("All files", "*.*")],
        )
        if not path:
            return

        try:
            # Generate unified diff
            a_lines = _split_lines(_read_text(self.path_a))
            b_lines = _split_lines(_read_text(self.path_b))
            diff = difflib.unified_diff(
                a_lines, b_lines, fromfile=str(self.path_a), tofile=str(self.path_b)
            )

            with open(path, "w", encoding="utf-8") as f:
                f.writelines(diff)

            messagebox.showinfo("Saved", f"Patch saved to {path}", parent=self)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save patch: {e}", parent=self)


def show_diff_viewer(parent: tk.Misc, path_a: VfsPath, path_b: VfsPath) -> None:
    """Show diff viewer for two files."""
    DiffViewer(parent, path_a, path_b)


def compare_directories(
    parent: tk.Misc,
    dir_a: VfsPath,
    dir_b: VfsPath,
) -> None:
    """Compare two directories and show a list of differing files.

    Opens a dialog with a tree view of files that differ, with double-click
    to open file diff.
    """

    dialog = tk.Toplevel(parent)
    dialog.title(f"Directory Compare: {dir_a.name} ↔ {dir_b.name}")
    dialog.geometry("900x600")
    _center_over(dialog, parent)

    # Toolbar
    toolbar = ttk.Frame(dialog)
    toolbar.pack(fill="x", padx=4, pady=4)

    status_var = tk.StringVar(value="Scanning...")
    ttk.Label(toolbar, textvariable=status_var).pack(side="left", padx=8)

    # Tree view
    tree_frame = ttk.Frame(dialog)
    tree_frame.pack(fill="both", expand=True, padx=4, pady=(0, 4))

    columns = ("status", "name", "size_a", "size_b", "mtime_a", "mtime_b")
    tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="browse")
    tree.heading("status", text="Status")
    tree.heading("name", text="Name")
    tree.heading("size_a", text="Size A")
    tree.heading("size_b", text="Size B")
    tree.heading("mtime_a", text="Modified A")
    tree.heading("mtime_b", text="Modified B")
    tree.column("status", width=100, anchor="center")
    tree.column("name", width=300, anchor="w")
    tree.column("size_a", width=100, anchor="e")
    tree.column("size_b", width=100, anchor="e")
    tree.column("mtime_a", width=150, anchor="center")
    tree.column("mtime_b", width=150, anchor="center")

    tree.tag_configure("same", foreground="gray")
    tree.tag_configure("different", foreground="red")
    tree.tag_configure("only_a", foreground="blue")
    tree.tag_configure("only_b", foreground="green")
    tree.tag_configure("dir", font=("TkDefaultFont", 10, "bold"))

    vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
    hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
    tree.grid(row=0, column=0, sticky="nsew")
    vsb.grid(row=0, column=1, sticky="ns")
    hsb.grid(row=1, column=0, sticky="ew")
    tree_frame.rowconfigure(0, weight=1)
    tree_frame.columnconfigure(0, weight=1)

    def on_double_click(event):
        item = tree.selection()
        if not item:
            return
        item = item[0]
        values = tree.item(item, "values")
        if not values:
            return
        name = values[1]
        # Skip directories
        if tree.item(item, "tags") and "dir" in tree.item(item, "tags"):
            return
        path_a = dir_a / name
        path_b = dir_b / name
        if path_a.fs.stat(path_a).is_dir or path_b.fs.stat(path_b).is_dir:
            return
        dialog.destroy()  # Close dir compare first
        show_diff_viewer(parent, path_a, path_b)

    tree.bind("<Double-1>", on_double_click)

    def _format_size(size: int) -> str:
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024 or unit == "GB":
                return f"{size:.1f} {unit}"
            size = size // 1024
        return f"{size:.1f} TB"

    def _format_mtime(mtime: float) -> str:
        import datetime

        if mtime <= 0:
            return ""
        return datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")

    def scan_dirs():
        """Background scan of both directories."""
        try:
            # Build file maps
            files_a: dict[str, VfsPath] = {}
            files_b: dict[str, VfsPath] = {}

            def walk(path: VfsPath, store: dict[str, VfsPath], prefix: str = ""):
                for entry in path.fs.list_dir(path):
                    if entry.is_parent:
                        continue
                    rel = prefix + entry.name
                    if entry.is_dir:
                        walk(entry.path, store, rel + "/")
                    else:
                        store[rel] = entry.path

            walk(dir_a, files_a)
            walk(dir_b, files_b)

            all_names = sorted(set(files_a.keys()) | set(files_b.keys()))

            def update_tree():
                for name in all_names:
                    in_a = name in files_a
                    in_b = name in files_b

                    if in_a and in_b:
                        stat_a = files_a[name].fs.stat(files_a[name])
                        stat_b = files_b[name].fs.stat(files_b[name])
                        if stat_a.size == stat_b.size and stat_a.mtime == stat_b.mtime:
                            status = "Identical"
                            tag = "same"
                        else:
                            status = "Different"
                            tag = "different"
                        tree.insert(
                            "",
                            "end",
                            values=(
                                status,
                                name,
                                _format_size(stat_a.size),
                                _format_size(stat_b.size),
                                _format_mtime(stat_a.mtime),
                                _format_mtime(stat_b.mtime),
                            ),
                            tags=(tag,),
                        )
                    elif in_a:
                        stat_a = files_a[name].fs.stat(files_a[name])
                        tree.insert(
                            "",
                            "end",
                            values=(
                                "Only in A",
                                name,
                                _format_size(stat_a.size),
                                "",
                                _format_mtime(stat_a.mtime),
                                "",
                            ),
                            tags=("only_a",),
                        )
                    else:
                        stat_b = files_b[name].fs.stat(files_b[name])
                        tree.insert(
                            "",
                            "end",
                            values=(
                                "Only in B",
                                name,
                                "",
                                _format_size(stat_b.size),
                                "",
                                _format_mtime(stat_b.mtime),
                            ),
                            tags=("only_b",),
                        )

                status_var.set(f"Done — {len(all_names)} items compared")

            dialog.after(0, update_tree)

        except Exception as exc:
            dialog.after(0, lambda err=exc: status_var.set(f"Error: {err}"))

    threading.Thread(target=scan_dirs, daemon=True).start()

    dialog.grab_set()
    dialog.wait_window()


OPERATIONS: list = []  # Diff viewer is not a FileOperation - it's a standalone viewer
