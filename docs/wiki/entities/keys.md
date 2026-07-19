---
title: keys — F-Key Specification Table
type: entity
sources:
  - linux_commander/keys.py
related:
  - "[[app]]"
  - "[[readme-summary]]"
created: 2026-07-14
updated: 2026-07-14
confidence: high
---

# keys — F-Key Specification Table

## Purpose
Single source of truth for the bottom F-key bar (F1–F10) and the global keyboard bindings. Both the on-screen buttons and the `bind_all` handlers in `CommanderApp` reference `F_KEY_SPECS`.

## Data Structure

### `FKeySpec` (frozen dataclass)
- `key: str` — Tk keysym (e.g., "F1")
- `label: str` — Button caption (e.g., "Help")
- `handler_name: str` — Method name on `CommanderApp` (e.g., "cmd_help")

### `F_KEY_SPECS: tuple[FKeySpec | None, ...]`
Fixed 10-element tuple, index 0 = F1, index 9 = F10. `None` = unused slot (F2 in v1).

| Slot | Key | Label | Handler | Notes |
|------|-----|-------|---------|-------|
| 0 | F1 | Help | `cmd_help` | Shows cheat-sheet dialog |
| 1 | F2 | — | — | Reserved (classic NC "User menu") |
| 2 | F3 | View | `cmd_view` | Read-only viewer |
| 3 | F4 | Edit | `cmd_edit` | Built-in editor |
| 4 | F5 | Copy | `cmd_copy` | Copy to other panel |
| 5 | F6 | Move | `cmd_move` | Move/rename |
| 6 | F7 | MkDir | `cmd_mkdir` | Create directory |
| 7 | F8 | Delete | `cmd_delete` | Delete with confirm |
| 8 | F9 | Menu | `cmd_menu` | Placeholder pulldown |
| 9 | F10 | Quit | `cmd_quit` | Confirm and exit |

## Usage in `CommanderApp`

### F-Key Bar Construction (`_build_fkey_bar`)
```python
for index, spec in enumerate(F_KEY_SPECS):
    if spec is None:
        ttk.Label(bar, text="").grid(...)
        continue
    text = f"{spec.key} {spec.label}"
    button = ttk.Button(bar, text=text, command=lambda s=spec: self._dispatch(s))
```

### Global Key Bindings (`_bind_global_keys`)
```python
for spec in F_KEY_SPECS:
    if spec is None:
        continue
    self.bind_all(f"<{spec.key}>", lambda event, s=spec: self._dispatch(s))
```

### Dispatch (`_dispatch`)
```python
handler = getattr(self, spec.handler_name, None)
if handler is not None:
    handler()
```

## Design Rationale
- **Single source**: changing a key/label/handler updates both UI and binding
- **Extensible**: adding a key = add entry to tuple + implement handler
- **None slots**: reserve positions for future features without shifting indices
- **Keysyms**: uses standard Tk keysyms ("F1".."F10") — works cross-platform

## Related
- [[app]] — consumes `F_KEY_SPECS` for bar and bindings; implements all handlers
- [[readme-summary]] — keybinding table in README sourced from this