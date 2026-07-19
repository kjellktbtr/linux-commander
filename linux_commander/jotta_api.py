"""Jottacloud (JFS) API client for Python.

Based on:
- https://github.com/mifi/jotta (TypeScript)
- https://github.com/albertony/jafs (C#)

Uses the JFS REST API (XML-based) at https://jfs.jottacloud.com/jfs/
with OAuth/OIDC authentication via https://id.jottacloud.com/
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from dataclasses import fields as dataclass_fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Data models (matching the JFS XML API response structure)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class JottaFile:
    """A file entry from JFS API."""

    name: str
    path: str
    uuid: str
    size: int
    md5: str
    mime: str
    state: str
    created: datetime
    modified: datetime
    updated: datetime
    deleted: bool = False
    public_secret: str | None = None
    public_download_url: str | None = None
    public_share_url: str | None = None


@dataclass(slots=True)
class JottaFolder:
    """A folder entry from JFS API."""

    name: str
    path: str | None = None
    deleted: bool = False
    modified: datetime | None = None
    files: list[JottaFile] = field(default_factory=list)
    folders: list[JottaFolder] = field(default_factory=list)


@dataclass(slots=True)
class JottaDevice:
    """A device (computer/phone) in Jottacloud."""

    name: str
    display_name: str
    device_id: str
    type: str
    mount_points: list[str] = field(default_factory=list)


@dataclass(slots=True)
class JottaUser:
    """User account info."""

    username: str
    account_type: str
    locked: bool
    read_locked: bool
    write_locked: bool
    quota_write_locked: bool
    sync_enabled: bool
    device_limit: int
    device_limit_unlimited: bool
    mobile_device_limit: int
    mobile_device_limit_unlimited: bool
    capacity_bytes: int
    capacity_unlimited: bool
    usage_bytes: int


# ---------------------------------------------------------------------------
# Token management
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AuthToken:
    """OAuth2 token response."""

    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str = "Bearer"
    scope: str = ""
    refresh_expires_in: int = 0
    id_token: str = ""
    not_before_policy: int = 0
    expires_at: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.expires_at:
            self.expires_at = time.time() + self.expires_in

    @property
    def is_expired(self) -> bool:
        """Check if token is expired (with 60s buffer)."""
        return time.time() >= self.expires_at - 60


def _auth_token_from_response(token_data: dict[str, Any]) -> AuthToken:
    """Build an ``AuthToken`` from a raw OIDC token response.

    The real Jottacloud token endpoint returns extra fields beyond the ones
    ``AuthToken`` models explicitly (e.g. ``session_state``, and
    ``not-before-policy`` which isn't even a valid Python identifier), so we
    can't just splat the response dict as kwargs. Known fields are mapped in;
    everything else is preserved in ``extra`` rather than dropped or crashing.
    """
    known_fields = {f.name for f in dataclass_fields(AuthToken)} - {"extra", "expires_at"}
    kwargs: dict[str, Any] = {}
    extra: dict[str, Any] = {}
    for key, value in token_data.items():
        if key in known_fields:
            kwargs[key] = value
        else:
            extra[key] = value
    return AuthToken(**kwargs, extra=extra)


class JottaAuthError(Exception):
    """Authentication/authorization error."""

    pass


class JottaAPIError(Exception):
    """API error response."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# XML parsing helpers (JFS API returns XML)
# ---------------------------------------------------------------------------


def _parse_jotta_date(date_str: str) -> datetime:
    """Parse Jottacloud date format: '2023-01-09-T16:51:53Z' (dash *and* a T) or similar.

    All formats below are UTC (the trailing ``Z``), so the result is always
    made timezone-aware in UTC -- otherwise ``datetime.timestamp()`` would
    silently interpret the naive result in the local timezone and skew every
    mtime by the local UTC offset.
    """
    if not date_str:
        return datetime.fromtimestamp(0, tz=UTC)
    for fmt in (
        "%Y-%m-%d-T%H:%M:%SZ",
        "%Y-%m-%d-%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%fZ",
    ):
        try:
            return datetime.strptime(date_str, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    # Fallback: try parsing as ISO format
    try:
        parsed = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return datetime.fromtimestamp(0, tz=UTC)


def _fmt_jfs_date(dt: datetime) -> str:
    """Format a datetime for the ``JCreated``/``JModified`` upload headers.

    Inverse of :func:`_parse_jotta_date`'s primary format. Naive datetimes are
    assumed to already be UTC (matching how the rest of this module treats
    JFS timestamps) rather than the local timezone.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _child_text(parent: ET.Element, tag: str, default: str = "") -> str:
    """Read a child element's text (JFS puts most metadata as child elements
    -- <size>, <md5>, <created>, etc. -- not XML attributes)."""
    child = parent.find(tag)
    if child is not None and child.text is not None:
        return child.text
    return default


def _is_deleted(elem: ET.Element) -> bool:
    """Whether a <file>/<folder> element's ``deleted`` attribute marks it as
    trashed. JFS keeps trashed items in listings rather than dropping them,
    marking them via a ``deleted`` attribute -- but per real-world captured
    responses and jottalib's own reference parsing (``attrib.get('deleted',
    None)``, checked for ``is None`` rather than compared against a literal
    string), the attribute's *value* is a deletion timestamp, not the literal
    string ``"true"``. So this checks *presence*, not an exact value match --
    still correctly catches a literal ``"true"`` too, just isn't limited to it.
    """
    return bool(elem.get("deleted"))


def _join_jfs_path(container_path: str, name: str) -> str:
    """Join a container's JFS-relative path with a child's name.

    Nested <file>/<folder> listing entries only carry their own ``name``
    attribute -- JFS gives no usable path on them directly -- so the full
    relative path has to be reconstructed from the containing folder's own
    (already-known) path plus this entry's name.
    """
    container_path = (container_path or "").strip("/")
    return f"{container_path}/{name}" if container_path else name


def _file_from_elem(elem: ET.Element, rel_path: str) -> JottaFile:
    """Build a JottaFile from a <file> element given its resolved relative path."""
    # Current revision info -- size/md5/mime/state/created/modified/updated
    # are child elements of <currentRevision>, not XML attributes.
    rev = elem.find("currentRevision")
    if rev is None:
        rev = elem

    return JottaFile(
        name=elem.get("name", ""),
        path=rel_path,
        uuid=elem.get("uuid", ""),
        size=int(_child_text(rev, "size", "0") or "0"),
        md5=_child_text(rev, "md5"),
        mime=_child_text(rev, "mime"),
        state=_child_text(rev, "state", "UNKNOWN"),
        created=_parse_jotta_date(_child_text(rev, "created")),
        modified=_parse_jotta_date(_child_text(rev, "modified")),
        updated=_parse_jotta_date(_child_text(rev, "updated")),
        deleted=_is_deleted(elem),
        public_secret=elem.get("publicURI"),
        public_download_url=None,  # Will be constructed if needed
        public_share_url=elem.get("publicSharePath"),
    )


def _parse_file_element(elem: ET.Element, folder_path: str) -> JottaFile:
    """Parse a nested <file> listing entry.

    ``folder_path`` is the JFS-relative path of the *containing* folder --
    see :func:`_join_jfs_path`.
    """
    return _file_from_elem(elem, _join_jfs_path(folder_path, elem.get("name", "")))


def _parse_folder_element(elem: ET.Element, folder_path: str) -> JottaFolder:
    """Parse a <folder> XML element recursively.

    ``folder_path`` is the JFS-relative path of the *containing* folder --
    see :func:`_join_jfs_path`.
    """
    name = elem.get("name", "")
    rel_path = _join_jfs_path(folder_path, name)
    deleted = _is_deleted(elem)
    modified_str = _child_text(elem, "modified")

    folder = JottaFolder(
        name=name,
        path=rel_path,
        deleted=deleted,
        modified=_parse_jotta_date(modified_str) if modified_str else None,
    )

    # Parse files
    files_elem = elem.find("files")
    if files_elem is not None:
        for file_elem in files_elem.findall("file"):
            folder.files.append(_parse_file_element(file_elem, rel_path))

    # Parse subfolders
    folders_elem = elem.find("folders")
    if folders_elem is not None:
        for subfolder_elem in folders_elem.findall("folder"):
            folder.folders.append(_parse_folder_element(subfolder_elem, rel_path))

    return folder


def _parse_device_element(elem: ET.Element) -> JottaDevice:
    """Parse a <device> XML element."""
    return JottaDevice(
        name=elem.get("name", ""),
        display_name=elem.get("displayName", ""),
        device_id=elem.get("id", ""),
        type=elem.get("type", ""),
        mount_points=[mp.get("name", "") for mp in elem.findall("mountPoint")],
    )


def _parse_user_element(elem: ET.Element) -> JottaUser:
    """Parse the root <user> XML element."""
    capacity_elem = elem.find("capacity")
    usage_elem = elem.find("usage")
    max_devices = elem.find("maxDevices")
    max_mobile = elem.find("maxMobileDevices")

    def _parse_bool(elem: ET.Element | None, attr: str) -> bool:
        if elem is None:
            return False
        return elem.get(attr, "false").lower() == "true"

    def _parse_int(elem: ET.Element | None, attr: str) -> int:
        if elem is None:
            return 0
        return int(elem.get(attr, "0"))

    return JottaUser(
        username=elem.get("username", ""),
        account_type=elem.get("accountType", ""),
        locked=elem.get("locked", "false").lower() == "true",
        read_locked=elem.get("readLocked", "false").lower() == "true",
        write_locked=elem.get("writeLocked", "false").lower() == "true",
        quota_write_locked=elem.get("quotaWriteLocked", "false").lower() == "true",
        sync_enabled=elem.get("enableSync", "false").lower() == "true",
        device_limit=_parse_int(max_devices, "value"),
        device_limit_unlimited=_parse_bool(max_devices, "unlimited"),
        mobile_device_limit=_parse_int(max_mobile, "value"),
        mobile_device_limit_unlimited=_parse_bool(max_mobile, "unlimited"),
        capacity_bytes=_parse_int(capacity_elem, "value"),
        capacity_unlimited=_parse_bool(capacity_elem, "unlimited"),
        usage_bytes=_parse_int(usage_elem, "value"),
    )


# ---------------------------------------------------------------------------
# Main API client
# ---------------------------------------------------------------------------


class JottaAPI:
    """Jottacloud JFS API client with automatic token refresh."""

    def __init__(
        self,
        token: AuthToken | None = None,
        *,
        client_id: str = "jottacli",
        token_endpoint: str = "https://id.jottacloud.com/auth/realms/jottacloud/protocol/openid-connect/token",
        api_base_url: str = "https://api.jottacloud.com/",
        jfs_base_url: str = "https://jfs.jottacloud.com/jfs/",
        default_device: str = "Jotta",
        default_mountpoint: str = "Archive",
        on_token_update: Callable[[AuthToken], Any] | None = None,
        on_auth_failed: Callable[[], Any] | None = None,
    ):
        self._client_id = client_id
        self._token_endpoint = token_endpoint
        self._api_base_url = api_base_url.rstrip("/") + "/"
        self._jfs_base_url = jfs_base_url.rstrip("/") + "/"
        self._default_device = default_device
        self._default_mountpoint = default_mountpoint
        self._on_token_update = on_token_update
        self._on_auth_failed = on_auth_failed

        self._token: AuthToken | None = token
        self._username: str | None = None
        self._refreshing: bool = False

        # HTTP clients
        self._api_client = httpx.AsyncClient(base_url=self._api_base_url, timeout=30.0)
        self._jfs_client = httpx.AsyncClient(base_url=self._jfs_base_url, timeout=30.0)

    @property
    def token(self) -> AuthToken | None:
        return self._token

    @property
    def username(self) -> str | None:
        return self._username

    @property
    def is_authenticated(self) -> bool:
        return self._token is not None and self._username is not None

    def _auth_header(self) -> dict[str, str]:
        if not self._token:
            return {}
        return {"Authorization": f"Bearer {self._token.access_token}"}

    def _jfs_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/xml"}
        headers.update(self._auth_header())
        return headers

    async def _request_token(self, data: dict[str, str]) -> AuthToken:
        """Request OAuth2 token from token endpoint."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                self._token_endpoint,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if resp.status_code >= 400:
            raise JottaAuthError(f"Token request failed: {resp.status_code} {resp.text}")
        token_data = resp.json()
        return _auth_token_from_response(token_data)

    async def authenticate(self, personal_login_token_b64: str) -> AuthToken:
        """Authenticate using a Personal Login Token (base64 encoded JSON)."""
        # Decode the personal login token
        login_token_json = base64.b64decode(personal_login_token_b64).decode()
        login_token = json.loads(login_token_json)

        # Request OAuth2 token using password grant
        data = {
            "client_id": self._client_id,
            "grant_type": "password",
            "username": login_token["username"],
            "password": login_token["auth_token"],
            "scope": "openid offline_access",
        }

        self._token = await self._request_token(data)
        self._username = login_token["username"]

        if self._on_token_update:
            await self._on_token_update(self._token)

        return self._token

    def restore(self, token: AuthToken, username: str) -> None:
        """Restore a previously-issued token/username pair without hitting the network.

        Use this instead of :meth:`authenticate` when a Personal Login Token has
        already been exchanged once (the login token is single-use — a second
        exchange fails with ``invalid_grant``) and the resulting refresh token was
        persisted. Call :meth:`refresh_token` afterwards if ``token.is_expired``.
        """
        self._token = token
        self._username = username

    async def refresh_token(self) -> AuthToken:
        """Refresh the access token using the refresh token."""
        if not self._token or not self._token.refresh_token:
            raise JottaAuthError("No refresh token available")

        if self._refreshing:
            # Wait for existing refresh to complete
            while self._refreshing:
                await asyncio.sleep(0.1)
            return self._token

        self._refreshing = True
        try:
            data = {
                "grant_type": "refresh_token",
                "client_id": self._client_id,
                "refresh_token": self._token.refresh_token,
            }
            new_token = await self._request_token(data)
            self._token = new_token
            if self._on_token_update:
                await self._on_token_update(self._token)
            return new_token
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 400 and e.response.status_code < 500:
                # Refresh token expired/invalid - clear auth
                self._token = None
                self._username = None
                if self._on_auth_failed:
                    await self._on_auth_failed()
            raise JottaAuthError(f"Token refresh failed: {e}") from e
        finally:
            self._refreshing = False

    async def _ensure_valid_token(self) -> None:
        """Ensure we have a valid access token, refreshing if necessary."""
        if self._token and self._token.is_expired:
            await self.refresh_token()

    async def _api_request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Make an authenticated API request with auto-refresh."""
        await self._ensure_valid_token()
        # Pulled out of kwargs (not just read) so it isn't also passed
        # positionally to httpx.request below; kept around (rather than
        # re-popped) so a 401 retry doesn't silently drop caller headers --
        # the second `kwargs.pop("headers", {})` would return {} since the
        # key is already gone from kwargs after the first pop.
        caller_headers = kwargs.pop("headers", {})
        headers = dict(caller_headers)
        headers.update(self._auth_header())
        resp = await self._api_client.request(method, path, headers=headers, **kwargs)

        if resp.status_code == 401 and self._token:
            # Token expired, try refresh once
            await self.refresh_token()
            headers = dict(caller_headers)
            headers.update(self._auth_header())
            resp = await self._api_client.request(method, path, headers=headers, **kwargs)

        if resp.status_code >= 400:
            msg = f"API request failed: {resp.status_code} {resp.text}"
            raise JottaAPIError(msg, resp.status_code)
        return resp

    async def _jfs_request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Make an authenticated JFS request with auto-refresh."""
        await self._ensure_valid_token()
        # See _api_request for why this is kept around rather than re-popped
        # on the 401 retry below.
        caller_headers = kwargs.pop("headers", {})
        headers = dict(caller_headers)
        headers.update(self._jfs_headers())
        resp = await self._jfs_client.request(method, path, headers=headers, **kwargs)

        if resp.status_code == 401 and self._token:
            await self.refresh_token()
            headers = dict(caller_headers)
            headers.update(self._jfs_headers())
            resp = await self._jfs_client.request(method, path, headers=headers, **kwargs)

        if resp.status_code >= 400:
            msg = f"JFS request failed: {resp.status_code} {resp.text}"
            raise JottaAPIError(msg, resp.status_code)
        return resp

    # -----------------------------------------------------------------------
    # High-level API methods
    # -----------------------------------------------------------------------

    async def get_customer_info(self) -> dict[str, Any]:
        """Get customer info (includes username)."""
        resp = await self._api_request("GET", "account/v1/customer")
        return resp.json()

    async def list_files(
        self, path: str = "", device: str | None = None, mountpoint: str | None = None
    ) -> tuple[list[JottaFolder], list[JottaFile]]:
        """List files and folders at a given path."""
        jfs_path = self._build_jfs_path(path, device, mountpoint)
        resp = await self._jfs_request("GET", jfs_path)
        return self._parse_listing(resp.text, base_path=path.strip("/"))

    async def list_files_recursive(
        self, path: str = "", device: str | None = None, mountpoint: str | None = None
    ) -> AsyncIterator[tuple[str, list[JottaFile]]]:
        """Recursively list all files under a path."""
        folders, files = await self.list_files(path, device, mountpoint)
        yield path, files
        for folder in folders:
            if not folder.deleted:
                subpath = f"{path}/{folder.name}" if path else folder.name
                async for sub_path, subfiles in self.list_files_recursive(
                    subpath, device, mountpoint
                ):
                    yield sub_path, subfiles

    async def get_file(
        self,
        path: str,
        device: str | None = None,
        mountpoint: str | None = None,
        *,
        range_header: tuple[int, int] | None = None,
        mode: str = "bin",
    ) -> httpx.Response:
        """Get a file (download). Returns streaming response."""
        jfs_path = self._build_jfs_path(path, device, mountpoint)
        # mode=bin must always be sent explicitly: a plain GET with no mode
        # param returns the file's XML metadata (like a folder listing does),
        # not its binary content.
        params = {"mode": mode}
        headers = {}
        if range_header:
            headers["Range"] = f"bytes={range_header[0]}-{range_header[1]}"
        return await self._jfs_client.request(
            "GET", jfs_path, params=params, headers={**self._jfs_headers(), **headers}
        )

    async def get_file_stream(
        self,
        path: str,
        device: str | None = None,
        mountpoint: str | None = None,
    ) -> AsyncIterator[bytes]:
        """Get a file as a stream (for large files)."""
        resp = await self.get_file(path, device, mountpoint)
        return resp.aiter_bytes()

    async def share_file(
        self,
        path: str,
        share: bool = True,
        device: str | None = None,
        mountpoint: str | None = None,
    ) -> JottaFile:
        """Enable or disable public sharing for a file."""
        jfs_path = self._build_jfs_path(path, device, mountpoint)
        mode = "enableShare" if share else "disableShare"
        resp = await self._jfs_request("GET", jfs_path, params={"mode": mode})
        folders, files = self._parse_listing(resp.text, base_path=path.strip("/"))
        if files:
            return files[0]
        raise JottaAPIError("No file returned from share operation")

    def get_download_url(self, path: str, mode: str = "bin") -> str:
        """Get a direct download URL for a file (requires valid token)."""
        jfs_path = self._build_jfs_path(path)
        query = f"mode={quote(mode)}"
        base = f"{self._jfs_base_url}{jfs_path}"
        return f"{base}?{query}"

    # -----------------------------------------------------------------------
    # Write operations
    # -----------------------------------------------------------------------

    async def upload_file(
        self,
        path: str,
        content: bytes,
        device: str | None = None,
        mountpoint: str | None = None,
        *,
        created: datetime | None = None,
        modified: datetime | None = None,
    ) -> JottaFile:
        """Upload (create or overwrite) a file's full content.

        JFS wants the md5/size known up front (they're sent as headers, and
        ``cphash`` lets the server skip the upload if it already has an
        identical copy), so this always takes the complete ``bytes`` rather
        than a stream -- there's no incremental/chunked upload here.
        """
        jfs_path = self._build_jfs_path(path, device, mountpoint)
        md5hex = hashlib.md5(content).hexdigest()  # noqa: S324 (JFS protocol requirement, not security use)
        now = datetime.now(UTC)
        headers = {
            "JMd5": md5hex,
            "JSize": str(len(content)),
            "JCreated": _fmt_jfs_date(created or now),
            "JModified": _fmt_jfs_date(modified or now),
            "Content-Type": "application/octet-stream",
        }
        resp = await self._jfs_request(
            "POST",
            jfs_path,
            params={"umode": "nomultipart", "cphash": md5hex},
            headers=headers,
            content=content,
        )
        _, files = self._parse_listing(resp.text, base_path=path.strip("/"))
        if files:
            return files[0]
        raise JottaAPIError("No file returned from upload")

    async def create_folder(
        self, path: str, device: str | None = None, mountpoint: str | None = None
    ) -> None:
        """Create a folder at ``path`` (parent folders must already exist)."""
        jfs_path = self._build_jfs_path(path, device, mountpoint)
        await self._jfs_request("POST", jfs_path, params={"mkDir": "true"})

    async def delete_path(
        self,
        path: str,
        *,
        is_dir: bool,
        device: str | None = None,
        mountpoint: str | None = None,
    ) -> None:
        """Move a file or folder to the Jottacloud trash (recoverable from the web UI).

        JFS has no separate permanent-delete verb exposed here -- ``dl``/``dlDir``
        is the trash operation used by every reference client.
        """
        jfs_path = self._build_jfs_path(path, device, mountpoint)
        param = "dlDir" if is_dir else "dl"
        await self._jfs_request("POST", jfs_path, params={param: "true"})

    async def move_path(
        self,
        path: str,
        new_path: str,
        *,
        is_dir: bool,
        device: str | None = None,
        mountpoint: str | None = None,
    ) -> None:
        """Move/rename a file or folder.

        ``path`` and ``new_path`` are both mountpoint-relative (like every
        other method here), but the JFS ``mv``/``mvDir`` query param itself
        must be the *absolute* JFS path (``/username/device/mountpoint/...``)
        of the destination -- so ``new_path`` is re-run through
        :meth:`_build_jfs_path` rather than used as-is.
        """
        jfs_path = self._build_jfs_path(path, device, mountpoint)
        abs_target = "/" + self._build_jfs_path(new_path, device, mountpoint)
        param = "mvDir" if is_dir else "mv"
        await self._jfs_request("POST", jfs_path, params={param: abs_target})

    # -----------------------------------------------------------------------
    # Path building helpers
    # -----------------------------------------------------------------------

    def _build_jfs_path(
        self, path: str = "", device: str | None = None, mountpoint: str | None = None
    ) -> str:
        """Build a JFS API path: /username/device/mountpoint/path"""
        if not self._username:
            raise JottaAuthError("Not authenticated - username unknown")
        parts = [self._username]
        if device:
            parts.append(device)
        elif self._default_device:
            parts.append(self._default_device)
        if mountpoint:
            parts.append(mountpoint)
        elif self._default_mountpoint:
            parts.append(self._default_mountpoint)
        if path:
            parts.append(path.lstrip("/"))
        return "/".join(quote(p) for p in parts)

    def _parse_listing(
        self, xml_text: str, base_path: str = ""
    ) -> tuple[list[JottaFolder], list[JottaFile]]:
        """Parse a JFS XML listing/detail response.

        ``base_path`` is the JFS-relative path that was requested: for a
        folder/mountPoint listing this is the path of the listed folder
        itself, used to resolve full paths of its direct children (which
        only carry their own ``name`` in the XML, no path); for a
        single-file response it's already that file's own full path.
        """
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            raise JottaAPIError(f"Failed to parse XML response: {e}") from e

        # Response can be <mountPoint>, <folder>, or <file>
        if root.tag == "file":
            return [], [_file_from_elem(root, base_path)]

        # Folder or mountPoint listing: <folders>/<files> are direct
        # children of the root element.
        folders: list[JottaFolder] = []
        files: list[JottaFile] = []

        folders_xml = root.find("folders")
        if folders_xml is not None:
            for f in folders_xml.findall("folder"):
                folders.append(_parse_folder_element(f, base_path))

        files_xml = root.find("files")
        if files_xml is not None:
            for f in files_xml.findall("file"):
                files.append(_parse_file_element(f, base_path))

        return folders, files

    async def get_devices(self) -> list[JottaDevice]:
        """List all devices for the current user."""
        if not self._username:
            raise JottaAuthError("Not authenticated")
        resp = await self._jfs_request("GET", f"{quote(self._username)}")
        root = ET.fromstring(resp.text)
        devices = []
        for dev_elem in root.findall("device"):
            devices.append(_parse_device_element(dev_elem))
        return devices

    async def get_user_info(self) -> JottaUser:
        """Get current user info."""
        if not self._username:
            raise JottaAuthError("Not authenticated")
        resp = await self._jfs_request("GET", f"{quote(self._username)}")
        root = ET.fromstring(resp.text)
        return _parse_user_element(root)

    async def close(self) -> None:
        """Close HTTP connections."""
        await self._api_client.aclose()
        await self._jfs_client.aclose()

    async def __aenter__(self) -> JottaAPI:
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()


# ---------------------------------------------------------------------------
# Synchronous wrapper for use in synchronous contexts (like tkinter)
# ---------------------------------------------------------------------------


class SyncJottaAPI:
    """Synchronous wrapper around JottaAPI for use in non-async code.

    Runs a single persistent event loop on a dedicated background thread for
    the lifetime of this object, and marshals every async call onto it via
    ``asyncio.run_coroutine_threadsafe``. This is required, not just tidy:
    the underlying ``JottaAPI``'s ``httpx.AsyncClient`` instances are created
    once and reused for the object's lifetime, but httpx/anyio/httpcore
    lazily bind connection-pool internals (SSL streams, locks,
    ``asyncio.Event``s) to whichever event loop first drives a request
    through them. This wrapper is called from both a background
    copy/move/delete worker thread (``operations.py``, via
    ``progress_dialog.run_with_progress``) and the Tk main thread (e.g. a
    post-copy panel refresh) -- the old per-call ``asyncio.get_event_loop()``
    is thread-local and handed back a *different* loop object to each caller
    thread, so whichever thread called in second crashed with "<Event ...>
    is bound to a different event loop" as soon as the shared client's
    already-loop-bound internals were touched from that other loop.
    """

    def __init__(self, *args, **kwargs):
        self._async_api = JottaAPI(*args, **kwargs)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="jotta-api-loop", daemon=True
        )
        self._thread.start()

    def _run(self, coro):
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()

    @property
    def token(self) -> AuthToken | None:
        return self._async_api.token

    @property
    def username(self) -> str | None:
        return self._async_api.username

    @property
    def is_authenticated(self) -> bool:
        return self._async_api.is_authenticated

    def authenticate(self, personal_login_token_b64: str) -> AuthToken:
        return self._run(self._async_api.authenticate(personal_login_token_b64))

    def restore(self, token: AuthToken, username: str) -> None:
        self._async_api.restore(token, username)

    def refresh_token(self) -> AuthToken:
        return self._run(self._async_api.refresh_token())

    def get_customer_info(self) -> dict[str, Any]:
        return self._run(self._async_api.get_customer_info())

    def list_files(
        self, path: str = "", device: str | None = None, mountpoint: str | None = None
    ) -> tuple[list[JottaFolder], list[JottaFile]]:
        return self._run(self._async_api.list_files(path, device, mountpoint))

    def list_files_recursive(
        self, path: str = "", device: str | None = None, mountpoint: str | None = None
    ) -> list[tuple[str, list[JottaFile]]]:
        # For sync version, collect all results
        async def _collect() -> list[tuple[str, list[JottaFile]]]:
            results: list[tuple[str, list[JottaFile]]] = []
            async for p, files in self._async_api.list_files_recursive(path, device, mountpoint):
                results.append((p, files))
            return results

        return self._run(_collect())

    def get_file(
        self,
        path: str,
        device: str | None = None,
        mountpoint: str | None = None,
        *,
        range_header: tuple[int, int] | None = None,
        mode: str = "bin",
    ) -> httpx.Response:
        return self._run(
            self._async_api.get_file(path, device, mountpoint, range_header=range_header, mode=mode)
        )

    def share_file(
        self,
        path: str,
        share: bool = True,
        device: str | None = None,
        mountpoint: str | None = None,
    ) -> JottaFile:
        return self._run(self._async_api.share_file(path, share, device, mountpoint))

    def get_download_url(self, path: str, mode: str = "bin") -> str:
        return self._async_api.get_download_url(path, mode)

    def upload_file(
        self,
        path: str,
        content: bytes,
        device: str | None = None,
        mountpoint: str | None = None,
        *,
        created: datetime | None = None,
        modified: datetime | None = None,
    ) -> JottaFile:
        return self._run(
            self._async_api.upload_file(
                path, content, device, mountpoint, created=created, modified=modified
            )
        )

    def create_folder(
        self, path: str, device: str | None = None, mountpoint: str | None = None
    ) -> None:
        self._run(self._async_api.create_folder(path, device, mountpoint))

    def delete_path(
        self,
        path: str,
        *,
        is_dir: bool,
        device: str | None = None,
        mountpoint: str | None = None,
    ) -> None:
        self._run(
            self._async_api.delete_path(path, is_dir=is_dir, device=device, mountpoint=mountpoint)
        )

    def move_path(
        self,
        path: str,
        new_path: str,
        *,
        is_dir: bool,
        device: str | None = None,
        mountpoint: str | None = None,
    ) -> None:
        self._run(
            self._async_api.move_path(
                path, new_path, is_dir=is_dir, device=device, mountpoint=mountpoint
            )
        )

    def get_devices(self) -> list[JottaDevice]:
        return self._run(self._async_api.get_devices())

    def get_user_info(self) -> JottaUser:
        return self._run(self._async_api.get_user_info())

    def close(self) -> None:
        self._run(self._async_api.close())
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        self._loop.close()


# ---------------------------------------------------------------------------
# Token persistence helpers
# ---------------------------------------------------------------------------


def save_token_to_file(token: AuthToken, path: Path, encrypt: bool = False) -> None:
    """Save token to file (optionally encrypted using platform keyring)."""
    import keyring

    data = json.dumps(
        {
            "access_token": token.access_token,
            "refresh_token": token.refresh_token,
            "expires_in": token.expires_in,
            "token_type": token.token_type,
            "scope": token.scope,
            "extra": token.extra,
        }
    )

    if encrypt:
        keyring.set_password("linux-commander", "jotta_token", data)
    else:
        path.write_text(data)


def load_token_from_file(path: Path, decrypt: bool = False) -> AuthToken | None:
    """Load token from file (or keyring if encrypted)."""
    import keyring

    try:
        if decrypt:
            data = keyring.get_password("linux-commander", "jotta_token")
            if data is None:
                return None
        else:
            if not path.exists():
                return None
            data = path.read_text()
        token_data = json.loads(data)
        return AuthToken(**token_data)
    except (json.JSONDecodeError, KeyError, ValidationError):
        return None
