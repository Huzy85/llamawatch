"""Agents page and endpoints: what is running now, and the replay of a run.
Read-only. See llamawatch/agents.py."""

import asyncio
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from .. import agents
from ..collectors import docker_collector

router = APIRouter()
_STATIC = Path(__file__).resolve().parent.parent / "static"


def _cfg() -> dict:
    import llamawatch.server as s
    return getattr(s, "_config", None) or {}


def _containers() -> list[dict]:
    try:
        return (docker_collector.collect(_cfg()) or {}).get("containers") or []
    except Exception:
        return []


@router.get("/agents")
async def agents_home():
    return FileResponse(str(_STATIC / "agents.html"), headers={"Cache-Control": "no-cache"})


@router.get("/api/agents")
async def agents_now():
    cfg = _cfg()
    containers = await asyncio.to_thread(_containers) if cfg.get("agents") else []
    return await asyncio.to_thread(agents.collect, cfg, containers)


@router.get("/api/agents/runs/{run_id}")
async def agents_replay(run_id: str):
    if not agents._ID.match(run_id or ""):
        return JSONResponse({"error": "bad run id"}, status_code=400)
    out = await asyncio.to_thread(agents.replay, _cfg(), run_id)
    if out is None:
        return JSONResponse({"error": "no such run"}, status_code=404)
    return out
