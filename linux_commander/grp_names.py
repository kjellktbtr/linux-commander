"""Shared name-truncation logic for the Build Engine GRP format.

GRP is a flat archive format: every member name is limited to 12 ASCII bytes
(the 8.3-style DOS convention). This module is the single source of truth for
turning an arbitrary relative path (possibly with '/'-separated directory
components and a name longer than 12 bytes) into a GRP-compliant name, so the
archive writer (``compression.py``) and the VFS reader/writer
(``plugins/grp_plugin.py``) always agree on the mapping.

Public API:
    make_grp_name(arcname, name_counts) -> str
    truncate_dir_component(name) -> str
    GRP_MAGIC, GRP_ENTRY, GRP_COUNT, GRP_MAX_NAME_LEN
"""

from __future__ import annotations

import struct

GRP_MAGIC = b"KenSilverman"
GRP_ENTRY = struct.Struct("<12sI")  # 12-byte name + 4-byte size (LE)
GRP_COUNT = struct.Struct("<I")
GRP_MAX_NAME_LEN = 12


def truncate_dir_component(name: str) -> str:
    """Truncate a single directory-name component the same way ``make_grp_name`` does.

    Any extension-like suffix (text after the last '.') is dropped before
    truncating, matching the non-leaf branch of ``make_grp_name`` below. This
    is the one place that rule lives, so callers that need to guess a GRP
    directory name for a component that isn't already in a mapping (e.g. the
    VFS plugin reconstructing virtual directories) stay consistent with the
    archive writer.
    """
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return stem[:6].upper()


def make_grp_name(arcname: str, name_counts: dict[str, int]) -> str:
    """Convert a relative path to a GRP-compliant name (12 chars max).

    Strategy: Use fixed 6 chars per directory component (depth-independent).
    - 1 component (root file): full 12 chars for filename
    - 2 components (dir/file): 6 + 1 + 5 = 12 chars
    - 3+ components: truncates to 12, deeper levels lose fidelity

    ``name_counts`` is mutated to track collisions on the *file* name so that
    repeated calls (e.g. across an archive) assign ``~1``, ``~2``, ... suffixes
    to colliding truncated names.
    """
    parts = arcname.split("/")
    grp_parts: list[str] = []

    for i, part in enumerate(parts):
        is_last = i == len(parts) - 1

        # Split into stem and extension
        if "." in part:
            stem, ext = part.rsplit(".", 1)
            ext = "." + ext[:3]
        else:
            stem, ext = part, ""

        if is_last:
            # File component: use up to 8 chars for stem
            base_stem = stem[:8].upper()
            if ext:
                ext = ext.upper()

            base_name = f"{base_stem}{ext}"
            if base_name in name_counts:
                name_counts[base_name] += 1
                count = name_counts[base_name]
                suffix = f"~{count}"
                available = 8 - len(ext) - len(suffix)
                if available < 1:
                    available = 1
                grp_part = f"{base_stem[:available]}{suffix}{ext}"
            else:
                name_counts[base_name] = 0
                grp_part = base_name
        else:
            # Directory component: consistent 6-char truncation
            grp_part = truncate_dir_component(part)

        grp_parts.append(grp_part)

    grp_name = "/".join(grp_parts)
    return grp_name[:GRP_MAX_NAME_LEN]
