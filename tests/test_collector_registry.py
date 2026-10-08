"""Tests for the auto-discovery collector registry."""

from llamawatch.collectors import CollectorRegistry


def test_registry_discovers_collectors():
    registry = CollectorRegistry()
    ids = registry.get_available_ids()
    assert len(ids) > 0
    assert "system" in ids


def test_registry_discovers_all_expected_collectors():
    registry = CollectorRegistry()
    ids = registry.get_available_ids()
    expected = {
        "system", "model-status", "services", "timers",
        "network", "weather", "email", "claude-code",
    }
    for wid in expected:
        assert wid in ids, f"Expected collector '{wid}' not discovered"


def test_registry_filters_enabled():
    registry = CollectorRegistry()
    enabled = registry.get_enabled(["system"])
    assert len(enabled) == 1
    assert enabled[0] == "system"


def test_registry_ignores_unknown_ids():
    registry = CollectorRegistry()
    enabled = registry.get_enabled(["nonexistent-widget"])
    assert len(enabled) == 0


def test_registry_manifest():
    registry = CollectorRegistry()
    manifest = registry.get_manifest()
    assert len(manifest) > 0
    for item in manifest:
        assert "id" in item
        assert "name" in item
        assert "defaultSize" in item
        assert "requires" in item


def test_registry_get_collector():
    registry = CollectorRegistry()
    mod = registry.get_collector("system")
    assert mod is not None
    assert mod.WIDGET_ID == "system"
    assert callable(mod.collect)


def test_registry_get_collector_missing():
    registry = CollectorRegistry()
    mod = registry.get_collector("does-not-exist")
    assert mod is None


def test_registry_preserves_enabled_order():
    registry = CollectorRegistry()
    order = ["network", "system", "email"]
    enabled = registry.get_enabled(order)
    assert enabled == order


def test_collector_modules_have_required_metadata():
    registry = CollectorRegistry()
    for wid in registry.get_available_ids():
        mod = registry.get_collector(wid)
        assert hasattr(mod, "WIDGET_ID")
        assert hasattr(mod, "WIDGET_NAME")
        assert hasattr(mod, "WIDGET_DEFAULT_SIZE")
        assert hasattr(mod, "WIDGET_REQUIRES")
        assert hasattr(mod, "collect")
        assert callable(mod.collect)
