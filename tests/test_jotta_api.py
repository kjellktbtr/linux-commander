"""Tests for the Jottacloud JFS API client (linux_commander.jotta_api).

These cover the two bugs found while diagnosing a broken Jottacloud
connection: (1) the real token endpoint returns extra fields --
notably ``not-before-policy``, which isn't even a valid Python
identifier -- that used to crash ``AuthToken(**token_data)``, and (2)
``is_expired`` never actually tracked an absolute expiry. No network
access is used; everything here exercises pure functions/dataclasses.

Skipped entirely when the ``jotta`` extra (httpx/pydantic) isn't installed.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

pytest.importorskip("httpx")
pytest.importorskip("pydantic")

import httpx  # noqa: E402

from linux_commander.jotta_api import (  # noqa: E402
    AuthToken,
    JottaAPI,
    SyncJottaAPI,
    _auth_token_from_response,
    _fmt_jfs_date,
    _parse_jotta_date,
)

# A representative real response from
# https://id.jottacloud.com/auth/realms/jottacloud/protocol/openid-connect/token
_REAL_TOKEN_RESPONSE = {
    "access_token": "eyJ...access",
    "expires_in": 3600,
    "refresh_expires_in": 0,
    "refresh_token": "eyJ...refresh",
    "token_type": "Bearer",
    "id_token": "eyJ...id",
    "not-before-policy": 0,  # not a valid Python identifier
    "session_state": "1eb7c82f-ffdf-47c3-b086-b9063562c1e7",
    "scope": "openid email offline_access",
}


def test_auth_token_from_response_tolerates_unknown_and_hyphenated_keys() -> None:
    token = _auth_token_from_response(_REAL_TOKEN_RESPONSE)

    assert token.access_token == "eyJ...access"
    assert token.refresh_token == "eyJ...refresh"
    assert token.expires_in == 3600
    assert token.token_type == "Bearer"
    assert token.scope == "openid email offline_access"

    # Unknown / unrepresentable keys are preserved rather than dropped or
    # raising.
    assert token.extra["not-before-policy"] == 0
    assert token.extra["session_state"] == "1eb7c82f-ffdf-47c3-b086-b9063562c1e7"


def test_auth_token_sets_expires_at_from_expires_in() -> None:
    before = time.time()
    token = AuthToken(access_token="a", refresh_token="r", expires_in=3600)
    after = time.time()

    assert before + 3600 <= token.expires_at <= after + 3600


def test_auth_token_is_expired_uses_absolute_expiry_not_expires_in() -> None:
    # A token minted an hour ago with a 3600s lifetime is expired now, even
    # though `expires_in` (the original lifetime) is still 3600 -- this is
    # the bug: the old code checked `expires_in <= 60`, which was always
    # false for a freshly-issued token and never became true afterward.
    stale = AuthToken(
        access_token="a",
        refresh_token="r",
        expires_in=3600,
        expires_at=time.time() - 3600,
    )
    assert stale.is_expired

    fresh = AuthToken(access_token="a", refresh_token="r", expires_in=3600)
    assert not fresh.is_expired


def test_auth_token_is_expired_within_60s_buffer() -> None:
    almost_expired = AuthToken(
        access_token="a",
        refresh_token="r",
        expires_in=3600,
        expires_at=time.time() + 30,
    )
    assert almost_expired.is_expired


# ---------------------------------------------------------------------------
# XML listing parsing -- based on real JFS responses captured while
# diagnosing why every file showed a 1970 timestamp, a 0-byte size, and F3
# opened the containing folder's XML instead of the file's content.
#
# The real API puts most revision metadata (size/md5/mime/state/dates) as
# *child elements* of <currentRevision>, not XML attributes, and gives no
# usable "path" at all on nested <file>/<folder> listing entries -- only a
# bare "name". The old code read all of these as attributes (always
# empty/absent) and derived a file's path from a nonexistent "path"
# attribute, so JottaFile.path was always "", size was always 0, and every
# date fell through to the epoch fallback.
# ---------------------------------------------------------------------------

_ROOT_LISTING_XML = """<?xml version="1.0" encoding="UTF-8"?>
<mountPoint time="2026-07-17-T20:39:01Z" host="b2-api-jfs">
  <name xml:space="preserve">Archive</name>
  <path xml:space="preserve">/user/Jotta</path>
  <folders>
    <folder name="2019"/>
    <folder name="nas"/>
  </folders>
  <files>
    <file name="Normill_V7.txt" uuid="d58e6f0e-529d-44e3-bb56-1dfe78455122">
      <currentRevision>
        <number>1</number>
        <state>COMPLETED</state>
        <created>2023-01-09-T16:51:53Z</created>
        <modified>2023-01-09-T16:51:53Z</modified>
        <mime>application/octet-stream</mime>
        <size>19020</size>
        <md5>81b8c8d08da78a57473dbd80aff6505a</md5>
        <updated>2023-01-09-T16:51:54Z</updated>
      </currentRevision>
    </file>
  </files>
  <metadata first="" max="" total="3" num_folders="2" num_files="1"/>
</mountPoint>
"""

_SUBFOLDER_LISTING_XML = """<?xml version="1.0" encoding="UTF-8"?>
<folder name="2019" time="2026-07-17-T20:41:53Z" host="b2-api-jfs">
  <path xml:space="preserve">/user/Jotta/Archive</path>
  <folders>
    <folder name="17. mai"/>
  </folders>
  <metadata first="" max="" total="1" num_folders="1" num_files="0"/>
</folder>
"""

_SINGLE_FILE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<file name="Normill_V7.txt" uuid="d58e6f0e-529d-44e3-bb56-1dfe78455122">
  <path xml:space="preserve">/user/Jotta/Archive</path>
  <currentRevision>
    <number>1</number>
    <state>COMPLETED</state>
    <created>2023-01-09-T16:51:53Z</created>
    <modified>2023-01-09-T16:51:53Z</modified>
    <mime>application/octet-stream</mime>
    <size>19020</size>
    <md5>81b8c8d08da78a57473dbd80aff6505a</md5>
    <updated>2023-01-09-T16:51:54Z</updated>
  </currentRevision>
</file>
"""


def test_parse_jotta_date_handles_real_dash_and_t_format() -> None:
    parsed = _parse_jotta_date("2023-01-09-T16:51:53Z")
    assert parsed.timestamp() == 1673283113.0


def test_parse_listing_root_resolves_child_paths_size_and_mtime() -> None:
    api = JottaAPI()
    folders, files = api._parse_listing(_ROOT_LISTING_XML, base_path="")

    assert {f.name for f in folders} == {"2019", "nas"}
    folder = next(f for f in folders if f.name == "2019")
    assert folder.path == "2019"

    assert len(files) == 1
    f = files[0]
    assert f.path == "Normill_V7.txt"
    assert f.size == 19020
    assert f.md5 == "81b8c8d08da78a57473dbd80aff6505a"
    assert f.mime == "application/octet-stream"
    assert f.state == "COMPLETED"
    assert f.modified.timestamp() == 1673283113.0


def test_parse_listing_subfolder_resolves_nested_child_path() -> None:
    api = JottaAPI()
    folders, _files = api._parse_listing(_SUBFOLDER_LISTING_XML, base_path="2019")

    assert len(folders) == 1
    assert folders[0].name == "17. mai"
    assert folders[0].path == "2019/17. mai"


def test_parse_listing_single_file_root_uses_base_path_directly() -> None:
    """A direct single-file GET returns <file> as the XML root; base_path is
    already the file's own full relative path (the caller already knows what
    it requested), so it must NOT be joined with the file's name again."""
    api = JottaAPI()
    folders, files = api._parse_listing(_SINGLE_FILE_XML, base_path="Normill_V7.txt")

    assert folders == []
    assert len(files) == 1
    assert files[0].path == "Normill_V7.txt"
    assert files[0].size == 19020


_LISTING_WITH_DELETED_ITEMS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<mountPoint time="2026-07-17-T20:39:01Z" host="b2-api-jfs">
  <name xml:space="preserve">Archive</name>
  <path xml:space="preserve">/user/Jotta</path>
  <folders>
    <folder name="live_folder"/>
    <folder name="trashed_folder" deleted="2026-07-18-T09:00:00Z"/>
  </folders>
  <files>
    <file name="live.txt" uuid="a">
      <currentRevision>
        <state>COMPLETED</state>
        <size>10</size>
      </currentRevision>
    </file>
    <file name="trashed.txt" uuid="b" deleted="2026-07-18-T09:00:00Z">
      <currentRevision>
        <state>COMPLETED</state>
        <size>10</size>
      </currentRevision>
    </file>
  </files>
  <metadata first="" max="" total="4" num_folders="2" num_files="2"/>
</mountPoint>
"""


def test_parse_listing_deleted_attribute_is_a_timestamp_not_literal_true() -> None:
    """Regression test for "Jottacloud delete moves the file to trash but it
    keeps appearing in the panel until the trash is emptied": JFS marks a
    trashed item's `deleted` attribute with a deletion *timestamp*
    (confirmed against jottalib's own reference parsing, which checks
    presence via `attrib.get('deleted', None) is None` rather than comparing
    against a literal string), not the literal string "true". The old
    `== "true"` comparison in this module never matched a real deletion, so
    the (correct) filtering added downstream in jotta_plugin.py's
    list_dir()/stat() never actually triggered against a live account."""
    api = JottaAPI()
    folders, files = api._parse_listing(_LISTING_WITH_DELETED_ITEMS_XML, base_path="")

    folders_by_name = {f.name: f for f in folders}
    files_by_name = {f.name: f for f in files}

    assert folders_by_name["live_folder"].deleted is False
    assert folders_by_name["trashed_folder"].deleted is True
    assert files_by_name["live.txt"].deleted is False
    assert files_by_name["trashed.txt"].deleted is True


# ---------------------------------------------------------------------------
# Write operations (upload/mkdir/delete/move) -- verifies the exact request
# shape (method, query params, headers) each JFS write verb needs, since
# getting any of these wrong produces a confusing 4xx from the real API
# rather than a local error. No network access: the JFS client's transport
# is swapped for an httpx.MockTransport that records the request and
# returns a canned response.
# ---------------------------------------------------------------------------


def _authed_api() -> JottaAPI:
    """A JottaAPI with a live-looking token/username, so requests don't
    trigger a (network-hitting) token refresh before reaching the mock
    transport."""
    api = JottaAPI()
    api._token = AuthToken(
        access_token="a",
        refresh_token="r",
        expires_in=3600,
        expires_at=time.time() + 3600,
    )
    api._username = "user"
    return api


def _mount(api: JottaAPI, handler) -> list[httpx.Request]:
    """Point ``api``'s JFS client at a MockTransport and return the list
    that captured requests get appended to."""
    captured: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    api._jfs_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_handler), base_url=api._jfs_base_url
    )
    return captured


def test_fmt_jfs_date_roundtrips_through_parse_jotta_date() -> None:
    from datetime import UTC, datetime

    dt = datetime(2023, 1, 9, 16, 51, 53, tzinfo=UTC)
    formatted = _fmt_jfs_date(dt)
    assert formatted == "2023-01-09T16:51:53Z"
    assert _parse_jotta_date(formatted).timestamp() == dt.timestamp()


def test_upload_file_sends_umode_cphash_and_md5_size_headers() -> None:
    api = _authed_api()
    content = b"hello jottacloud"
    import hashlib

    expected_md5 = hashlib.md5(content).hexdigest()  # noqa: S324

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.params["umode"] == "nomultipart"
        assert request.url.params["cphash"] == expected_md5
        assert request.headers["JMd5"] == expected_md5
        assert request.headers["JSize"] == str(len(content))
        assert request.headers["Content-Type"] == "application/octet-stream"
        assert request.read() == content
        return httpx.Response(200, text=_SINGLE_FILE_XML)

    _mount(api, handler)
    file = asyncio.run(
        api.upload_file("Normill_V7.txt", content, device="Jotta", mountpoint="Archive")
    )
    assert file.path == "Normill_V7.txt"


def test_create_folder_sends_mkdir_true() -> None:
    api = _authed_api()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.params["mkDir"] == "true"
        return httpx.Response(200, text="<folder/>")

    _mount(api, handler)
    asyncio.run(api.create_folder("newdir", device="Jotta", mountpoint="Archive"))


def test_delete_path_uses_dl_for_file_and_dldir_for_folder() -> None:
    api = _authed_api()
    seen_params: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_params.append(dict(request.url.params))
        return httpx.Response(200, text="<file/>")

    _mount(api, handler)
    asyncio.run(api.delete_path("file.txt", is_dir=False, device="Jotta", mountpoint="Archive"))
    asyncio.run(api.delete_path("folder", is_dir=True, device="Jotta", mountpoint="Archive"))

    assert seen_params[0] == {"dl": "true"}
    assert seen_params[1] == {"dlDir": "true"}


def test_move_path_targets_absolute_username_device_mountpoint_path() -> None:
    api = _authed_api()
    seen_params: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_params.append(dict(request.url.params))
        return httpx.Response(200, text="<file/>")

    _mount(api, handler)
    asyncio.run(
        api.move_path("old.txt", "sub/new.txt", is_dir=False, device="Jotta", mountpoint="Archive")
    )

    assert seen_params[0] == {"mv": "/user/Jotta/Archive/sub/new.txt"}


def test_move_path_folder_uses_mvdir_param() -> None:
    api = _authed_api()
    seen_params: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_params.append(dict(request.url.params))
        return httpx.Response(200, text="<folder/>")

    _mount(api, handler)
    asyncio.run(api.move_path("old", "new", is_dir=True, device="Jotta", mountpoint="Archive"))

    assert seen_params[0] == {"mvDir": "/user/Jotta/Archive/new"}


# ---------------------------------------------------------------------------
# SyncJottaAPI cross-thread regression test.
#
# The real-world crash: F5/F6 copy work runs on a background worker thread
# (progress_dialog.run_with_progress), and the app immediately refreshes
# panels on the Tk main thread afterwards. The old SyncJottaAPI._run()
# fetched a *thread-local* event loop per call (asyncio.get_event_loop()),
# so the worker thread and the main thread each got a different loop object.
# httpcore's real connection pool lazily binds asyncio synchronization
# primitives (locks/events) to whichever event loop first drives a request
# through them -- so the second thread to call in blew up with "<Event ...>
# is bound to a different event loop".
#
# httpx.MockTransport (used by every other test in this file) bypasses
# httpcore entirely -- it hands the request straight to a plain Python
# callable, with no loop-bound primitives involved -- so it can't reproduce
# this bug. This test fakes just enough of httpcore's real behavior (a
# shared asyncio.Event whose .wait() every "request" contends on, exactly
# mirroring a connection-pool lock) to make the underlying loop-affinity
# violation actually trigger.
# ---------------------------------------------------------------------------


def test_sync_api_survives_calls_from_two_different_threads() -> None:
    # Stands in for a loop-bound primitive inside httpcore's real connection
    # pool (e.g. a pool-wide lock). Never set, so every wait() genuinely
    # blocks (until the 0.05s timeout) rather than short-circuiting before
    # binding to the calling loop -- see asyncio.Event.wait()'s source: it
    # only calls _get_loop() (the method that raises on a loop mismatch) when
    # the event isn't already set.
    shared_event = asyncio.Event()

    async def fake_request(method: str, path: str, **kwargs: object) -> httpx.Response:
        try:
            await asyncio.wait_for(shared_event.wait(), timeout=0.05)
        except TimeoutError:
            pass
        return httpx.Response(200, text=_SINGLE_FILE_XML)

    sync_api = SyncJottaAPI()
    sync_api._async_api._token = AuthToken(
        access_token="a",
        refresh_token="r",
        expires_in=3600,
        expires_at=time.time() + 3600,
    )
    sync_api._async_api._username = "user"
    sync_api._async_api._jfs_client.request = fake_request  # type: ignore[method-assign]

    worker_errors: list[BaseException] = []

    def worker() -> None:
        # Mimics operations.py's copy running on progress_dialog's background thread.
        try:
            sync_api.list_files("Normill_V7.txt")
        except BaseException as exc:  # noqa: BLE001 - captured to assert on in the main thread
            worker_errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=5)
    assert not worker_errors

    # Mimics app.py's _refresh_both_panels() calling in right after, from
    # the Tk main thread (this test's own thread) -- this is the call that
    # used to crash.
    _, files = sync_api.list_files("Normill_V7.txt")
    assert files[0].path == "Normill_V7.txt"

    sync_api.close()
