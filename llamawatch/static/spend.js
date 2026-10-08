/* llamawatch Spend page.
   This month's paid API money and tokens as two dials, free local tokens with
   their share, stacked bars for the last 30 days by provider or by job, totals
   tables, and the price strip. Everything shown comes from /api/spend; the page
   never reads the clock itself, so a frozen or odd clock cannot skew it.
   Polls every 60 s. */
(function () {
  "use strict";
  var $ = function (s) { return document.querySelector(s); };
  var esc = function (s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); };
  var COLORS = ["#2dd4bf", "#60a5fa", "#a78bfa", "#f472b6", "#fb923c", "#fbbf24", "#34d399", "#22d3ee"];
  var OTHER = "#4b5473", POLL_MS = 60000, BY_KEY = "lw-spend-by", RING = 2 * Math.PI * 52;
  var data = null, by = "source", timer = null, colours = {};

  function get(k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } }
  function set(k, v) { try { window.localStorage.setItem(k, v); } catch (e) {} }

  async function api(path, body, method) {
    var opt = body === undefined && !method ? {} : { method: method || "POST", headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) };
    var r = await fetch(path, opt);
    var d = await r.json().catch(function () { return {}; });
    return { ok: r.ok, status: r.status, data: d };
  }

  // ── numbers ───────────────────────────────────────────────────
  function tok(n) {
    n = Number(n) || 0;
    if (n >= 1e9) return (n / 1e9).toFixed(2) + "B";
    if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 1 : 2) + "M";
    if (n >= 1e3) return (n / 1e3).toFixed(n >= 1e4 ? 0 : 1) + "K";
    return String(Math.round(n));
  }
  function money(v, cur) {
    if (v == null) return "";
    v = Number(v) || 0;
    var s = v >= 100 ? v.toFixed(0) : v >= 1 ? v.toFixed(2) : v > 0 ? v.toFixed(3).replace(/0$/, "") : "0.00";
    return (cur || "$") + s;
  }
  function pct(f) { return Math.round((Number(f) || 0) * 100) + "%"; }
  function dayShort(iso) {
    // "2026-01-14" -> "14 Jan"; no Date object, so the clock does not matter.
    var m = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    return parseInt(iso.slice(8, 10), 10) + " " + m[parseInt(iso.slice(5, 7), 10) - 1];
  }
  function weekend(iso) {
    // Zeller-free: day-of-week from the civil date, UTC.
    var y = +iso.slice(0, 4), mo = +iso.slice(5, 7), d = +iso.slice(8, 10);
    var t = [0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4];
    if (mo < 3) y -= 1;
    var dow = (y + Math.floor(y / 4) - Math.floor(y / 100) + Math.floor(y / 400) + t[mo - 1] + d) % 7;
    return dow === 0 || dow === 6;
  }
  function colourOf(name) {
    if (colours[name]) return colours[name];
    if (name.indexOf("Other") === 0) return (colours[name] = OTHER);
    var used = Object.keys(colours).length;
    return (colours[name] = COLORS[used % COLORS.length]);
  }

  // ── month cards ───────────────────────────────────────────────
  function ring(id, frac) {
    var el = document.getElementById(id);
    var f = Math.max(0, Math.min(1, Number(frac) || 0));
    el.style.strokeDasharray = (RING * f).toFixed(1) + " " + RING.toFixed(1);
  }
  function renderMonth() {
    var m = data.month || {}, loc = data.local || {};
    $("#sp-month-label").textContent = m.label || "";
    $("#sp-money").textContent = money(m.cost, m.currency);
    var extra = (m.extra || []).map(function (e) { return money(e.cost, e.currency); }).join(" + ");
    $("#sp-money-sub").textContent = extra ? "spent, plus " + extra : "spent";
    ring("sp-ring-money", m.days_in_month ? m.day_of_month / m.days_in_month : 0);
    $("#sp-tokens").textContent = tok(m.tokens);
    $("#sp-tokens-sub").textContent = m.requests ? "paid tokens, " + tok(m.requests) + " requests" : "paid tokens";
    var all = (Number(m.tokens) || 0) + (Number(loc.tokens) || 0);
    ring("sp-ring-tokens", all ? m.tokens / all : 0);
    var priced = (data.sources || []).filter(function (s) { return s.kind !== "local" && !s.priced && s.tokens; });
    $("#sp-month-note").textContent = priced.length
      ? "No price set for " + priced.map(function (s) { return s.name; }).slice(0, 3).join(", ") + (priced.length > 3 ? " and more" : "") + ", so their tokens are not counted here. Add one below if they cost money."
      : "Day " + (m.day_of_month || 0) + " of " + (m.days_in_month || 0) + ". The ring is how far through the month we are.";
    $("#sp-local").textContent = tok(loc.tokens);
    var share = loc.share;
    $("#sp-share-fill").style.width = share == null ? "0%" : pct(share);
    $("#sp-share-text").textContent = share == null ? "Nothing recorded this month yet."
      : pct(share) + " of all tokens this month ran locally, for nothing.";
  }

  // ── stacked bars ──────────────────────────────────────────────
  function stackKeys() {
    // Top names by total across the window; the rest fold into "Other".
    var totals = {};
    (data.days || []).forEach(function (d) {
      var rows = by === "job" ? d.jobs : d.sources;
      Object.keys(rows || {}).forEach(function (k) { totals[k] = (totals[k] || 0) + (rows[k].tokens || 0); });
    });
    var names = Object.keys(totals).sort(function (a, b) { return totals[b] - totals[a]; });
    var keep = names.slice(0, 6), rest = names.slice(6);
    return { keep: keep, rest: rest };
  }
  function renderChart() {
    var chart = $("#sp-chart"), legend = $("#sp-legend"), days = data.days || [];
    var ks = stackKeys(), keep = ks.keep, rest = ks.rest;
    var any = days.some(function (d) { var r = by === "job" ? d.jobs : d.sources; return Object.keys(r || {}).some(function (k) { return r[k].tokens; }); });
    $("#sp-chart-empty").hidden = any;
    chart.hidden = !any; legend.hidden = !any;
    if (!any) { chart.innerHTML = ""; legend.innerHTML = ""; return; }
    var max = 0;
    days.forEach(function (d) { var r = by === "job" ? d.jobs : d.sources, t = 0; Object.keys(r || {}).forEach(function (k) { t += r[k].tokens || 0; }); if (t > max) max = t; });
    var html = days.map(function (d) {
      var r = by === "job" ? d.jobs : d.sources, total = 0, segs = "", other = 0, otherCost = null;
      keep.forEach(function (k) {
        var v = r && r[k] ? r[k].tokens || 0 : 0; total += v;
        if (v) segs += '<div class="sp-seg-bar" style="height:' + (v / max * 100).toFixed(2) + '%;background:' + colourOf(k) + '"></div>';
      });
      rest.forEach(function (k) { if (r && r[k]) { other += r[k].tokens || 0; if (r[k].cost != null) otherCost = (otherCost || 0) + r[k].cost; } });
      if (other) { total += other; segs += '<div class="sp-seg-bar" style="height:' + (other / max * 100).toFixed(2) + '%;background:' + OTHER + '"></div>'; }
      var showLabel = days.length <= 10 || (parseInt(d.date.slice(8, 10), 10) % 5 === 0) || d === days[days.length - 1];
      return '<div class="sp-day' + (weekend(d.date) ? " weekend" : "") + '" tabindex="0" data-date="' + d.date + '" aria-label="' + esc(dayShort(d.date) + ": " + tok(total) + " tokens") + '">' +
        segs + (showLabel ? '<span class="sp-day-label">' + dayShort(d.date) + "</span>" : "") + "</div>";
    }).join("");
    chart.innerHTML = html;
    legend.innerHTML = keep.map(function (k) { return "<span><i style=\"background:" + colourOf(k) + '"></i>' + esc(k) + "</span>"; }).join("") +
      (rest.length ? '<span><i style="background:' + OTHER + '"></i>Other (' + rest.length + ")</span>" : "");
  }
  function tipFor(date) {
    var d = (data.days || []).filter(function (x) { return x.date === date; })[0];
    if (!d) return "";
    var r = (by === "job" ? d.jobs : d.sources) || {};
    var names = Object.keys(r).filter(function (k) { return r[k].tokens; }).sort(function (a, b) { return r[b].tokens - r[a].tokens; });
    var total = 0, cost = 0, cur = null;
    names.forEach(function (k) { total += r[k].tokens; if (r[k].cost != null) { cost += r[k].cost; cur = cur || curFor(k); } });
    var head = "<b>" + esc(dayShort(date)) + " · " + tok(total) + " tokens" + (cost ? " · " + money(cost, cur) : "") + "</b>";
    if (!names.length) return head + "<div><span>Nothing recorded</span><span></span></div>";
    return head + names.slice(0, 8).map(function (k) {
      return "<div><span>" + esc(k) + "</span><span>" + tok(r[k].tokens) + (r[k].cost != null ? " · " + money(r[k].cost, curFor(k)) : "") + "</span></div>";
    }).join("") + (names.length > 8 ? "<div><span>and " + (names.length - 8) + " more</span><span></span></div>" : "");
  }
  function curFor(name) {
    var s = (data.sources || []).filter(function (x) { return x.name === name; })[0];
    if (s && s.currency) return s.currency;
    var j = (data.jobs || []).filter(function (x) { return x.label === name; })[0];
    return (j && j.currency) || (data.month && data.month.currency) || "$";
  }
  function showTip(el) {
    var tip = $("#sp-tip"), card = $(".sp-chart-card");
    tip.innerHTML = tipFor(el.getAttribute("data-date")); tip.hidden = false;
    var cr = card.getBoundingClientRect(), er = el.getBoundingClientRect();
    var left = er.left - cr.left + er.width / 2 - tip.offsetWidth / 2;
    left = Math.max(8, Math.min(cr.width - tip.offsetWidth - 8, left));
    tip.style.left = left + "px";
    tip.style.top = Math.max(8, er.top - cr.top - tip.offsetHeight - 8) + "px";
  }
  function hideTip() { $("#sp-tip").hidden = true; }

  // ── tables ────────────────────────────────────────────────────
  function costCell(v, cur, kind, priced) {
    if (kind === "local") return '<td class="num cost free">free</td>';
    if (v == null) return '<td class="num cost none">' + (priced === false ? "no price" : "–") + "</td>";
    return '<td class="num cost">' + esc(money(v, cur)) + "</td>";
  }
  function renderTables() {
    var src = data.sources || [], jobs = data.jobs || [];
    $("#sp-sources tbody").innerHTML = src.length ? src.map(function (s) {
      return "<tr><td class=\"name\" title=\"" + esc(s.name) + '"><i class="sp-swatch" style="background:' + colourOf(s.name) + '"></i>' + esc(s.name) + "</td>" +
        '<td class="kind">' + esc(s.origin ? s.origin : s.kind === "local" ? "local" : s.kind === "api" ? "API" : "tool") + "</td>" +
        '<td class="num">' + (s.requests ? tok(s.requests) : "–") + "</td><td class=\"num\">" + tok(s.tokens) + "</td>" +
        costCell(s.cost, s.currency, s.kind, s.priced) + "</tr>";
    }).join("") : '<tr><td colspan="5" class="kind">Nothing recorded yet.</td></tr>';
    $("#sp-jobs tbody").innerHTML = jobs.map(function (j) {
      var s = src.filter(function (x) { return x.name === j.source; })[0] || {};
      return "<tr><td class=\"name\" title=\"" + esc(j.label) + '"><i class="sp-swatch" style="background:' + colourOf(j.label) + '"></i>' + esc(j.label) + "</td>" +
        '<td class="src">' + esc(j.source) + "</td><td class=\"num\">" + (j.requests ? tok(j.requests) : "–") + "</td><td class=\"num\">" + tok(j.tokens) + "</td>" +
        costCell(j.cost, j.currency || s.currency, s.kind, s.priced) + "</tr>";
    }).join("");
    $("#sp-jobs-empty").hidden = jobs.length > 0;
    $("#sp-jobs").hidden = !jobs.length;
    document.querySelectorAll(".sp-days-n, #sp-days-n").forEach(function (el) { el.textContent = data.days_n || 30; });
  }

  // ── prices ────────────────────────────────────────────────────
  function renderPrices() {
    var book = data.prices || {}, rows = {};
    (data.sources || []).forEach(function (s) { if (s.kind !== "local" && !s.origin) rows[s.name] = true; });
    Object.keys(book).forEach(function (n) { rows[n] = true; });
    var names = Object.keys(rows).sort();
    $("#sp-price-table tbody").innerHTML = names.length ? names.map(function (n) {
      var p = book[n] || {}, ro = p.from === "research";
      var inp = function (k, cls) { return '<input type="' + (k === "currency" ? "text" : "number") + '" class="' + (cls || "") + '" step="any" min="0" data-name="' + esc(n) + '" data-k="' + k + '" value="' + esc(p[k] != null && (k === "currency" || p[k]) ? p[k] : "") + '"' + (ro ? " disabled" : "") + (k === "currency" ? ' maxlength="3" placeholder="$"' : ' placeholder="0"') + ">"; };
      return "<tr><td class=\"name\" title=\"" + esc(n) + '">' + esc(n) + (ro ? ' <span class="from">set on Research page</span>' : "") + "</td>" +
        '<td class="num">' + inp("in") + '</td><td class="num">' + inp("out") + '</td><td class="num">' + inp("cache") + "</td><td>" + inp("currency", "cur") + "</td>" +
        "<td>" + (ro ? "" : '<button type="button" class="rs-btn small" data-save="' + esc(n) + '">Save</button>') + "</td></tr>";
    }).join("") : '<tr><td colspan="6" class="kind">No paid sources seen yet.</td></tr>';
  }
  async function savePrice(name) {
    var ins = document.querySelectorAll('#sp-price-table input[data-name="' + CSS.escape(name) + '"]'), p = {};
    ins.forEach(function (i) { p[i.getAttribute("data-k")] = i.value; });
    var empty = !(Number(p["in"]) || Number(p.out) || Number(p.cache));
    var body = {}; body[name] = empty ? null : { "in": p["in"], out: p.out, cache: p.cache, currency: p.currency || "$" };
    var r = await api("/api/spend/prices", { prices: body }, "PUT");
    if (r.ok) { $("#sp-err").textContent = ""; toast(empty ? "Price cleared for " + name : "Price saved for " + name); await load(); }
    else { $("#sp-err").textContent = r.data.error || ("Could not save (" + r.status + ")"); toast(r.data.error || "Could not save the price", true); }
  }

  // ── connected providers ───────────────────────────────────────
  var provs = [], openForm = null;
  function provState(p) {
    if (!p.connected) return '<span class="sp-hint">Not connected. Needs: ' + esc(p.key) + (p.extra.length ? " and " + esc(p.extra.join(", ").replace(/_/g, " ")) : "") + "</span>";
    if (p.error) return '<span class="sp-prov-bad">Last check failed: ' + esc(p.error) + "</span>";
    return '<span class="sp-prov-ok">Connected' + (p.balance ? ", balance " + esc(money(p.balance.amount, p.balance.currency)) : "") + "</span>";
  }
  function provForm(p) {
    var f = '<form class="sp-prov-form" data-id="' + esc(p.id) + '"><label>' + esc(p.key) + '<input type="password" name="api_key" autocomplete="off" required></label>';
    p.extra.forEach(function (x) { f += "<label>" + esc(x.replace(/_/g, " ")) + '<input type="text" name="' + esc(x) + '" autocomplete="off" required></label>'; });
    return f + '<button type="submit" class="rs-btn small">Test and save</button> <button type="button" class="rs-btn small" data-cancel>Cancel</button></form>';
  }
  function renderProviders() {
    $("#sp-prov-list").innerHTML = provs.map(function (p) {
      var btns = p.connected
        ? '<button type="button" class="rs-btn small" data-refresh="' + esc(p.id) + '">Check now</button> <button type="button" class="rs-btn small" data-off="' + esc(p.id) + '">Disconnect</button>'
        : '<button type="button" class="rs-btn small" data-open="' + esc(p.id) + '">Connect</button>';
      return '<li class="sp-prov"><div class="sp-prov-head"><div><strong>' + esc(p.label) + '</strong> <span class="sp-hint">' + esc(p.origin) + ": " + esc(p.detail) + "</span>" + provState(p) + "</div><div class=\"sp-prov-btns\">" + btns + "</div></div>" +
        (openForm === p.id ? provForm(p) : "") + "</li>";
    }).join("");
  }
  async function loadProviders() {
    var r = await api("/api/spend/providers");
    if (r.ok) { provs = r.data.providers || []; renderProviders(); }
  }
  async function provCall(path, method, body, okMsg) {
    var r = await api(path, body, method);
    if (r.ok) { $("#sp-prov-err").textContent = ""; provs = r.data.providers || provs; openForm = null; renderProviders(); toast(okMsg); await load(); }
    else { $("#sp-prov-err").textContent = r.data.error || ("Could not do that (" + r.status + ")"); toast(r.data.error || "Could not do that", true); }
  }

  // ── toast ─────────────────────────────────────────────────────
  var toastTimer = null;
  function toast(msg, isBad) {
    var el = $("#sp-toast"); el.textContent = msg; el.classList.toggle("bad", !!isBad); el.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(function () { el.hidden = true; }, 4000);
  }

  // ── load ──────────────────────────────────────────────────────
  function render() { renderMonth(); renderChart(); renderTables(); renderPrices(); }
  async function load() {
    var r = await api("/api/spend?days=30");
    if (!r.ok) { $("#sp-updated").textContent = "Could not load (" + r.status + ")"; return; }
    data = r.data; colours = {};
    render();
    $("#sp-updated").textContent = "ledger up to date";
  }
  function schedule() { clearTimeout(timer); timer = setTimeout(async function () { await load(); schedule(); }, POLL_MS); }

  document.addEventListener("DOMContentLoaded", function () {
    by = get(BY_KEY) === "job" ? "job" : "source";
    document.querySelectorAll(".sp-seg button").forEach(function (b) {
      b.setAttribute("aria-pressed", b.getAttribute("data-by") === by ? "true" : "false");
      b.addEventListener("click", function () {
        by = b.getAttribute("data-by"); set(BY_KEY, by);
        document.querySelectorAll(".sp-seg button").forEach(function (x) { x.setAttribute("aria-pressed", x === b ? "true" : "false"); });
        hideTip(); if (data) renderChart();
      });
    });
    var chart = $("#sp-chart");
    chart.addEventListener("mouseover", function (e) { var d = e.target.closest(".sp-day"); if (d) showTip(d); });
    chart.addEventListener("mouseleave", hideTip);
    chart.addEventListener("focusin", function (e) { var d = e.target.closest(".sp-day"); if (d) showTip(d); });
    chart.addEventListener("focusout", hideTip);
    chart.addEventListener("click", function (e) { var d = e.target.closest(".sp-day"); if (d) { if ($("#sp-tip").hidden || $("#sp-tip").getAttribute("data-for") !== d.getAttribute("data-date")) { showTip(d); $("#sp-tip").setAttribute("data-for", d.getAttribute("data-date")); } else hideTip(); } });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") hideTip(); });
    $("#sp-price-table").addEventListener("click", function (e) { var b = e.target.closest("[data-save]"); if (b) savePrice(b.getAttribute("data-save")); });
    $("#sp-price-table").addEventListener("keydown", function (e) { if (e.key === "Enter" && e.target.matches("input[data-name]")) { e.preventDefault(); savePrice(e.target.getAttribute("data-name")); } });
    document.addEventListener("visibilitychange", function () { if (!document.hidden) { load(); schedule(); } else clearTimeout(timer); });
    var pl = $("#sp-prov-list");
    pl.addEventListener("click", function (e) {
      var b = e.target.closest("button"); if (!b) return;
      if (b.hasAttribute("data-open")) { openForm = b.getAttribute("data-open"); renderProviders(); var i = pl.querySelector("input"); if (i) i.focus(); }
      else if (b.hasAttribute("data-cancel")) { openForm = null; renderProviders(); }
      else if (b.hasAttribute("data-off")) { if (window.confirm("Disconnect and forget this provider's stored history?")) provCall("/api/spend/providers/" + encodeURIComponent(b.getAttribute("data-off")), "DELETE", undefined, "Disconnected"); }
      else if (b.hasAttribute("data-refresh")) provCall("/api/spend/providers/" + encodeURIComponent(b.getAttribute("data-refresh")) + "/refresh", "POST", undefined, "Checked");
    });
    pl.addEventListener("submit", function (e) {
      e.preventDefault();
      var f = e.target, body = {};
      f.querySelectorAll("input").forEach(function (i) { body[i.name] = i.value.trim(); });
      provCall("/api/spend/providers/" + encodeURIComponent(f.getAttribute("data-id")), "PUT", body, "Connected");
    });
    loadProviders();
    load().then(schedule);
  });
})();
