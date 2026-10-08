"""Sample-data mode: serve a fixed JSON snapshot instead of live collectors.

Used by the browser test suite (tests/ui) and for demos. When the env var
LLAMAWATCH_SAMPLE points at a snapshot file, the dashboard shows that data,
never runs collectors, refuses every write, and refuses every WebSocket.
Snapshot format: {"widgets": {<widget id>: data}, "rest": {<GET path>: json}}.
"""

import copy
import json
import os
import time
from pathlib import Path
from urllib.parse import parse_qs

ENV = "LLAMAWATCH_SAMPLE"

# GET paths answered by the real route (they only read the sample config).
_PASSTHROUGH = {"/api/settings", "/api/widgets", "/api/layout"}

_cache: dict | None = None
_loaded = False


def current() -> dict | None:
    """The loaded snapshot, or None when sample mode is off."""
    global _cache, _loaded
    if not _loaded:
        path = os.environ.get(ENV)
        _cache = json.loads(Path(path).read_text()) if path else None
        _loaded = True
    return _cache


def reset() -> None:
    global _cache, _loaded
    _cache, _loaded = None, False


class SampleRegistry:
    """Stands in for CollectorRegistry: returns copies of the snapshot widgets."""

    def __init__(self, widgets: dict):
        self._widgets = widgets
        self._enabled = None

    def collect_all(self, config=None, adapters=None) -> dict:
        return copy.deepcopy(self._widgets)

    def collect_one(self, widget_id, config=None, adapters=None) -> dict:
        return {widget_id: copy.deepcopy(self._widgets.get(widget_id, {}))}

    def get_manifest(self) -> list:
        return []


async def _send_json(send, status: int, obj) -> None:
    body = json.dumps(obj).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class SampleGuard:
    """ASGI middleware, inert unless sample mode is on."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        snap = current()
        if snap is None or scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if scope["type"] == "websocket":
            if path == "/ws" or path.startswith("/ws/"):
                await receive()  # websocket.connect
                await send({"type": "websocket.close", "code": 1008})
                return
            return await self.app(scope, receive, send)
        if path.startswith("/api/") or path.startswith("/auth/"):
            if scope.get("method", "GET") not in ("GET", "HEAD"):
                return await _send_json(send, 403, {"error": "read-only sample mode"})
            rest = snap.get("rest", {})
            if path in rest:
                body = rest[path]
                if path.endswith("/events") and isinstance(body, dict):
                    # a research run's log is polled with ?since=N: hand back only the newer lines
                    q = parse_qs(scope.get("query_string", b"").decode())
                    since = int(q.get("since", ["0"])[0]) if q.get("since", ["0"])[0].isdigit() else 0
                    evs = body.get("events", [])
                    # times are moved so the last line is a few seconds old, keeping the run clock sensible
                    shift = time.time() - 5 - max((e.get("t", 0) for e in evs), default=0)
                    body = {**body, "events": [{**e, "t": e["t"] + shift} if e.get("t") else e for e in evs[since:]]}
                return await _send_json(send, 200, body)
            if path not in _PASSTHROUGH and not path.startswith("/auth/"):
                return await _send_json(send, 404, {"error": "not in sample data"})
        return await self.app(scope, receive, send)
