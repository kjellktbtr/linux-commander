"""Bzip2 compression codec."""

from __future__ import annotations

import bz2
import pathlib
import shutil

from linux_commander.codecs import Codec


class _Bz2Codec(Codec):
    @property
    def name(self) -> str:
        return "bz2"

    def compress(self, src: pathlib.Path, dst: pathlib.Path, level: int) -> None:
        with open(src, "rb") as f_in:
            with bz2.open(dst, "wb", compresslevel=max(1, min(level, 9))) as f_out:
                shutil.copyfileobj(f_in, f_out)


codec_class = _Bz2Codec
