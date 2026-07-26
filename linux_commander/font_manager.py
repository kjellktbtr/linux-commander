"""Font management extracted from CommanderApp (SRP).

Handles applying font settings to styled widgets and showing font picker
dialogs.
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from linux_commander import dialogs
from linux_commander.panel import FilePanel
from linux_commander.settings import Settings


def apply_font_settings(
    parent: tk.Tk, settings: Settings, left_panel: FilePanel | None, right_panel: FilePanel | None
) -> None:
    """Apply font settings from settings to all styled widgets.

    Everything goes through ``ttk.Style`` — direct ``.configure(font=...)``
    on ttk widgets raises ``TclError: unknown option "-font"``.

    Named Tk aliases such as "TkFixedFont" are NOT real family names; passing
    them to ``tkfont.Font(family=...)`` silently resolves to the wrong family
    (e.g. "Noto Sans" instead of "Noto Sans Mono").  We resolve them via
    ``nametofont`` first.  The Font object is stored on *parent* to prevent
    Python from garbage-collecting it while Tk still references it.
    """
    family = settings.font_family
    size = settings.font_size
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
    parent._panel_font = tkfont.Font(family=family, size=size, weight=weight)  # type: ignore[attr-defined]
    font = parent._panel_font  # type: ignore[attr-defined]
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
    for panel in (left_panel, right_panel):
        if panel is not None:
            panel.update_font(font)
            prev = panel.current_index()
            panel.load(panel.current_path)
            if prev is not None:
                panel.select_index(prev)


def show_font_picker(parent: tk.Tk, settings: Settings, apply_font_callback) -> None:
    """Font selection dialog for the main application panels (live preview)."""
    families = sorted(tkfont.families())
    mono_families = [
        f
        for f in families
        if "mono" in f.lower() or "courier" in f.lower() or "console" in f.lower()
    ]
    if mono_families:
        families = mono_families + [f for f in families if f not in mono_families]

    dialog = tk.Toplevel(parent)
    dialog.title("Font")
    dialog.transient(parent)
    dialog.resizable(False, False)

    ttk.Label(dialog, text="Font:").grid(row=0, column=0, padx=8, pady=8, sticky="w")
    font_var = tk.StringVar(value=settings.font_family)
    font_combo = ttk.Combobox(
        dialog, textvariable=font_var, values=families, state="readonly", width=30
    )
    font_combo.grid(row=0, column=1, padx=8, pady=8)

    ttk.Label(dialog, text="Size:").grid(row=1, column=0, padx=8, pady=8, sticky="w")
    size_var = tk.IntVar(value=settings.font_size)
    size_spin = ttk.Spinbox(dialog, from_=8, to=72, textvariable=size_var, width=5)
    size_spin.grid(row=1, column=1, padx=8, pady=8, sticky="w")

    # Snapshot for Cancel restore
    _saved_family = settings.font_family
    _saved_size = settings.font_size

    def preview(*_) -> None:
        fam = font_var.get()
        if not fam:
            return
        try:
            sz = int(size_var.get())
        except (ValueError, tk.TclError):
            return
        settings.font_family = fam
        settings.font_size = sz
        apply_font_callback()

    def apply_font() -> None:
        preview()
        dialog.destroy()

    def cancel_font() -> None:
        settings.font_family = _saved_family
        settings.font_size = _saved_size
        apply_font_callback()
        dialog.destroy()

    font_combo.bind("<<ComboboxSelected>>", preview)
    size_spin.configure(command=preview)
    size_spin.bind("<KeyRelease>", preview)

    btn_frame = ttk.Frame(dialog)
    btn_frame.grid(row=2, column=0, columnspan=2, pady=8)
    ttk.Button(btn_frame, text="OK", command=apply_font).pack(side="right", padx=4)
    ttk.Button(btn_frame, text="Cancel", command=cancel_font).pack(side="right", padx=4)

    dialog.protocol("WM_DELETE_WINDOW", cancel_font)
    dialogs._center_over(dialog, parent)
    dialog.grab_set()
    font_combo.focus_set()
    dialog.wait_window()


def show_font_dialog(
    parent: tk.Tk, settings: Settings, title: str, family_attr: str, size_attr: str
) -> None:
    """Generic font selection dialog for editor/viewer (apply-on-OK, cancel restores)."""
    families = sorted(tkfont.families())
    mono_families = [
        f
        for f in families
        if "mono" in f.lower() or "courier" in f.lower() or "console" in f.lower()
    ]
    if mono_families:
        families = mono_families + [f for f in families if f not in mono_families]

    dialog = tk.Toplevel(parent)
    dialog.title(title)
    dialog.transient(parent)
    dialog.resizable(False, False)

    ttk.Label(dialog, text="Font:").grid(row=0, column=0, padx=8, pady=8, sticky="w")
    font_var = tk.StringVar(value=getattr(settings, family_attr))
    font_combo = ttk.Combobox(
        dialog, textvariable=font_var, values=families, state="readonly", width=30
    )
    font_combo.grid(row=0, column=1, padx=8, pady=8)

    ttk.Label(dialog, text="Size:").grid(row=1, column=0, padx=8, pady=8, sticky="w")
    size_var = tk.IntVar(value=getattr(settings, size_attr))
    size_spin = ttk.Spinbox(dialog, from_=8, to=72, textvariable=size_var, width=5)
    size_spin.grid(row=1, column=1, padx=8, pady=8, sticky="w")

    _saved_family = getattr(settings, family_attr)
    _saved_size = getattr(settings, size_attr)

    def apply_font() -> None:
        setattr(settings, family_attr, font_var.get())
        setattr(settings, size_attr, size_var.get())
        dialog.destroy()

    def cancel_font() -> None:
        setattr(settings, family_attr, _saved_family)
        setattr(settings, size_attr, _saved_size)
        dialog.destroy()

    btn_frame = ttk.Frame(dialog)
    btn_frame.grid(row=2, column=0, columnspan=2, pady=8)
    ttk.Button(btn_frame, text="OK", command=apply_font).pack(side="right", padx=4)
    ttk.Button(btn_frame, text="Cancel", command=cancel_font).pack(side="right", padx=4)

    dialog.protocol("WM_DELETE_WINDOW", cancel_font)
    dialogs._center_over(dialog, parent)
    dialog.grab_set()
    font_combo.focus_set()
    dialog.wait_window()
