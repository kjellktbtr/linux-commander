"""JSON pretty-print viewer mode.

Toggles JSON pretty-printing: parses the raw text as JSON and displays it with
indentation.  Gracefully handles invalid JSON by showing an error dialog.
"""

from __future__ import annotations

import json as _json
import tkinter as tk

from linux_commander.viewer_modes import ViewerContext, ViewerMode


class JsonMode(ViewerMode):
    """JSON pretty-print display mode."""

    name = "JSON"
    exclusive_group = "display"

    # Tk state owned by this mode
    _var: tk.BooleanVar | None = None

    # ------------------------------------------------------------------
    # ViewerMode interface
    # ------------------------------------------------------------------

    def can_activate(self, ctx: ViewerContext) -> bool:
        return True

    def on_activate(self, ctx: ViewerContext) -> None:
        try:
            obj = _json.loads(ctx.raw_text)
            pretty = _json.dumps(obj, indent=ctx.settings.json_indent, ensure_ascii=False)
        except _json.JSONDecodeError as exc:
            ctx.show_error("JSON Pretty-Print", f"Not valid JSON:\n{exc}")
            if self._var is not None:
                self._var.set(False)
            return

        ctx.clear_text()
        ctx.insert_text(pretty)
        if not ctx.read_only:
            ctx.set_modified(True)
        ctx.set_title_suffix(self.title_suffix(ctx))
        ctx.apply_syntax_highlighting()

    def on_deactivate(self, ctx: ViewerContext) -> None:
        ctx.clear_text()
        ctx.insert_text(ctx.raw_text)
        if not ctx.read_only:
            ctx.set_modified(True)
        ctx.set_title_suffix("")
        ctx.apply_syntax_highlighting()

    def build_menu(self, ctx: ViewerContext, menu: tk.Menu) -> None:
        self._var = tk.BooleanVar(value=False)
        menu.add_checkbutton(
            label="JSON Pretty-Print",
            variable=self._var,
            command=lambda: self._toggle(ctx),
            underline=0,
        )

    def title_suffix(self, ctx: ViewerContext) -> str:
        return " [JSON]"

    # ------------------------------------------------------------------
    # Toggle handler
    # ------------------------------------------------------------------

    def _toggle(self, ctx: ViewerContext) -> None:
        if self._var is None:
            return
        entering = self._var.get()
        if entering:
            ctx.deactivate_other_modes(self.exclusive_group)
            self.on_activate(ctx)
        else:
            self.on_deactivate(ctx)
            ctx.reactivate_group(self.exclusive_group)


mode_class = JsonMode
