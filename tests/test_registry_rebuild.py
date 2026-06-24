"""Tests for AdapterRegistry.rebuild() and CollectorRegistry.refresh_enabled()."""


def test_adapter_registry_rebuild():
    from llamawatch.adapters import AdapterRegistry

    config1 = {"backends": [{"type": "llamacpp", "url": "http://localhost:8081", "name": "A"}]}
    registry = AdapterRegistry(config1)
    assert len(registry.adapters) == 1

    config2 = {
        "backends": [
            {"type": "llamacpp", "url": "http://localhost:8081", "name": "A"},
            {"type": "ollama", "url": "http://localhost:11434", "name": "B"},
        ]
    }
    registry.rebuild(config2)
    assert len(registry.adapters) == 2


def test_adapter_registry_rebuild_replaces_all():
    """rebuild() replaces all adapters, not just appends."""
    from llamawatch.adapters import AdapterRegistry

    config1 = {
        "backends": [
            {"type": "llamacpp", "url": "http://localhost:8081", "name": "A"},
            {"type": "ollama", "url": "http://localhost:11434", "name": "B"},
        ]
    }
    registry = AdapterRegistry(config1)
    assert len(registry.adapters) == 2

    config2 = {"backends": [{"type": "llamacpp", "url": "http://localhost:8082", "name": "C"}]}
    registry.rebuild(config2)
    assert len(registry.adapters) == 1
    assert "C" in registry.adapters
    assert "A" not in registry.adapters


def test_adapter_registry_rebuild_empty():
    """rebuild() with no backends clears all adapters."""
    from llamawatch.adapters import AdapterRegistry

    config1 = {"backends": [{"type": "llamacpp", "url": "http://localhost:8081", "name": "A"}]}
    registry = AdapterRegistry(config1)
    registry.rebuild({"backends": []})
    assert len(registry.adapters) == 0


def test_create_adapter_factory():
    """Module-level create_adapter() creates a temporary adapter without a registry."""
    from llamawatch.adapters import create_adapter
    from llamawatch.adapters.llamacpp import LlamaCppAdapter
    from llamawatch.adapters.ollama import OllamaAdapter

    adapter = create_adapter({"type": "llamacpp", "url": "http://localhost:8081", "name": "X"})
    assert isinstance(adapter, LlamaCppAdapter)

    adapter2 = create_adapter({"type": "ollama", "url": "http://localhost:11434", "name": "Y"})
    assert isinstance(adapter2, OllamaAdapter)


def test_create_adapter_unknown_type_fallback():
    """create_adapter() falls back to OpenAI-compat for unknown types."""
    from llamawatch.adapters import create_adapter
    from llamawatch.adapters.openai_compat import OpenAICompatAdapter

    adapter = create_adapter({"type": "vllm", "url": "http://localhost:5000", "name": "Z"})
    assert isinstance(adapter, OpenAICompatAdapter)


def test_collector_refresh_enabled():
    from llamawatch.collectors import CollectorRegistry

    config1 = {"widgets": {"enabled": ["system"], "config": {}}, "backends": []}
    registry = CollectorRegistry(config1)
    enabled = registry.get_enabled()
    assert "system" in enabled

    config2 = {"widgets": {"enabled": ["system", "network"], "config": {}}, "backends": []}
    registry.refresh_enabled(config2)
    enabled = registry.get_enabled()
    assert "network" in enabled


def test_collector_refresh_enabled_removes_old():
    """refresh_enabled() removes widgets no longer in enabled list."""
    from llamawatch.collectors import CollectorRegistry

    config1 = {"widgets": {"enabled": ["system", "network"], "config": {}}, "backends": []}
    registry = CollectorRegistry(config1)

    config2 = {"widgets": {"enabled": ["system"], "config": {}}, "backends": []}
    registry.refresh_enabled(config2)
    enabled = registry.get_enabled()
    assert "system" in enabled
    assert "network" not in enabled


def test_collector_registry_config_optional():
    """CollectorRegistry still works with no config argument (backward compat)."""
    from llamawatch.collectors import CollectorRegistry

    registry = CollectorRegistry()
    ids = registry.get_available_ids()
    assert len(ids) > 0


def test_collector_get_enabled_with_arg_still_works():
    """get_enabled(ids) still works for backward compatibility."""
    from llamawatch.collectors import CollectorRegistry

    registry = CollectorRegistry()
    enabled = registry.get_enabled(["system"])
    assert len(enabled) == 1
    assert enabled[0] == "system"
