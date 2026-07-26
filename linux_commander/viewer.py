"""Built-in file viewer (F3) and editor (F4) — unified TextWindow.

F3 opens a file read-only; F4 opens it editable.  Pressing F4 inside a
read-only window promotes it to edit mode.

The image viewer lives in ``linux_commander.image_viewer`` but is
re-exported here for backwards compatibility.

Public API (unchanged):
    view_file(parent, path, settings=None) -> tk.Toplevel | None
    edit_file(parent, path, on_saved=None, settings=None) -> tk.Toplevel | None
    view_image(parent, path, image_files, start_index, image_extensions, settings=None)
"""

from __future__ import annotations

import csv
import queue
import threading
import tkinter as tk
import tkinter.font as tkfont
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Literal

from linux_commander import dialogs, plugins
from linux_commander.settings import Settings
from linux_commander.syntax import apply_highlighting, available_languages
from linux_commander.vfs import LocalFileSystem, VfsPath, WritableFileSystem
from linux_commander.viewer_modes import ViewerMode, discover_modes

MAX_VIEW_BYTES = 2 * 1024 * 1024
"""Cap on how much of a file the built-in viewer/editor will read."""


# ---------------------------------------------------------------------------
# Pure helpers (unit-testable, no Tk)
# ---------------------------------------------------------------------------


def _read_raw_capped(path: VfsPath, max_bytes: int = MAX_VIEW_BYTES) -> tuple[bytes, bool]:
    """Read up to ``max_bytes`` of ``path`` as raw bytes.

    Returns ``(data, was_truncated)``.  Uses ``FileSystem.read_prefix`` so that
    backends with eager full-file downloads (FTP, tar) stop transferring data
    early rather than downloading the whole file first.
    """
    return path.fs.read_prefix(path, max_bytes)


def _read_capped(path: VfsPath, max_bytes: int = MAX_VIEW_BYTES) -> tuple[str, bool]:
    """Read up to ``max_bytes`` of ``path``, decoded as UTF-8 with errors replaced.

    Returns ``(text, was_truncated)``.
    """
    raw, truncated = _read_raw_capped(path, max_bytes)
    return raw.decode("utf-8", errors="replace"), truncated


def _format_hexdump(data: bytes) -> str:
    """Format ``data`` as a classic hexdump string.

    Each row covers 16 bytes::

        00000000  48 65 6c 6c 6f 2c 20 57  6f 72 6c 64 21 0a      |Hello, World!.|

    Short final rows are space-padded to keep the ASCII column aligned.
    Returns an empty string for empty input.
    """
    if not data:
        return ""
    lines: list[str] = []
    for offset in range(0, len(data), 16):
        chunk = data[offset : offset + 16]
        hex_parts = [f"{b:02x}" for b in chunk]
        # Two groups of 8 separated by an extra space
        hex_left = " ".join(hex_parts[:8])
        hex_right = " ".join(hex_parts[8:])
        hex_str = f"{hex_left:<23}  {hex_right:<23}"
        ascii_str = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in chunk)
        lines.append(f"{offset:08x}  {hex_str}  |{ascii_str}|")
    return "\n".join(lines)


_STRINGS_CHUNK_SIZE = 64 * 1024
"""Chunk size used by the background strings scan (View > Strings)."""

_STRINGS_POLL_MS = 100
"""How often the Tk thread drains queued strings during a scan."""

_STRINGS_MAX_RUN = 1024 * 1024
"""Safety cap on a single in-progress printable run before it is force-flushed.

Prevents unbounded memory growth when scanning a file that is entirely
printable ASCII (e.g. a huge plain-text file) with no separator byte.
"""


class _StringsScanner:
    """Incremental scanner for printable-ASCII runs, fed one chunk at a time.

    Mirrors the Unix ``strings`` tool: a "string" is a maximal run of bytes
    in ``[0x20, 0x7F)`` at least ``min_length`` bytes long. Runs may span
    chunk boundaries, so any trailing partial run is held in ``_carry`` and
    completed (or abandoned) on the next ``feed()`` call.
    """

    def __init__(self, min_length: int) -> None:
        self.min_length = max(1, min_length)
        self._carry = bytearray()

    def feed(self, chunk: bytes) -> list[str]:
        """Process one chunk; return complete strings found so far."""
        data = bytes(self._carry) + chunk
        found: list[str] = []
        run_start = 0
        for i, b in enumerate(data):
            if not (0x20 <= b < 0x7F):
                if i - run_start >= self.min_length:
                    found.append(data[run_start:i].decode("ascii"))
                run_start = i + 1
        self._carry = bytearray(data[run_start:])
        if len(self._carry) >= _STRINGS_MAX_RUN:
            # Force-flush an implausibly long uninterrupted run so memory
            # stays bounded; the string is simply split at this point.
            found.append(bytes(self._carry).decode("ascii"))
            self._carry = bytearray()
        return found

    def finish(self) -> list[str]:
        """Call once after the last chunk; flush any trailing pending run."""
        if len(self._carry) >= self.min_length:
            return [bytes(self._carry).decode("ascii")]
        return []


def _strings_worker(
    path: VfsPath,
    min_length: int,
    out_queue: queue.Queue[str | None],
    cancelled: threading.Event,
) -> None:
    """Background worker for View > Strings: stream ``path`` and queue results.

    Runs off the Tk thread; only touches thread-safe primitives (the queue
    and the cancellation event). Puts each found string individually onto
    ``out_queue``, always ending with a ``None`` sentinel (on EOF,
    cancellation, or read error) so the poll loop knows to stop.
    """
    scanner = _StringsScanner(min_length)
    try:
        with path.fs.open_read(path) as f:
            while not cancelled.is_set():
                chunk = f.read(_STRINGS_CHUNK_SIZE)
                if not chunk:
                    break
                for s in scanner.feed(chunk):
                    out_queue.put(s)
        if not cancelled.is_set():
            for s in scanner.finish():
                out_queue.put(s)
    except OSError:
        pass
    finally:
        out_queue.put(None)


CSV_EXTENSIONS = frozenset({".csv", ".tsv", ".tab"})
"""Extensions that auto-enable the CSV table view in the viewer."""

_CSV_DELIMITER_CANDIDATES = (",", ";", "\t")


def detect_delimiter(text: str, sample_lines: int = 50) -> str:
    """Guess the field delimiter for delimited text.

    Scores each candidate in ``,``, ``;``, ``\\t`` by how consistently it splits
    the first ``sample_lines`` non-empty lines into more than one field (using
    ``csv.reader`` so a delimiter char inside quotes doesn't count), preferring
    the delimiter whose field count agrees across the most lines. Falls back to
    comma when no candidate manages more than one field per line.
    """
    lines = [line for line in text.splitlines() if line.strip()][:sample_lines]
    if not lines:
        return ","

    best_delim = ","
    best_score = -1
    for delim in _CSV_DELIMITER_CANDIDATES:
        try:
            rows = list(csv.reader(lines, delimiter=delim))
        except csv.Error:
            continue
        counts = [len(row) for row in rows]
        if not counts or max(counts) <= 1:
            continue
        most_common_count = max(set(counts), key=counts.count)
        agreement = counts.count(most_common_count)
        score = agreement * most_common_count
        if score > best_score:
            best_score = score
            best_delim = delim
    return best_delim


def _center_over(top: tk.Toplevel, parent: tk.Misc) -> None:
    """Center ``top`` over ``parent``."""
    top.update_idletasks()
    try:
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
    except tk.TclError:
        return
    w, h = top.winfo_width(), top.winfo_height()
    x = max(px + (pw - w) // 2, 0)
    y = max(py + (ph - h) // 2, 0)
    top.geometry(f"+{x}+{y}")


_TK_FONT_ALIASES = frozenset(
    {
        "TkFixedFont",
        "TkDefaultFont",
        "TkTextFont",
        "TkHeadingFont",
        "TkCaptionFont",
        "TkSmallCaptionFont",
        "TkIconFont",
        "TkTooltipFont",
    }
)


def _resolve_font_family(family: str) -> str:
    """Resolve a Tk named-font alias (e.g. 'TkFixedFont') to a real family name.

    Tk aliases are not valid font-family names; passing them directly to
    tkfont.Font(family=...) silently picks the wrong typeface on some systems.
    """
    if family in _TK_FONT_ALIASES:
        return str(tkfont.nametofont(family).actual()["family"])
    return family


def _get_font_families() -> list[str]:
    """Return available font families for the font picker.

    When ``tkfont.families()`` returns very few entries — common when Tk is
    running in X11 core-font mode without Xft — supplements with ``fc-list``
    so scalable system fonts are still accessible.
    """
    families: set[str] = set(tkfont.families())
    if len(families) < 20:
        try:
            import subprocess

            proc = subprocess.run(["fc-list"], capture_output=True, text=True, timeout=5)
            for line in proc.stdout.splitlines():
                # Default fc-list format: /path/to/font.ttf: Family Name:style=Regular
                parts = line.split(":")
                if len(parts) >= 2:
                    for name in parts[1].split(","):
                        name = name.strip()
                        if name:
                            families.add(name)
        except Exception:
            pass
    return sorted(families)


def _font_dialog(
    parent: tk.Misc,
    settings: Settings,
    family_attr: str,
    size_attr: str,
    on_apply: Callable[[str, int], None],
) -> None:
    """Shared font chooser dialog.

    Reads initial values from ``settings.<family_attr>`` / ``settings.<size_attr>``,
    writes them back on OK, then calls ``on_apply(family, size)`` so the caller
    can update live widgets.
    """
    families = _get_font_families()
    mono_families = [
        f
        for f in families
        if "mono" in f.lower() or "courier" in f.lower() or "console" in f.lower()
    ]
    if mono_families:
        families = mono_families + [f for f in families if f not in mono_families]

    dialog = tk.Toplevel(parent)
    dialog.title("Font")
    dialog.transient(parent)  # type: ignore[call-overload]
    dialog.resizable(False, False)

    ttk.Label(dialog, text="Font:").grid(row=0, column=0, padx=8, pady=8, sticky="w")
    font_var = tk.StringVar(value=_resolve_font_family(getattr(settings, family_attr)))
    # Not readonly — allows typing a name when the system list is incomplete
    font_combo = ttk.Combobox(dialog, textvariable=font_var, values=families, width=30)
    font_combo.grid(row=0, column=1, padx=8, pady=8)

    ttk.Label(dialog, text="Size:").grid(row=1, column=0, padx=8, pady=8, sticky="w")
    size_var = tk.IntVar(value=getattr(settings, size_attr))
    ttk.Spinbox(dialog, from_=8, to=72, textvariable=size_var, width=5).grid(
        row=1, column=1, padx=8, pady=8, sticky="w"
    )

    def _ok() -> None:
        setattr(settings, family_attr, font_var.get())
        setattr(settings, size_attr, size_var.get())
        on_apply(font_var.get(), size_var.get())
        dialog.destroy()

    btn_frame = ttk.Frame(dialog)
    btn_frame.grid(row=2, column=0, columnspan=2, pady=8)
    ttk.Button(btn_frame, text="OK", command=_ok).pack(side="right", padx=4)
    ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side="right", padx=4)

    _center_over(dialog, parent)
    dialog.grab_set()
    font_combo.focus_set()
    dialog.wait_window()


# ---------------------------------------------------------------------------
# TextWindow — unified viewer / editor
# ---------------------------------------------------------------------------


class TextWindow:
    """A unified text viewer/editor window.

    Opened read-only (F3) or editable (F4).  F4 pressed inside a read-only
    window promotes it to edit mode.

    Features:
    - Ctrl+F search bar (regex + ignore-case, next/prev, wraps).
    - View > Hexdump — display-only hexdump, editing disabled while active.
    - View > JSON Pretty-Print — ``json.dumps(json.loads(...), indent=...)``
      toggle; graceful error on invalid JSON.
    - Syntax menu — radiobuttons for all loaded languages plus "Auto".
    """

    def __init__(
        self,
        parent: tk.Misc,
        path: VfsPath | None,
        *,
        read_only: bool,
        on_saved: Callable[[], None] | None = None,
        settings: Settings | None = None,
    ) -> None:
        from linux_commander.settings import Settings as _Settings

        self.parent = parent
        self.path: VfsPath | None = path
        self.on_saved = on_saved
        self._settings = settings or _Settings()

        # Mode flags
        self.read_only = read_only
        self.modified = False
        self.word_wrap = False
        self.forced_lang: str | None = None  # None = auto by extension

        # Rows from a document reader plugin (xlsx/pandas), bypassing CSV
        # parsing -- see _render_table. None means "not a document preview",
        # so csv_mode (if on) parses self._raw_text as delimited text instead.
        self._table_rows: list[list[str]] | None = None
        self._table_truncated = False  # only meaningful when _table_rows is set

        # True once a document reader plugin (xlsx/docx/pandas) produced the
        # current content -- forces read-only (saving plain text back over a
        # binary document would corrupt it) and blocks F4 edit-promotion.
        self._document_preview = False

        # Raw text as loaded from disk — used to restore on JSON/hex toggle
        self._raw_text: str = ""

        # Background strings-scan state (see _start_strings_scan)
        self._strings_cancel: threading.Event | None = None
        self._strings_queue: queue.Queue[str | None] | None = None

        # Discovered viewer modes (hex, json, csv, strings, ...)
        self._modes: list[ViewerMode] = []
        self._active_mode: ViewerMode | None = None
        self._title_suffix: str = ""
        for cls in discover_modes():
            self._modes.append(cls())

        self.top = tk.Toplevel(parent)
        self._setup_window()
        self._build_menu()
        self._create_search_bar()
        self._create_text_area()
        self._create_status_bar()
        self._bind_shortcuts()

        if path is not None:
            self._load_file(path)
        else:
            self._set_title("Untitled")

        self.text_widget.focus_set()
        self._update_status_bar()

    # ------------------------------------------------------------------
    # Window setup
    # ------------------------------------------------------------------

    def _setup_window(self) -> None:
        self.top.geometry("800x600")
        self.top.minsize(400, 300)
        self.top.protocol("WM_DELETE_WINDOW", self._on_close)
        self._set_title(self.path.name if self.path else "Untitled")

    def _set_title(self, name: str) -> None:
        mode = "View" if self.read_only else "Edit"
        suffix = " *" if self.modified else ""
        extra = self._title_suffix
        self.top.title(f"{name}{suffix}{extra} - {mode}")

    # ------------------------------------------------------------------
    # Menu bar
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        """Build (or rebuild) the entire menu bar from current state."""
        menubar = tk.Menu(self.top)
        self.top.config(menu=menubar)

        # --- File ---
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu, underline=0)
        if not self.read_only:
            file_menu.add_command(
                label="New", accelerator="Ctrl+N", command=self._cmd_new, underline=0
            )
            file_menu.add_command(
                label="Open...", accelerator="Ctrl+O", command=self._cmd_open, underline=0
            )
            file_menu.add_separator()
            file_menu.add_command(
                label="Save", accelerator="Ctrl+S", command=self._cmd_save, underline=0
            )
            file_menu.add_command(
                label="Save As...", accelerator="F12", command=self._cmd_save_as, underline=5
            )
            file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close, underline=1)

        # --- Edit (always present; Cut/Paste/Undo disabled when read-only) ---
        edit_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Edit", menu=edit_menu, underline=0)
        ro_state: Literal["normal", "disabled"] = "disabled" if self.read_only else "normal"
        edit_menu.add_command(
            label="Undo", accelerator="Ctrl+Z", command=self._cmd_undo, state=ro_state, underline=0
        )
        edit_menu.add_separator()
        edit_menu.add_command(
            label="Cut", accelerator="Ctrl+X", command=self._cmd_cut, state=ro_state, underline=2
        )
        edit_menu.add_command(
            label="Copy", accelerator="Ctrl+C", command=self._cmd_copy, underline=0
        )
        edit_menu.add_command(
            label="Paste",
            accelerator="Ctrl+V",
            command=self._cmd_paste,
            state=ro_state,
            underline=0,
        )
        edit_menu.add_separator()
        edit_menu.add_command(
            label="Select All", accelerator="Ctrl+A", command=self._cmd_select_all, underline=7
        )
        edit_menu.add_separator()
        edit_menu.add_command(
            label="Find...", accelerator="Ctrl+F", command=self._show_search, underline=0
        )

        # --- View ---
        view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="View", menu=view_menu, underline=0)
        self._status_bar_var = tk.BooleanVar(value=True)
        view_menu.add_checkbutton(
            label="Status Bar",
            variable=self._status_bar_var,
            command=self._toggle_status_bar,
            underline=7,
        )
        self._word_wrap_var = tk.BooleanVar(value=self.word_wrap)
        view_menu.add_checkbutton(
            label="Word Wrap",
            variable=self._word_wrap_var,
            command=self._toggle_word_wrap,
            underline=0,
        )
        view_menu.add_separator()

        # Plugin-discovered viewer modes (hex, json, csv, strings, ...)
        for mode in self._modes:
            mode.build_menu(self, view_menu)  # type: ignore[arg-type]

        view_menu.add_separator()
        view_menu.add_command(label="Font...", command=self._cmd_font, underline=0)

        # --- Syntax (disabled while a display mode is active) ---
        syntax_menu = tk.Menu(menubar, tearoff=0)
        syntax_cascade_state = "disabled" if self._active_mode is not None else "normal"
        menubar.add_cascade(
            label="Syntax",
            menu=syntax_menu,
            state=syntax_cascade_state,  # type: ignore[arg-type]
            underline=0,
        )
        self._syntax_var = tk.StringVar(value=self.forced_lang or "")
        syntax_menu.add_radiobutton(
            label="Auto (by extension)",
            value="",
            variable=self._syntax_var,
            command=self._on_syntax_pick,
        )
        syntax_menu.add_separator()
        for lang_name in available_languages():
            syntax_menu.add_radiobutton(
                label=lang_name,
                value=lang_name,
                variable=self._syntax_var,
                command=self._on_syntax_pick,
            )

    # ------------------------------------------------------------------
    # Search bar
    # ------------------------------------------------------------------

    def _create_search_bar(self) -> None:
        self._search_frame = ttk.Frame(self.top)
        # Not packed yet — shown only when Ctrl+F is pressed

        ttk.Label(self._search_frame, text="Find:").pack(side="left", padx=(4, 2))
        self._search_var = tk.StringVar()
        self._search_entry = ttk.Entry(self._search_frame, textvariable=self._search_var, width=25)
        self._search_entry.pack(side="left", padx=2)

        self._search_regex_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(self._search_frame, text="Regex", variable=self._search_regex_var).pack(
            side="left", padx=2
        )

        self._search_nocase_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            self._search_frame, text="Ignore case", variable=self._search_nocase_var
        ).pack(side="left", padx=2)

        ttk.Button(self._search_frame, text="Prev", command=lambda: self._search(-1), width=5).pack(
            side="left", padx=2
        )
        ttk.Button(self._search_frame, text="Next", command=lambda: self._search(1), width=5).pack(
            side="left", padx=2
        )
        ttk.Button(self._search_frame, text="X", command=self._hide_search, width=3).pack(
            side="left", padx=(2, 4)
        )

        self._search_entry.bind("<Return>", lambda e: self._search(1))
        self._search_entry.bind("<Shift-Return>", lambda e: self._search(-1))
        self._search_entry.bind("<Escape>", lambda e: self._hide_search())

        # Place in grid row 0 but hide immediately; grid_remove() remembers placement
        self._search_frame.grid(row=0, column=0, columnspan=2, sticky="ew")
        self._search_frame.grid_remove()
        self._search_visible = False
        # Track the last search position for next/prev continuity
        self._search_last_idx: str | None = None

    def _show_search(self, event: tk.Event | None = None) -> str:
        if not self._search_visible:
            self._search_frame.grid()  # restores remembered grid placement
            self._search_visible = True
        self._search_entry.focus_set()
        self._search_entry.select_range(0, "end")
        self._search_last_idx = None
        return "break"

    def _hide_search(self) -> None:
        if self._search_visible:
            self._search_frame.grid_remove()
            self._search_visible = False
        self.text_widget.tag_remove("search_hit", "1.0", "end")
        self.text_widget.tag_remove("search_current", "1.0", "end")
        self._search_last_idx = None
        self.text_widget.focus_set()

    def _search(self, direction: int) -> None:
        pattern = self._search_var.get()
        if not pattern:
            return

        use_regex = self._search_regex_var.get()
        nocase = self._search_nocase_var.get()

        # Start from current cursor or last found position
        if direction > 0:
            start = self._search_last_idx or self.text_widget.index("insert")
        else:
            start = self._search_last_idx or self.text_widget.index("insert")

        count_var = tk.IntVar()
        try:
            idx = self.text_widget.search(
                pattern,
                start,
                stopindex="end" if direction > 0 else "1.0",
                regexp=use_regex,
                nocase=nocase,
                backwards=(direction < 0),
                count=count_var,
            )
        except tk.TclError:
            # Bad regex
            return

        if not idx:
            # Wrap around
            wrap_start = "1.0" if direction > 0 else "end"
            try:
                idx = self.text_widget.search(
                    pattern,
                    wrap_start,
                    stopindex=start,
                    regexp=use_regex,
                    nocase=nocase,
                    backwards=(direction < 0),
                    count=count_var,
                )
            except tk.TclError:
                return

        if not idx:
            return

        match_len = count_var.get() or len(pattern)
        end_idx = f"{idx} + {match_len} chars"

        self.text_widget.tag_remove("search_hit", "1.0", "end")
        self.text_widget.tag_remove("search_current", "1.0", "end")
        self.text_widget.tag_configure("search_hit", background="#264f78")
        self.text_widget.tag_configure("search_current", background="#c65c00")
        self.text_widget.tag_add("search_current", idx, end_idx)
        self.text_widget.see(idx)

        # Advance position for next/prev
        if direction > 0:
            self._search_last_idx = end_idx
        else:
            self._search_last_idx = idx

    # ------------------------------------------------------------------
    # Text area + scrollbars
    # ------------------------------------------------------------------

    def _create_text_area(self) -> None:
        # Font: viewer_font_* when read-only, editor_font_* when editable
        family_attr = "viewer_font_family" if self.read_only else "editor_font_family"
        size_attr = "viewer_font_size" if self.read_only else "editor_font_size"
        # Store as instance attribute — tkfont.Font.__del__ deletes the Tk font,
        # so a local variable would cause the font to revert when GC'd.
        # Resolve Tk aliases (e.g. "TkFixedFont") to real family names first;
        # passing an alias directly silently picks the wrong typeface on some systems.
        self._font = tkfont.Font(
            family=_resolve_font_family(getattr(self._settings, family_attr)),
            size=getattr(self._settings, size_attr),
        )
        self._font_family_attr = family_attr
        self._font_size_attr = size_attr

        self.text_widget = tk.Text(self.top, wrap="none", font=self._font, undo=True)

        yscroll = ttk.Scrollbar(self.top, orient="vertical", command=self.text_widget.yview)
        xscroll = ttk.Scrollbar(self.top, orient="horizontal", command=self.text_widget.xview)
        self.text_widget.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self._text_yscroll = yscroll
        self._text_xscroll = xscroll

        # Row 0: search bar (managed by _create_search_bar, hidden by default)
        # Row 1: text + yscroll; Row 2: xscroll; Row 3: status bar
        self.text_widget.grid(row=1, column=0, sticky="nsew")
        yscroll.grid(row=1, column=1, sticky="ns")
        xscroll.grid(row=2, column=0, sticky="ew")
        self.top.columnconfigure(0, weight=1)
        self.top.rowconfigure(1, weight=1)

        self.text_widget.bind("<<Modified>>", self._on_modified)
        self.text_widget.bind("<KeyRelease>", self._on_cursor_move)
        self.text_widget.bind("<ButtonRelease-1>", self._on_cursor_move)

        self._apply_edit_state()

        # CSV table view — occupies the same footprint as the text widget and
        # its scrollbars (rows 1-2), shown instead of it while csv_mode is on.
        self._csv_frame = ttk.Frame(self.top)
        self._csv_tree = ttk.Treeview(self._csv_frame, show="headings", selectmode="browse")
        csv_yscroll = ttk.Scrollbar(
            self._csv_frame, orient="vertical", command=self._csv_tree.yview
        )
        csv_xscroll = ttk.Scrollbar(
            self._csv_frame, orient="horizontal", command=self._csv_tree.xview
        )
        self._csv_tree.configure(yscrollcommand=csv_yscroll.set, xscrollcommand=csv_xscroll.set)
        self._csv_tree.grid(row=0, column=0, sticky="nsew")
        csv_yscroll.grid(row=0, column=1, sticky="ns")
        csv_xscroll.grid(row=1, column=0, sticky="ew")
        self._csv_frame.columnconfigure(0, weight=1)
        self._csv_frame.rowconfigure(0, weight=1)
        self._csv_frame.grid(row=1, column=0, rowspan=2, columnspan=2, sticky="nsew")
        self._csv_frame.grid_remove()

    def _apply_edit_state(self) -> None:
        """Set the text widget state based on current mode flags."""
        editable = not self.read_only and self._active_mode is None
        self.text_widget.configure(state="normal" if editable else "disabled")

    # ------------------------------------------------------------------
    # ViewerContext protocol implementation
    # ------------------------------------------------------------------

    # is_active is a per-mode concept; TextWindow tracks _active_mode instead.
    # The protocol requires this attribute for compatibility.
    is_active = False  # type: ignore[assignability]

    # Protocol aliases — TextWindow stores these with underscore prefixes
    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def raw_text(self) -> str:
        return self._raw_text

    @property
    def csv_frame(self) -> ttk.Frame:
        return self._csv_frame

    @property
    def csv_tree(self) -> ttk.Treeview:
        return self._csv_tree

    def clear_text(self) -> None:
        self.text_widget.configure(state="normal")
        self.text_widget.delete("1.0", "end")

    def insert_text(self, text: str) -> None:
        self.text_widget.insert("1.0", text)

    def set_title_suffix(self, suffix: str) -> None:
        self._title_suffix = suffix
        self._set_title(self.path.name if self.path else "Untitled")

    def apply_syntax_highlighting(self) -> None:
        apply_highlighting(self.text_widget, self.path or _dummy_path(), self.forced_lang)  # type: ignore[arg-type]

    def clear_syntax_tags(self) -> None:
        from linux_commander.syntax import _clear_syntax_tags

        _clear_syntax_tags(self.text_widget)

    def set_editable(self, enabled: bool) -> None:
        self.text_widget.configure(state="normal" if enabled else "disabled")

    def show_error(self, title: str, message: str) -> None:
        dialogs.error(self.top, message, title=title)

    def set_modified(self, modified: bool) -> None:
        self.modified = modified
        if not modified:
            self.text_widget.edit_modified(False)

    def deactivate_other_modes(self, group: str) -> None:
        """Deactivate all modes in *group* except the one being activated."""
        for mode in self._modes:
            if mode.exclusive_group != group:
                continue
            # Skip the mode that's being activated (its var is already True)
            var = getattr(mode, "_var", None)
            if var is not None and var.get():
                continue
            # Deactivate this mode
            if var is not None:
                var.set(False)
            if mode is self._active_mode:
                mode.on_deactivate(self)  # type: ignore[arg-type]
                self._active_mode = None

    def reactivate_group(self, group: str) -> None:
        """No-op: menu state is managed by Tk variables."""

    def show_csv_area(self) -> None:
        self.text_widget.grid_remove()
        self._text_yscroll.grid_remove()
        self._text_xscroll.grid_remove()
        self._csv_frame.grid()

    def show_text_area(self) -> None:
        self._csv_frame.grid_remove()
        self.text_widget.grid()
        self._text_yscroll.grid()
        self._text_xscroll.grid()

    # ------------------------------------------------------------------
    # Status bar
    # ------------------------------------------------------------------

    def _create_status_bar(self) -> None:
        self._status_frame = ttk.Frame(self.top)
        self._status_frame.grid(row=3, column=0, columnspan=2, sticky="ew")
        self._status_label = ttk.Label(self._status_frame, text="Ln 1, Col 1", anchor="e")
        self._status_label.pack(fill="x", padx=4, pady=2)

    # ------------------------------------------------------------------
    # Keybindings
    # ------------------------------------------------------------------

    def _bind_shortcuts(self) -> None:
        top = self.top
        # Always available
        top.bind("<Escape>", self._close_and_break)
        top.bind("<F3>", self._close_and_break)
        top.bind("<F10>", self._close_and_break)
        top.bind("<F4>", lambda e: self._enable_editing())
        top.bind("<Control-f>", self._show_search)
        top.bind("<Control-a>", lambda e: self._cmd_select_all())
        top.bind("<Control-c>", lambda e: self._cmd_copy())
        # Edit-only (bound regardless, guarded inside the handler)
        top.bind("<Control-n>", lambda e: self._cmd_new())
        top.bind("<Control-o>", lambda e: self._cmd_open())
        top.bind("<Control-s>", lambda e: self._cmd_save())
        top.bind("<F2>", lambda e: self._cmd_save())
        top.bind("<F12>", lambda e: self._cmd_save_as())
        top.bind("<Control-z>", lambda e: self._cmd_undo())
        top.bind("<Control-x>", lambda e: self._cmd_cut())
        top.bind("<Control-v>", lambda e: self._cmd_paste())

    # ------------------------------------------------------------------
    # Mode transitions
    # ------------------------------------------------------------------

    def _enable_editing(self) -> None:
        """Promote a read-only window to editable (F4 inside the viewer)."""
        if not self.read_only:
            return
        if self._document_preview:
            dialogs.error(
                self.top,
                "This is a read-only document preview and cannot be edited.",
                title="Edit",
            )
            return
        self.read_only = False
        # Switch to editor font; store to prevent GC from deleting the Tk font
        self._font_family_attr = "editor_font_family"
        self._font_size_attr = "editor_font_size"
        self._font = tkfont.Font(
            family=_resolve_font_family(self._settings.editor_font_family),
            size=self._settings.editor_font_size,
        )
        self.text_widget.configure(font=self._font)
        self._apply_edit_state()
        self._build_menu()
        self._set_title(self.path.name if self.path else "Untitled")

    # ------------------------------------------------------------------
    # File loading
    # ------------------------------------------------------------------

    def _load_file(self, path: VfsPath) -> None:
        doc: plugins.ViewDocument | None = None
        doc_plugin = plugins.viewer_plugin_for_name(path.name)
        if doc_plugin is not None:
            try:
                doc = doc_plugin.read_document(path.fs, path)
            except Exception as exc:
                dialogs.error(
                    self.top,
                    f"Could not preview '{path.name}' as a document:\n{exc}\n\n"
                    "Showing raw content instead.",
                    title="Preview failed",
                )

        if doc is None:
            try:
                # Read raw bytes first to check for binary content
                raw_bytes, _ = _read_raw_capped(path)
                # Check if file appears to be binary (contains null bytes)
                is_binary = b"\x00" in raw_bytes
                text, truncated = _read_capped(path)
            except OSError as exc:
                dialogs.error(
                    self.top, f"Could not open '{path.name}':\n{exc}", title="Open failed"
                )
                self.top.destroy()
                return
        else:
            # For a table document the content lives in doc.rows, not text.
            text, truncated = doc.text, False
            is_binary = False

        # Deactivate any currently active display mode
        if self._active_mode is not None:
            self._active_mode.on_deactivate(self)  # type: ignore[arg-type]
            var = getattr(self._active_mode, "_var", None)
            if var is not None:
                var.set(False)
            self._active_mode = None
            self._title_suffix = ""

        self.path = path
        self._raw_text = text
        self._document_preview = doc is not None
        if doc is not None and doc.kind == "table":
            self._table_rows = doc.rows
            self._table_truncated = doc.truncated
        else:
            self._table_rows = None
            self._table_truncated = False
        if doc is not None:
            # A document preview can't be saved back over the original binary
            # file, so force read-only regardless of how the window was opened.
            self.read_only = True

        self._apply_edit_state()  # briefly enable so we can insert
        # Force normal state for insertion even in read-only mode
        self.text_widget.configure(state="normal")
        self.text_widget.delete("1.0", "end")
        self.text_widget.insert("1.0", text)
        if truncated:
            self.text_widget.insert(
                "end", "\n\n[... truncated - file exceeds the viewer's size limit ...]"
            )
        self.text_widget.edit_modified(False)
        self.modified = False
        self._set_title(path.name)
        apply_highlighting(self.text_widget, path, self.forced_lang)  # type: ignore[arg-type]
        self._apply_edit_state()

        # Auto-switch to hex mode for binary files
        if is_binary:
            for mode in self._modes:
                if mode.name == "Hex":
                    var = getattr(mode, "_var", None)
                    if var is not None:
                        var.set(True)
                    mode.on_activate(self)  # type: ignore[arg-type]
                    self._active_mode = mode
                    break

        # Auto-switch to CSV mode for CSV files
        if not is_binary and self._active_mode is None:
            if self._table_rows is not None or (
                doc is None and path.suffix.lower() in CSV_EXTENSIONS
            ):
                for mode in self._modes:
                    if mode.name == "CSV":
                        var = getattr(mode, "_var", None)
                        if var is not None:
                            var.set(True)
                        mode.on_activate(self)  # type: ignore[arg-type]
                        self._active_mode = mode
                        break

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _save_file(self, path: VfsPath) -> bool:
        content = self.text_widget.get("1.0", "end-1c")
        real = path.fs.realpath(path)
        try:
            if real is not None:
                # Real local file: write-to-temp + atomic replace, so a save
                # that fails partway never leaves a truncated/corrupt file.
                tmp_real = real.with_name(f".{real.name}.tmp")
                tmp_real.write_text(content, encoding="utf-8")
                tmp_real.replace(real)
            elif isinstance(path.fs, WritableFileSystem):
                # No real local file (archive member, remote backend like
                # Jottacloud/SMB/WebDAV/SFTP) but the backend is writable --
                # go through the VFS write API instead of assuming
                # read-only just because there's no local path.
                with path.fs.open_write(path) as f:
                    f.write(content.encode("utf-8"))
            else:
                dialogs.error(
                    self.top,
                    "Cannot save: this file is in a read-only filesystem.",
                    title="Save failed",
                )
                return False
        except OSError as exc:
            dialogs.error(self.top, f"Could not save '{path.name}':\n{exc}", title="Save failed")
            return False
        self.path = path
        self.text_widget.edit_modified(False)
        self.modified = False
        self._set_title(path.name)
        if self.on_saved:
            self.on_saved()
        return True

    def _prompt_save_if_modified(self) -> bool:
        """Returns True if OK to proceed (saved or discarded), False if cancelled."""
        if not self.modified:
            return True
        name = self.path.name if self.path else "Untitled"
        result = messagebox.askyesnocancel(
            "Text Window",
            f"'{name}' has unsaved changes. Save before closing?",
            parent=self.top,
        )
        if result is None:
            return False
        if result:
            return self._cmd_save()
        return True

    # ------------------------------------------------------------------
    # File menu commands
    # ------------------------------------------------------------------

    def _cmd_new(self) -> None:
        if self.read_only:
            return
        if not self._prompt_save_if_modified():
            return
        # Deactivate any active display mode
        if self._active_mode is not None:
            self._active_mode.on_deactivate(self)  # type: ignore[arg-type]
            var = getattr(self._active_mode, "_var", None)
            if var is not None:
                var.set(False)
            self._active_mode = None
            self._title_suffix = ""
        self.path = None
        self._raw_text = ""
        self.text_widget.configure(state="normal")
        self.text_widget.delete("1.0", "end")
        self.text_widget.edit_modified(False)
        self.modified = False
        self._table_rows = None
        self._table_truncated = False
        self._document_preview = False
        self._set_title("Untitled")
        self._update_status_bar()
        self._apply_edit_state()

    def _cmd_open(self) -> None:
        if self.read_only:
            return
        if not self._prompt_save_if_modified():
            return
        path_str = filedialog.askopenfilename(parent=self.top)
        if not path_str:
            return
        self._load_file(LocalFileSystem().from_path(Path(path_str)))

    def _cmd_save(self) -> bool:
        if self.read_only:
            return False
        if self.path is None:
            return self._cmd_save_as()
        return self._save_file(self.path)

    def _cmd_save_as(self) -> bool:
        if self.read_only:
            return False
        path_str = filedialog.asksaveasfilename(
            parent=self.top,
            defaultextension=".txt",
            filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")],
        )
        if not path_str:
            return False
        return self._save_file(LocalFileSystem().from_path(Path(path_str)))

    # ------------------------------------------------------------------
    # Edit menu commands
    # ------------------------------------------------------------------

    def _cmd_undo(self) -> None:
        if self.read_only:
            return
        try:
            self.text_widget.edit_undo()
        except tk.TclError:
            pass

    def _cmd_cut(self) -> None:
        if self.read_only:
            return
        self.text_widget.event_generate("<<Cut>>")

    def _cmd_copy(self) -> None:
        self.text_widget.event_generate("<<Copy>>")

    def _cmd_paste(self) -> None:
        if self.read_only:
            return
        self.text_widget.event_generate("<<Paste>>")

    def _cmd_select_all(self) -> None:
        self.text_widget.tag_add("sel", "1.0", "end")

    # ------------------------------------------------------------------
    # View menu commands
    # ------------------------------------------------------------------

    def _toggle_status_bar(self) -> None:
        if self._status_bar_var.get():
            self._status_frame.grid(row=2, column=0, columnspan=2, sticky="ew")
        else:
            self._status_frame.grid_remove()

    def _toggle_word_wrap(self) -> None:
        self.word_wrap = self._word_wrap_var.get()
        self.text_widget.configure(wrap="word" if self.word_wrap else "none")

    def _cmd_font(self) -> None:
        def _apply(family: str, size: int) -> None:
            self._font.configure(family=_resolve_font_family(family), size=size)

        _font_dialog(
            self.top,
            self._settings,
            self._font_family_attr,
            self._font_size_attr,
            _apply,
        )

    # ------------------------------------------------------------------
    # Hexdump toggle
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Hex mode helpers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # JSON pretty-print toggle
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # CSV table view
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Strings scan (background)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Syntax picker
    # ------------------------------------------------------------------

    def _on_syntax_pick(self) -> None:
        if self._active_mode is not None:
            return  # Syntax coloring is meaningless while a display mode is active
        name = self._syntax_var.get()
        self.forced_lang = name if name else None
        apply_highlighting(self.text_widget, self.path or _dummy_path(), self.forced_lang)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Helpers for rebuilding parts of the menu
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Change tracking + status bar
    # ------------------------------------------------------------------

    def _on_modified(self, event: tk.Event | None = None) -> None:
        if not self.text_widget.edit_modified():
            return
        # In read-only mode, buffer rewrites from hex/JSON toggles fire this
        # event too.  Always reset the flag but never mark the window modified.
        if self.read_only:
            self.text_widget.edit_modified(False)
            return
        self.modified = True
        self._set_title(self.path.name if self.path else "Untitled")
        self.text_widget.edit_modified(False)

    def _on_cursor_move(self, event: tk.Event | None = None) -> None:
        self._update_status_bar()

    def _update_status_bar(self) -> None:
        try:
            index = self.text_widget.index("insert")
            line, col = index.split(".")
            self._status_label.config(text=f"Ln {line}, Col {int(col) + 1}")
        except (tk.TclError, ValueError):
            self._status_label.config(text="Ln 1, Col 1")

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------

    def _on_close(self) -> None:
        if self._prompt_save_if_modified():
            # Deactivate any active display mode
            if self._active_mode is not None:
                self._active_mode.on_deactivate(self)  # type: ignore[arg-type]
                var = getattr(self._active_mode, "_var", None)
                if var is not None:
                    var.set(False)
                self._active_mode = None
            self.top.destroy()

    def _close_and_break(self, event: tk.Event | None = None) -> str:
        self._on_close()
        return "break"

    # ------------------------------------------------------------------
    # Public accessor (for callers that store the Toplevel reference)
    # ------------------------------------------------------------------

    def get_window(self) -> tk.Toplevel:
        return self.top


def _dummy_path() -> VfsPath:
    """Return a VfsPath with no suffix, used when self.path is None.

    Highlighting auto-detection will return None for a suffix-less path,
    which is fine — apply_highlighting clears stale tags and returns.
    """
    return LocalFileSystem().from_path(Path("untitled"))


# ---------------------------------------------------------------------------
# Public wrappers — keep app.py call sites unchanged
# ---------------------------------------------------------------------------


def view_file(
    parent: tk.Misc,
    path: VfsPath,
    settings: Settings | None = None,
) -> tk.Toplevel | None:
    """Open a read-only Toplevel showing ``path``'s contents.

    Returns the Toplevel (so callers/tests can drive it), or ``None`` if
    the file could not be read.
    """
    if plugins.viewer_plugin_for_name(path.name) is None:
        try:
            _read_capped(path)  # early error check before creating the window
        except OSError as exc:
            dialogs.error(parent, f"Could not open '{path.name}':\n{exc}", title="View failed")
            return None
    # Document-plugin files (xlsx/docx/...) are binary -- _read_capped's UTF-8
    # decode is meaningless for them; TextWindow._load_file reads and reports
    # errors itself via read_document.
    return TextWindow(parent, path, read_only=True, settings=settings).top


def edit_file(
    parent: tk.Misc,
    path: VfsPath,
    on_saved: Callable[[], None] | None = None,
    settings: Settings | None = None,
) -> tk.Toplevel | None:
    """Open an editable Toplevel for ``path``.

    If the file is truncated (> 2 MB), asks for confirmation before opening.
    Returns the Toplevel or ``None`` if the file could not be read or the user
    declined the truncation prompt.
    """
    # Document-plugin files (xlsx/docx/...) are binary and always opened
    # read-only (see TextWindow._load_file) -- the 2 MB text-truncation check
    # doesn't apply to them, and a multi-MB xlsx would otherwise falsely trip it.
    if plugins.viewer_plugin_for_name(path.name) is None:
        try:
            _, truncated = _read_capped(path)
        except OSError as exc:
            dialogs.error(parent, f"Could not open '{path.name}':\n{exc}", title="Edit failed")
            return None
        if truncated and not dialogs.confirm(
            parent,
            f"'{path.name}' exceeds the editor's size limit. Editing will only show "
            "(and saving will only write back) the first part of the file. Open anyway?",
            title="Large file",
        ):
            return None
    return TextWindow(parent, path, read_only=False, on_saved=on_saved, settings=settings).top


# ---------------------------------------------------------------------------
# Image viewer — re-exported from linux_commander.image_viewer
# ---------------------------------------------------------------------------

# Import here so callers that do `from linux_commander.viewer import view_image`
# keep working without change.
from linux_commander.image_viewer import view_image as view_image  # noqa: E402
