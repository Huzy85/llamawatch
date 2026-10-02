"""Logs viewer collector — push-based streaming log aggregator."""

import collections
import json
import re
import threading

WIDGET_ID = "logs"
WIDGET_NAME = "Logs Viewer"
WIDGET_ICON = "\U0001f4dc"
WIDGET_DESCRIPTION = "Aggregated log viewer from multiple sources"
WIDGET_DEFAULT_SIZE = {"w": 6, "h": 3, "minW": 4, "minH": 2}
WIDGET_REQUIRES = []
WIDGET_CONFIG_SCHEMA = [
    {
        "key": "sources",
        "label": "Log sources",
        "type": "source-list",
        "description": "Add journalctl units, Docker containers, or file paths",
        "item_schema": [
            {
                "key": "type",
                "label": "Source type",
                "type": "select",
                "options": ["journalctl", "docker", "file"],
            },
            {"key": "target", "label": "Unit / container / path", "type": "text"},
            {"key": "label", "label": "Display name", "type": "text"},
        ],
    }
]
WIDGET_CONFIG_REQUIRED = True
WIDGET_MULTI_INSTANCE = True

# ── Per-instance ring buffers ─────────────────────────────────────────────────
# Keyed by instance_id (str) or a fallback key derived from widget_config.
_instance_buffers: dict[str, "LogRingBuffer"] = {}
_buffers_lock = threading.Lock()


# ── Ring buffer ───────────────────────────────────────────────────────────────

class LogRingBuffer:
    """Thread-safe ring buffer that stores the latest *max_size* log lines."""

    def __init__(self, max_size: int = 500):
        self.lines: collections.deque = collections.deque(maxlen=max_size)
        self._lock = threading.Lock()

    def add(self, line: dict) -> None:
        with self._lock:
            self.lines.append(line)

    def get_all(self) -> list[dict]:
        with self._lock:
            return list(self.lines)


# ── Parsing helpers ───────────────────────────────────────────────────────────

_LEVEL_RE = re.compile(
    r"\b(ERROR|CRITICAL|FATAL|WARNING|WARN|DEBUG|TRACE)\b", re.IGNORECASE
)

_PRIORITY_MAP = {
    "0": "error",   # emerg
    "1": "error",   # alert
    "2": "error",   # crit
    "3": "error",
    "4": "warn",
    "5": "info",    # notice
    "6": "info",
    "7": "debug",
}


def parse_log_level(line: str) -> str:
    """Detect log level from a plain-text log line.

    Returns one of: "error", "warn", "info", "debug".
    """
    m = _LEVEL_RE.search(line)
    if not m:
        return "info"
    token = m.group(1).upper()
    if token in ("ERROR", "CRITICAL", "FATAL"):
        return "error"
    if token in ("WARNING", "WARN"):
        return "warn"
    if token in ("DEBUG", "TRACE"):
        return "debug"
    return "info"


def parse_journalctl_json(line: str) -> dict | None:
    """Parse a single JSON line emitted by ``journalctl -o json``.

    Returns a normalised log dict with keys:
        message, level, source, timestamp
    Returns None on parse failure.
    """
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None

    message = obj.get("MESSAGE", "")
    if isinstance(message, list):
        # journalctl sometimes encodes binary messages as a list of ints
        try:
            message = bytes(message).decode("utf-8", errors="replace")
        except (TypeError, ValueError):
            message = str(message)

    priority = str(obj.get("PRIORITY", "6"))
    level = _PRIORITY_MAP.get(priority, "info")

    source = obj.get("_SYSTEMD_UNIT", "") or obj.get("SYSLOG_IDENTIFIER", "")

    raw_ts = obj.get("__REALTIME_TIMESTAMP")
    timestamp = None
    if raw_ts:
        try:
            # Journalctl timestamps are in microseconds since epoch
            from datetime import datetime, timezone
            ts_sec = int(raw_ts) / 1_000_000
            timestamp = datetime.fromtimestamp(ts_sec, tz=timezone.utc).isoformat()
        except (ValueError, OSError):
            timestamp = str(raw_ts)

    return {
        "message": message,
        "level": level,
        "source": source,
        "timestamp": timestamp,
    }


# ── Buffer helpers ────────────────────────────────────────────────────────────

def _instance_key(widget_config: dict | None) -> str:
    """Derive a stable string key from widget_config for buffer lookup."""
    if not widget_config:
        return "__default__"
    instance_id = widget_config.get("instance_id") or widget_config.get("id")
    if instance_id:
        return str(instance_id)
    # Fall back to a hash of the sources list
    sources = widget_config.get("sources", [])
    return str(hash(json.dumps(sources, sort_keys=True)))


def get_or_create_buffer(key: str, max_size: int = 500) -> LogRingBuffer:
    """Return the ring buffer for *key*, creating it if needed."""
    with _buffers_lock:
        if key not in _instance_buffers:
            _instance_buffers[key] = LogRingBuffer(max_size=max_size)
        return _instance_buffers[key]


def _get_buffer_for_instance(widget_config: dict | None) -> list[dict]:
    """Return the current buffer contents for this widget instance."""
    key = _instance_key(widget_config)
    buf = get_or_create_buffer(key)
    return buf.get_all()


# ── Collector entry point ─────────────────────────────────────────────────────

def collect(config=None, adapters=None, widget_config=None) -> dict:
    """Return the current log buffer for this widget instance.

    Actual log tailing is started separately (push-based, via ws_hub).
    collect() here just returns whatever lines are already buffered so the
    standard poll loop can include them in the initial payload.
    """
    sources = []
    if widget_config:
        sources = widget_config.get("sources") or []

    lines = _get_buffer_for_instance(widget_config)
    return {"lines": lines, "source_count": len(sources)}
