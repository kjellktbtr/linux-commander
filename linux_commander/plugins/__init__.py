"""VFS plugin discovery and shared utilities.

Each plugin module in this package exposes some subset of:
   EXTENSIONS: tuple[str, ...] = (".zip",)   # file-extension backends
   SCHEMES: tuple[str, ...] = ()             # protocol backends, e.g. ("ftp",)
   def open_fs(host_fs, path: VfsPath) -> FileSystem   # for extension plugins
   def connect_fs(url: str) -> FileSystem              # for scheme plugins

    VIEW_EXTENSIONS: tuple[str, ...] = (".xlsx",)       # viewer-preview backends
    def read_document(host_fs, path: VfsPath) -> ViewDocument

Broken plugins are silently skipped so one bad module can't block discovery.

Optional/third-party plugins should guard their dependency at module top-level
and set EXTENSIONS/SCHEMES/VIEW_EXTENSIONS to empty tuples when the dependency
is absent so the plugin silently registers nothing rather than crashing at
import time:

    try:
        import py7zr
        EXTENSIONS = (".7z",)
    except ImportError:
        py7zr = None        # type: ignore[assignment]
        EXTENSIONS = ()
"""

from __future__ import annotations

import importlib
import os
import pkgutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Literal

from linux_commander.settings import StoredKey
from linux_commander.vfs import FileSystem as FileSystem
from linux_commander.vfs import ReadableFileSystem, VfsPath


@dataclass
class PluginLoadStatus:
    """Status of a plugin module load attempt."""

    name: str
    """Module name (e.g. 'zip_plugin')."""

    success: bool
    """Whether the plugin loaded successfully."""

    error: str | None = None
    """Error message if loading failed."""

    extensions: tuple[str, ...] = field(default_factory=tuple)
    """File extensions registered by this plugin."""

    schemes: tuple[str, ...] = field(default_factory=tuple)
    """URL schemes registered by this plugin."""

    view_extensions: tuple[str, ...] = field(default_factory=tuple)
    """Viewer extensions registered by this plugin."""


_plugin_load_status: list[PluginLoadStatus] | None = None


def get_plugin_load_status() -> list[PluginLoadStatus]:
    """Return the load status of all discovered VFS plugins.

    The first call triggers plugin discovery; subsequent calls return cached results.
    """
    global _plugin_load_status
    if _plugin_load_status is None:
        _discover()
    assert _plugin_load_status is not None
    return _plugin_load_status


_temp_files: set[str] = set()
_temp_dirs: set[str] = set()

_ext_map: dict[str, ModuleType] | None = None
_scheme_map: dict[str, ModuleType] | None = None
_view_map: dict[str, ModuleType] | None = None

MAX_PREVIEW_ROWS = 5000
"""Cap on rows loaded for a viewer document preview (table formats)."""


@dataclass
class ViewDocument:
    """Parsed content handed from a viewer reader plugin to the built-in viewer.

    ``kind="table"`` populates the CSV/table view from ``rows`` (``rows[0]``
    is the header); ``kind="text"`` inserts ``text`` into the plain text view.
    ``truncated`` is True when the source had more than ``MAX_PREVIEW_ROWS``
    rows (table) and only a prefix was loaded.
    ``meta`` holds format-specific metadata (e.g., sheet names, formats).
    """

    kind: Literal["table", "text", "pdf"]
    rows: list[list[str]] = field(default_factory=list)
    text: str = ""
    truncated: bool = False
    meta: dict = field(default_factory=dict)


# Credential-prompt hook for encrypted (.crp) archives -- see crypt_plugin.py.
# A VFS plugin's open_fs(host_fs, path) has no UI parent to show a dialog
# from, so the app registers a callback here at startup that does.
CredentialProvider = Callable[[str], "tuple[str | None, StoredKey | None] | None"]
_credential_provider: CredentialProvider | None = None


def set_credential_provider(fn: CredentialProvider) -> None:
    """Register the callback used to prompt for .crp decryption credentials.

    ``fn(name) -> (password, key) | None``: exactly one of ``(password,
    key)`` should be set (the other ``None``) on success, or return ``None``
    if the user cancelled. Called from ``plugins/crypt_plugin.py``.
    """
    global _credential_provider
    _credential_provider = fn


def get_credential_provider() -> CredentialProvider | None:
    """Return the registered credential-prompt callback, or ``None``."""
    return _credential_provider


def materialize(host_fs: ReadableFileSystem, path: VfsPath) -> Path:
    """Return a real OS ``Path`` for ``path``.

    If the backend exposes a real file via ``realpath()``, use it directly.
    Otherwise spill ``open_read()`` to a ``NamedTemporaryFile`` and return
    that path so seekable formats like ZIP can open it normally.
    """
    real = host_fs.realpath(path)
    if real is not None:
        return real
    suffix = path.suffix or ""
    fd, tmp_name = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as out:
            with host_fs.open_read(path) as src:
                while chunk := src.read(65536):
                    out.write(chunk)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    _temp_files.add(tmp_name)
    return Path(tmp_name)


def spill_named_temp(data: bytes, name: str) -> Path:
    """Write ``data`` to a new temp file named exactly ``name``.

    For plugins that must fully decode a blob in memory (e.g. decrypting a
    ``.crp`` file) before handing it to a real-file-only format's ``open_fs``.
    Unlike ``tempfile.mkstemp`` (which always assigns a random basename, only
    optionally keeping a suffix), this preserves the *whole* name -- required
    for compound extensions like ``.grp.zst`` or ``.tar.gz``, where a plugin
    determines its own inner member name by stripping just its one suffix off
    the *file's* name (e.g. ``compress_plugin`` strips ``.zst``): a random
    basename would leave that plugin computing an inner name from garbage.
    Creates a dedicated temp directory per call so ``name`` never collides.
    """
    tmp_dir = tempfile.mkdtemp()
    tmp_path = Path(tmp_dir) / name
    try:
        tmp_path.write_bytes(data)
    except Exception:
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass
        raise
    _temp_files.add(str(tmp_path))
    _temp_dirs.add(tmp_dir)
    return tmp_path


def cleanup_temp(path: Path) -> None:
    """Unlink a temp file created by ``materialize``/``spill_named_temp``, if we own it."""
    s = str(path)
    if s in _temp_files:
        _temp_files.discard(s)
        try:
            path.unlink()
        except OSError:
            pass
        parent = str(path.parent)
        if parent in _temp_dirs:
            _temp_dirs.discard(parent)
            try:
                os.rmdir(parent)
            except OSError:
                pass


def cleanup_all_temps() -> None:
    """Unlink every temp file/dir created by ``materialize``; called on app quit."""
    for tmp in list(_temp_files):
        try:
            Path(tmp).unlink()
        except OSError:
            pass
    _temp_files.clear()
    for tmp_dir in list(_temp_dirs):
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass
    _temp_dirs.clear()


def _discover() -> tuple[dict[str, ModuleType], dict[str, ModuleType], dict[str, ModuleType]]:
    """Scan this package for plugin modules and build extension/scheme/view maps."""
    global _plugin_load_status, _ext_map, _scheme_map, _view_map

    ext_map: dict[str, ModuleType] = {}
    scheme_map: dict[str, ModuleType] = {}
    view_map: dict[str, ModuleType] = {}
    status_list: list[PluginLoadStatus] = []

    for info in pkgutil.iter_modules(__path__):  # type: ignore[name-defined]
        mod_name = info.name
        try:
            mod = importlib.import_module(f"{__name__}.{mod_name}")
        except Exception as e:
            status_list.append(
                PluginLoadStatus(
                    name=mod_name,
                    success=False,
                    error=str(e),
                )
            )
            continue

        extensions = tuple(getattr(mod, "EXTENSIONS", ()))
        schemes = tuple(getattr(mod, "SCHEMES", ()))
        view_extensions = tuple(getattr(mod, "VIEW_EXTENSIONS", ()))

        for ext in extensions:
            ext_map[ext.lower()] = mod
        for scheme in schemes:
            scheme_map[scheme.lower()] = mod
        for ext in view_extensions:
            view_map[ext.lower()] = mod

        status_list.append(
            PluginLoadStatus(
                name=mod_name,
                success=True,
                extensions=extensions,
                schemes=schemes,
                view_extensions=view_extensions,
            )
        )

    _ext_map = ext_map
    _scheme_map = scheme_map
    _view_map = view_map
    _plugin_load_status = status_list
    return ext_map, scheme_map, view_map


def _ensure_discovered() -> None:
    global _ext_map, _scheme_map, _view_map
    if _ext_map is None:
        _ext_map, _scheme_map, _view_map = _discover()


def plugin_for_extension(suffix: str) -> ModuleType | None:
    """Return the plugin module for ``suffix`` (e.g. ``".zip"``), or ``None``."""
    _ensure_discovered()
    assert _ext_map is not None
    return _ext_map.get(suffix.lower())


def plugin_for_name(name: str) -> ModuleType | None:
    """Find a plugin for a filename, trying compound extensions first.

    For ``'archive.tar.gz'``, tries ``'.tar.gz'`` before ``'.gz'`` so that
    compound-extension formats (tar.gz, tar.bz2, tar.xz) are matched correctly.
    Leading dots in dotfiles are ignored (``'.gitignore'`` → ``None``).
    """
    _ensure_discovered()
    assert _ext_map is not None
    start = 1  # start > 0 so dotfiles never match their own leading dot
    while True:
        pos = name.find(".", start)
        if pos < 0:
            break
        plugin = _ext_map.get(name[pos:].lower())
        if plugin is not None:
            return plugin
        start = pos + 1
    return None


def plugin_for_scheme(scheme: str) -> ModuleType | None:
    """Return the plugin module for ``scheme`` (e.g. ``"ftp"``), or ``None``."""
    _ensure_discovered()
    assert _scheme_map is not None
    return _scheme_map.get(scheme.lower())


def viewer_plugin_for_name(name: str) -> ModuleType | None:
    """Find a viewer reader plugin for a filename, trying compound extensions first.

    Mirrors ``plugin_for_name``'s compound-extension matching (e.g. would try
    ``'.tar.gz'`` before ``'.gz'``), so a future ``VIEW_EXTENSIONS`` entry with
    a compound suffix works the same way. Leading dots in dotfiles are ignored.
    """
    _ensure_discovered()
    assert _view_map is not None
    start = 1  # start > 0 so dotfiles never match their own leading dot
    while True:
        pos = name.find(".", start)
        if pos < 0:
            break
        plugin = _view_map.get(name[pos:].lower())
        if plugin is not None:
            return plugin
        start = pos + 1
    return None
