"""TAR container builder plugin.

Uses stdlib tarfile — always available.
"""

from __future__ import annotations

import io
import pathlib
import tarfile

from linux_commander.containers import Container
from linux_commander.operations import CancelPredicate, OperationError, ProgressCallback
from linux_commander.vfs import FileSystem, VfsPath


class TarContainer(Container):
    @property
    def name(self) -> str:
        return "tar"

    @property
    def extension(self) -> str:
        return ".tar"

    def build(
        self,
        sources: list[VfsPath],
        dest: pathlib.Path,
        local_fs: FileSystem,
        on_progress: ProgressCallback,
        should_cancel: CancelPredicate,
    ) -> list[OperationError]:
        from linux_commander.archiving import _iter_sources

        with tarfile.open(dest, "w") as tf:

            def add_local_file(path: pathlib.Path, arcname: str) -> None:
                tf.add(path, arcname=arcname)

            def add_local_dir(path: pathlib.Path, arcname: str) -> None:
                tf.add(path, arcname=arcname)

            def add_bytes(arcname: str, data: bytes) -> None:
                ti = tarfile.TarInfo(name=arcname)
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))

            return _iter_sources(
                sources,
                local_fs,
                should_cancel,
                on_progress,
                add_local_file,
                add_local_dir,
                add_bytes,
            )


container_class = TarContainer
