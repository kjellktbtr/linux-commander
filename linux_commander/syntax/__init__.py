"""Syntax highlighting definitions and engine.

Highlights are applied as Text-widget tags with foreground colors.
Language definitions are loaded from JSON files in the ``syntax/`` directory.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_SYNTAX_DIR = Path(__file__).parent


class SyntaxLang:
    """Language definition loaded from a JSON file."""

    def __init__(self, name: str, data: dict) -> None:
        self.name = name
        self.extensions: list[str] = data.get("extensions", [])
        self.case_sensitive: bool = data.get("case_sensitive", True)

        # Collapse all keyword/type/preprocessor/builtins into one flat
        # word->color map for fast lookup.
        self.word_colors: dict[str, str] = {}
        for category in ("keywords", "types", "preprocessor", "builtins"):
            for word, color in data.get(category, {}).items():
                self.word_colors[word] = color

        self.string_color: str | None = data.get("string_color")
        self.comment_color: str | None = data.get("comment_color")
        self.number_color: str | None = data.get("number_color")
        # Explicit line-comment prefix (e.g. "#" or "//"); only highlight
        # comments when this is set so JSON/Markdown aren't wrongly tinted.
        self.line_comment: str | None = data.get("line_comment")
        # Extra regex patterns: [{regex, color, multiline?, dotall?}, ...]
        # Applied last so they take visual priority over keyword colors.
        self.patterns: list[dict] = data.get("patterns", [])


def _load_langs() -> list[SyntaxLang]:
    """Load all ``*.json`` files from the syntax directory."""
    langs: list[SyntaxLang] = []
    if not _SYNTAX_DIR.is_dir():
        return langs
    for path in sorted(_SYNTAX_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            name = data.get("name", path.stem)
            langs.append(SyntaxLang(name, data))
        except (OSError, json.JSONDecodeError):
            pass
    return langs


_LANGS: list[SyntaxLang] | None = None


def _get_langs() -> list[SyntaxLang]:
    global _LANGS
    if _LANGS is None:
        _LANGS = _load_langs()
    return _LANGS


def available_languages() -> list[str]:
    """Return the display names of all loaded syntax languages, sorted."""
    return sorted(lang.name for lang in _get_langs())


def lang_by_name(name: str) -> SyntaxLang | None:
    """Return the ``SyntaxLang`` whose ``name`` matches (case-sensitive), or ``None``."""
    for lang in _get_langs():
        if lang.name == name:
            return lang
    return None


def lang_for_extension(path: Path) -> SyntaxLang | None:
    """Return the ``SyntaxLang`` whose extensions includes ``path.suffix``
    (case-insensitive comparison), or ``None``."""
    suffix = path.suffix.lower()
    for lang in _get_langs():
        for ext in lang.extensions:
            if ext.lower() == suffix:
                return lang
    return None


def _clear_syntax_tags(widget) -> None:
    """Remove all tags whose names start with ``syntax_`` from ``widget``.

    This sweeps both the static named tags (``syntax_str``, ``syntax_cmt``,
    etc.) and the dynamically-named per-color word/pattern tags
    (``syntax_x...``, ``syntax_p_x...``), so that re-highlighting or
    switching languages leaves no stale colours behind.
    """
    for tag in widget.tag_names():
        if tag.startswith("syntax_"):
            widget.tag_delete(tag)


def apply_highlighting(
    widget,
    path: Path,
    lang: SyntaxLang | str | None = None,
) -> None:
    """Apply syntax highlighting to ``widget`` (a ``tk.Text``) based on
    ``path``'s extension, or a forced ``lang`` override.

    ``lang`` can be:
    - ``None`` (default) — detect language from ``path``'s extension.
    - a ``SyntaxLang`` instance — use it directly.
    - a ``str`` — look up by display name via ``lang_by_name``; if not found,
      falls through to extension-based detection.

    All previous ``syntax_*`` tags are always removed before re-applying, so
    switching languages leaves no stale colours.  The widget must already
    contain content before calling this.
    """
    # Resolve lang override
    resolved: SyntaxLang | None
    if isinstance(lang, SyntaxLang):
        resolved = lang
    elif isinstance(lang, str) and lang:
        resolved = lang_by_name(lang) or lang_for_extension(path)
    else:
        resolved = lang_for_extension(path)

    # Always clear previous syntax tags first (covers dynamic names too).
    _clear_syntax_tags(widget)

    if resolved is None:
        return

    content = widget.get("1.0", "end-1c")

    # Highlight strings
    if resolved.string_color:
        widget.tag_configure("syntax_str", foreground=resolved.string_color)
        for pattern in (r'"[^"]*"', r"'[^']*'", r'"""[\s\S]*?"""', r"'''[\s\S]*?'''"):
            for match in re.finditer(pattern, content):
                start = f"1.0 + {match.start()} chars"
                end = f"1.0 + {match.end()} chars"
                widget.tag_add("syntax_str", start, end)

    # Highlight line comments — only when an explicit prefix is declared so
    # JSON (no comments) and Markdown (# = heading) are not wrongly tinted.
    if resolved.comment_color and resolved.line_comment:
        widget.tag_configure("syntax_cmt", foreground=resolved.comment_color)
        comment_pat = re.escape(resolved.line_comment) + r".*$"
        for match in re.finditer(comment_pat, content, re.MULTILINE):
            start = f"1.0 + {match.start()} chars"
            end = f"1.0 + {match.end()} chars"
            widget.tag_add("syntax_cmt", start, end)

    # Highlight numbers
    if resolved.number_color:
        widget.tag_configure("syntax_num", foreground=resolved.number_color)
        for match in re.finditer(r"\b[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?[uUlLfF]?\b", content):
            start = f"1.0 + {match.start()} chars"
            end = f"1.0 + {match.end()} chars"
            widget.tag_add("syntax_num", start, end)

    # Highlight keywords, types, preprocessor, builtins
    if resolved.word_colors:
        color_groups: dict[str, list[str]] = {}
        for word, color in resolved.word_colors.items():
            color_groups.setdefault(color, []).append(word)

        for color, words in color_groups.items():
            tag_name = f"syntax_{color.replace('#', 'x')}"
            widget.tag_configure(tag_name, foreground=color)
            pattern = r"\b(" + "|".join(re.escape(w) for w in words) + r")\b"
            try:
                for match in re.finditer(pattern, content):
                    start = f"1.0 + {match.start()} chars"
                    end = f"1.0 + {match.end()} chars"
                    widget.tag_add(tag_name, start, end)
            except re.error:
                pass

    # Highlight custom patterns — applied last so they take visual priority
    # over all of the above (keys over string values, headings over plain text).
    for pat_def in resolved.patterns:
        try:
            regex = pat_def["regex"]
            color = pat_def["color"]
        except KeyError:
            continue
        flags = 0
        if pat_def.get("multiline"):
            flags |= re.MULTILINE
        if pat_def.get("dotall"):
            flags |= re.DOTALL
        tag_name = f"syntax_p_{color.replace('#', 'x')}"
        widget.tag_configure(tag_name, foreground=color)
        try:
            for match in re.finditer(regex, content, flags):
                start = f"1.0 + {match.start()} chars"
                end = f"1.0 + {match.end()} chars"
                widget.tag_add(tag_name, start, end)
        except re.error:
            pass
