"""Viewer reader plugin for extra tabular formats via pandas (the ``documents``
extra): ``.xls`` (legacy Excel), ``.ods`` (OpenDocument spreadsheet), and
``.parquet``.

``.xlsx``/``.xlsm`` are handled by ``xlsx_plugin`` instead (openpyxl gives a
cheaper streaming preview for that format); this module deliberately does not
claim those extensions.

pandas (and numpy, its dependency) is a heavy import, so unlike the other
optional plugins this module only *probes* for the package at discovery time
(``importlib.util.find_spec``, which does not execute it) and imports it
lazily inside ``read_document``. Registers no extensions when pandas isn't
installed, per the guard convention documented in ``plugins/__init__.py``.

Reading ``.xls``/``.ods`` additionally requires an engine (``xlrd`` / ``odfpy``
respectively) and ``.parquet`` requires ``pyarrow`` or ``fastparquet`` -- if
the format's engine is missing, ``read_document`` lets the resulting
``ImportError`` propagate; the viewer surfaces it as a friendly error.
"""

from __future__ import annotations

import importlib.util
from typing import TYPE_CHECKING

from linux_commander.vfs import FileSystem, VfsPath

if TYPE_CHECKING:
    from linux_commander.plugins import ViewDocument

VIEW_EXTENSIONS: tuple[str, ...] = (
    (".xls", ".ods", ".parquet") if importlib.util.find_spec("pandas") is not None else ()
)


def read_document(host_fs: FileSystem, path: VfsPath) -> ViewDocument:
    """Read ``path`` as a table preview via pandas, capped to the row limit.

    ``MAX_PREVIEW_ROWS`` caps the *total* number of rows in ``doc.rows``,
    header included -- matching ``xlsx_plugin``'s cap (there, the header is
    just the sheet's first row and is naturally included when the row-count
    loop stops at the limit). Here the header is separate from the data
    pandas reads, so the data-row cap is one less than the header-inclusive
    total to match.
    """
    import pandas as pd  # type: ignore[import-untyped]

    from linux_commander.plugins import MAX_PREVIEW_ROWS, ViewDocument, cleanup_temp, materialize

    data_cap = max(MAX_PREVIEW_ROWS - 1, 0)
    real_path = materialize(host_fs, path)
    try:
        suffix = path.suffix.lower()
        if suffix == ".parquet":
            df = pd.read_parquet(real_path)
            truncated = len(df) > data_cap
            df = df.head(data_cap)
        else:
            # engine="odf" for .ods, default (xlrd) for .xls
            engine = "odf" if suffix == ".ods" else None
            df = pd.read_excel(real_path, sheet_name=0, nrows=data_cap + 1, engine=engine)
            truncated = len(df) > data_cap
            df = df.head(data_cap)
        rows = [[str(c) for c in df.columns]] + df.astype(str).values.tolist()
        return ViewDocument(kind="table", rows=rows, truncated=truncated, meta={})
    finally:
        cleanup_temp(real_path)
