"""llama.cpp / swap-proxy backend adapter for LlamaWatch."""

import json
import time
import urllib.request
from pathlib import Path

from .base import BackendAdapter

SWAP_LOCK = Path("/tmp/model_swap_in_progress")

# Swap step timing thresholds (seconds elapsed -> step number + description)
# Based on observed model-swap timings
_SWAP_STEPS = [
    (5, 1, "Unloading old model"),
    (15, 2, "Waiting for unload"),
    (25, 3, "GTT memory release (10s)"),
    (35, 4, "Flushing RAM cache"),
    (55, 5, "Warming up subsystems"),
    (999, 6, "Loading new model"),
]


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


class LlamaCppAdapter(BackendAdapter):
    """Adapter for llama.cpp servers, with optional swap-proxy support."""

    def __init__(self, url: str, config: dict) -> None:
        super().__init__(url, config)
        self.is_swap_proxy: bool = config.get("swap_proxy", False)
        self.direct_port: int = config.get("direct_port", 8080)

    def health(self) -> str:
        """Check /health endpoint."""
        body = _http_get(f"{self.url}/health", timeout=3.0)
        if body is None:
            return "unreachable"
        # llama.cpp /health returns JSON with "status" field
        try:
            data = json.loads(body)
            status = data.get("status", "")
            if status == "ok":
                return "healthy"
            if status in ("no slot available", "loading model"):
                return "healthy"
            return "error"
        except (json.JSONDecodeError, AttributeError):
            # Non-JSON 200 response — treat as healthy
            return "healthy"

    def model_name(self) -> str:
        """Get model ID from /v1/models endpoint."""
        data = _http_get_json(f"{self.url}/v1/models", timeout=3.0)
        if data and "data" in data and len(data["data"]) > 0:
            return data["data"][0].get("id", "unknown")
        return "unknown"

    def is_generating(self) -> bool:
        """Check if any llama-server slot is actively processing.

        NOTE: Queries localhost:{direct_port}/slots directly. For remote
        proxy setups where llama-server is not on localhost, returns False.
        """
        model_id = self.model_name()
        if model_id != "unknown":
            url = f"http://localhost:{self.direct_port}/slots?model={model_id}"
        else:
            url = f"http://localhost:{self.direct_port}/slots"
        data = _http_get_json(url, timeout=2.0)
        if data and isinstance(data, list):
            for slot in data:
                if slot.get("is_processing", False):
                    return True
        return False

    def models_available(self) -> list[dict]:
        """Get available models from /v1/models."""
        data = _http_get_json(f"{self.url}/v1/models", timeout=3.0)
        if data and "data" in data:
            return data["data"]
        return []

    def chat_completions_url(self) -> str:
        """Return the chat completions endpoint URL."""
        return f"{self.url}/v1/chat/completions"

    def swap_status(self) -> dict | None:
        """Check if a model swap is in progress by reading the lock file."""
        if not self.is_swap_proxy:
            return None
        try:
            if not SWAP_LOCK.exists():
                return None

            content = SWAP_LOCK.read_text().strip()
            lines = content.split("\n")
            if not lines:
                return None

            timestamp = float(lines[0].strip())
            elapsed = time.time() - timestamp

            swap_from: str | None = None
            swap_to: str | None = None
            if len(lines) > 1 and "->" in lines[1]:
                parts = lines[1].split("->", 1)
                if len(parts) == 2:
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
