---
title: CommandPrompt — Command Entry Bar
type: entity
sources:
  - linux_commander/command_prompt.py
related:
  - "[[app]]"
created: 2026-07-22
updated: 2026-07-22
confidence: high
---

# CommandPrompt — Command Entry Bar

`linux_commander/command_prompt.py` provides the `CommandPrompt` widget — the command entry bar at the bottom of the CommanderApp window. Extracted from `app.py` during SOLID refactoring (SRP).

## Class: `CommandPrompt`

```python
class CommandPrompt:
    def __init__(self, parent: tk.Misc, *, on_execute: ExecuteCallback, on_focus_return: FocusCallback) -> None: ...
```

- Builds a `ttk.Frame` containing a prompt label and an entry field
- Manages command history with Up/Down arrow navigation
- Enter executes the command via `on_execute` callback
- Escape clears the entry and returns focus to the panel via `on_focus_return` callback
- Grid layout: row 2, spanning both columns

## Cross-Reference

- [[app]] — creates the CommandPrompt instance, provides execute and focus callbacks
