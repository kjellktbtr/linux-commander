"""File operations: Base64 encode and decode.

Encode: reads each source file via the VFS, base64-encodes it, and writes
``<filename>.b64`` into the destination directory.

Decode: reads each ``.b64`` source file, decodes it, and writes the result
as ``<filename>`` (stripping the trailing ``.b64`` suffix).  Files without a
``.b64`` suffix are skipped with an error.
"""

from __future__ import annotations

import base64

from linux_commander.file_ops import FileOperation
from linux_commander.operations import (
    CancelPredicate,
    OperationError,
    ProgressCallback,
    call_progress,
)
from linux_commander.vfs import VfsPath


def _encode(
    sources: list[VfsPath],
    dest_dir: VfsPath,
    on_progress: ProgressCallback,
    should_cancel: CancelPredicate,
) -> list[OperationError]:
    """Base64-encode each source file into dest_dir as ``<name>.b64``."""
    errors: list[OperationError] = []
    total = len(sources)
    for current, src in enumerate(sources, start=1):
        if should_cancel():
            break
        call_progress(on_progress, current, total, src.name, None, None)
        out_name = src.name + ".b64"
        dest = dest_dir / out_name
        try:
            with src.fs.open_read(src) as inp:
                raw = inp.read()
            encoded = base64.b64encode(raw)
            with dest_dir.fs.open_write(dest) as out:
                out.write(encoded)
        except OSError as exc:
            errors.append(OperationError(path=src, message=str(exc)))
    return errors


def _decode(
    sources: list[VfsPath],
    dest_dir: VfsPath,
    on_progress: ProgressCallback,
    should_cancel: CancelPredicate,
) -> list[OperationError]:
    """Base64-decode each ``.b64`` source file into dest_dir."""
    errors: list[OperationError] = []
    total = len(sources)
    for current, src in enumerate(sources, start=1):
        if should_cancel():
            break
        call_progress(on_progress, current, total, src.name, None, None)
        if not src.name.lower().endswith(".b64"):
            errors.append(
                OperationError(
                    path=src,
                    message=f"'{src.name}' does not have a .b64 extension; skipped.",
                )
            )
            continue
        out_name = src.name[:-4]  # strip .b64
        if not out_name:
            out_name = src.name + ".decoded"
        dest = dest_dir / out_name
        try:
            with src.fs.open_read(src) as inp:
                encoded = inp.read()
            decoded = base64.b64decode(encoded)
            with dest_dir.fs.open_write(dest) as out:
                out.write(decoded)
        except (OSError, Exception) as exc:
            errors.append(OperationError(path=src, message=str(exc)))
    return errors


OPERATIONS: list[FileOperation] = [
    FileOperation(
        name="Base64 Encode",
        run=_encode,
        description="Encode selected files to Base64 (.b64) in the same directory.",
    ),
    FileOperation(
        name="Base64 Decode",
        run=_decode,
        description="Decode selected .b64 files back to their original content.",
    ),
]
