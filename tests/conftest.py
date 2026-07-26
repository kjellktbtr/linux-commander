"""Shared fixtures for linux-commander tests.

Sets up a virtual framebuffer (Xvfb) so that tkinter-based integration tests
can run headless in CI or on systems without a physical display.
"""

from __future__ import annotations

import os
import subprocess

import pytest


@pytest.fixture(scope="session", autouse=True)
def xvfb_display():
    """Start an Xvfb server for the test session and set DISPLAY.

    Skips gracefully if Xvfb is not available or if a real display is already
    present (e.g. a developer running tests locally with a desktop).
    """
    # If a real display is already available, use it
    if os.environ.get("DISPLAY"):
        yield os.environ["DISPLAY"]
        return

    # Try to start Xvfb
    try:
        proc = subprocess.Popen(
            ["Xvfb", ":99", "-screen", "0", "1024x768x24"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        pytest.skip("Xvfb not found and no DISPLAY set — cannot run GUI tests")

    try:
        os.environ["DISPLAY"] = ":99"
        yield ":99"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        os.environ.pop("DISPLAY", None)
