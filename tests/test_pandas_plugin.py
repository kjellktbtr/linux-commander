"""Tests for the pandas viewer reader plugin (the ``documents`` extra):
``.ods`` and ``.parquet`` preview.

``.xls`` (legacy Excel, requiring the ``xlrd`` engine) isn't covered here
because pandas dropped *writing* xls support -- there's no easy way to build
an xls fixture without an external tool -- but the read path is identical to
``.ods``.

Skipped entirely when ``pandas`` isn't installed; the per-format tests are
separately skipped when their read/write engine (``odf`` / ``pyarrow``)
isn't installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pandas")

import pandas as pd  # noqa: E402

from linux_commander.plugins import viewer_plugin_for_name  # noqa: E402
from linux_commander.plugins.pandas_plugin import read_document  # noqa: E402
from linux_commander.vfs import LocalFileSystem, VfsPath  # noqa: E402

_FS = LocalFileSystem()


def _local_vpath(path: Path) -> VfsPath:
    return _FS.from_path(path)


def test_ods_and_parquet_extensions_are_discovered() -> None:
    for name in ("book.ods", "data.parquet"):
        mod = viewer_plugin_for_name(name)
        assert mod is not None
        assert mod.read_document is read_document


def test_xlsx_is_not_claimed_by_pandas_plugin() -> None:
    # xlsx_plugin (openpyxl) owns .xlsx/.xlsm -- pandas_plugin must not
    # shadow it in the discovery map.
    from linux_commander.plugins import xlsx_plugin

    mod = viewer_plugin_for_name("book.xlsx")
    assert mod is xlsx_plugin or mod is None  # None only if openpyxl absent


def test_read_document_ods(tmp_path: Path) -> None:
    pytest.importorskip("odf")
    df = pd.DataFrame({"name": ["Alice", "Bob"], "role": ["Dev", "QA"]})
    path = tmp_path / "book.ods"
    df.to_excel(path, engine="odf", index=False)

    doc = read_document(_FS, _local_vpath(path))
    assert doc.kind == "table"
    assert doc.rows[0] == ["name", "role"]
    assert doc.rows[1] == ["Alice", "Dev"]
    assert doc.truncated is False


def test_read_document_parquet(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    df = pd.DataFrame({"x": [1, 2, 3], "y": ["a", "b", "c"]})
    path = tmp_path / "data.parquet"
    df.to_parquet(path)

    doc = read_document(_FS, _local_vpath(path))
    assert doc.kind == "table"
    assert doc.rows[0] == ["x", "y"]
    assert doc.rows[1] == ["1", "a"]
    assert doc.truncated is False


def test_read_document_parquet_caps_rows_and_marks_truncated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("pyarrow")
    monkeypatch.setattr("linux_commander.plugins.MAX_PREVIEW_ROWS", 5)
    df = pd.DataFrame({"x": list(range(15))})
    path = tmp_path / "big.parquet"
    df.to_parquet(path)

    doc = read_document(_FS, _local_vpath(path))
    assert doc.truncated is True
    assert len(doc.rows) == 5  # header + data rows, capped at MAX_PREVIEW_ROWS total
