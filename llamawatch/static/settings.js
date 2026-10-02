/* llamawatch — Settings Modal
 * Full-screen settings with tabs: Widgets, Backends, Services, General.
 * Dynamic form rendering from widget config schemas.
 * Apple dark mode styling.
 */
(function() {
  "use strict";

  const Settings = {
    _modal: null,
    _config: null,
    _manifest: null,
    _activeTab: "studio",
    _configChanged: false,

    // ── Public API ──────────────────────────────────────────────────

    async open() {
      if (this._modal) this.close(false);
      try {
        const [configRes, widgetsRes] = await Promise.all([
          fetch("/api/settings"),
          fetch("/api/widgets"),
        ]);
        this._config = await configRes.json();
        this._manifest = await widgetsRes.json();
      } catch (e) {
        LlamaWatch.toast("Failed to load settings", "error");
        return;
      }
      this._configChanged = false;
      // First run (no backend yet): land on Backends — the essential first step.
      const noBackends = !(this._config.backends && this._config.backends.length);
      if (noBackends) this._activeTab = "backends";
      this._createModal();
      this._renderTab();
    },

    close(refresh) {
      if (!this._modal) return;
      this._modal.remove();
      this._modal = null;
      window.unlockBodyScroll();
      if (this._escHandler) {
        document.removeEventListener("keydown", this._escHandler);
        this._escHandler = null;
      }
      if (refresh !== false && this._configChanged) {
        // Reload the page to apply settings changes
        window.location.reload();
      }
    },

    // ── Modal Shell ─────────────────────────────────────────────────

    _createModal() {
      const overlay = document.createElement("div");
      overlay.className = "lw-settings-overlay";

      const backdrop = document.createElement("div");
      backdrop.className = "lw-settings-backdrop";
      backdrop.addEventListener("click", () => this.close());
      overlay.appendChild(backdrop);

      const panel = document.createElement("div");
      panel.className = "lw-settings-panel";

      // Header
      const header = document.createElement("div");
      header.className = "lw-settings-header";
      header.innerHTML = `
        <span class="lw-settings-title">Settings</span>
        <button class="lw-settings-close">&times;</button>
      `;
      header.querySelector(".lw-settings-close").addEventListener("click", () => this.close());
      panel.appendChild(header);

      // Tab bar
      const tabs = document.createElement("div");
      tabs.className = "lw-settings-tabs";
      const tabDefs = [
        { id: "studio",   label: "Studio" },
        { id: "fleet",    label: "Fleet" },
        { id: "backends", label: "Backends" },
        { id: "services", label: "Services" },
        { id: "general",  label: "General" },
      ];
      for (const t of tabDefs) {
        const btn = document.createElement("button");
        btn.className = "lw-settings-tab" + (t.id === this._activeTab ? " active" : "");
        btn.textContent = t.label;
        btn.dataset.tab = t.id;
        btn.addEventListener("click", () => {
          this._activeTab = t.id;
          tabs.querySelectorAll(".lw-settings-tab").forEach(b => b.classList.toggle("active", b.dataset.tab === t.id));
          this._renderTab();
        });
        tabs.appendChild(btn);
      }
      panel.appendChild(tabs);

      // First-run "start here" hint — shown until a backend exists
      const noBackends = !(this._config.backends && this._config.backends.length);
      if (noBackends) {
        const hint = document.createElement("div");
        hint.className = "lw-firstrun-hint";
        hint.innerHTML = `
          <div class="lw-firstrun-title">👋 Welcome — let's get set up</div>
          <ol class="lw-firstrun-steps">
            <li><strong>Backends</strong> — connect your LLM server (llama.cpp / Ollama / OpenAI-compatible). Start here.</li>
            <li><strong>Fleet</strong> — add the machine(s) you want to monitor.</li>
            <li><strong>General</strong> — name your dashboard and set a password if it's reachable on your network.</li>
          </ol>
          <div class="lw-firstrun-foot">Everything else is optional — explore the tabs and toggle what you want.</div>
        `;
        panel.appendChild(hint);
      }

      // Content area
      const content = document.createElement("div");
      content.className = "lw-settings-content";
      content.id = "lwSettingsContent";
      panel.appendChild(content);

      overlay.appendChild(panel);
      document.body.appendChild(overlay);
      this._modal = overlay;
      window.lockBodyScroll();

      // Escape to close
      this._escHandler = (e) => { if (e.key === "Escape") this.close(); };
      document.addEventListener("keydown", this._escHandler);
    },

    _renderTab() {
      const content = document.getElementById("lwSettingsContent");
      if (!content) return;
      content.innerHTML = "";
      switch (this._activeTab) {
        case "studio":   this._renderStudioTab(content);   break;
        case "fleet":    this._renderFleetTab(content);    break;
        case "backends": this._renderBackendsTab(content); break;
        case "services": this._renderServicesTab(content); break;
        case "general":  this._renderGeneralTab(content);  break;
      }
    },

    // ── Fleet Tab ────────────────────────────────────────────────────

    _renderFleetTab(container) {
      const hosts = (this._config.fleet && Array.isArray(this._config.fleet.hosts))
        ? this._config.fleet.hosts.map(h => ({ ...h }))
        : [];

      const section = document.createElement("div");
      section.className = "lw-add-form";
      section.innerHTML = '<div class="lw-add-form-title">Machines</div>';

      const note = document.createElement("div");
      note.className = "lw-settings-note";
      note.style.marginBottom = "10px";
      note.innerHTML = "Machines shown across the dashboard. Mark one as <strong>This machine</strong> (read locally, no SSH). " +
        "Remote machines are polled over SSH — the dashboard host needs key-based SSH access to each.";
      section.appendChild(note);

      const list = document.createElement("div");
      section.appendChild(list);

      const renderList = () => {
        list.innerHTML = "";
        hosts.forEach((h, idx) => {
          const card = document.createElement("div");
          card.className = "lw-qa-card";
          card.innerHTML = `
            <div class="lw-qa-card-top">
              <div class="lw-form-group" style="flex:1;margin:0">
                <label class="lw-form-label">Name</label>
                <input class="lw-form-input fl-name" value="${this._esc(h.name || "")}" placeholder="e.g. Server-1">
              </div>
              <div class="lw-form-group" style="width:54px;flex-shrink:0;margin:0">
                <label class="lw-form-label">Colour</label>
                <input type="color" class="lw-form-input fl-color" value="${this._esc(h.color || "#2dd4bf")}" style="padding:2px;height:32px">
              </div>
              <button class="lw-btn-sm lw-btn-danger fl-remove" style="margin-top:18px;flex-shrink:0" title="Remove">×</button>
            </div>
            <div class="lw-form-group" style="margin:6px 0 0">
              <label class="lw-toggle-inline"><input type="checkbox" class="fl-local" ${h.local ? "checked" : ""}><span>This machine (read locally, no SSH)</span></label>
            </div>
            <div class="lw-fl-remote" style="display:${h.local ? "none" : "block"}">
              <div class="lw-qa-card-top" style="margin-top:6px">
                <div class="lw-form-group" style="flex:1;margin:0">
                  <label class="lw-form-label">Host / IP</label>
                  <input class="lw-form-input fl-host" value="${this._esc(h.host || "")}" placeholder="hostname or IP">
                </div>
                <div class="lw-form-group" style="flex:1;margin:0">
                  <label class="lw-form-label">SSH user</label>
                  <input class="lw-form-input fl-user" value="${this._esc(h.user || "")}" placeholder="username">
                </div>
              </div>
            </div>
            <div class="lw-qa-card-top" style="margin-top:6px">
              <div class="lw-form-group" style="flex:1;margin:0">
                <label class="lw-form-label">Idle watts <span class="lw-form-note">(optional)</span></label>
                <input class="lw-form-input fl-idle" type="number" value="${h.idle_watts != null ? h.idle_watts : ""}" placeholder="e.g. 12">
              </div>
              <div class="lw-form-group" style="flex:1;margin:0">
                <label class="lw-form-label">Max watts <span class="lw-form-note">(optional)</span></label>
                <input class="lw-form-input fl-tdp" type="number" value="${h.tdp_watts != null ? h.tdp_watts : ""}" placeholder="e.g. 45">
              </div>
            </div>
          `;
          card.querySelector(".fl-remove").addEventListener("click", async () => {
            hosts.splice(idx, 1);
            renderList();
            await this._saveFleet(hosts);
          });
          const localCb = card.querySelector(".fl-local");
          localCb.addEventListener("change", () => {
            // Only one machine can be "this machine" — untick the others.
            if (localCb.checked) {
              hosts.forEach((h, i) => { if (i !== idx) h.local = false; });
            }
            card.querySelector(".lw-fl-remote").style.display = localCb.checked ? "none" : "block";
          });
          const save = async () => {
            const idle = card.querySelector(".fl-idle").value.trim();
            const tdp  = card.querySelector(".fl-tdp").value.trim();
            const entry = {
              name:  card.querySelector(".fl-name").value.trim(),
              local: localCb.checked,
              color: card.querySelector(".fl-color").value,
            };
            // Always preserve host/user (fields stay in the DOM even when the
            // local toggle hides them) so toggling "this machine" never loses
            // a machine's address.
            const host = card.querySelector(".fl-host").value.trim();
            const user = card.querySelector(".fl-user").value.trim();
            if (host) entry.host = host;
            if (user) entry.user = user;
            if (idle !== "") entry.idle_watts = parseInt(idle, 10);
            if (tdp  !== "") entry.tdp_watts  = parseInt(tdp, 10);
            // Enforce single local across the set before saving
            if (entry.local) hosts.forEach((h, i) => { if (i !== idx) h.local = false; });
            hosts[idx] = entry;
            await this._saveFleet(hosts);
            // Re-render if local-exclusivity changed other rows
            if (entry.local && hosts.length > 1) renderList();
          };
          card.querySelectorAll("input").forEach(inp => inp.addEventListener("change", save));
          list.appendChild(card);
        });
      };

      renderList();

      const addBtn = document.createElement("button");
      addBtn.className = "lw-btn-sm lw-btn-add";
      addBtn.style.marginTop = "8px";
      addBtn.textContent = "+ Add machine";
      addBtn.addEventListener("click", () => {
        const isFirst = hosts.length === 0;
        hosts.push({ name: "", local: isFirst, color: "#2dd4bf", user: "" });
        renderList();
      });
      section.appendChild(addBtn);

      container.appendChild(section);
      container.appendChild(this._makeSeparator());

      // ── Agents editor ────────────────────────────────────────────────
      this._renderAgentsEditor(container);
    },

    async _saveFleet(hosts) {
      // Drop entries with no name
      const clean = hosts.filter(h => (h.name || "").trim());
      await this._save({ fleet: { hosts: clean } });
      if (window.StudioApplyFleet) window.StudioApplyFleet(clean);
    },

    async _saveModelNames(pairs) {
      const map = {};
      for (const p of pairs) {
        if ((p.id || "").trim()) map[p.id.trim()] = (p.name || "").trim() || p.id.trim();
      }
      await this._save({ model_names: map });
    },

    _renderAgentsEditor(container) {
      const agents = Array.isArray(this._config.agents)
        ? this._config.agents.map(a => ({ ...a }))
        : [];

      const section = document.createElement("div");
      section.className = "lw-add-form";
      section.innerHTML = '<div class="lw-add-form-title">Agents</div>';

      const note = document.createElement("div");
      note.className = "lw-settings-note";
      note.style.marginBottom = "10px";
      note.innerHTML = "Background services to monitor in the Command screen's Agents panel. " +
        "Each maps to one or more Docker containers and shows up/down status with restart controls.";
      section.appendChild(note);

      const list = document.createElement("div");
      section.appendChild(list);

      const machineNames = (this._config.fleet && this._config.fleet.hosts || [])
        .map(h => h.name).filter(Boolean);

      const renderList = () => {
        list.innerHTML = "";
        agents.forEach((a, idx) => {
          const card = document.createElement("div");
          card.className = "lw-qa-card";
          const containersStr = Array.isArray(a.containers) ? a.containers.join(", ") : (a.containers || "");
          const machineOpts = ['<option value="">— machine —</option>']
            .concat(machineNames.map(n => `<option value="${this._esc(n)}" ${a.machine === n ? "selected" : ""}>${this._esc(n)}</option>`))
            .join("");
          card.innerHTML = `
            <div class="lw-qa-card-top">
              <div class="lw-form-group" style="flex:1;margin:0">
                <label class="lw-form-label">Display name</label>
                <input class="lw-form-input ag-name" value="${this._esc(a.name || "")}" placeholder="e.g. Web Scraper">
              </div>
              <div class="lw-form-group" style="width:110px;flex-shrink:0;margin:0">
                <label class="lw-form-label">Machine</label>
                <select class="lw-form-select ag-machine">${machineOpts}</select>
              </div>
              <button class="lw-btn-sm lw-btn-danger ag-remove" style="margin-top:18px;flex-shrink:0" title="Remove">×</button>
            </div>
            <div class="lw-form-group" style="margin:6px 0 0">
              <label class="lw-form-label">Container name(s) <span class="lw-form-note">(comma-separated)</span></label>
              <input class="lw-form-input ag-containers" value="${this._esc(containersStr)}" placeholder="e.g. myapp, myapp-db">
            </div>
            <div class="lw-form-group" style="margin:6px 0 0">
              <label class="lw-form-label">systemd unit <span class="lw-form-note">(optional — restarts alongside container)</span></label>
              <input class="lw-form-input ag-unit" value="${this._esc(a.service_unit || "")}" placeholder="e.g. myapp.service">
            </div>
          `;
          card.querySelector(".ag-remove").addEventListener("click", async () => {
            agents.splice(idx, 1);
            renderList();
            await this._saveAgents(agents);
          });
          const save = async () => {
            const name = card.querySelector(".ag-name").value.trim();
            const containers = card.querySelector(".ag-containers").value
              .split(",").map(s => s.trim()).filter(Boolean);
            const entry = {
              id: a.id || name.toLowerCase().replace(/[^a-z0-9]+/g, "-"),
              name,
              containers,
              machine: card.querySelector(".ag-machine").value,
              primary: containers[0] || "",
            };
            const unit = card.querySelector(".ag-unit").value.trim();
            if (unit) entry.service_unit = unit;
            agents[idx] = entry;
            await this._saveAgents(agents);
          };
          card.querySelectorAll("input, select").forEach(el => el.addEventListener("change", save));
          list.appendChild(card);
        });
      };

      renderList();

      const addBtn = document.createElement("button");
      addBtn.className = "lw-btn-sm lw-btn-add";
      addBtn.style.marginTop = "8px";
      addBtn.textContent = "+ Add agent";
      addBtn.addEventListener("click", () => {
        agents.push({ id: "", name: "", containers: [], machine: "" });
        renderList();
      });
      section.appendChild(addBtn);

      container.appendChild(section);
    },

    async _saveAgents(agents) {
      const clean = agents.filter(a => (a.name || "").trim() && (a.containers || []).length);
      await this._save({ agents: clean });
      if (window.StudioApplyAgents) window.StudioApplyAgents(clean);
    },

    // ── Studio Tab ───────────────────────────────────────────────────

    _renderStudioTab(container) {
      const p = this._config.studio_panels || {};
      const views = p.views || ["command", "system", "knowledge"];

      const groups = [
        {
          title: "Views",
          note: "Which of the three screens appear in the carousel. Swipe or use the arrow keys to navigate between them.",
          items: [
            { key: "views:command",   label: "Command",   desc: "Primary screen — live inference engine status, slot occupancy, system gauges, terminal, and background service health." },
            { key: "views:system",    label: "System",    desc: "Hardware monitoring — per-machine CPU, RAM, temperature and disk cards, Docker containers, and top CPU processes." },
            { key: "views:knowledge", label: "Knowledge", desc: "Intelligence and data — article/news feed, a predictions feed, your RAG library, markdown docs browser, and file transfers." },
          ],
        },
        {
          title: "Always Visible",
          note: "Bars that appear on every screen regardless of which view is active.",
          items: [
            { key: "stat_strip",   label: "Stat Strip",    desc: "Six KPI tiles at the top of each view: active model, parameter count, context window, backend health, free slots, and status." },
            { key: "bottom_strip", label: "Bottom Strip",  desc: "Compact vitals bar at the very bottom — CPU, RAM and temperature for each machine in your fleet at a glance." },
          ],
        },
        {
          title: "Command — Columns",
          note: "The Command screen has three columns. Hide either side column if you don't need it — the remaining columns expand to fill the space.",
          items: [
            { key: "command_left",  label: "Left column",  desc: "System gauges (CPU, RAM, temperature, disk), a compact per-machine vitals strip, and a token usage chart for the last 24 hours." },
            { key: "command_right", label: "Right column", desc: "Background service/agent status panel, GPU utilisation and VRAM bars, and per-machine RAM and KV cache fill bars." },
          ],
        },
        {
          title: "Command — Centre",
          note: "Sections within the centre column alongside the main inference engine display.",
          items: [
            { key: "slots",    label: "Slot occupancy", desc: "Coloured pips showing which inference slots are busy and which are free. Requires a llama.cpp or compatible backend." },
            { key: "terminal", label: "Terminal",       desc: "In-browser terminal tab strip and quick-action buttons. Requires the server to be running on a machine you can SSH into." },
          ],
        },
        {
          title: "System",
          note: "Panels on the System screen. Each panel can be hidden independently — useful if you only have one machine or don't run Docker.",
          items: [
            { key: "fleet_machines", label: "Fleet machines", desc: "Hardware cards for each machine — radial CPU gauge, RAM donut, and bars for temperature, disk usage and load average. Populates automatically for any host configured as a backend or service." },
            { key: "docker",         label: "Docker",         desc: "Lists running Docker containers with their state, uptime and start/stop/restart action buttons. Requires Docker on the host." },
            { key: "processes",      label: "Top processes",  desc: "CPU usage breakdown per machine shown as proportional donut charts. Requires SSH or agent access to each monitored host." },
          ],
        },
        {
          title: "Knowledge",
          note: "Sections within the Knowledge screen. Hide any section you don't have a data source for — empty sections just waste space.",
          items: [
            { key: "press_room",  label: "News / Articles", desc: "A scrollable feed of intelligence articles or news items. Requires a news collector or RSS-to-articles pipeline writing to your backend database." },
            { key: "predictions", label: "Predictions",     desc: "A timeline scatter chart of forecasts with confidence scores. Requires a predictions service that writes structured prediction records to your backend." },
            { key: "library",     label: "Library",         desc: "Search your RAG vector knowledge base, browse collections, and view document chunks. Requires ChromaDB or a compatible vector store configured in Backends." },
            { key: "docs",        label: "Docs",            desc: "Browse and copy markdown files from any path on your server or connected machines. Configure the scan paths in the server settings." },
            { key: "files",       label: "Files",           desc: "Upload files to the server inbox and download files that the server has staged for you. Works out of the box — no extra setup required." },
          ],
        },
        {
          title: "Voice",
          note: null,
          items: [
            { key: "voice", label: "Voice command", desc: "Mic button — speak to send commands to the terminal" },
          ],
          info: true,
        },
      ];

      // Render toggle groups


      for (const group of groups) {
        const section = document.createElement("div");
        section.className = "lw-add-form";

        const title = document.createElement("div");
        title.className = "lw-add-form-title";
        title.textContent = group.title;
        section.appendChild(title);

        if (group.note) {
          const note = document.createElement("div");
          note.className = "lw-settings-note";
          note.style.marginBottom = "8px";
          note.textContent = group.note;
          section.appendChild(note);
        }

        for (const item of group.items) {
          let checked;
          if (item.key.startsWith("views:")) {
            checked = views.includes(item.key.replace("views:", ""));
          } else {
            checked = p[item.key] !== false;
          }

          const row = document.createElement("div");
          row.className = "lw-widget-row";
          row.innerHTML = `
            <div class="lw-widget-info">
              <div class="lw-widget-name">${this._esc(item.label)}</div>
              <div class="lw-widget-desc">${this._esc(item.desc)}</div>
            </div>
            <label class="lw-toggle">
              <input type="checkbox" ${checked ? "checked" : ""}>
              <span class="lw-toggle-slider"></span>
            </label>
          `;

          const cb = row.querySelector("input");
          cb.addEventListener("change", () => this._toggleStudioPanel(item.key, cb.checked));
          section.appendChild(row);
        }

        if (group.info) {
          const box = document.createElement("div");
          box.className = "lw-settings-info-box";
          box.innerHTML = `
            <div class="lw-info-title">Requirements</div>
            <div class="lw-info-row"><span class="lw-info-badge lw-badge-ok">Option A</span><span><strong>Chrome / Edge</strong> — built-in Web Speech API. Requires HTTPS. On Android, allow the microphone in Chrome site settings the first time.</span></div>
            <div class="lw-info-row"><span class="lw-info-badge lw-badge-soon">Option B</span><span><strong>Whisper (local)</strong> — install <code>faster-whisper</code> on your server for fully offline, private transcription. Not yet implemented — planned.</span></div>
            <div class="lw-info-row"><span class="lw-info-badge lw-badge-ok">What it does</span><span>Tap the mic in the hero orb, speak, text is injected into the active terminal. You review it and press Enter.</span></div>
          `;
          section.appendChild(box);
        }

        container.appendChild(section);
        container.appendChild(this._makeSeparator());
      }

      // ── Quick Actions editor ──────────────────────────────────────────
      this._renderQuickActionsEditor(container);
    },

    _renderQuickActionsEditor(container) {
      const actions = [...(this._config.quick_actions || [])];

      const section = document.createElement("div");
      section.className = "lw-add-form";
      section.innerHTML = '<div class="lw-add-form-title">Quick Actions</div>';

      const note = document.createElement("div");
      note.className = "lw-settings-note";
      note.style.marginBottom = "10px";
      note.textContent = "Buttons shown below the terminal. Each runs a shell command on the server when tapped. Up to 6.";
      section.appendChild(note);

      const list = document.createElement("div");
      list.id = "lw-qa-list";
      section.appendChild(list);

      const renderList = () => {
        list.innerHTML = "";
        actions.forEach((a, idx) => {
          const card = document.createElement("div");
          card.className = "lw-qa-card";
          card.innerHTML = `
            <div class="lw-qa-card-top">
              <div class="lw-form-group" style="width:56px;flex-shrink:0;margin:0">
                <label class="lw-form-label">Icon</label>
                <input class="lw-form-input lw-qa-icon" value="${this._esc(a.icon || "")}" placeholder="↺" maxlength="4" style="text-align:center">
              </div>
              <div class="lw-form-group" style="flex:1;margin:0">
                <label class="lw-form-label">Button label</label>
                <input class="lw-form-input lw-qa-label" value="${this._esc(a.label || "")}" placeholder="e.g. Restart Nginx">
              </div>
              <button class="lw-btn-sm lw-btn-danger lw-qa-remove" style="margin-top:18px;flex-shrink:0" title="Remove">×</button>
            </div>
            <div class="lw-form-group" style="margin:6px 0 0">
              <label class="lw-form-label">Shell command <span class="lw-form-note">(runs on the server when tapped)</span></label>
              <input class="lw-form-input lw-qa-shell" value="${this._esc(a.shell || "")}" placeholder="e.g. sudo systemctl restart nginx">
            </div>
          `;
          card.querySelector(".lw-qa-remove").addEventListener("click", async () => {
            actions.splice(idx, 1);
            renderList();
            await this._saveQuickActions(actions);
          });
          const save = async () => {
            a.icon  = card.querySelector(".lw-qa-icon").value.trim();
            a.label = card.querySelector(".lw-qa-label").value.trim();
            a.shell = card.querySelector(".lw-qa-shell").value.trim();
            await this._saveQuickActions(actions);
          };
          card.querySelectorAll("input").forEach(inp => inp.addEventListener("change", save));
          list.appendChild(card);
        });
      };

      renderList();

      if (actions.length < 6) {
        const addBtn = document.createElement("button");
        addBtn.className = "lw-btn-sm lw-btn-add";
        addBtn.style.marginTop = "8px";
        addBtn.textContent = "+ Add action";
        addBtn.addEventListener("click", () => {
          if (actions.length >= 6) return;
          actions.push({ id: "qa-" + this._randomId(), icon: "", label: "", shell: "" });
          renderList();
        });
        section.appendChild(addBtn);
      }

      container.appendChild(section);
    },

    async _saveQuickActions(actions) {
      await this._save({ quick_actions: actions });
      localStorage.setItem("studio-quick-actions", JSON.stringify(actions));
      if (window.StudioRebuildQuickActions) window.StudioRebuildQuickActions(actions);
    },

    async _toggleStudioPanel(key, enabled) {
      const panels = { ...(this._config.studio_panels || {}) };

      if (key.startsWith("views:")) {
        const viewName = key.replace("views:", "");
        let views = [...(panels.views || ["command", "system", "knowledge"])];
        if (enabled && !views.includes(viewName)) views.push(viewName);
        else if (!enabled) views = views.filter(v => v !== viewName);
        if (views.length === 0) { LlamaWatch.toast("At least one view must be enabled", "error"); return; }
        panels.views = views;
      } else {
        panels[key] = enabled;
      }

      await this._save({ studio_panels: panels });

      // Apply to Studio DOM immediately without page reload
      if (window.StudioApplyPanels) window.StudioApplyPanels(panels);
    },

    // ── Backends Tab ────────────────────────────────────────────────

    _renderBackendsTab(container) {
      const backends = this._config.backends || [];

      // Existing backends
      if (backends.length === 0) {
        container.innerHTML = '<div class="lw-settings-empty">No backends configured</div>';
      } else {
        for (let i = 0; i < backends.length; i++) {
          const b = backends[i];
          const row = document.createElement("div");
          row.className = "lw-backend-row";
          row.innerHTML = `
            <span class="lw-health-dot lw-dot-unknown"></span>
            <div class="lw-backend-info">
              <div class="lw-backend-name">${this._esc(b.name || "Backend " + (i + 1))}</div>
              <div class="lw-backend-detail">
                <span class="lw-type-badge">${this._esc(b.type || "llamacpp")}</span>
                ${this._esc(b.url || "")}
              </div>
            </div>
            <div class="lw-backend-actions">
              <button class="lw-btn-sm" data-action="test" data-idx="${i}">Test</button>
              <button class="lw-btn-sm" data-action="edit" data-idx="${i}">Edit</button>
              <button class="lw-btn-sm lw-btn-danger" data-action="remove" data-idx="${i}">Remove</button>
            </div>
          `;

          row.querySelector('[data-action="test"]').addEventListener("click", () => this._testBackend(b, row.querySelector(".lw-health-dot")));
          row.querySelector('[data-action="edit"]').addEventListener("click", () => this._editBackend(i, container));
          row.querySelector('[data-action="remove"]').addEventListener("click", () => this._removeBackend(i));

          container.appendChild(row);
        }
      }

      // Separator
      container.appendChild(this._makeSeparator());

      // Add backend form
      const addSection = document.createElement("div");
      addSection.className = "lw-add-form";
      addSection.innerHTML = `
        <div class="lw-add-form-title">Add Backend</div>
        <div class="lw-settings-note" style="margin-bottom:10px">
          A backend is an LLM server the dashboard monitors — your llama.cpp, Ollama,
          or any OpenAI-compatible endpoint. This is the core connection: model status,
          slots and inference speed all come from here. Add at least one.
        </div>
        <div class="lw-form-group">
          <label class="lw-form-label">Name</label>
          <input class="lw-form-input" id="lwBackendName" placeholder="e.g. Main Server">
        </div>
        <div class="lw-form-group">
          <label class="lw-form-label">Type</label>
          <select class="lw-form-select" id="lwBackendType">
            <option value="llamacpp">llama.cpp</option>
            <option value="ollama">Ollama</option>
            <option value="openai">OpenAI-compatible</option>
          </select>
        </div>
        <div class="lw-form-group">
          <label class="lw-form-label">URL</label>
          <input class="lw-form-input" id="lwBackendUrl" placeholder="http://localhost:8080">
        </div>
        <div class="lw-form-group">
          <label class="lw-form-label">Context window <span class="lw-form-note">(optional, tokens — powers the chat usage meter)</span></label>
          <input class="lw-form-input" id="lwBackendCtx" type="number" placeholder="e.g. 8192">
        </div>
        <div class="lw-form-group" id="lwSwapProxyGroup">
          <label class="lw-toggle-inline">
            <input type="checkbox" id="lwBackendSwapProxy">
            <span>This URL is a model-swapping proxy</span>
          </label>
          <div class="lw-form-note" style="margin-top:3px">Tick only if a proxy in front of llama.cpp loads/unloads models on demand. Leave off for a plain llama.cpp or Ollama server.</div>
        </div>
        <button class="lw-save-btn" id="lwAddBackend">Add Backend</button>
      `;

      // Show/hide swap proxy based on type
      const typeSelect = addSection.querySelector("#lwBackendType");
      const swapGroup = addSection.querySelector("#lwSwapProxyGroup");
      typeSelect.addEventListener("change", () => {
        swapGroup.style.display = typeSelect.value === "llamacpp" ? "block" : "none";
      });

      addSection.querySelector("#lwAddBackend").addEventListener("click", () => this._addBackend(addSection));
      container.appendChild(addSection);

      // Discover button
      const discoverBtn = document.createElement("button");
      discoverBtn.className = "lw-btn-sm lw-btn-discover";
      discoverBtn.textContent = "Refresh Discovery";
      discoverBtn.addEventListener("click", () => this._runDiscovery(discoverBtn));
      container.appendChild(discoverBtn);

      // Model names section — row editor (model ID → friendly name)
      container.appendChild(this._makeSeparator());
      const modelSection = document.createElement("div");
      modelSection.className = "lw-add-form";
      modelSection.innerHTML = '<div class="lw-add-form-title">Model Display Names</div>';
      const mnNote = document.createElement("div");
      mnNote.className = "lw-settings-note";
      mnNote.style.marginBottom = "10px";
      mnNote.textContent = "Optional. Show a friendly name instead of the raw model ID. " +
        "Left = the model ID your backend reports; right = what you want shown on the dashboard.";
      modelSection.appendChild(mnNote);

      const mnPairs = Object.entries(this._config.model_names || {}).map(([id, name]) => ({ id, name }));
      const mnList = document.createElement("div");
      modelSection.appendChild(mnList);

      const renderMn = () => {
        mnList.innerHTML = "";
        mnPairs.forEach((p, idx) => {
          const row = document.createElement("div");
          row.className = "lw-qa-row";
          row.innerHTML = `
            <input class="lw-form-input mn-id" value="${this._esc(p.id)}" placeholder="model-id-from-backend" style="flex:1">
            <span style="color:var(--lw-text-3,rgba(255,255,255,.35));flex-shrink:0">→</span>
            <input class="lw-form-input mn-name" value="${this._esc(p.name)}" placeholder="Display name" style="flex:1">
            <button class="lw-btn-sm lw-btn-danger mn-remove" title="Remove">×</button>`;
          row.querySelector(".mn-remove").addEventListener("click", async () => {
            mnPairs.splice(idx, 1); renderMn(); await this._saveModelNames(mnPairs);
          });
          const save = async () => {
            p.id   = row.querySelector(".mn-id").value.trim();
            p.name = row.querySelector(".mn-name").value.trim();
            await this._saveModelNames(mnPairs);
          };
          row.querySelectorAll("input").forEach(i => i.addEventListener("change", save));
          mnList.appendChild(row);
        });
      };
      renderMn();

      const mnAdd = document.createElement("button");
      mnAdd.className = "lw-btn-sm lw-btn-add";
      mnAdd.style.marginTop = "8px";
      mnAdd.textContent = "+ Add name";
      mnAdd.addEventListener("click", () => { mnPairs.push({ id: "", name: "" }); renderMn(); });
      modelSection.appendChild(mnAdd);
      container.appendChild(modelSection);

      // Chat system prompt
      container.appendChild(this._makeSeparator());
      const chatSection = document.createElement("div");
      chatSection.className = "lw-add-form";
      chatSection.innerHTML = '<div class="lw-add-form-title">Chat System Prompt</div>';
      const chatNote = document.createElement("div");
      chatNote.className = "lw-settings-note";
      chatNote.style.marginBottom = "8px";
      chatNote.textContent = "Optional instructions prepended to every conversation in the Chat widget — sets the assistant's tone and behaviour. Leave blank for a plain assistant.";
      chatSection.appendChild(chatNote);

      const chatPrompt = document.createElement("textarea");
      chatPrompt.className = "lw-form-textarea";
      chatPrompt.placeholder = "e.g. You are a concise, helpful assistant.";
      chatPrompt.value = this._config.chat_system_prompt || "";
      chatPrompt.rows = 4;
      chatSection.appendChild(chatPrompt);

      const saveChatPrompt = document.createElement("button");
      saveChatPrompt.className = "lw-save-btn";
      saveChatPrompt.textContent = "Save System Prompt";
      saveChatPrompt.addEventListener("click", async () => {
        await this._save({ chat_system_prompt: chatPrompt.value });
      });
      chatSection.appendChild(saveChatPrompt);
      container.appendChild(chatSection);
    },

    async _testBackend(backend, dot) {
      dot.className = "lw-health-dot lw-dot-testing";
      try {
        const res = await fetch("/api/settings/test-backend", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url: backend.url, type: backend.type }),
        });
        const data = await res.json();
        dot.className = "lw-health-dot " + (data.status === "ok" ? "lw-dot-green" : "lw-dot-red");
        if (data.status !== "ok") LlamaWatch.toast(data.error || "Connection failed", "error");
      } catch (e) {
        dot.className = "lw-health-dot lw-dot-red";
        LlamaWatch.toast("Test failed: " + e.message, "error");
      }
    },

    _editBackend(idx, container) {
      const backends = [...(this._config.backends || [])];
      const b = backends[idx];
      if (!b) return;

      // Create inline edit form
      const rows = container.querySelectorAll(".lw-backend-row");
      const row = rows[idx];
      if (!row) return;

      const editForm = document.createElement("div");
      editForm.className = "lw-backend-edit";
      editForm.innerHTML = `
        <div class="lw-form-group">
          <label class="lw-form-label">Name</label>
          <input class="lw-form-input" value="${this._esc(b.name || "")}" data-field="name">
        </div>
        <div class="lw-form-group">
          <label class="lw-form-label">URL</label>
          <input class="lw-form-input" value="${this._esc(b.url || "")}" data-field="url">
        </div>
        <div class="lw-form-group">
          <label class="lw-form-label">Type</label>
          <select class="lw-form-select" data-field="type">
            <option value="llamacpp" ${b.type === "llamacpp" ? "selected" : ""}>llama.cpp</option>
            <option value="ollama" ${b.type === "ollama" ? "selected" : ""}>Ollama</option>
            <option value="openai" ${b.type === "openai" ? "selected" : ""}>OpenAI-compatible</option>
          </select>
        </div>
        <div class="lw-form-group">
          <label class="lw-form-label">Context window <span class="lw-form-note">(optional, tokens)</span></label>
          <input class="lw-form-input" type="number" value="${b.context_window != null ? b.context_window : ""}" data-field="context_window">
        </div>
        <div class="lw-backend-edit-actions">
          <button class="lw-save-btn" data-action="save">Save</button>
          <button class="lw-btn-sm" data-action="cancel">Cancel</button>
        </div>
      `;

      editForm.querySelector('[data-action="save"]').addEventListener("click", async () => {
        const ctxv = editForm.querySelector('[data-field="context_window"]').value.trim();
        const updated = {
          ...b,
          name: editForm.querySelector('[data-field="name"]').value,
          url: editForm.querySelector('[data-field="url"]').value,
          type: editForm.querySelector('[data-field="type"]').value,
        };
        if (ctxv) updated.context_window = parseInt(ctxv, 10);
        else delete updated.context_window;
        backends[idx] = updated;
        await this._save({ backends });
        this._renderTab();
      });
      editForm.querySelector('[data-action="cancel"]').addEventListener("click", () => {
        editForm.remove();
        row.style.display = "flex";
      });

      row.style.display = "none";
      row.parentNode.insertBefore(editForm, row.nextSibling);
    },

    async _removeBackend(idx) {
      const backends = [...(this._config.backends || [])];
      backends.splice(idx, 1);
      await this._save({ backends });
      this._renderTab();
    },

    async _addBackend(form) {
      const name = form.querySelector("#lwBackendName").value.trim();
      const type = form.querySelector("#lwBackendType").value;
      const url = form.querySelector("#lwBackendUrl").value.trim();

      if (!url) {
        LlamaWatch.toast("URL is required", "error");
        return;
      }

      const backend = { name: name || type, type, url };
      if (type === "llamacpp" && form.querySelector("#lwBackendSwapProxy").checked) {
        backend.swap_proxy = true;
      }
      const ctx = form.querySelector("#lwBackendCtx").value.trim();
      if (ctx) backend.context_window = parseInt(ctx, 10);

      const backends = [...(this._config.backends || []), backend];
      await this._save({ backends });
      this._renderTab();
    },

    async _runDiscovery(btn) {
      btn.disabled = true;
      btn.textContent = "Scanning...";
      try {
        const res = await fetch("/api/settings/discover", { method: "POST" });
        const data = await res.json();
        const found = (data.backends?.length || 0) + (data.services?.length || 0);
        LlamaWatch.toast(`Discovery found ${found} items`, "success");
        // Refresh config
        const configRes = await fetch("/api/settings");
        this._config = await configRes.json();
        this._renderTab();
      } catch (e) {
        LlamaWatch.toast("Discovery failed", "error");
      } finally {
        btn.disabled = false;
        btn.textContent = "Refresh Discovery";
      }
    },

    // ── Services Tab ────────────────────────────────────────────────

    _renderServicesTab(container) {
      const services = this._config.services || [];

      const intro = document.createElement("div");
      intro.className = "lw-settings-note";
      intro.style.marginBottom = "12px";
      intro.innerHTML = "Background services to show health for in the Services panel — systemd units, " +
        "Docker containers, or processes. <strong>Not the same as Backends</strong> (LLM endpoints) or " +
        "<strong>Fleet</strong> (machines): this is for any supporting service you want an up/down indicator on.";
      container.appendChild(intro);

      if (services.length === 0) {
        const empty = document.createElement("div");
        empty.className = "lw-settings-empty";
        empty.textContent = "No services configured yet — add one below.";
        container.appendChild(empty);
      } else {
        for (let i = 0; i < services.length; i++) {
          const s = services[i];
          const row = document.createElement("div");
          row.className = "lw-service-row";
          row.innerHTML = `
            <div class="lw-service-info">
              <div class="lw-service-name">${this._esc(s.name || s.unit || "Service " + (i + 1))}</div>
              <div class="lw-service-detail">
                <span class="lw-type-badge">${this._esc(s.type || "systemd")}</span>
                ${s.unit ? this._esc(s.unit) : ""}
                ${s.port ? " : " + s.port : ""}
              </div>
            </div>
            <div class="lw-service-actions">
              <button class="lw-btn-sm" data-action="edit" data-idx="${i}">Edit</button>
              <button class="lw-btn-sm lw-btn-danger" data-action="remove" data-idx="${i}">Remove</button>
            </div>
          `;

          row.querySelector('[data-action="edit"]').addEventListener("click", () => this._editService(i, container));
          row.querySelector('[data-action="remove"]').addEventListener("click", () => this._removeService(i));

          container.appendChild(row);
        }
      }

      // Separator
      container.appendChild(this._makeSeparator());

      // Add service form
      const addSection = document.createElement("div");
      addSection.className = "lw-add-form";
      addSection.innerHTML = `
        <div class="lw-add-form-title">Add Service</div>
        <div class="lw-form-group">
          <label class="lw-form-label">Name</label>
          <input class="lw-form-input" id="lwSvcName" placeholder="e.g. My Service">
        </div>
        <div class="lw-form-group">
          <label class="lw-form-label">Type</label>
          <select class="lw-form-select" id="lwSvcType">
            <option value="systemd">systemd</option>
            <option value="docker">Docker</option>
            <option value="process">Process</option>
          </select>
        </div>
        <div class="lw-form-group">
          <label class="lw-form-label" id="lwSvcUnitLabel">Unit name</label>
          <input class="lw-form-input" id="lwSvcUnit" placeholder="e.g. nginx.service">
        </div>
        <div class="lw-form-group">
          <label class="lw-form-label">Port (optional)</label>
          <input class="lw-form-input" id="lwSvcPort" type="number" placeholder="e.g. 8080">
        </div>
        <div class="lw-form-group">
          <label class="lw-form-label">Health URL (optional)</label>
          <input class="lw-form-input" id="lwSvcHealth" placeholder="http://localhost:8080/health">
        </div>
        <button class="lw-save-btn" id="lwAddService">Add Service</button>
      `;

      // Update unit label when type changes
      const svcTypeSelect = addSection.querySelector("#lwSvcType");
      const svcUnitLabel = addSection.querySelector("#lwSvcUnitLabel");
      svcTypeSelect.addEventListener("change", () => {
        svcUnitLabel.textContent = svcTypeSelect.value === "docker" ? "Container name" : "Unit name";
      });

      addSection.querySelector("#lwAddService").addEventListener("click", () => this._addService(addSection));
      container.appendChild(addSection);

      // Discover button
      const discoverBtn = document.createElement("button");
      discoverBtn.className = "lw-btn-sm lw-btn-discover";
      discoverBtn.textContent = "Refresh Discovery";
      discoverBtn.addEventListener("click", () => this._runDiscovery(discoverBtn));
      container.appendChild(discoverBtn);
    },

    _editService(idx, container) {
      const services = [...(this._config.services || [])];
      const s = services[idx];
      if (!s) return;

      const rows = container.querySelectorAll(".lw-service-row");
      const row = rows[idx];
      if (!row) return;

      const editForm = document.createElement("div");
      editForm.className = "lw-service-edit";
      editForm.innerHTML = `
        <div class="lw-form-group">
          <label class="lw-form-label">Name</label>
          <input class="lw-form-input" value="${this._esc(s.name || "")}" data-field="name">
        </div>
        <div class="lw-form-group">
          <label class="lw-form-label">Type</label>
          <select class="lw-form-select" data-field="type">
            <option value="systemd" ${s.type === "systemd" ? "selected" : ""}>systemd</option>
            <option value="docker" ${s.type === "docker" ? "selected" : ""}>Docker</option>
            <option value="process" ${s.type === "process" ? "selected" : ""}>Process</option>
          </select>
        </div>
        <div class="lw-form-group">
          <label class="lw-form-label">Unit / Container</label>
          <input class="lw-form-input" value="${this._esc(s.unit || s.container || "")}" data-field="unit">
        </div>
        <div class="lw-form-group">
          <label class="lw-form-label">Port</label>
          <input class="lw-form-input" type="number" value="${s.port || ""}" data-field="port">
        </div>
        <div class="lw-form-group">
          <label class="lw-form-label">Health URL</label>
          <input class="lw-form-input" value="${this._esc(s.health_url || "")}" data-field="health_url">
        </div>
        <div class="lw-backend-edit-actions">
          <button class="lw-save-btn" data-action="save">Save</button>
          <button class="lw-btn-sm" data-action="cancel">Cancel</button>
        </div>
      `;

      editForm.querySelector('[data-action="save"]').addEventListener("click", async () => {
        const port = editForm.querySelector('[data-field="port"]').value;
        const editedType = editForm.querySelector('[data-field="type"]').value;
        const editedUnit = editForm.querySelector('[data-field="unit"]').value;
        const updated = {
          ...s,
          name: editForm.querySelector('[data-field="name"]').value,
          type: editedType,
          port: port ? parseInt(port, 10) : undefined,
          health_url: editForm.querySelector('[data-field="health_url"]').value || undefined,
        };
        delete updated.unit; delete updated.container;
        if (editedUnit) {
          if (editedType === "docker") updated.container = editedUnit;
          else updated.unit = editedUnit;
        }
        services[idx] = updated;
        await this._save({ services });
        this._renderTab();
      });
      editForm.querySelector('[data-action="cancel"]').addEventListener("click", () => {
        editForm.remove();
        row.style.display = "flex";
      });

      row.style.display = "none";
      row.parentNode.insertBefore(editForm, row.nextSibling);
    },

    async _removeService(idx) {
      const services = [...(this._config.services || [])];
      services.splice(idx, 1);
      await this._save({ services });
      this._renderTab();
    },

    async _addService(form) {
      const name = form.querySelector("#lwSvcName").value.trim();
      const type = form.querySelector("#lwSvcType").value;
      const unit = form.querySelector("#lwSvcUnit").value.trim();
      const port = form.querySelector("#lwSvcPort").value;
      const health = form.querySelector("#lwSvcHealth").value.trim();

      if (!unit && !name) {
        LlamaWatch.toast("Name or unit is required", "error");
        return;
      }

      const service = { name: name || unit, type };
      if (unit) {
        if (type === "docker") service.container = unit;
        else service.unit = unit;
      }
      if (port) service.port = parseInt(port, 10);
      if (health) service.health_url = health;

      const services = [...(this._config.services || []), service];
      await this._save({ services });
      this._renderTab();
    },

    // ── General Tab ─────────────────────────────────────────────────

    _renderGeneralTab(container) {
      const version = this._config._version || "unknown";
      const authEnabled = !!this._config.auth_enabled;
      const dashName = this._config.dashboard_name || "";

      container.innerHTML = `
        <div class="lw-about">
          <div class="lw-about-title">${this._esc(dashName || "llamawatch")} <span class="lw-about-ver">llamawatch v${this._esc(version)}</span></div>
          <div class="lw-about-desc">A self-hosted ops dashboard for your local LLMs — model health, slots, GPU, your whole fleet, Docker, terminal and chat in one screen.</div>
          <div class="lw-about-links">
            <a href="https://github.com/Huzy85/llamawatch" target="_blank" rel="noopener">Documentation &amp; source ↗</a>
            <a href="https://github.com/Huzy85/llamawatch/blob/main/README.md" target="_blank" rel="noopener">README ↗</a>
          </div>
          <div class="lw-about-credit">Made by Steam Vibe · AGPL-3.0</div>
        </div>

        ${!authEnabled ? `
        <div class="lw-settings-warn">
          <span class="lw-settings-warn-icon">&#9888;</span>
          <span>Dashboard is open — anyone with the URL can access it. Set a password in the Authentication section below.</span>
        </div>` : ""}

        <div class="lw-add-form">
          <div class="lw-add-form-title">Dashboard</div>
          <div class="lw-form-group">
            <label class="lw-form-label">Name</label>
            <input class="lw-form-input" id="lwDashName" type="text" value="${this._esc(dashName)}" placeholder="llamawatch">
          </div>
          <button class="lw-save-btn" id="lwSaveDash">Save</button>
        </div>

        <div class="lw-settings-separator"></div>

        <div class="lw-add-form">
          <div class="lw-add-form-title">Authentication</div>
          <div class="lw-form-group">
            <label class="lw-toggle-inline">
              <input type="checkbox" id="lwAuthEnabled" ${authEnabled ? "checked" : ""}>
              <span>Require password</span>
            </label>
          </div>
          <div class="lw-form-group" id="lwAuthPasswordGroup" style="display:${authEnabled ? "block" : "none"}">
            <label class="lw-form-label">Password</label>
            <input class="lw-form-input" id="lwAuthPassword" type="password" placeholder="${authEnabled ? "Enter new password to change" : "Set a password"}">
          </div>
          <div class="lw-form-group" id="lwSessionExpiryGroup" style="display:${authEnabled ? "block" : "none"}">
            <label class="lw-form-label">Session expiry (days)</label>
            <input class="lw-form-input" id="lwSessionExpiry" type="number" value="${this._config.session_expiry_days || 7}">
          </div>
          <button class="lw-save-btn" id="lwSaveAuth">Save</button>
        </div>

        <div class="lw-settings-separator"></div>

        <div class="lw-add-form">
          <div class="lw-add-form-title">Server</div>
          <div class="lw-form-group">
            <label class="lw-form-label">Port <span class="lw-form-note">(restart required)</span></label>
            <input class="lw-form-input" id="lwPort" type="number" value="${this._config.port || 8400}">
          </div>
          <div class="lw-form-group">
            <label class="lw-form-label">Host <span class="lw-form-note">(restart required)</span></label>
            <input class="lw-form-input" id="lwHost" value="${this._esc(this._config.host || "0.0.0.0")}">
          </div>
          <button class="lw-save-btn" id="lwSaveServer">Save</button>
        </div>

        <div class="lw-settings-separator"></div>

        <div class="lw-add-form">
          <div class="lw-add-form-title">Web Search</div>
          <div class="lw-settings-note" style="margin-bottom:8px;line-height:1.55">
            The chat panel's <strong>Web search</strong> toggle works by querying a
            <a href="https://docs.searxng.org/admin/installation.html" target="_blank" style="color:var(--lw-blue,#22d3ee)">SearXNG</a>
            instance — a free, open-source metasearch engine you run yourself.
            We use SearXNG because it needs <strong>no API key</strong>, keeps your
            searches private, and fits llamawatch's self-hosted model.
            <br><br>
            <strong>You need to run one</strong> (it's a single Docker container —
            <code>docker run -d -p 8888:8080 searxng/searxng</code> — see the docs link),
            then paste its URL below. <strong>Leave blank</strong> to keep web search off.
            <br><span style="color:var(--lw-text-3,rgba(255,255,255,.4))">Hosted providers (e.g. Brave, Tavily) may come in a future version.</span>
          </div>
          <div class="lw-form-group">
            <label class="lw-form-label">SearXNG URL</label>
            <input class="lw-form-input" id="lwSearxngUrl" value="${this._esc(this._config.searxng_url || "")}" placeholder="http://localhost:8888">
          </div>
          <button class="lw-save-btn" id="lwSaveSearxng">Save</button>
        </div>

        <div class="lw-settings-separator"></div>

        <div class="lw-add-form">
          <div class="lw-add-form-title">Sensors</div>
          <div class="lw-settings-note" style="margin-bottom:8px">
            Whether to detect CPU/GPU temperature sensors when scanning your machine.
            Leave on Auto-detect unless temperature readings cause problems.
          </div>
          <div class="lw-form-group">
            <label class="lw-form-label">Hardware temperature sensors</label>
            <select class="lw-form-select" id="lwSensors">
              <option value="auto" ${this._config.sensors === "auto" ? "selected" : ""}>Auto-detect</option>
              <option value="none" ${this._config.sensors === "none" ? "selected" : ""}>Disabled</option>
            </select>
          </div>
          <button class="lw-save-btn" id="lwSaveSensors">Save</button>
        </div>
      `;

      // Dashboard name save
      container.querySelector("#lwSaveDash").addEventListener("click", async () => {
        const name = container.querySelector("#lwDashName").value.trim();
        await this._save({ dashboard_name: name || null });
        const display = name || "llamawatch";
        document.title = display;
        const titleEl = document.querySelector(".topbar-title");
        if (titleEl) titleEl.textContent = display;
        const logoEl = document.querySelector(".topbar-logo");
        if (logoEl) logoEl.textContent = display.substring(0, 2).toUpperCase();
      });

      // Auth toggle visibility
      const authCheckbox = container.querySelector("#lwAuthEnabled");
      const passwordGroup = container.querySelector("#lwAuthPasswordGroup");
      const expiryGroup = container.querySelector("#lwSessionExpiryGroup");
      authCheckbox.addEventListener("change", () => {
        passwordGroup.style.display = authCheckbox.checked ? "block" : "none";
        expiryGroup.style.display = authCheckbox.checked ? "block" : "none";
      });

      // Save handlers
      container.querySelector("#lwSaveAuth").addEventListener("click", async () => {
        const data = { auth_enabled: authCheckbox.checked };
        const password = container.querySelector("#lwAuthPassword").value;
        if (password) data.auth_password = password;
        const expiry = parseInt(container.querySelector("#lwSessionExpiry").value, 10);
        if (expiry > 0) data.session_expiry_days = expiry;
        await this._save(data);
      });

      container.querySelector("#lwSaveServer").addEventListener("click", async () => {
        const port = parseInt(container.querySelector("#lwPort").value, 10);
        const host = container.querySelector("#lwHost").value.trim();
        await this._save({ port, host });
        LlamaWatch.toast("Server settings saved. Restart to apply.", "info");
      });

      container.querySelector("#lwSaveSearxng").addEventListener("click", async () => {
        const v = container.querySelector("#lwSearxngUrl").value.trim();
        await this._save({ searxng_url: v || null });
      });

      container.querySelector("#lwSaveSensors").addEventListener("click", async () => {
        await this._save({ sensors: container.querySelector("#lwSensors").value });
      });
    },

    // ── Dynamic Form Generation ─────────────────────────────────────

    _renderConfigForm(schema, values, onSave) {
      const form = document.createElement("div");
      form.className = "lw-config-form";

      for (const field of schema) {
        const value = values[field.key] !== undefined ? values[field.key] : field.default;
        form.appendChild(this._renderField(field, value));
      }

      const saveBtn = document.createElement("button");
      saveBtn.className = "lw-save-btn";
      saveBtn.textContent = "Save";
      saveBtn.addEventListener("click", () => {
        const data = {};
        for (const field of schema) {
          data[field.key] = this._extractFieldValue(form, field);
        }
        onSave(data);
      });
      form.appendChild(saveBtn);

      return form;
    },

    _renderField(field, value) {
      const group = document.createElement("div");
      group.className = "lw-form-group";
      group.dataset.key = field.key;

      if (field.type === "source-list") {
        return this._renderListField(field, value);
      }

      if (field.type !== "boolean") {
        const label = document.createElement("label");
        label.className = "lw-form-label";
        label.textContent = field.label || field.key;
        group.appendChild(label);
      }

      if (field.description) {
        const desc = document.createElement("div");
        desc.className = "lw-form-desc";
        desc.textContent = field.description;
        group.appendChild(desc);
      }

      switch (field.type) {
        case "text": {
          const input = document.createElement("input");
          input.className = "lw-form-input";
          input.type = "text";
          input.value = value || "";
          if (field.placeholder) input.placeholder = field.placeholder;
          input.dataset.key = field.key;
          group.appendChild(input);
          break;
        }
        case "number": {
          const input = document.createElement("input");
          input.className = "lw-form-input";
          input.type = "number";
          input.value = value != null ? value : "";
          if (field.placeholder) input.placeholder = field.placeholder;
          if (field.min != null) input.min = field.min;
          if (field.max != null) input.max = field.max;
          input.dataset.key = field.key;
          group.appendChild(input);
          break;
        }
        case "boolean": {
          const label = document.createElement("label");
          label.className = "lw-toggle-inline";
          label.innerHTML = `
            <input type="checkbox" ${value ? "checked" : ""} data-key="${field.key}">
            <span>${this._esc(field.label || field.key)}</span>
          `;
          group.appendChild(label);
          break;
        }
        case "select": {
          const select = document.createElement("select");
          select.className = "lw-form-select";
          select.dataset.key = field.key;
          const options = field.options || [];
          for (const opt of options) {
            const optEl = document.createElement("option");
            if (typeof opt === "object") {
              optEl.value = opt.value;
              optEl.textContent = opt.label;
            } else {
              optEl.value = opt;
              optEl.textContent = opt;
            }
            if (String(value) === String(optEl.value)) optEl.selected = true;
            select.appendChild(optEl);
          }
          group.appendChild(select);
          break;
        }
        case "multiselect": {
          const wrapper = document.createElement("div");
          wrapper.className = "lw-multiselect";
          wrapper.dataset.key = field.key;

          // If options_from, we need to fetch dynamically
          if (field.options_from) {
            wrapper.textContent = "Loading options...";
            this._loadOptions(field.options_from).then(options => {
              wrapper.textContent = "";
              this._populateMultiselect(wrapper, options, value || []);
            });
          } else {
            this._populateMultiselect(wrapper, field.options || [], value || []);
          }
          group.appendChild(wrapper);
          break;
        }
        case "textarea": {
          const textarea = document.createElement("textarea");
          textarea.className = "lw-form-textarea";
          textarea.value = value || "";
          textarea.rows = field.rows || 3;
          if (field.placeholder) textarea.placeholder = field.placeholder;
          textarea.dataset.key = field.key;
          group.appendChild(textarea);
          break;
        }
        default: {
          const input = document.createElement("input");
          input.className = "lw-form-input";
          input.type = "text";
          input.value = value || "";
          input.dataset.key = field.key;
          group.appendChild(input);
        }
      }

      return group;
    },

    _renderListField(field, items) {
      const wrapper = document.createElement("div");
      wrapper.className = "lw-list-field";
      wrapper.dataset.key = field.key;

      const label = document.createElement("div");
      label.className = "lw-form-label";
      label.textContent = field.label || field.key;
      wrapper.appendChild(label);

      if (field.description) {
        const desc = document.createElement("div");
        desc.className = "lw-form-desc";
        desc.textContent = field.description;
        wrapper.appendChild(desc);
      }

      const listContainer = document.createElement("div");
      listContainer.className = "lw-list-items";

      const itemSchema = field.item_schema || [];
      const currentItems = Array.isArray(items) ? items : [];

      for (const item of currentItems) {
        listContainer.appendChild(this._createListItem(itemSchema, item));
      }

      wrapper.appendChild(listContainer);

      const addBtn = document.createElement("button");
      addBtn.className = "lw-btn-sm lw-btn-add";
      addBtn.textContent = "+ Add";
      addBtn.addEventListener("click", () => {
        listContainer.appendChild(this._createListItem(itemSchema, {}));
      });
      wrapper.appendChild(addBtn);

      return wrapper;
    },

    _createListItem(itemSchema, values) {
      const row = document.createElement("div");
      row.className = "lw-list-item";

      const fields = document.createElement("div");
      fields.className = "lw-list-item-fields";

      for (const field of itemSchema) {
        const val = values[field.key] !== undefined ? values[field.key] : field.default;
        const el = this._renderField(field, val);
        el.classList.add("lw-list-item-field");
        fields.appendChild(el);
      }
      row.appendChild(fields);

      const removeBtn = document.createElement("button");
      removeBtn.className = "lw-btn-sm lw-btn-danger lw-list-remove";
      removeBtn.textContent = "\u00d7";
      removeBtn.title = "Remove";
      removeBtn.addEventListener("click", () => row.remove());
      row.appendChild(removeBtn);

      return row;
    },

    _populateMultiselect(wrapper, options, selected) {
      const selectedSet = new Set(Array.isArray(selected) ? selected : []);
      for (const opt of options) {
        const val = typeof opt === "object" ? opt.value : opt;
        const lbl = typeof opt === "object" ? opt.label : opt;
        const label = document.createElement("label");
        label.className = "lw-multiselect-option";
        label.innerHTML = `<input type="checkbox" value="${this._esc(val)}" ${selectedSet.has(val) ? "checked" : ""}> ${this._esc(lbl)}`;
        wrapper.appendChild(label);
      }
    },

    async _loadOptions(key) {
      try {
        const res = await fetch(`/api/settings/options/${key}`);
        if (res.ok) return await res.json();
      } catch (e) {
        // ignore
      }
      return [];
    },

    _extractFieldValue(form, field) {
      if (field.type === "source-list") {
        return this._extractListValue(form, field);
      }

      const el = form.querySelector(`[data-key="${field.key}"]`);
      if (!el) return field.default;

      switch (field.type) {
        case "boolean":
          return el.checked;
        case "number": {
          const v = el.value.trim();
          return v === "" ? null : parseFloat(v);
        }
        case "multiselect": {
          const wrapper = form.querySelector(`.lw-multiselect[data-key="${field.key}"]`);
          if (!wrapper) return [];
          const checked = wrapper.querySelectorAll('input[type="checkbox"]:checked');
          return Array.from(checked).map(cb => cb.value);
        }
        default:
          return el.value;
      }
    },

    _extractListValue(form, field) {
      const wrapper = form.querySelector(`.lw-list-field[data-key="${field.key}"]`);
      if (!wrapper) return [];
      const items = wrapper.querySelectorAll(".lw-list-item");
      const result = [];
      for (const item of items) {
        const obj = {};
        for (const subField of (field.item_schema || [])) {
          const el = item.querySelector(`[data-key="${subField.key}"]`);
          if (el) {
            obj[subField.key] = subField.type === "boolean" ? el.checked :
                                subField.type === "number" ? (el.value.trim() ? parseFloat(el.value) : null) :
                                el.value;
          }
        }
        result.push(obj);
      }
      return result;
    },

    // ── Save to server ──────────────────────────────────────────────

    async _save(data) {
      try {
        const res = await fetch("/api/settings", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(data),
        });
        if (res.status === 401) {
          LlamaWatch.toast("Session expired — reloading to log in again…", "error");
          setTimeout(() => { window.location.href = "/studio"; }, 1800);
          return;
        }
        if (!res.ok) throw new Error("Server returned " + res.status);
        const result = await res.json();
        if (result.status !== "ok") throw new Error(result.error || "Save failed");

        // Refresh local config
        const configRes = await fetch("/api/settings");
        this._config = await configRes.json();
        this._configChanged = true;
        LlamaWatch.toast("Settings saved", "success");
      } catch (e) {
        LlamaWatch.toast("Save failed: " + e.message, "error");
      }
    },

    // ── Helpers ─────────────────────────────────────────────────────

    _esc(str) {
      // Escape for use in both text nodes AND double/single-quoted attributes.
      return String(str == null ? "" : str)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    },

    _randomId() {
      return Math.random().toString(36).substring(2, 10);
    },

    _makeSeparator() {
      const sep = document.createElement("div");
      sep.className = "lw-settings-separator";
      return sep;
    },
  };

  // Expose globally — LlamaWatch is a const in global scope, not on window
  if (typeof LlamaWatch !== 'undefined') {
    LlamaWatch.settings = Settings;
  } else {
    // If the host page has not defined LlamaWatch yet, queue for later
    window._lwSettingsModule = Settings;
  }
})();
