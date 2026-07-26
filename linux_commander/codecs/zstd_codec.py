"""Zstandard compression codec (optional dependency)."""

from __future__ import annotations

import pathlib
import shutil

from linux_commander.codecs import Codec

try:
    import zstandard as _zstd_mod  # type: ignore[import-not-found]

    _HAS_ZSTD = True
except ImportError:
    _zstd_mod = None  # type: ignore[assignment]
    _HAS_ZSTD = False

if _HAS_ZSTD:

    class _ZstdCodec(Codec):
        @property
        def name(self) -> str:
            return "zst"

        def compress(self, src: pathlib.Path, dst: pathlib.Path, level: int) -> None:
            with open(src, "rb") as f_in:
                with _zstd_mod.open(  # type: ignore[union-attr]
                    dst, "wb", level=max(1, min(level, 22))
                ) as f_out:
                    shutil.copyfileobj(f_in, f_out)

    codec_class = _ZstdCodec  # type: ignore[name-defined]
