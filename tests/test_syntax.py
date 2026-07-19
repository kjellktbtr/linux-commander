"""Tests for linux_commander.syntax engine additions.

Only pure, display-less functionality is tested here (registry, lang override,
tag cleanup).  Actual Tk tag rendering is verified by the scripted driver.
"""

from __future__ import annotations

from linux_commander.syntax import (
    available_languages,
    lang_by_name,
    lang_for_extension,
)
from linux_commander.vfs import LocalFileSystem

_FS = LocalFileSystem()


def _vfs(name: str):
    """Return a VfsPath whose .name and .suffix come from `name`."""
    from pathlib import Path

    return _FS.from_path(Path(f"/tmp/{name}"))


# ---------------------------------------------------------------------------
# available_languages / lang_by_name
# ---------------------------------------------------------------------------


def test_available_languages_returns_sorted_list() -> None:
    langs = available_languages()
    assert langs == sorted(langs)
    assert len(langs) > 0


def test_available_languages_includes_known_langs() -> None:
    langs = available_languages()
    for expected in ("Bash", "Batch", "C", "Python"):
        assert expected in langs, f"Expected '{expected}' in available_languages()"


def test_lang_by_name_finds_existing() -> None:
    lang = lang_by_name("Python")
    assert lang is not None
    assert lang.name == "Python"


def test_lang_by_name_returns_none_for_unknown() -> None:
    assert lang_by_name("NotALanguage_XYZ") is None


def test_lang_by_name_is_case_sensitive() -> None:
    # "python" != "Python"
    assert lang_by_name("python") is None


# ---------------------------------------------------------------------------
# lang_for_extension — new definitions
# ---------------------------------------------------------------------------


def test_lang_for_extension_bash_sh() -> None:
    lang = lang_for_extension(_vfs("script.sh"))
    assert lang is not None
    assert lang.name == "Bash"


def test_lang_for_extension_bash_bash() -> None:
    lang = lang_for_extension(_vfs("script.bash"))
    assert lang is not None
    assert lang.name == "Bash"


def test_lang_for_extension_batch_bat() -> None:
    lang = lang_for_extension(_vfs("run.bat"))
    assert lang is not None
    assert lang.name == "Batch"


def test_lang_for_extension_batch_cmd() -> None:
    lang = lang_for_extension(_vfs("run.cmd"))
    assert lang is not None
    assert lang.name == "Batch"


# ---------------------------------------------------------------------------
# apply_highlighting lang override (headless — tested via internal state)
# ---------------------------------------------------------------------------


def test_apply_highlighting_with_forced_lang_name(tmp_path) -> None:
    """Force Python highlighting on a .txt file — no Tk needed for the logic check."""
    # Just verify the lang resolution; Tk tag rendering tested in driver.

    from linux_commander.syntax import lang_by_name, lang_for_extension

    txt_path = _FS.from_path(tmp_path / "notes.txt")
    # Auto-detect should return None for .txt
    assert lang_for_extension(txt_path) is None
    # Forced override should resolve to Python
    lang = lang_by_name("Python")
    assert lang is not None
    assert ".py" in lang.extensions


def test_apply_highlighting_lang_none_for_unknown_ext_clears_tags(tmp_path) -> None:
    """Calling apply_highlighting with lang=None on unknown ext should clear tags.

    We verify _clear_syntax_tags works by calling it on a fake widget stub.
    """
    from linux_commander.syntax import _clear_syntax_tags

    class FakeWidget:
        def __init__(self):
            self._tags: list[str] = [
                "syntax_str",
                "syntax_cmt",
                "syntax_xblue",
                "sel",
                "search_hit",
            ]
            self._deleted: list[str] = []

        def tag_names(self):
            return list(self._tags)

        def tag_delete(self, name: str):
            self._tags.remove(name)
            self._deleted.append(name)

    w = FakeWidget()
    _clear_syntax_tags(w)
    # All syntax_ tags deleted
    assert "syntax_str" not in w._tags
    assert "syntax_cmt" not in w._tags
    assert "syntax_xblue" not in w._tags
    # Non-syntax tags untouched
    assert "sel" in w._tags
    assert "search_hit" in w._tags
