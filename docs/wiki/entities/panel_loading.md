---
title: PanelLoading — Tree Population Helpers
type: entity
sources:
  - linux_commander/panel_loading.py
related:
  - "[[panel]]"
  - "[[fs]]"
  - "[[vfs]]"
created: 2026-07-22
updated: 2026-07-22
confidence: high
---

# PanelLoading — Tree Population Helpers

`linux_commander/panel_loading.py` provides panel loading helpers — tree population and entry formatting. Extracted from `FilePanel.load()` to reduce the panel module size.

## Functions

### `list_and_sort(panel, path) -> list[FileEntry] | None`

List directory entries, filter hidden files, and sort. Returns `None` on error (error already reported via `panel._on_error`).

### `populate_tree(panel, entries) -> None`

Populate the panel's Treeview widget with file entries. Formats name, size, date, and extension columns using `fs.format_size()` and `fs.format_mtime()`.

### `format_entry(panel, entry) -> tuple`

Format a single `FileEntry` into a Treeview row tuple.

## Design Notes

Functions take a `FilePanel` reference to access shared state (tree widget, entries list, settings, mount manager, callbacks) rather than passing individual parameters.

## Cross-Reference

- [[panel]] — `FilePanel.load()` delegates to these helpers
- [[fs]] — uses `format_size`, `format_mtime`, and `sort_entries`
- [[vfs]] — uses `FileEntry` and `VfsPath`
