"""Tests for the token_usage collector."""

import sqlite3
import tempfile
import time
from pathlib import Path
from unittest import mock


def _make_db(path: Path, rows: list[tuple]) -> None:
    """Create a minimal usage.db with the given rows."""
    con = sqlite3.connect(path)
    con.execute(
        """CREATE TABLE llm_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER,
            caller TEXT,
            model TEXT,
            endpoint TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            latency_ms INTEGER,
            streamed INTEGER,
            status INTEGER
        )"""
    )
    con.executemany(
        "INSERT INTO llm_usage (ts,caller,model,endpoint,input_tokens,output_tokens,latency_ms,streamed,status) VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )
    con.commit()
    con.close()


# Config-driven aliases that map model IDs into the primary bucket. These are
# now supplied per-install via token_usage.primary_model_aliases.
_PRIMARY_ALIASES = ["primary-a", "primary-b", "auto"]


def _fake_config(aliases=None, labels=None):
    """Build a config dict the collector's load_config calls will read."""
    tu_cfg = {"primary_model_aliases": list(aliases or [])}
    if labels:
        tu_cfg.update(labels)
    return {"token_usage": tu_cfg}


def _patch_config_and_remote(aliases=None, labels=None):
    """Return a list of patchers configuring aliases/labels and zeroing the
    secondary (apollo) + Claude buckets so only the DB primary bucket counts."""
    import llamawatch.collectors.token_usage as tu
    cfg = _fake_config(aliases, labels)
    primary_label = (labels or {}).get("primary_label", "Primary")
    secondary_label = (labels or {}).get("secondary_label", "Secondary")
    claude_label = (labels or {}).get("claude_label", "Claude")
    return [
        mock.patch("llamawatch.config.load_config", return_value=cfg),
        mock.patch.object(
            tu, "_collect_apollo",
            return_value={"model": secondary_label, "requests": 0,
                          "in_tokens": 0, "out_tokens": 0},
        ),
        mock.patch.object(
            tu, "_collect_claude",
            return_value={"model": claude_label, "requests": 0,
                          "in_tokens": 0, "out_tokens": 0, "cache_read_tokens": 0},
        ),
    ]


def test_basic_aggregation():
    """Aggregation returns correct shape with expected sums for the primary bucket."""
    now = int(time.time())
    import llamawatch.collectors.token_usage as tu
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "usage.db"
        _make_db(
            db,
            [
                (now - 100, "caller-a", "primary-a", "/v1/chat/completions", 100, 50, 300, 0, 200),
                (now - 200, "caller-a", "primary-a", "/v1/chat/completions", 80, 40, 280, 0, 200),
                (now - 300, "caller-b", "primary-b", "/v1/chat/completions", 200, 100, 500, 0, 200),
            ],
        )
        patchers = [mock.patch.object(tu, "_DB_PATH", db)] + _patch_config_and_remote(_PRIMARY_ALIASES)
        with patchers[0], patchers[1], patchers[2], patchers[3]:
            result = tu.collect()

    assert "by_model" in result
    assert "by_caller" in result
    assert "total_requests" in result
    assert "total_tokens" in result

    assert result["total_requests"] == 3

    # Total tokens: (100+50) + (80+40) + (200+100) = 570
    assert result["total_tokens"] == 570

    # by_model: the primary bucket aggregates all alias rows under "Primary".
    models = {m["model"]: m for m in result["by_model"]}
    assert "Primary" in models
    assert models["Primary"]["requests"] == 3
    assert models["Primary"]["in_tokens"] == 380   # 100+80+200
    assert models["Primary"]["out_tokens"] == 190  # 50+40+100

    # by_caller
    callers = {c["caller"]: c for c in result["by_caller"]}
    assert "caller-a" in callers
    assert callers["caller-a"]["requests"] == 2
    assert callers["caller-a"]["tokens"] == 270  # (100+50)+(80+40)


def test_alias_rows_merge_into_primary_bucket():
    """All configured aliases merge into the single primary bucket, summing counts."""
    now = int(time.time())
    import llamawatch.collectors.token_usage as tu
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "usage.db"
        _make_db(
            db,
            [
                (now - 100, "caller-a",  "primary-a", "/v1/chat/completions", 200, 100, 400, 0, 200),
                (now - 200, "caller-b", "auto",      "/v1/chat/completions", 50,  25,  200, 0, 200),
                (now - 300, "caller-b", "primary-b", "/v1/chat/completions", 80,  40,  300, 0, 200),
                (now - 400, "other", "not-primary", "/v1/chat/completions", 300, 150, 500, 0, 200),
            ],
        )
        patchers = [mock.patch.object(tu, "_DB_PATH", db)] + _patch_config_and_remote(_PRIMARY_ALIASES)
        with patchers[0], patchers[1], patchers[2], patchers[3]:
            result = tu.collect()

    models = {m["model"]: m for m in result["by_model"]}

    # primary-a + auto + primary-b all → the "Primary" bucket
    assert "Primary" in models
    assert models["Primary"]["requests"] == 3
    assert models["Primary"]["in_tokens"] == 330   # 200+50+80
    assert models["Primary"]["out_tokens"] == 165  # 100+25+40

    # A model that matches no alias must NOT be counted into the primary bucket.
    assert "not-primary" not in models


def test_unknown_caller_relabelled_untagged():
    """Empty or 'unknown' callers are relabelled 'untagged'."""
    now = int(time.time())
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "usage.db"
        _make_db(
            db,
            [
                (now - 100, "",        "Hermes", "/v1/chat/completions", 100, 50, 300, 0, 200),
                (now - 200, "unknown", "Hermes", "/v1/chat/completions", 80,  40, 280, 0, 200),
                (now - 300, "caller-a",    "Hermes", "/v1/chat/completions", 60,  30, 200, 0, 200),
            ],
        )
        import llamawatch.collectors.token_usage as tu
        with mock.patch.object(tu, "_DB_PATH", db):
            result = tu.collect()

    callers = {c["caller"]: c for c in result["by_caller"]}
    # "" and "unknown" become "untagged"
    assert "untagged" in callers
    assert "" not in callers
    assert "caller-a" in callers


def test_missing_db_returns_zeros():
    """Missing db yields zero totals (primary bucket empty) without raising."""
    import llamawatch.collectors.token_usage as tu
    patchers = [mock.patch.object(tu, "_DB_PATH", Path("/nonexistent/path/usage.db"))] \
        + _patch_config_and_remote(_PRIMARY_ALIASES)
    with patchers[0], patchers[1], patchers[2], patchers[3]:
        result = tu.collect()
    assert result["total_requests"] == 0
    assert result["total_tokens"] == 0
    assert result["by_caller"] == []


def test_old_rows_excluded():
    """Rows older than 24h are excluded from aggregates."""
    now = int(time.time())
    old_ts = now - 90000  # > 24h ago
    import llamawatch.collectors.token_usage as tu
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "usage.db"
        _make_db(
            db,
            [
                (now - 60, "caller-a", "primary-a", "/v1/chat/completions", 100, 50, 300, 0, 200),
                (old_ts,   "caller-a", "primary-a", "/v1/chat/completions", 999, 999, 1000, 0, 200),
            ],
        )
        patchers = [mock.patch.object(tu, "_DB_PATH", db)] + _patch_config_and_remote(_PRIMARY_ALIASES)
        with patchers[0], patchers[1], patchers[2], patchers[3]:
            result = tu.collect()

    assert result["total_requests"] == 1
    assert result["total_tokens"] == 150  # only the recent row: 100+50


def test_empty_db_returns_zeros():
    """Empty db (no rows) returns valid shape with zero totals."""
    import llamawatch.collectors.token_usage as tu
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "usage.db"
        _make_db(db, [])
        patchers = [mock.patch.object(tu, "_DB_PATH", db)] + _patch_config_and_remote(_PRIMARY_ALIASES)
        with patchers[0], patchers[1], patchers[2], patchers[3]:
            result = tu.collect()

    assert result.get("total_requests") == 0
    assert result.get("total_tokens") == 0
    # by_model always carries the three (possibly zero) buckets.
    assert all(m["requests"] == 0 for m in result["by_model"])
    assert result.get("by_caller") == []
