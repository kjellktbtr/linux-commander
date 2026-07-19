# Improvements

## Documentation
- [ ] Create entity pages for all modules in CLAUDE.md (vfs, plugins, archiving, settings, search_engine, operations, syntax, compression_dialog, search_dialog, ftp_dialog, file_info)
- [x] Update CLAUDE.md with comprehensive project context from README.md and CONTRIBUTING.md — docs/wiki/log.md#2026-07-17
- [x] Update wiki index.md with new entity links — docs/wiki/log.md#2026-07-17
- [x] Add contributing-summary source page — docs/wiki/log.md#2026-07-17

## Architecture
- [ ] Implement Windows volume enumeration in volumes.py
- [ ] Implement macOS volume enumeration in volumes.py
- [ ] Add Windows file association handling in platform_util.py
- [ ] Add macOS file association handling in platform_util.py

## Features
- [ ] Add trash/recycle bin support for Delete (F8)
- [ ] Add file/folder properties dialog (beyond Shift+F3)
- [ ] Add split view (horizontal/vertical) for panels
- [ ] Add directory hotlist/bookmarks (Ctrl+1..9)
- [ ] Add command history search (Ctrl+R in command line)
- [ ] Add batch rename tool (Operations menu)
- [ ] Add checksum verification (sfv/md5/sha1 files)

## Plugin System
- [ ] Add plugin API versioning
- [ ] Add plugin manifest schema validation
- [ ] Create plugin development documentation

## Testing
- [ ] Add GUI integration tests with headless Xvfb
- [ ] Add VFS plugin test fixtures for all archive formats
- [ ] Add property-based tests for grp_names truncation

## Performance
- [ ] Profile and optimize large directory listing (>10k entries)
- [ ] Add virtualized Treeview for panel (load on demand)
- [ ] Optimize search engine for huge archives

## Accessibility
- [ ] Add screen reader support (ttkbootstrap accessibility)
- [ ] Add high contrast theme option
- [ ] Add keyboard navigation audit