import pytest
from llamawatch import auth


def test_hash_returns_argon2():
    h = auth.hash_password("correct horse")
    assert h.startswith("$argon2")


def test_hash_is_salted_unique():
    assert auth.hash_password("x") != auth.hash_password("x")


def test_verify_password_against_config(monkeypatch):
    h = auth.hash_password("s3cret")
    monkeypatch.setattr(auth, "load_config", lambda: {"auth_password_hash": h})
    assert auth.verify_password("s3cret") is True
    assert auth.verify_password("wrong") is False


def test_verify_false_when_no_hash(monkeypatch):
    monkeypatch.setattr(auth, "load_config", lambda: {})
    assert auth.verify_password("anything") is False
