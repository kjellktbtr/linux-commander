"""Theme management extracted from CommanderApp (SRP).

Handles ttkbootstrap initialization, theme switching, and the theme picker
dialog.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from linux_commander import dialogs
from linux_commander.settings import Settings

# Module-level flag set by app.py before any theme work
_HAS_TTKBOOTSTRAP = False


def set_ttkbootstrap_available(available: bool) -> None:
    """Set whether ttkbootstrap is available (called by app.py at startup)."""
    global _HAS_TTKBOOTSTRAP
    _HAS_TTKBOOTSTRAP = available


def init_ttkbootstrap(parent: tk.Tk, settings: Settings):
    """Initialize ttkbootstrap and return a Style object, or None."""
    global _HAS_TTKBOOTSTRAP
    if not _HAS_TTKBOOTSTRAP:
        return None
    try:
        import ttkbootstrap as tb
        from ttkbootstrap.window import apply_all_bindings

        apply_all_bindings(parent)  # type: ignore[arg-type]
        theme = settings.theme or "darkly"
        return tb.Style(theme=theme)
    except Exception:
        return None


def apply_theme(style, theme_name: str, settings: Settings, apply_font_settings) -> None:
    """Switch to *theme_name*, re-apply custom panel styles, then fonts."""
    if style is None:
        return
    settings.theme = theme_name
    style.theme_use(theme_name)
    from linux_commander.panel import reset_style

    reset_style()
    apply_font_settings()


def show_theme_picker(parent: tk.Tk, settings: Settings, apply_theme_callback) -> None:
    """Theme picker: two columns (dark / light) with live preview."""
    if not _HAS_TTKBOOTSTRAP:
        dialogs.error(
            parent,
            "ttkbootstrap is not installed.\n\nRun: pip install ttkbootstrap",
            title="Theme",
        )
        return

    import ttkbootstrap as tb

    style = tb.Style(theme=settings.theme)

    # Classify themes as dark or light using ttkbootstrap's own metadata
    all_themes = sorted(style.theme_names())
    dark: list[str] = []
    light: list[str] = []
    for name in all_themes:
        try:
            style.theme_use(name)
            t = style.theme.type
        except Exception:
            t = "light"
        (dark if t == "dark" else light).append(name)
    # Restore current theme after the classification loop
    style.theme_use(settings.theme)

    saved_theme = settings.theme

    dialog = tk.Toplevel(parent)
    dialog.title("Theme")
    dialog.transient(parent)
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
        apply_theme_callback(dark[sel[0]])

    def _pick_light(event=None):
        sel = light_lb.curselection()
        if not sel:
            return
        dark_lb.selection_clear(0, tk.END)
        apply_theme_callback(light[sel[0]])

    dark_lb.bind("<<ListboxSelect>>", _pick_dark)
    light_lb.bind("<<ListboxSelect>>", _pick_light)

    # Pre-select the active theme
    cur = settings.theme
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
        apply_theme_callback(saved_theme)
        dialog.destroy()

    btn_frame = ttk.Frame(dialog)
    btn_frame.grid(row=2, column=0, columnspan=2, pady=8)
    ttk.Button(btn_frame, text="OK", command=_apply).pack(side="right", padx=4)
    ttk.Button(btn_frame, text="Cancel", command=_cancel).pack(side="right", padx=4)

    dialog.protocol("WM_DELETE_WINDOW", _cancel)
    dialogs._center_over(dialog, parent)
    dialog.grab_set()
    dialog.wait_window()
