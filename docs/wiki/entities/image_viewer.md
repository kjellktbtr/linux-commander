---
title: Image Viewer
type: entity
sources:
  - linux_commander/image_viewer.py
  - CONTRIBUTING.md
related:
  - "[[viewer]]"
  - "[[app]]"
  - "[[panel]]"
created: 2026-07-17
updated: 2026-07-18
confidence: high
---

# Image Viewer — F3 on Image Files

`linux_commander/image_viewer.py` is a standalone `Toplevel` window opened by F3/Enter on image files (extensions in `Settings.image_extensions`).

## Features

- **Auto-fit to window**: scales image to fit window (never upscales past 100%)
- **Re-fits on resize**: window resize → image re-fits
- **Directory navigation**: Left/Right → prev/next image in **same directory**
- **Extension-filtered nav**: Shift+Left/Right → prev/next matching **current extension only**
- **Zoom modes**: View → Zoom → Fit to Window / 100% / 200%
- **Transform**: View → Transform → Rotate 90° CW/CCW, Flip Horizontal/Vertical
- **EXIF metadata panel**: View → EXIF Metadata (toggle) — shows camera, exposure, GPS, etc.
- **Slideshow**: Slideshow menu → Start/Stop, Space to toggle, configurable interval
- **Thumbnail strip**: Bottom bar with clickable thumbnails for quick navigation

## Supported Formats

Anything `PIL.Image.open()` supports (PNG, JPEG, GIF, BMP, TIFF, WebP, etc.). Falls back to built-in viewer if PIL missing (Pillow is a **hard** dependency, not optional, so in practice this only matters for genuinely corrupt/unsupported files).

**SVG (2026-07-18)**: `.svg` is now in `Settings.image_extensions`'s default list. Pillow itself has no SVG rasterizer, so `.svg` requires the optional `svg` extra (`cairosvg`, which itself needs the system Cairo library — a non-Python native dependency, `libcairo2` on Debian/Ubuntu, `cairo` on Arch). `_decode_image_bytes(data, name)` rasterizes SVG source to PNG bytes *once*, at every point raw file bytes are about to be handed to `Image.open()` (the initial load, EXIF extraction, thumbnail generation, and per-navigation reload) -- from that point on it's just a (raster) `PIL.Image` like any other format, so every existing feature (zoom, rotate, flip, thumbnails) works completely unchanged, with zero format-specific UI code. No re-rasterization on zoom -- renders once at the SVG's intrinsic size, then the normal PIL `.resize()` zoom path applies (same raster-upscale softness at high zoom as any other image; not chasing crisper vector zoom for v1).

Degrades gracefully without the extra: SVG bytes pass through `_decode_image_bytes()` unchanged, and `Image.open()` raises `UnidentifiedImageError` exactly as it would for any other unsupported format -- surfaced by the existing "Could not open image" error dialog, no special-casing needed. A malformed SVG (bad XML, a Cairo rendering failure) is caught inside `_decode_image_bytes()` and re-raised as `OSError`, since two of the four call sites only catch `(OSError, Image.UnidentifiedImageError)`, not a bare `Exception` -- letting a raw `cairosvg` exception type through would have crashed those call sites instead of degrading like any other bad file.

## Key Bindings

| Keys | Action |
|------|--------|
| Left / Right | Previous / next image (all extensions in dir) |
| Shift+Left / Shift+Right | Previous / next image (same extension only) |
| Space | Toggle slideshow |
| Escape / F3 / F10 | Close window |
| Ctrl+G | Go to Offset (in hex mode) |

## Menus

### File
- Exit — Close viewer

### View
- Zoom → Fit to Window / 100% / 200%
- Transform → Rotate 90° CW / 90° CCW / Flip Horizontal / Flip Vertical
- EXIF Metadata (toggle)

### Slideshow
- Start — Begin auto-advance
- Stop — Pause slideshow
- Interval... — Set delay between images (default 3s)

## Integration

- `app.py:_on_activate_file()` detects image extensions → calls `image_viewer.view_image()`
- `view_image(parent, fs, path, all_images, start_index, ext_filter=None)`
  - `all_images`: pre-filtered list of image `VfsPath`s in the directory
  - `start_index`: which one to show first
  - `ext_filter`: if set, navigation filters to that extension (Shift+arrows)

## Testing

- `tests/test_image_viewer.py` — `_decode_image_bytes()` (the one pure, non-GUI function here): non-SVG passthrough, real SVG→PNG rasterization (verified against the PNG magic number and decoded dimensions via `PIL.Image.open()`), case-insensitive `.svg`/`.SVG` matching, malformed-SVG→`OSError` normalization, and the no-`cairosvg`-installed passthrough path (via `monkeypatch.setattr(image_viewer, "HAS_CAIROSVG", False)`, so it runs regardless of whether the `svg` extra is actually installed). Tests requiring real rasterization are `@pytest.mark.skipif(not image_viewer.HAS_CAIROSVG, ...)`.
- `view_image()` itself is `Toplevel`-based and untested under pytest per CLAUDE.md's GUI-testing policy; verified instead via an ad-hoc Xvfb script driving the real function end-to-end (opens a real SVG file, confirms the canvas has a drawn image item and the window title reflects success) plus a second script confirming the resulting `PIL.Image` supports rotate/thumbnail/EXIF-extraction identically to any other loaded image.

## Cross-Reference

- [[viewer]] — F3/Enter on non-image opens this viewer
- [[app]] — CommanderApp routes activation
- [[panel]] — provides directory listing for `all_images`