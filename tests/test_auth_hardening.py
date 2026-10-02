"""Integration tests for the two auth-hardening fixes:
  1. the /ws dashboard socket enforces the auth gate before accepting;
  2. /auth/login is rate-limited per client IP.
"""

import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import llamawatch.config as config_mod
from llamawatch import security


@pytest.fixture()
def app_env(tmp_path):
    cfg = {
        "port": 8450,
        "host": "127.0.0.1",
        "auth_enabled": False,
        "backends": [],
        "services": [],
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
    security.reset_rate_limits()
    yield srv
    config_mod.reset_config()
    security.reset_rate_limits()


# ── /ws auth gate ─────────────────────────────────────────────────────────────

def test_ws_dashboard_rejects_unauthenticated(app_env, monkeypatch):
    """With auth on and no session cookie, the live-feed socket must refuse to
    connect (close before accept), not stream the dashboard to anyone."""
    monkeypatch.setattr(app_env, "is_auth_enabled", lambda: True)
    from llamawatch.server import app
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws"):
                pass


def test_ws_dashboard_rejects_remote_when_auth_off(app_env, monkeypatch):
    """Auth off: a non-local client (no testclient host) must be refused."""
    monkeypatch.setattr(app_env, "is_auth_enabled", lambda: False)
    # Make the security gate see a remote client regardless of transport.
    monkeypatch.setattr(security, "is_local_request", lambda req: False)
    from llamawatch.server import app
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws"):
                pass


# ── Login rate limit ──────────────────────────────────────────────────────────

def test_login_throttled_after_max_attempts(app_env):
    from llamawatch.server import app
    with TestClient(app) as client:
        # No password configured → every attempt is a 401, until the limiter trips (5 per 300s).
        for _ in range(5):
            r = client.post("/auth/login", json={"password": "wrong"})
            assert r.status_code == 401
        r = client.post("/auth/login", json={"password": "wrong"})
        assert r.status_code == 429
        assert r.headers.get("Retry-After") == "300"


def test_login_throttle_keys_on_forwarded_ip(app_env):
    """Behind a tunnel every visitor is 127.0.0.1; the limiter must key on X-Forwarded-For."""
    from llamawatch.server import app
    with TestClient(app) as client:
        for _ in range(5):
            r = client.post("/auth/login", json={"password": "wrong"}, headers={"X-Forwarded-For": "203.0.113.5"})
            assert r.status_code == 401
        r = client.post("/auth/login", json={"password": "wrong"}, headers={"X-Forwarded-For": "203.0.113.5"})
        assert r.status_code == 429
        # a different visitor is not locked out by the first one
        r = client.post("/auth/login", json={"password": "wrong"}, headers={"X-Forwarded-For": "203.0.113.6"})
        assert r.status_code == 401


def test_login_global_cap(app_env):
    """Rotating addresses cannot exceed the global cap (30 per window)."""
    from llamawatch.server import app
    with TestClient(app) as client:
        for i in range(30):
            r = client.post("/auth/login", json={"password": "wrong"}, headers={"X-Forwarded-For": f"198.51.100.{i}"})
            assert r.status_code == 401, i
        r = client.post("/auth/login", json={"password": "wrong"}, headers={"X-Forwarded-For": "198.51.100.99"})
        assert r.status_code == 429
