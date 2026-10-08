"""Spend: the daily ledger roll-up over coding-tool logs, backend daily files,
research runs and the chat log; pricing at read time; the prices store; the
routes; sample mode."""

import datetime as dt
import json
from unittest.mock import MagicMock

import httpx
import pytest

import llamawatch.config as config_mod
from llamawatch import audit, spend
from llamawatch.collectors import token_usage as tu

NOW = dt.datetime(2026, 1, 14, 12, 0, tzinfo=dt.timezone.utc).timestamp()
TODAY, YDAY, OLD = "2026-01-14", "2026-01-13", "2026-01-10"


# ── fixtures ─────────────────────────────────────────────────────────────────
@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Every path the roll-up reads points into tmp; config in tmp; audit in tmp."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("LLAMAWATCH_REQUEST_LOG_DIR", str(tmp_path / "reqlog"))
    monkeypatch.setattr(tu, "_SNAP_DIR", tmp_path / "data" / "llamawatch" / "usage")
    monkeypatch.setattr(tu, "_read_counters", lambda url: None)      # no real server is asked for /metrics
    monkeypatch.setattr(spend, "_last_rollup", 0.0)
    monkeypatch.setattr(audit, "_LOG_FILE", tmp_path / "audit.log")
    cfg = {"port": 8451, "host": "0.0.0.0", "auth_enabled": False, "auth_password_hash": "",
           "backends": [{"name": "local-main", "type": "llamacpp", "url": "http://127.0.0.1:8080"}],
           "research": {"data_dir": str(tmp_path / "research"),
                        "models": [{"name": "example-api", "price_in": 2.0, "price_out": 8.0, "currency": "£"}]},
           "spend": {"prices": {"claude-opus-5-5": {"in": 15, "out": 75, "cache": 1.5, "currency": "$"}}}}
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    config_mod.reset_config()
    import llamawatch.server as srv
    srv._config = config_mod.load_config(config_dir=tmp_path)
    srv._adapters = MagicMock()
    srv._collector_registry = MagicMock()
    yield tmp_path
    config_mod.reset_config()


@pytest.fixture()
def cfg(env):
    import llamawatch.server as srv
    return srv._config


@pytest.fixture()
def client(env):
    import llamawatch.server as srv
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url="http://testserver")


@pytest.fixture()
def remote(env):
    import llamawatch.server as srv
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app, client=("203.0.113.9", 4000)), base_url="http://testserver")


def claude_line(day, msg_id, req_id, t_in=100, t_out=20, cache=50, model="claude-opus-5-5", cwd="/srv/demo/relay", hour="10"):
    return json.dumps({"type": "assistant", "cwd": cwd, "timestamp": f"{day}T{hour}:00:00.000Z", "requestId": req_id,
                       "message": {"id": msg_id, "model": model, "usage": {"input_tokens": t_in, "output_tokens": t_out,
                                                                          "cache_read_input_tokens": cache, "cache_creation_input_tokens": 0}}})


def write_claude(env, lines, folder="-srv-demo-relay", name="s1.jsonl"):
    d = env / "claude" / "projects" / folder
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text("\n".join(lines) + "\n")


def codex_line(day, t_in, t_out, cached=0, hour="09"):
    return json.dumps({"timestamp": f"{day}T{hour}:00:00.000Z", "payload": {"type": "token_count", "info": {"total_token_usage": {
        "input_tokens": t_in, "output_tokens": t_out, "cached_input_tokens": cached}}}})


def write_research(env, folder, started, model, t_in, t_out, cost=0.0, question="Which heater is cheapest to run?"):
    d = env / "research" / folder
    d.mkdir(parents=True, exist_ok=True)
    (d / "run.json").write_text(json.dumps({"started": started, "model": model, "calls": 4, "tokens_in": t_in, "tokens_out": t_out,
                                            "cost": cost, "question": question, "status": "done"}))


def ledger(env, day):
    return json.loads((env / "data" / "llamawatch" / "spend" / f"{day}.json").read_text())["sources"]


# ── roll-up: sources ─────────────────────────────────────────────────────────
def test_claude_lines_dedup_and_split_by_project(env, cfg):
    write_claude(env, [claude_line(TODAY, "m1", "r1"), claude_line(TODAY, "m1", "r1"),   # same message, two tool-result lines
                       claude_line(TODAY, "m2", "r2", cwd="/srv/demo/photos"),
                       claude_line(TODAY, "m3", "r3", model="<synthetic>")])
    assert spend.rollup(cfg, now=NOW, force=True)
    s = ledger(env, TODAY)["claude-opus-5-5"]
    assert s["kind"] == "tool" and s["requests"] == 2 and s["in"] == 200 and s["out"] == 40 and s["cache"] == 100
    assert set(s["jobs"]) == {"claude:relay", "claude:photos"}
    assert s["jobs"]["claude:relay"] == {"label": "relay", "requests": 1, "in": 100, "out": 20, "cache": 50}
    assert "<synthetic>" not in ledger(env, TODAY)


def test_claude_day_is_utc_date_of_the_line(env, cfg):
    write_claude(env, [claude_line(YDAY, "m1", "r1", hour="23"), claude_line(TODAY, "m2", "r2", hour="00")])
    spend.rollup(cfg, now=NOW, force=True)
    assert ledger(env, YDAY)["claude-opus-5-5"]["requests"] == 1
    assert ledger(env, TODAY)["claude-opus-5-5"]["requests"] == 1


def test_codex_diffs_running_totals(env, cfg):
    d = env / "codex" / "sessions" / "2026" / "01"
    d.mkdir(parents=True)
    (d / "a.jsonl").write_text("\n".join([codex_line(YDAY, 1000, 100, 200), codex_line(TODAY, 1500, 160, 300), codex_line(TODAY, 1500, 160, 300)]) + "\n")
    spend.rollup(cfg, now=NOW, force=True)
    assert ledger(env, YDAY)["Codex"] == {"kind": "tool", "requests": 1, "in": 800, "out": 100, "cache": 200,
                                          "jobs": {"codex:Codex": {"label": "Codex", "requests": 1, "in": 800, "out": 100, "cache": 200}}}
    t = ledger(env, TODAY)["Codex"]
    assert (t["requests"], t["in"], t["out"], t["cache"]) == (1, 400, 60, 100)   # the unchanged third event adds nothing


def test_backend_daily_file_feeds_local_source(env, cfg):
    tu._add_daily("local-main", NOW, 5000, 700)
    tu._add_daily("local-main", NOW - 86400, 100, 10)
    spend.rollup(cfg, now=NOW, force=True)
    assert ledger(env, TODAY)["local-main"] == {"kind": "local", "requests": 0, "in": 5000, "out": 700, "cache": 0, "jobs": {}}
    assert ledger(env, YDAY)["local-main"]["in"] == 100


def test_rollup_takes_its_own_backend_reading(env, cfg, monkeypatch):
    readings = iter([(1000, 100), (1600, 150)])
    monkeypatch.setattr(tu, "_read_counters", lambda url: next(readings))
    spend.rollup(cfg, now=NOW, force=True)                 # first reading: a baseline, no growth yet
    spend.rollup(cfg, now=NOW, force=True)
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    assert tu.read_daily("local-main")[today] == {"in": 600, "out": 50}   # a day nobody watched still counts


def test_research_run_on_unpriced_model_counts_as_free_local(env, cfg):
    write_research(env, "r-0114-c", "2026-01-14 09:30", "own-server", 40000, 5000)   # research model, no price, not a watched backend
    spend.rollup(cfg, now=NOW, force=True)
    s = ledger(env, TODAY)["own-server"]
    assert s["kind"] == "local" and s["in"] == 40000 and s["requests"] == 4
    assert s["jobs"]["research:r-0114-c"]["in"] == 40000


def test_collect_backend_accumulates_daily_growth(env, monkeypatch):
    readings = iter([(1000, 100), (1600, 150), (200, 20)])    # third: server restarted, counters dropped
    monkeypatch.setattr(tu, "_read_counters", lambda url: next(readings))
    b = {"name": "local-main", "type": "llamacpp", "url": "http://127.0.0.1:8080"}
    tu._collect_backend(b); tu._collect_backend(b); tu._collect_backend(b)
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    assert tu.read_daily("local-main")[today] == {"in": 600 + 200, "out": 50 + 20}


def test_research_run_on_api_model_is_a_job_with_recorded_cost(env, cfg):
    write_research(env, "r-0114-a", "2026-01-14 09:30", "example-api", 40000, 5000, cost=0.12)
    spend.rollup(cfg, now=NOW, force=True)
    s = ledger(env, TODAY)["example-api"]
    assert s["kind"] == "api" and s["requests"] == 4 and s["in"] == 40000
    j = s["jobs"]["research:r-0114-a"]
    assert j["label"] == "Which heater is cheapest to run?" and j["cost"] == 0.12


def test_research_run_on_local_backend_does_not_double_count(env, cfg):
    tu._add_daily("local-main", NOW, 90000, 9000)
    write_research(env, "r-0114-b", "2026-01-14 09:30", "local-main", 40000, 5000)
    spend.rollup(cfg, now=NOW, force=True)
    s = ledger(env, TODAY)["local-main"]
    assert s["in"] == 90000 and s["kind"] == "local"                      # the backend total already holds the run
    assert s["jobs"]["research:r-0114-b"]["in"] == 40000


def test_chat_log_becomes_a_job_on_the_local_source(env, cfg):
    d = env / "reqlog"; d.mkdir()
    (d / f"requests-{TODAY}.jsonl").write_text(json.dumps({"timestamp": "x", "model": "local-main", "prompt_tokens": 300, "completion_tokens": 40, "source": "chat"}) + "\n"
                                                + json.dumps({"timestamp": "x", "model": "unknown-model", "prompt_tokens": 1, "completion_tokens": 1}) + "\n")
    tu._add_daily("local-main", NOW, 1000, 100)
    spend.rollup(cfg, now=NOW, force=True)
    s = ledger(env, TODAY)["local-main"]
    assert s["requests"] == 1 and s["jobs"]["chat"] == {"label": "Chat", "requests": 1, "in": 300, "out": 40, "cache": 0}
    assert "unknown-model" not in ledger(env, TODAY)


# ── roll-up: days, freezing, throttling ──────────────────────────────────────
def test_first_rollup_backfills_then_only_two_days(env, cfg):
    written = spend.rollup(cfg, now=NOW, force=True)
    assert len(written) == spend.BACKFILL_DAYS + 1 and written[0] == "2025-12-15" and written[-1] == TODAY
    again = spend.rollup(cfg, now=NOW + 1, force=True)
    assert again == [YDAY, TODAY]


def test_older_days_are_frozen(env, cfg):
    write_claude(env, [claude_line(OLD, "m1", "r1")])
    spend.rollup(cfg, now=NOW, force=True)
    assert ledger(env, OLD)["claude-opus-5-5"]["requests"] == 1
    write_claude(env, [claude_line(OLD, "m1", "r1"), claude_line(OLD, "m2", "r2")])
    spend.rollup(cfg, now=NOW + 5, force=True)
    assert ledger(env, OLD)["claude-opus-5-5"]["requests"] == 1       # a late line for an old day is not picked up


def test_rollup_is_throttled(env, cfg):
    assert spend.rollup(cfg, now=NOW)
    assert spend.rollup(cfg, now=NOW + 10) == []
    assert spend.rollup(cfg, now=NOW + spend.MIN_GAP + 1)


def test_broken_lines_and_files_are_skipped(env, cfg):
    write_claude(env, ["not json", '{"type":"assistant","message":{"usage":{}}}', claude_line(TODAY, "m1", "r1")])
    (env / "research" / "bad").mkdir(parents=True)
    (env / "research" / "bad" / "run.json").write_text("{")
    spend.rollup(cfg, now=NOW, force=True)
    assert ledger(env, TODAY)["claude-opus-5-5"]["requests"] == 1


def test_prune_keeps_only_recent_days(env, cfg, monkeypatch):
    monkeypatch.setattr(spend, "KEEP_DAYS", 5)
    spend.rollup(cfg, now=NOW, force=True)
    assert len(list((env / "data" / "llamawatch" / "spend").glob("????-??-??.json"))) == 5


# ── summary: money at read time ──────────────────────────────────────────────
def test_summary_prices_tokens_and_keeps_currencies_apart(env, cfg):
    write_claude(env, [claude_line(TODAY, "m1", "r1", t_in=1_000_000, t_out=100_000, cache=2_000_000)])
    write_research(env, "r-0114-a", "2026-01-14 09:30", "example-api", 40000, 5000, cost=0.12)
    d = env / "codex" / "sessions"; d.mkdir(parents=True)
    (d / "a.jsonl").write_text(codex_line(TODAY, 0, 0) + "\n" + codex_line(TODAY, 5000, 500) + "\n")
    tu._add_daily("local-main", NOW, 300_000, 30_000)
    spend.rollup(cfg, now=NOW, force=True)
    s = spend.summary(cfg, 30, now=NOW)
    # claude: 1M*15 + 0.1M*75 + 2M*1.5 = 15 + 7.5 + 3 = 25.5 $
    assert s["month"]["cost"] == 25.5 and s["month"]["currency"] == "$"
    assert s["month"]["extra"] == [{"currency": "£", "cost": 0.12}]
    assert s["month"]["tokens"] == 1_100_000 + 45_000 and s["month"]["requests"] == 1 + 4   # Codex has no price: tokens only
    assert s["month"]["label"] == "January 2026" and s["month"]["day_of_month"] == 14 and s["month"]["days_in_month"] == 31
    assert s["local"]["tokens"] == 330_000 and 0 < s["local"]["share"] < 1
    by = {x["name"]: x for x in s["sources"]}
    assert by["Codex"]["cost"] is None and by["Codex"]["priced"] is False and by["Codex"]["tokens"] == 5500
    assert by["local-main"]["cost"] is None and by["local-main"]["kind"] == "local"
    assert by["example-api"]["cost"] == 0.12 and by["example-api"]["currency"] == "£"
    assert [x["name"] for x in s["sources"]][-1] == "local-main"           # local sources sort last
    jobs = {j["id"]: j for j in s["jobs"]}
    assert jobs["research:r-0114-a"]["cost"] == 0.12 and jobs["claude:relay"]["cost"] == 25.5
    assert "other:local-main" in jobs and jobs["other:local-main"]["tokens"] == 330_000
    today = [d for d in s["days"] if d["date"] == TODAY][0]
    assert today["sources"]["claude-opus-5-5"] == {"tokens": 1_100_000, "cost": 25.5}
    assert today["jobs"]["Other on local-main"]["tokens"] == 330_000
    assert len(s["days"]) == 30 and s["days"][-1]["date"] == TODAY and s["days_n"] == 30


def test_summary_window_is_clamped_and_month_is_not(env, cfg):
    write_claude(env, [claude_line(OLD, "m1", "r1", t_in=1_000_000, t_out=0, cache=0)])
    spend.rollup(cfg, now=NOW, force=True)
    s = spend.summary(cfg, 3, now=NOW)
    assert len(s["days"]) == 3 and s["days"][0]["date"] == "2026-01-12"
    assert s["month"]["cost"] == 15.0                                     # the 10th is in the month even if not in the window
    assert spend.summary(cfg, 9999, now=NOW)["days_n"] == spend.MAX_DAYS
    assert spend.summary(cfg, 0, now=NOW)["days_n"] == 1


def test_price_change_reprices_history(env, cfg):
    write_claude(env, [claude_line(OLD, "m1", "r1", t_in=1_000_000, t_out=0, cache=0)])
    spend.rollup(cfg, now=NOW, force=True)
    assert spend.summary(cfg, 30, now=NOW)["month"]["cost"] == 15.0
    spend.save_prices({"claude-opus-5-5": {"in": 3.0, "out": 15.0, "cache": 0.3, "currency": "$"}})
    import llamawatch.server as srv
    assert spend.summary(srv._config, 30, now=NOW)["month"]["cost"] == 3.0


def test_prices_merge_research_and_own(cfg):
    p = spend.prices(cfg)
    assert p["example-api"] == {"in": 2.0, "out": 8.0, "cache": 0.0, "currency": "£", "from": "research"}
    assert p["claude-opus-5-5"]["from"] == "spend" and p["claude-opus-5-5"]["cache"] == 1.5


@pytest.mark.parametrize("bad", [{"in": "x"}, {"in": -1}, {"in": 1e9}, {"in": 1, "currency": "   "}, "nope"])
def test_clean_price_rejects(bad):
    if bad == "nope":
        assert spend.clean_price(bad) is None
    else:
        with pytest.raises(ValueError):
            spend.clean_price(bad)


def test_clean_price_empty_means_clear():
    assert spend.clean_price({"in": 0, "out": "", "cache": None}) is None
    assert spend.clean_price({"in": "1.5", "currency": "EUR"}) == {"in": 1.5, "out": 0.0, "cache": 0.0, "currency": "EUR"}


# ── routes ───────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_pages_and_summary_route(client, env):
    r = await client.get("/spend")
    assert r.status_code == 200 and "Local tokens (free)" in r.text and "layout.js" in r.text
    write_claude(env, [claude_line(dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"), "m1", "r1")])
    r = await client.get("/api/spend?days=7")
    assert r.status_code == 200
    d = r.json()
    assert d["days_n"] == 7 and len(d["days"]) == 7 and "month" in d and "prices" in d
    assert d["days"][-1]["sources"]["claude-opus-5-5"]["tokens"] == 120


@pytest.mark.asyncio
async def test_prices_route_saves_audits_and_validates(client, env):
    r = await client.put("/api/spend/prices", json={"prices": {"Codex": {"in": "1.25", "out": 10, "cache": 0, "currency": "$"}}})
    assert r.status_code == 200 and r.json()["prices"]["Codex"]["in"] == 1.25
    saved = json.loads((env / "config.local.json").read_text())["spend"]["prices"]
    assert saved["Codex"] == {"in": 1.25, "out": 10.0, "cache": 0.0, "currency": "$"}
    assert "claude-opus-5-5" not in saved            # only own entries land in config.local.json
    assert r.json()["prices"]["claude-opus-5-5"]["in"] == 15.0   # the config.json entry still shows, deep-merged
    assert [e for e in audit.read(limit=50) if e["action"] == "spend_prices"][0]["target"] == "Codex"
    r = await client.put("/api/spend/prices", json={"prices": {"Codex": {"in": -5}}})
    assert r.status_code == 400 and "Codex" in r.json()["error"]
    r = await client.put("/api/spend/prices", json={})
    assert r.status_code == 400
    r = await client.put("/api/spend/prices", json={"prices": {"Codex": None}})
    assert r.status_code == 200 and "Codex" not in json.loads((env / "config.local.json").read_text())["spend"]["prices"]


@pytest.mark.asyncio
async def test_prices_route_refuses_remote_without_auth(remote):
    r = await remote.put("/api/spend/prices", json={"prices": {"Codex": {"in": 1}}})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_summary_route_survives_rollup_error(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(spend, "rollup", boom)
    r = await client.get("/api/spend")
    assert r.status_code == 200 and r.json()["days_n"] == 30
    assert [e for e in audit.read(limit=50) if e["action"] == "spend_rollup"][0]["outcome"] == "fail"


# ── provider connectors ──────────────────────────────────────────────────────
from llamawatch import spend_providers as sp


def fake_http(monkeypatch, answers):
    """Answer each URL (matched by the end of its path) from `answers`; record the calls."""
    calls = []

    def _http(url, headers, params=None, body=None):
        calls.append((url, dict(headers), params))
        for tail, ans in answers.items():
            if url.endswith(tail):
                if isinstance(ans, Exception):
                    raise ans
                return ans(params) if callable(ans) else ans
        raise AssertionError("unexpected url " + url)
    monkeypatch.setattr(sp, "_http", _http)
    return calls


def conn(cfg_dict, **providers):
    cfg_dict.setdefault("spend", {})["providers"] = {k: dict(v) for k, v in providers.items()}
    return cfg_dict


def test_anthropic_reads_tokens_and_cents(monkeypatch):
    calls = fake_http(monkeypatch, {
        "usage_report/messages": {"data": [{"starting_at": "2026-01-13T00:00:00Z", "results": [
            {"model": "claude-x", "uncached_input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 50,
             "cache_creation": {"ephemeral_1h_input_tokens": 5, "ephemeral_5m_input_tokens": 5}}]}], "has_more": False, "next_page": None},
        "cost_report": {"data": [{"starting_at": "2026-01-13T00:00:00Z", "results": [
            {"model": "claude-x", "amount": "250.5", "currency": "USD"}]}], "has_more": False, "next_page": None}})
    r = sp.fetch_anthropic({"api_key": "k"}, NOW, 3)
    row = r["days"]["2026-01-13"]["models"]["claude-x"]
    assert (row["in"], row["out"], row["cache"]) == (100, 20, 60)
    assert row["cost"] == pytest.approx(2.505)
    assert calls[0][1]["x-api-key"] == "k"


def test_openai_splits_cached_and_takes_day_total(monkeypatch):
    fake_http(monkeypatch, {
        "usage/completions": {"data": [{"start_time": 1768262400, "results": [
            {"model": "m1", "input_tokens": 1000, "input_cached_tokens": 400, "output_tokens": 50, "num_model_requests": 7}]}]},
        "organization/costs": {"data": [{"start_time": 1768262400, "results": [{"amount": {"value": 0.4, "currency": "usd"}},
                                                                                {"amount": {"value": 0.1, "currency": "usd"}}]}]}})
    r = sp.fetch_openai({"api_key": "k"}, NOW, 3)
    d = r["days"]["2026-01-13"]
    assert d["models"]["m1"] == {"requests": 7, "in": 600, "out": 50, "cache": 400, "cost": None}
    assert d["cost_total"] == pytest.approx(0.5)


def test_paging_follows_next_page(monkeypatch):
    pages = iter([{"data": [{"starting_at": "2026-01-12T00:00:00Z", "results": []}], "has_more": True, "next_page": "p2"},
                  {"data": [{"starting_at": "2026-01-13T00:00:00Z", "results": []}], "has_more": False, "next_page": None}])
    calls = fake_http(monkeypatch, {"usage_report/messages": lambda p: next(pages), "cost_report": {"data": [], "has_more": False}})
    sp.fetch_anthropic({"api_key": "k"}, NOW, 3)
    assert ("page", "p2") in calls[1][2]


@pytest.mark.parametrize("fn,url,answer,want", [
    (sp.fetch_deepseek, "user/balance", {"balance_infos": [{"currency": "CNY", "total_balance": "12.50"}]}, ("¥", 12.5)),
    (sp.fetch_kimi, "me/balance", {"data": {"available_balance": 7.25}}, ("$", 7.25)),
    (sp.fetch_siliconflow, "user/info", {"data": {"totalBalance": "88.88"}}, ("$", 88.88)),
    (sp.fetch_novita, "balance/detail", {"availableBalance": "1000000"}, ("$", 100.0)),
])
def test_balance_providers(monkeypatch, fn, url, answer, want):
    fake_http(monkeypatch, {url: answer})
    b = fn({"api_key": "k"}, NOW, 3)["balance"]
    assert (b["currency"], b["amount"]) == (want[0], pytest.approx(want[1]))


def test_xai_needs_team_and_reads_credit_as_negative_cents(monkeypatch):
    fake_http(monkeypatch, {"prepaid/balance": {"total": {"val": "-1000"}}})
    with pytest.raises(sp.ProviderError):
        sp.fetch_xai({"api_key": "k"}, NOW, 3)
    assert sp.fetch_xai({"api_key": "k", "team_id": "t-1"}, NOW, 3)["balance"]["amount"] == 10.0


def test_deepinfra_and_openrouter(monkeypatch):
    fake_http(monkeypatch, {"payment/usage": {"months": [{"period": "2026.01", "total_cost": 1234}]},
                            "api/v1/key": {"data": {"usage_daily": 0.42}}})
    assert sp.fetch_deepinfra({"api_key": "k"}, NOW, 3)["cumulative"] == {"period": "2026.01", "currency": "$", "amount": pytest.approx(12.34)}
    assert sp.fetch_openrouter({"api_key": "k"}, NOW, 3)["days"][TODAY]["cost_total"] == 0.42


def test_balance_drop_is_spend_and_topup_is_not(env, monkeypatch):
    cfg_d = conn({}, deepseek={"api_key": "k"})
    bal = iter([50.0, 47.5, 100.0, 99.0])
    fake_http(monkeypatch, {"user/balance": lambda p: {"balance_infos": [{"currency": "USD", "total_balance": str(next(bal))}]}})
    for i in range(4):
        sp.poll(cfg_d, NOW + i * 4000)
    spent = sp.load_state()["deepseek"]["spent"][TODAY]["$"]
    assert spent == pytest.approx(3.5)                      # 2.5 drop + 1.0 drop; the top-up counted nothing


def test_cumulative_new_month_starts_from_nothing():
    e = {}
    sp._apply(e, {"cumulative": {"period": "2026.01", "currency": "$", "amount": 10.0}}, NOW)
    sp._apply(e, {"cumulative": {"period": "2026.01", "currency": "$", "amount": 12.0}}, NOW)
    sp._apply(e, {"cumulative": {"period": "2026.02", "currency": "$", "amount": 0.5}}, NOW)
    assert e["spent"][TODAY]["$"] == pytest.approx(2.5)


def test_poll_is_hourly_and_errors_are_kept(env, monkeypatch):
    cfg_d = conn({}, kimi={"api_key": "k"})
    calls = fake_http(monkeypatch, {"me/balance": sp.ProviderError("HTTP 401: key rejected")})
    sp.poll(cfg_d, NOW)
    sp.poll(cfg_d, NOW + 600)
    assert len(calls) == 1
    assert sp.load_state()["kimi"]["error"] == "HTTP 401: key rejected"


def test_summary_counts_provider_money_exactly_and_never_double_counts(env, cfg, monkeypatch):
    cfg_d = conn(dict(cfg), openai={"api_key": "k"})
    state = {"openai": {"ok": NOW, "days": {TODAY: {"currency": "$", "cost_total": 1.25, "models": {
        "gpt-x": {"requests": 3, "in": 1000, "out": 200, "cache": 0, "cost": None}}}}}}
    sp._save_state(state)
    monkeypatch.setattr(spend, "_last_rollup", NOW)          # skip the poll
    s = spend.summary(cfg_d, 7, now=NOW)
    src = [x for x in s["sources"] if x["name"] == "OpenAI"][0]
    assert src["cost"] == 1.25 and src["origin"] == "Usage report" and src["tokens"] == 1200
    assert s["month"]["cost"] >= 1.25
    assert "OpenAI" not in s["prices"]


def test_research_on_a_connected_provider_host_is_skipped(env, cfg):
    cfg_d = conn(dict(cfg), deepseek={"api_key": "k"})
    cfg_d["research"] = {"data_dir": cfg["research"]["data_dir"], "models": [
        {"name": "ds", "base_url": "https://api.deepseek.com/v1", "price_in": 1, "price_out": 2}]}
    write_research(env, "r1", TODAY, "ds", 1000, 100)
    spend.rollup(cfg_d, now=NOW, force=True)
    assert "ds" not in ledger(env, TODAY)


@pytest.mark.asyncio
async def test_provider_routes_test_before_saving_and_hide_keys(client, remote, env, monkeypatch):
    fake_http(monkeypatch, {"user/balance": {"balance_infos": [{"currency": "USD", "total_balance": "9.00"}]}})
    r = await remote.put("/api/spend/providers/deepseek", json={"api_key": "sk-secret-123"})
    assert r.status_code == 403
    r = await client.put("/api/spend/providers/deepseek", json={"api_key": "sk-secret-123"})
    assert r.status_code == 200
    assert "sk-secret-123" not in r.text
    assert "sk-secret-123" not in (env / "config.local.json").read_text()      # stored encrypted
    row = [p for p in r.json()["providers"] if p["id"] == "deepseek"][0]
    assert row["connected"] and row["balance"]["amount"] == 9.0
    r = await client.put("/api/spend/providers/nope", json={"api_key": "x"})
    assert r.status_code == 400
    r = await client.put("/api/spend/providers/xai", json={"api_key": "x"})
    assert r.status_code == 400 and "team id" in r.json()["error"]
    r = await client.delete("/api/spend/providers/deepseek")
    assert [p for p in r.json()["providers"] if p["id"] == "deepseek"][0]["connected"] is False
    assert "deepseek" not in sp.load_state()


@pytest.mark.asyncio
async def test_bad_key_is_not_saved(client, env, monkeypatch):
    fake_http(monkeypatch, {"user/balance": sp.ProviderError("HTTP 401: key rejected")})
    r = await client.put("/api/spend/providers/deepseek", json={"api_key": "sk-bad"})
    assert r.status_code == 400 and "not saved" in r.json()["error"]
    assert not (env / "config.local.json").exists() or "sk-bad" not in (env / "config.local.json").read_text()
