/* llamawatch Approvals page.
   Waiting requests with Approve / Deny, the per-kind rules, the always-blocked
   list (built-in plus your own patterns), the audit trail with family chips and
   search, and the settings strip (requester token, notify hook, expiry).
   Polls every 5 s while something waits, 30 s otherwise. */
(function () {
  "use strict";
  var $ = function (s) { return document.querySelector(s); };
  var esc = function (s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); };
  var FAST_MS = 5000, SLOW_MS = 30000, CHIP_KEY = "lw-approvals-chip";
  var state = null, trail = [], chip = "all", query = "", timer = null, busy = {}, revokeArmed = false;

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
  function full(t) {
    var d = new Date(t * 1000);
    return ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][d.getDay()] + " " + d.getDate() + " " +
      ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][d.getMonth()] + " " + clock(t);
  }
  function ago(t) {
    if (!t) return "";
    var s = Math.max(0, now() - t);
    if (s < 60) return "just now";
    if (s < 3600) return Math.round(s / 60) + " min ago";
    if (s < 86400) return Math.round(s / 3600) + " h ago";
    if (s < 86400 * 7) return Math.round(s / 86400) + " d ago";
    return full(t);
  }
  function left(t) {
    var s = t - now();
    if (s <= 0) return "expiring";
    if (s < 60) return "under a minute left";
    if (s < 3600) return Math.round(s / 60) + " min left";
    return Math.round(s / 3600 * 10) / 10 + " h left";
  }

  // ── waiting cards ─────────────────────────────────────────────
  function renderWaiting() {
    var list = state.pending || [];
    $("#ap-waiting-count").textContent = list.length ? String(list.length) : "";
    $("#ap-waiting-empty").hidden = list.length > 0;
    $("#ap-waiting").innerHTML = list.map(function (r) {
      var meta = ['<span>asked by <b>' + esc(r.requester) + "</b></span>", "<span>" + esc(ago(r.created)) + "</span>"];
      meta.push('<span class="' + (r.expires - now() < 300 ? "warn" : "") + '">' + esc(left(r.expires)) + "</span>");
      if (r.client && r.client !== "127.0.0.1" && r.client !== "::1") meta.push("<span>from " + esc(r.client) + "</span>");
      if (r.target) meta.push("<span>on " + esc(r.target) + "</span>");
      var tick = r.kind === "agent" && r.command ?
        '<label class="ap-always"><input type="checkbox" data-always> allow this exact command next time</label>' : "";
      return '<li class="ap-req' + (busy[r.id] ? " busy" : "") + '" id="req-' + esc(r.id) + '" data-id="' + esc(r.id) + '">' +
        '<div class="ap-req-top"><span class="ap-req-title">' + esc(r.title) + '</span><span class="ap-kind">' + esc(r.kind_label) + "</span></div>" +
        '<div class="ap-req-meta">' + meta.join("") + "</div>" +
        (r.detail ? '<p class="ap-req-detail">' + esc(r.detail) + "</p>" : "") +
        (r.command ? '<code class="ap-cmd">' + esc(r.command) + "</code>" : "") +
        '<div class="ap-req-foot">' + tick +
        '<div class="rs-actions"><button type="button" class="rs-btn small danger" data-decide="deny"' + (busy[r.id] ? " disabled" : "") + ">Deny</button>" +
        '<button type="button" class="rs-btn small primary" data-decide="approve"' + (busy[r.id] ? " disabled" : "") + ">Approve</button></div></div></li>";
    }).join("");
  }

  async function decide(id, what, always) {
    if (busy[id]) return;
    busy[id] = true; renderWaiting();
    var r = await api("/api/approvals/" + encodeURIComponent(id) + "/" + what, what === "approve" ? { allow_always: !!always } : {});
    delete busy[id];
    if (r.ok) {
      toast(r.data.message || (what === "approve" ? "Approved" : "Denied"));
      if (window.LWLayout && LWLayout.badge) LWLayout.badge.refresh();
      load();
    } else {
      toast(r.data.error || r.data.message || ("Could not " + what + " (" + r.status + ")"), true);
      if (r.status === 409 || r.status === 404) load(); else renderWaiting();
    }
  }

  // ── rules ─────────────────────────────────────────────────────
  function renderRules() {
    $("#ap-rules").innerHTML = (state.kinds || []).map(function (k) {
      var cur = (state.rules || {})[k.id] || "allow";
      return '<div class="ap-rule" data-kind="' + esc(k.id) + '"><span class="ap-rule-name">' + esc(k.label) + "</span>" +
        '<span class="ap-rule-about">' + esc(k.about) + "</span>" +
        '<div class="ap-seg" role="group" aria-label="' + esc(k.label) + ' rule">' +
        ["allow", "ask", "block"].map(function (p) {
          return '<button type="button" data-policy="' + p + '" aria-pressed="' + (cur === p) + '">' + p[0].toUpperCase() + p.slice(1) + "</button>";
        }).join("") + "</div></div>";
    }).join("");
  }

  async function setRule(kind, policy) {
    var before = state.rules[kind];
    if (before === policy) return;
    state.rules[kind] = policy; renderRules();
    var body = {}; body[kind] = policy;
    var r = await api("/api/approvals/rules", body, "PUT");
    if (r.ok) { state.rules = r.data.rules || state.rules; toast(r.data.message || "Rules saved"); }
    else { state.rules[kind] = before; toast(r.data.error || "Could not save the rule", true); }
    renderRules();
  }

  // ── always blocked ────────────────────────────────────────────
  function renderBlocked() {
    var b = state.builtin_blocks || [];
    $("#ap-builtin-count").textContent = b.length ? "(" + b.length + ")" : "";
    $("#ap-builtin").innerHTML = b.map(function (t) { return "<li>" + esc(t) + "</li>"; }).join("");
    var pats = state.block_patterns || [];
    $("#ap-patterns").innerHTML = pats.map(function (p) {
      return '<li><code>' + esc(p) + '</code><button type="button" class="ap-x" data-remove="' + esc(p) + '" aria-label="Remove ' + esc(p) + '">×</button></li>';
    }).join("");
    var refusals = (state.recent || []).filter(function (r) { return r.status === "blocked"; }).slice(0, 8);
    $("#ap-refusals-empty").hidden = refusals.length > 0;
    $("#ap-refusals").innerHTML = refusals.map(function (r) {
      return '<li><span class="who" title="' + esc(r.command || r.title) + '">' + esc(r.requester) + ": " + esc(r.command || r.title) + "</span>" +
        '<span class="when">' + esc(ago(r.created)) + "</span>" +
        '<span class="why">' + esc(r.result) + "</span></li>";
    }).join("");
  }

  async function savePatterns(pats) {
    var r = await api("/api/approvals/blocklist", { patterns: pats }, "PUT");
    if (r.ok) { state.block_patterns = r.data.block_patterns || pats; toast(r.data.message || "Saved"); $("#ap-pattern-input").value = ""; }
    else toast(r.data.error || "Could not save the patterns", true);
    renderBlocked();
  }

  // ── audit trail ───────────────────────────────────────────────
  var FAMILY = [["all", "All"], ["approvals", "Approvals"], ["jobs", "Jobs"], ["services", "Services"],
                ["containers", "Containers"], ["quick", "Quick actions"], ["other", "Other"]];
  function family(a) {
    a = a || "";
    if (a.indexOf("approval") === 0 || a === "terminal_open") return "approvals";
    if (a.indexOf("job") === 0 || a.indexOf("timer") === 0) return "jobs";
    if (a.indexOf("service") === 0) return "services";
    if (a.indexOf("docker") === 0 || a.indexOf("container") === 0) return "containers";
    if (a.indexOf("quick") === 0) return "quick";
    return "other";
  }
  var VERBS = {
    approval_requested: "asked", approval_granted: "approved", approval_denied: "denied", approval_blocked: "blocked",
    approval_expired: "expired", approval_settings: "changed", approval_token: "token", approval_notify: "notify hook",
    terminal_open: "terminal opened", research_start: "research started", quick_action: "quick action"
  };
  function verb(a) { return VERBS[a] || String(a || "").replace(/_/g, " "); }
  function bad(o) { return /deny|denied|block|fail|error|expired/.test(String(o || "")); }

  function evMatches(e) {
    if (chip !== "all" && family(e.action) !== chip) return false;
    if (query) {
      var q = query.toLowerCase(), hay = Object.keys(e).map(function (k) { return k === "ts" ? "" : String(e[k]); }).join(" ").toLowerCase();
      if (hay.indexOf(q) < 0) return false;
    }
    return true;
  }

  function renderChips() {
    var counts = {};
    trail.forEach(function (e) { var f = family(e.action); counts[f] = (counts[f] || 0) + 1; counts.all = (counts.all || 0) + 1; });
    $("#ap-chips").innerHTML = FAMILY.map(function (f) {
      return '<button type="button" class="ap-chip" data-chip="' + f[0] + '" aria-pressed="' + (chip === f[0]) + '">' + esc(f[1]) + " <b>" + (counts[f[0]] || 0) + "</b></button>";
    }).join("");
  }

  function renderTrail() {
    var rows = trail.filter(evMatches);
    $("#ap-trail-count").textContent = rows.length === trail.length ? (trail.length + (trail.length === 1 ? " event" : " events")) : rows.length + " of " + trail.length;
    $("#ap-trail-empty").hidden = rows.length > 0;
    $("#ap-trail").innerHTML = rows.map(function (e) {
      var sub = [esc(e.actor || "")];
      if (e.outcome && e.outcome !== "ok") sub.push('<span class="outcome' + (bad(e.outcome) ? " bad" : "") + '">' + esc(e.outcome) + "</span>");
      if (e.requester && e.requester !== "dashboard") sub.push(esc(e.requester));
      if (e.reason) sub.push(esc(e.reason));
      if (e.changed) sub.push(esc(e.changed));
      if (e.added) sub.push("added " + esc(e.added));
      if (e.change) sub.push(esc(e.change));
      if (e.detail) sub.push(esc(e.detail));
      if (e.id && family(e.action) === "approvals") sub.push('<a href="#req-' + esc(e.id) + '" data-req="' + esc(e.id) + '">request ' + esc(e.id) + "</a>");
      return '<li class="ap-ev" data-id="' + esc(e.id || "") + '"><span class="dot ' + esc(e.outcome || "") + '"></span>' +
        '<span class="what"><span class="verb">' + esc(verb(e.action)) + "</span> " + esc(e.target || "") + "</span>" +
        '<span class="when" title="' + esc(full(e.ts || 0)) + '">' + esc(ago(e.ts)) + "</span>" +
        '<span class="sub">' + sub.join(" · ") + "</span></li>";
    }).join("");
  }

  function showRequest(id) {
    var card = document.getElementById("req-" + id);
    if (card) { card.scrollIntoView({ block: "center" }); card.classList.add("flash"); setTimeout(function () { card.classList.remove("flash"); }, 1500); return; }
    var r = (state.recent || []).find(function (x) { return x.id === id; });
    if (!r) { toast("That request is no longer kept"); return; }
    var who = r.decided_by ? " by " + r.decided_by : "";
    toast(r.title + ": " + r.status + who + (r.result ? ". " + r.result : ""), bad(r.status));
  }

  // ── settings ──────────────────────────────────────────────────
  function renderSettings() {
    $("#ap-local-note").hidden = !state.local_only;
    $("#ap-token-state").textContent = state.token_set ? "A token is set. Making a new one replaces it." : "No token set. Local callers do not need one.";
    $("#ap-token-revoke").hidden = !state.token_set;
    if (!revokeArmed) $("#ap-token-revoke").textContent = "Revoke";
    if (document.activeElement !== $("#ap-notify")) $("#ap-notify").value = state.notify_command || "";
    if (document.activeElement !== $("#ap-ttl")) $("#ap-ttl").value = Math.round((state.ttl || 1800) / 60);
  }

  async function newToken() {
    var r = await api("/api/approvals/token", {});
    if (!r.ok) { toast(r.data.error || "Could not make a token", true); return; }
    $("#ap-token-value").textContent = r.data.token || "";
    $("#ap-token-show").hidden = false;
    state.token_set = true; renderSettings();
    toast(r.data.message || "New token made");
  }

  async function revokeToken() {
    if (!revokeArmed) { revokeArmed = true; $("#ap-token-revoke").textContent = "Revoke, sure?"; setTimeout(function () { revokeArmed = false; renderSettings(); }, 4000); return; }
    revokeArmed = false;
    var r = await api("/api/approvals/token", undefined, "DELETE");
    if (r.ok) { state.token_set = false; $("#ap-token-show").hidden = true; toast(r.data.message || "Token revoked"); }
    else toast(r.data.error || "Could not revoke the token", true);
    renderSettings();
  }

  async function saveSettings(patch) {
    var r = await api("/api/approvals/settings", patch, "PUT");
    if (r.ok) { state.notify_command = r.data.notify_command; state.ttl = r.data.ttl; toast(r.data.message || "Settings saved"); $("#ap-err").textContent = ""; }
    else $("#ap-err").textContent = r.data.error || "Could not save";
    renderSettings();
  }

  // ── load / poll ───────────────────────────────────────────────
  function render() { renderWaiting(); renderRules(); renderBlocked(); renderChips(); renderTrail(); renderSettings(); }

  async function load() {
    clearTimeout(timer);
    try {
      var rs = await Promise.all([api("/api/approvals"), api("/api/audit?limit=200")]);
      if (!rs[0].ok) throw new Error(rs[0].data.error || ("HTTP " + rs[0].status));
      state = rs[0].data;
      trail = rs[1].ok && Array.isArray(rs[1].data.events) ? rs[1].data.events : [];
      render();
      $("#ap-updated").textContent = "updated " + clock(now());
      $("#ap-err").textContent = "";
    } catch (e) {
      $("#ap-err").textContent = "Could not load approvals: " + (e.message || e);
    }
    timer = setTimeout(load, state && state.pending && state.pending.length ? FAST_MS : SLOW_MS);
  }

  var toastTimer = null;
  function toast(msg, isBad) {
    var el = $("#ap-toast"); el.textContent = msg; el.classList.toggle("bad", !!isBad); el.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(function () { el.hidden = true; }, 4000);
  }

  // ── events ────────────────────────────────────────────────────
  $("#ap-waiting").addEventListener("click", function (e) {
    var b = e.target.closest("[data-decide]"); if (!b) return;
    var card = b.closest(".ap-req"), always = card.querySelector("[data-always]");
    decide(card.getAttribute("data-id"), b.getAttribute("data-decide"), always && always.checked);
  });
  $("#ap-rules").addEventListener("click", function (e) {
    var b = e.target.closest("[data-policy]"); if (!b) return;
    setRule(b.closest(".ap-rule").getAttribute("data-kind"), b.getAttribute("data-policy"));
  });
  $("#ap-pattern-form").addEventListener("submit", function (e) {
    e.preventDefault();
    var v = $("#ap-pattern-input").value.trim(); if (!v) return;
    if ((state.block_patterns || []).indexOf(v) >= 0) { toast("That pattern is already listed"); return; }
    savePatterns((state.block_patterns || []).concat([v]));
  });
  $("#ap-patterns").addEventListener("click", function (e) {
    var b = e.target.closest("[data-remove]"); if (!b) return;
    var v = b.getAttribute("data-remove");
    savePatterns((state.block_patterns || []).filter(function (p) { return p !== v; }));
  });
  $("#ap-chips").addEventListener("click", function (e) {
    var b = e.target.closest("[data-chip]"); if (!b) return;
    chip = b.getAttribute("data-chip"); set(CHIP_KEY, chip); renderChips(); renderTrail();
  });
  $("#ap-search").addEventListener("input", function () { query = this.value.trim(); renderTrail(); });
  $("#ap-trail").addEventListener("click", function (e) {
    var a = e.target.closest("[data-req]"); if (!a) return;
    e.preventDefault(); showRequest(a.getAttribute("data-req"));
  });
  $("#ap-token-new").addEventListener("click", newToken);
  $("#ap-token-revoke").addEventListener("click", revokeToken);
  $("#ap-token-copy").addEventListener("click", function () {
    var t = $("#ap-token-value").textContent;
    var done = function () { toast("Token copied"); }, fail = function () { toast("Select the token and copy it by hand", true); };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(t).then(done, fail); else fail();
  });
  $("#ap-notify-form").addEventListener("submit", function (e) { e.preventDefault(); saveSettings({ notify_command: $("#ap-notify").value.trim() }); });
  $("#ap-ttl-form").addEventListener("submit", function (e) {
    e.preventDefault();
    var m = Number($("#ap-ttl").value);
    if (!(m >= 1 && m <= 1440)) { $("#ap-err").textContent = "Expiry must be between 1 and 1440 minutes"; return; }
    saveSettings({ ttl: Math.round(m * 60) });
  });
  document.addEventListener("visibilitychange", function () { if (!document.hidden) load(); });

  chip = get(CHIP_KEY) || "all";
  if (!FAMILY.some(function (f) { return f[0] === chip; })) chip = "all";
  load();
})();
