"""Tests for the inference speed collector."""

import pytest


def test_parse_prometheus_metrics():
    from llamawatch.collectors.inference_speed import parse_prometheus_metrics

    text = """# HELP llamacpp:tokens_predicted_total Total predicted tokens
# TYPE llamacpp:tokens_predicted_total counter
llamacpp:tokens_predicted_total 1500
# HELP llamacpp:tokens_predicted_seconds_total Total seconds for prediction
# TYPE llamacpp:tokens_predicted_seconds_total counter
llamacpp:tokens_predicted_seconds_total 50.5
# HELP llamacpp:prompt_tokens_total Total prompt tokens
# TYPE llamacpp:prompt_tokens_total counter
llamacpp:prompt_tokens_total 3000
# HELP llamacpp:prompt_seconds_total Total prompt processing seconds
# TYPE llamacpp:prompt_seconds_total counter
llamacpp:prompt_seconds_total 10.2
"""
    result = parse_prometheus_metrics(text)
    assert result["llamacpp:tokens_predicted_total"] == 1500.0
    assert result["llamacpp:tokens_predicted_seconds_total"] == 50.5
    assert result["llamacpp:prompt_tokens_total"] == 3000.0
    assert result["llamacpp:prompt_seconds_total"] == 10.2


def test_parse_prometheus_metrics_empty():
    from llamawatch.collectors.inference_speed import parse_prometheus_metrics

    result = parse_prometheus_metrics("")
    assert result == {}


def test_parse_prometheus_metrics_ignores_comments():
    from llamawatch.collectors.inference_speed import parse_prometheus_metrics

    text = """# HELP some_metric A metric
# TYPE some_metric counter
some_metric 42.0
"""
    result = parse_prometheus_metrics(text)
    assert "some_metric" in result
    assert result["some_metric"] == 42.0
    # Comment lines should not appear
    assert not any(k.startswith("#") for k in result)


def test_parse_prometheus_metrics_strips_labels():
    from llamawatch.collectors.inference_speed import parse_prometheus_metrics

    text = 'llamacpp:tokens_predicted_total{model="my-model"} 999\n'
    result = parse_prometheus_metrics(text)
    assert result["llamacpp:tokens_predicted_total"] == 999.0


def test_parse_prometheus_metrics_invalid_value():
    from llamawatch.collectors.inference_speed import parse_prometheus_metrics

    # Non-numeric value should be silently ignored
    text = "some_metric not_a_number\n"
    result = parse_prometheus_metrics(text)
    assert "some_metric" not in result


def test_history_ring_buffer():
    from llamawatch.collectors.inference_speed import _history

    _history.clear()
    for i in range(60):
        _history.append(float(i))
    assert len(_history) == 50
    assert _history[0] == 10.0


def test_collect_returns_idle_when_no_adapters():
    from llamawatch.collectors.inference_speed import collect

    result = collect(config={}, adapters=None, widget_config=None)
    assert result["idle"] is True
    assert result["generation_tps"] is None
    assert result["prompt_tps"] is None
    assert "history" in result
    assert isinstance(result["history"], list)


def test_collect_returns_idle_when_adapters_empty():
    from llamawatch.collectors.inference_speed import collect

    class FakeAdapters:
        def get_all(self):
            return []

    result = collect(config={}, adapters=FakeAdapters(), widget_config=None)
    assert result["idle"] is True


def test_collect_returns_data_structure():
    from llamawatch.collectors.inference_speed import collect

    result = collect(config={}, adapters=None, widget_config=None)
    required_keys = {"generation_tps", "prompt_tps", "ttft_ms", "history", "idle", "idle_since"}
    assert required_keys.issubset(result.keys())


def test_collect_with_unreachable_adapter():
    """When adapter returns no metrics, result should still be structurally valid."""
    from llamawatch.collectors.inference_speed import collect

    class FakeAdapter:
        url = "http://localhost:9999"
        config = {}

    class FakeAdapters:
        def get_all(self):
            return [FakeAdapter()]

    result = collect(config={}, adapters=FakeAdapters(), widget_config=None)
    # Should not raise; returns valid structure even when unreachable
    assert "idle" in result
    assert "history" in result


def test_delta_tps_computed_from_counter_diff():
    """Delta tok/s is computed correctly from two successive counter readings."""
    from llamawatch.collectors import inference_speed

    # Reset state
    inference_speed._prev_counters.clear()
    inference_speed._history.clear()
    inference_speed._last_gen_time = None

    url = "http://fake-adapter:8080"

    # First reading: set baseline
    first_metrics = {
        "llamacpp:tokens_predicted_total": 1000.0,
        "llamacpp:tokens_predicted_seconds_total": 20.0,
        "llamacpp:prompt_tokens_total": 500.0,
        "llamacpp:prompt_seconds_total": 5.0,
    }
    inference_speed._prev_counters[url] = first_metrics

    # Second reading: 500 tokens in 10 seconds = 50 tok/s
    second_metrics = {
        "llamacpp:tokens_predicted_total": 1500.0,
        "llamacpp:tokens_predicted_seconds_total": 30.0,
        "llamacpp:prompt_tokens_total": 700.0,
        "llamacpp:prompt_seconds_total": 7.0,
    }

    gen_tps, prompt_tps = inference_speed._compute_tps(url, second_metrics)

    assert gen_tps is not None
    assert abs(gen_tps - 50.0) < 0.01
    assert prompt_tps is not None
    assert abs(prompt_tps - 100.0) < 0.01


def test_delta_tps_zero_time_delta_returns_none():
    """If time delta is zero or negative, return None to avoid division by zero."""
    from llamawatch.collectors import inference_speed

    inference_speed._prev_counters.clear()

    url = "http://fake2:8080"
    same_metrics = {
        "llamacpp:tokens_predicted_total": 1000.0,
        "llamacpp:tokens_predicted_seconds_total": 20.0,
        "llamacpp:prompt_tokens_total": 500.0,
        "llamacpp:prompt_seconds_total": 5.0,
    }
    inference_speed._prev_counters[url] = same_metrics

    gen_tps, prompt_tps = inference_speed._compute_tps(url, same_metrics)

    assert gen_tps is None
    assert prompt_tps is None


def test_history_appended_when_generation_active():
    """History deque gets a new entry when gen_tps > 0."""
    from llamawatch.collectors import inference_speed

    inference_speed._history.clear()
    inference_speed._prev_counters.clear()
    inference_speed._last_gen_time = None

    url = "http://fake3:8080"
    inference_speed._prev_counters[url] = {
        "llamacpp:tokens_predicted_total": 0.0,
        "llamacpp:tokens_predicted_seconds_total": 0.0,
        "llamacpp:prompt_tokens_total": 0.0,
        "llamacpp:prompt_seconds_total": 0.0,
    }

    current = {
        "llamacpp:tokens_predicted_total": 100.0,
        "llamacpp:tokens_predicted_seconds_total": 2.0,
        "llamacpp:prompt_tokens_total": 50.0,
        "llamacpp:prompt_seconds_total": 0.5,
    }

    initial_len = len(inference_speed._history)
    inference_speed._update_history(url, current)
    assert len(inference_speed._history) == initial_len + 1
