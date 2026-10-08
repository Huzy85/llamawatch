"""Jobs page and endpoints: every scheduled job in one list, run or pause a user
timer, and add, edit or delete timers the page made itself.

Only user systemd timers can be acted on. System timers need root and a
crontab edit from a web page is a bad idea, so both are read-only here. Edit
and delete go further: they only touch units that carry the page's own marker
line. Every write asks `security.action_allowed` and lands in the audit log.
"""

import asyncio
import subprocess
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from . import srv
from .. import approvals, audit, jobs, security
from ..auth import is_auth_enabled

router = APIRouter()
_STATIC = Path(__file__).resolve().parent.parent / "static"

_ACTIONS = {
    "run": ("start", "service", "job_run"),
    "pause": ("stop", "timer", "job_pause"),
    "resume": ("start", "timer", "job_resume"),
}


def _config() -> dict:
    return getattr(srv, "_config", None) or {}


@router.get("/jobs")
async def jobs_home():
    # no-cache: the page names its script versions, so a stale copy loads old code
    return FileResponse(str(_STATIC / "jobs.html"), headers={"Cache-Control": "no-cache"})


@router.get("/api/jobs")
async def jobs_list(fresh: int = 0):
    return await asyncio.to_thread(jobs.collect, _config(), bool(fresh))


@router.get("/api/jobs/preview")
async def jobs_preview(schedule: str = ""):
    """Words -> systemd schedule, its plain-words form and the next run. For the add form."""
    try:
        return await asyncio.to_thread(jobs.preview, schedule)
    except jobs.JobError as e:
        return JSONResponse({"error": str(e)}, status_code=e.status)


def _gate(request: Request):
    if not security.action_allowed(request, is_auth_enabled()):
        return JSONResponse({"error": "not permitted from this client"}, status_code=403)
    return None


async def _body(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _actor(request: Request | None = None) -> str:
    return approvals.actor(request) if request is not None else ("local" if not is_auth_enabled() else "session")


@router.post("/api/jobs")
async def jobs_create(request: Request):
    """Make a new user timer + service from the form (name, description, command, schedule)."""
    if (denied := _gate(request)) is not None:
        return denied
    fields = await _body(request)
    if (reason := approvals.check_command(fields.get("command") or "")) is not None:
        return approvals.blocked("job_change", str(fields.get("name") or "?")[:60], reason, _actor(request))
    if (waiting := await approvals.gate(request, "job_change", str(fields.get("name") or "?")[:60], f"Create job {fields.get('name') or '?'}")) is not None:
        return waiting
    try:
        made = await asyncio.to_thread(jobs.create_job, fields)
    except jobs.JobError as e:
        audit.append("job_create", target=str(fields.get("name") or "?")[:60], outcome="fail", actor=_actor(request))
        return JSONResponse({"error": str(e)}, status_code=e.status)
    audit.append("job_create", target=made["name"], outcome="ok", actor=_actor(request))
    return JSONResponse({"status": "ok", "message": f"{made['name']} created, runs {made['schedule']}", **made}, status_code=201)


@router.put("/api/jobs/{job_id:path}")
async def jobs_update(job_id: str, request: Request):
    if (denied := _gate(request)) is not None:
        return denied
    name = _managed_name(job_id)
    if name is None:
        return JSONResponse({"error": "only jobs created from this page can be edited here"}, status_code=400)
    fields = await _body(request)
    if (reason := approvals.check_command(fields.get("command") or "")) is not None:
        return approvals.blocked("job_change", name, reason, _actor(request))
    if (waiting := await approvals.gate(request, "job_change", name, f"Edit job {name}")) is not None:
        return waiting
    try:
        made = await asyncio.to_thread(jobs.update_job, name, fields)
    except jobs.JobError as e:
        audit.append("job_update", target=name, outcome="fail", actor=_actor(request))
        return JSONResponse({"error": str(e)}, status_code=e.status)
    audit.append("job_update", target=name, outcome="ok", actor=_actor(request))
    return {"status": "ok", "message": f"{name} saved, runs {made['schedule']}", **made}


@router.delete("/api/jobs/{job_id:path}")
async def jobs_delete(job_id: str, request: Request):
    if (denied := _gate(request)) is not None:
        return denied
    name = _managed_name(job_id)
    if name is None:
        return JSONResponse({"error": "only jobs created from this page can be deleted here"}, status_code=400)
    if (waiting := await approvals.gate(request, "job_change", name, f"Delete job {name}")) is not None:
        return waiting
    try:
        await asyncio.to_thread(jobs.delete_job, name)
    except jobs.JobError as e:
        audit.append("job_delete", target=name, outcome="fail", actor=_actor(request))
        return JSONResponse({"error": str(e)}, status_code=e.status)
    audit.append("job_delete", target=name, outcome="ok", actor=_actor(request))
    return {"status": "ok", "message": f"{name} deleted"}


def _managed_name(job_id: str) -> str | None:
    """'user:<name>' -> name, only when the name is one the page could have made."""
    if not job_id.startswith("user:"):
        return None
    name = job_id[5:]
    return name if jobs.JOB_NAME(name) else None


@router.get("/api/jobs/{job_id:path}/log")
async def jobs_log(job_id: str):
    job = await asyncio.to_thread(jobs.find, job_id, _config())
    if job is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"lines": await asyncio.to_thread(jobs.journal, job)}


@router.post("/api/jobs/{job_id:path}/{action}")
async def jobs_action(job_id: str, action: str, request: Request):
    if not security.action_allowed(request, is_auth_enabled()):
        return JSONResponse({"error": "not permitted from this client"}, status_code=403)
    if action not in _ACTIONS:
        return JSONResponse({"error": f"unknown action: {action}"}, status_code=400)
    job = await asyncio.to_thread(jobs.find, job_id, _config())
    if job is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    if not job.get("can_act"):
        return JSONResponse({"error": "this job is read-only here"}, status_code=400)
    verb, which, audit_name = _ACTIONS[action]
    unit = (job.get("units") or {}).get(which) or ""
    if not unit or not jobs.SAFE_NAME(unit):
        return JSONResponse({"error": "no unit to act on"}, status_code=400)
    kind = "job_run" if action == "run" else "job_change"
    verb_words = {"run": "Run", "pause": "Pause", "resume": "Resume"}[action]
    if (waiting := await approvals.gate(request, kind, job["name"], f"{verb_words} job {job['name']}")) is not None:
        return waiting
    actor = approvals.actor(request)
    try:
        result = await asyncio.to_thread(
            lambda: subprocess.run(["systemctl", "--user", verb, unit], capture_output=True, text=True, timeout=30))
    except subprocess.TimeoutExpired:
        audit.append(audit_name, target=job["name"], outcome="timeout", actor=actor)
        return JSONResponse({"error": f"{action} took longer than 30 s"}, status_code=504)
    if result.returncode != 0:
        audit.append(audit_name, target=job["name"], outcome="fail", actor=actor)
        err = (result.stderr or result.stdout or "systemctl failed").strip()
        return JSONResponse({"error": err}, status_code=500)
    audit.append(audit_name, target=job["name"], outcome="ok", actor=actor)
    done = {"run": "started", "pause": "paused", "resume": "resumed"}[action]
    return {"status": "ok", "message": f"{job['name']} {done}"}
