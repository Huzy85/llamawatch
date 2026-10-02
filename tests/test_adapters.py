"""Tests for the backend adapter layer."""

import time
import textwrap
from pathlib import Path
from unittest.mock import patch

from llamawatch.adapters import AdapterRegistry
from llamawatch.adapters.base import BackendAdapter
from llamawatch.adapters.llamacpp import LlamaCppAdapter, SWAP_LOCK
from llamawatch.adapters.ollama import OllamaAdapter
from llamawatch.adapters.openai_compat import OpenAICompatAdapter


def test_adapter_interface():
    """LlamaCppAdapter has all required methods."""
    required = [
        "health",
        "model_name",
        "model_friendly_name",
        "is_generating",
        "models_available",
        "chat_completions_url",
        "swap_status",
    ]
    for method in required:
        assert hasattr(LlamaCppAdapter, method), f"Missing method: {method}"


def test_all_adapters_are_subclasses():
    """All adapters inherit from BackendAdapter."""
    for cls in (LlamaCppAdapter, OllamaAdapter, OpenAICompatAdapter):
        assert issubclass(cls, BackendAdapter)


def test_llamacpp_unreachable():
    """Returns 'unreachable' when server is down."""
    adapter = LlamaCppAdapter(url="http://localhost:9999", config={})
    assert adapter.health() == "unreachable"


def test_llamacpp_model_name_fallback():
    """Returns 'unknown' when unreachable."""
    adapter = LlamaCppAdapter(url="http://localhost:9999", config={})
    assert adapter.model_name() == "unknown"


def test_llamacpp_is_generating_fallback():
    """Returns False when unreachable."""
    adapter = LlamaCppAdapter(url="http://localhost:9999", config={"direct_port": 9998})
    assert adapter.is_generating() is False


def test_llamacpp_chat_completions_url():
    """Returns correct chat completions URL."""
    adapter = LlamaCppAdapter(url="http://localhost:8081", config={})
    assert adapter.chat_completions_url() == "http://localhost:8081/v1/chat/completions"


def test_llamacpp_url_trailing_slash():
    """Trailing slash is stripped from URL."""
    adapter = LlamaCppAdapter(url="http://localhost:8081/", config={})
    assert adapter.url == "http://localhost:8081"
    assert adapter.chat_completions_url() == "http://localhost:8081/v1/chat/completions"


def test_llamacpp_swap_status_no_proxy():
    """swap_status returns None when not a swap proxy."""
    adapter = LlamaCppAdapter(url="http://localhost:8081", config={"swap_proxy": False})
    assert adapter.swap_status() is None


def test_llamacpp_swap_status_no_lock_file():
    """swap_status returns None when no lock file exists."""
    adapter = LlamaCppAdapter(url="http://localhost:8081", config={"swap_proxy": True})
    # Ensure lock file doesn't exist
    if SWAP_LOCK.exists():
        original = SWAP_LOCK.read_text()
        SWAP_LOCK.unlink()
        try:
            assert adapter.swap_status() is None
        finally:
            SWAP_LOCK.write_text(original)
    else:
        assert adapter.swap_status() is None


def test_llamacpp_swap_status_with_lock_file(tmp_path):
    """swap_status correctly parses lock file."""
    lock_file = tmp_path / "model_swap_in_progress"
    now = time.time()
    lock_file.write_text(f"{now}\nOldModel -> NewModel\n")

    with patch("llamawatch.adapters.llamacpp.SWAP_LOCK", lock_file):
        adapter = LlamaCppAdapter(url="http://localhost:8081", config={"swap_proxy": True})
        status = adapter.swap_status()

    assert status is not None
    assert status["swap_from"] == "OldModel"
    assert status["swap_to"] == "NewModel"
    assert status["swap_step"] == 1
    assert status["swap_step_desc"] == "Unloading old model"
    assert status["swap_elapsed"] >= 0


def test_llamacpp_swap_status_step_progression(tmp_path):
    """swap_status maps elapsed time to correct steps."""
    lock_file = tmp_path / "model_swap_in_progress"

    test_cases = [
        (2, 1, "Unloading old model"),
        (10, 2, "Waiting for unload"),
        (20, 3, "GTT memory release (10s)"),
        (30, 4, "Flushing RAM cache"),
        (40, 5, "Warming up subsystems"),
        (60, 6, "Loading new model"),
    ]

    for elapsed, expected_step, expected_desc in test_cases:
        timestamp = time.time() - elapsed
        lock_file.write_text(f"{timestamp}\nA -> B\n")

        with patch("llamawatch.adapters.llamacpp.SWAP_LOCK", lock_file):
            adapter = LlamaCppAdapter(url="http://localhost:8081", config={"swap_proxy": True})
            status = adapter.swap_status()

        assert status is not None, f"Expected status for elapsed={elapsed}"
        assert status["swap_step"] == expected_step, (
            f"elapsed={elapsed}: expected step {expected_step}, got {status['swap_step']}"
        )
        assert status["swap_step_desc"] == expected_desc


def test_friendly_name_lookup():
    """model_friendly_name uses model_names mapping."""
    config = {"model_names": {"Qwen3.5-122B-Writer": "Homer"}}
    adapter = LlamaCppAdapter(url="http://localhost:9999", config=config)
    # When unreachable, model_name returns "unknown"
    assert adapter.model_friendly_name() == "unknown"

    # Patch model_name to return a known model
    with patch.object(adapter, "model_name", return_value="Qwen3.5-122B-Writer"):
        assert adapter.model_friendly_name() == "Homer"

    # Unknown model returns the ID unchanged
    with patch.object(adapter, "model_name", return_value="some-other-model"):
        assert adapter.model_friendly_name() == "some-other-model"


def test_ollama_unreachable():
    """Ollama returns 'unreachable' when server is down."""
    adapter = OllamaAdapter(url="http://localhost:9999", config={})
    assert adapter.health() == "unreachable"


def test_ollama_chat_completions_url():
    """Ollama uses OpenAI-compatible endpoint."""
    adapter = OllamaAdapter(url="http://localhost:11434", config={})
    assert adapter.chat_completions_url() == "http://localhost:11434/v1/chat/completions"


def test_openai_compat_unreachable():
    """OpenAI-compat returns 'unreachable' when server is down."""
    adapter = OpenAICompatAdapter(url="http://localhost:9999", config={})
    assert adapter.health() == "unreachable"


def test_openai_compat_is_generating_always_false():
    """Generic OpenAI servers can't report generation status."""
    adapter = OpenAICompatAdapter(url="http://localhost:9999", config={})
    assert adapter.is_generating() is False


def test_registry_creates_adapters():
    """Registry creates adapters from config backends list."""
    config = {
        "backends": [
            {"type": "llamacpp", "url": "http://localhost:8081", "name": "main"},
            {"type": "ollama", "url": "http://localhost:11434", "name": "ollama"},
            {"type": "openai", "url": "http://localhost:5000", "name": "vllm"},
        ]
    }
    registry = AdapterRegistry(config)
    adapters = registry.get_all()
    assert len(adapters) == 3
    assert isinstance(adapters[0], LlamaCppAdapter)
    assert isinstance(adapters[1], OllamaAdapter)
    assert isinstance(adapters[2], OpenAICompatAdapter)


def test_registry_get_primary():
    """get_primary returns the first adapter."""
    config = {
        "backends": [
            {"type": "llamacpp", "url": "http://localhost:8081", "name": "main"},
            {"type": "ollama", "url": "http://localhost:11434", "name": "ollama"},
        ]
    }
    registry = AdapterRegistry(config)
    primary = registry.get_primary()
    assert isinstance(primary, LlamaCppAdapter)


def test_registry_empty_backends():
    """Registry handles empty backends list gracefully."""
    registry = AdapterRegistry({"backends": []})
    assert registry.get_primary() is None
    assert registry.get_all() == []


def test_registry_no_backends_key():
    """Registry handles missing backends key."""
    registry = AdapterRegistry({})
    assert registry.get_primary() is None
    assert registry.get_all() == []


def test_registry_get_by_name():
    """Registry returns adapter by name."""
    config = {
        "backends": [
            {"type": "llamacpp", "url": "http://localhost:8081", "name": "main"},
            {"type": "ollama", "url": "http://localhost:11434", "name": "ollama"},
        ]
    }
    registry = AdapterRegistry(config)
    assert isinstance(registry.get_by_name("main"), LlamaCppAdapter)
    assert isinstance(registry.get_by_name("ollama"), OllamaAdapter)
    assert registry.get_by_name("nonexistent") is None


def test_registry_unknown_type_falls_back():
    """Unknown backend type falls back to OpenAI-compatible."""
    config = {
        "backends": [
            {"type": "vllm", "url": "http://localhost:5000", "name": "vllm"},
        ]
    }
    registry = AdapterRegistry(config)
    assert isinstance(registry.get_primary(), OpenAICompatAdapter)


def test_registry_passes_model_names():
    """Registry merges top-level model_names into backend config."""
    config = {
        "model_names": {"model-x": "Friendly X"},
        "backends": [
            {"type": "llamacpp", "url": "http://localhost:8081", "name": "main"},
        ],
    }
    registry = AdapterRegistry(config)
    adapter = registry.get_primary()
    assert adapter._model_names == {"model-x": "Friendly X"}
