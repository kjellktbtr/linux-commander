---
title: Sort Criteria — Plugin-based File Sorting
type: entity
sources:
  - linux_commander/sort_criteria/__init__.py
  - linux_commander/sort_criteria/name_criteria.py
  - linux_commander/sort_criteria/size_criteria.py
  - linux_commander/sort_criteria/mtime_criteria.py
  - linux_commander/sort_criteria/extension_criteria.py
related:
  - "[[fs]]"
  - "[[panel]]"
created: 2026-07-22
updated: 2026-07-22
confidence: high
---

# Sort Criteria — Plugin-based File Sorting

`linux_commander/sort_criteria/` is an auto-discovered plugin package for file sorting criteria. It replaces the hardcoded `if/elif` sort-key dispatch in `fs.py` with a plugin-based architecture following the **Open/Closed Principle (OCP)**.

## Architecture

### `SortCriterion(ABC)`

Base class that criterion plugins must subclass:

```python
class SortCriterion(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...       # Unique identifier (e.g. 'name', 'size')
    @property
    @abstractmethod
    def label(self) -> str: ...      # Display label (e.g. 'Name', 'Size')
    @abstractmethod
    def key(self, entry: FileEntry) -> Any: ...  # Sortable value for an entry
```

### Discovery

Uses `pkgutil.iter_modules` to auto-discover all criterion plugins. Results are cached after the first call. Broken modules are silently skipped.

```python
def discover_criteria() -> dict[str, SortCriterion]: ...
def get_criterion(name: str) -> SortCriterion | None: ...
def reset_cache() -> None: ...  # For testing
```

### Built-in Criteria

| Module | Name | Label | Sort Key |
|--------|------|-------|----------|
| `name_criteria.py` | `name` | `Name` | Case-insensitive name, dirs before files |
| `size_criteria.py` | `size` | `Size` | File size in bytes (dirs=0) |
| `mtime_criteria.py` | `mtime` | `Date` | Modification time as float |
| `extension_criteria.py` | `extension` | `Extension` | File extension (case-insensitive) |

### `sort_entries()`

Convenience function in `__init__.py` that sorts entries using a named criterion. Directories are always sorted before files, and `..` is pinned at index 0.

```python
def sort_entries(
    entries: list[FileEntry],
    criterion_name: str = "name",
    reverse: bool = False,
) -> list[FileEntry]: ...
```

## Usage

`fs.py` delegates `sort_entries()` to the plugin system. `panel.py` fetches sort labels from the plugin registry for header display.

## Adding New Criteria

Drop a module into `linux_commander/sort_criteria/` exposing a `criterion_class` attribute:

```python
from linux_commander.sort_criteria import SortCriterion
from linux_commander.vfs import FileEntry

class MyCriterion(SortCriterion):
    @property
    def name(self) -> str:
        return "my_key"

    @property
    def label(self) -> str:
        return "My Label"

    def key(self, entry: FileEntry) -> str:
        return entry.name  # example
```

## Cross-Reference

- [[fs]] — delegates sorting to this plugin system
- [[panel]] — fetches sort labels from the registry
