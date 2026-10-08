"""Model status collector for llamawatch."""

WIDGET_ID = "model-status"
WIDGET_NAME = "Model Status"
WIDGET_DEFAULT_SIZE = {"w": 4, "h": 2, "minW": 3, "minH": 2}
WIDGET_REQUIRES = ["backend"]
WIDGET_ICON = "🤖"
WIDGET_DESCRIPTION = "LLM model health and status"
WIDGET_CONFIG_SCHEMA: list[dict] = []
WIDGET_MULTI_INSTANCE = False

import json
import time
import urllib.request
from pathlib import Path

from ..config import get_model_friendly_name

SWAP_LOCK = Path("/tmp/model_swap_in_progress")

# Swap step timing thresholds (seconds elapsed -> step number + description)
# Based on observed model-swap timings
_SWAP_STEPS = [
    (5,  1, "Unloading old model"),
    (15, 2, "Waiting for unload"),
    (25, 3, "GTT memory release (10s)"),
    (35, 4, "Flushing RAM cache"),
    (55, 5, "Warming up subsystems"),
    (999, 6, "Loading new model"),
]

# Hardcoded RAM values per model class
_MODEL_RAM = {
    "default": {"ram_gb": 70, "kv_gb": 8},
}


def _http_get(url: str, timeout: float = 3.0) -> str | None:
    """GET a URL and return the response body, or None on failure."""
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except Exception:
        return None


def _http_get_json(url: str, timeout: float = 3.0) -> dict | list | None:
    """GET a URL and parse as JSON, or None on failure."""
    body = _http_get(url, timeout)
    if body is None:
        return None
    try:
        return json.loads(body)
    except Exception:
        return None


def _check_swap_status() -> dict | None:
    """Check if a model swap is in progress. Returns swap info or None."""
    try:
        if not SWAP_LOCK.exists():
            return None

        content = SWAP_LOCK.read_text().strip()
        lines = content.split("\n")
        if not lines:
            return None

        timestamp = float(lines[0].strip())
        elapsed = time.time() - timestamp

        swap_from = None
        swap_to = None
        if len(lines) > 1 and "->" in lines[1]:
            parts = lines[1].split("->")
            swap_from = parts[0].strip()
            swap_to = parts[1].strip()

        # Determine step from elapsed time
        step = 6
        step_desc = "Loading new model"
        for threshold, step_num, desc in _SWAP_STEPS:
            if elapsed < threshold:
                step = step_num
                step_desc = desc
                break

        return {
            "swap_from": swap_from,
            "swap_to": swap_to,
            "swap_step": step,
            "swap_step_desc": step_desc,
            "swap_elapsed": round(elapsed),
        }
    except Exception:
        return None


def _get_model_name() -> tuple[str, str]:
    """Get model ID and friendly name from swap-proxy. Returns (id, friendly)."""
    data = _http_get_json("http://localhost:8081/v1/models", timeout=3.0)
    if data and "data" in data and len(data["data"]) > 0:
        model_id = data["data"][0].get("id", "unknown")
        friendly = get_model_friendly_name(model_id)
        return model_id, friendly
    return "unknown", "Unknown"


def _check_health() -> bool:
    """Check if the main model is healthy."""
    body = _http_get("http://localhost:8081/health", timeout=3.0)
    return body is not None


def _get_n_decoded(slot: dict) -> int:
    """Extract n_decoded from a slot, handling both list and dict next_token."""
    nt = slot.get("next_token")
    if isinstance(nt, list) and nt:
        return nt[0].get("n_decoded", 0)
    if isinstance(nt, dict):
        return nt.get("n_decoded", 0)
    return slot.get("n_decoded", 0)


def _fetch_slots() -> list | None:
    """Fetch /slots from llama-server. Returns the parsed list or None."""
    model_id, _ = _get_model_name()
    url = f"http://localhost:8080/slots?model={model_id}" if model_id != "unknown" else "http://localhost:8080/slots"
    data = _http_get_json(url, timeout=2.0)
    if data and isinstance(data, list):
        return data
    return None


def _check_generating_from_slots(slots: list | None) -> bool:
    """Check if any slot is actively processing."""
    if not slots:
        return False
    for slot in slots:
        if slot.get("is_processing", False):
            return True
    return False


def _check_generating() -> bool:
    """Check if any llama-server slot is actively processing."""
    return _check_generating_from_slots(_fetch_slots())


def _read_kv_usage(slots: list | None = None) -> dict | None:
    """Read KV cache usage from slot data.

    Returns {kv_used: int, kv_total: int, kv_pct: float} or None.
    kv_used and kv_total are in tokens (not bytes).
    If slots is None, fetches from /slots API.
    """
    if slots is None:
        slots = _fetch_slots()
    if not slots:
        return None

    total_ctx = 0
    total_used = 0
    for slot in slots:
        total_ctx += slot.get("n_ctx", 0)
        total_used += _get_n_decoded(slot)

    if total_ctx == 0:
        return None

    return {
        "kv_used": total_used,
        "kv_total": total_ctx,
        "kv_pct": round((total_used / total_ctx) * 100, 1),
    }


def collect(config=None, adapters=None) -> dict:
    """Collect model status — registry-compatible entry point.

    When adapters are available, returns data for ALL adapters so the
    frontend can display every loaded model.
    """
    if adapters:
        all_adapters = adapters.get_all()
        if all_adapters:
            kv = _read_kv_usage()
            models = []
            for adapter in all_adapters:
                name = adapter.config.get("name", "")
                health = adapter.health()
                status = "generating" if adapter.is_generating() else "idle"
                model_data = {
                    "name": adapter.model_name(),
                    "friendly": adapter.model_friendly_name(),
                    "status": status,
                    "health": health,
                    "backend_name": name,
                    "ram_gb": _MODEL_RAM["default"]["ram_gb"],
                    "kv_gb": None if kv else _MODEL_RAM["default"]["kv_gb"],
                }
                if kv:
                    model_data.update(kv)
                # Check health — mark unreachable if down
                if health == "unreachable":
                    model_data["status"] = "unreachable"
                # Check for swap in progress
                swap = adapter.swap_status()
                if swap:
                    model_data["status"] = "swapping"
                    model_data.update(swap)
                models.append(model_data)
            primary = models[0] if models else None
            # Return both the models list AND the primary as top-level
            # keys for backward compatibility
            result = dict(primary) if primary else _collect_model()
            result["models"] = models
            return result
    return _collect_model()


def _collect_via_adapter(adapter) -> dict:
    """Collect model status using a backend adapter."""
    # Check for swap in progress
    swap = adapter.swap_status()
    if swap:
        swap_to = swap.get("swap_to", "unknown")
        friendly = adapter.model_friendly_name() if swap_to == "unknown" else adapter._model_names.get(swap_to, swap_to)
        return {
            "name": swap_to or "unknown",
            "friendly": friendly,
            "status": "swapping",
            "ram_gb": _MODEL_RAM["default"]["ram_gb"],
            "kv_gb": _MODEL_RAM["default"]["kv_gb"],
            **swap,
        }

    # Not swapping — query adapter
    health = adapter.health()
    if health == "unreachable":
        return {
            "name": adapter.model_name(),
            "friendly": adapter.model_friendly_name(),
            "status": "unreachable",
            "ram_gb": _MODEL_RAM["default"]["ram_gb"],
            "kv_gb": _MODEL_RAM["default"]["kv_gb"],
            "swap_step": None,
            "swap_from": None,
            "swap_to": None,
        }

    status = "generating" if adapter.is_generating() else "idle"
    return {
        "name": adapter.model_name(),
        "friendly": adapter.model_friendly_name(),
        "status": status,
        "ram_gb": _MODEL_RAM["default"]["ram_gb"],
        "kv_gb": _MODEL_RAM["default"]["kv_gb"],
        "swap_step": None,
        "swap_from": None,
        "swap_to": None,
    }


def collect_model() -> dict:
    """Legacy entry point — kept for backwards compatibility."""
    return _collect_model()


def _collect_model() -> dict:
    """Collect model status and return as a dict."""
    # Check for swap in progress first
    swap_info = _check_swap_status()

    if swap_info is not None:
        # During swap, try to get the target model name
        swap_to = swap_info.get("swap_to", "unknown")
        friendly = get_model_friendly_name(swap_to) if swap_to else "Unknown"

        return {
            "name": swap_to or "unknown",
            "friendly": friendly,
            "status": "swapping",
            "ram_gb": _MODEL_RAM["default"]["ram_gb"],
            "kv_gb": _MODEL_RAM["default"]["kv_gb"],
            "swap_step": swap_info["swap_step"],
            "swap_step_desc": swap_info["swap_step_desc"],
            "swap_elapsed": swap_info["swap_elapsed"],
            "swap_from": swap_info["swap_from"],
            "swap_to": swap_info["swap_to"],
        }

    # Not swapping — check what's loaded
    model_id, friendly = _get_model_name()

    # Check health
    if not _check_health():
        return {
            "name": model_id,
            "friendly": friendly,
            "status": "unreachable",
            "ram_gb": _MODEL_RAM["default"]["ram_gb"],
            "kv_gb": _MODEL_RAM["default"]["kv_gb"],
            "swap_step": None,
            "swap_from": None,
            "swap_to": None,
        }

    # Single /slots fetch for both generating status and KV cache
    slots = _fetch_slots()
    status = "generating" if _check_generating_from_slots(slots) else "idle"

    kv = _read_kv_usage(slots)
    result = {
        "name": model_id,
        "friendly": friendly,
        "status": status,
        "ram_gb": _MODEL_RAM["default"]["ram_gb"],
        "kv_gb": None if kv else _MODEL_RAM["default"]["kv_gb"],
        "swap_step": None,
        "swap_from": None,
        "swap_to": None,
    }
    if kv:
        result.update(kv)
    return result
