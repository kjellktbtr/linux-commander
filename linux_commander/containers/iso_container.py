"""ISO container builder plugin.

Requires libarchive-c — gracefully unavailable when the package is missing.
"""

from __future__ import annotations

import pathlib

from linux_commander.containers import Container
from linux_commander.operations import CancelPredicate, OperationError, ProgressCallback
from linux_commander.vfs import FileSystem, VfsPath

try:
    import libarchive

    container_class: type[Container] | None = None  # set below

except ImportError:
    libarchive = None  # type: ignore[assignment]


class IsoContainer(Container):
    @property
    def name(self) -> str:
        return "iso"

    @property
    def extension(self) -> str:
        return ".iso"

    @property
    def available(self) -> bool:
        return libarchive is not None

    def build(
        self,
        sources: list[VfsPath],
        dest: pathlib.Path,
        local_fs: FileSystem,
        on_progress: ProgressCallback,
        should_cancel: CancelPredicate,
    ) -> list[OperationError]:
        from linux_commander.archiving import _iter_sources

        if libarchive is None:
            first = sources[0] if sources else None
            return [OperationError(first, "libarchive-c package is not installed")] if first else []

        with libarchive.file_writer(str(dest), "iso9660") as archive:

            def add_local_file(path: pathlib.Path, arcname: str) -> None:
                archive.add_files(str(path), pathname=arcname, recursive=False)

            def add_local_dir(path: pathlib.Path, arcname: str) -> None:
                archive.add_files(str(path), pathname=arcname, recursive=True)

            def add_bytes(arcname: str, data: bytes) -> None:
                archive.add_file_from_memory(arcname, len(data), data)

            return _iter_sources(
                sources,
                local_fs,
                should_cancel,
                on_progress,
                add_local_file,
                add_local_dir,
                add_bytes,
            )


if libarchive is not None:
    container_class = IsoContainer
