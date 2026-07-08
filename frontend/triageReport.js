// --- START OF FILE frontend/triageReport.js ---
// Batch Intake Triage Report functionality

const refreshTriageBtn = document.getElementById("refreshTriageBtn");
const printTriageBtn = document.getElementById("printTriageBtn");
const exportCsvTriageBtn = document.getElementById("exportCsvTriageBtn");
const exportJsonTriageBtn = document.getElementById("exportJsonTriageBtn");
const triageTableBody = document.getElementById("triageTableBody");
const triageSummaryBar = document.getElementById("triageSummaryBar");
const filterInterface = document.getElementById("filterInterface");
const filterMediaType = document.getElementById("filterMediaType");
const filterRecommendation = document.getElementById("filterRecommendation");

let triageDrives = [];
let triageSortColumn = null;
let triageSortAsc = true;

// ──────────────────────────────────────────────────────────────────────
// Bay label: matches workbench priority (label → BAY {n} → raw bay id)
// but prefixes enclosure name when available for triage context.
// REGRESSION GUARD: Do NOT concatenate label with bay number using " - "
// (see lessons-learned.md Rule 108). Use label OR BAY {n}, never both.
// ──────────────────────────────────────────────────────────────────────
function _triageBayLabel(drive) {
  let primary;
  if (drive.label && String(drive.label).trim()) {
    primary = String(drive.label).trim();
  } else if (drive.display_number != null) {
    primary = `BAY ${drive.display_number}`;
  } else {
    primary = (drive.bay && drive.bay.toLowerCase().startsWith('bay') ? drive.bay.toUpperCase() : (drive.bay || "-"));
  }
  if (drive.enclosure_name) {
    return `${drive.enclosure_name} ${primary}`;
  }
  return primary;
}

// ──────────────────────────────────────────────────────────────────────
// Anomaly flags: surfaces SAS-specific critical signals from SMART data
// ──────────────────────────────────────────────────────────────────────
function _triageFlags(drive) {
  const smart = drive.smart || {};
  const flags = [];

  if (drive.status === "FAILED" || (smart.status && String(smart.status).toUpperCase() === "FAILED")) {
    flags.push({ label: "FAILED", title: "SMART health status: FAILED", cls: "triage-flag-critical" });
  }
  if (smart.sas_scan_status && String(smart.sas_scan_status).toLowerCase().includes("halted")) {
    flags.push({ label: "SCAN HALT", title: "Background scan halted due to fatal error", cls: "triage-flag-critical" });
  }
  const verifyErr = smart.sas_uncorrectable_verify_errors;
  const writeErr = smart.sas_uncorrectable_write_errors;
  const readErr = smart.sas_uncorrectable_read_errors;
  if ((verifyErr != null && verifyErr > 0) || (writeErr != null && writeErr > 0)) {
    flags.push({ label: "UNCORR W/V", title: `Uncorrectable errors — verify: ${verifyErr ?? 0}, write: ${writeErr ?? 0}`, cls: "triage-flag-critical" });
  }
  if (readErr != null && readErr > 0) {
    flags.push({ label: `UNCORR R:${readErr}`, title: `Uncorrectable read errors: ${readErr}`, cls: readErr >= 10 ? "triage-flag-critical" : "triage-flag-warning" });
  }
  if (smart.sas_sticky_lba_detected) {
    flags.push({ label: "STICKY LBA", title: "Recurring errors at same LBA — precursor to sector loss", cls: "triage-flag-warning" });
  }
  const defects = smart.sas_grown_defect_list;
  if (defects != null && defects > 0) {
    const cls = defects >= 1000 ? "triage-flag-critical" : defects >= 100 ? "triage-flag-warning" : "triage-flag-info";
    flags.push({ label: `DEF:${defects.toLocaleString()}`, title: `Grown defect list: ${defects.toLocaleString()}`, cls });
  }

  return flags;
}

function _flagsHtml(flags) {
  if (!flags.length) return '<span class="triage-flag-none">—</span>';
  return flags.map(f => `<span class="triage-flag ${f.cls}" title="${escapeHtml(f.title)}">${escapeHtml(f.label)}</span>`).join(" ");
}

// ──────────────────────────────────────────────────────────────────────
// Summary bar: aggregate counts by recommendation status
// ──────────────────────────────────────────────────────────────────────
const REC_ORDER = ["DESTROY", "SCRATCH", "USED_HEAVY", "USED_GOOD", "NEW_STOCK", "LOCKED", "UNKNOWN"];
const REC_COLORS = {
  DESTROY: "triage-chip-destroy",
  SCRATCH: "triage-chip-scratch",
  USED_HEAVY: "triage-chip-heavy",
  USED_GOOD: "triage-chip-good",
  NEW_STOCK: "triage-chip-new",
  LOCKED: "triage-chip-locked",
  UNKNOWN: "triage-chip-unknown"
};

function _renderSummaryBar(drives) {
  if (!triageSummaryBar) return;
  const counts = {};
  drives.forEach(d => {
    const status = (d.recommendation || { status: "UNKNOWN" }).status;
    counts[status] = (counts[status] || 0) + 1;
  });
  const chips = REC_ORDER.filter(s => counts[s]).map(s => {
    const label = s.replace(/_/g, " ");
    return `<span class="triage-chip ${REC_COLORS[s] || ""}">${escapeHtml(label)}: ${counts[s]}</span>`;
  });
  const unknownStatuses = Object.keys(counts).filter(s => !REC_ORDER.includes(s));
  unknownStatuses.forEach(s => {
    chips.push(`<span class="triage-chip triage-chip-unknown">${escapeHtml(s.replace(/_/g, " "))}: ${counts[s]}</span>`);
  });
  const total = drives.length;
  triageSummaryBar.innerHTML = `<span class="triage-summary-total">${total} drive${total !== 1 ? "s" : ""}</span>${chips.join("")}`;
}

// ──────────────────────────────────────────────────────────────────────
// Sorting
// ──────────────────────────────────────────────────────────────────────
function _sortValue(drive, column) {
  const smart = drive.smart || {};
  switch (column) {
    case "bay": return _triageBayLabel(drive).toLowerCase();
    case "serial": return (drive.serial || "").toLowerCase();
    case "model": return (drive.model || "").toLowerCase();
    case "media": return (drive.drive_type || "").toLowerCase();
    case "interface": return (drive.interface_type || "").toLowerCase();
    case "capacity": return smart.capacity_bytes || 0;
    case "poh": return smart.power_on_hours || 0;
    case "health": return drive.health_score ?? -1;
    case "recommendation": return (drive.recommendation || { status: "UNKNOWN" }).status;
    default: return 0;
  }
}

function _sortDrives(drives) {
  if (!triageSortColumn) return drives;
  return [...drives].sort((a, b) => {
    const av = _sortValue(a, triageSortColumn);
    const bv = _sortValue(b, triageSortColumn);
    if (av < bv) return triageSortAsc ? -1 : 1;
    if (av > bv) return triageSortAsc ? 1 : -1;
    return 0;
  });
}

// ──────────────────────────────────────────────────────────────────────
// Export (client-side CSV and JSON)
// ──────────────────────────────────────────────────────────────────────
function _getFilteredDrives() {
  const interfaceFilter = filterInterface.value;
  const mediaTypeFilter = filterMediaType.value;
  const recommendationFilter = filterRecommendation.value;

  const STATUS_MAP = {
    "new": "NEW_STOCK",
    "good": "USED_GOOD",
    "heavy": "USED_HEAVY",
    "destroy": "DESTROY",
    "scratch": "SCRATCH",
    "locked": "LOCKED",
    "unknown": "UNKNOWN"
  };

  return triageDrives.filter(drive => {
    const rec = drive.recommendation || { status: "UNKNOWN" };

    if (interfaceFilter !== "all") {
      const driveInterface = (drive.interface_type || "unknown").toLowerCase();
      if (interfaceFilter === "sata" && !driveInterface.includes("sata")) return false;
      if (interfaceFilter === "sas" && !driveInterface.includes("sas")) return false;
      if (interfaceFilter === "nvme" && !driveInterface.includes("nvme")) return false;
    }

    if (mediaTypeFilter !== "all") {
      const driveType = (drive.drive_type || "unknown").toLowerCase();
      if (mediaTypeFilter === "ssd" && driveType !== "ssd") return false;
      if (mediaTypeFilter === "hdd" && driveType !== "hdd") return false;
    }

    if (recommendationFilter !== "all") {
      const expectedStatus = STATUS_MAP[recommendationFilter];
      if (!expectedStatus || rec.status !== expectedStatus) return false;
    }

    return true;
  });
}

function _exportTriageData(format) {
  const exportDrives = _getFilteredDrives();
  if (!exportDrives.length) {
    alert("No drives to export");
    return;
  }
  const rows = exportDrives.map(drive => {
    const smart = drive.smart || {};
    const rec = drive.recommendation || { status: "UNKNOWN", comment: "" };
    const flags = _triageFlags(drive);
    return {
      bay: _triageBayLabel(drive),
      serial: drive.serial || "",
      model: drive.model || "",
      media_type: (drive.drive_type || "").toUpperCase(),
      interface: (drive.interface_type || "").toUpperCase(),
      capacity: drive.capacity_str || "",
      power_on_hours: smart.power_on_hours ?? "",
      health_score: drive.health_score ?? "",
      recommendation: rec.status,
      recommendation_comment: rec.comment || "",
      flags: flags.map(f => f.label).join("; "),
      sas_grown_defects: smart.sas_grown_defect_list ?? "",
      sas_scan_status: smart.sas_scan_status ?? "",
      sas_uncorrectable_read: smart.sas_uncorrectable_read_errors ?? "",
      sas_uncorrectable_write: smart.sas_uncorrectable_write_errors ?? "",
      sas_uncorrectable_verify: smart.sas_uncorrectable_verify_errors ?? "",
      sas_sticky_lba: smart.sas_sticky_lba_detected ?? ""
    };
  });

  if (format === "json") {
    const blob = new Blob([JSON.stringify(rows, null, 2)], { type: "application/json" });
    _downloadBlob(blob, `triage-report-${_timestamp()}.json`);
  } else {
    const headers = Object.keys(rows[0] || { bay: "" });
    const csvLines = [headers.join(",")];
    rows.forEach(row => {
      csvLines.push(headers.map(h => _csvEscape(row[h])).join(","));
    });
    const blob = new Blob([csvLines.join("\n")], { type: "text/csv" });
    _downloadBlob(blob, `triage-report-${_timestamp()}.csv`);
  }
}

function _csvEscape(val) {
  const s = String(val ?? "");
  if (s.includes(",") || s.includes('"') || s.includes("\n") || s.includes("\r")) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

function _timestamp() {
  return new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
}

function _downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// ──────────────────────────────────────────────────────────────────────
// Main render
// ──────────────────────────────────────────────────────────────────────
async function loadTriageReport() {
  try {
    const response = await safeFetch("/api/drives");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const drives = await response.json();
    triageDrives = drives.filter(d => d.present);
    renderTriageTable();
  } catch (error) {
    console.error("Failed to load triage report:", error);
    const tableHead = document.querySelector("#triageTable thead tr");
    const colCount = tableHead ? tableHead.querySelectorAll("th").length : 10;
    triageTableBody.innerHTML = `<tr><td colspan="${colCount}" class="triage-error-cell">Failed to load drives: ${escapeHtml(error.message)}</td></tr>`;
    if (triageSummaryBar) triageSummaryBar.innerHTML = "";
  }
}

function renderTriageTable() {
  const filteredDrives = _getFilteredDrives();

  _renderSummaryBar(filteredDrives);

  if (filteredDrives.length === 0) {
    const tableHead = document.querySelector("#triageTable thead tr");
    const colCount = tableHead ? tableHead.querySelectorAll("th").length : 10;
    triageTableBody.innerHTML = `<tr><td colspan="${colCount}" class="triage-empty-cell">No drives match the current filter criteria</td></tr>`;
    return;
  }

  const sortedDrives = _sortDrives(filteredDrives);

  const rowsHtml = sortedDrives.map(drive => {
    const smart = drive.smart || {};
    const rec = drive.recommendation || { status: "UNKNOWN", comment: "" };

    let healthClass = "health-score-good";
    if (drive.health_score != null && drive.health_score <= 25) healthClass = "health-score-fail";
    else if (drive.health_score != null && drive.health_score <= 50) healthClass = "health-score-warning";

    const bayLabel = _triageBayLabel(drive);
    const mediaType = (drive.drive_type || "unknown").toUpperCase();
    const poh = smart.power_on_hours != null ? `${smart.power_on_hours.toLocaleString()}h` : "-";
    const flags = _triageFlags(drive);
    const recLabel = rec.status.replace(/_/g, " ").toUpperCase();
    const recComment = rec.comment ? ` title="${escapeHtml(rec.comment)}"` : "";

    return `
      <tr>
        <td>${escapeHtml(bayLabel)}</td>
        <td>${escapeHtml(drive.serial || "-")}</td>
        <td>${escapeHtml(drive.model || "-")}</td>
        <td>${escapeHtml(mediaType)}</td>
        <td>${escapeHtml((drive.interface_type || "-").toUpperCase())}</td>
        <td>${escapeHtml(drive.capacity_str || "-")}</td>
        <td>${escapeHtml(poh)}</td>
        <td class="${healthClass}">${drive.health_score ?? '—'}</td>
        <td class="triage-flags-cell">${_flagsHtml(flags)}</td>
        <td${recComment}>${escapeHtml(recLabel)}</td>
      </tr>
    `;
  }).join("");

  triageTableBody.innerHTML = rowsHtml;
  _updateSortIndicators();
}

function _updateSortIndicators() {
  document.querySelectorAll("#triageTable th[data-sort]").forEach(th => {
    th.classList.remove("triage-sort-asc", "triage-sort-desc");
    if (th.dataset.sort === triageSortColumn) {
      th.classList.add(triageSortAsc ? "triage-sort-asc" : "triage-sort-desc");
    }
  });
}

// ──────────────────────────────────────────────────────────────────────
// WebSocket: update triage data from smart_data_updated events
// ──────────────────────────────────────────────────────────────────────
function updateTriageDriveData(data) {
  const { device, smart, health_score, recommendation, marker, status } = data || {};
  const idx = triageDrives.findIndex(d => d.device === device);
  if (idx !== -1) {
    if (smart) triageDrives[idx].smart = { ...triageDrives[idx].smart, ...smart };
    if (health_score !== undefined) triageDrives[idx].health_score = health_score;
    if (recommendation) triageDrives[idx].recommendation = recommendation;
    if (marker !== undefined) triageDrives[idx].marker = marker;
    if (status) triageDrives[idx].status = status;
    if (document.getElementById('triagePanel').classList.contains('active')) {
      renderTriageTable();
    }
  }
}

// ──────────────────────────────────────────────────────────────────────
// Event listeners
// ──────────────────────────────────────────────────────────────────────
if (refreshTriageBtn) {
  refreshTriageBtn.addEventListener("click", () => loadTriageReport());
}

if (printTriageBtn) {
  printTriageBtn.addEventListener("click", () => {
    window.print();
  });
}

if (exportCsvTriageBtn) {
  exportCsvTriageBtn.addEventListener("click", () => _exportTriageData("csv"));
}

if (exportJsonTriageBtn) {
  exportJsonTriageBtn.addEventListener("click", () => _exportTriageData("json"));
}

// Filter change listeners
[filterInterface, filterMediaType, filterRecommendation].forEach(select => {
  if (select) {
    select.addEventListener("change", () => renderTriageTable());
  }
});

// Sort listeners on column headers
document.querySelectorAll("#triageTable th[data-sort]").forEach(th => {
  th.addEventListener("click", () => {
    const col = th.dataset.sort;
    if (triageSortColumn === col) {
      triageSortAsc = !triageSortAsc;
    } else {
      triageSortColumn = col;
      triageSortAsc = true;
    }
    renderTriageTable();
  });
});

// Load triage report when tab is activated
const triageTab = document.querySelector('[data-tab="triagePanel"]');
if (triageTab) {
  triageTab.addEventListener("click", () => {
    loadTriageReport();
  });
}

// --- END OF FILE frontend/triageReport.js ---
