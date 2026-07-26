"""Search dialog: non-modal, composable criteria, live panelizes results.

``SearchCriteria`` (the UI-layer model) lives in
``linux_commander.search_criteria`` but is re-exported here for
backwards compatibility.
"""

from __future__ import annotations

import json
import tkinter as tk
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import ttk

from linux_commander import dialogs

# Re-export so callers using `from linux_commander.search_dialog import SearchCriteria` still work.
from linux_commander.search_criteria import SearchCriteria as SearchCriteria
from linux_commander.settings import get_config_dir
from linux_commander.vfs import VfsPath


def _get_presets_path() -> Path:
    """Return the path to the search presets JSON file."""
    return get_config_dir() / "search_presets.json"


def load_presets() -> dict:
    """Load search presets from disk."""
    path = _get_presets_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_presets(presets: dict) -> None:
    """Save search presets to disk."""
    path = _get_presets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("w", encoding="utf-8") as f:
            json.dump(presets, f, indent=2)
    except OSError:
        pass  # Silently fail if we can't write


class SearchDialog:
    """Non-modal search dialog that launches background searches."""

    def __init__(
        self,
        parent: tk.Misc,
        app,
        initial_path: VfsPath,
    ) -> None:
        self.parent = parent
        self.app = app
        self.criteria = SearchCriteria()
        self.criteria.root_path = initial_path
        self._presets = load_presets()

        self.top = tk.Toplevel(parent)
        self.top.title("Find Files")
        self.top.transient(parent)  # type: ignore[call-overload]
        self.top.resizable(False, False)

        self._create_widgets()
        self._bind_keys()

        dialogs._center_over(self.top, parent)
        self.top.grab_set()  # keep on top but allow interaction with main window

    def _create_widgets(self) -> None:
        # Main frame with notebook-like sections
        main = ttk.Frame(self.top, padding=8)
        main.pack(fill="both", expand=True)

        # Root path row
        root_frame = ttk.Frame(main)
        root_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(root_frame, text="Search in:").pack(side="left")
        self.root_var = tk.StringVar(value=str(self.criteria.root_path))
        ttk.Entry(root_frame, textvariable=self.root_var, width=50).pack(
            side="left", padx=4, fill="x", expand=True
        )
        ttk.Button(root_frame, text="...", width=3, command=self._browse_root).pack(side="left")

        # Preset dropdown
        preset_frame = ttk.Frame(main)
        preset_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(preset_frame, text="Preset:").pack(side="left")
        self.preset_var = tk.StringVar()
        self.preset_combo = ttk.Combobox(
            preset_frame,
            textvariable=self.preset_var,
            values=list(self._presets.keys()),
            state="readonly",
            width=30,
        )
        self.preset_combo.pack(side="left", padx=4, fill="x", expand=True)
        self.preset_combo.bind("<<ComboboxSelected>>", self._on_preset_selected)
        save_btn = ttk.Button(preset_frame, text="Save", command=self._save_preset, width=6)
        save_btn.pack(side="left", padx=2)
        del_btn = ttk.Button(preset_frame, text="Delete", command=self._delete_preset, width=6)
        del_btn.pack(side="left")

        # Notebook for criteria sections
        self.notebook = ttk.Notebook(main)
        self.notebook.pack(fill="both", expand=True)

        # Size tab
        self._create_size_tab()

        # Date tab
        self._create_date_tab()

        # Name tab
        self._create_name_tab()

        # Content tab
        self._create_content_tab()

        # Default to Name tab
        self.notebook.select(2)

        # Archive option
        arc_frame = ttk.Frame(main)
        arc_frame.pack(fill="x", pady=(4, 0))
        self.search_archives_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            arc_frame,
            text="Search inside archives (.zip, .tar, .tar.gz, …)",
            variable=self.search_archives_var,
        ).pack(anchor="w")

        # Bottom buttons
        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill="x", pady=(8, 0))
        self._search_btn = ttk.Button(btn_frame, text="Search", command=self._on_search)
        self._search_btn.pack(side="right", padx=4)
        ttk.Button(btn_frame, text="Cancel", command=self._on_cancel).pack(side="right")
        ttk.Button(btn_frame, text="Reset", command=self._on_reset).pack(side="left")

    def _create_size_tab(self) -> None:
        frame = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(frame, text="Size")

        self.size_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame,
            text="Enable size filter",
            variable=self.size_enabled,
            command=self._update_size_state,
        ).pack(anchor="w", pady=2)

        # Unit combobox
        unit_frame = ttk.Frame(frame)
        unit_frame.pack(fill="x", pady=2)
        ttk.Label(unit_frame, text="Unit:").pack(side="left")
        self.size_unit = tk.StringVar(value="KB")
        unit_combo = ttk.Combobox(
            unit_frame,
            textvariable=self.size_unit,
            values=["B", "KB", "MB", "GB"],
            width=6,
            state="readonly",
        )
        unit_combo.pack(side="left", padx=4)
        unit_combo.bind("<<ComboboxSelected>>", lambda e: self._update_size_entries())

        # Min/Max entries
        min_frame = ttk.Frame(frame)
        min_frame.pack(fill="x", pady=2)
        ttk.Label(min_frame, text="Min:").pack(side="left")
        self.size_min_var = tk.StringVar()
        self.size_min_entry = ttk.Entry(
            min_frame, textvariable=self.size_min_var, width=12, state="disabled"
        )
        self.size_min_entry.pack(side="left", padx=4)

        max_frame = ttk.Frame(frame)
        max_frame.pack(fill="x", pady=2)
        ttk.Label(max_frame, text="Max:").pack(side="left")
        self.size_max_var = tk.StringVar()
        self.size_max_entry = ttk.Entry(
            max_frame, textvariable=self.size_max_var, width=12, state="disabled"
        )
        self.size_max_entry.pack(side="left", padx=4)

        # Hint
        hint = ttk.Label(
            frame,
            text="Leave empty for no limit. Greater: fill Min only. Less: fill Max only.",
            foreground="gray",
        )
        hint.pack(anchor="w", pady=(4, 0))

    def _create_date_tab(self) -> None:
        frame = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(frame, text="Date")

        self.date_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame,
            text="Enable date filter",
            variable=self.date_enabled,
            command=self._update_date_state,
        ).pack(anchor="w", pady=2)

        # From
        from_frame = ttk.Frame(frame)
        from_frame.pack(fill="x", pady=2)
        ttk.Label(from_frame, text="From (newer than):").pack(side="left")
        self.date_from_var = tk.StringVar(
            value=(datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M")
        )
        self.date_from_entry = ttk.Entry(
            from_frame, textvariable=self.date_from_var, width=20, state="disabled"
        )
        self.date_from_entry.pack(side="left", padx=4)
        self.date_from_btn = ttk.Button(
            from_frame,
            text="...",
            width=3,
            state="disabled",
            command=lambda: self._pick_date("from"),
        )
        self.date_from_btn.pack(side="left")

        # To
        to_frame = ttk.Frame(frame)
        to_frame.pack(fill="x", pady=2)
        ttk.Label(to_frame, text="To (older than):").pack(side="left")
        self.date_to_var = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d %H:%M"))
        self.date_to_entry = ttk.Entry(
            to_frame, textvariable=self.date_to_var, width=20, state="disabled"
        )
        self.date_to_entry.pack(side="left", padx=4)
        self.date_to_btn = ttk.Button(
            to_frame, text="...", width=3, state="disabled", command=lambda: self._pick_date("to")
        )
        self.date_to_btn.pack(side="left")

        # Presets
        preset_frame = ttk.Frame(frame)
        preset_frame.pack(fill="x", pady=4)
        ttk.Label(preset_frame, text="Presets:").pack(side="left")
        ttk.Button(preset_frame, text="Last 7 days", command=lambda: self._preset_date(7)).pack(
            side="left", padx=2
        )
        ttk.Button(preset_frame, text="Last 30 days", command=lambda: self._preset_date(30)).pack(
            side="left", padx=2
        )
        ttk.Button(preset_frame, text="Today", command=lambda: self._preset_date(0)).pack(
            side="left", padx=2
        )
        ttk.Button(preset_frame, text="Yesterday", command=lambda: self._preset_date(1)).pack(
            side="left", padx=2
        )

        hint = ttk.Label(
            frame,
            text="Leave empty for no limit. Newer: fill From only. Older: fill To only.",
            foreground="gray",
        )
        hint.pack(anchor="w", pady=(4, 0))

    def _create_name_tab(self) -> None:
        frame = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(frame, text="Name")

        self.name_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame,
            text="Enable name filter",
            variable=self.name_enabled,
            command=self._update_name_state,
        ).pack(anchor="w", pady=2)

        pattern_frame = ttk.Frame(frame)
        pattern_frame.pack(fill="x", pady=2)
        ttk.Label(pattern_frame, text="Pattern:").pack(side="left")
        self.name_pattern_var = tk.StringVar(value="*")
        self.name_pattern_entry = ttk.Entry(
            pattern_frame, textvariable=self.name_pattern_var, width=40, state="disabled"
        )
        self.name_pattern_entry.pack(side="left", padx=4, fill="x", expand=True)

        opt_frame = ttk.Frame(frame)
        opt_frame.pack(fill="x", pady=2)
        self.name_case_var = tk.BooleanVar(value=False)
        self.name_case_cb = ttk.Checkbutton(
            opt_frame, text="Case sensitive", variable=self.name_case_var, state="disabled"
        )
        self.name_case_cb.pack(side="left")
        self.name_regex_var = tk.BooleanVar(value=False)
        self.name_regex_cb = ttk.Checkbutton(
            opt_frame, text="Regular expression", variable=self.name_regex_var, state="disabled"
        )
        self.name_regex_cb.pack(side="left", padx=8)

        hint = ttk.Label(
            frame,
            text="Glob syntax: * ? [abc]. Regex: check the box. History is saved.",
            foreground="gray",
        )
        hint.pack(anchor="w", pady=(4, 0))

    def _create_content_tab(self) -> None:
        frame = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(frame, text="Content")

        self.content_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame,
            text="Enable content search",
            variable=self.content_enabled,
            command=self._update_content_state,
        ).pack(anchor="w", pady=2)

        pattern_frame = ttk.Frame(frame)
        pattern_frame.pack(fill="x", pady=2)
        ttk.Label(pattern_frame, text="Search for:").pack(side="left")
        self.content_pattern_var = tk.StringVar()
        self.content_pattern_entry = ttk.Entry(
            pattern_frame, textvariable=self.content_pattern_var, width=40, state="disabled"
        )
        self.content_pattern_entry.pack(side="left", padx=4, fill="x", expand=True)

        mode_frame = ttk.Frame(frame)
        mode_frame.pack(fill="x", pady=2)
        ttk.Label(mode_frame, text="Mode:").pack(side="left")
        self.content_mode_var = tk.StringVar(value="string")
        self.content_mode_string = ttk.Radiobutton(
            mode_frame,
            text="String",
            variable=self.content_mode_var,
            value="string",
            state="disabled",
        )
        self.content_mode_regex = ttk.Radiobutton(
            mode_frame,
            text="Regex",
            variable=self.content_mode_var,
            value="regex",
            state="disabled",
        )
        self.content_mode_hex = ttk.Radiobutton(
            mode_frame,
            text="Hex bytes",
            variable=self.content_mode_var,
            value="hex",
            state="disabled",
        )
        for rb in (self.content_mode_string, self.content_mode_regex, self.content_mode_hex):
            rb.pack(side="left", padx=4)

        self.content_case_var = tk.BooleanVar(value=False)
        self.content_case_cb = ttk.Checkbutton(
            frame, text="Case sensitive", variable=self.content_case_var, state="disabled"
        )
        self.content_case_cb.pack(anchor="w", pady=2)

        hint = ttk.Label(
            frame,
            text="Hex mode: space-separated bytes (e.g. 'FF D8 FF E0'). "
            "Case-insensitive hex digits.",
            foreground="gray",
        )
        hint.pack(anchor="w", pady=(4, 0))

    def _bind_keys(self) -> None:
        self.top.bind("<Escape>", lambda e: self._on_cancel())
        self.top.protocol("WM_DELETE_WINDOW", self._on_cancel)

    def _update_size_state(self) -> None:
        state = "normal" if self.size_enabled.get() else "disabled"
        self.size_min_entry.config(state=state)
        self.size_max_entry.config(state=state)
        # Also enable/disable unit combo
        for child in self.size_min_entry.master.winfo_children():
            if isinstance(child, ttk.Combobox):
                child.config(state="readonly" if state == "normal" else "disabled")

    def _update_size_entries(self) -> None:
        # Could add unit conversion here if needed
        pass

    def _update_date_state(self) -> None:
        state = "normal" if self.date_enabled.get() else "disabled"
        self.date_from_entry.config(state=state)
        self.date_to_entry.config(state=state)
        self.date_from_btn.config(state=state)
        self.date_to_btn.config(state=state)

    def _update_name_state(self) -> None:
        state = "normal" if self.name_enabled.get() else "disabled"
        self.name_pattern_entry.config(state=state)
        self.name_case_cb.config(state=state)
        self.name_regex_cb.config(state=state)

    def _update_content_state(self) -> None:
        state = "normal" if self.content_enabled.get() else "disabled"
        self.content_pattern_entry.config(state=state)
        self.content_mode_string.config(state=state)
        self.content_mode_regex.config(state=state)
        self.content_mode_hex.config(state=state)
        self.content_case_cb.config(state=state)

    def _browse_root(self) -> None:
        from tkinter import filedialog

        current = self.root_var.get()
        path = filedialog.askdirectory(
            initialdir=current, parent=self.top, title="Choose search root"
        )
        if path:
            self.root_var.set(path)
            self.criteria.root_path = self.app._local_fs.from_path(path)

    def _pick_date(self, which: str) -> None:
        initial_str = self.date_from_var.get() if which == "from" else self.date_to_var.get()
        try:
            initial = datetime.strptime(initial_str, "%Y-%m-%d %H:%M")
        except ValueError:
            initial = datetime.now() if which == "to" else datetime.now() - timedelta(days=7)

        picked = dialogs.date_time_picker(
            self.top, f"Select {'From' if which == 'from' else 'To'} date/time", initial
        )
        if picked:
            if which == "from":
                self.date_from_var.set(picked.strftime("%Y-%m-%d %H:%M"))
            else:
                self.date_to_var.set(picked.strftime("%Y-%m-%d %H:%M"))

    def _preset_date(self, days_ago: int) -> None:
        now = datetime.now()
        if days_ago == 0:
            # Today: from 00:00 to now
            self.date_from_var.set(
                now.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M")
            )
            self.date_to_var.set(now.strftime("%Y-%m-%d %H:%M"))
        else:
            # Last N days: from N days ago 00:00 to now
            from_dt = (now - timedelta(days=days_ago)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            self.date_from_var.set(from_dt.strftime("%Y-%m-%d %H:%M"))
            self.date_to_var.set(now.strftime("%Y-%m-%d %H:%M"))

    def _on_preset_selected(self, event=None) -> None:
        """Load the selected preset into the dialog."""
        name = self.preset_var.get()
        if name in self._presets:
            preset = self._presets[name]
            self._apply_preset(preset)

    def _apply_preset(self, preset: dict) -> None:
        """Apply a preset dictionary to the dialog fields."""
        # Root path
        if "root_path" in preset:
            self.root_var.set(preset["root_path"])
            self.criteria.root_path = self.app._local_fs.from_path(preset["root_path"])

        # Search archives
        if "search_archives" in preset:
            self.search_archives_var.set(preset["search_archives"])

        # Size
        if "size" in preset:
            s = preset["size"]
            self.size_enabled.set(s.get("enabled", False))
            self._update_size_state()
            self.size_unit.set(s.get("unit", "KB"))
            self.size_min_var.set(s.get("min", ""))
            self.size_max_var.set(s.get("max", ""))

        # Date
        if "date" in preset:
            d = preset["date"]
            self.date_enabled.set(d.get("enabled", False))
            self._update_date_state()
            self.date_from_var.set(d.get("from", ""))
            self.date_to_var.set(d.get("to", ""))

        # Name
        if "name" in preset:
            n = preset["name"]
            self.name_enabled.set(n.get("enabled", False))
            self._update_name_state()
            self.name_pattern_var.set(n.get("pattern", "*"))
            self.name_case_var.set(n.get("case_sensitive", False))
            self.name_regex_var.set(n.get("regex", False))

        # Content
        if "content" in preset:
            c = preset["content"]
            self.content_enabled.set(c.get("enabled", False))
            self._update_content_state()
            self.content_pattern_var.set(c.get("pattern", ""))
            self.content_mode_var.set(c.get("mode", "string"))
            self.content_case_var.set(c.get("case_sensitive", False))

    def _save_preset(self) -> None:
        """Save current dialog state as a preset."""
        name = dialogs.prompt(self.top, "Save Search Preset", "Enter preset name:")
        if not name:
            return
        if name in self._presets:
            if not dialogs.confirm(self.top, f"Overwrite preset '{name}'?", "Confirm Overwrite"):
                return

        preset = self._collect_preset()
        self._presets[name] = preset
        save_presets(self._presets)
        self.preset_combo.config(values=list(self._presets.keys()))
        self.preset_var.set(name)

    def _collect_preset(self) -> dict:
        """Collect current dialog state into a preset dictionary."""
        preset: dict = {}

        # Root path
        preset["root_path"] = self.root_var.get()

        # Search archives
        preset["search_archives"] = self.search_archives_var.get()

        # Size
        preset["size"] = {
            "enabled": self.size_enabled.get(),
            "unit": self.size_unit.get(),
            "min": self.size_min_var.get(),
            "max": self.size_max_var.get(),
        }

        # Date
        preset["date"] = {
            "enabled": self.date_enabled.get(),
            "from": self.date_from_var.get(),
            "to": self.date_to_var.get(),
        }

        # Name
        preset["name"] = {
            "enabled": self.name_enabled.get(),
            "pattern": self.name_pattern_var.get(),
            "case_sensitive": self.name_case_var.get(),
            "regex": self.name_regex_var.get(),
        }

        # Content
        preset["content"] = {
            "enabled": self.content_enabled.get(),
            "pattern": self.content_pattern_var.get(),
            "mode": self.content_mode_var.get(),
            "case_sensitive": self.content_case_var.get(),
        }

        return preset

    def _delete_preset(self) -> None:
        """Delete the selected preset."""
        name = self.preset_var.get()
        if not name:
            return
        if not dialogs.confirm(self.top, f"Delete preset '{name}'?", "Confirm Delete"):
            return
        del self._presets[name]
        save_presets(self._presets)
        self.preset_combo.config(values=list(self._presets.keys()))
        self.preset_var.set("")

    def _on_search(self) -> None:
        if not self._collect_criteria():
            return

        # Keep the dialog open (so it can offer a Stop button) but release
        # its grab so the user can interact with the results panel — click
        # rows, sort columns, scroll — while the search runs in the
        # background.
        self.top.grab_release()
        self._search_btn.config(text="Stop", command=self._on_stop)

        # Start search in app
        engine_criteria = self.criteria.to_engine_criteria()
        summary = self.criteria.to_summary()

        self.app._search_controller.start(
            engine_criteria, summary, self.app.active_panel, self._on_search_done
        )

    def _on_stop(self) -> None:
        """Cancel the running search and restore the Search button."""
        self.app._search_controller.cancel()
        self._search_btn.config(text="Search", command=self._on_search)
        self.top.grab_set()

    def _on_search_done(self) -> None:
        """Callback when search finishes (or is cancelled)."""
        self._search_btn.config(text="Search", command=self._on_search)
        self.top.grab_set()

    def _collect_criteria(self) -> bool:
        """Collect all criteria from the dialog into self.criteria."""
        # Root path
        try:
            self.criteria.root_path = self.app._local_fs.from_path(self.root_var.get())
        except Exception:
            dialogs.error(self.top, "Invalid search root path.")
            return False

        # Search archives
        self.criteria.search_archives = self.search_archives_var.get()

        # Size
        if self.size_enabled.get():
            try:
                min_val = self._parse_size(self.size_min_var.get(), self.size_unit.get())
                max_val = self._parse_size(self.size_max_var.get(), self.size_unit.get())
                self.criteria.size_min = min_val
                self.criteria.size_max = max_val
            except ValueError:
                dialogs.error(self.top, "Invalid size value.")
                return False
        else:
            self.criteria.size_min = None
            self.criteria.size_max = None

        # Date
        if self.date_enabled.get():
            try:
                self.criteria.date_from = (
                    datetime.strptime(self.date_from_var.get(), "%Y-%m-%d %H:%M")
                    if self.date_from_var.get()
                    else None
                )
                self.criteria.date_to = (
                    datetime.strptime(self.date_to_var.get(), "%Y-%m-%d %H:%M")
                    if self.date_to_var.get()
                    else None
                )
            except ValueError:
                dialogs.error(self.top, "Invalid date format. Use YYYY-MM-DD HH:MM")
                return False
        else:
            self.criteria.date_from = None
            self.criteria.date_to = None

        # Name
        if self.name_enabled.get():
            self.criteria.name_pattern = self.name_pattern_var.get()
            self.criteria.name_case_sensitive = self.name_case_var.get()
            self.criteria.name_regex = self.name_regex_var.get()
        else:
            self.criteria.name_pattern = ""

        # Content
        if self.content_enabled.get():
            self.criteria.content_pattern = self.content_pattern_var.get()
            self.criteria.content_mode = self.content_mode_var.get()
            self.criteria.content_case_sensitive = self.content_case_var.get()
        else:
            self.criteria.content_pattern = ""

        return True

    def _parse_size(self, value: str, unit: str) -> int | None:
        if not value:
            return None
        try:
            num = float(value)
        except ValueError as e:
            raise ValueError(f"Invalid size: {value}") from e
        multipliers = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}
        return int(num * multipliers[unit])

    def _on_cancel(self) -> None:
        if self.app._search_controller is not None:
            self._on_stop()
        self.top.destroy()

    def _on_reset(self) -> None:
        """Reset all criteria to defaults."""
        self.size_enabled.set(False)
        self._update_size_state()
        self.size_min_var.set("")
        self.size_max_var.set("")
        self.size_unit.set("KB")

        self.date_enabled.set(False)
        self._update_date_state()
        self.date_from_var.set((datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M"))
        self.date_to_var.set(datetime.now().strftime("%Y-%m-%d %H:%M"))

        self.name_enabled.set(False)
        self._update_name_state()
        self.name_pattern_var.set("*")
        self.name_case_var.set(False)
        self.name_regex_var.set(False)

        self.content_enabled.set(False)
        self._update_content_state()
        self.content_pattern_var.set("")
        self.content_mode_var.set("string")
        self.content_case_var.set(False)

        self.search_archives_var.set(False)
