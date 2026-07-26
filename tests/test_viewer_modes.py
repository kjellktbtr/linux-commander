"""Tests for viewer mode plugin discovery."""

from __future__ import annotations

import pytest

from linux_commander.viewer_modes import (
    ViewerMode,
    discover_modes,
    reset_mode_cache,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Reset the mode cache before each test."""
    reset_mode_cache()
    yield
    reset_mode_cache()


def test_discover_modes_returns_non_empty_list() -> None:
    modes = discover_modes()
    assert len(modes) >= 4


def test_discovered_modes_are_viewer_mode_subclasses() -> None:
    for cls in discover_modes():
        assert issubclass(cls, ViewerMode)


def test_hex_mode_is_discovered() -> None:
    modes = discover_modes()
    names = [cls.name for cls in modes]
    assert "Hex" in names


def test_json_mode_is_discovered() -> None:
    modes = discover_modes()
    names = [cls.name for cls in modes]
    assert "JSON" in names


def test_csv_mode_is_discovered() -> None:
    modes = discover_modes()
    names = [cls.name for cls in modes]
    assert "CSV" in names


def test_strings_mode_is_discovered() -> None:
    modes = discover_modes()
    names = [cls.name for cls in modes]
    assert "Strings" in names


def test_modes_have_exclusive_group() -> None:
    for cls in discover_modes():
        assert hasattr(cls, "exclusive_group")
        assert isinstance(cls.exclusive_group, str)


def test_discovered_modes_are_classes_not_instances() -> None:
    """discover_modes() should return classes, not instances."""
    for cls in discover_modes():
        assert isinstance(cls, type)


def test_each_mode_can_be_instantiated() -> None:
    """Each discovered mode class should be instantiable."""
    for cls in discover_modes():
        instance = cls()
        assert isinstance(instance, ViewerMode)
