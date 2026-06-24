"""Tests for the /api/docker/{machine}/{container_id}/{action} endpoint."""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

import llamawatch.config as config_mod


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_config():
    config_mod.reset_config()
    yield
    config_mod.reset_config()


@pytest.fixture()
def tmp_cfg(tmp_path):
    cfg = {
        "port": 8450,
        "host": "0.0.0.0",
        "auth_enabled": False,
        "backends": [],
        "widgets": {"enabled": []},
        "services": [],
        "model_names": {},
        "auth_password_hash": "",
    }
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    config_mod.reset_config()
    config_mod.load_config(config_dir=tmp_path)
    return tmp_path


_REMOTE_HOSTS_MAP = {
    "TC1": {"host": "10.0.0.11", "user": "testuser"},
    "TC2": {"host": "10.0.0.12", "user": "testuser"},
}


@pytest.fixture()
def client(tmp_cfg, monkeypatch):
    from llamawatch.server import app
    import llamawatch.server as srv
    srv._config = config_mod.load_config()
    srv._adapters = MagicMock()
    srv._collector_registry = MagicMock()
    # Fleet hosts are now config-driven; pin the remote map and local name so
    # the M5/TC1/TC2 assertions hold regardless of the test machine's config.
    monkeypatch.setattr(srv, "_remote_hosts_map", lambda: dict(_REMOTE_HOSTS_MAP))
    monkeypatch.setattr(srv, "_local_machine_name", lambda: "M5")
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


# ── Tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_invalid_action_returns_400(client):
    """Unknown action returns 400."""
    async with client as c:
        resp = await c.post("/api/docker/M5/abc123def456/kill")
    assert resp.status_code == 400
    assert "Invalid action" in resp.json()["message"]


@pytest.mark.asyncio
async def test_unknown_machine_returns_400(client):
    """Unknown machine name returns 400."""
    async with client as c:
        resp = await c.post("/api/docker/BOGUS/abc123/restart")
    assert resp.status_code == 400
    assert "Unknown machine" in resp.json()["message"]


@pytest.mark.asyncio
async def test_m5_docker_unavailable_returns_503(client):
    """M5 action returns 503 when Docker socket is absent."""
    from llamawatch.collectors import docker_collector
    with patch.object(docker_collector, "docker_available", return_value=False):
        async with client as c:
            resp = await c.post("/api/docker/M5/abc123def456/start")
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_m5_docker_success(client):
    """M5 action succeeds when docker_post returns ok."""
    from llamawatch.collectors import docker_collector
    with patch.object(docker_collector, "docker_available", return_value=True), \
         patch.object(docker_collector, "_docker_post", return_value={}):
        async with client as c:
            resp = await c.post("/api/docker/M5/abc123def456/restart")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_tc1_action_ssh_success(client):
    """TC1 action succeeds when SSH returns rc=0."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = ""
    mock_result.stderr = ""
    with patch("subprocess.run", return_value=mock_result):
        async with client as c:
            resp = await c.post("/api/docker/TC1/mycontainer/stop")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_tc2_action_ssh_failure_returns_500(client):
    """TC2 action returns 500 when SSH returns non-zero."""
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_result.stderr = "No such container: mycontainer"
    with patch("subprocess.run", return_value=mock_result):
        async with client as c:
            resp = await c.post("/api/docker/TC2/mycontainer/start")
    assert resp.status_code == 500
    assert "No such container" in resp.json()["message"]


@pytest.mark.asyncio
async def test_tc1_action_timeout_returns_504(client):
    """TC1 action returns 504 on SSH timeout."""
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=[], timeout=15)):
        async with client as c:
            resp = await c.post("/api/docker/TC1/mycontainer/restart")
    assert resp.status_code == 504
    assert "timeout" in resp.json()["message"].lower()


@pytest.mark.asyncio
async def test_case_insensitive_machine(client):
    """Machine names are matched case-insensitively (tc1 == TC1)."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = ""
    mock_result.stderr = ""
    with patch("subprocess.run", return_value=mock_result):
        async with client as c:
            resp = await c.post("/api/docker/tc1/mycontainer/stop")
    assert resp.status_code == 200
