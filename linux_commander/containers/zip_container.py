"""ZIP container builder plugin.

Uses stdlib zipfile — always available.
"""

from __future__ import annotations

import os
import pathlib
import zipfile

from linux_commander.containers import Container
from linux_commander.operations import CancelPredicate, OperationError, ProgressCallback
from linux_commander.vfs import FileSystem, VfsPath


class ZipContainer(Container):
    @property
    def name(self) -> str:
        return "zip"

    @property
    def extension(self) -> str:
        return ".zip"

    def build(
        self,
        sources: list[VfsPath],
        dest: pathlib.Path,
        local_fs: FileSystem,
        on_progress: ProgressCallback,
        should_cancel: CancelPredicate,
    ) -> list[OperationError]:
        from linux_commander.archiving import _iter_sources

        with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:

            def add_local_file(path: pathlib.Path, arcname: str) -> None:
                zf.write(path, arcname)

            def add_local_dir(path: pathlib.Path, _arcname: str) -> None:
                parent = path.parent
                for root, _dirs, files in os.walk(path):
                    for fname in files:
                        fpath = pathlib.Path(root) / fname
                        zf.write(fpath, str(fpath.relative_to(parent)))

            def add_bytes(arcname: str, data: bytes) -> None:
                zf.writestr(arcname, data)

            return _iter_sources(
                sources,
                local_fs,
                should_cancel,
                on_progress,
                add_local_file,
                add_local_dir,
                add_bytes,
            )


container_class = ZipContainer
