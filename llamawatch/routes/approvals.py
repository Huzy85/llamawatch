"""Approvals page and endpoints: pending requests, approve or deny, rules, the
always-blocked list, the allow list, the requester token, the notify hook and
terminal tickets. See llamawatch/approvals.py for the gate itself.
"""

import secrets
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from .. import approvals, audit, security
from ..auth import is_auth_enabled

router = APIRouter()
_STATIC = Path(__file__).resolve().parent.parent / "static"


def _owner(request: Request):
    if not security.action_allowed(request, is_auth_enabled()):
        return JSONResponse({"error": "not permitted from this client"}, status_code=403)
    return None


async def _body(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _actor(request: Request) -> str:
    if approvals.token_ok(request) and not security.action_allowed(request, is_auth_enabled()):
        return "token"
    return approvals.actor(request)


@router.get("/approvals")
async def approvals_home():
    return FileResponse(str(_STATIC / "approvals.html"), headers={"Cache-Control": "no-cache"})


@router.get("/api/approvals/summary")
async def approvals_summary():
    return {"pending": len(approvals.store.pending())}


@router.get("/api/approvals")
async def approvals_state(request: Request):
    if (denied := _owner(request)) is not None:
        return denied
    return {
        "pending": [approvals.public(r) for r in approvals.store.pending()],
        "recent": [approvals.public(r) for r in approvals.store.recent(50)],
        "kinds": [{"id": k, "label": label, "about": about} for k, label, about in approvals.KINDS],
        "rules": approvals.rules(),
        "builtin_blocks": approvals.BUILTIN_REASONS,
        "block_patterns": approvals.block_patterns(),
        "allow_commands": approvals.allow_commands(),
        "token_set": bool(approvals.token()),
        "notify_command": approvals.notify_command(),
        "ttl": approvals.ttl(),
        "local_only": not is_auth_enabled(),
    }


@router.post("/api/approvals")
async def approvals_request(request: Request):
    """An agent or script asks: {title, detail?, command?, requester?}."""
    if not approvals.requester_allowed(request):
        return JSONResponse({"error": "not permitted: send the requester token in X-Llamawatch-Token"}, status_code=403)
    body = await _body(request)
    title = approvals.flat(body.get("title") or "")
    if not title or len(title) > 200:
        return JSONResponse({"error": "a title of 1 to 200 characters is needed"}, status_code=400)
    command = str(body.get("command") or "").strip()
    if len(command) > 4000:
        return JSONResponse({"error": "the command is too long (4000 characters at most)"}, status_code=400)
    requester = approvals.flat(body.get("requester") or "") or "agent"
    who = _actor(request)
    client = approvals._client(request)
    if command and (reason := approvals.check_command(command)) is not None:
        rec = approvals.store.create("agent", title, detail=body.get("detail") or "", command=command, requester=requester,
                                     client=client, status="blocked", decided_by="blocklist", result=f"blocked: {reason}")
        audit.append("approval_blocked", target=title[:80], outcome="blocked", actor=who, id=rec["id"], kind="agent",
                     requester=requester, reason=reason)
        return JSONResponse({**approvals.public(rec), "error": f"blocked: {reason}"}, status_code=403)
    pol = approvals.policy("agent")
    if pol == "block":
        rec = approvals.store.create("agent", title, detail=body.get("detail") or "", command=command, requester=requester,
                                     client=client, status="blocked", decided_by="rule", result="agent requests are blocked by your Approvals rules")
        audit.append("approval_blocked", target=title[:80], outcome="blocked", actor=who, id=rec["id"], kind="agent", requester=requester,
                     reason="rule")
        return JSONResponse({**approvals.public(rec), "error": rec["result"]}, status_code=403)
    if pol == "allow" or (command and command in approvals.allow_commands()):
        by = "allowlist" if pol != "allow" else "rule"
        rec = approvals.store.create("agent", title, detail=body.get("detail") or "", command=command, requester=requester,
                                     client=client, status="approved", decided_by=by, result="approved without asking")
        audit.append("approval_granted", target=title[:80], outcome="ok", actor=by, id=rec["id"], kind="agent", requester=requester)
        return approvals.public(rec)
    rec = approvals.store.create("agent", title, detail=body.get("detail") or "", command=command, requester=requester, client=client)
    audit.append("approval_requested", target=title[:80], outcome="pending", actor=who, id=rec["id"], kind="agent", requester=requester)
    approvals.notify(rec, base_url=str(request.base_url))
    return JSONResponse(approvals.public(rec), status_code=202)


@router.get("/api/approvals/{rid}")
async def approvals_poll(rid: str, request: Request, wait: float = 0):
    if not approvals.requester_allowed(request):
        return JSONResponse({"error": "not permitted"}, status_code=403)
    wait = max(0.0, min(float(wait or 0), approvals.MAX_WAIT))
    rec = await approvals.store.wait(rid, wait) if wait else approvals.store.get(rid)
    if rec is None:
        return JSONResponse({"error": "no such request"}, status_code=404)
    if rec["status"] == "pending":
        approvals.store.pending()   # sweeps expiry
        rec = approvals.store.get(rid)
    return approvals.public(rec)


@router.post("/api/approvals/{rid}/approve")
async def approvals_approve(rid: str, request: Request):
    if (denied := _owner(request)) is not None:
        return denied
    body = await _body(request)
    rec = approvals.store.decide(rid, "approved", by=approvals.actor(request))
    if rec is None:
        cur = approvals.store.get(rid)
        return JSONResponse({"error": "that request is no longer waiting" if cur else "no such request",
                             "status": cur["status"] if cur else None}, status_code=409 if cur else 404)
    audit.append("approval_granted", target=rec["title"][:80], outcome="ok", actor=approvals.actor(request), id=rid, kind=rec["kind"])
    if body.get("allow_always") and rec.get("command") and rec["kind"] == "agent":
        cmds = approvals.allow_commands()
        if rec["command"] not in cmds and len(cmds) < approvals.MAX_ALLOW:
            approvals.save({"allow_commands": cmds + [rec["command"]]})
            audit.append("approval_settings", target="allow_commands", outcome="ok", actor=approvals.actor(request), added=rec["command"][:200])
    if rec.get("replay"):
        code, msg = await approvals.replay(rec)
        approvals.store.finish(rid, "done" if code < 400 else "failed", msg)
        rec = approvals.store.get(rid)
    return {"status": "ok", "message": f"Approved: {rec['title']}" + (f". {rec['result']}" if rec.get("replay") and rec.get("result") else ""),
            "request": approvals.public(rec)}


@router.post("/api/approvals/{rid}/deny")
async def approvals_deny(rid: str, request: Request):
    if (denied := _owner(request)) is not None:
        return denied
    rec = approvals.store.decide(rid, "denied", by=approvals.actor(request), result="denied by the owner")
    if rec is None:
        cur = approvals.store.get(rid)
        return JSONResponse({"error": "that request is no longer waiting" if cur else "no such request",
                             "status": cur["status"] if cur else None}, status_code=409 if cur else 404)
    audit.append("approval_denied", target=rec["title"][:80], outcome="denied", actor=approvals.actor(request), id=rid, kind=rec["kind"])
    return {"status": "ok", "message": f"Denied: {rec['title']}", "request": approvals.public(rec)}


# ── settings ─────────────────────────────────────────────────────────────────
@router.put("/api/approvals/rules")
async def approvals_rules(request: Request):
    if (denied := _owner(request)) is not None:
        return denied
    body = await _body(request)
    given = body.get("rules") if isinstance(body.get("rules"), dict) else body
    new = dict(approvals.rules())
    for k, v in given.items():
        if k not in new:
            return JSONResponse({"error": f"unknown kind: {k}"}, status_code=400)
        if v not in approvals.POLICIES:
            return JSONResponse({"error": f"a rule must be allow, ask or block, not {v}"}, status_code=400)
        new[k] = v
    approvals.save({"rules": new})
    audit.append("approval_settings", target="rules", outcome="ok", actor=approvals.actor(request),
                 changed=",".join(f"{k}={v}" for k, v in given.items())[:200])
    return {"status": "ok", "message": "Rules saved", "rules": approvals.rules()}


@router.put("/api/approvals/blocklist")
async def approvals_blocklist(request: Request):
    if (denied := _owner(request)) is not None:
        return denied
    body = await _body(request)
    pats = body.get("patterns")
    if not isinstance(pats, list):
        return JSONResponse({"error": "send {\"patterns\": [...]}"}, status_code=400)
    clean = []
    for p in pats:
        p = str(p or "").strip()
        if p and p not in clean:
            if len(p) > 300:
                return JSONResponse({"error": "a pattern can be 300 characters at most"}, status_code=400)
            clean.append(p)
    if len(clean) > approvals.MAX_PATTERNS:
        return JSONResponse({"error": f"{approvals.MAX_PATTERNS} patterns at most"}, status_code=400)
    approvals.save({"block_patterns": clean})
    audit.append("approval_settings", target="block_patterns", outcome="ok", actor=approvals.actor(request), count=len(clean))
    return {"status": "ok", "message": "Blocked patterns saved", "block_patterns": approvals.block_patterns()}


@router.put("/api/approvals/allowlist")
async def approvals_allowlist(request: Request):
    if (denied := _owner(request)) is not None:
        return denied
    body = await _body(request)
    cmds = body.get("commands")
    if not isinstance(cmds, list):
        return JSONResponse({"error": "send {\"commands\": [...]}"}, status_code=400)
    clean = []
    for c in cmds:
        c = str(c or "").strip()
        if c and c not in clean:
            if len(c) > 4000:
                return JSONResponse({"error": "a command can be 4000 characters at most"}, status_code=400)
            if (reason := approvals.check_command(c)) is not None:
                return JSONResponse({"error": f"that command is always blocked: {reason}"}, status_code=400)
            clean.append(c)
    if len(clean) > approvals.MAX_ALLOW:
        return JSONResponse({"error": f"{approvals.MAX_ALLOW} commands at most"}, status_code=400)
    approvals.save({"allow_commands": clean})
    audit.append("approval_settings", target="allow_commands", outcome="ok", actor=approvals.actor(request), count=len(clean))
    return {"status": "ok", "message": "Allow list saved", "allow_commands": approvals.allow_commands()}


@router.put("/api/approvals/settings")
async def approvals_settings(request: Request):
    if (denied := _owner(request)) is not None:
        return denied
    body = await _body(request)
    patch = {}
    if "notify_command" in body:
        cmd = str(body.get("notify_command") or "").strip()
        if len(cmd) > 2000:
            return JSONResponse({"error": "the notify command is too long"}, status_code=400)
        if cmd and (reason := approvals.check_command(cmd)) is not None:
            return JSONResponse({"error": f"that command is always blocked: {reason}"}, status_code=400)
        patch["notify_command"] = cmd or None
    if "ttl" in body:
        try:
            t = int(body.get("ttl"))
        except (TypeError, ValueError):
            return JSONResponse({"error": "expiry must be a number of seconds"}, status_code=400)
        if not approvals.MIN_TTL <= t <= approvals.MAX_TTL:
            return JSONResponse({"error": f"expiry must be between {approvals.MIN_TTL} and {approvals.MAX_TTL} seconds"}, status_code=400)
        patch["ttl"] = t
    if not patch:
        return JSONResponse({"error": "nothing to change"}, status_code=400)
    approvals.save(patch)
    audit.append("approval_settings", target=",".join(patch), outcome="ok", actor=approvals.actor(request))
    return {"status": "ok", "message": "Settings saved", "notify_command": approvals.notify_command(), "ttl": approvals.ttl()}


@router.post("/api/approvals/token")
async def approvals_token_new(request: Request):
    if (denied := _owner(request)) is not None:
        return denied
    tok = "lwa_" + secrets.token_urlsafe(30)
    approvals.save({"token": tok})
    audit.append("approval_token", target="token", outcome="ok", actor=approvals.actor(request), change="new")
    return {"status": "ok", "message": "New requester token made. Copy it now, it is not shown again.", "token": tok}


@router.delete("/api/approvals/token")
async def approvals_token_revoke(request: Request):
    if (denied := _owner(request)) is not None:
        return denied
    approvals.save({"token": None})
    audit.append("approval_token", target="token", outcome="ok", actor=approvals.actor(request), change="revoked")
    return {"status": "ok", "message": "Requester token revoked"}


# ── terminal tickets ─────────────────────────────────────────────────────────
@router.post("/api/terminal/ticket")
async def terminal_ticket(request: Request):
    """A ticket for /ws/terminal/new?ticket=. Allow: at once. Ask: 202 until approved."""
    if (denied := _owner(request)) is not None:
        return denied
    pol = approvals.policy("terminal")
    who = approvals.actor(request)
    if pol == "block":
        return approvals.blocked("terminal", "terminal", "terminals are blocked by your Approvals rules", who)
    if pol == "allow":
        rec = approvals.store.create("terminal", "Open a terminal", requester="dashboard", client=approvals._client(request),
                                     status="approved", decided_by="rule", result="allowed by rule")
        return {"status": "ok", "ticket": rec["id"]}
    rec = approvals.store.create("terminal", "Open a terminal", requester="dashboard", client=approvals._client(request))
    audit.append("approval_requested", target="terminal", outcome="pending", actor=who, id=rec["id"], kind="terminal")
    approvals.notify(rec, base_url=str(request.base_url))
    return JSONResponse({"status": "pending", "id": rec["id"],
                         "message": "Waiting for approval to open a terminal. See the Approvals page."}, status_code=202)
