"""Agents collector and routes: transcripts to now-cards, run history and replay."""

import datetime as dt
import json
import os
import time
from unittest.mock import MagicMock

import httpx
import pytest

import llamawatch.config as config_mod
from llamawatch import agents

T0 = 1_767_268_800.0  # 2026-01-01T12:00:00Z


def iso(epoch: float) -> str:
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


# ── fixtures ─────────────────────────────────────────────────────────────────
@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(agents, "_open_claude_cwds", lambda: set())
    agents._cache.clear()
    cfg = {"port": 8451, "host": "0.0.0.0", "auth_enabled": False, "auth_password_hash": "",
           "research": {"data_dir": str(tmp_path / "research")},
           "agents": [{"id": "helper", "name": "Helper", "containers": ["helper", "helper-db"], "machine": "box-2", "primary": "helper"},
                      {"id": "scraper", "name": "Scraper", "containers": ["scraper"], "machine": "box-1"}]}
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    config_mod.reset_config()
    import llamawatch.server as srv
    srv._config = config_mod.load_config(config_dir=tmp_path)
    srv._adapters = MagicMock()
    srv._collector_registry = MagicMock()
    yield tmp_path
    config_mod.reset_config()
    agents._cache.clear()


def claude_line(kind, when, content, cwd="/srv/demo/demo-shop", usage=None, mid=None, **extra):
    d = {"type": kind, "timestamp": iso(when), "cwd": cwd, "sessionId": "s1", "message": {"role": kind, "content": content}}
    if usage:
        d["message"]["usage"] = usage
    if mid:
        d["message"]["id"] = mid
    d.update(extra)
    return json.dumps(d)


def write_claude(tmp_path, name="0f3c2a1e-0001", lines=None, title="Add a retry", mtime=None):
    folder = tmp_path / "claude" / "projects" / "-srv-demo-demo-shop"
    folder.mkdir(parents=True, exist_ok=True)
    u = {"input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 0}
    if lines is None:
        lines = [
            claude_line("user", T0, "Add a retry to the photo sync job"),
            claude_line("assistant", T0 + 4, [{"type": "thinking", "thinking": "hmm"}, {"type": "text", "text": "Reading the runner first."}], usage=u, mid="m1"),
            claude_line("assistant", T0 + 6, [{"type": "tool_use", "name": "Read", "input": {"file_path": "/srv/demo/demo-shop/jobs.py"}}], usage=u, mid="m2"),
            claude_line("user", T0 + 7, [{"type": "tool_result", "content": "..."}], toolUseResult={}),
            claude_line("assistant", T0 + 9, [{"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q", "description": "run the unit tests"}}], usage=u, mid="m3"),
            claude_line("user", T0 + 30, [{"type": "tool_result", "content": "ok"}]),
            claude_line("assistant", T0 + 30, [{"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q", "description": "run the unit tests"}}], usage=u, mid="m3"),  # repeated line, same id
            claude_line("assistant", T0 + 33, [{"type": "tool_use", "name": "TodoWrite", "input": {"todos": []}}], usage=u, mid="m4"),
            claude_line("assistant", T0 + 40, [{"type": "text", "text": "Done. The retry waits three times."}], usage=u, mid="msg5"),
            "{not json",
            json.dumps({"type": "user", "timestamp": iso(T0 + 50), "isSidechain": True, "cwd": "/srv/demo/other", "message": {"role": "user", "content": "side prompt"}}),
        ]
    if title:
        lines = [json.dumps({"type": "ai-title", "aiTitle": title, "sessionId": name})] + lines
    p = folder / f"{name}.jsonl"
    p.write_text("\n".join(lines) + "\n")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def write_codex(tmp_path, name="rollout-2026-01-01T10-00-abc"):
    folder = tmp_path / "codex" / "sessions" / "2026" / "01" / "01"
    folder.mkdir(parents=True, exist_ok=True)
    lines = [
        {"timestamp": iso(T0), "type": "session_meta", "payload": {"id": "abc-123", "cwd": "/srv/demo/image-tools", "timestamp": iso(T0)}},
        {"timestamp": iso(T0 + 1), "type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Write a smoke test"}]}},
        {"timestamp": iso(T0 + 5), "type": "response_item", "payload": {"type": "function_call", "name": "shell", "arguments": json.dumps({"command": ["ls", "tests"]})}},
        {"timestamp": iso(T0 + 6), "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 1000, "cached_input_tokens": 400, "output_tokens": 50}}}},
        {"timestamp": iso(T0 + 9), "type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Added tests/test_resize.py"}]}},
        {"timestamp": iso(T0 + 9), "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 1600, "cached_input_tokens": 500, "output_tokens": 120}}}},
    ]
    p = folder / f"{name}.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return p


def write_research(tmp_path, run_id="20260101-1050-tidal", status="done", with_events=True):
    folder = tmp_path / "research" / run_id
    folder.mkdir(parents=True, exist_ok=True)
    meta = {"question": "Can tidal lagoons cover a town's winter demand?", "status": status, "model": "example-api",
            "started": "2026-01-01 12:00", "tokens": 4200, "cost": 0.42,
            "by_stage": {"search": {"calls": 2, "tokens": 300}, "read": {"calls": 3, "tokens": 3900}}}
    if status != "running":
        meta["finished"] = "2026-01-01 12:20"
    (folder / "run.json").write_text(json.dumps(meta))
    if with_events:
        ev = [{"stage": "search", "msg": "12 results", "t": str(T0 + 2)}, {"stage": "search", "msg": "4 queries", "t": str(T0 + 5)},
              {"stage": "read", "msg": "reading page 3 of 5", "t": str(T0 + 60)}]
        (folder / "events.jsonl").write_text("\n".join(json.dumps(e) for e in ev) + "\n")
    return folder


# ── Claude Code ──────────────────────────────────────────────────────────────
def test_claude_session_summary(env):
    write_claude(env)
    s = agents._parse_claude(str(env / "claude" / "projects" / "-srv-demo-demo-shop" / "0f3c2a1e-0001.jsonl"))
    assert s["id"] == "claude:0f3c2a1e-0001" and s["agent"] == "Claude Code"
    assert s["title"] == "Add a retry" and s["project"] == "demo-shop"
    kinds = [(x["kind"], x["label"]) for x in s["steps"]]
    assert kinds == [("prompt", "Prompt"), ("text", "Answer"), ("tool", "Read"), ("tool", "Bash"), ("text", "Answer")]
    assert s["steps"][2]["detail"] == "jobs.py" and s["steps"][3]["detail"] == "run the unit tests"
    # five distinct assistant messages, the repeated m3 line counted once
    assert (s["in"], s["out"], s["cache"]) == (50, 100, 500)
    assert s["thinking"] == 1
    assert s["started"] == T0 and s["last"] == T0 + 40           # the sidechain line is ignored
    assert s["turn_started"] == T0 and s["turn_prompt"] == "Add a retry to the photo sync job"


def test_title_falls_back_to_first_prompt(env):
    write_claude(env, title=None)
    s = agents._parse_claude(str(next((env / "claude" / "projects").rglob("*.jsonl"))))
    assert s["title"] == "Add a retry to the photo sync job"


def test_tool_summary_rules():
    assert agents.tool_summary("Bash", {"command": "ls", "description": "list files"}) == "list files"
    assert agents.tool_summary("Bash", {"command": "ls -la"}) == "ls -la"
    assert agents.tool_summary("Write", {"file_path": "/srv/demo/x/notes.md"}) == "notes.md"
    assert agents.tool_summary("Grep", {"pattern": "def main"}) == "def main"
    assert agents.tool_summary("Agent", {"description": "check the fleet", "prompt": "..."}) == "check the fleet"
    assert agents.tool_summary("Custom", {"n": 3, "query": "weather"}) == "weather"
    assert agents.tool_summary("Custom", None) == ""
    assert len(agents.tool_summary("Bash", {"command": "x" * 500})) <= agents.LINE_LEN


def test_states_working_idle_done(env, monkeypatch):
    write_claude(env)
    now = T0 + 60
    out = agents.collect({}, now=now)
    assert [r["status"] for r in out["runs"]] == ["working"]       # last line 20 s ago
    assert out["live"][0]["line"] == "Done. The retry waits three times."
    assert out["live"][0]["since"] == T0                            # the turn timer starts at the prompt
    p = next((env / "claude" / "projects").rglob("*.jsonl"))
    os.utime(p, (T0, T0))
    agents._cache.clear()
    assert agents.collect({}, now=T0 + 1000)["runs"][0]["status"] == "done"
    monkeypatch.setattr(agents, "_open_claude_cwds", lambda: {"/srv/demo/demo-shop"})
    out = agents.collect({}, now=T0 + 1000)
    assert out["runs"][0]["status"] == "idle" and out["live"][0]["state"] == "idle"


def test_row_shape(env):
    write_claude(env)
    r = agents.collect({}, now=T0 + 60)["runs"][0]
    assert r["project"] == "demo-shop" and r["steps"] == 5 and r["tokens"] == 650 and r["seconds"] == 40
    assert r["ended"] is None and r["cost"] is None


# ── Codex ────────────────────────────────────────────────────────────────────
def test_codex_rollout(env):
    write_codex(env)
    out = agents.collect({}, now=T0 + 20)
    r = out["runs"][0]
    assert r["id"] == "codex:abc-123" and r["agent"] == "Codex" and r["project"] == "image-tools"
    assert r["title"] == "Write a smoke test" and r["steps"] == 3
    assert r["tokens"] == 1600 + 120                                 # grown totals: in 1100 (less cache) + cache 500 + out 120
    rep = agents.replay({}, "codex:abc-123", now=T0 + 20)
    assert [s["label"] for s in rep["steps"]] == ["Prompt", "shell", "Answer"]
    assert rep["steps"][1]["detail"] == "ls tests"


# ── Research ─────────────────────────────────────────────────────────────────
def test_research_running_and_done(env):
    write_research(env, status="running")
    cfg = {"research": {"data_dir": str(env / "research")}}
    out = agents.collect(cfg, now=T0 + 100)
    assert out["runs"][0]["status"] == "working" and out["live"][0]["agent"] == "Research"
    assert out["live"][0]["line"] == "reading page 3 of 5" and out["live"][0]["since"] == T0
    rep = agents.replay(cfg, "research:20260101-1050-tidal", now=T0 + 100)
    labels = [(s["label"], s["calls"], s["out"]) for s in rep["steps"]]
    assert labels == [("search", 2, 300), ("search", 0, 0), ("read", 3, 3900)]
    assert rep["steps"][0]["dur"] == 3 and rep["steps"][-1]["dur"] == 40      # last step runs to now while working
    write_research(env, status="failed")
    agents._cache.clear()
    r = agents.collect(cfg, now=T0 + 5000)["runs"][0]
    assert r["status"] == "failed" and r["cost"] == 0.42 and r["project"] == "example-api"
    assert r["seconds"] == 1200                                     # finished 12:20


def test_research_without_events(env):
    write_research(env, with_events=False)
    cfg = {"research": {"data_dir": str(env / "research")}}
    r = agents.collect(cfg, now=T0 + 5000)["runs"][0]
    assert r["steps"] == 0 and r["line"] == ""


# ── configured agents ────────────────────────────────────────────────────────
def test_configured_agents_from_containers(env):
    import llamawatch.server as srv
    containers = [{"name": "helper", "state": "running", "status": "Up 3 hours", "machine": "box-2"},
                  {"name": "helper-db", "state": "exited", "status": "Exited (1)", "machine": "box-2"}]
    live = agents._configured(srv._config, containers)
    assert [(a["agent"], a["state"], a["line"], a["machine"]) for a in live] == \
        [("Helper", "partial", "Up 3 hours", "box-2"), ("Scraper", "offline", "no container seen", "box-1")]
    containers[1]["state"] = "running"
    assert agents._configured(srv._config, containers)[0]["state"] == "online"
    out = agents.collect(srv._config, containers, now=T0)
    assert "Helper" in out["agents"] and out["live"][0]["kind"] == "service" and out["live"][0]["since"] is None


# ── cache, cutoff, fold ──────────────────────────────────────────────────────
def test_cache_hit_and_miss(env, monkeypatch):
    p = write_claude(env)
    calls = []
    real = agents._parse_claude
    monkeypatch.setattr(agents, "_parse_claude", lambda path: calls.append(path) or real(path))
    agents.collect({}, now=T0 + 60)
    agents.collect({}, now=T0 + 60)
    assert len(calls) == 1
    p.write_text(p.read_text() + claude_line("assistant", T0 + 70, [{"type": "text", "text": "more"}], mid="m6") + "\n")
    os.utime(p, (T0 + 70, T0 + 70))
    agents.collect({}, now=T0 + 80)
    assert len(calls) == 2


def test_cutoff_keeps_old_files_closed(env, monkeypatch):
    write_claude(env, mtime=T0 - 10 * 86400)
    opened = []
    real_open = open
    monkeypatch.setattr("builtins.open", lambda *a, **k: opened.append(a[0]) or real_open(*a, **k))
    out = agents.collect({}, now=T0)
    assert out["runs"] == [] and not [o for o in opened if str(o).endswith(".jsonl")]


def test_replay_folds_long_runs(env):
    lines = [claude_line("user", T0, "go")]
    for i in range(agents.MAX_STEPS + 20):
        lines.append(claude_line("assistant", T0 + 1 + i, [{"type": "tool_use", "name": "Read", "input": {"file_path": f"/srv/demo/f{i}.py"}}], mid=f"m{i}"))
    write_claude(env, lines=lines)
    rep = agents.replay({}, "claude:0f3c2a1e-0001", now=T0 + 1000)
    assert len(rep["steps"]) == agents.MAX_STEPS + 1
    assert rep["steps"][0]["kind"] == "fold" and rep["steps"][0]["label"] == "21 earlier steps"
    assert rep["run"]["steps"] == agents.MAX_STEPS + 21


def test_replay_offsets_and_durations(env):
    write_claude(env, mtime=T0 + 40)
    rep = agents.replay({}, "claude:0f3c2a1e-0001", now=T0 + 1000)
    assert [(s["offset"], s["dur"]) for s in rep["steps"]] == [(0, 4), (4, 2), (6, 3), (9, 31), (40, 0)]
    assert rep["run"]["in"] == 50 and rep["run"]["thinking"] == 1 and rep["run"]["cwd"] == "/srv/demo/demo-shop"
    assert rep["steps"][1]["cache"] == 100 and rep["steps"][2]["in"] == 10


def test_replay_rejects_bad_ids(env):
    assert agents.replay({}, "claude:../../etc/passwd") is None
    assert agents.replay({}, "other:abc") is None
    assert agents.replay({}, "claude:nothere") is None
    assert agents.replay({"research": {"data_dir": str(env / "research")}}, "research:nothere") is None


# ── routes ───────────────────────────────────────────────────────────────────
@pytest.fixture()
def client(env):
    import llamawatch.server as srv
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url="http://testserver")


@pytest.mark.asyncio
async def test_routes(client, env, monkeypatch):
    from llamawatch.routes import agents as routes
    monkeypatch.setattr(routes, "_containers", lambda: [{"name": "helper", "state": "running", "status": "Up"}])
    write_claude(env, mtime=time.time())
    r = await client.get("/agents")
    assert r.status_code == 200 and "Run history" in r.text
    r = await client.get("/api/agents")
    assert r.status_code == 200
    d = r.json()
    assert d["runs"][0]["id"] == "claude:0f3c2a1e-0001" and {a["agent"] for a in d["live"]} >= {"Helper", "Scraper"}
    r = await client.get("/api/agents/runs/claude:0f3c2a1e-0001")
    assert r.status_code == 200 and len(r.json()["steps"]) == 5
    assert (await client.get("/api/agents/runs/claude:nothere")).status_code == 404
    assert (await client.get("/api/agents/runs/claude:..%2F..%2Fx")).status_code in (400, 404)
    assert (await client.get("/api/agents/runs/bad")).status_code == 400


def test_sample_mode_serves_agents(monkeypatch):
    from pathlib import Path
    from llamawatch import config, sample, ws_hub
    snap = Path(__file__).parent / "ui" / "fixtures" / "snapshot.json"
    monkeypatch.setenv(sample.ENV, str(snap))
    sample.reset(); config.reset_config(); ws_hub.reset_hub()
    config.load_config(config_dir=snap.parent)
    from llamawatch.server import app
    import asyncio

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            a = await c.get("/api/agents")
            b = await c.get("/api/agents/runs/claude:0f3c2a1e-demo-0001")
            return a, b
    a, b = asyncio.run(go())
    sample.reset(); config.reset_config()
    assert a.status_code == 200 and a.json()["live"][0]["agent"] == "Claude Code"
    assert b.status_code == 200 and b.json()["steps"][0]["kind"] == "prompt"
