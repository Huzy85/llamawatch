"""Tests for the Logs Viewer collector."""

import json
import threading

import pytest


# ---------------------------------------------------------------------------
# parse_log_level
# ---------------------------------------------------------------------------


def test_parse_log_level_error():
    from llamawatch.collectors.logs_collector import parse_log_level
    assert parse_log_level("ERROR: something failed") == "error"


def test_parse_log_level_critical():
    from llamawatch.collectors.logs_collector import parse_log_level
    assert parse_log_level("CRITICAL failure in subsystem") == "error"


def test_parse_log_level_fatal():
    from llamawatch.collectors.logs_collector import parse_log_level
    assert parse_log_level("FATAL: out of memory") == "error"


def test_parse_log_level_warning():
    from llamawatch.collectors.logs_collector import parse_log_level
    assert parse_log_level("WARNING: disk full") == "warn"


def test_parse_log_level_warn_short():
    from llamawatch.collectors.logs_collector import parse_log_level
    assert parse_log_level("WARN low memory") == "warn"


def test_parse_log_level_info():
    from llamawatch.collectors.logs_collector import parse_log_level
    assert parse_log_level("[INFO] started") == "info"


def test_parse_log_level_debug():
    from llamawatch.collectors.logs_collector import parse_log_level
    assert parse_log_level("DEBUG checking cache") == "debug"


def test_parse_log_level_trace():
    from llamawatch.collectors.logs_collector import parse_log_level
    assert parse_log_level("TRACE: entering function foo") == "debug"


def test_parse_log_level_default():
    from llamawatch.collectors.logs_collector import parse_log_level
    assert parse_log_level("some random line") == "info"


def test_parse_log_level_case_insensitive():
    from llamawatch.collectors.logs_collector import parse_log_level
    assert parse_log_level("error: lower case keyword") == "error"
    assert parse_log_level("warning: mixed case") == "warn"


# ---------------------------------------------------------------------------
# LogRingBuffer
# ---------------------------------------------------------------------------


def test_ring_buffer_overflow():
    from llamawatch.collectors.logs_collector import LogRingBuffer
    buf = LogRingBuffer(max_size=3)
    for i in range(5):
        buf.add({"message": f"line {i}"})
    lines = buf.get_all()
    assert len(lines) == 3
    assert lines[0]["message"] == "line 2"
    assert lines[-1]["message"] == "line 4"


def test_ring_buffer_under_capacity():
    from llamawatch.collectors.logs_collector import LogRingBuffer
    buf = LogRingBuffer(max_size=10)
    buf.add({"message": "only one"})
    assert len(buf.get_all()) == 1


def test_ring_buffer_thread_safety():
    from llamawatch.collectors.logs_collector import LogRingBuffer
    buf = LogRingBuffer(max_size=1000)

    def writer():
        for i in range(500):
            buf.add({"message": f"line {i}"})

    threads = [threading.Thread(target=writer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(buf.get_all()) == 1000


def test_ring_buffer_get_all_returns_copy():
    """Mutating the returned list must not affect the buffer."""
    from llamawatch.collectors.logs_collector import LogRingBuffer
    buf = LogRingBuffer(max_size=5)
    buf.add({"message": "a"})
    snapshot = buf.get_all()
    snapshot.clear()
    assert len(buf.get_all()) == 1


# ---------------------------------------------------------------------------
# parse_journalctl_json
# ---------------------------------------------------------------------------


def test_parse_journalctl_json_basic():
    from llamawatch.collectors.logs_collector import parse_journalctl_json
    line = json.dumps({
        "MESSAGE": "hello world",
        "PRIORITY": "6",
        "_SYSTEMD_UNIT": "test.service",
        "__REALTIME_TIMESTAMP": "1711360000000000",
    })
    parsed = parse_journalctl_json(line)
    assert parsed is not None
    assert parsed["message"] == "hello world"
    assert parsed["level"] == "info"
    assert parsed["source"] == "test.service"
    assert parsed["timestamp"] is not None


def test_parse_journalctl_json_priority_warn():
    from llamawatch.collectors.logs_collector import parse_journalctl_json
    line = json.dumps({"MESSAGE": "low disk", "PRIORITY": "4"})
    parsed = parse_journalctl_json(line)
    assert parsed["level"] == "warn"


def test_parse_journalctl_json_priority_error():
    from llamawatch.collectors.logs_collector import parse_journalctl_json
    line = json.dumps({"MESSAGE": "crash", "PRIORITY": "3"})
    parsed = parse_journalctl_json(line)
    assert parsed["level"] == "error"


def test_parse_journalctl_json_priority_debug():
    from llamawatch.collectors.logs_collector import parse_journalctl_json
    line = json.dumps({"MESSAGE": "trace", "PRIORITY": "7"})
    parsed = parse_journalctl_json(line)
    assert parsed["level"] == "debug"


def test_parse_journalctl_json_fallback_syslog_identifier():
    from llamawatch.collectors.logs_collector import parse_journalctl_json
    line = json.dumps({
        "MESSAGE": "msg",
        "PRIORITY": "6",
        "SYSLOG_IDENTIFIER": "myapp",
    })
    parsed = parse_journalctl_json(line)
    assert parsed["source"] == "myapp"


def test_parse_journalctl_json_invalid_returns_none():
    from llamawatch.collectors.logs_collector import parse_journalctl_json
    assert parse_journalctl_json("not json at all") is None
    assert parse_journalctl_json("") is None


def test_parse_journalctl_json_no_timestamp():
    from llamawatch.collectors.logs_collector import parse_journalctl_json
    line = json.dumps({"MESSAGE": "no ts"})
    parsed = parse_journalctl_json(line)
    assert parsed is not None
    assert parsed["timestamp"] is None


# ---------------------------------------------------------------------------
# collect()
# ---------------------------------------------------------------------------


def test_collect_returns_dict():
    from llamawatch.collectors.logs_collector import collect
    result = collect()
    assert isinstance(result, dict)
    assert "lines" in result
    assert "source_count" in result


def test_collect_source_count_from_widget_config():
    from llamawatch.collectors.logs_collector import collect
    wc = {
        "instance_id": "test-inst-1",
        "sources": [
            {"type": "journalctl", "target": "foo.service", "label": "Foo"},
            {"type": "file", "target": "/var/log/syslog", "label": "Syslog"},
        ],
    }
    result = collect(widget_config=wc)
    assert result["source_count"] == 2


def test_collect_empty_when_no_lines_yet():
    from llamawatch.collectors.logs_collector import collect
    result = collect(widget_config={"instance_id": "empty-instance-xyz"})
    assert result["lines"] == []


def test_collect_returns_buffered_lines():
    from llamawatch.collectors.logs_collector import collect, get_or_create_buffer
    key = "test-buffered-instance"
    buf = get_or_create_buffer(key)
    buf.add({"message": "buffered log line", "level": "info", "source": "x", "timestamp": None})

    result = collect(widget_config={"instance_id": key})
    assert any(l["message"] == "buffered log line" for l in result["lines"])


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------


def test_module_constants():
    from llamawatch.collectors import logs_collector as lc
    assert lc.WIDGET_ID == "logs"
    assert lc.WIDGET_NAME == "Logs Viewer"
    assert lc.WIDGET_MULTI_INSTANCE is True
    assert lc.WIDGET_CONFIG_REQUIRED is True
    assert isinstance(lc.WIDGET_DEFAULT_SIZE, dict)
    assert lc.WIDGET_DEFAULT_SIZE["w"] == 6
    assert isinstance(lc.WIDGET_CONFIG_SCHEMA, list)
    assert lc.WIDGET_CONFIG_SCHEMA[0]["key"] == "sources"
