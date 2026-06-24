"""WebSocket hub for llamawatch — collects data and pushes diffs to clients."""

import asyncio
import collections
import json
import queue as _queue_mod

from fastapi import WebSocket, WebSocketDisconnect

# ── Module-level singleton ──────────────────────────────────────────
_hub_instance: "DashboardHub | None" = None


def get_hub(config: dict, adapters=None) -> "DashboardHub":
    """Return the shared DashboardHub singleton, creating it on first call."""
    global _hub_instance
    if _hub_instance is None:
        _hub_instance = DashboardHub(config=config, adapters=adapters)
    return _hub_instance


def reset_hub() -> None:
    """Destroy the singleton — used by tests."""
    global _hub_instance
    _hub_instance = None


class DashboardHub:
    """Singleton hub that polls collectors and streams diffs over WebSocket."""

    def __init__(self, registry=None, *, config: dict | None = None, adapters=None):
        self._last_state: dict = {}
        self._connections: set[WebSocket] = set()
        self._registry = registry
        self._config = config
        self._adapters = adapters

        # Push-based log support
        self.connections: set = set()
        self.log_buffer: collections.deque = collections.deque(maxlen=500)
        self.log_queue: _queue_mod.Queue = _queue_mod.Queue()

    # ── Connection management ─────────────────────────────────────
    def add_connection(self, ws) -> None:
        """Register an active WebSocket connection."""
        self.connections.add(ws)
        self._connections.add(ws)

    def remove_connection(self, ws) -> None:
        """Unregister a WebSocket connection."""
        self.connections.discard(ws)
        self._connections.discard(ws)

    # ── Log push support ────────────────────────────────────────
    def push_log(self, line: dict) -> None:
        """Add a log line to the ring buffer and the push queue."""
        self.log_buffer.append(line)
        self.log_queue.put_nowait(line)

    def collect_all(self, config=None, adapters=None, enabled_ids=None) -> dict:
        """Run enabled collectors and return a dict keyed by instance ID.

        Delegates to the registry's collect_all() which handles
        multi-instance widgets.  On failure the last known value is returned.
        """
        if self._registry is None:
            return {}

        # If explicit enabled_ids provided, temporarily update the registry
        if enabled_ids is not None:
            saved = self._registry._enabled
            self._registry._enabled = list(enabled_ids)
            try:
                result = self._registry.collect_all(config, adapters)
            finally:
                self._registry._enabled = saved
        else:
            result = self._registry.collect_all(config, adapters)

        # Fill in fallbacks for any failed collectors
        for key in list(result.keys()):
            if result[key] == {}:
                fallback = self._last_state.get(key)
                if fallback is not None:
                    result[key] = fallback

        return result

    @staticmethod
    def compute_diff(old: dict, new: dict) -> dict:
        """Return only the keys whose values changed between *old* and *new*.

        Nested structures are compared via their JSON serialisation so that
        ordering differences in dicts don't produce false positives.
        """
        diff = {}
        for key, value in new.items():
            old_value = old.get(key)
            try:
                old_json = json.dumps(old_value, sort_keys=True)
                new_json = json.dumps(value, sort_keys=True)
                if old_json != new_json:
                    diff[key] = value
            except (TypeError, ValueError):
                # If serialisation fails, assume changed
                diff[key] = value

        return diff

    async def run_poll_loop(self, websocket: WebSocket, config=None, adapters=None, enabled_ids=None):
        """Accept a WebSocket, send the full state, then stream diffs."""
        self.add_connection(websocket)

        def _collect():
            return self.collect_all(config, adapters, enabled_ids)

        try:
            # Send full snapshot on connect
            loop = asyncio.get_running_loop()
            state = await loop.run_in_executor(None, _collect)
            self._last_state = state
            await websocket.send_json({"type": "full", "data": state})

            # Poll and push diffs
            while True:
                # Poll faster during model swap (2s) vs normal (5s)
                is_swapping = (self._last_state.get("model-status", {}).get("status") == "swapping")
                await asyncio.sleep(2 if is_swapping else 5)

                new_state = await loop.run_in_executor(None, _collect)
                diff = self.compute_diff(self._last_state, new_state)

                if diff:
                    for widget_name, widget_data in diff.items():
                        await websocket.send_json({
                            "type": widget_name,
                            "data": widget_data,
                        })

                self._last_state = new_state

        except WebSocketDisconnect:
            self.remove_connection(websocket)
        except Exception:
            self.remove_connection(websocket)
            raise
