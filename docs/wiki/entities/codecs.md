---
title: Codecs — Plugin-based Compression
type: entity
sources:
  - linux_commander/codecs/__init__.py
  - linux_commander/codecs/none_codec.py
  - linux_commander/codecs/gzip_codec.py
  - linux_commander/codecs/bz2_codec.py
  - linux_commander/codecs/lzma_codec.py
  - linux_commander/codecs/zstd_codec.py
related:
  - "[[archiving]]"
  - "[[plugins]]"
created: 2026-07-22
updated: 2026-07-22
confidence: high
---

# Codecs — Plugin-based Compression

`linux_commander/codecs/` is an auto-discovered plugin package for compression codecs. It replaces the hardcoded codec wrapping logic in `archiving.py` with a plugin-based architecture following the **Open/Closed Principle (OCP)**.

## Architecture

### `Codec(ABC)`

Base class that codec plugins must subclass:

```python
class Codec(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...       # Unique identifier (e.g. 'gz', 'bz2')
    @abstractmethod
    def compress(self, src: Path, dst: Path, level: int) -> None: ...
```

### Discovery

Uses `pkgutil.iter_modules` to auto-discover all codec plugins. Results are cached after the first call. Broken modules are silently skipped.

```python
def discover_codecs() -> dict[str, Codec]: ...
def get_codec(name: str) -> Codec | None: ...
def reset_cache() -> None: ...  # For testing
```

### Built-in Codecs

| Module | Name | Format | Extension |
|--------|------|--------|-----------|
| `none_codec.py` | `none` | No compression (moves file) | none |
| `gzip_codec.py` | `gz` | gzip | `.gz` |
| `bz2_codec.py` | `bz2` | bzip2 | `.bz2` |
| `lzma_codec.py` | `xz` | xz/lzma | `.xz` |
| `zstd_codec.py` | `zst` | zstd | `.zst` |

### `compress_file()`

Convenience function that compresses a file using a named codec. Deletes the source after successful compression (it is always a temp file).

```python
def compress_file(
    src: Path,
    dst: Path,
    codec_name: str,
    level: int,
) -> None: ...
```

**Note**: The `none` codec uses `os.replace()` to move the file, so the wrapper checks `if src.exists()` before attempting `os.unlink(src)` to avoid double-deletion.

## Usage

`archiving.py` delegates `_wrap_codec()` to `compress_file()` from the codecs plugin system. The stdlib imports (`bz2`, `gzip`, `lzma`, `shutil`) were removed from `archiving.py` after this refactor.

## Adding New Codecs

Drop a module into `linux_commander/codecs/` exposing a `codec_class` attribute:

```python
from linux_commander.codecs import Codec
from pathlib import Path

class MyCodec(Codec):
    @property
    def name(self) -> str:
        return "my_fmt"

    def compress(self, src: Path, dst: Path, level: int) -> None:
        # Compress src into dst
        ...
```

## Cross-Reference

- [[archiving]] — delegates codec wrapping to this plugin system
- [[plugins]] — similar auto-discovery pattern
