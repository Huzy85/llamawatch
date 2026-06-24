"""Connections registry: define a host/endpoint once, reference by id from widgets."""
from .config import SECRET_KEYS

_TYPES = {
    "llm_backend": {"label": "LLM backend", "fields": [
        {"key": "url", "label": "URL", "type": "text", "required": True},
        {"key": "kind", "label": "Kind", "type": "select",
         "options": ["llamacpp", "ollama", "openai", "vllm", "lmstudio"], "required": True},
        {"key": "api_key", "label": "API key", "type": "password", "required": False, "secret": True},
    ]},
    "ssh_host": {"label": "SSH host", "fields": [
        {"key": "host", "label": "Host", "type": "text", "required": True},
        {"key": "port", "label": "Port", "type": "number", "required": False},
        {"key": "user", "label": "User", "type": "text", "required": True},
        {"key": "key_path", "label": "Key path", "type": "text", "required": False},
        {"key": "password", "label": "Password", "type": "password", "required": False, "secret": True},
    ]},
    "chromadb": {"label": "ChromaDB", "fields": [
        {"key": "url", "label": "URL", "type": "text", "required": True},
    ]},
    "http_endpoint": {"label": "HTTP endpoint", "fields": [
        {"key": "url", "label": "URL", "type": "text", "required": True},
        {"key": "api_key", "label": "API key", "type": "password", "required": False, "secret": True},
    ]},
    "database": {"label": "Database", "fields": [
        {"key": "dsn", "label": "SQLite path or Postgres DSN", "type": "text", "required": True},
    ]},
    "imap_smtp": {"label": "Email (IMAP/SMTP)", "fields": [
        {"key": "imap_host", "label": "IMAP host", "type": "text", "required": True},
        {"key": "smtp_host", "label": "SMTP host", "type": "text", "required": False},
        {"key": "user", "label": "User", "type": "text", "required": True},
        {"key": "password", "label": "Password", "type": "password", "required": False, "secret": True},
    ]},
}


def connection_types() -> dict:
    return _TYPES


def validate(c: dict):
    t = c.get("type")
    if t not in _TYPES:
        return False, f"unknown connection type: {t!r}"
    for field in _TYPES[t]["fields"]:
        if field.get("required") and not c.get(field["key"]):
            return False, f"missing required field: {field['key']}"
    return True, ""


def resolve(config: dict, conn_id: str) -> dict:
    conns = config.get("connections", {})
    if conn_id not in conns:
        raise KeyError(conn_id)
    return dict(conns[conn_id])


def list_redacted(config: dict) -> dict:
    out = {}
    for cid, c in config.get("connections", {}).items():
        out[cid] = {k: ("[REDACTED]" if k in SECRET_KEYS else v) for k, v in c.items()}
    return out
