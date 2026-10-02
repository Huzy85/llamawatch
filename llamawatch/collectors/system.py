"""System metrics collector for llamawatch."""

WIDGET_ID = "system"
WIDGET_NAME = "System Resources"
WIDGET_DEFAULT_SIZE = {"w": 8, "h": 2, "minW": 4, "minH": 2}
WIDGET_REQUIRES = []
WIDGET_ICON = "💻"
WIDGET_DESCRIPTION = "CPU, RAM, GPU, disk, temperatures"
WIDGET_CONFIG_SCHEMA: list[dict] = []
WIDGET_MULTI_INSTANCE = False

import os
import time
from pathlib import Path

# Cache previous CPU stats so subsequent calls don't need to sleep
_prev_cpu_stats: dict | None = None
_prev_cpu_time: float = 0.0


def _read_file(path: str) -> str | None:
    """Read a file and return its contents, or None on failure."""
    try:
        return Path(path).read_text().strip()
    except Exception:
        return None


def _read_hwmon_temps() -> dict:
    """Scan /sys/class/hwmon/ and return identified temperatures."""
    temps = {
        "cpu_temp": None,
        "gpu_temp": None,
        "ssd_temp": None,
        "wifi_temp": None,
    }
    sensor_map = {
        "k10temp": "cpu_temp",
        "amdgpu": "gpu_temp",
        "nvme": "ssd_temp",
        "mt7925_phy0": "wifi_temp",
    }
    hwmon_base = Path("/sys/class/hwmon")
    try:
        for hwmon_dir in hwmon_base.iterdir():
            name_file = hwmon_dir / "name"
            if not name_file.exists():
                continue
            try:
                name = name_file.read_text().strip()
            except Exception:
                continue
            if name in sensor_map:
                temp_file = hwmon_dir / "temp1_input"
                try:
                    raw = int(temp_file.read_text().strip())
                    temps[sensor_map[name]] = round(raw / 1000.0, 1)
                except Exception:
                    pass
    except Exception:
        pass
    return temps


def _read_gpu_utilisation() -> float | None:
    """Read GPU busy percent from DRM sysfs."""
    try:
        drm_base = Path("/sys/class/drm")
        for card_dir in sorted(drm_base.iterdir()):
            gpu_file = card_dir / "device" / "gpu_busy_percent"
            if gpu_file.exists():
                return float(gpu_file.read_text().strip())
    except Exception:
        pass
    return None


def _read_cpu_stat() -> tuple | None:
    """Read /proc/stat first line, return (idle, total) jiffies."""
    try:
        content = Path("/proc/stat").read_text()
        first_line = content.split("\n")[0]  # "cpu  user nice system idle ..."
        parts = first_line.split()
        # parts[0] = "cpu", parts[1:] = user, nice, system, idle, iowait, irq, softirq, steal, ...
        values = [int(v) for v in parts[1:]]
        idle = values[3] + values[4]  # idle + iowait
        total = sum(values)
        return (idle, total)
    except Exception:
        return None


def _calc_cpu_pct() -> float | None:
    """Calculate CPU usage percentage using cached previous reading."""
    global _prev_cpu_stats, _prev_cpu_time

    current = _read_cpu_stat()
    if current is None:
        return None

    now = time.monotonic()

    if _prev_cpu_stats is None or (now - _prev_cpu_time) > 5.0:
        # No previous reading or it's too stale — take a fresh pair
        _prev_cpu_stats = current
        _prev_cpu_time = now
        time.sleep(0.1)
        current = _read_cpu_stat()
        if current is None:
            return None

    prev_idle, prev_total = _prev_cpu_stats
    cur_idle, cur_total = current

    # Store current as previous for next call
    _prev_cpu_stats = current
    _prev_cpu_time = now

    idle_diff = cur_idle - prev_idle
    total_diff = cur_total - prev_total

    if total_diff == 0:
        return 0.0

    return round((1.0 - idle_diff / total_diff) * 100.0, 1)


def _read_ram() -> dict:
    """Parse /proc/meminfo for RAM stats in GB."""
    result = {"ram_used_gb": 0.0, "ram_total_gb": 0.0, "ram_available_gb": 0.0}
    try:
        content = Path("/proc/meminfo").read_text()
        meminfo = {}
        for line in content.split("\n"):
            if ":" in line:
                key, val = line.split(":", 1)
                # Value is like "123456 kB"
                parts = val.strip().split()
                if parts:
                    meminfo[key.strip()] = int(parts[0])

        total_kb = meminfo.get("MemTotal", 0)
        available_kb = meminfo.get("MemAvailable", 0)
        used_kb = total_kb - available_kb

        result["ram_total_gb"] = round(total_kb / (1024 * 1024), 1)
        result["ram_available_gb"] = round(available_kb / (1024 * 1024), 1)
        result["ram_used_gb"] = round(used_kb / (1024 * 1024), 1)
    except Exception:
        pass
    return result


def _read_disk_usage() -> float | None:
    """Read root filesystem usage percentage."""
    try:
        st = os.statvfs("/")
        total = st.f_blocks * st.f_frsize
        free = st.f_bfree * st.f_frsize
        used = total - free
        return round((used / total) * 100, 1) if total > 0 else None
    except Exception:
        return None


def collect(config=None, adapters=None) -> dict:
    """Collect system metrics — registry-compatible entry point."""
    return collect_system()


def collect_system() -> dict:
    """Collect all system metrics and return as a dict."""
    temps = _read_hwmon_temps()
    ram = _read_ram()

    return {
        "cpu_pct": _calc_cpu_pct(),
        "cpu_temp": temps["cpu_temp"],
        "gpu_temp": temps["gpu_temp"],
        "gpu_pct": _read_gpu_utilisation(),
        "ssd_temp": temps["ssd_temp"],
        "wifi_temp": temps["wifi_temp"],
        "ram_used_gb": ram["ram_used_gb"],
        "ram_total_gb": ram["ram_total_gb"],
        "ram_available_gb": ram["ram_available_gb"],
        "disk_pct": _read_disk_usage(),
    }
