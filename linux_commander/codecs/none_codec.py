"""No compression codec (passthrough)."""

from __future__ import annotations

import os
import pathlib

from linux_commander.codecs import Codec


class _NoneCodec(Codec):
    @property
    def name(self) -> str:
        return "none"

    def compress(self, src: pathlib.Path, dst: pathlib.Path, level: int) -> None:
        os.replace(src, dst)


codec_class = _NoneCodec
