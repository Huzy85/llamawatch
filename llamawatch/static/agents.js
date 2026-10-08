/* llamawatch Agents page: now cards with a ticking timer, run history, replay. Read-only. */
(function () {
  "use strict";
  var $ = function (s) { return document.querySelector(s); };
  var esc = function (s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); };
  var POLL_MS = 10000, FILTER_KEY = "lw-agents-filter";
  var data = null, filter = "all", search = "", timer = null, tick = null, openId = null, replayData = null;
  var serverNow = 0, gotAt = 0;          // the server's clock at the last load, and when we got it

  function get(k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } }
  function set(k, v) { try { window.localStorage.setItem(k, v); } catch (e) {} }
  async function api(path) {
    var r = await fetch(path);
    var d = await r.json().catch(function () { return {}; });
    return { ok: r.ok, status: r.status, data: d };
  }
  function nowSecs() { return serverNow + (performance.now() - gotAt) / 1000; }

  // ── formatting ────────────────────────────────────────────────
  function clock(secs) {
    secs = Math.max(0, Math.floor(secs || 0));
    var h = Math.floor(secs / 3600), m = Math.floor((secs % 3600) / 60), s = secs % 60;
    var mm = (h ? String(m).padStart(2, "0") : String(m)), ss = String(s).padStart(2, "0");
    return (h ? h + ":" : "") + mm + ":" + ss;
  }
  function length(secs) {
    secs = Math.max(0, Math.round(secs || 0));
    if (secs < 60) return secs + "s";
    if (secs < 3600) return Math.round(secs / 60) + "m";
    if (secs < 86400) return (secs / 3600).toFixed(1).replace(/\.0$/, "") + "h";
    return (secs / 86400).toFixed(1).replace(/\.0$/, "") + "d";
  }
  function tok(n) {
    n = Number(n) || 0;
    if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + "M";
    if (n >= 1e3) return (n / 1e3).toFixed(n >= 1e4 ? 0 : 1) + "k";
    return String(n);
  }
  function when(epoch) {
    if (!epoch) return "";
    var d = new Date(epoch * 1000), ref = new Date(nowSecs() * 1000);
    var sameDay = d.toDateString() === ref.toDateString();
    var hm = String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
    if (sameDay) return hm;
    return ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][d.getMonth()] + " " + d.getDate() + " " + hm;
  }
  function stateWord(s) {
    return { working: "working", idle: "idle", done: "done", failed: "failed", online: "online", partial: "partial", offline: "offline" }[s] || s;
  }

  // ── now cards ─────────────────────────────────────────────────
  function renderNow() {
    var live = data.live || [];
    var host = $("#ag-cards");
    host.innerHTML = live.map(function (a) {
      var chip = a.project || a.machine || "";
      var timed = a.since != null;
      return '<article class="ag-card ' + esc(a.kind || "") + '" data-id="' + esc(a.id) + '">' +
        '<div class="ag-card-top"><span class="ag-dot ' + esc(a.state) + '" aria-hidden="true"></span>' +
        '<span class="ag-card-name">' + esc(a.agent) + '</span>' +
        (chip ? '<span class="ag-chip" title="' + esc(chip) + '">' + esc(chip) + '</span>' : "") + '</div>' +
        '<div class="ag-card-line" title="' + esc(a.line || "") + '">' + esc(a.line || (a.title || "")) + '</div>' +
        '<div class="ag-card-foot"><span class="ag-card-state">' + esc(stateWord(a.state)) + '</span>' +
        (timed ? '<span class="ag-timer" data-since="' + esc(a.since) + '">' + clock(nowSecs() - a.since) + '</span>' : "") + '</div></article>';
    }).join("");
    var working = live.filter(function (a) { return a.state === "working" || a.state === "online"; }).length;
    $("#ag-now-empty").hidden = live.length > 0;
    $("#ag-now-hint").textContent = live.length ? (working + " of " + live.length + " active") : "";
  }
  function tickTimers() {
    document.querySelectorAll(".ag-timer[data-since]").forEach(function (el) {
      el.textContent = clock(nowSecs() - Number(el.getAttribute("data-since")));
    });
  }

  // ── history ───────────────────────────────────────────────────
  function renderChips() {
    var names = ["all"].concat(data.agents || []);
    if (names.indexOf(filter) < 0) filter = "all";
    $("#ag-chips").innerHTML = names.map(function (n) {
      return '<button type="button" data-filter="' + esc(n) + '" aria-pressed="' + (n === filter) + '">' + esc(n === "all" ? "All" : n) + '</button>';
    }).join("");
  }
  function visibleRuns() {
    var q = search.trim().toLowerCase();
    return (data.runs || []).filter(function (r) {
      if (filter !== "all" && r.agent !== filter) return false;
      if (q && (r.title + " " + r.project).toLowerCase().indexOf(q) < 0) return false;
      return true;
    });
  }
  function renderRuns() {
    var rows = visibleRuns();
    $("#ag-runs-body").innerHTML = rows.map(function (r) {
      return '<tr data-id="' + esc(r.id) + '" aria-selected="' + (r.id === openId) + '">' +
        '<td class="ag-agent">' + esc(r.agent) + '</td>' +
        '<td class="ag-title"><button type="button" data-open="' + esc(r.id) + '" title="' + esc(r.title) + '">' + esc(r.title) + '</button></td>' +
        '<td class="ag-project">' + esc(r.project) + '</td>' +
        '<td class="ag-started">' + esc(when(r.started)) + '</td>' +
        '<td class="num">' + esc(length(r.seconds)) + '</td>' +
        '<td class="num ag-steps-col">' + esc(r.steps) + '</td>' +
        '<td class="num">' + esc(tok(r.tokens)) + '</td>' +
        '<td class="ag-state"><span class="ag-state-dot ' + esc(r.status) + '" aria-hidden="true"></span>' + esc(stateWord(r.status)) + '</td></tr>';
    }).join("");
    $("#ag-runs").hidden = rows.length === 0;
    $("#ag-history-empty").hidden = rows.length > 0;
    $("#ag-history-empty").textContent = (data.runs || []).length ? "No runs match." : "No runs in the last " + (data.window_days || 7) + " days.";
  }

  // ── replay ────────────────────────────────────────────────────
  function renderReplay() {
    var run = replayData.run, steps = replayData.steps || [];
    $("#ag-replay-h").textContent = "Replay · " + run.title;
    $("#ag-replay-sub").textContent = run.agent + (run.project ? " · " + run.project : "") + " · " + stateWord(run.status);
    var parts = ["<span>Length <strong>" + esc(length(run.seconds)) + "</strong></span>",
                 "<span>Steps <strong>" + esc(run.steps) + "</strong></span>",
                 "<span>Tokens <strong>" + esc(tok(run.tokens)) + "</strong></span>"];
    if (run.in || run.out) parts.push("<span>In <strong>" + esc(tok(run.in)) + "</strong> · out <strong>" + esc(tok(run.out)) + "</strong>" + (run.cache ? " · cached <strong>" + esc(tok(run.cache)) + "</strong>" : "") + "</span>");
    if (run.thinking) parts.push("<span>Thinking blocks <strong>" + esc(run.thinking) + "</strong></span>");
    if (run.cost) parts.push("<span>Cost <strong>" + esc(Number(run.cost).toFixed(2)) + "</strong></span>");
    $("#ag-totals").innerHTML = parts.join("");
    $("#ag-steps").innerHTML = steps.map(function (s) {
      var t = (s.in || 0) + (s.out || 0) + (s.cache || 0);
      var tokTxt = s.kind === "stage" ? (s.calls ? s.calls + " calls · " + tok(s.out) : "") : (t ? tok(t) : "");
      return '<li class="ag-step ' + esc(s.kind) + '">' +
        '<span class="ag-step-off">' + esc(clock(s.offset)) + '</span>' +
        '<span class="ag-step-dur">' + esc(s.dur ? length(s.dur) : "") + '</span>' +
        '<span class="ag-step-main"><span class="ag-step-label">' + esc(s.label) + '</span>' +
        (s.detail ? ' <span class="ag-step-detail" title="' + esc(s.detail) + '">' + esc(s.detail) + '</span>' : "") + '</span>' +
        '<span class="ag-step-tok">' + esc(tokTxt) + '</span></li>';
    }).join("");
  }
  async function openReplay(id, keepFocus) {
    var r = await api("/api/agents/runs/" + encodeURIComponent(id));
    if (!r.ok) { $("#ag-err").textContent = r.data.error || ("Could not load the run (" + r.status + ")"); return; }
    $("#ag-err").textContent = "";
    openId = id; replayData = r.data;
    renderReplay();
    var panel = $("#ag-replay"); panel.hidden = false;
    document.querySelectorAll("#ag-runs-body tr").forEach(function (tr) { tr.setAttribute("aria-selected", tr.getAttribute("data-id") === id ? "true" : "false"); });
    if (!keepFocus) { panel.scrollIntoView({ block: "start" }); panel.focus({ preventScroll: true }); }
  }
  function closeReplay() {
    openId = null; replayData = null;
    $("#ag-replay").hidden = true;
    document.querySelectorAll("#ag-runs-body tr[aria-selected='true']").forEach(function (tr) { tr.setAttribute("aria-selected", "false"); });
  }

  // ── load ──────────────────────────────────────────────────────
  async function load() {
    var r = await api("/api/agents");
    if (!r.ok) { $("#ag-updated").textContent = "Could not load (" + r.status + ")"; return; }
    data = r.data; serverNow = Number(data.now) || 0; gotAt = performance.now();
    renderNow(); renderChips(); renderRuns();
    $("#ag-updated").textContent = (data.live || []).length + " live · " + (data.runs || []).length + " runs";
    if (openId && replayData && replayData.run && replayData.run.status === "working") openReplay(openId, true);
  }
  function schedule() { clearTimeout(timer); timer = setTimeout(async function () { await load(); schedule(); }, POLL_MS); }

  document.addEventListener("DOMContentLoaded", function () {
    filter = get(FILTER_KEY) || "all";
    $("#ag-chips").addEventListener("click", function (e) {
      var b = e.target.closest("[data-filter]"); if (!b) return;
      filter = b.getAttribute("data-filter"); set(FILTER_KEY, filter);
      renderChips(); renderRuns();
    });
    $("#ag-search").addEventListener("input", function (e) { search = e.target.value; renderRuns(); });
    $("#ag-runs-body").addEventListener("click", function (e) { var b = e.target.closest("[data-open]"); if (b) openReplay(b.getAttribute("data-open")); });
    $("#ag-replay-close").addEventListener("click", closeReplay);
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !$("#ag-replay").hidden) closeReplay(); });
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) { load(); schedule(); } else clearTimeout(timer);
    });
    tick = setInterval(tickTimers, 1000);
    load().then(schedule);
  });
})();
