(() => {
      "use strict";

      const STATUS_LABELS = {
        ACTIVE: "Active",
        SHORT_TIME: "Short Time",
        TRAFFIC: "Traffic",
        LIAISON: "Liaison",
        MOBILE: "Mobile",
        PORTABLE: "Portable",
        RELAY: "Relay",
        CHECKED_OUT: "Checked Out",
      };

      const STATUS_CYCLE = ["ACTIVE", "SHORT_TIME", "LIAISON", "MOBILE", "PORTABLE", "RELAY", "CHECKED_OUT"];

      const UI_FONTS = {
        system: '"Segoe UI", "Ubuntu", "Cantarell", "Noto Sans", system-ui, -apple-system, BlinkMacSystemFont, "Liberation Sans", sans-serif',
        humanist: '"Calibri", "Ubuntu", "Cantarell", "Trebuchet MS", "Liberation Sans", "DejaVu Sans", sans-serif',
        geometric: '"Verdana", "DejaVu Sans", "Liberation Sans", "Tahoma", "Segoe UI", sans-serif',
        serif: '"Georgia", "Liberation Serif", "DejaVu Serif", "Cambria", "Times New Roman", serif',
        mono: '"Cascadia Mono", "JetBrains Mono", "Ubuntu Mono", "Liberation Mono", "DejaVu Sans Mono", "Consolas", monospace',
      };

      const MONO_FONTS = {
        cascadia: '"Cascadia Mono", "JetBrains Mono", "Ubuntu Mono", "DejaVu Sans Mono", "Consolas", monospace',
        consolas: '"Consolas", "Liberation Mono", "DejaVu Sans Mono", "Lucida Console", monospace',
        courier: '"Courier New", "Liberation Mono", "DejaVu Sans Mono", Courier, monospace',
      };

      const DEFAULT_PANEL_LAYOUT = {
        scripts: { visible: false, position: "right" },
        radiograms: { visible: false, position: "right" },
        ntsHistory: { visible: false, position: "right" },
        roster: { visible: false, position: "right" },
        vfo: { visible: true },
        footer: { visible: true },
      };

      const LAYOUT_PRESETS = {
        standard: {
          scripts: { visible: true, position: "right" },
          radiograms: { visible: false, position: "right" },
          ntsHistory: { visible: false, position: "right" },
          roster: { visible: false, position: "right" },
          vfo: { visible: true },
          footer: { visible: true },
        },
        nts: {
          scripts: { visible: true, position: "top" },
          radiograms: { visible: true, position: "right" },
          ntsHistory: { visible: true, position: "left" },
          roster: { visible: false, position: "right" },
          vfo: { visible: true },
          footer: { visible: true },
        },
        skywarn: {
          scripts: { visible: true, position: "top" },
          radiograms: { visible: false, position: "right" },
          ntsHistory: { visible: false, position: "right" },
          roster: { visible: false, position: "right" },
          vfo: { visible: true },
          footer: { visible: true },
        },
        minimal: {
          scripts: { visible: false, position: "right" },
          radiograms: { visible: false, position: "right" },
          ntsHistory: { visible: false, position: "right" },
          roster: { visible: false, position: "right" },
          vfo: { visible: true },
          footer: { visible: false },
        },
      };

      const DOCKABLE_PANELS = {
        scripts: "panelScripts",
        radiograms: "panelRadiograms",
        ntsHistory: "panelNtsHistory",
        roster: "panelRoster",
      };

      const DOCK_ZONES = {
        left: "dockLeft",
        right: "dockRight",
        top: "dockTop",
        bottom: "dockBottom",
      };

      const DOCK_RESIZERS = {
        left: "resizerLeft",
        right: "resizerRight",
        top: "resizerTop",
        bottom: "resizerBottom",
      };

      const VALID_DOCK_POSITIONS = ["left", "right", "top", "bottom"];

      function sanitizePanelLayout(raw, fallbackScriptPos = "right") {
        const src = (raw && typeof raw === "object") ? raw : {};
        const out = {};
        for (const key of Object.keys(DEFAULT_PANEL_LAYOUT)) {
          const def = DEFAULT_PANEL_LAYOUT[key];
          const item = (src[key] && typeof src[key] === "object") ? src[key] : {};
          const entry = { visible: typeof item.visible === "boolean" ? item.visible : def.visible };
          if ("position" in def) {
            const rawPos = item.position || (key === "scripts" ? fallbackScriptPos : def.position);
            entry.position = VALID_DOCK_POSITIONS.includes(rawPos) ? rawPos : def.position;
          }
          out[key] = entry;
        }
        return out;
      }

      const state = {
        config: { operator_call: "W5CDN", qrz_configured: false, version: "0.1.8" },
        prefs: {
          theme: "shack-dark",
          ui_font: "system",
          mono_font: "cascadia",
          font_size: 14,
          callsign_font_size: 15,
          script_panel_orientation: "vertical",
          script_panel_position: "right",
          panel_layout: sanitizePanelLayout(null),
          operator_call: "W5CDN",
          qrz_user: "",
          qrz_pass_set: false,
          qrz_source: "none",
          qrz_configured: false,
          radiograms_dir: "radiograms",
          nws_county: "",
          nws_state: "",
          nws_contact: {},
          net_type_defaults: {},
          digital_routing: {},
          ncs_script: "",
        },
        draftTheme: "shack-dark",
        digitalDispatchData: null,
        scripts: [],
        activeScriptId: null,
        editingScriptId: null,
        scriptZoomPct: 100,
        radiograms: [],
        ntsHistory: null,
        monthlyReportPreview: null,
        nwsReport: null,
        nwsKnownAlertIds: new Set(),
        nwsPollTimer: null,
        nwsPollNetId: null,
        idTimerSeconds: 600,
        rgScope: "net",
        wrapNotes: false,
        nets: [],
        netsSearchQuery: "",
        activeNet: null,
        selectedCheckinId: null,
        filter: "ALL",
        searchQuery: "",
        entryTraffic: false,
        editingNetId: null,
        pendingNewNetPreset: null,
        pendingCloneNet: null,
        lastLookedUpCall: "",
        activeLookupCall: "",
        activeLookupPromise: null,
        autoFilled: { name: "", qth: "", grid: "" },
        jumpToStatusOnLookup: false,
        submittingCheckin: false,
        lookupSeq: 0,
        autocompleteItems: [],
        autocompleteIdx: -1,
        roster: [],
        rollCalled: new Map(),
        rollRoundByNet: new Map(),
        updateInfo: null,
      };

      const $ = (id) => document.getElementById(id);

      // -----------------------------------------------------------------------
      // Theme, Fonts & 5-Zone Dockable Panel Layout Engine
      // -----------------------------------------------------------------------
      function syncPanelsModalControls(layout) {
        const clean = sanitizePanelLayout(layout);
        document.querySelectorAll("[data-panel-vis]").forEach((chk) => {
          const key = chk.dataset.panelVis;
          if (clean[key]) chk.checked = Boolean(clean[key].visible);
        });
        document.querySelectorAll("[data-set-panel-pos]").forEach((btn) => {
          const [key, pos] = (btn.dataset.setPanelPos || "").split(":");
          if (clean[key]) {
            btn.classList.toggle("active", clean[key].position === pos);
          }
        });
      }

      function applyPanelLayout(layout) {
        const clean = sanitizePanelLayout(layout, state.prefs.script_panel_position || "right");

        // Place each dockable panel in its target zone and toggle visibility
        for (const [key, panelDomId] of Object.entries(DOCKABLE_PANELS)) {
          const panelEl = $(panelDomId);
          const cfg = clean[key];
          if (!panelEl || !cfg) continue;
          const zoneEl = $(DOCK_ZONES[cfg.position] || "dockRight");
          if (zoneEl && panelEl.parentElement !== zoneEl) {
            zoneEl.appendChild(panelEl);
          }
          panelEl.classList.toggle("hidden", !cfg.visible);
        }

        // Show or hide each dock zone and its drag resizer based on visible panels
        for (const [zoneKey, zoneDomId] of Object.entries(DOCK_ZONES)) {
          const zoneEl = $(zoneDomId);
          if (!zoneEl) continue;
          const hasVisible = Boolean(zoneEl.querySelector(".dock-panel:not(.hidden)"));
          zoneEl.classList.toggle("hidden", !hasVisible);
          const resizerEl = $(DOCK_RESIZERS[zoneKey]);
          if (resizerEl) resizerEl.classList.toggle("hidden", !hasVisible);
        }

        // Update mini 4-way dock buttons inside every panel header
        document.querySelectorAll("[data-panel-dock]").forEach((btn) => {
          const [key, pos] = (btn.dataset.panelDock || "").split(":");
          if (clean[key]) {
            btn.classList.toggle("active", clean[key].position === pos);
          }
        });

        // Update left rail active indicators for dockable panels
        $("btnToggleScript")?.classList.toggle("active", Boolean(clean.scripts.visible));
        $("btnOpenRadiograms")?.classList.toggle("active", Boolean(clean.radiograms.visible));
        $("btnOpenNtsHistory")?.classList.toggle("active", Boolean(clean.ntsHistory.visible));
        $("btnOpenRoster")?.classList.toggle("active", Boolean(clean.roster.visible));

        // Update in-net script reader button text
        const netScriptBtn = $("btnNetReadScript");
        if (netScriptBtn) {
          netScriptBtn.classList.toggle("btn-active-toggle", Boolean(clean.scripts.visible));
          netScriptBtn.textContent = clean.scripts.visible ? "Hide Script" : "Read On Air";
        }

        // Update toolbar strips (footer + VFO)
        $("shortcutFooter")?.classList.toggle("hidden", !clean.footer.visible);
        const vfoEl = $("vfoCluster");
        if (vfoEl) {
          const showVfo = Boolean(clean.vfo.visible) && Boolean(state.activeNet);
          vfoEl.classList.toggle("hidden", !showVfo);
        }

        syncPanelsModalControls(clean);
      }

      let panelLayoutSaveSeq = 0;
      let panelLayoutSavePromise = Promise.resolve();

      async function savePanelLayout(nextLayout, quiet = true) {
        const saveSeq = ++panelLayoutSaveSeq;
        const prev = sanitizePanelLayout(state.prefs.panel_layout);
        const clean = sanitizePanelLayout(nextLayout);
        state.prefs.panel_layout = clean;
        state.prefs.script_panel_position = clean.scripts.position;
        state.prefs.script_panel_orientation = (clean.scripts.position === "top" || clean.scripts.position === "bottom") ? "horizontal" : "vertical";
        applyPanelLayout(clean);

        try {
          const cached = JSON.parse(localStorage.getItem("w5cdn_visual_prefs") || "{}") || {};
          cached.panel_layout = clean;
          cached.script_panel_position = state.prefs.script_panel_position;
          cached.script_panel_orientation = state.prefs.script_panel_orientation;
          localStorage.setItem("w5cdn_visual_prefs", JSON.stringify(cached));
        } catch {
          // ignore storage restrictions
        }

        // Hydrate panel data when newly shown
        if (clean.scripts.visible && !prev.scripts.visible) {
          renderScriptReader();
        }
        if (clean.radiograms.visible && !prev.radiograms.visible) {
          syncRadiogramsPanel();
        }
        if (clean.ntsHistory.visible && !prev.ntsHistory.visible) {
          loadNtsHistory();
        }
        if (clean.roster.visible && !prev.roster.visible) {
          loadRoster($("rosterSearch")?.value.trim() || "");
        }

        panelLayoutSavePromise = panelLayoutSavePromise
          .catch(() => {})
          .then(async () => {
            if (saveSeq !== panelLayoutSaveSeq) return;
            try {
              const saved = await api("/api/preferences", {
                method: "PUT",
                body: JSON.stringify({ panel_layout: clean }),
              });
              if (saveSeq !== panelLayoutSaveSeq) return;
              state.prefs = { ...state.prefs, ...saved };
              if (!quiet) toast("Panel layout saved", "success");
            } catch (err) {
              if (saveSeq !== panelLayoutSaveSeq) return;
              toast(`Layout save error: ${err.message}`, "error");
            }
          });
        await panelLayoutSavePromise;
      }

      function togglePanel(panelKey, forceVisible = null) {
        const next = sanitizePanelLayout(state.prefs.panel_layout);
        if (!next[panelKey]) return;
        const willShow = forceVisible === null ? !next[panelKey].visible : Boolean(forceVisible);
        next[panelKey] = { ...next[panelKey], visible: willShow };
        savePanelLayout(next, true);
        if (panelKey === "roster" && willShow) {
          setTimeout(() => {
            if (state.prefs.panel_layout?.roster?.visible) $("rosterSearch")?.focus();
          }, 40);
        }
      }

      function movePanel(panelKey, position, ensureVisible = false) {
        if (!VALID_DOCK_POSITIONS.includes(position)) return;
        const next = sanitizePanelLayout(state.prefs.panel_layout);
        if (!next[panelKey] || !("position" in next[panelKey])) return;
        next[panelKey] = {
          ...next[panelKey],
          position,
          ...(ensureVisible ? { visible: true } : {}),
        };
        savePanelLayout(next, true);
      }

      function applyLayoutPreset(presetName) {
        const preset = LAYOUT_PRESETS[presetName];
        if (!preset) return;
        savePanelLayout(preset, false);
      }

      function applyVisualPrefs(p) {
        const root = document.documentElement;
        root.setAttribute("data-theme", p.theme || "shack-dark");
        root.style.setProperty("--font-sans", UI_FONTS[p.ui_font] || UI_FONTS.system);
        root.style.setProperty("--font-mono", MONO_FONTS[p.mono_font] || MONO_FONTS.cascadia);
        root.style.setProperty("--font-size", `${Number(p.font_size) || 14}px`);
        root.style.setProperty("--callsign-size", `${Number(p.callsign_font_size) || 15}px`);

        if (p.panel_layout) {
          applyPanelLayout(p.panel_layout);
        } else if (p.script_panel_position) {
          const previewLayout = sanitizePanelLayout(state.prefs.panel_layout);
          previewLayout.scripts.position = p.script_panel_position;
          applyPanelLayout(previewLayout);
        }
      }

      function applySavedPrefs(prefs) {
        const nextLayout = sanitizePanelLayout(
          prefs.panel_layout || state.prefs.panel_layout,
          prefs.script_panel_position || state.prefs.script_panel_position || "right"
        );
        state.prefs = { ...state.prefs, ...prefs, panel_layout: nextLayout };
        state.config.operator_call = state.prefs.operator_call || "W5CDN";
        state.config.qrz_configured = Boolean(state.prefs.qrz_configured);
        applyVisualPrefs(state.prefs);

        try {
          localStorage.setItem("w5cdn_visual_prefs", JSON.stringify({
            theme: state.prefs.theme,
            ui_font: state.prefs.ui_font,
            mono_font: state.prefs.mono_font,
            font_size: state.prefs.font_size,
            callsign_font_size: state.prefs.callsign_font_size,
            script_panel_orientation: state.prefs.script_panel_orientation,
            script_panel_position: state.prefs.script_panel_position,
            panel_layout: state.prefs.panel_layout,
          }));
        } catch {
          // ignore storage restrictions
        }

        const opBadge = $("operatorCallBadge");
        const activeOp = state.config.operator_call || "W5CDN";
        opBadge.textContent = `OP: ${activeOp}`;
        opBadge.classList.toggle("hidden", activeOp === "W5CDN");
        document.title = "W5CDN Net Logger";

        const pill = $("lookupSourcePill");
        if (state.prefs.qrz_configured) {
          pill.textContent = "QRZ XML + CALLOOK";
          pill.classList.add("qrz");
        } else {
          pill.textContent = "CALLOOK (SET QRZ)";
          pill.classList.remove("qrz");
        }

        if ($("rgFolderDisplay") && state.prefs.radiograms_dir) {
          $("rgFolderDisplay").textContent = state.prefs.radiograms_dir;
        }

        renderScriptReader();
      }

      try {
        const cachedVisual = JSON.parse(localStorage.getItem("w5cdn_visual_prefs") || "null");
        if (cachedVisual) {
          if (cachedVisual.panel_layout) {
            state.prefs.panel_layout = sanitizePanelLayout(cachedVisual.panel_layout, cachedVisual.script_panel_position);
          }
          applyVisualPrefs(cachedVisual);
        }
      } catch {
        // ignore invalid cached value
      }

      // -----------------------------------------------------------------------
      // API Helper
      // -----------------------------------------------------------------------
      let backendOffline = false;

      function setBackendOffline(offline) {
        const banner = $("backendOfflineBanner");
        if (offline) {
          if (!backendOffline) {
            backendOffline = true;
            banner?.classList.remove("hidden");
          }
        } else if (backendOffline) {
          backendOffline = false;
          banner?.classList.add("hidden");
          toast("Reconnected to local Net Logger backend", "success");
        }
      }

      async function checkBackendHealth(manual = false) {
        try {
          const resp = await fetch("/api/health", { cache: "no-store" });
          if (resp.ok) {
            setBackendOffline(false);
            if (manual) toast("Local Net Logger backend is online", "success");
            return true;
          }
          setBackendOffline(true);
          return false;
        } catch {
          setBackendOffline(true);
          return false;
        }
      }

      async function api(path, options = {}) {
        const opts = {
          headers: { "Content-Type": "application/json", ...(options.headers || {}) },
          ...options,
        };
        let resp;
        try {
          resp = await fetch(path, opts);
        } catch (err) {
          setBackendOffline(true);
          throw new Error("Local Net Logger backend is unreachable — please restart the app.");
        }
        setBackendOffline(false);
        if (resp.status === 204) return null;
        const isJson = (resp.headers.get("content-type") || "").includes("application/json");
        const body = isJson ? await resp.json() : await resp.text();
        if (!resp.ok) {
          const msg = (body && body.detail) ? (typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail)) : `HTTP ${resp.status}`;
          throw new Error(msg);
        }
        return body;
      }

      function toast(message, type = "info") {
        const el = document.createElement("div");
        el.className = `toast ${type}`;
        el.textContent = message;
        $("toastStack").appendChild(el);
        setTimeout(() => {
          el.style.opacity = "0";
          el.style.transition = "opacity 0.25s";
          setTimeout(() => el.remove(), 260);
        }, 2800);
      }

      function escHtml(str) {
        return String(str ?? "")
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;")
          .replace(/"/g, "&quot;")
          .replace(/'/g, "&#39;");
      }

      function isSafeHttpUrl(raw) {
        const s = String(raw ?? "").trim();
        return /^https?:\/\/\S+$/i.test(s);
      }

      function formatUtcTime(iso) {
        if (!iso || iso.length < 19) return "--:--Z";
        return `${iso.slice(11, 16)}Z`;
      }

      function netTypeClass(netType) {
        const t = (netType || "").toLowerCase();
        if (t.includes("ares") || t.includes("races")) return "ares";
        if (t.includes("skywarn")) return "skywarn";
        if (t === "nts" || t.includes("nts ")) return "nts";
        if (t === "hf" || t.includes("hf ")) return "hf";
        return "";
      }

      function setFilter(nextFilter) {
        state.filter = nextFilter;
        document.querySelectorAll(".filter-btn[data-filter]").forEach((b) => {
          b.classList.toggle("active", b.dataset.filter === nextFilter);
        });
        renderCheckinsTable();
      }

      // -----------------------------------------------------------------------
      // Multiple NCS Scripts Manager & Live Reader
      // -----------------------------------------------------------------------
      async function loadScripts(selectId = null) {
        state.scripts = await api("/api/scripts");
        renderScriptSelector();
        if (selectId && state.scripts.some((s) => s.id === selectId)) {
          state.activeScriptId = selectId;
        } else if (!state.activeScriptId || !state.scripts.some((s) => s.id === state.activeScriptId)) {
          const def = state.scripts.find((s) => s.is_default) || state.scripts[0];
          state.activeScriptId = def ? def.id : null;
        }
        syncScriptSelectors();
        renderScriptReader();
        renderPrefsScriptsList();
      }

      function autoSelectScriptForNet(net) {
        if (!net || !state.scripts.length) return;
        const t = (net.net_type || "").toLowerCase();
        let match = null;
        if (t.includes("skywarn")) {
          match = state.scripts.find((s) => s.category.toLowerCase().includes("skywarn") || s.name.toLowerCase().includes("skywarn"));
        } else if (t.includes("nts")) {
          match = state.scripts.find((s) => s.category.toLowerCase() === "nts" || s.name.toLowerCase().includes("nts"));
        } else if (t.includes("ares") || t.includes("races")) {
          match = state.scripts.find((s) => s.category.toLowerCase().includes("ares") || s.name.toLowerCase().includes("ares"));
        }
        if (match) {
          state.activeScriptId = match.id;
          syncScriptSelectors();
        }
      }

      function renderScriptSelector() {
        const html = !state.scripts.length
          ? `<option value="">No scripts defined</option>`
          : state.scripts.map((s) => {
              const prefix = s.category ? `[${s.category}] ` : "";
              return `<option value="${s.id}">${escHtml(prefix + s.name)}</option>`;
            }).join("");

        ["scriptSelect", "netScriptSelect"].forEach((id) => {
          const sel = $(id);
          if (!sel) return;
          sel.innerHTML = html;
          if (state.activeScriptId) sel.value = String(state.activeScriptId);
        });
      }

      function syncScriptSelectors() {
        ["scriptSelect", "netScriptSelect"].forEach((id) => {
          const sel = $(id);
          if (sel && state.activeScriptId) sel.value = String(state.activeScriptId);
        });
      }

      function getCurrentScriptObj() {
        return state.scripts.find((s) => s.id === state.activeScriptId) || state.scripts[0] || null;
      }

      function buildInterpolatedScript(rawMode = false) {
        const scriptObj = getCurrentScriptObj();
        const raw = scriptObj ? scriptObj.body : (state.prefs.ncs_script ?? "");
        if (rawMode) return raw;

        const now = new Date();
        const pad = (n) => String(n).padStart(2, "0");
        const utcTime = `${pad(now.getUTCHours())}:${pad(now.getUTCMinutes())}`;
        const utcDate = `${now.getUTCFullYear()}-${pad(now.getUTCMonth() + 1)}-${pad(now.getUTCDate())}`;
        const net = state.activeNet;
        const checkins = (net && net.checkins) ? net.checkins : [];
        const tfcCount = checkins.filter((c) => c.has_traffic).length;

        const replacements = {
          "{{NCS_CALL}}": (net && net.ncs_call) || state.config.operator_call || "W5CDN",
          "{{NET_NAME}}": (net && net.name) || "[Net Name]",
          "{{NET_TYPE}}": (net && net.net_type) || "[Net Type]",
          "{{FREQUENCY}}": (net && net.frequency) || "[Frequency]",
          "{{BAND}}": (net && net.band) || "[Band]",
          "{{MODE}}": (net && net.mode) || "[Mode]",
          "{{TONE}}": (net && net.tone_offset) || "[Tone/Offset]",
          "{{UTC_TIME}}": utcTime,
          "{{UTC_DATE}}": utcDate,
          "{{CHECKIN_COUNT}}": String(checkins.length),
          "{{TRAFFIC_COUNT}}": String(tfcCount),
          "{{RADIOGRAM_COUNT}}": String((net && net.radiogram_count) || 0),
        };

        let text = raw;
        for (const [token, value] of Object.entries(replacements)) {
          text = text.split(token).join(value);
        }
        return text;
      }

      function renderScriptReader() {
        const container = $("scriptReaderBody");
        if (!container) return;
        const rawMode = $("chkScriptRaw")?.checked || false;
        const text = buildInterpolatedScript(rawMode);
        if (!text.trim()) {
          container.innerHTML = `<span style="color:var(--text-muted);">No NCS script defined yet. Click <strong>Edit</strong> to create scripts.</span>`;
          return;
        }
        const htmlLines = text.split("\n").map((line) => {
          const safe = escHtml(line);
          const trimmed = line.trim();
          const isHeading =
            (trimmed.startsWith("===") && trimmed.endsWith("===") && trimmed.length > 6) ||
            (trimmed.startsWith("[") && trimmed.endsWith("]") && trimmed.length > 2);
          if (isHeading) {
            return `<span class="script-heading-line">${safe}</span>`;
          }
          return safe;
        });
        container.innerHTML = htmlLines.join("\n");
      }

      function toggleScriptPanel(forceState = null) {
        togglePanel("scripts", forceState);
      }

      function renderPrefsScriptsList() {
        const listEl = $("prefsScriptsList");
        if (!listEl) return;
        if (!state.scripts.length) {
          listEl.innerHTML = `<div style="font-size:0.84em; color:var(--text-muted); padding:8px 4px;">No scripts yet.</div>`;
          return;
        }
        listEl.innerHTML = state.scripts.map((s) => {
          const active = state.editingScriptId === s.id;
          return `
            <button type="button" class="script-list-item ${active ? "active" : ""}" data-script-item-id="${s.id}">
              <span style="font-weight:700; font-size:0.88em; color:var(--text-primary);">${escHtml(s.name)}</span>
              <span style="font-size:0.76em; color:var(--text-muted);">${escHtml(s.category)}${s.is_default ? " • Default" : ""}</span>
            </button>
          `;
        }).join("");
      }

      function loadScriptIntoEditor(scriptObj) {
        if (!scriptObj) {
          state.editingScriptId = null;
          $("scriptEditName").value = "";
          $("scriptEditCategory").value = "Preamble";
          $("scriptEditDefault").checked = false;
          $("prefNcsScript").value = "";
          $("btnDeleteCurrentScript").disabled = true;
        } else {
          state.editingScriptId = scriptObj.id;
          $("scriptEditName").value = scriptObj.name || "";
          $("scriptEditCategory").value = scriptObj.category || "General";
          $("scriptEditDefault").checked = Boolean(scriptObj.is_default);
          $("prefNcsScript").value = scriptObj.body || "";
          $("btnDeleteCurrentScript").disabled = false;
        }
        updateScriptCharCount();
        renderPrefsScriptsList();
      }

      async function saveCurrentScriptFromEditor() {
        const name = $("scriptEditName").value.trim();
        if (!name) {
          toast("Please enter a script name", "error");
          $("scriptEditName").focus();
          return;
        }
        const payload = {
          name,
          category: $("scriptEditCategory").value,
          body: $("prefNcsScript").value,
          is_default: $("scriptEditDefault").checked,
        };
        try {
          let saved;
          if (state.editingScriptId) {
            saved = await api(`/api/scripts/${state.editingScriptId}`, {
              method: "PUT",
              body: JSON.stringify(payload),
            });
          } else {
            saved = await api("/api/scripts", {
              method: "POST",
              body: JSON.stringify(payload),
            });
          }
          await loadScripts(saved.id);
          loadScriptIntoEditor(saved);
          toast(`Saved script: ${saved.name}`, "success");
        } catch (err) {
          toast(`Script save error: ${err.message}`, "error");
        }
      }

      async function deleteCurrentScriptFromEditor() {
        if (!state.editingScriptId) return;
        const target = state.scripts.find((s) => s.id === state.editingScriptId);
        if (!target || !confirm(`Delete script "${target.name}"?`)) return;
        try {
          await api(`/api/scripts/${target.id}`, { method: "DELETE" });
          state.editingScriptId = null;
          await loadScripts();
          loadScriptIntoEditor(state.scripts[0] || null);
          toast(`Deleted script: ${target.name}`);
        } catch (err) {
          toast(err.message, "error");
        }
      }

      // -----------------------------------------------------------------------
      // Preferences Modal
      // -----------------------------------------------------------------------
      const NET_TYPE_ORDER = ["Club VHF/UHF", "ARES", "Skywarn", "NTS", "HF", "RACES", "Other"];
      const NET_MODE_OPTIONS = ["FM", "LSB", "USB", "SSB", "DMR", "C4FM", "D-STAR", "CW", "AM", "JS8", "FT8"];

      function renderNetTypeDefaultsEditor(defs) {
        const tbody = $("prefNetDefaultsBody");
        if (!tbody) return;
        const map = (defs && typeof defs === "object") ? defs : {};
        tbody.innerHTML = NET_TYPE_ORDER.map((nt) => {
          const d = map[nt] || {};
          const modeOpts = NET_MODE_OPTIONS.map(
            (m) => `<option value="${m}"${(d.mode || "FM") === m ? " selected" : ""}>${m}</option>`
          ).join("");
          return `
            <tr data-nt-row="${escHtml(nt)}">
              <td style="padding:6px 8px; font-weight:700; white-space:nowrap; border-bottom:1px solid var(--border);">${escHtml(nt)}</td>
              <td style="padding:5px 6px; border-bottom:1px solid var(--border);">
                <input type="text" class="input mono" data-nt-field="frequency" value="${escHtml(d.frequency || "")}" placeholder="146.880" style="padding:4px 7px; min-width:96px;" />
              </td>
              <td style="padding:5px 6px; border-bottom:1px solid var(--border);">
                <input type="text" class="input mono" data-nt-field="band" value="${escHtml(d.band || "")}" placeholder="2m" style="padding:4px 7px; width:66px;" />
              </td>
              <td style="padding:5px 6px; border-bottom:1px solid var(--border);">
                <select class="select" data-nt-field="mode" style="padding:4px 6px; width:88px;">${modeOpts}</select>
              </td>
              <td style="padding:5px 6px; border-bottom:1px solid var(--border);">
                <input type="text" class="input" data-nt-field="tone_offset" value="${escHtml(d.tone_offset || "")}" placeholder="PL 110.9 Hz, -0.600" style="padding:4px 7px; min-width:150px;" />
              </td>
            </tr>
          `;
        }).join("");
      }

      function collectNetTypeDefaultsFromEditor() {
        const out = {};
        document.querySelectorAll("#prefNetDefaultsBody tr[data-nt-row]").forEach((tr) => {
          const nt = tr.dataset.ntRow;
          const prev = (state.prefs.net_type_defaults && state.prefs.net_type_defaults[nt]) || {};
          out[nt] = {
            name: prev.name || "",
            frequency: tr.querySelector('[data-nt-field="frequency"]')?.value.trim() || "",
            band: tr.querySelector('[data-nt-field="band"]')?.value.trim() || "",
            mode: tr.querySelector('[data-nt-field="mode"]')?.value || "FM",
            tone_offset: tr.querySelector('[data-nt-field="tone_offset"]')?.value.trim() || "",
          };
        });
        return out;
      }

      function populateNwsContactEditor(nc) {
        const c = (nc && typeof nc === "object") ? nc : {};
        $("prefNwsWfoName").value = c.wfo_name || "";
        $("prefNwsPhone").value = c.phone || "";
        $("prefNwsEmail").value = c.email || "";
        $("prefNwsChatUrl").value = c.nwschat_url || "";
        $("prefNwsRadioLiaison").value = c.radio_liaison || "";
        $("prefNwsWinlinkAddr").value = c.winlink_address || "";
        $("prefNwsContactNotes").value = c.reporting_notes || "";
      }

      function collectNwsContactFromEditor() {
        return {
          wfo_name: $("prefNwsWfoName").value.trim(),
          phone: $("prefNwsPhone").value.trim(),
          email: $("prefNwsEmail").value.trim(),
          nwschat_url: $("prefNwsChatUrl").value.trim(),
          radio_liaison: $("prefNwsRadioLiaison").value.trim(),
          winlink_address: $("prefNwsWinlinkAddr").value.trim(),
          reporting_notes: $("prefNwsContactNotes").value.trim(),
        };
      }

      function syncDigitalRemoteVisibility() {
        const wlOn = Boolean($("prefDigWinlinkEnabled")?.checked);
        const pktOn = Boolean($("prefDigPacketEnabled")?.checked);
        const anyOn = wlOn || pktOn;
        const method = $("prefDigMethod")?.value || "bpq_telnet";

        $("prefDigDisabledHint")?.classList.toggle("hidden", anyOn);
        $("prefDigConfigBody")?.classList.toggle("hidden", !anyOn);
        $("btnTestDigitalNode").disabled = !anyOn;

        $("prefDigPanelBpq")?.classList.toggle("hidden", method !== "bpq_telnet");
        $("prefDigPanelPat")?.classList.toggle("hidden", method !== "pat");
        $("prefDigPanelOutbox")?.classList.toggle("hidden", method !== "outbox");
        $("prefDigBpqNtsTypeField")?.classList.toggle("hidden", !pktOn);
        $("prefDigBpqWlPrefixField")?.classList.toggle("hidden", !wlOn);

        const pill = $("prefDigStatusPill");
        if (pill) {
          const enabledNames = [];
          if (pktOn) enabledNames.push("Packet");
          if (wlOn) enabledNames.push("Winlink");
          if (!enabledNames.length) {
            pill.textContent = "Disabled";
            pill.className = "pill";
          } else {
            const labels = {
              bpq_telnet: "LinBPQ Mail",
              pat: "Pat Winlink",
              outbox: "Outbox Folder",
            };
            pill.textContent = `${enabledNames.join(" + ")} • ${labels[method] || method}`;
            pill.className = "pill pill-rg";
          }
        }
      }

      function populateDigitalRoutingEditor(dr) {
        const d = (dr && typeof dr === "object") ? dr : {};
        $("prefDigWinlinkEnabled").checked = Boolean(d.winlink_enabled);
        $("prefDigPacketEnabled").checked = Boolean(d.packet_enabled);

        let method = "bpq_telnet";
        if (d.transport === "bpq_telnet" || d.tnc_protocol === "BPQ Telnet") {
          method = "bpq_telnet";
        } else if (d.transport === "pat" || d.tnc_protocol === "Pat HTTP") {
          method = (d.winlink_enabled || d.packet_enabled || d.pat_url !== "http://127.0.0.1:8080") ? "pat" : "bpq_telnet";
        } else {
          method = "outbox";
        }
        $("prefDigMethod").value = method;
        $("prefDigWlTo").value = d.winlink_to_default || "";

        // LinBPQ Mail Telnet fields
        $("prefDigBpqHost").value = (d.tnc_host && d.tnc_host !== "127.0.0.1") ? d.tnc_host : (method === "bpq_telnet" ? (d.tnc_host || "") : "");
        $("prefDigBpqPort").value = String(method === "bpq_telnet" ? (d.tnc_port || 8010) : 8010);
        $("prefDigBpqUser").value = d.bpq_user || "";
        $("prefDigBpqPass").value = "";
        $("prefDigBpqPass").placeholder = d.bpq_pass_set
          ? "•••••••• (saved — leave blank to keep)"
          : "Enter BBS Telnet password";
        $("prefDigClearBpqPass").checked = false;
        $("prefDigBpqBbsCmd").value = d.bpq_bbs_cmd !== undefined ? d.bpq_bbs_cmd : "BBS";
        $("prefDigBpqNtsType").value = d.bpq_nts_msg_type === "ST" ? "ST" : "SP";
        $("prefDigBpqWlPrefix").value = d.bpq_winlink_prefix !== undefined ? d.bpq_winlink_prefix : "RMS:";
        $("prefDigBpqWebmailUrl").value = d.bpq_webmail_url || "";

        // Pat & Outbox fields
        $("prefDigPatUrl").value = d.pat_url || "http://127.0.0.1:8080";
        $("prefDigPatOutbox").value = d.outbox_dir || "";
        $("prefDigOutboxDir").value = d.outbox_dir || "";
        $("prefDigNotes").value = d.routing_notes || "";

        $("digitalTestBanner").classList.add("hidden");
        $("digitalTestTranscript").classList.add("hidden");
        $("digitalTestTranscript").textContent = "";
        syncDigitalRemoteVisibility();
      }

      function collectDigitalRoutingFromEditor() {
        const method = $("prefDigMethod").value;
        const wlOn = $("prefDigWinlinkEnabled").checked;
        const pktOn = $("prefDigPacketEnabled").checked;

        let transport = "bpq_telnet";
        let tncProto = "BPQ Telnet";
        let stationMode = "remote_lan";
        let tncHost = "127.0.0.1";
        let tncPort = 8010;
        let outboxDir = "";

        if (method === "bpq_telnet") {
          transport = "bpq_telnet";
          tncProto = "BPQ Telnet";
          tncHost = $("prefDigBpqHost").value.trim() || "127.0.0.1";
          tncPort = Number($("prefDigBpqPort").value) || 8010;
          stationMode = (tncHost === "127.0.0.1" || tncHost.toLowerCase() === "localhost") ? "local" : "remote_lan";
        } else if (method === "pat") {
          transport = "pat";
          tncProto = "Pat HTTP";
          const patUrl = $("prefDigPatUrl").value.trim();
          stationMode = /127\.0\.0\.1|localhost/i.test(patUrl || "127.0.0.1") ? "local" : "remote_lan";
          outboxDir = $("prefDigPatOutbox").value.trim();
        } else {
          transport = wlOn ? "winlink_express" : "packet_bbs";
          tncProto = "None / Manual Relay";
          outboxDir = $("prefDigOutboxDir").value.trim();
          stationMode = (/^\\\\|^\/mnt\/|^\/media\//i.test(outboxDir)) ? "remote_lan" : "local";
        }

        return {
          winlink_enabled: wlOn,
          packet_enabled: pktOn,
          station_mode: stationMode,
          transport,
          bpq_user: $("prefDigBpqUser").value.trim(),
          bpq_bbs_cmd: $("prefDigBpqBbsCmd").value.trim().toUpperCase(),
          bpq_nts_msg_type: $("prefDigBpqNtsType").value === "ST" ? "ST" : "SP",
          bpq_winlink_prefix: $("prefDigBpqWlPrefix").value.trim().toUpperCase(),
          bpq_webmail_url: $("prefDigBpqWebmailUrl").value.trim(),
          winlink_to_default: $("prefDigWlTo").value.trim(),
          packet_bbs_call: "",
          tnc_protocol: tncProto,
          tnc_host: tncHost,
          tnc_port: tncPort,
          pat_url: $("prefDigPatUrl").value.trim(),
          outbox_dir: outboxDir,
          routing_notes: $("prefDigNotes").value.trim(),
        };
      }

      function setPrefTab(tabName) {
        document.querySelectorAll("[data-pref-tab]").forEach((btn) => {
          btn.classList.toggle("active", btn.dataset.prefTab === tabName);
        });
        $("prefTabAppearance").classList.toggle("hidden", tabName !== "appearance");
        $("prefTabQrz").classList.toggle("hidden", tabName !== "qrz");
        $("prefTabNetDefaults").classList.toggle("hidden", tabName !== "netdefaults");
        $("prefTabDigital").classList.toggle("hidden", tabName !== "digital");
        $("prefTabScript").classList.toggle("hidden", tabName !== "script");
      }

      function updateThemeCardHighlight(themeName) {
        state.draftTheme = themeName;
        document.querySelectorAll("[data-theme-pick]").forEach((card) => {
          card.classList.toggle("active", card.dataset.themePick === themeName);
        });
      }

      function updateSliderTooltip(sliderId, tipId) {
        const slider = $(sliderId);
        const tip = $(tipId);
        if (!slider || !tip) return;
        const min = Number(slider.min) || 0;
        const max = Number(slider.max) || 100;
        const val = Number(slider.value) || min;
        const pct = max > min ? (val - min) / (max - min) : 0;
        const label = `${val}px`;
        tip.textContent = label;
        tip.style.left = `calc(${pct * 100}% + ${8 - pct * 16}px)`;
        slider.title = label;
      }

      function previewDraftAppearance() {
        const fs = Number($("prefFontSize").value) || 14;
        const cs = Number($("prefCallsignSize").value) || 15;
        $("prefFontSizeVal").textContent = `${fs}px`;
        $("prefCallsignSizeVal").textContent = `${cs}px`;
        updateSliderTooltip("prefFontSize", "prefFontSizeTip");
        updateSliderTooltip("prefCallsignSize", "prefCallsignSizeTip");
        applyVisualPrefs({
          theme: state.draftTheme,
          ui_font: $("prefUiFont").value,
          mono_font: $("prefMonoFont").value,
          font_size: fs,
          callsign_font_size: cs,
        });
      }

      function updateScriptCharCount() {
        const len = $("prefNcsScript").value.length;
        $("prefScriptCharCount").textContent = `${len.toLocaleString()} / 20,000`;
      }

      function resetSecretInputs() {
        document.querySelectorAll("[data-toggle-secret]").forEach((btn) => {
          const inp = $(btn.dataset.toggleSecret);
          if (inp) inp.type = "password";
          btn.textContent = "Show";
          btn.setAttribute("aria-pressed", "false");
        });
      }

      async function openPreferencesModal(initialTab = "appearance") {
        resetSecretInputs();
        try {
          const fresh = await api("/api/preferences");
          applySavedPrefs(fresh);
        } catch {
          // use current state
        }

        const p = state.prefs;
        updateThemeCardHighlight(p.theme);
        $("prefUiFont").value = p.ui_font;
        $("prefMonoFont").value = p.mono_font;
        $("prefFontSize").value = String(p.font_size);
        $("prefCallsignSize").value = String(p.callsign_font_size);
        $("prefFontSizeVal").textContent = `${p.font_size}px`;
        $("prefCallsignSizeVal").textContent = `${p.callsign_font_size}px`;
        updateSliderTooltip("prefFontSize", "prefFontSizeTip");
        updateSliderTooltip("prefCallsignSize", "prefCallsignSizeTip");

        $("prefOperatorCall").value = p.operator_call || "";
        $("prefQrzUser").value = p.qrz_user || "";
        $("prefQrzPass").value = "";
        $("prefQrzPass").placeholder = p.qrz_pass_set
          ? "•••••••• (saved — leave blank to keep)"
          : "Enter QRZ password";
        $("prefClearQrzPass").checked = false;

        const srcText = {
          preferences: "Active source: Saved in local preferences.",
          env: "Active source: Loaded from .env / environment variables.",
          mixed: "Active source: Combined preferences and environment variables.",
          none: "Not configured — callsign lookups will use Callook.info fallback.",
        }[p.qrz_source] || "";
        $("prefQrzSourceInfo").textContent = srcText;
        $("qrzTestBanner").classList.add("hidden");
        $("prefNwsCounty").value = p.nws_county || "";
        $("prefNwsState").value = p.nws_state || "";
        populateNwsContactEditor(p.nws_contact);
        $("prefRadiogramsDir").value = p.radiograms_dir || "";
        renderNetTypeDefaultsEditor(p.net_type_defaults);
        populateDigitalRoutingEditor(p.digital_routing);
        renderUpdateStatus(state.updateInfo);

        try {
          await loadScripts();
        } catch {
          // keep existing scripts
        }
        loadScriptIntoEditor(getCurrentScriptObj() || state.scripts[0] || null);

        setPrefTab(initialTab);
        $("prefsModalBackdrop").classList.remove("hidden");
      }

      function cancelPreferencesModal() {
        resetSecretInputs();
        applyVisualPrefs(state.prefs);
        $("prefsModalBackdrop").classList.add("hidden");
      }

      async function handlePreferencesSubmit(e) {
        e.preventDefault();
        const payload = {
          theme: state.draftTheme,
          ui_font: $("prefUiFont").value,
          mono_font: $("prefMonoFont").value,
          font_size: Number($("prefFontSize").value),
          callsign_font_size: Number($("prefCallsignSize").value),
          operator_call: $("prefOperatorCall").value.trim().toUpperCase(),
          qrz_user: $("prefQrzUser").value.trim(),
          clear_qrz_pass: $("prefClearQrzPass").checked,
          nws_county: $("prefNwsCounty").value.trim(),
          nws_state: $("prefNwsState").value.trim().toUpperCase(),
          nws_contact: collectNwsContactFromEditor(),
          net_type_defaults: collectNetTypeDefaultsFromEditor(),
          digital_routing: collectDigitalRoutingFromEditor(),
          clear_bpq_pass: $("prefDigClearBpqPass").checked,
          radiograms_dir: $("prefRadiogramsDir").value.trim(),
        };
        const rawPass = $("prefQrzPass").value;
        if (rawPass && !payload.clear_qrz_pass) payload.qrz_pass = rawPass;
        const rawBpqPass = $("prefDigBpqPass").value;
        if (rawBpqPass && !payload.clear_bpq_pass) payload.bpq_pass = rawBpqPass;

        try {
          if ($("scriptEditName").value.trim()) {
            const sPayload = {
              name: $("scriptEditName").value.trim(),
              category: $("scriptEditCategory").value,
              body: $("prefNcsScript").value,
              is_default: $("scriptEditDefault").checked,
            };
            if (state.editingScriptId) {
              await api(`/api/scripts/${state.editingScriptId}`, {
                method: "PUT",
                body: JSON.stringify(sPayload),
              });
            } else {
              const createdScript = await api("/api/scripts", {
                method: "POST",
                body: JSON.stringify(sPayload),
              });
              state.editingScriptId = createdScript.id;
            }
            await loadScripts(state.editingScriptId);
          }

          const saved = await api("/api/preferences", {
            method: "PUT",
            body: JSON.stringify(payload),
          });
          applySavedPrefs(saved);
          resetSecretInputs();
          renderRadiogramsList();
          if (state.prefs.panel_layout?.ntsHistory?.visible) renderNtsHistory();
          $("prefsModalBackdrop").classList.add("hidden");
          toast("Preferences saved", "success");
        } catch (err) {
          toast(`Could not save preferences: ${err.message}`, "error");
        }
      }

      async function handleTestQrz() {
        const banner = $("qrzTestBanner");
        banner.className = "qrz-test-banner";
        banner.textContent = "Testing QRZ XML login...";
        banner.classList.remove("hidden");

        const userVal = $("prefQrzUser").value.trim();
        const passVal = $("prefQrzPass").value;
        const clearPass = $("prefClearQrzPass").checked;
        if (userVal !== state.prefs.qrz_user || passVal || clearPass) {
          try {
            const updated = await api("/api/preferences", {
              method: "PUT",
              body: JSON.stringify({
                qrz_user: userVal,
                ...(passVal && !clearPass ? { qrz_pass: passVal } : {}),
                clear_qrz_pass: clearPass,
              }),
            });
            applySavedPrefs(updated);
            $("prefQrzPass").value = "";
            $("prefQrzPass").placeholder = updated.qrz_pass_set
              ? "•••••••• (saved — leave blank to keep)"
              : "Enter QRZ password";
            resetSecretInputs();
          } catch (err) {
            banner.className = "qrz-test-banner fail";
            banner.textContent = `Could not save credentials: ${err.message}`;
            return;
          }
        }

        try {
          const res = await api("/api/preferences/test-qrz", { method: "POST" });
          banner.className = `qrz-test-banner ${res.ok ? "ok" : "fail"}`;
          banner.textContent = res.message;
        } catch (err) {
          banner.className = "qrz-test-banner fail";
          banner.textContent = `Test failed: ${err.message}`;
        }
      }

      async function handleTestDigitalNode() {
        const banner = $("digitalTestBanner");
        const transcriptEl = $("digitalTestTranscript");
        banner.className = "qrz-test-banner";
        banner.textContent = "Testing digital node connection...";
        banner.classList.remove("hidden");
        transcriptEl.classList.add("hidden");
        transcriptEl.textContent = "";
        try {
          const bpqPassVal = $("prefDigBpqPass").value;
          const clearBpq = $("prefDigClearBpqPass").checked;
          const updated = await api("/api/preferences", {
            method: "PUT",
            body: JSON.stringify({
              digital_routing: collectDigitalRoutingFromEditor(),
              ...(bpqPassVal && !clearBpq ? { bpq_pass: bpqPassVal } : {}),
              clear_bpq_pass: clearBpq,
            }),
          });
          applySavedPrefs(updated);
          $("prefDigBpqPass").value = "";
          $("prefDigBpqPass").placeholder = updated.digital_routing?.bpq_pass_set
            ? "•••••••• (saved — leave blank to keep)"
            : "Enter BBS Telnet password";
          $("prefDigClearBpqPass").checked = false;
          resetSecretInputs();

          const res = await api("/api/preferences/test-digital-node", { method: "POST" });
          banner.className = `qrz-test-banner ${res.ok ? "ok" : "fail"}`;
          banner.textContent = res.message;
          if (res.transcript) {
            transcriptEl.textContent = res.transcript;
            transcriptEl.classList.remove("hidden");
          }
        } catch (err) {
          banner.className = "qrz-test-banner fail";
          banner.textContent = `Node test failed: ${err.message}`;
        }
      }

      // -----------------------------------------------------------------------
      // Clocks & Elapsed Timer
      // -----------------------------------------------------------------------
      let lastMinuteKey = "";
      function renderIdTimer() {
        const btn = $("btnIdTimer");
        if (!btn) return;
        const s = state.idTimerSeconds;
        const pad = (n) => String(n).padStart(2, "0");
        btn.classList.remove("id-warn", "id-crit", "id-due");
        if (s <= 0) {
          const over = Math.abs(s);
          const mins = Math.floor(over / 60);
          const rem = over % 60;
          btn.textContent = `ID +${pad(mins)}:${pad(rem)}`;
          btn.classList.add("id-due");
        } else {
          const mins = Math.floor(s / 60);
          const rem = s % 60;
          btn.textContent = `ID ${pad(mins)}:${pad(rem)}`;
          if (s <= 30) {
            btn.classList.add("id-crit");
          } else if (s <= 180) {
            btn.classList.add("id-warn");
          }
        }
      }

      function resetIdTimer(quiet = false) {
        state.idTimerSeconds = 600;
        renderIdTimer();
        if (!quiet) toast("FCC 10-minute ID timer reset (10:00)", "success");
      }

      function updateClocks() {
        const now = new Date();
        const pad = (n) => String(n).padStart(2, "0");
        $("clockUtc").textContent = `${pad(now.getUTCHours())}:${pad(now.getUTCMinutes())}:${pad(now.getUTCSeconds())}Z`;
        $("clockDate").textContent = `${now.getUTCFullYear()}-${pad(now.getUTCMonth() + 1)}-${pad(now.getUTCDate())}`;
        $("clockLocal").textContent = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;

        const minKey = `${now.getUTCHours()}:${now.getUTCMinutes()}`;
        if (minKey !== lastMinuteKey) {
          lastMinuteKey = minKey;
          if (state.prefs.panel_layout?.scripts?.visible) renderScriptReader();
          if (state.activeNet && !state.activeNet.ended_at) renderCheckinsTable();
        }

        const net = state.activeNet;
        if (net && net.started_at) {
          $("elapsedWrap").classList.remove("hidden");
          const startMs = Date.parse(net.started_at);
          const endMs = net.ended_at ? Date.parse(net.ended_at) : now.getTime();
          const diffSec = Math.max(0, Math.floor((endMs - startMs) / 1000));
          const hrs = Math.floor(diffSec / 3600);
          const mins = Math.floor((diffSec % 3600) / 60);
          const secs = diffSec % 60;
          $("clockElapsed").textContent = `${pad(hrs)}:${pad(mins)}:${pad(secs)}`;
          $("clockElapsed").style.color = net.ended_at ? "var(--text-muted)" : "var(--accent-emerald-text)";
          if (!net.ended_at) {
            state.idTimerSeconds -= 1;
          }
        } else {
          $("elapsedWrap").classList.add("hidden");
          state.idTimerSeconds = 600;
        }
        renderIdTimer();
      }

      // -----------------------------------------------------------------------
      // Nets Loading & Rendering (including Digital VFO Readout)
      // -----------------------------------------------------------------------
      async function loadNets(selectId = null) {
        state.nets = await api("/api/nets");
        renderNetsList();
        const firstOpen = state.nets.find((n) => !n.ended_at);
        const targetId = selectId ?? (state.activeNet ? state.activeNet.id : (firstOpen ? firstOpen.id : null));
        if (targetId && state.nets.some((n) => n.id === targetId)) {
          await selectNet(targetId);
        } else {
          state.activeNet = null;
          renderActiveNet();
        }
      }

      function openNetsDrawer() {
        $("netsDrawerBackdrop").classList.remove("hidden");
        $("btnToggleSidebar").classList.add("active");
        renderNetsList();
        setTimeout(() => $("netsSearch")?.focus(), 40);
      }

      function closeNetsDrawer() {
        $("netsDrawerBackdrop").classList.add("hidden");
        $("btnToggleSidebar").classList.remove("active");
      }

      function toggleNetsDrawer() {
        if ($("netsDrawerBackdrop").classList.contains("hidden")) openNetsDrawer();
        else closeNetsDrawer();
      }

      function renderNetsList() {
        $("netCountLabel").textContent = String(state.nets.length);
        $("railNetBadge").textContent = String(state.nets.length);
        const container = $("netsList");
        if (!state.nets.length) {
          container.innerHTML = `<div style="padding:18px 10px; text-align:center; color:var(--text-muted); font-size:0.88em;">No nets logged yet.<br/>Click <strong>+ New</strong> to begin.</div>`;
          return;
        }
        const q = (state.netsSearchQuery || "").trim().toLowerCase();
        const visibleNets = q
          ? state.nets.filter((net) => {
              const statusWord = net.ended_at ? "closed" : "open active";
              const hay = `${net.name} ${net.net_type} ${net.frequency} ${net.band} ${net.mode} ${net.ncs_call} ${net.started_at} ${statusWord}`.toLowerCase();
              return hay.includes(q);
            })
          : state.nets;
        if (!visibleNets.length) {
          container.innerHTML = `<div style="padding:18px 10px; text-align:center; color:var(--text-muted); font-size:0.88em;">No net sessions match "${escHtml(q)}".</div>`;
          return;
        }
        container.innerHTML = visibleNets.map((net) => {
          const isSelected = state.activeNet && state.activeNet.id === net.id;
          const isOpen = !net.ended_at;
          const freqInfo = [net.frequency, net.mode].filter(Boolean).join(" ");
          const datePart = (net.started_at || "").slice(0, 10);
          const tClass = netTypeClass(net.net_type);
          const stateBadge = isOpen
            ? `<span class="net-state-badge open" title="Net session is open"><span class="status-dot open"></span>OPEN</span>`
            : `<span class="net-state-badge closed" title="Closed at ${escHtml(net.ended_at)}">CLOSED</span>`;
          return `
            <div class="net-card ${isSelected ? "active" : ""} ${isOpen ? "" : "is-closed"}" data-net-id="${net.id}">
              <div class="net-card-top">
                <span class="net-card-name" title="${escHtml(net.name)}">${escHtml(net.name)}</span>
                ${stateBadge}
              </div>
              <div class="net-card-meta">
                <span class="pill pill-type ${tClass}">${escHtml(net.net_type)}</span>
                <span class="mono">${escHtml(freqInfo || datePart)}</span>
              </div>
              <div class="net-card-counts">
                <span>${net.checkin_count} check-ins</span>
                <span>•</span>
                <span>${datePart}${!isOpen ? ` • Closed ${formatUtcTime(net.ended_at)}` : ""}</span>
                ${net.traffic_count > 0 ? `<span class="pill pill-traffic">⚡ ${net.traffic_count}</span>` : ""}
                ${!isOpen ? `<button type="button" class="btn btn-xs" data-clone-net="${net.id}" style="margin-left:auto;" title="Start a new session copying this net's settings">Start Next</button>` : ""}
              </div>
            </div>
          `;
        }).join("");
      }

      async function selectNet(netId) {
        state.activeNet = await api(`/api/nets/${netId}`);
        state.selectedCheckinId = null;
        autoSelectScriptForNet(state.activeNet);
        renderNetsList();
        renderActiveNet();
        renderScriptReader();
        if (state.prefs.panel_layout?.radiograms?.visible) {
          syncRadiogramsPanel();
        }
        if (state.prefs.panel_layout?.roster?.visible) {
          renderRoster();
        }
        updateClocks();
        focusCallsign();
      }

      function updateVfoDisplay(net) {
        const vfo = $("vfoCluster");
        const rgBadge = $("railRgBadge");
        if (!net) {
          vfo.classList.add("hidden");
          rgBadge.classList.add("hidden");
          return;
        }
        const vfoEnabled = state.prefs.panel_layout?.vfo?.visible !== false;
        vfo.classList.toggle("hidden", !vfoEnabled);
        const freqText = net.frequency
          ? (/mhz|khz|ghz/i.test(net.frequency) ? net.frequency : `${net.frequency} MHz`)
          : "NO FREQ";
        $("vfoFreq").textContent = freqText;
        $("vfoBand").textContent = net.band || "--";
        $("vfoMode").textContent = net.mode || "FM";
        const toneEl = $("vfoTone");
        if (net.tone_offset) {
          toneEl.textContent = net.tone_offset;
          toneEl.classList.remove("hidden");
        } else {
          toneEl.classList.add("hidden");
        }
        const isOpen = !net.ended_at;
        const beacon = $("vfoOnAir");
        beacon.textContent = isOpen ? "● ON AIR" : "● SECURED";
        beacon.classList.toggle("closed", !isOpen);

        const rgCount = net.radiogram_count || 0;
        rgBadge.textContent = String(rgCount);
        rgBadge.classList.toggle("hidden", rgCount === 0);
      }

      function renderActiveNet() {
        const net = state.activeNet;
        updateVfoDisplay(net);
        if (!net) {
          $("emptyWorkspace").classList.remove("hidden");
          $("activeNetWorkspace").classList.add("hidden");
          syncSkywarnAutoPoll();
          renderScriptReader();
          return;
        }
        $("emptyWorkspace").classList.add("hidden");
        $("activeNetWorkspace").classList.remove("hidden");

        const isOpen = !net.ended_at;
        $("netStatusDot").className = `status-dot ${isOpen ? "open" : "closed"}`;
        $("netTitle").textContent = net.name;
        const typePill = $("netTypePill");
        typePill.textContent = net.net_type;
        typePill.className = `pill pill-type ${netTypeClass(net.net_type)}`;

        const closedPill = $("netClosedPill");
        closedPill.textContent = isOpen ? "OPEN" : "CLOSED";
        closedPill.style.background = isOpen ? "var(--accent-emerald-bg)" : "var(--kbd-bg)";
        closedPill.style.color = isOpen ? "var(--accent-emerald-text)" : "var(--text-muted)";

        $("btnCloseReopenNet").textContent = isOpen ? "Close Net" : "Reopen Net";
        $("btnCloseReopenNet").className = isOpen ? "btn btn-amber btn-sm" : "btn btn-success btn-sm";
        $("checkinBar").classList.toggle("net-closed", !isOpen);
        $("netRgCount").textContent = String(net.radiogram_count || 0);
        const isSkywarn = `${net.net_type || ""} ${net.name || ""}`.toLowerCase().includes("skywarn");
        $("btnNwsReport").classList.toggle("hidden", !isSkywarn);
        const isRaces = `${net.net_type || ""} ${net.name || ""}`.toLowerCase().includes("races");
        $("inRacesIdField")?.classList.toggle("hidden", !isRaces);
        document.querySelector(".checkin-grid")?.classList.toggle("has-races", isRaces);
        syncSkywarnAutoPoll();
        const roundNum = state.rollRoundByNet.get(net.id) || 1;
        $("rollRoundLabel").textContent = `Rd ${roundNum}`;

        const specs = [
          net.ncs_call ? `<span class="net-spec-item">NCS: <strong class="mono">${escHtml(net.ncs_call)}</strong></span>` : "",
          net.frequency ? `<span class="net-spec-item">Freq: <strong class="mono">${escHtml(net.frequency)}</strong></span>` : "",
          net.band ? `<span class="net-spec-item">Band: <strong class="mono">${escHtml(net.band)}</strong></span>` : "",
          net.mode ? `<span class="net-spec-item">Mode: <strong class="mono">${escHtml(net.mode)}</strong></span>` : "",
          net.tone_offset ? `<span class="net-spec-item">Tone/Offset: <strong>${escHtml(net.tone_offset)}</strong></span>` : "",
          `<span class="net-spec-item">Started: <strong class="mono">${escHtml(net.started_at.replace("T", " "))}</strong></span>`,
          net.ended_at ? `<span class="net-spec-item">Ended: <strong class="mono">${escHtml(net.ended_at.replace("T", " "))}</strong></span>` : "",
        ].filter(Boolean);
        $("netSpecs").innerHTML = specs.join("");

        if (net.notes) {
          $("netNotesBar").textContent = net.notes;
          $("netNotesBar").classList.remove("hidden");
        } else {
          $("netNotesBar").classList.add("hidden");
        }

        renderCheckinsTable();
        checkDuplicateWarning();
      }

      function getFilteredCheckins() {
        const net = state.activeNet;
        if (!net || !net.checkins) return [];
        const q = state.searchQuery.trim().toLowerCase();
        return net.checkins.filter((c) => {
          if (state.filter === "ACTIVE" && c.status === "CHECKED_OUT") return false;
          if (state.filter === "TRAFFIC" && !c.has_traffic) return false;
          if (state.filter === "LIAISON" && c.status !== "LIAISON") return false;
          if (state.filter === "FIELD" && c.status !== "MOBILE" && c.status !== "PORTABLE") return false;
          if (state.filter === "CHECKED_OUT" && c.status !== "CHECKED_OUT") return false;
          if (q) {
            const hay = `${c.callsign} ${c.name} ${c.qth} ${c.grid} ${c.county} ${c.comments} ${c.status}`.toLowerCase();
            if (!hay.includes(q)) return false;
          }
          return true;
        });
      }

      function renderCheckinsTable(flashId = null) {
        const net = state.activeNet;
        if (!net) return;
        const all = net.checkins || [];
        const activeCount = all.filter((c) => c.status !== "CHECKED_OUT").length;
        const trafficCount = all.filter((c) => c.has_traffic).length;
        const outCount = all.length - activeCount;

        $("statTotal").textContent = String(all.length);
        $("statActive").textContent = String(activeCount);
        $("statTraffic").textContent = String(trafficCount);
        $("statOut").textContent = String(outCount);
        $("statTrafficChip").classList.toggle("has-traffic", trafficCount > 0);

        const sidebarNet = state.nets.find((n) => n.id === net.id);
        if (sidebarNet) {
          sidebarNet.checkin_count = all.length;
          sidebarNet.active_count = activeCount;
          sidebarNet.traffic_count = trafficCount;
          renderNetsList();
        }
        renderScriptReader();

        const filtered = getFilteredCheckins();
        const tbody = $("checkinsTbody");
        if (!filtered.length) {
          tbody.innerHTML = "";
          $("tableEmpty").classList.remove("hidden");
          return;
        }
        $("tableEmpty").classList.add("hidden");

        const rollSet = state.rollCalled.get(net.id) || new Set();
        const indexMap = new Map(all.map((c, idx) => [c.id, idx + 1]));
        const nowMs = Date.now();
        const isNetOpen = !net.ended_at;

        tbody.innerHTML = filtered.map((c) => {
          const num = indexMap.get(c.id) || 0;
          const isSelected = state.selectedCheckinId === c.id;
          const isOut = c.status === "CHECKED_OUT";
          const isRolled = rollSet.has(c.id);
          const classes = [
            isSelected ? "selected" : "",
            isOut ? "checked-out" : "",
            c.has_traffic ? "row-traffic" : "",
            flashId === c.id ? "row-flash" : "",
          ].filter(Boolean).join(" ");

          const heardIso = c.last_heard_at || c.checked_in_at;
          let heardHtml = "";
          if (isNetOpen && !isOut && heardIso) {
            const ageMin = Math.max(0, Math.floor((nowMs - Date.parse(heardIso)) / 60000));
            if (ageMin >= 15) {
              heardHtml = `<span class="heard-age heard-stale" title="Last heard ${ageMin}m ago (${escHtml(heardIso)})">⏱ ${ageMin}m</span>`;
            }
          }
          const spotterHtml = c.spotter_id
            ? `<span class="spotter-pill" title="Skywarn Spotter ID">${escHtml(c.spotter_id)}</span>`
            : "";
          const racesHtml = c.races_id
            ? `<span class="races-pill" title="RACES ID">RACES ${escHtml(c.races_id)}</span>`
            : "";

          return `
            <tr class="${classes}" data-checkin-id="${c.id}">
              <td class="col-roll">
                <input type="checkbox" class="roll-check" data-action="roll" ${isRolled ? "checked" : ""} aria-label="Mark ${escHtml(c.callsign)} roll-call heard" title="Mark called in this round &amp; update last-heard time (R)" />
              </td>
              <td class="col-num mono">${num}</td>
              <td class="col-time mono" title="Checked in: ${escHtml(c.checked_in_at)}">${formatUtcTime(c.checked_in_at)}${heardHtml}</td>
              <td class="col-call call-cell">${escHtml(c.callsign)}${spotterHtml}${racesHtml}</td>
              <td class="col-name" title="${escHtml(c.name)}">${escHtml(c.name || "—")}</td>
              <td class="col-qth" title="${escHtml(c.qth)}">
                ${escHtml(c.qth || "—")}${c.grid ? ` <span class="grid-sub">${escHtml(c.grid)}</span>` : ""}
              </td>
              <td class="col-status">
                <span class="status-badge status-${c.status}" data-action="cycle-status" title="Click to cycle status">
                  ${STATUS_LABELS[c.status] || c.status}
                </span>
              </td>
              <td class="col-traffic">
                <button type="button" class="traffic-pill-btn ${c.has_traffic ? "on" : ""}" data-action="toggle-traffic" title="Toggle traffic flag (T)">
                  ${c.has_traffic ? "⚡ TFC" : "None"}
                </button>
              </td>
              <td class="col-sig mono">${escHtml(c.signal_report)}</td>
              <td class="col-comments comment-cell-clickable" data-action="quick-note" title="${escHtml(c.comments || "Click or press M to add weather report / message")}">
                ${c.comments ? escHtml(c.comments) : `<span style="color:var(--text-muted); font-size:0.86em;">+ note / WX...</span>`}
              </td>
              <td class="col-actions">
                <div class="row-actions">
                  <button type="button" class="btn btn-ghost btn-xs" data-action="quick-note" title="Add weather report / message (M)">Note</button>
                  <button type="button" class="btn btn-ghost btn-xs" data-action="toggle-out" title="${isOut ? "Return to Active (O)" : "Check Out (O)"}">
                    ${isOut ? "In" : "Out"}
                  </button>
                  <button type="button" class="btn btn-ghost btn-xs" data-action="edit" title="Edit check-in (E)">Edit</button>
                  <button type="button" class="btn btn-ghost btn-xs" data-action="delete" title="Delete check-in (Del)" style="color:var(--accent-rose-text);">✕</button>
                </div>
              </td>
            </tr>
          `;
        }).join("");
      }

      // -----------------------------------------------------------------------
      // Rapid Check-In Entry & Instant Callsign Lookup on Focus Change
      // -----------------------------------------------------------------------
      function focusCallsign() {
        const el = $("inCallsign");
        if (el && !el.disabled) {
          el.focus();
          el.select();
        }
      }

      function clearCheckinEntry() {
        $("inCallsign").value = "";
        $("inName").value = "";
        $("inQth").value = "";
        $("inGrid").value = "";
        $("inCounty").value = "";
        $("inState").value = "";
        $("inRacesId").value = "";
        $("inStatus").value = "ACTIVE";
        $("inSignal").value = "";
        $("inComments").value = "";
        setEntryTraffic(false);
        $("lookupStatus").textContent = "";
        $("lookupStatus").className = "lookup-status";
        state.lastLookedUpCall = "";
        state.activeLookupCall = "";
        state.activeLookupPromise = null;
        state.autoFilled = { name: "", qth: "", grid: "" };
        state.jumpToStatusOnLookup = false;
        hideAutocomplete();
        $("dupBanner").classList.add("hidden");
      }

      function setEntryTraffic(on) {
        state.entryTraffic = Boolean(on);
        const btn = $("btnTrafficToggle");
        btn.classList.toggle("active", state.entryTraffic);
        btn.setAttribute("aria-pressed", String(state.entryTraffic));
      }

      function checkDuplicateWarning() {
        const call = $("inCallsign").value.trim().toUpperCase();
        const banner = $("dupBanner");
        if (!call || !state.activeNet) {
          banner.classList.add("hidden");
          return;
        }
        const existing = (state.activeNet.checkins || []).find((c) => c.callsign === call);
        if (existing) {
          $("dupBannerText").textContent = `${call} is already checked in (${formatUtcTime(existing.checked_in_at)} • ${STATUS_LABELS[existing.status]}). You can still log a re-check-in or highlight the existing entry.`;
          $("btnJumpDup").dataset.targetId = String(existing.id);
          banner.classList.remove("hidden");
        } else {
          banner.classList.add("hidden");
        }
      }

      async function performEntryLookup(forceRefresh = false) {
        const call = $("inCallsign").value.trim().toUpperCase();
        if (call.length < 3) return null;
        if (!forceRefresh && state.activeLookupPromise && state.activeLookupCall === call) {
          return state.activeLookupPromise;
        }
        if (!forceRefresh && call === state.lastLookedUpCall) return null;

        state.lastLookedUpCall = call;
        state.activeLookupCall = call;
        const seq = ++state.lookupSeq;
        const statusEl = $("lookupStatus");
        statusEl.textContent = "Looking up...";
        statusEl.className = "lookup-status";

        const run = (async () => {
          try {
            const data = await api(`/api/lookup/${encodeURIComponent(call)}?refresh=${forceRefresh ? "true" : "false"}`);
            if (seq !== state.lookupSeq) return null;
            if ($("inCallsign").value.trim().toUpperCase() !== call) return null;

            if (data.found) {
              const nameVal = data.display_name || data.name || "";
              const qthVal = data.qth || "";
              const gridVal = data.grid || "";
              const canFill = (el, prevAuto) => {
                const cur = el.value.trim();
                return forceRefresh || !cur || (prevAuto && cur === prevAuto);
              };
              if (canFill($("inName"), state.autoFilled.name)) $("inName").value = nameVal;
              if (canFill($("inQth"), state.autoFilled.qth)) $("inQth").value = qthVal;
              if (canFill($("inGrid"), state.autoFilled.grid)) $("inGrid").value = gridVal;
              state.autoFilled = { name: nameVal, qth: qthVal, grid: gridVal };
              $("inCounty").value = data.county || "";
              $("inState").value = data.state || "";
              if (data.races_id && !$("inRacesId").value.trim()) {
                $("inRacesId").value = data.races_id;
              }

              const srcLabel = data.cached ? `Cached (${data.source})` : data.source.toUpperCase();
              statusEl.textContent = srcLabel;
              statusEl.className = "lookup-status ok";

              if (state.jumpToStatusOnLookup && (nameVal || qthVal || gridVal)) {
                state.jumpToStatusOnLookup = false;
                if (document.activeElement === $("inName") || document.activeElement === $("btnManualLookup")) {
                  $("inStatus").focus();
                }
              }
            } else {
              state.jumpToStatusOnLookup = false;
              statusEl.textContent = "Not found";
              statusEl.className = "lookup-status warn";
            }
            return data;
          } catch {
            state.jumpToStatusOnLookup = false;
            if (seq === state.lookupSeq) {
              statusEl.textContent = "Offline";
              statusEl.className = "lookup-status warn";
            }
            return null;
          } finally {
            if (seq === state.lookupSeq) {
              state.activeLookupPromise = null;
              state.activeLookupCall = "";
            }
          }
        })();

        state.activeLookupPromise = run;
        return run;
      }

      let acTimer = null;
      function scheduleAutocomplete() {
        clearTimeout(acTimer);
        const val = $("inCallsign").value.trim();
        if (val.length < 2) {
          hideAutocomplete();
          return;
        }
        acTimer = setTimeout(async () => {
          try {
            const matches = await api(`/api/operators?q=${encodeURIComponent(val)}&limit=8`);
            if ($("inCallsign").value.trim() !== val) return;
            state.autocompleteItems = matches;
            state.autocompleteIdx = -1;
            renderAutocomplete();
          } catch {
            hideAutocomplete();
          }
        }, 90);
      }

      function renderAutocomplete() {
        const menu = $("callAutocomplete");
        if (!state.autocompleteItems.length) {
          menu.classList.add("hidden");
          return;
        }
        menu.innerHTML = state.autocompleteItems.map((op, idx) => {
          const displayName = op.preferred_name || op.name || "";
          const meta = [displayName, op.qth, op.grid].filter(Boolean).join(" • ");
          return `
            <div class="autocomplete-item ${idx === state.autocompleteIdx ? "active" : ""}" data-ac-idx="${idx}">
              <span class="autocomplete-call">${escHtml(op.callsign)}</span>
              <span class="autocomplete-sub">${escHtml(meta || op.notes || "")}</span>
            </div>
          `;
        }).join("");
        menu.classList.remove("hidden");
      }

      function hideAutocomplete() {
        $("callAutocomplete").classList.add("hidden");
        state.autocompleteItems = [];
        state.autocompleteIdx = -1;
      }

      function applyAutocompleteItem(op) {
        const nameVal = op.preferred_name || op.name || "";
        const qthVal = op.qth || "";
        const gridVal = op.grid || "";
        $("inCallsign").value = op.callsign;
        $("inName").value = nameVal;
        $("inQth").value = qthVal;
        $("inGrid").value = gridVal;
        $("inCounty").value = op.county || "";
        $("inState").value = op.state || "";
        state.lastLookedUpCall = op.callsign;
        state.autoFilled = { name: nameVal, qth: qthVal, grid: gridVal };
        state.jumpToStatusOnLookup = false;
        $("lookupStatus").textContent = "Roster";
        $("lookupStatus").className = "lookup-status ok";
        hideAutocomplete();
        checkDuplicateWarning();
        $("inStatus").focus();
      }

      async function handleCheckinSubmit(e) {
        e.preventDefault();
        if (!state.activeNet || state.submittingCheckin) return;

        const callsign = $("inCallsign").value.trim().toUpperCase();
        if (!callsign) {
          focusCallsign();
          return;
        }

        state.submittingCheckin = true;
        const submitBtn = $("btnLogCheckin");
        if (submitBtn) submitBtn.disabled = true;

        try {
          // Ensure lookup always completes before logging, regardless of key used to leave/submit
          if (callsign.length >= 3) {
            await performEntryLookup(false);
          }

          const payload = {
            callsign,
            name: $("inName").value.trim(),
            qth: $("inQth").value.trim(),
            grid: $("inGrid").value.trim().toUpperCase(),
            county: $("inCounty").value.trim(),
            state: $("inState").value.trim().toUpperCase(),
            races_id: $("inRacesId").value.trim().toUpperCase(),
            status: $("inStatus").value,
            has_traffic: state.entryTraffic || $("inStatus").value === "TRAFFIC",
            signal_report: $("inSignal").value.trim(),
            comments: $("inComments").value.trim(),
          };

          const created = await api(`/api/nets/${state.activeNet.id}/checkins`, {
            method: "POST",
            body: JSON.stringify(payload),
          });
          state.activeNet.checkins.push(created);
          state.selectedCheckinId = created.id;
          clearCheckinEntry();
          renderCheckinsTable(created.id);

          const rowEl = document.querySelector(`tr[data-checkin-id="${created.id}"]`);
          if (rowEl) rowEl.scrollIntoView({ block: "nearest" });

          $("footerStatus").textContent = `Logged ${created.callsign} at ${formatUtcTime(created.checked_in_at)}`;
          toast(`Checked in ${created.callsign}`, "success");
          focusCallsign();
        } catch (err) {
          toast(`Check-in error: ${err.message}`, "error");
        } finally {
          state.submittingCheckin = false;
          if (submitBtn) submitBtn.disabled = false;
        }
      }

      // -----------------------------------------------------------------------
      // Check-In Row Actions
      // -----------------------------------------------------------------------
      async function patchCheckin(checkinId, body) {
        const updated = await api(`/api/checkins/${checkinId}`, {
          method: "PATCH",
          body: JSON.stringify(body),
        });
        const idx = state.activeNet.checkins.findIndex((c) => c.id === checkinId);
        if (idx >= 0) state.activeNet.checkins[idx] = updated;
        renderCheckinsTable();
        return updated;
      }

      async function removeCheckin(checkinId) {
        const target = state.activeNet.checkins.find((c) => c.id === checkinId);
        if (!target) return;
        if (!confirm(`Remove ${target.callsign} from this net log?`)) return;
        await api(`/api/checkins/${checkinId}`, { method: "DELETE" });
        state.activeNet.checkins = state.activeNet.checkins.filter((c) => c.id !== checkinId);
        if (state.selectedCheckinId === checkinId) state.selectedCheckinId = null;
        renderCheckinsTable();
        toast(`Removed ${target.callsign}`);
      }

      async function toggleRollCall(checkinId) {
        if (!state.activeNet) return;
        let set = state.rollCalled.get(state.activeNet.id);
        if (!set) {
          set = new Set();
          state.rollCalled.set(state.activeNet.id, set);
        }
        if (set.has(checkinId)) {
          set.delete(checkinId);
          renderCheckinsTable();
        } else {
          set.add(checkinId);
          renderCheckinsTable();
          try {
            const updated = await api(`/api/checkins/${checkinId}/heard`, { method: "POST" });
            const idx = (state.activeNet.checkins || []).findIndex((item) => item.id === checkinId);
            if (idx >= 0) state.activeNet.checkins[idx] = updated;
            renderCheckinsTable();
          } catch {
            // keep local roll mark even if network hiccups
          }
        }
      }

      function jumpToNextUncalled() {
        if (!state.activeNet) return;
        const rollSet = state.rollCalled.get(state.activeNet.id) || new Set();
        const activeRows = (state.activeNet.checkins || []).filter((c) => c.status !== "CHECKED_OUT");
        const nextStation = activeRows.find((c) => !rollSet.has(c.id));
        if (!nextStation) {
          toast("All active stations have been called in this round!", "success");
          return;
        }
        state.selectedCheckinId = nextStation.id;
        renderCheckinsTable(nextStation.id);
        const tr = document.querySelector(`tr[data-checkin-id="${nextStation.id}"]`);
        if (tr) tr.scrollIntoView({ block: "center", behavior: "smooth" });
        toast(`Next uncalled: ${nextStation.callsign}${nextStation.name ? ` (${nextStation.name})` : ""}`);
      }

      function openEditCheckinModal(checkinId) {
        const c = (state.activeNet?.checkins || []).find((item) => item.id === checkinId);
        if (!c) return;
        $("editCheckinId").value = String(c.id);
        $("editCallsign").value = c.callsign;
        $("editName").value = c.name || "";
        $("editQth").value = c.qth || "";
        $("editCounty").value = c.county || "";
        $("editState").value = c.state || "";
        $("editGrid").value = c.grid || "";
        $("editSpotterId").value = c.spotter_id || "";
        $("editRacesId").value = c.races_id || "";
        $("editStatus").value = c.status;
        $("editSignal").value = c.signal_report || "";
        $("editTraffic").checked = Boolean(c.has_traffic);
        $("editComments").value = c.comments || "";
        $("editCheckinBackdrop").classList.remove("hidden");
        $("editName").focus();
      }

      // -----------------------------------------------------------------------
      // Net Modal (Create / Edit + Presets)
      // -----------------------------------------------------------------------
      const PRESETS = {
        club2m: { name: "Weekly 2m Club Net", net_type: "Club VHF/UHF", frequency: "146.880", band: "2m", mode: "FM", tone_offset: "PL 110.9 Hz, -0.600" },
        ares: { name: "County ARES Net", net_type: "ARES", frequency: "147.000", band: "2m", mode: "FM", tone_offset: "PL 100.0 Hz, +0.600" },
        races: { name: "County RACES Net", net_type: "RACES", frequency: "147.000", band: "2m", mode: "FM", tone_offset: "PL 100.0 Hz, +0.600" },
        skywarn: { name: "Skywarn Severe Weather Net", net_type: "Skywarn", frequency: "146.940", band: "2m", mode: "FM", tone_offset: "PL 110.9 Hz, -0.600" },
        nts: { name: "Section NTS Traffic Net", net_type: "NTS", frequency: "3.970", band: "80m", mode: "LSB", tone_offset: "Simplex" },
        hf40: { name: "40m Regional HF Net", net_type: "HF", frequency: "7.255", band: "40m", mode: "LSB", tone_offset: "Simplex" },
        hf80: { name: "80m Evening HF Net", net_type: "HF", frequency: "3.916", band: "80m", mode: "LSB", tone_offset: "Simplex" },
      };

      function getNetTypeDefault(netType) {
        const saved = state.prefs.net_type_defaults && state.prefs.net_type_defaults[netType];
        if (saved && typeof saved === "object") return saved;
        const fallbackMap = {
          "Club VHF/UHF": PRESETS.club2m,
          "ARES": PRESETS.ares,
          "Skywarn": PRESETS.skywarn,
          "NTS": PRESETS.nts,
          "HF": PRESETS.hf40,
          "RACES": PRESETS.ares,
          "Other": { name: "", frequency: "", band: "", mode: "FM", tone_offset: "" },
        };
        return fallbackMap[netType] || { name: "", frequency: "", band: "", mode: "FM", tone_offset: "" };
      }

      function applyNetTypeDefaults(netType, overwriteName = false) {
        const d = getNetTypeDefault(netType);
        if (overwriteName && d.name) $("netFormName").value = d.name;
        $("netFormFreq").value = d.frequency || "";
        $("netFormBand").value = d.band || "";
        $("netFormMode").value = d.mode || "FM";
        $("netFormTone").value = d.tone_offset || "";
      }

      function applyNetPreset(key) {
        const p = PRESETS[key];
        if (!p) return;
        const d = getNetTypeDefault(p.net_type);
        $("netFormName").value = p.name;
        $("netFormType").value = p.net_type;
        if (key === "hf80") {
          $("netFormFreq").value = p.frequency;
          $("netFormBand").value = p.band;
          $("netFormMode").value = p.mode;
          $("netFormTone").value = p.tone_offset;
        } else {
          $("netFormFreq").value = d.frequency || p.frequency;
          $("netFormBand").value = d.band || p.band;
          $("netFormMode").value = d.mode || p.mode;
          $("netFormTone").value = d.tone_offset || p.tone_offset;
        }
      }

      function getOpenNets() {
        return (state.nets || []).filter((n) => !n.ended_at);
      }

      function requestNewNet(presetKey = null, cloneSource = null) {
        const openNets = getOpenNets();
        if (!openNets.length) {
          openNetModal(null, presetKey, cloneSource ? "netFormNcs" : "netFormName", cloneSource);
          return;
        }
        state.pendingNewNetPreset = presetKey;
        state.pendingCloneNet = cloneSource;
        const plural = openNets.length > 1;
        $("openNetWarnLead").textContent = plural
          ? `You currently have ${openNets.length} open net sessions in progress. Close them before starting a new net, or stay in your current net.`
          : "You currently have an open net session in progress. Close it before starting a new net, or stay in your current net.";
        $("btnWarnCloseAndNew").textContent = plural
          ? `Close ${openNets.length} Open Nets & Start New`
          : "Close Open Net & Start New";
        $("openNetWarnList").innerHTML = openNets.map((net) => {
          const spec = [net.net_type, net.frequency, net.mode].filter(Boolean).join(" • ");
          return `
            <div style="display:flex; align-items:center; justify-content:space-between; gap:10px; padding:9px 11px; border-radius:var(--radius-sm); background:var(--bg-input); border:1px solid var(--accent-amber);">
              <div style="min-width:0;">
                <div style="font-weight:700; color:var(--text-primary); display:flex; align-items:center; gap:7px;">
                  <span class="status-dot open"></span>
                  <span>${escHtml(net.name)}</span>
                </div>
                <div class="mono" style="font-size:0.8em; color:var(--text-secondary); margin-top:2px;">
                  ${escHtml(spec || "Open Net")} • ${net.checkin_count || 0} check-ins
                </div>
              </div>
              <span class="pill" style="background:var(--accent-emerald-bg); color:var(--accent-emerald-text);">OPEN</span>
            </div>
          `;
        }).join("");
        $("openNetWarnBackdrop").classList.remove("hidden");
        $("btnWarnCancelNewNet").focus();
      }

      async function closeOpenNetsAndStartNew() {
        const openNets = getOpenNets();
        const preset = state.pendingNewNetPreset;
        const cloneSource = state.pendingCloneNet;
        state.pendingNewNetPreset = null;
        state.pendingCloneNet = null;
        try {
          for (const net of openNets) {
            await api(`/api/nets/${net.id}/close`, { method: "POST" });
          }
          $("openNetWarnBackdrop").classList.add("hidden");
          state.activeNet = null;
          state.nets = await api("/api/nets");
          renderNetsList();
          renderActiveNet();
          const msg = openNets.length === 1
            ? `Closed "${openNets[0].name}"`
            : `Closed ${openNets.length} open nets`;
          toast(msg, "success");
          openNetModal(null, preset, cloneSource ? "netFormNcs" : "netFormName", cloneSource);
        } catch (err) {
          toast(`Could not close open net: ${err.message}`, "error");
        }
      }

      function openNetModal(editNet = null, presetKey = null, focusFieldId = "netFormName", cloneSource = null) {
        state.editingNetId = editNet ? editNet.id : null;
        $("netModalTitle").textContent = editNet
          ? "Edit Net Details"
          : (cloneSource ? "Start Next Session (Review NCS & Details)" : "Start New Net Session");
        $("netFormSubmitBtn").textContent = editNet ? "Save Changes" : "Start Net";
        $("netPresetSection").classList.toggle("hidden", Boolean(editNet));

        if (editNet) {
          $("netFormName").value = editNet.name || "";
          $("netFormType").value = editNet.net_type || "Club VHF/UHF";
          $("netFormNcs").value = editNet.ncs_call || state.config.operator_call;
          $("netFormFreq").value = editNet.frequency || "";
          $("netFormBand").value = editNet.band || "";
          $("netFormMode").value = editNet.mode || "FM";
          $("netFormTone").value = editNet.tone_offset || "";
          $("netFormNotes").value = editNet.notes || "";
        } else if (cloneSource) {
          $("netForm").reset();
          $("netFormName").value = cloneSource.name || "";
          $("netFormType").value = cloneSource.net_type || "Club VHF/UHF";
          $("netFormNcs").value = cloneSource.ncs_call || state.config.operator_call;
          $("netFormFreq").value = cloneSource.frequency || "";
          $("netFormBand").value = cloneSource.band || "";
          $("netFormMode").value = cloneSource.mode || "FM";
          $("netFormTone").value = cloneSource.tone_offset || "";
          $("netFormNotes").value = "";
        } else {
          $("netForm").reset();
          $("netFormNcs").value = state.config.operator_call;
          if (presetKey) {
            applyNetPreset(presetKey);
          } else {
            applyNetTypeDefaults($("netFormType").value || "Club VHF/UHF", false);
          }
        }
        $("netModalBackdrop").classList.remove("hidden");
        const target = $(focusFieldId) || $("netFormName");
        target.focus();
        target.select();
      }

      async function handleNetFormSubmit(e) {
        e.preventDefault();
        const payload = {
          name: $("netFormName").value.trim(),
          net_type: $("netFormType").value,
          ncs_call: $("netFormNcs").value.trim().toUpperCase() || state.config.operator_call,
          frequency: $("netFormFreq").value.trim(),
          band: $("netFormBand").value.trim(),
          mode: $("netFormMode").value,
          tone_offset: $("netFormTone").value.trim(),
          notes: $("netFormNotes").value.trim(),
        };
        try {
          if (state.editingNetId) {
            await api(`/api/nets/${state.editingNetId}`, {
              method: "PATCH",
              body: JSON.stringify(payload),
            });
            closeAllModals();
            await loadNets(state.editingNetId);
            toast("Net updated", "success");
          } else {
            const created = await api("/api/nets", {
              method: "POST",
              body: JSON.stringify(payload),
            });
            closeAllModals();
            await loadNets(created.id);
            resetIdTimer(true);
            toast(`Started net: ${created.name} (NCS ${created.ncs_call} checked in)`, "success");
          }
        } catch (err) {
          toast(`Failed to save net: ${err.message}`, "error");
        }
      }

      // -----------------------------------------------------------------------
      // Exports (Native pywebview Save Dialog + Browser Download Fallback)
      // -----------------------------------------------------------------------
      async function downloadExport(kind) {
        if (!state.activeNet) return;
        const net = state.activeNet;
        const slug = (net.name || "net").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 48) || "net";
        const datePart = (net.started_at || "").slice(0, 10);
        const ext = kind === "adif" ? "adi" : (kind === "csv" ? "csv" : "txt");
        const suffix = kind === "radiograms" ? "_radiograms" : "";
        const filename = `net_${net.id}_${slug}${suffix}_${datePart}.${ext}`;

        try {
          const resp = await fetch(`/api/nets/${net.id}/export/${kind}`);
          if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
          const text = await resp.text();

          if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.save_text_file === "function") {
            const dialogKind = kind === "csv" || kind === "adif" ? kind : "txt";
            const res = await window.pywebview.api.save_text_file(filename, text, dialogKind);
            if (res && res.saved) toast(`Saved ${filename}`, "success");
            else if (res && res.error) throw new Error(res.error);
            return;
          }

          const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url;
          a.download = filename;
          document.body.appendChild(a);
          a.click();
          a.remove();
          URL.revokeObjectURL(url);
          toast(`Exported ${filename}`, "success");
        } catch (err) {
          toast(`Export failed: ${err.message}`, "error");
        }
      }

      function formatDurationShort(startIso, endIso) {
        if (!startIso) return "—";
        const startMs = Date.parse(startIso);
        const endMs = endIso ? Date.parse(endIso) : Date.now();
        if (Number.isNaN(startMs) || Number.isNaN(endMs)) return "—";
        const diffSec = Math.max(0, Math.floor((endMs - startMs) / 1000));
        const hrs = Math.floor(diffSec / 3600);
        const mins = Math.floor((diffSec % 3600) / 60);
        const secs = diffSec % 60;
        if (hrs > 0) return `${hrs}h ${mins}m`;
        if (mins > 0) return `${mins}m ${secs}s`;
        return `${secs}s`;
      }

      async function saveTextContent(filename, text, dialogKind = "txt") {
        if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.save_text_file === "function") {
          const res = await window.pywebview.api.save_text_file(filename, text, dialogKind);
          if (res && res.saved) toast(`Saved ${filename}`, "success");
          else if (res && res.error) throw new Error(res.error);
          return;
        }
        const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
        toast(`Saved ${filename}`, "success");
      }

      async function populateNetReportModal() {
        const net = state.activeNet;
        if (!net) return;

        const isOpen = !net.ended_at;
        $("netReportModalTitle").textContent = `Net Report — ${net.name}`;
        const typePill = $("reportTypePill");
        typePill.textContent = net.net_type || "Net";
        typePill.className = `pill pill-type ${netTypeClass(net.net_type)}`;

        const statusPill = $("reportStatusPill");
        statusPill.textContent = isOpen ? "OPEN" : "CLOSED";
        statusPill.className = `net-state-badge ${isOpen ? "open" : "closed"}`;

        const durationStr = formatDurationShort(net.started_at, net.ended_at);
        const metaParts = [
          `<span>NCS: <strong class="mono">${escHtml(net.ncs_call || state.config.operator_call)}</strong></span>`,
          net.frequency ? `<span>Freq: <strong class="mono">${escHtml(net.frequency)}</strong></span>` : "",
          net.band ? `<span>Band: <strong class="mono">${escHtml(net.band)}</strong></span>` : "",
          net.mode ? `<span>Mode: <strong class="mono">${escHtml(net.mode)}</strong></span>` : "",
          net.tone_offset ? `<span>Tone/Offset: <strong>${escHtml(net.tone_offset)}</strong></span>` : "",
          `<span>Started: <strong class="mono">${escHtml((net.started_at || "").replace("T", " "))}</strong></span>`,
          net.ended_at ? `<span>Ended: <strong class="mono">${escHtml(net.ended_at.replace("T", " "))}</strong></span>` : "",
          `<span>Duration: <strong class="mono">${escHtml(durationStr)}</strong></span>`,
        ].filter(Boolean);
        $("reportMetaStrip").innerHTML = metaParts.join("");

        const checkins = net.checkins || [];
        const uniqueCalls = new Set(checkins.map((c) => c.callsign)).size;
        const activeCount = checkins.filter((c) => c.status !== "CHECKED_OUT").length;
        const outCount = checkins.length - activeCount;
        const trafficCount = checkins.filter((c) => c.has_traffic).length;
        const fieldCount = checkins.filter((c) => ["MOBILE", "PORTABLE", "LIAISON", "RELAY"].includes(c.status)).length;
        const rgCount = net.radiogram_count || 0;
        const opHours = Number(net.operator_hours || 0).toFixed(2);

        $("reportKpiGrid").innerHTML = [
          { label: "Check-Ins", val: checkins.length },
          { label: "Unique Calls", val: uniqueCalls },
          { label: "Active / Out", val: `${activeCount} / ${outCount}` },
          { label: "Traffic", val: trafficCount },
          { label: "Mob / Port / Lia", val: fieldCount },
          { label: "Radiograms", val: rgCount },
          { label: "Operator-Hours", val: `${opHours}h` },
        ].map((k) => `
          <div class="report-kpi-card">
            <span class="report-kpi-label">${escHtml(k.label)}</span>
            <span class="report-kpi-value">${escHtml(String(k.val))}</span>
          </div>
        `).join("");

        const hasNotes = Boolean((net.notes || "").trim());
        $("reportAnnouncementsSection").classList.toggle("hidden", !hasNotes);
        $("reportAnnouncementsBox").textContent = (net.notes || "").trim();

        const stationNotes = checkins
          .filter((c) => {
            const txt = (c.comments || "").trim();
            return c.has_traffic || (txt && txt !== "Net Control Station (NCS)");
          })
          .map((c) => {
            const txt = (c.comments || "").trim();
            const cleanTxt = txt === "Net Control Station (NCS)" ? "" : txt;
            const who = [c.name, c.qth].filter(Boolean).join(" — ");
            const whoStr = who ? ` (${who})` : "";
            const tfc = c.has_traffic ? " [TRAFFIC]" : "";
            return `[${formatUtcTime(c.checked_in_at)}] ${c.callsign}${whoStr}${tfc}: ${cleanTxt || "(Traffic flagged — no text recorded)"}`;
          });
        $("reportStationNotesSection").classList.toggle("hidden", !stationNotes.length);
        $("reportStationNotesBox").textContent = stationNotes.join("\n");

        $("btnReportShare").classList.toggle("hidden", typeof navigator.share !== "function");

        const resp = await fetch(`/api/nets/${net.id}/export/summary`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        $("netReportText").value = await resp.text();
        const dr = state.prefs.digital_routing || {};
        $("btnReportPushWinlink")?.classList.toggle("hidden", !dr.winlink_target_configured);
        $("btnReportPushPacket")?.classList.toggle("hidden", !dr.packet_target_configured);
      }

      async function pushNetReportToTarget(mode) {
        if (!state.activeNet) return;
        const dr = state.prefs.digital_routing || {};
        let dest = (dr.winlink_to_default || "").trim();
        if (!dest) {
          const entered = prompt(
            `Enter ${mode === "winlink" ? "Winlink" : "Packet BBS"} destination address or callsign:`
          );
          if (!entered || !entered.trim()) return;
          dest = entered.trim();
        }
        const btn = mode === "winlink" ? $("btnReportPushWinlink") : $("btnReportPushPacket");
        const origLabel = btn.textContent;
        btn.disabled = true;
        btn.textContent = "Pushing...";
        try {
          const res = await api(`/api/nets/${state.activeNet.id}/push-report`, {
            method: "POST",
            body: JSON.stringify({
              mode,
              to_address: dest,
              body: $("netReportText").value,
            }),
          });
          if (res.ok) {
            toast(res.message || `Pushed net report via ${mode}`, "success");
          } else {
            toast(res.message || `Could not push net report via ${mode}`, "error");
          }
        } catch (err) {
          toast(`Push failed: ${err.message}`, "error");
        } finally {
          btn.disabled = false;
          btn.textContent = origLabel;
        }
      }

      async function openNetReportModal() {
        if (!state.activeNet) return;
        try {
          state.activeNet = await api(`/api/nets/${state.activeNet.id}`);
          await populateNetReportModal();
          $("netReportBackdrop").classList.remove("hidden");
        } catch (err) {
          toast(`Could not load net report: ${err.message}`, "error");
        }
      }

      async function printIcs214Report() {
        if (!state.activeNet) return;
        try {
          const resp = await fetch(`/api/nets/${state.activeNet.id}/export/ics214?format=html`);
          if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
          printHtmlViaFrame(await resp.text());
        } catch (err) {
          toast(`Could not print ICS-214: ${err.message}`, "error");
        }
      }

      async function downloadIcs214Report() {
        if (!state.activeNet) return;
        const net = state.activeNet;
        const slug = (net.name || "net").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 48) || "net";
        const datePart = (net.started_at || "").slice(0, 10);
        try {
          const resp = await fetch(`/api/nets/${net.id}/export/ics214?format=text`);
          if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
          await saveTextContent(`net_${net.id}_${slug}_ics214_${datePart}.txt`, await resp.text(), "txt");
        } catch (err) {
          toast(`ICS-214 export failed: ${err.message}`, "error");
        }
      }

      // -----------------------------------------------------------------------
      // Operator Roster Panel
      // -----------------------------------------------------------------------
      async function loadRoster(query = "") {
        state.roster = await api(`/api/operators?q=${encodeURIComponent(query)}&limit=200`);
        renderRoster();
      }

      function renderRoster() {
        const tbody = $("rosterTbody");
        if (!state.roster.length) {
          tbody.innerHTML = `<tr><td colspan="5" style="text-align:center; padding:24px; color:var(--text-muted);">No operators in local roster yet.</td></tr>`;
          return;
        }
        tbody.innerHTML = state.roster.map((op) => {
          const displayName = op.preferred_name || op.name || "—";
          const noteTip = op.notes ? ` — ${op.notes}` : "";
          const spotterBadge = op.spotter_id ? `<span class="spotter-pill">${escHtml(op.spotter_id)}</span>` : "";
          const racesBadge = op.races_id ? `<span class="races-pill">RACES ${escHtml(op.races_id)}</span>` : "";
          return `
            <tr data-op-call="${escHtml(op.callsign)}" title="${escHtml((op.name || "") + noteTip)}">
              <td class="call-cell">${escHtml(op.callsign)}${spotterBadge}${racesBadge}</td>
              <td title="${escHtml(op.name || "")}">
                ${escHtml(displayName)}${op.notes ? `<div style="font-size:0.78em; color:var(--text-muted); overflow:hidden; text-overflow:ellipsis;">${escHtml(op.notes)}</div>` : ""}
              </td>
              <td title="${escHtml(op.qth)}">${escHtml(op.qth || "—")}${op.grid ? ` <span class="grid-sub">${escHtml(op.grid)}</span>` : ""}</td>
              <td class="mono">${op.checkin_count || 0}</td>
              <td style="text-align:right;">
                ${state.activeNet ? `<button type="button" class="btn btn-success btn-xs" data-roster-action="checkin" title="Check into active net">+ In</button>` : ""}
                <button type="button" class="btn btn-ghost btn-xs" data-roster-action="edit">Edit</button>
                <button type="button" class="btn btn-ghost btn-xs" data-roster-action="delete" style="color:var(--accent-rose-text);">✕</button>
              </td>
            </tr>
          `;
        }).join("");
      }

      function openRosterForm(op = null) {
        $("rosterEditForm").classList.remove("hidden");
        $("ropCall").value = op ? op.callsign : "";
        $("ropCall").readOnly = Boolean(op);
        $("ropName").value = op ? (op.name || "") : "";
        $("ropPrefName").value = op ? (op.preferred_name || "") : "";
        $("ropQth").value = op ? (op.qth || "") : "";
        $("ropGrid").value = op ? (op.grid || "") : "";
        $("ropSpotterId").value = op ? (op.spotter_id || "") : "";
        $("ropRacesId").value = op ? (op.races_id || "") : "";
        $("ropNotes").value = op ? (op.notes || "") : "";
        (op ? $("ropPrefName") : $("ropCall")).focus();
      }

      // -----------------------------------------------------------------------
      // Quick Station Comments / Weather Report Modal
      // -----------------------------------------------------------------------
      function currentUtcStamp() {
        const now = new Date();
        const pad = (n) => String(n).padStart(2, "0");
        return `${pad(now.getUTCHours())}:${pad(now.getUTCMinutes())}Z`;
      }

      function currentUtcCompact() {
        const now = new Date();
        const pad = (n) => String(n).padStart(2, "0");
        return `${pad(now.getUTCHours())}${pad(now.getUTCMinutes())}Z`;
      }

      function currentUtcDateShort() {
        const months = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
        const now = new Date();
        return `${months[now.getUTCMonth()]} ${String(now.getUTCDate()).padStart(2, "0")}`;
      }

      function openQuickNoteModal(checkinId) {
        const c = (state.activeNet?.checkins || []).find((item) => item.id === checkinId);
        if (!c) return;
        $("quickNoteCheckinId").value = String(c.id);
        $("quickNoteTitle").textContent = `${c.callsign}${c.name ? ` (${c.name})` : ""} — Comments / WX Report`;
        $("quickNoteText").value = c.comments || "";
        $("quickNoteTraffic").checked = Boolean(c.has_traffic);
        $("quickNoteBackdrop").classList.remove("hidden");
        const ta = $("quickNoteText");
        ta.focus();
        ta.setSelectionRange(ta.value.length, ta.value.length);
      }

      // -----------------------------------------------------------------------
      // ARRL Radiograms (NTS Formal Traffic)
      // -----------------------------------------------------------------------
      function normalizeArrlHx(raw) {
        const cleaned = (raw || "").trim().toUpperCase();
        if (!cleaned) return "";
        return cleaned.split(/\s+/).map((tok) => (/^[A-G]\d*$/.test(tok) ? `HX${tok}` : tok)).join(" ");
      }

      const ARL_NUMBER_WORDS = new Set([
        "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE", "TEN",
        "ELEVEN", "TWELVE", "THIRTEEN", "FOURTEEN", "FIFTEEN", "SIXTEEN", "SEVENTEEN",
        "EIGHTEEN", "NINETEEN", "TWENTY", "THIRTY", "FORTY", "FIFTY", "SIXTY", "SEVENTY",
      ]);

      function computeArrlCheckLocal(text) {
        const tokens = (text || "").trim().split(/\s+/).filter(Boolean);
        const count = tokens.length;
        const upper = tokens.map((t) => t.toUpperCase());
        const hasArlNumbered = upper.some(
          (t, i) => t === "ARL" && i + 1 < upper.length && (ARL_NUMBER_WORDS.has(upper[i + 1]) || /^\d+$/.test(upper[i + 1]))
        );
        return { count, label: hasArlNumbered && count > 0 ? `ARL ${count}` : String(count) };
      }

      function formatRadiogramPreviewFromForm() {
        const text = $("rgMessageText").value || "";
        const ck = computeArrlCheckLocal(text);
        $("rgCheckPreview").textContent = ck.label;
        $("rgWordWarn").classList.toggle("hidden", ck.count <= 25);

        const preamble = [
          `NR ${($("rgNumber").value.trim() || "AUTO").toUpperCase()}`,
          ($("rgPrecedence").value || "R").toUpperCase(),
          normalizeArrlHx($("rgHx").value),
          $("rgOrigin").value.trim().toUpperCase() || state.config.operator_call || "W5CDN",
          ck.label.toUpperCase(),
          $("rgPlace").value.trim().toUpperCase(),
          $("rgTimeFiled").value.trim().toUpperCase(),
          $("rgDateFiled").value.trim().toUpperCase(),
        ].filter(Boolean).join(" ");

        const lines = [preamble];
        const addUpper = (v) => {
          const s = (v || "").trim().toUpperCase();
          if (s) lines.push(s);
        };
        addUpper($("rgToName").value);
        addUpper($("rgToAddress").value);
        addUpper($("rgToCity").value);
        addUpper($("rgToPhone").value);
        addUpper($("rgToEmail").value);

        lines.push("BT");
        const tokens = text.trim().split(/\s+/).filter(Boolean);
        for (let i = 0; i < tokens.length; i += 5) {
          lines.push(tokens.slice(i, i + 5).join(" ").toUpperCase());
        }
        lines.push("BT");
        addUpper($("rgSignature").value);
        lines.push("AR");

        const rcvd = $("rgRcvdFrom").value.trim().toUpperCase();
        const sent = $("rgSentTo").value.trim().toUpperCase();
        if (rcvd || sent) {
          lines.push("");
          if (rcvd) lines.push(`RCVD: ${rcvd} ${$("rgRcvdAt").value.trim().toUpperCase()}`.trim());
          if (sent) lines.push(`SENT: ${sent} ${$("rgSentAt").value.trim().toUpperCase()}`.trim());
        }
        const formatted = lines.join("\n").toUpperCase();
        $("rgOfficialPreview").textContent = formatted;
        return formatted;
      }

      async function loadRadiograms() {
        const params = new URLSearchParams();
        if (state.rgScope === "net" && state.activeNet) {
          params.set("net_id", String(state.activeNet.id));
        }
        const st = $("rgFilterStatus").value;
        if (st) params.set("status", st);
        const qs = params.toString();
        state.radiograms = await api(`/api/radiograms${qs ? `?${qs}` : ""}`);
        renderRadiogramsList();
      }

      function getRadiogramDataFromForm() {
        const msgText = ($("rgMessageText").value || "").trim().toUpperCase();
        const ck = computeArrlCheckLocal(msgText);
        return {
          msg_number: ($("rgNumber").value.trim() || "AUTO").toUpperCase(),
          precedence: ($("rgPrecedence").value || "R").toUpperCase(),
          hx: normalizeArrlHx($("rgHx").value),
          station_of_origin: ($("rgOrigin").value.trim() || state.config.operator_call || "W5CDN").toUpperCase(),
          check_count: ck.count,
          check_text: ck.label.toUpperCase(),
          place_of_origin: $("rgPlace").value.trim().toUpperCase(),
          time_filed: $("rgTimeFiled").value.trim().toUpperCase(),
          date_filed: $("rgDateFiled").value.trim().toUpperCase(),
          to_name: $("rgToName").value.trim().toUpperCase(),
          to_address: $("rgToAddress").value.trim().toUpperCase(),
          to_city_state_zip: $("rgToCity").value.trim().toUpperCase(),
          to_phone: $("rgToPhone").value.trim().toUpperCase(),
          to_email: $("rgToEmail").value.trim().toUpperCase(),
          message_text: msgText,
          signature: $("rgSignature").value.trim().toUpperCase(),
          status: ($("rgStatus").value || "LISTED").toUpperCase(),
          received_from: $("rgRcvdFrom").value.trim().toUpperCase(),
          received_at: $("rgRcvdAt").value.trim().toUpperCase(),
          sent_to: $("rgSentTo").value.trim().toUpperCase(),
          sent_at: $("rgSentAt").value.trim().toUpperCase(),
          notes: $("rgNotes").value.trim().toUpperCase(),
        };
      }

      function buildArrlRadiogramPrintHtml(rg) {
        const up = (v) => escHtml(String(v ?? "").trim().toUpperCase());
        const hxVal = normalizeArrlHx(rg.hx || "");
        const msgText = String(rg.message_text || "").trim().toUpperCase();
        const tokens = msgText ? msgText.split(/\s+/).filter(Boolean) : [];
        const ck = rg.check_text ? String(rg.check_text).toUpperCase() : computeArrlCheckLocal(msgText).label.toUpperCase();
        const totalSlots = Math.max(25, Math.ceil(tokens.length / 5) * 5);

        const gridRows = [];
        for (let i = 0; i < totalSlots; i += 5) {
          const cells = [];
          for (let j = 0; j < 5; j++) {
            const idx = i + j;
            const word = tokens[idx] ? escHtml(tokens[idx]) : "&nbsp;";
            cells.push(
              `<td class="word-cell"><span class="word-num">${idx + 1}</span><span class="word-val">${word}</span></td>`
            );
          }
          gridRows.push(`<tr>${cells.join("")}</tr>`);
        }

        const precNames = {
          R: "R (ROUTINE)",
          W: "W (HEALTH &amp; WELFARE)",
          P: "P (PRIORITY)",
          EMERGENCY: "EMERGENCY",
        };
        const precDisplay = precNames[String(rg.precedence || "R").toUpperCase()] || up(rg.precedence || "R");

        return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>ARRL RADIOGRAM NR ${up(rg.msg_number || "1")}</title>
  <style>
    @page { size: letter portrait; margin: 0.45in; }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      padding: 18px;
      background: #ffffff;
      color: #000000;
      font-family: "Segoe UI", Arial, Helvetica, sans-serif;
      font-size: 12px;
      line-height: 1.3;
      -webkit-print-color-adjust: exact;
      print-color-adjust: exact;
    }
    .sheet {
      max-width: 7.6in;
      margin: 0 auto;
      border: 2.5px solid #000000;
      padding: 14px 16px 12px;
      background: #ffffff;
    }
    .masthead {
      text-align: center;
      border-bottom: 3px double #000000;
      padding-bottom: 8px;
      margin-bottom: 10px;
    }
    .masthead-org {
      font-size: 9.5px;
      font-weight: 700;
      letter-spacing: 0.16em;
      text-transform: uppercase;
    }
    .masthead-title {
      font-size: 28px;
      font-weight: 900;
      letter-spacing: 0.28em;
      margin: 2px 0;
      line-height: 1.05;
    }
    .masthead-sub {
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.14em;
      text-transform: uppercase;
    }
    table.preamble {
      width: 100%;
      border-collapse: collapse;
      margin-bottom: 10px;
      table-layout: fixed;
    }
    table.preamble th,
    table.preamble td {
      border: 1.25px solid #000000;
      padding: 3px 5px;
      text-align: center;
      vertical-align: middle;
    }
    table.preamble th {
      background: #ececec;
      font-size: 8px;
      font-weight: 800;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }
    table.preamble td {
      font-family: Consolas, "Courier New", monospace;
      font-size: 12.5px;
      font-weight: 800;
      min-height: 24px;
      height: 26px;
      word-break: break-word;
    }
    .address-grid {
      display: grid;
      grid-template-columns: 1.35fr 0.65fr;
      gap: 12px;
      margin-bottom: 10px;
      align-items: stretch;
    }
    .to-box {
      border: 1.25px solid #000000;
      padding: 6px 9px;
    }
    .box-label {
      font-size: 8.5px;
      font-weight: 800;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      margin-bottom: 4px;
    }
    .to-line {
      display: flex;
      align-items: baseline;
      gap: 6px;
      border-bottom: 1px solid #444444;
      padding: 3px 2px 2px;
      min-height: 20px;
      font-family: Consolas, "Courier New", monospace;
      font-size: 12px;
      font-weight: 700;
    }
    .to-line:last-child { border-bottom: none; }
    .to-tag {
      font-family: "Segoe UI", Arial, sans-serif;
      font-size: 8px;
      font-weight: 700;
      color: #333333;
      min-width: 58px;
      text-transform: uppercase;
    }
    .notice-box {
      border: 1.25px solid #000000;
      padding: 7px 9px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      background: #f8f8f8;
    }
    .notice-text {
      font-size: 8.5px;
      line-height: 1.38;
      text-transform: uppercase;
    }
    .status-pill {
      margin-top: 6px;
      padding: 3px 6px;
      border: 1px solid #000000;
      background: #ffffff;
      font-family: Consolas, "Courier New", monospace;
      font-size: 10px;
      font-weight: 800;
      text-align: center;
    }
    .bt-divider {
      display: flex;
      align-items: center;
      gap: 8px;
      margin: 6px 0;
      font-family: Consolas, "Courier New", monospace;
      font-weight: 900;
      font-size: 11px;
      letter-spacing: 0.14em;
    }
    .bt-divider::before,
    .bt-divider::after {
      content: "";
      flex: 1;
      border-top: 1.5px solid #000000;
    }
    table.msg-grid {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
      margin-bottom: 6px;
    }
    table.msg-grid td.word-cell {
      border: 1px solid #000000;
      height: 30px;
      padding: 9px 5px 3px;
      position: relative;
      text-align: center;
      vertical-align: bottom;
      font-family: Consolas, "Courier New", monospace;
      font-size: 12.5px;
      font-weight: 800;
      word-break: break-word;
    }
    .word-num {
      position: absolute;
      top: 1px;
      left: 3px;
      font-family: "Segoe UI", Arial, sans-serif;
      font-size: 7.5px;
      font-weight: 700;
      color: #555555;
    }
    .sig-row {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 10px;
      border: 1.25px solid #000000;
      padding: 6px 10px;
      margin-bottom: 10px;
    }
    .sig-val {
      flex: 1;
      font-family: Consolas, "Courier New", monospace;
      font-size: 13px;
      font-weight: 800;
      border-bottom: 1px solid #444444;
      padding-bottom: 1px;
      min-height: 18px;
    }
    .ar-mark {
      font-family: Consolas, "Courier New", monospace;
      font-weight: 900;
      font-size: 12px;
      letter-spacing: 0.1em;
    }
    table.relay-log {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }
    table.relay-log th,
    table.relay-log td {
      border: 1.25px solid #000000;
      padding: 4px 7px;
      text-align: left;
    }
    table.relay-log th {
      background: #ececec;
      font-size: 8.5px;
      font-weight: 800;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }
    table.relay-log td {
      font-family: Consolas, "Courier New", monospace;
      font-size: 11.5px;
      font-weight: 700;
      height: 24px;
    }
    .notes-strip {
      margin-top: 8px;
      border: 1px dashed #333333;
      padding: 5px 8px;
      font-family: Consolas, "Courier New", monospace;
      font-size: 10.5px;
    }
    .footer-note {
      margin-top: 8px;
      display: flex;
      justify-content: space-between;
      font-size: 8.5px;
      color: #333333;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    @media print {
      body { padding: 0; }
    }
  </style>
</head>
<body>
  <div class="sheet">
    <div class="masthead">
      <div class="masthead-org">THE AMERICAN RADIO RELAY LEAGUE &bull; NATIONAL TRAFFIC SYSTEM</div>
      <div class="masthead-title">RADIOGRAM</div>
      <div class="masthead-sub">VIA AMATEUR RADIO</div>
    </div>

    <table class="preamble">
      <thead>
        <tr>
          <th style="width:9%;">NUMBER</th>
          <th style="width:16%;">PRECEDENCE</th>
          <th style="width:10%;">HX</th>
          <th style="width:14%;">STATION OF ORIGIN</th>
          <th style="width:11%;">CHECK</th>
          <th style="width:18%;">PLACE OF ORIGIN</th>
          <th style="width:11%;">TIME FILED</th>
          <th style="width:11%;">DATE</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td>${up(rg.msg_number || "1")}</td>
          <td>${precDisplay}</td>
          <td>${up(hxVal) || "&nbsp;"}</td>
          <td>${up(rg.station_of_origin || state.config.operator_call)}</td>
          <td>${escHtml(ck)}</td>
          <td>${up(rg.place_of_origin) || "&nbsp;"}</td>
          <td>${up(rg.time_filed) || "&nbsp;"}</td>
          <td>${up(rg.date_filed) || "&nbsp;"}</td>
        </tr>
      </tbody>
    </table>

    <div class="address-grid">
      <div class="to-box">
        <div class="box-label">TO (ADDRESSEE)</div>
        <div class="to-line"><span class="to-tag">NAME</span><span>${up(rg.to_name) || "&nbsp;"}</span></div>
        <div class="to-line"><span class="to-tag">ADDRESS</span><span>${up(rg.to_address) || "&nbsp;"}</span></div>
        <div class="to-line"><span class="to-tag">CITY/ST/ZIP</span><span>${up(rg.to_city_state_zip) || "&nbsp;"}</span></div>
        <div class="to-line"><span class="to-tag">TELEPHONE</span><span>${up(rg.to_phone) || "&nbsp;"}</span></div>
        ${rg.to_email ? `<div class="to-line"><span class="to-tag">EMAIL</span><span>${up(rg.to_email)}</span></div>` : ""}
      </div>
      <div class="notice-box">
        <div class="notice-text">
          <strong>PUBLIC SERVICE MESSAGE</strong><br/>
          THIS RADIOGRAM WAS HANDLED AS A FREE PUBLIC SERVICE BY AMATEUR RADIO OPERATORS PARTICIPATING IN THE NATIONAL TRAFFIC SYSTEM (NTS).
        </div>
        <div class="status-pill">STATUS: ${up(rg.status || "LISTED")}</div>
      </div>
    </div>

    <div class="bt-divider">BT &mdash; MESSAGE TEXT (5 WORDS PER LINE) &mdash; BT</div>
    <table class="msg-grid">
      <tbody>
        ${gridRows.join("")}
      </tbody>
    </table>
    <div class="bt-divider">BT</div>

    <div class="sig-row">
      <span class="box-label" style="margin:0;">SIGNATURE:</span>
      <span class="sig-val">${up(rg.signature) || "&nbsp;"}</span>
      <span class="ar-mark">AR</span>
    </div>

    <table class="relay-log">
      <thead>
        <tr>
          <th style="width:28%;">RECEIVED FROM (STATION)</th>
          <th style="width:22%;">RECEIVED UTC</th>
          <th style="width:28%;">SENT / RELAYED TO (STATION)</th>
          <th style="width:22%;">SENT UTC</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td>${up(rg.received_from) || "&nbsp;"}</td>
          <td>${up(rg.received_at) || "&nbsp;"}</td>
          <td>${up(rg.sent_to) || "&nbsp;"}</td>
          <td>${up(rg.sent_at) || "&nbsp;"}</td>
        </tr>
      </tbody>
    </table>

    ${rg.notes ? `<div class="notes-strip"><strong>HANDLING NOTES:</strong> ${up(rg.notes)}</div>` : ""}

    <div class="footer-note">
      <span>ARRL RADIOGRAM FORMAT &bull; CHECK: ${escHtml(ck)}</span>
      <span>HANDLED BY ${up(state.config.operator_call || "W5CDN")}</span>
    </div>
  </div>
</body>
</html>`;
      }

      function printHtmlViaFrame(html) {
        const oldFrame = document.getElementById("hiddenPrintFrame");
        if (oldFrame) oldFrame.remove();
        const iframe = document.createElement("iframe");
        iframe.id = "hiddenPrintFrame";
        iframe.style.position = "fixed";
        iframe.style.right = "0";
        iframe.style.bottom = "0";
        iframe.style.width = "0";
        iframe.style.height = "0";
        iframe.style.border = "0";
        document.body.appendChild(iframe);
        const doc = iframe.contentWindow.document;
        doc.open();
        doc.write(html);
        doc.close();
        setTimeout(() => {
          iframe.contentWindow.focus();
          iframe.contentWindow.print();
        }, 120);
      }

      function printArrlRadiogram(rg) {
        try {
          printHtmlViaFrame(buildArrlRadiogramPrintHtml(rg));
        } catch (err) {
          toast(`Could not print radiogram: ${err.message}`, "error");
        }
      }

      function renderRadiogramCard(rg) {
        const dest = [rg.to_name, rg.to_city_state_zip].filter(Boolean).join(" — ") || "No addressee";
        const relayInfo = [
          rg.received_from ? `Rcvd: ${rg.received_from} ${rg.received_at || ""}`.trim() : "",
          rg.sent_to ? `Sent: ${rg.sent_to} ${rg.sent_at || ""}`.trim() : "",
        ].filter(Boolean).join(" • ");
        const fileName = rg.file_path ? rg.file_path.split(/[\\/]/).pop() : "";
        const rgMonth = rg.report_month || (rg.created_at || "").slice(0, 7);
        const isNetReport = Boolean(rg.is_net_report);
        const isCancelled = rg.status === "CANCELLED";
        const statusPillClass = isCancelled ? "pill pill-cancelled" : "pill";
        const statusPillStyle = isCancelled ? "" : ' style="border:1px solid var(--border-strong); background:var(--bg-elevated);"';
        const dr = state.prefs.digital_routing || {};
        const wlOn = Boolean(dr.winlink_enabled);
        const pktOn = Boolean(dr.packet_enabled);
        const wlReady = Boolean(dr.winlink_target_configured);
        const pktReady = Boolean(dr.packet_target_configured);
        const digLabel = wlOn && pktOn ? "Packet / Winlink" : (wlOn ? "Winlink" : "Packet");
        return `
          <div class="rg-card${isCancelled ? " rg-cancelled" : ""}" data-rg-id="${rg.id}">
            <div class="rg-card-head">
              <div style="display:flex; align-items:center; gap:6px; flex-wrap:wrap;">
                <span class="mono rg-nr-label" style="font-weight:800; color:var(--text-callsign);">NR ${escHtml(rg.msg_number)}</span>
                <span class="pill prec-${escHtml(rg.precedence)}">${escHtml(rg.precedence)}</span>
                ${isNetReport ? `<span class="pill pill-net-report">NET REPORT • ${escHtml(rgMonth)}</span>` : ""}
                ${rg.hx ? `<span class="pill mono" style="border:1px solid var(--border-strong);">${escHtml(rg.hx)}</span>` : ""}
                <span class="pill pill-rg mono">CK ${escHtml(rg.check_text)}</span>
                <span class="mono" style="font-size:0.86em; color:var(--text-secondary);">FROM ${escHtml(rg.station_of_origin)}</span>
              </div>
              <span class="${statusPillClass}"${statusPillStyle}>${escHtml(rg.status)}</span>
            </div>
            <div style="font-size:0.9em; font-weight:600;">TO: ${escHtml(dest)}</div>
            <div class="mono rg-msg-body" style="font-size:0.84em; color:var(--text-secondary); white-space:pre-wrap;">${escHtml(rg.message_text || "(no text)")}</div>
            ${relayInfo ? `<div class="mono" style="font-size:0.8em; color:var(--text-muted);">${escHtml(relayInfo)}</div>` : ""}
            <div class="mono" style="font-size:0.78em; color:var(--text-muted); display:flex; gap:8px; flex-wrap:wrap;">
              ${rgMonth ? `<span>Month: ${escHtml(rgMonth)}</span>` : ""}
              ${isNetReport ? `<span style="color:var(--accent-violet-text);">Permanent Net Report</span>` : ""}
              ${fileName ? `<span style="color:var(--accent-emerald-text);" title="${escHtml(rg.file_path)}">Saved: ${escHtml(fileName)}</span>` : ""}
            </div>
            <div style="display:flex; justify-content:flex-end; gap:6px; margin-top:2px; flex-wrap:wrap;">
              <button type="button" class="btn btn-primary btn-xs" data-rg-action="view" title="View full formatted ARRL Radiogram message">View</button>
              <button type="button" class="btn btn-xs" data-rg-action="copy">Copy ARRL Text</button>
              <button type="button" class="btn btn-xs" data-rg-action="print">Print Form</button>
              ${(!isCancelled && wlReady) ? `<button type="button" class="btn btn-primary btn-xs" data-rg-action="push-winlink" title="Push directly to your configured Winlink target">⚡ Push Winlink</button>` : ""}
              ${(!isCancelled && pktReady) ? `<button type="button" class="btn btn-success btn-xs" data-rg-action="push-packet" title="Push directly to your configured Packet BBS target">⚡ Push Packet</button>` : ""}
              ${(wlOn || pktOn) ? `<button type="button" class="btn btn-xs" data-rg-action="digital" title="Open dispatch preview, routing options &amp; transcript">${escHtml(digLabel)}</button>` : ""}
              ${!isNetReport ? `<button type="button" class="btn btn-xs" data-rg-action="book" title="Duplicate message text for the next addressee (NTS Book traffic)">Book Copy</button>` : ""}
              <button type="button" class="btn btn-xs" data-rg-action="edit">Edit / Relay</button>
              ${(!isNetReport && !isCancelled) ? `<button type="button" class="btn btn-amber btn-xs" data-rg-action="cancel" title="Mark radiogram as CANCELLED (preserved in NTS history)">Cancel</button>` : ""}
            </div>
          </div>
        `;
      }

      function renderRadiogramsList() {
        const container = $("radiogramsList");
        if (!state.radiograms.length) {
          container.innerHTML = `<div style="text-align:center; padding:28px 16px; color:var(--text-muted);">No ARRL Radiograms logged yet.<br/>Click <strong>+ New</strong> to compose or list formal NTS traffic.</div>`;
          return;
        }
        container.innerHTML = state.radiograms.map(renderRadiogramCard).join("");
      }

      function findRadiogramById(id) {
        const direct = state.radiograms.find((item) => item.id === id);
        if (direct) return direct;
        if (state.ntsHistory && Array.isArray(state.ntsHistory.months)) {
          for (const grp of state.ntsHistory.months) {
            const rep = (grp.net_reports || []).find((item) => item.id === id);
            if (rep) return rep;
            const reg = (grp.radiograms || []).find((item) => item.id === id);
            if (reg) return reg;
          }
        }
        return null;
      }

      async function openRadiogramViewModal(rg) {
        if (!rg) return;
        $("rgViewId").value = String(rg.id);
        $("rgViewTitle").textContent = `ARRL Radiogram — NR ${rg.msg_number}`;
        const relaySummary = [
          rg.received_from ? `Rcvd: ${rg.received_from} ${rg.received_at || ""}`.trim() : "",
          rg.sent_to ? `Sent: ${rg.sent_to} ${rg.sent_at || ""}`.trim() : "",
        ].filter(Boolean).join(" • ");
        $("rgViewMeta").innerHTML = [
          `<span class="pill prec-${escHtml(rg.precedence)}">${escHtml(rg.precedence)}</span>`,
          rg.hx ? `<span class="pill mono">${escHtml(rg.hx)}</span>` : "",
          `<span class="pill pill-rg mono">CK ${escHtml(rg.check_text)}</span>`,
          `<span class="pill">${escHtml(rg.status)}</span>`,
          relaySummary ? `<span class="mono" style="color:var(--text-secondary);">${escHtml(relaySummary)}</span>` : "",
        ].filter(Boolean).join(" ");

        const dr = state.prefs.digital_routing || {};
        const digEnabled = Boolean(dr.winlink_enabled || dr.packet_enabled);
        const digBtn = $("btnRgViewDigital");
        if (digBtn) {
          digBtn.textContent = (dr.winlink_enabled && dr.packet_enabled)
            ? "Packet / Winlink"
            : (dr.winlink_enabled ? "Winlink" : "Packet");
          digBtn.classList.toggle("hidden", !digEnabled);
        }

        const notesText = String(rg.notes || "").trim();
        $("rgViewNotes").textContent = notesText;
        $("rgViewNotesWrap").classList.toggle("hidden", !notesText);
        $("rgViewText").textContent = "Loading formatted ARRL Radiogram...";
        $("radiogramViewBackdrop").classList.remove("hidden");

        try {
          const resp = await fetch(`/api/radiograms/${rg.id}/text`);
          if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
          $("rgViewText").textContent = await resp.text();
        } catch (err) {
          $("rgViewText").textContent = rg.message_text || "(Unable to load formatted radiogram)";
          toast(`Could not load radiogram text: ${err.message}`, "error");
        }
      }

      async function handleRadiogramCardAction(rg, action) {
        if (!rg) return;
        if (action === "view") {
          await openRadiogramViewModal(rg);
        } else if (action === "edit") {
          openRadiogramEditor(rg);
        } else if (action === "book") {
          openRadiogramEditor({
            ...rg,
            id: "",
            msg_number: "",
            to_name: "",
            to_address: "",
            to_city_state_zip: "",
            to_phone: "",
            to_email: "",
            status: "BOOKED",
            received_from: "",
            received_at: "",
            sent_to: "",
            sent_at: "",
          });
          $("rgModalTitle").textContent = `Book Copy of NR ${rg.msg_number}`;
          toast("Book copy ready — enter next addressee", "success");
        } else if (action === "digital") {
          await openDigitalSendModal(rg);
        } else if (action === "push-winlink") {
          await pushRadiogramDirectFromCard(rg, "winlink");
        } else if (action === "push-packet") {
          await pushRadiogramDirectFromCard(rg, "packet");
        } else if (action === "print") {
          printArrlRadiogram(rg);
        } else if (action === "copy") {
          try {
            const resp = await fetch(`/api/radiograms/${rg.id}/text`);
            const text = await resp.text();
            await navigator.clipboard.writeText(text);
            toast(`Copied ARRL Radiogram NR ${rg.msg_number}`, "success");
          } catch {
            toast("Could not copy radiogram text", "error");
          }
        } else if (action === "cancel") {
          if (!confirm(`Mark ARRL Radiogram NR ${rg.msg_number} as CANCELLED?\n\nIt will remain archived in NTS History.`)) return;
          try {
            await api(`/api/radiograms/${rg.id}/cancel`, { method: "POST" });
            await loadRadiograms();
            if (state.prefs.panel_layout?.ntsHistory?.visible) await loadNtsHistory();
            if (state.activeNet) {
              state.activeNet = await api(`/api/nets/${state.activeNet.id}`);
              $("netRgCount").textContent = String(state.activeNet.radiogram_count || 0);
              updateVfoDisplay(state.activeNet);
              renderScriptReader();
            }
            toast(`Radiogram NR ${rg.msg_number} marked CANCELLED`, "success");
          } catch (err) {
            toast(err.message, "error");
          }
        }
      }

      // -----------------------------------------------------------------------
      // NTS History Panel (Grouped by Month)
      // -----------------------------------------------------------------------
      async function loadNtsHistory() {
        const params = new URLSearchParams();
        const st = $("ntsHistoryStatus")?.value || "";
        const q = ($("ntsHistorySearch")?.value || "").trim();
        if (st) params.set("status", st);
        if (q) params.set("q", q);
        const qs = params.toString();
        try {
          state.ntsHistory = await api(`/api/radiograms/history${qs ? `?${qs}` : ""}`);
          renderNtsHistory();
        } catch (err) {
          toast(`Could not load NTS history: ${err.message}`, "error");
        }
      }

      function renderNtsHistory() {
        const data = state.ntsHistory;
        const container = $("ntsHistoryList");
        const totalsEl = $("ntsHistoryTotals");
        if (!data) return;

        totalsEl.textContent = `${data.total_messages} msgs • ${data.total_net_reports} reports • ${data.total_cancelled} cancelled`;

        if (!data.months || !data.months.length) {
          container.innerHTML = `<div style="text-align:center; padding:28px 16px; color:var(--text-muted);">No historical NTS radiograms match this filter yet.</div>`;
          return;
        }

        container.innerHTML = data.months.map((grp, idx) => {
          const hasReport = (grp.net_reports || []).length > 0;
          const reportBadge = hasReport
            ? `<span class="pill pill-net-report">REPORT FILED</span>`
            : grp.month_completed
              ? `<span class="pill" style="border:1px solid var(--accent-amber); color:var(--accent-amber-text); background:var(--accent-amber-bg);">REPORT PENDING</span>`
              : `<span class="pill" style="border:1px solid var(--border-strong); background:var(--bg-input);">IN PROGRESS</span>`;
          const cardsHtml = [
            ...(grp.net_reports || []).map(renderRadiogramCard),
            ...(grp.radiograms || []).map(renderRadiogramCard),
          ].join("");
          return `
            <details class="nts-month-group"${idx < 2 ? " open" : ""}>
              <summary class="nts-month-summary">
                <div class="nts-month-title">
                  <span class="nts-month-caret">▸</span>
                  <span class="mono">${escHtml(grp.month_label)}</span>
                  <span class="mono" style="font-size:0.82em; color:var(--text-muted);">(${escHtml(grp.month)})</span>
                  ${reportBadge}
                </div>
                <div class="nts-month-meta">
                  <span class="pill pill-rg mono">${grp.messages_count} msg${grp.messages_count === 1 ? "" : "s"}</span>
                  ${grp.cancelled_count > 0 ? `<span class="pill pill-cancelled mono">${grp.cancelled_count} canc</span>` : ""}
                  <span class="mono" style="color:var(--text-muted);">${grp.sessions_count} net${grp.sessions_count === 1 ? "" : "s"} • ${Number(grp.operator_hours || 0).toFixed(1)}h</span>
                  <button type="button" class="btn btn-xs" data-open-month-report="${escHtml(grp.month)}" title="View or prepare Monthly Net Report for ${escHtml(grp.month)}">Report</button>
                </div>
              </summary>
              <div class="nts-month-body">
                ${cardsHtml || `<div style="color:var(--text-muted); font-size:0.86em;">No radiograms in this month.</div>`}
              </div>
            </details>
          `;
        }).join("");
      }

      // -----------------------------------------------------------------------
      // Monthly Net Report Radiogram
      // -----------------------------------------------------------------------
      function getMonthlyReportDataFromForm() {
        const msgText = ($("mrMessageText").value || "").trim().toUpperCase();
        const ck = computeArrlCheckLocal(msgText);
        return {
          msg_number: ($("mrNumber").value.trim() || "AUTO").toUpperCase(),
          precedence: ($("mrPrecedence").value || "R").toUpperCase(),
          hx: normalizeArrlHx($("mrHx").value),
          station_of_origin: ($("mrOrigin").value.trim() || state.config.operator_call || "W5CDN").toUpperCase(),
          check_count: ck.count,
          check_text: ck.label.toUpperCase(),
          place_of_origin: $("mrPlace").value.trim().toUpperCase(),
          time_filed: currentUtcCompact(),
          date_filed: currentUtcDateShort(),
          to_name: $("mrToName").value.trim().toUpperCase(),
          to_address: "",
          to_city_state_zip: $("mrToCity").value.trim().toUpperCase(),
          to_phone: "",
          to_email: "",
          message_text: msgText,
          signature: $("mrSignature").value.trim().toUpperCase(),
          status: "LISTED",
          received_from: "",
          received_at: "",
          sent_to: "",
          sent_at: "",
          notes: `MONTHLY NET REPORT FOR ${$("mrMonth").value}`,
        };
      }

      function formatMonthlyReportPreview() {
        const d = getMonthlyReportDataFromForm();
        $("mrCheckPreview").textContent = d.check_text;
        const preamble = [
          `NR ${d.msg_number}`,
          d.precedence,
          d.hx,
          d.station_of_origin,
          d.check_text,
          d.place_of_origin,
          d.time_filed,
          d.date_filed,
        ].filter(Boolean).join(" ");
        const lines = [preamble];
        if (d.to_name) lines.push(d.to_name);
        if (d.to_city_state_zip) lines.push(d.to_city_state_zip);
        lines.push("BT");
        const tokens = d.message_text.split(/\s+/).filter(Boolean);
        for (let i = 0; i < tokens.length; i += 5) {
          lines.push(tokens.slice(i, i + 5).join(" "));
        }
        lines.push("BT");
        if (d.signature) lines.push(d.signature);
        lines.push("AR");
        const text = lines.join("\n").toUpperCase();
        $("mrOfficialPreview").textContent = text;
        return text;
      }

      function updateMonthlyReportButtons() {
        const p = state.monthlyReportPreview;
        const canSave = Boolean(p && p.can_save_report && $("mrValidated").checked);
        $("btnMrSave").disabled = !canSave;
      }

      async function loadMonthlyReportPreview(monthStr = "") {
        const qs = monthStr ? `?month=${encodeURIComponent(monthStr)}` : "";
        const data = await api(`/api/radiograms/monthly-report/preview${qs}`);
        state.monthlyReportPreview = data;
        $("mrMonth").value = data.month;

        const badge = $("mrMonthStateBadge");
        badge.textContent = data.month_completed ? "MONTH COMPLETE" : "CURRENT / IN PROGRESS";
        badge.className = `net-state-badge ${data.month_completed ? "open" : "closed"}`;

        const reportLabel = data.existing_report
          ? `Saved (NR ${data.existing_report.msg_number})`
          : "Not Filed";
        const msgVal = data.cancelled_radiograms_count > 0
          ? `${data.radiograms_count} (${data.cancelled_radiograms_count} canc)`
          : String(data.radiograms_count);
        $("mrKpiGrid").innerHTML = [
          { label: "Sessions", val: data.sessions_count },
          { label: "Check-Ins", val: data.checkins_count },
          { label: "Operator-Hours", val: `${Number(data.operator_hours || 0).toFixed(2)}h` },
          { label: "Messages (Active)", val: msgVal },
          { label: "Monthly Report", val: reportLabel },
        ].map((k) => `
          <div class="report-kpi-card">
            <span class="report-kpi-label">${escHtml(k.label)}</span>
            <span class="report-kpi-value" style="font-size:1.05em;">${escHtml(String(k.val))}</span>
          </div>
        `).join("");

        const warnEl = $("mrWarnBanner");
        if (data.validation_warnings && data.validation_warnings.length) {
          warnEl.innerHTML = data.validation_warnings.map((w) => `<div>⚠ ${escHtml(w)}</div>`).join("");
          warnEl.classList.remove("hidden");
        } else {
          warnEl.classList.add("hidden");
        }

        if (!$("mrOrigin").value.trim()) $("mrOrigin").value = state.config.operator_call || "W5CDN";
        if (!$("mrSignature").value.trim()) $("mrSignature").value = state.config.operator_call || "W5CDN";
        $("mrMessageText").value = data.suggested_message_text;
        $("mrValidated").checked = false;
        formatMonthlyReportPreview();
        updateMonthlyReportButtons();
      }

      async function openMonthlyReportModal(targetMonth = "") {
        try {
          $("mrNumber").value = "";
          $("mrPrecedence").value = "R";
          $("mrHx").value = "";
          $("mrOrigin").value = state.config.operator_call || "W5CDN";
          $("mrSignature").value = state.config.operator_call || "W5CDN";
          await loadMonthlyReportPreview(typeof targetMonth === "string" ? targetMonth : "");
          $("monthlyReportBackdrop").classList.remove("hidden");
        } catch (err) {
          toast(`Could not load monthly report: ${err.message}`, "error");
        }
      }

      async function handleMonthlyReportSubmit(e) {
        e.preventDefault();
        const hxNorm = normalizeArrlHx($("mrHx").value);
        $("mrHx").value = hxNorm;
        const payload = {
          month: $("mrMonth").value,
          validated: $("mrValidated").checked,
          msg_number: $("mrNumber").value.trim().toUpperCase(),
          precedence: $("mrPrecedence").value,
          hx: hxNorm,
          station_of_origin: ($("mrOrigin").value.trim() || state.config.operator_call || "W5CDN").toUpperCase(),
          place_of_origin: $("mrPlace").value.trim().toUpperCase(),
          to_name: $("mrToName").value.trim().toUpperCase(),
          to_city_state_zip: $("mrToCity").value.trim().toUpperCase(),
          message_text: $("mrMessageText").value.trim().toUpperCase(),
          signature: $("mrSignature").value.trim().toUpperCase(),
          status: "LISTED",
        };
        try {
          const saved = await api("/api/radiograms/monthly-report", {
            method: "POST",
            body: JSON.stringify(payload),
          });
          toast(`Saved permanent Monthly Net Report NR ${saved.msg_number} for ${payload.month}`, "success");
          await loadMonthlyReportPreview(payload.month);
          await loadRadiograms();
          if (state.prefs.panel_layout?.ntsHistory?.visible) await loadNtsHistory();
        } catch (err) {
          toast(`Monthly Report error: ${err.message}`, "error");
        }
      }

      async function syncRadiogramsPanel() {
        if (!state.activeNet) state.rgScope = "all";
        $("btnRgScopeNet").classList.toggle("active", state.rgScope === "net");
        $("btnRgScopeAll").classList.toggle("active", state.rgScope === "all");
        $("btnRgScopeNet").disabled = !state.activeNet;
        $("btnExportNetRadiograms").disabled = !state.activeNet;
        await loadRadiograms();
      }

      function openRadiogramEditor(rg = null) {
        const isEdit = Boolean(rg && rg.id);
        $("rgId").value = isEdit ? String(rg.id) : "";
        $("rgModalTitle").textContent = isEdit ? `Edit ARRL Radiogram NR ${rg.msg_number}` : "New ARRL Radiogram";
        $("rgNumber").value = rg ? (rg.msg_number || "") : "";
        $("rgPrecedence").value = rg ? (rg.precedence || "R") : "R";
        $("rgHx").value = rg ? (rg.hx || "") : "";
        $("rgOrigin").value = rg ? (rg.station_of_origin || "") : (state.config.operator_call || "W5CDN");
        $("rgPlace").value = rg ? (rg.place_of_origin || "") : "";
        $("rgTimeFiled").value = rg ? (rg.time_filed || "") : currentUtcCompact();
        $("rgDateFiled").value = rg ? (rg.date_filed || "") : currentUtcDateShort();
        $("rgToName").value = rg ? (rg.to_name || "") : "";
        $("rgToAddress").value = rg ? (rg.to_address || "") : "";
        $("rgToCity").value = rg ? (rg.to_city_state_zip || "") : "";
        $("rgToPhone").value = rg ? (rg.to_phone || "") : "";
        $("rgToEmail").value = rg ? (rg.to_email || "") : "";
        $("rgMessageText").value = rg ? (rg.message_text || "") : "";
        $("rgSignature").value = rg ? (rg.signature || "") : "";
        $("rgStatus").value = rg ? (rg.status || "LISTED") : "LISTED";
        $("rgRcvdFrom").value = rg ? (rg.received_from || "") : "";
        $("rgRcvdAt").value = rg ? (rg.received_at || "") : "";
        $("rgSentTo").value = rg ? (rg.sent_to || "") : "";
        $("rgSentAt").value = rg ? (rg.sent_at || "") : "";
        $("rgNotes").value = rg ? (rg.notes || "") : "";
        $("rgArlPicker").value = "";
        formatRadiogramPreviewFromForm();
        $("radiogramEditorBackdrop").classList.remove("hidden");
        $("rgToName").focus();
      }

      async function handleRadiogramSubmit(e) {
        e.preventDefault();
        const idStr = $("rgId").value;
        const normalizedHx = normalizeArrlHx($("rgHx").value);
        $("rgHx").value = normalizedHx;
        const payload = {
          net_id: state.activeNet ? state.activeNet.id : null,
          msg_number: $("rgNumber").value.trim().toUpperCase(),
          precedence: $("rgPrecedence").value,
          hx: normalizedHx,
          station_of_origin: $("rgOrigin").value.trim().toUpperCase() || state.config.operator_call,
          place_of_origin: $("rgPlace").value.trim().toUpperCase(),
          time_filed: $("rgTimeFiled").value.trim().toUpperCase(),
          date_filed: $("rgDateFiled").value.trim().toUpperCase(),
          to_name: $("rgToName").value.trim().toUpperCase(),
          to_address: $("rgToAddress").value.trim().toUpperCase(),
          to_city_state_zip: $("rgToCity").value.trim().toUpperCase(),
          to_phone: $("rgToPhone").value.trim().toUpperCase(),
          to_email: $("rgToEmail").value.trim().toUpperCase(),
          message_text: $("rgMessageText").value.trim().toUpperCase(),
          signature: $("rgSignature").value.trim().toUpperCase(),
          status: $("rgStatus").value,
          received_from: $("rgRcvdFrom").value.trim().toUpperCase(),
          received_at: $("rgRcvdAt").value.trim().toUpperCase(),
          sent_to: $("rgSentTo").value.trim().toUpperCase(),
          sent_at: $("rgSentAt").value.trim().toUpperCase(),
          notes: $("rgNotes").value.trim().toUpperCase(),
        };
        try {
          if (idStr) {
            await api(`/api/radiograms/${idStr}`, { method: "PATCH", body: JSON.stringify(payload) });
            toast("Radiogram updated", "success");
          } else {
            await api("/api/radiograms", { method: "POST", body: JSON.stringify(payload) });
            toast("ARRL Radiogram saved to routing folder", "success");
          }
          $("radiogramEditorBackdrop").classList.add("hidden");
          await loadRadiograms();
          if (state.prefs.panel_layout?.ntsHistory?.visible) await loadNtsHistory();
          if (state.activeNet) {
            state.activeNet = await api(`/api/nets/${state.activeNet.id}`);
            $("netRgCount").textContent = String(state.activeNet.radiogram_count || 0);
            updateVfoDisplay(state.activeNet);
            renderScriptReader();
          }
        } catch (err) {
          toast(`Radiogram error: ${err.message}`, "error");
        }
      }

      // -----------------------------------------------------------------------
      // NWS Skywarn County Weather Report
      // -----------------------------------------------------------------------
      function updateNwsAlertBadge(count, isNew = false) {
        const badge = $("nwsAlertBadge");
        const btn = $("btnNwsReport");
        if (!badge || !btn) return;
        if (count > 0) {
          badge.textContent = `(${count})`;
          badge.classList.remove("hidden");
        } else {
          badge.textContent = "";
          badge.classList.add("hidden");
        }
        if (isNew) btn.classList.add("nws-new-alert");
      }

      async function pollNwsAlertsBackground(silentInitial = false) {
        const net = state.activeNet;
        if (!net || net.ended_at) return;
        const isSkywarn = `${net.net_type || ""} ${net.name || ""}`.toLowerCase().includes("skywarn");
        if (!isSkywarn) return;
        try {
          const data = await api(`/api/nws/county-report?net_id=${encodeURIComponent(net.id)}`);
          if (data.needs_location) return;
          const alerts = data.alerts || [];
          const newAlerts = [];
          for (const a of alerts) {
            const key = a.id || `${a.event}|${a.effective}|${a.area_desc}`;
            if (!state.nwsKnownAlertIds.has(key)) {
              state.nwsKnownAlertIds.add(key);
              if (!silentInitial) newAlerts.push(a);
            }
          }
          updateNwsAlertBadge(alerts.length, newAlerts.length > 0);
          if (newAlerts.length > 0) {
            const names = newAlerts.map((a) => a.event).join(", ");
            toast(`NEW NWS ALERT (${data.county}, ${data.state}): ${names}`, "error");
          }
        } catch {
          // ignore background poll failures when offline
        }
      }

      function syncSkywarnAutoPoll() {
        const net = state.activeNet;
        const isOpenSkywarn = Boolean(
          net && !net.ended_at && `${net.net_type || ""} ${net.name || ""}`.toLowerCase().includes("skywarn")
        );
        if (!isOpenSkywarn) {
          if (state.nwsPollTimer) {
            clearInterval(state.nwsPollTimer);
            state.nwsPollTimer = null;
          }
          state.nwsPollNetId = null;
          $("btnNwsReport")?.classList.remove("nws-new-alert");
          updateNwsAlertBadge(0, false);
          return;
        }
        if (state.nwsPollNetId !== net.id) {
          if (state.nwsPollTimer) clearInterval(state.nwsPollTimer);
          state.nwsPollNetId = net.id;
          state.nwsKnownAlertIds = new Set();
          pollNwsAlertsBackground(true);
          state.nwsPollTimer = setInterval(() => pollNwsAlertsBackground(false), 180000);
        }
      }

      function renderNwsReport(data) {
        state.nwsReport = data;
        $("btnNwsReport")?.classList.remove("nws-new-alert");
        if (data.county) {
          $("nwsCountyInput").value = data.county;
          state.prefs.nws_county = data.county;
        }
        if (data.state) {
          $("nwsStateInput").value = data.state;
          state.prefs.nws_state = data.state;
        }

        const metaEl = $("nwsMetaBar");
        const alertsEl = $("nwsAlertsList");
        const hwoEl = $("nwsHwoContainer");
        const lsrEl = $("nwsLsrContainer");

        if (data.needs_location) {
          metaEl.classList.add("hidden");
          hwoEl.classList.add("hidden");
          lsrEl.classList.add("hidden");
          $("btnNwsCopy").disabled = true;
          $("btnNwsAppendNotes").disabled = true;
          alertsEl.innerHTML = `<div style="padding:14px 12px; color:var(--text-secondary); border:1px dashed var(--border-strong); border-radius:var(--radius-sm); background:var(--bg-input);">Enter one or more comma-separated <strong>Counties</strong> and a 2-letter <strong>State</strong> above and click <strong>Pull Latest NWS</strong> to retrieve active alerts, HWO, and Local Storm Reports from <code>api.weather.gov</code>.</div>`;
          return;
        }

        for (const a of (data.alerts || [])) {
          state.nwsKnownAlertIds.add(a.id || `${a.event}|${a.effective}|${a.area_desc}`);
        }
        updateNwsAlertBadge(data.alert_count || 0, false);

        const timeLabel = data.fetched_at ? data.fetched_at.replace("T", " ") : "";
        const zonesLabel = (data.zone_ids && data.zone_ids.length) ? data.zone_ids.join(", ") : data.zone_id;
        const wfoLabel = (data.cwas && data.cwas.length) ? data.cwas.join(", ") : data.cwa;
        const zoneParts = [
          zonesLabel ? `Zones: ${zonesLabel}` : "",
          wfoLabel ? `WFO: ${wfoLabel}` : "",
          timeLabel ? `Fetched: ${timeLabel}` : "",
        ].filter(Boolean).join(" • ");
        const countyNoun = (data.counties && data.counties.length > 1) ? "Counties" : "County";
        metaEl.innerHTML = `
          <div><strong>${escHtml(data.county)} ${countyNoun}, ${escHtml(data.state)}</strong> — <span class="mono">${escHtml(zoneParts)}</span></div>
          <span class="pill ${data.alert_count > 0 ? "prec-EMERGENCY" : "pill-rg"} mono">${data.alert_count} Active Alert${data.alert_count === 1 ? "" : "s"}</span>
        `;
        metaEl.classList.remove("hidden");

        if (!data.alerts || !data.alerts.length) {
          alertsEl.innerHTML = `<div class="nws-all-clear">✓ No active NWS watches, warnings, or advisories currently in effect for ${escHtml(data.county)} ${countyNoun}, ${escHtml(data.state)}.</div>`;
        } else {
          alertsEl.innerHTML = data.alerts.map((a) => {
            const sev = ["Extreme", "Severe", "Moderate", "Minor"].includes(a.severity) ? a.severity : "Minor";
            const times = [
              a.effective ? `Effective: ${a.effective}` : "",
              a.expires ? `Expires: ${a.expires}` : "",
            ].filter(Boolean).join(" • ");
            return `
              <div class="nws-alert-card sev-${sev}">
                <div class="rg-card-head">
                  <div style="display:flex; align-items:center; gap:6px; flex-wrap:wrap;">
                    <strong style="font-size:1.02em;">${escHtml(a.event)}</strong>
                    <span class="pill nws-sev-pill nws-sev-${sev}">${escHtml(a.severity)}</span>
                    ${a.urgency && a.urgency !== "Unknown" ? `<span class="pill" style="border:1px solid var(--border-strong); background:var(--bg-elevated);">${escHtml(a.urgency)}</span>` : ""}
                  </div>
                  ${a.sender_name ? `<span style="font-size:0.78em; color:var(--text-muted);">${escHtml(a.sender_name)}</span>` : ""}
                </div>
                ${a.headline ? `<div style="font-weight:700; font-size:0.92em;">${escHtml(a.headline)}</div>` : ""}
                ${times ? `<div class="mono" style="font-size:0.78em; color:var(--text-muted);">${escHtml(times)}</div>` : ""}
                ${a.area_desc ? `<div style="font-size:0.82em; color:var(--text-secondary);"><strong>Areas:</strong> ${escHtml(a.area_desc)}</div>` : ""}
                ${a.description ? `<pre class="nws-pre-block">${escHtml(a.description)}</pre>` : ""}
                ${a.instruction ? `<div style="font-size:0.86em; color:var(--accent-amber-text); font-weight:600;"><strong>Action:</strong> ${escHtml(a.instruction)}</div>` : ""}
              </div>
            `;
          }).join("");
        }

        if (data.hwo && data.hwo.text) {
          hwoEl.innerHTML = `
            <details class="nts-month-group"${data.alert_count === 0 ? " open" : ""}>
              <summary class="nts-month-summary">
                <div class="nts-month-title">
                  <span class="nts-month-caret">▸</span>
                  <span>Hazardous Weather Outlook (WFO ${escHtml(data.hwo.issuing_office || data.cwa)})</span>
                </div>
                <span class="mono" style="font-size:0.78em; color:var(--text-muted);">${escHtml(data.hwo.issuance_time || "")}</span>
              </summary>
              <div class="nts-month-body">
                <pre class="nws-pre-block">${escHtml(data.hwo.text)}</pre>
              </div>
            </details>
          `;
          hwoEl.classList.remove("hidden");
        } else {
          hwoEl.classList.add("hidden");
          hwoEl.innerHTML = "";
        }

        if (data.lsrs && data.lsrs.length) {
          lsrEl.innerHTML = `
            <details class="nts-month-group">
              <summary class="nts-month-summary">
                <div class="nts-month-title">
                  <span class="nts-month-caret">▸</span>
                  <span>Recent Local Storm Reports (LSR — ${data.lsrs.length})</span>
                </div>
                <span class="mono" style="font-size:0.78em; color:var(--text-muted);">WFO ${escHtml(wfoLabel || "")}</span>
              </summary>
              <div class="nts-month-body">
                ${data.lsrs.map((lsr) => `
                  <div>
                    <div class="mono" style="font-size:0.76em; color:var(--text-muted); margin-bottom:3px;">WFO ${escHtml(lsr.issuing_office)} • ${escHtml(lsr.issuance_time)}</div>
                    <pre class="nws-pre-block">${escHtml(lsr.text)}</pre>
                  </div>
                `).join("")}
              </div>
            </details>
          `;
          lsrEl.classList.remove("hidden");
        } else {
          lsrEl.classList.add("hidden");
          lsrEl.innerHTML = "";
        }

        $("btnNwsCopy").disabled = !data.plain_text;
        $("btnNwsAppendNotes").disabled = !(data.net_note_summary && state.activeNet);
      }

      async function fetchNwsCountyReport(county = "", stateCode = "") {
        const btn = $("btnNwsFetch");
        const origLabel = btn.textContent;
        btn.disabled = true;
        btn.textContent = "Pulling NWS...";
        try {
          const params = new URLSearchParams();
          if (county.trim()) params.set("county", county.trim());
          if (stateCode.trim()) params.set("state", stateCode.trim().toUpperCase());
          if (state.activeNet) params.set("net_id", String(state.activeNet.id));
          const data = await api(`/api/nws/county-report?${params.toString()}`);
          renderNwsReport(data);
        } catch (err) {
          $("nwsAlertsList").innerHTML = `<div class="dup-banner" style="margin-top:0;">⚠ ${escHtml(err.message)}</div>`;
          toast(`NWS fetch failed: ${err.message}`, "error");
        } finally {
          btn.disabled = false;
          btn.textContent = origLabel;
        }
      }

      function formatNwsContactPlainText(nc) {
        const c = (nc && typeof nc === "object") ? nc : {};
        const lines = ["=== NWS SKYWARN OFFICE CONTACT ==="];
        if (c.wfo_name) lines.push(`WFO Office   : ${c.wfo_name}`);
        if (c.phone) lines.push(`Spotter Phone: ${c.phone}`);
        if (c.email) lines.push(`Report Email : ${c.email}`);
        if (c.nwschat_url) lines.push(`NWSChat / Web: ${c.nwschat_url}`);
        if (c.radio_liaison) lines.push(`Radio Liaison: ${c.radio_liaison}`);
        if (c.winlink_address) lines.push(`Winlink Addr : ${c.winlink_address}`);
        if (c.reporting_notes) lines.push(`Criteria     : ${c.reporting_notes}`);
        return lines.join("\n");
      }

      function renderNwsContactCard() {
        const card = $("nwsContactCard");
        if (!card) return;
        const c = (state.prefs && state.prefs.nws_contact) || {};
        const hasAny = Boolean(
          c.wfo_name || c.phone || c.email || c.nwschat_url || c.radio_liaison || c.winlink_address || c.reporting_notes
        );
        if (!hasAny) {
          card.innerHTML = `
            <div style="display:flex; align-items:center; justify-content:space-between; gap:8px; flex-wrap:wrap; width:100%;">
              <span style="font-size:0.86em; color:var(--text-secondary);">No NWS Skywarn office contact details configured yet (spotter hotline, NWSChat, email, radio/Winlink liaison).</span>
              <button type="button" class="btn btn-xs" data-nws-contact-action="edit">Add NWS Contact Info</button>
            </div>
          `;
          return;
        }
        const items = [];
        if (c.phone) items.push(`<span><strong>Phone:</strong> <span class="mono">${escHtml(c.phone)}</span></span>`);
        if (c.email) items.push(`<span><strong>Email:</strong> <a href="mailto:${encodeURIComponent(c.email)}" class="mono" style="color:var(--accent-blue);">${escHtml(c.email)}</a></span>`);
        if (c.nwschat_url && isSafeHttpUrl(c.nwschat_url)) {
          items.push(`<span><strong>NWSChat/Web:</strong> <a href="${escHtml(c.nwschat_url)}" target="_blank" rel="noopener noreferrer" class="mono" style="color:var(--accent-blue);">${escHtml(c.nwschat_url)}</a></span>`);
        }
        if (c.radio_liaison) items.push(`<span><strong>Radio Liaison:</strong> <span class="mono">${escHtml(c.radio_liaison)}</span></span>`);
        if (c.winlink_address) items.push(`<span><strong>Winlink:</strong> <span class="mono">${escHtml(c.winlink_address)}</span></span>`);
        card.innerHTML = `
          <div style="display:flex; align-items:center; justify-content:space-between; gap:8px; flex-wrap:wrap; width:100%;">
            <div style="font-weight:700; color:var(--text-primary);">
              NWS Contact${c.wfo_name ? ` — ${escHtml(c.wfo_name)}` : ""}
            </div>
            <div style="display:flex; gap:6px; flex-wrap:wrap;">
              <button type="button" class="btn btn-xs" data-nws-contact-action="copy">Copy Contact</button>
              <button type="button" class="btn btn-ghost btn-xs" data-nws-contact-action="edit">Edit</button>
            </div>
          </div>
          ${items.length ? `<div style="display:flex; gap:10px 16px; flex-wrap:wrap; font-size:0.86em;">${items.join("")}</div>` : ""}
          ${c.reporting_notes ? `<div style="font-size:0.84em; color:var(--text-secondary);"><strong>Reporting Criteria:</strong> ${escHtml(c.reporting_notes)}</div>` : ""}
        `;
      }

      async function openNwsReportModal() {
        $("nwsReportBackdrop").classList.remove("hidden");
        $("btnNwsReport")?.classList.remove("nws-new-alert");
        $("nwsCountyInput").value = state.prefs.nws_county || "";
        $("nwsStateInput").value = state.prefs.nws_state || "";
        renderNwsContactCard();
        $("nwsAlertsList").innerHTML = `<div style="padding:14px 12px; color:var(--text-muted);">Contacting National Weather Service (api.weather.gov)...</div>`;
        await fetchNwsCountyReport($("nwsCountyInput").value, $("nwsStateInput").value);
      }

      // -----------------------------------------------------------------------
      // NCS Handoff, Recurring Net Clone, Backup/Restore, Roster CSV
      // -----------------------------------------------------------------------
      function openNcsHandoffModal() {
        if (!state.activeNet) return;
        $("ncsHandoffInfo").textContent = `Current NCS: ${state.activeNet.ncs_call}. Enter the relief operator's callsign to log a timestamped handoff and transfer net control.`;
        $("handoffNewNcs").value = "";
        $("handoffNotes").value = "";
        $("ncsHandoffBackdrop").classList.remove("hidden");
        $("handoffNewNcs").focus();
      }

      async function cloneNetSession(netId) {
        const src = (state.nets || []).find((n) => n.id === netId)
          || (state.activeNet && state.activeNet.id === netId ? state.activeNet : null);
        if (!src) return;
        closeNetsDrawer();
        requestNewNet(null, src);
      }

      async function downloadBackupArchive() {
        try {
          const resp = await fetch("/api/backup/export");
          if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
          const blob = await resp.blob();
          const disp = resp.headers.get("Content-Disposition") || "";
          const m = disp.match(/filename="([^"]+)"/);
          const filename = m ? m[1] : "net_logger_backup.zip";
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url;
          a.download = filename;
          document.body.appendChild(a);
          a.click();
          a.remove();
          URL.revokeObjectURL(url);
          toast(`Downloaded backup ${filename}`, "success");
        } catch (err) {
          toast(`Backup export failed: ${err.message}`, "error");
        }
      }

      async function restoreBackupArchiveFile(file) {
        if (!file) return;
        const ok = confirm(
          `Restore backup "${file.name}"?\n\n` +
          `This replaces the current database with the backup (a safety copy net_logger.pre_restore.bak is saved automatically).`
        );
        if (!ok) return;
        try {
          const resp = await fetch("/api/backup/restore", {
            method: "POST",
            headers: { "Content-Type": "application/zip" },
            body: file,
          });
          const data = await resp.json();
          if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
          toast(`Backup restored (${data.counts.nets} nets, ${data.counts.radiograms} radiograms)`, "success");
          closeAllModals();
          state.config = await api("/api/config");
          if (state.config.preferences) applySavedPrefs(state.config.preferences);
          await loadScripts();
          state.activeNet = null;
          await loadNets();
          await loadRadiograms();
          if (state.prefs.panel_layout?.ntsHistory?.visible) await loadNtsHistory();
          if (state.prefs.panel_layout?.roster?.visible) await loadRoster("");
        } catch (err) {
          toast(`Restore failed: ${err.message}`, "error");
        }
      }

      async function exportRosterCsv() {
        try {
          const resp = await fetch("/api/operators/export/csv");
          if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
          await saveTextContent("operator_roster.csv", await resp.text(), "csv");
        } catch (err) {
          toast(`Roster export failed: ${err.message}`, "error");
        }
      }

      async function importRosterCsvFile(file) {
        if (!file) return;
        try {
          const csvText = await file.text();
          const res = await api("/api/operators/import/csv", {
            method: "POST",
            body: JSON.stringify({ csv_text: csvText }),
          });
          await loadRoster($("rosterSearch").value.trim());
          toast(`Roster CSV imported: ${res.imported} added, ${res.updated} updated, ${res.skipped} skipped`, "success");
        } catch (err) {
          toast(`Roster import failed: ${err.message}`, "error");
        }
      }

      function isInternetEmailAddress(val) {
        const cleaned = String(val || "").trim();
        if (!cleaned || cleaned.includes("#")) return false;
        if (/\.(NOAM|SOAM|EURO|ASIA|AUST|WW)$/i.test(cleaned)) return false;
        return /^[^@\s#]+@[^@\s#]+\.[A-Za-z]{2,12}$/.test(cleaned);
      }

      async function refreshDigitalSendModal(opts = {}) {
        const rgId = Number($("digRgId").value);
        if (!rgId) return;
        const toVal = $("digToAddress").value.trim();
        const pktToVal = $("digPacketToAddress").value.trim();
        const subjVal = opts.resetSubject ? "" : $("digSubject").value.trim();
        const bbsTypeVal = opts.useDefaultType ? "" : ($("digBpqType").value || "");
        const params = new URLSearchParams();
        if (toVal) params.set("to", toVal);
        if (pktToVal) params.set("packet_to", pktToVal);
        if (subjVal) params.set("subject", subjVal);
        if (bbsTypeVal) params.set("bbs_type", bbsTypeVal);
        const qs = params.toString() ? `?${params.toString()}` : "";
        const data = await api(`/api/radiograms/${rgId}/digital${qs}`);
        state.digitalDispatchData = data;
        if (!toVal && data.suggested_to) $("digToAddress").value = data.suggested_to;
        if (!pktToVal && data.suggested_packet_to) $("digPacketToAddress").value = data.suggested_packet_to;
        if (!subjVal && data.suggested_subject) {
          $("digSubject").value = data.suggested_subject;
          $("digSubject").dataset.autoSubject = data.suggested_subject;
        }
        if (data.bbs_type) $("digBpqType").value = data.bbs_type;
        $("digWinlinkPreview").value = data.winlink_text || "";
        $("digPacketPreview").value = data.packet_text || "";

        const r = data.routing || {};
        const wlOn = Boolean(r.winlink_enabled);
        const pktOn = Boolean(r.packet_enabled);
        const wlReady = Boolean(r.winlink_target_configured);
        const pktReady = Boolean(r.packet_target_configured);
        const useBpq = r.push_method === "bpq_telnet";
        const bpqUser = r.bpq_user || state.prefs.operator_call || "";
        const hostPort = `${r.tnc_host || "127.0.0.1"}:${r.tnc_port || 8010}`;

        const summaryRows = [];
        if (useBpq) {
          summaryRows.push(
            `<div><strong>BBS / Mail Server:</strong> <span class="mono">${escHtml(`${bpqUser}@${hostPort}${r.bpq_bbs_cmd ? ` → ${r.bpq_bbs_cmd}` : ""}`)}</span> <span style="color:var(--text-muted);">(BBS handles RF &amp; Winlink routing)</span></div>`
          );
        } else {
          summaryRows.push(
            `<div><strong>Outbox / Mail Folder:</strong> <span class="mono">${escHtml(data.outbox_dir || "outbox")}</span></div>`
          );
        }
        if (r.routing_notes) summaryRows.push(`<div><strong>Notes:</strong> ${escHtml(r.routing_notes)}</div>`);
        $("digNodeSummary").innerHTML = summaryRows.join("");

        const patBtn = $("btnDigOpenPat");
        if (r.pat_url && r.transport === "pat") {
          patBtn.dataset.patUrl = r.pat_url;
          patBtn.classList.remove("hidden");
        } else {
          patBtn.classList.add("hidden");
        }

        const webmailBtn = $("btnDigOpenWebmail");
        if (r.bpq_webmail_url) {
          webmailBtn.dataset.webmailUrl = r.bpq_webmail_url;
          webmailBtn.classList.remove("hidden");
        } else {
          webmailBtn.classList.add("hidden");
        }

        $("digPacketToWrap").classList.toggle("hidden", !pktOn && wlOn);
        $("digWinlinkToWrap").classList.toggle("hidden", !wlOn && pktOn);
        $("digBpqTypeWrap").classList.toggle("hidden", !pktOn);
        $("digWinlinkCol").classList.toggle("hidden", !wlOn && pktOn);
        $("digPacketCol").classList.toggle("hidden", !pktOn && wlOn);
        $("digPreviewGrid").style.gridTemplateColumns = (wlOn && pktOn) ? "" : "1fr";
        $("btnDigCopyWinlink").classList.toggle("hidden", !wlOn);
        $("btnDigCopyPacket").classList.toggle("hidden", !pktOn);
        $("btnDigSendBpqWinlink").classList.toggle("hidden", !wlReady);
        $("btnDigSendBpqPacket").classList.toggle("hidden", !pktReady);
        $("digBpqSendBar").classList.toggle("hidden", !(wlReady || pktReady));

        if (useBpq) {
          $("digPushBarTitle").textContent = "Push to LinBPQ Mail (Telnet):";
          $("digBpqTargetInfo").textContent = hostPort;
          const hints = [];
          if (pktReady) hints.push(`Packet: "${data.bpq_packet_command || ""}"`);
          if (wlReady) hints.push(`Winlink: "${data.bpq_winlink_command || ""}"`);
          $("digBpqCmdHint").textContent = hints.join(" • ");
        } else {
          $("digPushBarTitle").textContent = "Push to Outbox / Mail Import:";
          $("digBpqTargetInfo").textContent = data.outbox_dir || "outbox";
          $("digBpqCmdHint").textContent = "Writes formatted message file directly to target folder";
        }
      }

      async function pushRadiogramDirectFromCard(rg, mode) {
        if (!rg) return;
        const dr = state.prefs.digital_routing || {};
        let dest = "";
        if (mode === "packet") {
          const sentTo = String(rg.sent_to || "").trim();
          const defTo = String(dr.winlink_to_default || "").trim();
          if (sentTo && !/^RMS:/i.test(sentTo) && !isInternetEmailAddress(sentTo)) {
            dest = sentTo;
          } else if (defTo && !/^RMS:/i.test(defTo) && !isInternetEmailAddress(defTo)) {
            dest = defTo;
          }
        } else {
          dest = (rg.to_email || dr.winlink_to_default || rg.to_name || "").trim();
        }
        if (!dest) {
          await openDigitalSendModal(rg);
          const targetInput = mode === "packet" ? $("digPacketToAddress") : $("digToAddress");
          targetInput?.focus();
          toast(
            mode === "packet"
              ? "Enter the Packet receiving station ID (callsign or NTS address), then click Push to Packet BBS"
              : "Enter destination address or callsign, then click Push",
            "info"
          );
          return;
        }
        try {
          const res = await api(`/api/radiograms/${rg.id}/push`, {
            method: "POST",
            body: JSON.stringify({
              mode,
              to_address: dest,
              mark_sent: true,
            }),
          });
          if (res.ok) {
            toast(res.message || `Pushed NR ${rg.msg_number} via ${mode}`, "success");
            await loadRadiograms();
            if (state.prefs.panel_layout?.ntsHistory?.visible) await loadNtsHistory();
          } else {
            toast(res.message || `Push failed for NR ${rg.msg_number}`, "error");
            await openDigitalSendModal(rg);
            $("digBpqStatusBanner").className = "qrz-test-banner fail";
            $("digBpqStatusBanner").textContent = res.message || "Push failed.";
            $("digBpqStatusBanner").classList.remove("hidden");
            if (res.transcript) {
              $("digBpqTranscript").textContent = res.transcript;
              $("digBpqTranscript").classList.remove("hidden");
            }
          }
        } catch (err) {
          toast(`Push failed: ${err.message}`, "error");
        }
      }

      async function openDigitalSendModal(rg) {
        if (!rg) return;
        $("digRgId").value = String(rg.id);
        $("digToAddress").value = "";
        $("digPacketToAddress").value = "";
        $("digSubject").value = "";
        $("digSubject").dataset.autoSubject = "";
        $("digitalSendTitle").textContent = `BBS / Mail Dispatch — NR ${rg.msg_number}`;
        $("digBpqStatusBanner").classList.add("hidden");
        $("digBpqTranscript").classList.add("hidden");
        $("digBpqTranscript").textContent = "";
        try {
          await refreshDigitalSendModal({ useDefaultType: true, resetSubject: true });
          $("digitalSendBackdrop").classList.remove("hidden");
        } catch (err) {
          toast(`Could not load digital dispatch format: ${err.message}`, "error");
        }
      }

      async function sendRadiogramViaBpqTelnet(mode) {
        const rgId = Number($("digRgId").value);
        if (!rgId) return;
        const inputEl = mode === "packet" ? $("digPacketToAddress") : $("digToAddress");
        const toAddr = (inputEl ? inputEl.value : "").trim();
        if (!toAddr) {
          toast(
            mode === "packet"
              ? "Enter a Packet receiving station ID (callsign or NTS address) first"
              : "Enter a Winlink destination address or callsign first",
            "error"
          );
          inputEl?.focus();
          return;
        }
        const subjectVal = $("digSubject").value.trim();
        const bbsTypeVal = $("digBpqType").value || "";
        const markSent = $("digBpqMarkSent").checked;
        const banner = $("digBpqStatusBanner");
        const transcriptEl = $("digBpqTranscript");
        const btnWl = $("btnDigSendBpqWinlink");
        const btnPkt = $("btnDigSendBpqPacket");
        btnWl.disabled = true;
        btnPkt.disabled = true;
        banner.className = "qrz-test-banner";
        banner.textContent = `Pushing ${mode === "winlink" ? "Winlink" : "Packet BBS"} message to configured target...`;
        banner.classList.remove("hidden");
        transcriptEl.classList.add("hidden");
        try {
          const res = await api(`/api/radiograms/${rgId}/push`, {
            method: "POST",
            body: JSON.stringify({
              mode,
              to_address: toAddr,
              subject: subjectVal,
              bbs_type: mode === "packet" ? bbsTypeVal : "",
              mark_sent: markSent,
            }),
          });
          banner.className = `qrz-test-banner ${res.ok ? "ok" : "fail"}`;
          banner.textContent = res.message || (res.ok ? "Message pushed to target." : "Push failed.");
          if (res.transcript) {
            transcriptEl.textContent = res.transcript;
            transcriptEl.classList.remove("hidden");
          }
          if (res.ok) {
            toast(res.message || `Pushed via ${mode}`, "success");
            await loadRadiograms();
            if (state.prefs.panel_layout?.ntsHistory?.visible) await loadNtsHistory();
          } else {
            toast(res.message || "Push failed", "error");
          }
        } catch (err) {
          banner.className = "qrz-test-banner fail";
          banner.textContent = `Push error: ${err.message}`;
          toast(`Push error: ${err.message}`, "error");
        } finally {
          btnWl.disabled = false;
          btnPkt.disabled = false;
        }
      }

      async function stageDigitalRadiogram(formatType) {
        const rgId = Number($("digRgId").value);
        if (!rgId) return;
        try {
          const res = await api(`/api/radiograms/${rgId}/stage-digital`, {
            method: "POST",
            body: JSON.stringify({
              format_type: formatType,
              to_address: $("digToAddress").value.trim(),
            }),
          });
          toast(`Staged ${res.filename} in outbox folder`, "success");
        } catch (err) {
          toast(`Stage failed: ${err.message}`, "error");
        }
      }

      // -----------------------------------------------------------------------
      // Application Self-Update (Git / Source & Standalone Bundles)
      // -----------------------------------------------------------------------
      function renderUpdateStatus(info) {
        const verEl = $("updateVersionPill");
        const topVerEl = $("appVersionPill");
        const modeEl = $("updateModePill");
        const statusEl = $("updateStatusText");
        const linkEl = $("updateReleaseLink");
        const notesEl = $("updateReleaseNotes");
        const applyBtn = $("btnApplyUpdate");
        const bannerBtn = $("btnUpdateBanner");
        if (!verEl || !modeEl || !statusEl) return;

        const curVer = info?.current_version || state.config?.version || "0.1.8";
        const mode = info?.install_mode || state.config?.install_mode || "source";
        const modeLabel = {
          standalone: "STANDALONE",
          git: "GIT CHECKOUT",
          source: "SOURCE ARCHIVE",
        }[mode] || mode.toUpperCase();

        verEl.textContent = `v${curVer}`;
        if (topVerEl) {
          topVerEl.textContent = `v${curVer}`;
          topVerEl.title = `Running W5CDN Net Logger v${curVer} (${modeLabel.toLowerCase()}) — click to check for updates`;
        }
        modeEl.textContent = modeLabel;

        if (!info) {
          statusEl.textContent = "Check GitHub Releases for newer versions. Updates never run automatically during an open net.";
          applyBtn?.classList.add("hidden");
          linkEl?.classList.add("hidden");
          notesEl?.classList.add("hidden");
          bannerBtn?.classList.add("hidden");
          return;
        }

        const openNet = state.nets.length
          ? state.nets.some((n) => !n.ended_at)
          : Boolean(info.has_open_net);
        let msg = info.message || "";
        if (info.update_available && openNet) {
          msg += " Close active net sessions before installing.";
        }
        statusEl.textContent = msg;

        if (linkEl) {
          const url = String(info.release_url || "");
          if (url.startsWith("https://github.com/")) {
            linkEl.href = url;
            linkEl.classList.remove("hidden");
          } else {
            linkEl.classList.add("hidden");
          }
        }

        if (notesEl) {
          const notes = String(info.release_notes || "").trim();
          if (info.update_available && notes) {
            notesEl.textContent = notes;
            notesEl.classList.remove("hidden");
          } else {
            notesEl.textContent = "";
            notesEl.classList.add("hidden");
          }
        }

        if (bannerBtn) {
          if (info.update_available) {
            bannerBtn.textContent = `↑ Update v${info.latest_version}`;
            bannerBtn.classList.remove("hidden");
          } else {
            bannerBtn.classList.add("hidden");
          }
        }

        if (applyBtn) {
          if (info.update_available && info.can_self_update) {
            applyBtn.classList.remove("hidden");
            applyBtn.disabled = openNet;
            applyBtn.textContent = mode === "standalone"
              ? `Download & Install v${info.latest_version}`
              : `Update to v${info.latest_version}`;
          } else {
            applyBtn.classList.add("hidden");
          }
        }
      }

      async function openUpdatesInPreferences() {
        await openPreferencesModal("qrz");
        const card = $("updateSectionCard");
        if (card) {
          card.scrollIntoView({ behavior: "smooth", block: "center" });
          card.classList.remove("update-card-highlight");
          void card.offsetWidth;
          card.classList.add("update-card-highlight");
          setTimeout(() => card.classList.remove("update-card-highlight"), 2200);
        }
        const applyBtn = $("btnApplyUpdate");
        if (applyBtn && !applyBtn.classList.contains("hidden") && !applyBtn.disabled) {
          applyBtn.focus({ preventScroll: true });
        } else {
          $("btnCheckUpdate")?.focus({ preventScroll: true });
        }
      }

      async function checkAppUpdates(silent = false) {
        const btn = $("btnCheckUpdate");
        if (btn && !silent) {
          btn.disabled = true;
          btn.textContent = "Checking...";
        }
        try {
          const info = await api("/api/update/check");
          state.updateInfo = info;
          renderUpdateStatus(info);
          if (!silent) {
            toast(info.message || "Update check complete", info.update_available ? "success" : "info");
          }
        } catch (err) {
          if (!silent) {
            toast(`Update check failed: ${err.message}`, "error");
          }
        } finally {
          if (btn && !silent) {
            btn.disabled = false;
            btn.textContent = "Check for Updates";
          }
        }
      }

      async function handleApplyUpdate() {
        if (state.nets.some((n) => !n.ended_at)) {
          toast("Close active net sessions before applying an application update.", "error");
          return;
        }
        const info = state.updateInfo;
        const mode = info?.install_mode || state.config?.install_mode || "source";
        const targetVer = info?.latest_version ? `v${info.latest_version}` : "the latest version";
        const promptText = mode === "standalone"
          ? `Download, SHA-256 verify, and install ${targetVer}?\n\nA backup (net_logger.pre_update.bak) will be created automatically, and W5CDN Net Logger will close and relaunch.`
          : `Fast-forward this Git checkout to ${targetVer} and sync dependencies?\n\nA backup (net_logger.pre_update.bak) will be created automatically before pulling.`;
        if (!confirm(promptText)) return;

        const applyBtn = $("btnApplyUpdate");
        const checkBtn = $("btnCheckUpdate");
        if (applyBtn) {
          applyBtn.disabled = true;
          applyBtn.textContent = mode === "standalone" ? "Downloading & Verifying..." : "Updating...";
        }
        if (checkBtn) checkBtn.disabled = true;

        try {
          const res = await api("/api/update/apply", { method: "POST" });
          const statusEl = $("updateStatusText");
          if (statusEl) statusEl.textContent = res.message || "Update applied.";
          $("btnUpdateBanner")?.classList.add("hidden");
          toast(res.message || "Update applied. Restart W5CDN Net Logger.", "success");
          if (applyBtn) {
            applyBtn.textContent = mode === "standalone" ? "Restarting..." : "Updated — Restart App";
          }
        } catch (err) {
          toast(`Update failed: ${err.message}`, "error");
          if (applyBtn) {
            applyBtn.disabled = false;
            renderUpdateStatus(state.updateInfo);
          }
        } finally {
          if (checkBtn) checkBtn.disabled = false;
        }
      }

      // -----------------------------------------------------------------------
      // Modal Helpers & Global Keyboard Navigation
      // -----------------------------------------------------------------------
      function closeAllModals() {
        if (!$("prefsModalBackdrop").classList.contains("hidden")) applyVisualPrefs(state.prefs);
        closeNetsDrawer();
        [
          "prefsModalBackdrop", "openNetWarnBackdrop", "netModalBackdrop", "editCheckinBackdrop",
          "quickNoteBackdrop", "radiogramViewBackdrop", "radiogramEditorBackdrop", "monthlyReportBackdrop",
          "panelsModalBackdrop", "netReportBackdrop", "nwsReportBackdrop", "ncsHandoffBackdrop",
          "digitalSendBackdrop", "shortcutsBackdrop",
        ].forEach((id) => $(id).classList.add("hidden"));
      }

      function anyOverlayOpen() {
        return [
          "netsDrawerBackdrop",
          "prefsModalBackdrop", "openNetWarnBackdrop", "netModalBackdrop", "editCheckinBackdrop",
          "quickNoteBackdrop", "radiogramViewBackdrop", "radiogramEditorBackdrop", "monthlyReportBackdrop",
          "panelsModalBackdrop", "netReportBackdrop", "nwsReportBackdrop", "ncsHandoffBackdrop",
          "digitalSendBackdrop", "shortcutsBackdrop",
        ].some((id) => !$(id).classList.contains("hidden"));
      }

      function isTypingElement(el) {
        if (!el) return false;
        const tag = el.tagName;
        return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
      }

      function moveRowSelection(delta) {
        const rows = getFilteredCheckins();
        if (!rows.length) return;
        const idx = rows.findIndex((c) => c.id === state.selectedCheckinId);
        let nextIdx = idx === -1 ? (delta > 0 ? 0 : rows.length - 1) : idx + delta;
        nextIdx = Math.max(0, Math.min(rows.length - 1, nextIdx));
        state.selectedCheckinId = rows[nextIdx].id;
        renderCheckinsTable();
        const tr = document.querySelector(`tr[data-checkin-id="${state.selectedCheckinId}"]`);
        if (tr) tr.scrollIntoView({ block: "nearest" });
      }

      // -----------------------------------------------------------------------
      // Event Wiring
      // -----------------------------------------------------------------------
      function openPanelsModal() {
        syncPanelsModalControls(state.prefs.panel_layout);
        $("panelsModalBackdrop").classList.remove("hidden");
      }

      function bindEvents() {
        $("btnToggleSidebar").addEventListener("click", toggleNetsDrawer);
        $("btnCloseNetsDrawer").addEventListener("click", closeNetsDrawer);
        $("netsDrawerBackdrop").addEventListener("click", (e) => {
          if (e.target === $("netsDrawerBackdrop")) closeNetsDrawer();
        });
        $("netsSearch").addEventListener("input", () => {
          state.netsSearchQuery = $("netsSearch").value;
          renderNetsList();
        });
        $("btnPanelsOpenNets").addEventListener("click", () => {
          $("panelsModalBackdrop").classList.add("hidden");
          openNetsDrawer();
        });
        $("btnNewNet").addEventListener("click", () => requestNewNet());
        $("btnSidebarNewNet").addEventListener("click", () => {
          closeNetsDrawer();
          requestNewNet();
        });
        $("btnWarnCancelNewNet").addEventListener("click", () => {
          state.pendingNewNetPreset = null;
          state.pendingCloneNet = null;
          $("openNetWarnBackdrop").classList.add("hidden");
        });
        $("btnWarnCloseAndNew").addEventListener("click", closeOpenNetsAndStartNew);
        $("btnWarnKeepAndNew").addEventListener("click", () => {
          const preset = state.pendingNewNetPreset;
          const cloneSource = state.pendingCloneNet;
          state.pendingNewNetPreset = null;
          state.pendingCloneNet = null;
          $("openNetWarnBackdrop").classList.add("hidden");
          openNetModal(null, preset, cloneSource ? "netFormNcs" : "netFormName", cloneSource);
        });
        $("btnOpenRadiograms").addEventListener("click", () => togglePanel("radiograms"));
        $("btnOpenNtsHistory").addEventListener("click", () => togglePanel("ntsHistory"));
        $("btnNetRadiograms").addEventListener("click", () => {
          const isOpen = Boolean(state.prefs.panel_layout?.radiograms?.visible);
          if (!isOpen && state.activeNet) state.rgScope = "net";
          togglePanel("radiograms", !isOpen);
        });
        $("btnToggleScript").addEventListener("click", () => togglePanel("scripts"));
        $("btnOpenRoster").addEventListener("click", () => togglePanel("roster"));
        $("btnOpenPanels").addEventListener("click", openPanelsModal);
        $("btnRailPanels").addEventListener("click", openPanelsModal);
        $("btnOpenPrefs").addEventListener("click", () => openPreferencesModal("appearance"));
        $("operatorCallBadge").addEventListener("click", () => openPreferencesModal("qrz"));
        $("lookupSourcePill").addEventListener("click", () => openPreferencesModal("qrz"));
        $("btnShortcuts").addEventListener("click", () => $("shortcutsBackdrop").classList.remove("hidden"));

        // Clicking digital VFO opens Net Editor focused on frequency
        $("vfoCluster").addEventListener("click", () => {
          if (state.activeNet) openNetModal(state.activeNet, null, "netFormFreq");
        });

        // Per-panel mini 4-way dock buttons & close buttons
        document.querySelectorAll("[data-panel-dock]").forEach((btn) => {
          btn.addEventListener("click", () => {
            const [panelKey, pos] = (btn.dataset.panelDock || "").split(":");
            if (panelKey && pos) movePanel(panelKey, pos, false);
          });
        });
        document.querySelectorAll("[data-panel-hide]").forEach((btn) => {
          btn.addEventListener("click", () => togglePanel(btn.dataset.panelHide, false));
        });

        // Panels & Layout Modal controls
        document.querySelectorAll("[data-panel-vis]").forEach((chk) => {
          chk.addEventListener("change", () => {
            const key = chk.dataset.panelVis;
            const next = sanitizePanelLayout(state.prefs.panel_layout);
            if (!next[key]) return;
            next[key] = { ...next[key], visible: chk.checked };
            savePanelLayout(next, true);
          });
        });
        document.querySelectorAll("[data-set-panel-pos]").forEach((btn) => {
          btn.addEventListener("click", () => {
            const [panelKey, pos] = (btn.dataset.setPanelPos || "").split(":");
            if (panelKey && pos) movePanel(panelKey, pos, true);
          });
        });
        document.querySelectorAll("[data-layout-preset]").forEach((btn) => {
          btn.addEventListener("click", () => applyLayoutPreset(btn.dataset.layoutPreset));
        });

        // Multi-Script Reader panel & in-net On-Air Script selector
        $("scriptSelect").addEventListener("change", () => {
          state.activeScriptId = Number($("scriptSelect").value) || null;
          syncScriptSelectors();
          renderScriptReader();
        });
        $("netScriptSelect").addEventListener("change", () => {
          state.activeScriptId = Number($("netScriptSelect").value) || null;
          syncScriptSelectors();
          togglePanel("scripts", true);
          renderScriptReader();
        });
        $("btnNetReadScript").addEventListener("click", () => togglePanel("scripts"));
        $("btnNetManageScripts").addEventListener("click", () => openPreferencesModal("script"));
        $("btnScriptZoomIn").addEventListener("click", () => {
          state.scriptZoomPct = Math.min(180, state.scriptZoomPct + 12);
          $("scriptReaderBody").style.fontSize = `${state.scriptZoomPct}%`;
        });
        $("btnScriptZoomOut").addEventListener("click", () => {
          state.scriptZoomPct = Math.max(76, state.scriptZoomPct - 12);
          $("scriptReaderBody").style.fontSize = `${state.scriptZoomPct}%`;
        });
        $("btnRgChangeFolder").addEventListener("click", () => {
          openPreferencesModal("qrz");
          setTimeout(() => $("prefRadiogramsDir").focus(), 50);
        });
        $("chkScriptRaw").addEventListener("change", renderScriptReader);
        $("btnScriptEdit").addEventListener("click", () => openPreferencesModal("script"));
        $("btnScriptCopy").addEventListener("click", async () => {
          const text = buildInterpolatedScript($("chkScriptRaw").checked);
          try {
            await navigator.clipboard.writeText(text);
            toast("NCS script copied to clipboard", "success");
          } catch {
            toast("Clipboard unavailable", "error");
          }
        });

        // Preferences modal wiring
        document.querySelectorAll("[data-toggle-secret]").forEach((btn) => {
          btn.addEventListener("click", () => {
            const inp = $(btn.dataset.toggleSecret);
            if (!inp) return;
            const reveal = inp.type === "password";
            inp.type = reveal ? "text" : "password";
            btn.textContent = reveal ? "Hide" : "Show";
            btn.setAttribute("aria-pressed", reveal ? "true" : "false");
            inp.focus();
          });
        });
        document.querySelectorAll("[data-pref-tab]").forEach((btn) => {
          btn.addEventListener("click", () => setPrefTab(btn.dataset.prefTab));
        });
        document.querySelectorAll("[data-theme-pick]").forEach((card) => {
          card.addEventListener("click", () => {
            updateThemeCardHighlight(card.dataset.themePick);
            previewDraftAppearance();
          });
        });
        ["prefUiFont", "prefMonoFont", "prefFontSize", "prefCallsignSize"].forEach((id) => {
          $(id).addEventListener("input", previewDraftAppearance);
          $(id).addEventListener("change", previewDraftAppearance);
        });
        $("prefNcsScript").addEventListener("input", updateScriptCharCount);
        $("btnNewScriptItem").addEventListener("click", () => {
          loadScriptIntoEditor(null);
          $("scriptEditName").focus();
        });
        $("btnSaveCurrentScript").addEventListener("click", saveCurrentScriptFromEditor);
        $("btnDeleteCurrentScript").addEventListener("click", deleteCurrentScriptFromEditor);
        $("prefsScriptsList").addEventListener("click", (e) => {
          const item = e.target.closest("[data-script-item-id]");
          if (!item) return;
          const found = state.scripts.find((s) => s.id === Number(item.dataset.scriptItemId));
          if (found) loadScriptIntoEditor(found);
        });
        $("scriptPlaceholderChips").addEventListener("click", (e) => {
          const chip = e.target.closest("[data-insert-tag]");
          if (!chip) return;
          const ta = $("prefNcsScript");
          const tag = chip.dataset.insertTag;
          const start = ta.selectionStart ?? ta.value.length;
          const end = ta.selectionEnd ?? ta.value.length;
          ta.value = ta.value.slice(0, start) + tag + ta.value.slice(end);
          ta.focus();
          ta.setSelectionRange(start + tag.length, start + tag.length);
          updateScriptCharCount();
        });
        $("btnTestQrz").addEventListener("click", handleTestQrz);
        $("btnTestDigitalNode").addEventListener("click", handleTestDigitalNode);
        $("prefDigWinlinkEnabled").addEventListener("change", syncDigitalRemoteVisibility);
        $("prefDigPacketEnabled").addEventListener("change", syncDigitalRemoteVisibility);
        $("prefDigMethod").addEventListener("change", syncDigitalRemoteVisibility);
        $("nwsContactCard").addEventListener("click", async (e) => {
          const btn = e.target.closest("[data-nws-contact-action]");
          if (!btn) return;
          const act = btn.dataset.nwsContactAction;
          if (act === "edit") {
            $("nwsReportBackdrop").classList.add("hidden");
            await openPreferencesModal("qrz");
            $("prefNwsWfoName")?.focus();
          } else if (act === "copy") {
            try {
              await navigator.clipboard.writeText(formatNwsContactPlainText(state.prefs.nws_contact));
              toast("NWS contact info copied to clipboard", "success");
            } catch {
              toast("Clipboard unavailable", "error");
            }
          }
        });
        $("btnRgViewCopy")?.addEventListener("click", async () => {
          try {
            await navigator.clipboard.writeText($("rgViewText").textContent || "");
            toast("Copied ARRL Radiogram text to clipboard", "success");
          } catch {
            toast("Could not copy radiogram text", "error");
          }
        });
        $("btnRgViewPrint")?.addEventListener("click", () => {
          const rg = findRadiogramById(Number($("rgViewId").value));
          if (rg) printArrlRadiogram(rg);
        });
        $("btnRgViewDigital")?.addEventListener("click", async () => {
          const rg = findRadiogramById(Number($("rgViewId").value));
          $("radiogramViewBackdrop").classList.add("hidden");
          if (rg) await openDigitalSendModal(rg);
        });
        $("btnRgViewEdit")?.addEventListener("click", () => {
          const rg = findRadiogramById(Number($("rgViewId").value));
          $("radiogramViewBackdrop").classList.add("hidden");
          if (rg) openRadiogramEditor(rg);
        });
        $("btnDigRefresh").addEventListener("click", () => {
          refreshDigitalSendModal().catch((err) => toast(err.message, "error"));
        });
        $("digPacketToAddress")?.addEventListener("change", () => {
          refreshDigitalSendModal().catch((err) => toast(err.message, "error"));
        });
        $("digToAddress")?.addEventListener("change", () => {
          refreshDigitalSendModal().catch((err) => toast(err.message, "error"));
        });
        $("digBpqType").addEventListener("change", () => {
          const curSubj = $("digSubject").value.trim().toUpperCase();
          const prevAuto = ($("digSubject").dataset.autoSubject || "").trim().toUpperCase();
          const shouldReset = !curSubj || curSubj === prevAuto;
          refreshDigitalSendModal({ resetSubject: shouldReset }).catch((err) => toast(err.message, "error"));
        });
        $("btnDigEditPrefs").addEventListener("click", () => {
          $("digitalSendBackdrop").classList.add("hidden");
          openPreferencesModal("digital");
        });
        $("btnDigCopyWinlink").addEventListener("click", async () => {
          try {
            await navigator.clipboard.writeText($("digWinlinkPreview").value);
            toast("Winlink message copied to clipboard", "success");
          } catch {
            toast("Clipboard unavailable", "error");
          }
        });
        $("btnDigCopyPacket").addEventListener("click", async () => {
          try {
            await navigator.clipboard.writeText($("digPacketPreview").value);
            toast("Packet BBS SP.../EX block copied to clipboard", "success");
          } catch {
            toast("Clipboard unavailable", "error");
          }
        });
        $("btnDigSaveTxt").addEventListener("click", async () => {
          const nr = (state.digitalDispatchData?.msg_number || $("digRgId").value || "1").replace(/[^A-Za-z0-9_-]/g, "");
          const r = state.digitalDispatchData?.routing || {};
          const usePacket = Boolean(r.packet_enabled) && !r.winlink_enabled;
          const text = usePacket ? $("digPacketPreview").value : $("digWinlinkPreview").value;
          const suffix = usePacket ? "packet" : "winlink";
          await saveTextContent(`NR_${nr}_${suffix}.txt`, text, "txt");
        });
        $("btnDigSendBpqWinlink").addEventListener("click", () => sendRadiogramViaBpqTelnet("winlink"));
        $("btnDigSendBpqPacket").addEventListener("click", () => sendRadiogramViaBpqTelnet("packet"));
        $("btnDigOpenPat").addEventListener("click", () => {
          const url = $("btnDigOpenPat").dataset.patUrl;
          if (isSafeHttpUrl(url)) window.open(url, "_blank", "noopener,noreferrer");
        });
        $("btnDigOpenWebmail").addEventListener("click", () => {
          const url = $("btnDigOpenWebmail").dataset.webmailUrl;
          if (isSafeHttpUrl(url)) window.open(url, "_blank", "noopener,noreferrer");
        });
        $("btnCancelPrefs").addEventListener("click", cancelPreferencesModal);
        $("btnCancelPrefsTop").addEventListener("click", cancelPreferencesModal);
        $("prefsModalBackdrop").addEventListener("click", (e) => {
          if (e.target === $("prefsModalBackdrop")) cancelPreferencesModal();
        });
        $("prefsForm").addEventListener("submit", handlePreferencesSubmit);

        document.querySelectorAll("[data-close-modal]").forEach((btn) => {
          btn.addEventListener("click", () => $(btn.dataset.closeModal).classList.add("hidden"));
        });

        document.querySelectorAll("[data-quick-preset]").forEach((btn) => {
          btn.addEventListener("click", () => {
            const map = { "Club VHF/UHF": "club2m", ARES: "ares", RACES: "races", Skywarn: "skywarn", NTS: "nts", HF: "hf40" };
            requestNewNet(map[btn.dataset.quickPreset]);
          });
        });

        document.querySelectorAll("[data-preset]").forEach((btn) => {
          btn.addEventListener("click", () => applyNetPreset(btn.dataset.preset));
        });

        // Interactive KPI stat chips act as quick filters
        document.querySelectorAll("[data-stat-filter]").forEach((btn) => {
          btn.addEventListener("click", () => setFilter(btn.dataset.statFilter));
        });

        $("btnWrapNotes").addEventListener("click", () => {
          state.wrapNotes = !state.wrapNotes;
          $("checkinsTable").classList.toggle("wrap-comments", state.wrapNotes);
          $("btnWrapNotes").classList.toggle("btn-active-toggle", state.wrapNotes);
        });

        function handleCommentChipClick(e, ta, getDefaultLoc, getStationCall) {
          const locChip = e.target.closest("[data-note-location]");
          if (locChip) {
            const loc = (getDefaultLoc() || "").trim();
            const start = ta.selectionStart ?? ta.value.length;
            const end = ta.selectionEnd ?? ta.value.length;
            const before = ta.value.slice(0, start);
            const after = ta.value.slice(end);
            const leadingSpace = before.length > 0 && !/[\s\n]$/.test(before) ? " " : "";
            const prefix = `${leadingSpace}[LOC: `;
            const suffix = after.startsWith(" ") || after.startsWith("\n") ? "]" : "] ";
            ta.value = before + prefix + loc + suffix + after;
            const selStart = before.length + prefix.length;
            const selEnd = selStart + loc.length;
            ta.focus();
            ta.setSelectionRange(selStart, selEnd);
            return;
          }
          const chip = e.target.closest("[data-note-stamp]");
          if (!chip) return;
          const call = (getStationCall ? getStationCall() : "").trim().toUpperCase();
          const loc = (getDefaultLoc() || "").trim();
          const tag = (chip.dataset.noteStamp || "").trim();
          const parts = [`[${currentUtcStamp()}]`];
          if (call) parts.push(call);
          if (loc) parts.push(`[LOC: ${loc}]`);
          if (tag) parts.push(tag);
          const prefix = `${parts.join(" ")} `;
          const needsNewline = ta.value.length > 0 && !ta.value.endsWith("\n");
          ta.value = ta.value + (needsNewline ? "\n" : "") + prefix;
          ta.focus();
          ta.setSelectionRange(ta.value.length, ta.value.length);
        }

        $("quickNoteChips").addEventListener("click", (e) => {
          const checkinId = Number($("quickNoteCheckinId").value);
          const c = (state.activeNet?.checkins || []).find((item) => item.id === checkinId);
          handleCommentChipClick(
            e,
            $("quickNoteText"),
            () => (c?.qth || [c?.county, c?.state].filter(Boolean).join(", ") || ""),
            () => (c?.callsign || ""),
          );
        });

        $("editCommentChips").addEventListener("click", (e) => {
          handleCommentChipClick(
            e,
            $("editComments"),
            () => ($("editQth").value || [$("editCounty").value, $("editState").value].filter(Boolean).join(", ") || ""),
            () => ($("editCallsign").value || ""),
          );
        });

        $("quickNoteForm").addEventListener("submit", async (e) => {
          e.preventDefault();
          const id = Number($("quickNoteCheckinId").value);
          if (!id) return;
          try {
            await patchCheckin(id, {
              comments: $("quickNoteText").value.trim(),
              has_traffic: $("quickNoteTraffic").checked,
            });
            $("quickNoteBackdrop").classList.add("hidden");
            toast("Station notes updated", "success");
          } catch (err) {
            toast(err.message, "error");
          }
        });

        // ARRL Radiograms drawer & editor wiring
        $("btnRgScopeNet").addEventListener("click", () => {
          if (!state.activeNet) return;
          state.rgScope = "net";
          $("btnRgScopeNet").classList.add("active");
          $("btnRgScopeAll").classList.remove("active");
          loadRadiograms();
        });
        $("btnRgScopeAll").addEventListener("click", () => {
          state.rgScope = "all";
          $("btnRgScopeNet").classList.remove("active");
          $("btnRgScopeAll").classList.add("active");
          loadRadiograms();
        });
        $("rgFilterStatus").addEventListener("change", loadRadiograms);
        $("btnRgOpenHistory").addEventListener("click", () => togglePanel("ntsHistory", true));
        $("btnMonthlyRgReport").addEventListener("click", () => openMonthlyReportModal(""));
        $("btnHistoryMonthlyReport").addEventListener("click", () => openMonthlyReportModal(""));
        $("btnRefreshNtsHistory").addEventListener("click", loadNtsHistory);
        $("ntsHistoryStatus").addEventListener("change", loadNtsHistory);
        let ntsHistorySearchTimer = null;
        $("ntsHistorySearch").addEventListener("input", () => {
          clearTimeout(ntsHistorySearchTimer);
          ntsHistorySearchTimer = setTimeout(loadNtsHistory, 180);
        });
        $("btnNewRadiogram").addEventListener("click", () => openRadiogramEditor(null));
        $("btnExportNetRadiograms").addEventListener("click", () => downloadExport("radiograms"));

        // Monthly Net Report modal wiring
        $("btnMrReload").addEventListener("click", () => {
          loadMonthlyReportPreview($("mrMonth").value).catch((err) => toast(err.message, "error"));
        });
        $("mrMonth").addEventListener("change", () => {
          loadMonthlyReportPreview($("mrMonth").value).catch((err) => toast(err.message, "error"));
        });
        [
          "mrNumber", "mrPrecedence", "mrHx", "mrOrigin", "mrPlace",
          "mrToName", "mrToCity", "mrMessageText", "mrSignature",
        ].forEach((id) => {
          $(id).addEventListener("input", formatMonthlyReportPreview);
          $(id).addEventListener("change", formatMonthlyReportPreview);
        });
        $("mrHx").addEventListener("blur", () => {
          $("mrHx").value = normalizeArrlHx($("mrHx").value);
          formatMonthlyReportPreview();
        });
        $("mrValidated").addEventListener("change", updateMonthlyReportButtons);
        $("btnMrPrint").addEventListener("click", () => {
          $("mrHx").value = normalizeArrlHx($("mrHx").value);
          formatMonthlyReportPreview();
          printArrlRadiogram(getMonthlyReportDataFromForm());
        });
        $("monthlyReportForm").addEventListener("submit", handleMonthlyReportSubmit);

        $("radiogramsList").addEventListener("click", async (e) => {
          const btn = e.target.closest("[data-rg-action]");
          if (!btn) return;
          const card = btn.closest("[data-rg-id]");
          const rg = findRadiogramById(Number(card.dataset.rgId));
          await handleRadiogramCardAction(rg, btn.dataset.rgAction);
        });

        $("ntsHistoryList").addEventListener("click", async (e) => {
          const monthBtn = e.target.closest("[data-open-month-report]");
          if (monthBtn) {
            e.preventDefault();
            e.stopPropagation();
            openMonthlyReportModal(monthBtn.dataset.openMonthReport || "");
            return;
          }
          const btn = e.target.closest("[data-rg-action]");
          if (!btn) return;
          const card = btn.closest("[data-rg-id]");
          const rg = findRadiogramById(Number(card.dataset.rgId));
          await handleRadiogramCardAction(rg, btn.dataset.rgAction);
        });

        [
          "rgNumber", "rgPrecedence", "rgHx", "rgOrigin", "rgPlace",
          "rgTimeFiled", "rgDateFiled", "rgToName", "rgToAddress", "rgToCity",
          "rgToPhone", "rgToEmail", "rgMessageText", "rgSignature",
          "rgRcvdFrom", "rgRcvdAt", "rgSentTo", "rgSentAt",
        ].forEach((id) => {
          $(id).addEventListener("input", formatRadiogramPreviewFromForm);
          $(id).addEventListener("change", formatRadiogramPreviewFromForm);
        });

        $("btnRgInsertX").addEventListener("click", () => {
          const ta = $("rgMessageText");
          const start = ta.selectionStart ?? ta.value.length;
          const end = ta.selectionEnd ?? ta.value.length;
          const sep = start > 0 && !/\s$/.test(ta.value.slice(0, start)) ? " " : "";
          const insert = `${sep}X `;
          ta.value = ta.value.slice(0, start) + insert + ta.value.slice(end);
          ta.focus();
          ta.setSelectionRange(start + insert.length, start + insert.length);
          formatRadiogramPreviewFromForm();
        });

        $("rgArlPicker").addEventListener("change", () => {
          const val = $("rgArlPicker").value;
          if (!val) return;
          const ta = $("rgMessageText");
          const prefix = ta.value.trim() ? `${ta.value.trim()} ` : "";
          ta.value = `${prefix}${val} `;
          $("rgArlPicker").value = "";
          ta.focus();
          formatRadiogramPreviewFromForm();
        });

        $("btnStampRcvd").addEventListener("click", () => {
          $("rgRcvdAt").value = currentUtcCompact();
          formatRadiogramPreviewFromForm();
        });
        $("btnStampSent").addEventListener("click", () => {
          $("rgSentAt").value = currentUtcCompact();
          formatRadiogramPreviewFromForm();
        });
        $("btnCopyRgPreview").addEventListener("click", async () => {
          try {
            await navigator.clipboard.writeText(formatRadiogramPreviewFromForm());
            toast("Official ARRL Radiogram text copied", "success");
          } catch {
            toast("Clipboard unavailable", "error");
          }
        });
        $("btnPrintRadiogram").addEventListener("click", () => {
          $("rgHx").value = normalizeArrlHx($("rgHx").value);
          formatRadiogramPreviewFromForm();
          printArrlRadiogram(getRadiogramDataFromForm());
        });
        $("rgHx").addEventListener("blur", () => {
          $("rgHx").value = normalizeArrlHx($("rgHx").value);
          formatRadiogramPreviewFromForm();
        });
        $("radiogramForm").addEventListener("submit", handleRadiogramSubmit);

        $("netsList").addEventListener("click", (e) => {
          const cloneBtn = e.target.closest("[data-clone-net]");
          if (cloneBtn) {
            e.stopPropagation();
            cloneNetSession(Number(cloneBtn.dataset.cloneNet));
            return;
          }
          const card = e.target.closest(".net-card");
          if (card) {
            closeNetsDrawer();
            selectNet(Number(card.dataset.netId));
          }
        });

        $("btnIdTimer").addEventListener("click", () => resetIdTimer(false));
        $("btnNcsHandoff").addEventListener("click", openNcsHandoffModal);
        $("ncsHandoffForm").addEventListener("submit", async (e) => {
          e.preventDefault();
          if (!state.activeNet) return;
          const newNcs = $("handoffNewNcs").value.trim().toUpperCase();
          if (!newNcs) return;
          try {
            state.activeNet = await api(`/api/nets/${state.activeNet.id}/handoff`, {
              method: "POST",
              body: JSON.stringify({
                new_ncs_call: newNcs,
                notes: $("handoffNotes").value.trim(),
              }),
            });
            $("ncsHandoffBackdrop").classList.add("hidden");
            await loadNets(state.activeNet.id);
            resetIdTimer(true);
            toast(`Net Control handed off to ${newNcs}`, "success");
          } catch (err) {
            toast(`Handoff failed: ${err.message}`, "error");
          }
        });
        $("btnCloneNet").addEventListener("click", () => {
          if (state.activeNet) cloneNetSession(state.activeNet.id);
        });
        $("btnEditNet").addEventListener("click", () => {
          if (state.activeNet) openNetModal(state.activeNet);
        });

        $("btnCloseReopenNet").addEventListener("click", async () => {
          if (!state.activeNet) return;
          const isOpen = !state.activeNet.ended_at;
          const endpoint = isOpen ? "close" : "reopen";
          try {
            const updated = await api(`/api/nets/${state.activeNet.id}/${endpoint}`, { method: "POST" });
            if (isOpen) {
              state.activeNet = null;
              state.nets = await api("/api/nets");
              renderNetsList();
              renderActiveNet();
              toast("Net secured (closed)", "success");
            } else {
              state.activeNet = updated;
              await loadNets(updated.id);
              toast("Net reopened", "success");
            }
          } catch (err) {
            toast(err.message, "error");
          }
        });

        $("netFormType").addEventListener("change", () => {
          if (!state.editingNetId) {
            applyNetTypeDefaults($("netFormType").value, false);
          }
        });

        $("btnExportCsv").addEventListener("click", () => downloadExport("csv"));
        $("btnExportAdif").addEventListener("click", () => downloadExport("adif"));
        $("btnExportIcs214").addEventListener("click", printIcs214Report);
        $("btnReportPrintIcs214").addEventListener("click", printIcs214Report);
        $("btnReportSaveIcs214").addEventListener("click", downloadIcs214Report);
        $("btnReportPushWinlink").addEventListener("click", () => pushNetReportToTarget("winlink"));
        $("btnReportPushPacket").addEventListener("click", () => pushNetReportToTarget("packet"));
        $("btnCopySummary").addEventListener("click", openNetReportModal);
        $("btnNwsReport").addEventListener("click", openNwsReportModal);
        $("btnDownloadBackup").addEventListener("click", downloadBackupArchive);
        $("btnRestoreBackup").addEventListener("click", () => $("restoreBackupFile").click());
        $("restoreBackupFile").addEventListener("change", (e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file) restoreBackupArchiveFile(file);
        });
        $("btnUpdateBanner").addEventListener("click", openUpdatesInPreferences);
        $("appVersionPill")?.addEventListener("click", openUpdatesInPreferences);
        $("btnCheckUpdate").addEventListener("click", () => checkAppUpdates(false));
        $("btnApplyUpdate").addEventListener("click", handleApplyUpdate);

        // NWS Skywarn County Weather Report modal actions
        $("nwsLocationForm").addEventListener("submit", (e) => {
          e.preventDefault();
          fetchNwsCountyReport($("nwsCountyInput").value, $("nwsStateInput").value);
        });
        $("btnNwsCopy").addEventListener("click", async () => {
          const text = state.nwsReport?.plain_text || "";
          if (!text) return;
          try {
            await navigator.clipboard.writeText(text);
            toast("NWS weather briefing copied to clipboard", "success");
          } catch {
            toast("Could not copy NWS briefing", "error");
          }
        });
        $("btnNwsAppendNotes").addEventListener("click", async () => {
          if (!state.activeNet || !state.nwsReport?.net_note_summary) return;
          const existing = (state.activeNet.notes || "").trim();
          const combined = existing
            ? `${existing}\n${state.nwsReport.net_note_summary}`
            : state.nwsReport.net_note_summary;
          try {
            const ncsCall = (state.activeNet.ncs_call || "").trim().toUpperCase();
            const ncsCheckin = (state.activeNet.checkins || []).find(
              (c) => (c.callsign || "").trim().toUpperCase() === ncsCall
            ) || (state.activeNet.checkins || [])[0];
            if (ncsCheckin) {
              const entryLine = state.nwsReport.ncs_comment_entry || state.nwsReport.net_note_summary;
              const prevComment = (ncsCheckin.comments || "").trim();
              const nextComment = prevComment ? `${prevComment}\n${entryLine}` : entryLine;
              await api(`/api/checkins/${ncsCheckin.id}`, {
                method: "PATCH",
                body: JSON.stringify({ comments: nextComment }),
              });
            }
            state.activeNet = await api(`/api/nets/${state.activeNet.id}`, {
              method: "PATCH",
              body: JSON.stringify({ notes: combined }),
            });
            renderActiveNet();
            toast("NWS summary appended to Net Notes and NCS comments", "success");
          } catch (err) {
            toast(`Could not update net notes: ${err.message}`, "error");
          }
        });

        // Net Report & Share modal actions
        $("btnReportRefresh").addEventListener("click", async () => {
          try {
            await populateNetReportModal();
            toast("Report refreshed from live log", "success");
          } catch (err) {
            toast(`Refresh failed: ${err.message}`, "error");
          }
        });
        $("btnReportCopy").addEventListener("click", async () => {
          const text = $("netReportText").value;
          try {
            await navigator.clipboard.writeText(text);
            toast("Net report copied to clipboard", "success");
          } catch {
            $("netReportText").select();
            toast("Select and press Ctrl+C to copy report", "error");
          }
        });
        $("btnReportSaveTxt").addEventListener("click", async () => {
          if (!state.activeNet) return;
          const net = state.activeNet;
          const slug = (net.name || "net").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 48) || "net";
          const datePart = (net.started_at || "").slice(0, 10);
          const filename = `net_${net.id}_${slug}_${datePart}.txt`;
          try {
            await saveTextContent(filename, $("netReportText").value, "txt");
          } catch (err) {
            toast(`Save failed: ${err.message}`, "error");
          }
        });
        $("btnReportEmail").addEventListener("click", async () => {
          if (!state.activeNet) return;
          const net = state.activeNet;
          const datePart = (net.started_at || "").slice(0, 10);
          const subject = `Net Report: ${net.name} (${datePart})`;
          const body = $("netReportText").value;
          if (body.length > 1800) {
            try {
              await navigator.clipboard.writeText(body);
            } catch {
              // ignore clipboard restriction
            }
            const shortBody = `${body.slice(0, 1600)}\n\n[Full report copied to clipboard — press Ctrl+V to paste complete log]`;
            window.location.href = `mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(shortBody)}`;
            toast("Full report copied to clipboard for email", "success");
          } else {
            window.location.href = `mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
          }
        });
        $("btnReportPrint").addEventListener("click", () => {
          if (!state.activeNet) return;
          const safeTitle = escHtml(`Net Report — ${state.activeNet.name}`);
          const safeText = escHtml($("netReportText").value);
          printHtmlViaFrame(
            `<!DOCTYPE html><html><head><title>${safeTitle}</title></head>` +
            `<body style="margin:24px; font-family:Consolas,'Courier New',monospace; font-size:12px;">` +
            `<pre style="white-space:pre-wrap; word-break:break-word;">${safeText}</pre></body></html>`
          );
        });
        $("btnReportShare").addEventListener("click", async () => {
          if (!state.activeNet || typeof navigator.share !== "function") return;
          try {
            await navigator.share({
              title: `Net Report: ${state.activeNet.name}`,
              text: $("netReportText").value,
            });
          } catch (err) {
            if (err && err.name !== "AbortError") toast(`Share failed: ${err.message}`, "error");
          }
        });

        $("netForm").addEventListener("submit", handleNetFormSubmit);
        $("checkinForm").addEventListener("submit", handleCheckinSubmit);

        $("inCallsign").addEventListener("input", () => {
          const cur = $("inCallsign");
          const pos = cur.selectionStart;
          cur.value = cur.value.toUpperCase().replace(/\s+/g, "");
          if (pos !== null) cur.setSelectionRange(pos, pos);
          checkDuplicateWarning();
          scheduleAutocomplete();
        });

        $("inCallsign").addEventListener("focusout", () => {
          setTimeout(hideAutocomplete, 160);
          performEntryLookup(false);
        });

        $("inCallsign").addEventListener("keydown", (e) => {
          if (state.autocompleteItems.length) {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              state.autocompleteIdx = (state.autocompleteIdx + 1) % state.autocompleteItems.length;
              renderAutocomplete();
              return;
            }
            if (e.key === "ArrowUp") {
              e.preventDefault();
              state.autocompleteIdx = (state.autocompleteIdx - 1 + state.autocompleteItems.length) % state.autocompleteItems.length;
              renderAutocomplete();
              return;
            }
            if ((e.key === "Tab" || e.key === "Enter") && state.autocompleteIdx >= 0) {
              e.preventDefault();
              applyAutocompleteItem(state.autocompleteItems[state.autocompleteIdx]);
              return;
            }
          }
          if ((e.key === "Tab" && !e.shiftKey) || (e.key === " " && $("inCallsign").value.trim().length >= 3)) {
            e.preventDefault();
            hideAutocomplete();
            const call = $("inCallsign").value.trim().toUpperCase();
            const alreadyPopulated = (
              call.length >= 3
              && call === state.lastLookedUpCall
              && !state.activeLookupPromise
              && Boolean($("inName").value.trim() || $("inQth").value.trim() || $("inGrid").value.trim())
            );
            if (alreadyPopulated) {
              state.jumpToStatusOnLookup = false;
              $("inStatus").focus();
              return;
            }
            if (call.length >= 3) {
              state.jumpToStatusOnLookup = true;
              $("inName").focus();
              performEntryLookup(false);
            } else {
              $("inName").focus();
            }
            return;
          }
        });

        $("inName").addEventListener("input", () => {
          state.jumpToStatusOnLookup = false;
        });

        $("callAutocomplete").addEventListener("mousedown", (e) => {
          const item = e.target.closest(".autocomplete-item");
          if (item) {
            e.preventDefault();
            const op = state.autocompleteItems[Number(item.dataset.acIdx)];
            if (op) applyAutocompleteItem(op);
          }
        });

        $("btnManualLookup").addEventListener("click", () => performEntryLookup(true));
        $("btnTrafficToggle").addEventListener("click", () => setEntryTraffic(!state.entryTraffic));
        $("inStatus").addEventListener("change", () => {
          if ($("inStatus").value === "TRAFFIC") setEntryTraffic(true);
        });

        $("btnJumpDup").addEventListener("click", () => {
          const id = Number($("btnJumpDup").dataset.targetId);
          if (!id) return;
          state.searchQuery = "";
          $("searchLog").value = "";
          setFilter("ALL");
          state.selectedCheckinId = id;
          renderCheckinsTable(id);
          const tr = document.querySelector(`tr[data-checkin-id="${id}"]`);
          if (tr) tr.scrollIntoView({ block: "center" });
        });

        document.querySelectorAll(".filter-btn[data-filter]").forEach((btn) => {
          btn.addEventListener("click", () => setFilter(btn.dataset.filter));
        });

        $("searchLog").addEventListener("input", () => {
          state.searchQuery = $("searchLog").value;
          renderCheckinsTable();
        });

        $("btnRollNext").addEventListener("click", jumpToNextUncalled);

        $("btnResetRoll").addEventListener("click", () => {
          if (!state.activeNet) return;
          state.rollCalled.set(state.activeNet.id, new Set());
          const nextRd = (state.rollRoundByNet.get(state.activeNet.id) || 1) + 1;
          state.rollRoundByNet.set(state.activeNet.id, nextRd);
          $("rollRoundLabel").textContent = `Rd ${nextRd}`;
          renderCheckinsTable();
          toast(`Started Roll-Call Round ${nextRd}`);
        });

        $("checkinsTbody").addEventListener("click", async (e) => {
          const tr = e.target.closest("tr[data-checkin-id]");
          if (!tr) return;
          const id = Number(tr.dataset.checkinId);
          state.selectedCheckinId = id;

          const actionEl = e.target.closest("[data-action]");
          if (!actionEl) {
            renderCheckinsTable();
            return;
          }
          const action = actionEl.dataset.action;
          const c = state.activeNet.checkins.find((item) => item.id === id);
          if (!c) return;

          try {
            if (action === "roll") toggleRollCall(id);
            else if (action === "quick-note") openQuickNoteModal(id);
            else if (action === "toggle-traffic") await patchCheckin(id, { has_traffic: !c.has_traffic });
            else if (action === "toggle-out") await patchCheckin(id, { checked_out: c.status !== "CHECKED_OUT" });
            else if (action === "cycle-status") {
              const curIdx = STATUS_CYCLE.indexOf(c.status);
              await patchCheckin(id, { status: STATUS_CYCLE[(curIdx + 1) % STATUS_CYCLE.length] });
            } else if (action === "edit") openEditCheckinModal(id);
            else if (action === "delete") await removeCheckin(id);
          } catch (err) {
            toast(err.message, "error");
          }
        });

        $("checkinsTbody").addEventListener("dblclick", (e) => {
          const tr = e.target.closest("tr[data-checkin-id]");
          if (tr && !e.target.closest("button, input, .status-badge")) {
            openEditCheckinModal(Number(tr.dataset.checkinId));
          }
        });

        $("editCheckinForm").addEventListener("submit", async (e) => {
          e.preventDefault();
          const id = Number($("editCheckinId").value);
          try {
            await patchCheckin(id, {
              callsign: $("editCallsign").value.trim().toUpperCase(),
              name: $("editName").value.trim(),
              qth: $("editQth").value.trim(),
              county: $("editCounty").value.trim(),
              state: $("editState").value.trim().toUpperCase(),
              grid: $("editGrid").value.trim().toUpperCase(),
              spotter_id: $("editSpotterId").value.trim().toUpperCase(),
              races_id: $("editRacesId").value.trim().toUpperCase(),
              status: $("editStatus").value,
              signal_report: $("editSignal").value.trim(),
              has_traffic: $("editTraffic").checked,
              comments: $("editComments").value.trim(),
            });
            $("editCheckinBackdrop").classList.add("hidden");
            toast("Check-in updated", "success");
          } catch (err) {
            toast(err.message, "error");
          }
        });

        $("btnEditRefreshLookup").addEventListener("click", async () => {
          const call = $("editCallsign").value.trim().toUpperCase();
          if (call.length < 3) return;
          try {
            const data = await api(`/api/lookup/${encodeURIComponent(call)}?refresh=true`);
            if (data.found) {
              $("editName").value = data.display_name || data.name || $("editName").value;
              $("editQth").value = data.qth || $("editQth").value;
              $("editCounty").value = data.county || $("editCounty").value;
              $("editState").value = data.state || $("editState").value;
              $("editGrid").value = data.grid || $("editGrid").value;
              toast(`Refreshed ${call} from ${data.source.toUpperCase()}`, "success");
            } else {
              toast(`No record found for ${call}`, "error");
            }
          } catch (err) {
            toast(err.message, "error");
          }
        });

        $("rosterSearch").addEventListener("input", () => loadRoster($("rosterSearch").value.trim()));
        $("btnRosterExportCsv").addEventListener("click", exportRosterCsv);
        $("btnRosterImportCsv").addEventListener("click", () => $("rosterCsvFile").click());
        $("rosterCsvFile").addEventListener("change", (e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file) importRosterCsvFile(file);
        });
        $("btnRosterNew").addEventListener("click", () => openRosterForm(null));
        $("btnRosterCancel").addEventListener("click", () => $("rosterEditForm").classList.add("hidden"));

        const lookupRosterCallsign = async (forceRefresh = false) => {
          const call = $("ropCall").value.trim().toUpperCase();
          if (call.length < 3) return;
          try {
            const data = await api(`/api/lookup/${encodeURIComponent(call)}?refresh=${forceRefresh ? "true" : "false"}`);
            if (data.found) {
              if (forceRefresh || !$("ropName").value.trim()) $("ropName").value = data.name || "";
              if (forceRefresh || !$("ropQth").value.trim()) $("ropQth").value = data.qth || "";
              if (forceRefresh || !$("ropGrid").value.trim()) $("ropGrid").value = data.grid || "";
              if (forceRefresh) toast(`Found ${call} (${data.source.toUpperCase()})`, "success");
            } else if (forceRefresh) {
              toast(`${call} not found`, "error");
            }
          } catch (err) {
            if (forceRefresh) toast(err.message, "error");
          }
        };

        $("btnRosterLookup").addEventListener("click", () => lookupRosterCallsign(true));
        $("ropCall").addEventListener("focusout", () => {
          if (!$("ropCall").readOnly) lookupRosterCallsign(false);
        });

        $("rosterEditForm").addEventListener("submit", async (e) => {
          e.preventDefault();
          const call = $("ropCall").value.trim().toUpperCase();
          try {
            await api(`/api/operators/${encodeURIComponent(call)}`, {
              method: "PUT",
              body: JSON.stringify({
                name: $("ropName").value.trim(),
                preferred_name: $("ropPrefName").value.trim(),
                qth: $("ropQth").value.trim(),
                grid: $("ropGrid").value.trim().toUpperCase(),
                spotter_id: $("ropSpotterId").value.trim().toUpperCase(),
                races_id: $("ropRacesId").value.trim().toUpperCase(),
                notes: $("ropNotes").value.trim(),
              }),
            });
            $("rosterEditForm").classList.add("hidden");
            await loadRoster($("rosterSearch").value.trim());
            toast(`Saved operator ${call}`, "success");
          } catch (err) {
            toast(err.message, "error");
          }
        });

        $("rosterTbody").addEventListener("click", async (e) => {
          const btn = e.target.closest("[data-roster-action]");
          if (!btn) return;
          const tr = btn.closest("tr[data-op-call]");
          const call = tr.dataset.opCall;
          const op = state.roster.find((o) => o.callsign === call);
          if (!op) return;

          const action = btn.dataset.rosterAction;
          if (action === "edit") openRosterForm(op);
          else if (action === "delete") {
            if (!confirm(`Delete ${call} from local operator roster?`)) return;
            await api(`/api/operators/${encodeURIComponent(call)}`, { method: "DELETE" });
            await loadRoster($("rosterSearch").value.trim());
            toast(`Deleted ${call}`);
          } else if (action === "checkin" && state.activeNet) {
            try {
              const created = await api(`/api/nets/${state.activeNet.id}/checkins`, {
                method: "POST",
                body: JSON.stringify({
                  callsign: op.callsign,
                  name: op.preferred_name || op.name || "",
                  qth: op.qth || "",
                  grid: op.grid || "",
                  county: op.county || "",
                  state: op.state || "",
                  status: "ACTIVE",
                  has_traffic: false,
                  comments: "",
                }),
              });
              state.activeNet.checkins.push(created);
              state.selectedCheckinId = created.id;
              renderCheckinsTable(created.id);
              toast(`Checked in ${op.callsign}`, "success");
            } catch (err) {
              toast(err.message, "error");
            }
          }
        });

        // Note: netModalBackdrop, radiogramEditorBackdrop, monthlyReportBackdrop, quickNoteBackdrop,
        // and editCheckinBackdrop are intentionally omitted so clicking outside never loses input (use Save, Cancel, or Esc).
        [
          "openNetWarnBackdrop",
          "panelsModalBackdrop", "netReportBackdrop", "nwsReportBackdrop", "ncsHandoffBackdrop", "digitalSendBackdrop", "shortcutsBackdrop",
        ].forEach((id) => {
          $(id).addEventListener("click", (e) => {
            if (e.target === $(id)) $(id).classList.add("hidden");
          });
        });

        // Global Keyboard Shortcuts
        document.addEventListener("keydown", async (e) => {
          const key = e.key;
          const lower = key.toLowerCase();

          if (key === "Escape") {
            if (anyOverlayOpen()) {
              closeAllModals();
              focusCallsign();
              return;
            }
            if (state.autocompleteItems.length) {
              hideAutocomplete();
              return;
            }
            clearCheckinEntry();
            focusCallsign();
            return;
          }

          if ((e.ctrlKey || e.metaKey) && key === "Enter" && !anyOverlayOpen() && state.activeNet) {
            e.preventDefault();
            const form = $("checkinForm");
            if (form) form.requestSubmit();
            return;
          }

          if (key === "F1") {
            e.preventDefault();
            $("shortcutsBackdrop").classList.toggle("hidden");
            return;
          }

          if (key === "F2" || (e.altKey && lower === "c")) {
            e.preventDefault();
            closeAllModals();
            focusCallsign();
            return;
          }

          if (key === "F4") {
            e.preventDefault();
            performEntryLookup(true);
            return;
          }

          if (e.altKey && lower === "n") {
            e.preventDefault();
            requestNewNet();
            return;
          }

          if (e.altKey && lower === "l") {
            e.preventDefault();
            if ($("panelsModalBackdrop").classList.contains("hidden")) openPanelsModal();
            else $("panelsModalBackdrop").classList.add("hidden");
            return;
          }

          if (e.altKey && lower === "p") {
            e.preventDefault();
            togglePanel("scripts");
            return;
          }

          if (e.altKey && lower === "g") {
            e.preventDefault();
            togglePanel("radiograms");
            return;
          }

          if (e.altKey && lower === "h") {
            e.preventDefault();
            togglePanel("ntsHistory");
            return;
          }

          if (e.altKey && lower === "i") {
            e.preventDefault();
            resetIdTimer(false);
            return;
          }

          if (e.altKey && key === ",") {
            e.preventDefault();
            if ($("prefsModalBackdrop").classList.contains("hidden")) openPreferencesModal("appearance");
            else cancelPreferencesModal();
            return;
          }

          if (e.altKey && lower === "r") {
            e.preventDefault();
            togglePanel("roster");
            return;
          }

          if (e.altKey && lower === "t") {
            e.preventDefault();
            setEntryTraffic(!state.entryTraffic);
            return;
          }

          if (e.altKey && lower === "f") {
            e.preventDefault();
            $("searchLog").focus();
            $("searchLog").select();
            return;
          }

          if (e.altKey && lower === "s") {
            e.preventDefault();
            toggleNetsDrawer();
            return;
          }

          if (anyOverlayOpen()) return;

          if (!isTypingElement(document.activeElement)) {
            if (key === "?") {
              e.preventDefault();
              $("shortcutsBackdrop").classList.remove("hidden");
              return;
            }
            if (key === "/") {
              e.preventDefault();
              focusCallsign();
              return;
            }
            if (key === "ArrowDown") {
              e.preventDefault();
              moveRowSelection(1);
              return;
            }
            if (key === "ArrowUp") {
              e.preventDefault();
              moveRowSelection(-1);
              return;
            }
            if (state.selectedCheckinId && state.activeNet) {
              const c = state.activeNet.checkins.find((item) => item.id === state.selectedCheckinId);
              if (!c) return;
              if (lower === "t") {
                e.preventDefault();
                await patchCheckin(c.id, { has_traffic: !c.has_traffic });
              } else if (lower === "m") {
                e.preventDefault();
                openQuickNoteModal(c.id);
              } else if (lower === "o") {
                e.preventDefault();
                await patchCheckin(c.id, { checked_out: c.status !== "CHECKED_OUT" });
              } else if (lower === "r") {
                e.preventDefault();
                toggleRollCall(c.id);
              } else if (lower === "e") {
                e.preventDefault();
                openEditCheckinModal(c.id);
              } else if (key === "Delete") {
                e.preventDefault();
                await removeCheckin(c.id);
              }
            }
          }
        });
        $("btnRetryBackend")?.addEventListener("click", () => checkBackendHealth(true));
        // Notify local server when window closes (backend uses a reload grace period)
        window.addEventListener("pagehide", (e) => {
          if (e.persisted) return;
          try {
            if (navigator.sendBeacon) {
              navigator.sendBeacon("/api/client/unload", "");
            } else {
              fetch("/api/client/unload", { method: "POST", keepalive: true });
            }
          } catch {
            // ignore unload network errors
          }
        });
      }

      // -----------------------------------------------------------------------
      // Boot
      // -----------------------------------------------------------------------
      function initDockResizers() {
        const stage = $("cockpitStage");
        document.querySelectorAll("[data-resize-zone]").forEach((handle) => {
          const zone = handle.dataset.resizeZone;
          let dragging = false;

          handle.addEventListener("pointerdown", (e) => {
            dragging = true;
            handle.classList.add("dragging");
            handle.setPointerCapture(e.pointerId);
          });

          handle.addEventListener("pointermove", (e) => {
            if (!dragging) return;
            const rect = stage.getBoundingClientRect();
            const root = document.documentElement;
            if (zone === "left") {
              const w = Math.max(180, Math.min(rect.width - 280, e.clientX - rect.left - 8));
              root.style.setProperty("--dock-left-w", `${Math.round(w)}px`);
            } else if (zone === "right") {
              const w = Math.max(180, Math.min(rect.width - 280, rect.right - e.clientX - 8));
              root.style.setProperty("--dock-right-w", `${Math.round(w)}px`);
            } else if (zone === "top") {
              const h = Math.max(110, Math.min(rect.height - 200, e.clientY - rect.top - 8));
              root.style.setProperty("--dock-top-h", `${Math.round(h)}px`);
            } else if (zone === "bottom") {
              const h = Math.max(110, Math.min(rect.height - 200, rect.bottom - e.clientY - 8));
              root.style.setProperty("--dock-bottom-h", `${Math.round(h)}px`);
            }
          });

          const stop = () => {
            if (!dragging) return;
            dragging = false;
            handle.classList.remove("dragging");
          };
          handle.addEventListener("pointerup", stop);
          handle.addEventListener("pointercancel", stop);
        });
      }

      async function boot() {
        bindEvents();
        initDockResizers();
        updateClocks();
        setInterval(updateClocks, 1000);
        setInterval(() => {
          if (document.visibilityState !== "hidden") checkBackendHealth(false);
        }, 10000);

        try {
          state.config = await api("/api/config");
          if (state.config.preferences) applySavedPrefs(state.config.preferences);
          else applySavedPrefs(await api("/api/preferences"));
          await loadScripts();
          state.activeNet = null;
          await loadNets();
          if (state.prefs.panel_layout?.radiograms?.visible) await syncRadiogramsPanel();
          if (state.prefs.panel_layout?.ntsHistory?.visible) await loadNtsHistory();
          if (state.prefs.panel_layout?.roster?.visible) await loadRoster("");
          renderUpdateStatus(null);
          checkAppUpdates(true);
        } catch (err) {
          toast(`Startup error: ${err.message}`, "error");
        }
      }

      boot();
    })();
