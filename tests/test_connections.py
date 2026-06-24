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
    cfg = {"connections": {"m5": {"type": "ssh_host", "host": "10.0.0.10", "user": "me", "password": "pw"}}}
    r = conn.resolve(cfg, "m5")
    assert r["host"] == "10.0.0.10" and r["password"] == "pw"


def test_resolve_unknown_id_raises():
    with pytest.raises(KeyError):
        conn.resolve({"connections": {}}, "missing")


def test_redacted_list_hides_secrets():
    cfg = {"connections": {"m5": {"type": "ssh_host", "host": "h", "user": "u", "password": "pw"}}}
    listed = conn.list_redacted(cfg)
    assert listed["m5"]["password"] == "[REDACTED]" and listed["m5"]["host"] == "h"
