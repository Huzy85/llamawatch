"""Tests for auth session persistence and edge cases."""

import json
import time
import tempfile
from pathlib import Path
from unittest import mock

import pytest

import llamawatch.auth as auth


@pytest.fixture(autouse=True)
def _reset_sessions():
    """Isolate each test — clear the in-process session store."""
    original = dict(auth._sessions)
    auth._sessions.clear()
    yield
    auth._sessions.clear()
    auth._sessions.update(original)


# ── _load_sessions ────────────────────────────────────────────────────────────

def test_load_sessions_corrupt_json(tmp_path, monkeypatch):
    p = tmp_path / "sessions.json"
    p.write_text("not valid json {{{{")
    monkeypatch.setattr(auth, "_sessions_path", lambda: p)
    auth._load_sessions()
    assert auth._sessions == {}


def test_load_sessions_discards_expired(tmp_path, monkeypatch):
    p = tmp_path / "sessions.json"
    now = time.time()
    p.write_text(json.dumps({
        "valid-token": now + 9999,
        "expired-token": now - 1,
    }))
    monkeypatch.setattr(auth, "_sessions_path", lambda: p)
    auth._load_sessions()
    assert "valid-token" in auth._sessions
    assert "expired-token" not in auth._sessions


def test_load_sessions_missing_file(tmp_path, monkeypatch):
    p = tmp_path / "no-such-file.json"
    monkeypatch.setattr(auth, "_sessions_path", lambda: p)
    auth._load_sessions()
    assert auth._sessions == {}


def test_load_sessions_all_expired_gives_empty(tmp_path, monkeypatch):
    p = tmp_path / "sessions.json"
    p.write_text(json.dumps({"tok": time.time() - 100}))
    monkeypatch.setattr(auth, "_sessions_path", lambda: p)
    auth._load_sessions()
    assert auth._sessions == {}


# ── _save_sessions ────────────────────────────────────────────────────────────

def test_save_sessions_write_failure_is_silent(monkeypatch):
    monkeypatch.setattr(auth, "_sessions_path", lambda: Path("/dev/full/no/such/path/x.json"))
    auth._sessions["tok"] = time.time() + 3600
    auth._save_sessions()  # must not raise


def test_save_sessions_sets_permissions(tmp_path, monkeypatch):
    p = tmp_path / "sessions.json"
    monkeypatch.setattr(auth, "_sessions_path", lambda: p)
    auth._sessions["t"] = time.time() + 3600
    auth._save_sessions()
    assert (p.stat().st_mode & 0o777) == 0o600


# ── validate_session ──────────────────────────────────────────────────────────

def test_validate_empty_string_returns_false(monkeypatch):
    monkeypatch.setattr(auth, "_save_sessions", lambda: None)
    assert auth.validate_session("") is False


def test_validate_none_returns_false():
    assert auth.validate_session(None) is False


def test_validate_unknown_token_returns_false():
    assert auth.validate_session("not-in-store") is False


def test_validate_expired_token_removes_it(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "_sessions_path", lambda: tmp_path / "s.json")
    auth._sessions["stale"] = time.time() - 1
    result = auth.validate_session("stale")
    assert result is False
    assert "stale" not in auth._sessions


def test_validate_valid_token_returns_true(monkeypatch):
    monkeypatch.setattr(auth, "_save_sessions", lambda: None)
    auth._sessions["good"] = time.time() + 9999
    assert auth.validate_session("good") is True


# ── create_session ────────────────────────────────────────────────────────────

def test_create_session_respects_expiry_days(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "_sessions_path", lambda: tmp_path / "s.json")
    monkeypatch.setattr(auth, "load_config", lambda: {"session_expiry_days": 2})
    token, max_age = auth.create_session()
    assert max_age == 2 * 86400
    assert token in auth._sessions
    assert auth._sessions[token] > time.time() + 86400


def test_create_session_default_7_days(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "_sessions_path", lambda: tmp_path / "s.json")
    monkeypatch.setattr(auth, "load_config", lambda: {})
    token, max_age = auth.create_session()
    assert max_age == 7 * 86400


# ── destroy_session ───────────────────────────────────────────────────────────

def test_destroy_session_removes_token(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "_sessions_path", lambda: tmp_path / "s.json")
    auth._sessions["bye"] = time.time() + 9999
    auth.destroy_session("bye")
    assert "bye" not in auth._sessions


def test_destroy_session_unknown_is_silent(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "_sessions_path", lambda: tmp_path / "s.json")
    auth.destroy_session("ghost")  # must not raise
