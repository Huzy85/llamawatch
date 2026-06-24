"""Slots occupancy collector — per-slot load across ALL configured backends."""

WIDGET_ID = "slots"
WIDGET_NAME = "Slots"
WIDGET_DEFAULT_SIZE = {"w": 4, "h": 3, "minW": 3, "minH": 2}
WIDGET_REQUIRES = ["backend"]
WIDGET_ICON = "⬛"
WIDGET_DESCRIPTION = "Per-slot occupancy for all llama-server backends"
WIDGET_CONFIG_SCHEMA: list[dict] = []
WIDGET_MULTI_INSTANCE = False

import json
import urllib.parse
import urllib.request

# Per-backend cache so we can show the correct pip count when a backend
# is temporarily unreachable (e.g. llama-router long-poll timeout).
_last_known: dict[str, dict] = {}  # name -> {"total": N, "model": str}


def _http_get_json(url: str, timeout: float = 2.0):
    """GET a URL and return parsed JSON, or None on failure."""
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
        return json.loads(body)
    except Exception:
        return None


def _resolve_model_id(base_url: str, timeout: float = 2.0) -> str | None:
    """Resolve the first model ID advertised at base_url/v1/models."""
    data = _http_get_json(f"{base_url}/v1/models", timeout=timeout)
    if data and isinstance(data.get("data"), list) and data["data"]:
        return data["data"][0].get("id")
    return None


def _slots_base_url(backend: dict) -> str:
    """Return the host:port URL to use for /slots queries.

    For swap-proxy backends (swap_proxy=True), the proxy itself does not
    forward /slots — use the direct_port instead.  For all other backends
    use their configured url.
    """
    if backend.get("swap_proxy") and backend.get("direct_port"):
        return f"http://localhost:{backend['direct_port']}"
    return backend.get("url", "").rstrip("/")


def _friendly_name(backend: dict, model_id: str | None) -> str:
    """Return a human-friendly backend name.

    Uses the user's configured model-names map (Settings → Backends → friendly
    names) to translate the model ID, then falls back to the backend's own
    configured name, then a generic label.
    """
    if model_id:
        try:
            from ..config import get_model_friendly_name
            friendly = get_model_friendly_name(model_id)
            if friendly and friendly != model_id:
                return friendly
        except Exception:
            pass
    return backend.get("name") or "Backend"


def _extract_activity(slot: dict) -> dict:
    """Pull meaningful activity fields out of a raw slot dict.

    Fields available in llama.cpp /slots response:
      id_task          — integer task id
      n_ctx            — slot context size (capacity)
      next_token[0]    — {has_next_token, n_decoded, n_remain}

    We derive:
      tokens_decoded   — tokens generated so far (n_decoded from next_token)
      tokens_remain    — tokens still to generate (n_remain from next_token)
      task_id          — id_task value
    """
    activity: dict = {}

    task_id = slot.get("id_task")
    if task_id is not None:
        activity["task_id"] = task_id

    nt_list = slot.get("next_token")
    if isinstance(nt_list, list) and nt_list:
        nt = nt_list[0]
        n_decoded = nt.get("n_decoded")
        n_remain  = nt.get("n_remain")
        if n_decoded is not None:
            activity["tokens_decoded"] = n_decoded
        if n_remain is not None:
            activity["tokens_remain"] = n_remain

    return activity


def _fetch_backend_slots(backend: dict) -> dict:
    """Query /slots for a single backend.

    Returns a dict::

        {
            "name": "Main",
            "model": "my-model",
            "total": 8,
            "busy": 1,
            "reachable": True,
            "slots": [
                {"id": 0, "busy": False, "ctx_total": 49152},
                {"id": 1, "busy": True,
                 "ctx_total": 49152,
                 "task_id": 2291582,
                 "tokens_decoded": 4,
                 "tokens_remain": 96},
                ...
            ],
        }
    """
    slots_url = _slots_base_url(backend)
    model_id  = _resolve_model_id(slots_url, timeout=2.0)

    if model_id is None:
        name = _friendly_name(backend, None)
        cached = _last_known.get(name, {})
        idle_slots = [{"id": i, "busy": False} for i in range(cached.get("total", 0))]
        return {
            "name": name,
            "model": cached.get("model"),
            "total": cached.get("total", 0),
            "busy": 0,
            "reachable": False,
            "slots": idle_slots,
        }

    name = _friendly_name(backend, model_id)

    # Try bare /slots first (fast, non-blocking).
    # The ?model= variant on llama-router is a long-poll "wait for slot" endpoint
    # and will hang when all slots are busy — use it only as a fallback.
    raw = _http_get_json(f"{slots_url}/slots", timeout=4.0)

    if not isinstance(raw, list):
        # Fallback: try with model param (direct llama-server behind a proxy)
        url = f"{slots_url}/slots?model={urllib.parse.quote(model_id)}"
        raw = _http_get_json(url, timeout=4.0)

    if not isinstance(raw, list):
        cached = _last_known.get(name, {})
        idle_slots = [{"id": i, "busy": False} for i in range(cached.get("total", 0))]
        return {
            "name": name,
            "model": model_id,
            "total": cached.get("total", 0),
            "busy": 0,
            "reachable": False,
            "slots": idle_slots,
        }

    slots = []
    for slot in raw:
        slot_id = slot.get("id", len(slots))
        busy    = bool(slot.get("is_processing", False))
        ctx_total = slot.get("n_ctx")

        entry: dict = {"id": slot_id, "busy": busy}
        if ctx_total is not None:
            entry["ctx_total"] = ctx_total

        if busy:
            entry.update(_extract_activity(slot))

        slots.append(entry)

    busy_count = sum(1 for s in slots if s["busy"])

    # Cache so we can show correct pip count during temporary outages
    _last_known[name] = {"total": len(slots), "model": model_id}

    return {
        "name": name,
        "model": model_id,
        "total": len(slots),
        "busy": busy_count,
        "reachable": True,
        "slots": slots,
    }


def collect(config=None, adapters=None, widget_config=None) -> dict:
    """Collect slot occupancy from ALL configured llama-cpp backends.

    Returns::

        {
            "backends": [
                {
                    "name": "Main",
                    "model": "my-model",
                    "total": 8,
                    "busy": 1,
                    "reachable": True,
                    "slots": [
                        {"id": 0, "busy": False, "ctx_total": 49152},
                        {"id": 1, "busy": True,
                         "ctx_total": 49152,
                         "task_id": 2291582,
                         "tokens_decoded": 4,
                         "tokens_remain": 96},
                        ...
                    ],
                },
                {
                    "name": "Secondary",
                    "model": "my-model",
                    "total": 2,
                    "busy": 0,
                    "reachable": True,
                    "slots": [...],
                },
            ],
            "total": 10,
            "busy": 1,
        }

    Returns {} when no backends are configured or all are unreachable.
    """
    backends_cfg: list[dict] = []
    if config and isinstance(config.get("backends"), list):
        backends_cfg = [b for b in config["backends"] if b.get("type") == "llamacpp"]

    if not backends_cfg:
        return {}

    results = []
    for backend in backends_cfg:
        results.append(_fetch_backend_slots(backend))

    total_slots = sum(b["total"] for b in results)
    total_busy  = sum(b["busy"]  for b in results)

    return {
        "backends": results,
        "total":    total_slots,
        "busy":     total_busy,
    }
