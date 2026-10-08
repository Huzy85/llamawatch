"""Provider connectors: paid API spend from accounts used outside llamawatch.

The owner pastes a key for a provider on the Spend page. llamawatch asks that
provider, at most once an hour, what the account spent, and keeps the answer in
``providers.json`` next to the daily ledger. ``spend.summary`` merges it in.

Three ways a provider can answer:

* ``usage``       a usage or cost report with a figure per day (and per model):
                  OpenAI, Anthropic, OpenRouter (today only).
* ``balance``     only the money left. Each drop since the last reading is that
                  day's spend; a top-up is ignored: DeepSeek, Kimi, xAI,
                  SiliconFlow, Novita.
* ``cumulative``  a running total for the month. Each rise is that day's spend:
                  DeepInfra.

Balance and cumulative history starts the day the key is saved. Keys live under
``spend.providers.<id>.api_key`` in config.local.json, so they are encrypted
like every other secret. Nothing here ever raises into the page: a failure is
stored as ``error`` on the provider and shown beside it.
"""

import datetime as _dt
import json
import time
import urllib.error
import urllib.parse
import urllib.request

POLL_GAP = 3600            # seconds between two asks of the same provider
FIRST_DAYS = 35            # how far back the first ask goes (usage providers)
REFRESH_DAYS = 3           # later asks re-read this many days
KEEP_DAYS = 400
TIMEOUT = 12
UA = "llamawatch (spend)"
MAX_PAGES = 12


class ProviderError(Exception):
    """A readable reason a provider could not be asked. Never holds a key."""


# ── http ─────────────────────────────────────────────────────────────────────

def _http(url: str, headers: dict, params=None, body=None) -> dict:
    """GET (or POST with a JSON body) and return parsed JSON. Patched in tests."""
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
    data = json.dumps(body).encode() if body is not None else None
    h = {"User-Agent": UA, "Accept": "application/json", **headers}
    if data is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        hint = {401: "key rejected", 403: "key not allowed (wrong key type?)", 404: "endpoint not found",
                429: "rate limited, will retry"}.get(e.code, "")
        raise ProviderError(f"HTTP {e.code}" + (f": {hint}" if hint else ""))
    except urllib.error.URLError as e:
        raise ProviderError(f"cannot reach provider ({type(e.reason).__name__})")
    except (ValueError, OSError):
        raise ProviderError("unreadable answer from provider")


def _bearer(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


# ── small helpers ────────────────────────────────────────────────────────────

_CUR = {"USD": "$", "CNY": "¥", "RMB": "¥", "GBP": "£", "EUR": "€"}


def _cur(code) -> str:
    c = str(code or "USD").upper()
    return _CUR.get(c, c[:3])


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _utc_day(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime("%Y-%m-%d")


def _window(now: float, since_days: int) -> tuple[str, str]:
    """RFC 3339 start (midnight UTC, since_days ago) and end (midnight tomorrow)."""
    today = _dt.datetime.fromtimestamp(now, _dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - _dt.timedelta(days=since_days)
    end = today + _dt.timedelta(days=1)
    f = "%Y-%m-%dT%H:%M:%SZ"
    return start.strftime(f), end.strftime(f)


def _model_row(days: dict, day: str, model: str) -> dict:
    d = days.setdefault(day, {"models": {}, "cost_total": None})
    return d["models"].setdefault(model or "(other)", {"requests": 0, "in": 0, "out": 0, "cache": 0, "cost": None})


def _add_cost(row: dict, v: float) -> None:
    row["cost"] = round((row["cost"] or 0.0) + v, 6)


def _paged(url: str, headers: dict, params: list, next_key="next_page", more_key="has_more") -> list:
    """Follow a cursor until the provider says there is no more. Returns the `data` buckets."""
    out, page = [], None
    for _ in range(MAX_PAGES):
        p = list(params) + ([("page", page)] if page else [])
        r = _http(url, headers, p)
        out.extend(r.get("data") or [])
        page = r.get(next_key)
        if not page or (more_key in r and not r.get(more_key)):
            break
    return out


# ── usage providers ──────────────────────────────────────────────────────────

def fetch_anthropic(s: dict, now: float, since_days: int) -> dict:
    h = {"x-api-key": s["api_key"], "anthropic-version": "2023-06-01"}
    start, end = _window(now, since_days)
    base = "https://api.anthropic.com/v1/organizations/"
    days: dict = {}
    usage = _paged(base + "usage_report/messages", h,
                   [("starting_at", start), ("ending_at", end), ("bucket_width", "1d"), ("group_by[]", "model"), ("limit", 31)])
    for b in usage:
        day = str(b.get("starting_at") or "")[:10]
        for r in b.get("results") or []:
            row = _model_row(days, day, r.get("model"))
            cc = r.get("cache_creation") or {}
            row["in"] += int(r.get("uncached_input_tokens") or 0)
            row["out"] += int(r.get("output_tokens") or 0)
            row["cache"] += int(r.get("cache_read_input_tokens") or 0) + int(cc.get("ephemeral_1h_input_tokens") or 0) \
                + int(cc.get("ephemeral_5m_input_tokens") or 0)
    costs = _paged(base + "cost_report", h,
                   [("starting_at", start), ("ending_at", end), ("group_by[]", "description"), ("limit", 31)])
    for b in costs:
        day = str(b.get("starting_at") or "")[:10]
        for r in b.get("results") or []:
            # amount is in cents, as a decimal string
            _add_cost(_model_row(days, day, r.get("model")), _num(r.get("amount")) / 100.0)
    return {"days": days, "currency": "$"}


def fetch_openai(s: dict, now: float, since_days: int) -> dict:
    h = _bearer(s["api_key"])
    start = int(now) - since_days * 86400
    start -= start % 86400
    base = "https://api.openai.com/v1/organization/"
    days: dict = {}
    for b in _paged(base + "usage/completions", h,
                    [("start_time", start), ("bucket_width", "1d"), ("group_by", "model"), ("limit", 31)]):
        day = _utc_day(b.get("start_time") or 0)
        for r in b.get("results") or []:
            row = _model_row(days, day, r.get("model"))
            cached = int(r.get("input_cached_tokens") or 0)
            row["requests"] += int(r.get("num_model_requests") or 0)
            row["in"] += max(0, int(r.get("input_tokens") or 0) - cached)
            row["out"] += int(r.get("output_tokens") or 0)
            row["cache"] += cached
    for b in _paged(base + "costs", h, [("start_time", start), ("bucket_width", "1d"), ("limit", 31)]):
        day = _utc_day(b.get("start_time") or 0)
        total = sum(_num((r.get("amount") or {}).get("value")) for r in b.get("results") or [])
        d = days.setdefault(day, {"models": {}, "cost_total": None})
        d["cost_total"] = round(total, 6)     # a day total; line items do not match usage model ids
    return {"days": days, "currency": "$"}


def fetch_openrouter(s: dict, now: float, since_days: int) -> dict:
    r = _http("https://openrouter.ai/api/v1/key", _bearer(s["api_key"]))
    d = r.get("data") or {}
    if "usage_daily" not in d:
        raise ProviderError("unexpected answer (no usage_daily)")
    return {"days": {_utc_day(now): {"models": {}, "cost_total": round(_num(d["usage_daily"]), 6)}}, "currency": "$"}


# ── balance providers ────────────────────────────────────────────────────────

def fetch_deepseek(s: dict, now: float, since_days: int) -> dict:
    r = _http("https://api.deepseek.com/user/balance", _bearer(s["api_key"]))
    infos = r.get("balance_infos") or []
    if not infos:
        raise ProviderError("no balance in answer")
    i = infos[0]
    return {"balance": {"currency": _cur(i.get("currency")), "amount": _num(i.get("total_balance"))}}


def fetch_kimi(s: dict, now: float, since_days: int) -> dict:
    r = _http("https://api.moonshot.ai/v1/users/me/balance", _bearer(s["api_key"]))
    d = r.get("data")
    if not isinstance(d, dict) or "available_balance" not in d:
        raise ProviderError("no balance in answer")
    return {"balance": {"currency": "$", "amount": _num(d["available_balance"])}}


def fetch_siliconflow(s: dict, now: float, since_days: int) -> dict:
    r = _http("https://api.siliconflow.com/v1/user/info", _bearer(s["api_key"]))
    d = r.get("data")
    if not isinstance(d, dict) or "totalBalance" not in d:
        raise ProviderError("no balance in answer")
    return {"balance": {"currency": "$", "amount": _num(d["totalBalance"])}}


def fetch_novita(s: dict, now: float, since_days: int) -> dict:
    r = _http("https://api.novita.ai/openapi/v1/billing/balance/detail", _bearer(s["api_key"]))
    if "availableBalance" not in r:
        raise ProviderError("no balance in answer")
    return {"balance": {"currency": "$", "amount": _num(r["availableBalance"]) / 10000.0}}


def fetch_xai(s: dict, now: float, since_days: int) -> dict:
    team = urllib.parse.quote(str(s.get("team_id") or "").strip(), safe="")
    if not team:
        raise ProviderError("team id is missing")
    r = _http(f"https://management-api.x.ai/v1/billing/teams/{team}/prepaid/balance", _bearer(s["api_key"]))
    total = (r.get("total") or {}).get("val")
    if total is None:
        raise ProviderError("no balance in answer")
    # The ledger counts credit as negative cents.
    return {"balance": {"currency": "$", "amount": -_num(total) / 100.0}}


def fetch_deepinfra(s: dict, now: float, since_days: int) -> dict:
    r = _http("https://api.deepinfra.com/payment/usage", _bearer(s["api_key"]), [("from", "current")])
    months = r.get("months") or []
    if not months:
        raise ProviderError("no month in answer")
    m = months[0]
    return {"cumulative": {"period": str(m.get("period") or ""), "currency": "$", "amount": _num(m.get("total_cost")) / 100.0}}


# ── catalogue ────────────────────────────────────────────────────────────────

PROVIDERS = {
    "openai": {"label": "OpenAI", "mode": "usage", "fetch": fetch_openai, "key": "Admin key (sk-admin-...)",
               "hosts": ["api.openai.com"], "detail": "Daily tokens and cost, by model"},
    "anthropic": {"label": "Anthropic API", "mode": "usage", "fetch": fetch_anthropic, "key": "Admin key (sk-ant-admin...)",
                  "hosts": ["api.anthropic.com"], "detail": "Daily tokens and cost, by model"},
    "openrouter": {"label": "OpenRouter", "mode": "usage", "fetch": fetch_openrouter, "key": "Normal API key",
                   "hosts": ["openrouter.ai"], "detail": "Cost per day, kept from the day you connect"},
    "deepseek": {"label": "DeepSeek", "mode": "balance", "fetch": fetch_deepseek, "key": "Normal API key",
                 "hosts": ["api.deepseek.com"], "detail": "Spend worked out from balance drops"},
    "kimi": {"label": "Kimi (Moonshot)", "mode": "balance", "fetch": fetch_kimi, "key": "Normal API key",
             "hosts": ["api.moonshot.ai"], "detail": "Spend worked out from balance drops"},
    "xai": {"label": "xAI (Grok)", "mode": "balance", "fetch": fetch_xai, "key": "Management key", "extra": ["team_id"],
            "hosts": ["api.x.ai"], "detail": "Spend worked out from prepaid balance drops"},
    "siliconflow": {"label": "SiliconFlow", "mode": "balance", "fetch": fetch_siliconflow, "key": "Normal API key",
                    "hosts": ["api.siliconflow.com"], "detail": "Spend worked out from balance drops"},
    "novita": {"label": "Novita AI", "mode": "balance", "fetch": fetch_novita, "key": "Normal API key",
               "hosts": ["api.novita.ai"], "detail": "Spend worked out from balance drops"},
    "deepinfra": {"label": "DeepInfra", "mode": "cumulative", "fetch": fetch_deepinfra, "key": "Normal API key",
                  "hosts": ["api.deepinfra.com"], "detail": "Spend worked out from the month's running total"},
}

MODE_TEXT = {"usage": "Usage report", "balance": "Balance drops", "cumulative": "Monthly total"}


# ── settings and state ───────────────────────────────────────────────────────

def connected(config: dict) -> dict:
    """{id: settings} for every provider with a key saved."""
    block = (config.get("spend") or {}).get("providers") if isinstance(config.get("spend"), dict) else None
    out = {}
    for pid, s in (block or {}).items():
        if pid in PROVIDERS and isinstance(s, dict) and s.get("api_key"):
            out[pid] = s
    return out


def covered_hosts(config: dict) -> set:
    """Hosts whose spend a connected provider already reports (so research runs there are not counted twice)."""
    return {h for pid in connected(config) for h in PROVIDERS[pid]["hosts"]}


def _state_path():
    from . import spend
    return spend.data_dir() / "providers.json"


def load_state() -> dict:
    try:
        d = json.loads(_state_path().read_text())
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _save_state(state: dict) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state))


def forget(pid: str) -> None:
    st = load_state()
    if st.pop(pid, None) is not None:
        _save_state(st)


def _apply(entry: dict, res: dict, now: float) -> None:
    """Fold one answer into a provider's stored record."""
    today = _utc_day(now)
    cur = res.get("currency", "$")
    if "days" in res:
        store = entry.setdefault("days", {})
        for day, d in res["days"].items():
            store[day] = {"currency": cur, "cost_total": d.get("cost_total"), "models": d.get("models") or {}}
        for day in sorted(store)[:-KEEP_DAYS]:
            store.pop(day, None)
    elif "balance" in res or "cumulative" in res:
        reading = res.get("balance") or res["cumulative"]
        last = entry.get("last")
        spent = entry.setdefault("spent", {})
        if last and last.get("currency") == reading["currency"]:
            if "balance" in res:
                delta = last["amount"] - reading["amount"]          # a drop is spend; a rise is a top-up
            elif last.get("period") == res["cumulative"]["period"]:
                delta = reading["amount"] - last["amount"]
            else:
                delta = reading["amount"]                            # a new month starts from nothing
            if delta > 0.00001:
                day = spent.setdefault(today, {})
                day[reading["currency"]] = round(day.get(reading["currency"], 0.0) + delta, 6)
        entry["last"] = dict(reading, period=(res.get("cumulative") or {}).get("period"), at=now)
        for day in sorted(spent)[:-KEEP_DAYS]:
            spent.pop(day, None)


def ask(pid: str, settings: dict, now: float, first: bool) -> dict:
    """Ask one provider. Returns the raw answer or raises ProviderError."""
    p = PROVIDERS[pid]
    try:
        return p["fetch"](settings, now, FIRST_DAYS if first else REFRESH_DAYS)
    except ProviderError:
        raise
    except Exception as e:       # a parsing slip must not reach the page
        raise ProviderError(f"unexpected answer ({type(e).__name__})")


def poll(config: dict, now: float | None = None, force: bool = False, only: str | None = None) -> dict:
    """Ask every connected provider that is due. Never raises. Returns the state."""
    now = time.time() if now is None else now
    conn = connected(config)
    state = load_state()
    changed = False
    for pid in list(state):
        if pid not in conn:        # key removed some other way
            state.pop(pid)
            changed = True
    for pid, settings in conn.items():
        if only and pid != only:
            continue
        entry = state.setdefault(pid, {})
        if not force and now - entry.get("polled", 0) < POLL_GAP:
            continue
        entry["polled"] = now
        changed = True
        try:
            _apply(entry, ask(pid, settings, now, first="ok" not in entry), now)
            entry["ok"] = now
            entry.pop("error", None)
        except ProviderError as e:
            entry["error"] = str(e)
    if changed:
        try:
            _save_state(state)
        except OSError:
            pass
    return state


# ── ledger view ──────────────────────────────────────────────────────────────

def sources_for(state: dict, day: str, config: dict) -> dict:
    """The provider sources for one day, shaped like the ledger's own sources."""
    out: dict = {}
    for pid in connected(config):
        e = state.get(pid) or {}
        label = PROVIDERS[pid]["label"]
        origin = MODE_TEXT[PROVIDERS[pid]["mode"]]
        d = (e.get("days") or {}).get(day)
        if d:
            models = d.get("models") or {}
            mcost = [m["cost"] for m in models.values() if m.get("cost") is not None]
            cost = d["cost_total"] if d.get("cost_total") is not None else (sum(mcost) if mcost else None)
            row = {"kind": "api", "requests": 0, "in": 0, "out": 0, "cache": 0, "jobs": {},
                   "currency": d.get("currency", "$"), "origin": origin}
            if cost is not None:
                row["cost"] = round(cost, 6)
            for m, v in models.items():
                for k in ("requests", "in", "out", "cache"):
                    row[k] += v.get(k, 0)
                j = {"label": m, "requests": v.get("requests", 0), "in": v.get("in", 0), "out": v.get("out", 0),
                     "cache": v.get("cache", 0)}
                if v.get("cost") is not None:
                    j["cost"] = v["cost"]
                row["jobs"][f"provider:{pid}:{m}"] = j
            if row["in"] or row["out"] or row["requests"] or cost is not None:
                out[label] = row
        for cur, amount in ((e.get("spent") or {}).get(day) or {}).items():
            name = label if label not in out else f"{label} ({cur})"
            out[name] = {"kind": "api", "requests": 0, "in": 0, "out": 0, "cache": 0, "jobs": {},
                         "cost": round(amount, 6), "currency": cur, "origin": origin}
    return out


def status(config: dict) -> list:
    """What the page lists: every supported provider and where it stands. No keys."""
    state = load_state()
    conn = connected(config)
    rows = []
    for pid, p in PROVIDERS.items():
        e = state.get(pid) or {}
        rows.append({"id": pid, "label": p["label"], "mode": p["mode"], "origin": MODE_TEXT[p["mode"]], "key": p["key"],
                     "extra": p.get("extra", []), "detail": p["detail"], "connected": pid in conn,
                     "last_ok": e.get("ok"), "error": e.get("error") if pid in conn else None,
                     "balance": ({"amount": round(e["last"]["amount"], 2), "currency": e["last"]["currency"]}
                                 if pid in conn and p["mode"] == "balance" and e.get("last") else None)})
    return rows


# ── saving ───────────────────────────────────────────────────────────────────

def _edit_config(mutate) -> None:
    import llamawatch.server as s
    from .config import encrypt_secrets, get_config_dir, reload_config
    import os
    d = get_config_dir()
    p = d / "config.local.json"
    existing = json.loads(p.read_text()) if p.is_file() else {}
    block = existing.get("spend") if isinstance(existing.get("spend"), dict) else {}
    provs = dict(block.get("providers") or {})
    mutate(provs)
    block["providers"] = provs
    existing["spend"] = block
    existing = encrypt_secrets(existing)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(existing, indent=2))
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    s._config = reload_config(config_dir=d)


def clean_settings(pid: str, body: dict) -> dict:
    if pid not in PROVIDERS:
        raise ValueError("unknown provider")
    key = str((body or {}).get("api_key") or "").strip()
    if not key or len(key) > 512 or any(c.isspace() for c in key):
        raise ValueError("paste the key with no spaces")
    out = {"api_key": key}
    for extra in PROVIDERS[pid].get("extra", []):
        v = str((body or {}).get(extra) or "").strip()
        if not v or len(v) > 128:
            raise ValueError(f"{extra.replace('_', ' ')} is needed for {PROVIDERS[pid]['label']}")
        out[extra] = v
    return out


def connect(pid: str, body: dict, now: float | None = None) -> dict:
    """Test the key against the provider; save it only if the provider accepts it."""
    now = time.time() if now is None else now
    settings = clean_settings(pid, body)
    res = ask(pid, settings, now, first=True)           # raises ProviderError, nothing saved
    _edit_config(lambda provs: provs.__setitem__(pid, settings))
    state = load_state()
    entry = {}
    _apply(entry, res, now)
    entry["polled"] = entry["ok"] = now
    state[pid] = entry
    _save_state(state)
    return {"ok": True}


def disconnect(pid: str) -> None:
    if pid not in PROVIDERS:
        raise ValueError("unknown provider")
    _edit_config(lambda provs: provs.pop(pid, None))
    forget(pid)
