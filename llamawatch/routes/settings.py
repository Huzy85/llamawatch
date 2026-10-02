"""Settings API: config read/write, quick actions, backend test, discovery."""

import asyncio
import json
import os
import subprocess

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import srv
from .. import security
from ..auth import is_auth_enabled, hash_password
from ..config import (
    load_config, reload_config, redact_config, get_config_dir,
    _deep_merge, encrypt_secrets,
)

router = APIRouter()


def _get_version() -> str:
    try:
        from llamawatch import __version__
        return __version__
    except Exception:
        return "0.4.0-dev"


@router.get("/api/settings")
async def get_settings():
    """Return current config with credentials redacted, plus version."""
    cfg = srv._config or load_config()
    redacted = redact_config(cfg)
    redacted["_version"] = _get_version()
    return redacted


@router.put("/api/settings")
async def put_settings(request: Request):
    """Update config.local.json with the provided overrides."""
    if not security.action_allowed(request, is_auth_enabled()):
        return JSONResponse(status_code=403, content={"status": "error", "message": "not permitted from this client"})
    body = await request.json()

    config_dir = get_config_dir()
    local_path = config_dir / "config.local.json"

    # Read existing local overrides
    if local_path.is_file():
        existing = json.loads(local_path.read_text())
    else:
        existing = {}

    # Handle auth_password: hash it and store as auth_password_hash (never plaintext)
    if "auth_password" in body:
        pw = body.pop("auth_password")
        body["auth_password_hash"] = hash_password(pw)

    # Dict-valued keys that the editor sends in full — replace wholesale so
    # removed entries actually disappear (deep-merge would keep stale keys).
    for replace_key in ("model_names", "container_descriptions", "topology_edges"):
        if replace_key in body:
            existing.pop(replace_key, None)

    # Deep merge body into existing (credentials are now kept and encrypted below)
    merged = _deep_merge(existing, body)

    # Encrypt credential values at rest before writing to disk
    merged = encrypt_secrets(merged)

    # Write with restricted permissions
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_text(json.dumps(merged, indent=2))
    os.chmod(str(local_path), 0o600)

    # Reload and propagate
    new_config = reload_config(config_dir=config_dir)
    srv._config = new_config
    if srv._adapters is not None:
        srv._adapters.rebuild(new_config)
    if srv._collector_registry is not None:
        srv._collector_registry.refresh_enabled(new_config)

    return {"status": "ok"}


@router.post("/api/settings/test-backend")
async def test_backend(request: Request):
    """Test connectivity to a backend URL (server-side fetch — gated to prevent SSRF)."""
    if not security.action_allowed(request, is_auth_enabled()):
        return JSONResponse(status_code=403, content={"status": "error", "message": "not permitted from this client"})
    body = await request.json()
    backend_config = {
        "url": body.get("url", ""),
        "type": body.get("type", "openai"),
        "name": body.get("name", "test"),
    }
    try:
        adapter = srv.create_adapter(backend_config, srv._config or {})
        health_status = adapter.health()
        model = adapter.model_name()
        return {"status": health_status, "model": model}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.post("/api/settings/discover")
async def discover():
    """Run auto-detection scans and return results."""
    loop = asyncio.get_running_loop()
    backends = await loop.run_in_executor(None, srv.scan_backends)
    sensors = await loop.run_in_executor(None, srv.scan_sensors)
    services = await loop.run_in_executor(None, srv.scan_services)
    return {"backends": backends, "services": services, "sensors": sensors}


@router.get("/api/settings/options/{key}")
async def settings_options(key: str):
    """Return available options for a given settings key."""
    if key == "discovered_timers":
        try:
            result = subprocess.run(
                ["systemctl", "--user", "list-timers", "--no-pager", "--plain"],
                capture_output=True, text=True, timeout=5,
            )
            timers = []
            for line in result.stdout.strip().split("\n"):
                line = line.strip()
                if line.endswith(".timer"):
                    timers.append(line.replace(".timer", ""))
                else:
                    # Parse multi-column output — look for .timer in any field
                    for part in line.split():
                        if part.endswith(".timer"):
                            timers.append(part.replace(".timer", ""))
                            break
            return {"options": timers}
        except Exception:
            return {"options": []}
    return {"options": []}
