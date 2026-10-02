"""Tests for the /api/quick-action/{id} endpoint (the single shell-action path)."""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest
import httpx

import llamawatch.config as config_mod


@pytest.fixture(autouse=True)
def _reset_config():
    config_mod.reset_config()
    yield
    config_mod.reset_config()


@pytest.fixture()
def tmp_config(tmp_path):
    cfg = {
        "port": 8400,
        "host": "0.0.0.0",
        "auth_enabled": False,
        "backends": [],
        "services": [],
        "model_names": {},
        "auth_password_hash": "",
        "quick_actions": [
            {"id": "hello", "icon": "x", "label": "Hello", "shell": "echo hello"},
            {"id": "noshell", "icon": "x", "label": "NoShell", "shell": ""},
            {"id": "slow", "icon": "x", "label": "Slow", "shell": "sleep 999"},
            {"id": "big", "icon": "x", "label": "Big",
             "shell": "python3 -c \"import sys; sys.stdout.write('A'*20000)\""},
        ],
    }
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    config_mod.reset_config()
    config_mod.load_config(config_dir=tmp_path)
    return tmp_path


@pytest.fixture()
def app_client(tmp_config):
    from llamawatch.server import app
    import llamawatch.server as srv
    srv._config = config_mod.load_config()
    srv._adapters = MagicMock()
    srv._collector_registry = MagicMock()
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


@pytest.mark.asyncio
async def test_unknown_id_returns_404(app_client):
    async with app_client as c:
        r = await c.post("/api/quick-action/does-not-exist")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_action_with_no_command_returns_400(app_client):
    async with app_client as c:
        r = await c.post("/api/quick-action/noshell")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_valid_action_runs_and_returns_stdout(app_client):
    async with app_client as c:
        r = await c.post("/api/quick-action/hello")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["returncode"] == 0
    assert "hello" in data["stdout"]
    assert "stderr" in data


@pytest.mark.asyncio
async def test_output_capped_at_10kb(app_client):
    async with app_client as c:
        r = await c.post("/api/quick-action/big")
    assert r.status_code == 200
    data = r.json()
    # 20000 bytes of output, sliced to the 10 KB cap
    assert len(data["stdout"]) == 10240


@pytest.mark.asyncio
async def test_timeout_returns_408(app_client):
    # Patch asyncio.wait_for to fire immediately, closing the un-awaited
    # proc.communicate() coroutine so it isn't reported as "never awaited".
    async def mock_wait_for(coro, timeout):
        coro.close()
        raise asyncio.TimeoutError()

    with patch("asyncio.wait_for", side_effect=mock_wait_for):
        async with app_client as c:
            r = await c.post("/api/quick-action/slow")
    assert r.status_code == 408
    assert "timed out" in r.json()["error"].lower()


@pytest.mark.asyncio
async def test_gate_denied_returns_403(app_client, monkeypatch):
    monkeypatch.setattr("llamawatch.security.action_allowed", lambda req, auth_enabled: False)
    async with app_client as c:
        r = await c.post("/api/quick-action/hello")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_successful_action_is_audited(app_client, monkeypatch):
    import llamawatch.audit as audit
    seen = {}
    monkeypatch.setattr(audit, "append", lambda action, **kw: seen.update({"action": action, **kw}))
    async with app_client as c:
        r = await c.post("/api/quick-action/hello")
    assert r.status_code == 200
    assert seen.get("action") == "quick_action"
    assert seen.get("outcome") == "ok"
    assert seen.get("target") == "hello"
