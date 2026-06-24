"""Tests for token usage counters and latency percentiles in inference speed collector."""

from unittest.mock import patch
import pytest

from llamawatch.collectors import inference_speed


METRICS_WITH_TOKENS = """# HELP llamacpp_tokens_predicted_total Total predicted tokens
llamacpp:tokens_predicted_total 15000
llamacpp:tokens_predicted_seconds_total 500
llamacpp:prompt_tokens_total 45000
llamacpp:prompt_seconds_total 300
llamacpp:time_to_first_token_ms_total 5000
llamacpp:time_to_first_token_ms_count 100
"""


class FakeAdapter:
    def __init__(self, url):
        self.url = url


class FakeAdapters:
    def __init__(self, adapters):
        self._adapters = adapters
    def get_all(self):
        return self._adapters


class TestTokenCounters:
    def setup_method(self):
        inference_speed._prev_counters.clear()
        inference_speed._history.clear()
        inference_speed._ttft_history.clear()

    def test_collect_includes_token_totals(self):
        adapters = FakeAdapters([FakeAdapter("http://localhost:8080")])
        with patch.object(inference_speed, "_http_get", return_value=METRICS_WITH_TOKENS):
            result = inference_speed.collect(config={}, adapters=adapters)
        assert result["total_generation_tokens"] == 15000
        assert result["total_prompt_tokens"] == 45000

    def test_collect_returns_none_when_no_metrics(self):
        result = inference_speed.collect(config={}, adapters=None)
        assert result["total_generation_tokens"] is None
        assert result["total_prompt_tokens"] is None

    def test_collect_returns_none_when_metrics_missing_keys(self):
        adapters = FakeAdapters([FakeAdapter("http://localhost:8080")])
        with patch.object(inference_speed, "_http_get", return_value="# no metrics\n"):
            result = inference_speed.collect(config={}, adapters=adapters)
        assert result["total_generation_tokens"] is None
        assert result["total_prompt_tokens"] is None


class TestLatencyPercentiles:
    def setup_method(self):
        inference_speed._prev_counters.clear()
        inference_speed._history.clear()
        inference_speed._ttft_history.clear()

    def test_collect_includes_percentile_keys(self):
        result = inference_speed.collect(config={}, adapters=None)
        assert "ttft_p50" in result
        assert "ttft_p95" in result

    def test_percentiles_computed_from_history(self):
        inference_speed._ttft_history.clear()
        for v in [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]:
            inference_speed._ttft_history.append(v)
        result = inference_speed.collect(config={}, adapters=None)
        assert result["ttft_p50"] == 55.0
        assert result["ttft_p95"] == 95.5
