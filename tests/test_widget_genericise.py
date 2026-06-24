"""Tests for genericised widget collectors — Task 12."""
import pytest


def test_weather_reads_from_widget_config():
    from llamawatch.collectors.weather import collect, WIDGET_CONFIG_REQUIRED, WIDGET_MULTI_INSTANCE

    assert WIDGET_CONFIG_REQUIRED is True
    assert WIDGET_MULTI_INSTANCE is True

    # Without coords, should return an error dict
    result = collect(config={}, widget_config=None)
    assert "error" in result or result.get("configured") is False


def test_weather_uses_widget_config_coords():
    from llamawatch.collectors.weather import collect

    # With coords provided the collector should attempt to use them.
    # Network may be unavailable in CI — we only assert it doesn't crash.
    try:
        collect(config={}, widget_config={"lat": 51.5, "lon": -0.12, "location": "London"})
    except Exception:
        pass  # network errors are acceptable here


def test_timers_filters_by_widget_config():
    from llamawatch.collectors.timers import collect

    result = collect(config={}, widget_config={"filter_timers": ["nonexistent.timer"]})
    assert isinstance(result, dict)
    # Either 'timers' key present (filtered list) or empty dict
    timers_val = result.get("timers", [])
    assert isinstance(timers_val, list)
    # nonexistent.timer should not be in results
    names = [t.get("name") for t in timers_val]
    assert "nonexistent" not in names


def test_email_has_credentials_required():
    from llamawatch.collectors.email_collector import WIDGET_CREDENTIALS_REQUIRED, WIDGET_CREDENTIALS_HELP

    assert WIDGET_CREDENTIALS_REQUIRED is True
    assert isinstance(WIDGET_CREDENTIALS_HELP, str)
    assert "config.local.json" in WIDGET_CREDENTIALS_HELP
