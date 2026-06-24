"""Tests for KV cache usage in model_status collector."""

from unittest.mock import patch
import pytest

from llamawatch.collectors import model_status


FAKE_SLOTS = [
    {"id": 0, "n_ctx": 204800, "is_processing": False, "next_token": [{"n_decoded": 5000}]},
    {"id": 1, "n_ctx": 204800, "is_processing": True, "next_token": [{"n_decoded": 30000}]},
    {"id": 2, "n_ctx": 204800, "is_processing": False, "next_token": [{"n_decoded": 0}]},
    {"id": 3, "n_ctx": 204800, "is_processing": False, "next_token": [{"n_decoded": 1000}]},
]


class TestReadKvUsage:
    def test_computes_usage_from_slots(self):
        with patch.object(model_status, "_http_get_json", return_value=FAKE_SLOTS),\
             patch.object(model_status, "_get_model_name", return_value=("TestModel", "Test")):
            result = model_status._read_kv_usage()
        assert result is not None
        assert "kv_used" in result
        assert "kv_total" in result
        assert "kv_pct" in result
        # 5000 + 30000 + 0 + 1000 = 36000 tokens used, 4 * 204800 = 819200 total
        assert result["kv_used"] == 36000
        assert result["kv_total"] == 819200
        assert 0 < result["kv_pct"] < 100

    def test_returns_none_when_slots_unavailable(self):
        with patch.object(model_status, "_http_get_json", return_value=None),\
             patch.object(model_status, "_get_model_name", return_value=("TestModel", "Test")):
            result = model_status._read_kv_usage()
        assert result is None

    def test_returns_none_when_slots_empty(self):
        with patch.object(model_status, "_http_get_json", return_value=[]),\
             patch.object(model_status, "_get_model_name", return_value=("TestModel", "Test")):
            result = model_status._read_kv_usage()
        assert result is None

    def test_handles_dict_next_token(self):
        """llama-server may return next_token as a dict, not a list."""
        slots = [
            {"id": 0, "n_ctx": 4096, "next_token": {"n_decoded": 100}},
            {"id": 1, "n_ctx": 4096, "next_token": {"n_decoded": 200}},
        ]
        result = model_status._read_kv_usage(slots)
        assert result is not None
        assert result["kv_used"] == 300
        assert result["kv_total"] == 8192

    def test_accepts_slots_directly(self):
        """When slots are passed directly, no HTTP call is made."""
        result = model_status._read_kv_usage(FAKE_SLOTS)
        assert result is not None
        assert result["kv_used"] == 36000
