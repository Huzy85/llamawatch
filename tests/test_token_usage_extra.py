"""Extra tests for the token_usage collector — edge cases not covered elsewhere."""

import json
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest import mock

import pytest

from llamawatch.collectors import token_usage as tu


def _fake_cfg(aliases=None, labels=None, apollo_url=None):
    cfg = {"token_usage": {"primary_model_aliases": list(aliases or [])}}
    if labels:
        cfg["token_usage"].update(labels)
    if apollo_url:
        cfg["token_usage"]["apollo_url"] = apollo_url
    return cfg


# ── _is_primary ───────────────────────────────────────────────────────────────

def test_is_primary_empty_string_returns_false(monkeypatch):
    monkeypatch.setattr("llamawatch.config.load_config", lambda: _fake_cfg(["hermes"]))
    assert tu._is_primary("") is False


def test_is_primary_none_returns_false(monkeypatch):
    monkeypatch.setattr("llamawatch.config.load_config", lambda: _fake_cfg(["hermes"]))
    assert tu._is_primary(None) is False


def test_is_primary_exact_match(monkeypatch):
    monkeypatch.setattr("llamawatch.config.load_config", lambda: _fake_cfg(["hermes"]))
    assert tu._is_primary("hermes") is True


def test_is_primary_prefix_match(monkeypatch):
    monkeypatch.setattr("llamawatch.config.load_config", lambda: _fake_cfg(["hermes"]))
    assert tu._is_primary("hermes-q4") is True


def test_is_primary_no_match(monkeypatch):
    monkeypatch.setattr("llamawatch.config.load_config", lambda: _fake_cfg(["hermes"]))
    assert tu._is_primary("apollo") is False


def test_is_primary_case_insensitive(monkeypatch):
    monkeypatch.setattr("llamawatch.config.load_config", lambda: _fake_cfg(["Hermes"]))
    assert tu._is_primary("HERMES") is True


def test_is_primary_no_aliases_always_false(monkeypatch):
    monkeypatch.setattr("llamawatch.config.load_config", lambda: _fake_cfg([]))
    assert tu._is_primary("anything") is False


# ── _labels ───────────────────────────────────────────────────────────────────

def test_labels_defaults_when_config_fails(monkeypatch):
    with mock.patch("llamawatch.config.load_config", side_effect=Exception("no config")):
        labels = tu._labels()
    assert labels["primary"] == "Primary"
    assert labels["secondary"] == "Secondary"
    assert labels["claude"] == "Claude"


def test_labels_custom_from_config(monkeypatch):
    monkeypatch.setattr("llamawatch.config.load_config", lambda: _fake_cfg(
        labels={"primary_label": "Hermes", "secondary_label": "Apollo", "claude_label": "Claude Code"}
    ))
    labels = tu._labels()
    assert labels["primary"] == "Hermes"
    assert labels["secondary"] == "Apollo"
    assert labels["claude"] == "Claude Code"


# ── _collect_primary ──────────────────────────────────────────────────────────

def test_collect_primary_no_db_returns_zeros(tmp_path, monkeypatch):
    monkeypatch.setattr(tu, "_DB_PATH", tmp_path / "nonexistent.db")
    monkeypatch.setattr("llamawatch.config.load_config", lambda: _fake_cfg([]))
    result = tu._collect_primary(0)
    assert result["requests"] == 0
    assert result["in_tokens"] == 0
    assert result["out_tokens"] == 0


def test_collect_primary_sums_matching_models(tmp_path, monkeypatch):
    db = tmp_path / "usage.db"
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE llm_usage (
        id INTEGER PRIMARY KEY, ts INTEGER, caller TEXT, model TEXT,
        endpoint TEXT, input_tokens INTEGER, output_tokens INTEGER,
        latency_ms INTEGER, streamed INTEGER, status INTEGER
    )""")
    now = int(time.time())
    con.executemany(
        "INSERT INTO llm_usage (ts,caller,model,input_tokens,output_tokens,latency_ms,streamed,status,endpoint) VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (now, "a", "hermes-q4", 100, 50, 200, 1, 200, "/v1/chat/completions"),
            (now, "b", "hermes-q4", 200, 80, 300, 1, 200, "/v1/chat/completions"),
            (now, "c", "apollo",    500, 200, 400, 1, 200, "/v1/chat/completions"),  # not primary
        ],
    )
    con.commit()
    con.close()
    monkeypatch.setattr(tu, "_DB_PATH", db)
    monkeypatch.setattr("llamawatch.config.load_config", lambda: _fake_cfg(["hermes"]))
    result = tu._collect_primary(now - 1)
    assert result["requests"] == 2
    assert result["in_tokens"] == 300
    assert result["out_tokens"] == 130


def test_collect_primary_corrupt_db_is_silent(tmp_path, monkeypatch):
    db = tmp_path / "broken.db"
    db.write_bytes(b"not a sqlite database")
    monkeypatch.setattr(tu, "_DB_PATH", db)
    monkeypatch.setattr("llamawatch.config.load_config", lambda: _fake_cfg([]))
    result = tu._collect_primary(0)
    assert result["requests"] == 0


# ── _collect_apollo (snapshot JSONL) ─────────────────────────────────────────
# _collect_apollo() takes no args — it fetches /metrics live and manages its
# own snapshot file. Tests mock out _http_get and _apollo_url.

_FAKE_METRICS = (
    "# HELP llamacpp:tokens_predicted_total Total tokens predicted\n"
    "llamacpp:tokens_predicted_total 1000\n"
    "llamacpp:prompt_tokens_total 500\n"
)


def test_collect_apollo_no_url_returns_zeros(monkeypatch):
    monkeypatch.setattr("llamawatch.config.load_config", lambda: _fake_cfg())
    result = tu._collect_apollo()
    assert result["out_tokens"] == 0
    assert result["in_tokens"] == 0


def test_collect_apollo_unreachable_returns_zeros(tmp_path, monkeypatch):
    monkeypatch.setattr(tu, "_APOLLO_SNAP_PATH", tmp_path / "snaps.jsonl")
    monkeypatch.setattr("llamawatch.config.load_config", lambda: _fake_cfg(apollo_url="http://localhost:8082"))
    monkeypatch.setattr(tu, "_http_get", lambda *a, **kw: None)
    result = tu._collect_apollo()
    assert result["out_tokens"] == 0


def test_collect_apollo_single_snapshot_gives_zero_delta(tmp_path, monkeypatch):
    """First call: only one snapshot → delta = 0 (need ≥2 for a delta)."""
    snap_file = tmp_path / "snaps.jsonl"
    monkeypatch.setattr(tu, "_APOLLO_SNAP_PATH", snap_file)
    monkeypatch.setattr("llamawatch.config.load_config", lambda: _fake_cfg(apollo_url="http://localhost:8082"))
    monkeypatch.setattr(tu, "_http_get", lambda *a, **kw: _FAKE_METRICS)
    # No pre-existing snapshots → function writes one and returns delta=0
    result = tu._collect_apollo()
    assert result["out_tokens"] == 0  # single snapshot, no delta yet


def test_collect_apollo_two_snapshots_gives_delta(tmp_path, monkeypatch):
    """Pre-seed one snapshot; second call produces a non-zero delta."""
    snap_file = tmp_path / "snaps.jsonl"
    now = time.time()
    # Existing snapshot from 10s ago with lower counts
    snap_file.write_text(json.dumps({"ts": now - 10, "out": 800, "in": 400}) + "\n")
    monkeypatch.setattr(tu, "_APOLLO_SNAP_PATH", snap_file)
    monkeypatch.setattr("llamawatch.config.load_config", lambda: _fake_cfg(apollo_url="http://localhost:8082"))
    # /metrics now returns 1000 out, 500 in
    monkeypatch.setattr(tu, "_http_get", lambda *a, **kw: _FAKE_METRICS)
    result = tu._collect_apollo()
    assert result["out_tokens"] == 200  # 1000 - 800
    assert result["in_tokens"] == 100   # 500 - 400


def test_collect_apollo_corrupt_snapshot_line_is_skipped(tmp_path, monkeypatch):
    snap_file = tmp_path / "snaps.jsonl"
    now = time.time()
    snap_file.write_text(
        json.dumps({"ts": now - 10, "out": 800, "in": 400}) + "\n"
        "NOT JSON {{{\n"
    )
    monkeypatch.setattr(tu, "_APOLLO_SNAP_PATH", snap_file)
    monkeypatch.setattr("llamawatch.config.load_config", lambda: _fake_cfg(apollo_url="http://localhost:8082"))
    monkeypatch.setattr(tu, "_http_get", lambda *a, **kw: _FAKE_METRICS)
    result = tu._collect_apollo()  # must not crash
    assert result["out_tokens"] >= 0


# ── _collect_claude ───────────────────────────────────────────────────────────

def test_collect_claude_no_matching_files(tmp_path, monkeypatch):
    monkeypatch.setattr(tu, "_CLAUDE_GLOB", str(tmp_path / "nonexistent" / "*.jsonl"))
    monkeypatch.setattr("llamawatch.config.load_config", lambda: _fake_cfg())
    result = tu._collect_claude(0)
    assert result["out_tokens"] == 0


def test_collect_claude_sums_usage_entries(tmp_path, monkeypatch):
    import datetime
    proj = tmp_path / "proj1"
    proj.mkdir()
    session = proj / "session.jsonl"
    # Use local time so the filter's dt.timestamp() comparison works correctly
    now_dt = datetime.datetime.now()
    old_dt = now_dt - datetime.timedelta(hours=48)  # clearly outside a 1-hour window
    entries = [
        {"timestamp": now_dt.isoformat(), "type": "assistant", "message": {"usage": {"output_tokens": 100, "input_tokens": 50}}},
        {"timestamp": now_dt.isoformat(), "type": "assistant", "message": {"usage": {"output_tokens": 200, "input_tokens": 80}}},
        {"timestamp": old_dt.isoformat(), "type": "assistant", "message": {"usage": {"output_tokens": 999, "input_tokens": 999}}},
    ]
    session.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    monkeypatch.setattr(tu, "_CLAUDE_GLOB", str(tmp_path / "*" / "*.jsonl"))
    monkeypatch.setattr("llamawatch.config.load_config", lambda: _fake_cfg())
    # window_secs=3600 → only the two recent entries included; old entry (48h) excluded
    result = tu._collect_claude(window_secs=3600)
    assert result["out_tokens"] == 300
    assert result["in_tokens"] == 130


def test_collect_claude_skips_corrupt_lines(tmp_path, monkeypatch):
    proj = tmp_path / "proj1"
    proj.mkdir()
    session = proj / "session.jsonl"
    now = int(time.time())
    session.write_text(
        '{"timestamp":' + str(now) + ',"type":"assistant","message":{"usage":{"output_tokens":50,"input_tokens":10}}}\n'
        "NOT JSON\n"
        '{"timestamp":' + str(now) + ',"type":"assistant","message":{"usage":{"output_tokens":30,"input_tokens":5}}}\n'
    )
    monkeypatch.setattr(tu, "_CLAUDE_GLOB", str(tmp_path / "*" / "*.jsonl"))
    monkeypatch.setattr("llamawatch.config.load_config", lambda: _fake_cfg())
    result = tu._collect_claude(now - 100)
    assert result["out_tokens"] == 80
