"""File operation: Search and replace in files with preview and backup.

Supports regex find/replace across selected files with:
- Preview of matches before applying
- Automatic .bak backup creation
- Per-file change reporting
"""

from __future__ import annotations

import re
import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk
from typing import TYPE_CHECKING

from linux_commander.dialogs import _center_over
from linux_commander.file_ops import FileOperation
from linux_commander.operations import CancelPredicate, OperationError, ProgressCallback
from linux_commander.vfs import VfsPath

if TYPE_CHECKING:
    pass


@dataclass
class MatchInfo:
    """A single match found in a file."""

    file: VfsPath
    line_num: int
    line: str
    match_start: int
    match_end: int
    replacement: str


@dataclass
class FileMatchSummary:
    """Summary of matches for a single file."""

    file: VfsPath
    matches: list[MatchInfo]
    total_matches: int


def _search_file(
    path: VfsPath,
    find_pattern: str,
    use_regex: bool,
    case_sensitive: bool,
    max_matches: int = 1000,
) -> list[MatchInfo]:
    """Search a file for pattern matches. Returns list of MatchInfo."""
    matches: list[MatchInfo] = []
    flags = 0 if case_sensitive else re.IGNORECASE

    try:
        with path.fs.open_read(path) as f:
            content = f.read().decode("utf-8", errors="replace")
    except Exception:
        return matches

    lines = content.splitlines(keepends=True)
    line_num = 0

    if use_regex:
        try:
            pattern = re.compile(find_pattern, flags)
        except re.error:
            return matches
        for line in lines:
            line_num += 1
            for m in pattern.finditer(line):
                matches.append(
                    MatchInfo(
                        file=path,
                        line_num=line_num,
                        line=line.rstrip("\n\r"),
                        match_start=m.start(),
                        match_end=m.end(),
                        replacement="",  # Filled in later
                    )
                )
                if len(matches) >= max_matches:
                    return matches
    else:
        # Literal search
        find_lower = find_pattern.lower() if not case_sensitive else find_pattern
        for line in lines:
            line_num += 1
            search_line = line.lower() if not case_sensitive else line
            start = 0
            while True:
                idx = search_line.find(find_lower, start)
                if idx == -1:
                    break
                matches.append(
                    MatchInfo(
                        file=path,
                        line_num=line_num,
                        line=line.rstrip("\n\r"),
                        match_start=idx,
                        match_end=idx + len(find_pattern),
                        replacement="",
                    )
                )
                start = idx + len(find_pattern)
                if len(matches) >= max_matches:
                    return matches

    return matches


def _replace_in_file(
    path: VfsPath,
    find_pattern: str,
    replace_pattern: str,
    use_regex: bool,
    case_sensitive: bool,
    create_backup: bool,
) -> tuple[int, str | None]:
    """Perform replacement in a file. Returns (num_replacements, error_message)."""
    flags = 0 if case_sensitive else re.IGNORECASE

    try:
        with path.fs.open_read(path) as f:
            content = f.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, f"Read error: {e}"

    original_content = content

    if use_regex:
        try:
            pattern = re.compile(find_pattern, flags)
            new_content, count = pattern.subn(replace_pattern, content)
        except re.error as e:
            return 0, f"Regex error: {e}"
    else:
        if case_sensitive:
            count = content.count(find_pattern)
            new_content = content.replace(find_pattern, replace_pattern)
        else:
            # Case-insensitive literal replace
            pattern = re.compile(re.escape(find_pattern), flags)
            new_content, count = pattern.subn(replace_pattern, content)

    if count == 0:
        return 0, None

    # Create backup if requested
    if create_backup:
        backup_path = path / (path.name + ".bak")
        try:
            with backup_path.fs.open_write(backup_path) as f:
                f.write(original_content.encode("utf-8"))
        except Exception as e:
            return 0, f"Backup creation failed: {e}"

    # Write modified content
    try:
        with path.fs.open_write(path) as f:
            f.write(new_content.encode("utf-8"))
    except Exception as e:
        return 0, f"Write error: {e}"

    return count, None


def _prepare_search_replace(parent: tk.Misc, sources: list[VfsPath]) -> dict | None:
    """Show search & replace dialog and return parameters."""
    dialog = SearchReplaceDialog(parent, sources)
    return dialog.result


class SearchReplaceDialog(tk.Toplevel):
    """Dialog for search and replace with preview."""

    def __init__(self, parent: tk.Misc, sources: list[VfsPath]) -> None:
        super().__init__(parent)
        self.title("Search & Replace in Files")
        self.sources = sources
        self.result: dict | None = None
        self._file_summaries: list[FileMatchSummary] = []
        self._build_ui()
        self._update_preview()
        _center_over(self, parent)
        self.grab_set()
        self.wait_window()

    def _build_ui(self) -> None:
        self.geometry("900x600")
        self.minsize(700, 450)

        # Top controls
        top_frame = ttk.Frame(self)
        top_frame.pack(fill="x", padx=8, pady=8)

        ttk.Label(top_frame, text="Find:").grid(row=0, column=0, sticky="w", padx=(0, 4))
        self.find_var = tk.StringVar()
        ttk.Entry(top_frame, textvariable=self.find_var, width=40).grid(
            row=0, column=1, sticky="ew", padx=(0, 8)
        )

        ttk.Label(top_frame, text="Replace:").grid(row=0, column=2, sticky="w", padx=(0, 4))
        self.replace_var = tk.StringVar()
        ttk.Entry(top_frame, textvariable=self.replace_var, width=40).grid(
            row=0, column=3, sticky="ew"
        )

        top_frame.columnconfigure(1, weight=1)
        top_frame.columnconfigure(3, weight=1)

        # Options row
        opts_frame = ttk.Frame(self)
        opts_frame.pack(fill="x", padx=8, pady=(0, 8))

        self.use_regex_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            opts_frame,
            text="Regular Expression",
            variable=self.use_regex_var,
            command=self._update_preview,
        ).pack(side="left", padx=(0, 8))

        self.case_sensitive_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            opts_frame,
            text="Case Sensitive",
            variable=self.case_sensitive_var,
            command=self._update_preview,
        ).pack(side="left", padx=(0, 8))

        self.backup_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            opts_frame,
            text="Create .bak backup",
            variable=self.backup_var,
        ).pack(side="left", padx=(0, 8))

        # Preview table
        table_frame = ttk.LabelFrame(self, text="Preview (double-click file to expand)", padding=4)
        table_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        columns = ("file", "matches", "first_match")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("file", text="File")
        self.tree.heading("matches", text="Matches", anchor="center")
        self.tree.heading("first_match", text="First Match Preview")
        self.tree.column("file", width=300, anchor="w")
        self.tree.column("matches", width=80, anchor="center")
        self.tree.column("first_match", width=400, anchor="w")
        self.tree.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")

        # Context menu for expanded matches
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Return>", self._on_double_click)

        # Bottom summary and buttons
        bottom_frame = ttk.Frame(self)
        bottom_frame.pack(fill="x", padx=8, pady=(0, 8))

        self.summary_var = tk.StringVar(value="")
        ttk.Label(bottom_frame, textvariable=self.summary_var).pack(side="left")

        btn_frame = ttk.Frame(bottom_frame)
        btn_frame.pack(side="right")

        ttk.Button(btn_frame, text="Preview", command=self._update_preview).pack(
            side="left", padx=4
        )
        ttk.Button(btn_frame, text="Cancel", command=self._on_cancel).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Replace", command=self._on_replace).pack(side="left", padx=4)

        self.bind("<Escape>", lambda e: self._on_cancel())

    def _update_preview(self) -> None:
        """Search all files and update preview table."""
        find_text = self.find_var.get()
        if not find_text:
            self._clear_tree()
            self.summary_var.set("Enter a search pattern")
            return

        use_regex = self.use_regex_var.get()
        case_sensitive = self.case_sensitive_var.get()

        self._file_summaries = []
        total_matches = 0
        files_with_matches = 0

        for src in self.sources:
            matches = _search_file(src, find_text, use_regex, case_sensitive)
            if matches:
                # Apply replacement to each match for preview
                replace_text = self.replace_var.get()
                if use_regex:
                    try:
                        pattern = re.compile(find_text, 0 if case_sensitive else re.IGNORECASE)
                        for m in matches:
                            matched_text = m.line[m.match_start : m.match_end]
                            m.replacement = pattern.sub(replace_text, matched_text)
                    except re.error:
                        for m in matches:
                            m.replacement = replace_text
                else:
                    for m in matches:
                        m.replacement = replace_text

                files_with_matches += 1
                total_matches += len(matches)
                self._file_summaries.append(
                    FileMatchSummary(
                        file=src,
                        matches=matches,
                        total_matches=len(matches),
                    )
                )

        self._refresh_tree()
        self.summary_var.set(f"{files_with_matches} files, {total_matches} matches")

    def _clear_tree(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)

    def _refresh_tree(self) -> None:
        self._clear_tree()
        for summary in self._file_summaries:
            first_match = summary.matches[0] if summary.matches else None
            preview = ""
            if first_match:
                line = first_match.line
                match_text = line[first_match.match_start : first_match.match_end]
                start_ctx = max(0, first_match.match_start - 20)
                preview = (
                    f"Line {first_match.line_num}: ...{line[start_ctx : first_match.match_start]}"
                )
                preview += f"▶{match_text}◀"
                preview += f"{line[first_match.match_end : first_match.match_end + 20]}..."
            self.tree.insert(
                "",
                "end",
                values=(
                    str(summary.file),
                    summary.total_matches,
                    preview,
                ),
                tags=(str(summary.file),),
            )

    def _on_double_click(self, event) -> None:
        """Show detailed matches for selected file."""
        selection = self.tree.selection()
        if not selection:
            return
        item = selection[0]
        file_path = self.tree.item(item, "tags")[0]

        # Find the summary
        summary = next((s for s in self._file_summaries if str(s.file) == file_path), None)
        if not summary:
            return

        MatchDetailDialog(self, summary, self.replace_var.get(), self.use_regex_var.get())

    def _on_cancel(self) -> None:
        self.result = None
        self.destroy()

    def _on_replace(self) -> None:
        find_text = self.find_var.get()
        if not find_text:
            return
        self.result = {
            "find_pattern": find_text,
            "replace_pattern": self.replace_var.get(),
            "use_regex": self.use_regex_var.get(),
            "case_sensitive": self.case_sensitive_var.get(),
            "create_backup": self.backup_var.get(),
        }
        self.destroy()


class MatchDetailDialog(tk.Toplevel):
    """Dialog showing all matches in a single file with replacements."""

    def __init__(
        self,
        parent: tk.Misc,
        summary: FileMatchSummary,
        replace_pattern: str,
        use_regex: bool,
    ) -> None:
        super().__init__(parent)
        self.title(f"Matches in {summary.file.name}")
        self.geometry("800x400")
        self._build_ui(summary, replace_pattern, use_regex)
        _center_over(self, parent)
        self.grab_set()
        self.wait_window()

    def _build_ui(self, summary: FileMatchSummary, replace_pattern: str, use_regex: bool) -> None:
        columns = ("line", "original", "replacement")
        self.tree = ttk.Treeview(self, columns=columns, show="headings")
        self.tree.heading("line", text="Line", anchor="center")
        self.tree.heading("original", text="Original")
        self.tree.heading("replacement", text="After Replace")
        self.tree.column("line", width=60, anchor="center")
        self.tree.column("original", width=350, anchor="w")
        self.tree.column("replacement", width=350, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=8, pady=8)

        for m in summary.matches:
            original = m.line
            replacement_line = original[: m.match_start] + m.replacement + original[m.match_end :]
            self.tree.insert("", "end", values=(m.line_num, original, replacement_line))

        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(btn_frame, text="Close", command=self.destroy).pack(side="right")


def _run_search_replace(
    sources: list[VfsPath],
    dest_dir: VfsPath,
    on_progress: ProgressCallback,
    should_cancel: CancelPredicate,
    find_pattern: str,
    replace_pattern: str,
    use_regex: bool,
    case_sensitive: bool,
    create_backup: bool,
) -> list[OperationError]:
    """Run search & replace on all source files."""
    errors: list[OperationError] = []
    total = len(sources)

    for current, src in enumerate(sources, start=1):
        if should_cancel():
            break
        on_progress(current, total, src.name, None, None)

        count, error = _replace_in_file(
            src,
            find_pattern,
            replace_pattern,
            use_regex,
            case_sensitive,
            create_backup,
        )
        if error:
            errors.append(OperationError(path=src, message=error))

    return errors


OPERATIONS: list[FileOperation] = [
    FileOperation(
        name="Search & Replace…",
        run=_run_search_replace,
        prepare=_prepare_search_replace,
        description="Search and replace text in selected files with regex support and preview",
    ),
]
