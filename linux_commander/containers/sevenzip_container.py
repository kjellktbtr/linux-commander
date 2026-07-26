"""7z container builder plugin.

Requires py7zr — gracefully unavailable when the package is missing.
"""

from __future__ import annotations

import os
import pathlib

from linux_commander.containers import Container
from linux_commander.operations import CancelPredicate, OperationError, ProgressCallback
from linux_commander.vfs import FileSystem, VfsPath

try:
    import py7zr

    container_class: type[Container] | None = None  # set below

except ImportError:
    py7zr = None  # type: ignore[assignment]


class SevenZipContainer(Container):
    @property
    def name(self) -> str:
        return "7z"

    @property
    def extension(self) -> str:
        return ".7z"

    @property
    def available(self) -> bool:
        return py7zr is not None

    def build(
        self,
        sources: list[VfsPath],
        dest: pathlib.Path,
        local_fs: FileSystem,
        on_progress: ProgressCallback,
        should_cancel: CancelPredicate,
    ) -> list[OperationError]:
        from linux_commander.archiving import _iter_sources

        if py7zr is None:
            first = sources[0] if sources else None
            return [OperationError(first, "py7zr package is not installed")] if first else []

        with py7zr.SevenZipFile(dest, "w") as zf:

            def add_local_file(path: pathlib.Path, arcname: str) -> None:
                zf.write(path, arcname)

            def add_local_dir(path: pathlib.Path, _arcname: str) -> None:
                parent = path.parent
                for root, _dirs, files in os.walk(path):
                    for fname in files:
                        fpath = pathlib.Path(root) / fname
                        zf.write(fpath, str(fpath.relative_to(parent)))

            def add_bytes(arcname: str, data: bytes) -> None:
                zf.writestr(data, arcname)

            return _iter_sources(
                sources,
                local_fs,
                should_cancel,
                on_progress,
                add_local_file,
                add_local_dir,
                add_bytes,
            )


if py7zr is not None:
    container_class = SevenZipContainer
