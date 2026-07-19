"""Tests for linux_commander.install_extras's pure (non-subprocess) helpers."""

from __future__ import annotations

from linux_commander.install_extras import (
    _import_name,
    _requirement_name,
    format_report,
    load_extras,
    missing_groups,
    probe_extras,
)

# ---------------------------------------------------------------------------
# _requirement_name / _import_name
# ---------------------------------------------------------------------------


def test_requirement_name_strips_version_specifier() -> None:
    assert _requirement_name("py7zr>=0.20") == "py7zr"
    assert _requirement_name("cryptography>=49.0.0") == "cryptography"
    assert _requirement_name("rarfile>=4.0") == "rarfile"


def test_requirement_name_bare_name_unchanged() -> None:
    assert _requirement_name("paramiko") == "paramiko"


def test_import_name_uses_override_for_libarchive_c() -> None:
    assert _import_name("libarchive-c") == "libarchive"


def test_import_name_uses_override_for_python_docx() -> None:
    assert _import_name("python-docx") == "docx"


def test_import_name_uses_override_for_odfpy() -> None:
    assert _import_name("odfpy") == "odf"


def test_import_name_defaults_to_underscored_requirement_name() -> None:
    assert _import_name("py7zr") == "py7zr"
    assert _import_name("some-package") == "some_package"


# ---------------------------------------------------------------------------
# load_extras — reads this repo's real pyproject.toml
# ---------------------------------------------------------------------------


def test_load_extras_finds_declared_groups() -> None:
    extras = load_extras()
    assert "archives" in extras
    assert "crypto" in extras
    assert "documents" in extras
    assert "py7zr" in extras["archives"]
    assert "rarfile" in extras["archives"]
    assert "cryptography" in extras["crypto"]
    assert "openpyxl" in extras["documents"]
    assert "python-docx" in extras["documents"]
    assert "pandas" in extras["documents"]


# ---------------------------------------------------------------------------
# probe_extras / missing_groups / format_report
# ---------------------------------------------------------------------------


def test_probe_extras_detects_stdlib_module_as_available() -> None:
    # "os" is always importable; use it as a stand-in requirement name that
    # also happens to be a valid import name.
    probed = probe_extras({"fake": ["os"]})
    assert probed == {"fake": [("os", True)]}


def test_probe_extras_detects_missing_module() -> None:
    probed = probe_extras({"fake": ["this_package_does_not_exist_xyz"]})
    assert probed == {"fake": [("this_package_does_not_exist_xyz", False)]}


def test_missing_groups_only_lists_groups_with_a_gap() -> None:
    probed = {
        "ok_group": [("os", True), ("sys", True)],
        "bad_group": [("os", True), ("nope_xyz", False)],
    }
    assert missing_groups(probed) == ["bad_group"]


def test_missing_groups_empty_when_everything_available() -> None:
    probed = {"ok_group": [("os", True)]}
    assert missing_groups(probed) == []


def test_format_report_lists_missing_and_install_hint() -> None:
    probed = {"archives": [("py7zr", False), ("rarfile", True)]}
    report = format_report(probed, zstd_available=False)
    assert "MISSING: py7zr" in report
    assert "rarfile: yes" in report
    assert "py7zr: no" in report
    assert "Missing extras: archives" in report
    assert "uv sync --extra archives" in report
    assert "pip install '.[archives]'" in report
    assert "zstd codec] unavailable" in report


def test_format_report_all_ok_no_install_hint() -> None:
    probed = {"crypto": [("cryptography", True)]}
    report = format_report(probed, zstd_available=True)
    assert "All optional extras are installed." in report
    assert "Missing extras" not in report
    assert "zstd codec] OK" in report
