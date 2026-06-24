"""llamawatch — FastAPI server.

This module is the application hub: it owns the ``app`` instance, shared runtime
state (``_config``, ``_adapters``, ``_collector_registry``), the auth middleware,
and a few helpers/dependencies that route modules and tests reference by the
``llamawatch.server.*`` path. Individual route groups live in ``llamawatch.routes.*``
and reach this shared state via ``import llamawatch.server as srv``.
"""

import json
import os
import socket
from pathlib import Path

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, WebSocket
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, RedirectResponse

from .ws_hub import get_hub
from .collectors import CollectorRegistry
from .config import (
    load_config, get_service, get_fleet_hosts, get_remote_fleet_hosts,
)
from .adapters import AdapterRegistry, create_adapter
from .auto_detect import scan_backends, scan_sensors, scan_services
from .auth import is_auth_enabled, validate_session
from . import security
from .request_log import RequestLog

_request_log: RequestLog | None = None


def _get_request_log() -> RequestLog:
    global _request_log
    if _request_log is None:
        _request_log = RequestLog()
    return _request_log

# ── Global state — populated at startup ──────────────────────────────
_adapters: AdapterRegistry | None = None
_collector_registry: CollectorRegistry | None = None
_config: dict | None = None


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _adapters, _collector_registry, _config
    _config = load_config()
    _adapters = AdapterRegistry(_config)
    _collector_registry = CollectorRegistry()
    yield


app = FastAPI(title="llamawatch", docs_url=None, redoc_url=None, lifespan=_lifespan)


# ── Auth middleware ───────────────────────────────────────────────────

_PUBLIC_PATHS = {"/health", "/auth/login", "/auth/status"}
_PUBLIC_PREFIXES = ("/static/", "/auth/")

_LOGIN_PAGE = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>llamawatch — Login</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:'Inter',system-ui,sans-serif;background:#07080c;color:#eef0f4;min-height:100vh;display:flex;align-items:center;justify-content:center}
  .card{background:rgba(255,255,255,0.025);border:1px solid rgba(255,255,255,0.05);border-radius:16px;padding:32px;width:320px;max-width:90%;backdrop-filter:blur(20px)}
  .logo{width:48px;height:48px;border-radius:12px;background:linear-gradient(135deg,#9b7bf7,#6366f1);display:flex;align-items:center;justify-content:center;font-weight:700;font-size:18px;color:white;margin:0 auto 16px;box-shadow:0 0 20px rgba(155,123,247,0.3)}
  h1{text-align:center;font-size:18px;font-weight:600;margin-bottom:24px;color:#a0a8bc}
  input{width:100%;padding:10px 14px;border-radius:8px;border:1px solid rgba(255,255,255,0.05);background:rgba(255,255,255,0.025);color:#eef0f4;font-size:14px;font-family:inherit;outline:none;margin-bottom:12px}
  input:focus{border-color:#9b7bf7}
  button{width:100%;padding:10px;border-radius:8px;border:none;background:linear-gradient(135deg,#9b7bf7,#6366f1);color:white;font-size:14px;font-weight:600;cursor:pointer;font-family:inherit}
  button:hover{opacity:0.9}
  .err{color:#ef5350;font-size:12px;text-align:center;margin-bottom:8px;display:none}
</style></head><body>
<div class="card">
  <div class="logo">LW</div>
  <h1>llamawatch</h1>
  <div class="err" id="err">Incorrect password</div>
  <form onsubmit="return doLogin(event)">
    <input type="password" id="pw" placeholder="Password" autofocus>
    <button type="submit">Sign in</button>
  </form>
</div>
<script>
async function doLogin(e){
  e.preventDefault();
  const pw=document.getElementById('pw').value;
  const r=await fetch('/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:pw})});
  if(r.ok){location.reload();}
  else{document.getElementById('err').style.display='block';document.getElementById('pw').value='';document.getElementById('pw').focus();}
  return false;
}
</script></body></html>"""


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path

    # Always-public paths (health, login, static assets).
    if path in _PUBLIC_PATHS or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
        return await call_next(request)

    # WebSocket upgrades reach their handler, which enforces its OWN auth gate
    # (security.action_allowed before accept) — see websocket_terminal/chat.
    if request.headers.get("upgrade", "").lower() == "websocket":
        return await call_next(request)

    if not is_auth_enabled():
        # Secure by default: with no password, only a genuine localhost client
        # may reach the dashboard. is_local_request() rejects requests relayed
        # through a reverse proxy or tunnel (they carry forwarding headers and
        # connect from 127.0.0.1), so exposing via nginx/Caddy/Cloudflare/Funnel
        # does NOT count as localhost — a password is required.
        if security.is_local_request(request):
            return await call_next(request)
        return JSONResponse(
            status_code=403,
            content={"error": "llamawatch is unprotected and reachable through a proxy or the network. Set a password (Settings → General) from a direct localhost session — e.g. on the machine itself or over an SSH tunnel — to allow access from anywhere else."},
        )

    # Auth enabled — require a valid session cookie.
    token = request.cookies.get("lw_session")
    if validate_session(token):
        return await call_next(request)

    # Not authenticated — show login page for HTML requests, 401 for API
    if path in ("/", "", "/studio"):
        return HTMLResponse(_LOGIN_PAGE)
    return JSONResponse(status_code=401, content={"error": "Not authenticated"})


# ── Core routes ───────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.websocket("/ws")
async def websocket_dashboard(websocket: WebSocket):
    # Auth/localhost gate BEFORE accept — WebSockets bypass the HTTP middleware,
    # so (like the chat/terminal sockets) this one must enforce the gate itself.
    # Without it, an authenticated-mode dashboard would still stream the full
    # live feed (machine names, IPs, CPU/RAM/GPU, containers, processes, tokens)
    # to anyone who could reach the host without logging in.
    if not security.action_allowed(websocket, is_auth_enabled()):
        await websocket.close(code=1008, reason="Not permitted")
        return
    await websocket.accept()
    enabled_ids = _config.get("widgets", {}).get("enabled") if _config else None
    hub = get_hub(_config, _adapters)
    hub._registry = _collector_registry
    await hub.run_poll_loop(websocket, _config, _adapters, enabled_ids)


# ── Shared helpers referenced by route modules and tests ──────────────────────
# These live here (rather than in their route module) because tests patch them
# via the ``llamawatch.server.*`` path. Route modules call them through ``srv``.

import re as _re
# Container names / systemd units interpolated into ssh command strings must be
# strictly validated to prevent shell command injection.
_SAFE_NAME = _re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@:-]{0,127}$").match


def _remote_hosts_map() -> dict:
    """Build {MACHINE_NAME_UPPER: {host, user}} from fleet config (remotes only)."""
    out = {}
    for h in get_remote_fleet_hosts():
        name = (h.get("name") or "").upper()
        if name and h.get("host"):
            out[name] = {"host": h["host"], "user": h.get("user") or os.getenv("USER", "user")}
    return out


def _local_machine_name() -> str:
    """Name of the local machine per fleet config (the entry with local:true)."""
    for h in get_fleet_hosts():
        if h.get("local"):
            return (h.get("name") or "").upper()
    return socket.gethostname().split(".")[0].upper()


def _pr_db() -> Path | None:
    """Path to the articles SQLite DB from config, or None when not configured."""
    p = load_config().get("press_room_db")
    return Path(os.path.expanduser(p)) if p else None


# ── PWA assets (registered before the static mount so they take precedence) ───

@app.get("/static/manifest.json")
async def web_manifest():
    """Serve the PWA manifest with the configured dashboard name injected."""
    path = Path(__file__).resolve().parent / "static" / "manifest.json"
    try:
        data = json.loads(path.read_text())
    except Exception:
        data = {}
    name = load_config().get("dashboard_name") or "llamawatch"
    data["name"] = name
    data["short_name"] = name
    return JSONResponse(data, headers={"Cache-Control": "no-cache"})


@app.get("/static/sw.js")
async def service_worker():
    """Serve sw.js with no-cache headers so browser always checks for updates."""
    return FileResponse(
        str(Path(__file__).resolve().parent / "static" / "sw.js"),
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"},
        media_type="application/javascript",
    )


# ── Route modules ─────────────────────────────────────────────────────
# Imported here (after state + helpers are defined) so each module's
# ``import llamawatch.server as srv`` binds the fully-initialised module.

from .routes.auth import router as auth_router
from .routes.dashboard import router as dashboard_router
from .routes.settings import router as settings_router
from .routes.chat import router as chat_router
from .routes.actions import router as actions_router
from .routes.knowledge import router as knowledge_router
from .routes_framework import router as framework_router

app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(settings_router)
app.include_router(chat_router)
app.include_router(actions_router)
app.include_router(knowledge_router)
app.include_router(framework_router)

app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent / "static")), name="static")


@app.get("/")
async def root():
    return RedirectResponse(url="/studio", status_code=301)


@app.get("/studio")
async def studio():
    return FileResponse(str(Path(__file__).resolve().parent / "static" / "studio.html"))


if __name__ == "__main__":
    import uvicorn
    cfg = load_config()
    uvicorn.run(app, host=cfg["host"], port=cfg["port"])
