"""Scheduled jobs on this machine, in one list.

Four sources: the user's systemd timers, the system's systemd timers, the
user's crontab, and research runs in progress. Each becomes a job record with
the same shape, so the Jobs page can show them together:

    id, kind, name, description, schedule, next, last, state, can_act

Times are epoch seconds. ``state`` is one of ok, failed, running, paused, off,
unknown. Only user timers can be run or paused from the page.
"""

import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

_CACHE_SECS = 10
_cache: dict = {"at": 0.0, "data": None}
_lock = threading.Lock()

SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@:\\-]{0,127}$").match


def _run(cmd: list[str], timeout: float = 8) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""
    return p.stdout if p.returncode == 0 else ""


# ── systemd ──────────────────────────────────────────────────────────
_TIMER_PROPS = "Id,Description,ActiveState,UnitFileState,TimersCalendar,TimersMonotonic,NextElapseUSecRealtime,LastTriggerUSec,Unit,FragmentPath"
_SERVICE_PROPS = "Id,Description,ActiveState,Result,ExecMainStatus,ExecMainStartTimestamp,ExecMainExitTimestamp"


def _systemctl(system: bool) -> list[str]:
    return ["systemctl"] if system else ["systemctl", "--user"]


def parse_show(text: str) -> dict[str, dict]:
    """``systemctl show a b -p ...`` output: blocks per unit, keyed by Id.

    A property that repeats (TimersMonotonic can) is kept as a list.
    """
    units: dict[str, dict] = {}
    for block in re.split(r"\n\s*\n", text.strip()):
        props: dict = {}
        for line in block.splitlines():
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k in props:
                props[k] = (props[k] if isinstance(props[k], list) else [props[k]]) + [v]
            else:
                props[k] = v
        if props.get("Id"):
            units[props["Id"]] = props
    return units


def systemd_time(raw) -> float | None:
    """'Mon 2026-10-12 02:40:00 BST' -> epoch seconds (local clock), else None."""
    if not raw or not isinstance(raw, str):
        return None
    m = re.search(r"(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})", raw)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + " " + m.group(2), "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        return None


_DOW = {"Mon": "Mon", "Tue": "Tue", "Wed": "Wed", "Thu": "Thu", "Fri": "Fri", "Sat": "Sat", "Sun": "Sun"}


def schedule_words(calendar, monotonic) -> str:
    """Put a systemd schedule into words; fall back to the expression itself."""
    out = []
    for raw in _as_list(calendar):
        m = re.search(r"OnCalendar=([^;}]+)", raw)
        if m:
            out.append(_calendar_words(m.group(1).strip()))
    for raw in _as_list(monotonic):
        m = re.search(r"On(\w+?)USec=([^;}]+)", raw)
        if not m:
            continue
        kind, span = m.group(1), _span(m.group(2).strip())
        if kind == "UnitActive":
            out.append(f"every {span} after the last run")
        elif kind == "UnitInactive":
            out.append(f"{span} after the last run ends")
        elif kind == "Boot" or kind == "Startup":
            out.append(f"{span} after boot")
        elif kind == "Active":
            out.append(f"{span} after the timer starts")
        else:
            out.append(f"{kind} {span}")
    return "; ".join(dict.fromkeys(out)) or "no schedule"


def _as_list(v) -> list:
    if not v:
        return []
    return v if isinstance(v, list) else [v]


def _span(s: str) -> str:
    """systemd spans with a space and plain units: '5min' -> '5 min', '1d' -> '1 day'."""
    units = {"usec": "µs", "ms": "ms", "s": "s", "min": "min", "h": "h", "d": "day", "w": "week", "month": "month", "y": "year"}
    def one(m):
        n, u = m.group(1), units.get(m.group(2), m.group(2))
        if u in ("day", "week", "month", "year") and n != "1":
            u += "s"
        return f"{n} {u}"
    return re.sub(r"(\d+(?:\.\d+)?)\s*([a-zµ]+)", one, s)


def _calendar_words(expr: str) -> str:
    e = expr.strip()
    tz = ""
    m = re.search(r"\s([A-Za-z]+/[A-Za-z_]+|UTC)$", e)   # trailing time zone, e.g. Europe/London
    if m:
        tz, e = f" ({m.group(1)})", e[:m.start()].strip()
    return _calendar_core(e) + tz


def _calendar_core(e: str) -> str:
    words = {"minutely": "every minute", "hourly": "every hour", "daily": "daily at 00:00",
             "weekly": "weekly, Mon 00:00", "monthly": "monthly, 1st 00:00", "yearly": "yearly, 1 Jan",
             "quarterly": "quarterly", "semiannually": "twice a year"}
    if e.lower() in words:
        return words[e.lower()]
    # "*:0/5", "*:0/5:00", "*-*-* *:00/5:00" -> every 5 min
    m = re.fullmatch(r"(?:\*-\*-\* )?\*:0?0/(\d+)(?::00)?", e)
    if m:
        return f"every {m.group(1)} min"
    m = re.fullmatch(r"(?:\*-\*-\* )?\*:0?0(?::00)?", e)
    if m:
        return "every hour"
    # "0/6:00", "*-*-* 00/6:00:00" -> every 6 h
    m = re.fullmatch(r"(?:\*-\*-\* )?0?0/(\d+):00(?::00)?", e)
    if m:
        return f"every {m.group(1)} h"
    # "*-*-* *:05,35:00" -> hourly at :05 and :35
    m = re.fullmatch(r"(?:\*-\*-\* )?\*:(\d{1,2}(?:,\d{1,2})+)(?::00)?", e)
    if m:
        mins = [f":{int(x):02d}" for x in m.group(1).split(",")]
        return "hourly at " + ", ".join(mins[:-1]) + " and " + mins[-1]
    # "Mon *-*-* 02:40:00", "Mon,Thu *-*-* 02:40:00", "*-*-* 02:40:00", "*-*-01 04:00:00"
    m = re.fullmatch(r"(?:([A-Za-z,.…-]+) )?\*-\*-(\*|\d{2}) (\d{2}):(\d{2})(?::\d{2})?", e)
    if m:
        dow, dom, hh, mm = m.groups()
        when = f"{hh}:{mm}"
        if dow:
            return f"{dow} {when}"
        if dom != "*":
            return f"monthly on the {int(dom)}{_ordinal(int(dom))} at {when}"
        return f"daily {when}"
    m = re.fullmatch(r"\*-\*-\* (\d{1,2}(?:,\d{1,2})+):(\d{2})(?::\d{2})?", e)
    if m:
        return "daily at " + ", ".join(f"{int(h):02d}:{m.group(2)}" for h in m.group(1).split(","))
    return e


def _ordinal(n: int) -> str:
    return "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def _service_last(svc: dict) -> dict | None:
    start, end = systemd_time(svc.get("ExecMainStartTimestamp")), systemd_time(svc.get("ExecMainExitTimestamp"))
    if start is None and end is None:
        return None
    code, result = (svc.get("ExecMainStatus") or "0"), (svc.get("Result") or "success")
    ok = code == "0" and result in ("success", "")
    detail = ""
    if not ok:
        detail = f"exit code {code}" if code != "0" else result
    return {"at": start, "end": end, "ok": ok, "detail": detail}


def _systemd_jobs(system: bool) -> list[dict]:
    base = _systemctl(system)
    listing = _run(base + ["list-timers", "--all", "--output=json"])
    if not listing:
        return []
    try:
        timers = json.loads(listing)
    except ValueError:
        return []
    names = [t.get("unit") for t in timers if t.get("unit")]
    services = [t.get("activates") for t in timers if t.get("activates")]
    if not names:
        return []
    tprops = parse_show(_run(base + ["show", *names, "-p", _TIMER_PROPS]))
    sprops = parse_show(_run(base + ["show", *services, "-p", _SERVICE_PROPS])) if services else {}
    kind = "system" if system else "user"
    out = []
    for t in timers:
        unit, act = t.get("unit", ""), t.get("activates", "")
        tp, sp = tprops.get(unit, {}), sprops.get(act, {})
        name = unit[:-6] if unit.endswith(".timer") else unit
        if not SAFE_NAME(name):
            continue
        last = _service_last(sp)
        nxt = (t.get("next") or 0) / 1e6 or None
        active = tp.get("ActiveState") == "active"
        if sp.get("ActiveState") in ("active", "activating", "reloading", "deactivating"):
            state = "running"
        elif not active:
            state = "off" if tp.get("UnitFileState") in ("disabled", "masked") else "paused"
        elif last is None:
            state = "unknown"
        else:
            state = "ok" if last["ok"] else "failed"
        job = {
            "id": f"{kind}:{name}", "kind": kind, "name": name,
            "description": tp.get("Description") or sp.get("Description") or "",
            "schedule": schedule_words(tp.get("TimersCalendar"), tp.get("TimersMonotonic")),
            "next": nxt if state not in ("paused", "off") else None,
            "last": last, "state": state, "can_act": not system,
            "units": {"timer": unit, "service": act},
            "managed": False,
        }
        if not system and is_managed(tp.get("FragmentPath")):
            job["managed"] = True
            job["spec"] = read_spec(name)
        out.append(job)
    return out


# ── jobs created from the page ───────────────────────────────────────
# A job made here is a pair of user units plus a small shell script that holds
# the command verbatim (so no systemd quoting rules apply to what the user
# typed). The units start with MARKER, which is how the page tells its own
# jobs from everyone else's. Only those can be edited or deleted.
_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
UNIT_DIR = _CONFIG_HOME / "systemd" / "user"
SCRIPT_DIR = _CONFIG_HOME / "llamawatch" / "jobs"
MARKER = "# Created by llamawatch. Edit it from the Jobs page, not by hand."
JOB_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$").match
_SCRIPT_SEP = "# --- your command, exactly as typed ---"


class JobError(ValueError):
    """A problem the user can fix; carries an HTTP status."""
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def _sh(cmd: list[str], timeout: float = 20) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def setup() -> dict:
    """Can this machine take new jobs? systemd user manager reachable, and does it linger."""
    p = _sh(["systemctl", "--user", "show", "-p", "Version"], timeout=8)
    if p is None:
        return {"available": False, "linger": None, "reason": "systemd is not available on this system, so jobs can only be viewed here."}
    if p.returncode != 0:
        return {"available": False, "linger": None, "reason": "the systemd user session is not reachable, so jobs can only be viewed here."}
    linger = None
    q = _sh(["loginctl", "show-user", str(os.getuid()), "-p", "Linger", "--value"], timeout=8)
    if q is not None and q.returncode == 0:
        linger = q.stdout.strip() == "yes"
    return {"available": True, "linger": linger, "reason": ""}


def is_managed(fragment_path) -> bool:
    if not fragment_path or not isinstance(fragment_path, str):
        return False
    p = Path(fragment_path)
    try:
        if p.parent.resolve() != UNIT_DIR.resolve():
            return False
        with p.open() as fh:
            return fh.readline().rstrip("\n") == MARKER
    except OSError:
        return False


def read_spec(name: str) -> dict:
    """What the page needs to show the edit form: command, schedule text, description."""
    spec = {"name": name, "description": "", "command": "", "schedule": ""}
    try:
        for line in (UNIT_DIR / f"{name}.timer").read_text().splitlines():
            if line.startswith("Description="):
                spec["description"] = line[12:]
            elif line.startswith("OnCalendar="):
                spec["schedule"] = line[11:]
        text = (SCRIPT_DIR / f"{name}.sh").read_text()
        head, sep, body = text.partition(_SCRIPT_SEP + "\n")
        spec["command"] = body.strip() if sep else text.strip()
    except OSError:
        pass
    return spec


_DAY_WORDS = {"mon": "Mon", "monday": "Mon", "tue": "Tue", "tues": "Tue", "tuesday": "Tue", "wed": "Wed", "wednesday": "Wed",
              "thu": "Thu", "thur": "Thu", "thurs": "Thu", "thursday": "Thu", "fri": "Fri", "friday": "Fri",
              "sat": "Sat", "saturday": "Sat", "sun": "Sun", "sunday": "Sun"}


def _hhmm(h: str, m: str | None) -> str:
    hh, mm = int(h), int(m or 0)
    if hh > 23 or mm > 59:
        raise JobError("that time does not exist; use HH:MM, 24-hour clock")
    return f"{hh:02d}:{mm:02d}:00"


def schedule_from_words(text: str) -> str:
    """Plain words -> a systemd OnCalendar expression.

    Accepts "every 15 min", "hourly", "daily 06:30", "every day at 6:30",
    "06:30", "Mon 02:00", "every monday at 2:00", "mon,wed,fri 09:00",
    "weekly", "monthly on the 1st at 04:00". Anything else is taken as a
    systemd expression and checked by `check_calendar`.
    """
    t = " ".join(text.strip().split())
    low = t.lower().rstrip(".")
    if not low:
        raise JobError("a schedule is needed, for example 'every 15 min' or 'daily 06:30'")
    if low in ("hourly", "every hour", "each hour"):
        return "hourly"
    if low in ("every minute", "minutely"):
        return "minutely"
    if low in ("daily", "every day", "midnight"):
        return "daily"
    if low in ("weekly", "every week"):
        return "weekly"
    if low in ("monthly", "every month"):
        return "monthly"
    m = re.fullmatch(r"every (\d+) ?(m|min|mins|minute|minutes)", low)
    if m:
        n = int(m.group(1))
        if not 1 <= n <= 59:
            raise JobError("minutes must be between 1 and 59; for longer gaps use hours")
        return f"*:0/{n}"
    m = re.fullmatch(r"every (\d+) ?(h|hr|hrs|hour|hours)", low)
    if m:
        n = int(m.group(1))
        if not 1 <= n <= 23:
            raise JobError("hours must be between 1 and 23; for once a day use 'daily HH:MM'")
        return "hourly" if n == 1 else f"0/{n}:00"
    time_re = r"(?:at )?(?P<h>\d{1,2})(?::(?P<mi>\d{2}))?\s*(?P<ap>am|pm)?"
    def tm(mm):
        hh = int(mm.group("h"))
        if mm.group("ap") == "pm" and hh < 12:
            hh += 12
        if mm.group("ap") == "am" and hh == 12:
            hh = 0
        return _hhmm(str(hh), mm.group("mi"))
    m = re.fullmatch(r"(?:daily|every day|each day) " + time_re, low)
    if m:
        return "*-*-* " + tm(m)
    m = re.fullmatch(time_re, low)
    if m and (m.group("mi") or m.group("ap")):
        return "*-*-* " + tm(m)
    days = r"(?P<days>(?:" + "|".join(_DAY_WORDS) + r")s?(?:\s*,\s*(?:" + "|".join(_DAY_WORDS) + r")s?)*)"
    m = re.fullmatch(r"(?:every |each |weekly |on )?" + days + r"(?: at)? " + time_re, low)
    if m:
        names = [_DAY_WORDS[d.strip().rstrip("s")] for d in m.group("days").split(",")]
        return ",".join(dict.fromkeys(names)) + " *-*-* " + tm(m)
    m = re.fullmatch(r"(?:monthly|every month|each month)(?: on)?(?: the)? (?P<dom>\d{1,2})(?:st|nd|rd|th)?(?: at)? " + time_re, low)
    if m:
        dom = int(m.group("dom"))
        if not 1 <= dom <= 28:
            raise JobError("pick a day between 1 and 28 so the job runs every month")
        return f"*-*-{dom:02d} " + tm(m)
    return t   # a systemd expression, checked next


def check_calendar(expr: str) -> dict:
    """Ask systemd whether an OnCalendar expression is valid; return its words and next run."""
    p = _sh(["systemd-analyze", "calendar", expr], timeout=8)
    if p is None:
        # no systemd-analyze: accept only shapes our own words parser knows
        if _calendar_core(expr) == expr:
            raise JobError("that schedule could not be checked on this system; use plain words like 'daily 06:30'")
        return {"calendar": expr, "words": _calendar_words(expr), "next": None}
    if p.returncode != 0:
        err = (p.stderr or p.stdout).strip().splitlines()
        raise JobError("systemd does not understand that schedule" + (f": {err[-1]}" if err else ""))
    nxt = None
    for line in p.stdout.splitlines():
        s = line.strip()
        if s.startswith("Next elapse:"):
            nxt = systemd_time(s)
        elif s.startswith("Normalized form:"):
            expr = s.split(":", 1)[1].strip()
    return {"calendar": expr, "words": _calendar_words(expr), "next": nxt}


def preview(schedule: str) -> dict:
    return check_calendar(schedule_from_words(schedule))


def _validate(fields: dict, *, new: bool) -> dict:
    name = str(fields.get("name") or "").strip().lower()
    if not JOB_NAME(name):
        raise JobError("the name can only have lowercase letters, numbers and dashes, up to 40 characters")
    command = str(fields.get("command") or "").strip()
    if not command:
        raise JobError("a command is needed")
    if len(command) > 4000:
        raise JobError("the command is too long (4000 characters at most)")
    from . import approvals
    if (reason := approvals.check_command(command)) is not None:
        raise JobError(f"that command is always blocked: {reason}")
    description = " ".join(str(fields.get("description") or "").split())[:200]
    cal = check_calendar(schedule_from_words(str(fields.get("schedule") or "")))
    if new:
        if (UNIT_DIR / f"{name}.timer").exists() or (UNIT_DIR / f"{name}.service").exists():
            raise JobError(f"a job called {name} already exists", 409)
        p = _sh(["systemctl", "--user", "show", f"{name}.timer", "-p", "LoadState", "--value"], timeout=8)
        if p is not None and p.returncode == 0 and p.stdout.strip() == "loaded":
            raise JobError(f"a timer called {name} already exists on this system", 409)
    return {"name": name, "command": command, "description": description, "calendar": cal["calendar"], "words": cal["words"], "next": cal["next"]}


def render_units(v: dict) -> tuple[str, str, str]:
    """(timer text, service text, script text) for a validated job."""
    desc = v["description"] or v["name"]
    script = SCRIPT_DIR / f"{v['name']}.sh"
    timer = "\n".join([
        MARKER, "[Unit]", f"Description={desc}", "",
        "[Timer]", f"OnCalendar={v['calendar']}", "Persistent=true", "",
        "[Install]", "WantedBy=timers.target", ""])
    service = "\n".join([
        MARKER, "[Unit]", f"Description={desc}", "",
        "[Service]", "Type=oneshot", f"ExecStart=/bin/sh {script}", f"WorkingDirectory={Path.home()}", ""])
    sh = "\n".join(["#!/bin/sh", MARKER, f"# Job: {v['name']}", "set -e", _SCRIPT_SEP, v["command"], ""])
    return timer, service, sh


def _write_units(v: dict) -> None:
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    SCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    timer, service, sh = render_units(v)
    (UNIT_DIR / f"{v['name']}.timer").write_text(timer)
    (UNIT_DIR / f"{v['name']}.service").write_text(service)
    script = SCRIPT_DIR / f"{v['name']}.sh"
    script.write_text(sh)
    script.chmod(0o700)


def _remove_units(name: str) -> None:
    for p in (UNIT_DIR / f"{name}.timer", UNIT_DIR / f"{name}.service", SCRIPT_DIR / f"{name}.sh"):
        try:
            p.unlink()
        except FileNotFoundError:
            pass


def _ctl(*args: str) -> None:
    p = _sh(["systemctl", "--user", *args], timeout=30)
    if p is None:
        raise JobError("systemctl did not answer", 500)
    if p.returncode != 0:
        raise JobError((p.stderr or p.stdout or "systemctl failed").strip().splitlines()[-1], 500)


def _forget() -> None:
    with _lock:
        _cache.update(at=0.0, data=None)


def create_job(fields: dict) -> dict:
    v = _validate(fields, new=True)
    _write_units(v)
    try:
        _ctl("daemon-reload")
        _ctl("enable", "--now", f"{v['name']}.timer")
    except JobError:
        _remove_units(v["name"])
        _sh(["systemctl", "--user", "daemon-reload"], timeout=30)
        raise
    _forget()
    return {"id": f"user:{v['name']}", "name": v["name"], "schedule": v["words"], "next": v["next"]}


def update_job(name: str, fields: dict) -> dict:
    if not is_managed(str(UNIT_DIR / f"{name}.timer")):
        raise JobError("only jobs created from this page can be edited here")
    v = _validate({**fields, "name": name}, new=False)
    _write_units(v)
    _ctl("daemon-reload")
    _ctl("restart", f"{name}.timer")
    _forget()
    return {"id": f"user:{name}", "name": name, "schedule": v["words"], "next": v["next"]}


def delete_job(name: str) -> dict:
    if not JOB_NAME(name) or not is_managed(str(UNIT_DIR / f"{name}.timer")):
        raise JobError("only jobs created from this page can be deleted here")
    _sh(["systemctl", "--user", "disable", "--now", f"{name}.timer"], timeout=30)
    _sh(["systemctl", "--user", "stop", f"{name}.service"], timeout=30)
    _remove_units(name)
    _ctl("daemon-reload")
    _sh(["systemctl", "--user", "reset-failed", f"{name}.service", f"{name}.timer"], timeout=30)
    _forget()
    return {"id": f"user:{name}", "name": name}


# ── cron ─────────────────────────────────────────────────────────────
_ALIASES = {"@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *", "@monthly": "0 0 1 * *",
            "@weekly": "0 0 * * 0", "@daily": "0 0 * * *", "@midnight": "0 0 * * *", "@hourly": "0 * * * *"}
_MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
_DAYS = {d: i for i, d in enumerate("sun mon tue wed thu fri sat".split())}


def _field(spec: str, lo: int, hi: int, names: dict | None = None) -> set[int] | None:
    vals: set[int] = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            if not s.isdigit() or int(s) < 1:
                return None
            step = int(s)
        if part == "*":
            a, b = lo, hi
        else:
            if names:
                part = "-".join(str(names.get(p.lower(), p)) for p in part.split("-"))
            if "-" in part:
                a_s, b_s = part.split("-", 1)
            else:
                a_s = b_s = part
            if not (a_s.isdigit() and b_s.isdigit()):
                return None
            a, b = int(a_s), int(b_s)
            if "/" in spec and "-" not in part and part != "*":
                b = hi   # "5/10" means from 5 every 10
        if a < lo or b > hi or a > b:
            return None
        vals.update(range(a, b + 1, step))
    return vals


def parse_cron(expr: str) -> dict | None:
    """Five cron fields (or an @alias) into sets; None when it is not valid cron."""
    expr = _ALIASES.get(expr.strip().lower(), expr.strip())
    parts = expr.split()
    if len(parts) != 5:
        return None
    mins, hours, dom, mon, dow = (_field(parts[0], 0, 59), _field(parts[1], 0, 23), _field(parts[2], 1, 31),
                                  _field(parts[3], 1, 12, _MONTHS), _field(parts[4], 0, 7, _DAYS))
    if None in (mins, hours, dom, mon, dow):
        return None
    if 7 in dow:
        dow = (dow - {7}) | {0}
    return {"min": mins, "hour": hours, "dom": dom, "mon": mon, "dow": dow,
            "any_dom": parts[2] == "*", "any_dow": parts[4] == "*"}


def cron_next(spec: dict, now: float) -> float | None:
    """Next fire time after *now* (local clock), within a year; None if never."""
    t = datetime.fromtimestamp(now).replace(second=0, microsecond=0) + timedelta(minutes=1)
    day = t.replace(hour=0, minute=0)
    for _ in range(367):
        if day.month in spec["mon"]:
            dom_ok = day.day in spec["dom"]
            dow_ok = ((day.weekday() + 1) % 7) in spec["dow"]   # cron: Sunday = 0
            # Like cron: when both day fields are set, either one matching counts.
            if spec["any_dom"] or spec["any_dow"]:
                day_ok = dom_ok and dow_ok
            else:
                day_ok = dom_ok or dow_ok
            if day_ok:
                for h in sorted(spec["hour"]):
                    for m in sorted(spec["min"]):
                        cand = day.replace(hour=h, minute=m)
                        if cand >= t:
                            return cand.timestamp()
        day += timedelta(days=1)
    return None


def cron_words(expr: str) -> str:
    e = _ALIASES.get(expr.strip().lower(), expr.strip())
    m = re.fullmatch(r"\*/(\d+) \* \* \* \*", e)
    if m:
        return f"every {m.group(1)} min"
    m = re.fullmatch(r"(\d+) \*/(\d+) \* \* \*", e)
    if m:
        return f"every {m.group(2)} h at :{int(m.group(1)):02d}"
    m = re.fullmatch(r"(\d+) (\d+) \* \* \*", e)
    if m:
        return f"daily {int(m.group(2)):02d}:{int(m.group(1)):02d}"
    m = re.fullmatch(r"(\d+) (\d+) \* \* ([0-7])", e)
    if m:
        names = "Sun Mon Tue Wed Thu Fri Sat Sun".split()
        return f"{names[int(m.group(3))]} {int(m.group(2)):02d}:{int(m.group(1)):02d}"
    m = re.fullmatch(r"(\d+) \* \* \* \*", e)
    if m:
        return f"every hour at :{int(m.group(1)):02d}"
    return e


def parse_crontab(text: str, now: float) -> list[dict]:
    jobs = []
    n = 0
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", s):
            continue
        if s.startswith("@"):
            parts = s.split(None, 1)
        else:
            parts = s.split(None, 5)
            parts = [" ".join(parts[:5]), parts[5] if len(parts) > 5 else ""]
        if len(parts) < 2 or not parts[1]:
            continue
        expr, cmd = parts
        spec = parse_cron(expr)
        if spec is None:
            continue
        n += 1
        name = _cron_name(cmd)
        jobs.append({
            "id": f"cron:{n}", "kind": "cron", "name": name, "description": cmd[:200],
            "schedule": cron_words(expr), "next": cron_next(spec, now), "last": None,
            "state": "unknown", "can_act": False, "units": {},
        })
    return jobs


def _cron_name(cmd: str) -> str:
    """A short label from the command: the first script or program named."""
    words = cmd.split()
    for w in words:
        base = os.path.basename(w.strip("\"'"))
        if base and base not in ("docker", "exec", "python3", "python", "bash", "sh", "nice", "flock", "-n", "timeout") \
                and not base.startswith("-") and not base.isdigit() and "=" not in base:
            return base[:60]
    return (words[0] if words else "cron job")[:60]


def _cron_jobs(now: float) -> list[dict]:
    return parse_crontab(_run(["crontab", "-l"]), now)


# ── research runs ────────────────────────────────────────────────────
def _research_jobs(config: dict | None) -> list[dict]:
    try:
        from .research.store import list_runs
    except Exception:
        return []
    cfg = (config or {}).get("research", {}) or {}
    root = Path(cfg.get("data_dir") or Path.home() / ".local" / "share" / "llamawatch" / "research").expanduser()
    if not root.is_dir():
        return []
    out = []
    for r in list_runs(root):
        if r.get("status") != "running":
            continue
        started = r.get("started")
        if not started:
            try:
                started = (root / r["id"] / "run.json").stat().st_mtime
            except OSError:
                started = None
        q = (r.get("question") or "").strip()
        out.append({
            "id": f"research:{r['id']}", "kind": "research", "name": "Research: " + (q[:70] + ("…" if len(q) > 70 else "")),
            "description": f"{r.get('depth', '')} on {r.get('model', '')}".strip(" on"),
            "schedule": "one-off", "next": None,
            "last": {"at": started, "end": None, "ok": True, "detail": ""} if started else None,
            "state": "running", "can_act": False, "units": {}, "href": f"/research#run/{r['id']}",
        })
    return out


# ── all together ─────────────────────────────────────────────────────
def collect(config: dict | None = None, fresh: bool = False) -> dict:
    now = time.time()
    with _lock:
        if not fresh and _cache["data"] is not None and now - _cache["at"] < _CACHE_SECS:
            return _cache["data"]
    jobs = _systemd_jobs(False) + _systemd_jobs(True) + _cron_jobs(now) + _research_jobs(config)
    order = {"running": 0, "failed": 1, "ok": 2, "unknown": 2, "paused": 3, "off": 4}
    jobs.sort(key=lambda j: (order.get(j["state"], 5), j["next"] if j["next"] is not None else 9e12, j["name"]))
    counts = {"total": len(jobs)}
    for j in jobs:
        counts[j["state"]] = counts.get(j["state"], 0) + 1
    data = {"jobs": jobs, "counts": counts, "at": now, "setup": setup()}
    with _lock:
        _cache.update(at=now, data=data)
    return data


def find(job_id: str, config: dict | None = None) -> dict | None:
    return next((j for j in collect(config)["jobs"] if j["id"] == job_id), None)


def journal(job: dict, lines: int = 30) -> list[str]:
    """The last journal lines of a systemd job's service, oldest first."""
    unit = (job.get("units") or {}).get("service")
    if not unit or job["kind"] not in ("user", "system"):
        return []
    base = _systemctl(job["kind"] == "system")
    cmd = ["journalctl"] + base[1:] + ["-u", unit, "-n", str(lines), "--no-pager", "-o", "short-iso"]
    text = _run(cmd, timeout=8)
    return [ln for ln in text.splitlines() if ln and not ln.startswith("-- ")]
