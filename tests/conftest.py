import os
import shutil
import tempfile
from pathlib import Path

import pytest

pytest_plugins = ["pytest_asyncio"]


_FAKE_HOME = None


def _isolate_home():
    """Run the whole test session under a throwaway home folder.

    llamawatch keeps its audit log, sessions, page-made jobs and research
    runs under the home folder, and falls back to ~/.config/llamawatch for
    config when the working folder has none. Without this, a test (or a
    server the UI tests start) could write into the real ones.
    """
    real = Path.home()
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(real / ".cache" / "ms-playwright"))
    os.environ.setdefault("PYTHONUSERBASE", str(real / ".local"))
    global _FAKE_HOME
    fake = Path(tempfile.mkdtemp(prefix="llamawatch-test-home-"))
    _FAKE_HOME = fake
    os.environ["HOME"] = str(fake)
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "CLAUDE_CONFIG_DIR", "CODEX_HOME"):
        os.environ.pop(var, None)


def pytest_configure(config):
    _isolate_home()
    config.option.asyncio_mode = "auto"
    # The in-process test clients present a non-loopback host
    # (fastapi TestClient → "testclient"). Treat it as local so the
    # secure-by-default localhost gate doesn't block endpoint-logic tests.
    # Tests that specifically assert the gate blocks remote hosts use an
    # explicit non-local client (e.g. 203.0.113.5), which stays blocked.
    import llamawatch.security as _sec
    _sec._LOCAL_HOSTS = set(_sec._LOCAL_HOSTS) | {"testclient"}


def pytest_unconfigure(config):
    if _FAKE_HOME is not None:
        shutil.rmtree(_FAKE_HOME, ignore_errors=True)
