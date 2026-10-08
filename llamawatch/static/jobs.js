/* llamawatch Jobs page.
   One list of every scheduled job (/api/jobs), a next-24h strip, filters and
   search, an inline detail with the journal, and Run / Pause / Resume on user
   timers behind a confirm dialog. An add/edit form writes user timers through
   /api/jobs; only jobs the page made (managed) get Edit and Delete. Polls every 30 s. */
(function () {
  "use strict";
  var $ = function (s) { return document.querySelector(s); };
  var esc = function (s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); };
  var POLL_MS = 30000, FILTER_KEY = "lw-jobs-filter";
  var data = { jobs: [], counts: {}, at: 0 }, filter = "all", hour = null, query = "", openId = null, pending = null, timer = null;

  function get(k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } }
  function set(k, v) { try { window.localStorage.setItem(k, v); } catch (e) {} }

  async function api(path, body, method) {
    var opt = body === undefined && !method ? {} : { method: method || "POST", headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) };
    var r = await fetch(path, opt);
    var d = await r.json().catch(function () { return {}; });
    return { ok: r.ok, status: r.status, data: d };
  }

  // ── time words ────────────────────────────────────────────────
  function now() { return Date.now() / 1000; }
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function clock(t) { var d = new Date(t * 1000); return pad(d.getHours()) + ":" + pad(d.getMinutes()); }
  function dayName(t) { return ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][new Date(t * 1000).getDay()]; }
  function full(t) { if (!t) return "never"; var d = new Date(t * 1000); return dayName(t) + " " + d.getDate() + " " + ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][d.getMonth()] + " " + clock(t); }
  function ago(t) {
    if (!t) return "never";
    var s = Math.max(0, now() - t);
    if (s < 60) return "just now";
    if (s < 3600) return Math.round(s / 60) + " min ago";
    if (s < 86400) return Math.round(s / 3600) + " h ago";
    if (s < 86400 * 7) return Math.round(s / 86400) + " d ago";
    return full(t);
  }
  function until(t) {
    if (!t) return "";
    var s = t - now();
    if (s < 0) return "due";
    if (s < 60) return "under a minute";
    if (s < 3600) return "in " + Math.round(s / 60) + " min";
    if (s < 86400) return "in " + Math.round(s / 3600) + " h, " + clock(t);
    if (s < 86400 * 7) return dayName(t) + " " + clock(t);
    return full(t);
  }
  function dur(a, b) {
    if (!a || !b || b < a) return "";
    var s = Math.round(b - a);
    if (s < 60) return s + " s";
    return Math.floor(s / 60) + " min " + (s % 60) + " s";
  }

  // ── derived views ─────────────────────────────────────────────
  function hourIndex(t) { var h = Math.floor((t - now()) / 3600); return h >= 0 && h < 24 ? h : -1; }

  function matches(j) {
    if (filter !== "all" && j.state !== filter) return false;
    if (hour !== null && hourIndex(j.next || 0) !== hour) return false;
    if (query) {
      var q = query.toLowerCase();
      if ((j.name + " " + j.description + " " + j.schedule + " " + j.kind).toLowerCase().indexOf(q) < 0) return false;
    }
    return true;
  }

  // ── render ────────────────────────────────────────────────────
  function renderChips() {
    var c = data.counts || {}, items = [
      { id: "all", label: "All", n: c.total || 0 },
      { id: "running", label: "Running", n: c.running || 0 },
      { id: "failed", label: "Failed", n: c.failed || 0 },
      { id: "paused", label: "Paused", n: c.paused || 0 }
    ];
    $("#jb-chips").innerHTML = items.map(function (it) {
      return '<button type="button" class="jb-chip ' + it.id + '" data-filter="' + it.id + '" aria-pressed="' + (filter === it.id) + '">' +
        esc(it.label) + " <b>" + it.n + "</b></button>";
    }).join("");
  }

  function renderStrip() {
    var counts = [], failed = [], t0 = now(), i;
    for (i = 0; i < 24; i++) { counts.push(0); failed.push(false); }
    data.jobs.forEach(function (j) {
      var h = hourIndex(j.next || 0);
      if (h >= 0) { counts[h]++; if (j.state === "failed") failed[h] = true; }
    });
    var max = Math.max(1, Math.max.apply(null, counts)), startH = new Date(t0 * 1000).getHours();
    $("#jb-strip").innerHTML = counts.map(function (n, h) {
      var pct = n ? Math.max(14, Math.round(n / max * 100)) : 0, lbl = pad((startH + h) % 24);
      return '<button type="button" class="jb-hour' + (n ? " has" : "") + (failed[h] ? " failed" : "") + (h === 0 ? " now" : "") +
        '" data-hour="' + h + '" aria-pressed="' + (hour === h) + '" aria-label="' + lbl + ':00, ' + n + (n === 1 ? " job" : " jobs") + '" title="' + lbl + ":00 · " + n + '">' +
        '<span class="bar" style="height:' + pct + '%"></span><span class="lbl">' + lbl + "</span></button>";
    }).join("");
    $("#jb-strip-note").textContent = hour === null ? "Tap an hour to see what runs then." :
      "Showing jobs due " + pad((startH + hour) % 24) + ":00 to " + pad((startH + hour + 1) % 24) + ":00. Tap again to clear.";
  }

  function kindTag(j) { return j.kind === "user" ? "" : '<span class="jb-kind">' + esc(j.kind) + "</span>"; }

  function lastCell(j) {
    if (j.state === "running") return '<span class="jb-last running">running now' + (j.last && j.last.at ? ", since " + clock(j.last.at) : "") + "</span>";
    if (!j.last) return '<span class="jb-last muted">' + (j.kind === "cron" ? "cron keeps no record" : "not run yet") + "</span>";
    if (!j.last.ok) var f = "failed " + ago(j.last.at || j.last.end) + (j.last.detail ? ", " + j.last.detail : "");
    if (!j.last.ok) return '<span class="jb-last failed" title="' + esc(f) + '">' + esc(f) + "</span>";
    return '<span class="jb-last">ok, ' + esc(ago(j.last.end || j.last.at)) + "</span>";
  }

  function nextCell(j) {
    if (j.state === "paused") return '<span class="jb-next muted">paused</span>';
    if (j.state === "off") return '<span class="jb-next muted">off</span>';
    if (!j.next) return '<span class="jb-next muted">' + (j.kind === "research" ? "when it finishes" : "not scheduled") + "</span>";
    var s = j.next - now();
    return '<span class="jb-next' + (s < 3600 ? " soon" : "") + '" title="' + esc(full(j.next)) + '">' + esc(until(j.next)) + "</span>";
  }

  function actions(j) {
    if (j.kind === "research") return '<a class="rs-btn small" href="' + esc(j.href || "/research") + '">Open</a>';
    if (!j.can_act) return '<span class="jb-ro">read-only</span>';
    var out = "";
    if (j.state === "paused" || j.state === "off") out += '<button type="button" class="rs-btn small go" data-act="resume">Resume</button>';
    else out += '<button type="button" class="rs-btn small warn" data-act="pause">Pause</button>';
    if (j.state !== "running") out += '<button type="button" class="rs-btn small" data-act="run">Run now</button>';
    if (j.managed) out += '<button type="button" class="rs-btn small edit" data-act="edit">Edit</button>';
    return out;
  }

  function renderList() {
    var shown = data.jobs.filter(matches);
    $("#jb-count").textContent = shown.length === data.jobs.length ? data.jobs.length + " jobs" : shown.length + " of " + data.jobs.length;
    $("#jb-empty").hidden = shown.length > 0 || !data.jobs.length;
    $("#jb-list").innerHTML = shown.map(function (j) {
      var open = j.id === openId;
      return '<li class="jb-item' + (open ? " open" : "") + '" data-id="' + esc(j.id) + '">' +
        '<div class="jb-row" role="button" tabindex="0" aria-expanded="' + open + '">' +
          '<span class="jb-dot ' + esc(j.state) + '" title="' + esc(j.state) + '"></span>' +
          '<div class="jb-name"><b>' + esc(j.name) + kindTag(j) + "</b><span>" + esc(j.description || "") + "</span></div>" +
          '<div class="jb-meta"><span class="jb-sched' + (j.schedule === "no schedule" ? " muted" : "") + '" title="' + esc(j.schedule) + '">' + esc(j.schedule) + '</span><span class="sep">·</span>' +
            lastCell(j) + '<span class="sep">·</span>' + nextCell(j) + "</div>" +
          '<div class="jb-acts">' + actions(j) + "</div>" +
        "</div>" + (open ? '<div class="jb-detail" id="jb-detail"></div>' : "") + "</li>";
    }).join("");
    if (openId) {
      var j = shown.find(function (x) { return x.id === openId; });
      if (j) renderDetail(j); else openId = null;
    }
  }

  function renderDetail(j) {
    var el = $("#jb-detail"); if (!el) return;
    var u = j.units || {}, facts = [];
    if (u.timer) facts.push(["Timer", u.timer]);
    if (u.service) facts.push(["Service", u.service]);
    facts.push(["Schedule", j.schedule]);
    if (j.last) {
      facts.push(["Last start", full(j.last.at)]);
      if (j.last.end) facts.push(["Last end", full(j.last.end)]);
      var d = dur(j.last.at, j.last.end); if (d) facts.push(["Took", d]);
      facts.push(["Result", j.last.ok ? "ok" : "failed" + (j.last.detail ? ", " + j.last.detail : ""), j.last.ok ? "" : "failed"]);
    }
    if (j.next) facts.push(["Next", full(j.next)]);
    var note = "";
    if (j.kind === "system") note = "A system timer. It runs as root, so this page only shows it.";
    else if (j.kind === "cron") note = "A crontab line. cron keeps no record of past runs, and this page does not edit the crontab.";
    else if (j.kind === "research") note = "A research run in progress. Open it to follow along.";
    else if (j.state === "paused") note = "Paused until the next login or reboot, when systemd starts the timer again. Resume starts it now.";
    else if (j.managed) note = "Made from this page. Edit changes its command or schedule; the name stays.";
    el.innerHTML = '<dl class="jb-facts">' + facts.map(function (f) {
      return "<div><dt>" + esc(f[0]) + '</dt><dd class="' + esc(f[2] || "") + '" title="' + esc(f[1]) + '">' + esc(f[1]) + "</dd></div>";
    }).join("") + "</dl>" + (note ? '<p class="jb-note">' + esc(note) + "</p>" : "") +
      (j.kind === "user" || j.kind === "system" ? '<pre class="jb-log empty" id="jb-log">Loading the log…</pre>' : "");
    if (j.kind === "user" || j.kind === "system") loadLog(j);
  }

  async function loadLog(j) {
    var r = await api("/api/jobs/" + encodeURIComponent(j.id) + "/log");
    var el = $("#jb-log"); if (!el || openId !== j.id) return;
    var lines = (r.ok && r.data.lines) || [];
    el.textContent = lines.length ? lines.join("\n") : (r.ok ? "No log lines yet." : (r.data.error || "Could not load the log."));
    el.classList.toggle("empty", !lines.length);
    el.scrollTop = el.scrollHeight;
  }

  function renderSetup() {
    var s = data.setup || {}, can = s.available !== false;
    $("#jb-add").hidden = !can;
    var note = $("#jb-add-note");
    note.hidden = can;
    if (!can) note.textContent = "Adding jobs is off: " + (s.reason || "systemd user timers are not available here.");
  }

  function render() { renderChips(); renderStrip(); renderList(); renderSetup(); }

  // ── data ──────────────────────────────────────────────────────
  async function load(fresh) {
    var r = await api("/api/jobs" + (fresh ? "?fresh=1" : ""));
    if (!r.ok) { $("#jb-err").textContent = r.data.error || "Could not load the job list."; return; }
    $("#jb-err").textContent = "";
    data = r.data; data.jobs = data.jobs || [];
    $("#jb-updated").textContent = "updated " + clock(data.at || now());
    render();
  }
  function schedule() { clearTimeout(timer); timer = setTimeout(function () { load(false).then(schedule); }, POLL_MS); }

  // ── actions ───────────────────────────────────────────────────
  var WORDS = {
    run: { title: "Run now?", text: function (j) { return "Start " + j.name + " straight away, outside its schedule. The timer carries on as normal afterwards."; }, ok: "Run now" },
    pause: { title: "Pause this job?", text: function (j) { return j.name + " will not run until the next login or reboot, or until you press Resume here."; }, ok: "Pause" },
    resume: { title: "Resume this job?", text: function (j) { return "Start the timer for " + j.name + " again. Its next run: " + (j.schedule || "as scheduled") + "."; }, ok: "Resume" },
    delete: { title: "Delete this job?", text: function (j) { return "Remove the timer, service and script for " + j.name + ". Nothing it has already written is touched. This cannot be undone."; }, ok: "Delete" }
  };

  function ask(j, act) {
    pending = { job: j, act: act };
    $("#jb-dlg-title").textContent = WORDS[act].title;
    $("#jb-dlg-text").textContent = WORDS[act].text(j);
    $("#jb-dlg-ok").textContent = WORDS[act].ok;
    $("#jb-dlg-ok").disabled = false;
    $("#jb-scrim").hidden = false;
    $("#jb-dlg-ok").focus();
  }
  function closeDialog() { $("#jb-scrim").hidden = true; pending = null; }

  async function confirmAction() {
    if (!pending) return;
    var p = pending; $("#jb-dlg-ok").disabled = true;
    var r = p.act === "delete"
      ? await api("/api/jobs/" + encodeURIComponent(p.job.id), undefined, "DELETE")
      : await api("/api/jobs/" + encodeURIComponent(p.job.id) + "/" + p.act, {});
    closeDialog();
    if (!r.ok) { toast(r.data.error || "That did not work.", true); return; }
    if (p.act === "delete") { closeForm(); if (openId === p.job.id) openId = null; }
    toast(r.data.message || "Done");
    // systemd needs a moment before `show` reflects the change
    setTimeout(function () { load(true); }, 700);
  }

  // ── add / edit form ───────────────────────────────────────────
  var editing = null, previewTimer = null, previewSeq = 0;
  var F = { scrim: "#jb-form-scrim", name: "#jb-f-name", desc: "#jb-f-desc", cmd: "#jb-f-cmd", sched: "#jb-f-sched", prev: "#jb-f-preview", err: "#jb-form-err", save: "#jb-f-save", del: "#jb-f-delete", title: "#jb-form-title", linger: "#jb-f-linger" };
  var PREVIEW_HELP = "Try “every 15 min”, “daily 06:30”, “Mon 02:00”, “mon,wed,fri 09:00”, or a systemd calendar expression.";

  function openForm(j) {
    editing = j || null;
    var spec = (j && j.spec) || {};
    $(F.title).textContent = j ? "Edit " + j.name : "New job";
    $(F.name).value = j ? j.name : ""; $(F.name).readOnly = !!j;
    $("#jb-f-name-hint").hidden = !!j;
    $(F.desc).value = spec.description || (j ? j.description : "") || "";
    $(F.cmd).value = spec.command || "";
    $(F.sched).value = spec.schedule || "";
    $(F.save).textContent = j ? "Save changes" : "Create job"; $(F.save).disabled = false;
    $(F.del).hidden = !j;
    $(F.err).textContent = "";
    $(F.linger).hidden = !(data.setup && data.setup.linger === false);
    setPreview(PREVIEW_HELP, "");
    $(F.scrim).hidden = false;
    if (j) previewNow();
    (j ? $(F.cmd) : $(F.name)).focus();
  }
  function closeForm() { $(F.scrim).hidden = true; editing = null; clearTimeout(previewTimer); }
  function setPreview(text, cls) { var el = $(F.prev); el.textContent = text; el.className = "jb-hint" + (cls ? " " + cls : ""); }

  async function previewNow() {
    var text = $(F.sched).value.trim(), seq = ++previewSeq;
    if (!text) { setPreview(PREVIEW_HELP, ""); return; }
    var r = await api("/api/jobs/preview?schedule=" + encodeURIComponent(text));
    if (seq !== previewSeq) return;
    if (!r.ok) { setPreview(r.data.error || "That schedule is not understood.", "bad"); return; }
    setPreview("Runs " + r.data.words + (r.data.next ? ", next " + full(r.data.next) : "") + (r.data.calendar !== text ? "  (" + r.data.calendar + ")" : ""), "good");
  }

  function formFields() {
    return { name: $(F.name).value.trim().toLowerCase(), description: $(F.desc).value.trim(), command: $(F.cmd).value.trim(), schedule: $(F.sched).value.trim() };
  }

  async function submitForm(e) {
    e.preventDefault();
    var f = formFields(), err = $(F.err);
    if (!editing && !/^[a-z0-9][a-z0-9-]{0,39}$/.test(f.name)) { err.textContent = "The name can only have lowercase letters, numbers and dashes, up to 40 characters."; $(F.name).focus(); return; }
    if (!f.command) { err.textContent = "A command is needed."; $(F.cmd).focus(); return; }
    if (!f.schedule) { err.textContent = "A schedule is needed."; $(F.sched).focus(); return; }
    err.textContent = ""; $(F.save).disabled = true;
    var r = editing ? await api("/api/jobs/" + encodeURIComponent(editing.id), f, "PUT") : await api("/api/jobs", f);
    $(F.save).disabled = false;
    if (!r.ok) { err.textContent = r.data.error || "That did not work."; return; }
    var id = r.data.id || (editing && editing.id);
    closeForm();
    toast(r.data.message || "Saved");
    openId = id || openId;
    setTimeout(function () { load(true); }, 700);
  }

  var toastTimer = null;
  function toast(msg, bad) {
    var el = $("#jb-toast"); el.textContent = msg; el.classList.toggle("bad", !!bad); el.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(function () { el.hidden = true; }, 4000);
  }

  // ── events ────────────────────────────────────────────────────
  $("#jb-chips").addEventListener("click", function (e) {
    var b = e.target.closest("[data-filter]"); if (!b) return;
    filter = b.getAttribute("data-filter"); set(FILTER_KEY, filter); render();
  });
  $("#jb-strip").addEventListener("click", function (e) {
    var b = e.target.closest("[data-hour]"); if (!b) return;
    var h = Number(b.getAttribute("data-hour")); hour = hour === h ? null : h; render();
  });
  $("#jb-search").addEventListener("input", function () { query = this.value.trim(); renderList(); });
  $("#jb-list").addEventListener("click", function (e) {
    var act = e.target.closest("[data-act]");
    var item = e.target.closest(".jb-item"); if (!item) return;
    var j = data.jobs.find(function (x) { return x.id === item.getAttribute("data-id"); }); if (!j) return;
    if (act) {
      e.stopPropagation();
      var a = act.getAttribute("data-act");
      if (a === "edit") openForm(j); else ask(j, a);
      return;
    }
    if (e.target.closest("a")) return;
    if (e.target.closest(".jb-detail")) return;
    openId = openId === j.id ? null : j.id; renderList();
  });
  $("#jb-list").addEventListener("keydown", function (e) {
    if ((e.key === "Enter" || e.key === " ") && e.target.classList.contains("jb-row")) { e.preventDefault(); e.target.click(); }
  });
  $("#jb-dlg-cancel").addEventListener("click", closeDialog);
  $("#jb-dlg-ok").addEventListener("click", confirmAction);
  $("#jb-scrim").addEventListener("click", function (e) { if (e.target === this) closeDialog(); });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    if (!$("#jb-scrim").hidden) closeDialog();
    else if (!$(F.scrim).hidden) closeForm();
  });
  $("#jb-add").addEventListener("click", function () { openForm(null); });
  $("#jb-form").addEventListener("submit", submitForm);
  $("#jb-f-cancel").addEventListener("click", closeForm);
  $(F.scrim).addEventListener("click", function (e) { if (e.target === this) closeForm(); });
  $(F.sched).addEventListener("input", function () { clearTimeout(previewTimer); previewTimer = setTimeout(previewNow, 350); });
  $(F.del).addEventListener("click", function () { if (editing) ask(editing, "delete"); });
  document.addEventListener("visibilitychange", function () { if (!document.hidden) { load(false); schedule(); } });

  var saved = get(FILTER_KEY);
  if (saved && ["all", "running", "failed", "paused"].indexOf(saved) >= 0) filter = saved;
  load(false).then(schedule);
})();
