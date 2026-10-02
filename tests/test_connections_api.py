"""Tests for the Connections API endpoints."""

import json
from unittest.mock import MagicMock

import pytest
import httpx

import llamawatch.config as config_mod
import llamawatch.secrets_vault as sv


@pytest.fixture(autouse=True)
def _reset_config():
    """Reset config cache between tests."""
    config_mod.reset_config()
    yield
    config_mod.reset_config()


@pytest.fixture(autouse=True)
def _reset_vault_key(tmp_path, monkeypatch):
    """Point secrets_vault at a tmp key file and clear the cache."""
    monkeypatch.setattr(sv, "_KEY_FILE", tmp_path / "secret.key")
    monkeypatch.delenv("LLAMAWATCH_SECRET_KEY", raising=False)
    sv._reset_key_cache()
    yield
    sv._reset_key_cache()


@pytest.fixture()
def tmp_config(tmp_path):
    """Create a temp config dir with config.json and point the module at it."""
    defaults = {
        "port": 8400,
        "host": "0.0.0.0",
        "auth_enabled": False,
        "backends": [{"type": "llamacpp", "url": "http://localhost:8080", "name": "local"}],
        "widgets": {"enabled": ["system"]},
        "services": [],
        "model_names": {},
        "auth_password_hash": "",
        "connections": {},
    }
    (tmp_path / "config.json").write_text(json.dumps(defaults))
    config_mod.reset_config()
    config_mod.load_config(config_dir=tmp_path)
    return tmp_path


@pytest.fixture()
def app_client(tmp_config):
    """Return an httpx.AsyncClient wired to the FastAPI app with mocked state."""
    from llamawatch.server import app

    import llamawatch.server as srv
    srv._config = config_mod.load_config()
    srv._adapters = MagicMock()
    srv._collector_registry = MagicMock()

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ), tmp_config


@pytest.mark.asyncio
async def test_list_connections_redacted(app_client):
    client, _ = app_client
    async with client as c:
        r = await c.get("/api/connections")
    assert r.status_code == 200
    body = r.json()
    assert "types" in body and "connections" in body


@pytest.mark.asyncio
async def test_create_connection_persists_encrypted(app_client, monkeypatch):
    monkeypatch.setattr("llamawatch.security.action_allowed", lambda req, auth_enabled: True)
    client, tmp_config = app_client
    async with client as c:
        r = await c.put("/api/connections/m5", json={"type": "ssh_host", "host": "h", "user": "u", "password": "pw"})
    assert r.status_code == 200
    # Verify it was persisted to config.local.json
    local_path = tmp_config / "config.local.json"
    assert local_path.exists()
    saved = json.loads(local_path.read_text())
    assert "connections" in saved
    assert "m5" in saved["connections"]
    # Password must be encrypted on disk
    assert saved["connections"]["m5"]["password"].startswith("enc:")


@pytest.mark.asyncio
async def test_create_invalid_connection_400(app_client, monkeypatch):
    monkeypatch.setattr("llamawatch.security.action_allowed", lambda req, auth_enabled: True)
    client, _ = app_client
    async with client as c:
        r = await c.put("/api/connections/bad", json={"type": "ssh_host", "user": "u"})  # missing host
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_create_connection_blocked_when_gate_denies(app_client, monkeypatch):
    monkeypatch.setattr("llamawatch.security.action_allowed", lambda req, auth_enabled: False)
    client, _ = app_client
    async with client as c:
        r = await c.put("/api/connections/x", json={"type": "ssh_host", "host": "h", "user": "u"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_delete_connection_removes_it(app_client, monkeypatch):
    monkeypatch.setattr("llamawatch.security.action_allowed", lambda req, auth_enabled: True)
    client, tmp_config = app_client
    async with client as c:
        await c.put("/api/connections/tmp", json={"type": "ssh_host", "host": "h", "user": "u"})
        r = await c.delete("/api/connections/tmp")
    assert r.status_code == 200
    local_path = tmp_config / "config.local.json"
    assert local_path.exists()
    saved = json.loads(local_path.read_text())
    assert "tmp" not in saved.get("connections", {})


@pytest.mark.asyncio
async def test_audit_blocked_when_gate_denies(app_client, monkeypatch):
    monkeypatch.setattr("llamawatch.security.action_allowed", lambda req, auth_enabled: False)
    client, _ = app_client
    async with client as c:
        r = await c.get("/api/audit")
    assert r.status_code == 403
