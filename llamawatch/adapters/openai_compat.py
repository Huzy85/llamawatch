"""Generic OpenAI-compatible backend adapter for LlamaWatch."""

from .base import BackendAdapter
from .llamacpp import _http_get, _http_get_json


class OpenAICompatAdapter(BackendAdapter):
    """Adapter for any OpenAI-compatible API server."""

    def health(self) -> str:
        """Check health by querying /v1/models."""
        data = _http_get_json(f"{self.url}/v1/models", timeout=3.0)
        if data is None:
            return "unreachable"
        if "data" in data:
            return "healthy"
        return "error"

    def model_name(self) -> str:
        """Get the first model from /v1/models."""
        data = _http_get_json(f"{self.url}/v1/models", timeout=3.0)
        if data and "data" in data and len(data["data"]) > 0:
            return data["data"][0].get("id", "unknown")
        return "unknown"

    def is_generating(self) -> bool:
        """Generic OpenAI servers don't expose generation status."""
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
