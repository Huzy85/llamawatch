"""Power consumption collector for LlamaWatch.

Reads CPU power via RAPL (works on Intel and AMD Zen) and GPU power via
hwmon sysfs. Returns watts computed from cumulative energy counters.
"""

WIDGET_ID = "power"
WIDGET_NAME = "Power"
WIDGET_ICON = "\U0001f50b"
WIDGET_DESCRIPTION = "CPU and GPU power consumption in watts"
WIDGET_DEFAULT_SIZE = {"w": 3, "h": 2, "minW": 2, "minH": 2}
WIDGET_REQUIRES: list[str] = []
WIDGET_CONFIG_SCHEMA: list[dict] = []
WIDGET_CONFIG_REQUIRED = False
WIDGET_MULTI_INSTANCE = False

import collections
import time
from pathlib import Path

# Paths as module-level variables so tests can patch them
_RAPL_ENERGY_PATH = Path("/sys/class/powercap/intel-rapl:0/energy_uj")
_RAPL_MAX_PATH = Path("/sys/class/powercap/intel-rapl:0/max_energy_range_uj")
_HWMON_BASE = Path("/sys/class/hwmon")

# Module-level state for CPU delta computation
_prev_rapl_energy: int | None = None
_prev_rapl_time: float | None = None

# Ring buffer: last 50 total-watts readings for sparkline
_history: collections.deque = collections.deque(maxlen=50)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_rapl_energy_uj() -> int | None:
    """Read the RAPL cumulative energy counter in microjoules, or None on failure."""
    try:
        return int(_RAPL_ENERGY_PATH.read_text().strip())
    except Exception:
        return None


def _read_rapl_max_energy_uj() -> int | None:
    """Read the RAPL max_energy_range_uj counter, or None on failure."""
    try:
        return int(_RAPL_MAX_PATH.read_text().strip())
    except Exception:
        return None


def _read_cpu_power() -> float | None:
    """Return CPU package power in watts using RAPL delta, or None.

    Returns None on the first call (no baseline) or when RAPL is unavailable.
    Handles counter wrap using max_energy_range_uj.
    """
    global _prev_rapl_energy, _prev_rapl_time

    current_energy = _read_rapl_energy_uj()
    current_time = time.monotonic()

    if current_energy is None:
        return None

    if _prev_rapl_energy is None or _prev_rapl_time is None:
        # First call — store baseline, no reading yet
        _prev_rapl_energy = current_energy
        _prev_rapl_time = current_time
        return None

    delta_time = current_time - _prev_rapl_time
    if delta_time <= 0:
        return None

    delta_energy = current_energy - _prev_rapl_energy
    if delta_energy < 0:
        # Counter wrapped — add max_energy_range to compensate
        max_range = _read_rapl_max_energy_uj()
        if max_range is not None:
            delta_energy += max_range
        else:
            # Can't correct wrap without max range — skip this reading
            _prev_rapl_energy = current_energy
            _prev_rapl_time = current_time
            return None

    _prev_rapl_energy = current_energy
    _prev_rapl_time = current_time

    return delta_energy / 1_000_000 / delta_time


def _read_gpu_power() -> float | None:
    """Return GPU power in watts by scanning hwmon sysfs, or None.

    Looks for an amdgpu (power1_average) or nvidia (power1_input) entry.
    Values in sysfs are in microwatts.
    """
    if not _HWMON_BASE.exists():
        return None

    for hwmon_dir in sorted(_HWMON_BASE.iterdir()):
        name_file = hwmon_dir / "name"
        if not name_file.exists():
            continue

        try:
            name = name_file.read_text().strip()
        except Exception:
            continue

        if "amdgpu" in name:
            power_file = hwmon_dir / "power1_average"
            if power_file.exists():
                try:
                    return int(power_file.read_text().strip()) / 1_000_000
                except Exception:
                    continue

        if "nvidia" in name:
            power_file = hwmon_dir / "power1_input"
            if power_file.exists():
                try:
                    return int(power_file.read_text().strip()) / 1_000_000
                except Exception:
                    continue

    return None


# ---------------------------------------------------------------------------
# Collector entry point
# ---------------------------------------------------------------------------


def collect(config=None, adapters=None, widget_config=None) -> dict:
    """Collect CPU and GPU power consumption in watts.

    CPU power is computed from the RAPL energy counter delta between polls.
    GPU power is read directly from hwmon sysfs. Both return None when the
    sensor is unavailable or on the first RAPL call (no baseline yet).

    Returns
    -------
    dict with keys:
        cpu_watts   : float | None  — CPU package power in watts
        gpu_watts   : float | None  — GPU power in watts
        total_watts : float | None  — sum of available readings, or None if none
        history     : list[float]   — last <=50 total_watts readings for sparkline
    """
    cpu_watts = _read_cpu_power()
    gpu_watts = _read_gpu_power()

    # Compute total from whichever sensors have data
    values = [v for v in (cpu_watts, gpu_watts) if v is not None]
    total_watts: float | None = sum(values) if values else None

    if total_watts is not None:
        _history.append(total_watts)

    return {
        "cpu_watts": cpu_watts,
        "gpu_watts": gpu_watts,
        "total_watts": total_watts,
        "history": list(_history),
    }
