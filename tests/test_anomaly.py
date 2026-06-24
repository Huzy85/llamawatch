"""Tests for the anomaly correlation collector."""

from unittest.mock import patch
import pytest

from llamawatch.collectors import anomaly


def test_module_constants():
    assert anomaly.WIDGET_ID == "anomaly"
    assert anomaly.WIDGET_NAME == "Anomaly Correlation"
    assert anomaly.WIDGET_MULTI_INSTANCE is False


class TestDetectAnomalies:
    def test_no_anomalies_when_all_normal(self):
        metrics = {"cpu_pct": 30.0, "ram_used_pct": 50.0, "gpu_pct": 20.0, "inference_idle": True, "services_down": []}
        result = anomaly.detect_anomalies(metrics)
        assert result["anomalies"] == []
        assert result["incident"] is None

    def test_high_ram_flagged(self):
        metrics = {"cpu_pct": 30.0, "ram_used_pct": 96.0, "gpu_pct": 20.0, "inference_idle": True, "services_down": []}
        result = anomaly.detect_anomalies(metrics)
        names = [a["name"] for a in result["anomalies"]]
        assert "high_ram" in names

    def test_two_anomalies_triggers_incident(self):
        metrics = {"cpu_pct": 95.0, "ram_used_pct": 96.0, "gpu_pct": 20.0, "inference_idle": True, "services_down": []}
        result = anomaly.detect_anomalies(metrics)
        assert len(result["anomalies"]) >= 2
        assert result["incident"] is not None
        assert "severity" in result["incident"]

    def test_slow_inference_flagged(self):
        metrics = {"cpu_pct": 30.0, "ram_used_pct": 50.0, "gpu_pct": 99.0, "inference_idle": False, "inference_tps": 2.0, "services_down": []}
        result = anomaly.detect_anomalies(metrics)
        names = [a["name"] for a in result["anomalies"]]
        assert "high_gpu" in names

    def test_service_down_flagged(self):
        metrics = {"cpu_pct": 30.0, "ram_used_pct": 50.0, "gpu_pct": 20.0, "inference_idle": True, "services_down": ["llama-server"]}
        result = anomaly.detect_anomalies(metrics)
        names = [a["name"] for a in result["anomalies"]]
        assert "service_down" in names


class TestCollect:
    def test_collect_returns_expected_keys(self):
        with patch.object(anomaly, "_gather_metrics", return_value={
            "cpu_pct": 10.0, "ram_used_pct": 30.0, "gpu_pct": 0.0, "inference_idle": True, "services_down": []}):
            result = anomaly.collect(config={})
        assert "anomalies" in result
        assert "incident" in result

    def test_collect_accepts_all_kwargs(self):
        with patch.object(anomaly, "_gather_metrics", return_value={
            "cpu_pct": 10.0, "ram_used_pct": 30.0, "gpu_pct": 0.0, "inference_idle": True, "services_down": []}):
            result = anomaly.collect(config={}, adapters=None, widget_config={})
        assert "anomalies" in result


class TestBuildLLMPrompt:
    def test_prompt_contains_anomaly_details(self):
        anomalies_list = [
            {"name": "high_ram", "message": "RAM at 96%", "severity": "warning"},
            {"name": "high_cpu", "message": "CPU at 95%", "severity": "warning"},
        ]
        prompt = anomaly.build_llm_prompt(anomalies_list)
        assert "RAM at 96%" in prompt
        assert "CPU at 95%" in prompt
