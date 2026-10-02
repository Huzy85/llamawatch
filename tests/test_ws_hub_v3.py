"""Tests for the singleton WebSocket hub with push support."""

import asyncio


def test_hub_is_singleton():
    from llamawatch.ws_hub import get_hub, reset_hub
    reset_hub()
    config = {"widgets": {"enabled": []}, "backends": []}
    hub1 = get_hub(config)
    hub2 = get_hub(config)
    assert hub1 is hub2


def test_hub_tracks_connections():
    from llamawatch.ws_hub import get_hub, reset_hub
    reset_hub()
    config = {"widgets": {"enabled": []}, "backends": []}
    hub = get_hub(config)
    assert len(hub.connections) == 0


def test_push_log_line():
    from llamawatch.ws_hub import get_hub, reset_hub
    reset_hub()
    config = {"widgets": {"enabled": []}, "backends": []}
    hub = get_hub(config)
    hub.push_log({"source": "test", "level": "info", "message": "hello"})
    assert len(hub.log_buffer) == 1
    assert hub.log_buffer[0]["message"] == "hello"


def test_log_buffer_overflow():
    from llamawatch.ws_hub import get_hub, reset_hub
    reset_hub()
    config = {"widgets": {"enabled": []}, "backends": []}
    hub = get_hub(config)
    for i in range(600):
        hub.push_log({"message": f"line {i}"})
    assert len(hub.log_buffer) == 500
    assert hub.log_buffer[0]["message"] == "line 100"


def test_reset_hub_clears_singleton():
    from llamawatch.ws_hub import get_hub, reset_hub
    reset_hub()
    config = {"widgets": {"enabled": []}, "backends": []}
    hub1 = get_hub(config)
    reset_hub()
    hub2 = get_hub(config)
    assert hub1 is not hub2


def test_add_remove_connection():
    from llamawatch.ws_hub import get_hub, reset_hub
    reset_hub()
    config = {"widgets": {"enabled": []}, "backends": []}
    hub = get_hub(config)
    fake_ws = object()
    hub.add_connection(fake_ws)
    assert fake_ws in hub.connections
    assert len(hub.connections) == 1
    hub.remove_connection(fake_ws)
    assert fake_ws not in hub.connections
    assert len(hub.connections) == 0


def test_remove_connection_idempotent():
    from llamawatch.ws_hub import get_hub, reset_hub
    reset_hub()
    config = {"widgets": {"enabled": []}, "backends": []}
    hub = get_hub(config)
    fake_ws = object()
    hub.remove_connection(fake_ws)  # should not raise
    assert len(hub.connections) == 0


def test_log_queue_receives_pushed_lines():
    from llamawatch.ws_hub import get_hub, reset_hub
    reset_hub()
    config = {"widgets": {"enabled": []}, "backends": []}
    hub = get_hub(config)
    hub.push_log({"message": "queued"})
    assert not hub.log_queue.empty()
    item = hub.log_queue.get_nowait()
    assert item["message"] == "queued"
