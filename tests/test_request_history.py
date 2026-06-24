"""Tests for the request history logger and collector."""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from llamawatch import request_log
from llamawatch.collectors import request_history


class TestRequestLog:
    def test_log_request_creates_file(self, tmp_path):
        log = request_log.RequestLog(log_dir=str(tmp_path))
        log.log_request(
            model="hercules",
            prompt_preview="What is 2+2?",
            response_preview="4",
            prompt_tokens=10,
            completion_tokens=5,
            duration_ms=250,
        )
        files = list(tmp_path.glob("*.jsonl"))
        assert len(files) == 1

    def test_log_request_writes_valid_jsonl(self, tmp_path):
        log = request_log.RequestLog(log_dir=str(tmp_path))
        log.log_request(
            model="hercules",
            prompt_preview="Hello",
            response_preview="Hi there",
            prompt_tokens=3,
            completion_tokens=5,
            duration_ms=100,
        )
        files = list(tmp_path.glob("*.jsonl"))
        line = files[0].read_text().strip()
        entry = json.loads(line)
        assert entry["model"] == "hercules"
        assert entry["prompt_tokens"] == 3
        assert entry["completion_tokens"] == 5
        assert entry["duration_ms"] == 100
        assert "timestamp" in entry

    def test_get_recent_returns_entries(self, tmp_path):
        log = request_log.RequestLog(log_dir=str(tmp_path))
        for i in range(5):
            log.log_request(
                model="test",
                prompt_preview=f"Prompt {i}",
                response_preview=f"Response {i}",
                prompt_tokens=10,
                completion_tokens=5,
                duration_ms=100 + i,
            )
        entries = log.get_recent(3)
        assert len(entries) == 3
        # Most recent first
        assert entries[0]["prompt_preview"] == "Prompt 4"

    def test_get_recent_caps_at_limit(self, tmp_path):
        log = request_log.RequestLog(log_dir=str(tmp_path))
        for i in range(100):
            log.log_request(model="test", prompt_preview=f"P{i}", response_preview="R",
                           prompt_tokens=1, completion_tokens=1, duration_ms=10)
        entries = log.get_recent(20)
        assert len(entries) == 20

    def test_get_recent_handles_empty_dir(self, tmp_path):
        log = request_log.RequestLog(log_dir=str(tmp_path))
        entries = log.get_recent(10)
        assert entries == []


class TestRequestHistoryCollector:
    def test_module_constants(self):
        assert request_history.WIDGET_ID == "request-history"
        assert request_history.WIDGET_NAME == "Request History"

    def test_collect_returns_requests_key(self, tmp_path):
        with patch.object(request_history, "_get_log", return_value=request_log.RequestLog(log_dir=str(tmp_path))):
            result = request_history.collect()
        assert "requests" in result
        assert isinstance(result["requests"], list)

    def test_collect_accepts_all_kwargs(self, tmp_path):
        with patch.object(request_history, "_get_log", return_value=request_log.RequestLog(log_dir=str(tmp_path))):
            result = request_history.collect(config={}, adapters=None, widget_config={})
        assert "requests" in result
