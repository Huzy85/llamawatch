"""Spend: real money against free local tokens, day by day.

A small JSON file per UTC day under ``~/.local/share/llamawatch/spend/``
holds, for every source found on this machine, the tokens and requests of
that day and how they split across jobs. Sources are the same ones the Token
Usage widget finds: llama.cpp backends (free, from the daily counter growth
the widget records), Claude Code sessions (one source per model id), Codex
sessions, and research runs on online models. Money is worked out when the
page asks, from the prices the owner typed (``spend.prices`` in
config.local.json) or the price on a research model, so a price change
re-prices the history. A source with no price shows tokens only.

Today and yesterday are rebuilt on every roll-up (a log line can land after
midnight); older days are frozen once written. The first roll-up on a machine
goes back 30 days.
"""

import datetime as _dt
import glob
import json
import os
import re
import time
import urllib.parse
from pathlib import Path

from .collectors import token_usage as tu

KEEP_DAYS = 400
MAX_DAYS = 400
DEFAULT_DAYS = 30
BACKFILL_DAYS = 30
MIN_GAP = 60            # seconds between two roll-ups
JOB_LABEL_MAX = 60
CURRENCY_MAX = 3
_last_rollup = 0.0


def data_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")) / "llamawatch" / "spend"


def _day(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime("%Y-%m-%d")


def _today(now: float | None = None) -> str:
    return _day(now if now is not None else time.time())


def _days_back(day: str, n: int) -> str:
    d = _dt.date.fromisoformat(day) - _dt.timedelta(days=n)
    return d.isoformat()


def _date_range(last: str, n: int) -> list[str]:
    return [_days_back(last, i) for i in range(n - 1, -1, -1)]


def _blank(kind: str) -> dict:
    return {"kind": kind, "requests": 0, "in": 0, "out": 0, "cache": 0, "jobs": {}}


def _bump(row: dict, requests=0, t_in=0, t_out=0, cache=0) -> None:
    row["requests"] += int(requests or 0)
    row["in"] += int(t_in or 0)
    row["out"] += int(t_out or 0)
    row["cache"] += int(cache or 0)


def _job(src: dict, job_id: str, label: str) -> dict:
    j = src["jobs"].get(job_id)
    if j is None:
        j = src["jobs"][job_id] = {"label": str(label or job_id)[:JOB_LABEL_MAX], "requests": 0, "in": 0, "out": 0, "cache": 0}
    return j


def _source(days: dict, day: str, name: str, kind: str) -> dict:
    d = days.setdefault(day, {})
    s = d.get(name)
    if s is None:
        s = d[name] = _blank(kind)
    return s


# ── the sources ──────────────────────────────────────────────────────────────

def _project_label(cwd: str, folder: str) -> str:
    """A short job name for a coding session: the working folder's own name."""
    base = os.path.basename((cwd or "").rstrip("/"))
    if base:
        return base
    parts = [p for p in folder.split("-") if p]
    return parts[-1] if parts else "session"


def _scan_claude(days: dict, wanted: set, cutoff: float) -> None:
    seen: set = set()
    for path in glob.glob(tu._claude_glob()):
        try:
            if os.path.getmtime(path) < cutoff:
                continue
            folder = os.path.basename(os.path.dirname(path))
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if '"assistant"' not in line or '"usage"' not in line:
                        continue
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    if d.get("type") != "assistant":
                        continue
                    msg = d.get("message") or {}
                    usage = msg.get("usage")
                    if not usage:
                        continue
                    when = tu._ts(d.get("timestamp"))
                    if when is None:
                        continue
                    day = _day(when)
                    if day not in wanted:
                        continue
                    # The same assistant message is written once per tool result; count it once.
                    key = (msg.get("id"), d.get("requestId"), day)
                    if key[0] and key in seen:
                        continue
                    seen.add(key)
                    model = str(msg.get("model") or "Claude")
                    if model.startswith("<"):
                        continue   # "<synthetic>" rows carry no real usage
                    src = _source(days, day, model, "tool")
                    cache = int(usage.get("cache_read_input_tokens") or 0) + int(usage.get("cache_creation_input_tokens") or 0)
                    _bump(src, 1, usage.get("input_tokens"), usage.get("output_tokens"), cache)
                    label = _project_label(d.get("cwd") or "", folder)
                    _bump(_job(src, "claude:" + label, label), 1, usage.get("input_tokens"), usage.get("output_tokens"), cache)
        except Exception:
            continue


def _scan_codex(days: dict, wanted: set, cutoff: float) -> None:
    for path in glob.glob(tu._codex_glob(), recursive=True):
        try:
            if os.path.getmtime(path) < cutoff:
                continue
            prev = None
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
                    when = tu._ts(d.get("timestamp"))
                    b = prev or {}
                    prev = tot
                    if when is None or _day(when) not in wanted:
                        continue
                    cached = max(0, tot.get("cached_input_tokens", 0) - b.get("cached_input_tokens", 0))
                    t_in = max(0, tot.get("input_tokens", 0) - b.get("input_tokens", 0) - cached)
                    t_out = max(0, tot.get("output_tokens", 0) - b.get("output_tokens", 0))
                    if not (t_in or t_out or cached):
                        continue
                    src = _source(days, _day(when), "Codex", "tool")
                    _bump(src, 1, t_in, t_out, cached)
                    _bump(_job(src, "codex:Codex", "Codex"), 1, t_in, t_out, cached)
        except Exception:
            continue


def _scan_backends(days: dict, wanted: set, config: dict) -> None:
    """Free local tokens: the per-day counter growth kept from each llama.cpp
    server's /metrics. The Monitor page records a reading while it is open;
    the roll-up takes one too, so a day with nobody watching still counts."""
    for b in config.get("backends") or []:
        if b.get("type") != "llamacpp":
            continue
        name = b.get("name") or b.get("url", "llama.cpp")
        try:
            tu._collect_backend(b)
        except Exception:
            pass
        for day, row in tu.read_daily(name).items():
            if day in wanted and (row.get("in") or row.get("out")):
                src = _source(days, day, name, "local")
                _bump(src, 0, row.get("in"), row.get("out"))


def _scan_research(days: dict, wanted: set, config: dict, local_names: set) -> None:
    """Research runs. A run on a watched backend is already inside that
    backend's total, so only its job row is added. A run on a research model
    with a price is paid API use; one with no price is free (own server, or a
    plan) and counts as local."""
    root = Path((config.get("research") or {}).get("data_dir")
                or Path.home() / ".local" / "share" / "llamawatch" / "research").expanduser()
    priced = {m.get("name") for m in (config.get("research") or {}).get("models") or []
              if m.get("name") and (m.get("price_in") or m.get("price_out"))}
    from . import spend_providers as sp
    covered_hosts = sp.covered_hosts(config)
    covered = {m.get("name") for m in (config.get("research") or {}).get("models") or []
               if m.get("name") and urllib.parse.urlparse(str(m.get("base_url") or "")).hostname in covered_hosts}
    for f in root.glob("*/run.json"):
        try:
            meta = json.loads(f.read_text())
        except Exception:
            continue
        started = str(meta.get("started") or "")[:10]
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", started) or started not in wanted:
            continue
        model = str(meta.get("model") or "")
        if not model or model in covered:
            continue     # the connected provider's own report already holds these
        t_in, t_out = meta.get("tokens_in"), meta.get("tokens_out")
        if t_in is None and t_out is None:
            t_in, t_out = meta.get("tokens") or 0, 0
        on_backend = model in local_names
        src = _source(days, started, model, "api" if model in priced else "local")
        if not on_backend:
            _bump(src, meta.get("calls"), t_in, t_out)    # a backend's total already holds these
        job = _job(src, "research:" + f.parent.name, meta.get("title") or meta.get("question") or f.parent.name)
        _bump(job, meta.get("calls"), t_in, t_out)
        if meta.get("cost"):
            job["cost"] = round(float(job.get("cost", 0)) + float(meta["cost"]), 4)


def _scan_chat(days: dict, wanted: set, local_names: set) -> None:
    """Chats through llamawatch, from the request log: a job on the model that answered."""
    log_dir = Path(os.environ.get("LLAMAWATCH_REQUEST_LOG_DIR") or os.path.expanduser("~/.config/llamawatch/request_history"))
    for day in wanted:
        p = Path(log_dir) / f"requests-{day}.jsonl"
        if not p.is_file():
            continue
        try:
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                model = str(e.get("model") or "")
                if model not in local_names:
                    continue
                src = _source(days, day, model, "local")
                _bump(src, 1)
                _bump(_job(src, "chat", "Chat"), 1, e.get("prompt_tokens"), e.get("completion_tokens"))
        except Exception:
            continue


# ── roll-up ──────────────────────────────────────────────────────────────────

def _read_day(day: str) -> dict | None:
    p = data_dir() / f"{day}.json"
    try:
        d = json.loads(p.read_text()) if p.is_file() else None
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and isinstance(d.get("sources"), dict) else None


def _write_day(day: str, sources: dict) -> None:
    p = data_dir() / f"{day}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"date": day, "sources": sources, "written": time.time()}, indent=0))


def _prune() -> None:
    files = sorted(data_dir().glob("????-??-??.json"))
    for f in files[:-KEEP_DAYS] if len(files) > KEEP_DAYS else []:
        try:
            f.unlink()
        except OSError:
            pass


def rollup(config: dict, now: float | None = None, force: bool = False) -> list[str]:
    """Bring the ledger up to date. Returns the days that were (re)written."""
    global _last_rollup
    now = time.time() if now is None else now
    if not force and now - _last_rollup < MIN_GAP:
        return []
    today = _today(now)
    back = 1 if _read_day(_days_back(today, 2)) is not None else BACKFILL_DAYS
    wanted = set(_date_range(today, back + 1))
    cutoff = now - (back + 2) * 86400
    local_names = {b.get("name") or b.get("url", "llama.cpp") for b in config.get("backends") or [] if b.get("type") == "llamacpp"}
    days: dict = {d: {} for d in wanted}
    _scan_backends(days, wanted, config)
    _scan_claude(days, wanted, cutoff)
    _scan_codex(days, wanted, cutoff)
    _scan_research(days, wanted, config, local_names)
    _scan_chat(days, wanted, local_names)
    written = []
    for day in sorted(wanted):
        frozen = day < _days_back(today, 1) and _read_day(day) is not None
        if frozen:
            continue
        _write_day(day, days[day])
        written.append(day)
    _prune()
    _last_rollup = now
    try:
        from . import spend_providers
        spend_providers.poll(config, now)
    except Exception:
        pass     # a provider problem never stops the roll-up
    return written


# ── prices ───────────────────────────────────────────────────────────────────

def _top() -> dict:
    import llamawatch.server as s
    return getattr(s, "_config", None) or {}


def own_prices(config: dict | None = None) -> dict:
    cfg = config if config is not None else _top()
    p = (cfg.get("spend") or {}).get("prices") if isinstance(cfg.get("spend"), dict) else None
    return p if isinstance(p, dict) else {}


def prices(config: dict | None = None) -> dict:
    """Every price in play: the owner's own, then research models' own prices."""
    cfg = config if config is not None else _top()
    out: dict = {}
    for m in (cfg.get("research") or {}).get("models") or []:
        if m.get("name") and (m.get("price_in") or m.get("price_out")):
            out[m["name"]] = {"in": float(m.get("price_in") or 0), "out": float(m.get("price_out") or 0),
                              "cache": 0.0, "currency": str(m.get("currency") or "$")[:CURRENCY_MAX], "from": "research"}
    for name, p in own_prices(cfg).items():
        if isinstance(p, dict):
            out[name] = {"in": float(p.get("in") or 0), "out": float(p.get("out") or 0), "cache": float(p.get("cache") or 0),
                         "currency": str(p.get("currency") or "$")[:CURRENCY_MAX], "from": "spend"}
    return out


def clean_price(p: dict) -> dict | None:
    """Validate a price entry from the page; None clears it."""
    if not isinstance(p, dict):
        return None
    out = {}
    for k in ("in", "out", "cache"):
        try:
            v = float(p.get(k) or 0)
        except (TypeError, ValueError):
            raise ValueError(f"{k} must be a number")
        if v < 0 or v > 1_000_000:
            raise ValueError(f"{k} must be between 0 and 1,000,000")
        out[k] = v
    cur = str(p.get("currency") or "$").strip()[:CURRENCY_MAX]
    if not cur:
        raise ValueError("currency is empty")
    out["currency"] = cur
    return out if (out["in"] or out["out"] or out["cache"]) else None


def save_prices(patch: dict) -> dict:
    """Merge price entries into spend.prices in config.local.json and reload."""
    import llamawatch.server as s
    from .config import encrypt_secrets, get_config_dir, reload_config
    d = get_config_dir()
    p = d / "config.local.json"
    existing = json.loads(p.read_text()) if p.is_file() else {}
    block = existing.get("spend") if isinstance(existing.get("spend"), dict) else {}
    cur = dict(block.get("prices") or {})
    for name, val in patch.items():
        if val is None:
            cur.pop(name, None)
        else:
            cur[name] = val
    block["prices"] = cur
    existing["spend"] = block
    existing = encrypt_secrets(existing)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(existing, indent=2))
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    s._config = reload_config(config_dir=d)
    return own_prices()


def cost_of(row: dict, price: dict | None) -> float | None:
    if not price:
        return None
    return (row.get("in", 0) * price["in"] + row.get("out", 0) * price["out"] + row.get("cache", 0) * price["cache"]) / 1_000_000


# ── read side ────────────────────────────────────────────────────────────────

def _tokens(row: dict) -> int:
    return int(row.get("in", 0)) + int(row.get("out", 0))


def summary(config: dict, days_n: int = DEFAULT_DAYS, now: float | None = None) -> dict:
    """What the page shows: this month's paid API spend and tokens, free local
    tokens, per-day stacks by source and by job, totals per source and job."""
    now = time.time() if now is None else now
    days_n = max(1, min(MAX_DAYS, int(days_n)))
    today = _today(now)
    from . import spend_providers
    price_book = prices(config)
    pstate = spend_providers.load_state()
    month_prefix = today[:7]
    month_days = _date_range(today, int(today[8:10]))
    listed = sorted(set(_date_range(today, days_n)) | set(month_days))

    sources: dict = {}
    jobs: dict = {}
    days_out = []
    month = {"cost": 0.0, "tokens": 0, "requests": 0, "by_currency": {}}
    local_month = {"tokens": 0, "all": 0}
    for day in listed:
        rec = _read_day(day) or {"sources": {}}
        rec = {"sources": {**rec["sources"], **spend_providers.sources_for(pstate, day, config)}}
        by_src, by_job = {}, {}
        for name, row in rec["sources"].items():
            price = price_book.get(name)
            kind = row.get("kind", "tool")
            exact = row.get("cost") is not None      # a provider's own figure beats any typed price
            paid = exact or (kind != "local" and price is not None)
            cur = row.get("currency") if exact else (price["currency"] if price else None)
            tok = _tokens(row)
            cost = float(row["cost"]) if exact else (cost_of(row, price) if paid else None)
            by_src[name] = {"tokens": tok, "cost": round(cost, 4) if cost is not None else None}
            in_window = day in listed[-days_n:]
            if in_window:
                s = sources.setdefault(name, {"name": name, "kind": kind, "tokens": 0, "in": 0, "out": 0, "cache": 0,
                                              "requests": 0, "cost": 0.0 if paid else None, "currency": cur,
                                              "priced": paid, "origin": row.get("origin")})
                s["tokens"] += tok; s["in"] += row.get("in", 0); s["out"] += row.get("out", 0)
                s["cache"] += row.get("cache", 0); s["requests"] += row.get("requests", 0)
                if paid:
                    s["cost"] += cost
            if day.startswith(month_prefix):
                local_month["all"] += tok
                if kind == "local":
                    local_month["tokens"] += tok
                elif paid:
                    month["tokens"] += tok; month["requests"] += row.get("requests", 0)
                    month["by_currency"][cur] = month["by_currency"].get(cur, 0.0) + cost
            job_rows = row.get("jobs") or {}
            seen_tok = 0
            for jid, j in job_rows.items():
                jtok = _tokens(j)
                seen_tok += jtok
                jcost = j.get("cost") if j.get("cost") is not None else (cost_of(j, price) if paid else None)
                label = j.get("label") or jid
                prev = by_job.get(label) or {"tokens": 0, "cost": None}
                by_job[label] = {"tokens": prev["tokens"] + jtok,
                                 "cost": round((prev["cost"] or 0) + jcost, 4) if jcost is not None else prev["cost"]}
                if in_window:
                    jr = jobs.setdefault(jid, {"id": jid, "label": label, "source": name, "tokens": 0, "requests": 0,
                                               "cost": 0.0 if jcost is not None else None, "currency": cur})
                    jr["tokens"] += jtok; jr["requests"] += j.get("requests", 0)
                    if jcost is not None:
                        jr["cost"] = (jr["cost"] or 0) + jcost
            rest = tok - seen_tok
            if rest > 0:
                label = "Other on " + name
                by_job[label] = {"tokens": by_job.get(label, {}).get("tokens", 0) + rest, "cost": None}
                if in_window:
                    jr = jobs.setdefault("other:" + name, {"id": "other:" + name, "label": label, "source": name, "tokens": 0,
                                                            "requests": 0, "cost": None, "currency": None})
                    jr["tokens"] += rest
        if day in listed[-days_n:]:
            days_out.append({"date": day, "sources": by_src, "jobs": {k: v for k, v in by_job.items()}})

    # Money: the biggest currency is the headline, the rest are listed beside it.
    by_cur = sorted(month["by_currency"].items(), key=lambda kv: kv[1], reverse=True)
    headline = by_cur[0] if by_cur else (_default_currency(price_book), 0.0)
    month_out = {"label": _dt.date.fromisoformat(today).strftime("%B %Y"), "cost": round(headline[1], 2), "currency": headline[0],
                 "tokens": month["tokens"], "requests": month["requests"],
                 "extra": [{"currency": c, "cost": round(v, 2)} for c, v in by_cur[1:]],
                 "day_of_month": int(today[8:10]), "days_in_month": _days_in_month(today)}
    for s in sources.values():
        if s["cost"] is not None:
            s["cost"] = round(s["cost"], 4)
    for j in jobs.values():
        if j["cost"] is not None:
            j["cost"] = round(j["cost"], 4)
    local_tokens = local_month["tokens"]
    share = (local_tokens / local_month["all"]) if local_month["all"] else None
    return {
        "month": month_out,
        "local": {"tokens": local_tokens, "share": round(share, 3) if share is not None else None},
        "days": days_out,
        "days_n": days_n,
        "sources": sorted(sources.values(), key=lambda s: (s["kind"] == "local", -s["tokens"])),
        "jobs": sorted(jobs.values(), key=lambda j: -j["tokens"])[:60],
        "prices": price_book,
        "updated": now,
    }


def _default_currency(book: dict) -> str:
    for p in book.values():
        if p.get("currency"):
            return p["currency"]
    return "$"


def _days_in_month(day: str) -> int:
    d = _dt.date.fromisoformat(day)
    nxt = (d.replace(day=28) + _dt.timedelta(days=4)).replace(day=1)
    return (nxt - _dt.timedelta(days=1)).day
