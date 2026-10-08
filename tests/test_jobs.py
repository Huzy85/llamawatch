"""Jobs: schedule words, cron next-run, systemd show parsing, state rules, endpoint gating."""
import json
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from llamawatch import jobs
from llamawatch.routes import jobs as jobs_routes

FIXTURE = Path(__file__).parent / "ui" / "fixtures" / "snapshot.json"


# ── schedule words ───────────────────────────────────────────────────
@pytest.mark.parametrize("cal,mono,words", [
    ("{ OnCalendar=Mon *-*-* 02:40:00 ; next_elapse=Mon 2026-10-12 02:40:00 BST }", None, "Mon 02:40"),
    ("{ OnCalendar=*-*-* 06:30:00 ; next_elapse=n/a }", None, "daily 06:30"),
    ("{ OnCalendar=*-*-* 07:15:00 Europe/London ; next_elapse=n/a }", None, "daily 07:15 (Europe/London)"),
    ("{ OnCalendar=*-*-* *:00/5:00 ; next_elapse=n/a }", None, "every 5 min"),
    ("{ OnCalendar=*:0/15 ; next_elapse=n/a }", None, "every 15 min"),
    ("{ OnCalendar=*-*-* *:05,35:00 ; next_elapse=n/a }", None, "hourly at :05 and :35"),
    ("{ OnCalendar=*-*-01 04:00:00 ; next_elapse=n/a }", None, "monthly on the 1st at 04:00"),
    ("{ OnCalendar=daily ; next_elapse=n/a }", None, "daily at 00:00"),
    (None, "{ OnUnitActiveUSec=5min ; next_elapse=n/a }", "every 5 min after the last run"),
    (None, ["{ OnUnitActiveUSec=15min ; next_elapse=n/a }", "{ OnBootUSec=2min ; next_elapse=n/a }"],
     "every 15 min after the last run; 2 min after boot"),
    (None, "{ OnUnitInactiveUSec=3h ; next_elapse=n/a }", "3 h after the last run ends"),
    (None, "{ OnUnitActiveUSec=1d ; next_elapse=n/a }", "every 1 day after the last run"),
    ("{ OnCalendar=Wed *-*-01..07 02:00:00 ; next_elapse=n/a }", None, "Wed *-*-01..07 02:00:00"),  # odd shapes stay raw
    (None, None, "no schedule"),
])
def test_schedule_words(cal, mono, words):
    assert jobs.schedule_words(cal, mono) == words


def test_systemd_time_parses_local_stamp():
    t = jobs.systemd_time("Tue 2026-10-06 10:14:18 BST")
    assert datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S") == "2026-10-06 10:14:18"
    assert jobs.systemd_time("n/a") is None and jobs.systemd_time("") is None


# ── show parsing ─────────────────────────────────────────────────────
SHOW = """Id=a.timer
Description=A
ActiveState=active
TimersMonotonic={ OnUnitActiveUSec=5min ; next_elapse=n/a }
TimersMonotonic={ OnBootUSec=1min ; next_elapse=n/a }

Id=b.timer
Description=B
ActiveState=inactive
UnitFileState=disabled
"""


def test_parse_show_blocks_and_repeats():
    u = jobs.parse_show(SHOW)
    assert set(u) == {"a.timer", "b.timer"}
    assert isinstance(u["a.timer"]["TimersMonotonic"], list) and len(u["a.timer"]["TimersMonotonic"]) == 2
    assert u["b.timer"]["UnitFileState"] == "disabled"


# ── state rules (through _systemd_jobs with subprocess stubbed) ─────
def _fake_systemd(monkeypatch, timers, tprops, sprops):
    def run(cmd, timeout=8):
        if "list-timers" in cmd:
            return json.dumps(timers)
        if "show" in cmd and cmd[-1] == jobs._TIMER_PROPS:
            return tprops
        if "show" in cmd:
            return sprops
        return ""
    monkeypatch.setattr(jobs, "_run", run)


def test_states_from_timer_and_service(monkeypatch):
    timers = [{"unit": "ok-job.timer", "activates": "ok-job.service", "next": 1_800_000_000_000_000, "left": 1, "last": 0, "passed": 0},
              {"unit": "bad-job.timer", "activates": "bad-job.service", "next": 1_800_000_000_000_000, "left": 1, "last": 0, "passed": 0},
              {"unit": "busy.timer", "activates": "busy.service", "next": 0, "left": 0, "last": 0, "passed": 0},
              {"unit": "napping.timer", "activates": "napping.service", "next": 0, "left": 0, "last": 0, "passed": 0},
              {"unit": "dead.timer", "activates": "dead.service", "next": 0, "left": 0, "last": 0, "passed": 0}]
    tprops = "\n\n".join([
        "Id=ok-job.timer\nDescription=Fine\nActiveState=active\nUnitFileState=enabled\nTimersCalendar={ OnCalendar=*-*-* 02:00:00 ; next_elapse=n/a }",
        "Id=bad-job.timer\nActiveState=active\nUnitFileState=enabled",
        "Id=busy.timer\nActiveState=active\nUnitFileState=enabled",
        "Id=napping.timer\nActiveState=inactive\nUnitFileState=enabled",
        "Id=dead.timer\nActiveState=inactive\nUnitFileState=disabled"])
    sprops = "\n\n".join([
        "Id=ok-job.service\nActiveState=inactive\nResult=success\nExecMainStatus=0\nExecMainStartTimestamp=Tue 2026-10-06 02:00:00 BST\nExecMainExitTimestamp=Tue 2026-10-06 02:00:09 BST",
        "Id=bad-job.service\nDescription=From the service\nActiveState=failed\nResult=exit-code\nExecMainStatus=1\nExecMainStartTimestamp=Tue 2026-10-06 02:00:00 BST\nExecMainExitTimestamp=Tue 2026-10-06 02:00:01 BST",
        "Id=busy.service\nActiveState=active\nExecMainStartTimestamp=Tue 2026-10-06 02:00:00 BST",
        "Id=napping.service\nActiveState=inactive\nResult=success\nExecMainStatus=0",
        "Id=dead.service\nActiveState=inactive"])
    _fake_systemd(monkeypatch, timers, tprops, sprops)
    by = {j["name"]: j for j in jobs._systemd_jobs(False)}
    assert by["ok-job"]["state"] == "ok" and by["ok-job"]["last"]["ok"] and by["ok-job"]["schedule"] == "daily 02:00"
    assert by["ok-job"]["next"] == 1_800_000_000.0 and by["ok-job"]["can_act"] and by["ok-job"]["kind"] == "user"
    assert by["bad-job"]["state"] == "failed" and by["bad-job"]["last"]["detail"] == "exit code 1"
    assert by["bad-job"]["description"] == "From the service"   # falls back to the service's
    assert by["busy"]["state"] == "running"
    assert by["napping"]["state"] == "paused" and by["napping"]["next"] is None
    assert by["dead"]["state"] == "off"
    assert all(not j["can_act"] for j in jobs._systemd_jobs(True))


def test_unsafe_unit_names_are_skipped(monkeypatch):
    _fake_systemd(monkeypatch, [{"unit": "bad name;rm.timer", "activates": "x.service", "next": 0}], "", "")
    assert jobs._systemd_jobs(False) == []


# ── cron ─────────────────────────────────────────────────────────────
NOON = datetime(2026, 1, 1, 12, 0, 0).timestamp()   # a Thursday


@pytest.mark.parametrize("expr,expect", [
    ("*/15 * * * *", "2026-01-01 12:15"),
    ("0 2 * * *", "2026-01-02 02:00"),
    ("0 2 * * 0", "2026-01-04 02:00"),          # next Sunday
    ("0 2 * * 7", "2026-01-04 02:00"),          # 7 is Sunday too
    ("30 6 1 * *", "2026-02-01 06:30"),         # 1st of the month, already past today
    ("0 9 * * mon-fri", "2026-01-02 09:00"),
    ("0 12 * * *", "2026-01-02 12:00"),         # exactly now counts as passed
    ("@daily", "2026-01-02 00:00"),
    ("@hourly", "2026-01-01 13:00"),
    ("0 0 15 * 1", "2026-01-05 00:00"),         # both set: dom 15 OR Monday, Monday comes first
])
def test_cron_next(expr, expect):
    spec = jobs.parse_cron(expr)
    assert spec is not None
    assert datetime.fromtimestamp(jobs.cron_next(spec, NOON)).strftime("%Y-%m-%d %H:%M") == expect


@pytest.mark.parametrize("expr", ["", "* * *", "60 * * * *", "a b c d e", "*/0 * * * *", "0 25 * * *"])
def test_cron_rejects_bad_lines(expr):
    assert jobs.parse_cron(expr) is None


def test_cron_never_in_a_year():
    assert jobs.cron_next(jobs.parse_cron("0 0 31 2 *"), NOON) is None


@pytest.mark.parametrize("expr,words", [
    ("*/5 * * * *", "every 5 min"), ("0 */2 * * *", "every 2 h at :00"), ("30 2 * * *", "daily 02:30"),
    ("0 2 * * 0", "Sun 02:00"), ("5 * * * *", "every hour at :05"), ("0 0 1 * *", "0 0 1 * *"), ("@weekly", "Sun 00:00"),
])
def test_cron_words(expr, words):
    assert jobs.cron_words(expr) == words


def test_parse_crontab_skips_noise_and_names_jobs():
    text = """# a comment
MAILTO=""
SHELL=/bin/bash

*/15 * * * * /home/demo/bin/check-mail.sh >> /home/demo/logs/mail.log 2>&1
0 23 * * * docker exec assistant python3 -m assistant.cron
@daily nice -n 10 /usr/local/bin/tidy
this line is rubbish
"""
    out = jobs.parse_crontab(text, NOON)
    assert [j["name"] for j in out] == ["check-mail.sh", "assistant", "tidy"]
    assert out[0]["id"] == "cron:1" and out[0]["kind"] == "cron" and out[0]["last"] is None
    assert out[0]["state"] == "unknown" and not out[0]["can_act"] and out[0]["schedule"] == "every 15 min"
    assert out[2]["schedule"] == "daily 00:00"


# ── research ─────────────────────────────────────────────────────────
def test_research_running_runs_become_jobs(tmp_path):
    (tmp_path / "r1").mkdir(); (tmp_path / "r2").mkdir()
    (tmp_path / "r1" / "run.json").write_text(json.dumps({"question": "Which kettle?", "status": "running", "model": "demo", "depth": "quick", "started": NOON}))
    (tmp_path / "r2" / "run.json").write_text(json.dumps({"question": "Done one", "status": "done", "model": "demo"}))
    out = jobs._research_jobs({"research": {"data_dir": str(tmp_path)}})
    assert len(out) == 1 and out[0]["id"] == "research:r1" and out[0]["state"] == "running"
    assert out[0]["name"] == "Research: Which kettle?" and out[0]["href"] == "/research#run/r1" and not out[0]["can_act"]
    assert jobs._research_jobs({"research": {"data_dir": str(tmp_path / "nope")}}) == []


# ── collect: order, counts, cache ───────────────────────────────────
def test_collect_orders_and_counts(monkeypatch):
    monkeypatch.setattr(jobs, "_systemd_jobs", lambda system: [] if system else [
        {"id": "user:a", "kind": "user", "name": "a", "state": "ok", "next": 50, "last": None, "schedule": "", "description": "", "can_act": True, "units": {}},
        {"id": "user:b", "kind": "user", "name": "b", "state": "failed", "next": 90, "last": None, "schedule": "", "description": "", "can_act": True, "units": {}},
        {"id": "user:c", "kind": "user", "name": "c", "state": "paused", "next": None, "last": None, "schedule": "", "description": "", "can_act": True, "units": {}}])
    monkeypatch.setattr(jobs, "_cron_jobs", lambda now: [])
    monkeypatch.setattr(jobs, "_research_jobs", lambda cfg: [
        {"id": "research:r", "kind": "research", "name": "Research: x", "state": "running", "next": None, "last": None, "schedule": "", "description": "", "can_act": False, "units": {}}])
    d = jobs.collect(fresh=True)
    assert [j["id"] for j in d["jobs"]] == ["research:r", "user:b", "user:a", "user:c"]
    assert d["counts"] == {"total": 4, "running": 1, "failed": 1, "ok": 1, "paused": 1}
    monkeypatch.setattr(jobs, "_systemd_jobs", lambda system: [])
    assert len(jobs.collect()["jobs"]) == 4          # cached
    assert [j["id"] for j in jobs.collect(fresh=True)["jobs"]] == ["research:r"]


# ── endpoints ────────────────────────────────────────────────────────
SAMPLE_JOBS = [
    {"id": "user:photo-sync", "kind": "user", "name": "photo-sync", "state": "failed", "next": 1, "last": None, "schedule": "", "description": "", "can_act": True,
     "units": {"timer": "photo-sync.timer", "service": "photo-sync.service"}},
    {"id": "system:fstrim", "kind": "system", "name": "fstrim", "state": "ok", "next": 1, "last": None, "schedule": "", "description": "", "can_act": False,
     "units": {"timer": "fstrim.timer", "service": "fstrim.service"}},
]


@pytest.fixture
def client(monkeypatch, tmp_path):
    import llamawatch.config as config_mod
    import llamawatch.server as srv
    from llamawatch.server import app
    (tmp_path / "config.json").write_text(json.dumps({"auth_enabled": False, "auth_password_hash": "", "backends": [], "services": []}))
    config_mod.reset_config()
    srv._config = config_mod.load_config(config_dir=tmp_path)
    monkeypatch.setattr(jobs, "collect", lambda config=None, fresh=False: {"jobs": SAMPLE_JOBS, "counts": {"total": 2}, "at": 0})
    calls, log = [], []
    monkeypatch.setattr(jobs_routes.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    monkeypatch.setattr(jobs_routes.audit, "append", lambda *a, **k: log.append((a, k)))
    monkeypatch.setattr(jobs_routes.security, "action_allowed", lambda req, auth: req.headers.get("x-test-allow") == "1")
    monkeypatch.setattr(jobs_routes, "is_auth_enabled", lambda: False)
    c = TestClient(app, raise_server_exceptions=True)
    c.calls, c.log = calls, log
    yield c
    config_mod.reset_config()


def test_list_endpoint(client):
    r = client.get("/api/jobs")
    assert r.status_code == 200 and r.json()["counts"]["total"] == 2


def test_action_blocked_when_not_allowed(client):
    r = client.post("/api/jobs/user:photo-sync/run")
    assert r.status_code == 403 and "not permitted" in r.json()["error"]
    assert client.calls == [] and client.log == []


def test_run_pause_resume_call_systemctl_and_audit(client):
    h = {"x-test-allow": "1"}
    assert client.post("/api/jobs/user:photo-sync/run", headers=h).json()["message"] == "photo-sync started"
    assert client.post("/api/jobs/user:photo-sync/pause", headers=h).status_code == 200
    assert client.post("/api/jobs/user:photo-sync/resume", headers=h).status_code == 200
    assert client.calls == [["systemctl", "--user", "start", "photo-sync.service"],
                            ["systemctl", "--user", "stop", "photo-sync.timer"],
                            ["systemctl", "--user", "start", "photo-sync.timer"]]
    assert [a[0] for a, k in client.log] == ["job_run", "job_pause", "job_resume"]
    assert all(k["outcome"] == "ok" and k["actor"] == "local" and k["target"] == "photo-sync" for a, k in client.log)


def test_read_only_and_unknown(client):
    h = {"x-test-allow": "1"}
    assert client.post("/api/jobs/system:fstrim/run", headers=h).status_code == 400
    assert client.post("/api/jobs/user:nope/run", headers=h).status_code == 404
    assert client.post("/api/jobs/user:photo-sync/explode", headers=h).status_code == 400
    assert client.calls == []


def test_failed_systemctl_is_reported_and_audited(client, monkeypatch):
    monkeypatch.setattr(jobs_routes.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "Unit not found."))
    r = client.post("/api/jobs/user:photo-sync/run", headers={"x-test-allow": "1"})
    assert r.status_code == 500 and r.json()["error"] == "Unit not found."
    assert client.log[-1][1]["outcome"] == "fail"


def test_log_endpoint(client, monkeypatch):
    monkeypatch.setattr(jobs, "journal", lambda job, lines=30: [f"line for {job['name']}"])
    assert client.get("/api/jobs/user:photo-sync/log").json() == {"lines": ["line for photo-sync"]}
    assert client.get("/api/jobs/user:nope/log").status_code == 404


def test_jobs_page_served(client):
    r = client.get("/jobs")
    assert r.status_code == 200 and "Scheduled jobs" in r.text and r.headers["cache-control"] == "no-cache"


# ── sample fixture shape ────────────────────────────────────────────
def test_sample_fixture_has_jobs_in_shape():
    d = json.loads(FIXTURE.read_text())["rest"]["/api/jobs"]
    keys = {"id", "kind", "name", "description", "schedule", "next", "last", "state", "can_act", "units"}
    assert d["jobs"] and all(keys <= set(j) for j in d["jobs"])
    assert {j["kind"] for j in d["jobs"]} == {"user", "system", "cron", "research"}
    assert {j["state"] for j in d["jobs"]} >= {"running", "failed", "ok", "paused", "unknown"}
    assert d["counts"]["total"] == len(d["jobs"])
    assert all(j["can_act"] == (j["kind"] == "user") for j in d["jobs"])
    # nothing personal: invented hosts only
    text = json.dumps(d)
    assert "steamvibe" not in text and "192.168." not in text


# ── add / edit / delete ─────────────────────────────────────────────
ANALYZE = ("  Original form: {expr}\nNormalized form: {norm}\n    Next elapse: Thu 2026-10-08 06:30:00 BST\n"
           "       (in UTC): Thu 2026-10-08 05:30:00 UTC\n       From now: 10h left\n")


def _fake_sh(monkeypatch, fail=(), loaded=(), no_analyze=False):
    """Stand in for systemctl / loginctl / systemd-analyze. Records every call."""
    calls = []

    def sh(cmd, timeout=20):
        calls.append(cmd)
        if cmd[0] == "systemd-analyze":
            if no_analyze:
                return None
            expr = cmd[2]
            if "garbage" in expr:
                return subprocess.CompletedProcess(cmd, 1, "", f"Failed to parse calendar specification '{expr}': Invalid argument\n")
            return subprocess.CompletedProcess(cmd, 0, ANALYZE.format(expr=expr, norm=expr if " " in expr else f"*-*-* {expr}"), "")
        if cmd[0] == "loginctl":
            return subprocess.CompletedProcess(cmd, 0, "yes\n", "")
        if "show" in cmd and "LoadState" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "loaded\n" if cmd[3] in loaded else "not-found\n", "")
        if any(f in cmd for f in fail):
            return subprocess.CompletedProcess(cmd, 1, "", "Failed to enable unit: boom\n")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(jobs, "_sh", sh)
    return calls


@pytest.fixture
def unit_dirs(monkeypatch, tmp_path):
    monkeypatch.setattr(jobs, "UNIT_DIR", tmp_path / "systemd" / "user")
    monkeypatch.setattr(jobs, "SCRIPT_DIR", tmp_path / "llamawatch" / "jobs")
    return tmp_path


@pytest.mark.parametrize("text,expr", [
    ("every 15 min", "*:0/15"), ("every 1 minute", "*:0/1"), ("Every 30 mins", "*:0/30"),
    ("hourly", "hourly"), ("every hour", "hourly"), ("every 1 hour", "hourly"), ("every 6 hours", "0/6:00"), ("every 6h", "0/6:00"),
    ("daily", "daily"), ("daily 06:30", "*-*-* 06:30:00"), ("every day at 6:30pm", "*-*-* 18:30:00"), ("daily at 12am", "*-*-* 00:00:00"),
    ("06:30", "*-*-* 06:30:00"), ("6:05", "*-*-* 06:05:00"), ("9pm", "*-*-* 21:00:00"),
    ("Mon 02:00", "Mon *-*-* 02:00:00"), ("every monday at 2:00", "Mon *-*-* 02:00:00"), ("mondays 02:00", "Mon *-*-* 02:00:00"),
    ("mon,wed,fri 09:00", "Mon,Wed,Fri *-*-* 09:00:00"), ("Tue, Thu at 7:15", "Tue,Thu *-*-* 07:15:00"), ("weekly", "weekly"),
    ("monthly on the 1st at 04:00", "*-*-01 04:00:00"), ("monthly 15th 03:30", "*-*-15 03:30:00"), ("monthly", "monthly"),
    ("*-*-* 09,12,15:00:00", "*-*-* 09,12,15:00:00"),   # systemd expressions pass through untouched
    ("  daily   06:30  ", "*-*-* 06:30:00"),
])
def test_schedule_from_words(text, expr):
    assert jobs.schedule_from_words(text) == expr


@pytest.mark.parametrize("text,hint", [
    ("", "schedule is needed"), ("every 90 min", "between 1 and 59"), ("every 0 min", "between 1 and 59"),
    ("every 24 hours", "between 1 and 23"), ("25:00", "does not exist"), ("daily 10:75", "does not exist"),
    ("monthly on the 31st at 01:00", "between 1 and 28"),
])
def test_schedule_from_words_rejects(text, hint):
    with pytest.raises(jobs.JobError) as e:
        jobs.schedule_from_words(text)
    assert hint in str(e.value) and e.value.status == 400


def test_calendar_words_every_n_hours():
    assert jobs.schedule_words("{ OnCalendar=*-*-* 00/6:00:00 ; next_elapse=n/a }", None) == "every 6 h"
    assert jobs._calendar_core("0/3:00") == "every 3 h"


def test_check_calendar_uses_systemd_analyze(monkeypatch):
    calls = _fake_sh(monkeypatch)
    r = jobs.check_calendar("06:30")
    assert r == {"calendar": "*-*-* 06:30", "words": "daily 06:30", "next": datetime(2026, 10, 8, 6, 30).timestamp()}
    assert calls[0][:2] == ["systemd-analyze", "calendar"]
    with pytest.raises(jobs.JobError) as e:
        jobs.check_calendar("garbage here")
    assert "does not understand" in str(e.value) and "Invalid argument" in str(e.value)


def test_check_calendar_without_analyze_trusts_known_shapes_only(monkeypatch):
    _fake_sh(monkeypatch, no_analyze=True)
    assert jobs.check_calendar("*-*-* 06:30:00")["words"] == "daily 06:30"
    with pytest.raises(jobs.JobError):
        jobs.check_calendar("Wed *-*-01..07 02:00:00")


def test_preview_joins_words_and_check(monkeypatch):
    _fake_sh(monkeypatch)
    assert jobs.preview("daily 06:30")["words"] == "daily 06:30"


def test_setup_reads_systemd_and_linger(monkeypatch):
    _fake_sh(monkeypatch)
    assert jobs.setup() == {"available": True, "linger": True, "reason": ""}
    monkeypatch.setattr(jobs, "_sh", lambda cmd, timeout=20: None)
    s = jobs.setup()
    assert s["available"] is False and s["linger"] is None and "not available" in s["reason"]
    monkeypatch.setattr(jobs, "_sh", lambda cmd, timeout=20: subprocess.CompletedProcess(cmd, 1, "", "Failed to connect to bus"))
    assert "not reachable" in jobs.setup()["reason"]


def test_render_units_carry_marker_and_script(unit_dirs):
    v = {"name": "demo-job", "description": "Demo", "command": "echo hi\necho there", "calendar": "*-*-* 06:30:00"}
    timer, service, sh = jobs.render_units(v)
    assert timer.startswith(jobs.MARKER + "\n[Unit]") and "OnCalendar=*-*-* 06:30:00" in timer and "Persistent=true" in timer
    assert "WantedBy=timers.target" in timer and "Description=Demo" in timer
    assert service.startswith(jobs.MARKER) and "Type=oneshot" in service
    assert f"ExecStart=/bin/sh {jobs.SCRIPT_DIR / 'demo-job.sh'}" in service and f"WorkingDirectory={Path.home()}" in service
    assert sh.startswith("#!/bin/sh\n") and sh.endswith("set -e\n" + jobs._SCRIPT_SEP + "\necho hi\necho there\n")


def test_create_job_writes_files_and_enables(unit_dirs, monkeypatch):
    calls = _fake_sh(monkeypatch)
    made = jobs.create_job({"name": "Demo-Job", "description": "  Demo   thing ", "command": "echo hi", "schedule": "daily 06:30"})
    assert made["id"] == "user:demo-job" and made["schedule"] == "daily 06:30" and made["next"]
    t = jobs.UNIT_DIR / "demo-job.timer"
    assert t.read_text().splitlines()[0] == jobs.MARKER and "Description=Demo thing" in t.read_text()
    assert (jobs.UNIT_DIR / "demo-job.service").exists()
    script = jobs.SCRIPT_DIR / "demo-job.sh"
    assert script.read_text().endswith(jobs._SCRIPT_SEP + "\necho hi\n") and (script.stat().st_mode & 0o777) == 0o700
    ctl = [c for c in calls if c[0] == "systemctl" and c[2] not in ("show",)]
    assert ctl == [["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", "--now", "demo-job.timer"]]
    assert jobs.is_managed(str(t)) and not jobs.is_managed(str(unit_dirs / "elsewhere.timer")) and not jobs.is_managed(None)
    assert jobs.read_spec("demo-job") == {"name": "demo-job", "description": "Demo thing", "command": "echo hi", "schedule": "*-*-* 06:30:00"}


def test_create_job_rolls_back_when_enable_fails(unit_dirs, monkeypatch):
    calls = _fake_sh(monkeypatch, fail=("enable",))
    with pytest.raises(jobs.JobError) as e:
        jobs.create_job({"name": "demo-job", "command": "echo hi", "schedule": "hourly"})
    assert e.value.status == 500 and "boom" in str(e.value)
    assert not (jobs.UNIT_DIR / "demo-job.timer").exists() and not (jobs.SCRIPT_DIR / "demo-job.sh").exists()
    assert calls[-1] == ["systemctl", "--user", "daemon-reload"]


@pytest.mark.parametrize("fields,hint,status", [
    ({"name": "Bad Name!", "command": "x", "schedule": "hourly"}, "lowercase letters", 400),
    ({"name": "", "command": "x", "schedule": "hourly"}, "lowercase letters", 400),
    ({"name": "a" * 41, "command": "x", "schedule": "hourly"}, "lowercase letters", 400),
    ({"name": "ok", "command": "  ", "schedule": "hourly"}, "command is needed", 400),
    ({"name": "ok", "command": "x" * 4001, "schedule": "hourly"}, "too long", 400),
    ({"name": "ok", "command": "x", "schedule": "garbage here"}, "does not understand", 400),
    ({"name": "ok", "command": "x", "schedule": ""}, "schedule is needed", 400),
])
def test_create_job_rejects_bad_input(unit_dirs, monkeypatch, fields, hint, status):
    calls = _fake_sh(monkeypatch)
    with pytest.raises(jobs.JobError) as e:
        jobs.create_job(fields)
    assert hint in str(e.value) and e.value.status == status
    assert not any("daemon-reload" in c for c in calls) and not list(jobs.UNIT_DIR.glob("*")) if jobs.UNIT_DIR.exists() else True


def test_create_job_refuses_duplicates(unit_dirs, monkeypatch):
    _fake_sh(monkeypatch, loaded=("taken.timer",))
    with pytest.raises(jobs.JobError) as e:
        jobs.create_job({"name": "taken", "command": "x", "schedule": "hourly"})
    assert e.value.status == 409 and "already exists on this system" in str(e.value)
    jobs.UNIT_DIR.mkdir(parents=True)
    (jobs.UNIT_DIR / "mine.timer").write_text("[Timer]\n")   # someone else's file, no marker
    with pytest.raises(jobs.JobError) as e:
        jobs.create_job({"name": "mine", "command": "x", "schedule": "hourly"})
    assert e.value.status == 409


def test_update_and_delete_only_touch_managed_jobs(unit_dirs, monkeypatch):
    calls = _fake_sh(monkeypatch)
    jobs.create_job({"name": "demo-job", "command": "echo hi", "schedule": "hourly"})
    jobs.UNIT_DIR.joinpath("theirs.timer").write_text("[Unit]\nDescription=not ours\n")
    with pytest.raises(jobs.JobError) as e:
        jobs.update_job("theirs", {"command": "x", "schedule": "hourly"})
    assert "only jobs created from this page" in str(e.value)
    with pytest.raises(jobs.JobError):
        jobs.delete_job("theirs")
    with pytest.raises(jobs.JobError):
        jobs.delete_job("../../etc/passwd")
    assert (jobs.UNIT_DIR / "theirs.timer").exists()
    del calls[:]
    made = jobs.update_job("demo-job", {"name": "ignored", "description": "New words", "command": "echo bye", "schedule": "daily 06:30"})
    assert made["id"] == "user:demo-job" and made["schedule"] == "daily 06:30"
    assert "echo bye" in (jobs.SCRIPT_DIR / "demo-job.sh").read_text() and "Description=New words" in (jobs.UNIT_DIR / "demo-job.timer").read_text()
    ctl = [c for c in calls if c[0] == "systemctl"]
    assert ctl == [["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "restart", "demo-job.timer"]]
    del calls[:]
    assert jobs.delete_job("demo-job") == {"id": "user:demo-job", "name": "demo-job"}
    assert not (jobs.UNIT_DIR / "demo-job.timer").exists() and not (jobs.UNIT_DIR / "demo-job.service").exists() and not (jobs.SCRIPT_DIR / "demo-job.sh").exists()
    verbs = [c[2] for c in calls if c[0] == "systemctl"]
    assert verbs == ["disable", "stop", "daemon-reload", "reset-failed"]


def test_systemd_jobs_flag_managed_units(unit_dirs, monkeypatch):
    jobs.UNIT_DIR.mkdir(parents=True); jobs.SCRIPT_DIR.mkdir(parents=True)
    (jobs.UNIT_DIR / "ours.timer").write_text(jobs.MARKER + "\n[Unit]\nDescription=Ours\n\n[Timer]\nOnCalendar=*-*-* 02:00:00\n")
    (jobs.SCRIPT_DIR / "ours.sh").write_text("#!/bin/sh\n# marker\nset -e\n" + jobs._SCRIPT_SEP + "\necho hi\n")
    (jobs.UNIT_DIR / "theirs.timer").write_text("[Unit]\nDescription=Theirs\n")
    timers = [{"unit": "ours.timer", "activates": "ours.service", "next": 0}, {"unit": "theirs.timer", "activates": "theirs.service", "next": 0}]
    tprops = "\n\n".join([f"Id=ours.timer\nActiveState=active\nUnitFileState=enabled\nFragmentPath={jobs.UNIT_DIR / 'ours.timer'}",
                          f"Id=theirs.timer\nActiveState=active\nUnitFileState=enabled\nFragmentPath={jobs.UNIT_DIR / 'theirs.timer'}"])
    _fake_systemd(monkeypatch, timers, tprops, "Id=ours.service\nActiveState=inactive\n\nId=theirs.service\nActiveState=inactive")
    by = {j["name"]: j for j in jobs._systemd_jobs(False)}
    assert by["ours"]["managed"] and by["ours"]["spec"] == {"name": "ours", "description": "Ours", "command": "echo hi", "schedule": "*-*-* 02:00:00"}
    assert by["theirs"]["managed"] is False and "spec" not in by["theirs"]
    assert all(j["managed"] is False for j in jobs._systemd_jobs(True))


def test_collect_includes_setup(monkeypatch):
    monkeypatch.setattr(jobs, "_systemd_jobs", lambda system: [])
    monkeypatch.setattr(jobs, "_cron_jobs", lambda now: [])
    monkeypatch.setattr(jobs, "_research_jobs", lambda cfg: [])
    monkeypatch.setattr(jobs, "setup", lambda: {"available": False, "linger": None, "reason": "nope"})
    assert jobs.collect(fresh=True)["setup"]["reason"] == "nope"


# endpoints for the form
def test_preview_endpoint(client, monkeypatch):
    monkeypatch.setattr(jobs, "preview", lambda s: {"calendar": "*-*-* 06:30:00", "words": "daily 06:30", "next": 1.0})
    assert client.get("/api/jobs/preview?schedule=daily+06:30").json()["words"] == "daily 06:30"

    def boom(s):
        raise jobs.JobError("systemd does not understand that schedule")
    monkeypatch.setattr(jobs, "preview", boom)
    r = client.get("/api/jobs/preview?schedule=garbage")
    assert r.status_code == 400 and "does not understand" in r.json()["error"]


def test_create_update_delete_endpoints_gate_and_audit(client, monkeypatch):
    seen = []
    monkeypatch.setattr(jobs, "create_job", lambda f: seen.append(("create", f)) or {"id": "user:demo-job", "name": "demo-job", "schedule": "daily 06:30", "next": 1.0})
    monkeypatch.setattr(jobs, "update_job", lambda n, f: seen.append(("update", n, f)) or {"id": f"user:{n}", "name": n, "schedule": "hourly", "next": 2.0})
    monkeypatch.setattr(jobs, "delete_job", lambda n: seen.append(("delete", n)) or {"id": f"user:{n}", "name": n})
    body = {"name": "demo-job", "command": "echo hi", "schedule": "daily 06:30"}
    assert client.post("/api/jobs", json=body).status_code == 403
    assert client.put("/api/jobs/user:demo-job", json=body).status_code == 403
    assert client.delete("/api/jobs/user:demo-job").status_code == 403
    assert seen == [] and client.log == []
    h = {"x-test-allow": "1"}
    r = client.post("/api/jobs", json=body, headers=h)
    assert r.status_code == 201 and r.json()["message"] == "demo-job created, runs daily 06:30" and r.json()["id"] == "user:demo-job"
    r = client.put("/api/jobs/user:demo-job", json={"command": "echo bye", "schedule": "hourly"}, headers=h)
    assert r.status_code == 200 and r.json()["message"] == "demo-job saved, runs hourly"
    assert client.delete("/api/jobs/user:demo-job", headers=h).json()["message"] == "demo-job deleted"
    assert [s[0] for s in seen] == ["create", "update", "delete"] and seen[1][1] == "demo-job"
    assert [(a[0], k["target"], k["outcome"]) for a, k in client.log] == [("job_create", "demo-job", "ok"), ("job_update", "demo-job", "ok"), ("job_delete", "demo-job", "ok")]
    # not a user job, or a name the page could never have made
    assert client.put("/api/jobs/system:fstrim", json=body, headers=h).status_code == 400
    assert client.delete("/api/jobs/user:Bad%20Name", headers=h).status_code == 400
    assert len(seen) == 3


def test_create_endpoint_reports_job_errors(client, monkeypatch):
    def dup(f):
        raise jobs.JobError("a job called demo-job already exists", 409)
    monkeypatch.setattr(jobs, "create_job", dup)
    r = client.post("/api/jobs", json={"name": "demo-job"}, headers={"x-test-allow": "1"})
    assert r.status_code == 409 and "already exists" in r.json()["error"]
    assert client.log[-1][1]["outcome"] == "fail" and client.log[-1][1]["target"] == "demo-job"


def test_sample_fixture_has_setup_and_managed_jobs():
    rest = json.loads(FIXTURE.read_text())["rest"]
    d = rest["/api/jobs"]
    assert d["setup"] == {"available": True, "linger": True, "reason": ""}
    managed = [j for j in d["jobs"] if j.get("managed")]
    assert managed and all(j["kind"] == "user" and set(j["spec"]) == {"name", "description", "command", "schedule"} for j in managed)
    assert all("managed" in j for j in d["jobs"])
    assert set(rest["/api/jobs/preview"]) == {"calendar", "words", "next"}
