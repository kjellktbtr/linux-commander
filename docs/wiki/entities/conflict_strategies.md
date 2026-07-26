---
title: Conflict Strategies — Plugin-based Resolution
type: entity
sources:
  - linux_commander/conflict_strategies/__init__.py
  - linux_commander/conflict_strategies/skip_strategy.py
  - linux_commander/conflict_strategies/replace_strategy.py
  - linux_commander/conflict_strategies/replace_if_newer_strategy.py
  - linux_commander/conflict_strategies/replace_if_different_size_strategy.py
  - linux_commander/conflict_strategies/compare_strategy.py
related:
  - "[[operations]]"
  - "[[vfs]]"
created: 2026-07-22
updated: 2026-07-22
confidence: high
---

# Conflict Strategies — Plugin-based Resolution

`linux_commander/conflict_strategies/` is an auto-discovered plugin package for copy/move conflict resolution strategies. It replaces the hardcoded `if/elif` conflict dispatch in `operations_controller.py` with a plugin-based architecture following the **Strategy Pattern** and **Open/Closed Principle (OCP)**.

## Architecture

### `ConflictInfo`

Frozen dataclass holding conflict metadata:

```python
@dataclass(frozen=True)
class ConflictInfo:
    source: VfsPath
    dest: VfsPath
    source_size: int
    dest_size: int
    source_mtime: float
    dest_mtime: float
```

### `ConflictStrategy(ABC)`

Base class that strategy plugins must subclass:

```python
class ConflictStrategy(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...       # Unique identifier (e.g. 'skip', 'replace')
    @property
    @abstractmethod
    def label(self) -> str: ...      # Display label (e.g. 'Skip', 'Replace')
    @abstractmethod
    def should_delete(self, conflict: ConflictInfo, dest_fs: WritableFileSystem) -> bool: ...
```

### Discovery

Uses `pkgutil.iter_modules` to auto-discover all strategy plugins. Results are cached after the first call. Broken modules are silently skipped.

```python
def discover_strategies() -> dict[str, ConflictStrategy]: ...
def get_strategy(name: str) -> ConflictStrategy | None: ...
def reset_cache() -> None: ...  # For testing
```

### Built-in Strategies

| Module | Name | Label | Behavior |
|--------|------|-------|----------|
| `skip_strategy.py` | `skip` | `Skip` | Never delete dest (skip the file) |
| `replace_strategy.py` | `replace` | `Replace` | Always delete dest (overwrite) |
| `replace_if_newer_strategy.py` | `replace_if_newer` | `Replace if Newer` | Delete dest only if source mtime > dest mtime |
| `replace_if_different_size_strategy.py` | `replace_if_different_size` | `Replace if Different Size` | Delete dest only if sizes differ |
| `compare_strategy.py` | `compare` | `Compare` | Opens diff viewer, then skips (non-destructive) |

## Usage

`OperationsController._copy_or_move()` dispatches conflict resolution to the strategy plugin system. The `ConflictResolution` enum values use `.name.lower()` to match the strategy plugin names.

## Adding New Strategies

Drop a module into `linux_commander/conflict_strategies/` exposing a `strategy_class` attribute:

```python
from linux_commander.conflict_strategies import ConflictStrategy, ConflictInfo
from linux_commander.vfs import WritableFileSystem

class MyStrategy(ConflictStrategy):
    @property
    def name(self) -> str:
        return "my_strategy"

    @property
    def label(self) -> str:
        return "My Strategy"

    def should_delete(self, conflict: ConflictInfo, dest_fs: WritableFileSystem) -> bool:
        # Custom logic
        return True
```

## Cross-Reference

- [[operations]] — uses strategy dispatch for conflict resolution
- [[vfs]] — `WritableFileSystem` is the dest filesystem type
