"""Smoke tests for the button-backing endpoints touched by the config-driven
refactor. These catch wiring/route regressions automatically; interactive
DOM behaviour (clicking buttons in a browser) is still verified manually.
"""

import json
from unittest.mock import MagicMock

import httpx
import pytest

import llamawatch.config as config_mod


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
        "services": [],
        "model_names": {},
        "quick_actions": [
            {"id": "qa-echo", "icon": "x", "label": "Echo", "shell": "echo hello"}
        ],
        "fleet": {"hosts": [{"name": "Box", "local": True, "color": "#2dd4bf"}]},
        "dashboard_name": "TestBoard",
    }
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    config_mod.reset_config()
    config_mod.load_config(config_dir=tmp_path)
    return tmp_path


@pytest.fixture()
def client(tmp_cfg):
    from llamawatch.server import app
    import llamawatch.server as srv
    srv._config = config_mod.load_config()
    srv._adapters = MagicMock()
    srv._adapters.get_all.return_value = []
    srv._collector_registry = MagicMock()
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


@pytest.mark.asyncio
async def test_settings_get_exposes_config(client):
    async with client as c:
        r = await c.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["dashboard_name"] == "TestBoard"
    assert body["fleet"]["hosts"][0]["name"] == "Box"


@pytest.mark.asyncio
async def test_settings_put_round_trip(client):
    async with client as c:
        r = await c.put("/api/settings", json={"dashboard_name": "Renamed"})
        assert r.status_code == 200 and r.json()["status"] == "ok"
        r2 = await c.get("/api/settings")
    assert r2.json()["dashboard_name"] == "Renamed"


@pytest.mark.asyncio
async def test_model_names_removal_persists(client):
    """Removing a model name must not linger (wholesale replace, not merge)."""
    async with client as c:
        await c.put("/api/settings", json={"model_names": {"a": "A", "b": "B"}})
        await c.put("/api/settings", json={"model_names": {"a": "A"}})
        r = await c.get("/api/settings")
    assert r.json()["model_names"] == {"a": "A"}


@pytest.mark.asyncio
async def test_quick_action_runs_configured_command(client):
    async with client as c:
        r = await c.post("/api/quick-action/qa-echo")
    assert r.status_code == 200
    body = r.json()
    assert body["returncode"] == 0
    assert "hello" in body["stdout"]


@pytest.mark.asyncio
async def test_quick_action_unknown_id_404(client):
    async with client as c:
        r = await c.post("/api/quick-action/does-not-exist")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_predictions_disabled_without_dsn(client):
    async with client as c:
        r = await c.get("/api/predictions")
    assert r.status_code == 200
    assert r.json()["disabled"] is True


@pytest.mark.asyncio
async def test_search_disabled_without_url(client):
    async with client as c:
        r = await c.post("/api/search", params={"q": "anything"})
    assert r.status_code == 200
    assert r.json().get("disabled") is True


@pytest.mark.asyncio
async def test_manifest_injects_dashboard_name(client):
    async with client as c:
        r = await c.get("/static/manifest.json")
    assert r.status_code == 200
    assert r.json()["name"] == "TestBoard"


@pytest.mark.asyncio
async def test_root_redirects_to_studio(client):
    async with client as c:
        r = await c.get("/", follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "/studio"


def test_config_dir_selected_by_local_override(tmp_path, monkeypatch):
    """Regression: a user dir with only config.local.json (no config.json) must
    be selected, with the base template loaded from the package. This is the
    pip-install case — `init` writes config.local.json to ~/.config/llamawatch/
    which has no config.json. Server must still read it."""
    import json as _json
    import llamawatch.config as cfg
    user_dir = tmp_path / ".config" / "llamawatch"
    user_dir.mkdir(parents=True)
    (user_dir / "config.local.json").write_text(_json.dumps({"backends": [{"name": "X", "url": "u"}]}))
    monkeypatch.setattr(cfg.Path, "cwd", staticmethod(lambda: tmp_path))   # neutral CWD
    monkeypatch.setattr(cfg.Path, "home", staticmethod(lambda: tmp_path))  # HOME=tmp
    cfg.reset_config()
    found = cfg._find_config_dir()
    assert found == user_dir, f"expected user dir, got {found}"
    loaded = cfg.load_config(config_dir=found)
    assert len(loaded["backends"]) == 1   # local override read
    assert "studio_panels" in loaded       # base template merged from package
    cfg.reset_config()


@pytest.mark.asyncio
async def test_chat_extract_text_file(client):
    """Chat attachment extraction returns decoded text for a text file."""
    files = {"file": ("notes.md", b"# Title\nhello world", "text/markdown")}
    async with client as c:
        r = await c.post("/api/chat/extract", files=files)
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "notes.md"
    assert "hello world" in body["text"]
    assert body["truncated"] is False


@pytest.mark.asyncio
async def test_chat_extract_no_file_400(client):
    async with client as c:
        r = await c.post("/api/chat/extract", data={"x": "y"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_write_endpoints_blocked_from_nonlocal_when_auth_off(tmp_cfg):
    """SECURITY: with auth off, write/exec endpoints must reject a non-localhost
    client (the default-0.0.0.0 RCE blocker). 127.0.0.1 is allowed; 203.0.113.5 is not."""
    from llamawatch.server import app
    import llamawatch.server as srv
    srv._config = config_mod.load_config()
    srv._adapters = MagicMock(); srv._adapters.get_all.return_value = []
    srv._collector_registry = MagicMock()
    attacker = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("203.0.113.5", 9999)),
        base_url="http://testserver")
    async with attacker as c:
        assert (await c.put("/api/settings", json={"dashboard_name": "x"})).status_code == 403
        assert (await c.post("/api/quick-action/qa-echo")).status_code == 403
        assert (await c.post("/api/settings/test-backend", json={"url": "http://x"})).status_code == 403


@pytest.mark.asyncio
async def test_predictions_dsn_password_redacted(tmp_path):
    """SECURITY: a password embedded in a DSN must not leak via GET /api/settings."""
    cfg = {"port": 8450, "host": "127.0.0.1", "auth_enabled": False, "backends": [],
           "predictions_dsn": "postgresql://user:SUPERSECRET@db:5432/x"}
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    config_mod.reset_config(); config_mod.load_config(config_dir=tmp_path)
    from llamawatch.server import app
    import llamawatch.server as srv
    srv._config = config_mod.load_config()
    srv._adapters = MagicMock(); srv._adapters.get_all.return_value = []
    srv._collector_registry = MagicMock()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    async with client as c:
        body = (await c.get("/api/settings")).json()
    assert "SUPERSECRET" not in json.dumps(body)
    config_mod.reset_config()
