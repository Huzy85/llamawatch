"""Backend adapter base class for LlamaWatch."""

from abc import ABC, abstractmethod
from typing import AsyncGenerator


class BackendAdapter(ABC):
    """Abstract base for all LLM backend adapters."""

    def __init__(self, url: str, config: dict) -> None:
        self.url: str = url.rstrip("/")
        self.config: dict = config
        self._model_names: dict[str, str] = config.get("model_names", {})
        self.timeout: float = config.get("timeout", 3.0)

    @abstractmethod
    def health(self) -> str:
        """Return 'healthy', 'unreachable', or 'error'."""

    @abstractmethod
    def model_name(self) -> str:
        """Return the currently loaded model ID."""

    @abstractmethod
    def is_generating(self) -> bool:
        """Return True if active inference is happening."""

    @abstractmethod
    def models_available(self) -> list[dict]:
        """Return list of available models."""

    @abstractmethod
    def chat_completions_url(self) -> str:
        """Return the chat completions endpoint URL."""

    def model_friendly_name(self) -> str:
        """Map model ID to user-configured display name."""
        name = self.model_name()
        return self._model_names.get(name, name)

    def swap_status(self) -> dict | None:
        """Return swap status info, or None if not swapping. Override in subclasses."""
        return None
