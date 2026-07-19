"""Standalone image viewer (opened via F3 on image files).

Extracted from viewer.py — kept separate because it has no dependency on
TextWindow and uses Pillow (PIL) rather than the Tk Text widget.

Public API:
    view_image(parent, path, image_files, start_index, image_extensions, settings=None)
        -> tk.Toplevel | None
"""

from __future__ import annotations

import io
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from PIL import ExifTags, Image, ImageTk

from linux_commander import dialogs
from linux_commander.settings import Settings
from linux_commander.vfs import VfsPath

try:
    import cairosvg

    HAS_CAIROSVG = True
except ImportError:
    cairosvg = None  # type: ignore[assignment]
    HAS_CAIROSVG = False
except OSError:
    # cairosvg imports fine but raises OSError at import time if the
    # system Cairo library itself isn't installed (a non-Python native
    # dependency) -- degrade the same way as a missing Python package.
    cairosvg = None  # type: ignore[assignment]
    HAS_CAIROSVG = False

# Default slideshow interval in milliseconds
DEFAULT_SLIDESHOW_INTERVAL_MS = 3000


def _decode_image_bytes(data: bytes, name: str) -> bytes:
    """Rasterize SVG source to PNG bytes so it flows through the same
    PIL-based pipeline as every other format here -- zoom, rotate, flip,
    thumbnails, and the EXIF panel (which simply finds no EXIF on a
    rasterized SVG and shows "No EXIF data available") all keep working
    unchanged, since from this point on it's just a (raster) PIL Image
    like any other. Non-SVG bytes pass through untouched.

    If ``cairosvg`` (or its system Cairo library dependency) isn't
    installed, SVG bytes are returned as-is; ``Image.open()`` then raises
    ``UnidentifiedImageError``, which every call site below already
    handles the same way it would for any other unsupported/corrupt image.

    A malformed SVG can make ``cairosvg`` itself raise (an XML parse error,
    a Cairo rendering failure, ...) with exception types that vary by
    failure and aren't necessarily ``OSError`` -- but two of the call sites
    below only catch ``(OSError, Image.UnidentifiedImageError)``, not a bare
    ``Exception``, so any such failure is normalized to ``OSError`` here to
    degrade the same way a corrupt raster image already does, rather than
    crashing with an unexpected exception type.
    """
    if HAS_CAIROSVG and name.lower().endswith(".svg"):
        try:
            return cairosvg.svg2png(bytestring=data)  # type: ignore[no-any-return]
        except Exception as exc:
            raise OSError(f"Could not rasterize SVG: {exc}") from exc
    return data


def _format_exif_value(tag: str, value: object) -> str:
    """Format an EXIF value for display."""
    if value is None:
        return ""
    # Handle bytes by decoding if possible
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="replace")
        except Exception:
            return str(value)
    # Handle tuples/lists
    if isinstance(value, (tuple, list)):
        return ", ".join(str(v) for v in value)
    return str(value)


def _extract_exif_data(img: Image.Image) -> dict[str, str]:
    """Extract EXIF data from a PIL Image and return as a dict of tag->value."""
    exif_data: dict[str, str] = {}
    try:
        raw_exif = img.getexif()
        if raw_exif:
            for tag_id, value in raw_exif.items():
                tag_name = ExifTags.TAGS.get(tag_id, f"Tag_{tag_id}")
                exif_data[tag_name] = _format_exif_value(tag_name, value)
    except Exception:
        pass
    return exif_data


def view_image(
    parent: tk.Misc,
    path: VfsPath,
    image_files: list[VfsPath],
    start_index: int,
    image_extensions: list[str],
    settings: Settings | None = None,
) -> tk.Toplevel | None:
    """Open an image viewer with navigation (Left/Right, Shift+Left/Right).

    Args:
        parent: Parent widget.
        path: The VfsPath of the image to view.
        image_files: List of all image VfsPaths in the same directory (sorted).
        start_index: Index in image_files where to start viewing.
        image_extensions: List of image extensions (e.g., [".png", ".jpg"])
            for Shift+Arrow navigation.
        settings: Optional Settings object for font configuration.

    Returns:
        The Toplevel window, or None if the image could not be loaded.
    """
    from linux_commander.settings import Settings as _Settings

    if settings is None:
        settings = _Settings()

    _LARGE_IMAGE_BYTES = 20 * 1024 * 1024  # 20 MB
    try:
        st = path.fs.stat(path)
        if st.size > _LARGE_IMAGE_BYTES and not dialogs.confirm(
            parent,
            f"'{path.name}' is {st.size // (1024 * 1024)} MB.\n"
            "Loading a large image may take a moment. Continue?",
            title="Large image",
        ):
            return None
    except OSError:
        pass  # can't stat (e.g. FTP without size info); proceed

    try:
        with path.fs.open_read(path) as f:
            img_data = f.read()
        _initial_img = Image.open(io.BytesIO(_decode_image_bytes(img_data, path.name)))
        _initial_img.load()  # force full decode so img_data can be freed
    except (OSError, Image.UnidentifiedImageError) as exc:
        dialogs.error(
            parent, f"Could not open image '{path.name}':\n{exc}", title="View image failed"
        )
        return None

    top = tk.Toplevel(parent)
    top.title(f"Image View - {path.name}")
    top.geometry("900x700")
    top.minsize(400, 300)

    # State variables
    show_exif = tk.BooleanVar(value=False)
    zoom_mode = tk.StringVar(value="fit")  # "fit", "100", "200"
    slideshow_running = tk.BooleanVar(value=False)
    slideshow_interval_ms = tk.IntVar(value=DEFAULT_SLIDESHOW_INTERVAL_MS)
    slideshow_after_id: str | None = None

    # Menu bar
    menubar = tk.Menu(top)
    top.config(menu=menubar)

    file_menu = tk.Menu(menubar, tearoff=0)
    menubar.add_cascade(label="File", menu=file_menu, underline=0)
    file_menu.add_command(label="Exit", command=top.destroy, underline=1)

    view_menu = tk.Menu(menubar, tearoff=0)
    menubar.add_cascade(label="View", menu=view_menu, underline=0)

    # Zoom submenu
    zoom_menu = tk.Menu(view_menu, tearoff=0)
    view_menu.add_cascade(label="Zoom", menu=zoom_menu, underline=0)
    zoom_menu.add_radiobutton(label="Fit to Window", variable=zoom_mode, value="fit", underline=0)
    zoom_menu.add_radiobutton(label="100%", variable=zoom_mode, value="100", underline=0)
    zoom_menu.add_radiobutton(label="200%", variable=zoom_mode, value="200", underline=0)

    # Transform submenu
    transform_menu = tk.Menu(view_menu, tearoff=0)
    view_menu.add_cascade(label="Transform", menu=transform_menu, underline=0)

    view_menu.add_separator()
    view_menu.add_checkbutton(label="EXIF Metadata", variable=show_exif, underline=0)

    # Slideshow menu
    slideshow_menu = tk.Menu(menubar, tearoff=0)
    menubar.add_cascade(label="Slideshow", menu=slideshow_menu, underline=0)
    slideshow_menu.add_command(label="Start", command=lambda: _start_slideshow())
    slideshow_menu.add_command(label="Stop", command=lambda: _stop_slideshow())
    slideshow_menu.add_separator()
    slideshow_menu.add_command(
        label="Interval...",
        command=lambda: _slideshow_interval_dialog(),
    )

    # Canvas for image
    canvas = tk.Canvas(top, bg="gray20", highlightthickness=0)
    hscroll = ttk.Scrollbar(top, orient="horizontal", command=canvas.xview)
    vscroll = ttk.Scrollbar(top, orient="vertical", command=canvas.yview)
    canvas.configure(xscrollcommand=hscroll.set, yscrollcommand=vscroll.set)

    canvas.grid(row=0, column=0, sticky="nsew")
    hscroll.grid(row=1, column=0, sticky="ew")
    vscroll.grid(row=0, column=1, sticky="ns")

    # EXIF panel (initially hidden)
    exif_frame = ttk.LabelFrame(top, text="EXIF Metadata")
    exif_text = tk.Text(
        exif_frame, width=40, height=10, wrap="none", state="disabled", font=("TkFixedFont", 9)
    )
    exif_vscroll = ttk.Scrollbar(exif_frame, orient="vertical", command=exif_text.yview)
    exif_text.configure(yscrollcommand=exif_vscroll.set)
    exif_text.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
    exif_vscroll.grid(row=0, column=1, sticky="ns", pady=4)
    exif_frame.columnconfigure(0, weight=1)
    exif_frame.rowconfigure(0, weight=1)

    # Thumbnail strip frame (initially hidden)
    thumb_frame = ttk.Frame(top)
    thumb_canvas = tk.Canvas(thumb_frame, height=80, bg="gray30", highlightthickness=0)
    thumb_hscroll = ttk.Scrollbar(thumb_frame, orient="horizontal", command=thumb_canvas.xview)
    thumb_canvas.configure(xscrollcommand=thumb_hscroll.set)
    thumb_inner = ttk.Frame(thumb_canvas)
    thumb_canvas.create_window((0, 0), window=thumb_inner, anchor="nw")
    thumb_canvas.grid(row=0, column=0, sticky="ew")
    thumb_hscroll.grid(row=1, column=0, sticky="ew")
    thumb_frame.columnconfigure(0, weight=1)

    # Grid layout
    top.columnconfigure(0, weight=1)
    top.rowconfigure(0, weight=1)

    # Store references on the toplevel for access in nested functions
    top._image_files = image_files  # type: ignore[attr-defined]
    top._current_index = start_index  # type: ignore[attr-defined]
    top._image_extensions = [ext.lower() for ext in image_extensions]  # type: ignore[attr-defined]
    top._current_img = None  # type: ignore[attr-defined]  # live PhotoImage reference
    top._orig_img = _initial_img  # type: ignore[attr-defined]  # cached decoded PIL image
    top._orig_img_idx = start_index  # type: ignore[attr-defined]  # index of cached image
    top._resize_after = None  # type: ignore[attr-defined]  # pending debounce id
    top._exif_data = {}  # type: ignore[attr-defined]  # cached EXIF data
    top._thumb_images = []  # type: ignore[attr-defined]  # thumbnail PhotoImage refs

    def _load_exif_for_current() -> None:
        """Load EXIF data for the current image."""
        idx = top._current_index  # type: ignore[attr-defined]
        if idx < 0 or idx >= len(top._image_files):  # type: ignore[attr-defined]
            return
        current_path = top._image_files[idx]  # type: ignore[attr-defined]
        try:
            with current_path.fs.open_read(current_path) as f:
                data = f.read()
            img = Image.open(io.BytesIO(_decode_image_bytes(data, current_path.name)))
            img.load()
            top._exif_data = _extract_exif_data(img)  # type: ignore[attr-defined]
        except Exception:
            top._exif_data = {}  # type: ignore[attr-defined]

    def _update_exif_display() -> None:
        """Update the EXIF text widget with current image's EXIF data."""
        exif_text.configure(state="normal")
        exif_text.delete("1.0", "end")
        if top._exif_data:  # type: ignore[attr-defined]
            for tag, value in top._exif_data.items():  # type: ignore[attr-defined]
                exif_text.insert("end", f"{tag}: {value}\n")
        else:
            exif_text.insert("end", "No EXIF data available")
        exif_text.configure(state="disabled")

    def _toggle_exif_panel() -> None:
        """Show/hide the EXIF panel."""
        if show_exif.get():
            _load_exif_for_current()
            _update_exif_display()
            exif_frame.grid(row=0, column=2, sticky="ns", padx=(4, 0), pady=4)
        else:
            exif_frame.grid_remove()

    def _build_thumbnails() -> None:
        """Build thumbnail strip for all images."""
        # Clear existing thumbnails
        for widget in thumb_inner.winfo_children():
            widget.destroy()
        top._thumb_images = []  # type: ignore[attr-defined]

        max_thumb_size = (100, 75)
        for i, img_path in enumerate(top._image_files):  # type: ignore[attr-defined]
            try:
                with img_path.fs.open_read(img_path) as f:
                    data = f.read()
                img = Image.open(io.BytesIO(_decode_image_bytes(data, img_path.name)))
                img.load()
                img.thumbnail(max_thumb_size, Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                top._thumb_images.append(photo)  # type: ignore[attr-defined]

                btn = ttk.Button(
                    thumb_inner,
                    image=photo,
                    command=lambda idx=i: _go_to_index(idx),  # type: ignore[misc]
                )
                btn.grid(row=0, column=i, padx=2, pady=2)
                # Highlight current image
                if i == top._current_index:  # type: ignore[attr-defined]
                    btn.state(["pressed"])
            except Exception:
                # Skip images that can't be loaded
                pass

        thumb_inner.update_idletasks()
        thumb_canvas.configure(scrollregion=thumb_canvas.bbox("all"))

    def _update_thumbnail_selection() -> None:
        """Update the highlighted thumbnail."""
        for i, widget in enumerate(thumb_inner.winfo_children()):
            if isinstance(widget, ttk.Button):
                if i == top._current_index:  # type: ignore[attr-defined]
                    widget.state(["pressed"])
                else:
                    widget.state(["!pressed"])

    def _go_to_index(idx: int) -> None:
        """Navigate to a specific image index."""
        if 0 <= idx < len(top._image_files):  # type: ignore[attr-defined]
            top._current_index = idx  # type: ignore[attr-defined]
            _update_image()
            _update_thumbnail_selection()

    def _update_image() -> None:
        # Guard against calls that arrive after the window has been destroyed
        # or before Tk has performed its first layout pass (winfo_width returns 1).
        if not top.winfo_exists() or top.winfo_width() <= 1:
            return
        top._resize_after = None  # type: ignore[attr-defined]
        idx = top._current_index  # type: ignore[attr-defined]
        if idx < 0 or idx >= len(top._image_files):  # type: ignore[attr-defined]
            return
        current_path = top._image_files[idx]  # type: ignore[attr-defined]

        # Re-read the file only when navigating to a new image; on pure resize
        # events reuse the already-decoded original to avoid full re-download.
        if idx != top._orig_img_idx or top._orig_img is None:  # type: ignore[attr-defined]
            try:
                with current_path.fs.open_read(current_path) as f:
                    img_data = f.read()
                orig = Image.open(io.BytesIO(_decode_image_bytes(img_data, current_path.name)))
                orig.load()  # force full decode so img_data can be freed
            except (OSError, Image.UnidentifiedImageError):
                _navigate(1)
                return
            top._orig_img = orig  # type: ignore[attr-defined]
            top._orig_img_idx = idx  # type: ignore[attr-defined]
        else:
            orig = top._orig_img  # type: ignore[attr-defined]

        win_w = max(top.winfo_width(), 1)
        win_h = max(top.winfo_height(), 1)
        img_w, img_h = orig.size

        # Calculate scale based on zoom mode
        if zoom_mode.get() == "100":
            scale = 1.0
        elif zoom_mode.get() == "200":
            scale = 2.0
        else:  # "fit"
            scale = min(win_w / img_w, win_h / img_h, 1.0)

        if scale < 1.0 or zoom_mode.get() in ("100", "200"):
            new_size = (max(int(img_w * scale), 1), max(int(img_h * scale), 1))
            img: Image.Image = orig.resize(new_size, Image.Resampling.LANCZOS)  # type: ignore[attr-defined]
        else:
            img = orig

        photo = ImageTk.PhotoImage(img)
        top._current_img = photo  # type: ignore[attr-defined]  # single live reference prevents GC
        canvas.delete("all")
        canvas.create_image(win_w // 2, win_h // 2, image=photo, anchor="center")
        canvas.configure(scrollregion=canvas.bbox("all"))
        top.title(
            f"Image View - {current_path.name} ({idx + 1}/{len(top._image_files)})"  # type: ignore[attr-defined]
        )

        # Update EXIF if panel is visible
        if show_exif.get():
            _load_exif_for_current()
            _update_exif_display()

        # Update thumbnail selection
        _update_thumbnail_selection()

    def _navigate(delta: int) -> None:
        new_idx = top._current_index + delta  # type: ignore[attr-defined]
        if 0 <= new_idx < len(top._image_files):  # type: ignore[attr-defined]
            top._current_index = new_idx  # type: ignore[attr-defined]
            _update_image()

    def _navigate_same_ext(delta: int) -> None:
        current_path = top._image_files[top._current_index]  # type: ignore[attr-defined]
        current_ext = Path(current_path.name).suffix.lower()
        if current_ext not in top._image_extensions:  # type: ignore[attr-defined]
            _navigate(delta)
            return
        idx = top._current_index  # type: ignore[attr-defined]
        while True:
            idx += delta
            if idx < 0 or idx >= len(top._image_files):  # type: ignore[attr-defined]
                break
            next_path = top._image_files[idx]  # type: ignore[attr-defined]
            if Path(next_path.name).suffix.lower() == current_ext:
                top._current_index = idx  # type: ignore[attr-defined]
                _update_image()
                break

    def _on_key(event: tk.Event) -> str:
        keysym = event.keysym
        state = int(event.state)  # type: ignore[arg-type]
        if keysym == "Right":
            if state & 0x0001:  # Shift
                _navigate_same_ext(1)
            else:
                _navigate(1)
            return "break"
        if keysym == "Left":
            if state & 0x0001:  # Shift
                _navigate_same_ext(-1)
            else:
                _navigate(-1)
            return "break"
        if keysym == "space":
            if slideshow_running.get():
                _stop_slideshow()
            else:
                _start_slideshow()
            return "break"
        if keysym in ("Escape", "F3", "F10"):
            top.destroy()
            return "break"
        return ""

    def _on_resize(event: tk.Event) -> None:
        if event.widget is top:
            if top._resize_after is not None:  # type: ignore[attr-defined]
                top.after_cancel(top._resize_after)  # type: ignore[attr-defined]
            top._resize_after = top.after(100, _update_image)  # type: ignore[attr-defined]

    # Slideshow functions
    def _slideshow_step() -> None:
        if not slideshow_running.get():
            return
        _navigate(1)
        # Loop back to start
        if top._current_index >= len(top._image_files) - 1:  # type: ignore[attr-defined]
            top._current_index = 0  # type: ignore[attr-defined]
        nonlocal slideshow_after_id
        slideshow_after_id = top.after(slideshow_interval_ms.get(), _slideshow_step)

    def _start_slideshow() -> None:
        if not slideshow_running.get():
            slideshow_running.set(True)
            _slideshow_step()

    def _stop_slideshow() -> None:
        nonlocal slideshow_after_id
        if slideshow_running.get():
            slideshow_running.set(False)
            if slideshow_after_id is not None:
                top.after_cancel(slideshow_after_id)
                slideshow_after_id = None

    def _slideshow_interval_dialog() -> None:
        dialog = tk.Toplevel(top)
        dialog.title("Slideshow Interval")
        dialog.transient(top)
        dialog.resizable(False, False)

        ttk.Label(dialog, text="Interval (seconds):").grid(
            row=0, column=0, padx=8, pady=8, sticky="w"
        )
        interval_var = tk.DoubleVar(value=slideshow_interval_ms.get() / 1000.0)
        spinbox = ttk.Spinbox(
            dialog, from_=0.5, to=60.0, increment=0.5, textvariable=interval_var, width=10
        )
        spinbox.grid(row=0, column=1, padx=8, pady=8)

        def _ok() -> None:
            slideshow_interval_ms.set(int(interval_var.get() * 1000))
            dialog.destroy()

        btn_frame = ttk.Frame(dialog)
        btn_frame.grid(row=1, column=0, columnspan=2, pady=8)
        ttk.Button(btn_frame, text="OK", command=_ok).pack(side="right", padx=4)
        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side="right", padx=4)

        dialog.grab_set()
        spinbox.focus_set()

    # Transform functions
    def _rotate_90() -> None:
        if top._orig_img is not None:  # type: ignore[attr-defined]
            top._orig_img = top._orig_img.rotate(-90, expand=True)  # type: ignore[attr-defined]
            _update_image()

    def _rotate_270() -> None:
        if top._orig_img is not None:  # type: ignore[attr-defined]
            top._orig_img = top._orig_img.rotate(90, expand=True)  # type: ignore[attr-defined]
            _update_image()

    def _flip_horizontal() -> None:
        if top._orig_img is not None:  # type: ignore[attr-defined]
            top._orig_img = top._orig_img.transpose(Image.FLIP_LEFT_RIGHT)  # type: ignore[attr-defined]
            _update_image()

    def _flip_vertical() -> None:
        if top._orig_img is not None:  # type: ignore[attr-defined]
            top._orig_img = top._orig_img.transpose(Image.FLIP_TOP_BOTTOM)  # type: ignore[attr-defined]
            _update_image()

    # Add transform menu items
    transform_menu.add_command(label="Rotate 90° CW", command=_rotate_90, underline=7)
    transform_menu.add_command(label="Rotate 90° CCW", command=_rotate_270, underline=7)
    transform_menu.add_separator()
    transform_menu.add_command(label="Flip Horizontal", command=_flip_horizontal, underline=0)
    transform_menu.add_command(label="Flip Vertical", command=_flip_vertical, underline=0)

    # Bindings
    def _on_close() -> None:
        _stop_slideshow()
        top.destroy()

    top.bind("<Key>", _on_key)
    top.bind("<Configure>", _on_resize)
    top.protocol("WM_DELETE_WINDOW", _on_close)

    # Build thumbnails and initial image
    _build_thumbnails()
    canvas.focus_set()
    _update_image()

    # Watch for EXIF toggle
    def _on_exif_toggle(*args):
        _toggle_exif_panel()

    show_exif.trace_add("write", _on_exif_toggle)
    zoom_mode.trace_add("write", lambda *args: _update_image())

    return top
