/* llamawatch Research page.
   New question -> /api/research/start (paid models ask for a spending cap first),
   then follow the run by polling its events. Past runs open the report page.
   #run/<id> in the address shows that run's progress. */
(function () {
  "use strict";
  var $ = function (s) { return document.querySelector(s); };
  var esc = function (s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); };

  // Plain-English names for the run's stages, in order.
  var STEPS = [
    { id: "plan", label: "Planning", stages: ["scan", "scope"] },
    { id: "read", label: "Reading", stages: ["gather", "read", "extract"] },
    { id: "facts", label: "Facts", stages: ["claims"] },
    { id: "check", label: "Checking", stages: ["verify", "counter", "gaps"] },
    { id: "write", label: "Writing", stages: ["write", "check", "drafts"] }
  ];
  var LEVEL_KEY = "lw-research-level", MODEL_KEY = "lw-research-model";
  var info = { models: [], depths: [] }, runs = [], showAll = false, lastModel = "";
  var live = null;   // { id, since, timer, tick, started, status }

  function get(k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } }
  function set(k, v) { try { window.localStorage.setItem(k, v); } catch (e) {} }

  async function api(path, body) {
    var opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
    var r = await fetch(path, opt);
    var d = await r.json().catch(function () { return {}; });
    return { ok: r.ok, status: r.status, data: d };
  }

  function money(n, cur) { return (cur || "£") + Number(n || 0).toFixed(2); }
  function mins(s) { s = Math.max(0, Math.round(s || 0)); var m = Math.floor(s / 60); return (m ? m + " min " : "") + (s % 60) + " s"; }
  function model() { return info.models.find(function (m) { return m.name === $("#rs-model").value; }); }
  function level() { var r = document.querySelector('input[name="rs-level"]:checked'); return r ? r.value : "quick"; }

  // ── New question form ──────────────────────────────────────────
  async function loadModels() {
    var r = await api("/api/research/models");
    if (!r.ok) { $("#rs-err").textContent = r.data.error || "Could not load the model list."; return; }
    info = r.data;
    var params = new URLSearchParams(location.search);
    var want = params.get("model") || get(MODEL_KEY) || info.default;
    $("#rs-model").innerHTML = info.models.length ? info.models.map(function (m) {
      return '<option value="' + esc(m.name) + '">' + esc(m.name) + (m.paid ? " (paid)" : m.local ? " (local, free)" : " (online, free)") + "</option>";
    }).join("") + '<option value="__add__">+ Add an online API…</option>' : '<option value="">No models set up</option><option value="__add__">+ Add an online API…</option>';
    if (info.models.some(function (m) { return m.name === want; })) $("#rs-model").value = want;
    lastModel = $("#rs-model").value;

    var lv = get(LEVEL_KEY) || "quick";
    if (!info.depths.some(function (d) { return d.id === lv; })) lv = info.depths.length ? info.depths[0].id : "quick";
    $("#rs-levels").innerHTML = '<legend class="rs-label">How deep</legend>' + info.depths.map(function (d) {
      return '<label class="rs-level"><input type="radio" name="rs-level" value="' + esc(d.id) + '"' + (d.id === lv ? " checked" : "") + ">" +
        "<b>" + esc(d.label) + '</b><span class="t">' + esc(d.time) + '</span><span class="a">' + esc(d.about) + "</span></label>";
    }).join("");
    $("#rs-note").textContent = info.depth_note || info.warning || "";
    if (params.get("q")) { $("#rs-q").value = params.get("q"); history.replaceState(null, "", location.pathname + location.hash); }
    count(); showModel(); estimate();
  }

  function showModel() {
    var m = model();
    $("#rs-model-note").textContent = m ? (m.note || "") : "";
    $("#rs-api-remove").hidden = !(m && m.added);
  }

  var estSeq = 0;
  async function estimate() {
    var m = model(), seq = ++estSeq;
    if (!m) { $("#rs-cost").textContent = ""; return; }
    if (!m.paid) { $("#rs-cost").innerHTML = '<span class="free">Free (' + (m.local ? "local model" : "no price set") + ")</span>"; return; }
    $("#rs-cost").textContent = "Working out the cost…";
    var r = await api("/api/research/estimate", { model: m.name, depth: level() });
    if (seq !== estSeq) return;
    $("#rs-cost").innerHTML = r.ok ? 'Up to <span class="paid">' + esc(money(r.data.cost, m.currency)) + "</span>, you approve it first" : "";
  }

  function count() {
    var n = $("#rs-q").value.trim().length;
    $("#rs-count").textContent = n ? n + " / 2,000" : "";
  }

  async function start(approved) {
    var q = $("#rs-q").value.trim(), m = model();
    $("#rs-err").textContent = "";
    if (q.length < 8) { $("#rs-err").textContent = "Write a question of at least 8 characters."; $("#rs-q").focus(); return; }
    if (!m) { $("#rs-err").textContent = "No model is set up for research."; return; }
    var body = { question: q, model: m.name, depth: level() };
    if (approved) body.approved_cost = approved;
    $("#rs-start").disabled = true;
    var r;
    try { r = await api("/api/research/start", body); }
    catch (e) { $("#rs-start").disabled = false; $("#rs-err").textContent = "Could not reach llamawatch."; return; }
    $("#rs-start").disabled = false;
    if (r.status === 402) {
      var cap = Math.max(0.01, Number(r.data.estimate || 0));
      var ok = await confirmCost(m, cap);
      if (ok) start(cap);
      return;
    }
    if (r.status === 409) { $("#rs-err").textContent = "A research run is already going. Wait for it to finish or stop it."; loadRuns(); return; }
    if (r.status === 403) { $("#rs-err").textContent = "This browser is not allowed to start runs. Log in first."; return; }
    if (!r.ok) { $("#rs-err").textContent = r.data.error || "The run did not start."; return; }
    set(MODEL_KEY, m.name); set(LEVEL_KEY, level());
    $("#rs-q").value = ""; count();
    location.hash = "run/" + r.data.id;
  }

  // ── Online API: add and remove ────────────────────────────────
  var apiFrom = null;
  function openApi() {
    apiFrom = document.activeElement;
    $("#rs-api-form").reset(); $("#rs-api-err").textContent = "";
    $("#rs-api-scrim").hidden = false;
    $("#rs-api-name").focus();
  }
  function closeApi() {
    $("#rs-api-scrim").hidden = true;
    $("#rs-api-key").value = "";
    if (apiFrom) apiFrom.focus();
  }
  async function saveApi(e) {
    e.preventDefault();
    var btn = $("#rs-api-save");
    btn.disabled = true; btn.textContent = "Testing…"; $("#rs-api-err").textContent = "";
    var name = $("#rs-api-name").value.trim();
    var r = await api("/api/research/models", {
      name: name, base_url: $("#rs-api-base").value.trim(), model: $("#rs-api-model").value.trim(),
      api_key: $("#rs-api-key").value.trim(), price_in: $("#rs-api-in").value, price_out: $("#rs-api-out").value,
      currency: $("#rs-api-cur").value.trim()
    }).catch(function () { return { ok: false, data: { error: "Could not reach llamawatch." } }; });
    btn.disabled = false; btn.textContent = "Test and save";
    if (!r.ok) { $("#rs-api-err").textContent = r.data.error || "Not saved."; return; }
    set(MODEL_KEY, name);
    closeApi();
    await loadModels();
  }
  async function removeApi() {
    var m = model();
    if (!m || !m.added || !window.confirm("Remove " + m.name + " and its saved key?")) return;
    var r = await api("/api/research/models/remove", { name: m.name });
    if (!r.ok) { $("#rs-err").textContent = r.data.error || "Could not remove it."; return; }
    await loadModels();
  }

  // ── Cost dialog ────────────────────────────────────────────────
  var dlgDone = null, dlgFrom = null;
  function confirmCost(m, cap) {
    dlgFrom = document.activeElement;
    $("#rs-dlg-text").textContent = m.name + " charges per use. The run stops if it reaches this amount, and the report is written from what it found by then.";
    $("#rs-dlg-cost").textContent = money(cap, m.currency);
    $("#rs-dlg-ok").textContent = "Approve " + money(cap, m.currency) + " and start";
    $("#rs-scrim").hidden = false;
    $("#rs-dlg-ok").focus();
    return new Promise(function (res) { dlgDone = res; });
  }
  function closeDlg(v) {
    $("#rs-scrim").hidden = true;
    if (dlgFrom) dlgFrom.focus();
    var f = dlgDone; dlgDone = null; if (f) f(v);
  }

  // ── Following a run ────────────────────────────────────────────
  function stepOf(stage) {
    for (var i = 0; i < STEPS.length; i++) if (STEPS[i].stages.indexOf(stage) >= 0) return i;
    return -1;
  }
  function drawSteps(at, finished) {
    $("#rs-steps").innerHTML = STEPS.map(function (s, i) {
      var c = finished || i < at ? "done" : i === at ? "on" : "";
      return '<li class="' + c + '"' + (i === at && !finished ? ' aria-current="step"' : "") + ">" + esc(s.label) + "</li>";
    }).join("");
  }
  function isOver(status) { return status && status !== "running"; }
  function hasReport(status) { return isOver(status) && status !== "cancelled" && status !== "missing"; }

  function stopFollowing() {
    if (!live) return;
    clearTimeout(live.timer); clearInterval(live.tick);
    live = null;
  }

  async function follow(id) {
    stopFollowing();
    live = { id: id, since: 0, at: 0, started: 0, status: "running" };
    $("#rs-live").hidden = false;
    $("#rs-log").innerHTML = ""; $("#rs-now").textContent = ""; drawSteps(0);
    $("#rs-open").hidden = true; $("#rs-close").hidden = true; $("#rs-cancel").hidden = false; $("#rs-cancel").disabled = false;
    $("#rs-live-kicker").className = "rs-kicker"; $("#rs-live-kicker").textContent = "Researching";
    $("#rs-live-hint").hidden = false;
    var r = await api("/api/research/runs/" + encodeURIComponent(id));
    if (!live || live.id !== id) return;
    if (!r.ok) { $("#rs-live-q").textContent = "That run was not found."; finish("missing"); return; }
    var m = r.data.meta || {};
    $("#rs-live-q").textContent = m.question || "";
    $("#rs-live-meta").textContent = [m.model, m.depth, m.started].filter(Boolean).join(" · ");
    $("#rs-live").scrollIntoView({ block: "start", behavior: "smooth" });
    live.tick = setInterval(elapsed, 1000);
    poll();
  }

  function elapsed() {
    if (!live || !live.started) return;
    $("#rs-elapsed").textContent = mins(Date.now() / 1000 - live.started);
  }

  async function poll() {
    if (!live) return;
    var id = live.id, r;
    try { r = await api("/api/research/runs/" + encodeURIComponent(id) + "/events?since=" + live.since); }
    catch (e) { r = { ok: false }; }
    if (!live || live.id !== id) return;
    if (r.ok) {
      var ev = r.data.events || [];
      live.since = r.data.next || live.since;
      ev.forEach(function (e) {
        if (!live.started && e.t) live.started = e.t;
        var k = stepOf(e.stage);
        if (k > live.at) live.at = k;
        var li = document.createElement("li");
        if (e.level === "error") li.className = "error";
        var t = e.t ? new Date(e.t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";
        li.innerHTML = "<time>" + esc(t) + "</time>" + esc(e.msg || "");
        $("#rs-log").appendChild(li);
        if (e.stage !== "run") $("#rs-now").textContent = e.msg || "";
      });
      if (ev.length) { var log = $("#rs-log"); log.scrollTop = log.scrollHeight; }
      drawSteps(live.at);
      elapsed();
      if (isOver(r.data.status)) return finish(r.data.status);
    }
    live.timer = setTimeout(poll, document.hidden ? 8000 : 2500);
  }

  function finish(status) {
    if (!live) return;
    var id = live.id, k = $("#rs-live-kicker");
    clearInterval(live.tick); clearTimeout(live.timer);
    live.status = status;
    $("#rs-cancel").hidden = true; $("#rs-close").hidden = false; $("#rs-live-hint").hidden = true;
    if (status === "done") { k.textContent = "Finished"; k.className = "rs-kicker done"; drawSteps(STEPS.length, true); $("#rs-now").textContent = "The report is ready."; }
    else if (status === "no evidence") { k.textContent = "No answer found"; k.className = "rs-kicker bad"; $("#rs-now").textContent = "The pages it found did not answer the question. Try wording it differently or pick the full report."; }
    else if (status === "cancelled") { k.textContent = "Stopped"; k.className = "rs-kicker bad"; $("#rs-now").textContent = "You stopped this run."; }
    else if (status === "missing") { k.textContent = "Not found"; k.className = "rs-kicker bad"; }
    else { k.textContent = /^stopped/.test(status) ? "Stopped at the spending cap" : "Failed"; k.className = "rs-kicker bad"; $("#rs-now").textContent = status; }
    if (hasReport(status)) { $("#rs-open").href = "/research/runs/" + encodeURIComponent(id) + "/report"; $("#rs-open").hidden = false; }
    loadRuns();
  }

  async function cancel() {
    if (!live) return;
    $("#rs-cancel").disabled = true;
    var r = await api("/api/research/runs/" + encodeURIComponent(live.id) + "/cancel", {});
    if (!r.ok) { $("#rs-cancel").disabled = false; $("#rs-now").textContent = r.data.error === "not running" ? "This run is no longer going." : (r.data.error || "Could not stop it."); }
    else $("#rs-now").textContent = "Stopping after the current step…";
  }

  function route() {
    var m = location.hash.match(/^#run\/([0-9]{8}-[0-9]{6}-[0-9a-f]{6})$/);
    if (m) { if (!live || live.id !== m[1]) follow(m[1]); }
    else { stopFollowing(); $("#rs-live").hidden = true; }
  }

  // ── Past runs ──────────────────────────────────────────────────
  function statusChip(r) {
    var s = r.status || "";
    if (s === "running") return '<span class="chip running">running</span>';
    if (s === "done" && r.trust && r.trust.level) return '<span class="chip ' + esc(r.trust.level) + '">trust: ' + esc(r.trust.level) + "</span>";
    if (s === "done") return '<span class="chip muted">done</span>';
    if (s === "no evidence") return '<span class="chip bad">no answer found</span>';
    if (s === "cancelled") return '<span class="chip muted">stopped</span>';
    return '<span class="chip bad">' + esc(/^stopped/.test(s) ? "cap reached" : "failed") + "</span>";
  }
  async function loadRuns() {
    var r = await api("/api/research/runs").catch(function () { return { ok: false, data: {} }; });
    if (!r.ok) { $("#rs-list").innerHTML = '<li class="rs-empty">Could not load past research.</li>'; return; }
    runs = r.data.runs || [];
    drawRuns();
    if (!location.hash) {     // a run left going in another tab: show it
      var going = runs.find(function (x) { return x.status === "running"; });
      if (going) location.hash = "run/" + going.id;
    }
  }
  function drawRuns() {
    var list = showAll ? runs : runs.slice(0, 12);
    $("#rs-more").hidden = showAll || runs.length <= 12;
    $("#rs-list").innerHTML = list.length ? list.map(function (r) {
      var href = !hasReport(r.status) ? "#run/" + r.id : "/research/runs/" + encodeURIComponent(r.id) + "/report";
      var meta = [r.started, r.model, r.depth, r.seconds ? mins(r.seconds) : "", r.cost ? money(r.cost) : ""].filter(Boolean);
      return '<li><a href="' + esc(href) + '"><span class="q">' + esc(r.title && r.status === "done" ? r.title : r.question || "(no question)") + "</span>" +
        '<span class="m">' + statusChip(r) + meta.map(function (x) { return "<span>" + esc(x) + "</span>"; }).join("") + "</span></a></li>";
    }).join("") : '<li class="rs-empty">Nothing yet. Ask a question above.</li>';
  }

  // ── Wiring ─────────────────────────────────────────────────────
  $("#rs-q").addEventListener("input", count);
  $("#rs-q").addEventListener("keydown", function (e) { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) start(); });
  $("#rs-model").addEventListener("change", function () {
    if (this.value === "__add__") { this.value = lastModel; openApi(); return; }
    lastModel = this.value; showModel(); estimate();
  });
  $("#rs-levels").addEventListener("change", estimate);
  $("#rs-start").addEventListener("click", function () { start(); });
  $("#rs-cancel").addEventListener("click", cancel);
  $("#rs-close").addEventListener("click", function () { history.pushState(null, "", location.pathname); route(); });
  $("#rs-refresh").addEventListener("click", loadRuns);
  $("#rs-more").addEventListener("click", function () { showAll = true; drawRuns(); });
  $("#rs-api-add").addEventListener("click", openApi);
  $("#rs-api-remove").addEventListener("click", removeApi);
  $("#rs-api-cancel").addEventListener("click", closeApi);
  $("#rs-api-form").addEventListener("submit", saveApi);
  $("#rs-dlg-cancel").addEventListener("click", function () { closeDlg(false); });
  $("#rs-dlg-ok").addEventListener("click", function () { closeDlg(true); });
  $("#rs-scrim").addEventListener("click", function (e) { if (e.target.id === "rs-scrim") closeDlg(false); });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    if (!$("#rs-scrim").hidden) closeDlg(false);
    else if (!$("#rs-api-scrim").hidden) closeApi();
  });
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden && live && !isOver(live.status)) { clearTimeout(live.timer); poll(); }
  });
  window.addEventListener("hashchange", route);

  loadModels();
  loadRuns().then(route);
})();
