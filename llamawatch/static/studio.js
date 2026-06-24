/* llamawatch — Studio Command Surface  v075
 * 3-page carousel: Command / System / Knowledge.
 * Live data via EventSource('/sse') → widget events.
 * No build step. Vanilla ES2020+.
 */
(function () {
  "use strict";

  // ── Panel Visibility ───────────────────────────────────────────────────────
  const VIEW_NAME_MAP = { command: 0, system: 1, knowledge: 2 };
  let _enabledViewIndices = [0, 1, 2]; // physical indices of enabled views

  function _applyPanels(panels) {
    if (!panels || typeof panels !== "object") return;

    // Enabled views
    const viewNames = Array.isArray(panels.views) ? panels.views : ["command", "system", "knowledge"];
    _enabledViewIndices = viewNames
      .map(n => VIEW_NAME_MAP[n])
      .filter(i => i !== undefined)
      .sort((a, b) => a - b);
    if (_enabledViewIndices.length === 0) _enabledViewIndices = [0];

    // Show/hide view dots
    const allDots = Array.from(document.querySelectorAll(".view-dot"));
    allDots.forEach((d, i) => {
      d.style.display = _enabledViewIndices.includes(i) ? "" : "none";
    });

    // Apply panel visibility via data-panel attributes
    document.querySelectorAll("[data-panel]").forEach(el => {
      const key = el.dataset.panel;
      if (key === "intel" || key === "library") return; // knowledge tab buttons — leave alone
      const visible = panels[key] !== false;
      el.style.display = visible ? "" : "none";
    });

    // Recalculate Command grid if columns changed
    const mainRegion = document.getElementById("main-region");
    if (mainRegion) {
      const leftHidden  = panels.command_left  === false;
      const rightHidden = panels.command_right === false;
      if (leftHidden && rightHidden) {
        mainRegion.style.gridTemplateColumns = "1fr";
      } else if (leftHidden) {
        mainRegion.style.gridTemplateColumns = "minmax(200px,32fr) minmax(220px,28fr)";
      } else if (rightHidden) {
        mainRegion.style.gridTemplateColumns = "minmax(200px,26fr) minmax(200px,32fr)";
      } else {
        mainRegion.style.gridTemplateColumns = "";
      }
    }

    // System row: if only one panel visible, make it full width
    const sysRow1 = document.getElementById("sys-row-1");
    if (sysRow1) {
      const dockerHidden    = panels.docker    === false;
      const processesHidden = panels.processes === false;
      if (dockerHidden || processesHidden) {
        sysRow1.style.gridTemplateColumns = "1fr";
      } else {
        sysRow1.style.gridTemplateColumns = "";
      }
    }

    // Voice button
    const voiceBtn = document.getElementById("hero-voice-btn");
    if (voiceBtn) voiceBtn.style.display = panels.voice !== false ? "" : "none";

    // Intel split: if one side hidden, give full width to the other
    const intelSplit = document.getElementById("intel-split");
    if (intelSplit) {
      const prHidden   = panels.press_room  === false;
      const predHidden = panels.predictions === false;
      if (prHidden || predHidden) {
        intelSplit.style.gridTemplateColumns = "1fr";
      } else {
        intelSplit.style.gridTemplateColumns = "";
      }
    }
  }

  // Expose globally so settings modal can call it after saving
  window.StudioApplyPanels = function(panels) {
    _applyPanels(panels);
    // Re-navigate to ensure we're on a valid enabled view
    const cur = _enabledViewIndices.includes(_viewIndex) ? _viewIndex : _enabledViewIndices[0];
    goToView(cur, false);
  };

  // Apply from localStorage cache immediately (no network wait, no flash)
  try {
    const cached = JSON.parse(localStorage.getItem("studio-panels") || "{}");
    if (Object.keys(cached).length) _applyPanels(cached);
  } catch (e) {}

  // Build fleet-derived maps (colours, power model) from config hosts.
  function _applyFleetConfig(hosts) {
    if (!Array.isArray(hosts)) return;
    const colors = {};
    const power = {};
    hosts.forEach((h, i) => {
      const name = h.name || "";
      if (!name) return;
      const col = h.color || _CHIP_PALETTE[i % _CHIP_PALETTE.length];
      colors[name] = col;
      colors[name.toUpperCase()] = col;
      if (h.idle_watts != null && h.tdp_watts != null) {
        power[name.toLowerCase()] = { idle: h.idle_watts, tdp: h.tdp_watts };
      }
    });
    MACHINE_CHIP_COLOR = colors;
    PWR_MODEL = power;
    renderFleetDOM(hosts);
  }

  // Generate all per-machine DOM (cards, strips, bars, donuts) from the fleet
  // config so any number of machines — named anything — render correctly.
  // Element IDs use the lowercased machine name; the SSE update handlers
  // (updateFleetMachine etc.) already address elements by that scheme.
  let _fleetDOMSig = null;
  function renderFleetDOM(hosts) {
    if (!Array.isArray(hosts) || !hosts.length) return;
    const sig = hosts.map(h => (h.name || "") + ":" + (h.color || "")).join("|");
    if (sig === _fleetDOMSig) return;  // unchanged — skip rebuild
    _fleetDOMSig = sig;

    const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

    // Left-column gauges show the local machine — label it accordingly
    const localHost = hosts.find(h => h.local) || hosts[0];
    _localMachineKey = (localHost && localHost.name ? localHost.name : "local").toLowerCase();
    const colTitle = document.querySelector("#left-col .col-title");
    if (colTitle && localHost && localHost.name) colTitle.textContent = localHost.name + " Vitals";

    // Column sizing: each card has a readable minimum, expands to fill when
    // there's room (1-4 machines), and the row scrolls horizontally when
    // there are many — so cards never cram or get clipped vertically.
    const n = hosts.length;
    // Card minimum (~300px) fits two gauges + bars stack + padding without
    // squashing. One machine: capped + centred. Many: keep the minimum and
    // the row scrolls horizontally so nothing is clipped.
    const cardCols  = n === 1 ? "minmax(300px, 480px)" : `repeat(${n}, minmax(300px, 1fr))`;
    const donutCols = n === 1 ? "minmax(180px, 300px)" : `repeat(${n}, minmax(170px, 1fr))`;
    const justify   = n === 1 ? "center" : "";

    // 1. Fleet machine cards (System view)
    const fm = document.getElementById("fleet-machines");
    if (fm) {
      fm.style.gridTemplateColumns = cardCols;
      fm.style.justifyContent = justify;
      fm.innerHTML = hosts.map(h => {
        const k = (h.name || "").toLowerCase();
        return `<div class="fleet-machine" id="fm-${k}">
          <div class="fm-header">
            <span class="fm-dot fm-dot-online" id="fm-${k}-dot"></span>
            <span class="fm-name">${esc(h.name)}</span>
            <span class="fm-host" id="fm-${k}-host">${esc(h.host || "")}</span>
            <span class="fm-uptime" id="fm-${k}-uptime">—</span>
          </div>
          <div class="fm-viz-row">
            <div class="fm-gauge-wrap"><svg class="fm-gauge-svg" viewBox="0 0 72 72">
              <circle cx="36" cy="36" r="28" fill="none" stroke="rgba(255,255,255,0.07)" stroke-width="8" stroke-dasharray="141.4" stroke-dashoffset="35.3" stroke-linecap="butt" transform="rotate(135 36 36)"/>
              <circle id="fm-${k}-cpu-arc" cx="36" cy="36" r="28" fill="none" stroke="#22d3ee" stroke-width="8" stroke-dasharray="0 141.4" stroke-dashoffset="35.3" stroke-linecap="butt" transform="rotate(135 36 36)" style="transition:stroke-dasharray .5s ease"/>
              <text x="36" y="33" text-anchor="middle" fill="#22d3ee" font-size="9" font-weight="700" font-family="monospace" id="fm-${k}-cpu-txt">—</text>
              <text x="36" y="44" text-anchor="middle" fill="#42496a" font-size="6" font-family="monospace">CPU</text>
            </svg></div>
            <div class="fm-gauge-wrap"><svg class="fm-gauge-svg" viewBox="0 0 72 72">
              <circle cx="36" cy="36" r="26" fill="none" stroke="rgba(255,255,255,0.07)" stroke-width="9"/>
              <circle id="fm-${k}-ram-arc" cx="36" cy="36" r="26" fill="none" stroke="#34d399" stroke-width="9" stroke-dasharray="0 163.4" stroke-dashoffset="40.8" style="transition:stroke-dasharray .5s ease"/>
              <text x="36" y="33" text-anchor="middle" fill="#34d399" font-size="9" font-weight="700" font-family="monospace" id="fm-${k}-ram-txt">—</text>
              <text x="36" y="44" text-anchor="middle" fill="#42496a" font-size="6" font-family="monospace">RAM</text>
            </svg></div>
            <div class="fm-bars-stack">
              <div class="fm-bar-item"><span class="fm-bar-lbl" style="color:var(--orange-hi)">TEMP</span><div class="fm-bar-track"><div class="fm-bar-fill" id="fm-${k}-temp-bar" style="background:linear-gradient(90deg,#fb923c,#f87171)"></div></div><span class="fm-bar-val" id="fm-${k}-temp-val" style="color:var(--orange-hi)">—</span></div>
              <div class="fm-bar-item"><span class="fm-bar-lbl" style="color:var(--violet-hi)">DISK</span><div class="fm-bar-track"><div class="fm-bar-fill" id="fm-${k}-disk-bar" style="background:linear-gradient(90deg,#a78bfa,#6d28d9)"></div></div><span class="fm-bar-val" id="fm-${k}-disk-val" style="color:var(--violet-hi)">—</span></div>
              <div class="fm-bar-item"><span class="fm-bar-lbl" style="color:var(--amber-hi)">LOAD</span><div class="fm-bar-track"><div class="fm-bar-fill" id="fm-${k}-load-bar" style="background:linear-gradient(90deg,#fbbf24,#fb923c)"></div></div><span class="fm-bar-val" id="fm-${k}-load-val" style="color:var(--amber-hi)">—</span></div>
            </div>
          </div>
          <div class="fm-stats-row"><span class="fm-stat"><span class="fm-stat-val" id="fm-${k}-ram-gb">—</span><span class="fm-stat-lbl">GB used</span></span></div>
        </div>`;
      }).join("");
    }

    // 2. Command compact vitals strip — remote machines only (index > 0)
    const cfs = document.getElementById("cmd-fleet-strip");
    if (cfs) {
      const remotes = hosts.slice(1);
      cfs.innerHTML = `<div class="cfs-title">Fleet Vitals</div>` + remotes.map(h => {
        const k = (h.name || "").toLowerCase();
        const col = h.color || "#8d98b4";
        return `<div class="cfs-machine" id="cfs-${k}">
          <span class="cfs-chip" style="background:${col}18;color:${col};border-color:${col}40">${esc(h.name)}</span>
          <span class="cfs-kv"><span class="cfs-lbl">CPU</span><span class="cfs-val" id="cfs-${k}-cpu">—</span></span>
          <span class="cfs-kv"><span class="cfs-lbl">RAM</span><span class="cfs-val" id="cfs-${k}-ram">—</span></span>
          <span class="cfs-kv"><span class="cfs-lbl">TEMP</span><span class="cfs-val" id="cfs-${k}-temp">—</span></span>
        </div>`;
      }).join("");
      cfs.style.display = remotes.length ? "" : "none";
    }

    // 3. RAM bars (right column Memory section)
    const ramBars = document.getElementById("fleet-ram-bars");
    if (ramBars) {
      ramBars.innerHTML = hosts.map(h => {
        const k = (h.name || "").toLowerCase();
        const col = h.color || "#8d98b4";
        return `<div class="fleet-ram-row">
          <span class="fleet-ram-chip" style="background:${col}18;color:${col};border-color:${col}40">${esc(h.name)}</span>
          <div class="fleet-ram-bar-wrap"><div class="gpu-bar-track"><div class="gpu-bar-fill" id="ram-${k}-bar" style="background:linear-gradient(90deg,${col},${col}aa);width:0%"></div></div></div>
          <span class="fleet-ram-val" id="ram-${k}-label" style="color:${col}">—</span>
        </div>`;
      }).join("");
    }

    // 4. Bottom strip machine groups (before the right-aligned cells)
    const bottom = document.getElementById("bottom-strip");
    if (bottom) {
      bottom.querySelectorAll(".bs-machine-group").forEach(el => el.remove());
      const anchor = bottom.querySelector(".bs-right-cells");
      hosts.forEach(h => {
        const k = (h.name || "").toLowerCase();
        const col = h.color || "#8d98b4";
        const g = document.createElement("div");
        g.className = "bs-machine-group";
        g.innerHTML = `<span class="bs-chip" style="background:${col}18;color:${col};border-color:${col}40">${esc(h.name)}</span>
          <span class="bs-kv"><span class="bs-lbl">CPU</span><span class="bs-val bs-val-sm" id="bs-${k}-cpu" style="color:var(--cyan-hi)">—</span></span>
          <span class="bs-kv"><span class="bs-lbl">RAM</span><span class="bs-val bs-val-sm" id="bs-${k}-ram" style="color:var(--green-hi)">—</span></span>
          <span class="bs-kv"><span class="bs-lbl">TEMP</span><span class="bs-val bs-val-sm" id="bs-${k}-temp" style="color:var(--orange-hi)">—</span></span>`;
        bottom.insertBefore(g, anchor);
      });
    }

    // 5. Process donut cells (System view)
    const donutRow = document.getElementById("proc-donuts-row");
    if (donutRow) {
      donutRow.style.gridTemplateColumns = donutCols;
      donutRow.style.justifyContent = justify;
      donutRow.innerHTML = hosts.map(h => {
        const k = (h.name || "").toLowerCase();
        const col = h.color || "#8d98b4";
        return `<div class="proc-donut-cell" id="proc-donut-${k}">
          <div class="proc-donut-title"><span class="dr-machine-chip" style="background:${col}18;color:${col};border-color:${col}40">${esc(h.name)}</span></div>
          <svg class="proc-donut-svg" viewBox="0 0 80 80">
            <circle cx="40" cy="40" r="30" fill="none" stroke="rgba(255,255,255,0.07)" stroke-width="10"/>
            <g id="proc-donut-arcs-${k}"></g>
            <text id="proc-donut-label-${k}" x="40" y="44" text-anchor="middle" fill="var(--text-2)" font-size="7" font-family="monospace">—</text>
          </svg>
          <div class="proc-donut-legend" id="proc-donut-legend-${k}"></div>
        </div>`;
      }).join("");
    }
  }

  // Expose for the settings Fleet editor — re-render machines without reload
  window.StudioApplyFleet = function(hosts) {
    localStorage.setItem("studio-fleet", JSON.stringify(hosts || []));
    _fleetDOMSig = null;  // force rebuild
    _applyFleetConfig(hosts || []);
  };

  // Cached fleet first (instant), then refresh from server
  try {
    const cachedFleet = JSON.parse(localStorage.getItem("studio-fleet") || "null");
    if (cachedFleet) _applyFleetConfig(cachedFleet);
    const cachedWrp = localStorage.getItem("studio-warroom-port");
    window._warRoomPort = cachedWrp ? +cachedWrp : null;
  } catch (e) {}

  // Fetch from server and re-apply (picks up any changes since last cache)
  fetch("/api/settings").then(r => r.json()).then(cfg => {
    const panels = cfg.studio_panels || {};
    localStorage.setItem("studio-panels", JSON.stringify(panels));
    _applyPanels(panels);

    const hosts = (cfg.fleet && cfg.fleet.hosts) || [];
    localStorage.setItem("studio-fleet", JSON.stringify(hosts));
    _applyFleetConfig(hosts);

    // Container descriptions (tooltips) — optional
    const cdesc = cfg.container_descriptions || {};
    localStorage.setItem("studio-container-desc", JSON.stringify(cdesc));
    CONTAINER_DESC = cdesc;

    // Agents panel — optional background services to monitor
    const agents = cfg.agents || [];
    localStorage.setItem("studio-agents", JSON.stringify(agents));
    renderAgents(agents);

    // War Room port for the iframe panel (default: hidden)
    window._warRoomPort = cfg.war_room_port || null;
    if (cfg.war_room_port) localStorage.setItem("studio-warroom-port", String(cfg.war_room_port));
    else localStorage.removeItem("studio-warroom-port");
    const _wrBtn = document.getElementById("warroom-graph-btn");
    if (_wrBtn) _wrBtn.style.display = cfg.war_room_port ? "" : "none";

    // Re-validate current view after server config applied
    if (!_enabledViewIndices.includes(_viewIndex)) {
      _viewIndex = _enabledViewIndices[0];
    }
    goToView(_viewIndex, false);
  }).catch(() => {});

  // ── Carousel ───────────────────────────────────────────────────────────────
  const VIEW_COUNT  = 3;
  const VIEW_LABELS = ["COMMAND", "SYSTEM", "KNOWLEDGE"];
  let _viewIndex = +(localStorage.getItem("studio-view") || 0);
  if (_viewIndex < 0 || _viewIndex >= VIEW_COUNT) _viewIndex = 0;
  // If saved view is now disabled, snap to first enabled
  if (!_enabledViewIndices.includes(_viewIndex)) _viewIndex = _enabledViewIndices[0];

  const track   = document.getElementById("view-track");
  const dots    = Array.from(document.querySelectorAll(".view-dot"));
  const label   = document.getElementById("view-label");
  const btnPrev = document.getElementById("view-prev");
  const btnNext = document.getElementById("view-next");

  function goToView(idx, smooth) {
    idx = Math.max(0, Math.min(VIEW_COUNT - 1, idx));
    _viewIndex = idx;

    // Each view = 33.333% of track. Translate -idx * 33.333%.
    track.style.transition = smooth === false
      ? "none"
      : "transform .35s cubic-bezier(.4,0,.2,1)";
    track.style.transform = `translateX(-${(idx * 100 / VIEW_COUNT).toFixed(4)}%)`;

    dots.forEach((d, i) => d.classList.toggle("active", i === idx));
    if (label) label.textContent = VIEW_LABELS[idx] || "";
    localStorage.setItem("studio-view", idx);

    // Knowledge page (view 2): collapse stat strip for more vertical space
    const studioEl = document.getElementById("studio");
    if (studioEl) studioEl.classList.toggle("knowledge-active", idx === 2);

    setTimeout(redrawSparks, 380);
  }

  function _prevView() {
    const cur = _enabledViewIndices.indexOf(_viewIndex);
    if (cur > 0) goToView(_enabledViewIndices[cur - 1], true);
  }
  function _nextView() {
    const cur = _enabledViewIndices.indexOf(_viewIndex);
    if (cur < _enabledViewIndices.length - 1) goToView(_enabledViewIndices[cur + 1], true);
  }

  goToView(_viewIndex, false);

  btnPrev && btnPrev.addEventListener("click", _prevView);
  btnNext && btnNext.addEventListener("click", _nextView);
  dots.forEach(d => d.addEventListener("click", () => {
    const idx = +d.dataset.view;
    if (_enabledViewIndices.includes(idx)) goToView(idx, true);
  }));

  document.addEventListener("keydown", e => {
    if (e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA")) return;
    // Don't steal arrows from xterm — xterm renders in a div.xterm-helper-textarea
    if (e.target && e.target.closest && e.target.closest(".xterm")) return;
    if (e.key === "ArrowRight") _nextView();
    if (e.key === "ArrowLeft")  _prevView();
  });

  let _tx = null, _ty = null;
  const vport = document.getElementById("view-port");
  vport.addEventListener("touchstart", e => {
    if (e.touches.length !== 1) return;
    _tx = e.touches[0].clientX; _ty = e.touches[0].clientY;
  }, { passive: true });
  vport.addEventListener("touchend", e => {
    if (_tx === null) return;
    const dx = e.changedTouches[0].clientX - _tx;
    const dy = e.changedTouches[0].clientY - _ty;
    _tx = null; _ty = null;
    if (Math.abs(dx) < 40 || Math.abs(dx) < Math.abs(dy) * 1.2) return;
    if (dx < 0) _nextView();
    else        _prevView();
  }, { passive: true });

  // ── Rolling buffers ────────────────────────────────────────────────────────
  const MAX_POINTS = 60;
  let _heroStaticName = "—";
  const _buf = {};
  function push(key, val) {
    if (!_buf[key]) _buf[key] = [];
    _buf[key].push(val);
    if (_buf[key].length > MAX_POINTS) _buf[key].shift();
  }
  function get(key) { return _buf[key] || []; }

  // ── Estimated fleet power ──────────────────────────────────────────────────
  // Model: watts ≈ idle + cpu_pct/100 * (tdp - idle) per machine, plus the
  // local GPU's measured draw added separately. The idle/TDP figures come from
  // each host's fleet config (idle_watts / tdp_watts) — nothing hardcoded.
  // Keyed by lowercase machine name. Empty until config loads → estimate hidden.
  let PWR_MODEL = {};
  const _fleetPct  = {};
  let   _gpuWatts  = null;  // local GPU — real measured value from `gpu` event
  let   _localMachineKey = null; // set from fleet config; used by system SSE handler

  function recomputeEstPower() {
    let total = 0;
    let any = false;
    for (const [key, model] of Object.entries(PWR_MODEL)) {
      const pct = _fleetPct[key];
      if (pct != null) {
        total += model.idle + (pct / 100) * (model.tdp - model.idle);
        any = true;
      }
    }
    if (!any) return;
    // Add measured local GPU watts if available, else estimate GPU at 20W idle
    const gpuContrib = _gpuWatts != null ? _gpuWatts : 20;
    total += gpuContrib;

    const estEl = document.getElementById("bs-est-pwr");
    if (estEl) estEl.textContent = "~" + Math.round(total) + "W";
    const lblEl = document.getElementById("bs-est-pwr-lbl");
    if (lblEl) lblEl.textContent = "est · all";

    // Also update System page power area with fleet estimate tile
    const pwrEstEl = document.getElementById("pwr-fleet-est");
    if (pwrEstEl) pwrEstEl.textContent = "~" + Math.round(total) + "W";
  }

  // ── Canvas sparkline ────────────────────────────────────────────────────────
  function drawSpark(canvas, data, color) {
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    if (w < 2 || h < 2) return;
    canvas.width  = w * dpr;
    canvas.height = h * dpr;
    const ctx2 = canvas.getContext("2d");
    ctx2.scale(dpr, dpr);
    ctx2.clearRect(0, 0, w, h);
    if (!data || data.length < 2) {
      const [rr, gg, bb] = parseHex(color);
      ctx2.strokeStyle = `rgba(${rr},${gg},${bb},0.18)`;
      ctx2.lineWidth = 1;
      ctx2.setLineDash([3, 5]);
      ctx2.beginPath();
      ctx2.moveTo(0, h - 3);
      ctx2.lineTo(w, h - 3);
      ctx2.stroke();
      ctx2.setLineDash([]);
      return;
    }
    const ctx = ctx2;

    const mn = Math.min(...data);
    const mx = Math.max(...data);
    const range = mx - mn || 1;
    const pts = data.map((v, i) => ({
      x: (i / (data.length - 1)) * w,
      y: h - ((v - mn) / range) * (h * 0.8) - h * 0.1,
    }));

    const [r, g, b] = parseHex(color);
    ctx.beginPath();
    ctx.moveTo(pts[0].x, h);
    pts.forEach(p => ctx.lineTo(p.x, p.y));
    ctx.lineTo(pts[pts.length - 1].x, h);
    ctx.closePath();
    const grad = ctx.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0,   `rgba(${r},${g},${b},0.40)`);
    grad.addColorStop(1,   `rgba(${r},${g},${b},0.0)`);
    ctx.fillStyle = grad;
    ctx.fill();

    ctx.beginPath();
    pts.forEach((p, i) => i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y));
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.shadowColor = color;
    ctx.shadowBlur = 5;
    ctx.stroke();
  }

  function parseHex(hex) {
    const h = (hex || "#22d3ee").replace("#", "");
    return [parseInt(h.slice(0,2),16), parseInt(h.slice(2,4),16), parseInt(h.slice(4,6),16)];
  }

  // ── Network area chart ──────────────────────────────────────────────────────
  function drawNetArea(canvas) {
    if (!canvas) return;
    const dl = get("net-dl");
    const ul = get("net-ul");
    if (dl.length < 2 && ul.length < 2) { showNetIdle(true); return; }

    // Show idle if all values near zero
    const maxDl = Math.max(...dl, 0);
    const maxUl = Math.max(...ul, 0);
    const idle  = maxDl < 0.05 && maxUl < 0.05;
    showNetIdle(idle);
    if (idle) return;

    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    if (w < 4 || h < 4) return;
    canvas.width  = w * dpr;
    canvas.height = h * dpr;
    const ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);

    const allVals = [...dl, ...ul];
    const globalMax = Math.max(...allVals) || 1;

    function drawSeries(data, color, alpha) {
      const len = data.length;
      if (len < 2) return;
      const pts = data.map((v, i) => ({
        x: (i / (len - 1)) * w,
        y: h - (v / globalMax) * h * 0.85 - h * 0.05,
      }));
      const [r, g, b] = parseHex(color);
      ctx.beginPath();
      ctx.moveTo(pts[0].x, h);
      pts.forEach(p => ctx.lineTo(p.x, p.y));
      ctx.lineTo(pts[pts.length-1].x, h);
      ctx.closePath();
      const grad = ctx.createLinearGradient(0, 0, 0, h);
      grad.addColorStop(0,   `rgba(${r},${g},${b},${alpha})`);
      grad.addColorStop(0.7, `rgba(${r},${g},${b},${(alpha*0.4).toFixed(2)})`);
      grad.addColorStop(1,   `rgba(${r},${g},${b},0)`);
      ctx.fillStyle = grad;
      ctx.fill();

      ctx.beginPath();
      pts.forEach((p, i) => i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y));
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.5;
      ctx.shadowColor = color;
      ctx.shadowBlur = 4;
      ctx.stroke();
    }

    drawSeries(ul, "#60a5fa", 0.35);
    drawSeries(dl, "#2dd4bf", 0.40);
  }

  function showNetIdle(show) {
    const lbl = document.getElementById("net-idle-label");
    if (lbl) lbl.style.display = show ? "flex" : "none";
  }

  // ── Token usage bar chart ───────────────────────────────────────────────────
  const TU_COLORS = ["#2dd4bf","#60a5fa","#a78bfa","#f472b6","#fb923c","#fbbf24","#34d399","#22d3ee"];

  function renderTokenBars(byModel) {
    const container = document.getElementById("token-bar-chart");
    if (!container || !byModel || !byModel.length) return;

    const maxTokens = Math.max(...byModel.map(m => m.in_tokens + m.out_tokens), 1);
    container.innerHTML = "";

    byModel.slice(0, 3).forEach((m, i) => {
      const total = (m.in_tokens || 0) + (m.out_tokens || 0);
      const pct   = (total / maxTokens * 100).toFixed(1);
      const color = TU_COLORS[i % TU_COLORS.length];
      const label = m.model.length > 14 ? m.model.slice(0, 13) + "…" : m.model;
      const display = total >= 1000000
        ? (total/1000000).toFixed(1) + "M"
        : total >= 1000
        ? (total/1000).toFixed(0) + "K"
        : String(total);

      const row = document.createElement("div");
      row.className = "tu-bar-row";
      row.innerHTML = `
        <span class="tu-bar-label" style="color:${color}">${esc(label)}</span>
        <div class="tu-bar-track">
          <div class="tu-bar-fill" style="width:${pct}%;background:${color}"></div>
        </div>
        <span class="tu-bar-val" style="color:${color}">${display}</span>
      `;
      container.appendChild(row);
    });
  }

  // ── Donut helpers ──────────────────────────────────────────────────────────
  // Full circle donut: r, circumference C = 2πr
  // stroke-dasharray = pct/100 * C  then gap
  function setDonut(arcId, pct, circumference) {
    const arc = document.getElementById(arcId);
    if (!arc) return;
    const used = (Math.min(pct, 100) / 100) * circumference;
    arc.setAttribute("stroke-dasharray", `${used.toFixed(1)} ${circumference.toFixed(1)}`);
  }

  // RAM donut: r=30, C=188.5, dashoffset=47.1 (start at top)
  const RAM_C = 2 * Math.PI * 30;  // 188.5
  function setRamDonut(pct) {
    setDonut("ram-donut-arc", pct, RAM_C);
    const txt = document.getElementById("ram-donut-pct");
    if (txt) txt.textContent = pct != null ? Math.round(pct) + "%" : "—";
  }

  // KV arc: r=72 in the condensed 240×240 orb, C=452.4
  const KV_CIRC = 2 * Math.PI * 72;
  function setHeroKvArc(pct) {
    const arc = document.getElementById("hero-kv-arc");
    if (!arc) return;
    const used = (pct / 100) * KV_CIRC;
    arc.setAttribute("stroke-dasharray", `${used.toFixed(1)} ${KV_CIRC.toFixed(1)}`);
  }

  // ── Context window donut (centre mini panel) ──────────────────────────────
  const CTX_DON_C = 2 * Math.PI * 26; // 163.4 — same r as fleet RAM donuts
  function setCtxDonut(pct) {
    const arc = document.getElementById("ctx-donut-arc");
    if (!arc) return;
    const used = (Math.min(pct, 100) / 100) * CTX_DON_C;
    const gap  = CTX_DON_C - used;
    arc.setAttribute("stroke-dasharray", `${used.toFixed(1)} ${gap.toFixed(1)}`);
    const txt = document.getElementById("ctx-donut-pct");
    if (txt) txt.textContent = pct != null ? Math.round(pct) + "%" : "—";
  }

  // ── Backends comparison bar chart (centre mini panel) ─────────────────────
  const BC_COLORS = ["#2dd4bf", "#60a5fa", "#a78bfa", "#fb923c"];
  function renderBackendsCmp(backends) {
    const wrap = document.getElementById("backends-cmp-bars");
    if (!wrap || !backends || !backends.length) return;
    const maxSlots = Math.max(...backends.map(b => b.total || 0), 1);
    wrap.innerHTML = "";
    backends.forEach((be, i) => {
      const busyPct  = be.total > 0 ? (be.busy  / be.total) * 100 : 0;
      const totalPct = (be.total / maxSlots) * 100;
      const color    = BC_COLORS[i % BC_COLORS.length];
      const name     = (be.name || "BE").slice(0, 8).toUpperCase();
      const row = document.createElement("div");
      row.className = "bc-bar-row";
      row.innerHTML = `
        <span class="bc-bar-lbl" style="color:${color}">${esc(name)}</span>
        <div class="bc-bar-track">
          <div class="bc-bar-fill" style="width:${busyPct.toFixed(1)}%;background:${color}"></div>
        </div>
        <span class="bc-bar-val" style="color:${color}">${be.busy}/${be.total}</span>
      `;
      wrap.appendChild(row);
    });
  }

  // ── Fleet gauge helpers ────────────────────────────────────────────────────
  // Arc gauges (270° sweep): r=28, C=175.9, dasharray total=141.4 (270/360 * C)
  // The arc is rotated 135° so it starts bottom-left.
  const ARC_R    = 28;
  const ARC_C    = 2 * Math.PI * ARC_R;   // 175.9
  const ARC_SPAN = ARC_C * 0.75;          // 270° = 0.75 of circle = 131.9

  function setArcGauge(arcId, pct) {
    const arc = document.getElementById(arcId);
    if (!arc || pct == null) return;
    const used = (Math.min(pct, 100) / 100) * ARC_SPAN;
    const gap  = ARC_C - used;
    arc.setAttribute("stroke-dasharray", `${used.toFixed(1)} ${gap.toFixed(1)}`);
  }

  // Donut gauges for RAM columns: r=26, C=163.4
  const RAM_DON_C = 2 * Math.PI * 26;  // 163.4

  function setFleetDonut(arcId, pct) {
    const arc = document.getElementById(arcId);
    if (!arc || pct == null) return;
    const used = (Math.min(pct, 100) / 100) * RAM_DON_C;
    const gap  = RAM_DON_C - used;
    arc.setAttribute("stroke-dasharray", `${used.toFixed(1)} ${gap.toFixed(1)}`);
  }

  function updateFleetMachine(prefix, machine) {
    // Online dot
    const dot = document.getElementById(`fm-${prefix}-dot`);
    if (dot) {
      dot.className = `fm-dot ${machine.online ? "fm-dot-online" : "fm-dot-offline"}`;
    }
    // Host
    const host = document.getElementById(`fm-${prefix}-host`);
    if (host) host.textContent = machine.host || "";
    // Uptime
    const up = document.getElementById(`fm-${prefix}-uptime`);
    if (up) up.textContent = machine.uptime || (machine.online ? "—" : "OFFLINE");

    // CPU arc gauge
    setArcGauge(`fm-${prefix}-cpu-arc`, machine.cpu_pct);
    const cpuTxt = document.getElementById(`fm-${prefix}-cpu-txt`);
    if (cpuTxt) cpuTxt.textContent = machine.cpu_pct != null ? Math.round(machine.cpu_pct) + "%" : "—";

    // RAM donut
    setFleetDonut(`fm-${prefix}-ram-arc`, machine.ram_pct);
    const ramTxt = document.getElementById(`fm-${prefix}-ram-txt`);
    if (ramTxt) ramTxt.textContent = machine.ram_pct != null ? Math.round(machine.ram_pct) + "%" : "—";

    // Temp bar (0-100°C scale, capped at 90)
    const tempPct = machine.cpu_temp != null ? Math.min((machine.cpu_temp / 90) * 100, 100) : 0;
    const tempBar = document.getElementById(`fm-${prefix}-temp-bar`);
    if (tempBar) tempBar.style.width = tempPct.toFixed(1) + "%";
    const tempVal = document.getElementById(`fm-${prefix}-temp-val`);
    if (tempVal) tempVal.textContent = machine.cpu_temp != null ? machine.cpu_temp.toFixed(0) + "°" : "—";

    // Disk bar
    const diskBar = document.getElementById(`fm-${prefix}-disk-bar`);
    if (diskBar) diskBar.style.width = (machine.disk_pct || 0).toFixed(1) + "%";
    const diskVal = document.getElementById(`fm-${prefix}-disk-val`);
    if (diskVal) diskVal.textContent = machine.disk_pct != null ? machine.disk_pct.toFixed(0) + "%" : "—";

    // Load bar (scaled 0..16 cores)
    const loadPct = machine.load1 != null ? Math.min((machine.load1 / 16) * 100, 100) : 0;
    const loadBar = document.getElementById(`fm-${prefix}-load-bar`);
    if (loadBar) loadBar.style.width = loadPct.toFixed(1) + "%";
    const loadVal = document.getElementById(`fm-${prefix}-load-val`);
    if (loadVal) loadVal.textContent = machine.load1 != null ? machine.load1.toFixed(2) : "—";

    // RAM GB
    const ramGb = document.getElementById(`fm-${prefix}-ram-gb`);
    if (ramGb) ramGb.textContent = machine.ram_used_gb != null ? machine.ram_used_gb.toFixed(1) + "G" : "—";
  }

  // ── Clock ──────────────────────────────────────────────────────────────────
  const clockEl = document.getElementById("top-clock");
  function updateClock() {
    const n = new Date();
    const p = v => String(v).padStart(2,"0");
    clockEl.textContent = `${p(n.getHours())}:${p(n.getMinutes())}:${p(n.getSeconds())}`;
  }
  updateClock();
  setInterval(updateClock, 1000);

  // ── Gauge init ─────────────────────────────────────────────────────────────
  document.querySelectorAll(".gauge-wrap").forEach(el => {
    lwGauge(el, {
      value: null, max: 100,
      label: el.dataset.label || "",
      unit:  el.dataset.unit  || "",
      kind:  el.dataset.kind  || "percent",
      color: el.dataset.color || "cyan",
    });
  });

  function setGauge(id, value, max) {
    const el = document.getElementById(id);
    if (!el) return;
    lwGauge(el, {
      value, max: max || 100,
      label: el.dataset.label || "",
      unit:  el.dataset.unit  || "",
      kind:  el.dataset.kind  || "percent",
      color: el.dataset.color || "cyan",
    });
  }

  // ── Hero state ─────────────────────────────────────────────────────────────
  function setHeroStatus(status) {
    const studio = document.getElementById("studio");
    studio.classList.toggle("generating", status === "generating");
    const badge = document.getElementById("hero-status-badge");
    badge.className = "hero-status-badge badge-" + (status || "idle");
    badge.textContent = (status || "idle").toUpperCase();
  }

  function parseParams(name) {
    if (!name) return null;
    const m = name.match(/(\d+\.?\d*)\s*[Bb]/);
    return m ? m[0].toUpperCase() : null;
  }

  // ── Agents: derive from docker containers ──────────────────────────────────
  // Built from config (agents array). Keyed by row id "agent-<id>".
  let AGENT_MAP = {};

  // Render agent rows + rebuild AGENT_MAP from a config agents array.
  // Each agent: {id, name, containers:[...], machine, primary?, service_unit?}
  function renderAgents(agents) {
    const list = document.getElementById("agents-list");
    const empty = document.getElementById("agents-empty");
    if (!list) return;
    agents = Array.isArray(agents) ? agents : [];
    AGENT_MAP = {};
    const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

    list.innerHTML = agents.map(a => {
      const rowId = "agent-" + (a.id || (a.name || "").toLowerCase().replace(/[^a-z0-9]+/g, "-"));
      const containers = Array.isArray(a.containers) ? a.containers : (a.containers ? [a.containers] : []);
      AGENT_MAP[rowId] = {
        containers,
        machine: a.machine || "",
        primary: a.primary || containers[0] || "",
        serviceUnit: a.service_unit || a.serviceUnit || undefined,
      };
      const col = _chipColor(a.machine || "");
      return `<div class="agent-row" id="${rowId}">
        <div class="agent-main">
          <span class="agent-dot agent-dot-unknown"></span>
          <span class="agent-name">${esc(a.name || a.id)}</span>
          ${a.machine ? `<span class="agent-chip" style="background:${col}18;color:${col};border-color:${col}40">${esc(a.machine)}</span>` : ""}
          <span class="agent-state agent-state-unknown">—</span>
        </div>
        <div class="agent-actions"></div>
      </div>`;
    }).join("");

    if (empty) empty.style.display = agents.length ? "none" : "block";
    // Action/click handlers are (re)wired by updateAgents() on each docker tick.
  }

  window.StudioApplyAgents = function(agents) {
    localStorage.setItem("studio-agents", JSON.stringify(agents || []));
    renderAgents(agents || []);
  };

  // Render agents from cache immediately (refreshed by the /api/settings fetch)
  try {
    const _cachedAgents = JSON.parse(localStorage.getItem("studio-agents") || "null");
    if (_cachedAgents) renderAgents(_cachedAgents);
  } catch (e) {}

  // Track container → machine from the live docker payload
  const _containerMachine = {};

  function _agentAction(rowId, action) {
    const cfg = AGENT_MAP[rowId];
    if (!cfg) return;
    const machine = _containerMachine[cfg.primary] || cfg.machine;
    const actWrap = document.querySelector(`#${rowId} .agent-actions`);
    const fb = actWrap && actWrap.querySelector(".agent-act-feedback");

    if (actWrap) actWrap.querySelectorAll(".agent-act-btn").forEach(b => { b.disabled = true; });
    if (fb) fb.textContent = action + "ing…";

    // Docker container action
    dockerAction(machine, cfg.primary, cfg.primary, action);

    // If the agent also has a systemd service unit (e.g. background workers),
    // restart that too so the whole stack comes back up cleanly
    if (cfg.serviceUnit && action === "restart") {
      fetch(`/api/remote-service/${encodeURIComponent(machine)}/${encodeURIComponent(cfg.serviceUnit)}/restart`, { method: "POST" })
        .catch(err => console.warn("remote-service restart error:", err));
    }

    setTimeout(() => {
      if (actWrap) actWrap.querySelectorAll(".agent-act-btn").forEach(b => { b.disabled = false; });
      if (fb) fb.textContent = "";
    }, 6000);
  }

  function _buildAgentActions(rowId, state) {
    const row = document.getElementById(rowId);
    if (!row) return;
    const actWrap = row.querySelector(".agent-actions");
    if (!actWrap) return;

    actWrap.innerHTML = "";
    const fb = document.createElement("span");
    fb.className = "agent-act-feedback";

    if (state === "online") {
      const restartBtn = document.createElement("button");
      restartBtn.className = "agent-act-btn btn-restart";
      restartBtn.textContent = "↺ Restart";
      restartBtn.addEventListener("click", e => { e.stopPropagation(); _agentAction(rowId, "restart"); });

      const stopBtn = document.createElement("button");
      stopBtn.className = "agent-act-btn btn-stop";
      stopBtn.textContent = "■ Stop";
      stopBtn.addEventListener("click", e => { e.stopPropagation(); _agentAction(rowId, "stop"); });

      actWrap.appendChild(restartBtn);
      actWrap.appendChild(stopBtn);
    } else if (state === "offline") {
      const startBtn = document.createElement("button");
      startBtn.className = "agent-act-btn btn-start";
      startBtn.textContent = "▶ Start";
      startBtn.addEventListener("click", e => { e.stopPropagation(); _agentAction(rowId, "start"); });
      actWrap.appendChild(startBtn);
    } else {
      fb.textContent = "Status unknown";
    }

    actWrap.appendChild(fb);
  }

  // Wire click-to-expand once (idempotent flag on element)
  function _wireAgentClick(rowId) {
    const row = document.getElementById(rowId);
    if (!row || row.dataset.clickWired) return;
    row.dataset.clickWired = "1";
    row.querySelector(".agent-main").addEventListener("click", () => {
      const isOpen = row.classList.contains("expanded");
      // Collapse all others
      document.querySelectorAll(".agent-row.expanded").forEach(r => r.classList.remove("expanded"));
      if (!isOpen) row.classList.add("expanded");
    });
  }

  function updateAgents(containers) {
    const stateMap = {};
    (containers || []).forEach(c => {
      stateMap[c.name] = c.state;
      if (c.machine) _containerMachine[c.name] = c.machine;
    });

    Object.entries(AGENT_MAP).forEach(([rowId, cfg]) => {
      const row = document.getElementById(rowId);
      if (!row) return;

      const present = cfg.containers.filter(n => stateMap[n] !== undefined);
      const running = cfg.containers.filter(n => stateMap[n] === "running");

      let state = "unknown";
      if (present.length > 0) state = running.length > 0 ? "online" : "offline";

      const dot  = row.querySelector(".agent-dot");
      const stEl = row.querySelector(".agent-state");
      if (dot)  dot.className  = `agent-dot agent-dot-${state}`;
      if (stEl) {
        stEl.className  = `agent-state agent-state-${state}`;
        stEl.textContent = state === "online" ? "UP" : state === "offline" ? "DOWN" : "—";
      }

      _buildAgentActions(rowId, state);
      _wireAgentClick(rowId);
    });

    // War Room scout dot
    const scoutState = stateMap["scout"];
    const wrDot = document.getElementById("wr-scout-dot");
    if (wrDot) {
      wrDot.className = `agent-dot ${
        scoutState === "running" ? "agent-dot-online"
        : scoutState ? "agent-dot-offline"
        : "agent-dot-unknown"
      }`;
    }
  }

  // ── Lib segment bar ────────────────────────────────────────────────────────
  const LIB_SEG_COLORS = ["#2dd4bf","#60a5fa","#a78bfa","#f472b6","#fb923c","#fbbf24","#34d399"];

  function renderLibSegment(collections) {
    const bar    = document.getElementById("lib-segment-bar");
    const legend = document.getElementById("lib-segment-legend");
    if (!bar || !legend || !collections || !collections.length) return;

    const total = collections.reduce((s, c) => s + (c.count || 0), 0) || 1;
    bar.innerHTML = "";
    legend.innerHTML = "";

    collections.slice(0, 7).forEach((col, i) => {
      const pct  = ((col.count || 0) / total * 100).toFixed(1);
      const col_color = LIB_SEG_COLORS[i % LIB_SEG_COLORS.length];
      const seg  = document.createElement("div");
      seg.className = "lib-seg";
      seg.style.cssText = `width:${pct}%;background:${col_color};`;
      bar.appendChild(seg);

      const item = document.createElement("div");
      item.className = "lib-leg-item";
      const name = (col.name || "?").length > 16 ? col.name.slice(0,15)+"…" : col.name;
      item.innerHTML = `<span class="lib-leg-dot" style="background:${col_color}"></span>${esc(name)}`;
      legend.appendChild(item);
    });
  }

  // ── SSE handlers ──────────────────────────────────────────────────────────
  const handlers = {};

  handlers["model-status"] = function (d) {
    const friendly  = d.friendly || d.name || "—";
    const shortName = friendly.length > 18 ? friendly.slice(0, 17) + "…" : friendly;
    _heroStaticName = shortName;
    if (!document.getElementById("studio").dataset.generatingBackends) {
      document.getElementById("hero-model-name").textContent = shortName;
    }
    setHeroStatus(d.status || "idle");

    document.getElementById("sc-model").textContent  = shortName;
    const params = parseParams(friendly) || parseParams(d.name || "");
    document.getElementById("sc-params").textContent = params || "—";
    document.getElementById("tick-model").textContent = shortName;

    if (Array.isArray(d.models)) {
      const total   = d.models.length;
      const healthy = d.models.filter(m => m.health !== "unreachable").length;
      document.getElementById("sc-backends").textContent  = `${healthy}/${total}`;
      document.getElementById("tick-health").textContent  = `${healthy}/${total}`;
      const th = document.getElementById("tick-health");
      th.className = "tick-health" + (healthy < total ? (healthy === 0 ? " bad" : " warn") : "");
    }

    if (d.kv_pct != null) {
      setHeroKvArc(d.kv_pct);
      document.getElementById("kv-val").textContent = d.kv_pct.toFixed(1) + "%";
      document.getElementById("kv-bar").style.width = Math.min(d.kv_pct, 100).toFixed(1) + "%";
      // Mini context-window donut in center column
      setCtxDonut(d.kv_pct);
    }

    const statusColors = { idle:"#10b981", generating:"#38bdf8", swapping:"#fbbf24", unreachable:"#f87171" };
    const sc = document.getElementById("sc-status");
    sc.textContent = (d.status || "idle").toUpperCase();
    sc.style.color = statusColors[d.status] || "#e8ecf4";

    updateSentiment();
  };

  handlers["slots"] = function (d) {
    const backends = d.backends || (d.total != null ? [{
      name: "Slots", total: d.total || 0, busy: d.busy || 0,
      reachable: true, slots: d.slots || [],
    }] : []);

    const total = d.total != null ? d.total : backends.reduce((a, b) => a + b.total, 0);
    const busy  = d.busy  != null ? d.busy  : backends.reduce((a, b) => a + b.busy,  0);
    const free  = total - busy;

    document.getElementById("sc-slots").textContent   = `${free}/${total}`;
    document.getElementById("tick-slots").textContent  = `${free}/${total}`;
    document.getElementById("bs-slots").textContent    = `${free}/${total}`;

    const section   = document.getElementById("slots-section");
    const summaryEl = document.getElementById("slots-summary");
    if (summaryEl) summaryEl.textContent = `${busy}/${total} busy`;

    if (!backends.length) return;

    // Stash per-backend context window (max ctx across its slots) for the chat
    // panel's context-usage meter. Keyed by lowercased backend name.
    window._chatCtx = window._chatCtx || {};
    backends.forEach(b => {
      const ctx = Math.max(0, ...(b.slots || []).map(s => s.ctx_total || 0));
      if (!ctx) return;
      if (b.name)  window._chatCtx[b.name.toLowerCase()] = ctx;
      if (b.model) window._chatCtx[String(b.model).toLowerCase()] = ctx;  // key by model_id too
    });

    // Backends comparison bar chart in centre mini panel
    renderBackendsCmp(backends);

    const topoKey = backends.map(b => b.name + ":" + b.total).join("|");
    if (section.dataset.slotsKey !== topoKey) {
      const labelDiv = document.getElementById("slots-label");
      section.innerHTML = "";
      section.appendChild(labelDiv || (() => {
        const d2 = document.createElement("div");
        d2.id = "slots-label";
        return d2;
      })());
      const lbl = section.querySelector("#slots-label");
      if (lbl) lbl.innerHTML = `Slot Occupancy — <span id="slots-summary">${busy}/${total} busy</span>`;

      backends.forEach((be, beIndex) => {
        const beRow = document.createElement("div");
        beRow.className = "slots-be-row";
        beRow.dataset.beName = be.name;
        beRow.dataset.be = String(beIndex);

        const beLbl = document.createElement("span");
        beLbl.className = "slots-be-label";
        beLbl.textContent = be.name.toUpperCase();
        beRow.appendChild(beLbl);

        const pipWrap = document.createElement("div");
        pipWrap.className = "slots-pip-wrap";
        pipWrap.id = "slots-pips-" + be.name.toLowerCase();
        for (let i = 0; i < be.total; i++) {
          const pip = document.createElement("div");
          pip.className = "slot-pip";
          pip.dataset.be = String(beIndex);
          pip.title = `${be.name} Slot ${i}`;
          pipWrap.appendChild(pip);
        }
        beRow.appendChild(pipWrap);
        section.appendChild(beRow);

        const actRow = document.createElement("div");
        actRow.className = "slots-activity";
        actRow.id = "slots-act-" + be.name.toLowerCase();
        section.appendChild(actRow);
      });
      section.dataset.slotsKey = topoKey;
    }

    for (const be of backends) {
      const pipWrap = document.getElementById("slots-pips-" + be.name.toLowerCase());
      if (pipWrap) {
        const pips = pipWrap.querySelectorAll(".slot-pip");
        const slotMap = {};
        for (const s of (be.slots || [])) slotMap[s.id] = s;
        pips.forEach((pip, i) => pip.classList.toggle("busy", !!(slotMap[i] || {}).busy));
      }

      const actEl = document.getElementById("slots-act-" + be.name.toLowerCase());
      if (actEl) {
        const busySlots = (be.slots || []).filter(s => s.busy);
        actEl.textContent = busySlots.length === 0 ? "" :
          be.name.toUpperCase() + " " + busySlots.map(s => {
            const parts = [];
            if (s.tokens_decoded != null) {
              const d2 = s.tokens_decoded;
              parts.push((d2 >= 1000 ? (d2/1000).toFixed(1)+"K" : d2) + " tok");
            }
            if (s.ctx_total != null) {
              parts.push((s.ctx_total >= 1000 ? (s.ctx_total/1000).toFixed(0)+"K" : s.ctx_total) + " ctx");
            }
            return `s${s.id}: ${parts.join(", ")}`;
          }).join("  ");
      }
    }

    // ctx for stat strip
    for (const be of backends) {
      if (!be.reachable) continue;
      const busySlot = (be.slots || []).find(s => s.busy && s.ctx_total != null);
      if (busySlot) {
        const ctx = busySlot.ctx_total;
        const ctxStr = ctx >= 1000 ? `${(ctx/1000).toFixed(0)}K` : String(ctx);
        document.getElementById("sc-ctx").textContent = ctxStr;
        const chCtx = document.getElementById("ch-ctx-val");
        if (chCtx) chCtx.textContent = ctxStr + " tokens";
        break;
      }
    }

    // Hero + stat strip model name — reflect which backend(s) are actively generating
    const generatingBe = backends.filter(b => b.busy > 0);
    document.getElementById("studio").dataset.generatingBackends = generatingBe.length;
    const heroNameEl = document.getElementById("hero-model-name");
    const scModelEl  = document.getElementById("sc-model");
    let activeName;
    if (generatingBe.length === 0) {
      activeName = _heroStaticName;
    } else if (generatingBe.length === 1) {
      activeName = generatingBe[0].name;
    } else {
      const joined = generatingBe.map(b => b.name).join(" + ");
      activeName = joined.length > 20 ? `${generatingBe.length}× active` : joined;
    }
    heroNameEl.textContent = activeName;
    if (scModelEl) scModelEl.textContent = activeName;
  };

  let _localCpuPct = null; // kept for local machine process donut

  handlers["system"] = function (d) {
    if (d.cpu_pct != null) _localCpuPct = d.cpu_pct;
    setGauge("g-cpu",    d.cpu_pct,  100);
    setGauge("g-cputmp", d.cpu_temp, 100);
    setGauge("g-disk",   d.disk_pct, 100);

    const lk = _localMachineKey || "local";
    // Local machine bottom strip — CPU
    const localCpuEl = document.getElementById("bs-" + lk + "-cpu");
    if (localCpuEl && d.cpu_pct != null) {
      localCpuEl.textContent = Math.round(d.cpu_pct) + "%";
      const p = d.cpu_pct;
      localCpuEl.style.color = p > 80 ? "var(--red-hi)" : p > 50 ? "var(--amber-hi)" : "var(--cyan-hi)";
    }
    // Track local CPU for power estimate
    if (d.cpu_pct != null) {
      _fleetPct[lk] = d.cpu_pct;
      recomputeEstPower();
    }

    // Legacy compat: keep hidden spans updated
    const bsCpuEl = document.getElementById("bs-cpu");
    if (bsCpuEl) bsCpuEl.textContent = d.cpu_pct != null ? d.cpu_pct.toFixed(0) + "%" : "—";

    if (d.ram_total_gb > 0) {
      const pct = (d.ram_used_gb / d.ram_total_gb) * 100;
      // Update local machine RAM bar in Memory section
      const ramUsedBar = document.getElementById("ram-used-bar");
      if (ramUsedBar) ramUsedBar.style.width = pct.toFixed(1) + "%";
      const ramUsedVal = document.getElementById("ram-used-val");
      if (ramUsedVal) ramUsedVal.textContent = d.ram_used_gb.toFixed(1) + "G";
      // Update local machine label in fleet RAM bar
      const localLblEl = document.getElementById("ram-" + lk + "-label");
      if (localLblEl) localLblEl.textContent = d.ram_used_gb.toFixed(1) + "/" + d.ram_total_gb.toFixed(0) + "G";
      // Local machine bottom strip RAM
      const localRamEl = document.getElementById("bs-" + lk + "-ram");
      if (localRamEl) {
        localRamEl.textContent = Math.round(pct) + "%";
        localRamEl.style.color = pct > 85 ? "var(--red-hi)" : pct > 65 ? "var(--amber-hi)" : "var(--green-hi)";
      }
      const bsRamEl = document.getElementById("bs-ram");
      if (bsRamEl) bsRamEl.textContent = d.ram_used_gb.toFixed(0) + "G/" + d.ram_total_gb.toFixed(0) + "G";
      setGauge("g-ram", pct, 100);
      setRamDonut(pct);
    }

    if (d.gpu_temp != null) {
      setGauge("g-gputmp", d.gpu_temp, 120);
      const bsGpuTmpEl = document.getElementById("bs-gputmp");
      if (bsGpuTmpEl) bsGpuTmpEl.textContent = d.gpu_temp.toFixed(0) + "°C";
      // Local machine TEMP — use GPU temp as primary (it's the hottest)
      const localTmpEl = document.getElementById("bs-" + lk + "-temp");
      if (localTmpEl) {
        const tmp = d.gpu_temp;
        localTmpEl.textContent = Math.round(tmp) + "°";
        localTmpEl.style.color = tmp > 80 ? "var(--red-hi)" : tmp > 65 ? "var(--amber-hi)" : "var(--orange-hi)";
      }
    } else if (d.cpu_temp != null) {
      // Fallback to CPU temp if GPU temp absent
      const localTmpEl = document.getElementById("bs-" + lk + "-temp");
      if (localTmpEl) {
        const tmp = d.cpu_temp;
        localTmpEl.textContent = Math.round(tmp) + "°";
        localTmpEl.style.color = tmp > 80 ? "var(--red-hi)" : tmp > 65 ? "var(--amber-hi)" : "var(--orange-hi)";
      }
    }
    if (d.gpu_pct != null) setGauge("g-gpu", d.gpu_pct, 100);

    updateSentiment();
    _mobSum("mob-sum-vitals",
      (d.cpu_pct != null ? Math.round(d.cpu_pct) + "% CPU" : "") +
      (d.ram_total_gb > 0 ? " · " + Math.round((d.ram_used_gb / d.ram_total_gb) * 100) + "% RAM" : ""));
    _mobSum("mob-sum-fm-" + lk, d.cpu_pct != null ? "CPU " + Math.round(d.cpu_pct) + "%" : "—");
  };

  handlers["gpu"] = function (d) {
    document.getElementById("gpu-vendor").textContent = (d.vendor || "—").toUpperCase();

    if (d.utilization_pct != null) {
      document.getElementById("gpu-util-bar").style.width = Math.min(d.utilization_pct, 100) + "%";
      document.getElementById("gpu-util-val").textContent = d.utilization_pct + "%";
      setGauge("g-gpu", d.utilization_pct, 100);
      document.getElementById("tick-gpu").textContent = d.utilization_pct + "%";
    }

    if (d.vram_used_mb != null && d.vram_total_mb != null && d.vram_total_mb > 0) {
      const pct = (d.vram_used_mb / d.vram_total_mb) * 100;
      document.getElementById("gpu-vram-bar").style.width = pct.toFixed(1) + "%";
      const used = d.vram_used_mb >= 1024
        ? (d.vram_used_mb/1024).toFixed(1)+" GB" : d.vram_used_mb+" MB";
      document.getElementById("gpu-vram-val").textContent = used;
    }

    if (d.temperature_c != null) {
      const maxTemp = 110;
      document.getElementById("gpu-temp-bar").style.width =
        Math.min((d.temperature_c / maxTemp) * 100, 100).toFixed(1) + "%";
      document.getElementById("gpu-temp-val").textContent = d.temperature_c + "°C";
      setGauge("g-gputmp", d.temperature_c, maxTemp);
      document.getElementById("bs-gputmp").textContent = d.temperature_c + "°C";
    }

    if (d.power_watts != null) {
      const maxPwr = 150;
      document.getElementById("gpu-power-bar").style.width =
        Math.min((d.power_watts / maxPwr) * 100, 100).toFixed(1) + "%";
      document.getElementById("gpu-power-val").textContent = d.power_watts.toFixed(1) + "W";
      // Store for fleet power estimate
      _gpuWatts = d.power_watts;
      recomputeEstPower();
      // Show measured GPU watts in bottom strip (hidden span) and System page GPU tile label
      const gpuWEl = document.getElementById("bs-gpu-watts");
      if (gpuWEl) gpuWEl.textContent = d.power_watts.toFixed(1);
      // Update local GPU watts label in power section if present
      const gpuLabelEl = document.getElementById("pwr-gpu-measured-val");
      if (gpuLabelEl) gpuLabelEl.textContent = d.power_watts.toFixed(1) + "W";
      // Update local machine TEMP in bottom strip from GPU temp
      if (d.temperature_c != null) {
        const localTmpEl = document.getElementById("bs-" + (_localMachineKey || "local") + "-temp");
        if (localTmpEl) {
          const tmp = d.temperature_c;
          localTmpEl.textContent = Math.round(tmp) + "°";
          localTmpEl.style.color = tmp > 80 ? "var(--red-hi)" : tmp > 65 ? "var(--amber-hi)" : "var(--orange-hi)";
        }
      }
    }

    updateSentiment();
    if (d.utilization_pct != null) {
      const vramPct = d.vram_total_mb ? " · " + Math.round((d.vram_used_mb / d.vram_total_mb) * 100) + "% VRAM" : "";
      _mobSum("mob-sum-gpu", d.utilization_pct + "% load" + vramPct);
    }
  };

  handlers["inference-speed"] = function (d) {
    const tps  = d.generation_tps;
    const ttft = d.ttft_ms;

    if (tps != null) push("tps", tps);

    const lastTps = get("tps");
    const lastKnown = lastTps.length > 0 ? lastTps[lastTps.length - 1] : null;
    document.getElementById("tick-tps").textContent = tps != null ? tps.toFixed(1) : (lastKnown != null ? lastKnown.toFixed(1) : "—");

    if (Array.isArray(d.history) && d.history.length > 0) {
      _buf["tps"] = d.history.slice(-MAX_POINTS);
    }
    drawSpark(document.getElementById("tps-spark"), get("tps"), "#2dd4bf");
  };

  handlers["network"] = function (d) {
    const dl = d.download_mbps != null ? d.download_mbps : 0;
    const ul = d.upload_mbps  != null ? d.upload_mbps  : 0;

    document.getElementById("net-dl").textContent = dl.toFixed(2);
    document.getElementById("net-ul").textContent = ul.toFixed(2);
    document.getElementById("bs-net").textContent = `↓${dl.toFixed(1)} ↑${ul.toFixed(1)}`;

    push("net-dl", dl);
    push("net-ul", ul);

    // Right-column live network graph (fills the panel; scales with its size)
    const rcLbl = document.getElementById("net-rc-rates");
    if (rcLbl) rcLbl.textContent = `↓${dl.toFixed(1)} ↑${ul.toFixed(1)} Mbps`;
    drawNetArea(document.getElementById("net-rc-canvas"));
  };

  handlers["power"] = function (d) {
    // Per-component tiles removed — only fleet estimate shown (pwr-fleet-est via recomputeEstPower).
    // Store history for future use and update hidden compat spans.
    const cpu   = d.cpu_watts;
    const total = d.total_watts;
    if (cpu   != null) push("pwr-cpu",   cpu);
    if (total != null) push("pwr-total", total);
    updateSentiment();
  };

  // Machine chip colours for docker list
  // Populated from fleet config; falls back to a palette cycle by index.
  let MACHINE_CHIP_COLOR = {};
  const _CHIP_PALETTE = ["#2dd4bf", "#60a5fa", "#f472b6", "#fbbf24", "#a78bfa", "#34d399", "#fb923c"];
  function _chipColor(machine) {
    return MACHINE_CHIP_COLOR[machine] || MACHINE_CHIP_COLOR[(machine || "").toUpperCase()] || "#8d98b4";
  }

  // Docker action: POST to /api/docker/{machine}/{id}/{action}
  function dockerAction(machine, id, name, action) {
    const endpoint = `/api/docker/${encodeURIComponent(machine)}/${encodeURIComponent(id || name)}/${action}`;
    const _toast = (m, t) => { if (typeof LlamaWatch !== "undefined" && LlamaWatch.toast) LlamaWatch.toast(m, t); };
    fetch(endpoint, { method: "POST" })
      .then(r => r.json())
      .then(data => {
        const ok = data.status === "ok";
        _toast(ok ? (data.message || `${action} ${name} — done`)
                  : `${action} ${name} failed: ${data.message || "error"}`,
               ok ? "success" : "error");
        if (!ok) console.warn("docker action failed:", data.message);
      })
      .catch(err => { _toast(`${action} ${name} — error`, "error"); console.warn("docker action error:", err); });
  }

  // Optional friendly descriptions per container name, from config
  // (container_descriptions: { "<name>": "<description>" }). Cached for instant
  // load. Falls back to the container name when no description is set.
  let CONTAINER_DESC = {};
  try {
    CONTAINER_DESC = JSON.parse(localStorage.getItem("studio-container-desc") || "{}");
  } catch (e) {}

  function _containerTooltip(name) {
    return CONTAINER_DESC[name] || name;
  }

  handlers["docker"] = function (d) {
    const containers = d.containers || [];
    const table = document.getElementById("docker-table");
    if (!containers.length) return;

    table.innerHTML = "";
    containers.forEach((c, idx) => {
      const row = document.createElement("div");
      const isRunning = c.state === "running";
      row.className = "data-row docker-row" + (idx === 0 && isRunning ? " featured" : "");
      const stateClass =
        c.state === "running" ? "dr-state-running" :
        c.state === "exited" || c.state === "stopped" ? "dr-state-stopped" : "dr-state-other";
      const machine = c.machine || "";
      const chipColor = _chipColor(machine);
      // Container name: show full name (ellipsis via CSS if truly too long)
      const nameDisplay = esc(c.name);

      // CPU badge: only for running containers that have a measurement
      let cpuBadge = `<span class="docker-cpu docker-cpu-nil">—</span>`;
      if (isRunning && c.cpu_pct != null) {
        const pct = c.cpu_pct;
        const cls = pct >= 50 ? "docker-cpu-hi" : pct >= 10 ? "docker-cpu-mid" : "docker-cpu-lo";
        cpuBadge = `<span class="docker-cpu ${cls}">${pct.toFixed(1)}%</span>`;
      }

      row.innerHTML = `
        <span class="dr-machine-chip" style="background:${chipColor}18;color:${chipColor};border-color:${chipColor}40">${esc(machine)}</span>
        <span class="dr-name" title="${esc(_containerTooltip(c.name))}">${nameDisplay}</span>
        ${cpuBadge}
        <span class="${stateClass}">${esc(c.state)}</span>
        <span class="docker-actions"></span>
      `;
      table.appendChild(row);

      // Wire action buttons into the last cell
      const actWrap = row.querySelector(".docker-actions");
      const containerId = c.id || c.name;

      if (isRunning) {
        const stopBtn = document.createElement("button");
        stopBtn.className = "docker-act-btn btn-stop";
        stopBtn.textContent = "■";
        stopBtn.title = "Stop";
        stopBtn.addEventListener("click", () => dockerAction(machine, containerId, c.name, "stop"));
        actWrap.appendChild(stopBtn);

        const restartBtn = document.createElement("button");
        restartBtn.className = "docker-act-btn btn-restart";
        restartBtn.textContent = "↺";
        restartBtn.title = "Restart";
        restartBtn.addEventListener("click", () => dockerAction(machine, containerId, c.name, "restart"));
        actWrap.appendChild(restartBtn);
      } else {
        const startBtn = document.createElement("button");
        startBtn.className = "docker-act-btn btn-start";
        startBtn.textContent = "▶";
        startBtn.title = "Start";
        startBtn.addEventListener("click", () => dockerAction(machine, containerId, c.name, "start"));
        actWrap.appendChild(startBtn);
      }
    });

    updateAgents(containers);
    // Mobile accordion summaries
    const _runningCt = containers.filter(c => c.state === "running").length;
    _mobSum("mob-sum-docker", _runningCt + " running");
    const _agOnline = document.querySelectorAll("#agents-list .agent-dot-online").length;
    const _agTotal  = document.querySelectorAll("#agents-list .agent-row").length;
    _mobSum("mob-sum-agents", _agOnline + "/" + _agTotal + " up");
  };

  // Donut colours for process segments
  const PROC_DONUT_COLORS = ["#2dd4bf","#60a5fa","#a78bfa","#f472b6","#fb923c","#fbbf24","#34d399"];
  const PROC_DONUT_R = 30;
  const PROC_DONUT_C = 2 * Math.PI * PROC_DONUT_R; // 188.5

  function renderProcDonut(machineKey, procs, machineCpuPct) {
    const arcsG  = document.getElementById(`proc-donut-arcs-${machineKey}`);
    const legend = document.getElementById(`proc-donut-legend-${machineKey}`);
    const label  = document.getElementById(`proc-donut-label-${machineKey}`);
    if (!arcsG || !legend) return;

    arcsG.innerHTML  = "";
    legend.innerHTML = "";

    if (!procs || !procs.length) {
      if (label) label.textContent = "idle";
      return;
    }

    // Use actual machine CPU% as the donut ceiling.
    // ps %cpu is per-core so summing processes can exceed 100% on multi-core machines.
    const top = procs.slice(0, 5);
    const rawSum = top.reduce((s, p) => s + (p.cpu_pct || 0), 0);
    // If we have real machine CPU%, use it; otherwise fall back to the process sum (capped)
    const cappedSum = machineCpuPct != null
      ? Math.min(Math.max(machineCpuPct, 0), 100)
      : Math.min(rawSum, 100);
    const other = Math.max(0, 100 - cappedSum);

    // Scale process segments to fit within cappedSum (they're per-core %; normalise to machine total)
    const scale = rawSum > 0 ? cappedSum / rawSum : 1;
    const segments = top.map((p, i) => ({
      name: p.name,
      pct: Math.min((p.cpu_pct || 0) * scale, cappedSum),
      rawPct: p.cpu_pct || 0,
      color: PROC_DONUT_COLORS[i % PROC_DONUT_COLORS.length],
    }));
    if (other > 1) {
      segments.push({ name: "other", pct: other, color: "rgba(255,255,255,0.12)" });
    }

    // Render SVG arcs (stroke-dasharray technique)
    let offset = 0; // degrees
    const cx = 40, cy = 40;

    segments.forEach(seg => {
      if (seg.pct < 0.5) return;
      const arc = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      const arcLen = (seg.pct / 100) * PROC_DONUT_C;
      const gap    = PROC_DONUT_C - arcLen;
      // Rotate so this segment starts where previous ended
      const rotateDeg = -90 + offset;
      arc.setAttribute("cx", cx);
      arc.setAttribute("cy", cy);
      arc.setAttribute("r", PROC_DONUT_R);
      arc.setAttribute("fill", "none");
      arc.setAttribute("stroke", seg.color);
      arc.setAttribute("stroke-width", "10");
      arc.setAttribute("stroke-dasharray", `${arcLen.toFixed(2)} ${gap.toFixed(2)}`);
      arc.setAttribute("stroke-dashoffset", "0");
      arc.style.transform = `rotate(${rotateDeg}deg)`;
      arc.style.transformOrigin = `${cx}px ${cy}px`;
      arc.style.transition = "stroke-dasharray .5s ease";
      arcsG.appendChild(arc);
      offset += (seg.pct / 100) * 360;
    });

    // Centre label: total CPU%
    if (label) label.textContent = cappedSum.toFixed(0) + "%";

    // Legend: top procs (shown even when idle, so the machine never looks empty)
    top.forEach((p, i) => {
      if (!p.name) return;
      const item = document.createElement("div");
      item.className = "proc-leg-item";
      const nameShort = (p.name || "?").length > 10 ? p.name.slice(0, 9) + "…" : p.name;
      item.innerHTML = `
        <span class="proc-leg-dot" style="background:${PROC_DONUT_COLORS[i % PROC_DONUT_COLORS.length]}"></span>
        <span style="overflow:hidden;text-overflow:ellipsis">${esc(nameShort)}</span>
        <span class="proc-leg-pct">${(p.cpu_pct || 0).toFixed(1)}%</span>
      `;
      legend.appendChild(item);
    });
  }

  handlers["resource-hogs"] = function (d) {
    // Render per-machine donuts (new shape)
    if (d.machines && d.machines.length) {
      d.machines.forEach(m => {
        const key = m.name.toLowerCase();
        const lk = _localMachineKey || "local";
        const cpuPct = key === lk ? _localCpuPct : (m.machine_cpu_pct ?? null);
        renderProcDonut(key, m.procs || [], cpuPct);
      });
    } else if (d.processes || d.hogs) {
      // Legacy: single-machine flat list — render donut for local machine
      const procs = d.processes || d.hogs || [];
      const localProcs = procs.slice(0, 5).map(p => ({ name: p.name, cpu_pct: p.cpu_pct || 0 }));
      renderProcDonut(_localMachineKey || "local", localProcs);
    }
  };

  // ── Library shelf popup ──────────────────────────────────────────────────────
  function _openShelf(col, color) {
    const id    = col.id || col.name;
    const label = col.friendly_name || col.name || id;
    const wmId  = "shelf-" + id.slice(0, 16);

    StudioWM.open({
      id:     wmId,
      title:  label,
      icon:   "◈",
      width:  640,
      height: 500,
      render: function (body) {
        body.innerHTML = '<div style="padding:12px 16px;font-size:11px;color:var(--text-3)">Loading…</div>';
        fetch("/api/library/shelf/" + encodeURIComponent(id))
          .then(r => r.json())
          .then(d => {
            const docs = d.documents || [];
            if (!docs.length) {
              body.innerHTML = '<div style="padding:24px;text-align:center;color:var(--text-3)">No documents in this collection.</div>';
              return;
            }
            body.innerHTML = "";
            body.style.cssText = "display:flex;flex-direction:column;overflow:hidden;height:100%";

            // Header count
            const hdr = document.createElement("div");
            hdr.style.cssText = "padding:8px 14px;font-size:10px;color:var(--text-3);border-bottom:1px solid var(--rule);flex-shrink:0";
            hdr.textContent = docs.length + " documents";
            body.appendChild(hdr);

            // Scrollable doc list
            const list = document.createElement("div");
            list.style.cssText = "flex:1;overflow-y:auto;";
            docs.forEach(doc => {
              const src  = (doc.source || "").split("/").pop() || doc.id;
              const text = doc.content || "";
              const item = document.createElement("div");
              item.className = "lib-doc-item";
              item.innerHTML =
                `<div class="lib-doc-src" style="color:${color}">${esc(src)}</div>` +
                `<div class="lib-doc-preview">${esc(text)}</div>` +
                `<button class="lib-doc-copy">Copy</button>`;
              item.querySelector(".lib-doc-copy").addEventListener("click", function (e) {
                e.stopPropagation();
                navigator.clipboard.writeText(text).then(() => {
                  this.textContent = "Copied";
                  setTimeout(() => { this.textContent = "Copy"; }, 2000);
                });
              });
              list.appendChild(item);
            });
            body.appendChild(list);
          })
          .catch(() => {
            body.innerHTML = '<div style="padding:24px;text-align:center;color:var(--red-hi)">Could not load collection.</div>';
          });
      }
    });
  }

  // Update the 3-machine memory bars in the right-col Memory section
  function updateFleetRamBars(machine) {
    const n = machine.name;
    const key = n.toLowerCase();
    const pct = machine.ram_pct;
    const usedGb  = machine.ram_used_gb;
    const totalGb = machine.ram_total_gb;

    const barEl = document.getElementById(`ram-${key}-bar`);
    const lblEl = document.getElementById(`ram-${key}-label`);

    const targetBar = barEl;
    if (targetBar && pct != null) {
      targetBar.style.width = Math.min(pct, 100).toFixed(1) + "%";
    }
    if (lblEl) {
      if (usedGb != null && totalGb != null) {
        lblEl.textContent = usedGb.toFixed(1) + "/" + totalGb.toFixed(0) + "G";
      } else if (pct != null) {
        lblEl.textContent = Math.round(pct) + "%";
      }
    }
  }

  handlers["fleet"] = function (d) {
    const machines = d.machines || [];
    machines.forEach((m, i) => {
      // Prefix is the lowercased machine name — matches the dynamically
      // generated card/element IDs (fm-<prefix>-*).
      const prefix = (m.name || "").toLowerCase();
      if (prefix) updateFleetMachine(prefix, m);
      // Compact vitals strip on Command shows remote (non-first) machines
      if (i > 0) {
        updateCmdFleetMini(prefix, m);
      }
      // Update persistent bottom strip for all machines
      updateBottomStripMachine(m);
      // Update 3-machine RAM bars in Memory section
      updateFleetRamBars(m);
      // Mobile accordion summaries for fleet machine cards
      const _fk = m.name.toLowerCase();
      _mobSum("mob-sum-fm-" + _fk,
        (m.cpu_pct != null ? "CPU " + Math.round(m.cpu_pct) + "%" : "") +
        (m.ram_pct != null ? " · RAM " + Math.round(m.ram_pct) + "%" : ""));
    });
    // Memory section summary: local machine RAM (primary indicator)
    const _local = machines.find(m => m.name.toLowerCase() === (_localMachineKey || "local")) || machines[0];
    if (_local && _local.ram_pct != null) _mobSum("mob-sum-mem", _local.name + " " + Math.round(_local.ram_pct) + "% RAM");
  };

  function updateBottomStripMachine(machine) {
    const n = machine.name;
    const key = n.toLowerCase();
    const grp = document.getElementById("bs-" + key + "-grp") ||
                document.querySelector(`.bs-machine-group[data-machine="${n}"]`);

    // Bottom strip CPU/RAM/TEMP spans use IDs bs-{key}-cpu etc.
    const cpuEl  = document.getElementById("bs-" + key + "-cpu");
    const ramEl  = document.getElementById("bs-" + key + "-ram");
    const tempEl = document.getElementById("bs-" + key + "-temp");

    // Find the wrapping group element (sibling lookup via chip)
    const chipEl = document.querySelector(`.bs-chip[data-machine="${n}"]`) ||
                   (cpuEl && cpuEl.closest(".bs-machine-group"));
    if (chipEl && chipEl.closest) {
      const groupEl = chipEl.closest ? chipEl : chipEl.parentElement;
      if (groupEl) groupEl.classList.toggle("bs-offline", !machine.online);
    }

    if (cpuEl && machine.cpu_pct != null) {
      cpuEl.textContent = Math.round(machine.cpu_pct) + "%";
      const p = machine.cpu_pct;
      cpuEl.style.color = p > 80 ? "var(--red-hi)" : p > 50 ? "var(--amber-hi)" : "var(--cyan-hi)";
    }
    if (ramEl && machine.ram_pct != null) {
      ramEl.textContent = Math.round(machine.ram_pct) + "%";
      const p = machine.ram_pct;
      ramEl.style.color = p > 85 ? "var(--red-hi)" : p > 65 ? "var(--amber-hi)" : "var(--green-hi)";
    }
    if (tempEl && machine.cpu_temp != null) {
      tempEl.textContent = Math.round(machine.cpu_temp) + "°";
      const t = machine.cpu_temp;
      tempEl.style.color = t > 75 ? "var(--red-hi)" : t > 60 ? "var(--amber-hi)" : "var(--orange-hi)";
    }

    // Track CPU% for power estimate
    if (machine.cpu_pct != null && PWR_MODEL[key]) {
      _fleetPct[key] = machine.cpu_pct;
      recomputeEstPower();
    }
  }

  function updateCmdFleetMini(prefix, machine) {
    const cpuEl  = document.getElementById(`cfs-${prefix}-cpu`);
    const ramEl  = document.getElementById(`cfs-${prefix}-ram`);
    const tempEl = document.getElementById(`cfs-${prefix}-temp`);
    const row    = document.getElementById(`cfs-${prefix}`);

    if (row) {
      row.classList.toggle("cfs-offline", !machine.online);
    }
    if (cpuEl) {
      cpuEl.textContent = machine.cpu_pct != null ? Math.round(machine.cpu_pct) + "%" : "—";
      const pct = machine.cpu_pct || 0;
      cpuEl.style.color = pct > 80 ? "var(--red-hi)" : pct > 50 ? "var(--amber-hi)" : "var(--cyan-hi)";
    }
    if (ramEl) {
      ramEl.textContent = machine.ram_pct != null ? Math.round(machine.ram_pct) + "%" : "—";
      const pct = machine.ram_pct || 0;
      ramEl.style.color = pct > 85 ? "var(--red-hi)" : pct > 65 ? "var(--amber-hi)" : "var(--green-hi)";
    }
    if (tempEl) {
      tempEl.textContent = machine.cpu_temp != null ? machine.cpu_temp.toFixed(0) + "°" : "—";
      const tmp = machine.cpu_temp || 0;
      tempEl.style.color = tmp > 75 ? "var(--red-hi)" : tmp > 60 ? "var(--amber-hi)" : "var(--orange-hi)";
    }
  }

  handlers["token-usage"] = function (d) {
    if (!d || !d.by_model) return;

    renderTokenBars(d.by_model);

    const totalReq = d.total_requests || 0;
    const totalTok = d.total_tokens   || 0;

    const reqEl = document.getElementById("tu-total-req");
    const tokEl = document.getElementById("tu-total-tok");
    if (reqEl) reqEl.textContent = totalReq >= 1000 ? (totalReq/1000).toFixed(0)+"K" : String(totalReq);
    if (tokEl) tokEl.textContent = totalTok >= 1000000
      ? (totalTok/1000000).toFixed(1)+"M"
      : totalTok >= 1000
      ? (totalTok/1000).toFixed(0)+"K"
      : String(totalTok);

  };

  handlers["library"] = function (d) {
    // If not configured or errored, show clean empty state — not an alarm
    if (!d || d.error || d.configured === false) {
      const feed = document.getElementById("library-feed");
      if (feed) feed.innerHTML = `
        <div class="know-empty-state know-empty-state-sm">
          <div class="know-empty-icon">◈</div>
          <div class="know-empty-title">Not configured</div>
          <div class="know-empty-sub">Configure ChromaDB URL in widget settings</div>
        </div>`;
      return;
    }

    const shelves = d.shelves || d.collections || [];
    const chunks = shelves.reduce((s, c) => s + (c.count || 0), 0);
    const colls  = shelves.length;

    const chEl = document.getElementById("lib-chunks");
    const doEl = document.getElementById("lib-docs");
    const coEl = document.getElementById("lib-collections");
    if (chEl) chEl.textContent = chunks > 0 ? (chunks >= 1000 ? (chunks/1000).toFixed(1)+"K" : chunks) : "—";
    if (doEl) doEl.textContent = "—";
    if (coEl) coEl.textContent = colls > 0 ? String(colls) : "—";

    if (shelves.length > 0) {
      renderLibSegment(shelves);
      // Render shelf list
      const feed = document.getElementById("library-feed");
      if (feed) {
        feed.innerHTML = "";
        shelves.slice(0, 12).forEach((col, i) => {
          const color = LIB_SEG_COLORS[i % LIB_SEG_COLORS.length];
          const name = col.friendly_name || col.name || "—";
          const count = col.count != null ? (col.count >= 1000 ? (col.count/1000).toFixed(1)+"K" : col.count) : "—";
          const row = document.createElement("div");
          row.className = "data-row lib-shelf-row";
          row.style.cssText = "grid-template-columns:1fr auto;cursor:pointer";
          row.title = "Click to browse documents";
          row.innerHTML = `
            <span class="dr-name" style="color:${color}">${esc(name)}</span>
            <span style="color:var(--text-3);font-size:10px;font-variant-numeric:tabular-nums">${count} chunks ›</span>`;
          row.addEventListener("click", () => _openShelf(col, color));
          feed.appendChild(row);
        });
      }
    } else {
      const feed = document.getElementById("library-feed");
      if (feed) feed.innerHTML = `
        <div class="know-empty-state know-empty-state-sm">
          <div class="know-empty-icon">◈</div>
          <div class="know-empty-title">No collections found</div>
          <div class="know-empty-sub">ChromaDB empty or unreachable</div>
        </div>`;
    }
  };

  // ── Press Room handler ─────────────────────────────────────────────────────
  const PR_STATUS_COLORS = {
    "tier-0": "#2dd4bf",
    "tier-1": "#a78bfa",
    "tier-2": "#fbbf24",
    "article": "#60a5fa",
  };

  handlers["press-room"] = function (d) {
    const feed = document.getElementById("pressroom-feed");
    if (!feed) return;

    const articles = d.articles || [];
    if (!articles.length) {
      feed.innerHTML = `
        <div class="know-empty-state know-empty-state-sm">
          <div class="know-empty-icon">▤</div>
          <div class="know-empty-title">No articles yet</div>
          <div class="know-empty-sub">Press Room v2 articles appear here</div>
        </div>`;
      return;
    }

    feed.innerHTML = "";
    articles.forEach(a => {
      const color = PR_STATUS_COLORS[a.status] || "#60a5fa";
      const title = a.title || "(untitled)";
      const when  = a.when  || "";
      const row = document.createElement("div");
      row.className = "pr-article-row";
      row.innerHTML = `
        <div class="pr-article-title">${esc(title)}</div>
        <div class="pr-article-meta">
          <span class="pr-status-pill" style="color:${color};border-color:${color}20;background:${color}10">${esc(a.status || "")}</span>
          <span class="pr-when">${esc(when)}</span>
        </div>`;
      feed.appendChild(row);
    });

    // Update sub header count
    const sub = document.querySelector("#pressroom-panel .know-section-sub");
    if (sub && d.total) sub.textContent = `${d.total} articles`;
  };

  // ── Sentiment bar ──────────────────────────────────────────────────────────
  function updateSentiment() {
    const tps  = get("tps");
    let score = 50;
    if (tps.length > 0) {
      const avgTps = tps.reduce((a,b) => a+b, 0) / tps.length;
      score = Math.min(100, 20 + avgTps * 1.5);
    }
    document.getElementById("sentiment-fill").style.width = score.toFixed(1) + "%";
  }

  // ── HTML escape ────────────────────────────────────────────────────────────
  function esc(s) {
    return String(s || "")
      .replace(/&/g,"&amp;")
      .replace(/</g,"&lt;")
      .replace(/>/g,"&gt;")
      .replace(/"/g,"&quot;");
  }

  // ── SSE connection ─────────────────────────────────────────────────────────
  let _es = null;
  let _retryMs = 2000;

  function connect() {
    _es = new EventSource("/sse");

    _es.addEventListener("widget", function(e) {
      let msg;
      try { msg = JSON.parse(e.data); } catch { return; }
      const { id, data } = msg;
      if (handlers[id]) {
        try { handlers[id](data); } catch(err) { console.warn("studio handler error", id, err); }
      }
    });

    _es.addEventListener("full", function(e) {
      let state;
      try { state = JSON.parse(e.data); } catch { return; }
      Object.entries(state).forEach(([id, data]) => {
        if (handlers[id]) {
          try { handlers[id](data); } catch { }
        }
      });
    });

    _es.onerror = function() {
      _es.close();
      setTimeout(connect, _retryMs);
      _retryMs = Math.min(_retryMs * 1.5, 30000);
    };
    _es.onopen = function() { _retryMs = 2000; };
  }

  connect();

  // ── StudioWM — Minimizable Window Manager ─────────────────────────────────
  const StudioWM = (function () {
    "use strict";

    // Map<id, {el, dockBtn, onClose}>
    const _windows = new Map();
    let _zBase = 8800;
    let _zNext = _zBase;

    const _dock = document.getElementById("swm-dock");

    // ── Bring window to front ──────────────────────────────────────────────
    function _focus(id) {
      const rec = _windows.get(id);
      if (!rec) return;
      _zNext++;
      rec.el.style.zIndex = _zNext;
      rec.el.classList.add("swm-focused");
      _windows.forEach((r, k) => {
        if (k !== id) r.el.classList.remove("swm-focused");
      });
    }

    // ── Open (or restore) a window ─────────────────────────────────────────
    // opts: { id, title, icon, render(contentEl), onClose, width, height }
    function open(opts) {
      const id = opts.id;
      if (_windows.has(id)) {
        restore(id);
        _focus(id);
        return _windows.get(id).el;
      }

      const w = opts.width  || 820;
      const h = opts.height || 560;

      // Center loosely (slightly offset from perfect center)
      const left = Math.max(20, Math.floor((window.innerWidth  - w) / 2) + _windows.size * 24);
      const top  = Math.max(60, Math.floor((window.innerHeight - h) / 2) + _windows.size * 24);

      // Build window element
      const el = document.createElement("div");
      el.className = "swm-window";
      el.style.cssText = `left:${left}px;top:${top}px;width:${w}px;height:${h}px;`;

      const iconHtml = opts.icon ? `<span class="swm-title-icon">${opts.icon}</span>` : "";
      el.innerHTML = `
        <div class="swm-titlebar">
          ${iconHtml}
          <span class="swm-title-text">${esc(opts.title || id)}</span>
          <button class="swm-ctrl-btn swm-min-btn" title="Minimise">&#8212;</button>
          <button class="swm-ctrl-btn swm-ctrl-btn-close swm-close-btn" title="Close">&times;</button>
        </div>
        <div class="swm-body"></div>
      `;

      document.body.appendChild(el);

      // Wire titlebar drag
      _makeDraggable(el, el.querySelector(".swm-titlebar"));

      // Render content into body
      const bodyEl = el.querySelector(".swm-body");
      try { opts.render && opts.render(bodyEl); } catch (err) { console.warn("WM render error:", err); }

      // Focus on click anywhere
      el.addEventListener("mousedown", () => _focus(id), { capture: true });

      // Minimise button
      el.querySelector(".swm-min-btn").addEventListener("click", e => {
        e.stopPropagation();
        minimise(id);
      });

      // Close button
      el.querySelector(".swm-close-btn").addEventListener("click", e => {
        e.stopPropagation();
        close(id);
      });

      // Resize grip — bottom-right corner drag
      const grip = document.createElement("div");
      grip.className = "swm-resize-grip";
      el.appendChild(grip);
      grip.addEventListener("mousedown", function (e) {
        e.preventDefault();
        e.stopPropagation();
        const startX = e.clientX, startY = e.clientY;
        const startW = el.offsetWidth,  startH = el.offsetHeight;
        function onMove(e) {
          const nw = Math.max(280, startW + (e.clientX - startX));
          const nh = Math.max(200, startH + (e.clientY - startY));
          el.style.width  = nw + "px";
          el.style.height = nh + "px";
          const rec = _windows.get(id);
          if (rec && rec._fitAddon) {
            try { rec._fitAddon.fit(); rec._sendResize && rec._sendResize(); } catch {}
          }
        }
        function onUp() {
          document.removeEventListener("mousemove", onMove);
          document.removeEventListener("mouseup", onUp);
        }
        document.addEventListener("mousemove", onMove);
        document.addEventListener("mouseup", onUp);
      });

      // Dock button
      const dockBtn = document.createElement("button");
      dockBtn.className = "swm-dock-btn swm-dock-active";
      dockBtn.dataset.wmId = id;
      dockBtn.innerHTML = `
        <span class="swm-dock-indicator"></span>
        <span class="swm-dock-label">${esc(opts.title || id)}</span>
      `;
      dockBtn.addEventListener("click", () => {
        if (el.classList.contains("swm-hidden")) {
          restore(id);
        } else {
          _focus(id);
        }
      });
      _dock && _dock.appendChild(dockBtn);

      const rec = { el, dockBtn, onClose: opts.onClose, title: opts.title || id };
      _windows.set(id, rec);
      _focus(id);
      return el;
    }

    // ── Minimise: hide window, update dock indicator ───────────────────────
    function minimise(id) {
      const rec = _windows.get(id);
      if (!rec) return;
      rec.el.classList.add("swm-hidden");
      const ind = rec.dockBtn.querySelector(".swm-dock-indicator");
      if (ind) ind.classList.add("swm-dock-min");
      rec.dockBtn.classList.remove("swm-dock-active");
    }

    // ── Restore: show window ───────────────────────────────────────────────
    function restore(id) {
      const rec = _windows.get(id);
      if (!rec) return;
      rec.el.classList.remove("swm-hidden");
      const ind = rec.dockBtn.querySelector(".swm-dock-indicator");
      if (ind) ind.classList.remove("swm-dock-min");
      rec.dockBtn.classList.add("swm-dock-active");
      _focus(id);
      // Re-fit any terminal inside (in case resize happened while minimised)
      const termFit = rec._fitAddon;
      if (termFit) {
        requestAnimationFrame(() => {
          try { termFit.fit(); rec._sendResize && rec._sendResize(); } catch {}
        });
      }
    }

    // ── Close: destroy DOM + dock entry ───────────────────────────────────
    function close(id) {
      const rec = _windows.get(id);
      if (!rec) return;
      try { rec.onClose && rec.onClose(); } catch {}
      rec.el.remove();
      rec.dockBtn.remove();
      _windows.delete(id);
    }

    // ── Drag helper ───────────────────────────────────────────────────────
    function _makeDraggable(win, handle) {
      let _ox = 0, _oy = 0, _startX = 0, _startY = 0;
      handle.addEventListener("mousedown", onDown);
      function onDown(e) {
        if (e.target.closest("button")) return;
        e.preventDefault();
        _startX = e.clientX;
        _startY = e.clientY;
        _ox = win.offsetLeft;
        _oy = win.offsetTop;
        document.addEventListener("mousemove", onMove);
        document.addEventListener("mouseup", onUp);
      }
      function onMove(e) {
        const dx = e.clientX - _startX;
        const dy = e.clientY - _startY;
        const nx = Math.max(0, _ox + dx);
        const ny = Math.max(0, _oy + dy);
        win.style.left = nx + "px";
        win.style.top  = ny + "px";
      }
      function onUp() {
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
      }
    }

    // Public API (expose _windows for terminal fitAddon hookup)
    return { open, minimise, restore, close, _windows };
  })();

  // ── TermTabs — floating terminal windows with tab-strip taskbar ──────────
  const TermTabs = (function () {
    "use strict";

    // id → { label, wmId, ws, term, fitAddon, resizeObs }
    const _sessions = new Map();
    let _counter = 0;

    const TERM_THEME = {
      background:          "#0a0c14", foreground:    "#b3b1ad",
      cursor:              "#2dd4bf", cursorAccent:  "#0a0c14",
      selectionBackground: "rgba(45,212,191,0.25)",
      black:    "#0a0c14", red:    "#ef5350", green:   "#3dd68c",
      yellow:   "#f5a623", blue:   "#9b7bf7", magenta: "#c678dd",
      cyan:     "#2dd4bf", white:  "#b3b1ad",
      brightBlack:   "#565869", brightRed:     "#ef5350", brightGreen:   "#3dd68c",
      brightYellow:  "#f5a623", brightBlue:    "#9b7bf7", brightMagenta: "#c678dd",
      brightCyan:    "#22d3ee", brightWhite:   "#e6e6e6",
    };

    function _isVisible(wmId) {
      const rec = StudioWM._windows.get(wmId);
      return rec && !rec.el.classList.contains("swm-hidden");
    }

    function _renderTabs() {
      const tabsEl = document.getElementById("term-tabs");
      if (!tabsEl) return;
      tabsEl.innerHTML = "";
      for (const [id, sess] of _sessions) {
        const visible = _isVisible(sess.wmId);
        const tab = document.createElement("button");
        tab.className = "term-tab" + (visible ? " active" : "");

        const icon = document.createElement("span");
        icon.textContent = ">_";
        const lbl = document.createElement("span");
        lbl.textContent = sess.label;
        const cls = document.createElement("span");
        cls.className = "term-tab-close";
        cls.textContent = "×";
        cls.dataset.closeId = id;

        tab.appendChild(icon);
        tab.appendChild(lbl);
        tab.appendChild(cls);

        tab.addEventListener("click", function (e) {
          const cid = e.target.dataset.closeId || e.target.closest("[data-close-id]")?.dataset.closeId;
          if (cid) { _destroy(cid); return; }
          if (_isVisible(sess.wmId)) {
            StudioWM.minimise(sess.wmId);
          } else {
            StudioWM.restore(sess.wmId);
          }
          _renderTabs();
        });
        tabsEl.appendChild(tab);
      }
    }

    function _destroy(id) {
      // StudioWM.close → triggers onClose → cleanup
      const sess = _sessions.get(id);
      if (sess) StudioWM.close(sess.wmId);
    }

    function open() {
      const id    = "t" + (++_counter);
      const wmId  = "term-wm-" + id;
      const label = "Shell " + _counter;
      const sid   = "studio-" + Math.random().toString(36).slice(2, 10);

      const sess = { label, wmId, ws: null, term: null, fitAddon: null, resizeObs: null };
      _sessions.set(id, sess);

      const winEl = StudioWM.open({
        id:     wmId,
        title:  ">_ " + label,
        icon:   ">_",
        width:  920,
        height: 580,

        render(bodyEl) {
          const termBody = document.createElement("div");
          termBody.className = "swm-term-body";
          bodyEl.appendChild(termBody);

          // Re-focus xterm whenever user clicks anywhere in the terminal window
          // (xterm loses focus if user interacts with the dashboard UI then comes back)
          bodyEl.addEventListener("mousedown", function () {
            if (sess.term) sess.term.focus();
          });

          const proto = location.protocol === "https:" ? "wss:" : "ws:";
          sess.ws = new WebSocket(`${proto}//${location.host}/ws/terminal/${sid}`);
          sess.ws.binaryType = "arraybuffer";

          sess.ws.onopen = function () {
            sess.term = new Terminal({
              cursorBlink: true, fontSize: 14,
              fontFamily: "'JetBrains Mono','Fira Code','Cascadia Code','Menlo',monospace",
              theme: TERM_THEME, allowProposedApi: true,
            });
            sess.fitAddon = new FitAddon.FitAddon();
            sess.term.loadAddon(sess.fitAddon);
            if (typeof WebLinksAddon !== "undefined") {
              sess.term.loadAddon(new WebLinksAddon.WebLinksAddon());
            }
            sess.term.open(termBody);
            sess.term.onData(function (data) {
              if (sess.ws && sess.ws.readyState === WebSocket.OPEN) sess.ws.send(data);
            });

            const rec = StudioWM._windows.get(wmId);
            if (rec) {
              rec._fitAddon   = sess.fitAddon;
              rec._sendResize = function () {
                if (sess.ws && sess.ws.readyState === WebSocket.OPEN && sess.term) {
                  sess.ws.send(JSON.stringify({ type: "resize", cols: sess.term.cols, rows: sess.term.rows }));
                }
              };
            }

            sess.resizeObs = new ResizeObserver(function () {
              if (sess.fitAddon && sess.term) {
                sess.fitAddon.fit();
                rec && rec._sendResize && rec._sendResize();
              }
            });
            sess.resizeObs.observe(termBody);

            requestAnimationFrame(function () { sess.fitAddon.fit(); sess.term.focus(); });
          };

          sess.ws.onmessage = function (event) {
            if (!sess.term) return;
            if (event.data instanceof ArrayBuffer) sess.term.write(new Uint8Array(event.data));
            else sess.term.write(event.data);
          };
          sess.ws.onclose = function () {
            if (sess.term) sess.term.write("\r\n\x1b[33m[Connection closed]\x1b[0m\r\n");
          };
          sess.ws.onerror = function () {
            if (sess.term) sess.term.write("\r\n\x1b[31m[Connection error]\x1b[0m\r\n");
          };
        },

        onClose() {
          if (sess.resizeObs) { sess.resizeObs.disconnect(); sess.resizeObs = null; }
          if (sess.ws)        { sess.ws.close(); sess.ws = null; }
          if (sess.term)      { sess.term.dispose(); sess.term = null; }
          sess.fitAddon = null;
          _sessions.delete(id);
          _renderTabs();
        },
      });

      // Remove terminal windows from the generic swm-dock — our tab strip handles them
      if (winEl) {
        const rec = StudioWM._windows.get(wmId);
        if (rec && rec.dockBtn) { rec.dockBtn.remove(); rec.dockBtn = null; }

        // Inject arrow key buttons into titlebar
        const titlebar = winEl.querySelector(".swm-titlebar");
        const minBtn   = titlebar && titlebar.querySelector(".swm-min-btn");
        if (titlebar && minBtn) {
          const TERM_KEYS = [
            { label: "Esc", seq: "\x1b",   title: "Escape" },
            { label: "↑",   seq: "\x1b[A", title: "Arrow Up" },
            { label: "↓",   seq: "\x1b[B", title: "Arrow Down" },
            { label: "Tab", seq: "\t",     title: "Tab / autocomplete" },
            { label: "↵",   seq: "\r",     title: "Enter" },
          ];
          const group = document.createElement("div");
          group.className = "swm-term-keys";
          TERM_KEYS.forEach(function (k) {
            const b = document.createElement("button");
            b.className = "swm-term-key-btn";
            b.textContent = k.label;
            b.title = k.title;
            b.addEventListener("mousedown", function (e) { e.preventDefault(); }); // keep xterm focus
            b.addEventListener("click", function () {
              if (sess.ws && sess.ws.readyState === WebSocket.OPEN) {
                sess.ws.send(k.seq);
                if (sess.term) sess.term.focus();
              }
            });
            group.appendChild(b);
          });
          titlebar.insertBefore(group, minBtn);
        }

        // Override WM's × to minimise instead of destroy (tab strip × destroys)
        const closeBtn = winEl.querySelector(".swm-close-btn");
        if (closeBtn) {
          const fresh = closeBtn.cloneNode(true);
          closeBtn.replaceWith(fresh);
          fresh.addEventListener("click", function (e) {
            e.stopPropagation();
            // On mobile (tab strip hidden) — fully destroy so session doesn't get lost
            // On desktop — minimise, tab strip lets user restore it
            if (window.innerWidth <= 640) {
              _destroy(id);
            } else {
              StudioWM.minimise(wmId);
            }
            _renderTabs();
          });
        }
      }

      _renderTabs();
    }

    const newBtn = document.getElementById("term-new-btn");
    if (newBtn) newBtn.addEventListener("click", function () {
      // On mobile: if a session already exists, restore it rather than opening another
      if (window.innerWidth <= 640 && _sessions.size > 0) {
        const existing = _sessions.values().next().value;
        if (existing) { StudioWM.restore(existing.wmId); return; }
      }
      open();
    });

    // Expose last session for voice injection
    function getSession() {
      var last = null;
      _sessions.forEach(function (s) { last = s; });
      return last;
    }

    return { open, getSession };
  })();

  // ── ChatPanel — chat with local models in a floating, persistent window ──
  const ChatPanel = (function () {
    "use strict";
    const WM_ID = "chat";
    let _ws = null, _model = null, _busy = false, _searchOn = false;
    let _messages = [];          // {role, content} conversation history
    let _attachment = null;      // {name, text}
    let _els = {};               // cached DOM refs (rebuilt each open)
    let _modelsList = [];        // /api/models — carries per-backend context_window + model_id

    const estTokens = s => Math.ceil((s || "").length / 4);

    function _ctxWindow() {
      // 1) explicit per-backend context_window from /api/models (reliable)
      const sel = _modelsList.find(m => m.name === _model);
      if (sel && sel.context_window) return sel.context_window;
      // 2) best-effort auto-detect from the slots feed, keyed by model_id
      const ctxMap = window._chatCtx || {};
      if (sel && sel.model_id && ctxMap[sel.model_id.toLowerCase()]) return ctxMap[sel.model_id.toLowerCase()];
      if (ctxMap[(_model || "").toLowerCase()]) return ctxMap[(_model || "").toLowerCase()];
      return 0;
    }

    function _updateContextBar() {
      if (!_els.ctx) return;
      const used = estTokens(_messages.map(m => m.content || "").join(""));
      const window_ = _ctxWindow();
      if (window_) {
        const pct = Math.min(100, Math.round((used / window_) * 100));
        const colour = pct >= 90 ? "var(--red-hi)" : pct >= 70 ? "var(--amber-hi)" : "var(--text-3)";
        _els.ctx.innerHTML = `<span style="color:${colour}">~${used.toLocaleString()} / ${window_.toLocaleString()} tokens (${pct}%)</span>`;
        if (pct >= 90) _els.ctx.title = "Approaching the model's context limit — older messages may be forgotten.";
      } else {
        _els.ctx.textContent = `~${used.toLocaleString()} tokens`;
      }
    }

    function _addBubble(role, text) {
      const row = document.createElement("div");
      row.className = "chat-bubble chat-bubble-" + role;
      row.textContent = text || "";
      _els.feed.appendChild(row);
      _els.feed.scrollTop = _els.feed.scrollHeight;
      return row;
    }

    function _connect() {
      if (_ws) { try { _ws.close(); } catch {} _ws = null; }
      if (!_model) return;
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      _ws = new WebSocket(`${proto}//${location.host}/ws/chat/${encodeURIComponent(_model)}`);
      _ws.addEventListener("message", _onMessage);
      _ws.addEventListener("close", () => {
        _ws = null;
        // If a stream was in flight (e.g. server restarted), don't leave the
        // Send button stuck — reset and let the user retry.
        if (_busy) {
          _finishStream();
          _addBubble("note", "Connection lost — message may be incomplete. Try again.");
        }
      });
    }

    let _streamBubble = null, _streamText = "";
    function _onMessage(ev) {
      let msg; try { msg = JSON.parse(ev.data); } catch { return; }
      if (msg.type === "token") {
        if (!_streamBubble) { _streamBubble = _addBubble("assistant", ""); _streamText = ""; }
        _streamText += msg.content || "";
        _streamBubble.textContent = _streamText;
        _els.feed.scrollTop = _els.feed.scrollHeight;
      } else if (msg.type === "error") {
        _addBubble("error", "⚠ " + (msg.message || "error"));
        _finishStream();
      } else if (msg.type === "done") {
        _finishStream();
      }
    }

    function _finishStream() {
      if (_streamBubble) { _messages.push({ role: "assistant", content: _streamText }); }
      _streamBubble = null; _streamText = "";
      _busy = false;
      if (_els.send) { _els.send.disabled = false; _els.send.textContent = "Send"; }
      _updateContextBar();
    }

    async function _send() {
      if (_busy) return;
      const text = (_els.input.value || "").trim();
      if (!text || !_model) return;
      _els.input.value = "";

      // Build the user message, optionally augmented with file + web search
      let content = text;
      if (_attachment) {
        content = `Attached file "${_attachment.name}":\n\n${_attachment.text}\n\n---\n\n${text}`;
        _addBubble("note", `📎 ${_attachment.name} attached`);
        _attachment = null;
        if (_els.attachLbl) _els.attachLbl.textContent = "";
      }
      if (_searchOn) {
        const noteEl = _addBubble("note", "🔍 searching the web…");
        let ok = false;
        try {
          const r = await fetch("/api/search?q=" + encodeURIComponent(text), { method: "POST" });
          const d = await r.json();
          const hasAny = (d.results && d.results.length) || (d.answers && d.answers.length) || (d.infoboxes && d.infoboxes.length);
          if (hasAny) {
            let blocks = "";
            if (d.answers && d.answers.length)   blocks += "Direct answers:\n" + d.answers.join("\n") + "\n\n";
            if (d.infoboxes && d.infoboxes.length) blocks += "Reference:\n" + d.infoboxes.join("\n\n") + "\n\n";
            if (d.results && d.results.length) {
              blocks += d.results.slice(0, 5).map((x, i) =>
                `[${i + 1}] ${x.title}\n${x.url}\n${x.content || ""}`).join("\n\n");
            }
            content = `Current web search results for "${text}":\n\n${blocks}\n\n---\n\n`
              + `Answer the user's question using these results as your source of current information. `
              + `Give the most specific answer the results support; cite result number(s). If the results only `
              + `cover part of the question, answer that part and say what's missing. Question: ${text}`;
            noteEl.textContent = `🔍 ${(d.results || []).length} web results`;
            ok = true;
          } else if (d.disabled) {
            noteEl.textContent = "🔍 Web search isn't configured — set a SearXNG URL in Settings → General.";
          } else {
            noteEl.textContent = "🔍 No web results found.";
          }
        } catch {
          noteEl.textContent = "🔍 Web search couldn't be reached.";
        }
        // If search was requested but produced nothing, tell the model plainly
        // so it doesn't pretend to have live data.
        if (!ok) {
          content = `${text}\n\n(Note: a web search was attempted but returned no usable results. `
            + `If you don't have current information, say so plainly — do not invent live data.)`;
        }
      }

      _addBubble("user", text);
      _messages.push({ role: "user", content });
      _updateContextBar();

      if (!_ws || _ws.readyState !== WebSocket.OPEN) {
        _connect();
        await new Promise(res => {
          const t = setInterval(() => { if (_ws && _ws.readyState === WebSocket.OPEN) { clearInterval(t); res(); } }, 50);
          setTimeout(() => { clearInterval(t); res(); }, 4000);
        });
      }
      if (!_ws || _ws.readyState !== WebSocket.OPEN) { _addBubble("error", "⚠ Could not reach the model."); return; }

      _busy = true;
      _els.send.disabled = true; _els.send.textContent = "…";
      _ws.send(JSON.stringify({ messages: _messages.slice() }));
    }

    function _buildUI(body, models) {
      _modelsList = models || [];
      body.style.cssText = "padding:0;display:flex;flex-direction:column;height:100%;";
      const opts = models.map(m => {
        const label = m.friendly_name || m.name;
        const dot = ["ok", "online", "healthy"].includes(m.health) ? "" : " (offline)";
        return `<option value="${esc(m.name)}">${esc(label)}${dot}</option>`;
      }).join("");
      body.innerHTML = `
        <div class="chat-toolbar">
          <select class="chat-model-select">${opts || '<option>No backends</option>'}</select>
          <label class="chat-tool-toggle" title="Run a web search before answering">
            <input type="checkbox" class="chat-search-cb"><span>🔍 Web search</span>
          </label>
          <button class="chat-tool-btn chat-attach-btn" title="Attach a file as context">📎</button>
          <span class="chat-attach-lbl"></span>
          <button class="chat-tool-btn chat-clear-btn" title="Clear conversation" style="margin-left:auto">Clear</button>
          <input type="file" class="chat-file-input" hidden>
        </div>
        <div class="chat-feed"></div>
        <div class="chat-ctx"></div>
        <div class="chat-input-row">
          <textarea class="chat-input" rows="1" placeholder="Message your model…"></textarea>
          <button class="chat-send">Send</button>
        </div>
      `;
      _els = {
        feed:   body.querySelector(".chat-feed"),
        input:  body.querySelector(".chat-input"),
        send:   body.querySelector(".chat-send"),
        ctx:    body.querySelector(".chat-ctx"),
        select: body.querySelector(".chat-model-select"),
        attachLbl: body.querySelector(".chat-attach-lbl"),
      };

      _model = _els.select.value || (models[0] && models[0].name) || null;
      _connect();
      _updateContextBar();
      if (_messages.length) _messages.forEach(m => _addBubble(m.role === "user" ? "user" : "assistant", m.content));

      _els.select.addEventListener("change", () => { _model = _els.select.value; _connect(); _updateContextBar(); });
      body.querySelector(".chat-search-cb").addEventListener("change", e => { _searchOn = e.target.checked; });
      _els.send.addEventListener("click", _send);
      _els.input.addEventListener("keydown", e => {
        if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); _send(); }
      });
      body.querySelector(".chat-clear-btn").addEventListener("click", () => {
        _messages = []; _els.feed.innerHTML = ""; _updateContextBar();
      });
      const fileInput = body.querySelector(".chat-file-input");
      body.querySelector(".chat-attach-btn").addEventListener("click", () => fileInput.click());
      fileInput.addEventListener("change", async () => {
        const f = fileInput.files[0]; if (!f) return;
        _els.attachLbl.textContent = "extracting…";
        const fd = new FormData(); fd.append("file", f);
        try {
          const r = await fetch("/api/chat/extract", { method: "POST", body: fd });
          const d = await r.json();
          if (d.error) { _els.attachLbl.textContent = ""; _addBubble("note", "⚠ " + d.error); }
          else {
            _attachment = { name: d.name, text: d.text };
            _els.attachLbl.textContent = `📎 ${d.name}${d.truncated ? " (truncated)" : ""}`;
          }
        } catch { _els.attachLbl.textContent = ""; _addBubble("note", "Attach failed."); }
        fileInput.value = "";
      });
    }

    async function open() {
      if (StudioWM._windows && StudioWM._windows.has(WM_ID)) { StudioWM.restore(WM_ID); return; }
      let models = [];
      try { models = (await (await fetch("/api/models")).json()).models || []; } catch {}
      const winEl = StudioWM.open({
        id: WM_ID, title: "Chat", icon: "💬", width: 560, height: 620,
        render: body => _buildUI(body, models),
        onClose: () => {
          if (_ws) { try { _ws.close(); } catch {} _ws = null; }
          _removeTab();
        },
      });
      _ensureTab(winEl);
    }

    // ── Tab in the terminal bar (instead of the bottom-right dock label) ──
    let _tab = null, _tabObs = null;
    function _ensureTab(winEl) {
      // Drop the generic bottom-right dock button — the bar tab replaces it
      const rec = StudioWM._windows && StudioWM._windows.get(WM_ID);
      if (rec && rec.dockBtn) { rec.dockBtn.remove(); rec.dockBtn = null; }

      if (!_tab) {
        _tab = document.createElement("div");
        _tab.className = "chat-tab";
        _tab.innerHTML = `<span>💬 Chat</span><span class="chat-tab-close" title="Close">&times;</span>`;
        _tab.addEventListener("click", e => {
          if (e.target.closest(".chat-tab-close")) { StudioWM.close(WM_ID); return; }
          StudioWM.restore(WM_ID);
        });
        // Insert just after the 💬 launcher button, left of the terminal tabs
        const bar = document.getElementById("term-tab-bar");
        const launcher = document.getElementById("chat-new-btn");
        if (bar && launcher) bar.insertBefore(_tab, launcher.nextSibling);
        else if (bar) bar.appendChild(_tab);
      }
      // Reflect minimised state by watching the window's class
      if (_tabObs) _tabObs.disconnect();
      if (winEl) {
        const sync = () => _tab && _tab.classList.toggle("dimmed", winEl.classList.contains("swm-hidden"));
        _tabObs = new MutationObserver(sync);
        _tabObs.observe(winEl, { attributes: true, attributeFilter: ["class"] });
        sync();
      }
    }
    function _removeTab() {
      if (_tabObs) { _tabObs.disconnect(); _tabObs = null; }
      if (_tab) { _tab.remove(); _tab = null; }
    }

    const btn = document.getElementById("chat-new-btn");
    if (btn) btn.addEventListener("click", open);
    return { open };
  })();

  // ── Press Room: article click → WM window ────────────────────────────────
  // Patch press-room handler to make rows clickable
  const _origPressRoomHandler = handlers["press-room"];
  handlers["press-room"] = function (d) {
    _origPressRoomHandler(d);
    // Wire click handlers on article rows
    const feed = document.getElementById("pressroom-feed");
    if (!feed) return;
    const rows = feed.querySelectorAll(".pr-article-row[data-article-id]");
    rows.forEach(row => {
      if (row.dataset.wmWired) return;
      row.dataset.wmWired = "1";
      row.style.cursor = "pointer";
      row.addEventListener("click", function () {
        openArticleWindow(row.dataset.articleId, row.dataset.articleTitle);
      });
    });
  };

  // Also patch the handler to attach data-article-id to rows
  handlers["press-room"] = function (d) {
    const feed = document.getElementById("pressroom-feed");
    if (!feed) return;

    const articles = d.articles || [];
    if (!articles.length) {
      feed.innerHTML = `
        <div class="know-empty-state know-empty-state-sm">
          <div class="know-empty-icon">&#9636;</div>
          <div class="know-empty-title">No articles yet</div>
          <div class="know-empty-sub">Press Room v2 articles appear here</div>
        </div>`;
      const sub = document.querySelector("#pressroom-panel .know-section-sub");
      if (sub) sub.textContent = "Curated articles";
      return;
    }

    const PR_STATUS_COLORS_LOCAL = {
      "tier-0": "#2dd4bf", "tier-1": "#a78bfa",
      "tier-2": "#fbbf24", "article": "#60a5fa",
    };

    feed.innerHTML = "";
    articles.forEach(a => {
      const color = PR_STATUS_COLORS_LOCAL[a.status] || "#60a5fa";
      const title = a.title || "(untitled)";
      const when  = a.when  || "";
      const row = document.createElement("div");
      row.className = "pr-article-row";
      row.style.cursor = "pointer";
      if (a.id != null) row.dataset.articleId = String(a.id);
      row.dataset.articleTitle = title;
      row.innerHTML = `
        <div class="pr-article-title">${esc(title)}</div>
        <div class="pr-article-meta">
          <span class="pr-status-pill" style="color:${color};border-color:${color}20;background:${color}10">${esc(a.status || "")}</span>
          <span class="pr-when">${esc(when)}</span>
        </div>`;
      row.addEventListener("click", function () {
        openArticleWindow(row.dataset.articleId, title);
      });
      feed.appendChild(row);
    });

    const sub = document.querySelector("#pressroom-panel .know-section-sub");
    if (sub && d.total) sub.textContent = `${d.total} articles`;
  };

  function openArticleWindow(articleId, title) {
    if (!articleId) return;
    const wmId = "article:" + articleId;

    StudioWM.open({
      id:     wmId,
      title:  title || "Article",
      icon:   "&#9096;",
      width:  760,
      height: 580,
      render(bodyEl) {
        bodyEl.className += " swm-article-body";
        bodyEl.style.cssText = "display:flex;flex-direction:column;overflow:hidden;height:100%";
        bodyEl.innerHTML = `<div class="swm-article-loading">Loading&#8230;</div>`;

        fetch("/api/press-room/article/" + encodeURIComponent(articleId))
          .then(r => r.json())
          .then(data => {
            if (data.error) {
              bodyEl.innerHTML = `<div class="swm-article-loading" style="color:var(--red-hi)">${esc(data.error)}</div>`;
              return;
            }

            const sections = [];
            if (data.hook)        sections.push(["Hook",        data.hook]);
            if (data.analysis)    sections.push(["Analysis",    data.analysis]);
            if (data.predictions) sections.push(["Predictions", data.predictions]);
            if (data.signal_card) sections.push(["Signal",      data.signal_card]);

            const body_html = sections.map(([lbl, txt]) => `
              <div class="swm-article-section">
                <div class="swm-article-section-label">${esc(lbl)}</div>
                <div class="swm-article-text">${esc(txt)}</div>
              </div>`).join("");

            bodyEl.innerHTML = `
              <div class="swm-article-scroll">
                <div class="swm-article-title">${esc(data.title)}</div>
                <div class="swm-article-meta">
                  ${data.topic ? `<span class="swm-article-meta-chip">${esc(data.topic)}</span>` : ""}
                  <span class="swm-article-when">${esc(data.created_at ? data.created_at.slice(0,16).replace("T"," ") : "")}</span>
                </div>
                ${body_html || '<div class="swm-article-loading" style="color:var(--text-3)">No body text available</div>'}
              </div>
              <div class="swm-article-actions">
                <button class="swm-act-btn" id="art-copy-btn">Copy article</button>
                <button class="swm-act-btn" id="art-dl-btn">Download .txt</button>
                <button class="swm-act-btn swm-act-draft" id="art-draft-btn">Generate X draft</button>
              </div>
              <div class="swm-draft-area" id="swm-draft-area" style="display:none">
                <div class="swm-draft-label">X DRAFT</div>
                <textarea class="swm-draft-textarea" id="swm-draft-text" rows="4" spellcheck="false"></textarea>
                <div class="swm-draft-row">
                  <span class="swm-draft-chars" id="swm-draft-chars">0 / 280</span>
                  <button class="swm-act-btn" id="swm-draft-copy-btn">Copy draft</button>
                </div>
              </div>`;

            // Full article text for copy/download
            const fullText = [data.title,
              data.hook     ? "\n\n" + data.hook     : "",
              data.analysis ? "\n\n" + data.analysis : "",
              data.predictions ? "\n\nPredictions:\n" + data.predictions : "",
            ].join("").trim();

            // Copy article
            bodyEl.querySelector("#art-copy-btn").addEventListener("click", function () {
              navigator.clipboard.writeText(fullText).then(() => {
                this.textContent = "Copied!";
                setTimeout(() => { this.textContent = "Copy article"; }, 2000);
              });
            });

            // Download .txt
            bodyEl.querySelector("#art-dl-btn").addEventListener("click", function () {
              const blob = new Blob([fullText], { type: "text/plain" });
              const url  = URL.createObjectURL(blob);
              const a    = document.createElement("a");
              a.href     = url;
              a.download = (data.title || "article").slice(0, 60).replace(/[^a-z0-9]/gi, "-").replace(/-+/g, "-") + ".txt";
              a.click();
              URL.revokeObjectURL(url);
            });

            // Generate X draft
            const draftBtn  = bodyEl.querySelector("#art-draft-btn");
            const draftArea = bodyEl.querySelector("#swm-draft-area");
            const draftText = bodyEl.querySelector("#swm-draft-text");
            const draftChars = bodyEl.querySelector("#swm-draft-chars");
            const draftCopy = bodyEl.querySelector("#swm-draft-copy-btn");

            draftText && draftText.addEventListener("input", function () {
              const n = draftText.value.length;
              draftChars.textContent = n + " / 280";
              draftChars.style.color = n > 280 ? "var(--red-hi)" : n > 240 ? "var(--amber-hi)" : "var(--text-3)";
            });

            draftCopy && draftCopy.addEventListener("click", function () {
              navigator.clipboard.writeText(draftText.value).then(() => {
                draftCopy.textContent = "Copied!";
                setTimeout(() => { draftCopy.textContent = "Copy draft"; }, 2000);
              });
            });

            draftBtn.addEventListener("click", function () {
              draftBtn.textContent = "Generating…";
              draftBtn.disabled = true;
              draftArea.style.display = "block";
              draftText.value = "";
              draftChars.textContent = "0 / 280";

              fetch("/api/press-room/draft/" + encodeURIComponent(articleId), { method: "POST" })
                .then(r => r.json())
                .then(d => {
                  draftBtn.textContent = "Regenerate";
                  draftBtn.disabled = false;
                  if (d.error) {
                    draftText.value = "Error: " + d.error;
                    return;
                  }
                  draftText.value = d.draft || "";
                  if (d.model) {
                    const lbl = bodyEl.querySelector(".swm-draft-label");
                    if (lbl) lbl.textContent = "X DRAFT — via " + d.model;
                  }
                  const n = draftText.value.length;
                  draftChars.textContent = n + " / 280";
                  draftChars.style.color = n > 280 ? "var(--red-hi)" : n > 240 ? "var(--amber-hi)" : "var(--text-3)";
                })
                .catch(err => {
                  draftBtn.textContent = "Regenerate";
                  draftBtn.disabled = false;
                  draftText.value = "Request failed: " + String(err);
                });
            });
          })
          .catch(err => {
            bodyEl.innerHTML = `<div class="swm-article-loading" style="color:var(--red-hi)">Failed to load: ${esc(String(err))}</div>`;
          });
      },
    });
  }

  // ── Press Room: search + Latest/All toggle ────────────────────────────────
  (function () {
    const searchInput  = document.getElementById("pr-search-input");
    const btnLatest    = document.getElementById("pr-toggle-latest");
    const btnAll       = document.getElementById("pr-toggle-all");
    if (!searchInput) return;

    let _prMode  = "latest";   // "latest" | "all"
    let _prTimer = null;

    function _fetchAndRender(q, limit) {
      const url = "/api/press-room/search?limit=" + limit + (q ? "&q=" + encodeURIComponent(q) : "");
      fetch(url)
        .then(r => r.json())
        .then(data => {
          if (!data || !data.articles) return;
          const sub = document.getElementById("pr-sub");
          if (sub) sub.textContent = data.total != null ? data.total + " articles" : "Curated articles";
          // Reuse the existing press-room render path by firing the handler
          // with the search result payload shaped the same as the SSE event
          if (handlers["press-room"]) {
            handlers["press-room"]({ articles: data.articles, total: data.total });
          }
        })
        .catch(() => {});
    }

    function _setMode(mode) {
      _prMode = mode;
      const q = searchInput.value.trim();
      if (mode === "latest") {
        btnLatest.classList.add("know-toggle-active");
        btnAll.classList.remove("know-toggle-active");
        _fetchAndRender(q, 30);
      } else {
        btnAll.classList.add("know-toggle-active");
        btnLatest.classList.remove("know-toggle-active");
        _fetchAndRender(q, 200);
      }
    }

    searchInput.addEventListener("input", function () {
      clearTimeout(_prTimer);
      _prTimer = setTimeout(function () {
        _setMode(_prMode);
      }, 250);
    });

    btnLatest && btnLatest.addEventListener("click", function () { _setMode("latest"); });
    btnAll    && btnAll.addEventListener("click",    function () { _setMode("all"); });

    // Load latest on view enter
    _setMode("latest");
  })();

  // ── RAG Library: search via knowledge hub ─────────────────────────────────
  (function () {
    const searchInput   = document.getElementById("lib-search-input");
    const resultsEl     = document.getElementById("lib-search-results");
    const collBreakdown = document.getElementById("lib-segment-bar-wrap");
    const libraryFeed   = document.getElementById("library-feed");
    if (!searchInput || !resultsEl) return;

    let _libTimer = null;

    function _showResults(show) {
      resultsEl.style.display = show ? "" : "none";
    }

    function _renderResults(results, query, err) {
      resultsEl.innerHTML = "";
      if (err) {
        resultsEl.innerHTML = `<div class="lib-search-empty" style="color:var(--text-3)">${esc(err)}</div>`;
        _showResults(true);
        return;
      }
      if (!results || !results.length) {
        resultsEl.innerHTML = `<div class="lib-search-empty">No results for <em>${esc(query)}</em></div>`;
        _showResults(true);
        return;
      }
      results.forEach(function (r) {
        const row = document.createElement("div");
        row.className = "lib-result-row";
        const title   = r.title   || r.source || "—";
        const snippet = r.snippet || "";
        const source  = r.source  || "";
        row.innerHTML =
          `<div class="lib-result-title">${esc(title)}</div>` +
          (snippet ? `<div class="lib-result-snippet">${esc(snippet)}</div>` : "") +
          (source  ? `<div class="lib-result-source">${esc(source)}</div>`  : "");
        resultsEl.appendChild(row);
      });
      _showResults(true);
    }

    searchInput.addEventListener("input", function () {
      clearTimeout(_libTimer);
      const q = searchInput.value.trim();
      if (!q) {
        _showResults(false);
        return;
      }
      _libTimer = setTimeout(function () {
        fetch("/api/library/hub-search?q=" + encodeURIComponent(q))
          .then(function (r) { return r.json(); })
          .then(function (data) {
            _renderResults(data.results || [], q, data.error || null);
          })
          .catch(function () {
            _renderResults([], q, "Search failed");
          });
      }, 300);
    });
  })();

  // ── Docs browser ──────────────────────────────────────────────────────────
  (function () {
    const feed        = document.getElementById("docs-feed");
    const searchInput = document.getElementById("docs-search-input");
    const sub         = document.getElementById("docs-sub");
    if (!feed) return;

    let _allFiles = [];

    function _renderFiles(files) {
      feed.innerHTML = "";
      if (!files || !files.length) {
        feed.innerHTML =
          `<div class="know-empty-state know-empty-state-sm">` +
          `<div class="know-empty-icon">📄</div>` +
          `<div class="know-empty-title">No docs found</div>` +
          `<div class="know-empty-sub">Workspace .md files appear here</div>` +
          `</div>`;
        return;
      }
      files.forEach(function (f) {
        const row = document.createElement("div");
        row.className = "docs-file-row doc-file-row";
        const name   = f.name || "unknown.md";
        const folder = f.folder || "";
        const label  = f.root_label || "";
        row.innerHTML =
          `<span class="docs-file-name" title="${esc(f.path || name)}">${esc(name)}</span>` +
          (folder ? `<span class="docs-file-folder" title="${esc(folder)}">${esc(folder)}</span>` : "") +
          (label  ? `<span class="docs-file-label">${esc(label)}</span>` : "");
        // Click row to open popup — Copy/Download are inside the popup
        row.addEventListener("click", function () {
          openDocWindow(f.path, name);
        });
        feed.appendChild(row);
      });
    }

    function _applyFilter() {
      const q = searchInput ? searchInput.value.trim().toLowerCase() : "";
      if (!q) {
        _renderFiles(_allFiles);
        return;
      }
      _renderFiles(_allFiles.filter(function (f) {
        return (f.name || "").toLowerCase().includes(q) ||
               (f.folder || "").toLowerCase().includes(q) ||
               (f.root_label || "").toLowerCase().includes(q);
      }));
    }

    searchInput && searchInput.addEventListener("input", _applyFilter);

    // Load on page load
    fetch("/api/docs/tree")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        _allFiles = data.files || [];
        if (sub) sub.textContent = _allFiles.length + " files";
        _applyFilter();
      })
      .catch(function () {
        feed.innerHTML =
          `<div class="know-empty-state know-empty-state-sm">` +
          `<div class="know-empty-icon">📄</div>` +
          `<div class="know-empty-title">Could not load docs</div>` +
          `<div class="know-empty-sub">Check server logs</div>` +
          `</div>`;
      });

    function openDocWindow(filePath, name) {
      if (!filePath) return;
      const wmId = "doc:" + filePath;
      StudioWM.open({
        id:     wmId,
        title:  name || "Doc",
        icon:   "&#128196;",
        width:  720,
        height: 540,
        render: function (bodyEl) {
          bodyEl.className += " swm-article-body";
          bodyEl.style.cssText = "display:flex;flex-direction:column;overflow:hidden;height:100%";
          bodyEl.innerHTML =
            '<div class="swm-article-scroll" id="doc-scroll-' + wmId.replace(/[^a-z0-9]/gi,"") + '">' +
              '<pre class="swm-doc-pre" style="color:var(--text-3)">Loading…</pre>' +
            '</div>' +
            '<div class="swm-article-actions">' +
              '<button class="swm-act-btn" id="doc-copy-btn">Copy content</button>' +
              '<a class="swm-act-btn" id="doc-dl-btn" href="/api/docs/file?path=' + encodeURIComponent(filePath) + '&download=1" download="' + esc(name) + '">Download .md</a>' +
            '</div>';

          const scrollEl = bodyEl.querySelector(".swm-article-scroll");
          const copyBtn  = bodyEl.querySelector("#doc-copy-btn");
          let _content = "";

          fetch("/api/docs/file?path=" + encodeURIComponent(filePath))
            .then(function (r) { return r.json(); })
            .then(function (data) {
              if (data.error) {
                scrollEl.innerHTML = '<pre class="swm-doc-pre" style="color:var(--red-hi)">' + esc(data.error) + "</pre>";
                return;
              }
              _content = data.content || "";
              scrollEl.innerHTML = '<pre class="swm-doc-pre">' + esc(_content) + "</pre>";
            })
            .catch(function (err) {
              scrollEl.innerHTML = '<pre class="swm-doc-pre" style="color:var(--red-hi)">Failed: ' + esc(String(err)) + "</pre>";
            });

          copyBtn.addEventListener("click", function () {
            if (!_content) return;
            navigator.clipboard.writeText(_content).then(function () {
              copyBtn.textContent = "Copied!";
              setTimeout(function () { copyBtn.textContent = "Copy content"; }, 2000);
            });
          });
        },
      });
    }
  })();

  // ── War Room: interactive graph popup ─────────────────────────────────────
  (function () {
    const btn = document.getElementById("warroom-graph-btn");
    if (!btn) return;
    // No war-room port configured → hide the button entirely
    if (!window._warRoomPort) { btn.style.display = "none"; return; }
    btn.addEventListener("click", function () {
      const WM_ID = "warroom-graph";
      if (StudioWM._windows && StudioWM._windows.has(WM_ID)) {
        StudioWM.restore(WM_ID);
        return;
      }
      const graphUrl = `http://${window.location.hostname}:${window._warRoomPort}`;
      StudioWM.open({
        id:     WM_ID,
        title:  "War Room — Interactive Graph",
        icon:   "⬡",
        width:  1100,
        height: 700,
        render: function (bodyEl) {
          bodyEl.style.cssText = "padding:0;display:flex;flex-direction:column;height:100%;";
          const note = document.createElement("div");
          note.style.cssText =
            "font-size:10px;color:var(--text-3);text-transform:uppercase;letter-spacing:.1em;" +
            "padding:4px 10px;border-bottom:1px solid var(--rule);flex-shrink:0;";
          note.textContent = "War Room server (port " + (window._warRoomPort || "") + ") — if blank, server may be offline";
          const iframe = document.createElement("iframe");
          iframe.src = graphUrl;
          iframe.style.cssText = "flex:1;width:100%;border:none;background:#0d1018;";
          iframe.allow = "autoplay";
          bodyEl.appendChild(note);
          bodyEl.appendChild(iframe);
        },
      });
    });
  })();

  // ── Quick-action buttons — config-driven ──────────────────────────────────
  (function () {
    const container = document.getElementById("quick-actions");
    if (!container) return;

    const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

    function buildButtons(actions) {
      container.innerHTML = "";
      if (!actions || !actions.length) return;
      actions.forEach(function (a) {
        if (!a.id || !a.label) return;
        const btn = document.createElement("button");
        btn.className = "qa-btn";
        btn.title = a.label;
        btn.dataset.actionId = a.id;
        btn.innerHTML = (a.icon ? '<span class="qa-icon">' + esc(a.icon) + '</span>' : '') +
                        '<span class="qa-lbl">' + esc(a.label) + '</span>';
        btn.addEventListener("click", function () {
          if (!a.shell) return;
          btn.disabled = true;
          btn.style.opacity = "0.5";
          fetch("/api/quick-action/" + a.id, { method: "POST" })
            .then(function (r) { return r.json(); })
            .then(function (d) {
              const ok = d.returncode === 0 || d.status === "ok";
              if (typeof LlamaWatch !== "undefined") {
                LlamaWatch.toast(ok ? (a.label + " — done") : (a.label + " failed: " + (d.stderr || d.error || "error")), ok ? "success" : "error");
              }
            })
            .catch(function () {
              if (typeof LlamaWatch !== "undefined") LlamaWatch.toast(a.label + " — error", "error");
            })
            .finally(function () {
              btn.disabled = false;
              btn.style.opacity = "";
            });
        });
        container.appendChild(btn);
      });
    }

    // Build from cached config first, then refresh from server
    try {
      const cached = JSON.parse(localStorage.getItem("studio-quick-actions") || "null");
      if (cached) buildButtons(cached);
    } catch (e) {}

    fetch("/api/settings").then(function (r) { return r.json(); }).then(function (cfg) {
      const actions = cfg.quick_actions || [];
      localStorage.setItem("studio-quick-actions", JSON.stringify(actions));
      buildButtons(actions);
    }).catch(function () {});

    // Expose so settings can rebuild without reload
    window.StudioRebuildQuickActions = buildButtons;
  })();

  // ── Redraw sparklines ──────────────────────────────────────────────────────
  function redrawSparks() {
    drawSpark(document.getElementById("tps-spark"), get("tps"), "#2dd4bf");
    drawNetArea(document.getElementById("net-rc-canvas"));
    // Power sparklines removed from UI (hidden compat elements only)
  }

  let _resizeTimer;
  window.addEventListener("resize", () => {
    clearTimeout(_resizeTimer);
    _resizeTimer = setTimeout(redrawSparks, 150);
  });
  // Entering/leaving full screen changes the panel size — redraw canvases
  document.addEventListener("fullscreenchange", () => setTimeout(redrawSparks, 120));

  // ── Hero neural network animation ─────────────────────────────────────────
  (function () {
    const canvas = document.getElementById("hero-neural");
    if (!canvas) return;

    const W = 100, H = 58;
    const dpr = window.devicePixelRatio || 1;
    canvas.width  = W * dpr;
    canvas.height = H * dpr;
    canvas.style.width  = W + "px";
    canvas.style.height = H + "px";
    const ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);

    // 3-layer layout: 3 | 4 | 3 neurons
    // Centred vertically within H=58
    const NODES = [
      // Layer 0 (input)   x=10
      [10, 10], [10, 29], [10, 48],
      // Layer 1 (hidden)  x=50
      [50,  4], [50, 19], [50, 35], [50, 50],
      // Layer 2 (output)  x=90
      [90, 10], [90, 29], [90, 48],
    ];
    const L0 = [0,1,2], L1 = [3,4,5,6], L2 = [7,8,9];

    // All-to-all edges between adjacent layers
    const EDGES = [];
    L0.forEach(a => L1.forEach(b => EDGES.push([a, b])));
    L1.forEach(a => L2.forEach(b => EDGES.push([a, b])));

    // Pulse colours
    const COLORS = ["#2dd4bf", "#a78bfa", "#2dd4bf", "#60a5fa", "#2dd4bf", "#a78bfa"];

    // Active pulses: { edge, t, speed, color }
    const pulses = [];
    const MAX_PULSES = 18;

    function isGenerating() {
      const n = parseInt(document.getElementById("studio")?.dataset.generatingBackends || "0");
      return n > 0;
    }

    function spawnPulse() {
      if (pulses.length >= MAX_PULSES) return;
      const edge  = EDGES[Math.floor(Math.random() * EDGES.length)];
      const gen   = isGenerating();
      const speed = gen ? (0.012 + Math.random() * 0.018) : (0.004 + Math.random() * 0.006);
      const color = COLORS[Math.floor(Math.random() * COLORS.length)];
      pulses.push({ edge, t: 0, speed, color });
    }

    // Node glow intensities (0-1, decay each frame)
    const nodeGlow = new Array(NODES.length).fill(0);

    let frame = 0;
    function draw() {
      requestAnimationFrame(draw);
      ctx.clearRect(0, 0, W, H);
      frame++;

      const gen = isGenerating();
      const spawnEvery = gen ? 3 : 9;
      if (frame % spawnEvery === 0) spawnPulse();

      // Connections — dim base lines
      ctx.lineWidth = 0.6;
      for (const [a, b] of EDGES) {
        const [ax, ay] = NODES[a], [bx, by] = NODES[b];
        ctx.beginPath();
        ctx.moveTo(ax, ay);
        ctx.lineTo(bx, by);
        ctx.strokeStyle = "rgba(45,212,191,0.09)";
        ctx.stroke();
      }

      // Pulses along edges
      for (let i = pulses.length - 1; i >= 0; i--) {
        const p = pulses[i];
        p.t += p.speed;
        if (p.t >= 1) {
          // Light up destination node
          nodeGlow[p.edge[1]] = 1;
          pulses.splice(i, 1);
          continue;
        }
        const [ax, ay] = NODES[p.edge[0]];
        const [bx, by] = NODES[p.edge[1]];
        const x = ax + (bx - ax) * p.t;
        const y = ay + (by - ay) * p.t;

        // Soft glow halo
        const g = ctx.createRadialGradient(x, y, 0, x, y, 5);
        g.addColorStop(0, p.color + "cc");
        g.addColorStop(1, p.color + "00");
        ctx.beginPath();
        ctx.arc(x, y, 5, 0, Math.PI * 2);
        ctx.fillStyle = g;
        ctx.fill();

        // Bright core
        ctx.beginPath();
        ctx.arc(x, y, 1.8, 0, Math.PI * 2);
        ctx.fillStyle = p.color;
        ctx.fill();
      }

      // Nodes
      NODES.forEach(([nx, ny], i) => {
        nodeGlow[i] = Math.max(0, nodeGlow[i] - 0.04);
        const glow  = nodeGlow[i];
        const base  = gen ? 0.22 : 0.13;
        const alpha = base + glow * 0.55;

        // Outer glow ring
        ctx.beginPath();
        ctx.arc(nx, ny, 4.5, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(45,212,191,${(alpha * 0.35).toFixed(2)})`;
        ctx.fill();

        // Core dot
        ctx.beginPath();
        ctx.arc(nx, ny, 2.2, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(45,212,191,${alpha.toFixed(2)})`;
        ctx.shadowColor = "#2dd4bf";
        ctx.shadowBlur  = glow > 0.1 ? 8 : (gen ? 4 : 2);
        ctx.fill();
        ctx.shadowBlur = 0;
      });
    }

    draw();
  })();

  // ── Knowledge page: tabs, predictions, files, article actions ───────────────

  (function KnowledgePage() {

    // ── Tab switching ─────────────────────────────────────────────────────────
    var _tabsEl   = document.getElementById("know-tabs");
    var _panels   = document.querySelectorAll(".know-panel");

    function _activateTab(panelId) {
      document.querySelectorAll(".know-tab").forEach(function (t) {
        t.classList.toggle("know-tab-active", t.dataset.panel === panelId);
      });
      _panels.forEach(function (p) {
        p.classList.toggle("know-panel-active", p.id === "know-panel-" + panelId);
      });
      // Lazy-load library once; always refresh predictions on every visit
      var panel = document.getElementById("know-panel-" + panelId);
      if (panelId === "intel") {
        _loadPredictions(_predFilter);
        if (panel && !panel.dataset.loaded) { panel.dataset.loaded = "1"; _loadFileList(); }
      } else if (panel && !panel.dataset.loaded) {
        panel.dataset.loaded = "1";
        if (panelId === "library") { _loadFileList(); }
      }
    }

    if (_tabsEl) {
      _tabsEl.addEventListener("click", function (e) {
        var tab = e.target.closest(".know-tab");
        if (tab) _activateTab(tab.dataset.panel);
      });
    }

    // ── Predictions ───────────────────────────────────────────────────────────
    var _predsAll = [];
    var _predFilter = "all";

    var DOMAIN_CLASS = {
      conflict: "pred-domain-conflict",
      economic: "pred-domain-economic",
      political: "pred-domain-political",
      technology: "pred-domain-technology",
      social: "pred-domain-social",
    };

    function _fmtDate(iso) {
      if (!iso) return "";
      try {
        var d = new Date(iso);
        return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "2-digit" });
      } catch (e) { return iso.slice(0, 10); }
    }

    function _fmtConfidence(c) {
      if (c == null) return "";
      return Math.round(c * 100) + "%";
    }

    // Domain hex colors for SVG dots
    var DOMAIN_HEX = {
      conflict:   "#f87171",
      economic:   "#fbbf24",
      political:  "#60a5fa",
      technology: "#2dd4bf",
      social:     "#a78bfa",
      general:    "#8d98b4",
    };

    var _selectedPredId = null;

    function _renderMap(preds) {
      var feed = document.getElementById("predictions-feed");
      if (!feed) return;
      _selectedPredId = null;
      feed.innerHTML = "";

      var shown = preds.filter(function (p) {
        if (_predFilter === "pending")  return p.verified == null;
        if (_predFilter === "verified") return p.verified != null;
        return true;
      });

      if (!shown.length) {
        feed.innerHTML = '<div class="know-empty-state"><div class="know-empty-icon">◎</div><div class="know-empty-title">No predictions for this filter</div></div>';
        return;
      }

      _renderWorldMap(feed, shown, DOMAIN_HEX);
    }

    if (false) {
      var VW = 600, VH = 240;
      var ML = 50, MR = 20, MT = 18, MB = 42;
      var PW = VW - ML - MR; // 530
      var PH = VH - MT - MB; // 180

      // X domain from timeframes (pad 8% each side, min 21-day span)
      var now = new Date();
      var tfDates = hasTf.map(function (p) { return new Date(p.timeframe).getTime(); });
      var rawMin  = tfDates.length ? Math.min.apply(null, tfDates) : now.getTime();
      var rawMax  = tfDates.length ? Math.max.apply(null, tfDates) : now.getTime() + 21 * 864e5;
      var span    = Math.max(rawMax - rawMin, 21 * 864e5);
      var xMin    = new Date(rawMin - span * 0.08);
      var xMax    = new Date(rawMax + span * 0.12);
      var xRange  = xMax - xMin;

      function xPx(iso) { return ML + ((new Date(iso) - xMin) / xRange) * PW; }
      function yPx(c)   { return MT + PH - ((c != null ? c : 0.5) * PH); }

      function fmtAxis(d) {
        return d.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
      }

      var NS = "http://www.w3.org/2000/svg";
      function svgEl(tag, attrs) {
        var el = document.createElementNS(NS, tag);
        Object.keys(attrs).forEach(function (k) { el.setAttribute(k, attrs[k]); });
        return el;
      }
      function svgTxt(txt, attrs) {
        var el = svgEl("text", attrs);
        el.textContent = txt;
        return el;
      }

      var svg = svgEl("svg", { viewBox: "0 0 " + VW + " " + VH, preserveAspectRatio: "xMidYMid meet" });
      svg.style.cssText = "width:100%;height:auto;display:block";

      // Plot background
      svg.appendChild(svgEl("rect", { x: ML, y: MT, width: PW, height: PH, fill: "rgba(13,16,24,0.45)", rx: "4" }));

      // Y gridlines + labels (0, 25, 50, 75, 100)
      [0, 0.25, 0.5, 0.75, 1.0].forEach(function (v) {
        var y = yPx(v);
        svg.appendChild(svgEl("line", { x1: ML, x2: ML + PW, y1: y, y2: y,
          stroke: v === 0.5 ? "rgba(255,255,255,0.09)" : "rgba(255,255,255,0.04)", "stroke-width": "1" }));
        svg.appendChild(svgTxt(Math.round(v * 100) + "%", { x: ML - 5, y: y + 3.5,
          "text-anchor": "end", fill: "rgba(141,152,180,0.6)", "font-size": "9", "font-family": "monospace" }));
      });

      // X axis ticks (5 evenly spaced)
      for (var ti = 0; ti <= 4; ti++) {
        var td = new Date(xMin.getTime() + (ti / 4) * xRange);
        var tx = ML + (ti / 4) * PW;
        svg.appendChild(svgEl("line", { x1: tx, x2: tx, y1: MT + PH, y2: MT + PH + 4,
          stroke: "rgba(255,255,255,0.14)", "stroke-width": "1" }));
        svg.appendChild(svgTxt(fmtAxis(td), { x: tx, y: MT + PH + 14,
          "text-anchor": "middle", fill: "rgba(141,152,180,0.6)", "font-size": "9", "font-family": "monospace" }));
      }

      // Axis labels
      svg.appendChild(svgTxt("CONFIDENCE", { x: ML - 38, y: MT + PH / 2,
        transform: "rotate(-90 " + (ML - 38) + " " + (MT + PH / 2) + ")",
        "text-anchor": "middle", fill: "rgba(141,152,180,0.4)", "font-size": "8", "font-family": "monospace" }));
      svg.appendChild(svgTxt("DEADLINE", { x: ML + PW / 2, y: VH - 4,
        "text-anchor": "middle", fill: "rgba(141,152,180,0.4)", "font-size": "8", "font-family": "monospace" }));

      // Today line
      var todayX = ML + ((now - xMin) / xRange) * PW;
      if (todayX >= ML && todayX <= ML + PW) {
        svg.appendChild(svgEl("line", { x1: todayX, x2: todayX, y1: MT, y2: MT + PH,
          stroke: "rgba(45,212,191,0.22)", "stroke-width": "1", "stroke-dasharray": "3 3" }));
        svg.appendChild(svgTxt("TODAY", { x: todayX + 3, y: MT + 10,
          fill: "rgba(45,212,191,0.4)", "font-size": "8", "font-family": "monospace" }));
      }

      // ── Dots ──────────────────────────────────────────────────────────────
      // Detail panel (inserted into feed, below SVG)
      var detailPanel = document.createElement("div");
      detailPanel.className = "pred-detail";
      detailPanel.style.display = "none";

      var dotMap = {}; // id → { dot, glow }

      // Deterministic jitter — spreads overlapping dots without randomness
      function _jitter(id) {
        var h = 0;
        for (var i = 0; i < (id || "").length; i++) h = (h * 31 + (id || "").charCodeAt(i)) & 0xffff;
        var angle = (h & 0xff) / 255 * 2 * Math.PI;
        var r = 7 + (h >> 8) % 9; // 7–15px radius
        return { dx: Math.cos(angle) * r, dy: Math.sin(angle) * r };
      }

      hasTf.forEach(function (p) {
        var j   = _jitter(p.id);
        var cx  = Math.max(ML + 8, Math.min(ML + PW - 8, xPx(p.timeframe) + j.dx));
        var cy  = Math.max(MT + 8, Math.min(MT + PH - 8, yPx(p.confidence) + j.dy));
        var col = DOMAIN_HEX[p.domain] || DOMAIN_HEX.general;
        var R   = 7;

        // Glow halo
        var glow = svgEl("circle", { cx: cx, cy: cy, r: R + 6,
          fill: col, "fill-opacity": "0" });
        glow.style.transition = "fill-opacity .15s";
        svg.appendChild(glow);

        // Main dot
        var dot = svgEl("circle", { cx: cx, cy: cy, r: R,
          fill: col, "fill-opacity": p.verified === false ? "0.3" : "0.82",
          stroke: col, "stroke-width": "0" });
        if (p.verified == null) {
          dot.setAttribute("stroke-width", "1.5");
          dot.setAttribute("stroke-opacity", "0.55");
          dot.setAttribute("fill-opacity", "0.65");
        }
        dot.style.cssText = "cursor:pointer;transition:r .12s,fill-opacity .12s";
        svg.appendChild(dot);

        // Verified ring
        if (p.verified === true) {
          svg.appendChild(svgEl("circle", { cx: cx, cy: cy, r: R + 3.5,
            fill: "none", stroke: "#34d399", "stroke-width": "1.5", "stroke-opacity": "0.7" }));
        } else if (p.verified === false) {
          svg.appendChild(svgEl("circle", { cx: cx, cy: cy, r: R + 3.5,
            fill: "none", stroke: "#f87171", "stroke-width": "1.5",
            "stroke-dasharray": "4 3", "stroke-opacity": "0.6" }));
        }

        dotMap[p.id] = { dot: dot, glow: glow };

        function _resetDot(pred) {
          var els = dotMap[pred.id];
          if (!els) return;
          els.dot.setAttribute("r", R);
          els.dot.setAttribute("fill-opacity", pred.verified === false ? "0.3" : (pred.verified == null ? "0.65" : "0.82"));
          els.glow.setAttribute("fill-opacity", "0");
        }
        function _highlightDot(pred) {
          var els = dotMap[pred.id];
          if (!els) return;
          els.dot.setAttribute("r", R + 2.5);
          els.dot.setAttribute("fill-opacity", "1");
          els.glow.setAttribute("fill-opacity", "0.18");
        }

        dot.addEventListener("mouseenter", function () { if (_scatterSelected !== p.id) _highlightDot(p); });
        dot.addEventListener("mouseleave", function () { if (_scatterSelected !== p.id) _resetDot(p); });

        dot.addEventListener("click", function (e) {
          e.stopPropagation();

          // Deselect if already selected
          if (_scatterSelected === p.id) {
            _scatterSelected = null;
            _resetDot(p);
            detailPanel.style.display = "none";
            return;
          }

          // Reset previously selected
          if (_scatterSelected) {
            var prev = shown.find(function (x) { return x.id === _scatterSelected; });
            if (prev) _resetDot(prev);
          }
          _scatterSelected = p.id;
          _highlightDot(p);

          // Populate detail panel
          var conf = p.confidence != null ? Math.round(p.confidence * 100) + "%" : "—";
          var tf   = _fmtDate(p.timeframe);
          var vHtml = p.verified === true  ? '<span class="pred-v-ok">&#10003; Verified correct</span>'
                    : p.verified === false ? '<span class="pred-v-err">&#10007; Verified wrong</span>'
                    : '<span class="pred-v-pend">Pending</span>';
          var domClass = DOMAIN_CLASS[p.domain] || "pred-domain-general";
          detailPanel.innerHTML =
            '<div class="pred-detail-row">' +
              '<span class="pred-domain ' + domClass + '">' + esc(p.domain || "general") + '</span>' +
              (p.geography ? '<span class="pred-geo-tag">&#9679; ' + esc(p.geography) + '</span>' : '') +
              '<span class="pred-confidence">' + conf + '</span>' +
              '<span class="pred-timeframe">by ' + esc(tf) + '</span>' +
              vHtml +
              '<button class="pred-detail-x" title="Close">&times;</button>' +
            '</div>' +
            '<p class="pred-detail-body">' + esc(p.text) + '</p>' +
            (p.brief ? '<p class="pred-detail-brief">' + esc(p.brief) + '</p>' : '') +
            '<button class="pred-copy-btn pred-detail-copy">Copy</button>';

          detailPanel.querySelector(".pred-detail-x").addEventListener("click", function (e) {
            e.stopPropagation();
            _scatterSelected = null;
            _resetDot(p);
            detailPanel.style.display = "none";
          });
          detailPanel.querySelector(".pred-detail-copy").addEventListener("click", function () {
            var txt = "[" + (p.domain || "general").toUpperCase() + " · " + conf + "] by " + tf + "\n" + p.text;
            navigator.clipboard.writeText(txt).then(function () {
              detailPanel.querySelector(".pred-detail-copy").textContent = "Copied";
              setTimeout(function () {
                var b = detailPanel.querySelector(".pred-detail-copy");
                if (b) b.textContent = "Copy";
              }, 2000);
            });
          });
          detailPanel.style.display = "block";
        });
      });

      // Click outside SVG → deselect
      svg.addEventListener("click", function () {
        if (_scatterSelected) {
          var prev = shown.find(function (x) { return x.id === _scatterSelected; });
          if (prev) {
            var els = dotMap[prev.id];
            if (els) { els.dot.setAttribute("r", 7); els.dot.setAttribute("fill-opacity", prev.verified === false ? "0.3" : (prev.verified == null ? "0.65" : "0.82")); els.glow.setAttribute("fill-opacity", "0"); }
          }
          _scatterSelected = null;
          detailPanel.style.display = "none";
        }
      });

      feed.appendChild(svg);
      feed.appendChild(detailPanel);

      // Legend row
      var legendDomains = Object.keys(DOMAIN_HEX).filter(function (d) {
        return shown.some(function (p) { return (p.domain || "general") === d; });
      });
      if (legendDomains.length > 1) {
        var legend = document.createElement("div");
        legend.className = "pred-legend";
        legend.innerHTML = legendDomains.map(function (d) {
          return '<span class="pred-legend-item"><svg width="8" height="8" viewBox="0 0 8 8"><circle cx="4" cy="4" r="4" fill="' + DOMAIN_HEX[d] + '" fill-opacity="0.85"/></svg>' + esc(d) + '</span>';
        }).join("");
        feed.appendChild(legend);
      }

      // No-timeframe list below
      if (noTf.length) {
        var noTfHdr = document.createElement("div");
        noTfHdr.className = "pred-notf-hdr";
        noTfHdr.textContent = "No deadline set (" + noTf.length + ")";
        feed.appendChild(noTfHdr);
        noTf.forEach(function (p) {
          var col = DOMAIN_HEX[p.domain] || DOMAIN_HEX.general;
          var domClass = DOMAIN_CLASS[p.domain] || "pred-domain-general";
          var row = document.createElement("div");
          row.className = "pred-notf-row";
          row.style.borderLeft = "3px solid " + col + "50";
          row.innerHTML =
            '<span class="pred-domain ' + domClass + '">' + esc(p.domain || "general") + '</span>' +
            (p.confidence ? '<span class="pred-confidence">' + Math.round(p.confidence * 100) + '%</span>' : "") +
            '<span class="pred-text" style="flex:1">' + esc(p.text) + '</span>' +
            '<button class="pred-copy-btn">Copy</button>';
          row.querySelector(".pred-copy-btn").addEventListener("click", function () {
            navigator.clipboard.writeText(p.text).then(function () {
              row.querySelector(".pred-copy-btn").textContent = "Copied";
              setTimeout(function () { var b = row.querySelector(".pred-copy-btn"); if (b) b.textContent = "Copy"; }, 2000);
            });
          });
          feed.appendChild(row);
        });
      }

      // World map below scatter
      _renderWorldMap(feed, shown, DOMAIN_HEX);
    }

    // ── Geographic signal world map ───────────────────────────────────────────
    var _worldData = null;

    // ISO 3166-1 numeric → used to look up country polygon in TopoJSON
    var _GEO_ISO = {
      "Iran":                364, "Iraq":           368, "Lebanon":      422,
      "Northern Israel":     376, "Israel":          376, "Gaza":         275,
      "United Arab Emirates":784, "UAE":             784, "Bahrain":       48,
      "Kuwait":              414, "Saudi Arabia":    682, "Yemen":        887,
      "Syria":               760, "Turkey":          792, "United States":840,
      "Russia":              643, "India":           356, "Pakistan":     586,
      "United Kingdom":      826, "UK":              826, "Belarus":      112,
      "Ukraine":             804, "China":           156, "Japan":        392,
      "North Korea":         408, "South Korea":     410, "Taiwan":       158,
      "Egypt":               818, "France":          250, "Germany":      276,
      "Poland":              616, "Australia":        36, "Brazil":        76,
      "Canada":              124, "Mexico":          484,
    };

    // Approximate polygons for countries too small to appear in Natural Earth 110m TopoJSON.
    // Used both for SVG rendering and polygon-constrained dot placement.
    var _MINI_POLYS = {
      48: { type:"Feature", id:"48", geometry:{ type:"Polygon",
        coordinates:[[[50.44,25.95],[50.67,25.95],[50.67,26.32],[50.44,26.32],[50.44,25.95]]] } },
    };

    var _GEO_COORDS = {
      "Persian Gulf":       [26.5,  52.0],
      "Iran":               [32.5,  53.7],
      "Iraq":               [33.2,  43.7],
      "Lebanon":            [33.9,  35.5],
      "Northern Israel":    [32.9,  35.3],
      "Israel":             [31.5,  34.8],
      "Gaza":               [31.4,  34.4],
      "United Arab Emirates":[23.4, 53.8],
      "UAE":                [23.4,  53.8],
      "Bahrain":            [26.2,  50.6],
      "Kuwait":             [29.4,  47.5],
      "Saudi Arabia":       [23.9,  45.1],
      "Yemen":              [15.6,  48.5],
      "Syria":              [35.0,  38.0],
      "Turkey":             [38.9,  35.2],
      "United States":      [38.0, -97.0],
      "Russia":             [61.5, 105.3],
      "India":              [20.6,  78.9],
      "Pakistan":           [30.4,  69.3],
      "European Union":     [51.2,  10.4],
      "Europe":             [50.0,  10.0],
      "United Kingdom":     [55.4,  -3.4],
      "UK":                 [55.4,  -3.4],
      "Belarus":            [53.7,  27.9],
      "Ukraine":            [49.0,  31.5],
      "China":              [35.9, 104.2],
      "Japan":              [36.2, 138.3],
      "North Korea":        [40.3, 127.5],
      "South Korea":        [36.5, 127.9],
      "Taiwan":             [23.7, 121.0],
      "Egypt":              [26.8,  30.8],
      "France":             [46.2,   2.2],
      "Germany":            [51.2,  10.4],
      "Poland":             [52.0,  19.1],
      "Australia":          [-25.3, 133.8],
      "Brazil":             [-14.2, -51.9],
      "Canada":             [56.1, -96.8],
      "Mexico":             [23.6,-102.6],
    };

    function _geoToSvgPath(geometry, W, H) {
      function projRing(ring) {
        var d = "";
        for (var i = 0; i < ring.length; i++) {
          var x = ((ring[i][0] + 180) / 360) * W;
          var y = ((90 - ring[i][1]) / 180) * H;
          // Break path on antimeridian jump to prevent horizontal lines across the map
          var jump = i > 0 && Math.abs(ring[i][0] - ring[i - 1][0]) > 180;
          d += (i === 0 || jump ? "M" : "L") + x.toFixed(1) + "," + y.toFixed(1);
        }
        return d + "Z";
      }
      var d = "";
      if (!geometry) return d;
      if (geometry.type === "Polygon") {
        geometry.coordinates.forEach(function (r) { d += projRing(r); });
      } else if (geometry.type === "MultiPolygon") {
        geometry.coordinates.forEach(function (poly) {
          poly.forEach(function (r) { d += projRing(r); });
        });
      }
      return d;
    }

    // Point-in-polygon (ray casting, lon/lat space)
    function _ptInPoly(lon, lat, feature) {
      function testRing(ring) {
        var inside = false;
        for (var i = 0, j = ring.length - 1; i < ring.length; j = i++) {
          var xi = ring[i][0], yi = ring[i][1], xj = ring[j][0], yj = ring[j][1];
          if (((yi > lat) !== (yj > lat)) &&
              (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi)) inside = !inside;
        }
        return inside;
      }
      var geom = feature.geometry;
      if (geom.type === "Polygon") return testRing(geom.coordinates[0]);
      if (geom.type === "MultiPolygon") {
        for (var k = 0; k < geom.coordinates.length; k++)
          if (testRing(geom.coordinates[k][0])) return true;
      }
      return false;
    }

    // Bounding box of a feature's geometry (ignores antimeridian artefacts)
    function _polyBounds(feature) {
      var minLon = 180, maxLon = -180, minLat = 90, maxLat = -90;
      function scan(ring) {
        ring.forEach(function (pt) {
          if (Math.abs(pt[0]) < 180) { minLon = Math.min(minLon, pt[0]); maxLon = Math.max(maxLon, pt[0]); }
          minLat = Math.min(minLat, pt[1]); maxLat = Math.max(maxLat, pt[1]);
        });
      }
      var geom = feature.geometry;
      if (geom.type === "Polygon") geom.coordinates.forEach(scan);
      else if (geom.type === "MultiPolygon") geom.coordinates.forEach(function (p) { p.forEach(scan); });
      return { minLon: minLon, maxLon: maxLon, minLat: minLat, maxLat: maxLat };
    }

    // Place a prediction dot within the country polygon.
    // Spreads evenly by index, shrinks radius 10% at a time until inside polygon.
    // Falls back to exact centroid if nothing fits.
    function _spreadLonLat(baseLon, baseLat, idx, n, h, feature) {
      if (!feature || n <= 1) return { lon: baseLon, lat: baseLat };
      var b = _polyBounds(feature);
      var rLon = Math.min((b.maxLon - b.minLon) * 0.18, 3.5);
      var rLat = Math.min((b.maxLat - b.minLat) * 0.18, 3.5);
      var angle = 2 * Math.PI * idx / n + ((h >> 8) & 0xff) / 255 * 0.3 - 0.15;
      for (var s = 10; s >= 1; s--) {
        var tryLon = baseLon + Math.cos(angle) * rLon * s / 10;
        var tryLat = baseLat + Math.sin(angle) * rLat * s / 10;
        if (_ptInPoly(tryLon, tryLat, feature)) return { lon: tryLon, lat: tryLat };
      }
      return { lon: baseLon, lat: baseLat };
    }

    function _renderWorldMap(feed, shown, domainHex) {
      var W = 800, H = 400;
      var NS = "http://www.w3.org/2000/svg";

      var wrap = document.createElement("div");
      wrap.className = "pred-worldmap-wrap";
      wrap.style.position = "relative";

      var mapSvg = document.createElementNS(NS, "svg");
      mapSvg.setAttribute("viewBox", "0 0 800 400");
      mapSvg.setAttribute("preserveAspectRatio", "xMidYMid meet");
      mapSvg.style.cssText = "width:100%;height:400px;display:block;cursor:grab;touch-action:none";
      wrap.appendChild(mapSvg);

      var detailEl = document.createElement("div");
      detailEl.className = "pred-detail pred-geo-detail";
      detailEl.style.display = "none";
      wrap.appendChild(detailEl);

      var resetBtnEl = document.createElement("button");
      resetBtnEl.className = "pred-map-reset-btn";
      resetBtnEl.textContent = "↺ reset";
      wrap.appendChild(resetBtnEl);

      feed.appendChild(wrap);

      // Legend — built from the actual domainHex keys so it always stays in sync
      var legendEl = document.createElement("div");
      legendEl.className = "pred-map-legend";
      Object.keys(domainHex).forEach(function (key) {
        var col = domainHex[key];
        var label = key.charAt(0).toUpperCase() + key.slice(1);
        var item = document.createElement("span");
        item.className = "pred-map-legend-item";
        item.innerHTML = '<svg width="10" height="10" style="vertical-align:middle;margin-right:4px"><circle cx="5" cy="5" r="4" fill="' + col + '" fill-opacity="0.88"/></svg>' + label;
        legendEl.appendChild(item);
      });
      feed.appendChild(legendEl);

      // Zoom/pan state
      var _vx = 0, _vy = 0, _vw = W;
      var MIN_W = W / 12;
      // Non-scaling dot inner groups — updated on every zoom change
      var _dotInners = [];

      function _vh() { return _vw * H / W; }
      function _applyVB() {
        mapSvg.setAttribute("viewBox", _vx.toFixed(2) + " " + _vy.toFixed(2) + " " + _vw.toFixed(2) + " " + _vh().toFixed(2));
        // Counteract zoom on dot visuals so they stay fixed screen size
        var s = (_vw / W).toFixed(5);
        for (var i = 0; i < _dotInners.length; i++)
          _dotInners[i].setAttribute("transform", "scale(" + s + ")");
      }
      function _zoomAt(mx, my, factor) {
        var sx = _vx + mx * _vw, sy = _vy + my * _vh();
        _vw = Math.max(MIN_W, Math.min(W, _vw * factor));
        _vx = Math.max(0, Math.min(W - _vw, sx - mx * _vw));
        _vy = Math.max(0, Math.min(H - _vh(), sy - my * _vh()));
        _applyVB();
      }

      // No setPointerCapture — window listeners are added/removed per gesture so
      // click events fire normally on dot elements without capture interference.
      var _ptrs = {}, _drag = null, _pinch = null, _wasDrag = false;
      var _winMove = null, _winUp = null;

      function _cleanupWin() {
        if (_winMove) { window.removeEventListener("pointermove", _winMove); _winMove = null; }
        if (_winUp)   { window.removeEventListener("pointerup",   _winUp);   _winUp   = null; }
      }

      mapSvg.addEventListener("pointerdown", function (e) {
        _ptrs[e.pointerId] = { x: e.clientX, y: e.clientY };
        var n = Object.keys(_ptrs).length;
        if (n === 1) {
          _drag = { sx: e.clientX, sy: e.clientY, vx: _vx, vy: _vy };
          _wasDrag = false;
          mapSvg.style.cursor = "grabbing";
        } else if (n === 2) {
          _drag = null;
          var pp = Object.values(_ptrs);
          _pinch = { dist: Math.hypot(pp[1].x - pp[0].x, pp[1].y - pp[0].y),
                     cx: (pp[0].x + pp[1].x) / 2, cy: (pp[0].y + pp[1].y) / 2,
                     vx: _vx, vy: _vy, vw: _vw };
        }
        if (!_winMove) {
          _winMove = function (e2) {
            if (!_ptrs[e2.pointerId]) return;
            _ptrs[e2.pointerId] = { x: e2.clientX, y: e2.clientY };
            var n2 = Object.keys(_ptrs).length;
            if (n2 === 1 && _drag) {
              var dx = e2.clientX - _drag.sx, dy = e2.clientY - _drag.sy;
              if (Math.abs(dx) > 3 || Math.abs(dy) > 3) _wasDrag = true;
              var rect = mapSvg.getBoundingClientRect();
              _vx = Math.max(0, Math.min(W - _vw, _drag.vx - dx / rect.width * _vw));
              _vy = Math.max(0, Math.min(H - _vh(), _drag.vy - dy / rect.height * _vh()));
              _applyVB();
            } else if (n2 === 2 && _pinch) {
              var pp2 = Object.values(_ptrs);
              var newDist = Math.hypot(pp2[1].x - pp2[0].x, pp2[1].y - pp2[0].y);
              var rect2 = mapSvg.getBoundingClientRect();
              _vx = _pinch.vx; _vy = _pinch.vy; _vw = _pinch.vw;
              _zoomAt((_pinch.cx - rect2.left) / rect2.width, (_pinch.cy - rect2.top) / rect2.height, _pinch.dist / newDist);
              _wasDrag = true;
            }
          };
          _winUp = function (e2) {
            delete _ptrs[e2.pointerId];
            if (Object.keys(_ptrs).length === 0) {
              _cleanupWin();
              _drag = null; _pinch = null; mapSvg.style.cursor = "grab";
            }
          };
          window.addEventListener("pointermove", _winMove);
          window.addEventListener("pointerup",   _winUp);
        }
      });

      mapSvg.addEventListener("wheel", function (e) {
        e.preventDefault();
        var rect = mapSvg.getBoundingClientRect();
        _zoomAt((e.clientX - rect.left) / rect.width, (e.clientY - rect.top) / rect.height, e.deltaY > 0 ? 1.25 : 0.8);
      }, { passive: false });

      mapSvg.addEventListener("dblclick", function (e) {
        if (_wasDrag) return;
        var rect = mapSvg.getBoundingClientRect();
        _zoomAt((e.clientX - rect.left) / rect.width, (e.clientY - rect.top) / rect.height, 0.4);
      });

      resetBtnEl.addEventListener("click", function () {
        _vx = 0; _vy = 0; _vw = W; _applyVB();
      });

      function _buildMap(topoData) {
        _dotInners = [];
        while (mapSvg.firstChild) mapSvg.removeChild(mapSvg.firstChild);

        var bg = document.createElementNS(NS, "rect");
        bg.setAttribute("x", 0); bg.setAttribute("y", 0);
        bg.setAttribute("width", W); bg.setAttribute("height", H);
        bg.setAttribute("fill", "rgba(13,16,24,0.65)"); bg.setAttribute("rx", "4");
        mapSvg.appendChild(bg);

        var isoPolyMap = {};
        if (typeof topojson !== "undefined" && topoData && topoData.objects) {
          var countries = topojson.feature(topoData, topoData.objects.countries);
          countries.features.forEach(function (feat) {
            isoPolyMap[feat.id] = feat;
            var d = _geoToSvgPath(feat.geometry, W, H);
            if (!d) return;
            var path = document.createElementNS(NS, "path");
            path.setAttribute("d", d);
            path.setAttribute("fill", "rgba(55,70,105,0.7)");
            path.setAttribute("stroke", "rgba(141,152,180,0.18)");
            path.setAttribute("stroke-width", "0.4");
            path.setAttribute("pointer-events", "none");
            mapSvg.appendChild(path);
          });
        }

        // Draw and register mini polygons for countries too small for 110m TopoJSON (e.g. Bahrain)
        Object.keys(_MINI_POLYS).forEach(function (isoId) {
          if (isoPolyMap[isoId]) return;
          var feat = _MINI_POLYS[isoId];
          isoPolyMap[isoId] = feat;
          var d = _geoToSvgPath(feat.geometry, W, H);
          if (!d) return;
          var path = document.createElementNS(NS, "path");
          path.setAttribute("d", d);
          path.setAttribute("fill", "rgba(55,70,105,0.7)");
          path.setAttribute("stroke", "rgba(141,152,180,0.4)");
          path.setAttribute("stroke-width", "0.8");
          path.setAttribute("pointer-events", "none");
          mapSvg.appendChild(path);
        });

        var eqY = ((90 - 0) / 180) * H;
        var eq = document.createElementNS(NS, "line");
        eq.setAttribute("x1", 0); eq.setAttribute("x2", W);
        eq.setAttribute("y1", eqY); eq.setAttribute("y2", eqY);
        eq.setAttribute("stroke", "rgba(141,152,180,0.07)");
        eq.setAttribute("stroke-width", "1");
        eq.setAttribute("pointer-events", "none");
        mapSvg.appendChild(eq);

        // Pre-compute per-geography sorted ID list for evenly-spaced spread
        var geoIdLists = {};
        shown.forEach(function (p) {
          if (!p.geography || !_GEO_COORDS[p.geography]) return;
          if (!geoIdLists[p.geography]) geoIdLists[p.geography] = [];
          geoIdLists[p.geography].push(p.id);
        });
        Object.keys(geoIdLists).forEach(function (geo) { geoIdLists[geo].sort(); });

        var glowMap = {};

        shown.forEach(function (p) {
          var coords = _GEO_COORDS[p.geography];
          if (!coords) return;
          var lat = coords[0], lon = coords[1];

          // Hash for per-prediction variety
          var h = 0;
          for (var i = 0; i < p.id.length; i++) h = (h * 31 + p.id.charCodeAt(i)) & 0xffff;

          var ids = geoIdLists[p.geography] || [p.id];
          var n = ids.length;
          var idx = ids.indexOf(p.id);

          // Polygon-constrained spread: positions are in lat/lon space, guaranteed inside border
          var isoId = _GEO_ISO[p.geography];
          var poly = isoId ? isoPolyMap[isoId] : null;
          var pos = _spreadLonLat(lon, lat, idx, n, h, poly);
          var x = ((pos.lon + 180) / 360) * W;
          var y = ((90 - pos.lat) / 180) * H;

          var r = 5; // fixed logical radius — inner group counteracts zoom for fixed screen size
          var col = domainHex[p.domain] || domainHex.general;

          // Outer group: moves with map (translate in SVG space)
          var outer = document.createElementNS(NS, "g");
          outer.setAttribute("class", "pred-geo-pin");
          outer.setAttribute("transform", "translate(" + x.toFixed(1) + "," + y.toFixed(1) + ")");
          outer.style.cursor = "pointer";

          // Inner group: scale(1/zoom) applied by _applyVB so dot stays fixed screen size
          var inner = document.createElementNS(NS, "g");
          inner.setAttribute("transform", "scale(1)");
          _dotInners.push(inner);
          outer.appendChild(inner);

          if (p.verified == null) {
            var ring = document.createElementNS(NS, "circle");
            ring.setAttribute("r", r + 6);
            ring.setAttribute("fill", col);
            ring.setAttribute("fill-opacity", "0.22");
            ring.setAttribute("class", "pred-geo-ring");
            ring.style.animationDelay = (Math.abs(h % 200) / 100) + "s";
            inner.appendChild(ring);
          }

          var glow = document.createElementNS(NS, "circle");
          glow.setAttribute("r", r + 5);
          glow.setAttribute("fill", col);
          glow.setAttribute("fill-opacity", "0");
          glow.style.transition = "fill-opacity .12s";
          inner.appendChild(glow);
          glowMap[p.id] = glow;

          if (p.verified === true) {
            var vRing = document.createElementNS(NS, "circle");
            vRing.setAttribute("r", r + 4);
            vRing.setAttribute("fill", "none");
            vRing.setAttribute("stroke", "#34d399");
            vRing.setAttribute("stroke-width", "1.5");
            vRing.setAttribute("stroke-opacity", "0.7");
            vRing.setAttribute("pointer-events", "none");
            inner.appendChild(vRing);
          }

          var dot = document.createElementNS(NS, "circle");
          dot.setAttribute("r", r);
          dot.setAttribute("fill", col);
          dot.setAttribute("fill-opacity", p.verified === false ? "0.28" : "0.88");
          inner.appendChild(dot);

          outer.addEventListener("mouseenter", function () {
            if (_selectedPredId !== p.id) glow.setAttribute("fill-opacity", "0.2");
          });
          outer.addEventListener("mouseleave", function () {
            if (_selectedPredId !== p.id) glow.setAttribute("fill-opacity", "0");
          });

          outer.addEventListener("click", function (e) {
            e.stopPropagation();
            if (_wasDrag) { _wasDrag = false; return; }
            if (_selectedPredId === p.id) {
              _selectedPredId = null; glow.setAttribute("fill-opacity", "0");
              detailEl.style.display = "none"; return;
            }
            if (_selectedPredId && glowMap[_selectedPredId])
              glowMap[_selectedPredId].setAttribute("fill-opacity", "0");
            _selectedPredId = p.id;
            glow.setAttribute("fill-opacity", "0.32");

            var conf = p.confidence != null ? Math.round(p.confidence * 100) + "%" : "—";
            var tf = _fmtDate(p.timeframe);
            var vHtml = p.verified === true
              ? '<span class="pred-v-ok">&#10003; Verified correct</span>'
              : p.verified === false
                ? '<span class="pred-v-err">&#10007; Verified wrong</span>'
                : '<span class="pred-v-pend">Pending</span>';
            var domClass = DOMAIN_CLASS[p.domain] || "pred-domain-general";
            detailEl.innerHTML =
              '<div class="pred-detail-row">' +
                '<span class="pred-domain ' + domClass + '">' + esc(p.domain || "general") + '</span>' +
                (p.geography ? '<span class="pred-geo-tag">&#9679; ' + esc(p.geography) + '</span>' : '') +
                '<span class="pred-confidence">' + conf + '</span>' +
                (p.timeframe ? '<span class="pred-timeframe">by ' + esc(tf) + '</span>' : '') +
                vHtml +
                '<button class="pred-detail-x" title="Close">&times;</button>' +
              '</div>' +
              '<p class="pred-detail-body">' + esc(p.text) + '</p>' +
              (p.brief ? '<p class="pred-detail-brief">' + esc(p.brief) + '</p>' : '') +
              '<button class="pred-copy-btn pred-detail-copy">Copy</button>';
            detailEl.style.display = "block";
            detailEl.querySelector(".pred-detail-x").addEventListener("click", function (ev) {
              ev.stopPropagation(); _selectedPredId = null;
              glow.setAttribute("fill-opacity", "0"); detailEl.style.display = "none";
            });
            detailEl.querySelector(".pred-detail-copy").addEventListener("click", function () {
              var txt = "[" + (p.domain || "general").toUpperCase() + " \xb7 " + conf + "] by " + tf + "\n" + p.text;
              navigator.clipboard.writeText(txt).then(function () {
                var b = detailEl.querySelector(".pred-detail-copy");
                if (b) { b.textContent = "Copied"; setTimeout(function () { if (b) b.textContent = "Copy"; }, 2000); }
              });
            });
          });

          mapSvg.appendChild(outer);
        });

        bg.addEventListener("click", function () {
          if (_wasDrag) { _wasDrag = false; return; }
          if (_selectedPredId && glowMap[_selectedPredId])
            glowMap[_selectedPredId].setAttribute("fill-opacity", "0");
          _selectedPredId = null; detailEl.style.display = "none";
        });
      }

      if (_worldData) {
        _buildMap(_worldData);
      } else {
        var loadTxt = document.createElementNS(NS, "text");
        loadTxt.setAttribute("x", W / 2); loadTxt.setAttribute("y", H / 2);
        loadTxt.setAttribute("text-anchor", "middle");
        loadTxt.setAttribute("fill", "rgba(141,152,180,0.35)");
        loadTxt.setAttribute("font-size", "11"); loadTxt.setAttribute("font-family", "monospace");
        loadTxt.textContent = "Loading map…";
        mapSvg.appendChild(loadTxt);
        fetch("/static/world-110m.json").then(function (r) { return r.json(); }).then(function (data) {
          _worldData = data; _buildMap(_worldData);
        }).catch(function () { loadTxt.textContent = "Map unavailable"; });
      }
    }

    function _fetchPredictions(onDone) {
      fetch("/api/predictions?limit=200").then(function (r) { return r.json(); }).then(function (d) {
        _predsAll = d.predictions || [];
        if (onDone) onDone();
      }).catch(function (e) {
        if (onDone) onDone(e);
      });
    }

    function _loadPredictions(filter) {
      if (filter) _predFilter = filter;
      var feed = document.getElementById("predictions-feed");
      if (!feed) return;
      // Show stale data immediately, then replace once fresh data arrives
      if (_predsAll.length) _renderMap(_predsAll);
      else feed.innerHTML = '<div class="know-empty-state"><div class="know-empty-icon">◎</div><div class="know-empty-title">Loading…</div></div>';
      _fetchPredictions(function (err) {
        if (err) {
          if (!_predsAll.length)
            feed.innerHTML = '<div class="know-empty-state"><div class="know-empty-title">Could not reach predictions source</div><div class="know-empty-sub">' + esc(String(err)) + "</div></div>";
        } else {
          _renderMap(_predsAll);
        }
      });
    }

    // Background poll — re-renders map only if the intel panel is visible
    setInterval(function () {
      _fetchPredictions(function () {
        var panel = document.getElementById("know-panel-intel");
        if (panel && panel.classList.contains("know-panel-active")) {
          _renderMap(_predsAll);
        }
      });
    }, 60000);

    // Filter buttons
    ["pred-filter-all", "pred-filter-pending", "pred-filter-verified"].forEach(function (id) {
      var btn = document.getElementById(id);
      if (!btn) return;
      btn.addEventListener("click", function () {
        document.querySelectorAll("#intel-predictions .know-toggle-btn").forEach(function (b) {
          b.classList.remove("know-toggle-active");
        });
        btn.classList.add("know-toggle-active");
        _predFilter = id.replace("pred-filter-", "");
        _renderMap(_predsAll);
      });
    });

    // ── File upload ───────────────────────────────────────────────────────────
    var _dropZone   = document.getElementById("file-drop-zone");
    var _fileInput  = document.getElementById("file-upload-input");
    var _statusList = document.getElementById("file-upload-status");

    function _addStatus(name, ok, msg) {
      if (!_statusList) return;
      var el = document.createElement("div");
      el.className = "file-status-item " + (ok ? "file-status-ok" : "file-status-err");
      el.textContent = (ok ? "✓ " : "✗ ") + name + (msg ? " — " + msg : "");
      _statusList.insertBefore(el, _statusList.firstChild);
      setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 8000);
    }

    function _uploadFiles(files) {
      if (!files || !files.length) return;
      var fd = new FormData();
      Array.from(files).forEach(function (f) { fd.append("file", f); });
      fetch("/api/files/upload", { method: "POST", body: fd })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          (d.saved || []).forEach(function (n) { _addStatus(n, true); });
          (d.errors || []).forEach(function (e) { _addStatus(e, false); });
        })
        .catch(function (e) { _addStatus("Upload failed", false, String(e)); });
    }

    if (_dropZone) {
      ["dragenter", "dragover"].forEach(function (ev) {
        _dropZone.addEventListener(ev, function (e) { e.preventDefault(); _dropZone.classList.add("drag-over"); });
      });
      ["dragleave", "drop"].forEach(function (ev) {
        _dropZone.addEventListener(ev, function (e) { e.preventDefault(); _dropZone.classList.remove("drag-over"); });
      });
      _dropZone.addEventListener("drop", function (e) { _uploadFiles(e.dataTransfer.files); });
    }
    if (_fileInput) {
      _fileInput.addEventListener("change", function () { _uploadFiles(_fileInput.files); _fileInput.value = ""; });
    }

    // ── File download list ────────────────────────────────────────────────────
    function _fmtSize(bytes) {
      if (bytes < 1024) return bytes + " B";
      if (bytes < 1048576) return (bytes / 1024).toFixed(1) + " KB";
      return (bytes / 1048576).toFixed(1) + " MB";
    }

    function _loadFileList() {
      var list = document.getElementById("file-download-list");
      if (!list) return;
      fetch("/api/files/list").then(function (r) { return r.json(); }).then(function (d) {
        var files = d.files || [];
        if (!files.length) {
          list.innerHTML = '<div class="know-empty-state know-empty-state-sm"><div class="know-empty-icon">▤</div><div class="know-empty-title">No files ready</div><div class="know-empty-sub">Ask Claude Code to put a file in ~/inbox/llamawatch-share/</div></div>';
          return;
        }
        list.innerHTML = files.map(function (f) {
          return '<div class="file-dl-row">' +
            '<span class="file-dl-name" title="' + esc(f.name) + '">' + esc(f.name) + '</span>' +
            '<span class="file-dl-size">' + _fmtSize(f.size) + '</span>' +
            '<a class="file-dl-btn" href="/api/files/download/' + encodeURIComponent(f.name) + '" download="' + esc(f.name) + '">Download</a>' +
            '<button class="file-dl-del" data-fname="' + esc(f.name) + '" title="Remove">&#10005;</button>' +
          '</div>';
        }).join("");
        list.querySelectorAll(".file-dl-del").forEach(function (btn) {
          btn.addEventListener("click", function () {
            var name = btn.dataset.fname;
            fetch("/api/files/share/" + encodeURIComponent(name), { method: "DELETE" })
              .then(function () { _loadFileList(); });
          });
        });
      }).catch(function () {
        list.innerHTML = '<div class="know-empty-state know-empty-state-sm"><div class="know-empty-title">Could not load file list</div></div>';
      });
    }

    var _refreshBtn = document.getElementById("files-refresh-btn");
    if (_refreshBtn) _refreshBtn.addEventListener("click", _loadFileList);

    // Article rows are already wired to openArticleWindow via the WM patch above.
    // No additional row buttons needed — Copy/Download/Draft live in the popup.

    // Pre-fetch predictions + world map on page load so the intel tab renders instantly
    fetch("/api/predictions?limit=200").then(function (r) { return r.json(); }).then(function (d) {
      _predsAll = d.predictions || [];
    }).catch(function () {});
    fetch("/static/world-110m.json").then(function (r) { return r.json(); }).then(function (data) {
      _worldData = data;
    }).catch(function () {});

  })();

  // ── Voice command ────────────────────────────────────────────────────────────
  (function VoiceCommand() {
    var btn = document.getElementById("hero-voice-btn");
    if (!btn) return;
    // Don't wire up if voice is disabled in config (button will be hidden anyway)
    try {
      var _cached = JSON.parse(localStorage.getItem("studio-panels") || "{}");
      if (_cached.voice === false) return;
    } catch(e) {}


    var SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;

    if (!SpeechRec) {
      btn.title = "Voice not supported — use Chrome";
      return;
    }

    // Requires HTTPS — mic won't work over plain HTTP
    if (!window.isSecureContext) {
      btn.title = "Voice requires HTTPS — use the Tailscale URL";
      btn.disabled = false;
      btn.classList.add("voice-ready");
      btn.addEventListener("click", function () {
        alert("Voice command requires a secure (HTTPS) connection. Open the dashboard over HTTPS to use it.");
      });
      return;
    }

    // Ready to use — make button fully visible
    btn.disabled = false;
    btn.classList.add("voice-ready");
    btn.title = "Voice command — tap and speak";

    // Overlay for interim text (floats above everything including fullscreen terminal)
    var overlay = document.createElement("div");
    overlay.className = "voice-overlay";
    overlay.style.display = "none";
    document.body.appendChild(overlay);

    var _rec    = null;
    var _active = false;
    var _stream = null; // getUserMedia stream kept alive during recognition

    function _waitForTerminal(cb) {
      var attempts = 0;
      var timer = setInterval(function () {
        var sess = TerminalSessions.getSession();
        if (sess && sess.ws && sess.ws.readyState === WebSocket.OPEN) {
          clearInterval(timer);
          cb(sess);
        } else if (++attempts > 50) {  // 5s max
          clearInterval(timer);
          var s = TerminalSessions.getSession();
          if (s) cb(s);
        }
      }, 100);
    }

    function _stopListening() {
      _active = false;
      btn.classList.remove("voice-active");
      btn.title = "Voice command — tap and speak";
      if (_rec) { try { _rec.stop(); } catch (e) {} _rec = null; }
      // Release the getUserMedia stream now that recognition is done
      if (_stream) { _stream.getTracks().forEach(function (t) { t.stop(); }); _stream = null; }
      setTimeout(function () {
        overlay.classList.add("voice-overlay-hide");
        setTimeout(function () {
          overlay.style.display = "none";
          overlay.classList.remove("voice-overlay-hide");
          overlay.textContent = "";
          overlay.style.color = "";
        }, 300);
      }, 600);
    }

    function _startRecognition(sess) {
      _rec = new SpeechRec();
      _rec.continuous      = false;
      _rec.interimResults  = true;
      _rec.lang            = "en-GB";
      _rec.maxAlternatives = 1;

      _rec.onresult = function (e) {
        var interim = "", final = "";
        for (var i = e.resultIndex; i < e.results.length; i++) {
          if (e.results[i].isFinal) final += e.results[i][0].transcript;
          else interim += e.results[i][0].transcript;
        }
        overlay.textContent = final || interim;
        if (final) {
          // Inject text into terminal PTY — no newline, user reviews and hits Enter
          if (sess.ws && sess.ws.readyState === WebSocket.OPEN) {
            sess.ws.send(final);
            if (sess.term) sess.term.focus();
          }
          _stopListening();
        }
      };

      _rec.onerror = function (e) {
        var msg = e.error === "not-allowed"      ? "Mic blocked — check browser permissions"
                : e.error === "no-speech"        ? "No speech detected — try again"
                : e.error === "not-allowed"      ? "Mic permission denied"
                : e.error === "service-not-allowed" ? "Mic blocked — use HTTPS"
                : e.error === "network"          ? "Network error — check connection"
                : "Mic error: " + (e.error || "unknown");
        overlay.textContent = msg;
        overlay.style.color = "#f87171"; // red so it's obvious
        setTimeout(function () {
          overlay.style.color = "";
          _stopListening();
        }, 3000);
      };

      _rec.onend = function () { if (_active) _stopListening(); };

      try {
        _rec.start();
      } catch (e) {
        overlay.textContent = "Could not start mic";
        setTimeout(_stopListening, 1500);
      }
    }

    btn.addEventListener("click", function () {
      // Toggle off if already listening
      if (_active) { _stopListening(); return; }

      _active = true;
      btn.classList.add("voice-active");
      btn.title = "Listening… tap to cancel";
      overlay.textContent = "Requesting mic…";
      overlay.style.display = "flex";
      overlay.style.color = "";

      // Request mic permission explicitly first — this triggers the OS dialog on Android
      // then immediately release the stream and hand off to SpeechRecognition
      navigator.mediaDevices.getUserMedia({ audio: true })
        .then(function (stream) {
          if (!_active) { stream.getTracks().forEach(function (t) { t.stop(); }); return; }
          _stream = stream; // keep alive — helps Chrome bridge mic permission to SpeechRecognition
          overlay.textContent = "Listening…";

          // Open a terminal if none exists, then start recognition
          var sess = TerminalSessions.getSession();
          if (!sess) {
            TerminalSessions.open();
            _waitForTerminal(function (s) { _startRecognition(s); });
          } else if (sess.ws && sess.ws.readyState === WebSocket.OPEN) {
            _startRecognition(sess);
          } else {
            _waitForTerminal(function (s) { _startRecognition(s); });
          }
        })
        .catch(function () {
          overlay.textContent = "Mic blocked — allow in browser site settings";
          overlay.style.color = "#f87171";
          setTimeout(function () { overlay.style.color = ""; _stopListening(); }, 4000);
        });
    });
  })();

  // ── Mobile accordion ────────────────────────────────────────────────────────

  function _mobSum(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text || "—";
  }

  (function _initMobileAccordions() {
    if (window.innerWidth > 640) return;

    const SECTIONS = [
      // Command page
      { id: "left-col",       title: "VITALS",     sumId: "mob-sum-vitals"  },
      { id: "agents-section", title: "AGENTS",     sumId: "mob-sum-agents"  },
      { id: "gpu-section",    title: "GPU",         sumId: "mob-sum-gpu"     },
      { id: "mem-section",    title: "MEMORY",      sumId: "mob-sum-mem"     },
      // System page
      // System page fleet machine cards — generated from fleet config
      ...JSON.parse(localStorage.getItem("studio-fleet") || "[]").map(function(h) {
        var k = (h.name || "").toLowerCase();
        return { id: "fm-" + k, title: h.name || k, sumId: "mob-sum-fm-" + k };
      }),
      { id: "docker-panel",   title: "DOCKER",      sumId: "mob-sum-docker" },
      { id: "hogs-panel",     title: "PROCESSES",   sumId: null              },
    ];

    SECTIONS.forEach(function (def) {
      var el = document.getElementById(def.id);
      if (!el || el.dataset.mobWired) return;
      el.dataset.mobWired = "1";
      el.classList.add("mob-section");

      // Wrap existing children in a body div (preserves desktop layout — function only runs on mobile)
      var body = document.createElement("div");
      body.className = "mob-section-body";
      while (el.firstChild) body.appendChild(el.firstChild);
      el.appendChild(body);

      // Inject accordion header before the body
      var sumHtml = def.sumId
        ? '<span class="mob-sum" id="' + def.sumId + '">—</span>'
        : "";
      var hdr = document.createElement("div");
      hdr.className = "mob-section-hdr";
      hdr.innerHTML =
        '<div class="mob-hdr-left">' +
          '<span class="mob-hdr-title">' + def.title + "</span>" +
          sumHtml +
        "</div>" +
        '<span class="mob-hdr-chevron">&#8250;</span>';
      el.insertBefore(hdr, body);

      hdr.addEventListener("click", function () {
        var open = el.classList.toggle("mob-open");
        hdr.querySelector(".mob-hdr-chevron").style.transform = open ? "rotate(90deg)" : "";
      });
    });
  })();

})();
