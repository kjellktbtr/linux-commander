"""VFS plugin for browsing and writing Build Engine GRP archives."""

from __future__ import annotations

import io
import json
import os
import tempfile
from pathlib import Path
from typing import BinaryIO

from linux_commander.grp_names import GRP_COUNT, GRP_ENTRY, GRP_MAGIC, make_grp_name
from linux_commander.plugins import materialize
from linux_commander.vfs import FileEntry, FileSystem, StatResult, VfsPath

EXTENSIONS: tuple[str, ...] = (".grp",)

MAGIC = GRP_MAGIC
_ENTRY = GRP_ENTRY
_COUNT = GRP_COUNT

_MAPPING_MEMBER = "__GRPMAP.J"


class GrpFileSystem(FileSystem):
    """Read-write VFS backend backed by a Build Engine GRP archive.

    GRP format:
      12 bytes  magic        — b"KenSilverman"
      4 bytes   numfiles     — LE uint32
      numfiles × 16 bytes   — directory entries (12-byte null-padded name + 4-byte size)
      concatenated file data

    GRP itself is flat with a 12-byte name limit (8.3 convention, no
    directories). A hidden member (``__GRPMAP.J``, a JSON object) maps
    truncated member names back to their real original path when one was
    too long or needed a directory separator baked in; a member absent from
    that map is its own original path verbatim.

    ``_orig_to_grp`` is the single runtime index built from that: every
    member has exactly one entry, keyed case-insensitively (Build engine
    convention), value = (exact-case original path, grp member key). All
    hierarchy/listing logic below works in original-path space -- the grp
    (truncated) name is only consulted to actually fetch a member's bytes.
    This mirrors how zip_plugin/tar_plugin work; GRP just adds the name
    mapping layer since its own on-disk names are truncated.

    Since GRP archives are small (DOS-era, typically < 100 MB), the entire
    archive is loaded into memory on mount. Write operations modify the
    in-memory structure and the archive is rewritten atomically on close().
    """

    writable: bool = True

    def __init__(self, archive_path: Path, host_vpath: VfsPath) -> None:
        self._archive_path = archive_path
        self._host_vpath = host_vpath
        self.display_prefix = f"grp:{archive_path}!"
        self._members: dict[str, bytes] = {}  # grp_name -> data
        self._name_map: dict[str, str] = {}  # grp_name -> original path (only when truncated)
        # UPPER(original path) -> (exact-case original path, grp_name); one entry per member.
        self._orig_to_grp: dict[str, tuple[str, str]] = {}
        # UPPER(dir path) -> exact-case dir path, for every ancestor dir of every member.
        self._dirs: dict[str, str] = {}
        self._name_counts: dict[str, int] = {}  # collision counters for make_grp_name
        self._dirty = False
        self._load_archive()

    # ----------------------------------------------------------------------
    # Loading / saving
    # ----------------------------------------------------------------------

    def _load_archive(self) -> None:
        """Read the entire GRP file into memory and build the member index."""
        data = self._archive_path.read_bytes()
        if len(data) < 16:
            raise OSError(f"{self._archive_path}: file too short to be a GRP archive")
        if data[:12] != MAGIC:
            raise OSError(f"{self._archive_path}: bad GRP magic (expected b'KenSilverman')")

        (numfiles,) = _COUNT.unpack_from(data, 12)
        dir_size = numfiles * _ENTRY.size
        dir_end = 16 + dir_size
        if len(data) < dir_end:
            raise OSError(
                f"{self._archive_path}: directory truncated ({numfiles} entries declared)"
            )

        entries: list[tuple[str, int]] = []
        for i in range(numfiles):
            raw_name, size = _ENTRY.unpack_from(data, 16 + i * _ENTRY.size)
            name = raw_name.rstrip(b"\x00").decode("ascii", errors="replace")
            entries.append((name, size))

        offset = dir_end
        for name, size in entries:
            if offset + size > len(data):
                raise OSError(f"{self._archive_path}: member '{name}' extends past end of file")
            self._members[name] = data[offset : offset + size]
            offset += size

        # Load name mapping if present (stored as __GRPMAP.J), hiding it from listings.
        for k in list(self._members):
            if k.upper() != _MAPPING_MEMBER:
                continue
            try:
                self._name_map = json.loads(self._members[k].decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass  # Ignore corrupt mapping file
            del self._members[k]
            break

        # Replay the mapped names through make_grp_name so its collision
        # counters (self._name_counts) reflect what's already in the
        # archive; new writes then continue the same ~1, ~2, ... numbering
        # instead of colliding with an existing truncated name. Best-effort:
        # entries that fit without truncation were never recorded in
        # _name_map, so they aren't replayed here.
        for orig in self._name_map.values():
            make_grp_name(orig, self._name_counts)

        self._build_virtual_dirs()

    def _build_virtual_dirs(self) -> None:
        """Rebuild ``_orig_to_grp``/``_dirs`` from ``_members``/``_name_map``."""
        self._orig_to_grp = {}
        self._dirs = {}
        for grp_name in self._members:
            orig_path = self._name_map.get(grp_name, grp_name)
            self._orig_to_grp[orig_path.upper()] = (orig_path, grp_name)
            parts = orig_path.split("/")
            for i in range(1, len(parts)):
                dir_path = "/".join(parts[:i])
                self._dirs.setdefault(dir_path.upper(), dir_path)

    @classmethod
    def create(cls, path: Path, files: dict[str, bytes]) -> None:
        """Create a new GRP archive from a dict of name -> data."""
        # Validate names
        name_bytes: dict[str, bytes] = {}
        for name in files:
            encoded = name.encode("ascii")
            if len(encoded) > 12:
                raise OSError(f"Filename '{name}' exceeds 12-byte GRP limit")
            name_bytes[name] = encoded.ljust(12, b"\x00")

        # Check for duplicate names (case-insensitive)
        seen: dict[str, str] = {}
        for name in files:
            up = name.upper()
            if up in seen:
                raise OSError(f"Duplicate name '{name}' (conflicts with '{seen[up]}')")
            seen[up] = name

        parts: list[bytes] = [MAGIC, _COUNT.pack(len(files))]
        parts.extend(_ENTRY.pack(name_bytes[name], len(data)) for name, data in files.items())
        parts.extend(files.values())
        new_data = b"".join(parts)
        path.write_bytes(new_data)

    def _rewrite(self) -> None:
        """Atomically rewrite the GRP archive from the in-memory member dict."""
        if not self._members:
            raise OSError("Cannot create an empty GRP archive")

        # Include the name mapping (if any) as a hidden member so the
        # original long/nested names survive this rewrite. self._members
        # itself is left untouched -- the map is only ever materialized at
        # serialization time.
        to_write = dict(self._members)
        if self._name_map:
            to_write[_MAPPING_MEMBER] = json.dumps(self._name_map, ensure_ascii=False).encode(
                "utf-8"
            )

        # Validate all names before writing
        name_bytes: dict[str, bytes] = {}
        for name in to_write:
            encoded = name.encode("ascii")
            if len(encoded) > 12:
                raise OSError(f"Filename '{name}' exceeds 12-byte GRP limit")
            name_bytes[name] = encoded.ljust(12, b"\x00")

        # Check for duplicate names (case-insensitive)
        seen: dict[str, str] = {}
        for name in to_write:
            up = name.upper()
            if up in seen:
                raise OSError(f"Duplicate name '{name}' (conflicts with '{seen[up]}')")
            seen[up] = name

        parts: list[bytes] = [MAGIC, _COUNT.pack(len(to_write))]
        parts.extend(_ENTRY.pack(name_bytes[name], len(data)) for name, data in to_write.items())
        parts.extend(to_write.values())
        new_data = b"".join(parts)

        # Atomic write: temp file in same directory, then replace
        dir_path = self._archive_path.parent
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=dir_path, delete=False, suffix=".grp.tmp"
        ) as tmp:
            tmp.write(new_data)
            tmp_path = Path(tmp.name)
        try:
            os.replace(tmp_path, self._archive_path)
        except Exception:
            try:
                tmp_path.unlink()
            except OSError:
                pass
            raise

    # ----------------------------------------------------------------------
    # FileSystem API
    # ----------------------------------------------------------------------

    @staticmethod
    def _orig_path(path: VfsPath) -> str:
        """Return the '/'-joined original path for a VfsPath (root -> '')."""
        if len(path.parts) <= 1:
            return ""
        return "/".join(path.parts[1:])

    def __contains__(self, name: str) -> bool:
        """Case-insensitive containment check."""
        key = name.upper()
        return key in self._orig_to_grp or key in self._dirs

    def list_dir(self, path: VfsPath) -> list[FileEntry]:
        prefix = self._orig_path(path)
        prefix_norm = prefix + "/" if prefix else ""

        entries: list[FileEntry] = [
            FileEntry(
                name="..",
                path=path.parent,
                is_dir=True,
                size=0,
                mtime=0.0,
                is_parent=True,
            )
        ]

        seen: set[str] = set()
        for orig_path, grp_name in self._orig_to_grp.values():
            if prefix_norm:
                if not orig_path.startswith(prefix_norm):
                    continue
                rest = orig_path[len(prefix_norm) :]
            else:
                rest = orig_path
            if not rest:
                continue
            child, sep, _remainder = rest.partition("/")
            key = child.upper()
            if key in seen:
                continue
            seen.add(key)
            if sep:
                entries.append(
                    FileEntry(name=child, path=path / child, is_dir=True, size=0, mtime=0.0)
                )
            else:
                data = self._members[grp_name]
                entries.append(
                    FileEntry(
                        name=child, path=path / child, is_dir=False, size=len(data), mtime=0.0
                    )
                )

        # Directories with no file directly inside them yet (freshly
        # mkdir'd, still empty) have no entry in _orig_to_grp to derive from
        # above -- list them from _dirs directly.
        for dir_path in self._dirs.values():
            if prefix_norm:
                if not dir_path.startswith(prefix_norm):
                    continue
                rest = dir_path[len(prefix_norm) :]
            else:
                rest = dir_path
            if not rest:
                continue
            child, _sep, _remainder = rest.partition("/")
            key = child.upper()
            if key in seen:
                continue
            seen.add(key)
            entries.append(FileEntry(name=child, path=path / child, is_dir=True, size=0, mtime=0.0))
        return entries

    def stat(self, path: VfsPath) -> StatResult:
        orig_path = self._orig_path(path)
        if not orig_path:
            return StatResult(is_dir=True, size=0, mtime=0.0)
        found = self._orig_to_grp.get(orig_path.upper())
        if found is not None:
            _, grp_name = found
            return StatResult(is_dir=False, size=len(self._members[grp_name]), mtime=0.0)
        if orig_path.upper() in self._dirs:
            return StatResult(is_dir=True, size=0, mtime=0.0)
        raise OSError(f"Not found in archive: {orig_path!r}")

    def open_read(self, path: VfsPath) -> BinaryIO:
        orig_path = self._orig_path(path)
        found = self._orig_to_grp.get(orig_path.upper())
        if found is None:
            raise OSError(f"Not found in archive: {orig_path!r}")
        _, grp_name = found
        return io.BytesIO(self._members[grp_name])

    def read_prefix(self, path: VfsPath, max_bytes: int) -> tuple[bytes, bool]:
        orig_path = self._orig_path(path)
        found = self._orig_to_grp.get(orig_path.upper())
        if found is None:
            raise OSError(f"Not found in archive: {orig_path!r}")
        _, grp_name = found
        data = self._members[grp_name]
        truncated = len(data) > max_bytes
        if truncated:
            data = data[:max_bytes]
        return data, truncated

    def _assign_grp_name(self, orig_path: str) -> str:
        """Compute a fresh GRP-compliant member name for ``orig_path``.

        Uses ``make_grp_name`` (the same routine ``archiving.py`` uses when
        building an archive from scratch) so writes through the VFS produce
        names consistent with the writer. Falls back to perturbing the
        input if the computed name genuinely collides with something
        already in the archive that ``self._name_counts`` didn't anticipate.
        """
        grp_name = make_grp_name(orig_path, self._name_counts)
        attempt = 0
        while grp_name in self._members:
            attempt += 1
            if attempt > 1000:
                raise OSError(f"Could not find a unique GRP name for {orig_path!r}")
            grp_name = make_grp_name(f"{orig_path}~{attempt}", self._name_counts)
        return grp_name

    def _grp_name_for_write(self, orig_path: str) -> str:
        """Return the GRP member name to use when writing to ``orig_path``.

        Reuses an existing mapping so repeated writes to the same path
        always land on the same member; otherwise assigns and registers a
        fresh GRP-compliant name. Registers the new entry directly (rather
        than via a full ``_build_virtual_dirs`` rebuild) so it's visible
        immediately, even before the write buffer this backs is closed and
        ``self._members`` actually gains the key.
        """
        existing = self._orig_to_grp.get(orig_path.upper())
        if existing is not None:
            return existing[1]

        grp_name = self._assign_grp_name(orig_path)
        if grp_name != orig_path:
            self._name_map[grp_name] = orig_path
        else:
            self._name_map.pop(grp_name, None)

        self._orig_to_grp[orig_path.upper()] = (orig_path, grp_name)
        parts = orig_path.split("/")
        for i in range(1, len(parts)):
            dir_path = "/".join(parts[:i])
            self._dirs.setdefault(dir_path.upper(), dir_path)
        return grp_name

    def open_write(self, path: VfsPath) -> BinaryIO:
        orig_path = self._orig_path(path)
        if not orig_path:
            raise OSError("Cannot write to archive root")
        grp_name = self._grp_name_for_write(orig_path)

        fs = self

        class _GrpWriteBuffer(io.BytesIO):
            def close(self) -> None:
                if not self.closed:
                    fs._members[grp_name] = self.getvalue()
                    fs._dirty = True
                super().close()

        return _GrpWriteBuffer()

    def mkdir(self, path: VfsPath) -> None:
        # GRP has no directory entries at all: an empty directory is purely
        # in-memory bookkeeping and (like the rest of self._dirs) is not
        # written by _rewrite -- it only persists once a file is created
        # under it, at which point _build_virtual_dirs reconstructs it from
        # that file's mapping.
        orig_path = self._orig_path(path)
        if not orig_path:
            raise OSError("Cannot create archive root directory")
        key = orig_path.upper()
        if key in self._orig_to_grp or key in self._dirs:
            raise OSError(f"Name already exists: {orig_path!r}")
        self._dirs[key] = orig_path
        self._dirty = True

    def delete(self, path: VfsPath) -> None:
        orig_path = self._orig_path(path)
        key = orig_path.upper()
        found = self._orig_to_grp.get(key)
        if found is None:
            if key in self._dirs:
                del self._dirs[key]
                self._dirty = True
                return
            raise OSError(f"Not found in archive: {orig_path!r}")
        _, grp_name = found
        del self._members[grp_name]
        self._name_map.pop(grp_name, None)
        self._build_virtual_dirs()
        self._dirty = True

    def rename(self, src: VfsPath, dst: VfsPath) -> None:
        src_orig = self._orig_path(src)
        dst_orig = self._orig_path(dst)
        if not dst_orig:
            raise OSError("Cannot rename to the archive root")

        src_key = src_orig.upper()
        found = self._orig_to_grp.get(src_key)
        if found is None:
            # Renaming a synthetic (empty) directory -- no member/mapping involved.
            if src_key not in self._dirs:
                raise OSError(f"Source not found in archive: {src_orig!r}")
            dst_key = dst_orig.upper()
            if dst_key in self._orig_to_grp or dst_key in self._dirs:
                raise OSError(f"Destination already exists: {dst_orig!r}")
            del self._dirs[src_key]
            self._dirs[dst_key] = dst_orig
            self._dirty = True
            return

        _, grp_name = found
        dst_key = dst_orig.upper()
        existing_dst = self._orig_to_grp.get(dst_key)
        if (existing_dst is not None and existing_dst[1] != grp_name) or dst_key in self._dirs:
            raise OSError(f"Destination already exists: {dst_orig!r}")

        # Compute (and register, if needed) the GRP name for the
        # destination's original path, so a rename to a long/nested name is
        # truncated and mapped exactly like a fresh write would be.
        new_grp_name = self._grp_name_for_write(dst_orig)
        if new_grp_name != grp_name:
            self._members[new_grp_name] = self._members.pop(grp_name)
            self._name_map.pop(grp_name, None)
        self._build_virtual_dirs()
        self._dirty = True

    def close(self) -> None:
        if self._dirty:
            self._rewrite()
            self._dirty = False


def open_fs(host_fs: FileSystem, host_path: VfsPath) -> GrpFileSystem:
    """Entry point for MountManager — returns a GrpFileSystem for the archive."""
    real_path = materialize(host_fs, host_path)
    return GrpFileSystem(real_path, host_path)
