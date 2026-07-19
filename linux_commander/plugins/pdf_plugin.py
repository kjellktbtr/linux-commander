"""Viewer reader plugin for PDF documents (the ``documents`` extra).

Provides a rasterized page preview of ``.pdf`` files using PyMuPDF (fitz).
Registers no extensions when the ``pymupdf`` package isn't installed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from linux_commander.vfs import FileSystem, VfsPath

if TYPE_CHECKING:
    from linux_commander.plugins import ViewDocument

try:
    import fitz  # type: ignore[import-untyped]  # PyMuPDF

    VIEW_EXTENSIONS: tuple[str, ...] = (".pdf",)
except ImportError:
    fitz = None  # type: ignore[assignment]
    VIEW_EXTENSIONS = ()


def read_document(host_fs: FileSystem, path: VfsPath) -> ViewDocument:
    """Render the first page of a PDF as an image for table view.

    Since PDF is a page-based format, we render the first page as a rasterized
    image and return it as a single-cell table with metadata for navigation.
    """
    from linux_commander.plugins import ViewDocument, cleanup_temp, materialize

    real_path = materialize(host_fs, path)
    try:
        doc = fitz.open(str(real_path))
        try:
            page_count = doc.page_count
            if page_count == 0:
                return ViewDocument(
                    kind="text",
                    text="[Empty PDF document]",
                )

            # Render first page at 150 DPI for good quality preview
            page = doc[0]
            mat = fitz.Matrix(150 / 72, 150 / 72)  # 150 DPI
            pix = page.get_pixmap(matrix=mat, alpha=False)

            # Convert to bytes for the viewer
            img_bytes = pix.tobytes("png")

            # Return as a special "pdf" kind with image data and page info
            meta = {
                "page_count": page_count,
                "current_page": 0,
                "image_data": img_bytes,
                "page_size": (page.rect.width, page.rect.height),
            }
            return ViewDocument(kind="pdf", text="", meta=meta)
        finally:
            doc.close()
    finally:
        cleanup_temp(real_path)
