"""Tests for the two-file config system."""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure the project root is importable

import llamawatch.config as config


@pytest.fixture(autouse=True)
def _reset():
    """Reset config cache and clean env vars between tests."""
    config.reset_config()
    for var in ("LLAMAWATCH_PORT", "LLAMAWATCH_HOST", "LLAMAWATCH_AUTH"):
        os.environ.pop(var, None)
    yield
    config.reset_config()
    for var in ("LLAMAWATCH_PORT", "LLAMAWATCH_HOST", "LLAMAWATCH_AUTH"):
        os.environ.pop(var, None)


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data))


def test_config_loads_defaults():
    """config.json alone produces a valid config with expected default keys."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        defaults = {
            "port": 8400,
            "host": "0.0.0.0",
            "auth_enabled": False,
            "backends": [],
            "widgets": {"enabled": ["model-status", "system"], "layout": None},
            "services": [],
            "sensors": "auto",
            "model_names": {},
        }
        _write_json(tmp_path / "config.json", defaults)

        os.chdir(tmp)
        cfg = config.load_config()

        assert cfg["port"] == 8400
        assert cfg["host"] == "0.0.0.0"
        assert cfg["auth_enabled"] is False
        assert cfg["services"] == []
        assert cfg["model_names"] == {}
        assert cfg["widgets"]["enabled"] == ["model-status", "system"]


def test_config_local_overrides():
    """config.local.json deep-merges: dicts merged, lists replaced."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        defaults = {
            "port": 8400,
            "auth_enabled": False,
            "widgets": {"enabled": ["a", "b"], "layout": None},
            "services": [],
            "model_names": {},
        }
        overrides = {
            "auth_enabled": True,
            "widgets": {"enabled": ["x", "y", "z"]},
            "services": [{"name": "svc1", "port": 80}],
            "model_names": {"model-a": "Alpha"},
        }
        _write_json(tmp_path / "config.json", defaults)
        _write_json(tmp_path / "config.local.json", overrides)

        os.chdir(tmp)
        cfg = config.load_config()

        # Scalar override
        assert cfg["auth_enabled"] is True
        # List replaced, not appended
        assert cfg["widgets"]["enabled"] == ["x", "y", "z"]
        # Dict key preserved from base (layout still there)
        assert cfg["widgets"]["layout"] is None
        # List replaced entirely
        assert len(cfg["services"]) == 1
        assert cfg["services"][0]["name"] == "svc1"
        # Dict merged
        assert cfg["model_names"]["model-a"] == "Alpha"
        # Base scalar preserved
        assert cfg["port"] == 8400


def test_config_env_override():
    """LLAMAWATCH_PORT env var overrides values from both files."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        defaults = {"port": 8400, "host": "0.0.0.0", "auth_enabled": False}
        overrides = {"port": 9000}
        _write_json(tmp_path / "config.json", defaults)
        _write_json(tmp_path / "config.local.json", overrides)

        os.chdir(tmp)
        os.environ["LLAMAWATCH_PORT"] = "7777"
        cfg = config.load_config()

        # Env var wins over both files
        assert cfg["port"] == 7777
        assert isinstance(cfg["port"], int)


def test_config_env_bool():
    """LLAMAWATCH_AUTH env var converts string to bool."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _write_json(tmp_path / "config.json", {"auth_enabled": False})

        os.chdir(tmp)
        os.environ["LLAMAWATCH_AUTH"] = "true"
        cfg = config.load_config()

        assert cfg["auth_enabled"] is True


def test_config_env_host():
    """LLAMAWATCH_HOST env var overrides host."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _write_json(tmp_path / "config.json", {"host": "0.0.0.0"})

        os.chdir(tmp)
        os.environ["LLAMAWATCH_HOST"] = "127.0.0.1"
        cfg = config.load_config()

        assert cfg["host"] == "127.0.0.1"


def test_deep_merge_nested():
    """Deep merge handles nested dicts correctly."""
    base = {"a": {"b": {"c": 1, "d": 2}, "e": 3}}
    override = {"a": {"b": {"c": 99}}}
    result = config._deep_merge(base, override)

    assert result["a"]["b"]["c"] == 99
    assert result["a"]["b"]["d"] == 2
    assert result["a"]["e"] == 3
    # Original not mutated
    assert base["a"]["b"]["c"] == 1


def test_cli_overrides():
    """CLI overrides are applied on top of everything."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _write_json(tmp_path / "config.json", {"port": 8400, "host": "0.0.0.0"})

        os.chdir(tmp)
        cfg = config.load_config(cli_overrides={"port": 3000})

        assert cfg["port"] == 3000
        assert cfg["host"] == "0.0.0.0"


def test_reset_config():
    """reset_config() clears the cache so next load_config() re-reads files."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _write_json(tmp_path / "config.json", {"port": 8400})

        os.chdir(tmp)
        cfg1 = config.load_config()
        assert cfg1["port"] == 8400

        # Modify file and reset
        _write_json(tmp_path / "config.json", {"port": 9999})
        config.reset_config()
        cfg2 = config.load_config()
        assert cfg2["port"] == 9999
