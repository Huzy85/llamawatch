"""Tests for the network speed collector."""

import pytest

from llamawatch.collectors import network as net


@pytest.fixture(autouse=True)
def _reset_global_state():
    """Reset module-level cache so tests don't bleed into each other."""
    original_reading = net._prev_reading
    original_time = net._prev_time
    net._prev_reading = None
    net._prev_time = 0.0
    yield
    net._prev_reading = original_reading
    net._prev_time = original_time


# ── _read_net_dev ─────────────────────────────────────────────────────────────

def test_read_net_dev_skips_short_lines(tmp_path, monkeypatch):
    content = (
        "Inter-|   Receive                                                |  Transmit\n"
        " face |bytes packets errs drop...\n"
        "  eth0: 1000 2000\n"  # only 3 parts — should be skipped
        "  lo:   500 100 0 0 0 0 0 0 400 50 0 0 0 0 0 0\n"
    )
    monkeypatch.setattr("pathlib.Path.read_text", lambda self: content if "net/dev" in str(self) else open(self).read())
    # Exercise via the actual file path mock
    import unittest.mock as mock
    from pathlib import Path
    with mock.patch("pathlib.Path.read_text", return_value=content):
        result = net._read_net_dev()
    # eth0 line had <10 parts — only lo should be in result
    assert "eth0" not in result


def test_read_net_dev_exception_returns_empty(monkeypatch):
    import unittest.mock as mock
    from pathlib import Path
    with mock.patch("pathlib.Path.read_text", side_effect=OSError("no proc")):
        result = net._read_net_dev()
    assert result == {}


# ── _pick_primary ─────────────────────────────────────────────────────────────

def test_pick_primary_skips_lo():
    stats = {"lo": (999999, 999999)}
    assert net._pick_primary(stats) is None


def test_pick_primary_no_interfaces():
    assert net._pick_primary({}) is None


def test_pick_primary_picks_highest_traffic():
    stats = {
        "eth0": (100, 50),
        "eth1": (1000, 500),
        "wlan0": (10, 5),
    }
    assert net._pick_primary(stats) == "eth1"


def test_pick_primary_excludes_lo_with_others():
    stats = {
        "lo": (999999, 999999),
        "eth0": (100, 50),
    }
    assert net._pick_primary(stats) == "eth0"


def test_pick_primary_single_iface():
    stats = {"eth0": (1000, 500)}
    assert net._pick_primary(stats) == "eth0"


# ── collect_network ───────────────────────────────────────────────────────────

def test_collect_returns_zeros_when_no_interfaces(monkeypatch):
    monkeypatch.setattr(net, "_read_net_dev", lambda: {})
    result = net.collect_network()
    assert result == {"download_mbps": 0.0, "upload_mbps": 0.0}


def test_collect_returns_zeros_when_only_lo(monkeypatch):
    monkeypatch.setattr(net, "_read_net_dev", lambda: {"lo": (1000, 500)})
    result = net.collect_network()
    assert result == {"download_mbps": 0.0, "upload_mbps": 0.0}


def test_collect_uses_cached_reading_without_sleep(monkeypatch):
    """Second call should use prev_reading and not call time.sleep."""
    import time as time_mod
    calls = []

    def fake_read():
        calls.append(1)
        return {"eth0": (len(calls) * 1_000_000, 0)}

    monkeypatch.setattr(net, "_read_net_dev", fake_read)

    sleep_called = []
    monkeypatch.setattr(time_mod, "sleep", lambda s: sleep_called.append(s))

    # Seed the cache manually so the first call skips the sleep path
    net._prev_reading = {"eth0": (0, 0)}
    net._prev_time = time_mod.monotonic()

    result = net.collect_network()
    assert sleep_called == [], "sleep should not be called when cache is warm"
    assert "download_mbps" in result


def test_collect_counter_rollover_clamped_to_zero(monkeypatch):
    """rx_diff = max(0, ...) — counter rollover must not produce negative Mbps."""
    import time as time_mod

    net._prev_reading = {"eth0": (9_000_000, 0)}
    net._prev_time = time_mod.monotonic() - 1.0

    # Simulate counter reset: new value < old value
    monkeypatch.setattr(net, "_read_net_dev", lambda: {"eth0": (100, 0)})

    result = net.collect_network()
    assert result["download_mbps"] == 0.0


def test_collect_zero_elapsed_uses_fallback(monkeypatch):
    """elapsed <= 0 is replaced with 1.0 to prevent division by zero."""
    import time as time_mod

    net._prev_reading = {"eth0": (0, 0)}
    # Set prev_time to NOW so elapsed is ~0 when measured
    net._prev_time = time_mod.monotonic() + 10  # force elapsed < 0

    monkeypatch.setattr(net, "_read_net_dev", lambda: {"eth0": (8_000_000, 0)})

    result = net.collect_network()
    # With elapsed clamped to 1s, 8MB → 64 Mbps
    assert result["download_mbps"] == pytest.approx(64.0, rel=0.01)
