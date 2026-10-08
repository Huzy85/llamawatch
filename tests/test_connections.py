"""Tests for the connections registry module."""
import pytest
from llamawatch import connections as conn


def test_known_types_have_schemas():
    types = conn.connection_types()
    assert "ssh_host" in types and "llm_backend" in types
    keys = {f["key"] for f in types["ssh_host"]["fields"]}
    assert {"host", "user"}.issubset(keys)


def test_validate_rejects_unknown_type():
    ok, err = conn.validate({"type": "nonsense"})
    assert ok is False and "type" in err.lower()


def test_validate_requires_mandatory_fields():
    ok, err = conn.validate({"type": "ssh_host", "user": "me"})  # missing host
    assert ok is False and "host" in err.lower()


def test_resolve_returns_value():
    cfg = {"connections": {"node-a": {"type": "ssh_host", "host": "192.0.2.100", "user": "me", "password": "pw"}}}
    r = conn.resolve(cfg, "node-a")
    assert r["host"] == "192.0.2.100" and r["password"] == "pw"


def test_resolve_unknown_id_raises():
    with pytest.raises(KeyError):
        conn.resolve({"connections": {}}, "missing")


def test_redacted_list_hides_secrets():
    cfg = {"connections": {"node-a": {"type": "ssh_host", "host": "h", "user": "u", "password": "pw"}}}
    listed = conn.list_redacted(cfg)
    assert listed["node-a"]["password"] == "[REDACTED]" and listed["node-a"]["host"] == "h"
