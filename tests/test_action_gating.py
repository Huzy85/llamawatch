"""Integration tests: security gate + audit logging on write endpoints."""
import json
import subprocess
import types
from unittest.mock import MagicMock

import httpx
import pytest

import llamawatch.config as config_mod


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_config():
    config_mod.reset_config()
    yield
    config_mod.reset_config()


@pytest.fixture()
def tmp_config_with_services(tmp_path):
    """Temp config with auth disabled and a sample service registered."""
    defaults = {
        "port": 8400,
        "host": "0.0.0.0",
        "auth_enabled": False,
        "backends": [{"type": "llamacpp", "url": "http://localhost:8080", "name": "local"}],
        "quick_actions": [
            {"id": "echo", "icon": "x", "label": "Echo", "shell": "echo hello"}
        ],
        "services": [
            {"name": "swap-proxy", "type": "user", "unit": "swap-proxy.service"}
        ],
        "model_names": {},
        "auth_password_hash": "",
    }
    (tmp_path / "config.json").write_text(json.dumps(defaults))
    config_mod.reset_config()
    config_mod.load_config(config_dir=tmp_path)
    return tmp_path


@pytest.fixture()
def app_client(tmp_config_with_services):
    """Return an httpx.AsyncClient wired to the FastAPI app."""
    from llamawatch.server import app
    import llamawatch.server as srv

    srv._config = config_mod.load_config()
    srv._adapters = MagicMock()
    srv._collector_registry = MagicMock()

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


# ── Gate tests — /api/services ────────────────────────────────────────


@pytest.mark.asyncio
async def test_service_action_blocked_when_gate_denies(app_client, monkeypatch):
    """Remote caller gets 403 when the security gate denies the request."""
    monkeypatch.setattr("llamawatch.security.action_allowed", lambda req, auth_enabled: False)
    mock_run = MagicMock()
    monkeypatch.setattr(subprocess, "run", mock_run)
    async with app_client as c:
        r = await c.post("/api/services/swap-proxy/restart")
    assert r.status_code == 403
    mock_run.assert_not_called()


@pytest.mark.asyncio
async def test_service_action_audits(app_client, monkeypatch):
    """Allowed service action records audit entry with correct action name."""
    import llamawatch.audit as audit

    seen = {}
    monkeypatch.setattr(audit, "append", lambda action, **kw: seen.update({"action": action, **kw}))
    monkeypatch.setattr("llamawatch.security.action_allowed", lambda req, auth_enabled: True)
    monkeypatch.setattr(
        "llamawatch.server.get_service",
        lambda n: {"type": "user", "unit": "x.service"},
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    async with app_client as c:
        r = await c.post("/api/services/swap-proxy/restart")
    assert r.status_code == 200
    assert seen.get("action") == "service_restart"


# ── Gate tests — /api/timers ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_timer_trigger_blocked_when_gate_denies(app_client, monkeypatch):
    """Timer trigger returns 403 when gate denies."""
    monkeypatch.setattr("llamawatch.security.action_allowed", lambda req, auth_enabled: False)
    mock_run = MagicMock()
    monkeypatch.setattr(subprocess, "run", mock_run)
    async with app_client as c:
        r = await c.post("/api/timers/news-fetcher/trigger")
    assert r.status_code == 403
    mock_run.assert_not_called()


@pytest.mark.asyncio
async def test_timer_trigger_audits(app_client, monkeypatch):
    """Allowed timer trigger records an audit entry."""
    import llamawatch.audit as audit

    seen = {}
    monkeypatch.setattr(audit, "append", lambda action, **kw: seen.update({"action": action, **kw}))
    monkeypatch.setattr("llamawatch.security.action_allowed", lambda req, auth_enabled: True)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    async with app_client as c:
        r = await c.post("/api/timers/news-fetcher/trigger")
    assert r.status_code == 200
    assert seen.get("action") == "timer_trigger"
    assert seen.get("target") == "news-fetcher"


# ── Gate tests — /api/docker ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_docker_action_blocked_when_gate_denies(app_client, monkeypatch):
    """Docker action returns 403 when gate denies."""
    from llamawatch.collectors import docker_collector
    monkeypatch.setattr("llamawatch.security.action_allowed", lambda req, auth_enabled: False)
    mock_docker_post = MagicMock()
    monkeypatch.setattr(docker_collector, "_docker_post", mock_docker_post)
    async with app_client as c:
        r = await c.post("/api/docker/abc123/restart")
    assert r.status_code == 403
    mock_docker_post.assert_not_called()


@pytest.mark.asyncio
async def test_docker_action_audits(app_client, monkeypatch):
    """Allowed docker action records an audit entry."""
    import llamawatch.audit as audit
    from llamawatch.collectors import docker_collector

    seen = {}
    monkeypatch.setattr(audit, "append", lambda action, **kw: seen.update({"action": action, **kw}))
    monkeypatch.setattr("llamawatch.security.action_allowed", lambda req, auth_enabled: True)
    monkeypatch.setattr(docker_collector, "docker_available", lambda: True)
    monkeypatch.setattr(docker_collector, "_docker_post", lambda path: None)
    async with app_client as c:
        r = await c.post("/api/docker/abc123/restart")
    assert r.status_code == 200
    assert seen.get("action") == "docker_restart"
    assert seen.get("target") == "abc123"


# ── Gate tests — /api/quick-action (shell) ───────────────────────────


@pytest.mark.asyncio
async def test_shell_action_blocked_when_gate_denies(app_client, monkeypatch):
    """Quick action returns 403 when the gate denies (before running anything)."""
    import llamawatch.audit as audit
    monkeypatch.setattr("llamawatch.security.action_allowed", lambda req, auth_enabled: False)
    mock_audit = MagicMock()
    monkeypatch.setattr(audit, "append", mock_audit)
    async with app_client as c:
        r = await c.post("/api/quick-action/echo")
    assert r.status_code == 403
    mock_audit.assert_not_called()


@pytest.mark.asyncio
async def test_shell_action_audits_on_success(app_client, monkeypatch):
    """A quick action records an audit entry after the command runs."""
    import llamawatch.audit as audit

    seen = {}
    monkeypatch.setattr(audit, "append", lambda action, **kw: seen.update({"action": action, **kw}))
    monkeypatch.setattr("llamawatch.security.action_allowed", lambda req, auth_enabled: True)
    async with app_client as c:
        r = await c.post("/api/quick-action/echo")
    assert r.status_code == 200
    assert seen.get("action") == "quick_action"
    assert seen.get("outcome") == "ok"
