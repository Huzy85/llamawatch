"""Approvals: rules, the always-blocked list, the request store, the gate in front of
every machine action, replay on approve, agent requests with the token, terminal
tickets, settings and the notify hook."""

import asyncio
import json
import subprocess
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

import llamawatch.config as config_mod
from llamawatch import approvals, audit, jobs
from llamawatch.routes import actions as actions_routes
from llamawatch.routes import jobs as jobs_routes


# ── fixtures ─────────────────────────────────────────────────────────────────
@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Config in tmp, approvals store in tmp, audit log in tmp, auth off."""
    cfg = {"port": 8451, "host": "0.0.0.0", "auth_enabled": False, "auth_password_hash": "", "backends": [],
           "services": [{"name": "demo-svc", "type": "user", "unit": "demo.service"}], "model_names": {},
           "quick_actions": [{"id": "hello", "label": "Say hello", "shell": "echo hello"},
                             {"id": "wipe", "label": "Wipe", "shell": "rm -rf / --no-preserve-root"}]}
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    config_mod.reset_config()
    import llamawatch.server as srv
    srv._config = config_mod.load_config(config_dir=tmp_path)
    srv._adapters = MagicMock()
    srv._collector_registry = MagicMock()
    approvals.reset(tmp_path / "approvals.json")
    monkeypatch.setattr(audit, "_LOG_FILE", tmp_path / "audit.log")
    yield tmp_path
    approvals.reset(tmp_path / "gone.json")
    config_mod.reset_config()


@pytest.fixture()
def client(env):
    """Local client with no password: TestClient's host is not loopback, so make it one."""
    import llamawatch.server as srv
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url="http://testserver")


@pytest.fixture()
def remote(env):
    import llamawatch.server as srv
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app, client=("203.0.113.9", 4000)), base_url="http://testserver")


def events(action=None):
    ev = audit.read(limit=500)
    return [e for e in ev if action is None or e["action"] == action]


def set_rules(**kw):
    approvals.save({"rules": {**approvals.DEFAULT_RULES, **kw}})


# ── blocklist ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("cmd", [
    "rm -rf /", "sudo rm -rf /*", "rm -r ~", "rm -rf $HOME", "rm -rf /etc", "rm -Rf --no-preserve-root /x",
    "mkfs.ext4 /dev/sdb1", "wipefs -a /dev/nvme0n1", "dd if=/dev/zero of=/dev/sda bs=1M", "cat x > /dev/sdb",
    "shred /dev/nvme0n1", "shutdown -h now", "sudo reboot", "echo hi; poweroff", "systemctl reboot", "sudo systemctl --no-block poweroff",
    ":(){ :|:& };:", "chmod -R 777 /", "chown -R nobody /usr", "crontab -r", "curl -s https://example.com/x.sh | sh",
    "wget -qO- https://example.com/i | sudo bash", "kill -9 -1", "init 0",
])
def test_builtin_blocks_catch(cmd, env):
    assert approvals.check_command(cmd) is not None, cmd


@pytest.mark.parametrize("cmd", [
    "rm -rf ./build", "rm -rf /srv/demo/tmp/cache", "rm -r build/ dist/", "dd if=a.img of=b.img", "echo reboot",
    "echo 'rm -rf /' > notes.txt", "systemctl --user restart demo.service", "curl -s https://example.com/api | jq .",
    "mkfs_notes.txt", "crontab -l", "kill -9 1234", "chmod -R 755 /srv/demo/www", "rsync -a /srv/demo/ nas.example.com:/backup/",
    "shutdown_report.sh", "git push", "",
])
def test_builtin_blocks_let_normal_commands_through(cmd, env):
    assert approvals.check_command(cmd) is None, cmd


def test_owner_patterns_regex_or_substring(env):
    assert approvals.check_command("docker system prune -af", ["docker\\s+system\\s+prune"]) .startswith("matches your blocked pattern")
    assert approvals.check_command("git push --force", ["push --force"]) is not None
    assert approvals.check_command("git push", ["push --force"]) is None
    assert approvals.check_command("echo x", ["([unclosed"]) is None          # bad regex falls back to substring
    assert approvals.check_command("echo ([unclosed", ["([unclosed"]) is not None
    approvals.save({"block_patterns": ["drop table"]})
    assert approvals.check_command("psql -c 'DROP TABLE users'") is not None   # case-insensitive, read from config


# ── rules and settings ───────────────────────────────────────────────────────
def test_default_rules_allow_everything_but_agents(env):
    r = approvals.rules()
    assert r["agent"] == "ask" and all(v == "allow" for k, v in r.items() if k != "agent")
    assert approvals.ttl() == approvals.DEFAULT_TTL and approvals.token() == "" and approvals.notify_command() == ""


def test_save_merges_and_encrypts_token(env):
    approvals.save({"rules": {"service": "ask"}, "token": "lwa_secret", "ttl": 120})
    assert approvals.policy("service") == "ask" and approvals.policy("docker") == "allow"
    assert approvals.token() == "lwa_secret" and approvals.ttl() == 120
    raw = json.loads((env / "config.local.json").read_text())
    assert raw["approvals"]["token"].startswith("enc:") and "lwa_secret" not in (env / "config.local.json").read_text()
    approvals.save({"token": None})
    assert approvals.token() == "" and "token" not in json.loads((env / "config.local.json").read_text())["approvals"]
    assert approvals.ttl() == 120   # other keys kept


def test_bad_rule_values_ignored(env):
    approvals.save({"rules": {"service": "maybe", "nope": "ask"}, "ttl": "soon"})
    assert approvals.policy("service") == "allow" and approvals.ttl() == approvals.DEFAULT_TTL


# ── store ────────────────────────────────────────────────────────────────────
def test_store_lifecycle_and_persistence(env):
    s = approvals.store
    a = s.create("agent", "  Restart   the relay ", command="echo 1", requester="relay-bot")
    assert a["status"] == "pending" and a["title"] == "Restart the relay" and len(a["id"]) == 12
    assert [r["id"] for r in s.pending()] == [a["id"]]
    assert s.decide(a["id"], "approved") ["status"] == "approved"
    assert s.decide(a["id"], "denied") is None                   # already decided
    b = s.create("agent", "second")
    assert s.decide(b["id"], "denied")["status"] == "denied"
    assert [r["id"] for r in s.recent()] == [b["id"], a["id"]]  # newest first, no pending
    again = approvals.Store(s.path)
    assert again.get(a["id"])["status"] == "approved" and len(again._load()) == 2


def test_store_expiry_counts_as_denied(env, monkeypatch):
    s = approvals.store
    r = s.create("quick_action", "old one", ttl_s=60)
    monkeypatch.setattr(approvals.time, "time", lambda: r["created"] + 61)
    assert s.pending() == []
    assert s.get(r["id"])["status"] == "expired" and s.get(r["id"])["decided_by"] == "timeout"
    assert events("approval_expired") and events("approval_expired")[0]["id"] == r["id"]
    assert s.decide(r["id"], "approved") is None


def test_store_keeps_last_300(env):
    s = approvals.store
    for i in range(approvals.KEEP + 20):
        s.create("agent", f"n{i}", status="denied")
    assert len(json.loads(s.path.read_text())) == approvals.KEEP
    assert s.get("x") is None and s.recent(5)[0]["title"] == f"n{approvals.KEEP + 19}"


async def test_store_wait_wakes_on_decision(env):
    s = approvals.store
    r = s.create("agent", "waiting")

    async def decide_soon():
        await asyncio.sleep(0.05)
        s.decide(r["id"], "approved")

    t0 = asyncio.get_running_loop().time()
    got, _ = await asyncio.gather(s.wait(r["id"], 5), decide_soon())
    assert got["status"] == "approved" and asyncio.get_running_loop().time() - t0 < 2


async def test_store_wait_times_out_quietly(env):
    r = approvals.store.create("agent", "slow")
    got = await approvals.store.wait(r["id"], 0.05)
    assert got["status"] == "pending"


def test_consume_ticket_once(env, monkeypatch):
    s = approvals.store
    t = s.create("terminal", "Open a terminal", status="approved", decided_by="rule")
    assert s.consume(t["id"]) and not s.consume(t["id"])
    other = s.create("agent", "not a ticket", status="approved")
    assert not s.consume(other["id"]) and not s.consume("") and not s.consume("nope")
    stale = s.create("terminal", "stale", status="approved")
    monkeypatch.setattr(approvals.time, "time", lambda: stale["created"] + approvals.TICKET_LIFE + 1)
    assert not s.consume(stale["id"])


def test_public_hides_replay_body(env):
    r = approvals.store.create("service", "Restart demo", replay={"method": "POST", "path": "/api/services/demo-svc/restart", "body": "{\"x\":1}"})
    p = approvals.public(r)
    assert p["replay"] == {"method": "POST", "path": "/api/services/demo-svc/restart"} and "body" not in json.dumps(p)
    assert p["expires_in"] > 1700 and p["kind_label"] == "Services"


# ── the gate in front of actions ─────────────────────────────────────────────
async def test_actions_allow_by_default(client, monkeypatch):
    monkeypatch.setattr(actions_routes.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""))
    async with client as c:
        r = await c.post("/api/quick-action/hello")
        assert r.status_code == 200 and r.json()["stdout"].strip() == "hello"
        r = await c.post("/api/services/demo-svc/restart")
        assert r.status_code == 200
    assert approvals.store.pending() == [] and events("quick_action")[0]["actor"] == "local"


async def test_actions_ask_returns_202_and_runs_nothing(client, monkeypatch):
    set_rules(quick_action="ask", service="ask", docker="ask", job_run="ask")
    calls = []
    monkeypatch.setattr(actions_routes.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    monkeypatch.setattr(actions_routes.docker_collector, "docker_available", lambda: True)
    monkeypatch.setattr(actions_routes.docker_collector, "_docker_post", lambda path: calls.append(path))
    async with client as c:
        for path in ("/api/quick-action/hello", "/api/services/demo-svc/restart", "/api/docker/abc123/stop", "/api/timers/demo/trigger"):
            r = await c.post(path)
            assert r.status_code == 202 and r.json()["status"] == "pending" and "Approvals" in r.json()["message"], path
    assert calls == []
    pend = approvals.store.pending()
    assert len(pend) == 4 and {p["kind"] for p in pend} == {"quick_action", "service", "docker", "job_run"}
    assert pend[0]["replay"]["path"] == "/api/quick-action/hello" and pend[0]["title"] == "Quick action: Say hello"
    assert len(events("approval_requested")) == 4 and events("quick_action") == []


async def test_actions_block_returns_403(client, monkeypatch):
    set_rules(service="block")
    monkeypatch.setattr(actions_routes.subprocess, "run", lambda cmd, **kw: pytest.fail("ran"))
    async with client as c:
        r = await c.post("/api/services/demo-svc/restart")
    assert r.status_code == 403 and r.json()["status"] == "blocked" and "blocked by your Approvals rules" in r.json()["error"]
    assert events("approval_blocked")[0]["target"] == "demo-svc"


async def test_quick_action_blocklist_wins_even_under_allow(client):
    async with client as c:
        r = await c.post("/api/quick-action/wipe")
    assert r.status_code == 403 and "removes the root" in r.json()["error"]
    assert events("approval_blocked")[0]["kind"] == "quick_action" and approvals.store.pending() == []


async def test_approve_replays_once_and_records_result(client, monkeypatch):
    set_rules(quick_action="ask")
    async with client as c:
        rid = (await c.post("/api/quick-action/hello")).json()["id"]
        r = await c.post(f"/api/approvals/{rid}/approve")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["request"]["status"] == "done" and d["request"]["result"].strip() == "hello"
        assert "Approved: Quick action: Say hello" in d["message"]
        again = await c.post(f"/api/approvals/{rid}/approve")
        assert again.status_code == 409 and again.json()["status"] == "done"
    qa = events("quick_action")
    assert len(qa) == 1 and qa[0]["actor"] == "approved" and qa[0]["outcome"] == "ok"
    assert events("approval_granted")[0]["id"] == rid and approvals.store.pending() == []


async def test_approve_replay_failure_marks_failed(client, monkeypatch):
    set_rules(service="ask")
    monkeypatch.setattr(actions_routes.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "unit not found"))
    async with client as c:
        rid = (await c.post("/api/services/demo-svc/restart")).json()["id"]
        d = (await c.post(f"/api/approvals/{rid}/approve")).json()
    assert d["request"]["status"] == "failed" and "unit not found" in d["request"]["result"]


async def test_deny_and_bad_ids(client):
    set_rules(quick_action="ask")
    async with client as c:
        rid = (await c.post("/api/quick-action/hello")).json()["id"]
        r = await c.post(f"/api/approvals/{rid}/deny")
        assert r.status_code == 200 and r.json()["request"]["status"] == "denied"
        assert (await c.post(f"/api/approvals/{rid}/deny")).status_code == 409
        assert (await c.post("/api/approvals/nope/approve")).status_code == 404
        assert (await c.get("/api/approvals/nope")).status_code == 404
    assert events("quick_action") == [] and events("approval_denied")[0]["id"] == rid


async def test_replay_is_not_forgeable_from_the_network(remote):
    async with remote as c:
        r = await c.post("/api/quick-action/hello", headers={approvals.security.REPLAY_HEADER: "guess"})
    assert r.status_code == 403
    assert not approvals.security.is_replay(MagicMock(headers={approvals.security.REPLAY_HEADER: "guess"}))
    assert approvals.security.is_replay(MagicMock(headers={approvals.security.REPLAY_HEADER: approvals.security.replay_secret()}))


# ── jobs ─────────────────────────────────────────────────────────────────────
@pytest.fixture()
def jobs_client(env, monkeypatch):
    import llamawatch.server as srv
    sample = [{"id": "user:demo-job", "name": "demo-job", "kind": "user", "can_act": True,
               "units": {"timer": "demo-job.timer", "service": "demo-job.service"}}]
    monkeypatch.setattr(jobs, "collect", lambda config=None, fresh=False: {"jobs": sample, "counts": {"total": 1}, "at": 0})
    monkeypatch.setattr(jobs, "find", lambda job_id, cfg=None: next((j for j in sample if j["id"] == job_id), None))
    calls = []
    monkeypatch.setattr(jobs_routes.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    monkeypatch.setattr(jobs, "create_job", lambda fields: calls.append(("create", fields)) or {"name": fields["name"], "schedule": "daily"})
    monkeypatch.setattr(jobs, "update_job", lambda name, fields: calls.append(("update", name)) or {"name": name, "schedule": "daily"})
    monkeypatch.setattr(jobs, "delete_job", lambda name: calls.append(("delete", name)))
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url="http://testserver")
    c.calls = calls
    return c


async def test_jobs_run_and_change_kinds(jobs_client):
    set_rules(job_run="ask")
    async with jobs_client as c:
        assert (await c.post("/api/jobs/user:demo-job/run")).status_code == 202
        assert (await c.post("/api/jobs/user:demo-job/pause")).status_code == 200          # job_change still allow
        set_rules(job_run="allow", job_change="ask")
        assert (await c.post("/api/jobs/user:demo-job/run")).status_code == 200
        assert (await c.post("/api/jobs/user:demo-job/resume")).status_code == 202
        assert (await c.post("/api/jobs", json={"name": "x-job", "command": "echo hi", "schedule": "hourly"})).status_code == 202
        assert (await c.put("/api/jobs/user:demo-job", json={"command": "echo hi"})).status_code == 202
        assert (await c.delete("/api/jobs/user:demo-job")).status_code == 202
    kinds = sorted(p["kind"] for p in approvals.store.pending())
    assert kinds == ["job_change"] * 4 + ["job_run"]
    assert len(c.calls) == 2   # the pause and the allowed run


async def test_job_create_blocklist_and_replay_with_body(jobs_client):
    set_rules(job_change="ask")
    async with jobs_client as c:
        r = await c.post("/api/jobs", json={"name": "bad", "command": "rm -rf /", "schedule": "hourly"})
        assert r.status_code == 403 and "always" not in r.json()["error"] and "root" in r.json()["error"]
        rid = (await c.post("/api/jobs", json={"name": "good-job", "command": "echo hi", "schedule": "hourly"})).json()["id"]
        d = (await c.post(f"/api/approvals/{rid}/approve")).json()
    assert d["request"]["status"] == "done" and "good-job created" in d["request"]["result"]
    assert ("create", {"name": "good-job", "command": "echo hi", "schedule": "hourly"}) in c.calls


def test_job_validate_refuses_blocked_command(env, tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "UNIT_DIR", tmp_path / "units")
    with pytest.raises(jobs.JobError) as e:
        jobs._validate({"name": "x", "command": "sudo shutdown -h now", "schedule": "hourly"}, new=True)
    assert "always blocked" in str(e.value) and e.value.status == 400


# ── agent requests ───────────────────────────────────────────────────────────
async def test_agent_request_local_without_token(client):
    async with client as c:
        r = await c.post("/api/approvals", json={"title": "Restart the relay", "command": "systemctl --user restart relay", "requester": "relay-bot"})
        assert r.status_code == 202, r.text
        d = r.json()
        assert d["status"] == "pending" and d["requester"] == "relay-bot" and d["kind"] == "agent"
        poll = await c.get(f"/api/approvals/{d['id']}?wait=0.05")
        assert poll.json()["status"] == "pending"
        await c.post(f"/api/approvals/{d['id']}/approve", json={"allow_always": True})
        poll = await c.get(f"/api/approvals/{d['id']}")
        assert poll.json()["status"] == "approved"
        # the exact command now auto-approves
        r2 = await c.post("/api/approvals", json={"title": "again", "command": "systemctl --user restart relay"})
        assert r2.status_code == 200 and r2.json()["status"] == "approved" and r2.json()["decided_by"] == "allowlist"
        r3 = await c.post("/api/approvals", json={"title": "different", "command": "systemctl --user restart relay2"})
        assert r3.status_code == 202
    assert approvals.allow_commands() == ["systemctl --user restart relay"]
    assert events("approval_settings")[0]["target"] == "allow_commands"


async def test_agent_request_remote_needs_token(remote, env):
    async with remote as c:
        assert (await c.post("/api/approvals", json={"title": "x"})).status_code == 403
        approvals.save({"token": "lwa_good"})
        assert (await c.post("/api/approvals", json={"title": "x"}, headers={"X-Llamawatch-Token": "lwa_bad"})).status_code == 403
        r = await c.post("/api/approvals", json={"title": "from afar", "requester": "night-shift"}, headers={"X-Llamawatch-Token": "lwa_good"})
        assert r.status_code == 202
        rid = r.json()["id"]
        assert (await c.get(f"/api/approvals/{rid}", headers={"X-Llamawatch-Token": "lwa_good"})).status_code == 200
        assert (await c.get(f"/api/approvals/{rid}")).status_code == 403
        # the token never lets a caller decide, read the queue or change settings
        assert (await c.post(f"/api/approvals/{rid}/approve", headers={"X-Llamawatch-Token": "lwa_good"})).status_code == 403
        assert (await c.get("/api/approvals", headers={"X-Llamawatch-Token": "lwa_good"})).status_code == 403
        assert (await c.put("/api/approvals/rules", json={"agent": "allow"}, headers={"X-Llamawatch-Token": "lwa_good"})).status_code == 403
        assert (await c.post("/api/quick-action/hello", headers={"X-Llamawatch-Token": "lwa_good"})).status_code == 403
    assert events("approval_requested")[0]["actor"] == "token"


async def test_agent_request_blocked_and_rule_block(client):
    async with client as c:
        r = await c.post("/api/approvals", json={"title": "nuke", "command": "mkfs.ext4 /dev/sda"})
        assert r.status_code == 403 and r.json()["status"] == "blocked" and "formats" in r.json()["error"]
        set_rules(agent="block")
        r = await c.post("/api/approvals", json={"title": "plain"})
        assert r.status_code == 403 and r.json()["decided_by"] == "rule"
        set_rules(agent="allow")
        r = await c.post("/api/approvals", json={"title": "plain"})
        assert r.status_code == 200 and r.json()["status"] == "approved"
        assert (await c.post("/api/approvals", json={})).status_code == 400
    assert [e["outcome"] for e in events("approval_blocked")] == ["blocked", "blocked"]
    assert approvals.store.pending() == [] and len(approvals.store.recent()) == 3


async def test_agent_long_poll_returns_when_decided(client):
    async with client as c:
        rid = (await c.post("/api/approvals", json={"title": "wait for me"})).json()["id"]

        async def decide():
            await asyncio.sleep(0.05)
            await c.post(f"/api/approvals/{rid}/deny")

        t0 = asyncio.get_running_loop().time()
        poll, _ = await asyncio.gather(c.get(f"/api/approvals/{rid}?wait=10"), decide())
        assert poll.json()["status"] == "denied" and asyncio.get_running_loop().time() - t0 < 3


# ── page state, settings, token ──────────────────────────────────────────────
async def test_state_summary_and_settings(client):
    async with client as c:
        await c.post("/api/approvals", json={"title": "one"})
        s = (await c.get("/api/approvals/summary")).json()
        assert s == {"pending": 1}
        d = (await c.get("/api/approvals")).json()
        assert len(d["pending"]) == 1 and d["rules"]["agent"] == "ask" and d["token_set"] is False
        assert [k["id"] for k in d["kinds"]] == [k for k, _, _ in approvals.KINDS] and d["builtin_blocks"]
        r = await c.put("/api/approvals/rules", json={"service": "ask"})
        assert r.status_code == 200 and r.json()["rules"]["service"] == "ask"
        assert (await c.put("/api/approvals/rules", json={"service": "maybe"})).status_code == 400
        assert (await c.put("/api/approvals/rules", json={"nope": "ask"})).status_code == 400
        r = await c.put("/api/approvals/blocklist", json={"patterns": [" docker system prune ", "docker system prune", ""]})
        assert r.json()["block_patterns"] == ["docker system prune"]
        assert (await c.put("/api/approvals/blocklist", json={"patterns": "x"})).status_code == 400
        r = await c.put("/api/approvals/allowlist", json={"commands": ["echo ok"]})
        assert r.json()["allow_commands"] == ["echo ok"]
        assert (await c.put("/api/approvals/allowlist", json={"commands": ["rm -rf /"]})).status_code == 400
        r = await c.put("/api/approvals/settings", json={"notify_command": "ntfy pub demo \"$LW_TITLE\"", "ttl": 600})
        assert r.json()["ttl"] == 600 and "ntfy" in r.json()["notify_command"]
        assert (await c.put("/api/approvals/settings", json={"ttl": 5})).status_code == 400
        assert (await c.put("/api/approvals/settings", json={})).status_code == 400
        r = await c.put("/api/approvals/settings", json={"notify_command": ""})
        assert r.json()["notify_command"] == ""
        t = (await c.post("/api/approvals/token")).json()
        assert t["token"].startswith("lwa_") and approvals.token() == t["token"]
        assert (await c.get("/api/approvals")).json()["token_set"] is True
        assert t["token"] not in json.dumps((await c.get("/api/approvals")).json())
        assert (await c.delete("/api/approvals/token")).status_code == 200 and approvals.token() == ""
    assert {e["target"] for e in events("approval_settings")} == {"rules", "block_patterns", "allow_commands", "notify_command,ttl", "notify_command"}
    assert [e["change"] for e in events("approval_token")] == ["revoked", "new"]


async def test_settings_need_owner(remote):
    async with remote as c:
        assert (await c.get("/api/approvals")).status_code == 403
        assert (await c.put("/api/approvals/rules", json={"service": "ask"})).status_code == 403
        assert (await c.post("/api/approvals/token")).status_code == 403
        assert (await c.get("/api/approvals/summary")).status_code == 403   # middleware: not local, no password
        assert (await c.get("/approvals")).status_code == 403


# ── terminal tickets ─────────────────────────────────────────────────────────
async def test_terminal_ticket_allow_and_ask(client):
    async with client as c:
        r = await c.post("/api/terminal/ticket")
        assert r.status_code == 200 and approvals.store.consume(r.json()["ticket"])
        set_rules(terminal="ask")
        r = await c.post("/api/terminal/ticket")
        assert r.status_code == 202
        rid = r.json()["id"]
        assert not approvals.store.consume(rid)
        await c.post(f"/api/approvals/{rid}/approve")
        assert approvals.store.consume(rid) and not approvals.store.consume(rid)
        set_rules(terminal="block")
        assert (await c.post("/api/terminal/ticket")).status_code == 403
    assert approvals.store.recent()[0]["kind"] == "terminal"


def test_terminal_ws_needs_ticket_under_ask(env, monkeypatch):
    import llamawatch.server as srv
    from llamawatch.routes import chat as chat_routes
    monkeypatch.setattr(chat_routes.security, "action_allowed", lambda req, auth: True)
    opened = []
    monkeypatch.setattr(chat_routes.subprocess, "Popen", lambda *a, **k: opened.append(a) or pytest.skip("shell would open here"))
    tc = TestClient(srv.app)
    set_rules(terminal="ask")
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect) as e:
        with tc.websocket_connect("/ws/terminal/new"):
            pass
    assert e.value.code == 1008
    with pytest.raises(WebSocketDisconnect):
        with tc.websocket_connect("/ws/terminal/new?ticket=nope"):
            pass
    assert opened == [] and events("terminal_open") == []
    t = approvals.store.create("terminal", "Open a terminal", status="approved", decided_by="owner")
    with pytest.raises(pytest.skip.Exception):
        with tc.websocket_connect(f"/ws/terminal/new?ticket={t['id']}"):
            pass
    assert events("terminal_open")[0]["actor"] == "approved" and not approvals.store.consume(t["id"])


# ── notify ───────────────────────────────────────────────────────────────────
async def test_notify_runs_with_environment(env, tmp_path):
    out = tmp_path / "notified.txt"
    approvals.save({"notify_command": f"printf '%s|%s|%s|%s|%s' \"$LW_ID\" \"$LW_KIND\" \"$LW_TITLE\" \"$LW_REQUESTER\" \"$LW_URL\" > {out}"})
    rec = approvals.store.create("agent", "Ping me", requester="relay-bot")
    await approvals.notify_now(rec, base_url="http://127.0.0.1:8451/")
    assert out.read_text() == f"{rec['id']}|agent|Ping me|relay-bot|http://127.0.0.1:8451/approvals"
    assert events("approval_notify") == []


async def test_notify_failure_is_audited_not_raised(env):
    approvals.save({"notify_command": "echo nope >&2; exit 3"})
    rec = approvals.store.create("agent", "x")
    await approvals.notify_now(rec)
    e = events("approval_notify")
    assert len(e) == 1 and e[0]["outcome"] == "fail" and "nope" in e[0]["detail"]


async def test_notify_fires_on_pending_request(client, tmp_path):
    out = tmp_path / "n.txt"
    approvals.save({"notify_command": f"echo \"$LW_TITLE\" >> {out}"})
    async with client as c:
        await c.post("/api/approvals", json={"title": "Hello owner"})
    for _ in range(50):
        if out.exists():
            break
        await asyncio.sleep(0.02)
    assert out.read_text().strip() == "Hello owner"


# ── research audit line ──────────────────────────────────────────────────────
def test_sample_fixture_has_approvals_data():
    snap = json.loads(open("tests/ui/fixtures/snapshot.json").read())["rest"]
    assert snap["/api/approvals/summary"]["pending"] == len(snap["/api/approvals"]["pending"]) >= 2
    assert snap["/api/approvals"]["recent"] and snap["/api/audit"]["events"]
    assert {k["id"] for k in snap["/api/approvals"]["kinds"]} == {k for k, _, _ in approvals.KINDS}
