"""Tests for collector registry v3 — config schemas & multi-instance support."""

from llamawatch.collectors import CollectorRegistry


def test_manifest_includes_config_schema():
    config = {"widgets": {"enabled": ["weather"], "config": {}}, "backends": []}
    registry = CollectorRegistry(config)
    manifest = registry.get_manifest()
    weather = [w for w in manifest if w["id"] == "weather"][0]
    assert "config_schema" in weather
    assert "icon" in weather
    assert "description" in weather
    assert weather.get("config_required") is True


def test_multi_instance_enabled():
    config = {
        "widgets": {
            "enabled": ["weather", "weather:abc123"],
            "config": {
                "weather": {"lat": 51.5, "lon": -0.12},
                "weather:abc123": {"lat": 40.7, "lon": -74.0},
            },
        },
        "backends": [],
    }
    registry = CollectorRegistry(config)
    enabled = registry.get_enabled()
    weather_instances = [e for e in enabled if e.startswith("weather")]
    assert len(weather_instances) == 2


def test_single_instance_no_duplicate():
    config = {"widgets": {"enabled": ["system", "system:xyz"], "config": {}}, "backends": []}
    registry = CollectorRegistry(config)
    enabled = registry.get_enabled()
    system_instances = [e for e in enabled if e.startswith("system")]
    assert len(system_instances) == 1


def test_manifest_has_icon_for_all_collectors():
    """Every collector with metadata should expose an icon."""
    registry = CollectorRegistry()
    manifest = registry.get_manifest()
    for item in manifest:
        assert "icon" in item, f"Collector {item['id']} missing icon"
        assert "description" in item, f"Collector {item['id']} missing description"
        assert "config_schema" in item, f"Collector {item['id']} missing config_schema"
        assert "multi_instance" in item, f"Collector {item['id']} missing multi_instance"


def test_manifest_weather_is_multi_instance():
    registry = CollectorRegistry()
    manifest = registry.get_manifest()
    weather = [w for w in manifest if w["id"] == "weather"][0]
    assert weather["multi_instance"] is True


def test_manifest_system_is_single_instance():
    registry = CollectorRegistry()
    manifest = registry.get_manifest()
    system = [w for w in manifest if w["id"] == "system"][0]
    assert system["multi_instance"] is False


def test_email_credentials_required():
    registry = CollectorRegistry()
    manifest = registry.get_manifest()
    email_item = [w for w in manifest if w["id"] == "email"][0]
    assert email_item.get("credentials_required") is True


def test_get_enabled_returns_instance_ids_for_multi():
    """get_enabled() should return instance IDs (e.g. 'weather:abc123'), not modules."""
    config = {
        "widgets": {
            "enabled": ["weather", "weather:abc123"],
            "config": {
                "weather": {"lat": 51.5, "lon": -0.12},
                "weather:abc123": {"lat": 40.7, "lon": -74.0},
            },
        },
        "backends": [],
    }
    registry = CollectorRegistry(config)
    enabled = registry.get_enabled()
    assert "weather" in enabled
    assert "weather:abc123" in enabled


def test_get_enabled_strips_invalid_single_instance_duplicates():
    """Single-instance widgets should only appear once even if config has duplicates."""
    config = {
        "widgets": {
            "enabled": ["system", "system:dup1", "network"],
            "config": {},
        },
        "backends": [],
    }
    registry = CollectorRegistry(config)
    enabled = registry.get_enabled()
    assert "system" in enabled
    assert "system:dup1" not in enabled
    assert "network" in enabled
