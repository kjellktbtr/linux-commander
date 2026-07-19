"""Settings persistence for linux-commander.

Loads/saves JSON settings from the platform-appropriate config directory.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

from linux_commander.fs import SortKey


@dataclass(slots=True)
class StoredKey:
    """A named 256-bit encryption key stored as hex in settings."""

    name: str
    hex_value: str  # 64 hex characters = 32 bytes

    def to_bytes(self) -> bytes:
        return bytes.fromhex(self.hex_value)

    @classmethod
    def generate(cls, name: str) -> StoredKey:
        return cls(name=name, hex_value=secrets.token_hex(32))


@dataclass(slots=True)
class FtpSession:
    """Saved remote-connection profile (FTP or SFTP).

    Kept under its original name/JSON shape for backward compatibility with
    settings files written before SFTP support existed; ``protocol`` defaults
    to ``"ftp"`` so old saved sessions (with no ``protocol`` key) still load
    unchanged via ``FtpSession(**s)``. ``key_path``/``key_passphrase`` are
    SFTP-only (private-key auth) and ignored for FTP sessions.
    """

    name: str
    host: str
    port: int = 21
    user: str = "anonymous"
    password: str = ""
    path: str = "/"
    protocol: str = "ftp"
    key_path: str = ""
    key_passphrase: str = ""

    def to_url(self) -> str:
        """Return a ``<protocol>://`` URL for this session (password auth only).

        SFTP private-key auth can't be embedded in a URL; callers that need
        it should use the sftp plugin's ``connect()`` helper directly with
        ``key_path``/``key_passphrase`` instead of this URL.
        """
        user_pass = ""
        if self.user:
            user_pass = quote(self.user, safe="")
            if self.password:
                user_pass += f":{quote(self.password, safe='')}"
            user_pass += "@"
        return f"{self.protocol}://{user_pass}{self.host}:{self.port}{self.path}"

    @classmethod
    def from_url(cls, name: str, url: str) -> FtpSession:
        """Parse a ``ftp://``/``sftp://`` URL into a session (name given separately)."""
        parsed = urlparse(url)
        protocol = parsed.scheme or "ftp"
        default_port = 22 if protocol == "sftp" else 21
        default_user = "" if protocol == "sftp" else "anonymous"
        return cls(
            name=name,
            protocol=protocol,
            host=parsed.hostname or "",
            port=parsed.port or default_port,
            user=unquote(parsed.username) if parsed.username else default_user,
            password=unquote(parsed.password) if parsed.password else "",
            path=parsed.path or "/",
        )


@dataclass(slots=True)
class JottaSession:
    """Saved Jottacloud connection profile.

    Uses a Personal Login Token from https://www.jottacloud.com/web/secure
    which is exchanged for OAuth2 access/refresh tokens automatically.
    """

    name: str
    token: str  # Personal Login Token (base64-encoded JSON) - single-use, bootstrap only
    device: str = "Jotta"  # Default device name
    mountpoint: str = "Archive"  # Default mountpoint
    path: str = "/"  # Sub-path within mountpoint
    # OAuth2 credentials obtained by exchanging `token` once. Once populated,
    # reconnects must use these (via SyncJottaAPI.restore()) instead of
    # re-exchanging `token`, which Jottacloud invalidates after first use.
    username: str = ""
    refresh_token: str = ""
    access_token: str = ""
    token_expiry: float = 0.0

    @property
    def is_provisioned(self) -> bool:
        """Whether this session already holds an exchanged OAuth refresh token."""
        return bool(self.refresh_token)

    def to_url(self) -> str:
        """Return a ``jotta://`` URL for this session."""
        # Token goes in userinfo, device/mountpoint/path in path
        path_parts = [self.device, self.mountpoint]
        if self.path and self.path != "/":
            path_parts.append(self.path.lstrip("/"))
        path = "/" + "/".join(path_parts)
        return f"jotta://{quote(self.token, safe='')}@{path}"

    @classmethod
    def from_url(cls, name: str, url: str) -> JottaSession:
        """Parse a ``jotta://`` URL into a session."""
        parsed = urlparse(url)
        if parsed.scheme != "jotta":
            raise ValueError(f"Expected jotta:// URL, got {parsed.scheme}://")

        token = unquote(parsed.username) if parsed.username else ""
        path_parts = [p for p in parsed.path.split("/") if p]

        device = path_parts[0] if len(path_parts) > 0 else "Jotta"
        mountpoint = path_parts[1] if len(path_parts) > 1 else "Archive"
        path = "/" + "/".join(path_parts[2:]) if len(path_parts) > 2 else "/"

        return cls(
            name=name,
            token=token,
            device=device,
            mountpoint=mountpoint,
            path=path,
        )


@dataclass(slots=True)
class Settings:
    """Application settings, persisted to JSON."""

    font_family: str = "TkFixedFont"
    font_size: int = 10
    # Editor and Viewer font settings (shared)
    editor_font_family: str = "TkFixedFont"
    editor_font_size: int = 10
    viewer_font_family: str = "TkFixedFont"
    viewer_font_size: int = 10
    ftp_sessions: list[FtpSession] = field(default_factory=list)
    jotta_sessions: list[JottaSession] = field(default_factory=list)
    selection_patterns: list[str] = field(default_factory=lambda: ["*"])
    stored_keys: list[StoredKey] = field(default_factory=list)
    terminal_command_linux: str = 'xterm -e bash -c "{cmd}; exec bash"'
    terminal_command_windows: str = 'start "" cmd /k {cmd}'
    json_indent: int = 2
    image_extensions: list[str] = field(
        default_factory=lambda: [
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".bmp",
            ".tiff",
            ".tif",
            ".webp",
            ".ico",
            ".svg",
        ]
    )
    # Theme (ttkbootstrap theme name, e.g. "darkly", "cosmo")
    theme: str = "darkly"
    show_hidden: bool = False
    sort_key: SortKey = "name"
    sort_reverse: bool = False
    # Icons
    show_icons: bool = True
    # Extension column
    show_extension: bool = True
    # Visible columns in file panel
    visible_columns: list[str] = field(
        default_factory=lambda: ["name", "size", "modified", "extension"]
    )
    # Session state — per-panel paths, marks, sort, hidden
    left_path: str = ""
    right_path: str = ""
    left_marks: list[str] = field(default_factory=list)
    right_marks: list[str] = field(default_factory=list)
    active_side: str = "left"
    left_sort_key: SortKey = "name"
    left_sort_reverse: bool = False
    left_show_hidden: bool = False
    right_sort_key: SortKey = "name"
    right_sort_reverse: bool = False
    right_show_hidden: bool = False
    # Duplicate finder: files whose checksums already match get a full
    # byte-for-byte content comparison automatically if they're at or below
    # this size; above it, the user is prompted (assume similar / assume
    # different / compare content) before that comparison runs.
    duplicate_large_file_mb: float = 10.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "font_family": self.font_family,
            "font_size": self.font_size,
            "editor_font_family": self.editor_font_family,
            "editor_font_size": self.editor_font_size,
            "viewer_font_family": self.viewer_font_family,
            "viewer_font_size": self.viewer_font_size,
            "stored_keys": [asdict(s) for s in self.stored_keys],
            "ftp_sessions": [asdict(s) for s in self.ftp_sessions],
            "jotta_sessions": [asdict(s) for s in self.jotta_sessions],
            "selection_patterns": self.selection_patterns,
            "terminal_command_linux": self.terminal_command_linux,
            "terminal_command_windows": self.terminal_command_windows,
            "json_indent": self.json_indent,
            "image_extensions": self.image_extensions,
            "theme": self.theme,
            "show_hidden": self.show_hidden,
            "sort_key": self.sort_key,
            "sort_reverse": self.sort_reverse,
            "show_icons": self.show_icons,
            "show_extension": self.show_extension,
            "visible_columns": self.visible_columns,
            "left_path": self.left_path,
            "right_path": self.right_path,
            "left_marks": self.left_marks,
            "right_marks": self.right_marks,
            "active_side": self.active_side,
            "left_sort_key": self.left_sort_key,
            "left_sort_reverse": self.left_sort_reverse,
            "left_show_hidden": self.left_show_hidden,
            "right_sort_key": self.right_sort_key,
            "right_sort_reverse": self.right_sort_reverse,
            "right_show_hidden": self.right_show_hidden,
            "duplicate_large_file_mb": self.duplicate_large_file_mb,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Settings:
        ftp_sessions = [FtpSession(**s) for s in data.get("ftp_sessions", [])]
        jotta_sessions = [JottaSession(**s) for s in data.get("jotta_sessions", [])]
        default_linux_term = 'xterm -e bash -c "{cmd}; exec bash"'
        default_win_term = 'start "" cmd /k {cmd}'
        default_img_exts = [
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".bmp",
            ".tiff",
            ".tif",
            ".webp",
            ".ico",
            ".svg",
        ]
        stored_keys = [StoredKey(**s) for s in data.get("stored_keys", [])]
        return cls(
            font_family=data.get("font_family", "TkFixedFont"),
            font_size=data.get("font_size", 10),
            editor_font_family=data.get("editor_font_family", "TkFixedFont"),
            editor_font_size=data.get("editor_font_size", 10),
            viewer_font_family=data.get("viewer_font_family", "TkFixedFont"),
            viewer_font_size=data.get("viewer_font_size", 10),
            stored_keys=stored_keys,
            ftp_sessions=ftp_sessions,
            jotta_sessions=jotta_sessions,
            selection_patterns=data.get("selection_patterns", ["*"]),
            terminal_command_linux=data.get("terminal_command_linux", default_linux_term),
            terminal_command_windows=data.get("terminal_command_windows", default_win_term),
            json_indent=data.get("json_indent", 2),
            image_extensions=data.get("image_extensions", default_img_exts),
            theme=data.get("theme", "darkly"),
            show_hidden=data.get("show_hidden", False),
            sort_key=data.get("sort_key", "name"),
            sort_reverse=data.get("sort_reverse", False),
            show_icons=data.get("show_icons", True),
            show_extension=data.get("show_extension", True),
            visible_columns=data.get("visible_columns", ["name", "size", "modified", "extension"]),
            left_path=data.get("left_path", ""),
            right_path=data.get("right_path", ""),
            left_marks=data.get("left_marks", []),
            right_marks=data.get("right_marks", []),
            active_side=data.get("active_side", "left"),
            left_sort_key=data.get("left_sort_key", "name"),
            left_sort_reverse=data.get("left_sort_reverse", False),
            left_show_hidden=data.get("left_show_hidden", False),
            right_sort_key=data.get("right_sort_key", "name"),
            right_sort_reverse=data.get("right_sort_reverse", False),
            right_show_hidden=data.get("right_show_hidden", False),
            duplicate_large_file_mb=data.get("duplicate_large_file_mb", 10.0),
        )


def get_config_dir() -> Path:
    """Return the platform-appropriate config directory for linux-commander."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "linux-commander"


def get_settings_path() -> Path:
    """Return the full path to the settings.json file."""
    return get_config_dir() / "settings.json"


def load_settings() -> Settings:
    """Load settings from disk, returning defaults if not found or invalid."""
    path = get_settings_path()
    if not path.exists():
        return Settings()
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return Settings.from_dict(data)
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return Settings()


def save_settings(settings: Settings) -> None:
    """Save settings to disk, creating the config directory if needed."""
    path = get_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write atomically: write to temp, then rename
    tmp = path.with_suffix(".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(settings.to_dict(), f, indent=2)
        tmp.replace(path)
        # Restrict permissions on Unix
        if sys.platform != "win32":
            path.chmod(0o600)
    except OSError:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise
