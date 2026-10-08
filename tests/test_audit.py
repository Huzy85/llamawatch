import json
import pytest
from llamawatch import audit


@pytest.fixture(autouse=True)
def _tmp_log(monkeypatch, tmp_path):
    monkeypatch.setattr(audit, "_LOG_FILE", tmp_path / "audit.log")
    yield


def test_append_writes_jsonl_line():
    audit.append("service_restart", target="swap-proxy", outcome="ok", actor="local")
    lines = (audit._LOG_FILE).read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["action"] == "service_restart"
    assert rec["target"] == "swap-proxy"
    assert rec["outcome"] == "ok"
    assert rec["actor"] == "local"
    assert "ts" in rec


def test_read_returns_most_recent_first():
    audit.append("a", target="t1", outcome="ok")
    audit.append("b", target="t2", outcome="fail")
    recent = audit.read(limit=10)
    assert [r["action"] for r in recent] == ["b", "a"]


def test_read_respects_limit():
    for i in range(5):
        audit.append("x", target=str(i), outcome="ok")
    assert len(audit.read(limit=3)) == 3


def test_read_empty_when_no_file():
    assert audit.read() == []
