"""Token-usage collector: last-24h tokens per model and per coding tool.

Every source is found automatically and named as the user named it:
  - each llama.cpp backend in config "backends", read from its /metrics
    counters (the server needs --metrics). A router-mode server is read
    model by model with /metrics?model=<id>.
  - Claude Code session logs (~/.claude/projects, or $CLAUDE_CONFIG_DIR)
  - Codex session logs (~/.codex/sessions, or $CODEX_HOME)

  - Research runs on models that are not llama.cpp backends (online APIs,
    or local servers without /metrics), from the runs' own token counts.

Rows with no usage in the window are left out, so a machine with only
local models shows only those models. Names in token_usage.hidden are
never shown; "sources" lists everything found so the page can offer them.
"""

import datetime
import glob
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

WIDGET_ID = "token-usage"
WIDGET_NAME = "Token Usage"
WIDGET_DEFAULT_SIZE = {"w": 4, "h": 3, "minW": 3, "minH": 2}
WIDGET_REQUIRES = []
WIDGET_ICON = "📊"
WIDGET_DESCRIPTION = "Last-24h token usage per model and coding tool"
WIDGET_CONFIG_SCHEMA: list[dict] = []
WIDGET_MULTI_INSTANCE = False
WIDGET_CONFIG_REQUIRED = False

_SNAP_DIR    = Path(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")) / "llamawatch" / "usage"
_WINDOW_SECS = 86400  # 24 hours


def _claude_glob() -> str:
    root = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    return os.path.join(root, "projects", "*", "*.jsonl")


def _codex_sessions() -> str:
    return os.path.join(os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex"), "sessions")


def _codex_glob() -> str:
    return os.path.join(_codex_sessions(), "**", "*.jsonl")


def _ts(value) -> float | None:
    """ISO-8601 timestamp (with or without Z) to epoch seconds."""
    if not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


# ── llama.cpp backends (/metrics) ─────────────────────────────────────────────

def _http_get(url: str, timeout: float = 2.0) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def _loaded_models(url: str) -> list[str]:
    """Model ids a router-mode llama-server has loaded right now."""
    try:
        data = json.loads(_http_get(f"{url}/v1/models") or "{}")
    except Exception:
        return []
    out = []
    for m in data.get("data") or []:
        status = m.get("status")
        state = status.get("value") if isinstance(status, dict) else status
        if m.get("id") and state in (None, "loaded"):
            out.append(m["id"])
    return out


def _read_counters(url: str) -> tuple[int, int] | None:
    """(prompt tokens, generated tokens) since the server started, or None.

    A router-mode server refuses a bare /metrics, so each loaded model is
    read on its own and the counts are added together.
    """
    body = _http_get(f"{url}/metrics")
    bodies = [body] if body else [
        _http_get(f"{url}/metrics?model={urllib.parse.quote(m)}") for m in _loaded_models(url)
    ]
    found, t_in, t_out = False, 0, 0
    for b in bodies:
        if not b:
            continue
        m = _parse_prometheus(b)
        if "llamacpp:prompt_tokens_total" in m or "llamacpp:tokens_predicted_total" in m:
            found = True
            t_in += int(m.get("llamacpp:prompt_tokens_total", 0))
            t_out += int(m.get("llamacpp:tokens_predicted_total", 0))
    return (t_in, t_out) if found else None


def _parse_prometheus(text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        name = parts[0]
        brace = name.find("{")
        if brace != -1:
            name = name[:brace]
        try:
            out[name] = float(parts[1])
        except ValueError:
            continue
    return out


def _snap_file(name: str) -> Path:
    return _SNAP_DIR / (re.sub(r"[^A-Za-z0-9._-]+", "_", name) + ".jsonl")


def _collect_backend(backend: dict) -> dict:
    """24h token growth for one llama.cpp backend, from stored readings.

    The counters only ever grow until the server restarts, so each reading
    is kept and the growth between readings is added up.
    """
    name = backend.get("name") or backend.get("url", "llama.cpp")
    result = {"model": name, "kind": "local", "requests": 0, "in_tokens": 0, "out_tokens": 0}
    url = (backend.get("url") or "").rstrip("/")
    counters = _read_counters(url) if url else None
    if counters is None:
        result["no_metrics"] = True
        return result

    now = time.time()
    cutoff = now - _WINDOW_SECS
    path = _snap_file(name)
    snapshots: list[dict] = []
    base = None
    try:
        if path.exists():
            for line in path.read_text().splitlines():
                try:
                    s = json.loads(line)
                except Exception:
                    continue
                if s.get("ts", 0) >= cutoff:
                    snapshots.append(s)
                else:
                    base = s   # last reading before the window: the baseline
    except Exception:
        pass

    # Growth since the last reading goes into today's daily total (kept for the
    # Spend page, which needs more than 24 hours).
    last = max(snapshots, key=lambda s: s["ts"]) if snapshots else base
    if last is not None:
        _add_daily(name, now, _grown([last, {"ts": now, "in": counters[0], "out": counters[1]}], "in"),
                   _grown([last, {"ts": now, "in": counters[0], "out": counters[1]}], "out"))

    snapshots.append({"ts": now, "in": counters[0], "out": counters[1]})
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(s) + "\n" for s in ([base] if base else []) + snapshots))
    except Exception:
        pass

    if len(snapshots) < 2 and base is None:
        return result  # need two readings for a difference
    result["in_tokens"], result["out_tokens"] = _grown(snapshots, "in", base), _grown(snapshots, "out", base)
    return result


DAILY_KEEP = 400


def _daily_file(name: str) -> Path:
    return _SNAP_DIR / (re.sub(r"[^A-Za-z0-9._-]+", "_", name) + ".daily.json")


def read_daily(name: str) -> dict:
    """{YYYY-MM-DD (UTC): {"in": n, "out": n}} for one backend."""
    try:
        d = json.loads(_daily_file(name).read_text())
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _add_daily(name: str, now: float, t_in: int, t_out: int) -> None:
    if not (t_in or t_out):
        return
    day = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).strftime("%Y-%m-%d")
    daily = read_daily(name)
    row = daily.setdefault(day, {"in": 0, "out": 0})
    row["in"] = int(row.get("in", 0)) + int(t_in)
    row["out"] = int(row.get("out", 0)) + int(t_out)
    for old in sorted(daily)[:-DAILY_KEEP] if len(daily) > DAILY_KEEP else []:
        daily.pop(old, None)
    try:
        _daily_file(name).parent.mkdir(parents=True, exist_ok=True)
        _daily_file(name).write_text(json.dumps(daily))
    except Exception:
        pass


def _grown(snapshots: list[dict], key: str, base: dict | None = None) -> int:
    """Sum growth between readings. A drop means the server restarted and
    its counters went back to 0, so the new value counts in full."""
    snaps = sorted(snapshots, key=lambda s: s["ts"])
    total, prev = 0, (base or snaps[0]).get(key, 0)
    for s in snaps:
        v = s.get(key, 0)
        total += v - prev if v >= prev else v
        prev = v
    return total


# ── Coding tools ─────────────────────────────────────────────────────────────

def _collect_claude(window_secs: int = _WINDOW_SECS) -> dict:
    """Read all Claude Code JSONL sessions and return a single consolidated entry."""
    result = {"model": "Claude", "kind": "tool", "requests": 0, "in_tokens": 0, "out_tokens": 0, "cache_read_tokens": 0}
    cutoff = time.time() - window_secs

    for path in glob.glob(_claude_glob()):
        try:
            if os.path.getmtime(path) < cutoff:
                continue
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    if d.get("type") != "assistant":
                        continue
                    msg = d.get("message", {})
                    usage = msg.get("usage")
                    if not usage:
                        continue
                    # Timestamp filter (skip entries older than 24h)
                    ts = d.get("timestamp", "")
                    when = _ts(ts)
                    if when is not None and when < cutoff:
                        continue
                    result["requests"]          += 1
                    result["in_tokens"]         += usage.get("input_tokens", 0)
                    result["out_tokens"]        += usage.get("output_tokens", 0)
                    result["cache_read_tokens"] += (
                        usage.get("cache_read_input_tokens", 0)
                        + usage.get("cache_creation_input_tokens", 0)
                    )
        except Exception:
            continue

    return result


def _collect_codex(window_secs: int = _WINDOW_SECS) -> dict:
    """Read Codex session logs. Each token_count event carries the session's
    running total, so a session's usage in the window is its last total
    minus the last total from before the window."""
    result = {"model": "Codex", "kind": "tool", "requests": 0, "in_tokens": 0, "out_tokens": 0, "cache_read_tokens": 0}
    cutoff = time.time() - window_secs
    for path in glob.glob(_codex_glob(), recursive=True):
        try:
            if os.path.getmtime(path) < cutoff:
                continue
            before, last, seen = None, None, set()
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if '"token_count"' not in line:
                        continue
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    p = d.get("payload") or {}
                    tot = (p.get("info") or {}).get("total_token_usage") if p.get("type") == "token_count" else None
                    if not tot:
                        continue
                    when = _ts(d.get("timestamp"))
                    if when is not None and when < cutoff:
                        before = tot
                        continue
                    last = tot
                    seen.add(tot.get("total_tokens", json.dumps(tot, sort_keys=True)))
            if last is None:
                continue
            b = before or {}
            cached = last.get("cached_input_tokens", 0) - b.get("cached_input_tokens", 0)
            result["requests"]          += len(seen - ({b.get("total_tokens")} if before else set()))
            result["in_tokens"]         += max(0, last.get("input_tokens", 0) - b.get("input_tokens", 0) - cached)
            result["out_tokens"]        += max(0, last.get("output_tokens", 0) - b.get("output_tokens", 0))
            result["cache_read_tokens"] += max(0, cached)
        except Exception:
            continue
    return result


# ── Research runs on other models ─────────────────────────────────────────────

def _research_models(config: dict, skip: set) -> list[str]:
    names = [m.get("name") for m in ((config.get("research") or {}).get("models") or [])]
    return [n for n in names if n and n not in skip]


def _collect_research(config: dict, names: list[str], window_secs: int = _WINDOW_SECS) -> list[dict]:
    """Tokens Research spent on each of these models in the window. Calls
    the app does not make itself (other programs using the same API key)
    cannot be seen."""
    rows = {n: {"model": n, "kind": "api", "requests": 0, "in_tokens": 0, "out_tokens": 0} for n in names}
    if not rows:
        return []
    root = Path((config.get("research") or {}).get("data_dir")
                or Path.home() / ".local" / "share" / "llamawatch" / "research").expanduser()
    cutoff = time.time() - window_secs
    for f in root.glob("*/run.json"):
        try:
            if f.stat().st_mtime < cutoff:
                continue
            meta = json.loads(f.read_text())
        except Exception:
            continue
        row = rows.get(meta.get("model"))
        if row is None:
            continue
        t_in, t_out = meta.get("tokens_in"), meta.get("tokens_out")
        if t_in is None and t_out is None:
            t_in, t_out = meta.get("tokens") or 0, 0   # older runs kept only the total
        row["requests"] += int(meta.get("calls") or 0)
        row["in_tokens"] += int(t_in or 0)
        row["out_tokens"] += int(t_out or 0)
    return list(rows.values())


def _tool_found(pattern: str) -> bool:
    return bool(glob.glob(pattern, recursive=True))


# ── Collector entry point ─────────────────────────────────────────────────────

def _live(config: dict) -> dict:
    """The server's current config. The live stream holds the config it was
    opened with, so without this a saved choice would only show after the
    page is reloaded."""
    import sys
    server = sys.modules.get("llamawatch.server")
    return getattr(server, "_config", None) or config


def collect(config=None, adapters=None, widget_config=None) -> dict:
    """Return last-24h token usage: one row per llama.cpp backend and per
    coding tool found on this machine, leaving out rows with no usage."""
    if config is None:
        try:
            from ..config import load_config
            config = load_config()
        except Exception:
            config = {}
    config = _live(config)
    backends = [b for b in (config.get("backends") or []) if b.get("type") == "llamacpp"]
    hidden = set((config.get("token_usage") or {}).get("hidden") or [])
    backend_names = {b.get("name") or b.get("url", "llama.cpp") for b in backends}
    research = _research_models(config, backend_names)

    sources = [{"name": n, "kind": "local"} for n in backend_names] + \
              [{"name": n, "kind": "research"} for n in research]
    if _tool_found(_claude_glob()):
        sources.append({"name": "Claude", "kind": "tool"})
    if os.path.isdir(_codex_sessions()):
        sources.append({"name": "Codex", "kind": "tool"})
    for src in sources:
        src["hidden"] = src["name"] in hidden

    # Backends are read even when hidden so their history keeps building.
    rows = [_collect_backend(b) for b in backends] + _collect_research(config, research) \
        + [_collect_claude(), _collect_codex()]
    no_metrics = [r["model"] for r in rows if r.pop("no_metrics", False) and r["model"] not in hidden]
    by_model = sorted(
        [r for r in rows if (r["in_tokens"] or r["out_tokens"]) and r["model"] not in hidden],
        key=lambda m: m["in_tokens"] + m["out_tokens"],
        reverse=True,
    )
    return {
        "by_model":       by_model,
        "sources":        sorted(sources, key=lambda x: x["name"].lower()),
        "no_metrics":     no_metrics,
        "total_requests": sum(m["requests"] for m in by_model),
        "total_tokens":   sum(m["in_tokens"] + m["out_tokens"] for m in by_model),
    }
