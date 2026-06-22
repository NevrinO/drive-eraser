// --- START OF FILE frontend/triageReport.js ---
// Batch Intake Triage Report functionality

const refreshTriageBtn = document.getElementById("refreshTriageBtn");
const printTriageBtn = document.getElementById("printTriageBtn");
const triageTableBody = document.getElementById("triageTableBody");
const filterInterface = document.getElementById("filterInterface");
const filterMediaType = document.getElementById("filterMediaType");
const filterRecommendation = document.getElementById("filterRecommendation");

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
    const tableHead = document.querySelector("#triageTable thead tr");
    const colCount = tableHead ? tableHead.querySelectorAll("th").length : 8;
    triageTableBody.innerHTML = `<tr><td colspan="${colCount}" style="text-align: center; color: var(--color-danger);">Failed to load drives: ${error.message}</td></tr>`;
  }
}

function renderTriageTable() {
  const interfaceFilter = filterInterface.value;
  const mediaTypeFilter = filterMediaType.value;
  const recommendationFilter = filterRecommendation.value;

  // Status mapping for exact matching (backend returns these specific values)
  const STATUS_MAP = {
    "new": "NEW_STOCK",
    "good": "USED_GOOD",
    "heavy": "USED_HEAVY",
    "destroy": "DESTROY",
    "scratch": "SCRATCH",
    "locked": "LOCKED",
    "unknown": "UNKNOWN"
  };

  const filteredDrives = triageDrives.filter(drive => {
    const smart = drive.smart || {};
    const rec = drive.recommendation || { status: "UNKNOWN" };
    
    // Interface filter
    if (interfaceFilter !== "all") {
      const driveInterface = (drive.interface_type || "unknown").toLowerCase();
      if (interfaceFilter === "sata" && !driveInterface.includes("sata")) return false;
      if (interfaceFilter === "sas" && !driveInterface.includes("sas")) return false;
      if (interfaceFilter === "nvme" && !driveInterface.includes("nvme")) return false;
    }
    
    // Media type filter
    if (mediaTypeFilter !== "all") {
      const driveType = (drive.drive_type || "unknown").toLowerCase();
      if (mediaTypeFilter === "ssd" && driveType !== "ssd") return false;
      if (mediaTypeFilter === "hdd" && driveType !== "hdd") return false;
    }
    
    // Recommendation filter (exact match with status mapping)
    if (recommendationFilter !== "all") {
      const expectedStatus = STATUS_MAP[recommendationFilter];
      if (!expectedStatus || rec.status !== expectedStatus) return false;
    }
    
    return true;
  });

  if (filteredDrives.length === 0) {
    const tableHead = document.querySelector("#triageTable thead tr");
    const colCount = tableHead ? tableHead.querySelectorAll("th").length : 8;
    triageTableBody.innerHTML = `<tr><td colspan="${colCount}" style="text-align: center; color: var(--color-text-muted);">No drives match the current filter criteria</td></tr>`;
    return;
  }

  const rowsHtml = filteredDrives.map(drive => {
    const smart = drive.smart || {};
    const rec = drive.recommendation || { status: "UNKNOWN" };
    
    // Health score class
    let healthClass = "health-score-good";
    if (drive.health_score <= 30) healthClass = "health-score-fail";
    else if (drive.health_score <= 50) healthClass = "health-score-warning";
    
    // Bay label: enclosure name + bay name, or just bay name if no enclosure
    let bayLabel;
    if (drive.enclosure_name && drive.display_number) {
      bayLabel = `${drive.enclosure_name} BAY ${drive.display_number}`;
    } else if (drive.display_number) {
      bayLabel = `BAY ${drive.display_number}`;
    } else {
      bayLabel = (drive.bay || "-").toUpperCase();
    }
    
    // Media type display
    const mediaType = (drive.drive_type || "unknown").toUpperCase();
    
    // POH display
    const poh = smart.power_on_hours ? `${smart.power_on_hours.toLocaleString()}h` : "-";
    
    return `
      <tr>
        <td>${escapeHtml(bayLabel)}</td>
        <td>${escapeHtml(drive.serial || "-")}</td>
        <td>${escapeHtml(drive.model || "-")}</td>
        <td>${escapeHtml(mediaType)}</td>
        <td>${escapeHtml((drive.interface_type || "-").toUpperCase())}</td>
        <td>${escapeHtml(drive.capacity_str || "-")}</td>
        <td>${escapeHtml(poh)}</td>
        <td class="${healthClass}">${drive.health_score || 0}</td>
        <td>${escapeHtml(rec.status.replace(/_/g, " ").toUpperCase())}</td>
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
[filterInterface, filterMediaType, filterRecommendation].forEach(select => {
  if (select) {
    select.addEventListener("change", () => renderTriageTable());
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
