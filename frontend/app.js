const baysGrid = document.getElementById("baysGrid");
const refreshButton = document.getElementById("refreshButton");
const apiStatus = document.getElementById("apiStatus");
const lastUpdated = document.getElementById("lastUpdated");
const mainTabs = document.getElementById("mainTabs");

// Batch / Safety Mode Elements
const batchSelectToggleBtn = document.getElementById("batchSelectToggleBtn");
const batchActionFooter = document.getElementById("batchActionFooter");
const selectedCountLabel = document.getElementById("selectedCountLabel");
const openBatchWipeModalBtn = document.getElementById("openBatchWipeModalBtn");

// Modal Elements
const batchWipeModal = document.getElementById("batchWipeModal");
const batchEraseForm = document.getElementById("batchEraseForm");
const selectedDrivesConfigList = document.getElementById("selectedDrivesConfigList");
const dynamicConfirmationHint = document.getElementById("dynamicConfirmationHint");
const confirmationText = document.getElementById("confirmationText");

// Detail Elements
const bayDetailModal = document.getElementById("bayDetailModal");
const bayDetailContent = document.getElementById("bayDetailContent");

// Ledger Elements
const historyList = document.getElementById("historyList");
const historyQuery = document.getElementById("historyQuery");
const historyStatusFilter = document.getElementById("historyStatusFilter");
const historyRefreshButton = document.getElementById("historyRefreshButton");

// Admin Panel Elements
const testWebhookBtn = document.getElementById("testWebhookBtn");
const webhookTestResult = document.getElementById("webhookTestResult");
const exportCsvBtn = document.getElementById("exportCsvBtn");
const downloadBundleBtn = document.getElementById("downloadBundleBtn");
const bayMappingContainer = document.getElementById("bayMappingContainer");
const saveBayMapBtn = document.getElementById("saveBayMapBtn");
const addBayBtn = document.getElementById("addBayBtn");

// Metric Elements
const metricDiskBar = document.getElementById("metricDiskBar");
const metricDiskText = document.getElementById("metricDiskText");
const metricRamBar = document.getElementById("metricRamBar");
const metricRamText = document.getElementById("metricRamText");
const metricCpuBar = document.getElementById("metricCpuBar");
const metricCpuText = document.getElementById("metricCpuText");
const metricUptimeText = document.getElementById("metricUptimeText");

// Auth Overlay Elements
const authOverlay = document.getElementById("authOverlay");
const authForm = document.getElementById("authForm");
const authPassphrase = document.getElementById("authPassphrase");
const authErrorMsg = document.getElementById("authErrorMsg");

let currentDrives = [];
let currentHistoryJobs = [];
let selectedBays = new Set();
let isBatchMode = false;
let ledgerExpandedJobs = new Set(); 
let localBayMapCopy = {};

const POLL_INTERVAL_MS = 2000;
const METHOD_ORDER = ["crypto", "block", "enhanced_secure_erase", "secure_erase", "overwrite"];

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatIsoDate(value) {
  if (!value) return "-";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
}

function formatTraffic(drive, type) {
  const smart = drive.smart || {};
  let totalBytes = type === 'read' ? smart.data_read_bytes : smart.data_written_bytes;
  
  if (totalBytes === null || totalBytes === undefined || isNaN(totalBytes)) {
    const raw = type === 'read' ? smart.data_read_raw : smart.data_written_raw;
    if (raw === null || raw === undefined || isNaN(raw)) return "N/A";
    const iface = String(drive.interface_type || "sata").toLowerCase();
    totalBytes = iface.includes("nvme") ? raw * 512000 : raw * 512;
  }
  
  if (totalBytes === 0) return "0 B";
  const k = 1024;
  const sizes = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB'];
  const i = Math.floor(Math.log(totalBytes) / Math.log(k));
  return parseFloat((totalBytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

function formatPowerOnTime(hours) {
  if (hours === null || hours === undefined || isNaN(hours) || hours === 0) return "-";
  const h = Number(hours);
  const days = (h / 24).toFixed(1);
  return `${h.toLocaleString()} hrs (${days} days)`;
}

function computeRecommendedMethod(drive) {
  const supported_methods = Array.isArray(drive?.supported_methods) ? drive.supported_methods : [];
  for (const method of METHOD_ORDER) {
    if (supported_methods.includes(method)) return method;
  }
  return "overwrite";
}

function calculateDriveHealthScore(drive) {
  if (!drive || !drive.present) return 0;

  if (drive.health_score !== undefined && drive.health_score !== null) {
    return drive.health_score;
  }

  const smart = drive.smart || {};
  if (Object.keys(smart).length === 0) return 0;

  let health = 100;
  const iface = String(drive.interface_type || "").toLowerCase();
  const isSsd = String(drive.model || "").toLowerCase().includes("ssd") || iface.includes("nvme") || smart.wear_level !== null;

  if (isSsd && smart.wear_level !== null) {
    let base = iface.includes("nvme") || iface.includes("sas") ? 100 - smart.wear_level : smart.wear_level;
    
    const poh = smart.power_on_hours || 0;
    if (poh > 40000) {
      const ssdPohPenalty = Math.min(20, ((poh - 40000) / 40000) * 20);
      base = Math.max(10, base - ssdPohPenalty);
    }
    health = base;
  } else {
    const poh = smart.power_on_hours || 0;
    let pohPenalty = 0;
    if (poh > 20000) {
      pohPenalty = Math.min(30, ((poh - 20000) / 40000) * 30);
    }
    
    const rawWritten = smart.data_written_raw || 0;
    const capacityBytes = smart.capacity_bytes || 1;
    const writtenBytes = rawWritten * 512;
    const fdw = writtenBytes / capacityBytes;
    const fdwPenalty = Math.min(30, (fdw / 150.0) * 30);

    health = Math.max(40, 100 - poh_penalty - fdwPenalty);
  }

  const reallocated = smart.reallocated_sectors || 0;
  const pending = smart.pending_sectors || 0;

  if (isSsd) {
    const reallocNorm = smart.reallocated_normalized;
    if (reallocNorm !== undefined && reallocNorm !== null && reallocNorm < 100) {
      health -= Math.min(40, (100 - reallocNorm) * 1.0);
    }
  } else {
    if (reallocated > 0) {
      let penalty = 0;
      if (reallocated === 1) {
        penalty = 10;
      } else if (reallocated <= 5) {
        penalty = 10 + (reallocated - 1) * 5;
      } else {
        penalty = 10 + 20 + (reallocated - 5) * 10;
      }
      health -= Math.min(40, penalty);
    }
  }

  health -= Math.min(60, pending * 15);

  if (smart.interface_errors > 50) {
    health -= 10;
  }

  if (String(drive.status).toUpperCase() === "FAILED") {
    health = Math.min(health, 5);
  }

  return Math.max(0, Math.min(100, Math.round(health)));
}

// --- GATEWAY AUTH SECURITY CONTROLLERS ---

async function safeFetch(url, options = {}) {
  const response = await fetch(url, options);
  if (response.status === 401) {
    if (!url.includes("/api/auth/verify")) {
      showAuthOverlay();
      throw new Error("Authorization Required");
    }
  }
  return response;
}

function showAuthOverlay() {
  authOverlay.classList.remove("hidden");
  authPassphrase.focus();
}

function hideAuthOverlay() {
  authOverlay.classList.add("hidden");
  authErrorMsg.classList.add("hidden");
  authPassphrase.value = "";
}

authForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const passphrase = authPassphrase.value;
  try {
    const response = await fetch("/api/auth/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ passphrase })
    });
    if (response.ok) {
      hideAuthOverlay();
      loadSecurityStatus();
      loadDrives();
    } else {
      authErrorMsg.classList.remove("hidden");
    }
  } catch (err) {
    authErrorMsg.classList.remove("hidden");
  }
});


// Unconditional async poller for running status updates
async function pollActiveWipes() {
  while (true) {
    await loadDrives(true); 
    
    const adminTab = document.querySelector('[data-tab="adminPanel"]');
    if (adminTab && adminTab.classList.contains("active")) {
      await loadAdminMetrics();
    }
    
    await sleep(POLL_INTERVAL_MS);
  }
}

async function loadDrives(silent = false) {
  try {
    if (!silent) apiStatus.textContent = "API Status: Loading...";
    const response = await safeFetch("/api/drives");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const drives = await response.json();
    currentDrives = Array.isArray(drives) ? drives : [];
    renderBays(currentDrives);
    if (!silent) {
      apiStatus.textContent = "API Status: Ready";
      lastUpdated.textContent = `Last updated: ${new Date().toLocaleTimeString()}`;
    }
  } catch (error) {
    if (!silent) apiStatus.textContent = `API Status: Error (${error.message})`;
  }
}

function renderBays(drives) {
  baysGrid.innerHTML = drives.map((drive) => {
    const isReady = drive.present && !drive.locked && drive.role !== "os" && drive.role !== "reserved";
    const isEmpty = !drive.present;
    const isCritical = String(drive.status).toUpperCase() === "FAILED";
    const isRunning = String(drive.status).toUpperCase() === "RUNNING";
    const isCompleted = drive.marker && drive.marker.status !== "none" && drive.marker.status !== "corrupted";
    
    let stateClass = "healthy";
    let bannerLabel = "READY / UNPROCESSED";

    if (isEmpty) {
      stateClass = "empty";
      bannerLabel = "EMPTY BAY";
    } else if (drive.locked || drive.role === "os") {
      stateClass = "locked";
      bannerLabel = "VIEW ONLY - OS DRIVE";
    } else if (isCritical) {
      stateClass = "failed";
      bannerLabel = "⚠️ CRITICAL FAILURE";
    } else if (isRunning) {
      stateClass = "running";
      bannerLabel = "WIPING IN PROGRESS";
    } else if (isCompleted) {
      stateClass = "completed";
      bannerLabel = "SANITIZED & VERIFIED";
    }

    const healthScore = calculateDriveHealthScore(drive);
    const classes = ["bay-card", stateClass];
    if (selectedBays.has(drive.bay)) classes.push("selected");

    const ifaceLabel = drive.interface_type ? drive.interface_type.toUpperCase() : "SATA";
    const badgeClass = ifaceLabel.includes("NVME") ? "badge-nvme" : ifaceLabel.includes("SAS") ? "badge-sas" : "badge-sata";

    const progressPercent = drive.progress_percent !== undefined ? drive.progress_percent : 0.0;
    const phaseLabel = drive.current_phase || "Sanitizing...";

    // Dynamically append display labels adjacent to headers
    const displayLabel = drive.label && drive.label !== drive.bay ? ` (${drive.label})` : "";

    return `
      <article class="${classes.join(" ")}" data-bay="${escapeHtml(drive.bay)}">
        <input type="checkbox" class="card-checkbox" data-checkbox-bay="${escapeHtml(drive.bay)}" ${selectedBays.has(drive.bay) ? "checked" : ""} ${isBatchMode && isReady ? 'style="display: block;"' : ""}>
        <div class="bay-banner">${escapeHtml(bannerLabel)}</div>
        <div class="bay-header-row">
          <div class="bay-number">
            ${escapeHtml(drive.bay.toUpperCase())}
            <span style="font-size: 0.72rem; font-weight: normal; opacity: 0.7;">${escapeHtml(displayLabel)}</span>
          </div>
          ${isEmpty ? "" : `<div class="drive-type-badge ${badgeClass}">${escapeHtml(ifaceLabel)}</div>`}
        </div>
        ${isEmpty ? `<div class="empty-label">— Empty slot —</div>` : `
          <div class="drive-model">${escapeHtml(drive.model || "Generic Drive")}</div>
          <div class="drive-serial">S/N: ${escapeHtml(drive.serial || "-")}</div>
          
          ${isRunning ? `
            <!-- Live Progress Bar Mode -->
            <div class="health-label">
              <span style="color: var(--color-primary); font-weight: bold;">${escapeHtml(phaseLabel)}</span>
              <span style="color: var(--color-primary); font-weight: bold;">${progressPercent}%</span>
            </div>
            <div class="health-bar-track">
              <div class="health-bar-fill fill-blue" style="width: ${progressPercent}%"></div>
            </div>
          ` : `
            <!-- Standard Health Bar Mode -->
            <div class="health-label">
              <span>Life Expectancy</span>
              <span>${healthScore}%</span>
            </div>
            <div class="health-bar-track">
              <div class="health-bar-fill ${healthScore > 75 ? 'fill-green' : healthScore > 40 ? 'fill-yellow' : 'fill-red'}" style="width: ${healthScore}%"></div>
            </div>
          `}
          
          <div class="drive-meta">
            <span>${escapeHtml(drive.capacity_str)}</span>
            <span>${escapeHtml(drive.device || "-")}</span>
          </div>
        `}
      </article>
    `;
  }).join("");
}

baysGrid.addEventListener("click", (event) => {
  const checkbox = event.target.closest(".card-checkbox");
  if (checkbox) {
    const bay = checkbox.getAttribute("data-checkbox-bay");
    toggleBaySelection(bay);
    return;
  }

  const card = event.target.closest("[data-bay]");
  if (!card) return;
  const bay = card.getAttribute("data-bay");
  const drive = currentDrives.find((d) => d.bay === bay);

  if (isBatchMode) {
    const isReady = drive && drive.present && !drive.locked && drive.role !== "os" && drive.role !== "reserved";
    if (isReady) {
      toggleBaySelection(bay);
    }
  } else {
    renderLiveDetails(drive);
    openModal(bayDetailModal);
  }
});

function toggleBaySelection(bay) {
  if (selectedBays.has(bay)) {
    selectedBays.delete(bay);
  } else {
    selectedBays.add(bay);
  }
  selectedCountLabel.textContent = `${selectedBays.size} Bay(s) Staged`;
  batchActionFooter.classList.toggle("hidden", selectedBays.size === 0);
  renderBays(currentDrives);
}

batchSelectToggleBtn.addEventListener("click", () => {
  isBatchMode = !isBatchMode;
  batchSelectToggleBtn.classList.toggle("active", isBatchMode);
  batchSelectToggleBtn.textContent = isBatchMode ? "Sanitize Mode: ACTIVE" : "Sanitize Mode: OFF";
  if (!isBatchMode) {
    selectedBays.clear();
    batchActionFooter.classList.add("hidden");
  }
  renderBays(currentDrives);
});

openBatchWipeModalBtn.addEventListener("click", () => {
  renderBatchModalForm();
  openModal(batchWipeModal);
});

function renderBatchModalForm() {
  const techInput = document.getElementById("technician");
  const ticketInput = document.getElementById("ticketNumber");
  if (techInput) techInput.value = "";
  if (ticketInput) ticketInput.value = "";

  const listHtml = Array.from(selectedBays).map(bay => {
    const drive = currentDrives.find(d => d.bay === bay);
    const recommended = computeRecommendedMethod(drive);
    
    const optionsHtml = (drive?.supported_methods || ["overwrite"]).map(method => {
      const isRec = method === recommended ? " (Recommended)" : "";
      return `<option value="${escapeHtml(method)}" ${method === recommended ? "selected" : ""}>${escapeHtml(method)}${isRec}</option>`;
    }).join("");

    return `
      <div class="batch-config-row">
        <span>${escapeHtml(bay.toUpperCase())}</span>
        <small style="color: var(--color-text-muted); text-overflow: ellipsis; overflow: hidden; white-space: nowrap;">
          ${escapeHtml(drive?.model || "Generic")} (S/N: ${escapeHtml(drive?.serial || "-")})
        </small>
        <select class="batch-drive-method-select" data-bay="${escapeHtml(bay)}" style="padding: 6px; font-size: 0.75rem;">
          ${optionsHtml}
        </select>
      </div>
    `;
  }).join("");

  selectedDrivesConfigList.innerHTML = listHtml;
  
  const count = selectedBays.size;
  const hintText = count === 1 ? `Type "erase ${Array.from(selectedBays)[0]}" to confirm:` : `Type "erase ${count} drives" to confirm:`;
  dynamicConfirmationHint.textContent = hintText;
  confirmationText.value = "";
}

batchEraseForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  
  let tech = document.getElementById("technician").value.trim();
  let ticket = document.getElementById("ticketNumber").value.trim();
  const confirmTextVal = confirmationText.value.trim().toLowerCase();
  
  if (!confirmTextVal) {
    alert("Validation Error: Please type the confirmation phrase to continue.");
    return;
  }

  if (!tech || !ticket) {
    let missingInfo = [];
    if (!tech) missingInfo.push("Technician Name");
    if (!ticket) missingInfo.push("Ticket Number");

    const proceed = confirm(
      `Notice: You left the following audit fields blank:\n- ${missingInfo.join("\n- ")}\n\nWould you like to continue anyway using the default placeholders?\n- Technician: "System Operator"\n- Ticket Number: "INTERNAL"\n\nPress Cancel to go back and write your audit info.`
    );
    if (!proceed) {
      return;
    }
    if (!tech) {
      tech = "System Operator";
      document.getElementById("technician").value = tech;
    }
    if (!ticket) {
      ticket = "INTERNAL";
      document.getElementById("ticketNumber").value = ticket;
    }
  }

  const payload = {
    technician: tech,
    ticket_number: ticket,
    bays: Array.from(selectedBays),
    confirmation_text: confirmTextVal,
    methods: {}
  };

  document.querySelectorAll(".batch-drive-method-select").forEach(select => {
    const bay = select.getAttribute("data-bay");
    payload.methods[bay] = select.value;
  });

  try {
    const response = await safeFetch("/api/erase/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const result = await response.json();
    if (!response.ok) {
      alert(`Wipe Rejected: ${result.error || "Unknown Error"}`);
      return;
    }
    
    closeModal(batchWipeModal);
    isBatchMode = false;
    batchSelectToggleBtn.classList.remove("active");
    batchSelectToggleBtn.textContent = "Sanitize Mode: OFF";
    selectedBays.clear();
    batchActionFooter.classList.add("hidden");
    
    alert("Sanitization batch successfully initiated.");
    
    loadDrives();
    loadHistoryIndex();
  } catch (err) {
    alert(`Failed to launch batch process: ${err.message}`);
  }
});

function renderLiveDetails(drive) {
  if (!drive) return;
  
  const opStatusText = String(drive.status || "READY").toUpperCase();
  const isRunning = opStatusText === "RUNNING";
  const isCompleted = drive.marker && drive.marker.status !== "none" && drive.marker.status !== "corrupted";
  
  let displayStatus = "IDLE / READY";
  let statusClass = "status-empty";

  if (opStatusText === "FAILED") {
    displayStatus = "WIPE FAILED";
    statusClass = "status-failed";
  } else if (isRunning) {
    displayStatus = "WIPING";
    statusClass = "status-ready";
  } else if (opStatusText === "QUEUED") {
    displayStatus = "QUEUED";
    statusClass = "status-ready";
  } else if (isCompleted) {
    displayStatus = "SANITIZED";
    statusClass = "status-complete";
  }
  
  let markerStatusText = "NO PRIOR SANITIZATION MARKER DETECTED";
  let markerClass = "status-empty";
  
  if (drive.marker?.status === "pristine_secure") {
    markerStatusText = "VERIFIED PRISTINE & SECURED (PASS)";
    markerClass = "status-complete";
  } else if (drive.marker?.status === "pristine_insecure") {
    markerStatusText = "PRISTINE (UNAUTHENTICATED PASS)";
    markerClass = "status-view-only";
  } else if (drive.marker?.status === "written_since_wipe") {
    markerStatusText = "ACTIVE USE (POST-WIPE WRITES DETECTED)";
    markerClass = "status-view-only";
  } else if (drive.marker?.status === "corrupted") {
    markerStatusText = "SIGNATURE CORRUPTED / INVALID";
    markerClass = "status-failed";
  }

  const rec = drive.recommendation || { status: "UNKNOWN", comment: "-" };
  const recLabel = rec.status.replace("_", " ").toUpperCase();
  const recClass = rec.status === "NEW_STOCK" ? "status-complete" : rec.status === "USED_GOOD" ? "status-view-only" : "status-warning";

  let terminalSection = "";
  if (isRunning) {
    const runPercent = drive.progress_percent !== undefined ? drive.progress_percent : 0.0;
    const runPhase = drive.current_phase || "Initializing...";
    terminalSection = `
      <div class="detail-section">
        <h4>Live Execution Pipe Console</h4>
        <pre class="terminal-pre">Running process subprocess monitoring active...\nActive Phase: ${runPhase}\nCompletion percentage: ${runPercent}%</pre>
      </div>
    `;
  }

  const smart = drive.smart || {};
  const realloc = smart.reallocated_sectors ?? 0;
  const pending = smart.pending_sectors ?? 0;
  const interfaceErrors = smart.interface_errors ?? 0;

  let smartHealthText = "SMART: PASSED";
  let smartHealthClass = "status-complete";
  if (drive.health_score <= 40 || (opStatusText === "FAILED" && !drive.marker)) {
    smartHealthText = "SMART: FAILING";
    smartHealthClass = "status-failed";
  }

  let remainingLife = "N/A";
  if (smart.wear_level !== null) {
    const iface = String(drive.interface_type || "").toLowerCase();
    const score = (iface.includes("nvme") || iface.includes("sas")) ? (100 - smart.wear_level) : smart.wear_level;
    remainingLife = Math.max(0, Math.min(100, Math.round(score))) + "%";
  }

  let smartDetailsHtml = `
    <div class="kv"><span>SMART Health Status:</span><span class="status-chip ${smartHealthClass}">${smartHealthText}</span></div>
    <div class="kv"><span>Estimated Remaining Life:</span><span>${remainingLife}</span></div>
    <div class="kv"><span>Total Lifetime Reads:</span><span>${formatTraffic(drive, 'read')}</span></div>
    <div class="kv"><span>Total Lifetime Writes:</span><span>${formatTraffic(drive, 'written')}</span></div>
    <div class="kv"><span>Power-On Time:</span><span>${formatPowerOnTime(smart.power_on_hours)}</span></div>
    <div class="kv"><span>Reallocated Sectors count:</span><span>${realloc}</span></div>
    <div class="kv"><span>Pending/Unstable Sectors:</span><span>${pending}</span></div>
    <div class="kv"><span>SATA Interface Errors:</span><span>${interfaceErrors}</span></div>
  `;

  bayDetailContent.innerHTML = `
    <div class="detail-section">
      <div class="detail-head">
        <strong>${escapeHtml(drive.bay.toUpperCase())} · ${escapeHtml(drive.model)}</strong>
        <span class="status-chip ${statusClass}">${escapeHtml(displayStatus)}</span>
      </div>
      <div class="kv"><span>Mount Path:</span><span>${escapeHtml(drive.device || "none")}</span></div>
      <div class="kv"><span>Serial:</span><span>${escapeHtml(drive.serial || "-")}</span></div>
      <div class="kv"><span>Capacity:</span><span>${escapeHtml(drive.capacity_str)}</span></div>
      <div class="kv"><span>Interface:</span><span>${escapeHtml(drive.interface_type?.toUpperCase() || "-")}</span></div>
    </div>
    
    <div class="detail-section">
      <h4>Normalized SMART Essentials</h4>
      ${smartDetailsHtml}
    </div>

    <div class="detail-section">
      <h4>Compliance Marker Integrity</h4>
      <div class="kv"><span>Marker Status:</span><span class="status-chip ${markerClass}">${escapeHtml(markerStatusText)}</span></div>
      <div class="kv"><span>Last Ticket:</span><span>${escapeHtml(drive.marker?.details?.ticket_number || "-")}</span></div>
      <div class="kv"><span>Wiped on:</span><span>${escapeHtml(formatIsoDate(drive.marker?.details?.finished_at))}</span></div>
    </div>

    <div class="detail-section">
      <h4>System Triage Recommendation</h4>
      <div class="kv"><span>Target Destination:</span><span class="status-chip ${recClass}">${escapeHtml(recLabel)}</span></div>
      <div class="kv"><span>Comments:</span><span>${escapeHtml(rec.comment)}</span></div>
    </div>

    ${terminalSection}
  `;
}

mainTabs.addEventListener("click", (event) => {
  const btn = event.target.closest(".tab-button");
  if (!btn) return;
  document.querySelectorAll(".tab-button").forEach(b => b.classList.remove("active"));
  document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
  btn.classList.add("active");
  document.getElementById(btn.dataset.tab).classList.add("active");
  
  if (btn.dataset.tab === "auditPanel") {
    loadHistoryIndex();
  } else if (btn.dataset.tab === "adminPanel") {
    loadAdminMetrics();
    loadBayMappingConfig();
  }
});

async function loadHistoryIndex() {
  const query = historyQuery.value.trim();
  const filter = historyStatusFilter.value;
  try {
    const response = await safeFetch(`/api/erase/history?query=${encodeURIComponent(query)}&limit=100`);
    if (!response.ok) throw new Error("HTTP " + response.status);
    const data = await response.json();
    currentHistoryJobs = Array.isArray(data?.jobs) ? data.jobs : [];
    
    let filtered = currentHistoryJobs;
    if (filter !== "all") {
      filtered = filtered.filter(j => j.status === filter);
    }
    
    renderAuditLedger(filtered);
  } catch (err) {
    historyList.innerHTML = `<div class="history-empty">Failed to load records: ${escapeHtml(err.message)}</div>`;
  }
}

function renderAuditLedger(jobs) {
  if (!jobs.length) {
    historyList.innerHTML = '<div class="history-empty">No audit matching database entries found.</div>';
    return;
  }

  historyList.innerHTML = jobs.map(job => {
    const isExpanded = ledgerExpandedJobs.has(job.id);
    const detailsHtml = isExpanded ? renderExpandedAuditRow(job) : "";
    
    const uiBadge = job.status === "completed" ? "complete" : job.status === "failed" ? "failed" : job.status === "running" ? "running" : "queued";
    const statusLabel = job.status === "completed" ? "PASSED" : job.status.toUpperCase();

    return `
      <article class="audit-row" data-audit-job-id="${escapeHtml(job.id)}">
        <div class="audit-summary-line">
          <div class="job-id-text">${escapeHtml(job.friendly_id || "SANI-******")}</div>
          <div class="ticket-text">${escapeHtml(job.request?.ticket_number || "-")}</div>
          <div style="font-weight: 700;">${escapeHtml(job.request?.model || "Generic")}</div>
          <div style="font-size: 0.8rem; font-family: monospace;">S/N: ${escapeHtml(job.request?.serial || "-")}</div>
          <div class="audit-status-chip">
            <span class="status-badge ${uiBadge}">${escapeHtml(statusLabel)}</span>
          </div>
        </div>
        ${detailsHtml}
      </article>
    `;
  }).join("");
}

function renderExpandedAuditRow(job) {
  const isCompleted = job.status === "completed";
  const isFailed = job.status === "failed";
  
  let diagnosticsHtml = "";
  if (isFailed) {
    const errText = job.error || "Unknown Error";
    const stderrText = job.result?.stderr || job.result?.stdout || "No console stderr captured.";
    const exitCode = job.result?.exit_code !== undefined ? job.result.exit_code : "N/A";
    
    diagnosticsHtml = `
      <div class="detail-section" style="grid-column: span 2; border-color: var(--color-danger); background: #220a0d; margin-top: 12px; padding: 14px;">
        <h4 style="color: var(--color-danger); margin-bottom: 6px; font-weight: 800; font-size: 0.75rem; letter-spacing: 0.5px;">⚠️ OPERATION FAILURE DIAGNOSTICS</h4>
        <div class="kv"><span>System Error Code:</span><span style="color: var(--color-danger) !important; font-weight: 800;">${escapeHtml(errText)}</span></div>
        <div class="kv"><span>Process Exit Code:</span><span>${escapeHtml(exitCode)}</span></div>
        <div style="margin-top: 10px;">
          <div style="font-size: 0.65rem; font-weight: 800; text-transform: uppercase; color: var(--color-text-muted); margin-bottom: 4px; letter-spacing: 0.5px;">Raw Disk Controller Console Output (stderr)</div>
          <pre class="terminal-pre" style="background: #000; border-color: #4c1d1d; max-height: 180px; color: #fecaca; white-space: pre-wrap; font-size: 11px;">${escapeHtml(stderrText)}</pre>
        </div>
      </div>
    `;
  }

  const isPrintable = job.status === "completed" || job.status === "failed";

  return `
    <div class="expanded-audit-details">
      <div class="audit-meta-col">
        <div class="kv"><span>Technician:</span><span>${escapeHtml(job.request?.technician || "-")}</span></div>
        <div class="kv"><span>Target Device:</span><span>${escapeHtml(job.request?.device || "-")}</span></div>
        <div class="kv"><span>Wipe Method:</span><span>${escapeHtml(job.request?.method || "-")}</span></div>
        <div class="kv"><span>Created At:</span><span>${escapeHtml(formatIsoDate(job.created_at))}</span></div>
        <div class="kv"><span>Finished At:</span><span>${escapeHtml(formatIsoDate(job.finished_at))}</span></div>
      </div>
      <div class="audit-actions-col">
        <div style="font-size: 0.72rem; font-weight: 800; text-transform: uppercase; color: var(--color-text-muted); text-align: center;">Distribution Actions</div>
        <div class="audit-actions-grid">
          <button type="button" data-cert-id="${escapeHtml(job.friendly_id)}" data-action="print" ${isPrintable ? "" : "disabled"} style="padding: 6px;">Print Certificate</button>
          <button type="button" data-cert-id="${escapeHtml(job.friendly_id)}" data-action="html" ${isPrintable ? "" : "disabled"} style="padding: 6px;">HTML Download</button>
          <button type="button" data-cert-id="${escapeHtml(job.friendly_id)}" data-action="json" ${isPrintable ? "" : "disabled"} style="padding: 6px;">JSON Download</button>
          <button type="button" class="copy-fields-btn" data-job-index="${escapeHtml(job.id)}" style="padding: 6px;">Copy Fields</button>
        </div>
      </div>
      ${diagnosticsHtml}
    </div>
  `;
}

historyList.addEventListener("click", async (event) => {
  const certButton = event.target.closest("[data-cert-id]");
  if (certButton) {
    event.stopPropagation();
    const id = certButton.getAttribute("data-cert-id");
    const act = certButton.getAttribute("data-action");
    
    if (act === "print") {
      openPrintWindow(id);
    } else {
      triggerCertDownload(id, act);
    }
    return;
  }

  const copyBtn = event.target.closest(".copy-fields-btn");
  if (copyBtn) {
    event.stopPropagation();
    const jobId = copyBtn.getAttribute("data-job-index");
    const targetJob = currentHistoryJobs.find(j => j.id === jobId);
    if (targetJob) {
      const payload = JSON.stringify({
        job_id: targetJob.friendly_id || targetJob.id,
        technician: targetJob.request?.technician,
        ticket_number: targetJob.request?.ticket_number,
        serial: targetJob.request?.serial,
        status: targetJob.status,
        sha256_hash: targetJob.certificate?.signature
      }, null, 2);
      
      copyTextToClipboard(payload);
    }
    return;
  }

  const row = event.target.closest("[data-audit-job-id]");
  if (!row) return;
  const id = row.getAttribute("data-audit-job-id");
  if (ledgerExpandedJobs.has(id)) {
    ledgerExpandedJobs.delete(id);
  } else {
    ledgerExpandedJobs.add(id);
  }
  renderAuditLedger(currentHistoryJobs);
});

async function copyTextToClipboard(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      alert("Compliance fields copied to clipboard.");
      return;
    } catch (err) {
      // Fallback
    }
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.style.top = "0";
  textarea.style.left = "0";
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();

  try {
    const successful = document.execCommand("copy");
    if (successful) {
      alert("Compliance fields copied to clipboard.");
    } else {
      alert("Failed to copy compliance fields automatically.");
    }
  } catch (err) {
    alert("Copy failed. Please manually select and copy fields.");
  }

  document.body.removeChild(textarea);
}

function triggerCertDownload(friendlyId, format) {
  const url = `/api/certificates/${encodeURIComponent(friendlyId)}?format=${encodeURIComponent(format)}`;
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.target = "_blank";
  anchor.rel = "noopener";
  anchor.click();
}

async function openPrintWindow(friendlyId) {
  const printWindow = window.open("", "_blank");
  if (!printWindow) {
    alert("Popup blocked! Enable popups to allow certificate printing.");
    return;
  }

  printWindow.document.open();
  printWindow.document.write(`
    <!doctype html>
    <html lang="en">
    <head><title>Loading Certificate...</title></head>
    <body style="font-family: Arial, sans-serif; padding: 32px; text-align: center; color: #555;">
      <h2 style="margin-bottom: 8px;">Retrieving compliance record...</h2>
      <p>Fetching the HTML certificate layout from the station.</p>
    </body>
    </html>
  `);
  printWindow.document.close();

  try {
    const response = await safeFetch(`/api/certificates/${encodeURIComponent(friendlyId)}?format=html`);
    if (!response.ok) throw new Error("HTTP " + response.status);
    const htmlContent = await response.text();

    printWindow.document.open();
    printWindow.document.write(htmlContent);
    printWindow.document.close();
    printWindow.focus();
    printWindow.print();
  } catch (err) {
    printWindow.document.open();
    printWindow.document.write(`
      <!doctype html>
      <html lang="en">
      <head><title>Error Retreiving Certificate</title></head>
      <body style="font-family: Arial, sans-serif; padding: 32px; text-align: center; color: #dc2626;">
        <h2>Retrieval failure occurred</h2>
        <p style="color: #555;">Error details: ${err.message}</p>
      </body>
      </html>
    `);
    printWindow.document.close();
  }
}

historyQuery.addEventListener("input", loadHistoryIndex);
historyStatusFilter.addEventListener("change", loadHistoryIndex);
historyRefreshButton.addEventListener("click", loadHistoryIndex);

function openModal(modal) {
  modal.classList.add("open");
  modal.setAttribute("aria-hidden", "false");
}

function closeModal(modal) {
  modal.classList.remove("open");
  modal.setAttribute("aria-hidden", "true");
}

document.querySelectorAll("[data-close-modal='true']").forEach(elem => {
  elem.addEventListener("click", (event) => {
    const modal = event.target.closest(".modal");
    if (modal) closeModal(modal);
  });
});

refreshButton.addEventListener("click", () => loadDrives(false));

async function loadSecurityStatus() {
  const badge = document.getElementById("securityBadge");
  if (!badge) return;
  try {
    const response = await safeFetch("/api/status");
    if (!response.ok) throw new Error();
    const data = await response.json();
    if (data.passphrase_enabled) {
      badge.textContent = "SECURE MODE";
      badge.className = "reliability-badge secure";
    } else {
      badge.textContent = "UNSECURED MODE";
      badge.className = "reliability-badge unsecured";
    }
  } catch (error) {
    badge.textContent = "UNSECURED MODE";
    badge.className = "reliability-badge unsecured";
  }
}


// --- ADMIN PANEL HANDLERS & BINDINGS ---

async function loadAdminMetrics() {
  const adminTab = document.querySelector('[data-tab="adminPanel"]');
  if (!adminTab || !adminTab.classList.contains("active")) return;
  
  try {
    const response = await safeFetch("/api/admin/metrics");
    if (!response.ok) throw new Error();
    const data = await response.json();
    
    metricDiskBar.style.width = `${data.disk_pct}%`;
    metricDiskText.textContent = `${data.disk_pct}% (${data.disk_str})`;
    
    metricRamBar.style.width = `${data.ram_pct}%`;
    metricRamText.textContent = `${data.ram_pct}%`;
    
    metricCpuBar.style.width = `${data.cpu_pct}%`;
    metricCpuText.textContent = `${data.cpu_pct}%`;
    
    metricUptimeText.textContent = data.uptime;
    
    const ipLabel = document.getElementById("metricIpText");
    if (ipLabel) {
      ipLabel.textContent = data.ip_address || "Unknown";
    }
  } catch (err) {
    // Suppress background poll failures quietly
  }
}

testWebhookBtn.addEventListener("click", async () => {
  testWebhookBtn.disabled = true;
  testWebhookBtn.textContent = "Testing...";
  webhookTestResult.classList.add("hidden");
  
  try {
    const response = await safeFetch("/api/admin/test-webhook", { method: "POST" });
    const data = await response.json();
    webhookTestResult.classList.remove("hidden");
    if (response.ok) {
      webhookTestResult.className = "test-result-label test-result-success";
      webhookTestResult.textContent = data.message || "Test Notification Sent!";
    } else {
      webhookTestResult.className = "test-result-label test-result-error";
      webhookTestResult.textContent = `Failure: ${data.error || "Unknown response"}`;
    }
  } catch (err) {
    webhookTestResult.classList.remove("hidden");
    webhookTestResult.className = "test-result-label test-result-error";
    webhookTestResult.textContent = `Error: ${err.message}`;
  } finally {
    testWebhookBtn.disabled = false;
    testWebhookBtn.textContent = "Test Alert Notification";
  }
});

exportCsvBtn.addEventListener("click", () => {
  window.location.href = "/api/admin/export-csv";
});

downloadBundleBtn.addEventListener("click", () => {
  window.location.href = "/api/admin/support-bundle";
});

async function loadBayMappingConfig() {
  try {
    const unmappedResponse = await safeFetch("/api/admin/unmapped-drives");
    if (!unmappedResponse.ok) throw new Error();
    const unmappedDrives = await unmappedResponse.json();
    
    let html = "";
    localBayMapCopy = {};

    // Sort bays chronologically by extracting numerical indices
    const sortedBayKeys = Object.keys(currentDrives.reduce((acc, d) => { acc[d.bay] = true; return acc; }, {})).sort((a, b) => {
      const numA = parseInt(a.replace(/\D/g, ""), 10) || 0;
      const numB = parseInt(b.replace(/\D/g, ""), 10) || 0;
      return numA - numB;
    });

    sortedBayKeys.forEach(bayKey => {
      const drive = currentDrives.find(d => d.bay === bayKey);
      if (!drive) return;

      localBayMapCopy[drive.bay] = {
        role: drive.role,
        locked: drive.locked,
        label: drive.label,
        type: drive.interface_type === "nvme" ? "nvme" : "sas_sata",
        by_path: drive.configured_by_path || ""
      };

      const currentPath = drive.configured_by_path || "";
      
      let options = `<option value="">-- Unassigned / Empty --</option>`;
      if (currentPath) {
        options += `<option value="${escapeHtml(currentPath)}" selected>${escapeHtml(currentPath)} (Current)</option>`;
      }
      
      unmappedDrives.forEach(ud => {
        options += `<option value="${escapeHtml(ud.by_path)}">${escapeHtml(ud.by_path)} (${escapeHtml(ud.model)} S/N: ${escapeHtml(ud.serial)})</option>`;
      });

      const lockStatusText = drive.locked ? "Locked" : "Editable";
      
      // Conditionally append Delete button only on non-locked bays
      const deleteBtnHtml = drive.locked ? "" : `
        <button type="button" class="btn-delete-bay" data-delete-bay-id="${escapeHtml(drive.bay)}" style="padding: 4px 10px; font-size: 0.7rem; background: var(--color-danger); border-color: var(--color-danger); margin-left: 12px; color: #fff;">
          Delete
        </button>
      `;

      html += `
        <div class="mapping-row" data-mapping-row-id="${escapeHtml(drive.bay)}">
          <span>${escapeHtml(drive.bay.toUpperCase())}</span>
          <select class="bay-path-select" data-bay-id="${escapeHtml(drive.bay)}" ${drive.locked ? "disabled" : ""}>
            ${options}
          </select>
          <div style="display: flex; align-items: center; justify-content: flex-end; min-width: 140px;">
            <small style="font-size: 0.7rem; color: #888;">${lockStatusText}</small>
            ${deleteBtnHtml}
          </div>
        </div>
      `;
    });

    bayMappingContainer.innerHTML = html;
    bindDeleteBayButtons();
  } catch (err) {
    bayMappingContainer.innerHTML = `<div style="color: var(--color-danger); font-size: 0.8rem; padding: 12px;">Failed to load mapping configurations: ${err.message}</div>`;
  }
}

function bindDeleteBayButtons() {
  document.querySelectorAll(".btn-delete-bay").forEach(button => {
    button.addEventListener("click", (event) => {
      const bayId = event.target.getAttribute("data-delete-bay-id");
      
      // Constraint check: At least 1 active bay must always remain
      if (Object.keys(localBayMapCopy).length <= 1) {
        alert("Delete Blocked: A minimum threshold of 1 active bay configuration is required.");
        return;
      }

      // Check current drives array to verify run-state status of this specific bay
      const drive = currentDrives.find(d => d.bay === bayId);
      if (drive && (String(drive.status).toUpperCase() === "RUNNING" || String(drive.status).toUpperCase() === "QUEUED")) {
        alert(`Delete Blocked: Cannot delete ${bayId.toUpperCase()} while an active or queued sanitization job is running on it.`);
        return;
      }

      const proceed = confirm(`Are you sure you want to stage the removal of ${bayId.toUpperCase()}?\n\nThis change will take effect only after you click 'Save Mapping Configuration'.`);
      if (!proceed) return;

      // Delete locally (staged)
      delete localBayMapCopy[bayId];
      
      // Clean selected lists and trigger direct staged redrawing
      selectedBays.delete(bayId);
      
      // Temporarily override the currentDrives array so re-rendering redraws the UI without the deleted card
      currentDrives = currentDrives.filter(d => d.bay !== bayId);
      renderBays(currentDrives);
      loadBayMappingConfig();
    });
  });
}

addBayBtn.addEventListener("click", () => {
  // Constraint check: Maximum 128 active bays limit safeguard
  if (Object.keys(localBayMapCopy).length >= 128) {
    alert("Add Blocked: Maximum threshold of 128 active bay configurations has been reached.");
    return;
  }

  const label = prompt("Enter a descriptive label for the new physical bay (e.g., shelf1_slot5):");
  if (label === null) return; // Technician cancelled
  
  const cleanLabel = label.trim() || "Work Bay Extension";
  const typeSelection = prompt("Enter Interface Slot Type ('sas_sata' or 'nvme'):", "sas_sata");
  if (typeSelection === null) return;
  
  const cleanType = typeSelection.trim().toLowerCase() === "nvme" ? "nvme" : "sas_sata";

  // Calculate highest numeric bay key index dynamically to auto-increment sequentially
  const bayKeys = Object.keys(localBayMapCopy);
  let highestNum = 0;
  bayKeys.forEach(k => {
    const num = parseInt(k.replace(/\D/g, ""), 10);
    if (!isNaN(num) && num > highestNum) {
      highestNum = num;
    }
  });
  
  const nextBayId = `bay${highestNum + 1}`;

  // Stage local addition
  localBayMapCopy[nextBayId] = {
    role: "wipe",
    locked: false,
    label: cleanLabel,
    type: cleanType,
    by_path: ""
  };

  // Push custom mock drive structure so the staged card shows up in Active Workbench instantly
  currentDrives.push({
    bay: nextBayId,
    label: cleanLabel,
    role: "wipe",
    locked: false,
    present: false,
    status: "EMPTY",
    interface_type: cleanType === "nvme" ? "nvme" : "sata",
    capacity_str: "-",
    marker: { status: "none" }
  });

  renderBays(currentDrives);
  loadBayMappingConfig();
});

saveBayMapBtn.addEventListener("click", async () => {
  saveBayMapBtn.disabled = true;
  saveBayMapBtn.textContent = "Saving...";
  
  try {
    document.querySelectorAll(".bay-path-select").forEach(select => {
      const bayId = select.getAttribute("data-bay-id");
      if (localBayMapCopy[bayId]) {
        localBayMapCopy[bayId].by_path = select.value;
      }
    });
    
    const response = await safeFetch("/api/save-bay-map", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(localBayMapCopy)
    });
    
    if (response.ok) {
      alert("Mapping configurations saved successfully. Reloading workspace.");
      await loadDrives();
      await loadBayMappingConfig();
    } else {
      const data = await response.json();
      alert(`Save Failed: ${data.error || "Unknown response"}`);
    }
  } catch (err) {
    alert(`Error: ${err.message}`);
  } finally {
    saveBayMapBtn.disabled = false;
    saveBayMapBtn.textContent = "Save Mapping Configuration";
  }
});


(async () => {
  await loadSecurityStatus();
  await loadDrives(false);
  pollActiveWipes();
})();