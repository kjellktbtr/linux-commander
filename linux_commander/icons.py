"""Tiny file-type icons generated at runtime via PIL/ImageDraw.

Icons are 16×16 RGBA images, created lazily on first use (after the Tk root
exists) and cached for the lifetime of the process so Tk doesn't GC them.
"""

from __future__ import annotations

from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageTk

    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

# Module-level cache: name → ImageTk.PhotoImage  (keeps refs alive for Tk)
_cache: dict[str, object] = {}

# Master Tk instance for PhotoImage creation — set by CommanderApp at startup
# so that icons are registered with the correct root after destroy/recreate cycles.
_tk_master: object = None

_SZ = 16  # icon side length in pixels


# ---------------------------------------------------------------------------
# Icon drawing helpers
# ---------------------------------------------------------------------------


def _file_base(bg: tuple, fold: tuple, outline: str) -> Image.Image:
    """White document with a dog-eared top-right corner."""
    img = Image.new("RGBA", (_SZ, _SZ), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    right, bottom, fold_sz = 12, 14, 4
    # Body polygon (corner cut)
    d.polygon(
        [(1, 1), (right - fold_sz, 1), (right, 1 + fold_sz), (right, bottom), (1, bottom)],
        fill=bg,
    )
    # Fold triangle
    d.polygon(
        [(right - fold_sz, 1), (right - fold_sz, 1 + fold_sz), (right, 1 + fold_sz)], fill=fold
    )
    # Outline
    d.line(
        [(1, 1), (right - fold_sz, 1), (right, 1 + fold_sz), (right, bottom), (1, bottom), (1, 1)],
        fill=outline,
        width=1,
    )
    d.line(
        [(right - fold_sz, 1), (right - fold_sz, 1 + fold_sz), (right, 1 + fold_sz)],
        fill=outline,
        width=1,
    )
    return img


def _make_folder() -> Image.Image:
    img = Image.new("RGBA", (_SZ, _SZ), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 4, 6, 6], fill="#d4a800")
    d.rectangle([0, 5, 15, 13], fill="#e8c84a")
    d.rectangle([0, 5, 15, 13], outline="#a07800", width=1)
    d.rectangle([0, 4, 6, 6], outline="#a07800", width=1)
    return img


def _make_parent() -> Image.Image:
    """Folder icon with a small up-arrow overlay."""
    img = _make_folder()
    d = ImageDraw.Draw(img)
    # Up arrow (white, centred in body)
    d.polygon([(7, 6), (11, 6), (9, 3)], fill="white")
    d.line([(9, 6), (9, 11)], fill="white", width=1)
    return img


def _make_file() -> Image.Image:
    return _file_base((240, 240, 240), (180, 180, 180), "#888888")


def _make_exec() -> Image.Image:
    img = _file_base((200, 240, 200), (140, 200, 140), "#2a7a2a")
    d = ImageDraw.Draw(img)
    d.polygon([(3, 6), (3, 10), (8, 8)], fill="#2a7a2a")
    return img


def _make_text() -> Image.Image:
    img = _file_base((240, 240, 240), (180, 180, 180), "#888888")
    d = ImageDraw.Draw(img)
    for y in (5, 7, 9, 11):
        w = 7 if y == 11 else 9
        d.line([(2, y), (2 + w, y)], fill="#6666aa", width=1)
    return img


def _make_image_icon() -> Image.Image:
    img = _file_base((235, 240, 255), (170, 180, 220), "#4466aa")
    d = ImageDraw.Draw(img)
    d.ellipse([(2, 5), (5, 8)], fill="#f0c000")
    d.polygon([(2, 12), (6, 8), (10, 12)], fill="#3a7a3a")
    return img


def _make_archive() -> Image.Image:
    img = _file_base((220, 200, 170), (170, 150, 120), "#886644")
    d = ImageDraw.Draw(img)
    for y in (5, 7, 9, 11):
        d.line([(3, y), (10, y)], fill="#886644", width=1)
    return img


# ---------------------------------------------------------------------------
# Extension sets for type detection
# ---------------------------------------------------------------------------

_IMAGE_EXTS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".tiff",
    ".tif",
    ".webp",
    ".ico",
    ".svg",
}
_ARCHIVE_EXTS = {
    ".zip",
    ".tar",
    ".gz",
    ".bz2",
    ".xz",
    ".7z",
    ".rar",
    ".tgz",
    ".tbz2",
    ".txz",
    ".zst",
}
_TEXT_EXTS = {
    ".txt",
    ".md",
    ".rst",
    ".log",
    ".cfg",
    ".conf",
    ".ini",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".xml",
    ".html",
    ".htm",
    ".css",
    ".sh",
    ".bash",
}
_EXEC_EXTS = {
    ".sh",
    ".bash",
    ".py",
    ".pl",
    ".rb",
    ".exe",
    ".bat",
    ".cmd",
    ".run",
    ".appimage",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _build_cache(master: object | None = None) -> None:
    """Render all icons into PhotoImage objects.  Must be called after a Tk
    root window exists so ImageTk can create the PhotoImages."""
    if not _PIL_AVAILABLE:
        return
    _cache["folder"] = ImageTk.PhotoImage(_make_folder(), master=master)
    _cache["parent"] = ImageTk.PhotoImage(_make_parent(), master=master)
    _cache["file"] = ImageTk.PhotoImage(_make_file(), master=master)
    _cache["exec"] = ImageTk.PhotoImage(_make_exec(), master=master)
    _cache["text"] = ImageTk.PhotoImage(_make_text(), master=master)
    _cache["image"] = ImageTk.PhotoImage(_make_image_icon(), master=master)
    _cache["archive"] = ImageTk.PhotoImage(_make_archive(), master=master)


def set_tk_master(master: object) -> None:
    """Set the master Tk instance for icon PhotoImage creation.

    Call this from CommanderApp.__init__ so that icons are registered with
    the correct root window.  This matters when apps are destroyed and
    recreated (e.g. in integration tests).
    """
    global _tk_master
    _tk_master = master


def icon_for_entry(entry) -> object | None:
    """Return the ``ImageTk.PhotoImage`` for *entry*, or ``None`` when PIL is
    not installed.  Initialises the cache on the very first call."""
    if not _PIL_AVAILABLE:
        return None
    if not _cache:
        _build_cache(_tk_master)
    if entry.is_parent:
        return _cache.get("parent")
    if entry.is_dir:
        return _cache.get("folder")
    ext = Path(entry.name).suffix.lower()
    if ext in _EXEC_EXTS:
        return _cache.get("exec")
    if ext in _IMAGE_EXTS:
        return _cache.get("image")
    if ext in _ARCHIVE_EXTS:
        return _cache.get("archive")
    if ext in _TEXT_EXTS:
        return _cache.get("text")
    return _cache.get("file")
