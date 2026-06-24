"""Backend adapter registry for LlamaWatch.

Provides AdapterRegistry factory that creates the right adapter
based on backend type configuration.
"""

from .base import BackendAdapter
from .llamacpp import LlamaCppAdapter
from .ollama import OllamaAdapter
from .openai_compat import OpenAICompatAdapter

_ADAPTER_TYPES: dict[str, type[BackendAdapter]] = {
    "llamacpp": LlamaCppAdapter,
    "ollama": OllamaAdapter,
    "openai": OpenAICompatAdapter,
}


def create_adapter(backend_config: dict, top_config: dict | None = None) -> BackendAdapter:
    """Module-level factory: create a single adapter from a backend config dict.

    Parameters
    ----------
    backend_config:
        A single backend entry (type, url, name, …).
    top_config:
        Optional top-level config used to inherit ``model_names``.

    Returns
    -------
    BackendAdapter
        Concrete adapter instance (LlamaCppAdapter, OllamaAdapter, …).
    """
    top_config = top_config or {}
    backend_type = backend_config.get("type", "openai")
    url = backend_config.get("url", "")

    adapter_cls = _ADAPTER_TYPES.get(backend_type, OpenAICompatAdapter)

    merged_config = {**backend_config}
    if "model_names" not in merged_config and "model_names" in top_config:
        merged_config["model_names"] = top_config["model_names"]

    return adapter_cls(url=url, config=merged_config)


class AdapterRegistry:
    """Factory that creates and manages backend adapters from config."""

    def __init__(self, config: dict) -> None:
        self._adapters: list[BackendAdapter] = []
        self._by_name: dict[str, BackendAdapter] = {}
        self._top_config = config
        self._load(config)

    # ------------------------------------------------------------------
    # Public dict-style view (name → adapter) for hot-reload consumers
    # ------------------------------------------------------------------

    @property
    def adapters(self) -> dict[str, BackendAdapter]:
        """Dict mapping backend name → adapter (live view, not a copy)."""
        return self._by_name

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self, config: dict) -> None:
        """Populate internal lists from *config* without clearing first."""
        backends = config.get("backends", [])
        for backend_cfg in backends:
            name = backend_cfg.get("name", backend_cfg.get("url", ""))
            adapter = create_adapter(backend_cfg, top_config=config)
            self._adapters.append(adapter)
            self._by_name[name] = adapter

    # ------------------------------------------------------------------
    # Hot-reload
    # ------------------------------------------------------------------

    def rebuild(self, config: dict) -> None:
        """Replace all adapters in-place from a new config dict.

        Existing adapter objects are discarded and recreated from *config*.
        All references to ``registry.adapters`` (the dict) will see the
        updated entries because we mutate the same dict object.
        """
        self._adapters.clear()
        self._by_name.clear()
        self._top_config = config
        self._load(config)

    # ------------------------------------------------------------------
    # Public accessors (unchanged API)
    # ------------------------------------------------------------------

    def get_primary(self) -> BackendAdapter | None:
        """Return the first adapter, or None if no backends configured."""
        return self._adapters[0] if self._adapters else None

    def get_all(self) -> list[BackendAdapter]:
        """Return all adapters."""
        return list(self._adapters)

    def get_by_name(self, name: str) -> BackendAdapter | None:
        """Return adapter matching the given name, or None."""
        return self._by_name.get(name)
