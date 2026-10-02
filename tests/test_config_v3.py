"""Tests for config hot-reload, credential redaction, env vars, deep merge."""

import json
from pathlib import Path


def test_reload_config_picks_up_changes(tmp_path):
    from llamawatch.config import load_config, reload_config, reset_config
    reset_config()
    base = tmp_path / "config.json"
    base.write_text(json.dumps({"port": 8400}))
    local = tmp_path / "config.local.json"
    local.write_text(json.dumps({"port": 9999}))
    config = load_config(config_dir=tmp_path)
    assert config["port"] == 9999
    local.write_text(json.dumps({"port": 7777}))
    config = reload_config(config_dir=tmp_path)
    assert config["port"] == 7777


def test_redact_credentials():
    from llamawatch.config import redact_config
    config = {
        "auth_password_hash": "$2b$12$abc",
        "port": 8400,
        "email": {"password": "secret123", "imap_host": "mail.example.com"},
        "backends": [{"type": "openai", "api_key": "sk-abc123", "url": "http://localhost"}]
    }
    redacted = redact_config(config)
    assert redacted["auth_password_hash"] == "[REDACTED]"
    assert redacted["email"]["password"] == "[REDACTED]"
    assert redacted["email"]["imap_host"] == "mail.example.com"
    assert redacted["backends"][0]["api_key"] == "[REDACTED]"
    assert redacted["backends"][0]["url"] == "http://localhost"


def test_get_widget_config():
    from llamawatch.config import get_widget_config
    config = {"widgets": {"config": {"weather": {"lat": 51.5}, "logs:abc": {"sources": []}}}}
    assert get_widget_config(config, "weather") == {"lat": 51.5}
    assert get_widget_config(config, "logs:abc") == {"sources": []}
    assert get_widget_config(config, "nonexistent") == {}


def test_env_var_overrides(tmp_path, monkeypatch):
    from llamawatch.config import load_config, reset_config
    reset_config()
    base = tmp_path / "config.json"
    base.write_text(json.dumps({"port": 8400, "host": "0.0.0.0"}))
    monkeypatch.setenv("LLAMAWATCH_PORT", "9999")
    config = load_config(config_dir=tmp_path)
    assert config["port"] == 9999


def test_deep_merge():
    from llamawatch.config import _deep_merge
    base = {"a": 1, "b": {"c": 2, "d": 3}}
    override = {"b": {"c": 99, "e": 4}, "f": 5}
    result = _deep_merge(base, override)
    assert result == {"a": 1, "b": {"c": 99, "d": 3, "e": 4}, "f": 5}


def test_deep_merge_skips_credentials():
    from llamawatch.config import _deep_merge
    base = {"password": "old", "port": 8400}
    override = {"password": "new", "port": 9999}
    result = _deep_merge(base, override, skip_credentials=True)
    assert result["password"] == "old"
    assert result["port"] == 9999
