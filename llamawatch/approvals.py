"""Approvals: a gate and audit trail for the machine actions llamawatch can take.

Every kind of action (quick action, service, container, run a job, change a
job, open a terminal, a request from an agent) has a rule: allow, ask or block.
"Ask" parks the action as a pending request on the Approvals page. Approving a
dashboard request replays the stored HTTP request in-process, so the handler
runs exactly as if the owner had clicked; approving an agent request just
tells the caller it may go ahead. A built-in list of destructive shell
patterns is refused wherever a command enters and can never be approved.

Settings live under ``approvals`` in config.local.json; the request store is
``~/.config/llamawatch/approvals.json`` (last 300 requests).
"""

import asyncio
import hmac
import json
import os
import re
import secrets
import time
from pathlib import Path

from fastapi.responses import JSONResponse

from . import audit, security
from .auth import is_auth_enabled
from .config import encrypt_secrets, get_config_dir, reload_config

# ── kinds and rules ──────────────────────────────────────────────────────────
KINDS = [
    ("quick_action", "Quick actions", "the shell buttons on the Monitor page"),
    ("service", "Services", "start, stop or restart a service, here or on another machine"),
    ("docker", "Containers", "start, stop or restart a Docker container"),
    ("job_run", "Run a job now", "Run now on the Jobs page, and timer triggers"),
    ("job_change", "Change a job", "pause, resume, add, edit or delete a job"),
    ("terminal", "Open a terminal", "a shell in the browser"),
    ("agent", "Agent requests", "scripts and agents asking through the API"),
]
POLICIES = ("allow", "ask", "block")
DEFAULT_RULES = {k: "allow" for k, _, _ in KINDS}
DEFAULT_RULES["agent"] = "ask"
LABELS = {k: label for k, label, _ in KINDS}

DEFAULT_TTL = 1800          # a pending request lives 30 minutes
MIN_TTL, MAX_TTL = 60, 86400
KEEP = 300                  # requests kept on disk
MAX_WAIT = 30               # longest a poll may hang, seconds
TICKET_LIFE = 600           # an approved terminal ticket must be used within 10 minutes
MAX_PATTERNS, MAX_ALLOW = 50, 100

# ── always blocked ───────────────────────────────────────────────────────────
_DISK = r"/dev/(?:sd[a-z]|nvme\d|hd[a-z]|vd[a-z]|xvd[a-z]|mmcblk\d|disk\d)"
_ROOTS = r"(?:/|~|\$\{?HOME\}?|/(?:bin|boot|dev|etc|home|lib\w*|opt|proc|root|sbin|srv|sys|usr|var)/?)\*?"
_END = r"(?=\s|$|[;&|)])"

BUILTIN_BLOCKS = [
    (re.compile(rf"\brm\s+(?:-\S*[rR]\S*|--recursive)(?:\s+-\S+)*\s+{_ROOTS}{_END}"), "removes the root or a system folder"),
    (re.compile(r"--no-preserve-root"), "removes the root folder"),
    (re.compile(r"\b(?:mkfs(?:\.\w+)?|wipefs)\b"), "formats or wipes a disk"),
    (re.compile(rf"(?:\bdd\b[^;|&]*\bof=|>\s*|\bshred\b[^;|&]*\s){_DISK}"), "writes to a raw disk device"),
    (re.compile(r"(?:^|[;&|(]\s*|\b(?:sudo|doas)\s+(?:-\S+\s+)*)(?:shutdown|poweroff|reboot|halt|telinit\s+[06]|init\s+[06])\b"), "powers off or restarts the machine"),
    (re.compile(r"\bsystemctl\s+(?:-\S+\s+)*(?:poweroff|reboot|halt|kexec)\b"), "powers off or restarts the machine"),
    (re.compile(r"(?::|\b(\w+))\(\)\s*\{\s*(?::|\1)\s*\|\s*(?::|\1)\s*&"), "fork bomb"),
    (re.compile(rf"\b(?:chmod|chown)\s+(?:-\S*R\S*|--recursive)(?:\s+-\S+)*\s+\S+\s+{_ROOTS}{_END}"), "changes ownership or permissions across the whole system"),
    (re.compile(r"\bcrontab\s+(?:-\S+\s+)*-r\b"), "wipes the crontab"),
    (re.compile(r"\b(?:curl|wget)\b[^|;&]*\|\s*(?:sudo\s+)?(?:ba|z|da|k|fi)?sh\b"), "pipes a download straight into a shell"),
    (re.compile(r"\bkill\s+(?:-9\s+|-KILL\s+|-s\s+KILL\s+)?-1\b"), "kills every process"),
]
BUILTIN_REASONS = list(dict.fromkeys(reason for _, reason in BUILTIN_BLOCKS))


def flat(text: str) -> str:
    return " ".join(str(text or "").split())


def check_command(text: str, patterns: list | None = None) -> str | None:
    """The reason a command is refused, or None when it may run.

    Built-in patterns first, then the owner's own (a regex when it compiles,
    otherwise a plain substring, case-insensitive).
    """
    s = flat(text)
    if not s:
        return None
    for rx, reason in BUILTIN_BLOCKS:
        if rx.search(s):
            return reason
    for pat in (block_patterns() if patterns is None else patterns):
        pat = str(pat or "").strip()
        if not pat:
            continue
        try:
            hit = re.search(pat, s, re.I) is not None
        except re.error:
            hit = pat.lower() in s.lower()
        if hit:
            return f"matches your blocked pattern: {pat}"
    return None


# ── settings (config.local.json → approvals) ─────────────────────────────────
def _top() -> dict:
    import llamawatch.server as s
    return getattr(s, "_config", None) or {}


def settings() -> dict:
    a = _top().get("approvals")
    return a if isinstance(a, dict) else {}


def rules() -> dict:
    out = dict(DEFAULT_RULES)
    for k, v in (settings().get("rules") or {}).items():
        if k in out and v in POLICIES:
            out[k] = v
    return out


def policy(kind: str) -> str:
    return rules().get(kind, "allow")


def ttl() -> int:
    try:
        return max(MIN_TTL, min(MAX_TTL, int(settings().get("ttl") or DEFAULT_TTL)))
    except (TypeError, ValueError):
        return DEFAULT_TTL


def _str_list(key: str) -> list[str]:
    v = settings().get(key)
    return [str(x) for x in v if str(x).strip()] if isinstance(v, list) else []


def block_patterns() -> list[str]:
    return _str_list("block_patterns")


def allow_commands() -> list[str]:
    return _str_list("allow_commands")


def token() -> str:
    return str(settings().get("token") or "")


def notify_command() -> str:
    return str(settings().get("notify_command") or "")


def token_ok(request) -> bool:
    """True when the request carries the requester token (X-Llamawatch-Token)."""
    t = token()
    given = (getattr(request, "headers", None) or {}).get("x-llamawatch-token", "")
    return bool(t) and bool(given) and hmac.compare_digest(given, t)


def requester_allowed(request) -> bool:
    """Owner session, genuine localhost with no password, or the requester token."""
    return security.action_allowed(request, is_auth_enabled()) or token_ok(request)


def actor(request) -> str:
    """Who an audit line should name for this request."""
    if security.is_replay(request):
        return "approved"
    return "local" if not is_auth_enabled() else "session"


def save(patch: dict) -> dict:
    """Write keys into the approvals block of config.local.json and reload.

    Lists and dicts are replaced whole. A value of None removes the key.
    The token is in SECRET_KEYS, so it is encrypted at rest like any API key.
    """
    import llamawatch.server as s
    d = get_config_dir()
    p = d / "config.local.json"
    existing = json.loads(p.read_text()) if p.is_file() else {}
    cur = existing.get("approvals") if isinstance(existing.get("approvals"), dict) else {}
    cur = {**cur, **patch}
    for k in [k for k, v in cur.items() if v is None]:
        cur.pop(k)
    existing["approvals"] = cur
    existing = encrypt_secrets(existing)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(existing, indent=2))
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    s._config = reload_config(config_dir=d)
    return settings()


# ── the request store ────────────────────────────────────────────────────────
class Store:
    """Requests in memory, mirrored to a JSON file. One asyncio.Event per pending id."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else Path.home() / ".config" / "llamawatch" / "approvals.json"
        self._items: list[dict] | None = None
        self._events: dict[str, asyncio.Event] = {}

    def _load(self) -> list[dict]:
        if self._items is None:
            try:
                data = json.loads(self.path.read_text()) if self.path.is_file() else []
            except (OSError, ValueError):
                data = []
            self._items = [r for r in data if isinstance(r, dict) and r.get("id")] if isinstance(data, list) else []
        return self._items

    def _save(self) -> None:
        items = self._load()
        del items[:-KEEP]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(items))
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _wake(self, rid: str) -> None:
        ev = self._events.pop(rid, None)
        if ev is not None:
            ev.set()

    def _sweep(self) -> None:
        now = time.time()
        changed = False
        for r in self._load():
            if r["status"] == "pending" and r["expires"] <= now:
                r.update(status="expired", decided_at=now, decided_by="timeout")
                audit.append("approval_expired", target=r["title"][:80], outcome="expired", actor="system", id=r["id"], kind=r["kind"])
                self._wake(r["id"])
                changed = True
        if changed:
            self._save()

    def create(self, kind: str, title: str, *, detail: str = "", command: str = "", target: str = "",
               requester: str = "dashboard", client: str = "", replay: dict | None = None,
               ttl_s: int | None = None, status: str = "pending", decided_by: str = "", result: str = "") -> dict:
        now = time.time()
        rec = {
            "id": secrets.token_hex(6), "kind": kind, "title": flat(title)[:200], "detail": str(detail or "")[:2000],
            "command": str(command or "").strip()[:4000], "target": str(target or "")[:200], "requester": flat(requester)[:60] or "dashboard",
            "client": str(client or "")[:80], "created": now, "expires": now + (ttl_s or ttl()), "status": status,
            "decided_at": None if status == "pending" else now, "decided_by": decided_by, "result": str(result or "")[:500],
            "replay": replay, "consumed": False,
        }
        self._load().append(rec)
        self._save()
        return rec

    def get(self, rid: str) -> dict | None:
        return next((r for r in self._load() if r["id"] == rid), None)

    def pending(self) -> list[dict]:
        self._sweep()
        return [r for r in self._load() if r["status"] == "pending"]

    def recent(self, n: int = 50) -> list[dict]:
        self._sweep()
        return [r for r in reversed(self._load()) if r["status"] != "pending"][:n]

    def decide(self, rid: str, status: str, by: str = "owner", result: str = "") -> dict | None:
        """pending → approved | denied. None when the request is not pending any more."""
        self._sweep()
        r = self.get(rid)
        if r is None or r["status"] != "pending":
            return None
        r.update(status=status, decided_at=time.time(), decided_by=by, result=str(result or "")[:500])
        self._save()
        self._wake(rid)
        return r

    def finish(self, rid: str, status: str, result: str) -> None:
        """approved → done | failed, after the replay ran."""
        r = self.get(rid)
        if r is not None:
            r.update(status=status, result=str(result or "")[:500])
            self._save()

    def consume(self, rid: str) -> bool:
        """Use an approved terminal ticket once."""
        r = self.get(rid) if rid else None
        if r is None or r["kind"] != "terminal" or r["status"] != "approved" or r["consumed"]:
            return False
        if time.time() - (r["decided_at"] or r["created"]) > TICKET_LIFE:
            return False
        r["consumed"] = True
        self._save()
        return True

    async def wait(self, rid: str, timeout: float) -> dict | None:
        """The request, after waiting up to *timeout* seconds for a decision."""
        r = self.get(rid)
        if r is None or r["status"] != "pending" or timeout <= 0:
            self._sweep()
            return self.get(rid)
        timeout = min(timeout, max(0.0, r["expires"] - time.time()) + 0.05)
        ev = self._events.setdefault(rid, asyncio.Event())
        try:
            await asyncio.wait_for(ev.wait(), timeout)
        except asyncio.TimeoutError:
            pass
        self._sweep()
        return self.get(rid)


store = Store()


def reset(path: Path | str | None = None) -> None:
    """Point the store at another file (tests)."""
    global store
    store = Store(path)


def public(rec: dict) -> dict:
    """A request as the API shows it: no replay body, with seconds left."""
    out = {k: v for k, v in rec.items() if k != "replay"}
    rp = rec.get("replay")
    out["replay"] = {"method": rp.get("method"), "path": rp.get("path")} if rp else None
    out["expires_in"] = max(0, int(rec["expires"] - time.time())) if rec["status"] == "pending" else 0
    out["kind_label"] = LABELS.get(rec["kind"], rec["kind"])
    return out


# ── the gate every handler calls ─────────────────────────────────────────────
def _client(request) -> str:
    return str(getattr(getattr(request, "client", None), "host", "") or "")


def blocked(kind: str, target: str, reason: str, who: str) -> JSONResponse:
    """Audit a refusal and build its 403."""
    audit.append("approval_blocked", target=flat(target)[:80], outcome="blocked", actor=who, kind=kind, reason=reason)
    return JSONResponse({"status": "blocked", "error": f"blocked: {reason}"}, status_code=403)


async def gate(request, kind: str, target: str, title: str, *, replay: bool = True):
    """None to proceed; a 403 when the rule is block; a 202 when it is ask.

    Under ask the request (method, path, query, body) is stored so approving
    it can replay it. A replayed request passes straight through.
    """
    if security.is_replay(request):
        return None
    pol = policy(kind)
    if pol == "allow":
        return None
    who = actor(request)
    if pol == "block":
        return blocked(kind, target, f"{LABELS.get(kind, kind).lower()} are blocked by your Approvals rules", who)
    stored = None
    if replay:
        body = await request.body()
        stored = {"method": request.method, "path": request.url.path, "query": request.url.query,
                  "body": body.decode("utf-8", "replace")}
    rec = store.create(kind, title, target=target, requester="dashboard", client=_client(request), replay=stored)
    audit.append("approval_requested", target=flat(target)[:80], outcome="pending", actor=who, id=rec["id"], kind=kind)
    notify(rec, base_url=str(getattr(request, "base_url", "") or ""))
    return JSONResponse({"status": "pending", "id": rec["id"],
                         "message": f"Waiting for approval: {rec['title']}. See the Approvals page."}, status_code=202)


async def replay(rec: dict) -> tuple[int, str]:
    """Re-send a stored dashboard request to the app in-process. (status, message)."""
    import llamawatch.server as s
    rp = rec.get("replay") or {}
    if not rp.get("path"):
        return 400, "nothing to replay"
    body = (rp.get("body") or "").encode()
    path = rp["path"]
    headers = [(b"host", b"127.0.0.1"), (b"content-type", b"application/json"),
               (b"content-length", str(len(body)).encode()),
               (security.REPLAY_HEADER.encode(), security.replay_secret().encode())]
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
             "method": rp.get("method") or "POST", "scheme": "http", "path": path, "raw_path": path.encode(),
             "query_string": (rp.get("query") or "").encode(), "root_path": "", "headers": headers,
             "client": ("127.0.0.1", 0), "server": ("127.0.0.1", 0), "state": {}}
    msgs = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        return msgs.pop(0) if msgs else {"type": "http.disconnect"}

    out = {"status": 500, "body": b""}

    async def send(m):
        if m["type"] == "http.response.start":
            out["status"] = m["status"]
        elif m["type"] == "http.response.body":
            out["body"] += m.get("body", b"")

    try:
        await asyncio.wait_for(s.app(scope, receive, send), 90)
    except Exception as e:
        return 500, f"replay failed: {e}"
    try:
        d = json.loads(out["body"] or b"{}")
    except ValueError:
        d = {}
    if not isinstance(d, dict):
        d = {}
    msg = d.get("message") or d.get("error") or (d.get("stdout") or "").strip()[-300:]
    if not msg:
        msg = "done" if out["status"] < 400 else f"HTTP {out['status']}"
    return out["status"], str(msg)


# ── notify hook ──────────────────────────────────────────────────────────────
_tasks: set = set()


async def notify_now(rec: dict, base_url: str = "") -> None:
    cmd = notify_command().strip()
    if not cmd:
        return
    env = {**os.environ, "LW_ID": rec["id"], "LW_KIND": rec["kind"], "LW_TITLE": rec["title"],
           "LW_REQUESTER": rec["requester"], "LW_COMMAND": rec.get("command") or "", "LW_TARGET": rec.get("target") or "",
           "LW_URL": (base_url.rstrip("/") + "/approvals") if base_url else "/approvals"}
    try:
        p = await asyncio.create_subprocess_shell(cmd, env=env, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        try:
            _, err = await asyncio.wait_for(p.communicate(), 10)
        except asyncio.TimeoutError:
            p.kill()
            await p.communicate()
            raise RuntimeError("notify command took longer than 10 s")
        if p.returncode != 0:
            audit.append("approval_notify", target=rec["id"], outcome="fail", actor="system",
                         detail=(err or b"").decode("utf-8", "replace").strip()[-200:] or f"exit {p.returncode}")
    except Exception as e:
        audit.append("approval_notify", target=rec["id"], outcome="fail", actor="system", detail=str(e)[:200])


def notify(rec: dict, base_url: str = "") -> None:
    """Run the owner's notify command in the background; never raises."""
    if not notify_command().strip():
        return
    try:
        t = asyncio.get_running_loop().create_task(notify_now(rec, base_url))
    except RuntimeError:
        return
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
