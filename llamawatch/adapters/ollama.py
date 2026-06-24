"""Ollama backend adapter for LlamaWatch."""

from .base import BackendAdapter

# Reuse HTTP helpers from llamacpp module (stdlib only, no extra deps)
from .llamacpp import _http_get, _http_get_json


class OllamaAdapter(BackendAdapter):
    """Adapter for Ollama API servers."""

    def health(self) -> str:
        """Check Ollama health via root endpoint."""
        body = _http_get(f"{self.url}/", timeout=3.0)
        if body is None:
            return "unreachable"
        return "healthy"

    def model_name(self) -> str:
        """Get the currently running model from /api/ps."""
        data = _http_get_json(f"{self.url}/api/ps", timeout=3.0)
        if data and "models" in data and len(data["models"]) > 0:
            return data["models"][0].get("name", "unknown")
        return "unknown"

    def is_generating(self) -> bool:
        """Ollama does not expose per-request generation status.

        Always returns False. The generating indicator will not work for
        Ollama backends — this is a known limitation of the Ollama API.
        """
        return False

    def models_available(self) -> list[dict]:
        """Get available models from /api/tags."""
        data = _http_get_json(f"{self.url}/api/tags", timeout=3.0)
        if data and "models" in data:
            return data["models"]
        return []

    def chat_completions_url(self) -> str:
        """Return the Ollama chat completions endpoint (OpenAI-compatible)."""
        return f"{self.url}/v1/chat/completions"
