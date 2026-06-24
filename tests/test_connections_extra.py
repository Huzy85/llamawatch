"""Extra tests for the connections registry."""

import pytest

from llamawatch import connections
from llamawatch.config import SECRET_KEYS


# ── validate ──────────────────────────────────────────────────────────────────

def test_validate_unknown_type():
    ok, err = connections.validate({"type": "totally_unknown"})
    assert ok is False
    assert "unknown connection type" in err


def test_validate_missing_required_field():
    # llm_backend requires 'url' and 'kind'
    ok, err = connections.validate({"type": "llm_backend", "kind": "ollama"})
    assert ok is False
    assert "url" in err


def test_validate_missing_kind():
    ok, err = connections.validate({"type": "llm_backend", "url": "http://localhost:11434"})
    assert ok is False
    assert "kind" in err


def test_validate_all_required_fields_passes():
    ok, err = connections.validate({
        "type": "llm_backend",
        "url": "http://localhost:11434",
        "kind": "ollama",
    })
    assert ok is True
    assert err == ""


def test_validate_optional_fields_not_required():
    # api_key is optional for llm_backend
    ok, _ = connections.validate({
        "type": "llm_backend",
        "url": "http://localhost:8080",
        "kind": "llamacpp",
    })
    assert ok is True


def test_validate_ssh_host_requires_host_and_user():
    ok, err = connections.validate({"type": "ssh_host", "host": "10.0.0.1"})
    assert ok is False
    assert "user" in err


def test_validate_all_types_accept_valid_input():
    valid_cases = [
        {"type": "llm_backend", "url": "http://localhost:8080", "kind": "llamacpp"},
        {"type": "ssh_host", "host": "10.0.0.1", "user": "ubuntu"},
        {"type": "chromadb", "url": "http://localhost:8200"},
        {"type": "http_endpoint", "url": "http://localhost:9000"},
        {"type": "database", "dsn": "/tmp/test.db"},
        {"type": "imap_smtp", "imap_host": "imap.example.com", "user": "u@example.com"},
    ]
    for case in valid_cases:
        ok, err = connections.validate(case)
        assert ok is True, f"Failed for {case['type']}: {err}"


# ── list_redacted ─────────────────────────────────────────────────────────────

def test_list_redacted_hides_password():
    cfg = {
        "connections": {
            "email-1": {"type": "imap_smtp", "imap_host": "imap.example.com",
                        "user": "u", "password": "hunter2"}
        }
    }
    result = connections.list_redacted(cfg)
    assert result["email-1"]["password"] == "[REDACTED]"
    assert result["email-1"]["user"] == "u"


def test_list_redacted_hides_api_key():
    cfg = {
        "connections": {
            "llm-1": {"type": "llm_backend", "url": "http://localhost:8080",
                      "kind": "openai", "api_key": "sk-supersecret"}
        }
    }
    result = connections.list_redacted(cfg)
    assert result["llm-1"]["api_key"] == "[REDACTED]"
    assert result["llm-1"]["url"] == "http://localhost:8080"


def test_list_redacted_empty_connections():
    assert connections.list_redacted({}) == {}
    assert connections.list_redacted({"connections": {}}) == {}


def test_list_redacted_preserves_non_secret_fields():
    cfg = {
        "connections": {
            "db-1": {"type": "database", "dsn": "/data/usage.db"}
        }
    }
    result = connections.list_redacted(cfg)
    assert result["db-1"]["dsn"] == "/data/usage.db"


def test_secret_keys_covers_password_and_api_key():
    """Verify that the fields marked secret in connection types are in SECRET_KEYS."""
    secret_field_names = set()
    for type_def in connections.connection_types().values():
        for field in type_def["fields"]:
            if field.get("secret"):
                secret_field_names.add(field["key"])
    for name in secret_field_names:
        assert name in SECRET_KEYS, f"Secret field '{name}' is not in SECRET_KEYS — it would leak through list_redacted()"


# ── resolve ───────────────────────────────────────────────────────────────────

def test_resolve_returns_copy():
    cfg = {
        "connections": {
            "c1": {"type": "database", "dsn": "/original.db"}
        }
    }
    copy = connections.resolve(cfg, "c1")
    copy["dsn"] = "/mutated.db"
    # Original in config must be unchanged
    assert cfg["connections"]["c1"]["dsn"] == "/original.db"


def test_resolve_raises_keyerror_for_unknown():
    with pytest.raises(KeyError):
        connections.resolve({}, "nonexistent")


def test_resolve_raises_when_no_connections_key():
    with pytest.raises(KeyError):
        connections.resolve({"other": "stuff"}, "any-id")


def test_resolve_returns_correct_connection():
    cfg = {
        "connections": {
            "a": {"type": "database", "dsn": "/a.db"},
            "b": {"type": "database", "dsn": "/b.db"},
        }
    }
    assert connections.resolve(cfg, "a")["dsn"] == "/a.db"
    assert connections.resolve(cfg, "b")["dsn"] == "/b.db"
