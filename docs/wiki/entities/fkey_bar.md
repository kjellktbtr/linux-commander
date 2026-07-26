---
title: FKeyBar — F-key Button Row Widget
type: entity
sources:
  - linux_commander/fkey_bar.py
  - linux_commander/keys.py
related:
  - "[[app]]"
  - "[[keys]]"
created: 2026-07-22
updated: 2026-07-22
confidence: high
---

# FKeyBar — F-key Button Row Widget

`linux_commander/fkey_bar.py` provides the `FKeyBar` widget — the row of F-key buttons at the bottom of the CommanderApp window. Extracted from `app.py` during SOLID refactoring (SRP).

## Class: `FKeyBar`

```python
class FKeyBar:
    def __init__(self, parent: tk.Misc, specs: list[FKeySpec | None], dispatch: Callable) -> None: ...
```

- Builds a `ttk.Frame` containing one button per F-key spec from `keys.F_KEY_SPECS`
- Empty slots (`None` in the spec list) render as invisible spacers
- Each button displays `F<n> Label` and calls the dispatch callback when pressed
- Grid layout: row 3, spanning both columns

## Cross-Reference

- [[app]] — creates the FKeyBar instance, provides dispatch callback
- [[keys]] — provides `F_KEY_SPECS` and `FKeySpec` type
