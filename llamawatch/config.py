"""Config loader for llamawatch dashboard.

Two-file system: config.json (defaults) + config.local.json (user overrides).
Files are deep-merged, then environment variables are applied on top.
"""

import copy
import json
import os
from pathlib import Path

from . import secrets_vault as _sv

_config: dict | None = None
_config_dir: Path | None = None


_CREDENTIAL_KEYS = frozenset({"password", "api_key", "auth_password_hash", "secret", "token", "key_passphrase"})

SECRET_KEYS = frozenset({"password", "api_key", "secret", "token", "key_passphrase"})


def _walk_secrets(obj, fn):
    """Recursively walk obj, applying fn to string values whose key is in SECRET_KEYS."""
    if isinstance(obj, dict):
        return {k: (fn(v) if k in SECRET_KEYS and isinstance(v, str) else _walk_secrets(v, fn))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk_secrets(v, fn) for v in obj]
    return obj


def encrypt_secrets(obj):
    """Return a copy of obj with all SECRET_KEYS values Fernet-encrypted."""
    return _walk_secrets(obj, lambda v: v if _sv.is_encrypted(v) else _sv.encrypt(v))


def decrypt_secrets(obj):
    """Return a copy of obj with all enc:-prefixed SECRET_KEYS values decrypted."""
    return _walk_secrets(obj, _sv.decrypt)


def _deep_merge(base: dict, override: dict, skip_credentials: bool = False) -> dict:
    """Deep-merge override into base. Dicts are merged recursively, lists are replaced.

    If skip_credentials is True, credential keys (password, api_key, etc.) in
    override are ignored — the base value is preserved.
    """
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if skip_credentials and key in _CREDENTIAL_KEYS:
            continue
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value, skip_credentials=skip_credentials)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


_PACKAGE_DIR = Path(__file__).resolve().parent


def _find_config_dir() -> Path:
    """Locate the directory holding user config.

    A directory qualifies if it contains EITHER config.local.json (user
    overrides, written by `init` and the settings UI) OR config.json (the
    base template). This matters for pip installs: `init` writes
    config.local.json to ~/.config/llamawatch/ which has no config.json —
    the base template is loaded from the package instead (see _load_and_merge).

    Order: CWD, ~/.config/llamawatch/, package dir.
    """
    candidates = [
        Path.cwd(),
        Path.home() / ".config" / "llamawatch",
        _PACKAGE_DIR,
    ]
    for candidate in candidates:
        if (candidate / "config.local.json").is_file() or (candidate / "config.json").is_file():
            return candidate
    # Fall back to package dir (ships the base config.json)
    return _PACKAGE_DIR


def _apply_env_overrides(cfg: dict) -> dict:
    """Apply LLAMAWATCH_* environment variables over config."""
    env_map: dict[str, tuple[str, type]] = {
        "LLAMAWATCH_PORT": ("port", int),
        "LLAMAWATCH_HOST": ("host", str),
        "LLAMAWATCH_AUTH": ("auth_enabled", bool),
    }
    for env_var, (key, cast) in env_map.items():
        value = os.environ.get(env_var)
        if value is not None:
            if cast is bool:
                cfg[key] = value.lower() in ("1", "true", "yes")
            elif cast is int:
                try:
                    cfg[key] = int(value)
                except ValueError:
                    raise ValueError(f"Invalid integer for {env_var}: {value!r}")
            else:
                cfg[key] = value
    return cfg


def _load_and_merge(config_dir: Path) -> dict:
    """Load base config.json, merge config.local.json overrides, apply env.

    The base template is read from config_dir if present, otherwise from the
    package (so a user dir holding only config.local.json still gets the
    shipped defaults). Overrides are read from config_dir/config.local.json.
    """
    config_path = config_dir / "config.json"
    if not config_path.is_file():
        config_path = _PACKAGE_DIR / "config.json"
    try:
        with open(config_path) as f:
            cfg = json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Base config not found: {config_path}.")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {config_path}: {e}")

    local_path = config_dir / "config.local.json"
    if local_path.is_file():
        try:
            with open(local_path) as f:
                local_cfg = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in {local_path}: {e}")
        cfg = _deep_merge(cfg, local_cfg)

    cfg = _apply_env_overrides(cfg)
    cfg = decrypt_secrets(cfg)
    return cfg


def load_config(cli_overrides: dict | None = None, config_dir: Path | str | None = None) -> dict:
    """Load and cache config. Optional cli_overrides are deep-merged last.

    If config_dir is provided, use that directory instead of auto-detecting.
    """
    global _config, _config_dir
    if _config is None:
        if config_dir is not None:
            _config_dir = Path(config_dir)
        else:
            _config_dir = _find_config_dir()
        _config = _load_and_merge(_config_dir)
    if cli_overrides:
        _config = _deep_merge(_config, cli_overrides)
    return _config


def reset_config() -> None:
    """Clear cached config. Used for testing."""
    global _config, _config_dir
    _config = None
    _config_dir = None


def get_config_dir() -> Path:
    """Return the directory where config was loaded from."""
    if _config_dir is None:
        load_config()
    if _config_dir is None:
        raise RuntimeError("Config not loaded — call load_config() first")
    return _config_dir


def get_services() -> list[dict]:
    """Return the list of configured services."""
    return load_config().get("services", [])


def get_service(name: str) -> dict | None:
    """Look up a service by name."""
    for svc in get_services():
        if svc["name"] == name:
            return svc
    return None


def get_fleet_hosts() -> list[dict]:
    """Return the configured fleet hosts.

    Falls back to a single auto-detected local machine (named from the
    hostname) when no fleet is configured — so a fresh install shows the
    machine it's running on with zero setup.
    """
    import socket
    fleet_cfg = load_config().get("fleet", {})
    hosts = fleet_cfg.get("hosts")
    if hosts:
        return hosts
    name = socket.gethostname().split(".")[0] or "localhost"
    return [{"name": name, "local": True, "user": os.getenv("USER", "user")}]


def get_remote_fleet_hosts() -> list[dict]:
    """Return only the non-local fleet hosts (those reached over SSH)."""
    return [h for h in get_fleet_hosts() if not h.get("local")]


def get_model_friendly_name(model_id: str) -> str:
    """Map a model ID to its friendly name, or return the ID unchanged."""
    names = load_config().get("model_names", {})
    return names.get(model_id, model_id)


def get_model_id(friendly_name: str) -> str:
    """Reverse lookup: friendly name -> model ID (inverse of get_model_friendly_name)."""
    names = load_config().get("model_names", {})
    for model_id, name in names.items():
        if name.lower() == friendly_name.lower():
            return model_id
    return friendly_name


def reload_config(config_dir: Path | str | None = None) -> dict:
    """Clear cached config and re-read from disk. Return new config."""
    reset_config()
    return load_config(config_dir=config_dir)


def redact_config(config: dict) -> dict:
    """Deep-copy config, replacing credential fields with '[REDACTED]'.

    Walks dicts and lists recursively. Credential keys:
    password, api_key, auth_password_hash, secret.
    """
    import re as _re
    # Scrub credentials embedded in URL/DSN strings, e.g.
    # postgresql://user:secret@host/db  →  postgresql://user:[REDACTED]@host/db
    _dsn_cred = _re.compile(r"(://[^:/@\s]+:)[^@/\s]+(@)")

    def _redact(obj):
        if isinstance(obj, dict):
            return {
                k: "[REDACTED]" if k in _CREDENTIAL_KEYS else _redact(v)
                for k, v in obj.items()
            }
        if isinstance(obj, list):
            return [_redact(item) for item in obj]
        if isinstance(obj, str):
            return _dsn_cred.sub(r"\1[REDACTED]\2", obj)
        return obj

    return _redact(copy.deepcopy(config))


def get_widget_config(config: dict, instance_id: str) -> dict:
    """Return config['widgets']['config'][instance_id] or empty dict."""
    try:
        return config["widgets"]["config"][instance_id]
    except (KeyError, TypeError):
        return {}
