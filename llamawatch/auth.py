"""llamawatch — authentication module."""

import json
import secrets
import time
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError

from .config import load_config, get_config_dir

_ph = PasswordHasher()  # argon2id defaults

# Session store: token → expiry timestamp. Persisted to sessions.json.
_sessions: dict[str, float] = {}
_sessions_dirty = False


def _sessions_path() -> Path:
    try:
        return get_config_dir() / "sessions.json"
    except Exception:
        return Path("/tmp/llamawatch-sessions.json")


def _load_sessions():
    global _sessions
    p = _sessions_path()
    if not p.exists():
        return
    try:
        raw = json.loads(p.read_text())
        now = time.time()
        # Discard already-expired sessions on load
        _sessions = {t: exp for t, exp in raw.items() if exp > now}
    except Exception:
        _sessions = {}


def _save_sessions():
    p = _sessions_path()
    try:
        p.write_text(json.dumps(_sessions))
        p.chmod(0o600)
    except Exception:
        pass


# Load persisted sessions at import time
_load_sessions()


def is_auth_enabled() -> bool:
    cfg = load_config()
    return cfg.get("auth_enabled", False)


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str) -> bool:
    cfg = load_config()
    stored_hash = cfg.get("auth_password_hash", "")
    if not stored_hash:
        return False
    try:
        return _ph.verify(stored_hash, password)
    except (VerifyMismatchError, InvalidHashError, Exception):
        return False


def create_session() -> tuple[str, int]:
    """Create a session token. Returns (token, max_age_seconds)."""
    cfg = load_config()
    expiry_days = cfg.get("session_expiry_days", 7)
    max_age = expiry_days * 86400
    token = secrets.token_urlsafe(32)
    _sessions[token] = time.time() + max_age
    _save_sessions()
    return token, max_age


def validate_session(token: str | None) -> bool:
    if not token:
        return False
    expiry = _sessions.get(token)
    if not expiry:
        return False
    if time.time() > expiry:
        _sessions.pop(token, None)
        _save_sessions()
        return False
    return True


def destroy_session(token: str):
    _sessions.pop(token, None)
    _save_sessions()
