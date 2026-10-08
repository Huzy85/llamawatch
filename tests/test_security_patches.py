"""Checks for the October 2026 security patches: the central cross-site check on
writes, the login throttle's address handling, password changes, upload limits,
layout limits and the terminal environment filter."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import llamawatch.config as config_mod
from llamawatch import auth, security

PASSWORD = "correct horse battery"


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    cfg = {
        "port": 8450, "host": "127.0.0.1", "backends": [], "services": [],
        "auth_enabled": True, "auth_password_hash": auth.hash_password(PASSWORD),
        "inbox_path": str(tmp_path / "inbox"),
        "fleet": {"hosts": [{"name": "Box", "local": True, "color": "#fff"}]},
    }
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    config_mod.reset_config()
    config_mod.load_config(config_dir=tmp_path)
    import llamawatch.server as srv
    srv._config = config_mod.load_config()
    srv._adapters = MagicMock()
    srv._adapters.get_all.return_value = []
    srv._collector_registry = MagicMock()
    monkeypatch.setattr(auth, "_sessions", {})
    monkeypatch.setattr(auth, "_sessions_path", lambda: tmp_path / "sessions.json")
    security.reset_rate_limits()
    yield SimpleNamespace(srv=srv, dir=tmp_path)
    config_mod.reset_config()
    security.reset_rate_limits()


def _client(app_env):
    from llamawatch.server import app
    c = TestClient(app)
    token, _ = auth.create_session()
    c.cookies.set("lw_session", token)
    return c, token


# ── central cross-site check ─────────────────────────────────────────────────

def test_write_from_another_site_is_refused(app_env):
    c, _ = _client(app_env)
    r = c.post("/api/layout", json={"widgets": []}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_write_from_same_site_is_allowed(app_env):
    c, _ = _client(app_env)
    r = c.post("/api/layout", json={"widgets": []}, headers={"Origin": "http://testserver"})
    assert r.status_code == 200


def test_read_from_another_site_is_not_blocked_by_the_check(app_env):
    c, _ = _client(app_env)
    r = c.get("/api/layout", headers={"Origin": "https://evil.example"})
    assert r.status_code == 200


def test_layout_size_cap(app_env):
    c, _ = _client(app_env)
    r = c.post("/api/layout", content=b'{"x":"' + b"a" * 300_000 + b'"}',
               headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_layout_must_be_an_object(app_env):
    c, _ = _client(app_env)
    assert c.post("/api/layout", json=[1, 2]).status_code == 400


# ── login throttle address ───────────────────────────────────────────────────

def _req(peer, xff=None):
    headers = {"x-forwarded-for": xff} if xff else {}
    return SimpleNamespace(client=SimpleNamespace(host=peer), headers=headers)


def test_direct_visitor_cannot_choose_their_address():
    from llamawatch.routes.auth import _visitor_ip
    assert _visitor_ip(_req("203.0.113.9", "198.51.100.1")) == "203.0.113.9"


def test_local_proxy_address_uses_last_hop():
    from llamawatch.routes.auth import _visitor_ip
    assert _visitor_ip(_req("127.0.0.1", "198.51.100.1, 203.0.113.7")) == "203.0.113.7"
    assert _visitor_ip(_req("127.0.0.1")) == "127.0.0.1"


# ── password changes ─────────────────────────────────────────────────────────

def test_password_change_needs_current_password(app_env):
    c, _ = _client(app_env)
    r = c.put("/api/settings", json={"auth_password": "new one please"})
    assert r.status_code == 403
    r = c.put("/api/settings", json={"auth_password": "new one please", "auth_current_password": "wrong"})
    assert r.status_code == 403


def test_turning_sign_in_off_needs_current_password(app_env):
    c, _ = _client(app_env)
    assert c.put("/api/settings", json={"auth_enabled": False}).status_code == 403


def test_password_change_ends_other_sessions(app_env):
    c, mine = _client(app_env)
    other, _ = auth.create_session()
    r = c.put("/api/settings", json={"auth_password": "new one please", "auth_current_password": PASSWORD})
    assert r.status_code == 200
    assert auth.validate_session(mine)
    assert not auth.validate_session(other)
    assert auth.verify_password("new one please")


def test_hash_cannot_be_set_directly(app_env):
    c, _ = _client(app_env)
    r = c.put("/api/settings", json={"auth_password_hash": auth.hash_password("sneaky")})
    assert r.status_code == 200
    assert not auth.verify_password("sneaky")
    assert auth.verify_password(PASSWORD)


def test_other_settings_save_without_current_password(app_env):
    c, _ = _client(app_env)
    assert c.put("/api/settings", json={"dashboard_name": "Example"}).status_code == 200


# ── uploads ──────────────────────────────────────────────────────────────────

def test_upload_never_overwrites(app_env):
    c, _ = _client(app_env)
    for body in (b"first", b"second"):
        r = c.post("/api/files/upload", files={"f": ("notes.txt", body)})
        assert r.status_code == 200
    inbox = app_env.dir / "inbox"
    assert (inbox / "notes.txt").read_bytes() == b"first"
    assert (inbox / "notes (1).txt").read_bytes() == b"second"


def test_upload_refused_when_declared_too_big(app_env, monkeypatch):
    import llamawatch.routes.knowledge as kn
    monkeypatch.setattr(kn, "_MAX_UPLOAD_MB", 0)
    c, _ = _client(app_env)
    r = c.post("/api/files/upload", files={"f": ("big.bin", b"x" * 10)})
    assert r.status_code == 413
    assert not (app_env.dir / "inbox" / "big.bin").exists()


def test_upload_strips_path_parts(app_env):
    c, _ = _client(app_env)
    r = c.post("/api/files/upload", files={"f": ("../../escape.txt", b"x")})
    assert r.json()["saved"] == ["escape.txt"]
    assert (app_env.dir / "inbox" / "escape.txt").exists()


# ── terminal environment ─────────────────────────────────────────────────────

def test_terminal_env_drops_secrets():
    from llamawatch.routes.chat import _terminal_env
    env = _terminal_env({"PATH": "/bin", "HOME": "/h", "LANG": "C", "DB_PASSWORD": "x",
                         "GH_TOKEN": "x", "OPENAI_API_KEY": "x", "CLIENT_SECRET": "x",
                         "PREDICTIONS_DSN": "x", "KEYBOARD_LAYOUT": "gb"})
    assert sorted(env) == ["HOME", "KEYBOARD_LAYOUT", "LANG", "PATH"]


# ── stored secrets ───────────────────────────────────────────────────────────

def test_predictions_dsn_is_encrypted_at_rest():
    from llamawatch.config import decrypt_secrets, encrypt_secrets
    dsn = "postgresql://reader:pw@db.example.com/stats"
    stored = encrypt_secrets({"predictions_dsn": dsn})
    assert stored["predictions_dsn"] != dsn
    assert decrypt_secrets(stored)["predictions_dsn"] == dsn
