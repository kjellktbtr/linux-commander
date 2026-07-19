# linux-commander

A dual-pane "orthodox file manager" in the tradition of Norton Commander, Midnight
Commander, and Total Commander — built with **plain tkinter** (no third-party GUI
libraries). Beyond classic dual-pane browsing it ships a full built-in file viewer/editor
(hexdump, JSON, CSV/table, strings, syntax highlighting, spreadsheet/document preview),
read/write archive browsing for a dozen formats, ChaCha20-Poly1305 file encryption,
FTP/SFTP remote connections, and a background multi-criteria file search with archive
descent.

Two file panels sit side by side. One panel is "active" at a time (switched with Tab);
navigation, tagging, and the classic F-key command bar all act on the active panel.

## Features

**Dual-pane browsing** — Name/Size/Modified columns per panel, each with its own volume
bar; keyboard navigation (arrows, PgUp/PgDn, Home/End, Enter/Backspace); Insert-to-tag
with cursor auto-advance; `+`/`-`/`*` pattern select/deselect/invert with a glob or
regex pattern dialog; quick search (**Alt+Shift+&lt;char&gt;**) to jump to a name by prefix;
column-header click to sort; right-click/drag to tag a range of files; a bottom command
prompt (**Ctrl+X**) for running shell commands with history.

**Built-in viewer / editor (F3 / F4)** — a single unified window used both read-only and
editable, with: regex/case-insensitive search (Ctrl+F); a hexdump view; JSON
pretty-printing; a CSV/TSV table view with delimiter auto-detection; a background
printable-strings scan; syntax highlighting for 8 languages (Bash, Batch, C, JSON,
Markdown, Python, TOML, YAML — easy to add more, see
[CONTRIBUTING.md](CONTRIBUTING.md)); and, with the `documents` extra, read-only document
preview for spreadsheets and tabular data (`.xlsx`/`.xlsm`/`.xls`/`.ods`/`.parquet`,
shown in the table view, capped at 5000 rows) and Word documents (`.docx`, extracted as
plain text).

**Archives** — press Enter on an archive to browse it as a folder right inside the
panel, with a refcounted shared backend so both panels can browse the same archive at
once. `.zip`/`.tar`(`.gz`/`.bz2`/`.xz`)/`.grp` are read **and** write out of the box;
`.7z` (read+write) and `.rar`/`.iso`/`.cpio`/`.a`/`.ar`/`.xar`/`.lha`/`.lzh` (read-only)
need the `archives` extra. The **Shift+F5** compression dialog builds new archives from
any of 5 containers (zip/tar/grp/7z/iso) crossed with any of 5 codecs
(none/gz/bz2/xz/zst) — every combination is valid, including double-compression like
`.7z.xz` — plus an optional final encryption stage.

**Encryption** — ChaCha20-Poly1305 `.crp` files (the `crypto` extra), by password or a
named key stored in the config file. Available as standalone Operations menu commands,
as the compression dialog's final stage, or just by pressing Enter on a `.crp` file
(prompts for the credential, then drops you into the decrypted archive or file).

**Remote connections** — built-in FTP, and SFTP (password or private-key auth) with the
`ssh` extra. A Connections manager (**File > Connections...**) saves, edits, and connects
multiple sessions; `ftp://`/`sftp://` URLs also work directly from the volume chooser.

**Search (Alt+F7 / Shift+F7)** — combine name (glob or regex), size, modification date,
and content (plain string, regex, or hex bytes) criteria, optionally descending into
archives. Runs on a background thread, is cancellable mid-search, and streams matches
into a live, sortable results panel while the rest of the app stays usable.

**File info (Shift+F3)** — file type, POSIX permissions/owner/group/links, and
MD5/SHA1/SHA256 checksums, computed on a background thread (works on remote/archive
files too).

**Image viewer** — F3 or Enter on an image opens a scrollable, auto-fit-to-window viewer;
Left/Right navigate every image in the directory, Shift+Left/Right navigate only images
matching the current extension.

**Everything else** — Base64 encode/decode (Operations menu, always available); a
Theme/Font picker; per-file-op threaded progress dialogs with cancel; settings (fonts,
theme, stored keys, saved sessions, per-panel state) persisted to a config file; an
auto-discovered plugin system for all of the above (see
[Plugin architecture](#plugin-architecture) below).

## Getting started

**Prerequisites:**
- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Tk installed at the system level (tkinter's `_tkinter` module links against it). On
  Arch/Manjaro: `sudo pacman -S tk`. Verify with:
  ```bash
  python3 -c "import tkinter; print(tkinter.TkVersion)"
  ```

**Run it:**

```bash
uv sync
uv run linux-commander
# or
uv run python -m linux_commander
```

**Get every feature:** several capabilities above (archive formats, encryption, SFTP,
document preview) depend on third-party packages that aren't installed by default — the
app degrades gracefully (the feature just doesn't register) when a package is missing.
Install them all at once with:

```bash
uv sync --all-extras
```

...or pick specific extras:

| Extra | Packages | Enables |
|---|---|---|
| `archives` | `py7zr`, `rarfile`, `libarchive-c` | `.7z`/`.rar` browsing; read-only `.iso`/`.cpio`/`.a`/`.ar`/`.xar`/`.lha`/`.lzh`; creating new `.iso` images |
| `crypto` | `cryptography` | The Encrypt/Decrypt operation and `.crp` browsing |
| `ssh` | `paramiko` | SFTP sessions in the Connections manager |
| `documents` | `openpyxl`, `python-docx`, `pandas`, `xlrd`, `odfpy` | Viewer preview for `.xlsx`/`.xlsm`/`.ods`/`.xls`/`.parquet` (table, see note below) and `.docx` (text) |

```bash
uv sync --extra archives --extra crypto --extra ssh --extra documents
```

Notes:
- `libarchive-c` also needs the system `libarchive` shared library (commonly already
  present on Linux; install via your package manager if `import libarchive` fails after
  installing the extra).
- `.parquet` preview additionally needs `pyarrow` or `fastparquet` (not bundled in the
  `documents` extra since `pyarrow` is a heavy dependency) — without one, opening a
  `.parquet` file falls back to the raw viewer with an explanatory error.
- The `zst` compression codec is separate from all of the above: it comes from the
  `compression.zstd` standard-library module added in Python 3.14, so it appears
  automatically on a new-enough interpreter and can't be pip-installed.

To check which extras are currently installed (and install what's missing), use the
bundled helper — also reachable from **File > Optional Dependencies...**:

```bash
uv run linux-commander-install-extras            # report only
uv run linux-commander-install-extras --install   # also install missing extras
```

## Detailed reference

### Main window — F-key bar

| Key | Action | Notes |
|---|---|---|
| F1 | Help | Shows the built-in keybindings cheat-sheet |
| F2 | *(unused)* | Reserved slot (classic NC "User menu") |
| F3 | View | Cursor on a directory enters it; on an image, opens the image viewer; otherwise opens the built-in read-only viewer |
| F4 | Edit | Opens the built-in editor; falls back to the read-only viewer if the filesystem isn't writable (archives, some remote targets) |
| F5 | Copy | Tagged files (or the cursor file) to the other panel's directory, or a typed path |
| F6 | Move | Same, or renames in place if you type just a new filename |
| F7 | MkDir | Create a new directory in the active panel |
| F8 | Delete | Permanently delete, after confirmation |
| F9 | Menu | Placeholder pulldown; active items are Search... (Alt+F7) and Command Prompt (Ctrl+X) |
| F10 | Quit | With confirmation |

F5/F6/F8 (and Shift+F5 compress) run on a background thread with a cancellable progress
dialog. All F-key operations act on the tagged set, or just the cursor file if nothing
is tagged.

### Main window — other global shortcuts

| Key | Action |
|---|---|
| Alt+F1 / Alt+F2 | Choose a volume for the left / right panel (includes a "Connect to FTP..." entry) |
| Shift+F3 | File Info — type, permissions, checksums (see below) |
| Shift+F4 | New File — create and open in the editor |
| Shift+F5 | Compress — open the compression dialog (see Archive & compression) |
| Alt+F7 / Shift+F7 | Search — open the Find Files dialog (see Search) |
| Ctrl+H | Toggle hidden (dotfile) visibility in the active panel |
| Ctrl+R | Refresh the active panel's listing |
| Ctrl+F3 / Ctrl+F5 / Ctrl+F6 | Sort by name / date / size (press again to reverse) |
| Ctrl+Q | Quit |
| Ctrl+X | Focus the command prompt |
| Escape | Exit search-results mode, if the active panel is showing search results |

### Panel navigation & selection

| Key | Action |
|---|---|
| Up / Down / PgUp / PgDn / Home / End | Move the cursor |
| Enter / Right | Activate: enter a directory, or open a file (OS default app, falling back to the built-in viewer) |
| Left / Backspace | Go to the parent directory |
| Insert | Tag/untag the cursor file, move down |
| `+` / `-` / `*` | Tag / untag by glob-or-regex pattern / invert selection |
| Tab | Switch the active panel |

Mouse: clicking a column header sorts by that column; right-click (or right-drag) tags
or untags a file or a contiguous range; double-click activates like Enter.

### Quick search (active panel)

| Key | Action |
|---|---|
| Alt+Shift+&lt;char&gt; | Append to the quick-search buffer, jump to the first matching name (buffer auto-clears after ~1s) |
| Alt+Shift+Backspace | Delete the last character from the buffer |
| Alt+Shift+Escape | Clear the buffer |

### Command line (always visible, bottom of window)

| Key | Action |
|---|---|
| Any letter/digit typed in a panel | Focus the command line and start typing (excludes `+`/`-`/`*` and anything with Ctrl/Alt held) |
| Ctrl+X | Focus the command line |
| Enter | Run the command in a terminal |
| Up / Down | Navigate command history |
| Escape | Clear the command line, return focus to the panel |

### Menu bar

**File**: Theme... · Font... · Editor Font... · Viewer Font... · Connections... ·
Command Settings... · Optional Dependencies... · Command Prompt (Ctrl+X) · Quit (Ctrl+Q)

**View**: Show Hidden Files · Refresh · Sort by Name/Date/Size/Extension · Show Icons ·
Show Extension Column · Command Prompt

**Operations** *(shown only when at least one operation is registered)*: one item per
auto-discovered `FileOperation` — Base64 Encode/Decode is always present (stdlib);
Encrypt/Decrypt appears only with the `crypto` extra installed. None of these have menu
accelerators; the F-key operations (Copy/Move/etc.) likewise exist only as key-bar
buttons and global bindings, not menu items.

### Viewer / Editor (F3 / F4) window

F3 opens a file read-only; F4 opens it editable. Pressing **F4 inside the viewer**
promotes it to edit mode without reopening (except for document previews, which are
always read-only — see below).

| Key | Action |
|---|---|
| Escape / F3 / F10 | Close the window |
| F4 | Enable editing (promote from read-only) |
| Ctrl+F | Show the Find bar |
| Ctrl+A | Select all |
| Ctrl+C | Copy |
| Ctrl+N | New *(edit mode)* |
| Ctrl+O | Open... *(edit mode)* |
| Ctrl+S / F2 | Save *(edit mode)* |
| F12 | Save As... *(edit mode)* |
| Ctrl+Z | Undo *(edit mode)* |
| Ctrl+X | Cut *(edit mode)* |
| Ctrl+V | Paste *(edit mode)* |

Find bar: **Enter** finds next, **Shift+Enter** finds previous, **Escape** hides it.

**View menu**: Status Bar · Word Wrap · Hexdump *(disabled while CSV/Strings is active)*
· JSON Pretty-Print *(disabled while Hex/CSV/Strings is active)* · CSV Table with a CSV
Separator submenu (Auto/Comma/Semicolon/Tab) · Strings with a Min Length submenu
(3/4/6/8/16) · Font...

**Syntax menu**: Auto (by extension) plus one radiobutton per loaded language; disabled
entirely while Hexdump is active.

A document preview (xlsx/ods/xls/parquet/docx, with the `documents` extra) is always read-only
— F4 refuses to promote it, since there's no way to save a generated table/text preview
back over the original binary without corrupting it.

### Image viewer

| Key | Action |
|---|---|
| Left / Right | Previous / next image in the directory (all extensions) |
| Shift+Left / Shift+Right | Previous / next image with the same extension as the current one |
| Escape / F3 / F10 | Close the viewer |

Zoom is automatic: the image scales to fit the window (never upscaling past 100%) and
re-fits whenever the window is resized — there's no manual zoom control.

### Dialog conventions

Across the app's modal dialogs (confirm/prompt/error, the Connections manager, Search,
Compression, File Info) **Enter** activates the default action (OK / Yes / Connect) and
**Escape** cancels/closes — you rarely need the mouse to dismiss one.

### Search

Opened with **Alt+F7** or **Shift+F7** ("Find Files"). Criteria (combine any subset):

| Tab | Options |
|---|---|
| Size | Min/max with a unit selector (B/KB/MB/GB); leave either side empty for open-ended |
| Date | From/To (`YYYY-MM-DD HH:MM`, with a picker), plus presets: Last 7 days, Last 30 days, Today, Yesterday |
| Name | Glob or regex pattern, independent case-sensitive toggle |
| Content | String, Regex, or Hex bytes (e.g. `FF D8 FF E0`) mode, independent case-sensitive toggle; files over 10 MB are skipped |

Checking **"Search inside archives"** descends into `.zip`/`.tar`/etc. members using the
same criteria. The search runs on a background thread, is cancellable (the Search button
becomes Stop), and streams matches into a live, sortable results panel — the dialog
releases focus so the rest of the app stays usable while it runs.

### Remote connections (FTP / SFTP)

Volume choosers (**Alt+F1**/**Alt+F2**) include a "Connect to FTP..." entry taking a URL:

```
ftp://[user:pass@]host[:port][/path]
sftp://[user:pass@]host[:port][/path]
```

Omitting credentials uses anonymous FTP login. **F3** views a remote file; **F5**
downloads it to the other panel's local directory. The connection closes cleanly when
you navigate away from its root or on quit.

**File > Connections...** opens a manager to save, edit, and connect to multiple
sessions (fields: Name, Protocol, Host, Port, User, Password, Path, and for SFTP a
Private Key path + Key Passphrase). SFTP authentication tries, in order: the private key
(if set), then the password, then falls back to the SSH agent / default key files.
Host-key verification is trust-on-first-use, checked against `~/.ssh/known_hosts`.
Private-key auth can't be expressed as a URL, so key-based sessions must go through the
Connections manager rather than a typed URL. Sessions are persisted in the config file.

### Archive & compression

Press **Enter** on an archive to browse it as a folder in the panel; **Backspace**/`..`
returns to the containing directory with the cursor on the archive. Both panels can
browse the same archive at once via a shared, refcounted backend. **F3** views a member;
**F5** copies it out. Formats marked read-only refuse F4/F5-into-archive/F7/F8 with a
clear message.

| Format | Extension(s) | Read/Write | Needs |
|---|---|---|---|
| Zip | `.zip` | Read + Write | — |
| Tar | `.tar`, `.tar.gz`, `.tgz`, `.tar.bz2`, `.tar.xz`, (`.tar.zst`/`.tzst` on Python 3.14+) | Read + Write | — |
| GRP (Build Engine) | `.grp` | Read + Write | — |
| 7-Zip | `.7z` | Read + Write | `archives` |
| RAR | `.rar` | Read-only | `archives` |
| ISO 9660 | `.iso` | Read-only browse; write via compression dialog | `archives` |
| cpio, ar, xar, lha/lzh | `.cpio`, `.a`/`.ar`, `.xar`, `.lha`/`.lzh` | Read-only | `archives` |
| gzip / bzip2 / xz / zstd | `.gz`, `.bz2`, `.xz`, (`.zst` on Python 3.14+) | Read-only (single file as a one-entry archive) | — |
| Encrypted | `.crp` | Read-only browse (decrypt, then delegate to the inner format) | `crypto` |

**Shift+F5** opens the compression dialog: an archive name, a container combobox (zip /
tar / grp / 7z¹ / iso¹), a codec combobox (none / gz / bz2 / xz / zst²), a compression
level spinbox, and an optional **Encrypt output (.crp)** stage (password or a stored
key). Every container × codec combination is valid — including double-compression like
`.7z.xz` — and the codec composes onto the container's extension (`.tar.gz`, `.grp.zst`,
...), with `.crp` appended last if encryption is on. *¹Needs the `archives` extra.
²Needs Python 3.14+.*

### Encryption

`.crp` files use ChaCha20-Poly1305 (an AEAD cipher): the file is `MAGIC("CRP1") + salt +
nonce + ciphertext`, decrypted as a single non-seekable blob. Two credential modes,
which must match between encryption and decryption:

- **Password**: the key is derived via PBKDF2-HMAC-SHA256 (200,000 iterations) from the
  password and the stored salt.
- **Stored key**: a named 256-bit key kept in the config file (**Manage Keys...**); the
  salt is used as authentication data instead of key derivation input.

Three ways to produce/consume `.crp`, all producing byte-identical output: the Operations
menu's **Encrypt**/**Decrypt** (works on any file, whether or not it's an archive); the
compression dialog's **Encrypt output** checkbox (wraps the finished archive as a final
stage); or pressing **Enter** on an existing `.crp` file, which prompts for the
credential and then either drops you into the decrypted inner archive (e.g.
`backup.tar.gz.crp`) or shows the single decrypted file.

### Settings

Persisted to a config file (`settings.json` under the platform config directory — XDG
config home / `%APPDATA%` / `~/Library/Application Support`, `chmod 0o600` on Unix):
panel/editor/viewer fonts; theme (a ttkbootstrap theme name) and icon/extension-column
toggles; image-viewer extensions; JSON indent width; terminal command templates
(Linux/Windows); tag-pattern history; stored encryption keys; saved FTP/SFTP sessions;
sort order and hidden-file visibility, both globally and per-panel; and each panel's
last path, tagged files, and active-side selection, restored on the next launch.

### File info

**Shift+F3** shows: name, path, size, and modified time; file type (via the `file`
command on Linux/macOS, a `mimetypes` guess on Windows); POSIX permissions, owner,
group, and link count (owner/group omitted on Windows); and MD5/SHA1/SHA256 checksums.
Everything is computed on a background thread with a progress bar, and works on
remote/archive files too (they're materialized to a local temp file first).

### Volumes / drives (Alt+F1 / Alt+F2)

On Linux, the volume list always includes `/` and your home directory, plus every real
mount point from `/proc/mounts` (pseudo/virtual filesystems — `proc`, `tmpfs`, `overlay`,
GVFS `fuse.*` mounts, etc. — are filtered out). On Windows, it enumerates drive letters.
macOS support is a stub today — the app just shows an empty volume list there rather
than crashing.

## Plugin architecture

`linux_commander/plugins/` is an auto-discovered package: dropping in a Python module
with the right module-level attributes is all it takes to support a new format, no
registration step. Three independent extension points, any subset of which a module may
expose:

- **VFS archive/container formats** — `EXTENSIONS: tuple[str, ...]` +
  `open_fs(host_fs, path) -> FileSystem`, mounted for Enter-to-browse.
- **VFS network protocols** — `SCHEMES: tuple[str, ...]` + `connect_fs(url) ->
  FileSystem`, used by the Connections manager.
- **Viewer document previews** — `VIEW_EXTENSIONS: tuple[str, ...]` +
  `read_document(host_fs, path) -> ViewDocument`, a separate, non-VFS mechanism consumed
  directly by the F3/F4 viewer (used for spreadsheet/document preview).

Discovery scans the package once (`pkgutil.iter_modules`) and builds three lookup maps;
a broken or unimportable module is silently skipped so one bad plugin can't block the
rest of the app from starting. Optional-dependency plugins guard their import at module
top level and set their extension tuple to `()` on `ImportError`, so the feature just
doesn't register rather than crashing.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the project layout, the development
workflow, and step-by-step guides to adding a new archive/protocol plugin, a new viewer
document-preview plugin, a new syntax-highlighting language, or a new optional
dependency.
