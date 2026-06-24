"""Tests for the email collector — pure-function and unit-level coverage."""

import imaplib
import time
from unittest import mock

import pytest

from llamawatch.collectors import email_collector as ec


@pytest.fixture(autouse=True)
def _clear_cache():
    """Reset module cache between tests."""
    ec._cache["data"] = None
    ec._cache["ts"] = 0
    yield
    ec._cache["data"] = None
    ec._cache["ts"] = 0


# ── _decode_header ────────────────────────────────────────────────────────────

def test_decode_header_plain_ascii():
    assert ec._decode_header("Hello World") == "Hello World"


def test_decode_header_rfc2047_utf8():
    # "=?utf-8?b?SGVsbG8=?=" is base64("Hello")
    result = ec._decode_header("=?utf-8?b?SGVsbG8=?=")
    assert result == "Hello"


def test_decode_header_empty_string():
    assert ec._decode_header("") == ""


def test_decode_header_mixed_encoded_and_plain():
    # Plain prefix then encoded suffix
    result = ec._decode_header("Hi =?utf-8?b?V29ybGQ=?=")
    assert "Hi" in result
    assert "World" in result


# ── _extract_name ─────────────────────────────────────────────────────────────

def test_extract_name_returns_display_name():
    result = ec._extract_name("Alice Smith <alice@example.com>")
    assert result == "Alice Smith"


def test_extract_name_falls_back_to_email():
    result = ec._extract_name("bob@example.com")
    assert result == "bob@example.com"


def test_extract_name_angle_only():
    result = ec._extract_name("<carol@example.com>")
    assert result == "carol@example.com"


def test_extract_name_empty_string():
    result = ec._extract_name("")
    assert result == ""


# ── _get_preview ──────────────────────────────────────────────────────────────

def _make_imap_fetch_response(content_type: str, body: bytes, status: str = "OK"):
    """Build a fake imaplib.fetch() response tuple."""
    raw_msg = (
        f"From: test@example.com\r\n"
        f"Content-Type: {content_type}\r\n"
        f"\r\n"
    ).encode() + body
    return status, [(b"1 (BODY[])", raw_msg)]


def test_get_preview_plain_text():
    mail = mock.MagicMock()
    mail.fetch.return_value = _make_imap_fetch_response("text/plain", b"Hello this is a message body.")
    result = ec._get_preview(mail, b"1")
    assert "Hello" in result


def test_get_preview_skips_pgp_block():
    mail = mock.MagicMock()
    body = b"-----BEGIN PGP MESSAGE-----\nhQIMA...\n-----END PGP MESSAGE-----"
    mail.fetch.return_value = _make_imap_fetch_response("text/plain", body)
    result = ec._get_preview(mail, b"1")
    assert result == ""


def test_get_preview_fetch_failure_returns_empty():
    mail = mock.MagicMock()
    mail.fetch.return_value = ("NO", [None])
    result = ec._get_preview(mail, b"1")
    assert result == ""


def test_get_preview_exception_returns_empty():
    mail = mock.MagicMock()
    mail.fetch.side_effect = imaplib.IMAP4.error("connection lost")
    result = ec._get_preview(mail, b"1")
    assert result == ""


def test_get_preview_html_fallback():
    mail = mock.MagicMock()
    body = b"<html><body><p>Hello from HTML</p></body></html>"
    mail.fetch.return_value = _make_imap_fetch_response("text/html", body)
    result = ec._get_preview(mail, b"1")
    assert "Hello from HTML" in result


# ── collect_email ─────────────────────────────────────────────────────────────

def test_collect_returns_not_configured_when_no_cfg():
    result = ec.collect_email(email_cfg=None)
    # Falls back to reading config.json from disk; we mock the open to fail
    with mock.patch("builtins.open", side_effect=OSError("no file")):
        ec._cache["data"] = None
        result = ec.collect_email(email_cfg=None)
    assert result["status"] == "not_configured"


def test_collect_not_configured_when_disabled():
    cfg = {"enabled": False, "imap_host": "imap.example.com", "username": "u", "password": "p"}
    result = ec.collect_email(email_cfg=cfg)
    assert result["status"] == "not_configured"


def test_collect_not_configured_missing_host():
    cfg = {"enabled": True, "imap_host": "", "username": "u", "password": "p"}
    result = ec.collect_email(email_cfg=cfg)
    assert result["status"] == "not_configured"


def test_collect_not_configured_missing_password():
    cfg = {"enabled": True, "imap_host": "imap.example.com", "username": "u", "password": ""}
    result = ec.collect_email(email_cfg=cfg)
    assert result["status"] == "not_configured"


def test_collect_returns_cached_if_fresh():
    cached = {"status": "connected", "unread_count": 5, "recent": []}
    ec._cache["data"] = cached
    ec._cache["ts"] = time.time()
    result = ec.collect_email(email_cfg={"enabled": True, "imap_host": "h", "username": "u", "password": "p"})
    assert result is cached


def test_collect_imap_error_returns_error_dict():
    cfg = {"enabled": True, "imap_host": "bad.host", "imap_port": 993,
           "username": "u", "password": "p", "use_ssl": True}
    with mock.patch("imaplib.IMAP4_SSL", side_effect=ConnectionRefusedError("refused")):
        result = ec.collect_email(email_cfg=cfg)
    assert result["status"] == "error"
    assert result["unread_count"] == 0


def test_collect_imap_error_falls_back_to_cache():
    cached = {"status": "connected", "unread_count": 3, "recent": []}
    ec._cache["data"] = cached
    ec._cache["ts"] = 0  # expired cache, but it's all we have

    cfg = {"enabled": True, "imap_host": "bad.host", "imap_port": 993,
           "username": "u", "password": "p", "use_ssl": True}
    with mock.patch("imaplib.IMAP4_SSL", side_effect=ConnectionRefusedError("refused")):
        result = ec.collect_email(email_cfg=cfg)
    assert result is cached


def test_collect_successful_imap():
    """Full success path with mocked IMAP."""
    cfg = {"enabled": True, "imap_host": "imap.example.com", "imap_port": 993,
           "username": "user@example.com", "password": "secret", "use_ssl": True}

    mock_mail = mock.MagicMock()
    mock_mail.search.side_effect = [
        ("OK", [b"1 2"]),   # UNSEEN
        ("OK", [b"1 2 3"]), # ALL
    ]
    # Header fetch for each message
    header_bytes = b"From: Alice <alice@example.com>\r\nSubject: Hi\r\nDate: Mon, 01 Jan 2024 12:00:00 +0000\r\n\r\n"
    mock_mail.fetch.return_value = ("OK", [(b"1 (BODY[HEADER])", header_bytes)])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_mail):
        result = ec.collect_email(email_cfg=cfg, max_emails=3)

    assert result["status"] == "connected"
    assert result["unread_count"] == 2


def test_collect_starttls_failure_continues(monkeypatch):
    """STARTTLS failure should not abort — collect continues."""
    cfg = {"enabled": True, "imap_host": "imap.example.com", "imap_port": 143,
           "username": "u", "password": "p", "use_ssl": False}

    mock_mail = mock.MagicMock()
    mock_mail.starttls.side_effect = Exception("STARTTLS not supported")
    mock_mail.search.side_effect = [("OK", [b""]), ("OK", [b""])]

    with mock.patch("imaplib.IMAP4", return_value=mock_mail):
        result = ec.collect_email(email_cfg=cfg)

    # Should still connect and get a valid result
    assert result["status"] == "connected"
    mock_mail.login.assert_called_once()
