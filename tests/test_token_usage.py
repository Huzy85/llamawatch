"""Tests for the token_usage collector: one row per llama.cpp backend and per
coding tool, rows with no usage left out."""

import json
import time

import pytest

import llamawatch.collectors.token_usage as tu


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(tu, "_SNAP_DIR", tmp_path / "snaps")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(tu, "_live", lambda config: config)


def _metrics(t_in, t_out):
    return f"# HELP x\nllamacpp:prompt_tokens_total {t_in}\nllamacpp:tokens_predicted_total {t_out}\n"


def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(ts))


def _cfg(*backends):
    return {"backends": [{"type": "llamacpp", "name": n, "url": u} for n, u in backends]}


def test_nothing_set_up_gives_no_rows():
    out = tu.collect({})
    assert out["by_model"] == [] and out["total_tokens"] == 0 and out["no_metrics"] == []


def test_each_backend_gets_its_own_row_by_name(monkeypatch):
    readings = {"http://a:1": [(100, 10), (300, 50)], "http://b:2": [(0, 0), (40, 4)],
                "http://c:3": [(5, 5), (5, 5)]}
    calls = {}

    def fake_get(url, timeout=2.0):
        base = url.rsplit("/metrics", 1)[0]
        i = calls.get(base, 0)
        calls[base] = i + 1
        return _metrics(*readings[base][i])

    monkeypatch.setattr(tu, "_http_get", fake_get)
    cfg = _cfg(("Writer", "http://a:1"), ("tiny-coder", "http://b:2"), ("Idle", "http://c:3"))
    tu.collect(cfg)
    out = tu.collect(cfg)
    rows = {r["model"]: r for r in out["by_model"]}
    assert set(rows) == {"Writer", "tiny-coder"}   # Idle had no usage, so it is left out
    assert (rows["Writer"]["in_tokens"], rows["Writer"]["out_tokens"]) == (200, 40)
    assert (rows["tiny-coder"]["in_tokens"], rows["tiny-coder"]["out_tokens"]) == (40, 4)
    assert out["total_tokens"] == 284


def test_backend_without_metrics_is_reported(monkeypatch):
    monkeypatch.setattr(tu, "_http_get", lambda url, timeout=2.0: None)
    out = tu.collect(_cfg(("NoMetrics", "http://x:9")))
    assert out["by_model"] == [] and out["no_metrics"] == ["NoMetrics"]


def test_router_server_is_read_model_by_model(monkeypatch):
    models = {"data": [{"id": "big", "status": {"value": "loaded"}},
                       {"id": "small", "status": {"value": "loaded"}},
                       {"id": "off", "status": {"value": "unloaded"}}]}
    n = {"i": 0}

    def fake_get(url, timeout=2.0):
        if url.endswith("/metrics"):
            return None   # a router refuses a bare /metrics
        if url.endswith("/v1/models"):
            return json.dumps(models)
        step = 1 if n["i"] >= 2 else 0
        n["i"] += 1
        return _metrics(10 + 100 * step, 1 + 10 * step) if "big" in url else _metrics(5 + 50 * step, 0)

    monkeypatch.setattr(tu, "_http_get", fake_get)
    tu.collect(_cfg(("Router", "http://r:8080")))
    out = tu.collect(_cfg(("Router", "http://r:8080")))
    assert out["by_model"][0]["model"] == "Router"
    assert (out["by_model"][0]["in_tokens"], out["by_model"][0]["out_tokens"]) == (150, 10)


def test_restart_counts_new_usage_in_full():
    snaps = [{"ts": 1, "in": 100, "out": 10}, {"ts": 2, "in": 160, "out": 20}, {"ts": 3, "in": 30, "out": 5}]
    assert tu._grown(snaps, "in") == 60 + 30
    assert tu._grown(snaps, "out", base={"ts": 0, "in": 0, "out": 0}) == 10 + 10 + 5


def test_old_readings_become_the_baseline(monkeypatch):
    now = time.time()
    f = tu._snap_file("M")
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({"ts": now - 90000, "in": 1000, "out": 100}) + "\nnot json\n")
    monkeypatch.setattr(tu, "_http_get", lambda url, timeout=2.0: _metrics(1500, 130))
    row = tu.collect(_cfg(("M", "http://m:1")))["by_model"][0]
    assert (row["in_tokens"], row["out_tokens"]) == (500, 30)
    assert len(f.read_text().splitlines()) == 2


def test_claude_logs_are_counted(tmp_path):
    d = tmp_path / "claude" / "projects" / "p"
    d.mkdir(parents=True)
    now = time.time()
    lines = [
        {"type": "assistant", "timestamp": _iso(now - 60),
         "message": {"usage": {"input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 5}}},
        {"type": "assistant", "timestamp": _iso(now - 90000),
         "message": {"usage": {"input_tokens": 999, "output_tokens": 999}}},
        {"type": "user"},
    ]
    (d / "s.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\nbroken\n")
    row = tu.collect({})["by_model"][0]
    assert row["model"] == "Claude"
    assert (row["requests"], row["in_tokens"], row["out_tokens"], row["cache_read_tokens"]) == (1, 10, 20, 5)


def _codex_event(ts, inp, cached, out):
    tot = {"input_tokens": inp, "cached_input_tokens": cached, "output_tokens": out,
           "total_tokens": inp + out}
    return json.dumps({"timestamp": _iso(ts), "type": "event_msg",
                       "payload": {"type": "token_count", "info": {"total_token_usage": tot,
                                                                   "last_token_usage": tot}}})


def test_codex_logs_are_counted_from_running_totals(tmp_path):
    d = tmp_path / "codex" / "sessions" / "2026" / "10" / "07"
    d.mkdir(parents=True)
    now = time.time()
    (d / "rollout-1.jsonl").write_text("\n".join([
        _codex_event(now - 90000, 1000, 400, 100),   # before the window: baseline
        _codex_event(now - 600, 1500, 600, 150),
        _codex_event(now - 590, 1500, 600, 150),     # repeated event, same totals
        _codex_event(now - 60, 2000, 900, 220),
        json.dumps({"type": "response_item", "payload": {}}),
    ]) + "\n")
    row = tu.collect({})["by_model"][0]
    assert row["model"] == "Codex"
    assert row["requests"] == 2
    assert row["cache_read_tokens"] == 500
    assert row["in_tokens"] == 1000 - 500
    assert row["out_tokens"] == 120


def test_codex_absent_gives_no_row():
    assert all(r["model"] != "Codex" for r in tu.collect({})["by_model"])


def test_hidden_names_are_left_out_but_still_offered(monkeypatch):
    monkeypatch.setattr(tu, "_http_get", lambda url, timeout=2.0: _metrics(10, 1))
    cfg = _cfg(("A", "http://a:1"), ("B", "http://b:2"))
    cfg["token_usage"] = {"hidden": ["B"]}
    tu.collect(cfg)
    tu._SNAP_DIR.joinpath("A.jsonl").write_text(json.dumps({"ts": time.time() - 60, "in": 0, "out": 0}) + "\n")
    tu._SNAP_DIR.joinpath("B.jsonl").write_text(json.dumps({"ts": time.time() - 60, "in": 0, "out": 0}) + "\n")
    out = tu.collect(cfg)
    assert [r["model"] for r in out["by_model"]] == ["A"]
    assert {s["name"]: s["hidden"] for s in out["sources"]} == {"A": False, "B": True}


def test_research_runs_on_other_models_are_counted(tmp_path):
    run = tmp_path / "runs" / "r1"
    run.mkdir(parents=True)
    (run / "run.json").write_text(json.dumps({"model": "DeepSeek", "calls": 7, "tokens_in": 900, "tokens_out": 100}))
    old = tmp_path / "runs" / "r0"
    old.mkdir()
    (old / "run.json").write_text(json.dumps({"model": "Kimi", "calls": 2, "tokens": 500}))
    local = tmp_path / "runs" / "r2"
    local.mkdir()
    (local / "run.json").write_text(json.dumps({"model": "Main", "calls": 3, "tokens": 50}))
    cfg = {"backends": [{"type": "llamacpp", "name": "Main", "url": ""}],
           "research": {"data_dir": str(tmp_path / "runs"),
                        "models": [{"name": "DeepSeek"}, {"name": "Kimi"}]}}
    out = tu.collect(cfg)
    rows = {r["model"]: r for r in out["by_model"]}
    assert (rows["DeepSeek"]["requests"], rows["DeepSeek"]["in_tokens"], rows["DeepSeek"]["out_tokens"]) == (7, 900, 100)
    assert rows["Kimi"]["in_tokens"] == 500          # older runs kept only the total
    assert "Main" not in rows                         # Main is counted from its own server
    assert {s["name"] for s in out["sources"]} >= {"Main", "DeepSeek", "Kimi"}
