---
title: viewer — Unified TextWindow (F3 viewer + F4 editor)
type: entity
sources:
  - linux_commander/viewer.py
  - linux_commander/view_modes.py
related:
  - "[[app]]"
  - "[[panel]]"
  - "[[platform_util]]"
  - "[[readme-summary]]"
  - "[[syntax]]"
created: 2026-07-14
updated: 2026-07-22
confidence: high
---

# viewer — Unified TextWindow (F3 viewer + F4 editor)

## Purpose

A single `TextWindow` class drives both the F3 read-only viewer and the F4
editor.  F3 opens read-only; F4 opens editable; pressing F4 inside a read-only
window promotes it to edit mode in place.  The image viewer (`view_image`) is a
separate, standalone function.

## SOLID Refactoring — ViewMode Strategy

`linux_commander/view_modes.py` provides the `ViewMode(ABC)` base class and concrete implementations (`CsvMode`, `HexMode`, `StringsMode`, `JsonMode`). Each mode encapsulates its own rendering logic, toggle behavior, and whether it blocks editing. Composed with `TextWindow` rather than inheriting from it.

```python
class ViewMode(ABC):
    name: str
    @property
    def is_active(self) -> bool: ...
    def toggle(self, window: object) -> None: ...
    def render(self, window: object) -> None: ...
    @property
    def blocks_edit(self) -> bool: ...
```

## Public API

### `view_file(parent, path, settings=None) -> Toplevel | None`
Opens read-only `TextWindow` for `path`.
- Early-exit read check: returns `None` on `OSError`, shows error dialog.
- Window: 800x600, title `"View - {filename}"`.
- Text widget `state="disabled"`.

### `edit_file(parent, path, on_saved=None, settings=None) -> Toplevel | None`
Opens editable `TextWindow` for `path`.
- If the file exceeds 2 MB, confirms before opening (truncation warning).
- Returns `None` on error or user-declined truncation.
- `on_saved()` called after each successful atomic save.

### `view_image(parent, path, image_files, start_index, image_extensions, settings=None) -> Toplevel | None`
Standalone image viewer (unchanged from earlier design).
- Canvas-based with h/v scrollbars; Left/Right navigation; Shift+Left/Right = same extension.
- Menu: File -> Exit, View -> Font...

## TextWindow — Mode/State Model

Instance attributes (single source of truth):

| Attribute | Type | Meaning |
|-----------|------|---------|
| `read_only` | bool | view vs edit; drives text-widget `state`, menus, title suffix |
| `hex_mode` | bool | hexdump display active; mutually exclusive with editing |
| `json_formatted` | bool | pretty-printed JSON currently shown |
| `forced_lang` | `str \| None` | None = auto by extension; else a language name from the Syntax menu |
| `_raw_text` | str | as-loaded text; preserved so JSON/hex toggles can restore original |
| `modified` | bool | unsaved changes; drives `*` in title |
| `word_wrap` | bool | wrap mode |

**Derived rule:** text widget is editable only when `not read_only and not hex_mode`.
`_apply_edit_state()` sets `state="normal"/"disabled"` and is called after every mode change.

## Features

### F4 toggle (read-only -> edit mode)
`_enable_editing()` flips `read_only=False`, switches to editor font, rebuilds
menus, updates title (`View` -> `Edit`). Save guard: `_save_file()` checks
`path.fs.realpath(path) is None` and blocks saves on read-only filesystems.

### Search (Ctrl+F)
- Search bar: entry, Regex toggle, Ignore-case toggle (default on), Prev/Next buttons.
- Uses `tk.Text.search(..., regexp=, nocase=, backwards=, count=count_var)`.
- Wraps at end/start. Active match highlighted with `search_current` tag
  (orange), others with `search_hit` tag (dark blue).
- Works in both read-only and edit modes.
- Enter = next, Shift+Enter = prev, Escape = hide + clear.

### Hexdump (View menu toggle)
- `_format_hexdump(data)`: 8-hex offset, 16 bytes per row (gap after 8), ASCII
  gutter with non-printables as `.`.
- Raw bytes read via `_read_raw_capped(path)` (capped at 2 MB).
- While active: editing disabled, syntax tags cleared, JSON toggle greyed.
- Leaving: restores `_raw_text`, re-applies highlighting, restores edit state.
- Never marks modified.
- **Auto-switch**: Files containing null bytes (`\x00`) are automatically opened
  in hex mode.
- **Hex Tools submenu** (enabled in hex mode):
  - **Go to Offset... (Ctrl+G)**: Jump to a specific byte offset (hex or decimal).
  - **Find in Hex... (Ctrl+Shift+F)**: Search for hex byte sequences (e.g. `48 65 6C 6C 6F`) or ASCII text.
  - **Copy as Hex (Ctrl+Shift+C)**: Copy selected bytes as space-separated hex string.
  - **Copy as C Array**: Copy selected bytes as a C array initializer (`unsigned char data[] = { 0x48, 0x65, ... };`).

### JSON Pretty-Print (View menu toggle)
- `json.dumps(json.loads(src), indent=settings.json_indent)`.
- On `JSONDecodeError`: error dialog, checkbutton reverted, content unchanged.
- In edit mode: marks buffer modified (`*` in title). In read-only: does not.
- Saving in edit mode writes the currently-shown content (format-on-save).
- Disabled while hex mode is active.

### Syntax Language Picker (Syntax menu)
- Radiobuttons: "Auto (by extension)" + one per `available_languages()`.
- Selecting a language sets `forced_lang` and re-calls
  `apply_highlighting(widget, path, forced_lang)`.
- Switching clears all `syntax_*` tags (complete sweep via `_clear_syntax_tags`),
  leaving no stale colours from a prior language.

## Menus

| Menu | Read-only | Editable |
|------|-----------|----------|
| File | Exit only | New/Open/Save/Save As/Exit |
| Edit | Copy, Select All only | Undo, Cut, Copy, Paste, Select All, Find... |
| View | Status Bar, Word Wrap, Hexdump, JSON Pretty-Print, Font... | same |
| Syntax | Auto + language radiobuttons | same |

## Pure Helpers (unit-testable)

| Function | Description |
|----------|-------------|
| `_read_capped(path, max_bytes=2MB)` | `(str, truncated)` — UTF-8 with error replacement |
| `_read_raw_capped(path, max_bytes=2MB)` | `(bytes, truncated)` — raw bytes |
| `_format_hexdump(data)` | Classic hexdump string |
| `_center_over(top, parent)` | Center a Toplevel over its parent |
| `_font_dialog(parent, settings, family_attr, size_attr, on_apply)` | Shared font chooser |

## Key Bindings

| Window | Keys | Action |
|--------|------|--------|
| Both | F4 | Enable editing (no-op if already editable) |
| Both | Ctrl+F | Show search bar |
| Both | Ctrl+G | Go to Offset (hex mode) |
| Both | Esc, F3, F10, X | Close (with unsaved-changes prompt in edit mode) |
| Both | Ctrl+C | Copy selection |
| Both | Ctrl+A | Select All |
| Edit | Ctrl+S, F2 | Save |
| Edit | Ctrl+N | New |
| Edit | Ctrl+O | Open... |
| Edit | F12 | Save As... |
| Edit | Ctrl+Z | Undo |
| Edit | Ctrl+X | Cut |
| Edit | Ctrl+V | Paste |
| Hex | Ctrl+Shift+F | Find in Hex |
| Hex | Ctrl+Shift+C | Copy as Hex |

## `_save_file()` and non-local writable backends (2026-07-18)

`_save_file()` used to treat `path.fs.realpath(path) is None` as unconditionally read-only — it only ever knew how to save via a local write-to-temp-then-atomic-`replace` dance, so **saving any file on a writable backend with no real local file (Jottacloud, SMB, WebDAV, SFTP) always failed** with "Cannot save: this file is in a read-only filesystem," even though the backend was genuinely writable (Shift+F4 → create the file worked fine, since `operations.make_file()` correctly uses `path.fs.open_write()` — only the *save* step was broken). Fixed to branch three ways: real local path → the existing atomic temp+replace; no real path but `path.fs.writable` → `path.fs.open_write(path)` (the same VFS write API every other write path in the app uses); neither → the original read-only error, now only shown when actually true.

## Testing

- `tests/test_viewer.py`: `_read_capped`, `_read_raw_capped` (truncation, exact
  boundary, binary), `_format_hexdump` (empty, one row, two rows, non-printable,
  multi-row offset increments).
- `tests/test_syntax.py`: `available_languages()`, `lang_by_name()`, Bash/Batch
  extension detection, `_clear_syntax_tags` stub test.
- GUI behavior verified via scripted drivers against a real Tk display.

## Related
- [[app]] — `cmd_view`, `cmd_edit`, `_on_activate_file`
- [[panel]] — triggers viewer/editor on file activate
- [[syntax]] — `apply_highlighting`, `available_languages`, `_clear_syntax_tags`
- [[settings]] — `json_indent`, `viewer_font_*`, `editor_font_*`
- [[dialogs]] — `confirm`, `error` used for prompts/errors
