"""Research API: model list, paid-run approval, run lifecycle, path safety."""

import json
import time

import pytest
from fastapi.testclient import TestClient

import llamawatch.config as config_mod
from llamawatch.research.llm import Model

from tests.test_research_pipeline import FakeReader, FakeSearcher, fake_chat


@pytest.fixture
def client(tmp_path, monkeypatch):
    cfg = {"port": 8451, "host": "0.0.0.0", "auth_enabled": False, "auth_password_hash": "",
           "widgets": {"enabled": []}, "services": [], "model_names": {},
           "research": {"data_dir": str(tmp_path / "runs"), "workers": 2,
                        "models": [
                            {"name": "Free box", "base_url": "http://127.0.0.1:9/v1", "model": "m"},
                            {"name": "Paid API", "base_url": "https://api.example.com/v1", "model": "p",
                             "api_key": "k", "price_in": 1.0, "price_out": 4.0}]}}
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    config_mod.reset_config()
    config_mod.load_config(config_dir=tmp_path)
    import llamawatch.server as srv
    srv._config = config_mod.load_config()
    import llamawatch.routes.research as rr
    monkeypatch.setattr(Model, "chat", fake_chat)
    monkeypatch.setattr(rr, "Searcher", lambda cfg: FakeSearcher())
    monkeypatch.setattr(rr, "Reader", lambda cfg, cache_dir=None: FakeReader())
    yield TestClient(srv.app)
    config_mod.reset_config()


def test_models_and_estimate(client):
    d = client.get("/api/research/models").json()
    names = {m["name"]: m for m in d["models"]}
    assert names["Paid API"]["paid"] and not names["Free box"]["paid"]
    assert "api_key" not in json.dumps(d)
    e = client.post("/api/research/estimate", json={"model": "Paid API", "depth": "quick"}).json()
    assert e["cost"] > 0 and e["paid"]
    assert [x["id"] for x in d["depths"]] == ["quick", "full"]
    assert all(x["about"] and x["time"] for x in d["depths"]) and d["depth_note"]
    full = client.post("/api/research/estimate", json={"model": "Paid API", "depth": "full"}).json()
    old = client.post("/api/research/estimate", json={"model": "Paid API", "depth": "deep"}).json()
    assert full["cost"] == old["cost"] > e["cost"]


def test_paid_run_needs_approval(client):
    r = client.post("/api/research/start", json={"question": "Is the Widget 9 good?", "model": "Paid API"})
    assert r.status_code == 402 and r.json()["estimate"] > 0


def test_run_lifecycle(client):
    r = client.post("/api/research/start", json={"question": "Is the Widget 9 good for 120B models?",
                                                 "model": "Free box", "depth": "quick"})
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    for _ in range(100):
        d = client.get(f"/api/research/runs/{rid}").json()
        if d["meta"].get("status") != "running":
            break
        time.sleep(0.05)
    assert d["meta"]["status"] == "done", d["meta"]
    assert "## Sources" in d["report"]
    ev = client.get(f"/api/research/runs/{rid}/events?since=0").json()
    assert ev["events"] and ev["next"] == len(ev["events"])
    page = client.get(f"/api/research/runs/{rid}/pages/P1")
    assert page.status_code == 200 and "example" in page.text
    assert any(x["id"] == rid for x in client.get("/api/research/runs").json()["runs"])
    dr = client.post(f"/api/research/runs/{rid}/drafts", json={}).json()
    assert "short" in dr


@pytest.mark.parametrize("bad", ["../etc", "..%2F..%2Fetc", "20261007-000000-zzzzzz"])
def test_bad_run_ids(client, bad):
    assert client.get(f"/api/research/runs/{bad}").status_code == 404
    assert client.get(f"/api/research/runs/20261007-000000-abcdef/pages/{bad}").status_code == 404


def test_research_page_and_report_view(client, tmp_path):
    page = client.get("/research")
    assert page.status_code == 200 and 'id="rs-start"' in page.text
    assert client.get("/research/runs/20261007-000000-abcdef/report").status_code == 404
    assert client.get("/research/runs/..%2Fetc/report").status_code == 404

    run = tmp_path / "runs" / "20261007-120000-abc123"
    run.mkdir(parents=True)
    (run / "run.json").write_text(json.dumps({"question": "Q?", "model": "Free box", "depth": "Quick",
                                              "status": "failed: x", "trust": {"level": "weak"}}))
    r = client.get(f"/research/runs/{run.name}/report", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/research#run/{run.name}"

    (run / "report.md").write_text("# Title\n\nA line </script><script>alert(1)</script>\n")
    (run / "sources.json").write_text(json.dumps({"1": {"url": "https://example.com/a", "title": "A </script>"}}))
    html = client.get(f"/research/runs/{run.name}/report").text
    assert "</script><script>alert(1)" not in html and "<\\/script><script>alert(1)" in html
    data = html.split('id="reportData">', 1)[1].split("</script>", 1)[0]
    d = json.loads(data.replace("<\\/", "</"))
    assert d["runId"] == run.name and d["run"]["question"] == "Q?" and d["run"]["trust"]["level"] == "weak"
    assert d["sources"]["1"]["url"] == "https://example.com/a"


def test_adapter_spec_reads_context_window():
    from types import SimpleNamespace
    from llamawatch.research.llm import spec_from_adapter
    ad = SimpleNamespace(config={"context_window": 49152, "disable_thinking": True},
                         chat_completions_url=lambda: "http://x/v1/chat/completions", model_name=lambda: "m")
    spec = spec_from_adapter("Main", ad)
    assert spec.context == 49152 and spec.no_thinking
    ad.config = {}
    assert spec_from_adapter("Main", ad).context == 32768


def test_excluded_backends_are_not_offered(monkeypatch):
    from types import SimpleNamespace
    from llamawatch.routes import research as rr
    fake = SimpleNamespace(adapters={"Main": object(), "Kept": object()})
    monkeypatch.setattr(rr.srv, "_adapters", fake)
    monkeypatch.setattr(rr, "_cfg", lambda: {"exclude": ["Kept"]})
    assert set(rr._models()) == {"Main"}
    monkeypatch.setattr(rr, "_cfg", lambda: {})
    assert set(rr._models()) == {"Main", "Kept"}


def _api_client(tmp_path, monkeypatch, reply=None):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from llamawatch.routes import research as rr
    (tmp_path / "config.local.json").write_text("{}")
    monkeypatch.setattr(rr, "get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(rr, "reload_config", lambda config_dir=None: {})
    monkeypatch.setattr(rr.srv, "_adapters", None)
    monkeypatch.setattr(rr.security, "action_allowed", lambda *a: True)
    store = {"models": []}
    monkeypatch.setattr(rr, "_cfg", lambda: store)
    monkeypatch.setattr(rr, "_try_model", lambda e: reply or "")
    monkeypatch.setattr(rr, "encrypt_secrets", lambda o: {**o, "_enc": True})
    app = FastAPI(); app.include_router(rr.router)
    return TestClient(app), store


def test_add_online_api_saves_entry_with_key(tmp_path, monkeypatch):
    import json as _j
    c, store = _api_client(tmp_path, monkeypatch)
    r = c.post("/api/research/models", json={"name": "DeepSeek", "base_url": "https://api.example.com/v1",
               "model": "chat-1", "api_key": "sk-test", "price_in": "0.27", "price_out": "1.1"})
    assert r.status_code == 200, r.text
    saved = _j.loads((tmp_path / "config.local.json").read_text())
    assert saved["_enc"] is True
    e = saved["research"]["models"][0]
    assert e["name"] == "DeepSeek" and e["api_key"] == "sk-test" and e["added"] and e["price_out"] == 1.1


def test_add_online_api_rejects_bad_input_and_failed_test(tmp_path, monkeypatch):
    c, _ = _api_client(tmp_path, monkeypatch)
    assert c.post("/api/research/models", json={"name": "X", "base_url": "ftp://x", "model": "m"}).status_code == 400
    assert c.post("/api/research/models", json={"name": "", "base_url": "https://x", "model": "m"}).status_code == 400
    c, _ = _api_client(tmp_path, monkeypatch, reply="the API answered 401: bad key")
    r = c.post("/api/research/models", json={"name": "X", "base_url": "https://x.example", "model": "m"})
    assert r.status_code == 400 and "401" in r.json()["error"]
    assert (tmp_path / "config.local.json").read_text() == "{}"


def test_only_added_apis_can_be_removed(tmp_path, monkeypatch):
    c, store = _api_client(tmp_path, monkeypatch)
    store["models"] = [{"name": "Ants", "base_url": "http://127.0.0.1:8090", "model": "ants"},
                       {"name": "Mine", "base_url": "https://x.example", "model": "m", "added": True}]
    assert c.post("/api/research/models/remove", json={"name": "Ants"}).status_code == 400
    (tmp_path / "config.local.json").write_text('{"research": {"models": %s}}' % __import__("json").dumps(store["models"]))
    assert c.post("/api/research/models/remove", json={"name": "Mine"}).status_code == 200
    left = __import__("json").loads((tmp_path / "config.local.json").read_text())["research"]["models"]
    assert [m["name"] for m in left] == ["Ants"]


def test_model_list_never_returns_keys(tmp_path, monkeypatch):
    c, store = _api_client(tmp_path, monkeypatch)
    store["models"] = [{"name": "Mine", "base_url": "https://x.example", "model": "m", "api_key": "sk-secret",
                        "price_in": 1, "price_out": 2, "added": True}]
    r = c.get("/api/research/models")
    assert "sk-secret" not in r.text and r.json()["models"][0]["added"] is True
