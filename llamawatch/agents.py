"""Agents: what is running now, and a replay of what each run did.

Four kinds of agent, all found without configuration beyond what llamawatch
already has:

* Claude Code sessions (one transcript per session under the Claude config
  dir), * Codex rollouts (best effort on the rollout format), * Research runs
  made by llamawatch itself, * the configured agents from ``config["agents"]``
  (containers on a machine), whose state comes from the Docker collector.

Transcripts are summarised once per (path, mtime, size) and kept in memory,
so a poll only re-reads files that changed. Nothing older than ``WINDOW_DAYS``
is opened. Nothing here changes the machine.
"""

from __future__ import annotations

import glob
import json
import os
import re
import time
from pathlib import Path

from .collectors import token_usage as tu

WINDOW_DAYS = 7
MAX_RUNS = 200
MAX_STEPS = 500
WORKING_SECS = 120          # a transcript touched this recently is "working"
TITLE_LEN = 80
LINE_LEN = 90
_ID = re.compile(r"^(claude|codex|research):[A-Za-z0-9._-]{1,120}$")
_SKIP_TOOLS = {"TodoWrite"}  # bookkeeping calls that would drown the real steps

_cache: dict[str, tuple[float, int, dict]] = {}   # path -> (mtime, size, summary)


# ── small helpers ────────────────────────────────────────────────────────────

def _cut(s, n: int) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _project(cwd: str) -> str:
    return os.path.basename((cwd or "").rstrip("/")) or ""


def _text_of(content) -> str:
    """First text block of a message's content, as a line."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") in ("text", "input_text", "output_text") and b.get("text"):
                return b["text"]
    return ""


def tool_summary(name: str, inp) -> str:
    """One short line for a tool call: what it did, not how."""
    inp = inp if isinstance(inp, dict) else {}
    if name == "Bash":
        return _cut(inp.get("description") or inp.get("command"), LINE_LEN)
    if name in ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit"):
        return os.path.basename(str(inp.get("file_path") or inp.get("notebook_path") or ""))
    if name == "Glob":
        return _cut(inp.get("pattern"), LINE_LEN)
    if name == "Grep":
        return _cut(inp.get("pattern"), LINE_LEN)
    if name in ("Agent", "Task"):
        return _cut(inp.get("description") or inp.get("prompt"), LINE_LEN)
    if name in ("WebFetch", "WebSearch"):
        return _cut(inp.get("url") or inp.get("query"), LINE_LEN)
    if name == "Skill":
        return _cut(inp.get("skill"), LINE_LEN)
    for v in inp.values():
        if isinstance(v, str) and v.strip():
            return _cut(v, LINE_LEN)
        if isinstance(v, list) and v and all(isinstance(x, str) for x in v):
            return _cut(" ".join(v), LINE_LEN)
    return ""


def _usage(msg: dict) -> tuple[int, int, int]:
    u = msg.get("usage") if isinstance(msg, dict) else None
    if not isinstance(u, dict):
        return 0, 0, 0
    cache = int(u.get("cache_read_input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0)
    return int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0), cache


# ── Claude Code ──────────────────────────────────────────────────────────────

def _parse_claude(path: str) -> dict:
    """Summary of one Claude Code session transcript, steps included."""
    steps: list[dict] = []
    title = ""; first_prompt = ""; cwd = ""; session = os.path.splitext(os.path.basename(path))[0]
    t_in = t_out = t_cache = 0; thinking = 0; seen_msgs: set = set(); seen_blocks: set = set()
    started = last = None; last_prompt_t = None; last_prompt_text = ""
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except Exception:
                continue
            kind = d.get("type")
            if kind == "ai-title" and d.get("aiTitle"):
                title = _cut(d["aiTitle"], TITLE_LEN)
                continue
            if kind not in ("user", "assistant") or d.get("isSidechain"):
                continue
            when = tu._ts(d.get("timestamp"))
            if when is None:
                continue
            cwd = d.get("cwd") or cwd
            started = when if started is None else min(started, when)
            last = when if last is None else max(last, when)
            msg = d.get("message") if isinstance(d.get("message"), dict) else {}
            content = msg.get("content")
            if kind == "user":
                if d.get("isMeta") or d.get("isCompactSummary"):
                    continue
                if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                    continue
                text = _text_of(content)
                if not text or text.startswith("<"):
                    continue
                last_prompt_t, last_prompt_text = when, _cut(text, TITLE_LEN)
                first_prompt = first_prompt or last_prompt_text
                steps.append({"t": when, "kind": "prompt", "label": "Prompt", "detail": last_prompt_text})
                continue
            mid = msg.get("id")
            if mid and mid in seen_msgs:
                tokens = (0, 0, 0)          # one API call is logged once per content block
            else:
                seen_msgs.add(mid)
                tokens = _usage(msg)
            t_in += tokens[0]; t_out += tokens[1]; t_cache += tokens[2]
            if not isinstance(content, list):
                continue
            for b in content:
                if not isinstance(b, dict):
                    continue
                bt = b.get("type")
                key = (mid, b.get("id") or json.dumps(b, sort_keys=True)[:400])
                if mid and key in seen_blocks:     # the same block logged twice
                    continue
                seen_blocks.add(key)
                if bt == "thinking":
                    thinking += 1
                elif bt == "tool_use":
                    name = str(b.get("name") or "tool")
                    if name in _SKIP_TOOLS:
                        continue
                    steps.append({"t": when, "kind": "tool", "label": name, "detail": tool_summary(name, b.get("input")),
                                  "in": tokens[0], "out": tokens[1], "cache": tokens[2]})
                    tokens = (0, 0, 0)
                elif bt == "text" and b.get("text") and b["text"].strip():
                    steps.append({"t": when, "kind": "text", "label": "Answer", "detail": _cut(b["text"], LINE_LEN),
                                  "in": tokens[0], "out": tokens[1], "cache": tokens[2]})
                    tokens = (0, 0, 0)
    return {"id": "claude:" + session, "agent": "Claude Code", "title": title or first_prompt or session[:8],
            "project": _project(cwd), "cwd": cwd, "started": started, "last": last, "steps": steps,
            "in": t_in, "out": t_out, "cache": t_cache, "thinking": thinking,
            "turn_started": last_prompt_t, "turn_prompt": last_prompt_text}


# ── Codex ────────────────────────────────────────────────────────────────────

def _parse_codex(path: str) -> dict:
    """Summary of one Codex rollout. Best effort: the format has no contract."""
    steps: list[dict] = []
    cwd = ""; sid = os.path.splitext(os.path.basename(path))[0]; first_prompt = ""
    started = last = None; prev_tot: dict = {}; t_in = t_out = t_cache = 0; last_prompt_t = None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except Exception:
                continue
            when = tu._ts(d.get("timestamp"))
            p = d.get("payload") if isinstance(d.get("payload"), dict) else {}
            kind = d.get("type"); pt = p.get("type")
            if kind == "session_meta":
                cwd = p.get("cwd") or cwd
                sid = str(p.get("id") or sid)
                when = when or tu._ts(p.get("timestamp"))
            if when is None:
                continue
            started = when if started is None else min(started, when)
            last = when if last is None else max(last, when)
            if pt == "token_count":
                tot = (p.get("info") or {}).get("total_token_usage") or {}
                if tot:
                    cached = max(0, int(tot.get("cached_input_tokens", 0)) - int(prev_tot.get("cached_input_tokens", 0)))
                    t_in += max(0, int(tot.get("input_tokens", 0)) - int(prev_tot.get("input_tokens", 0)) - cached)
                    t_out += max(0, int(tot.get("output_tokens", 0)) - int(prev_tot.get("output_tokens", 0)))
                    t_cache += cached
                    prev_tot = tot
            elif pt == "function_call":
                name = str(p.get("name") or "tool")
                args = p.get("arguments")
                try:
                    args = json.loads(args) if isinstance(args, str) else args
                except Exception:
                    args = {"arguments": str(args)}
                steps.append({"t": when, "kind": "tool", "label": name, "detail": tool_summary(name, args)})
            elif pt == "message":
                text = _text_of(p.get("content"))
                role = p.get("role")
                if role == "user" and text and not text.startswith("<"):
                    last_prompt_t = when
                    first_prompt = first_prompt or _cut(text, TITLE_LEN)
                    steps.append({"t": when, "kind": "prompt", "label": "Prompt", "detail": _cut(text, TITLE_LEN)})
                elif role == "assistant" and text:
                    steps.append({"t": when, "kind": "text", "label": "Answer", "detail": _cut(text, LINE_LEN)})
            elif pt == "agent_message" and p.get("message"):
                steps.append({"t": when, "kind": "text", "label": "Answer", "detail": _cut(p["message"], LINE_LEN)})
    return {"id": "codex:" + sid, "agent": "Codex", "title": first_prompt or sid[:8], "project": _project(cwd), "cwd": cwd,
            "started": started, "last": last, "steps": steps, "in": t_in, "out": t_out, "cache": t_cache, "thinking": 0,
            "turn_started": last_prompt_t, "turn_prompt": ""}


# ── Research ─────────────────────────────────────────────────────────────────

def _research_root(config: dict) -> Path:
    d = (config.get("research") or {}).get("data_dir")
    if d:
        return Path(d).expanduser()
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "llamawatch" / "research"


def _parse_research(folder: str) -> dict:
    meta = json.loads(Path(folder, "run.json").read_text(encoding="utf-8"))
    steps: list[dict] = []
    started = tu._ts(str(meta.get("started", "")).replace(" ", "T")) if meta.get("started") else None
    last = started
    try:
        for line in Path(folder, "events.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except Exception:
                continue
            try:
                when = float(e.get("t"))
            except (TypeError, ValueError):
                continue
            last = when if last is None else max(last, when)
            steps.append({"t": when, "kind": "stage", "label": str(e.get("stage") or "run"), "detail": _cut(e.get("msg"), LINE_LEN)})
    except FileNotFoundError:
        pass
    by_stage = meta.get("by_stage") if isinstance(meta.get("by_stage"), dict) else {}
    seen_stage: set = set()
    for s in steps:                       # tokens from by_stage land on the first event of each stage
        st = s["label"]
        if st in by_stage and st not in seen_stage:
            seen_stage.add(st)
            s["out"] = int(by_stage[st].get("tokens") or 0)
            s["calls"] = int(by_stage[st].get("calls") or 0)
    status = str(meta.get("status") or "")
    if meta.get("finished"):
        fin = tu._ts(str(meta["finished"]).replace(" ", "T"))
        last = fin or last
    return {"id": "research:" + os.path.basename(folder), "agent": "Research",
            "title": _cut(meta.get("title") or meta.get("question") or os.path.basename(folder), TITLE_LEN),
            "project": str(meta.get("model") or ""), "cwd": "", "started": started, "last": last, "steps": steps,
            "in": 0, "out": int(meta.get("tokens") or 0), "cache": 0, "thinking": 0, "cost": float(meta.get("cost") or 0),
            "status": "working" if status == "running" else ("failed" if status in ("failed", "error") else "done"),
            "line": steps[-1]["detail"] if steps else "", "turn_started": started, "turn_prompt": ""}


# ── the cache and the scan ───────────────────────────────────────────────────

def _summary(path: str, parser) -> dict | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    hit = _cache.get(path)
    if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
        return hit[2]
    try:
        s = parser(path)
    except Exception:
        return None
    s["mtime"] = st.st_mtime
    _cache[path] = (st.st_mtime, st.st_size, s)
    return s


def _runs(config: dict, now: float) -> list[dict]:
    cutoff = now - WINDOW_DAYS * 86400
    out: list[dict] = []
    for path in glob.glob(tu._claude_glob()):
        try:
            if os.path.getmtime(path) < cutoff:
                continue
        except OSError:
            continue
        s = _summary(path, _parse_claude)
        if s and s.get("started") is not None and s["steps"]:
            out.append(s)
    for path in glob.glob(tu._codex_glob(), recursive=True):
        try:
            if os.path.getmtime(path) < cutoff:
                continue
        except OSError:
            continue
        s = _summary(path, _parse_codex)
        if s and s.get("started") is not None:
            out.append(s)
    for run_json in glob.glob(str(_research_root(config) / "*" / "run.json")):
        folder = os.path.dirname(run_json)
        try:
            if os.path.getmtime(run_json) < cutoff:
                continue
        except OSError:
            continue
        s = _summary(run_json, lambda p: _parse_research(os.path.dirname(p)))
        if s and s.get("started") is not None:
            out.append(s)
    for p in list(_cache):                 # forget files that fell out of the window
        if _cache[p][0] < cutoff:
            _cache.pop(p, None)
    out.sort(key=lambda s: s.get("last") or 0, reverse=True)
    return out


def _open_claude_cwds() -> set:
    """Working folders of Claude Code processes that are still open."""
    try:
        from .collectors.claude_code import collect_claude_code
        return {p.get("cwd") for p in collect_claude_code() if p.get("cwd")}
    except Exception:
        return set()


def _state(s: dict, now: float, open_cwds: set) -> str:
    if s["agent"] == "Research":
        return s.get("status", "done")
    if (s.get("mtime") or 0) >= now - WORKING_SECS or (s.get("last") or 0) >= now - WORKING_SECS:
        return "working"
    if s["agent"] == "Claude Code" and s.get("cwd") in open_cwds:
        return "idle"
    return "done"


def _line(s: dict) -> str:
    if s.get("line"):
        return s["line"]
    if not s["steps"]:
        return ""
    last = s["steps"][-1]
    if last["kind"] == "prompt":
        return "thinking about: " + last["detail"]
    if last["kind"] == "tool":
        return last["label"] + (": " + last["detail"] if last["detail"] else "")
    return last["detail"]


def _row(s: dict, now: float, state: str, prices: dict | None = None) -> dict:
    ended = None if state in ("working", "idle") else s.get("last")
    return {"id": s["id"], "agent": s["agent"], "title": s["title"], "project": s.get("project") or "",
            "started": s["started"], "ended": ended, "seconds": int(max(0, (s.get("last") or now) - s["started"])),
            "steps": len(s["steps"]), "tokens": int(s["in"] + s["out"] + s["cache"]), "cost": s.get("cost"),
            "status": state, "line": _line(s)}


def _configured(config: dict, containers: list[dict]) -> list[dict]:
    """The agents from config, with their state from the container list."""
    by_name = {c.get("name"): c for c in containers or [] if c.get("name")}
    out = []
    for a in config.get("agents") or []:
        names = [n for n in (a.get("containers") or []) if n]
        present = [by_name[n] for n in names if n in by_name]
        running = [c for c in present if c.get("state") == "running"]
        state = "online" if names and len(running) == len(names) else ("partial" if running else "offline")
        primary = by_name.get(a.get("primary")) or (present[0] if present else None)
        out.append({"id": "service:" + str(a.get("id") or a.get("name") or ""), "agent": str(a.get("name") or a.get("id") or ""),
                    "kind": "service", "state": state, "machine": a.get("machine") or "",
                    "line": (primary or {}).get("status") or ("no container seen" if not present else ""),
                    "containers": names, "since": None})
    return out


def collect(config: dict, containers: list[dict] | None = None, now: float | None = None) -> dict:
    """Everything the page needs in one call: the Now cards and the run history."""
    now = time.time() if now is None else now
    runs = _runs(config, now)
    open_cwds = _open_claude_cwds()
    live: list[dict] = []
    rows: list[dict] = []
    for s in runs:
        state = _state(s, now, open_cwds)
        rows.append(_row(s, now, state))
        if state in ("working", "idle"):
            live.append({"id": s["id"], "agent": s["agent"], "kind": s["agent"].lower().replace(" ", "-"),
                         "state": state, "title": s["title"], "project": s.get("project") or "",
                         "line": _line(s), "since": s.get("turn_started") or s["started"], "started": s["started"]})
    live.extend(_configured(config, containers or []))
    agents = sorted({r["agent"] for r in rows} | {a["agent"] for a in live if a.get("kind") == "service"})
    return {"now": now, "live": live, "runs": rows[:MAX_RUNS], "agents": agents, "window_days": WINDOW_DAYS}


def replay(config: dict, run_id: str, now: float | None = None) -> dict | None:
    """One run with its steps, offsets and per-step durations."""
    if not _ID.match(run_id or ""):
        return None
    now = time.time() if now is None else now
    kind, _, key = run_id.partition(":")
    s = None
    if kind == "claude":
        for path in glob.glob(tu._claude_glob()):
            if os.path.splitext(os.path.basename(path))[0] == key:
                s = _summary(path, _parse_claude)
                break
    elif kind == "codex":
        for path in glob.glob(tu._codex_glob(), recursive=True):
            c = _summary(path, _parse_codex)
            if c and c["id"] == run_id:
                s = c
                break
    elif kind == "research":
        p = _research_root(config) / key / "run.json"
        if p.is_file():
            s = _summary(str(p), lambda q: _parse_research(os.path.dirname(q)))
    if not s or s.get("started") is None:
        return None
    state = _state(s, now, _open_claude_cwds())
    steps = s["steps"]
    folded = 0
    if len(steps) > MAX_STEPS:
        folded = len(steps) - MAX_STEPS
        steps = steps[-MAX_STEPS:]
    out = []
    for i, st in enumerate(steps):
        nxt = steps[i + 1]["t"] if i + 1 < len(steps) else (s.get("last") if state not in ("working", "idle") else now)
        out.append({"t": st["t"], "offset": int(max(0, st["t"] - s["started"])), "dur": int(max(0, (nxt or st["t"]) - st["t"])),
                    "kind": st["kind"], "label": st["label"], "detail": st.get("detail") or "",
                    "in": int(st.get("in") or 0), "out": int(st.get("out") or 0), "cache": int(st.get("cache") or 0),
                    "calls": int(st.get("calls") or 0)})
    if folded:
        out.insert(0, {"t": s["started"], "offset": 0, "dur": int(max(0, steps[0]["t"] - s["started"])), "kind": "fold",
                       "label": f"{folded} earlier steps", "detail": "", "in": 0, "out": 0, "cache": 0, "calls": 0})
    row = _row(s, now, state)
    row.update({"in": int(s["in"]), "out": int(s["out"]), "cache": int(s["cache"]), "thinking": int(s.get("thinking") or 0),
                "cwd": s.get("cwd") or ""})
    return {"run": row, "steps": out, "now": now}
