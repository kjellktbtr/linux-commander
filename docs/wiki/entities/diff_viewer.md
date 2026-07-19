---
title: Diff Viewer — File Compare with Syntax Highlighting
type: entity
sources:
  - linux_commander/diff_viewer.py
  - linux_commander/app.py
related:
  - "[[operations]]"
  - "[[viewer]]"
  - "[[syntax]]"
  - "[[panel]]"
created: 2026-07-18
updated: 2026-07-18
confidence: high
---

# Diff Viewer — File Compare with Syntax Highlighting

`linux_commander/diff_viewer.py` implements a **file compare (diff) viewer** integrated into the Operations menu. It provides side-by-side and unified diff views with syntax highlighting, change navigation, and external tool integration.

## Features

- **Side-by-side view**: Two panels showing both files with synchronized scrolling
- **Unified view**: Single panel with `+`/`-` lines (like `diff -u`)
- **Syntax highlighting**: Diff hunks colored (green = added, red = removed) using the existing syntax engine
- **Navigation**: Prev/Next change buttons and keyboard shortcuts (Page Up/Down)
- **Line numbers**: Both views show line numbers
- **Word wrap toggle**: Checkbox in toolbar
- **External tools**: "Open in Meld", "Open in Vimdiff", "Save Patch…" buttons
- **Directory compare**: "Compare Directories" shows differing files list; double-click opens file diff

## Entry Points

| Menu Item | Shortcut | Enabled When |
|-----------|----------|--------------|
| Operations → Compare Files… | — | Exactly 2 files selected (across panels or same panel) |
| Operations → Compare Directories… | — | Always (compares the two panel directories) |

## Architecture

### `compute_diff(text_a, text_b) -> DiffResult`

Uses `difflib.SequenceMatcher` to compute line-level diff, then `difflib.ndiff` for intra-line granularity within changed chunks. Returns a `DiffResult` with:

```python
@dataclass
class DiffHunk:
    a_start: int      # 0-based line index in file A
    a_len: int        # number of lines in A
    b_start: int      # 0-based line index in file B
    b_len: int        # number of lines in B
    lines: list[tuple[str, str]]  # (type, text) — type: ' ', '-', '+', '?'

@dataclass
class DiffResult:
    hunks: list[DiffHunk]
    a_lines: list[str]
    b_lines: list[str]
```

### `DiffViewer` (tk.Toplevel)

Main window with:

1. **Toolbar**: Prev/Next change, View mode combo (Side-by-Side / Unified), Highlight mode combo (Text / Syntax Highlighted), Wrap Lines checkbox, external tool buttons, hunk counter label
2. **Content area**: Rebuilt on view mode change
   - Side-by-side: two `Text` widgets with shared `yview` (synchronized scroll)
   - Unified: single `Text` widget with tagged lines
3. **Status bar**: Current hunk info, file names

### Syntax Highlighting

Reuses `linux_commander.syntax` engine. For side-by-side, each line is tokenized and tagged with the appropriate language (detected from file extension). For unified view, diff markers (`+`, `-`, ` `) get priority colors (green/red/gray), then syntax tokens within unchanged lines.

### External Tool Integration

- **Meld**: `meld <file_a> <file_b>` (spawns detached)
- **Vimdiff**: Opens in a new terminal (`xterm -e vimdiff <file_a> <file_b>` on Linux)
- **Save Patch**: Writes unified diff to user-selected `.patch` file

## Directory Compare

`compare_directories(parent, dir_a, dir_b)`:
1. Walks both trees (non-recursive VFS listing)
2. Compares by name + size + mtime
3. Shows tree with three columns: Path (A), Path (B), Status (Same / Different / Only in A / Only in B)
4. Double-click "Different" row → opens `DiffViewer` for that pair

## Usage from Code

```python
from linux_commander.diff_viewer import DiffViewer, compare_directories
from linux_commander.vfs import VfsPath

# File compare
DiffViewer(parent_window, path_a, path_b, title="Compare")

# Directory compare
compare_directories(parent_window, dir_a_path, dir_b_path)
```

## Cross-Reference

- [[operations]] — menu integration, called from CommanderApp
- [[viewer]] — shares syntax highlighting engine
- [[syntax]] — JSON language definitions used for diff highlighting
- [[panel]] — gets selected files / current directories from panels