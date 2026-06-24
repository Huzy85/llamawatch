"""Token-usage collector — last-24h usage across up to three sources.

This is a reference implementation wired to a few optional, self-hosted
sources. Each is independent and absent sources simply report zero:
  - Primary backend: a SQLite usage log (path + model aliases from config)
  - Secondary backend: llama.cpp /metrics snapshots (URL from config)
  - Claude Code: ~/.claude/projects/*/*.jsonl session files

All sources are optional. With none configured the widget shows zeros.
"""

import glob
import json
import os
import sqlite3
import time
import urllib.request
from pathlib import Path

WIDGET_ID = "token-usage"
WIDGET_NAME = "Token Usage"
WIDGET_DEFAULT_SIZE = {"w": 4, "h": 3, "minW": 3, "minH": 2}
WIDGET_REQUIRES = []
WIDGET_ICON = "📊"
WIDGET_DESCRIPTION = "Last-24h token usage across configured backends"
WIDGET_CONFIG_SCHEMA: list[dict] = []
WIDGET_MULTI_INSTANCE = False
WIDGET_CONFIG_REQUIRED = False

_DB_PATH          = Path(os.path.expanduser("~/.local/share/llm-usage/usage.db"))
_APOLLO_SNAP_PATH = Path(os.path.expanduser("~/.local/share/llm-usage/apollo-snapshots.jsonl"))
_CLAUDE_GLOB      = os.path.expanduser("~/.claude/projects/*/*.jsonl")
_WINDOW_SECS      = 86400  # 24 hours


def _apollo_url():
    """Secondary-backend metrics URL from config (token_usage.apollo_url), or None."""
    try:
        from ..config import load_config
        return (load_config().get("token_usage") or {}).get("apollo_url")
    except Exception:
        return None

# ── Primary backend bucket ──────────────────────────────────────────────────
# Model-name substrings that count toward the primary backend's usage bucket.
# Configured per-install via token_usage.primary_model_aliases (default: none).

def _primary_aliases() -> set:
    try:
        from ..config import load_config
        aliases = (load_config().get("token_usage") or {}).get("primary_model_aliases") or []
        return {a.strip().lower() for a in aliases if a}
    except Exception:
        return set()


def _is_primary(raw: str) -> bool:
    key = (raw or "").strip().lower()
    if not key:
        return False
    aliases = _primary_aliases()
    return any(key == a or key.startswith(a) for a in aliases)


def _labels() -> dict:
    """Bucket display labels from config, with generic defaults."""
    try:
        from ..config import load_config
        tu = load_config().get("token_usage") or {}
    except Exception:
        tu = {}
    return {
        "primary":   tu.get("primary_label", "Primary"),
        "secondary": tu.get("secondary_label", "Secondary"),
        "claude":    tu.get("claude_label", "Claude"),
    }


def _collect_primary(cutoff: int) -> dict:
    """Read the primary backend's 24h totals from the usage SQLite log."""
    result = {"model": _labels()["primary"], "requests": 0, "in_tokens": 0, "out_tokens": 0}
    if not _DB_PATH.exists():
        return result
    try:
        con = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True, timeout=2)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        rows = cur.execute(
            """
            SELECT model,
                   COUNT(*)                       AS requests,
                   SUM(COALESCE(input_tokens,  0)) AS in_tokens,
                   SUM(COALESCE(output_tokens, 0)) AS out_tokens
            FROM llm_usage
            WHERE ts >= ?
            GROUP BY model
            """,
            (cutoff,),
        ).fetchall()
        con.close()
        for row in rows:
            if _is_primary(row["model"]):
                result["requests"]  += row["requests"]  or 0
                result["in_tokens"] += row["in_tokens"]  or 0
                result["out_tokens"]+= row["out_tokens"] or 0
    except Exception:
        pass
    return result


def _collect_db_totals(cutoff: int) -> tuple[int, int]:
    """Return (total_requests, total_tokens) from the DB for the 24h window."""
    if not _DB_PATH.exists():
        return 0, 0
    try:
        con = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True, timeout=2)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        row = cur.execute(
            """
            SELECT COUNT(*) AS total_requests,
                   SUM(COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0)) AS total_tokens
            FROM llm_usage WHERE ts >= ?
            """,
            (cutoff,),
        ).fetchone()
        con.close()
        if row:
            return row["total_requests"] or 0, row["total_tokens"] or 0
    except Exception:
        pass
    return 0, 0


def _collect_db_callers(cutoff: int) -> list[dict]:
    if not _DB_PATH.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True, timeout=2)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        rows = cur.execute(
            """
            SELECT COALESCE(NULLIF(TRIM(caller),''), 'untagged') AS caller,
                   COUNT(*) AS requests,
                   SUM(COALESCE(input_tokens,0)+COALESCE(output_tokens,0)) AS tokens
            FROM llm_usage WHERE ts >= ?
            GROUP BY caller ORDER BY tokens DESC LIMIT 10
            """,
            (cutoff,),
        ).fetchall()
        con.close()
        return [{"caller": r["caller"] or "untagged", "requests": r["requests"], "tokens": r["tokens"] or 0} for r in rows]
    except Exception:
        return []


# ── Secondary backend (llama.cpp /metrics) ───────────────────────────────────

def _http_get(url: str, timeout: float = 2.0) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception:
        return None


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


def _collect_apollo() -> dict:
    """Read the secondary backend's 24h token delta via /metrics snapshots.

    Returns zero tokens if /metrics is unavailable. Once the backend exposes
    --metrics, snapshots accumulate and the 24h delta becomes meaningful.
    """
    result = {"model": _labels()["secondary"], "requests": 0, "in_tokens": 0, "out_tokens": 0}

    apollo_url = _apollo_url()
    if not apollo_url:
        return result
    body = _http_get(f"{apollo_url}/metrics", timeout=2.0)
    if not body:
        return result

    metrics = _parse_prometheus(body)
    current_out = int(metrics.get("llamacpp:tokens_predicted_total", 0))
    current_in  = int(metrics.get("llamacpp:prompt_tokens_total", 0))

    if current_out == 0 and current_in == 0:
        return result

    now = time.time()
    cutoff = now - _WINDOW_SECS

    # Load recent snapshots (within 24h)
    snapshots: list[dict] = []
    try:
        if _APOLLO_SNAP_PATH.exists():
            with open(_APOLLO_SNAP_PATH) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        s = json.loads(line)
                        if s.get("ts", 0) >= cutoff:
                            snapshots.append(s)
                    except Exception:
                        continue
    except Exception:
        pass

    # Append current snapshot and rewrite file (trim old entries)
    new_snap = {"ts": now, "out": current_out, "in": current_in}
    snapshots.append(new_snap)
    try:
        _APOLLO_SNAP_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_APOLLO_SNAP_PATH, "w") as fh:
            for s in snapshots:
                fh.write(json.dumps(s) + "\n")
    except Exception:
        pass

    # 24h delta = current - oldest snapshot in window
    if len(snapshots) < 2:
        return result  # Need at least 2 readings for a delta

    oldest = min(snapshots, key=lambda s: s["ts"])
    delta_out = max(0, current_out - oldest["out"])
    delta_in  = max(0, current_in  - oldest["in"])

    result["out_tokens"] = delta_out
    result["in_tokens"]  = delta_in
    return result


# ── Claude ────────────────────────────────────────────────────────────────────

def _collect_claude(window_secs: int = _WINDOW_SECS) -> dict:
    """Read all Claude Code JSONL sessions and return a single consolidated entry."""
    result = {"model": _labels()["claude"], "requests": 0, "in_tokens": 0, "out_tokens": 0, "cache_read_tokens": 0}
    cutoff = time.time() - window_secs

    for path in glob.glob(_CLAUDE_GLOB):
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
                    if ts:
                        try:
                            import datetime
                            dt = datetime.datetime.fromisoformat(ts.rstrip("Z"))
                            if dt.timestamp() < cutoff:
                                continue
                        except Exception:
                            pass
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


# ── Collector entry point ─────────────────────────────────────────────────────

def collect(config=None, adapters=None, widget_config=None) -> dict:
    """Return last-24h token usage as three buckets (primary, secondary, Claude)."""
    cutoff = int(time.time()) - _WINDOW_SECS

    primary = _collect_primary(cutoff)
    apollo  = _collect_apollo()
    claude  = _collect_claude()

    by_model = sorted(
        [primary, apollo, claude],
        key=lambda m: m["out_tokens"],
        reverse=True,
    )

    total_req = sum(m["requests"]  for m in by_model)
    total_tok = sum(m["in_tokens"] + m["out_tokens"] for m in by_model)

    by_caller = _collect_db_callers(cutoff)

    return {
        "by_model":       by_model,
        "by_caller":      by_caller,
        "total_requests": total_req,
        "total_tokens":   total_tok,
    }
