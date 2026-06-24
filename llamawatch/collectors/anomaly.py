"""Anomaly correlation collector — cross-widget anomaly detection with LLM root cause analysis."""

WIDGET_ID = "anomaly"
WIDGET_NAME = "Anomaly Correlation"
WIDGET_ICON = "\u26a0"
WIDGET_DESCRIPTION = "Cross-widget anomaly detection with LLM root cause analysis"
WIDGET_DEFAULT_SIZE = {"w": 4, "h": 2, "minW": 3, "minH": 2}
WIDGET_REQUIRES = []
WIDGET_CONFIG_SCHEMA: list[dict] = []
WIDGET_CONFIG_REQUIRED = False
WIDGET_MULTI_INSTANCE = False

_CPU_HIGH = 90.0
_RAM_HIGH = 92.0
_GPU_HIGH = 95.0
_TPS_LOW = 5.0


def _gather_metrics(config=None, adapters=None) -> dict:
    """Collect current metrics from other collectors inline."""
    metrics = {"cpu_pct": None, "ram_used_pct": None, "gpu_pct": None,
               "inference_idle": True, "inference_tps": None, "services_down": []}
    try:
        from llamawatch.collectors.system import collect_system
        sys_data = collect_system()
        metrics["cpu_pct"] = sys_data.get("cpu_pct")
        total = sys_data.get("ram_total_gb", 0)
        used = sys_data.get("ram_used_gb", 0)
        if total > 0:
            metrics["ram_used_pct"] = round((used / total) * 100, 1)
        metrics["gpu_pct"] = sys_data.get("gpu_pct")
    except Exception:
        pass
    try:
        from llamawatch.collectors.inference_speed import collect as speed_collect
        speed_data = speed_collect(config=config, adapters=adapters)
        metrics["inference_idle"] = speed_data.get("idle", True)
        metrics["inference_tps"] = speed_data.get("generation_tps")
    except Exception:
        pass
    try:
        from llamawatch.collectors.services import collect as svc_collect
        svc_list = svc_collect(config=config)
        metrics["services_down"] = [s.get("name", "?") for s in svc_list
                                     if s.get("status") not in ("active", "running")]
    except Exception:
        pass
    return metrics


def detect_anomalies(metrics: dict) -> dict:
    """Detect anomalies. Returns {anomalies: [{name, message, severity}], incident: dict|None}."""
    anomalies: list[dict] = []

    cpu = metrics.get("cpu_pct")
    if cpu is not None and cpu >= _CPU_HIGH:
        anomalies.append({"name": "high_cpu", "message": f"CPU at {cpu}%", "severity": "warning"})

    ram = metrics.get("ram_used_pct")
    if ram is not None and ram >= _RAM_HIGH:
        sev = "critical" if ram >= 97.0 else "warning"
        anomalies.append({"name": "high_ram", "message": f"RAM at {ram}%", "severity": sev})

    gpu = metrics.get("gpu_pct")
    if gpu is not None and gpu >= _GPU_HIGH:
        anomalies.append({"name": "high_gpu", "message": f"GPU at {gpu}%", "severity": "warning"})

    if not metrics.get("inference_idle") and metrics.get("inference_tps") is not None:
        if metrics["inference_tps"] < _TPS_LOW:
            anomalies.append({"name": "slow_inference", "message": f"Inference at {metrics['inference_tps']:.1f} tok/s", "severity": "warning"})

    for svc_name in metrics.get("services_down", []):
        anomalies.append({"name": "service_down", "message": f"{svc_name} is down", "severity": "warning"})

    incident = None
    if len(anomalies) >= 2:
        has_critical = any(a["severity"] == "critical" for a in anomalies)
        incident = {
            "severity": "critical" if has_critical else "warning",
            "summary": "; ".join(a["message"] for a in anomalies),
            "count": len(anomalies),
        }

    return {"anomalies": anomalies, "incident": incident}


def build_llm_prompt(anomalies: list[dict]) -> str:
    lines = ["You are an ops assistant for a local LLM server. Multiple anomalies detected simultaneously:"]
    for a in anomalies:
        lines.append(f"- {a['message']}")
    lines.append("")
    lines.append("In one sentence, what is the most likely root cause and what should the user check first?")
    return "\n".join(lines)


def collect(config=None, adapters=None, widget_config=None) -> dict:
    try:
        metrics = _gather_metrics(config=config, adapters=adapters)
        return detect_anomalies(metrics)
    except Exception:
        return {"anomalies": [], "incident": None}
