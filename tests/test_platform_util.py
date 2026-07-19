"""Tests for linux_commander.platform_util.open_with_default_app.

subprocess.Popen is monkeypatched so these tests never actually launch a
real application on the machine running them.
"""

from pathlib import Path
from typing import Any

import pytest

from linux_commander import platform_util


def test_open_with_default_app_uses_xdg_open_on_linux(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    def fake_popen(args: list[str], **kwargs: Any) -> None:
        calls.append(args)

    monkeypatch.setattr(platform_util.sys, "platform", "linux")
    monkeypatch.setattr(platform_util.subprocess, "Popen", fake_popen)

    target = tmp_path / "file.txt"
    result = platform_util.open_with_default_app(target)

    assert result is True
    assert calls == [["xdg-open", str(target)]]


def test_open_with_default_app_returns_false_when_opener_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def raise_not_found(args: list[str], **kwargs: Any) -> None:
        raise FileNotFoundError("no xdg-open")

    monkeypatch.setattr(platform_util.sys, "platform", "linux")
    monkeypatch.setattr(platform_util.subprocess, "Popen", raise_not_found)

    result = platform_util.open_with_default_app(tmp_path / "file.txt")

    assert result is False


def test_open_with_default_app_returns_false_on_unknown_platform(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(platform_util.sys, "platform", "some-other-os")

    result = platform_util.open_with_default_app(tmp_path / "file.txt")

    assert result is False
