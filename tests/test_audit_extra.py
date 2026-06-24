"""Extra tests for the audit log — edge cases not covered elsewhere."""

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

import llamawatch.audit as audit


@pytest.fixture()
def tmp_log(tmp_path, monkeypatch):
    log_file = tmp_path / "audit.log"
    monkeypatch.setattr(audit, "_LOG_FILE", log_file)
    return log_file


# ── read ──────────────────────────────────────────────────────────────────────

def test_read_empty_file_returns_empty(tmp_log):
    tmp_log.write_text("")
    assert audit.read() == []


def test_read_only_newlines_returns_empty(tmp_log):
    tmp_log.write_text("\n\n\n")
    assert audit.read() == []


def test_read_skips_corrupt_lines(tmp_log):
    tmp_log.write_text('{"action":"ok"}\nnot-json\n{"action":"also-ok"}\n')
    result = audit.read()
    assert len(result) == 2
    assert all("action" in r for r in result)


def test_read_crlf_line_endings(tmp_log):
    tmp_log.write_bytes(b'{"action":"crlf"}\r\n{"action":"two"}\r\n')
    result = audit.read()
    assert len(result) == 2


def test_read_respects_limit(tmp_log):
    lines = [json.dumps({"action": f"a{i}"}) for i in range(20)]
    tmp_log.write_text("\n".join(lines) + "\n")
    result = audit.read(limit=5)
    assert len(result) == 5


def test_read_returns_most_recent_first(tmp_log):
    tmp_log.write_text('{"action":"first"}\n{"action":"second"}\n{"action":"third"}\n')
    result = audit.read()
    assert result[0]["action"] == "third"
    assert result[-1]["action"] == "first"


def test_read_missing_file_returns_empty(tmp_log):
    # Don't create the file
    assert audit.read() == []


# ── append ────────────────────────────────────────────────────────────────────

def test_append_creates_file(tmp_log):
    assert not tmp_log.exists()
    audit.append("test_action", target="x")
    assert tmp_log.exists()


def test_append_writes_valid_json(tmp_log):
    audit.append("deploy", target="svc", outcome="ok")
    line = tmp_log.read_text().strip()
    data = json.loads(line)
    assert data["action"] == "deploy"
    assert data["target"] == "svc"
    assert data["outcome"] == "ok"
    assert "ts" in data


def test_append_includes_extra_kwargs(tmp_log):
    audit.append("login", actor="admin", ip="127.0.0.1")
    data = json.loads(tmp_log.read_text().strip())
    assert data["ip"] == "127.0.0.1"


def test_append_chmod_failure_is_silent(tmp_log):
    with mock.patch("os.chmod", side_effect=OSError("permission denied")):
        audit.append("nochmod", target="y")  # must not raise
    assert tmp_log.exists()


def test_append_multiple_entries_each_on_own_line(tmp_log):
    audit.append("a1")
    audit.append("a2")
    audit.append("a3")
    lines = [l for l in tmp_log.read_text().splitlines() if l.strip()]
    assert len(lines) == 3
    for line in lines:
        json.loads(line)  # each line must be valid JSON


def test_read_roundtrip(tmp_log):
    audit.append("action_a", target="t1")
    audit.append("action_b", target="t2")
    entries = audit.read()
    assert entries[0]["action"] == "action_b"
    assert entries[1]["action"] == "action_a"
