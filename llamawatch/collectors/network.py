"""Network speed collector for llamawatch."""

WIDGET_ID = "network"
WIDGET_NAME = "Network"
WIDGET_DEFAULT_SIZE = {"w": 4, "h": 2, "minW": 3, "minH": 1}
WIDGET_REQUIRES = []
WIDGET_ICON = "🌐"
WIDGET_DESCRIPTION = "Network interface statistics"
WIDGET_CONFIG_SCHEMA: list[dict] = []
WIDGET_MULTI_INSTANCE = False

import time
from pathlib import Path

# Cache previous reading so subsequent calls don't need to sleep
_prev_reading: dict | None = None
_prev_time: float = 0.0


def _read_net_dev() -> dict[str, tuple[int, int]]:
    """Read /proc/net/dev and return {iface: (rx_bytes, tx_bytes)}."""
    result = {}
    try:
        content = Path("/proc/net/dev").read_text()
        for line in content.strip().split("\n")[2:]:  # skip header lines
            parts = line.split()
            if len(parts) < 10:
                continue
            iface = parts[0].rstrip(":")
            rx_bytes = int(parts[1])
            tx_bytes = int(parts[9])
            result[iface] = (rx_bytes, tx_bytes)
    except Exception:
        pass
    return result


def _pick_primary(stats: dict[str, tuple[int, int]]) -> str | None:
    """Pick the non-lo interface with the most total traffic."""
    best_iface = None
    best_total = -1
    for iface, (rx, tx) in stats.items():
        if iface == "lo":
            continue
        total = rx + tx
        if total > best_total:
            best_total = total
            best_iface = iface
    return best_iface


def collect(config=None, adapters=None) -> dict:
    """Collect network stats — registry-compatible entry point."""
    return collect_network()


def collect_network() -> dict:
    """Return current network speed in Mbps.

    On first call, reads /proc/net/dev twice with a 1-second gap.
    On subsequent calls, uses the cached previous reading (no sleep).
    """
    global _prev_reading, _prev_time

    now = time.monotonic()
    current = _read_net_dev()

    if not current:
        return {"download_mbps": 0.0, "upload_mbps": 0.0}

    iface = _pick_primary(current)
    if not iface:
        return {"download_mbps": 0.0, "upload_mbps": 0.0}

    if _prev_reading is None or (now - _prev_time) > 10.0:
        # First call or stale — take a fresh pair with 1s gap
        _prev_reading = current
        _prev_time = now
        time.sleep(1)
        current = _read_net_dev()
        now = time.monotonic()

    elapsed = now - _prev_time
    if elapsed <= 0:
        elapsed = 1.0

    prev_rx, prev_tx = _prev_reading.get(iface, (0, 0))
    cur_rx, cur_tx = current.get(iface, (0, 0))

    rx_diff = max(0, cur_rx - prev_rx)
    tx_diff = max(0, cur_tx - prev_tx)

    # Convert bytes/elapsed to Mbps (bytes * 8 / 1_000_000)
    dl_mbps = round((rx_diff * 8) / (elapsed * 1_000_000), 2)
    ul_mbps = round((tx_diff * 8) / (elapsed * 1_000_000), 2)

    # Store current as previous for next call
    _prev_reading = current
    _prev_time = now

    return {"download_mbps": dl_mbps, "upload_mbps": ul_mbps}
