// --- START OF FILE frontend/modals.js ---
// Modal dialog handling

// These elements are defined in the main app.js file
const bayDetailModal = document.getElementById("bayDetailModal");
const bayDetailContent = document.getElementById("bayDetailContent");

if (!bayDetailModal || !bayDetailContent) {
  console.error("Critical: bayDetailModal or bayDetailContent element not found in DOM");
}

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
// --- END OF FILE frontend/modals.js ---
