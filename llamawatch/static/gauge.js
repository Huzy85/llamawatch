/* llamawatch — lwGauge  v32
 * Reusable SVG radial gauge. Clean solid arcs, label below the circle.
 *
 * Usage:  lwGauge(el, { value, max, label, unit, kind, color })
 *
 * kind:  'percent' — thresholds at 75% (warn) and 90% (bad)
 *        'temp'    — thresholds at 60°C (warn) and 80°C (bad)
 *
 * SVG: 270° arc, 100×100 viewBox, r=42, sw=8.
 *   Circumference ≈ 263.9 → full arc ≈ 197.9 → gap ≈ 65.97.
 *   SVG rotated 135° so the open gap sits at the bottom.
 */

(function () {
  "use strict";

  const R        = 42;
  const SW       = 8;
  const CIRC     = 2 * Math.PI * R;
  const FULL_ARC = CIRC * (270 / 360);
  const GAP_ARC  = CIRC - FULL_ARC;

  // ── Colour map ──────────────────────────────────────────────────────────────
  const _solidColor = {
    cyan:    "#22d3ee",
    lime:    "#86efac",
    magenta: "#e879f9",
    violet:  "#a78bfa",
    orange:  "#fb923c",
    pink:    "#f472b6",
    amber:   "#fbbf24",
    teal:    "#2dd4bf",
  };

  function _pickColor(kind, value, color) {
    // Danger thresholds always override custom color
    const isPercent = (kind !== "temp");
    const pct = isPercent ? value : null;
    const temp = !isPercent ? value : null;

    if (pct  != null && pct  >= 90) return { stroke: "#f87171", text: "#f87171" };
    if (pct  != null && pct  >= 75) return { stroke: "#fbbf24", text: "#fbbf24" };
    if (temp != null && temp >= 80) return { stroke: "#f87171", text: "#f87171" };
    if (temp != null && temp >= 60) return { stroke: "#fbbf24", text: "#fbbf24" };

    const c = _solidColor[color] || _solidColor.cyan;
    return { stroke: c, text: c };
  }

  // ── Shared SVG defs (single gradient stop per color — injected once) ────────
  let _defsInjected = false;
  function _ensureDefs() {
    if (_defsInjected) return;
    _defsInjected = true;
    // Nothing needed — using solid stroke colors directly (no gradients).
  }

  // ── Build gauge DOM (called once per element) ───────────────────────────────
  function _build(el, opts) {
    const { label, unit } = opts;
    const ns = "http://www.w3.org/2000/svg";

    // Gauge-wrap is already flex-column (set by CSS or here).
    el.style.cssText = [
      "position:relative",
      "display:flex",
      "flex-direction:column",
      "align-items:center",
      "justify-content:flex-start",
    ].join(";");

    // ── SVG area (takes all height except label row) ────────────────────────
    const svgArea = document.createElement("div");
    svgArea.style.cssText = [
      "position:relative",
      "flex:1",
      "width:100%",
      "min-height:0",
    ].join(";");

    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 100 100");
    svg.setAttribute("width",   "100%");
    svg.setAttribute("height",  "100%");
    svg.style.transform = "rotate(135deg)";
    svg.style.display   = "block";

    // Track (background ring)
    const track = document.createElementNS(ns, "circle");
    track.setAttribute("cx", "50"); track.setAttribute("cy", "50");
    track.setAttribute("r",  String(R));
    track.setAttribute("fill", "none");
    track.setAttribute("stroke", "rgba(255,255,255,0.08)");
    track.setAttribute("stroke-width", String(SW));
    track.setAttribute("stroke-linecap", "round");
    track.setAttribute("stroke-dasharray", `${FULL_ARC} ${GAP_ARC + 0.5}`);

    // Value arc — driven by stroke-dasharray (arcLen + gap) to avoid wrap-around artefacts
    const arc = document.createElementNS(ns, "circle");
    arc.setAttribute("cx", "50"); arc.setAttribute("cy", "50");
    arc.setAttribute("r",  String(R));
    arc.setAttribute("fill", "none");
    arc.setAttribute("stroke-width", String(SW));
    arc.setAttribute("stroke-linecap", "round");
    arc.setAttribute("stroke-dasharray", `0 ${CIRC}`);
    arc.setAttribute("stroke-dashoffset", "0");
    arc.style.transition = "stroke-dasharray .6s cubic-bezier(.4,0,.2,1), stroke .35s ease";

    svg.appendChild(track);
    svg.appendChild(arc);
    svgArea.appendChild(svg);

    // Centre overlay: value + unit (NO label — label goes below)
    const centre = document.createElement("div");
    centre.style.cssText = [
      "position:absolute",
      "top:50%", "left:50%",
      "transform:translate(-50%,-50%)",
      "display:flex",
      "flex-direction:column",
      "align-items:center",
      "justify-content:center",
      "pointer-events:none",
      "line-height:1.15",
      "text-align:center",
    ].join(";");

    const valSpan = document.createElement("span");
    valSpan.style.cssText = [
      "font-family:var(--font-mono,'SF Mono',monospace)",
      "font-variant-numeric:tabular-nums",
      "font-size:1.3em",
      "font-weight:700",
      "color:var(--text-1)",
      "letter-spacing:-.02em",
    ].join(";");
    valSpan.textContent = "--";

    const unitSpan = document.createElement("span");
    unitSpan.style.cssText = [
      "font-size:.58em",
      "color:var(--text-3)",
      "margin-top:1px",
    ].join(";");
    unitSpan.textContent = unit || "";

    centre.appendChild(valSpan);
    if (unit) centre.appendChild(unitSpan);
    svgArea.appendChild(centre);
    el.appendChild(svgArea);

    // ── Label row — below the circle ───────────────────────────────────────
    const labelDiv = document.createElement("div");
    labelDiv.style.cssText = [
      "flex-shrink:0",
      "height:14px",
      "line-height:14px",
      "font-size:9px",
      "font-weight:600",
      "color:var(--text-2)",
      "text-transform:uppercase",
      "letter-spacing:.12em",
      "text-align:center",
      "white-space:nowrap",
    ].join(";");
    labelDiv.textContent = label || "";
    el.appendChild(labelDiv);

    // Store refs
    el._lwgArc     = arc;
    el._lwgVal     = valSpan;
    el._lwgLabel   = labelDiv;
    el._lwgBuilt   = true;
  }

  // ── Public API ──────────────────────────────────────────────────────────────
  function lwGauge(el, opts) {
    if (!el) return;
    _ensureDefs();

    if (!el._lwgBuilt) _build(el, opts);

    const { value, max, kind, color } = opts;

    if (value == null || isNaN(value)) {
      if (el._lwgVal) el._lwgVal.textContent = "--";
      if (el._lwgArc) el._lwgArc.setAttribute("stroke-dasharray", `0 ${CIRC}`);
      return;
    }

    const effectiveMax = (max != null && max > 0) ? max : 100;
    const pct    = Math.max(0, Math.min(100, (value / effectiveMax) * 100));
    const arcLen = (pct / 100) * FULL_ARC;
    const colors = _pickColor(kind, kind === "temp" ? value : pct, color);

    const arc = el._lwgArc;
    if (arc) {
      arc.setAttribute("stroke", colors.stroke);
      arc.setAttribute("stroke-dasharray", `${arcLen.toFixed(2)} ${(CIRC - arcLen).toFixed(2)}`);
    }

    const valEl = el._lwgVal;
    if (valEl) {
      valEl.textContent = (kind === "percent" || kind === "temp")
        ? String(Math.round(value))
        : value.toFixed(1);
      valEl.style.color = colors.text;
    }
  }

  window.lwGauge = lwGauge;

})();
