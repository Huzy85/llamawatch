"""Tests for the GPU monitor collector."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from llamawatch.collectors import gpu


@pytest.fixture(autouse=True)
def reset_vendor_cache():
    """Reset the module-level vendor cache before each test."""
    gpu._vendor_cache = gpu._UNSET
    yield
    gpu._vendor_cache = gpu._UNSET


# ---------------------------------------------------------------------------
# _detect_vendor
# ---------------------------------------------------------------------------


class TestDetectVendor:
    def test_no_gpu_returns_none(self, tmp_path):
        """Returns None when no GPU detected."""
        # tmp_path has no card*/device/vendor files — AMD check finds nothing
        with (
            patch("llamawatch.collectors.gpu.shutil.which", return_value=None),
            patch("llamawatch.collectors.gpu.platform.system", return_value="Linux"),
            patch("llamawatch.collectors.gpu._DRM_BASE", tmp_path),
        ):
            result = gpu._detect_vendor()
        assert result is None

    def test_nvidia_detected_via_nvidia_smi(self):
        """Returns 'nvidia' when nvidia-smi is on PATH."""
        with patch("llamawatch.collectors.gpu.shutil.which", return_value="/usr/bin/nvidia-smi"):
            result = gpu._detect_vendor()
        assert result == "nvidia"

    def test_amd_detected_via_vendor_file(self, tmp_path):
        """Returns 'amd' when a DRM vendor file contains AMD's PCI ID."""
        # Build fake sysfs tree: tmp_path/card0/device/vendor
        card_device = tmp_path / "card0" / "device"
        card_device.mkdir(parents=True)
        (card_device / "vendor").write_text("0x1002\n")

        drm_base = tmp_path

        with (
            patch("llamawatch.collectors.gpu.shutil.which", return_value=None),
            patch("llamawatch.collectors.gpu.platform.system", return_value="Linux"),
            patch("llamawatch.collectors.gpu._DRM_BASE", drm_base),
        ):
            result = gpu._detect_vendor()
        assert result == "amd"

    def test_apple_detected_via_platform(self):
        """Returns 'apple' on macOS when no NVIDIA is found."""
        with (
            patch("llamawatch.collectors.gpu.shutil.which", return_value=None),
            patch("llamawatch.collectors.gpu.platform.system", return_value="Darwin"),
            patch("llamawatch.collectors.gpu._DRM_BASE", Path("/nonexistent")),
        ):
            result = gpu._detect_vendor()
        assert result == "apple"

    def test_result_is_cached(self, tmp_path):
        """Second call returns cached result without re-scanning."""
        with (
            patch("llamawatch.collectors.gpu.shutil.which", return_value="/usr/bin/nvidia-smi") as mock_which,
            patch("llamawatch.collectors.gpu._DRM_BASE", tmp_path),
        ):
            gpu._detect_vendor()
            gpu._detect_vendor()
        # which() is only called once — second call uses the cache
        assert mock_which.call_count == 1


# ---------------------------------------------------------------------------
# collect — no GPU
# ---------------------------------------------------------------------------


class TestCollectNoGpu:
    def test_all_none_when_no_vendor(self):
        """collect() returns all-None dict when vendor is None."""
        with patch.object(gpu, "_detect_vendor", return_value=None):
            result = gpu.collect()
        assert result == {
            "vendor": None,
            "vram_used_mb": None,
            "vram_total_mb": None,
            "utilization_pct": None,
            "temperature_c": None,
            "power_watts": None,
        }


# ---------------------------------------------------------------------------
# collect — AMD path
# ---------------------------------------------------------------------------


class TestCollectAmd:
    def _build_amd_sysfs(self, tmp_path):
        """Create a minimal fake AMD sysfs tree under tmp_path."""
        device = tmp_path / "card0" / "device"
        device.mkdir(parents=True)

        # AMD vendor ID
        (device / "vendor").write_text("0x1002\n")

        # VRAM: 2 GiB used, 8 GiB total
        (device / "mem_info_vram_used").write_text(str(2 * 1024 * 1024 * 1024))
        (device / "mem_info_vram_total").write_text(str(8 * 1024 * 1024 * 1024))

        # GPU busy percent
        (device / "gpu_busy_percent").write_text("65\n")

        # hwmon tree with amdgpu driver
        hwmon0 = device / "hwmon" / "hwmon0"
        hwmon0.mkdir(parents=True)
        (hwmon0 / "name").write_text("amdgpu\n")
        (hwmon0 / "temp1_input").write_text("75000\n")   # millidegrees → 75 °C
        (hwmon0 / "power1_average").write_text("85000000\n")  # microwatts → 85 W

        return tmp_path

    def test_amd_collect_full(self, tmp_path):
        """AMD collect reads VRAM, utilization, temperature and power correctly."""
        self._build_amd_sysfs(tmp_path)

        with (
            patch.object(gpu, "_detect_vendor", return_value="amd"),
            patch("llamawatch.collectors.gpu._DRM_BASE", tmp_path),
        ):
            result = gpu.collect()

        assert result["vendor"] == "amd"
        assert result["vram_used_mb"] == 2048
        assert result["vram_total_mb"] == 8192
        assert result["utilization_pct"] == 65
        assert result["temperature_c"] == 75
        assert result["power_watts"] == pytest.approx(85.0)

    def test_amd_missing_files_returns_none(self, tmp_path):
        """AMD collect returns None for metrics when files don't exist."""
        # Create vendor file only — none of the metric files
        card_device = tmp_path / "card0" / "device"
        card_device.mkdir(parents=True)
        (card_device / "vendor").write_text("0x1002\n")

        with (
            patch.object(gpu, "_detect_vendor", return_value="amd"),
            patch("llamawatch.collectors.gpu._DRM_BASE", tmp_path),
        ):
            result = gpu.collect()

        assert result["vendor"] == "amd"
        assert result["vram_used_mb"] is None
        assert result["vram_total_mb"] is None
        assert result["utilization_pct"] is None
        assert result["temperature_c"] is None
        assert result["power_watts"] is None


# ---------------------------------------------------------------------------
# collect — NVIDIA path
# ---------------------------------------------------------------------------


class TestCollectNvidia:
    def test_nvidia_collect_parses_output(self):
        """NVIDIA collect parses nvidia-smi CSV output correctly."""
        fake_output = "50, 4096, 8192, 72, 120.5\n"
        with (
            patch.object(gpu, "_detect_vendor", return_value="nvidia"),
            patch(
                "llamawatch.collectors.gpu.subprocess.check_output",
                return_value=fake_output,
            ),
        ):
            result = gpu.collect()

        assert result["vendor"] == "nvidia"
        assert result["utilization_pct"] == 50
        assert result["vram_used_mb"] == 4096
        assert result["vram_total_mb"] == 8192
        assert result["temperature_c"] == 72
        assert result["power_watts"] == pytest.approx(120.5)

    def test_nvidia_subprocess_failure_returns_none_metrics(self):
        """When nvidia-smi fails, vendor is set but all metrics are None."""
        with (
            patch.object(gpu, "_detect_vendor", return_value="nvidia"),
            patch(
                "llamawatch.collectors.gpu.subprocess.check_output",
                side_effect=subprocess.SubprocessError("nvidia-smi not available"),
            ),
        ):
            result = gpu.collect()

        assert result["vendor"] == "nvidia"
        assert result["vram_used_mb"] is None
        assert result["utilization_pct"] is None

    def test_nvidia_invokes_correct_query(self):
        """nvidia-smi is called with the expected --query-gpu flags."""
        fake_output = "0, 0, 8192, 30, 10.0\n"
        with (
            patch.object(gpu, "_detect_vendor", return_value="nvidia"),
            patch(
                "llamawatch.collectors.gpu.subprocess.check_output",
                return_value=fake_output,
            ) as mock_sp,
        ):
            gpu.collect()

        call_args = mock_sp.call_args[0][0]  # first positional arg is the cmd list
        assert call_args[0] == "nvidia-smi"
        assert any("utilization.gpu" in a for a in call_args)
        assert any("memory.used" in a for a in call_args)
        assert any("temperature.gpu" in a for a in call_args)
        assert any("power.draw" in a for a in call_args)


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------


def test_module_constants():
    assert gpu.WIDGET_ID == "gpu"
    assert gpu.WIDGET_NAME == "GPU Monitor"
    assert gpu.WIDGET_MULTI_INSTANCE is False
    assert gpu.WIDGET_CONFIG_REQUIRED is False
    assert isinstance(gpu.WIDGET_DEFAULT_SIZE, dict)
    assert gpu.WIDGET_DEFAULT_SIZE["w"] == 4
