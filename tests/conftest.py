import pytest

pytest_plugins = ["pytest_asyncio"]


def pytest_configure(config):
    config.option.asyncio_mode = "auto"
    # The in-process test clients present a non-loopback host
    # (fastapi TestClient → "testclient"). Treat it as local so the
    # secure-by-default localhost gate doesn't block endpoint-logic tests.
    # Tests that specifically assert the gate blocks remote hosts use an
    # explicit non-local client (e.g. 203.0.113.5), which stays blocked.
    import llamawatch.security as _sec
    _sec._LOCAL_HOSTS = set(_sec._LOCAL_HOSTS) | {"testclient"}
