import os
from pathlib import Path
import pytest
from llamawatch import secrets_vault as sv


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    monkeypatch.delenv("LLAMAWATCH_SECRET_KEY", raising=False)
    monkeypatch.setattr(sv, "_KEY_FILE", tmp_path / "secret.key")
    sv._reset_key_cache()
    yield


def test_roundtrip_encrypt_decrypt():
    token = sv.encrypt("hunter2")
    assert token.startswith("enc:")
    assert token != "enc:hunter2"
    assert sv.decrypt(token) == "hunter2"


def test_decrypt_passes_through_plaintext():
    assert sv.decrypt("plain-value") == "plain-value"


def test_is_encrypted():
    assert sv.is_encrypted(sv.encrypt("x")) is True
    assert sv.is_encrypted("x") is False


def test_key_file_created_with_strict_perms(tmp_path, monkeypatch):
    monkeypatch.setattr(sv, "_KEY_FILE", tmp_path / "secret.key")
    sv._reset_key_cache()
    sv.encrypt("x")
    mode = (tmp_path / "secret.key").stat().st_mode & 0o777
    assert mode == 0o600


def test_env_key_takes_precedence(monkeypatch):
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("LLAMAWATCH_SECRET_KEY", key)
    sv._reset_key_cache()
    token = sv.encrypt("secret")
    assert sv.decrypt(token) == "secret"
