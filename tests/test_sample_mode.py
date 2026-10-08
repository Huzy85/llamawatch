"""Sample-data mode: fixed snapshot, no collectors, no writes, nothing personal."""
import json
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

FIX = Path(__file__).parent / "ui" / "fixtures"
SNAP = FIX / "snapshot.json"


@pytest.fixture
def sample_client(monkeypatch):
    from llamawatch import config, sample, ws_hub
    monkeypatch.setenv(sample.ENV, str(SNAP))
    sample.reset(); config.reset_config(); ws_hub.reset_hub()
    config.load_config(config_dir=FIX)
    import llamawatch.collectors as coll
    def _boom(*a, **k):
        raise AssertionError("real collectors must not run in sample mode")
    monkeypatch.setattr(coll.CollectorRegistry, "collect_all", _boom)
    monkeypatch.setattr(coll.CollectorRegistry, "collect_one", _boom)
    from llamawatch.server import app
    with TestClient(app) as c:
        yield c
    sample.reset(); config.reset_config(); ws_hub.reset_hub()


def test_registry_returns_snapshot():
    from llamawatch.sample import SampleRegistry
    reg = SampleRegistry({"system": {"cpu_pct": 37}})
    assert reg.collect_all() == {"system": {"cpu_pct": 37}}
    assert reg.collect_one("system") == {"system": {"cpu_pct": 37}}
    assert reg.collect_one("nope") == {"nope": {}}
    assert reg.get_manifest() == []
    out = reg.collect_all(); out["system"]["cpu_pct"] = 99
    assert reg.collect_all()["system"]["cpu_pct"] == 37  # copies, not shared


def test_sample_mode_uses_sample_registry(sample_client):
    import llamawatch.server as srv
    from llamawatch.sample import SampleRegistry
    assert isinstance(srv._collector_registry, SampleRegistry)


def test_rest_served_from_snapshot(sample_client):
    r = sample_client.get("/api/models")
    assert r.status_code == 200
    assert r.json()["models"][0]["model_id"] == "demo-model-30b"
    assert sample_client.get("/api/predictions?limit=200").json()["predictions"][0]["id"] == 1


def test_research_log_honours_since(sample_client):
    path = "/api/research/runs/20260101-104800-a1b2c3/events"
    full = sample_client.get(path + "?since=0").json()["events"]
    assert len(full) > 2
    assert [e["msg"] for e in sample_client.get(path + "?since=2").json()["events"]] == [e["msg"] for e in full[2:]]
    assert abs(full[-1]["t"] - time.time()) < 60   # the run clock reads minutes, not years


def test_settings_passthrough_uses_fixture_config(sample_client):
    cfg = sample_client.get("/api/settings").json()
    assert cfg["fleet"]["hosts"][0]["name"] == "node-a"


def test_unknown_api_get_is_404(sample_client):
    assert sample_client.get("/api/press-room/search").status_code == 404


@pytest.mark.parametrize("method,path", [("post", "/api/quick-action/x"), ("put", "/api/settings"),
                                         ("delete", "/api/connections/x"), ("post", "/auth/login")])
def test_writes_blocked(sample_client, method, path):
    assert getattr(sample_client, method)(path).status_code == 403


def test_websockets_refused(sample_client):
    from starlette.websockets import WebSocketDisconnect
    for path in ("/ws", "/ws/terminal/new", "/ws/chat/x"):
        with pytest.raises(WebSocketDisconnect):
            with sample_client.websocket_connect(path) as ws:
                ws.receive_text()


def test_sample_off_by_default(monkeypatch):
    from llamawatch import sample
    monkeypatch.delenv(sample.ENV, raising=False)
    sample.reset()
    assert sample.current() is None


FORBIDDEN = re.compile(r"steamvibe|petru|huzur|192\.168\.|\bnwl\b|hermes|\bm5\b|/home/", re.I)


@pytest.mark.parametrize("f", sorted(p.name for p in FIX.glob("*.json")))
def test_fixtures_hold_nothing_personal(f):
    text = (FIX / f).read_text()
    json.loads(text)
    assert not FORBIDDEN.search(text), FORBIDDEN.search(text).group(0)
