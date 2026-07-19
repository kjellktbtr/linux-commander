---
title: Orthodox File Manager Design Patterns
type: concept
sources:
  - linux_commander/app.py
  - linux_commander/panel.py
  - linux_commander/keys.py
  - docs/raw/README.md
related:
  - "[[app]]"
  - "[[panel]]"
  - "[[keys]]"
  - "[[readme-summary]]"
created: 2026-07-14
updated: 2026-07-14
confidence: high
---

# Orthodox File Manager (OFM) Design Patterns

## Definition
An "orthodox file manager" (also "Norton Commander clone" or "Midnight Commander style") is a dual-pane file manager with a specific interaction model originating from Norton Commander (1986) and carried forward by Midnight Commander, Total Commander, Far Manager, and others.

## Core Principles

### Dual Panels
- Two independent directory listings side by side
- Exactly one panel is **active** (focused) at a time
- `Tab` switches active panel
- Operations (copy/move/delete) act on the **active** panel's selection
- Target directory defaults to the **inactive** panel's current directory

### Keyboard-First, F-Key Command Bar
- Permanent bottom bar showing F1–F10 functions
- Same functions accessible via keyboard F-keys
- Classic Norton/MC layout:
  - F1 Help, F2 User Menu, F3 View, F4 Edit
  - F5 Copy, F6 Move/Rename, F7 MkDir, F8 Delete
  - F9 Menu, F10 Quit
- Buttons are clickable too (mouse support)

### Cursor Navigation (Not Selection-Driven)
- Single cursor (highlighted row) in active panel
- Arrow keys, PgUp/PgDn, Home/End move cursor
- **Insert** (or `Ctrl+T` in MC) toggles **tag/mark** on cursor file and moves down
- `+` / `-` / `*` pattern tag/untag/invert
- Operations use **tagged set**; if empty, use **cursor file only**
- Tags persist within a directory; cleared on navigation/refresh

### Directory Navigation
- **Enter** on directory → descend
- **Enter** on `..` (or **Backspace**) → go up
- When going up, previously selected directory is re-selected in parent
- Root (`/`) has no `..` entry

### File Operations
- F5/F6: prompt for target (defaults to other panel's path)
- Progress dialog with cancel for long operations
- F8 Delete: permanent (no trash in v1), confirm with count
- F7 MkDir: prompt name, create in active panel

### Viewer/Editor (F3/F4)
- Built-in, no external `$PAGER`/`$EDITOR` dependency
- F3: read-only, monospace, scrollbars
- F4: editable, Ctrl+S/F2 save, atomic write (temp + replace)

### Volume/Drive Selector
- Per-panel row of buttons (drive bar) showing selectable roots
- Alt+F1 / Alt+F2: pop-up chooser for left / right panel (fixed panels, not active)
- Linux: mount points from `/proc/mounts` (filtered)
- Windows (future): drive letters (A:, C:, D:...)
- macOS (future): `/Volumes`

## linux-commander Implementation Mapping

| OFM Concept | linux-commander Module |
|-------------|------------------------|
| Dual panels | `app.py`: `CommanderApp` with two `FilePanel` |
| Active panel | `active_panel` attribute, `Tab` → `_switch_active_panel` |
| F-key bar | `keys.F_KEY_SPECS` + `_build_fkey_bar` + `_bind_global_keys` |
| Cursor navigation | `panel.py`: `move_cursor`, `move_to_first/last`, `_activate_cursor` |
| Tagging | `panel.py`: `marked: set[Path]`, `_on_insert_key`, `apply_pattern`, `invert_selection` |
| Selected entries | `panel.selected_entries()` → marked or `[cursor]` |
| Directory load | `panel.load(path, select_name)` |
| File ops | `operations.py` + `dialogs.run_with_progress` |
| Viewer/Editor | `viewer.py`: `view_file`, `edit_file` |
| Volume bar | `volumes.list_volumes()` → `panel._populate_volume_bar` |
| Alt+F1/F2 | `app._choose_volume(panel)` |

## Key Differences from Classic OFM
| Feature | Classic | linux-commander v1 |
|---------|---------|-------------------|
| F2 User Menu | Yes | Reserved (None) |
| F9 Menu | Full pulldown | Placeholder only |
| Trash on Delete | Sometimes | No (permanent) |
| External editor | `$EDITOR` | Built-in only |
| Panel tabs | Some (TC) | No (single dir per panel) |
| Command line | Bottom input | Status line only (no CLI) |
| Color themes | Extensive | Monospace + active panel highlight |

## Future Extensions
- [ ] F9 full menu system
- [ ] Command line (bottom input for shell commands)
- [ ] Panel tabs (multiple dirs per panel)
- [ ] External editor/viewer config
- [ ] Trash integration (`send2trash`)
- [ ] Color themes / highlighting rules