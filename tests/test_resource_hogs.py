"""Tests for the Resource Hogs collector."""

from unittest.mock import patch, mock_open, MagicMock

import pytest

from llamawatch.collectors import resource_hogs
from llamawatch.collectors.resource_hogs import (
    _get_top_processes,
    _read_proc,
    collect,
)


# ---------------------------------------------------------------------------
# _read_proc parsing
# ---------------------------------------------------------------------------

SAMPLE_STAT = (
    "1234 (python3) S 1 1234 1234 0 -1 4194304 5000 0 0 0 150 30 0 0 20 0 1 0 "
    "100 100000000 25000 18446744073709551615"
)
SAMPLE_STATUS = "Name:\tpython3\nPid:\t1234\nVmRSS:\t524288 kB\n"
SAMPLE_CMDLINE = "python3\x00-m\x00llamawatch\x00"


class TestReadProc:
    def _make_open(self, stat=SAMPLE_STAT, status=SAMPLE_STATUS, cmdline=SAMPLE_CMDLINE):
        """Return a side_effect function for builtins.open that returns appropriate content."""
        def _open_side_effect(path, *args, **kwargs):
            path = str(path)
            mode = args[0] if args else kwargs.get("mode", "r")
            if "stat" in path and "status" not in path:
                return mock_open(read_data=stat)()
            if "status" in path:
                return mock_open(read_data=status)()
            if "cmdline" in path:
                # binary open
                m = MagicMock()
                m.__enter__ = lambda s: s
                m.__exit__ = MagicMock(return_value=False)
                m.read = MagicMock(return_value=cmdline.encode())
                return m
            raise FileNotFoundError(path)
        return _open_side_effect

    def test_cpu_jiffies_parsed_correctly(self):
        with patch("builtins.open", side_effect=self._make_open()):
            result = _read_proc(1234)
        assert result is not None
        # utime=150, stime=30 -> cpu_jiffies=180
        assert result["cpu_jiffies"] == 180

    def test_ram_mb_parsed_correctly(self):
        with patch("builtins.open", side_effect=self._make_open()):
            result = _read_proc(1234)
        assert result is not None
        # 524288 kB / 1024 = 512.0 MB
        assert result["ram_mb"] == 512.0

    def test_pid_returned(self):
        with patch("builtins.open", side_effect=self._make_open()):
            result = _read_proc(1234)
        assert result is not None
        assert result["pid"] == 1234

    def test_name_parsed_correctly(self):
        with patch("builtins.open", side_effect=self._make_open()):
            result = _read_proc(1234)
        assert result is not None
        assert result["name"] == "python3"

    def test_cmdline_parsed_correctly(self):
        with patch("builtins.open", side_effect=self._make_open()):
            result = _read_proc(1234)
        assert result is not None
        assert result["cmdline"] == "python3 -m llamawatch"

    def test_returns_none_on_missing_stat(self):
        def _open_missing(path, *args, **kwargs):
            raise FileNotFoundError(path)
        with patch("builtins.open", side_effect=_open_missing):
            result = _read_proc(9999)
        assert result is None

    def test_returns_none_when_fields_too_short(self):
        short_stat = "1234 (python3) S 1 1234"
        with patch("builtins.open", side_effect=self._make_open(stat=short_stat)):
            result = _read_proc(1234)
        assert result is None


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------


def test_module_constants():
    assert resource_hogs.WIDGET_ID == "resource-hogs"
    assert resource_hogs.WIDGET_NAME == "Resource Hogs"
    assert resource_hogs.WIDGET_MULTI_INSTANCE is False
    assert resource_hogs.WIDGET_CONFIG_REQUIRED is False
    assert isinstance(resource_hogs.WIDGET_DEFAULT_SIZE, dict)
    assert resource_hogs.WIDGET_DEFAULT_SIZE["w"] == 4
    assert resource_hogs.WIDGET_REQUIRES == []
    assert resource_hogs.WIDGET_CONFIG_SCHEMA == []
    assert resource_hogs.WIDGET_ICON == "\U0001f525"


# ---------------------------------------------------------------------------
# _read_all_procs delta CPU cache
# ---------------------------------------------------------------------------


class TestReadAllProcsCache:
    def setup_method(self):
        # Clear cache before each test
        resource_hogs._prev_cpu.clear()

    def _make_proc_info(self, pid, jiffies, ram_mb):
        return {"pid": pid, "name": f"proc{pid}", "cmdline": "", "cpu_jiffies": jiffies, "ram_mb": ram_mb}

    def test_first_poll_cpu_pct_is_zero(self):
        with (
            patch.object(resource_hogs, "_list_pids", return_value=[42]),
            patch.object(resource_hogs, "_read_proc", return_value=self._make_proc_info(42, 1000, 50.0)),
            patch.object(resource_hogs, "_total_cpu_jiffies", return_value=10000),
        ):
            procs = resource_hogs._read_all_procs()
        # No prior cache entry — cpu_pct must be 0.0
        assert procs[0]["cpu_pct"] == 0.0

    def test_second_poll_computes_delta(self):
        # Seed cache manually
        resource_hogs._prev_cpu[42] = (1000, 10000)
        with (
            patch.object(resource_hogs, "_list_pids", return_value=[42]),
            patch.object(resource_hogs, "_read_proc", return_value=self._make_proc_info(42, 1200, 50.0)),
            patch.object(resource_hogs, "_total_cpu_jiffies", return_value=10200),
        ):
            procs = resource_hogs._read_all_procs()
        # delta_proc=200, delta_total=200 -> 100%
        assert procs[0]["cpu_pct"] == 100.0

    def test_stale_pid_removed_from_cache(self):
        # PID 99 in cache but not in current pids
        resource_hogs._prev_cpu[99] = (500, 5000)
        with (
            patch.object(resource_hogs, "_list_pids", return_value=[42]),
            patch.object(resource_hogs, "_read_proc", return_value=self._make_proc_info(42, 100, 50.0)),
            patch.object(resource_hogs, "_total_cpu_jiffies", return_value=10000),
        ):
            resource_hogs._read_all_procs()
        assert 99 not in resource_hogs._prev_cpu

    def test_cache_updated_after_poll(self):
        resource_hogs._prev_cpu[42] = (500, 5000)
        with (
            patch.object(resource_hogs, "_list_pids", return_value=[42]),
            patch.object(resource_hogs, "_read_proc", return_value=self._make_proc_info(42, 700, 50.0)),
            patch.object(resource_hogs, "_total_cpu_jiffies", return_value=6000),
        ):
            resource_hogs._read_all_procs()
        assert resource_hogs._prev_cpu[42] == (700, 6000)


# ---------------------------------------------------------------------------
# _get_top_processes
# ---------------------------------------------------------------------------

_FAKE_PROCS = [
    {"pid": 1, "name": "systemd",   "cmdline": "/sbin/init", "cpu_pct": 5.0,  "ram_mb": 20.0},
    {"pid": 2, "name": "python3",   "cmdline": "python3 app.py", "cpu_pct": 75.0, "ram_mb": 400.0},
    {"pid": 3, "name": "llama",     "cmdline": "./llama-server", "cpu_pct": 60.0, "ram_mb": 8000.0},
    {"pid": 4, "name": "chrome",    "cmdline": "chrome --headless", "cpu_pct": 30.0, "ram_mb": 300.0},
    {"pid": 5, "name": "node",      "cmdline": "node server.js", "cpu_pct": 15.0, "ram_mb": 150.0},
    {"pid": 6, "name": "postgres",  "cmdline": "postgres", "cpu_pct": 8.0,  "ram_mb": 80.0},
    {"pid": 7, "name": "redis",     "cmdline": "redis-server", "cpu_pct": 2.0,  "ram_mb": 15.0},
    {"pid": 8, "name": "nginx",     "cmdline": "nginx", "cpu_pct": 1.0,  "ram_mb": 12.0},
    {"pid": 9, "name": "docker",    "cmdline": "dockerd", "cpu_pct": 12.0, "ram_mb": 110.0},
]


class TestGetTopProcesses:
    def test_returns_list(self):
        with patch.object(resource_hogs, "_read_all_procs", return_value=_FAKE_PROCS):
            result = _get_top_processes()
        assert isinstance(result, list)

    def test_sorted_by_cpu_descending(self):
        with patch.object(resource_hogs, "_read_all_procs", return_value=_FAKE_PROCS):
            result = _get_top_processes()
        cpu_values = [p["cpu_pct"] for p in result]
        assert cpu_values == sorted(cpu_values, reverse=True)

    def test_respects_max_n(self):
        with patch.object(resource_hogs, "_read_all_procs", return_value=_FAKE_PROCS):
            result = _get_top_processes(n=3)
        assert len(result) <= 3

    def test_default_max_n_is_8(self):
        many_procs = [
            {"pid": i, "name": f"proc{i}", "cmdline": "", "cpu_pct": float(i), "ram_mb": 50.0}
            for i in range(1, 15)
        ]
        with patch.object(resource_hogs, "_read_all_procs", return_value=many_procs):
            result = _get_top_processes()
        assert len(result) <= 8

    def test_returns_empty_on_no_procs(self):
        with patch.object(resource_hogs, "_read_all_procs", return_value=[]):
            result = _get_top_processes()
        assert result == []

    def test_top_proc_has_highest_cpu(self):
        with patch.object(resource_hogs, "_read_all_procs", return_value=_FAKE_PROCS):
            result = _get_top_processes(n=8)
        assert result[0]["cpu_pct"] == 75.0
        assert result[0]["name"] == "python3"


# ---------------------------------------------------------------------------
# collect
# ---------------------------------------------------------------------------


class TestCollect:
    def test_returns_processes_key(self):
        with patch.object(resource_hogs, "_get_top_processes", return_value=_FAKE_PROCS[:3]):
            result = collect()
        assert "processes" in result

    def test_processes_is_list(self):
        with patch.object(resource_hogs, "_get_top_processes", return_value=_FAKE_PROCS[:3]):
            result = collect()
        assert isinstance(result["processes"], list)

    def test_accepts_all_kwargs(self):
        with patch.object(resource_hogs, "_get_top_processes", return_value=[]):
            result = collect(config={}, adapters=None, widget_config={})
        assert "processes" in result

    def test_handles_exception_gracefully(self):
        with patch.object(resource_hogs, "_get_top_processes", side_effect=RuntimeError("oops")):
            result = collect()
        assert "processes" in result
        assert result["processes"] == []

    def test_passes_through_process_data(self):
        procs = _FAKE_PROCS[:2]
        with patch.object(resource_hogs, "_get_top_processes", return_value=procs):
            result = collect()
        assert result["processes"] == procs

    def test_returns_machines_key(self):
        """collect() returns a 'machines' list with per-machine process donuts."""
        with patch.object(resource_hogs, "_get_top_processes", return_value=_FAKE_PROCS[:3]), \
             patch.object(resource_hogs, "_collect_remote_procs_all", return_value=[
                 {"name": "TC1", "procs": [{"name": "nginx", "cpu_pct": 12.0}]},
                 {"name": "TC2", "procs": []},
             ]):
            result = collect()
        assert "machines" in result
        assert isinstance(result["machines"], list)
        assert len(result["machines"]) == 3  # M5 + TC1 + TC2

    def test_machines_includes_m5_as_first(self):
        """The local machine is always first in the machines list.

        Its label comes from _local_name() (fleet config / hostname); pin it
        to "M5" so the ordering assertion is independent of the test host.
        """
        with patch.object(resource_hogs, "_get_top_processes", return_value=_FAKE_PROCS[:2]), \
             patch.object(resource_hogs, "_local_name", return_value="M5"), \
             patch.object(resource_hogs, "_collect_remote_procs_all", return_value=[
                 {"name": "TC1", "procs": []},
                 {"name": "TC2", "procs": []},
             ]):
            result = collect()
        assert result["machines"][0]["name"] == "M5"

    def test_machines_m5_procs_are_compact(self):
        """M5 machine procs in 'machines' have name+cpu_pct only (no pid/cmdline/ram_mb)."""
        with patch.object(resource_hogs, "_get_top_processes", return_value=_FAKE_PROCS[:2]), \
             patch.object(resource_hogs, "_collect_remote_procs_all", return_value=[
                 {"name": "TC1", "procs": []},
                 {"name": "TC2", "procs": []},
             ]):
            result = collect()
        m5_procs = result["machines"][0]["procs"]
        for p in m5_procs:
            assert "name" in p
            assert "cpu_pct" in p
            assert "pid" not in p


# ---------------------------------------------------------------------------
# _fetch_remote_procs
# ---------------------------------------------------------------------------

from llamawatch.collectors.resource_hogs import _fetch_remote_procs


class TestFetchRemoteProcs:
    def test_parses_ps_output(self):
        """_fetch_remote_procs parses ps output correctly."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "llama-server 45.2\npython3 12.5\nidle-proc 0.0\n"

        with patch("subprocess.run", return_value=mock_result):
            result = _fetch_remote_procs({"name": "TC1", "host": "10.0.0.11", "user": "testuser"})

        # _fetch_remote_procs now returns {"procs": [...], "machine_cpu_pct": ...}
        procs = result["procs"]
        # 0.0% processes should be excluded
        assert len(procs) == 2
        assert procs[0]["name"] == "llama-server"
        assert procs[0]["cpu_pct"] == 45.2

    def test_returns_empty_on_ssh_failure(self):
        """_fetch_remote_procs returns an empty procs list on SSH non-zero return."""
        mock_result = MagicMock()
        mock_result.returncode = 255
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            result = _fetch_remote_procs({"name": "TC1", "host": "10.0.0.11", "user": "testuser"})

        assert result["procs"] == []
        assert result["machine_cpu_pct"] is None

    def test_returns_empty_on_timeout(self):
        """_fetch_remote_procs returns an empty procs list on timeout."""
        import subprocess as _sp
        with patch("subprocess.run", side_effect=_sp.TimeoutExpired(cmd=[], timeout=6)):
            result = _fetch_remote_procs({"name": "TC2", "host": "10.0.0.12", "user": "testuser"})

        assert result["procs"] == []
        assert result["machine_cpu_pct"] is None

    def test_sorted_descending_by_cpu(self):
        """_fetch_remote_procs returns procs sorted by cpu_pct descending."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "python3 5.0\nllama 80.0\nnginx 15.0\n"

        with patch("subprocess.run", return_value=mock_result):
            result = _fetch_remote_procs({"name": "TC1", "host": "10.0.0.11", "user": "testuser"})

        cpu_values = [p["cpu_pct"] for p in result["procs"]]
        assert cpu_values == sorted(cpu_values, reverse=True)
