"""Report and (optionally) install linux-commander's optional dependency extras.

Several features are gated behind optional dependencies declared as extras
in ``pyproject.toml``'s ``[project.optional-dependencies]`` (e.g. ``archives``
for 7z/RAR browsing, ``crypto`` for the Encrypt/Decrypt file operation). Each
module that needs one of these guards its import and quietly disables the
feature when the package is missing (see ``plugins/__init__.py``'s docstring
for the convention). This module answers "which of those packages are
actually installed right now?" and can drive an install.

Console entry point (see ``pyproject.toml`` ``[project.scripts]``)::

    uv run linux-commander-install-extras              # report only
    uv run linux-commander-install-extras --install    # install what's missing
    uv run linux-commander-install-extras --install --yes   # skip the prompt

Public API:
    load_extras() -> dict[str, list[str]]
    probe_extras(extras) -> dict[str, list[tuple[str, bool]]]
    format_report(probed, zstd_available) -> str
    main(argv=None) -> int
"""

from __future__ import annotations

import argparse
import importlib
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

# Requirement name -> the name you `import`, on the rare occasions they differ.
_IMPORT_NAME_OVERRIDES: dict[str, str] = {
    "libarchive-c": "libarchive",
    "python-docx": "docx",
    "odfpy": "odf",
}

_REQUIRES_DIST_RE = re.compile(
    r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)"  # requirement name
    r"[^;]*"  # version specifier / extras marker on the requirement, ignored
    r"(?:;\s*extra\s*==\s*['\"]([^'\"]+)['\"])?\s*$"  # ; extra == "group"
)


def _requirement_name(spec: str) -> str:
    """Strip a PEP 508 version specifier off a requirement string.

    ``"py7zr>=0.20"`` -> ``"py7zr"``.
    """
    for sep in ("[", ">=", "<=", "==", "!=", "~=", ">", "<", " ", ";"):
        idx = spec.find(sep)
        if idx != -1:
            spec = spec[:idx]
    return spec.strip()


def _import_name(requirement_name: str) -> str:
    """Map a PyPI requirement name to the name it is ``import``ed as."""
    return _IMPORT_NAME_OVERRIDES.get(requirement_name, requirement_name.replace("-", "_"))


def _load_extras_from_metadata(dist_name: str) -> dict[str, list[str]]:
    """Fall back path: read extras from the installed distribution's metadata.

    Used when this module is running from an installed package rather than
    a source checkout (no ``pyproject.toml`` alongside it to read directly).
    """
    from importlib.metadata import PackageNotFoundError, metadata

    try:
        meta = metadata(dist_name)
    except PackageNotFoundError:
        return {}
    extras: dict[str, list[str]] = {}
    for req in meta.get_all("Requires-Dist") or []:
        m = _REQUIRES_DIST_RE.match(req.strip())
        if not m:
            continue
        name, extra = m.group(1), m.group(2)
        if extra:
            extras.setdefault(extra, []).append(name)
    return extras


def load_extras() -> dict[str, list[str]]:
    """Return ``{extra_group_name: [requirement_name, ...]}``.

    Reads ``pyproject.toml`` directly when running from a source checkout
    (the documented way to run linux-commander, via ``uv run``); falls back
    to the installed distribution's metadata otherwise.
    """
    pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    if pyproject_path.exists():
        data = tomllib.loads(pyproject_path.read_text())
        raw: dict[str, list[str]] = data.get("project", {}).get("optional-dependencies", {})
        return {group: [_requirement_name(s) for s in specs] for group, specs in raw.items()}
    return _load_extras_from_metadata("linux-commander")


def probe_extras(extras: dict[str, list[str]]) -> dict[str, list[tuple[str, bool]]]:
    """For each extra group, return ``[(requirement_name, is_importable), ...]``."""
    result: dict[str, list[tuple[str, bool]]] = {}
    for group, names in extras.items():
        rows: list[tuple[str, bool]] = []
        for name in names:
            try:
                importlib.import_module(_import_name(name))
                available = True
            except ImportError:
                available = False
            rows.append((name, available))
        result[group] = rows
    return result


def is_zstd_available() -> bool:
    """Whether the stdlib ``compression.zstd`` module is importable.

    Zstandard support (the ``zst`` codec in the compression dialog) comes
    from this Python 3.14+ stdlib module, not a pip-installable package, so
    it is reported separately from the pip extras above.
    """
    try:
        importlib.import_module("compression.zstd")
        return True
    except ImportError:
        return False


def missing_groups(probed: dict[str, list[tuple[str, bool]]]) -> list[str]:
    """Names of extra groups with at least one missing requirement."""
    return [group for group, rows in probed.items() if any(not ok for _, ok in rows)]


def format_report(probed: dict[str, list[tuple[str, bool]]], zstd_available: bool) -> str:
    """Render a human-readable status report (used by both the CLI and the GUI)."""
    lines = ["linux-commander optional dependencies", ""]
    for group, rows in sorted(probed.items()):
        missing = [name for name, ok in rows if not ok]
        status = "OK" if not missing else f"MISSING: {', '.join(missing)}"
        lines.append(f"[{group}] {status}")
        for name, ok in rows:
            mark = "yes" if ok else "no"
            lines.append(f"    {name}: {mark}")
    lines.append("")
    lines.append(
        f"[zstd codec] {'OK' if zstd_available else 'unavailable'} "
        "(stdlib compression.zstd, requires Python 3.14+ -- not pip-installable)"
    )
    missing = missing_groups(probed)
    if missing:
        extras_spec = ",".join(missing)
        lines.append("")
        lines.append(f"Missing extras: {', '.join(missing)}")
        lines.append(f"Install with: uv sync --extra {' --extra '.join(missing)}")
        lines.append(f"          or: pip install '.[{extras_spec}]'")
    else:
        lines.append("")
        lines.append("All optional extras are installed.")
    return "\n".join(lines)


def _detect_installer() -> list[str]:
    """Prefer ``uv pip install``, fall back to ``python -m pip install``."""
    if shutil.which("uv"):
        return ["uv", "pip", "install"]
    return [sys.executable, "-m", "pip", "install"]


def install_groups(groups: list[str], cwd: Path) -> int:
    """Install ``groups`` (extras) via the detected installer. Returns the exit code."""
    extras_spec = f".[{','.join(groups)}]"
    cmd = [*_detect_installer(), extras_spec]
    print(f"Running: {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=cwd)
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--install", action="store_true", help="install missing extras after reporting"
    )
    parser.add_argument("--yes", "-y", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args(argv)

    extras = load_extras()
    if not extras:
        print("Could not determine optional-dependency groups.", file=sys.stderr)
        return 1
    probed = probe_extras(extras)
    print(format_report(probed, is_zstd_available()))

    missing = missing_groups(probed)
    if not missing or not args.install:
        return 0

    if not args.yes:
        reply = input(f"\nInstall missing extras ({', '.join(missing)})? [y/N] ")
        if reply.strip().lower() not in ("y", "yes"):
            print("Aborted.")
            return 1

    root = Path(__file__).resolve().parent.parent
    return install_groups(missing, root)


if __name__ == "__main__":
    raise SystemExit(main())
