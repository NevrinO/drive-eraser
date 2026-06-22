// --- START OF FILE frontend/triageReport.js ---
// Batch Intake Triage Report functionality

const refreshTriageBtn = document.getElementById("refreshTriageBtn");
const printTriageBtn = document.getElementById("printTriageBtn");
const triageTableBody = document.getElementById("triageTableBody");
const filterFailed = document.getElementById("filterFailed");
const filterHalted = document.getElementById("filterHalted");
const filterStickyLBA = document.getElementById("filterStickyLBA");
const filterUncorrectable = document.getElementById("filterUncorrectable");

let triageDrives = [];

async function loadTriageReport() {
  try {
    const response = await safeFetch("/api/drives");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const drives = await response.json();
    triageDrives = drives.filter(d => d.present); // Only show present drives
    renderTriageTable();
  } catch (error) {
    console.error("Failed to load triage report:", error);
    triageTableBody.innerHTML = `<tr><td colspan="11" style="text-align: center; color: var(--color-danger);">Failed to load drives: ${error.message}</td></tr>`;
  }
}

function renderTriageTable() {
  const showFailed = filterFailed.checked;
  const showHalted = filterHalted.checked;
  const showStickyLBA = filterStickyLBA.checked;
  const showUncorrectable = filterUncorrectable.checked;

  const filteredDrives = triageDrives.filter(drive => {
    const smart = drive.smart || {};
    
    // Check FAILED status
    const isFailed = drive.health_score <= 30 || (smart.status === "FAILED");
    if (showFailed && isFailed) return true;
    
    // Check SAS scan halted
    const isHalted = smart.sas_scan_status && smart.sas_scan_status.toLowerCase().includes("halted");
    if (showHalted && isHalted) return true;
    
    // Check sticky LBA
    const isStickyLBA = smart.sas_sticky_lba_detected === true;
    if (showStickyLBA && isStickyLBA) return true;
    
    // Check uncorrectable errors
    const hasUncorrectable = 
      (smart.sas_uncorrectable_read_errors || 0) > 0 ||
      (smart.sas_uncorrectable_write_errors || 0) > 0 ||
      (smart.sas_uncorrectable_verify_errors || 0) > 0;
    if (showUncorrectable && hasUncorrectable) return true;
    
    // If no filters match, show all drives
    if (!showFailed && !showHalted && !showStickyLBA && !showUncorrectable) {
      return true;
    }
    
    return false;
  });

  if (filteredDrives.length === 0) {
    triageTableBody.innerHTML = `<tr><td colspan="11" style="text-align: center; color: var(--color-text-muted);">No drives match the current filter criteria</td></tr>`;
    return;
  }

  const rowsHtml = filteredDrives.map(drive => {
    const smart = drive.smart || {};
    const rec = drive.recommendation || { status: "UNKNOWN" };
    
    // Determine flag values
    const isFailed = drive.health_score <= 30 || (smart.status === "FAILED");
    const isHalted = smart.sas_scan_status && smart.sas_scan_status.toLowerCase().includes("halted");
    const isStickyLBA = smart.sas_sticky_lba_detected === true;
    const hasUncorrectable = 
      (smart.sas_uncorrectable_read_errors || 0) > 0 ||
      (smart.sas_uncorrectable_write_errors || 0) > 0 ||
      (smart.sas_uncorrectable_verify_errors || 0) > 0;
    
    // Health score class
    let healthClass = "health-score-good";
    if (drive.health_score <= 30) healthClass = "health-score-fail";
    else if (drive.health_score <= 50) healthClass = "health-score-warning";
    
    const bayLabel = drive.display_number ? `BAY ${drive.display_number}` : (drive.bay || "-").toUpperCase();
    
    return `
      <tr>
        <td>${escapeHtml(bayLabel)}</td>
        <td>${escapeHtml(drive.serial || "-")}</td>
        <td>${escapeHtml(drive.model || "-")}</td>
        <td>${escapeHtml((drive.interface_type || "-").toUpperCase())}</td>
        <td>${escapeHtml(drive.capacity_str || "-")}</td>
        <td class="${healthClass}">${drive.health_score || 0}</td>
        <td>${escapeHtml(rec.status.replace("_", " ").toUpperCase())}</td>
        <td class="flag-cell ${isFailed ? 'flag-true' : 'flag-false'}">${isFailed ? '✓' : '-'}</td>
        <td class="flag-cell ${isHalted ? 'flag-true' : 'flag-false'}">${isHalted ? '✓' : '-'}</td>
        <td class="flag-cell ${isStickyLBA ? 'flag-true' : 'flag-false'}">${isStickyLBA ? '✓' : '-'}</td>
        <td class="flag-cell ${hasUncorrectable ? 'flag-true' : 'flag-false'}">${hasUncorrectable ? '✓' : '-'}</td>
      </tr>
    `;
  }).join("");

  triageTableBody.innerHTML = rowsHtml;
}

// Event listeners
if (refreshTriageBtn) {
  refreshTriageBtn.addEventListener("click", () => loadTriageReport());
}

if (printTriageBtn) {
  printTriageBtn.addEventListener("click", () => {
    window.print();
  });
}

// Filter change listeners
[filterFailed, filterHalted, filterStickyLBA, filterUncorrectable].forEach(checkbox => {
  if (checkbox) {
    checkbox.addEventListener("change", () => renderTriageTable());
  }
});

// Load triage report when tab is activated
const triageTab = document.querySelector('[data-tab="triagePanel"]');
if (triageTab) {
  triageTab.addEventListener("click", () => {
    loadTriageReport();
  });
}

// --- END OF FILE frontend/triageReport.js ---
