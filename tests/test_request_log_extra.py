"""Extra tests for RequestLog — edge cases not covered elsewhere."""

import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from llamawatch.request_log import RequestLog


@pytest.fixture()
def log(tmp_path):
    return RequestLog(log_dir=str(tmp_path))


# ── log_request — preview truncation ─────────────────────────────────────────

def test_log_prompt_truncated_at_200(log):
    long_prompt = "A" * 300
    log.log_request("gpt-4", long_prompt, "response")
    entries = log.get_recent()
    assert len(entries[0]["prompt_preview"]) == 200


def test_log_exactly_200_chars_not_truncated(log):
    exact = "B" * 200
    log.log_request("gpt-4", exact, "response")
    entries = log.get_recent()
    assert entries[0]["prompt_preview"] == exact


def test_log_201_chars_truncated_to_200(log):
    s = "C" * 201
    log.log_request("gpt-4", s, "response")
    entries = log.get_recent()
    assert len(entries[0]["prompt_preview"]) == 200


def test_log_response_truncated_at_200(log):
    long_resp = "R" * 250
    log.log_request("gpt-4", "prompt", long_resp)
    entries = log.get_recent()
    assert len(entries[0]["response_preview"]) == 200


def test_log_short_strings_not_padded(log):
    log.log_request("m", "hi", "bye")
    entries = log.get_recent()
    assert entries[0]["prompt_preview"] == "hi"
    assert entries[0]["response_preview"] == "bye"


# ── get_recent — empty/missing directory ─────────────────────────────────────

def test_get_recent_empty_directory(tmp_path):
    r = RequestLog(log_dir=str(tmp_path))
    assert r.get_recent() == []


def test_get_recent_file_with_only_whitespace(log, tmp_path):
    # Write a log file that contains only whitespace
    f = Path(tmp_path) / "requests-2024-01-01.jsonl"
    f.write_text("   \n\n   \n")
    assert log.get_recent() == []


def test_get_recent_skips_corrupt_lines(log, tmp_path):
    f = Path(tmp_path) / "requests-2024-01-01.jsonl"
    f.write_text('{"model":"a"}\nnot-json\n{"model":"b"}\n')
    entries = log.get_recent()
    assert len(entries) == 2
    assert all("model" in e for e in entries)


def test_get_recent_respects_limit(log):
    for i in range(20):
        log.log_request(f"m{i}", "p", "r")
    entries = log.get_recent(limit=5)
    assert len(entries) == 5


def test_get_recent_most_recent_first(log):
    log.log_request("m1", "first", "r")
    log.log_request("m2", "second", "r")
    log.log_request("m3", "third", "r")
    entries = log.get_recent()
    assert entries[0]["model"] == "m3"


def test_get_recent_spans_multiple_files(tmp_path):
    r = RequestLog(log_dir=str(tmp_path))
    # Write two separate "day" files
    (tmp_path / "requests-2024-01-01.jsonl").write_text('{"model":"day1"}\n')
    (tmp_path / "requests-2024-01-02.jsonl").write_text('{"model":"day2"}\n')
    entries = r.get_recent(limit=10)
    models = {e["model"] for e in entries}
    assert "day1" in models
    assert "day2" in models


def test_get_recent_limit_across_files(tmp_path):
    """Stops reading older files once limit reached."""
    r = RequestLog(log_dir=str(tmp_path))
    # Newer file: 3 entries
    lines = "\n".join(json.dumps({"model": f"new{i}"}) for i in range(3)) + "\n"
    (tmp_path / "requests-2024-01-02.jsonl").write_text(lines)
    # Older file: 5 entries
    lines2 = "\n".join(json.dumps({"model": f"old{i}"}) for i in range(5)) + "\n"
    (tmp_path / "requests-2024-01-01.jsonl").write_text(lines2)

    entries = r.get_recent(limit=3)
    assert len(entries) == 3
    # All 3 should come from the newer file
    assert all(e["model"].startswith("new") for e in entries)


# ── log_request — field storage ──────────────────────────────────────────────

def test_log_stores_all_fields(log):
    log.log_request("mymodel", "p", "r", prompt_tokens=10, completion_tokens=20,
                    duration_ms=500, source="api")
    entries = log.get_recent()
    e = entries[0]
    assert e["model"] == "mymodel"
    assert e["prompt_tokens"] == 10
    assert e["completion_tokens"] == 20
    assert e["duration_ms"] == 500
    assert e["source"] == "api"


def test_log_write_failure_is_silent(log):
    with mock.patch("builtins.open", side_effect=OSError("disk full")):
        log.log_request("m", "p", "r")  # must not raise
