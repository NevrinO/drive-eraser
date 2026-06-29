// --- START OF FILE frontend/modals.js ---
// Modal dialog handling

// These elements are defined in the main app.js file
const bayDetailModal = document.getElementById("bayDetailModal");
const bayDetailContent = document.getElementById("bayDetailContent");

if (!bayDetailModal || !bayDetailContent) {
  console.error("Critical: bayDetailModal or bayDetailContent element not found in DOM");
}

function renderZeroCheckDetailSection(drive) {
  if (!drive.present || drive.locked || drive.role === "os" || drive.role === "reserved") return "";
  if (String(drive.status).toUpperCase() === "RUNNING") return "";

  const zc = drive.zero_check || {};
  const status = zc.status || "not_started";

  let statusText = "Not started";
  let statusClass = "status-empty";
  let details = "";

  if (status === "running" || status === "queued") {
    statusText = status === "running" ? "Zero check running" : "Zero check queued";
    statusClass = "status-ready";
  } else if (status === "completed") {
    if (zc.result === "zeroed") {
      statusText = "Likely zeroed (sampled)";
      statusClass = "status-complete";
    } else if (zc.result === "data_present") {
      statusText = "Data present (sampled)";
      statusClass = "status-warning";
    } else if (zc.result === "inconclusive") {
      statusText = "Inconclusive (timed out)";
      statusClass = "status-view-only";
    } else {
      statusText = "Completed";
      statusClass = "status-view-only";
    }
    if (zc.chunks_checked) {
      details += `<div class="kv"><span>Chunks checked:</span><span>${zc.chunks_checked}</span></div>`;
    }
    if (zc.bytes_checked) {
      const mb = Math.round(zc.bytes_checked / (1024 * 1024));
      details += `<div class="kv"><span>Bytes checked:</span><span>${mb} MB</span></div>`;
    }
  } else if (status === "failed" || status === "cancelled") {
    statusText = status === "failed" ? "Zero check failed" : "Zero check cancelled";
    statusClass = "status-failed";
    if (zc.error) {
      details += `<div class="kv"><span>Error:</span><span>${escapeHtml(zc.error)}</span></div>`;
    }
  }

  const isRunning = status === "running" || status === "queued";
  const action = isRunning ? "cancel" : "start";
  const label = isRunning ? "Cancel Zero Check" : (status === "not_started" ? "Check Zero" : "Re-check Zero");

  return `
    <div class="detail-section">
      <h4>Pre-Wipe Zero Detection</h4>
      <div class="kv"><span>Status:</span><span class="status-chip ${statusClass}">${escapeHtml(statusText)}</span></div>
      ${details}
      <button type="button" class="btn btn--secondary" data-zero-check-action="${action}" data-bay="${escapeHtml(drive.bay)}">${label}</button>
    </div>
  `;
}

function renderLiveDetails(drive) {
  if (!drive) return;
  
  const opStatusText = String(drive.status || "READY").toUpperCase();
  const isRunning = opStatusText === "RUNNING";
  const hasValidMarker = drive.marker && drive.marker.status !== "none" && drive.marker.status !== "corrupted";
  const isCompleted = hasValidMarker;
  
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
  if (smart.smart_polling) {
    smartHealthText = "SMART: POLLING";
    smartHealthClass = "status-view-only";
  } else if (drive.health_score <= 30) {
    smartHealthText = "SMART: FAILING";
    smartHealthClass = "status-failed";
  }

  let remainingLife = "N/A";
  if (smart.wear_level !== null) {
    const score = 100 - smart.wear_level;
    remainingLife = Math.max(0, Math.min(100, Math.round(score))) + "%";
  }

  let smartDetailsHtml = `
    <div class="kv"><span>SMART Health Status:</span><span class="status-chip ${smartHealthClass}">${smartHealthText}</span></div>
    <div class="kv"><span>NAND Remaining Life:</span><span>${remainingLife}</span></div>
    <div class="kv"><span>Total Lifetime Reads:</span><span>${formatTraffic(drive, 'read')}</span></div>
    <div class="kv"><span>Total Lifetime Writes:</span><span>${formatTraffic(drive, 'written')}</span></div>
    <div class="kv"><span>Power-On Time:</span><span>${formatPowerOnTime(smart.power_on_hours)}</span></div>
    <div class="kv"><span>Reallocated Sectors count:</span><span>${realloc}</span></div>
    <div class="kv"><span>Pending/Unstable Sectors:</span><span>${pending}</span></div>
    <div class="kv"><span>SATA Interface Errors:</span><span>${interfaceErrors}</span></div>
  `;


  // SMART Export and Deep Dive buttons
  const deviceName = drive.device ? drive.device.replace('/dev/', '') : '';
  const smartButtonsHtml = deviceName ? `
    <div class="detail-section">
      <h4>SMART Data Tools</h4>
      <button type="button" class="btn btn--secondary" data-smart-export data-device="${escapeHtml(deviceName)}" data-serial="${escapeHtml(drive.serial || 'unknown')}">Download Raw SMART JSON</button>
      <button type="button" class="btn btn--secondary" data-deep-dive data-device="${escapeHtml(deviceName)}" data-serial="${escapeHtml(drive.serial || 'unknown')}" data-interface="${escapeHtml(drive.interface_type || 'unknown')}">Open Deep Dive Viewer</button>
    </div>
  ` : '';

  // Prior Visit UI (Phase 6 Feature D)
  let priorVisitHtml = "";
  const priorVisit = drive.prior_visit;
  if (priorVisit) {
    const prevHealthScore = priorVisit.health_score || 0;
    const currentHealthScore = drive.health_score || 0;
    const healthDelta = currentHealthScore - prevHealthScore;
    const healthDeltaClass = healthDelta > 0 ? "status-complete" : healthDelta < 0 ? "status-failed" : "status-view-only";
    const healthDeltaText = healthDelta > 0 ? `+${healthDelta}` : healthDelta.toString();
    
    const prevRecStatus = priorVisit.recommendation || "UNKNOWN";
    const currentRec = rec.status || "UNKNOWN";
    
    priorVisitHtml = `
      <div class="detail-section">
        <h4>Previous Visit</h4>
        <div class="kv"><span>Last Seen:</span><span>${escapeHtml(formatIsoDate(priorVisit.seen_at))}</span></div>
        <div class="kv"><span>Previous Health Score:</span><span>${prevHealthScore}</span></div>
        <div class="kv"><span>Current Health Score:</span><span>${currentHealthScore}</span></div>
        <div class="kv"><span>Health Score Delta:</span><span class="${healthDeltaClass}">${healthDeltaText}</span></div>
        <div class="kv"><span>Previous Recommendation:</span><span>${escapeHtml(prevRecStatus.replace("_", " ").toUpperCase())}</span></div>
        <div class="kv"><span>Current Recommendation:</span><span>${escapeHtml(currentRec.replace("_", " ").toUpperCase())}</span></div>
      </div>
    `;
  }

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
      <h4>System Triage Recommendation</h4>
      <div class="kv"><span>Target Destination:</span><span class="status-chip ${recClass}">${escapeHtml(recLabel)}</span></div>
      <div class="kv"><span>Comments:</span><span>${escapeHtml(rec.comment)}</span></div>
    </div>

    ${hasValidMarker ? `
    <div class="detail-section">
      <h4>Compliance Marker Integrity</h4>
      <div class="kv"><span>Marker Status:</span><span class="status-chip ${markerClass}">${escapeHtml(markerStatusText)}</span></div>
      <div class="kv"><span>Last Ticket:</span><span>${escapeHtml(drive.marker?.details?.ticket_number || "-")}</span></div>
      <div class="kv"><span>Wiped on:</span><span>${escapeHtml(formatIsoDate(drive.marker?.details?.finished_at))}</span></div>
    </div>
    ` : renderZeroCheckDetailSection(drive)}

    <div class="detail-section">
      <h4>Normalized SMART Essentials</h4>
      ${smartDetailsHtml}
    </div>

    ${smartButtonsHtml}

    ${priorVisitHtml}

    ${terminalSection}
  `;
}

function openModal(modal) {
  modal.classList.add("open");
  modal.setAttribute("aria-hidden", "false");
  trapFocus(modal);
}

function closeModal(modal) {
  modal.classList.remove("open");
  modal.setAttribute("aria-hidden", "true");
  releaseFocusTrap(modal);
}

document.querySelectorAll("[data-close-modal='true']").forEach(elem => {
  elem.addEventListener("click", (event) => {
    const modal = event.target.closest(".modal");
    if (modal) closeModal(modal);
  });
});

// Event delegation for SMART export buttons
document.addEventListener("click", (event) => {
  if (event.target.matches("[data-smart-export]")) {
    const button = event.target;
    const device = button.dataset.device;
    const serial = button.dataset.serial;
    downloadSmartData(device, serial);
  }
});

// Event delegation for Deep Dive buttons
document.addEventListener("click", (event) => {
  if (event.target.matches("[data-deep-dive]")) {
    const button = event.target;
    const device = button.dataset.device;
    const serial = button.dataset.serial;
    const interfaceType = button.dataset.interface;
    // Keep bay detail modal open - deep dive will stack on top (nested modal)
    openSmartDeepDive(device, serial, interfaceType);
  }
});

// Event delegation for zero-check buttons in detail modal
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-zero-check-action]");
  if (!button) return;
  if (typeof handleZeroCheckAction === "function") {
    const bay = button.dataset.bay;
    const action = button.dataset.zeroCheckAction;
    handleZeroCheckAction(bay, action);
  }
});

// Phase 6 Feature E: Download SMART data
async function downloadSmartData(device, serial) {
  try {
    const response = await safeFetch(`/api/admin/drives/${device}/smart-export`);
    if (!response.ok) {
      const error = await response.json();
      alert(`Failed to download SMART data: ${error.error || "Unknown error"}`);
      return;
    }
    
    // Get the blob from the response
    const blob = await response.blob();
    
    // Create a download link
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    
    // Generate filename with timestamp
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, -5);
    a.download = `smartctl-${serial}-${timestamp}.json`;
    
    // Trigger download
    document.body.appendChild(a);
    a.click();
    
    // Cleanup
    window.URL.revokeObjectURL(url);
    document.body.removeChild(a);
  } catch (error) {
    console.error("Failed to download SMART data:", error);
    alert(`Failed to download SMART data: ${error.message}`);
  }
}

// Phase 7 Feature G: Open SMART Deep Dive modal
function openSmartDeepDive(device, serial, interfaceType) {
  if (typeof openSmartDeepDiveModal === 'function') {
    openSmartDeepDiveModal(device, serial, interfaceType);
  } else {
    console.error("openSmartDeepDiveModal function not available. Ensure smartDeepDive.js is loaded.");
    alert("Failed to open SMART Deep Dive viewer");
  }
}
// --- END OF FILE frontend/modals.js ---
