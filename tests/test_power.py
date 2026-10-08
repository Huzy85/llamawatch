"""Tests for the power consumption collector."""

from pathlib import Path
from unittest.mock import patch

import pytest

from llamawatch.collectors import power
from llamawatch.collectors.power import (
    _read_cpu_power,
    _read_gpu_power,
    collect,
)


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------


def test_module_constants():
    assert power.WIDGET_ID == "power"
    assert power.WIDGET_NAME == "Power"
    assert power.WIDGET_MULTI_INSTANCE is False
    assert power.WIDGET_CONFIG_REQUIRED is False
    assert isinstance(power.WIDGET_DEFAULT_SIZE, dict)
    assert power.WIDGET_DEFAULT_SIZE["w"] == 3
    assert power.WIDGET_REQUIRES == []
    assert power.WIDGET_CONFIG_SCHEMA == []
    assert power.WIDGET_ICON == "\U0001f50b"


# ---------------------------------------------------------------------------
# _read_gpu_power
# ---------------------------------------------------------------------------


class TestReadGpuPower:
    def test_reads_amdgpu_power(self, tmp_path):
        """Returns watts from amdgpu hwmon power1_average."""
        hwmon0 = tmp_path / "hwmon0"
        hwmon0.mkdir()
        (hwmon0 / "name").write_text("amdgpu\n")
        (hwmon0 / "power1_average").write_text("45000000\n")  # 45 W in microwatts

        with patch.object(power, "_HWMON_BASE", tmp_path):
            result = _read_gpu_power()

        assert result == pytest.approx(45.0)

    def test_reads_nvidia_power(self, tmp_path):
        """Returns watts from nvidia hwmon power1_input."""
        hwmon0 = tmp_path / "hwmon0"
        hwmon0.mkdir()
        (hwmon0 / "name").write_text("nvidia\n")
        (hwmon0 / "power1_input").write_text("120000000\n")  # 120 W in microwatts

        with patch.object(power, "_HWMON_BASE", tmp_path):
            result = _read_gpu_power()

        assert result == pytest.approx(120.0)

    def test_returns_none_when_no_gpu(self, tmp_path):
        """Returns None when no amdgpu or nvidia hwmon entries exist."""
        hwmon0 = tmp_path / "hwmon0"
        hwmon0.mkdir()
        (hwmon0 / "name").write_text("coretemp\n")

        with patch.object(power, "_HWMON_BASE", tmp_path):
            result = _read_gpu_power()

        assert result is None

    def test_returns_none_when_hwmon_missing(self, tmp_path):
        """Returns None when _HWMON_BASE directory does not exist."""
        with patch.object(power, "_HWMON_BASE", tmp_path / "nonexistent"):
            result = _read_gpu_power()

        assert result is None

    def test_returns_none_when_power_file_missing(self, tmp_path):
        """Returns None when name matches but power file is absent."""
        hwmon0 = tmp_path / "hwmon0"
        hwmon0.mkdir()
        (hwmon0 / "name").write_text("amdgpu\n")
        # No power1_average file

        with patch.object(power, "_HWMON_BASE", tmp_path):
            result = _read_gpu_power()

        assert result is None


# ---------------------------------------------------------------------------
# _read_cpu_power
# ---------------------------------------------------------------------------


class TestReadCpuPower:
    def setup_method(self):
        """Reset module-level state before each test."""
        power._prev_rapl_energy = None
        power._prev_rapl_time = None

    def test_returns_none_on_first_call(self):
        """Returns None on first call because there is no baseline."""
        with patch.object(power, "_read_rapl_energy_uj", return_value=1000000):
            with patch("time.monotonic", return_value=100.0):
                result = _read_cpu_power()
        assert result is None

    def test_returns_watts_on_second_call(self):
        """Returns correct watts computed from delta energy / delta time."""
        call_count = 0

        def fake_energy():
            nonlocal call_count
            call_count += 1
            return 1_000_000 if call_count == 1 else 6_000_000  # delta = 5 J

        time_values = iter([100.0, 101.0])  # delta = 1 second

        with patch.object(power, "_read_rapl_energy_uj", side_effect=fake_energy):
            with patch("time.monotonic", side_effect=time_values):
                _read_cpu_power()   # first call — sets baseline
                result = _read_cpu_power()  # second call — computes watts

        # 5_000_000 uj delta / 1_000_000 / 1.0 s = 5.0 W
        assert result == pytest.approx(5.0)

    def test_returns_none_when_no_rapl(self):
        """Returns None when RAPL energy file is not readable."""
        with patch.object(power, "_read_rapl_energy_uj", return_value=None):
            result = _read_cpu_power()
        assert result is None

    def test_handles_counter_wrap(self):
        """Handles counter wrap using max_energy_range_uj."""
        call_count = 0
        max_range = 10_000_000  # 10 J

        def fake_energy():
            nonlocal call_count
            call_count += 1
            return 9_500_000 if call_count == 1 else 500_000  # wraps: delta = 1 J

        time_values = iter([100.0, 101.0])

        with patch.object(power, "_read_rapl_energy_uj", side_effect=fake_energy):
            with patch.object(power, "_read_rapl_max_energy_uj", return_value=max_range):
                with patch("time.monotonic", side_effect=time_values):
                    _read_cpu_power()
                    result = _read_cpu_power()

        # wrap: delta = 500_000 + (10_000_000 - 9_500_000) = 1_000_000 uj = 1 J in 1 s = 1 W
        assert result == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# collect
# ---------------------------------------------------------------------------


class TestCollect:
    def setup_method(self):
        """Reset module-level state before each test."""
        power._prev_rapl_energy = None
        power._prev_rapl_time = None
        power._history.clear()

    def test_returns_expected_keys(self):
        """collect() always returns cpu_watts, gpu_watts, total_watts, history."""
        with patch.object(power, "_read_cpu_power", return_value=None):
            with patch.object(power, "_read_gpu_power", return_value=None):
                result = collect()

        assert "cpu_watts" in result
        assert "gpu_watts" in result
        assert "total_watts" in result
        assert "history" in result

    def test_total_watts_is_sum(self):
        """total_watts is the sum of cpu and gpu watts."""
        with patch.object(power, "_read_cpu_power", return_value=30.0):
            with patch.object(power, "_read_gpu_power", return_value=50.0):
                result = collect()

        assert result["cpu_watts"] == pytest.approx(30.0)
        assert result["gpu_watts"] == pytest.approx(50.0)
        assert result["total_watts"] == pytest.approx(80.0)

    def test_total_watts_none_when_both_missing(self):
        """total_watts is None when both sensors return None."""
        with patch.object(power, "_read_cpu_power", return_value=None):
            with patch.object(power, "_read_gpu_power", return_value=None):
                result = collect()

        assert result["total_watts"] is None

    def test_total_watts_partial_none(self):
        """total_watts uses available sensor when the other is None."""
        with patch.object(power, "_read_cpu_power", return_value=25.0):
            with patch.object(power, "_read_gpu_power", return_value=None):
                result = collect()

        assert result["total_watts"] == pytest.approx(25.0)

    def test_history_accumulates(self):
        """History list grows with each call that has a total_watts reading."""
        with patch.object(power, "_read_cpu_power", return_value=40.0):
            with patch.object(power, "_read_gpu_power", return_value=60.0):
                collect()
                collect()
                result = collect()

        assert len(result["history"]) == 3

    def test_history_is_list(self):
        """history is always a plain list (not a deque)."""
        with patch.object(power, "_read_cpu_power", return_value=None):
            with patch.object(power, "_read_gpu_power", return_value=None):
                result = collect()
        assert isinstance(result["history"], list)

    def test_accepts_all_kwargs(self):
        """collect() accepts config, adapters, widget_config without error."""
        with patch.object(power, "_read_cpu_power", return_value=None):
            with patch.object(power, "_read_gpu_power", return_value=None):
                result = collect(config={}, adapters=None, widget_config={})
        assert "total_watts" in result
