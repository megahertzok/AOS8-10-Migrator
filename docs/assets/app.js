(function () {
  "use strict";

  const state = {
    proxyUrl: "http://localhost:8765",
    aos8SessionId: null,
    centralSessionId: null,
    aps: [], // tracked AP rows from /api/aos8/aps, /api/tracking, or a CSV import
    selected: new Set(),
    unassignedSelected: new Set(),
    unassignedRows: [],
    sites: [],
    topology: [], // Mobility Controllers from /api/aos8/topology ("show switches")
    rollbackRows: [],
  };

  // ---------------------------------------------------------------- helpers

  async function api(method, path, { body, params } = {}) {
    const url = new URL(state.proxyUrl.replace(/\/$/, "") + path);
    if (params) {
      Object.entries(params).forEach(([k, v]) => v !== undefined && v !== "" && url.searchParams.set(k, v));
    }
    const resp = await fetch(url.toString(), {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      throw new Error(data.error || `Request failed (${resp.status})`);
    }
    return data;
  }

  function setStatus(elId, text, kind) {
    const el = document.getElementById(elId);
    el.textContent = text;
    el.className = "status-line" + (kind ? " " + kind : "");
  }

  function appendLog(elId, text) {
    const log = document.getElementById(elId);
    log.textContent += `[${new Date().toLocaleTimeString()}] ${text}\n`;
    log.scrollTop = log.scrollHeight;
  }

  function formatDate(unixSeconds) {
    if (!unixSeconds) return "";
    try {
      return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(unixSeconds * 1000));
    } catch (err) {
      return new Date(unixSeconds * 1000).toLocaleString();
    }
  }

  // -------------------------------------------------------------- CSV helpers

  function toCSV(rows, columns) {
    const esc = (v) => {
      const s = v === null || v === undefined ? "" : String(v);
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const lines = [columns.join(",")];
    rows.forEach((row) => lines.push(columns.map((c) => esc(row[c])).join(",")));
    return lines.join("\n");
  }

  function parseCSV(text) {
    // Minimal RFC4180-ish parser: handles quoted fields, escaped quotes, commas/newlines
    // inside quotes. No external dependency, keeps the site offline-capable.
    const rows = [];
    let row = [];
    let field = "";
    let inQuotes = false;
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (inQuotes) {
        if (c === '"') {
          if (text[i + 1] === '"') { field += '"'; i++; } else { inQuotes = false; }
        } else {
          field += c;
        }
      } else if (c === '"') {
        inQuotes = true;
      } else if (c === ",") {
        row.push(field); field = "";
      } else if (c === "\n" || c === "\r") {
        if (c === "\r" && text[i + 1] === "\n") i++;
        row.push(field); field = "";
        if (row.length > 1 || row[0] !== "") rows.push(row);
        row = [];
      } else {
        field += c;
      }
    }
    if (field !== "" || row.length) { row.push(field); rows.push(row); }
    if (!rows.length) return [];
    const headers = rows[0].map((h) => h.trim());
    return rows.slice(1).map((r) => Object.fromEntries(headers.map((h, idx) => [h, r[idx] !== undefined ? r[idx] : ""])));
  }

  function downloadCSV(filename, csvText) {
    const blob = new Blob([csvText], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  const AP_CSV_COLUMNS = ["mac", "name", "ap_group", "original_ap_group", "state", "md_ip", "md_name", "md_config_path", "serial", "ap_ip", "central_site_id", "notes"];

  // -------------------------------------------------------------- step nav

  document.getElementById("sidebar").addEventListener("click", (e) => {
    const btn = e.target.closest(".step");
    if (!btn) return;
    document.querySelectorAll(".step").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`panel-${btn.dataset.tab}`).classList.add("active");
    window.scrollTo({ top: 0 });
  });

  function markStepDone(step, done) {
    const el = document.getElementById(`check-${step}`);
    if (el) el.classList.toggle("done", !!done);
  }

  // -------------------------------------------------------------- session persistence
  // Session IDs (not credentials) are cached per-browser so a page refresh doesn't
  // orphan an active proxy-agent session. Cleared on explicit Disconnect or
  // "Clear saved data".

  const SESSION_KEY = "aos8-10-migrator-session";
  const FIELDS_KEY = "aos8-10-migrator-fields";

  // Convenience only -- so you don't retype hostnames/URLs every visit. Deliberately
  // excludes every password/secret/token field; those are never written to storage.
  const PERSISTED_FIELD_IDS = [
    "aos8Host", "aos8User", "aos8VerifyTls",
    "centralBaseUrl", "centralClientId",
    "apSshUser",
    "fwServerType", "fwHost", "fwPort", "fwPath", "fwUsername",
    "trackingAutoRefresh",
  ];

  function saveSession() {
    try {
      localStorage.setItem(
        SESSION_KEY,
        JSON.stringify({ proxyUrl: state.proxyUrl, aos8SessionId: state.aos8SessionId, centralSessionId: state.centralSessionId })
      );
    } catch (err) { /* private browsing / storage unavailable -- fine, just skip persistence */ }
  }

  function restoreSession() {
    try {
      const raw = localStorage.getItem(SESSION_KEY);
      if (!raw) return;
      const saved = JSON.parse(raw);
      if (saved.proxyUrl) {
        state.proxyUrl = saved.proxyUrl;
        document.getElementById("proxyUrl").value = saved.proxyUrl;
      }
      if (saved.aos8SessionId) {
        state.aos8SessionId = saved.aos8SessionId;
        document.getElementById("aos8Dot").className = "dot dot-on";
        setStatus("aos8Status", "Restored previous session (reload AP inventory to confirm it's still valid).", "ok");
        markStepDone("connect", true);
        fetchCountryCode();
      }
      if (saved.centralSessionId) {
        state.centralSessionId = saved.centralSessionId;
        document.getElementById("centralDot").className = "dot dot-on";
      }
    } catch (err) { /* ignore malformed/blocked storage */ }
  }

  function saveFields() {
    try {
      const values = {};
      PERSISTED_FIELD_IDS.forEach((id) => {
        const el = document.getElementById(id);
        if (!el) return;
        values[id] = el.type === "checkbox" ? el.checked : el.value;
      });
      localStorage.setItem(FIELDS_KEY, JSON.stringify(values));
    } catch (err) { /* private browsing / storage unavailable -- fine, just skip persistence */ }
  }

  function restoreFields() {
    try {
      const raw = localStorage.getItem(FIELDS_KEY);
      if (!raw) return;
      const values = JSON.parse(raw);
      PERSISTED_FIELD_IDS.forEach((id) => {
        const el = document.getElementById(id);
        if (!el || values[id] === undefined) return;
        if (el.type === "checkbox") el.checked = values[id];
        else el.value = values[id];
      });
    } catch (err) { /* ignore malformed/blocked storage */ }
  }

  function wirePersistedFields() {
    PERSISTED_FIELD_IDS.forEach((id) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.addEventListener(el.tagName === "SELECT" || el.type === "checkbox" ? "change" : "input", saveFields);
    });
  }

  document.getElementById("btnClearSavedData").addEventListener("click", () => {
    if (!confirm("Clear saved connection info (hostnames, URLs, remembered session) from this browser? This does not delete any migration history -- that lives in the proxy agent's tracking store, not here.")) return;
    try {
      localStorage.removeItem(SESSION_KEY);
      localStorage.removeItem(FIELDS_KEY);
    } catch (err) { /* ignore */ }
    location.reload();
  });

  // -------------------------------------------------------------- connect

  document.getElementById("proxyUrl").addEventListener("change", (e) => {
    state.proxyUrl = e.target.value.trim();
    saveSession();
  });

  document.getElementById("btnCheckProxy").addEventListener("click", async () => {
    state.proxyUrl = document.getElementById("proxyUrl").value.trim();
    try {
      await api("GET", "/api/health");
      setStatus("proxyStatus", "Proxy agent reachable.", "ok");
    } catch (err) {
      setStatus("proxyStatus", `Cannot reach proxy agent: ${err.message}`, "error");
    }
  });

  document.getElementById("btnAos8Connect").addEventListener("click", async () => {
    const host = document.getElementById("aos8Host").value.trim();
    const username = document.getElementById("aos8User").value.trim();
    const password = document.getElementById("aos8Pass").value;
    const verify_tls = document.getElementById("aos8VerifyTls").checked;
    if (!host || !username || !password) return setStatus("aos8Status", "Host, username, and password are required.", "error");
    try {
      const data = await api("POST", "/api/aos8/connect", { body: { host, username, password, verify_tls } });
      state.aos8SessionId = data.session_id;
      document.getElementById("aos8Dot").className = "dot dot-on";
      setStatus("aos8Status", `Connected to ${host}.`, "ok");
      markStepDone("connect", !!state.centralSessionId || true);
      saveSession();
      fetchCountryCode();
    } catch (err) {
      document.getElementById("aos8Dot").className = "dot dot-off";
      setStatus("aos8Status", `Connect failed: ${err.message}`, "error");
    }
  });

  /** Best-effort: shows the controller's configured regulatory domain / country code
   * next to the pre-flight warning that `ap convert` permanently writes it onto every
   * AP it converts. Never blocks the workflow if the lookup itself fails. */
  async function fetchCountryCode() {
    const el = document.getElementById("countryCodeValue");
    if (!state.aos8SessionId || !el) return;
    try {
      const data = await api("GET", "/api/aos8/country-code", { params: { session_id: state.aos8SessionId } });
      el.textContent = data.country_code
        ? `${data.country_code} (double-check this is correct for the APs' destination before continuing)`
        : "could not be detected automatically — confirm manually on the controller before continuing";
    } catch (err) {
      el.textContent = "could not be detected automatically — confirm manually on the controller before continuing";
    }
  }

  document.getElementById("btnAos8Disconnect").addEventListener("click", async () => {
    try {
      if (state.aos8SessionId) await api("POST", "/api/aos8/disconnect", { body: { session_id: state.aos8SessionId } });
    } catch (err) { /* best-effort */ }
    state.aos8SessionId = null;
    document.getElementById("aos8Dot").className = "dot dot-off";
    setStatus("aos8Status", "Disconnected.", "");
    markStepDone("connect", false);
    saveSession();
  });

  document.getElementById("btnCentralConnect").addEventListener("click", async () => {
    const base_url = document.getElementById("centralBaseUrl").value.trim();
    const client_id = document.getElementById("centralClientId").value.trim();
    const client_secret = document.getElementById("centralClientSecret").value;
    const access_token = document.getElementById("centralAccessToken").value;
    if (!base_url || !client_id || !client_secret || !access_token) {
      return setStatus("centralStatus", "API Gateway base URL, Client ID, Client secret, and Access token are required.", "error");
    }
    const body = { base_url, client_id, client_secret, access_token, refresh_token: document.getElementById("centralRefreshToken").value || undefined };
    try {
      const data = await api("POST", "/api/central/connect", { body });
      state.centralSessionId = data.session_id;
      document.getElementById("centralDot").className = "dot dot-on";
      setStatus("centralStatus", "Connected to Central.", "ok");
      saveSession();
    } catch (err) {
      document.getElementById("centralDot").className = "dot dot-off";
      setStatus("centralStatus", `Connect failed: ${err.message}`, "error");
    }
  });

  document.getElementById("btnCentralDisconnect").addEventListener("click", async () => {
    try {
      if (state.centralSessionId) await api("POST", "/api/central/disconnect", { body: { session_id: state.centralSessionId } });
    } catch (err) { /* best-effort */ }
    state.centralSessionId = null;
    document.getElementById("centralDot").className = "dot dot-off";
    setStatus("centralStatus", "Disconnected.", "");
    saveSession();
  });

  function apSshCreds() {
    const user = document.getElementById("apSshUser").value.trim() || document.getElementById("aos8User").value.trim();
    const pass = document.getElementById("apSshPass").value || document.getElementById("aos8Pass").value;
    return { user, pass };
  }

  // ------------------------------------------------------------ inventory

  function requireAos8() {
    if (!state.aos8SessionId) throw new Error("Connect to the AOS8 controller first (Connect tab).");
  }

  /** ap convert needs ArubaOS 8.6.0.0+ -- firmware_ok is true/false if the topology
   * endpoint could parse and compare the controller's version, or null/undefined if
   * the version string didn't parse (treated as "verify manually", not a pass). */
  function firmwareBadge(firmwareOk) {
    if (firmwareOk === true) return '<span class="status-line ok">8.6+ OK</span>';
    if (firmwareOk === false) return '<span class="status-line error">Below 8.6.0.0</span>';
    return '<span class="status-line">unknown &mdash; verify manually</span>';
  }

  async function ensureTopologyLoaded() {
    if (state.topology.length) return;
    const data = await api("GET", "/api/aos8/topology", { params: { session_id: state.aos8SessionId } });
    state.topology = data.switches || [];
  }

  document.getElementById("btnFirmwareVersionCheck").addEventListener("click", async () => {
    try {
      requireAos8();
      if (!state.selected.size) throw new Error("No APs selected — check some in the Inventory tab.");
      await ensureTopologyLoaded();
      const selectedAps = state.aps.filter((ap) => state.selected.has(ap.mac));
      const anchorIps = new Set(selectedAps.map((ap) => ap.md_ip).filter(Boolean));
      const controllers = state.topology.filter((sw) => anchorIps.has(sw.ip));
      if (!controllers.length) {
        setStatus("firmwareVersionStatus", "Couldn't match selected APs to a loaded controller — load Topology on the Inventory tab, then retry.", "error");
        return;
      }
      const failing = controllers.filter((sw) => sw.firmware_ok === false);
      const unknown = controllers.filter((sw) => sw.firmware_ok === null || sw.firmware_ok === undefined);
      const lines = controllers.map((sw) => `${sw.name || sw.ip}: ${sw.version || "unknown version"} — ${sw.firmware_ok === true ? "OK" : sw.firmware_ok === false ? "BELOW 8.6.0.0" : "unknown, verify manually"}`);
      const summary = lines.join("\n");
      if (failing.length) {
        setStatus("firmwareVersionStatus", `${failing.length} controller(s) below the minimum firmware for ap convert:\n${summary}`, "error");
      } else if (unknown.length) {
        setStatus("firmwareVersionStatus", `Couldn't confirm firmware version for ${unknown.length} controller(s) — verify manually before converting:\n${summary}`, "");
      } else {
        setStatus("firmwareVersionStatus", `All anchor controllers meet the 8.6.0.0 minimum:\n${summary}`, "ok");
      }
    } catch (err) {
      setStatus("firmwareVersionStatus", `Check failed: ${err.message}`, "error");
    }
  });

  document.getElementById("btnLoadTopology").addEventListener("click", async () => {
    try {
      requireAos8();
      const data = await api("GET", "/api/aos8/topology", { params: { session_id: state.aos8SessionId } });
      state.topology = data.switches || [];
      const tbody = document.getElementById("topologyTableBody");
      tbody.innerHTML = "";
      state.topology.forEach((sw) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${sw.name || ""}</td><td>${sw.ip || ""}</td><td>${sw.location || ""}</td><td>${sw.type || ""}</td><td>${sw.status || ""}</td><td>${sw.model || ""}</td><td>${sw.version || ""}</td><td>${firmwareBadge(sw.firmware_ok)}</td>`;
        tbody.appendChild(tr);
      });
    } catch (err) {
      alert(err.message);
    }
  });

  function renderApTable() {
    const tbody = document.getElementById("apTableBody");
    tbody.innerHTML = "";
    const groups = new Set();
    state.aps.forEach((ap) => {
      groups.add(ap.ap_group || "");
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><input type="checkbox" class="ap-check" data-mac="${ap.mac}" ${state.selected.has(ap.mac) ? "checked" : ""}></td>
        <td>${ap.name || ""}</td>
        <td>${ap.mac}</td>
        <td>${ap.serial || ""}</td>
        <td>${ap.ap_group || ""}</td>
        <td>${ap.md_name || ap.md_ip || ""}</td>
        <td><span class="state-badge state-${ap.state}">${ap.state}</span></td>
        <td>${ap.notes || ""}</td>
      `;
      tbody.appendChild(tr);
    });

    tbody.querySelectorAll(".ap-check").forEach((cb) => {
      cb.addEventListener("change", () => {
        if (cb.checked) state.selected.add(cb.dataset.mac);
        else state.selected.delete(cb.dataset.mac);
        updateSelectedCount();
      });
    });

    const bar = document.getElementById("groupFilterBar");
    bar.innerHTML = "<span class='hint-inline'>Select by group:</span>";
    groups.forEach((g) => {
      if (!g) return;
      const b = document.createElement("button");
      b.className = "secondary";
      b.textContent = g;
      b.addEventListener("click", () => {
        state.aps.filter((ap) => ap.ap_group === g).forEach((ap) => state.selected.add(ap.mac));
        renderApTable();
        updateSelectedCount();
      });
      bar.appendChild(b);
    });
  }

  function updateSelectedCount() {
    document.getElementById("selectedCountPreflight").textContent = state.selected.size;
    document.getElementById("selectedCountExecute").textContent = state.selected.size;
  }

  document.getElementById("btnLoadAps").addEventListener("click", async () => {
    try {
      requireAos8();
      const data = await api("GET", "/api/aos8/aps", { params: { session_id: state.aos8SessionId } });
      state.aps = data.tracked || [];
      renderApTable();
      markStepDone("inventory", state.aps.length > 0);
    } catch (err) {
      alert(err.message);
    }
  });

  document.getElementById("selectAllAps").addEventListener("change", (e) => {
    if (e.target.checked) state.aps.forEach((ap) => state.selected.add(ap.mac));
    else state.selected.clear();
    renderApTable();
    updateSelectedCount();
  });

  document.getElementById("btnExportApsCsv").addEventListener("click", () => {
    downloadCSV("ap-inventory.csv", toCSV(state.aps, AP_CSV_COLUMNS));
  });

  document.getElementById("btnImportCsvTrigger").addEventListener("click", () => {
    document.getElementById("csvImportInput").click();
  });

  document.getElementById("csvImportInput").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    try {
      const text = await file.text();
      const rows = parseCSV(text);
      if (!rows.length) throw new Error("CSV had no data rows.");
      const data = await api("POST", "/api/tracking/import", { body: { rows } });
      setStatus("csvImportStatus", `Imported ${data.imported} AP(s)${data.errors.length ? `, ${data.errors.length} row(s) skipped (missing mac)` : ""}.`, data.errors.length ? "error" : "ok");
      const tracked = await api("GET", "/api/tracking", {});
      state.aps = tracked;
      renderApTable();
      markStepDone("inventory", state.aps.length > 0);
    } catch (err) {
      setStatus("csvImportStatus", `Import failed: ${err.message}`, "error");
    } finally {
      e.target.value = "";
    }
  });

  // -------------------------------------------------------------- convert

  function selectedMacs() {
    return Array.from(state.selected);
  }

  /** Group the current selection by each AP's anchor MD (config_path), so a batch
   * spanning multiple Mobility Controllers is executed as one action per MD instead
   * of accidentally landing on the wrong controller (or the Mobility Master, which
   * never terminates APs and can't run AP-local actions). */
  function selectedGroups() {
    const byPath = new Map();
    state.aps
      .filter((ap) => state.selected.has(ap.mac))
      .forEach((ap) => {
        const configPath = ap.md_config_path || "/md";
        if (!byPath.has(configPath)) byPath.set(configPath, []);
        byPath.get(configPath).push(ap.mac);
      });
    return Array.from(byPath.entries()).map(([config_path, ap_names]) => ({ config_path, ap_names }));
  }

  function logGroupResults(logId, label, groups) {
    (groups.groups || []).forEach((g) => {
      appendLog(logId, `${label} @ ${g.config_path} (${g.ap_names.length} AP): ${g.error ? "ERROR " + g.error : JSON.stringify(g.result)}`);
    });
  }

  document.getElementById("btnConvertAdd").addEventListener("click", async () => {
    try {
      requireAos8();
      const groups = selectedGroups();
      if (!groups.length) throw new Error("No APs selected — check some in the Inventory tab.");
      const data = await api("POST", "/api/aos8/convert/add", { body: { session_id: state.aos8SessionId, groups } });
      logGroupResults("preflightLog", "Add", data);
    } catch (err) {
      appendLog("preflightLog", `ERROR: ${err.message}`);
    }
  });

  document.getElementById("btnConvertPrevalidate").addEventListener("click", async () => {
    try {
      requireAos8();
      const groups = selectedGroups();
      const data = await api("POST", "/api/aos8/convert/prevalidate", { body: { session_id: state.aos8SessionId, groups } });
      logGroupResults("preflightLog", "Pre-validate", data);
    } catch (err) {
      appendLog("preflightLog", `ERROR: ${err.message}`);
    }
  });

  document.getElementById("btnConvertStatus").addEventListener("click", async () => {
    try {
      requireAos8();
      const groups = selectedGroups();
      const configPaths = groups.length ? groups.map((g) => g.config_path) : ["/md"];
      for (const config_path of configPaths) {
        const data = await api("GET", "/api/aos8/convert/status", {
          params: { session_id: state.aos8SessionId, config_path },
        });
        appendLog("preflightLog", `Status @ ${config_path}: ${JSON.stringify(data)}`);
      }
    } catch (err) {
      appendLog("preflightLog", `ERROR: ${err.message}`);
    }
  });

  function firmwareParams() {
    return {
      server_type: document.getElementById("fwServerType").value,
      host: document.getElementById("fwHost").value.trim(),
      port: document.getElementById("fwPort").value ? Number(document.getElementById("fwPort").value) : undefined,
      path: document.getElementById("fwPath").value.trim(),
      filename: document.getElementById("fwFilename").value.trim(),
      username: document.getElementById("fwUsername").value.trim(),
      password: document.getElementById("fwPassword").value,
    };
  }

  document.getElementById("btnFirmwareCheck").addEventListener("click", async () => {
    const fw = firmwareParams();
    try {
      const body = { ...fw };
      if (fw.server_type === "local-flash") {
        requireAos8();
        body.session_id = state.aos8SessionId;
        const groups = selectedGroups();
        body.config_path = groups[0] ? groups[0].config_path : "/md";
      }
      const data = await api("POST", "/api/aos8/firmware-check", { body });
      setStatus("firmwareCheckStatus", data.reachable ? `Reachable: ${data.detail || ""}` : `Not reachable: ${data.error || data.detail || ""}`, data.reachable ? "ok" : "error");
      if (data.raw) appendLog("preflightLog", `Storage listing: ${JSON.stringify(data.raw)}`);
    } catch (err) {
      setStatus("firmwareCheckStatus", `Check failed: ${err.message}`, "error");
    }
  });

  const ackCountryCodeBox = document.getElementById("ackCountryCode");
  const btnConvertExecuteEl = document.getElementById("btnConvertExecute");
  function syncExecuteButtonState() {
    btnConvertExecuteEl.disabled = !ackCountryCodeBox.checked;
  }
  ackCountryCodeBox.addEventListener("change", syncExecuteButtonState);
  syncExecuteButtonState();

  document.getElementById("btnConvertExecute").addEventListener("click", async () => {
    if (!ackCountryCodeBox.checked) return alert("Check the country-code acknowledgement in Step 3 before executing.");
    if (!confirm(`Execute conversion for ${state.selected.size} AP(s)? This reboots them into AOS10 and permanently writes the controller's country code onto each one.`)) return;
    try {
      requireAos8();
      const groups = selectedGroups();
      const firmware = firmwareParams();
      const data = await api("POST", "/api/aos8/convert/execute", { body: { session_id: state.aos8SessionId, groups, firmware } });
      logGroupResults("convertLog", "Execute", data);
    } catch (err) {
      appendLog("convertLog", `ERROR: ${err.message}`);
    }
  });

  document.getElementById("btnConvertCancel").addEventListener("click", async () => {
    try {
      requireAos8();
      const groups = selectedGroups();
      const data = await api("POST", "/api/aos8/convert/cancel", { body: { session_id: state.aos8SessionId, groups } });
      logGroupResults("convertLog", "Cancel", data);
    } catch (err) {
      appendLog("convertLog", `ERROR: ${err.message}`);
    }
  });

  // ----------------------------------------------------- post-migration verify

  document.getElementById("btnVerifyCentral").addEventListener("click", async () => {
    if (!state.centralSessionId) return alert("Connect to Central first (Connect tab).");
    const rows = state.aps.filter((ap) => state.selected.has(ap.mac) && ap.serial);
    if (!rows.length) return alert("Select at least one AP with a known serial.");
    const tbody = document.getElementById("verifyTableBody");
    tbody.innerHTML = "";
    for (const ap of rows) {
      let result;
      try {
        result = await api("POST", "/api/central/verify", {
          body: { session_id: state.centralSessionId, serial: ap.serial, expected_name: ap.name, expected_site_id: ap.central_site_id },
        });
      } catch (err) {
        result = { found: false, online: false, name_match: false, site_match: false, error: err.message };
      }
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${ap.mac}</td><td>${ap.serial}</td>
        <td>${result.found ? "yes" : "no"}</td>
        <td>${result.online ? "yes" : "no"}</td>
        <td>${result.name_match ? "yes" : "no"}</td>
        <td>${result.site_match ? "yes" : "no"}</td>
      `;
      tbody.appendChild(tr);
    }
  });

  // -------------------------------------------------------------- rollback

  document.getElementById("btnLoadRollbackScope").addEventListener("click", async () => {
    const scope = document.getElementById("rollbackScope").value;
    let value;
    if (scope === "ap") {
      const macs = selectedMacs();
      if (!macs.length) return alert("Select an AP in the Inventory tab first.");
      value = macs[0];
    } else if (scope === "group") {
      value = document.getElementById("rollbackGroupValue").value.trim();
      if (!value) return alert("Enter an AP group name.");
    } else {
      value = document.getElementById("rollbackSiteValue").value;
      if (!value) return alert("Load Central sites (Site Assignment tab) and pick one.");
    }
    try {
      const rows = await api("POST", "/api/tracking/rollback-scope", { body: { scope, value } });
      state.rollbackRows = rows;
      const tbody = document.getElementById("rollbackScopeTableBody");
      tbody.innerHTML = "";
      rows.forEach((ap) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${ap.mac}</td><td>${ap.name || ""}</td><td>${ap.original_ap_group || ""}</td><td><span class="state-badge state-${ap.state}">${ap.state}</span></td><td>${ap.md_name || ap.md_ip || ""}</td>`;
        tbody.appendChild(tr);
      });
      appendLog("rollbackLog", `Loaded ${rows.length} AP(s) for scope "${scope}"=${value}.`);
    } catch (err) {
      alert(err.message);
    }
  });

  document.getElementById("btnRollbackExecute").addEventListener("click", async () => {
    if (!state.rollbackRows.length) return alert("Load a rollback scope first.");
    if (!confirm(`Roll back ${state.rollbackRows.length} AP(s)? In-flight conversions are cancelled; already-converted APs are reverted to Campus AP mode over SSH.`)) return;

    const { user: sshUser, pass: sshPass } = apSshCreds();

    for (const ap of state.rollbackRows) {
      if (ap.state === "converting" || ap.state === "pre_validated" || ap.state === "discovered") {
        try {
          requireAos8();
          const data = await api("POST", "/api/aos8/convert/cancel", {
            body: { session_id: state.aos8SessionId, groups: [{ config_path: ap.md_config_path || "/md", ap_names: [ap.mac] }] },
          });
          logGroupResults("rollbackLog", `Cancel ${ap.mac}`, data);
        } catch (err) {
          appendLog("rollbackLog", `${ap.mac}: cancel failed — ${err.message}`);
        }
        continue;
      }

      // Already converted -- SSH revert. Prefer Central's current IP over the
      // pre-migration ap_ip, since the AP typically gets a new DHCP lease post-conversion.
      let ap_host = ap.ap_ip;
      if (state.centralSessionId && ap.serial) {
        try {
          const verify = await api("POST", "/api/central/verify", { body: { session_id: state.centralSessionId, serial: ap.serial } });
          if (verify.current_ip) ap_host = verify.current_ip;
        } catch (err) {
          appendLog("rollbackLog", `${ap.mac}: Central IP lookup failed (${err.message}), falling back to last-known IP ${ap.ap_ip || "(none)"}`);
        }
      }
      if (!ap_host) {
        appendLog("rollbackLog", `${ap.mac}: no known IP to SSH to — skipped. Refresh AP inventory or connect Central to resolve this.`);
        continue;
      }
      if (!ap.md_ip) {
        appendLog("rollbackLog", `${ap.mac}: no anchor-controller IP recorded — skipped.`);
        continue;
      }
      try {
        const data = await api("POST", "/api/aos8/rollback", {
          body: {
            ap_host,
            ap_username: sshUser,
            ap_password: sshPass,
            controller_address: ap.md_ip,
            mac: ap.mac,
            aos8_session_id: state.aos8SessionId,
            config_path: ap.md_config_path,
          },
        });
        appendLog("rollbackLog", `${ap.mac}: reverted via SSH to ${ap_host} → ${ap.md_ip}. ${JSON.stringify(data)}`);
      } catch (err) {
        appendLog("rollbackLog", `${ap.mac}: SSH rollback failed — ${err.message}`);
      }
    }
  });

  // ------------------------------------------------------------- tracking

  function renderTrackingTable(rows) {
    const tbody = document.getElementById("trackingTableBody");
    tbody.innerHTML = "";
    rows.forEach((ap) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${ap.mac}</td>
        <td>${ap.serial || ""}</td>
        <td>${ap.name || ""}</td>
        <td>${ap.ap_group || ""}</td>
        <td>${ap.original_ap_group || ""}</td>
        <td>${ap.md_name || ap.md_ip || ""}</td>
        <td><span class="state-badge state-${ap.state}">${ap.state}</span></td>
        <td>${formatDate(ap.last_updated)}</td>
        <td>${ap.notes || ""}</td>
      `;
      tbody.appendChild(tr);
    });
    return rows;
  }

  let lastTrackingRows = [];

  document.getElementById("btnLoadTracking").addEventListener("click", async () => {
    try {
      const state_filter = document.getElementById("trackingStateFilter").value;
      const rows = await api("GET", "/api/tracking", { params: { state: state_filter } });
      lastTrackingRows = rows;
      renderTrackingTable(rows);
    } catch (err) {
      alert(err.message);
    }
  });

  document.getElementById("btnExportTrackingCsv").addEventListener("click", () => {
    downloadCSV("migration-tracking.csv", toCSV(lastTrackingRows, AP_CSV_COLUMNS.concat(["last_updated"])));
  });

  // -------------------------------------------------------------- sites

  function renderUnassignedTable(rows) {
    state.unassignedRows = rows;
    const tbody = document.getElementById("unassignedTableBody");
    tbody.innerHTML = "";
    state.unassignedSelected.clear();
    rows.forEach((ap) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><input type="checkbox" class="unassigned-check" data-mac="${ap.mac}" ${ap.serial ? "" : "disabled title='No serial captured for this AP yet — reload AP inventory after conversion.'"}></td>
        <td>${ap.serial || "<em>missing</em>"}</td>
        <td>${ap.mac}</td>
        <td>${ap.name || ""}</td>
        <td>${ap.ap_group || ""}</td>
      `;
      tbody.appendChild(tr);
    });
    tbody.querySelectorAll(".unassigned-check").forEach((cb) => {
      cb.addEventListener("change", () => {
        if (cb.checked) state.unassignedSelected.add(cb.dataset.mac);
        else state.unassignedSelected.delete(cb.dataset.mac);
      });
    });
  }

  document.getElementById("btnLoadUnassigned").addEventListener("click", async () => {
    try {
      const rows = await api("GET", "/api/tracking", { params: { state: "converted_unassigned" } });
      renderUnassignedTable(rows);
    } catch (err) {
      alert(err.message);
    }
  });

  document.getElementById("btnExportUnassignedCsv").addEventListener("click", () => {
    downloadCSV("unassigned-aps.csv", toCSV(state.unassignedRows, AP_CSV_COLUMNS));
  });

  document.getElementById("selectAllUnassigned").addEventListener("change", (e) => {
    document.querySelectorAll(".unassigned-check:not(:disabled)").forEach((cb) => {
      cb.checked = e.target.checked;
      if (e.target.checked) state.unassignedSelected.add(cb.dataset.mac);
      else state.unassignedSelected.delete(cb.dataset.mac);
    });
  });

  function populateSiteSelects() {
    [document.getElementById("siteSelect"), document.getElementById("rollbackSiteValue")].forEach((select) => {
      select.innerHTML = "";
      state.sites.forEach((site) => {
        const opt = document.createElement("option");
        opt.value = site.site_id || site.id;
        opt.textContent = site.site_name || site.name || opt.value;
        select.appendChild(opt);
      });
    });
  }

  document.getElementById("btnLoadSites").addEventListener("click", async () => {
    try {
      if (!state.centralSessionId) throw new Error("Connect to Central first (Connect tab).");
      const data = await api("GET", "/api/central/sites", { params: { session_id: state.centralSessionId } });
      state.sites = data.sites || data.data || (Array.isArray(data) ? data : []);
      populateSiteSelects();
    } catch (err) {
      alert(err.message);
    }
  });

  document.getElementById("btnAssignSite").addEventListener("click", async () => {
    const site_id = document.getElementById("siteSelect").value;
    const device_type = document.getElementById("deviceTypeSelect").value;
    const macs = Array.from(state.unassignedSelected);
    if (!macs.length) return alert("Select at least one AP.");
    if (!site_id) return alert("Load and pick a target site first.");
    const rowsByMac = new Map(state.unassignedRows.map((r) => [r.mac, r]));
    const serials = macs.map((mac) => rowsByMac.get(mac)?.serial).filter(Boolean);
    if (serials.length !== macs.length) {
      return alert("One or more selected APs is missing a serial number — reload AP inventory (Inventory tab) after conversion so serials are captured, then reload this list.");
    }
    try {
      const data = await api("POST", "/api/central/devices/assign-site", {
        body: { session_id: state.centralSessionId, serials, macs, site_id, device_type },
      });
      setStatus("assignStatus", `Assigned ${serials.length} AP(s) to site: ${JSON.stringify(data)}`, "ok");
      document.querySelectorAll(".unassigned-check:checked").forEach((cb) => {
        const row = state.unassignedRows.find((r) => r.mac === cb.dataset.mac);
        if (row) row.central_site_id = site_id;
      });
    } catch (err) {
      setStatus("assignStatus", `Assignment failed: ${err.message}`, "error");
    }
  });

  // -------------------------------------------------------------- debug console

  let debugPollTimer = null;
  let debugSince = 0;

  function renderDebugEntries(entries) {
    if (!entries.length) return;
    const log = document.getElementById("debugLogBody");
    entries.forEach((e) => {
      debugSince = Math.max(debugSince, e.ts);
      const time = new Date(e.ts * 1000).toLocaleTimeString();
      log.textContent += `[${time}] [${e.category}] ${e.message}\n`;
    });
    log.scrollTop = log.scrollHeight;
  }

  async function pollDebugLog() {
    try {
      const data = await api("GET", "/api/debug/log", { params: { since: debugSince || undefined } });
      document.getElementById("debugModeToggle").checked = data.enabled;
      renderDebugEntries(data.entries || []);
    } catch (err) { /* proxy agent not reachable yet -- stay quiet, the Connect step surfaces this */ }
  }

  document.getElementById("btnToggleDebug").addEventListener("click", () => {
    const drawer = document.getElementById("debugDrawer");
    drawer.hidden = !drawer.hidden;
    if (!drawer.hidden) {
      pollDebugLog();
      if (!debugPollTimer) debugPollTimer = setInterval(pollDebugLog, 3000);
    }
  });

  document.getElementById("btnCloseDebug").addEventListener("click", () => {
    document.getElementById("debugDrawer").hidden = true;
  });

  document.getElementById("debugModeToggle").addEventListener("change", async (e) => {
    try {
      await api("POST", "/api/debug/mode", { body: { enabled: e.target.checked } });
    } catch (err) {
      alert(`Could not change debug mode: ${err.message}`);
      e.target.checked = !e.target.checked;
    }
  });

  // -------------------------------------------------------------- tracking auto-refresh

  let trackingAutoTimer = null;

  function armTrackingAutoRefresh() {
    if (trackingAutoTimer) { clearInterval(trackingAutoTimer); trackingAutoTimer = null; }
    const seconds = Number(document.getElementById("trackingAutoRefresh").value);
    if (seconds > 0) {
      trackingAutoTimer = setInterval(() => document.getElementById("btnLoadTracking").click(), seconds * 1000);
    }
  }

  document.getElementById("trackingAutoRefresh").addEventListener("change", armTrackingAutoRefresh);

  // -------------------------------------------------------------- init

  restoreSession();
  restoreFields();
  wirePersistedFields();
  armTrackingAutoRefresh(); // in case a saved auto-refresh interval was just restored
  pollDebugLog();
})();
