/* llamawatch layout: how many dashboard pages fit side by side, and the side rail.
   Loaded before studio.js. No storage access here may throw. */
(function () {
  "use strict";
  var _pages = 1, _subs = [], _timer = null;

  // Window shape decides: 16:9 stays 1 page even with browser bars (1920x950 = 2.02).
  function pagesFor(w, h) {
    if (!w || !h || h > w) return 1;
    var r = w / h;
    if (r >= 3.0 && w >= 3600) return 3;
    if (r >= 2.2 && w >= 2200) return 2;
    return 1;
  }

  function measure() {
    var n = pagesFor(window.innerWidth, window.innerHeight);
    document.documentElement.dataset.pages = String(n);
    var changed = n !== _pages;
    _pages = n;
    return changed;
  }

  function onResize() {
    clearTimeout(_timer);
    _timer = setTimeout(function () {
      if (measure()) _subs.forEach(function (fn) { try { fn(_pages); } catch (e) { console.warn(e); } });
    }, 100);
  }

  measure();
  window.addEventListener("resize", onResize);
  window.addEventListener("orientationchange", onResize);

  // ── Rail ─────────────────────────────────────────────────────────────────
  var PIN_KEY = "lw-rail-pinned";

  // Storage that never throws: blocked or private-mode storage falls back to memory for this tab.
  var store = (function () {
    try { var s = window.localStorage; s.getItem("lw-probe"); return s; } catch (e) {}
    var m = {};
    return {
      getItem: function (k) { return Object.prototype.hasOwnProperty.call(m, k) ? m[k] : null; },
      setItem: function (k, v) { m[k] = String(v); },
      removeItem: function (k) { delete m[k]; }
    };
  })();
  function _get(k) { try { return store.getItem(k); } catch (e) { return null; } }
  function _set(k, v) { try { store.setItem(k, v); } catch (e) {} }

  var ICONS = {
    monitor: '<path d="M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z"/>',
    research: '<circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5.5 5.5M8 10.5h5M10.5 8v5"/>',
    jobs: '<circle cx="12" cy="12" r="8"/><path d="M12 7.5V12l3 2"/>',
    approvals: '<path d="M12 3l7 3v5c0 4.5-3 8.5-7 10-4-1.5-7-5.5-7-10V6z"/><path d="M9 12l2 2 4-4"/>',
    spend: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5v9M9.5 10.2c0-1 1-1.7 2.5-1.7s2.5.7 2.5 1.7-1 1.4-2.5 1.8-2.5.8-2.5 1.8 1 1.7 2.5 1.7 2.5-.7 2.5-1.7"/>',
    agents: '<rect x="5" y="8" width="14" height="11" rx="2"/><path d="M12 4v4M9 13h.01M15 13h.01M9 16.5h6"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/>'
  };

  // Pending-approvals count on the rail. Polls quietly; any failure just hides it.
  var badge = (function () {
    var el = null, timer = null, count = 0;
    function show(n) {
      count = n;
      if (!el) return;
      el.hidden = !(n > 0);
      el.textContent = n > 99 ? "99+" : String(n);
      var btn = el.parentNode;
      if (btn) btn.setAttribute("aria-label", "Approvals" + (n > 0 ? ", " + n + " waiting" : ""));
    }
    function poll() {
      clearTimeout(timer);
      fetch("/api/approvals/summary", { cache: "no-store" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) { show(d && typeof d.pending === "number" ? d.pending : 0); })
        .catch(function () { show(0); })
        .then(function () { timer = setTimeout(poll, count > 0 ? 10000 : 30000); });
    }
    function init(btn) {
      if (!btn) return;
      el = document.createElement("span");
      el.className = "rail-badge"; el.hidden = true; el.setAttribute("aria-hidden", "true");
      btn.appendChild(el);
      poll();
      document.addEventListener("visibilitychange", function () { if (!document.hidden) poll(); });
    }
    return { init: init, refresh: poll, set: show, count: function () { return count; } };
  })();

  var rail = (function () {
    var el, items, toggle, pin, closeTimer = null, openedAt = 0;
    var root = document.documentElement;

    function mode() {
      var w = window.innerWidth, h = window.innerHeight;
      root.dataset.rail = (w <= 640 || (h > w && _pages === 1)) ? "bottom" : "side";
    }
    function isPinned() { return root.classList.contains("rail-pinned"); }
    function applyPin(on) {
      root.classList.toggle("rail-pinned", !!on);
      if (pin) pin.setAttribute("aria-pressed", on ? "true" : "false");
    }
    function setPinned(on) { _set(PIN_KEY, on ? "1" : "0"); applyPin(on); }
    function defaultPin() { var s = _get(PIN_KEY); applyPin(s === null ? _pages >= 2 : s === "1"); }
    function open() {
      clearTimeout(closeTimer);
      if (!el.classList.contains("open")) openedAt = Date.now();
      el.classList.add("open");
      toggle.setAttribute("aria-expanded", "true");
    }
    function close() {
      clearTimeout(closeTimer);
      el.classList.remove("open");
      toggle.setAttribute("aria-expanded", "false");
    }
    function closeSoon() { clearTimeout(closeTimer); closeTimer = setTimeout(close, 2000); }

    function add(item) {
      var b = document.createElement("button");
      b.type = "button"; b.id = "rail-item-" + item.id; b.className = "rail-item";
      b.setAttribute("aria-label", item.label);
      b.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true">' + (ICONS[item.icon] || "") + '</svg>'
                  + '<span class="rail-label"></span>';
      b.querySelector(".rail-label").textContent = item.label;
      b.addEventListener("click", function () { item.onClick && item.onClick(); if (!isPinned()) close(); });
      items.appendChild(b);
      return b;
    }

    function init() {
      el = document.getElementById("rail");
      if (!el) return;
      items = document.getElementById("rail-items");
      toggle = document.getElementById("rail-toggle");
      pin = document.getElementById("rail-pin");
      mode(); defaultPin();

      el.addEventListener("mouseenter", open);
      el.addEventListener("mouseleave", closeSoon);
      el.addEventListener("focusin", open);
      el.addEventListener("focusout", function (e) { if (!el.contains(e.relatedTarget)) closeSoon(); });
      // A tap fires mouseenter/focusin before click; that same gesture must not close it again.
      toggle.addEventListener("click", function () {
        if (el.classList.contains("open") && Date.now() - openedAt > 400) close(); else open();
      });
      pin.addEventListener("click", function () { setPinned(!isPinned()); });
      document.addEventListener("pointerdown", function (e) {
        if (el.classList.contains("open") && !el.contains(e.target)) close();
      });

      var here = location.pathname.indexOf("/research") === 0 ? "research"
               : location.pathname.indexOf("/jobs") === 0 ? "jobs"
               : location.pathname.indexOf("/approvals") === 0 ? "approvals"
               : location.pathname.indexOf("/spend") === 0 ? "spend"
               : location.pathname.indexOf("/agents") === 0 ? "agents" : "monitor";
      [{ id: "monitor", label: "Monitor", icon: "monitor", href: "/studio" },
       { id: "research", label: "Research", icon: "research", href: "/research" },
       { id: "jobs", label: "Jobs", icon: "jobs", href: "/jobs" },
       { id: "approvals", label: "Approvals", icon: "approvals", href: "/approvals" },
       { id: "spend", label: "Spend", icon: "spend", href: "/spend" },
       { id: "agents", label: "Agents", icon: "agents", href: "/agents" }].forEach(function (p) {
        var btn = add({ id: p.id, label: p.label, icon: p.icon, onClick: function () {
          if (p.id !== here) location.href = p.href;
        } });
        if (p.id === here) btn.setAttribute("aria-current", "page");
      });
      badge.init(document.getElementById("rail-item-approvals"));
      // Settings live in the Monitor page's modal; other pages leave it out.
      if (typeof LlamaWatch !== "undefined" && LlamaWatch.settings) {
        add({ id: "settings", label: "Settings", icon: "settings", onClick: function () { LlamaWatch.settings.open(); } });
      }

      _subs.push(function () { mode(); if (_get(PIN_KEY) === null) defaultPin(); });
      window.addEventListener("resize", mode);
    }

    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
    else init();
    return { add: add, isPinned: isPinned, setPinned: setPinned, open: open, close: close };
  })();

  window.LWLayout = {
    pagesFor: pagesFor,
    pages: function () { return _pages; },
    onChange: function (fn) { _subs.push(fn); },
    rail: rail,
    badge: badge,
    store: store
  };
})();
