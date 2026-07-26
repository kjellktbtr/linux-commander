"""GRP container builder plugin.

GRP is a flat archive format with 12-byte filename limit (8.3 convention).
Uses stdlib only — always available.
"""

from __future__ import annotations

import json
import pathlib
from collections import defaultdict

from linux_commander.containers import Container
from linux_commander.grp_names import (
    GRP_COUNT,
    GRP_ENTRY,
    GRP_MAGIC,
    GRP_MAX_NAME_LEN,
    make_grp_name,
)
from linux_commander.operations import (
    CancelPredicate,
    OperationError,
    ProgressCallback,
    call_progress,
)
from linux_commander.vfs import FileSystem, VfsPath


class GrpContainer(Container):
    @property
    def name(self) -> str:
        return "grp"

    @property
    def extension(self) -> str:
        return ".grp"

    def build(
        self,
        sources: list[VfsPath],
        dest: pathlib.Path,
        local_fs: FileSystem,
        on_progress: ProgressCallback,
        should_cancel: CancelPredicate,
    ) -> list[OperationError]:
        from linux_commander.archiving import _iter_sources

        all_files: list[tuple[str, bytes]] = []

        def add_local_file(path: pathlib.Path, arcname: str) -> None:
            all_files.append((arcname, path.read_bytes()))

        def add_local_dir(path: pathlib.Path, _arcname: str) -> None:
            import os

            parent = path.parent
            for root, _dirs, files in os.walk(path):
                for fname in files:
                    fpath = pathlib.Path(root) / fname
                    all_files.append((str(fpath.relative_to(parent)), fpath.read_bytes()))

        def add_bytes(arcname: str, data: bytes) -> None:
            all_files.append((arcname, data))

        errors = _iter_sources(
            sources,
            local_fs,
            should_cancel,
            on_progress,
            add_local_file,
            add_local_dir,
            add_bytes,
        )
        if errors:
            return errors

        # Generate GRP-compliant names
        name_counts: dict[str, int] = defaultdict(int)
        grp_entries: list[tuple[str, bytes]] = []
        name_mapping: dict[str, str] = {}

        for orig_name, data in all_files:
            if should_cancel():
                first = sources[0] if sources else None
                if first is not None:
                    errors.append(OperationError(first, "Cancelled by user"))
                break

            grp_name = make_grp_name(orig_name, name_counts)
            if grp_name != orig_name:
                name_mapping[grp_name] = orig_name

            grp_entries.append((grp_name, data))
            call_progress(
                on_progress,
                len(grp_entries),
                len(all_files),
                f"Packing {grp_name}",
                None,
                None,
            )

        if errors:
            return errors

        # Add mapping file if any names were modified
        if name_mapping:
            mapping_json = json.dumps(name_mapping, ensure_ascii=False).encode("utf-8")
            grp_entries.append(("__GRPMAP.J", mapping_json))

        # Build GRP archive
        parts: list[bytes] = [GRP_MAGIC, GRP_COUNT.pack(len(grp_entries))]
        for name, data in grp_entries:
            encoded = name.encode("ascii", errors="replace").ljust(GRP_MAX_NAME_LEN, b"\x00")[
                :GRP_MAX_NAME_LEN
            ]
            parts.append(GRP_ENTRY.pack(encoded, len(data)))
        for _, data in grp_entries:
            parts.append(data)

        dest.write_bytes(b"".join(parts))
        return errors


container_class = GrpContainer
