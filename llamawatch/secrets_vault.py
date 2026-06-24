"""Encrypt/decrypt secrets at rest with a Fernet master key.

Master key resolution order:
  1. LLAMAWATCH_SECRET_KEY env var (a urlsafe base64 Fernet key)
  2. ~/.config/llamawatch/secret.key (chmod 600, auto-generated if absent)

Encrypted values are stored as the string "enc:<token>". Plain values pass
through decrypt() unchanged, so existing plaintext configs keep working and get
upgraded the next time they are written.
"""
import os
from pathlib import Path
from cryptography.fernet import Fernet

_PREFIX = "enc:"
_KEY_FILE = Path.home() / ".config" / "llamawatch" / "secret.key"
_fernet = None


def _reset_key_cache():
    global _fernet
    _fernet = None


def _load_key() -> bytes:
    env = os.environ.get("LLAMAWATCH_SECRET_KEY")
    if env:
        return env.encode()
    if _KEY_FILE.is_file():
        return _KEY_FILE.read_bytes().strip()
    _KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    _KEY_FILE.write_bytes(key)
    os.chmod(_KEY_FILE, 0o600)
    return key


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_key())
    return _fernet


def is_encrypted(value) -> bool:
    return isinstance(value, str) and value.startswith(_PREFIX)


def encrypt(value: str) -> str:
    token = _get_fernet().encrypt(value.encode()).decode()
    return _PREFIX + token


def decrypt(value: str) -> str:
    if not is_encrypted(value):
        return value
    token = value[len(_PREFIX):]
    try:
        return _get_fernet().decrypt(token.encode()).decode()
    except Exception:
        # Key lost/rotated or value corrupt — don't crash startup. The secret
        # is simply unavailable; the feature using it degrades to "not set".
        return ""
