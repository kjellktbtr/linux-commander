---
title: VFS — Virtual File System
type: entity
sources:
  - linux_commander/vfs.py
  - linux_commander/plugins/__init__.py
  - CONTRIBUTING.md
related:
  - "[[plugins]]"
  - "[[archiving]]"
  - "[[operations]]"
  - "[[mount_manager]]"
  - "[[local_fs]]"
created: 2026-07-17
updated: 2026-07-17
confidence: high
---

# VFS — Virtual File System Abstraction

`linux_commander/vfs.py` is the **single filesystem abstraction** for the entire application. All I/O goes through `FileSystem` methods; never call methods directly on a `VfsPath`.

## Core Types

### `FileSystem` (ABC)

```python
class FileSystem(ABC):
    # Abstract — MUST implement
    @abstractmethod
    def list_dir(self, path: VfsPath) -> list[VfsPath]: ...
    @abstractmethod
    def list_dir_flat(self, path: VfsPath) -> list[VfsPath]: ...
    @abstractmethod
    def stat(self, path: VfsPath) -> FileStat: ...
    @abstractmethod
    def open_read(self, path: VfsPath) -> BinaryIO: ...

    # Optional — override for writable backends
    writable: ClassVar[bool] = False
    def open_write(self, path: VfsPath, mode: str = "wb") -> BinaryIO: ...
    def mkdir(self, path: VfsPath, parents: bool = False) -> None: ...
    def delete(self, path: VfsPath) -> None: ...
    def rename(self, src: VfsPath, dst: VfsPath) -> None: ...

    # Optional helpers
    def realpath(self, path: VfsPath) -> Path: ...  # real local path if backed by real file
    def read_prefix(self, path: VfsPath, n: int) -> bytes: ...  # first N bytes (quick peek)
```

### `FileStat`

```python
@dataclass(frozen=True)
class FileStat:
    name: str
    path: VfsPath
    size: int
    mtime: float          # Unix timestamp
    is_dir: bool
    is_file: bool
    is_symlink: bool = False
    mode: int | None = None    # POSIX mode bits
    uid: int | None = None
    gid: int | None = None
    nlink: int | None = None
```

### `VfsPath`

```python
@dataclass(frozen=True)
class VfsPath:
    fs: FileSystem        # owning filesystem
    path: str             # path within that filesystem (POSIX-style, "/"-separated)
    # Properties delegate to fs: .name, .parent, .exists(), .stat(), etc.
```

**Rule**: Always use `path.fs.open_read(path)`, `path.fs.stat(path)`, etc. Never call `path.fs.list_dir(path.path)` directly — use the `VfsPath` methods.

## Built-in Filesystems

| Class | Purpose |
|-------|---------|
| `LocalFileSystem` | Native OS filesystem (root FS) |
| `MountManager` | Ref-counted mount point manager for archives/protocols |

## MountManager — Shared Archive/Protocol Backends

```python
class MountManager:
    def mount(self, host_fs: FileSystem, path: VfsPath) -> FileSystem:
        # Returns a shared backend; refcounted so both panels can browse same archive
    def unmount(self, fs: FileSystem) -> None:
        # Decrements refcount; destroys backend when count reaches 0
```

- Keyed by `(host_fs, path)` — same archive opened in both panels shares one backend
- Enter (Enter key) → `mount()`; Leave (Backspace/..) → `unmount()`
- Plugin's `open_fs(host_fs, path)` returns the backend FS; MountManager wraps it

## Plugin Integration

- `plugins._discover()` builds three maps:
  - `EXTENSION_MAP: {".ext": open_fs_fn}` — Enter-to-browse archives
  - `SCHEME_MAP: {"ftp": connect_fs_fn, "sftp": ...}` — Connections manager
  - `VIEW_EXTENSION_MAP: {".xlsx": read_document_fn}` — viewer document preview

- Lookup order: longest extension match first (`.tar.gz` before `.gz`)

## Usage Patterns

```python
# Panel listing
entries = panel.path.fs.list_dir(panel.path)

# Viewer opening a file
with panel.path.fs.open_read(panel.path) as f:
    data = f.read()

# Copy to archive (F5)
with src_fs.open_read(src_path) as r, dst_fs.open_write(dst_path) as w:
    shutil.copyfileobj(r, w)

# Check if writable
if dst_fs.writable:
    dst_fs.mkdir(new_dir_path)
```

## Cross-Reference

- [[plugins]] — how archive/protocol plugins register `open_fs` / `connect_fs`
- [[archiving]] — compression dialog writes via VFS
- [[operations]] — copy/move/delete use VFS methods
- [[mount_manager]] — shared backend lifecycle
- [[local_fs]] — LocalFileSystem implementation