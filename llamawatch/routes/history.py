"""History endpoint: past temperatures (highs, lows, averages) for the wide-screen
System page. Reads a JSON-lines temperature log; when the file is absent the
panel simply stays empty (disabled=True), so this is safe on any install.

Config key `temp_log_path` (default ~/logs/temp-monitor.jsonl). Each line:
  {"ts": "<ISO UTC>", "temps": {"cpu": 54.3, "gpu": 48.0, ...}}
The rotated sibling `<path>.1` is read as well when present."""

import asyncio
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Query

from ..config import load_config

router = APIRouter()

SENSORS = ("cpu", "gpu", "nvme", "network")
RANGES = {"24h": 24, "3d": 72, "7d": 168, "30d": 720}
BUCKETS = 96          # points per chart
HOT_LIMIT = 85.0      # "time above" threshold, deg C
_cache: dict = {}


def _log_paths():
    p = Path(os.path.expanduser(load_config().get("temp_log_path") or "~/logs/temp-monitor.jsonl"))
    return [q for q in (p.with_name(p.name + ".1"), p) if q.exists()]


def _compute(hours: int) -> dict:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)
    cut_s = cutoff.isoformat()
    span = hours * 3600.0
    step = span / BUCKETS
    acc = {s: [[None, None, 0, 0.0] for _ in range(BUCKETS)] for s in SENSORS}  # min,max,n,sum
    ext = {s: {"max": None, "max_ts": None, "min": None, "min_ts": None, "n": 0, "sum": 0.0, "hot": 0} for s in SENSORS}
    first_ts = None
    for path in _log_paths():
        with open(path, "r", errors="replace") as f:
            for line in f:
                i = line.find('"ts": "')
                if i < 0:
                    continue
                ts = line[i + 7:i + 40]
                if ts < cut_s[:26]:
                    continue
                try:
                    d = json.loads(line)
                    t = datetime.fromisoformat(d["ts"])
                except Exception:
                    continue
                temps = d.get("temps") or {}
                b = min(BUCKETS - 1, max(0, int((t - cutoff).total_seconds() / step)))
                if first_ts is None or t < first_ts:
                    first_ts = t
                for s in SENSORS:
                    v = temps.get(s)
                    if v is None:
                        continue
                    a = acc[s][b]
                    a[0] = v if a[0] is None or v < a[0] else a[0]
                    a[1] = v if a[1] is None or v > a[1] else a[1]
                    a[2] += 1
                    a[3] += v
                    e = ext[s]
                    e["n"] += 1
                    e["sum"] += v
                    if v >= HOT_LIMIT:
                        e["hot"] += 1
                    if e["max"] is None or v > e["max"]:
                        e["max"], e["max_ts"] = v, d["ts"]
                    if e["min"] is None or v < e["min"]:
                        e["min"], e["min_ts"] = v, d["ts"]
    series = {}
    for s in SENSORS:
        series[s] = [None if a[2] == 0 else {"min": round(a[0], 1), "max": round(a[1], 1), "avg": round(a[3] / a[2], 1)} for a in acc[s]]
    summary = {}
    for s in SENSORS:
        e = ext[s]
        if e["n"]:
            summary[s] = {"max": round(e["max"], 1), "max_ts": e["max_ts"], "min": round(e["min"], 1),
                          "min_ts": e["min_ts"], "avg": round(e["sum"] / e["n"], 1),
                          "hot_pct": round(100.0 * e["hot"] / e["n"], 1)}
    return {"start": cutoff.isoformat(), "end": now.isoformat(), "buckets": BUCKETS, "hot_limit": HOT_LIMIT,
            "covers_from": first_ts.isoformat() if first_ts else None, "series": series, "summary": summary}


@router.get("/api/history")
async def history(range: str = Query("24h")):
    hours = RANGES.get(range, 24)
    paths = _log_paths()
    if not paths:
        return {"disabled": True}
    key = (range, tuple(int(p.stat().st_mtime // 60) for p in paths))
    hit = _cache.get(range)
    if hit and hit[0] == key and time.time() - hit[1] < 120:
        return hit[2]
    data = await asyncio.to_thread(_compute, hours)
    _cache[range] = (key, time.time(), data)
    return data
