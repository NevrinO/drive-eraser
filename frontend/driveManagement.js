// --- START OF FILE frontend/driveManagement.js ---
// Drive discovery, polling, and core DOM element references.
// Rendering functions are in driveRendering.js (loaded before this file).
// Batch wipe and health gate functions are in batchWipe.js (loaded after this file).

// These elements are defined in the main app.js file
const baysGrid = document.getElementById("baysGrid");
const refreshButton = document.getElementById("refreshButton");
const apiStatus = document.getElementById("apiStatus");
const lastUpdated = document.getElementById("lastUpdated");

// Enclosure data for workbench grouping

let workbenchEnclosures = {};
let workbenchEnclosuresFetchedAt = 0;
let workbenchEnclosuresPromise = null;
const ENCLOSURE_CACHE_TTL_MS = 60000;

// Load enclosures for workbench grouping (cached; static config changes rarely)
async function loadEnclosuresForWorkbench() {
  const now = Date.now();
  if (now - workbenchEnclosuresFetchedAt < ENCLOSURE_CACHE_TTL_MS) {
    return;
  }
  if (workbenchEnclosuresPromise) {
    return workbenchEnclosuresPromise;
  }
  workbenchEnclosuresPromise = (async () => {
    try {
      const response = await safeFetch("/api/admin/enclosures");
      if (!response.ok) { console.warn("Enclosures fetch failed:", response.status); return; }
      const data = await response.json();
      workbenchEnclosures = {};
      (data.enclosures || []).forEach(enc => {
        workbenchEnclosures[enc.id] = enc;
      });
      workbenchEnclosuresFetchedAt = Date.now();
    } catch (e) {
      console.error("Failed to load enclosures for workbench:", e);
    } finally {
      workbenchEnclosuresPromise = null;
    }
  })();
  return workbenchEnclosuresPromise;
}

if (!baysGrid) {
  console.error("Critical: baysGrid element not found in DOM");
}
const batchSelectToggleBtn = document.getElementById("batchSelectToggleBtn");
const batchActionFooter = document.getElementById("batchActionFooter");
const selectedCountLabel = document.getElementById("selectedCountLabel");
const openBatchWipeModalBtn = document.getElementById("openBatchWipeModalBtn");
const batchWipeModal = document.getElementById("batchWipeModal");
const batchEraseForm = document.getElementById("batchEraseForm");
const selectedDrivesConfigList = document.getElementById("selectedDrivesConfigList");
const dynamicConfirmationHint = document.getElementById("dynamicConfirmationHint");
const confirmationText = document.getElementById("confirmationText");
const zeroCheckWarning = document.getElementById("zeroCheckWarning");
const zeroCheckWarningList = document.getElementById("zeroCheckWarningList");
const healthGateWarningModal = document.getElementById("healthGateWarningModal");
const healthGateWarningContent = document.getElementById("healthGateWarningContent");
const healthGateOverrideSection = document.getElementById("healthGateOverrideSection");
const healthGateOverrideJustification = document.getElementById("healthGateOverrideJustification");
const healthGateOverrideBtn = document.getElementById("healthGateOverrideBtn");
const healthGateCancelBtn = document.getElementById("healthGateCancelBtn");
const healthGateWarningClose = document.getElementById("healthGateWarningClose");
const healthGateDropSection = document.getElementById("healthGateDropSection");
const healthGateDropBtn = document.getElementById("healthGateDropBtn");

const POLL_INTERVAL_MS = 5000;

// Track polling interval for cleanup
let pollingIntervalId = null;

function isBayUnconfigured(drive) {
  if (!drive) return false;
  
  const bayConfig = localBayMapCopy[drive.bay];
  if (!bayConfig) return false;
  
  const byPath = bayConfig.by_path || "";
  const byPathNvme = bayConfig.by_path_nvme || "";
  
  const isEmptyPath = !byPath || byPath === "" || byPath === "REPLACE_ME_WITH_OS_PATH" || byPath.startsWith("REPLACE_ME");
  const isEmptyNvmePath = !byPathNvme || byPathNvme === "" || byPathNvme.startsWith("REPLACE_ME");
  
  return isEmptyPath && isEmptyNvmePath;
}

async function pollActiveWipes() {
  if (pollingIntervalId !== null) {
    console.warn("Polling already active, skipping duplicate call");
    return;
  }

  pollingIntervalId = setInterval(async () => {
    try {
      await loadDrives(true);
      
      const adminTab = document.querySelector('[data-tab="adminPanel"]');
      if (adminTab && adminTab.classList.contains("active")) {
        await loadAdminMetrics();
      }
    } catch (error) {
      console.error("Polling error:", error);
    }
  }, POLL_INTERVAL_MS);
}

function stopPolling() {
  if (pollingIntervalId !== null) {
    clearInterval(pollingIntervalId);
    pollingIntervalId = null;
  }
  if (_zeroCheckRenderTimer !== null) {
    clearTimeout(_zeroCheckRenderTimer);
    _zeroCheckRenderTimer = null;
  }
}

let _zeroCheckRenderTimer = null;

function handleZeroCheckUpdate(data) {
  const { bay, zero_check } = data || {};
  if (!bay || !zero_check) return;
  const driveIndex = currentDrives.findIndex(d => d.bay === bay);
  if (driveIndex !== -1) {
    currentDrives[driveIndex].zero_check = zero_check;
    if (document.getElementById('workbenchPanel').classList.contains('active')) {
      clearTimeout(_zeroCheckRenderTimer);
      _zeroCheckRenderTimer = setTimeout(() => renderBays(currentDrives), 100);
    }
    // Refresh the bay detail modal if it is open for the affected bay
    const modal = document.getElementById('bayDetailModal');
    if (modal && modal.classList.contains('open') && currentDetailDrive && currentDetailDrive.bay === bay) {
      currentDetailDrive = currentDrives[driveIndex];
      renderLiveDetails(currentDetailDrive);
    }
  }
}

async function loadDrives(silent = false, forceRefresh = false) {
  try {
    if (!silent) apiStatus.textContent = "API Status: Loading...";

    // Load enclosures and drives in parallel — they are independent
    const drivesUrl = forceRefresh ? "/api/drives?force_refresh=true" : "/api/drives";
    const [_, response] = await Promise.all([
      loadEnclosuresForWorkbench(),
      safeFetch(drivesUrl)
    ]);

    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    let drives;
    try {
      drives = await response.json();
    } catch (e) {
      console.error("Failed to parse drives JSON:", e);
      throw new Error("Invalid JSON response from drives API");
    }

    let fetchedDrives = Array.isArray(drives) ? drives : [];

    // Safety: Merge staged bays from memory into background pollers so they are not deleted
    if (Object.keys(localBayMapCopy).length > 0) {
      Object.keys(localBayMapCopy).forEach(bayId => {
        const exists = fetchedDrives.some(d => d.bay === bayId);
        if (!exists) {
          const conf = localBayMapCopy[bayId];
          fetchedDrives.push({
            bay: bayId,
            label: conf.label,
            role: conf.role,
            locked: conf.locked,
            present: false,
            status: "EMPTY",
            interface_type: conf.type === "nvme" ? "nvme" : "sata",
            capacity_str: "-",
            marker: { status: "none" },
            display_number: conf.display_number,
            physical_position: conf.physical_position
          });
        }
      });
    }

    currentDrives = fetchedDrives;
    renderBays(currentDrives);

    if (!silent) {
      apiStatus.textContent = "API Status: Ready";
    }
    lastUpdated.textContent = `Last updated: ${new Date().toLocaleTimeString()}`;
  } catch (error) {
    console.error("loadDrives error:", error);
    if (!silent) apiStatus.textContent = `API Status: Error (${error.message})`;
  }
}

if (baysGrid) baysGrid.addEventListener("click", (event) => {
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
    currentDetailDrive = drive;
    renderLiveDetails(drive);
    openModal(bayDetailModal);
  }
});

async function handleZeroCheckAction(bay, action) {
  const button = document.querySelector(`[data-zero-check-action][data-bay="${CSS.escape(bay)}"]`);
  let originalText = "";
  if (button) {
    if (button.disabled) return;
    button.disabled = true;
    originalText = button.textContent;
    button.textContent = action === "start" ? "Starting..." : "Cancelling...";
  }
  try {
    const method = action === "start" ? "POST" : "DELETE";
    const response = await safeFetch(`/api/drives/${encodeURIComponent(bay)}/zero-check`, { method });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      const errMsg = data.error || response.status;
      console.error(`Zero-check ${action} failed for ${bay}:`, errMsg);
      alert(`Zero-check ${action} failed for ${bay}: ${errMsg}`);
      if (button) {
        button.disabled = false;
        button.textContent = originalText;
      }
      return;
    }
    await loadDrives(true);
    const modal = document.getElementById('bayDetailModal');
    if (modal.classList.contains('open') && currentDetailDrive && currentDetailDrive.bay === bay) {
      const updated = currentDrives.find(d => d.bay === bay);
      if (updated) {
        currentDetailDrive = updated;
        renderLiveDetails(updated);
      }
    }
  } catch (e) {
    console.error(`Zero-check ${action} error for ${bay}:`, e);
    if (button) {
      button.disabled = false;
      button.textContent = originalText;
    }
  }
}

refreshButton.addEventListener("click", () => loadDrives(false, true));

// --- Log tail polling for active wipe detail modal ---

let _logTailInterval = null;
let _logTailJobId = null;
let _logTailPrevStatus = null;

function encodePathKey(relPath) {
  return btoa(String.fromCharCode(...new TextEncoder().encode(relPath))).replace(/\+/g, "-").replace(/\//g, "_");
}

function stopLogTailPolling() {
  if (_logTailInterval) {
    clearInterval(_logTailInterval);
    _logTailInterval = null;
  }
  _logTailJobId = null;
  _logTailPrevStatus = null;
}

async function fetchLogTail(relPath, lines) {
  try {
    const pathKey = encodePathKey(relPath);
    const response = await safeFetch(`/api/admin/logs/${encodeURIComponent(pathKey)}/preview?lines=${lines}`);
    if (!response.ok) return null;
    const data = await response.json();
    return data.content || "";
  } catch {
    return null;
  }
}

function updateLogTailElement(content) {
  const el = document.getElementById("liveLogTail");
  if (!el) return;
  el.textContent = content || "(empty)";
  el.scrollTop = el.scrollHeight;
}

async function pollActiveLogTail(jobId) {
  const content = await fetchLogTail(`active/job-${jobId}.log`, 50);
  if (content !== null) {
    updateLogTailElement(content);
  }

  // Check if job status changed (completed/failed) — stop polling
  const drive = currentDrives.find((d) => d.job_id === jobId);
  if (drive) {
    const status = String(drive.status || "").toUpperCase();
    if (_logTailPrevStatus !== status && (status === "COMPLETED" || status === "FAILED" || status === "READY")) {
      stopLogTailPolling();
      // Load final log content
      const finalContent = await fetchLogTail(`active/job-${jobId}.log`, 50);
      if (finalContent !== null) updateLogTailElement(finalContent);
    }
    _logTailPrevStatus = status;
  }
}

function startLogTailPolling(jobId) {
  stopLogTailPolling();
  _logTailJobId = jobId;
  const drive = currentDrives.find((d) => d.job_id === jobId);
  _logTailPrevStatus = drive ? String(drive.status || "").toUpperCase() : "RUNNING";

  // Immediately fetch once
  pollActiveLogTail(jobId);

  // Poll every 3 seconds
  _logTailInterval = setInterval(() => pollActiveLogTail(jobId), 3000);
}

async function loadFailedLog(jobId) {
  const content = await fetchLogTail(`failed/job-${jobId}.log`, 50);
  if (content !== null) {
    updateLogTailElement(content);
  } else {
    updateLogTailElement("(Failed log not available. Use Log Viewer to browse logs.)");
  }
}

// Hook into bay detail modal open: start polling for running jobs, load failed log for failed jobs
if (baysGrid) baysGrid.addEventListener("click", (event) => {
  const card = event.target.closest("[data-bay]");
  if (!card) return;
  if (event.target.closest(".card-checkbox")) return;
  if (isBatchMode) return;

  const bay = card.getAttribute("data-bay");
  const drive = currentDrives.find((d) => d.bay === bay);
  if (!drive) return;

  const status = String(drive.status || "").toUpperCase();
  if (status === "RUNNING" && drive.job_id) {
    startLogTailPolling(drive.job_id);
  } else if (status === "FAILED" && drive.job_id) {
    loadFailedLog(drive.job_id);
  }
});

// Stop polling when bay detail modal closes (close button or backdrop click)
document.addEventListener("click", (event) => {
  const isCloseBtn = event.target.closest("[data-close-modal='true']");
  const isBackdrop = event.target.classList && event.target.classList.contains("modal-backdrop");
  if (isCloseBtn || isBackdrop) {
    const modal = event.target.closest(".modal");
    if (modal && modal.id === "bayDetailModal") {
      stopLogTailPolling();
    }
  }
});

// --- END OF FILE frontend/driveManagement.js ---
