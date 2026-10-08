import asyncio
import json
import pytest
from llamawatch import sse


class FakeRegistry:
    """Minimal registry stub that exposes collect_one without touching shared state."""

    def collect_one(self, widget_id, config=None, adapters=None):
        return {widget_id: {"value": widget_id}}


class FakeHub:
    def __init__(self, slow_ids=None):
        self.calls = 0
        self._registry = FakeRegistry()
        self._slow_ids = slow_ids or set()

    def collect_all(self, config, adapters, enabled_ids=None):
        self.calls += 1
        if enabled_ids is not None:
            return {w: {"down": self.calls} for w in enabled_ids}
        return {"network": {"down": self.calls}}

    @staticmethod
    def compute_diff(old, new):
        return {k: v for k, v in new.items() if old.get(k) != v}


class SlowRegistry:
    """Registry where one widget is slow, another is fast."""

    def collect_one(self, widget_id, config=None, adapters=None):
        import time
        if widget_id == "slow":
            time.sleep(0.3)
        return {widget_id: {"id": widget_id}}


class SlowFakeHub(FakeHub):
    def __init__(self):
        super().__init__()
        self._registry = SlowRegistry()

    def collect_all(self, config, adapters, enabled_ids=None):
        self.calls += 1
        ids = enabled_ids or ["fast", "slow"]
        return {w: {"id": w, "call": self.calls} for w in ids}


async def test_event_stream_progressive_widget_first():
    """With enabled_ids set, first event should be a widget event (not full)."""
    hub = FakeHub()
    gen = sse.event_stream(hub, config={}, adapters=None, enabled_ids=["network"], interval=0.01, max_iterations=1)
    events = [e async for e in gen]
    # First event is a widget event for network
    assert events[0]["event"] == "widget"
    first_data = json.loads(events[0]["data"])
    assert first_data["id"] == "network"
    # Subsequent events are also widget events (diff loop)
    assert any(e["event"] == "widget" for e in events[1:])


async def test_event_stream_full_when_no_ids():
    """Without enabled_ids, first event is the legacy full event."""
    hub = FakeHub()
    gen = sse.event_stream(hub, config={}, adapters=None, enabled_ids=None, interval=0.01, max_iterations=2)
    events = [e async for e in gen]
    assert events[0]["event"] == "full"
    assert any(e["event"] == "widget" for e in events[1:])


async def test_event_stream_concurrent_all_ids_emitted():
    """All widget ids are emitted even when one collector is slow."""
    hub = SlowFakeHub()
    gen = sse.event_stream(
        hub, config={}, adapters=None,
        enabled_ids=["fast", "slow"],
        interval=0.01, max_iterations=0,
    )
    events = [e async for e in gen]
    widget_events = [e for e in events if e["event"] == "widget"]
    emitted_ids = {json.loads(e["data"])["id"] for e in widget_events}
    # Both widgets must have been emitted regardless of order
    assert "fast" in emitted_ids
    assert "slow" in emitted_ids


async def test_event_stream_concurrent_order_independent():
    """Emission order is not guaranteed — assert the SET of ids, not their sequence."""
    hub = SlowFakeHub()
    gen = sse.event_stream(
        hub, config={}, adapters=None,
        enabled_ids=["fast", "slow"],
        interval=0.01, max_iterations=0,
    )
    events = [e async for e in gen]
    widget_ids = {
        json.loads(e["data"])["id"]
        for e in events
        if e["event"] == "widget"
    }
    assert widget_ids == {"fast", "slow"}


async def test_collect_one_no_registry_mutation():
    """collect_one must not touch _enabled on the registry."""
    from llamawatch.collectors import CollectorRegistry

    class _Mod:
        WIDGET_ID = "dummy"
        WIDGET_MULTI_INSTANCE = False

        @staticmethod
        def collect(cfg, adapters, widget_config=None):
            return {"ok": True}

    reg = CollectorRegistry.__new__(CollectorRegistry)
    reg._collectors = {"dummy": _Mod}
    reg._enabled = ["original"]
    reg._widget_configs = {}
    reg._config = {}

    result = reg.collect_one("dummy")
    assert result == {"dummy": {"ok": True}}
    # _enabled must be completely untouched
    assert reg._enabled == ["original"]
