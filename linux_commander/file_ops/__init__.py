"""Self-registering file-operation plugin registry.

An operation plugin is any Python module inside this package that exposes a
module-level list ``OPERATIONS: list[FileOperation]``.  Discovery is automatic
via ``pkgutil.iter_modules`` — dropping a new ``*_op.py`` file here is enough
to register it.  Optional operations (e.g. those requiring third-party crypto
packages) should guard their dependency and set ``OPERATIONS = []`` when the
dep is absent.

``FileOperation`` contract
--------------------------
``name``
   The label shown in the Operations menu (e.g. ``"Base64 Encode"``).

``run(sources, dest_dir, on_progress, should_cancel, **params)``
   Called on a background thread (via ``progress_dialog.run_with_progress``).
   Signature matches the batch-operation convention in ``operations.py``:
   - *sources* — ``list[VfsPath]`` of selected files to process.
   - *dest_dir* — ``VfsPath`` of the active panel directory; output files
     are written here next to their source (e.g. ``foo.txt`` → ``foo.txt.b64``).
   - *on_progress* — ``ProgressCallback`` (current, total, name).
   - *should_cancel* — ``CancelPredicate``; return early if it returns True.
   - **params* — extra keyword args forwarded from ``prepare``'s return dict.
   Must return ``list[OperationError]``.

``prepare(parent_widget, sources)`` *(optional, may be None)*
   Called on the main thread **before** the worker starts.  Use it to show a
   dialog and collect parameters (passphrase, output format, …).  Return a
   ``dict`` of keyword args to forward to ``run``, or ``None`` to cancel the
   operation without starting the worker.
"""

from __future__ import annotations

import importlib
import pkgutil
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass, field
from types import ModuleType

from linux_commander.operations import CancelPredicate as CancelPredicate
from linux_commander.operations import OperationError
from linux_commander.operations import ProgressCallback as ProgressCallback
from linux_commander.vfs import VfsPath

PrepareFunc = Callable[[tk.Misc, list[VfsPath]], dict | None]
RunFunc = Callable[..., list[OperationError]]


@dataclass(frozen=True)
class FileOperation:
    """Descriptor for a single file operation that appears in the Operations menu."""

    name: str
    """Menu label, e.g. ``'Base64 Encode'``."""

    run: RunFunc
    """Worker function called on a background thread."""

    prepare: PrepareFunc | None = field(default=None)
    """Optional main-thread hook to collect extra parameters before the worker."""

    description: str = ""
    """One-line description shown as a tooltip (future use)."""


@dataclass
class OperationPluginLoadStatus:
    """Status of an operation plugin module load attempt."""

    name: str
    """Module name (e.g. 'base64_op')."""

    success: bool
    """Whether the plugin loaded successfully."""

    error: str | None = None
    """Error message if loading failed."""

    operations: tuple[str, ...] = field(default_factory=tuple)
    """Names of operations registered by this plugin."""


_operation_plugin_load_status: list[OperationPluginLoadStatus] | None = None


def get_operation_plugin_load_status() -> list[OperationPluginLoadStatus]:
    """Return the load status of all discovered operation plugins.

    The first call triggers plugin discovery; subsequent calls return cached results.
    """
    global _operation_plugin_load_status
    if _operation_plugin_load_status is None:
        _discover()
    assert _operation_plugin_load_status is not None
    return _operation_plugin_load_status


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

_ops: list[FileOperation] | None = None


def _discover() -> list[FileOperation]:
    """Scan this package for operation modules and collect their OPERATIONS lists."""
    global _operation_plugin_load_status
    result: list[FileOperation] = []
    status_list: list[OperationPluginLoadStatus] = []
    for info in pkgutil.iter_modules(__path__):  # type: ignore[name-defined]
        try:
            mod: ModuleType = importlib.import_module(f"{__name__}.{info.name}")
        except Exception as e:
            status_list.append(
                OperationPluginLoadStatus(
                    name=info.name,
                    success=False,
                    error=str(e),
                )
            )
            continue
        ops = getattr(mod, "OPERATIONS", [])
        op_names = tuple(op.name for op in ops if isinstance(op, FileOperation))
        status_list.append(
            OperationPluginLoadStatus(
                name=info.name,
                success=True,
                operations=op_names,
            )
        )
        for op in ops:
            if isinstance(op, FileOperation):
                result.append(op)
    _operation_plugin_load_status = status_list
    return result


def available_operations() -> list[FileOperation]:
    """Return all registered file operations (cached after first call)."""
    global _ops
    if _ops is None:
        _ops = _discover()
    return _ops
