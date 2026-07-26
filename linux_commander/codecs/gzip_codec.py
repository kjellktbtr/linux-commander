"""Gzip compression codec."""

from __future__ import annotations

import gzip
import pathlib
import shutil

from linux_commander.codecs import Codec


class _GzipCodec(Codec):
    @property
    def name(self) -> str:
        return "gz"

    def compress(self, src: pathlib.Path, dst: pathlib.Path, level: int) -> None:
        with open(src, "rb") as f_in:
            with gzip.open(dst, "wb", compresslevel=max(0, min(level, 9))) as f_out:
                shutil.copyfileobj(f_in, f_out)


codec_class = _GzipCodec
