"""Tests for linux_commander.settings, focused on FtpSession (FTP/SFTP profiles).

Covers the backward-compatibility guarantee that settings.json files written
before SFTP support existed (no "protocol"/"key_path"/"key_passphrase" keys)
still load correctly.
"""

from __future__ import annotations

from linux_commander.settings import FtpSession, Settings

# ---------------------------------------------------------------------------
# Defaults and backward compatibility
# ---------------------------------------------------------------------------


def test_ftp_session_defaults_to_ftp_protocol() -> None:
    session = FtpSession(name="home", host="example.com")
    assert session.protocol == "ftp"
    assert session.key_path == ""
    assert session.key_passphrase == ""


def test_ftp_session_from_old_style_dict_missing_new_fields() -> None:
    # Simulates a settings.json written before SFTP support existed.
    old_style = {
        "name": "home",
        "host": "example.com",
        "port": 21,
        "user": "anonymous",
        "password": "",
        "path": "/",
    }
    session = FtpSession(**old_style)
    assert session.protocol == "ftp"
    assert session.key_path == ""
    assert session.key_passphrase == ""


def test_settings_from_dict_loads_old_style_ftp_sessions() -> None:
    data = {
        "ftp_sessions": [
            {"name": "home", "host": "example.com", "port": 21, "user": "anonymous", "path": "/"}
        ]
    }
    settings = Settings.from_dict(data)
    assert len(settings.ftp_sessions) == 1
    assert settings.ftp_sessions[0].protocol == "ftp"


def test_settings_round_trips_sftp_session_through_to_dict() -> None:
    session = FtpSession(
        name="server",
        host="example.com",
        port=22,
        user="alice",
        protocol="sftp",
        key_path="/home/alice/.ssh/id_ed25519",
        key_passphrase="secret",
    )
    settings = Settings(ftp_sessions=[session])
    data = settings.to_dict()
    restored = Settings.from_dict(data)
    assert restored.ftp_sessions[0] == session


def test_settings_show_extension_defaults_true_and_round_trips() -> None:
    assert Settings().show_extension is True

    settings = Settings(show_extension=False)
    restored = Settings.from_dict(settings.to_dict())
    assert restored.show_extension is False


def test_settings_from_dict_defaults_show_extension_when_missing() -> None:
    # Simulates a settings.json written before the Extension column existed.
    restored = Settings.from_dict({})
    assert restored.show_extension is True


# ---------------------------------------------------------------------------
# to_url / from_url
# ---------------------------------------------------------------------------


def test_to_url_uses_ftp_scheme_by_default() -> None:
    session = FtpSession(name="x", host="example.com", user="bob", password="pw", path="/pub")
    assert session.to_url() == "ftp://bob:pw@example.com:21/pub"


def test_to_url_uses_sftp_scheme_when_set() -> None:
    session = FtpSession(
        name="x",
        host="example.com",
        port=22,
        user="bob",
        password="pw",
        path="/home",
        protocol="sftp",
    )
    assert session.to_url() == "sftp://bob:pw@example.com:22/home"


def test_from_url_ftp_defaults() -> None:
    session = FtpSession.from_url("home", "ftp://example.com")
    assert session.protocol == "ftp"
    assert session.port == 21
    assert session.user == "anonymous"


def test_from_url_sftp_defaults() -> None:
    session = FtpSession.from_url("server", "sftp://example.com")
    assert session.protocol == "sftp"
    assert session.port == 22
    assert session.user == ""


def test_from_url_sftp_with_credentials() -> None:
    session = FtpSession.from_url("server", "sftp://alice:s3cr3t@example.com:2222/home/alice")
    assert session.protocol == "sftp"
    assert session.host == "example.com"
    assert session.port == 2222
    assert session.user == "alice"
    assert session.password == "s3cr3t"
    assert session.path == "/home/alice"
