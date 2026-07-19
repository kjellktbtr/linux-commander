---
title: Syntax Highlighting
type: entity
sources:
  - linux_commander/syntax/__init__.py
  - linux_commander/syntax/*.json
  - CONTRIBUTING.md
related:
  - "[[viewer]]"
  - "[[plugins]]"
created: 2026-07-17
updated: 2026-07-17
confidence: high
---

# Syntax Highlighting Engine

`linux_commander/syntax/` provides **declarative syntax highlighting** for the viewer/editor. No code changes needed — drop a `<lang>.json` file in `syntax/` and it's auto-loaded.

## Engine (`syntax/__init__.py`)

- `discover_syntaxes()` — globs `syntax/*.json` at startup, builds name→`SyntaxLang` map
- `apply_highlighting(widget, path, lang=None)` — called by viewer on load, and when Syntax menu changes
- `available_languages() -> list[str]` — for Syntax menu radiobuttons
- `lang_by_name(name) -> SyntaxLang | None` — lookup for menu

## Language Definition (JSON Schema)

```json
{
  "name": "Python",           // Display name in Syntax menu
  "extensions": [".py", ".pyw"],  // File extensions (for Auto)
  "case_sensitive": false,    // Keyword matching case sensitivity
  "keywords": { "def": "blue", "class": "blue", ... },
  "types": { "int": "purple", "str": "purple", ... },
  "preprocessor": { ... },    // e.g., #define, #include (C)
  "builtins": { "print": "green", "len": "green", ... },
  "string_color": "#d69d85",
  "comment_color": "#6a9955",
  "number_color": "#b5cea8",
  "line_comment": "#",        // Only if language actually has line comments
  "patterns": [               // Regex patterns (highest visual priority)
    { "regex": "@\\w+", "color": "#dojdbc": "#dcdcaa" },
    { "regex": "\"\"\".*?\"\"\"", "color": "#d69d85", "multiline": true, "dotall": true }
  ]
}
```

All word maps (`keywords`, `types`, `preprocessor`, `builtins`) are flattened into one lookup → color.

## Built-in Languages

| Language | File | Extensions |
|----------|------|------------|
| Bash | `bash.json` | `.sh`, `.bash`, `.zsh` |
| Batch | `bat.json` | `.bat`, `.cmd` |
| C | `c.json` | `.c`, `.h` |
| JSON | `json.json` | `.json` |
| Markdown | `md.json` | `.md`, `.markdown` |
| Python | `py.json` | `.py`, `.pyw` |
| TOML | `toml.json` | `.toml` |
| YAML | `yaml.json` | `.yaml`, `.yml` |

## Adding a New Language

1. Create `linux_commander/syntax/<lang>.json` using `py.json` as template
2. Restart app — auto-discovered via `pkgutil` glob

## Viewer Integration

- **Syntax menu**: "Auto (by extension)" + one radiobutton per loaded language
- **Auto**: matches `path.suffix` against each language's `extensions`
- **Manual override**: picks language regardless of extension
- **Disabled** when Hexdump view is active (no highlighting on raw bytes)

## Cross-Reference

- [[viewer]] — applies highlighting via `apply_highlighting()`
- [[plugins]] — separate plugin system (drop-in modules), not JSON-based