/* Wide-screen extras (>= 1700px): fill empty space with more information.
   Read-only mirrors of data the dashboard already loads. The prediction list
   also shows on laptops (>= 1025px); the rest is display:none below 1700px. */
(function () {
  "use strict";
  var esc = function (s) { var d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; };
  var $ = function (id) { return document.getElementById(id); };

  function mk(tag, id, cls, html) {
    var el = document.createElement(tag);
    if (id) el.id = id;
    el.className = "wide-only " + (cls || "");
    if (html) el.innerHTML = html;
    return el;
  }
  function watch(src, fn) {
    if (!src) return;
    var t = null;
    new MutationObserver(function () { clearTimeout(t); t = setTimeout(fn, 150); })
      .observe(src, { childList: true, subtree: true, characterData: true });
    fn();
  }

  function init() {
    // ── Command page: the real container panel moves here on wide screens ───
    var gg = $("gauges-grid"), dock = $("docker-panel"), row0 = $("sys-row-1");
    if (gg && dock && row0) {
      var slot = mk("div", "wide-docker", "wide-panel");
      gg.parentNode.insertBefore(slot, gg.nextSibling);
      var mq = window.matchMedia("(min-width: 1700px)");
      var place = function () {
        if (mq.matches) slot.appendChild(dock);          // one panel, buttons still work
        else row0.insertBefore(dock, row0.firstChild);   // back on the System page
      };
      (mq.addEventListener ? mq.addEventListener("change", place) : mq.addListener(place));
      place();
    }

    // ── System page: past temperatures (highs, lows, averages) ───────────────
    var sysRegion = $("system-region");
    if (sysRegion) {
      var COL = { cpu: "#22d3ee", gpu: "#f472b6", nvme: "#a78bfa", network: "#fbbf24" };
      var NAME = { cpu: "CPU", gpu: "GPU", nvme: "NVMe", network: "Network chip" };
      var hp = mk("div", "wide-history", "wide-panel",
        '<div class="wh-head"><span class="panel-title" style="padding:0">HISTORY · TEMPERATURES</span>' +
        '<span id="wh-note" class="wh-note"></span>' +
        '<span class="wh-ranges"><button data-r="24h" class="know-toggle-btn know-toggle-active">24h</button>' +
        '<button data-r="3d" class="know-toggle-btn">3d</button><button data-r="7d" class="know-toggle-btn">7d</button>' +
        '<button data-r="30d" class="know-toggle-btn">30d</button></span></div>' +
        '<div id="wh-tiles"></div>' +
        '<div id="wh-chart"><div id="wh-y"></div><svg id="wh-svg" viewBox="0 0 1000 200" preserveAspectRatio="none"></svg></div>' +
        '<div id="wh-x"></div>');
      sysRegion.appendChild(hp);
      var fmt = function (iso) {
        var d = new Date(iso);
        return d.toLocaleDateString("en-GB", { weekday: "short" }) + " " + d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
      };
      var cur = "24h";
      var draw = function (d) {
        var tiles = $("wh-tiles"), svg = $("wh-svg");
        if (!d || d.disabled || !d.summary || !Object.keys(d.summary).length) {
          tiles.innerHTML = '<div class="wide-empty">No temperature history found yet</div>'; svg.innerHTML = ""; return;
        }
        tiles.innerHTML = Object.keys(COL).filter(function (k) { return d.summary[k]; }).map(function (k) {
          var m = d.summary[k];
          return '<div class="wh-tile" style="border-top-color:' + COL[k] + '"><div class="wh-name" style="color:' + COL[k] + '">' + NAME[k] + '</div>' +
            '<div class="wh-hi">' + Math.round(m.max) + '°<small> high · ' + esc(fmt(m.max_ts)) + '</small></div>' +
            '<div class="wh-row"><span>low</span><b>' + Math.round(m.min) + '° · ' + esc(fmt(m.min_ts)) + '</b></div>' +
            '<div class="wh-row"><span>average</span><b>' + Math.round(m.avg) + '°</b></div>' +
            '<div class="wh-row"><span>above ' + Math.round(d.hot_limit) + '°</span><b>' + m.hot_pct + '% of the time</b></div></div>';
        }).join("");
        // scale to the data, so the lines use the height; show the hot line only when temps get near it
        var lo = Infinity, hi = -Infinity;
        Object.keys(COL).forEach(function (k) { (d.series[k] || []).forEach(function (p) { if (p) { lo = Math.min(lo, p.avg); hi = Math.max(hi, p.avg); } }); });
        if (!isFinite(lo)) { lo = 20; hi = 100; }
        var Y0 = Math.floor((lo - 3) / 10) * 10, Y1 = Math.ceil((hi + 3) / 10) * 10;
        var peak = Math.max.apply(null, Object.keys(d.summary).map(function (k) { return d.summary[k].max; }));
        var showHot = peak >= d.hot_limit - 10;
        if (showHot) Y1 = Math.max(Y1, Math.ceil((d.hot_limit + 5) / 10) * 10);
        var step = Y1 - Y0 > 50 ? 20 : 10, ticks = [];
        for (var t = Y1; t >= Y0; t -= step) ticks.push(t);
        var W = 1000, H = 200, n = d.buckets;
        var X = function (i) { return (i + 0.5) / n * W; };
        var Y = function (v) { return H - (Math.min(Y1, Math.max(Y0, v)) - Y0) / (Y1 - Y0) * H; };
        var out = "";
        ticks.slice(1, -1).forEach(function (g) { out += '<line x1="0" x2="' + W + '" y1="' + Y(g) + '" y2="' + Y(g) + '" stroke="rgba(255,255,255,.07)" vector-effect="non-scaling-stroke"/>'; });
        if (showHot) out += '<line x1="0" x2="' + W + '" y1="' + Y(d.hot_limit) + '" y2="' + Y(d.hot_limit) + '" stroke="rgba(248,113,113,.55)" stroke-dasharray="6 5" vector-effect="non-scaling-stroke"/>';
        ["network", "nvme", "gpu", "cpu"].forEach(function (k) {
          var pts = (d.series[k] || []).map(function (p, i) { return p && { x: X(i), mn: p.min, mx: p.max, av: p.avg }; }).filter(Boolean);
          if (pts.length < 2) return;
          out += '<polyline points="' + pts.map(function (p) { return p.x + "," + Y(p.av); }).join(" ") + '" fill="none" stroke="' + COL[k] + '" stroke-width="1.6" vector-effect="non-scaling-stroke"/>';
        });
        svg.innerHTML = out;
        $("wh-y").innerHTML = ticks.map(function (v) { return '<span style="top:' + ((Y1 - v) / (Y1 - Y0) * 100) + '%">' + v + '°</span>'; }).join("");
        var t0 = new Date(d.start).getTime(), t1 = new Date(d.end).getTime();
        $("wh-x").innerHTML = [0, 0.25, 0.5, 0.75, 1].map(function (f) { return "<span>" + esc(fmt(new Date(t0 + (t1 - t0) * f).toISOString())) + "</span>"; }).join("");
        var cf = d.covers_from && new Date(d.covers_from).getTime();
        $("wh-note").textContent = cf && cf > t0 + 3600e3 ? "data starts " + fmt(d.covers_from) : "line = average, " + (showHot ? "dashed = " : "all under ") + Math.round(d.hot_limit) + "°";
      };
      var loadH = function () {
        fetch("/api/history?range=" + cur).then(function (r) { return r.json(); }).then(draw).catch(function () {});
      };
      hp.querySelectorAll(".wh-ranges button").forEach(function (b) {
        b.addEventListener("click", function () {
          cur = b.dataset.r;
          hp.querySelectorAll(".wh-ranges button").forEach(function (x) { x.classList.toggle("know-toggle-active", x === b); });
          loadH();
        });
      });
      loadH();
      setInterval(loadH, 120000);
    }

    // ── Knowledge page: prediction list under the map ────────────────────────
    var pred = $("intel-predictions");
    if (pred) {
      var list = mk("div", "wide-preds", "wide-panel");
      pred.appendChild(list);
      var load = function () {
        fetch("/api/predictions?limit=200").then(function (r) { return r.json(); }).then(function (d) {
          unwatchRows();
          var ps = (d.predictions || []).slice().sort(function (a, b) { return (b.confidence || 0) - (a.confidence || 0); });
          list.innerHTML = '<div class="col-title">All predictions · ' + ps.length + '</div>' + ps.slice(0, 40).map(function (p) {
            var conf = p.confidence != null ? Math.round(p.confidence * 100) + "%" : "—";
            var tf = p.timeframe ? String(p.timeframe).slice(0, 10) : "no deadline";
            var st = p.verified == null ? "pending" : "verified";
            return '<div class="wide-pred-row"><b>' + esc(conf) + '</b><span>' + esc(p.text) + '</span><em>' + esc(p.domain || "general") + ' · ' + esc(tf) + ' · ' + st + '</em></div>';
          }).join("");
          watchRows();
          fitWhole();
        }).catch(function () {});
      };
      // end the list on a whole row; the rest scrolls
      var fitWhole = function () {
        list.style.maxHeight = "";
        if (list.scrollHeight <= list.clientHeight + 1) return;
        var top = list.getBoundingClientRect().top, bottom = top + list.clientHeight, last = 0;
        list.querySelectorAll(".wide-pred-row").forEach(function (r) { var b = r.getBoundingClientRect().bottom; if (b <= bottom + 1) last = b; });
        if (last) list.style.maxHeight = Math.floor(last - top) + "px";
      };
      // rows can change height on their own (a late web font), so watch them too
      var ro = window.ResizeObserver && new ResizeObserver(function () { requestAnimationFrame(fitWhole); });
      if (ro) { ro.observe(pred); ro.observe(list); }
      var unwatchRows = function () { if (ro) list.querySelectorAll(".wide-pred-row").forEach(function (r) { ro.unobserve(r); }); };
      var watchRows = function () { if (ro) list.querySelectorAll(".wide-pred-row").forEach(function (r) { ro.observe(r); }); };
      load();
      setInterval(load, 120000);
    }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
