"""Tests for the Settings API endpoints."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import httpx

import llamawatch.config as config_mod


@pytest.fixture(autouse=True)
def _reset_config():
    """Reset config cache between tests."""
    config_mod.reset_config()
    yield
    config_mod.reset_config()


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
        "auth_password_hash": "secret123",
    }
    (tmp_path / "config.json").write_text(json.dumps(defaults))
    config_mod.reset_config()
    config_mod.load_config(config_dir=tmp_path)
    return tmp_path


@pytest.fixture()
def app_client(tmp_config):
    """Return an httpx.AsyncClient wired to the FastAPI app with mocked state."""
    from llamawatch.server import app, _adapters, _collector_registry

    # Inject global state so startup isn't needed
    import llamawatch.server as srv
    srv._config = config_mod.load_config()
    srv._adapters = MagicMock()
    srv._collector_registry = MagicMock()

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ), tmp_config


# ── GET /api/settings ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_settings_returns_redacted(app_client):
    """GET /api/settings returns config with credential fields redacted."""
    client, _ = app_client
    async with client:
        resp = await client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["auth_password_hash"] == "[REDACTED]"
    assert "_version" in data


@pytest.mark.asyncio
async def test_get_settings_has_version(app_client):
    """GET /api/settings includes _version field."""
    client, _ = app_client
    async with client:
        resp = await client.get("/api/settings")
    data = resp.json()
    assert isinstance(data["_version"], str)
    assert len(data["_version"]) > 0


# ── PUT /api/settings ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_settings_updates_config(app_client):
    """PUT /api/settings writes config.local.json and returns ok."""
    client, tmp_config = app_client
    async with client:
        resp = await client.put(
            "/api/settings",
            json={"port": 9999},
        )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

    # Verify config.local.json was written
    local_path = tmp_config / "config.local.json"
    assert local_path.exists()
    saved = json.loads(local_path.read_text())
    assert saved["port"] == 9999


@pytest.mark.asyncio
async def test_put_settings_encrypts_credentials(app_client):
    """PUT /api/settings encrypts credential keys on disk (no longer drops them)."""
    from llamawatch import secrets_vault as sv
    client, tmp_config = app_client
    async with client:
        resp = await client.put(
            "/api/settings",
            json={"password": "hunter2", "port": 7777},
        )
    assert resp.status_code == 200
    local_path = tmp_config / "config.local.json"
    saved = json.loads(local_path.read_text())
    # password must be present on disk but Fernet-encrypted
    assert "password" in saved
    assert saved["password"].startswith("enc:")
    assert sv.decrypt(saved["password"]) == "hunter2"
    assert saved["port"] == 7777


# ── POST /api/settings/test-backend ──────────────────────────────────


@pytest.mark.asyncio
async def test_test_backend_success(app_client):
    """POST /api/settings/test-backend returns health and model on success."""
    client, _ = app_client

    mock_adapter = MagicMock()
    mock_adapter.health.return_value = "healthy"
    mock_adapter.model_name.return_value = "test-model-7b"

    with patch("llamawatch.server.create_adapter", return_value=mock_adapter):
        async with client:
            resp = await client.post(
                "/api/settings/test-backend",
                json={"url": "http://localhost:8080", "type": "llamacpp", "name": "test"},
            )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["model"] == "test-model-7b"


@pytest.mark.asyncio
async def test_test_backend_error(app_client):
    """POST /api/settings/test-backend returns error on exception."""
    client, _ = app_client

    with patch("llamawatch.server.create_adapter", side_effect=ConnectionError("refused")):
        async with client:
            resp = await client.post(
                "/api/settings/test-backend",
                json={"url": "http://localhost:9999", "type": "llamacpp", "name": "bad"},
            )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "error"
    assert "refused" in data["error"]


# ── GET /api/settings/options/{key} ──────────────────────────────────


@pytest.mark.asyncio
async def test_options_discovered_timers(app_client):
    """GET /api/settings/options/discovered_timers returns timer list."""
    client, _ = app_client

    mock_output = "news-fetcher.timer\ndaily-scheduler.timer\n"
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=mock_output, returncode=0)
        async with client:
            resp = await client.get("/api/settings/options/discovered_timers")

    assert resp.status_code == 200
    data = resp.json()
    assert "options" in data
    assert "news-fetcher" in data["options"]
    assert "daily-scheduler" in data["options"]


@pytest.mark.asyncio
async def test_options_unknown_key(app_client):
    """GET /api/settings/options/<unknown> returns empty list."""
    client, _ = app_client
    async with client:
        resp = await client.get("/api/settings/options/nonexistent")
    assert resp.status_code == 200
    assert resp.json() == {"options": []}


# ── POST /api/settings/discover ──────────────────────────────────────


@pytest.mark.asyncio
async def test_discover_returns_scan_results(app_client):
    """POST /api/settings/discover returns backends, services, sensors."""
    client, _ = app_client

    with patch("llamawatch.server.scan_backends", return_value=[{"type": "llamacpp", "url": "http://localhost:8080"}]), \
         patch("llamawatch.server.scan_sensors", return_value={"temps": [], "gpu_util": False, "nvidia": False}), \
         patch("llamawatch.server.scan_services", return_value=[]):
        async with client:
            resp = await client.post("/api/settings/discover")

    assert resp.status_code == 200
    data = resp.json()
    assert "backends" in data
    assert "services" in data
    assert "sensors" in data
    assert len(data["backends"]) == 1
