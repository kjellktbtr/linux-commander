"""Tests for linux_commander.image_viewer's pure (non-GUI) helpers.

`view_image()` itself is Toplevel-based and needs a real Tk display, so per
CLAUDE.md it's verified separately with scripted drivers rather than under
pytest. `_decode_image_bytes()` is the one new pure function -- SVG source
bytes in, PNG bytes out -- and is fully testable here.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from linux_commander import image_viewer
from linux_commander.image_viewer import _decode_image_bytes

_SIMPLE_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20">
  <rect width="40" height="20" fill="red"/>
</svg>"""


def test_decode_image_bytes_passes_through_non_svg() -> None:
    data = b"not actually a valid image, just some bytes"
    assert _decode_image_bytes(data, "photo.png") == data


def test_decode_image_bytes_passes_through_when_no_extension_match() -> None:
    # A file literally named "svg" with no dot -- must not be treated as SVG.
    data = b"raw bytes"
    assert _decode_image_bytes(data, "svg") == data


@pytest.mark.skipif(not image_viewer.HAS_CAIROSVG, reason="cairosvg (svg extra) not installed")
def test_decode_image_bytes_rasterizes_svg_to_valid_png() -> None:
    png_bytes = _decode_image_bytes(_SIMPLE_SVG, "icon.svg")
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic number
    img = Image.open(io.BytesIO(png_bytes))
    img.load()
    assert img.size == (40, 20)


@pytest.mark.skipif(not image_viewer.HAS_CAIROSVG, reason="cairosvg (svg extra) not installed")
def test_decode_image_bytes_svg_extension_is_case_insensitive() -> None:
    png_bytes = _decode_image_bytes(_SIMPLE_SVG, "ICON.SVG")
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.skipif(not image_viewer.HAS_CAIROSVG, reason="cairosvg (svg extra) not installed")
def test_decode_image_bytes_malformed_svg_raises_oserror() -> None:
    """cairosvg can raise a variety of exception types for bad input (an XML
    parse error, a Cairo rendering failure, ...); two of image_viewer.py's
    Image.open() call sites only catch (OSError, UnidentifiedImageError),
    not a bare Exception, so any cairosvg failure must be normalized to
    OSError rather than propagating as whatever cairosvg happened to raise."""
    with pytest.raises(OSError):
        _decode_image_bytes(b"<svg this is not well-formed xml at all <<<", "broken.svg")


def test_decode_image_bytes_svg_passthrough_when_cairosvg_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulates the extra not being installed: SVG bytes must pass through
    unchanged (letting Image.open()'s existing UnidentifiedImageError path
    handle it) rather than crashing on a missing cairosvg reference."""
    monkeypatch.setattr(image_viewer, "HAS_CAIROSVG", False)
    assert _decode_image_bytes(_SIMPLE_SVG, "icon.svg") == _SIMPLE_SVG
