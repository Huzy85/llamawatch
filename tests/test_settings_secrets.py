"""Tests for credential encryption and password hashing in PUT /api/settings."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import httpx

import llamawatch.config as config_mod
import llamawatch.secrets_vault as sv


@pytest.fixture(autouse=True)
def _reset_config():
    """Reset config cache between tests."""
    config_mod.reset_config()
    yield
    config_mod.reset_config()


@pytest.fixture(autouse=True)
def _reset_vault_key(tmp_path, monkeypatch):
    """Point secrets_vault at a tmp key file and clear the cache."""
    monkeypatch.setattr(sv, "_KEY_FILE", tmp_path / "secret.key")
    monkeypatch.delenv("LLAMAWATCH_SECRET_KEY", raising=False)
    sv._reset_key_cache()
    yield
    sv._reset_key_cache()


@pytest.fixture()
def tmp_config(tmp_path):
    """Create a temp config dir with config.json and point the module at it."""
    defaults = {
        "port": 8400,
        "host": "0.0.0.0",
        "auth_enabled": False,
        "backends": [{"type": "llamacpp", "url": "http://localhost:8080", "name": "local"}],
        "widgets": {"enabled": ["system"]},
        "services": [],
        "model_names": {},
        "auth_password_hash": "",
        "connections": {},
    }
    (tmp_path / "config.json").write_text(json.dumps(defaults))
    config_mod.reset_config()
    config_mod.load_config(config_dir=tmp_path)
    return tmp_path


@pytest.fixture()
def app_client(tmp_config):
    """Return an httpx.AsyncClient wired to the FastAPI app with mocked state."""
    from llamawatch.server import app

    import llamawatch.server as srv
    srv._config = config_mod.load_config()
    srv._adapters = MagicMock()
    srv._collector_registry = MagicMock()

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ), tmp_config


# ── password hashing ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_password_is_hashed_not_plaintext(app_client):
    """PUT auth_password stores an argon2 hash, never the plaintext."""
    client, tmp_config = app_client
    async with client:
        resp = await client.put("/api/settings", json={"auth_password": "s3cret"})
    assert resp.status_code == 200

    local_path = tmp_config / "config.local.json"
    assert local_path.exists()
    saved = json.loads(local_path.read_text())

    # Plaintext key must be gone
    assert "auth_password" not in saved

    # Hash must be stored as flat key
    assert "auth_password_hash" in saved
    assert saved["auth_password_hash"].startswith("$argon2")


# ── credential encryption ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_credential_is_encrypted_on_disk(app_client):
    """PUT a password in connections — must be Fernet-encrypted on disk."""
    client, tmp_config = app_client
    async with client:
        resp = await client.put(
            "/api/settings",
            json={"connections": {"m5": {"type": "ssh_host", "password": "pw"}}},
        )
    assert resp.status_code == 200

    local_path = tmp_config / "config.local.json"
    saved = json.loads(local_path.read_text())

    stored_pw = saved["connections"]["m5"]["password"]
    assert stored_pw.startswith("enc:"), f"Expected enc: prefix, got: {stored_pw!r}"
    assert sv.decrypt(stored_pw) == "pw"


@pytest.mark.asyncio
async def test_put_api_key_is_encrypted_on_disk(app_client):
    """PUT an api_key must be Fernet-encrypted on disk."""
    client, tmp_config = app_client
    async with client:
        resp = await client.put(
            "/api/settings",
            json={"backends": [{"type": "openai", "url": "http://x", "name": "x", "api_key": "sk-abc"}]},
        )
    assert resp.status_code == 200

    local_path = tmp_config / "config.local.json"
    saved = json.loads(local_path.read_text())

    stored = saved["backends"][0]["api_key"]
    assert stored.startswith("enc:"), f"Expected enc: prefix, got: {stored!r}"
    assert sv.decrypt(stored) == "sk-abc"


@pytest.mark.asyncio
async def test_already_encrypted_value_not_double_encrypted(app_client):
    """If a value is already enc:-prefixed, encrypt_secrets must not double-encrypt it."""
    client, tmp_config = app_client

    # Pre-encrypt a value
    already_enc = sv.encrypt("mypassword")

    async with client:
        resp = await client.put(
            "/api/settings",
            json={"connections": {"host1": {"password": already_enc}}},
        )
    assert resp.status_code == 200

    local_path = tmp_config / "config.local.json"
    saved = json.loads(local_path.read_text())

    stored = saved["connections"]["host1"]["password"]
    # Should still decrypt to original
    assert sv.decrypt(stored) == "mypassword"
    # Should only have one enc: prefix
    assert stored.count("enc:") == 1
