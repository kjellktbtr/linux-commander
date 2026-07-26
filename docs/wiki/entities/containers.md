---
title: Containers — Plugin-based Archive Builders
type: entity
sources:
  - linux_commander/containers/__init__.py
  - linux_commander/containers/zip_container.py
  - linux_commander/containers/tar_container.py
  - linux_commander/containers/grp_container.py
  - linux_commander/containers/sevenzip_container.py
  - linux_commander/containers/iso_container.py
related:
  - "[[archiving]]"
  - "[[codecs]]"
  - "[[plugins]]"
created: 2026-07-22
updated: 2026-07-22
confidence: high
---

# Containers — Plugin-based Archive Builders

`linux_commander/containers/` is an auto-discovered plugin package for archive container builders. It replaces the hardcoded container builder functions in `archiving.py` with a plugin-based architecture following the **Open/Closed Principle (OCP)**.

## Architecture

### `Container(ABC)`

Base class that container plugins must subclass:

```python
class Container(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...              # Unique identifier (e.g. 'zip', 'tar')
    @property
    @abstractmethod
    def extension(self) -> str: ...         # File extension (e.g. '.zip', '.tar')
    @abstractmethod
    def build(self, sources, dest, local_fs, on_progress, should_cancel) -> list[OperationError]: ...
    @property
    def available(self) -> bool: ...        # True by default; override for optional deps
```

### Discovery

Uses `pkgutil.iter_modules` to auto-discover all container plugins. Results are cached after the first call. Broken modules are silently skipped. Containers with `available=False` are excluded from discovery.

```python
def discover_containers() -> dict[str, Container]: ...
def get_container(name: str) -> Container | None: ...
def reset_cache() -> None: ...  # For testing
```

### Built-in Containers

| Module | Name | Extension | Dependency |
|--------|------|-----------|------------|
| `zip_container.py` | `zip` | `.zip` | stdlib (always) |
| `tar_container.py` | `tar` | `.tar` | stdlib (always) |
| `grp_container.py` | `grp` | `.grp` | stdlib (always) |
| `sevenzip_container.py` | `7z` | `.7z` | `py7zr` (optional) |
| `iso_container.py` | `iso` | `.iso` | `libarchive-c` (optional) |

### Shared Infrastructure

`_iter_sources()` in `archiving.py` is the shared driver that handles local/remote source splitting, progress reporting, and cancellation. Container plugins call it with format-specific `add_local_file`, `add_local_dir`, and `add_bytes` callbacks.

## Usage

`archiving.py` derives `CONTAINERS` and `CONTAINER_EXTENSIONS` from `discover_containers()`. `compress_sources()` calls `get_container(name).build()` instead of dispatching through a hardcoded dict.

## Adding New Containers

Drop a module into `linux_commander/containers/` exposing a `container_class` attribute:

```python
from linux_commander.containers import Container
from linux_commander.archiving import _iter_sources

class MyContainer(Container):
    @property
    def name(self) -> str:
        return "my_fmt"

    @property
    def extension(self) -> str:
        return ".myf"

    def build(self, sources, dest, local_fs, on_progress, should_cancel):
        # Use _iter_sources with format-specific callbacks
        ...

container_class = MyContainer
```

For optional dependencies, guard the import and set `container_class = None` when unavailable.

## Cross-Reference

- [[archiving]] — delegates container building to this plugin system
- [[codecs]] — sibling plugin system for compression codecs
- [[plugins]] — similar auto-discovery pattern
