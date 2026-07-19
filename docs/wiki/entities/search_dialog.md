---
title: Search Dialog — Find Files (Alt+F7 / Shift+F7)
type: entity
sources:
  - linux_commander/search_dialog.py
  - linux_commander/search_criteria.py
  - linux_commander/search_controller.py
  - CONTRIBUTING.md
related:
  - "[[search_engine]]"
  - "[[panel]]"
  - "[[dialogs]]"
  - "[[vfs]]"
created: 2026-07-17
updated: 2026-07-18
confidence: high
---

# Search Dialog — Find Files (Alt+F7 / Shift+F7)

`linux_commander/search_dialog.py` is the **modeless** Find Files dialog with 4 criteria tabs + options.

## Tabs

| Tab | Fields |
|-----|--------|
| **Name** | Pattern, ☐ Regex, ☐ Case sensitive |
| **Size** | Min / Max + unit (B/KB/MB/GB), empty = unbounded |
| **Date** | From / To (YYYY-MM-DD HH:MM + picker), presets: Last 7d, 30d, Today, Yesterday |
| **Content** | Pattern, Mode (String/Regex/Hex), ☐ Case sensitive |
| **Options** | ☐ Search inside archives |

## Presets

The dialog includes a **Preset** dropdown at the top with **Save** and **Delete** buttons. Presets store the complete search criteria (root path, all tab settings, archive option) to `~/.config/linux-commander/search_presets.json`.

- **Save** — prompts for a name, stores current criteria
- **Load** — select from dropdown to populate all fields
- **Delete** — removes the selected preset

## Behavior

- **Modeless** — dialog stays open, main window usable
- **Background search** — `SearchController` starts `search_engine.search_worker()` on `Thread`
- **Live results** — matches stream into a **results panel** (FilePanel subclass) in the active panel
- **Search → Stop** — button toggles; clicking Stop sets cancel event
- **Escape** — if results panel active: exit results mode; else: close dialog

## Results Panel

`SearchController` (in `search_controller.py`):
- Replaces active panel's view with a virtual "search results" panel
- Panel shows matches with Name/Size/Modified columns
- Click column header to sort; double-click/Enter opens original file
- Tab switches to other panel (results persist in the panel that ran search)

## UI → Engine Conversion

`SearchCriteriaUI` (in `search_criteria.py`):
- Holds Tkinter variables for each field
- `to_engine()` → frozen `SearchCriteria` dataclass for worker

## Cross-Reference

- [[search_engine]] — background worker, criteria, archive descent
- [[panel]] — FilePanel reused for results display
- [[dialogs]] — base modal-dialog infrastructure (buttons, layout)
- [[vfs]] — roots passed to worker are `VfsPath`s (local, archive, remote)