---
title: panel — FilePanel Widget
type: entity
sources:
  - linux_commander/panel.py
related:
  - "[[fs]]"
  - "[[app]]"
  - "[[volumes]]"
  - "[[dialogs]]"
  - "[[readme-summary]]"
created: 2026-07-14
updated: 2026-07-15
confidence: high
---

# panel — FilePanel Widget

## Purpose
A single directory-listing pane (one half of the dual-panel OFM). Wraps a `ttk.Treeview` with Name/Size/Modified columns, keyboard navigation, tagging/selection, volume bar, and directory loading.

## Public API

### `FilePanel(master, initial_path, show_hidden=True, on_activate_file, on_tab, on_marks_changed, on_directory_changed, on_error)`
Constructs the panel, builds UI, loads `initial_path`.

**Callbacks:**
- `on_activate_file(entry)` — Enter/double-click on a file (not dir)
- `on_tab()` — Tab pressed (switch panel)
- `on_marks_changed()` — Tag set/cleared
- `on_directory_changed()` — Directory loaded (navigation or refresh)
- `on_error(msg)` — Errors during load (permission, etc.)

### `load(path, select_name=None)`
Reloads panel at `path`. Clears marks. If `select_name` given, tries to select that entry (used when navigating up to re-select the directory we came from).

### Navigation
- `move_cursor(delta)` — Up/down (delta=±1), PgUp/PgDn (±PAGE_SIZE), Home/End
- `go_up()` — Load parent directory, re-select child we came from
- `_activate_cursor()` — Enter on dir → descend; on file → `on_activate_file`; on ".." → go up

### Tagging/Selection
- `marked: set[Path]` — paths of tagged entries
- `_on_insert_key()` — Insert toggles mark on cursor entry (skips ".."), moves down
- `apply_pattern(pattern, mark, case_sensitive=False, is_regex=False)` — `+`/`-`: tag/untag by glob or regex pattern
- `invert_selection()` — flip all marks (except "..")
- `mark_all()` — `*`: mark every non-parent entry
- `_prompt_and_apply_pattern(mark)` — `+`/`-`: opens pattern dialog with history combobox, case-sensitive and regex checkboxes
- `marked_entries() -> list[FileEntry]` — only explicitly marked entries
- `selected_entries() -> list[FileEntry]` — marked entries, or `[cursor_entry]` if none marked (never includes "..")
- `cursor_entry() -> FileEntry | None` — currently focused row

### View Options
- `toggle_hidden()` — Ctrl+H: toggle `show_hidden`, reload
- `set_sort(key)` — Ctrl+F3/F5/F6: set sort key (name/mtime/size), toggle reverse on repeat
- Header shows sort indicator like `[Name v]` (ascending: `^`, descending: `v`)
- `toggle_flat_view()` — toggle recursive (flat) directory listing
- `set_visible_columns(columns)` — set visible columns from: name, size, modified, extension, permissions, owner, group

### History Navigation
- `go_back()` — navigate to previous directory in history
- `go_forward()` — navigate to next directory in history
- `can_go_back()` — check if back navigation is possible
- `can_go_forward()` — check if forward navigation is possible
- History is limited to 50 entries per panel

### Volume Bar
- `_populate_volume_bar()` — builds row of buttons from `volumes.list_volumes()` at construction (static snapshot)
- Clicking a volume button loads that root

### Active/Inactive Style
- `set_active(bool)` — updates header style (blue active, gray inactive) and focuses Treeview when activated

## Key Bindings (on Treeview)
- Arrows, PgUp/PgDn, Home/End → cursor movement
- Enter / Double-click → activate
- Backspace → go up
- Insert → toggle mark + move down
- `+` / `-` / `*` → pattern tag / untag / invert
- Tab → `on_tab` (handled by app for panel switch)

## Internal State
- `_entries: list[FileEntry]` — current listing (sorted)
- `marked: set[Path]` — tagged paths
- `current_path: Path` — directory being shown
- `sort_key: SortKey`, `sort_reverse: bool`
- `show_hidden: bool`
- `_rowid_to_entry: dict[str, FileEntry]` — Treeview iid → FileEntry

## Testing
Verified via scripted driver (`verify_panel.py`) against real Tk: cursor movement, clamping, Home/End, descend into dir, go-up-reselects-child, activate "..", marks clearing on load.

## Related
- [[fs]] — `list_directory`, `sort_entries`, `format_size`, `format_mtime`
- [[app]] — creates two panels, routes callbacks
- [[volumes]] — volume bar buttons
- [[dialogs]] — pattern prompts via `simpledialog.askstring`