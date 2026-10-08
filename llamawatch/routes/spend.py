"""Spend page and endpoints: the daily ledger summary and the owner's prices.
See llamawatch/spend.py for the roll-up."""

import asyncio
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from .. import audit, security, spend, spend_providers
from ..auth import is_auth_enabled

router = APIRouter()
_STATIC = Path(__file__).resolve().parent.parent / "static"


def _cfg() -> dict:
    import llamawatch.server as s
    return getattr(s, "_config", None) or {}


@router.get("/spend")
async def spend_home():
    return FileResponse(str(_STATIC / "spend.html"), headers={"Cache-Control": "no-cache"})


@router.get("/api/spend")
async def spend_summary(request: Request, days: int = spend.DEFAULT_DAYS):
    cfg = _cfg()
    try:
        await asyncio.to_thread(spend.rollup, cfg)   # the first roll-up reads weeks of logs; keep the loop free
    except Exception as e:   # a broken log must never take the page down
        audit.append("spend_rollup", outcome="fail", error=str(e)[:200])
    return await asyncio.to_thread(spend.summary, cfg, days)


@router.put("/api/spend/prices")
async def spend_prices(request: Request):
    if not security.action_allowed(request, is_auth_enabled()):
        return JSONResponse({"error": "not permitted from this client"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        body = {}
    given = body.get("prices") if isinstance(body, dict) and isinstance(body.get("prices"), dict) else body
    if not isinstance(given, dict) or not given:
        return JSONResponse({"error": "send {\"prices\": {name: {in, out, cache, currency}}}"}, status_code=400)
    patch = {}
    for name, val in given.items():
        name = str(name).strip()[:120]
        if not name:
            continue
        try:
            patch[name] = spend.clean_price(val) if val is not None else None
        except ValueError as e:
            return JSONResponse({"error": f"{name}: {e}"}, status_code=400)
    saved = spend.save_prices(patch)
    audit.append("spend_prices", target=", ".join(sorted(patch)), outcome="ok",
                 actor="local" if not is_auth_enabled() else "session")
    return {"prices": spend.prices(_cfg()), "own": saved}


def _denied(request: Request):
    if not security.action_allowed(request, is_auth_enabled()):
        return JSONResponse({"error": "not permitted from this client"}, status_code=403)
    return None


def _actor() -> str:
    return "local" if not is_auth_enabled() else "session"


@router.get("/api/spend/providers")
async def providers_list():
    """Every supported provider and where it stands. Never carries a key."""
    return {"providers": spend_providers.status(_cfg())}


@router.put("/api/spend/providers/{pid}")
async def providers_connect(pid: str, request: Request):
    """Test the pasted key against the provider and save it (encrypted) only if it works."""
    if (d := _denied(request)):
        return d
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        await asyncio.to_thread(spend_providers.connect, pid, body if isinstance(body, dict) else {})
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except spend_providers.ProviderError as e:
        audit.append("spend_provider_connect", target=pid, outcome="fail", actor=_actor())
        return JSONResponse({"error": f"{e}. The key was not saved."}, status_code=400)
    audit.append("spend_provider_connect", target=pid, outcome="ok", actor=_actor())
    return {"providers": spend_providers.status(_cfg())}


@router.delete("/api/spend/providers/{pid}")
async def providers_disconnect(pid: str, request: Request):
    if (d := _denied(request)):
        return d
    try:
        await asyncio.to_thread(spend_providers.disconnect, pid)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    audit.append("spend_provider_disconnect", target=pid, outcome="ok", actor=_actor())
    return {"providers": spend_providers.status(_cfg())}


@router.post("/api/spend/providers/{pid}/refresh")
async def providers_refresh(pid: str, request: Request):
    if (d := _denied(request)):
        return d
    if pid not in spend_providers.PROVIDERS:
        return JSONResponse({"error": "unknown provider"}, status_code=400)
    await asyncio.to_thread(spend_providers.poll, _cfg(), None, True, pid)
    return {"providers": spend_providers.status(_cfg())}
