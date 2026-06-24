"""Tests for the fleet collector (M5 local + TC1/TC2 SSH)."""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from llamawatch.collectors import fleet
from llamawatch.collectors.fleet import collect


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_cache():
    """Flush the module-level cache before every test."""
    fleet._cache = {"ts": 0.0, "data": None}
    yield
    fleet._cache = {"ts": 0.0, "data": None}


# Fleet hosts are now config-driven (the module no longer ships a
# _DEFAULT_HOSTS list). Tests that want the classic 3-machine fleet pass this
# explicitly via config={"fleet": {"hosts": _DEFAULT_HOSTS}}.
_DEFAULT_HOSTS = [
    {"name": "M5", "host": "10.0.0.10", "local": True, "user": "testuser"},
    {"name": "TC1", "host": "10.0.0.11", "local": False, "user": "testuser"},
    {"name": "TC2", "host": "10.0.0.12", "local": False, "user": "testuser"},
]
_DEFAULT_CONFIG = {"fleet": {"hosts": _DEFAULT_HOSTS}}


_FAKE_SSH_OUTPUT = json.dumps({
    "cpu_pct": 12.5,
    "ram_pct": 45.0,
    "ram_used_gb": 7.2,
    "ram_total_gb": 16.0,
    "cpu_temp": 52.3,
    "disk_pct": 38.0,
    "load1": 0.8,
    "uptime": "2d 4h",
})


def _make_ssh_result(stdout=_FAKE_SSH_OUTPUT, returncode=0):
    r = MagicMock()
    r.stdout = stdout
    r.stderr = ""
    r.returncode = returncode
    return r


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------


def test_module_constants():
    assert fleet.WIDGET_ID == "fleet"
    assert fleet.WIDGET_NAME == "Fleet"
    assert fleet.WIDGET_MULTI_INSTANCE is False
    assert fleet.WIDGET_CONFIG_REQUIRED is False
    assert isinstance(fleet.WIDGET_DEFAULT_SIZE, dict)
    assert fleet.WIDGET_REQUIRES == []
    assert fleet.WIDGET_CONFIG_SCHEMA == []


# ---------------------------------------------------------------------------
# collect — happy path (3 machines, all online)
# ---------------------------------------------------------------------------


class TestCollectHappyPath:
    def _run(self):
        """Run collect() with local reads mocked and SSH returning fake data."""
        def fake_ssh(host_def):
            return {
                "name": host_def["name"], "host": host_def["host"], "online": True,
                "cpu_pct": 12.5, "ram_pct": 45.0, "ram_used_gb": 7.2,
                "ram_total_gb": 16.0, "cpu_temp": 52.3,
                "disk_pct": 38.0, "load1": 0.8, "uptime": "2d 4h",
            }

        with (
            patch.object(fleet, "_collect_local", return_value={
                "name": "M5", "host": "10.0.0.10", "online": True,
                "cpu_pct": 5.0, "ram_pct": 30.0, "ram_used_gb": 38.0,
                "ram_total_gb": 128.0, "cpu_temp": 48.0,
                "disk_pct": 18.0, "load1": 0.3, "uptime": "10d 2h",
            }),
            patch.object(fleet, "_collect_ssh", side_effect=fake_ssh),
        ):
            return collect(config=_DEFAULT_CONFIG)

    def test_returns_machines_list(self):
        result = self._run()
        assert "machines" in result
        assert isinstance(result["machines"], list)

    def test_three_machines(self):
        result = self._run()
        assert len(result["machines"]) == 3

    def test_machine_names_present(self):
        result = self._run()
        names = [m["name"] for m in result["machines"]]
        assert "M5" in names
        assert "TC1" in names
        assert "TC2" in names

    def test_m5_is_online(self):
        result = self._run()
        m5 = next(m for m in result["machines"] if m["name"] == "M5")
        assert m5["online"] is True

    def test_all_required_fields_present(self):
        result = self._run()
        required = {"name", "host", "online", "cpu_pct", "ram_pct",
                    "ram_used_gb", "ram_total_gb", "cpu_temp",
                    "disk_pct", "load1", "uptime"}
        for machine in result["machines"]:
            missing = required - set(machine.keys())
            assert not missing, f"{machine['name']} missing fields: {missing}"

    def test_m5_host_address(self):
        result = self._run()
        m5 = next(m for m in result["machines"] if m["name"] == "M5")
        assert m5["host"] == "10.0.0.10"


# ---------------------------------------------------------------------------
# collect — SSH failure path
# ---------------------------------------------------------------------------


class TestSshFailure:
    def _run_with_ssh_fail(self):
        """TC1/TC2 SSH returns failure."""
        def fake_collect_ssh(host_def):
            return {
                "name": host_def["name"], "host": host_def["host"],
                "online": False,
                "cpu_pct": None, "ram_pct": None, "ram_used_gb": None,
                "ram_total_gb": None, "cpu_temp": None,
                "disk_pct": None, "load1": None, "uptime": None,
            }

        with (
            patch.object(fleet, "_collect_local", return_value={
                "name": "M5", "host": "10.0.0.10", "online": True,
                "cpu_pct": 5.0, "ram_pct": 30.0, "ram_used_gb": 38.0,
                "ram_total_gb": 128.0, "cpu_temp": 48.0,
                "disk_pct": 18.0, "load1": 0.3, "uptime": "10d 2h",
            }),
            patch.object(fleet, "_collect_ssh", side_effect=fake_collect_ssh),
        ):
            return collect(config=_DEFAULT_CONFIG)

    def test_offline_machines_have_online_false(self):
        result = self._run_with_ssh_fail()
        for m in result["machines"]:
            if m["name"] != "M5":
                assert m["online"] is False, f"{m['name']} should be offline"

    def test_offline_stats_are_none(self):
        result = self._run_with_ssh_fail()
        for m in result["machines"]:
            if m["name"] != "M5":
                assert m["cpu_pct"] is None
                assert m["ram_pct"] is None
                assert m["uptime"] is None

    def test_m5_still_online_when_ssh_fails(self):
        result = self._run_with_ssh_fail()
        m5 = next(m for m in result["machines"] if m["name"] == "M5")
        assert m5["online"] is True


# ---------------------------------------------------------------------------
# _collect_ssh — unit tests (subprocess mocked)
# ---------------------------------------------------------------------------


class TestCollectSsh:
    _HOST = {"name": "TC1", "host": "10.0.0.11", "user": "testuser"}

    def test_parses_numeric_fields(self):
        with patch("llamawatch.collectors.fleet.subprocess.run",
                   return_value=_make_ssh_result()):
            result = fleet._collect_ssh(self._HOST)

        assert result["online"] is True
        assert result["cpu_pct"] == 12.5
        assert result["ram_pct"] == 45.0
        assert result["ram_used_gb"] == 7.2
        assert result["ram_total_gb"] == 16.0
        assert result["cpu_temp"] == 52.3
        assert result["disk_pct"] == 38.0
        assert result["load1"] == 0.8
        assert result["uptime"] == "2d 4h"

    def test_non_zero_returncode_yields_offline(self):
        with patch("llamawatch.collectors.fleet.subprocess.run",
                   return_value=_make_ssh_result(stdout="", returncode=255)):
            result = fleet._collect_ssh(self._HOST)

        assert result["online"] is False
        assert result["cpu_pct"] is None

    def test_timeout_yields_offline(self):
        with patch("llamawatch.collectors.fleet.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=4)):
            result = fleet._collect_ssh(self._HOST)

        assert result["online"] is False
        assert result["ram_pct"] is None

    def test_exception_yields_offline(self):
        with patch("llamawatch.collectors.fleet.subprocess.run",
                   side_effect=OSError("connection refused")):
            result = fleet._collect_ssh(self._HOST)

        assert result["online"] is False

    def test_name_and_host_always_set(self):
        with patch("llamawatch.collectors.fleet.subprocess.run",
                   side_effect=OSError("network unreachable")):
            result = fleet._collect_ssh(self._HOST)

        assert result["name"] == "TC1"
        assert result["host"] == "10.0.0.11"

    def test_ssh_command_includes_batch_mode_and_timeout(self):
        """subprocess.run is called with ConnectTimeout and BatchMode flags."""
        with patch("llamawatch.collectors.fleet.subprocess.run",
                   return_value=_make_ssh_result()) as mock_run:
            fleet._collect_ssh(self._HOST)

        cmd = mock_run.call_args[0][0]
        cmd_str = " ".join(cmd)
        assert "BatchMode=yes" in cmd_str
        assert "ConnectTimeout=3" in cmd_str
        assert "10.0.0.11" in cmd_str

    def test_ssh_uses_4s_subprocess_timeout(self):
        """subprocess.run is called with timeout=4."""
        with patch("llamawatch.collectors.fleet.subprocess.run",
                   return_value=_make_ssh_result()) as mock_run:
            fleet._collect_ssh(self._HOST)

        kwargs = mock_run.call_args[1]
        assert kwargs.get("timeout") == 4


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


def _fake_ssh(host_def):
    return {
        "name": host_def["name"], "host": host_def["host"], "online": True,
        "cpu_pct": 12.5, "ram_pct": 45.0, "ram_used_gb": 7.2,
        "ram_total_gb": 16.0, "cpu_temp": 52.3,
        "disk_pct": 38.0, "load1": 0.8, "uptime": "2d 4h",
    }


_LOCAL_RESULT = {
    "name": "M5", "host": "10.0.0.10", "online": True,
    "cpu_pct": 5.0, "ram_pct": 30.0, "ram_used_gb": 38.0,
    "ram_total_gb": 128.0, "cpu_temp": 48.0,
    "disk_pct": 18.0, "load1": 0.3, "uptime": "10d 2h",
}


class TestCache:
    def test_cache_prevents_second_ssh_call(self):
        """A second collect() within the TTL window reuses cached data."""
        with (
            patch.object(fleet, "_collect_local", return_value=_LOCAL_RESULT) as mock_local,
            patch.object(fleet, "_collect_ssh", side_effect=_fake_ssh) as mock_ssh,
        ):
            collect(config=_DEFAULT_CONFIG)
            collect(config=_DEFAULT_CONFIG)  # should hit cache

        # local is called for M5 in first pass only
        assert mock_local.call_count == 1
        # SSH called for TC1 + TC2 in first pass, then cached
        assert mock_ssh.call_count == 2

    def test_cache_expires_after_ttl(self):
        """collect() re-runs SSH after the TTL has elapsed."""
        with (
            patch.object(fleet, "_collect_local", return_value=_LOCAL_RESULT) as mock_local,
            patch.object(fleet, "_collect_ssh", side_effect=_fake_ssh) as mock_ssh,
        ):
            collect(config=_DEFAULT_CONFIG)
            # Expire the cache
            fleet._cache["ts"] = 0.0
            collect(config=_DEFAULT_CONFIG)

        assert mock_local.call_count == 2
        assert mock_ssh.call_count == 4


# ---------------------------------------------------------------------------
# Config-driven host list
# ---------------------------------------------------------------------------


class TestConfigHosts:
    def test_custom_hosts_from_config(self):
        """When config supplies fleet.hosts, those are used instead of defaults."""
        custom_hosts = [
            {"name": "MyBox", "host": "10.0.0.1", "local": False, "user": "admin"},
        ]
        cfg = {"fleet": {"hosts": custom_hosts}}

        with patch.object(fleet, "_collect_ssh", return_value={
            "name": "MyBox", "host": "10.0.0.1", "online": True,
            "cpu_pct": 3.0, "ram_pct": 20.0, "ram_used_gb": 2.0,
            "ram_total_gb": 8.0, "cpu_temp": None,
            "disk_pct": 10.0, "load1": 0.1, "uptime": "1h 5m",
        }):
            result = collect(config=cfg)

        assert len(result["machines"]) == 1
        assert result["machines"][0]["name"] == "MyBox"

    def test_no_fleet_config_falls_back_to_single_local(self):
        """With no fleet config, get_fleet_hosts() yields one auto-detected
        local machine, so collect() returns exactly that machine."""
        fallback_hosts = [
            {"name": "auto-host", "local": True, "user": "tester"},
        ]
        with (
            patch.object(fleet, "_collect_local", return_value={
                "name": "auto-host", "host": "localhost", "online": True,
                "cpu_pct": 1.0, "ram_pct": 10.0, "ram_used_gb": 1.0,
                "ram_total_gb": 128.0, "cpu_temp": 40.0,
                "disk_pct": 5.0, "load1": 0.1, "uptime": "1d 0h",
            }) as mock_local,
            patch.object(fleet, "_collect_ssh", side_effect=_fake_ssh) as mock_ssh,
            patch("llamawatch.config.get_fleet_hosts", return_value=fallback_hosts),
        ):
            result = collect(config={})

        assert len(result["machines"]) == 1
        assert result["machines"][0]["name"] == "auto-host"
        assert mock_local.call_count == 1
        assert mock_ssh.call_count == 0


# ---------------------------------------------------------------------------
# collect() accepts all keyword args (registry compat)
# ---------------------------------------------------------------------------


def test_collect_accepts_all_kwargs():
    with (
        patch.object(fleet, "_collect_local", return_value={
            "name": "M5", "host": "10.0.0.10", "online": True,
            "cpu_pct": 1.0, "ram_pct": 10.0, "ram_used_gb": 1.0,
            "ram_total_gb": 128.0, "cpu_temp": 40.0,
            "disk_pct": 5.0, "load1": 0.1, "uptime": "1d 0h",
        }),
        patch.object(fleet, "_collect_ssh", side_effect=_fake_ssh),
    ):
        result = collect(config=_DEFAULT_CONFIG, adapters=None, widget_config={})

    assert "machines" in result
