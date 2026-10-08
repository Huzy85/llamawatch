"""Services added from Settings must report real status, same as ones from init."""
from types import SimpleNamespace
from unittest import mock

import llamawatch.collectors.services as services


def _run(returncode=0, stdout=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")


def test_settings_systemd_type_is_checked_as_user_unit():
    svcs = [{"name": "web", "type": "systemd", "unit": "web.service"}]
    with mock.patch.object(services, "get_services", return_value=svcs), \
         mock.patch.object(services.subprocess, "run", return_value=_run(stdout="active\n")) as run:
        out = services.collect_services()
    assert out[0]["status"] == "active"
    assert run.call_args[0][0] == ["systemctl", "--user", "is-active", "web.service"]


def test_process_type_uses_exact_pgrep():
    svcs = [{"name": "worker", "type": "process", "unit": "worker"}]
    with mock.patch.object(services, "get_services", return_value=svcs), \
         mock.patch.object(services.subprocess, "run", return_value=_run(returncode=1)) as run:
        out = services.collect_services()
    assert out[0]["status"] == "inactive"
    assert run.call_args[0][0] == ["pgrep", "-x", "worker"]


def test_health_url_key_from_settings_is_used():
    svcs = [{"name": "api", "type": "systemd", "unit": "api.service",
             "health_url": "http://127.0.0.1:9/health"}]
    with mock.patch.object(services, "get_services", return_value=svcs), \
         mock.patch.object(services.subprocess, "run", return_value=_run(stdout="active\n")), \
         mock.patch.object(services, "urlopen", side_effect=OSError("refused")):
        out = services.collect_services()
    assert out[0]["status"] == "degraded"
