"""GPU monitor collector — AMD, NVIDIA, and Apple Silicon."""

WIDGET_ID = "gpu"
WIDGET_NAME = "GPU Monitor"
WIDGET_ICON = "\U0001f3ae"
WIDGET_DESCRIPTION = "GPU utilization, VRAM, temperature, and power draw"
WIDGET_DEFAULT_SIZE = {"w": 4, "h": 2, "minW": 3, "minH": 2}
WIDGET_REQUIRES: list = []
WIDGET_CONFIG_SCHEMA: list[dict] = []
WIDGET_CONFIG_REQUIRED = False
WIDGET_MULTI_INSTANCE = False

import platform
import shutil
import subprocess
from pathlib import Path

# Base path for DRM sysfs — module-level constant so tests can patch it.
_DRM_BASE = Path("/sys/class/drm")

# Sentinel value meaning "_detect_vendor has not yet run".
# Tests reset _vendor_cache to _UNSET (via the reset_vendor_cache fixture)
# to force re-detection.  Exposed as module attribute for test patching.
_UNSET = object()
_vendor_cache: object = _UNSET  # set to 'amd'|'nvidia'|'apple'|None after first call


def _detect_vendor() -> str | None:
    """Detect GPU vendor, returning 'amd', 'nvidia', 'apple', or None.

    Result is cached in ``_vendor_cache`` so subsequent calls are free.
    Reset by setting ``gpu._vendor_cache = gpu._UNSET`` in tests.
    """
    global _vendor_cache

    if _vendor_cache is not _UNSET:
        return _vendor_cache  # type: ignore[return-value]

    # NVIDIA — nvidia-smi present on PATH
    if shutil.which("nvidia-smi") is not None:
        _vendor_cache = "nvidia"
        return _vendor_cache  # type: ignore[return-value]

    # AMD — check PCI vendor ID in DRM sysfs
    try:
        for vendor_file in sorted(_DRM_BASE.glob("card*/device/vendor")):
            try:
                vid = vendor_file.read_text().strip()
                if vid == "0x1002":
                    _vendor_cache = "amd"
                    return _vendor_cache  # type: ignore[return-value]
            except Exception:
                continue
    except Exception:
        pass

    # Apple Silicon
    if platform.system() == "Darwin":
        _vendor_cache = "apple"
        return _vendor_cache  # type: ignore[return-value]

    _vendor_cache = None
    return _vendor_cache  # type: ignore[return-value]


def _read_file(path: Path) -> str | None:
    """Read a file, returning stripped text or None on any error."""
    try:
        return path.read_text().strip()
    except Exception:
        return None


def _collect_amd() -> dict:
    """Read AMD GPU metrics from sysfs."""
    result: dict = {
        "vram_used_mb": None,
        "vram_total_mb": None,
        "utilization_pct": None,
        "temperature_c": None,
        "power_watts": None,
    }

    # Find the first DRM card whose vendor is AMD
    card_device: Path | None = None
    try:
        for vendor_file in sorted(_DRM_BASE.glob("card*/device/vendor")):
            try:
                if vendor_file.read_text().strip() == "0x1002":
                    card_device = vendor_file.parent
                    break
            except Exception:
                continue
    except Exception:
        pass

    if card_device is None:
        return result

    # VRAM (bytes → MiB)
    raw_used = _read_file(card_device / "mem_info_vram_used")
    raw_total = _read_file(card_device / "mem_info_vram_total")
    if raw_used is not None:
        try:
            result["vram_used_mb"] = int(raw_used) // (1024 * 1024)
        except ValueError:
            pass
    if raw_total is not None:
        try:
            result["vram_total_mb"] = int(raw_total) // (1024 * 1024)
        except ValueError:
            pass

    # Utilization %
    raw_busy = _read_file(card_device / "gpu_busy_percent")
    if raw_busy is not None:
        try:
            result["utilization_pct"] = int(raw_busy)
        except ValueError:
            pass

    # Temperature — scan hwmon dirs beneath the card device
    try:
        for hwmon_dir in sorted((card_device / "hwmon").glob("hwmon*")):
            raw_temp = _read_file(hwmon_dir / "temp1_input")
            if raw_temp is not None:
                try:
                    result["temperature_c"] = int(raw_temp) // 1000
                    break
                except ValueError:
                    pass
    except Exception:
        pass

    # Power — power1_average in microwatts
    try:
        for hwmon_dir in sorted((card_device / "hwmon").glob("hwmon*")):
            raw_power = _read_file(hwmon_dir / "power1_average")
            if raw_power is not None:
                try:
                    result["power_watts"] = int(raw_power) / 1_000_000.0
                    break
                except ValueError:
                    pass
    except Exception:
        pass

    return result


def _collect_nvidia() -> dict:
    """Run nvidia-smi and parse GPU metrics."""
    result: dict = {
        "vram_used_mb": None,
        "vram_total_mb": None,
        "utilization_pct": None,
        "temperature_c": None,
        "power_watts": None,
    }
    try:
        cmd = [
            "nvidia-smi",
            "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
            "--format=csv,noheader,nounits",
        ]
        raw = subprocess.check_output(cmd, encoding="utf-8", timeout=5).strip()
        if not raw:
            return result
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) < 5:
            return result
        result["utilization_pct"] = int(float(parts[0]))
        result["vram_used_mb"] = int(parts[1])
        result["vram_total_mb"] = int(parts[2])
        result["temperature_c"] = int(float(parts[3]))
        result["power_watts"] = float(parts[4])
    except Exception:
        pass
    return result


def collect(config=None, adapters=None, widget_config=None) -> dict:
    """Collect GPU metrics — registry-compatible entry point."""
    vendor = _detect_vendor()

    if vendor is None:
        return {
            "vendor": None,
            "vram_used_mb": None,
            "vram_total_mb": None,
            "utilization_pct": None,
            "temperature_c": None,
            "power_watts": None,
        }

    if vendor == "amd":
        metrics = _collect_amd()
    elif vendor == "nvidia":
        metrics = _collect_nvidia()
    else:
        # Apple or future vendors — no sysfs path yet
        metrics = {
            "vram_used_mb": None,
            "vram_total_mb": None,
            "utilization_pct": None,
            "temperature_c": None,
            "power_watts": None,
        }

    return {"vendor": vendor, **metrics}
