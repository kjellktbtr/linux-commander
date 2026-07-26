---
title: app — CommanderApp (Main Application Shell)
type: entity
sources:
  - linux_commander/app.py
  - linux_commander/session_manager.py
  - linux_commander/theme_manager.py
  - linux_commander/font_manager.py
  - linux_commander/operations_controller.py
related:
  - "[[panel]]"
  - "[[fs]]"
  - "[[operations]]"
  - "[[dialogs]]"
  - "[[viewer]]"
  - "[[volumes]]"
  - "[[platform_util]]"
  - "[[keys]]"
  - "[[readme-summary]]"
created: 2026-07-14
updated: 2026-07-22
confidence: high
---

# app — CommanderApp (Main Application Shell)

## Purpose
Top-level `tk.Tk` subclass. Creates the dual-pane layout, F-key bar, status line, and wires all global key bindings and F-key handlers. After SOLID refactoring, `CommanderApp` delegates to extracted controllers for session management, theme/font handling, and file operations.

## Extracted Controllers

| Controller | Module | Responsibility |
|------------|--------|----------------|
| `SessionManager` | `session_manager.py` | Save/restore panel paths, marks, sort state, active side |
| `ThemeManager` | `theme_manager.py` | ttkbootstrap init, theme switching, theme picker dialog |
| `FontManager` | `font_manager.py` | Apply font settings, font picker dialogs |
| `OperationsController` | `operations_controller.py` | Copy, move, delete, mkdir, compress, new file, file info |

## Class: `CommanderApp(tk.Tk)`

### Construction
```python
CommanderApp(left_path=None, right_path=None)
```
- Defaults both to `Path.cwd()`
- Creates two `FilePanel` instances (left/right) in a 2-column grid (equal weight)
- Builds F-key bar (row 2) from `keys.F_KEY_SPECS`
- Builds menu bar: **File** (Theme..., Font..., Editor Font..., Viewer Font..., FTP Connections..., Command Settings..., Command Prompt, Quit), **View** (Show Hidden Files, Refresh, Sort by Name/Date/Size)
- Status label at row 1 (spans both columns)
- Sets `active_panel = left_panel` initially
- Binds global keys (`_bind_global_keys`)
- `WM_DELETE_WINDOW` → `cmd_quit`

### Panel Callbacks
Each `FilePanel` receives:
- `on_activate_file` → `_on_activate_file` (delegates to `OperationsController`)
- `on_tab` → `_switch_active_panel`
- `on_marks_changed` → `_update_status`
- `on_directory_changed` → `_update_title`
- `on_error` → `_show_error`

### Active Panel Management
- `_switch_active_panel()` — toggles `active_panel`, updates header styles, status, title
- `_update_active_panel_style()` — calls `panel.set_active(bool)` on both
- `_other_panel()` — returns the inactive panel (for copy/move target default)

### Window Title
- `_update_title()` — `"linux-commander - {active_panel.current_path}"`
- Fired on panel load, Tab switch, and explicitly after ops

### Status Line
- `_update_status()` — shows `"N marked (size)"` if any tagged files, else empty

## F-Key Handlers (match `keys.F_KEY_SPECS`)

| Handler | Action |
|---------|--------|
| `cmd_help` | `dialogs.show_text` with `HELP_TEXT` cheat-sheet |
| `cmd_view` | `viewer.view_file(self, cursor_entry.path)` for files; `active_panel.load()` for directories |
| `cmd_edit` | `viewer.edit_file(self, cursor_entry.path, on_saved=refresh)` |
| `cmd_copy` | Delegates to `OperationsController.cmd_copy()` |
| `cmd_move` | Delegates to `OperationsController.cmd_move()` |
| `cmd_mkdir` | Delegates to `OperationsController.cmd_mkdir()` |
| `cmd_delete` | Delegates to `OperationsController.cmd_delete()` |
| `cmd_compress` | Delegates to `OperationsController.cmd_compress()` |
| `cmd_new_file` | Delegates to `OperationsController.cmd_new_file()` |
| `cmd_file_info` | Delegates to `OperationsController.cmd_file_info()` |
| `cmd_menu` | Popup `tk.Menu` at pointer (placeholder disabled items) |
| `cmd_font` | Delegates to `FontManager` |
| `cmd_theme` | Delegates to `ThemeManager` |
| `cmd_quit` | `dialogs.confirm` → `self.destroy()` |

## Global Key Bindings (`_bind_global_keys`)

| Keys | Action |
|------|--------|
| F1–F10 | Dispatch via `F_KEY_SPECS` (bind_all) |
| Alt+F1 | `_choose_volume(left_panel)` (fixed left panel) |
| Alt+F2 | `_choose_volume(right_panel)` (fixed right panel) |
| Ctrl+H | `active_panel.toggle_hidden()` |
| Ctrl+R | `_refresh_panel_preserving_position(active_panel)` |
| Ctrl+F3 | `active_panel.set_sort("name")` |
| Ctrl+F5 | `active_panel.set_sort("mtime")` |
| Ctrl+F6 | `active_panel.set_sort("size")` |
| Ctrl+Q | `cmd_quit()` |

**Note**: Tab, Enter, Backspace, Insert, +, -, * are bound on each `FilePanel`'s Treeview (not `bind_all`) so they don't interfere with modal dialogs.

## Volume Chooser (`_choose_volume`)
- `volumes.list_volumes()` → labels → `dialogs.choose_from_list`
- Selected volume → `panel.load(volume.path)`
- Alt+F1 always targets left, Alt+F2 always targets right (classic convention)

## Error Handling
- `_show_error(msg)` → `dialogs.error(self, msg)`
- Panel `on_error` callback wired here

## Theme-switching crash fix (2026-07-18)

`ttkbootstrap.Style.theme_use()` restyles every widget it knows about via a global `Publisher`/subscriber registry that every `ttk.Combobox` auto-joins on creation. Widgets are normally removed from that registry by a `<Destroy>` binding that `ttkbootstrap.window.Window`/`Toplevel` install automatically in their own `__init__` — but `CommanderApp` subclasses plain `tk.Tk`, not ttkbootstrap's `Window`, so that cleanup was never wired up. Every closed dialog containing a Combobox (compression dialog, connections manager, search dialog, the viewer's font picker, the duplicate finder, ...) left a stale widget reference behind, and the next `theme_use()` call (`cmd_theme`, `_apply_theme`) crashed with `_tkinter.TclError: bad window path name` the moment it reached one — which for `cmd_theme`'s dark/light-classification loop (calls `theme_use()` once per available theme) meant almost any theme switch after any dialog had been opened and closed. Fixed by calling `ttkbootstrap.window.apply_all_bindings(self)` in `_init_ttkbootstrap()`, installing the same `<Destroy>`-triggered unsubscribe that `Window.__init__` sets up. Verified by reproducing the exact crash (create+destroy a bare `ttk.Combobox` in a `Toplevel`, then cycle `theme_use()`) under Xvfb, both before and after the fix.

## Entry Point
```python
def main(left_path=None, right_path=None):
    app = CommanderApp(left_path, right_path)
    app.mainloop()
```
Called by `__main__.py` and `pyproject.toml` script entry point.

## Related
- [[panel]] — creates two instances, receives callbacks
- [[keys]] — consumes `F_KEY_SPECS` for bar and bindings
- [[operations]] — invoked by copy/move/delete/mkdir handlers
- [[dialogs]] — all modals (confirm, prompt, error, progress, choose_from_list, show_text)
- [[viewer]] — F3/F4 handlers
- [[volumes]] — Alt+F1/F2 volume chooser
- [[platform_util]] — Enter-on-file default app fallback