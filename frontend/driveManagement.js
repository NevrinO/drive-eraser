// --- START OF FILE frontend/driveManagement.js ---
// Drive discovery, rendering, and batch operations

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
      if (!response.ok) return;
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

let pendingHealthGatePayload = null;

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
      lastUpdated.textContent = `Last updated: ${new Date().toLocaleTimeString()}`;
    }
  } catch (error) {
    if (!silent) apiStatus.textContent = `API Status: Error (${error.message})`;
  }
}

function renderSkeletonBays() {
  const bayEntries = Object.entries(localBayMapCopy);
  if (bayEntries.length === 0) return;

  const hasEnclosures = workbenchEnclosures && Object.keys(workbenchEnclosures).length > 0;

  if (hasEnclosures) {
    _renderSkeletonByEnclosure(bayEntries);
  } else {
    _renderSkeletonLegacy(bayEntries);
  }
}

function _skeletonCardHtml(bayId, conf) {
  let bayPrimaryText;
  if (conf.label && String(conf.label).trim()) {
    bayPrimaryText = String(conf.label).trim();
  } else if (conf.display_number != null) {
    bayPrimaryText = `BAY ${conf.display_number}`;
  } else {
    bayPrimaryText = (bayId && bayId.toLowerCase().startsWith('bay') ? bayId.toUpperCase() : 'Bay');
  }

  return `
    <article class="bay-card skeleton" data-bay="${escapeHtml(bayId)}">
      <div class="bay-banner">LOADING...</div>
      <div class="bay-header-row">
        <div class="bay-number">${escapeHtml(bayPrimaryText)}</div>
      </div>
      <div class="skeleton-line"></div>
      <div class="skeleton-line short"></div>
      <div class="skeleton-bar"></div>
    </article>
  `;
}

function _renderSkeletonByEnclosure(bayEntries) {
  const baysByEnclosure = {};
  const unassigned = [];

  bayEntries.forEach(([bayId, conf]) => {
    if (conf.enclosure_id && workbenchEnclosures[conf.enclosure_id]) {
      if (!baysByEnclosure[conf.enclosure_id]) {
        baysByEnclosure[conf.enclosure_id] = [];
      }
      baysByEnclosure[conf.enclosure_id].push([bayId, conf]);
    } else {
      unassigned.push([bayId, conf]);
    }
  });

  let gridHtml = "";

  Object.keys(workbenchEnclosures).sort((a, b) => {
    const orderA = workbenchEnclosures[a].display_order || 0;
    const orderB = workbenchEnclosures[b].display_order || 0;
    return orderA - orderB;
  }).forEach(enclosureId => {
    const enclosure = workbenchEnclosures[enclosureId];
    const template = enclosure.template || {};
    const templateRows = template.rows || 1;
    const templateCols = template.cols || 1;
    const skipPositions = template.skip_positions || [];
    const skipSet = new Set(skipPositions.map(p => `${p.row},${p.col}`));

    const enclosureBays = baysByEnclosure[enclosureId] || [];
    const bayByPosition = new Map();
    enclosureBays.forEach(([bayId, conf]) => {
      const pos = conf.physical_position;
      if (pos && Number.isInteger(pos.row) && Number.isInteger(pos.col)) {
        bayByPosition.set(`${pos.row},${pos.col}`, [bayId, conf]);
      }
    });

    gridHtml += `
      <div class="enclosure-section" data-enclosure-id="${escapeHtml(enclosureId)}">
        <div class="enclosure-section-header">
          <h3 style="margin: 0; font-size: 1.1rem; color: var(--color-primary);">${escapeHtml(enclosure.name || enclosureId)}</h3>
          <small style="color: #888;">${enclosureBays.length} slots</small>
        </div>
        <div class="enclosure-bays-grid" style="grid-template-columns: repeat(${templateCols}, minmax(0, 1fr));">
    `;

    for (let row = 0; row < templateRows; row++) {
      for (let col = 0; col < templateCols; col++) {
        const posKey = `${row},${col}`;
        if (skipSet.has(posKey)) {
          gridHtml += `
            <article class="bay-card blocked" data-bay="blocked-${row}-${col}">
              <div class="bay-banner" style="background: transparent; color: #444;"></div>
              <div class="bay-header-row">
                <div class="bay-number" style="color: #444;"></div>
              </div>
            </article>
          `;
        } else {
          const entry = bayByPosition.get(posKey);
          if (entry) {
            gridHtml += _skeletonCardHtml(entry[0], entry[1]);
          } else {
            gridHtml += `
              <article class="bay-card empty" data-bay="empty-${row}-${col}">
                <div class="bay-banner">EMPTY BAY</div>
                <div class="bay-header-row">
                  <div class="bay-number">— Empty slot —</div>
                </div>
              </article>
            `;
          }
        }
      }
    }

    gridHtml += `
        </div>
      </div>
    `;
  });

  if (unassigned.length > 0) {
    gridHtml += `
      <div class="enclosure-section">
        <div class="enclosure-section-header">
          <h3 style="margin: 0; font-size: 1.1rem; color: var(--color-warning);">Unassigned Drives</h3>
          <small style="color: #888;">${unassigned.length} drives</small>
        </div>
        <div class="enclosure-bays-grid">
    `;
    unassigned.forEach(([bayId, conf]) => {
      gridHtml += _skeletonCardHtml(bayId, conf);
    });
    gridHtml += `
        </div>
      </div>
    `;
  }

  baysGrid.innerHTML = gridHtml;
  baysGrid.style.display = 'block';
}

function _renderSkeletonLegacy(bayEntries) {
  let skipPositions = [];
  if (localLayoutMetadata.template_id && availableLayoutTemplates.length > 0) {
    const template = availableLayoutTemplates.find(t => t.id === localLayoutMetadata.template_id);
    if (template && template.skip_positions) {
      skipPositions = template.skip_positions;
    }
  }
  const skipSet = new Set(skipPositions.map(p => `${p.row},${p.col}`));

  let templateRows = 1;
  let templateCols = 4;
  if (localLayoutMetadata.template_id && availableLayoutTemplates.length > 0) {
    const template = availableLayoutTemplates.find(t => t.id === localLayoutMetadata.template_id);
    if (template) {
      templateRows = template.rows || 1;
      templateCols = template.cols || 4;
    }
  } else {
    const bayCount = bayEntries.length;
    if (bayCount <= 4) templateCols = 4;
    else if (bayCount <= 8) templateCols = 4;
    else if (bayCount <= 10) templateCols = 5;
    else templateCols = 4;
  }

  baysGrid.style.gridTemplateColumns = `repeat(${templateCols}, minmax(0, 1fr))`;

  const bayByPosition = new Map();
  bayEntries.forEach(([bayId, conf]) => {
    const pos = conf.physical_position;
    if (pos && Number.isInteger(pos.row) && Number.isInteger(pos.col)) {
      bayByPosition.set(`${pos.row},${pos.col}`, [bayId, conf]);
    }
  });

  let gridHtml = "";
  for (let row = 0; row < templateRows; row++) {
    for (let col = 0; col < templateCols; col++) {
      const posKey = `${row},${col}`;
      if (skipSet.has(posKey)) {
        gridHtml += `
          <article class="bay-card blocked" data-bay="blocked-${row}-${col}">
            <div class="bay-banner" style="background: transparent; color: #444;"></div>
            <div class="bay-header-row">
              <div class="bay-number" style="color: #444;"></div>
            </div>
          </article>
        `;
      } else {
        const entry = bayByPosition.get(posKey);
        if (entry) {
          gridHtml += _skeletonCardHtml(entry[0], entry[1]);
        } else {
          gridHtml += `
            <article class="bay-card empty" data-bay="empty-${row}-${col}">
              <div class="bay-banner">EMPTY BAY</div>
              <div class="bay-header-row">
                <div class="bay-number">— Empty slot —</div>
              </div>
            </article>
          `;
        }
      }
    }
  }

  baysGrid.innerHTML = gridHtml;
}

function renderBays(drives) {
  // Check if enclosures are available for grouping
  const hasEnclosures = workbenchEnclosures && Object.keys(workbenchEnclosures).length > 0;

  if (hasEnclosures) {
    renderBaysByEnclosure(drives);
  } else {
    renderBaysLegacy(drives);
  }
}

function renderBaysByEnclosure(drives) {
  // Group drives by enclosure
  const drivesByEnclosure = {};
  const unassignedDrives = [];

  drives.forEach(drive => {
    if (drive.enclosure_id && workbenchEnclosures[drive.enclosure_id]) {
      if (!drivesByEnclosure[drive.enclosure_id]) {
        drivesByEnclosure[drive.enclosure_id] = [];
      }
      drivesByEnclosure[drive.enclosure_id].push(drive);
    } else {
      unassignedDrives.push(drive);
    }
  });

  let gridHtml = "";
  const allDrivesWithInvalidPositions = [];

  // Render each enclosure
  Object.keys(workbenchEnclosures).sort((a, b) => {
    const orderA = workbenchEnclosures[a].display_order || 0;
    const orderB = workbenchEnclosures[b].display_order || 0;
    return orderA - orderB;
  }).forEach(enclosureId => {
    const enclosure = workbenchEnclosures[enclosureId];
    const enclosureDrives = drivesByEnclosure[enclosureId] || [];
    const template = enclosure.template || {};

    // Get template dimensions
    const templateRows = template.rows || 1;
    const templateCols = template.cols || 1;
    const skipPositions = template.skip_positions || [];
    const skipSet = new Set(skipPositions.map(p => `${p.row},${p.col}`));

    // Create a map of drives by their physical position
    const driveByPosition = new Map();
    const drivesWithInvalidPositions = [];
    enclosureDrives.forEach(drive => {
      const pos = drive.physical_position;
      if (pos && Number.isInteger(pos.row) && Number.isInteger(pos.col)) {
        driveByPosition.set(`${pos.row},${pos.col}`, drive);
      } else {
        drivesWithInvalidPositions.push(drive);
        allDrivesWithInvalidPositions.push(drive);
      }
    });

    gridHtml += `
      <div class="enclosure-section" data-enclosure-id="${escapeHtml(enclosureId)}">
        <div class="enclosure-section-header">
          <h3 style="margin: 0; font-size: 1.1rem; color: var(--color-primary);">${escapeHtml(enclosure.name || enclosureId)}</h3>
          <small style="color: #888;">${enclosureDrives.length} slots</small>
        </div>
        <div class="enclosure-bays-grid" style="grid-template-columns: repeat(${templateCols}, minmax(0, 1fr));">
    `;

    // Generate grid cells based on template dimensions
    for (let row = 0; row < templateRows; row++) {
      for (let col = 0; col < templateCols; col++) {
        const posKey = `${row},${col}`;
        const isSkipped = skipSet.has(posKey);
        const drive = driveByPosition.get(posKey);

        if (isSkipped) {
          // Render blocked placeholder
          gridHtml += `
            <article class="bay-card blocked" data-bay="blocked-${row}-${col}">
              <div class="bay-banner" style="background: transparent; color: #444;"></div>
              <div class="bay-header-row">
                <div class="bay-number" style="color: #444;"></div>
              </div>
            </article>
          `;
        } else if (drive) {
          gridHtml += renderBayCard(drive);
        } else {
          // Render empty placeholder for grid position with no drive
          gridHtml += `
            <article class="bay-card empty" data-bay="empty-${row}-${col}">
              <div class="bay-banner">EMPTY BAY</div>
              <div class="bay-header-row">
                <div class="bay-number">— Empty slot —</div>
              </div>
            </article>
          `;
        }
      }
    }

    gridHtml += `
        </div>
      </div>
    `;
  });

  // Render unassigned drives if any
  if (unassignedDrives.length > 0) {
    gridHtml += `
      <div class="enclosure-section">
        <div class="enclosure-section-header">
          <h3 style="margin: 0; font-size: 1.1rem; color: var(--color-warning);">Unassigned Drives</h3>
          <small style="color: #888;">${unassignedDrives.length} drives</small>
        </div>
        <div class="enclosure-bays-grid">
    `;

    // Sort unassigned drives by physical position (row, col) to follow traversal pattern
    unassignedDrives.sort((a, b) => {
      const aPos = a.physical_position || {};
      const bPos = b.physical_position || {};
      const hasAPos = Number.isInteger(aPos.row) && Number.isInteger(aPos.col);
      const hasBPos = Number.isInteger(bPos.row) && Number.isInteger(bPos.col);
      if (hasAPos && hasBPos) {
        if (aPos.row !== bPos.row) return aPos.row - bPos.row;
        if (aPos.col !== bPos.col) return aPos.col - bPos.col;
      }
      // Fallback to physical_slot_number if physical_position is missing
      const slotA = a.physical_slot_number || 0;
      const slotB = b.physical_slot_number || 0;
      return slotA - slotB;
    });

    unassignedDrives.forEach(drive => {
      gridHtml += renderBayCard(drive);
    });

    gridHtml += `
        </div>
      </div>
    `;
  }

  // Render drives with invalid positions if any
  // Filter to exclude: duplicates (MPIO), loops, dvdroms, usbs, and other unwanted device types
  if (allDrivesWithInvalidPositions.length > 0) {
    // Deduplicate by serial number to avoid MPIO duplicates
    const seenSerials = new Set();
    const filteredDrives = allDrivesWithInvalidPositions.filter(drive => {
      const serial = drive.serial;
      if (!serial) return false;
      if (seenSerials.has(serial)) return false;
      seenSerials.add(serial);

      // Filter out unwanted device types (loops, dvdroms, usbs)
      // Only include drives with known interface_type (nvme, sata, sas) and known drive_type (ssd, hdd)
      const iface = (drive.interface_type || "").toLowerCase();
      const dtype = (drive.drive_type || "").toLowerCase();
      const isKnownInterface = ["nvme", "sata", "sas"].includes(iface);
      const isKnownDriveType = ["ssd", "hdd"].includes(dtype);

      return isKnownInterface && isKnownDriveType;
    });

    if (filteredDrives.length > 0) {
      console.warn(`Warning: ${filteredDrives.length} drive(s) have invalid or missing physical_position data and are rendered in fallback section`);

      gridHtml += `
        <div class="enclosure-section">
          <div class="enclosure-section-header">
            <h3 style="margin: 0; font-size: 1.1rem; color: var(--color-warning);">Drives with Invalid Positions</h3>
            <small style="color: #888;">${filteredDrives.length} drives</small>
          </div>
          <div class="enclosure-bays-grid">
      `;

      // Sort by bay for consistent ordering
      filteredDrives.sort((a, b) => {
        const bayA = a.bay || "";
        const bayB = b.bay || "";
        return bayA.localeCompare(bayB);
      });

      filteredDrives.forEach(drive => {
        gridHtml += renderBayCard(drive);
      });

      gridHtml += `
          </div>
        </div>
      `;
    }
  }

  baysGrid.innerHTML = gridHtml;
  baysGrid.style.display = 'block';
}

function renderBaysLegacy(drives) {
  // Get skip positions from template if available
  let skipPositions = [];
  if (localLayoutMetadata.template_id && availableLayoutTemplates.length > 0) {
    const template = availableLayoutTemplates.find(t => t.id === localLayoutMetadata.template_id);
    if (template && template.skip_positions) {
      skipPositions = template.skip_positions;
    }
  }
  const skipSet = new Set(skipPositions.map(p => `${p.row},${p.col}`));

  const orderedDrives = [...drives].sort((a, b) => {
    const aPos = a.physical_position || {};
    const bPos = b.physical_position || {};
    const hasAPos = Number.isInteger(aPos.row) && Number.isInteger(aPos.col);
    const hasBPos = Number.isInteger(bPos.row) && Number.isInteger(bPos.col);
    if (hasAPos && hasBPos) {
      if (aPos.row !== bPos.row) return aPos.row - bPos.row;
      if (aPos.col !== bPos.col) return aPos.col - bPos.col;
    }
    const aNum = parseInt(String(a.display_number || a.bay).replace(/\D/g, ""), 10) || 0;
    const bNum = parseInt(String(b.display_number || b.bay).replace(/\D/g, ""), 10) || 0;
    return aNum - bNum;
  });

  // Determine grid columns based on template or default to 4
  let gridCols = 4;
  if (localLayoutMetadata.template_id && availableLayoutTemplates.length > 0) {
    const template = availableLayoutTemplates.find(t => t.id === localLayoutMetadata.template_id);
    if (template && template.cols) {
      gridCols = template.cols;
    }
  } else {
    const bayCount = drives.length;
    if (bayCount <= 4) {
      gridCols = 4;
    } else if (bayCount <= 8) {
      gridCols = 4;
    } else if (bayCount <= 10) {
      gridCols = 5;
    } else {
      gridCols = 4;
    }
  }
  baysGrid.style.gridTemplateColumns = `repeat(${gridCols}, minmax(0, 1fr))`;

  // Get template dimensions for grid generation
  let templateRows = 1;
  let templateCols = gridCols;
  if (localLayoutMetadata.template_id && availableLayoutTemplates.length > 0) {
    const template = availableLayoutTemplates.find(t => t.id === localLayoutMetadata.template_id);
    if (template) {
      templateRows = template.rows || 1;
      templateCols = template.cols || gridCols;
    }
  }

  // Create a map of drives by their physical position
  const driveByPosition = new Map();
  orderedDrives.forEach(drive => {
    const pos = drive.physical_position;
    if (pos && Number.isInteger(pos.row) && Number.isInteger(pos.col)) {
      driveByPosition.set(`${pos.row},${pos.col}`, drive);
    }
  });

  // Generate grid cells
  let gridHtml = "";
  for (let row = 0; row < templateRows; row++) {
    for (let col = 0; col < templateCols; col++) {
      const posKey = `${row},${col}`;
      const isSkipped = skipSet.has(posKey);
      const drive = driveByPosition.get(posKey);

      if (isSkipped) {
        // Render blocked placeholder
        gridHtml += `
          <article class="bay-card blocked" data-bay="blocked-${row}-${col}">
            <div class="bay-banner" style="background: transparent; color: #444;"></div>
            <div class="bay-header-row">
              <div class="bay-number" style="color: #444;"></div>
            </div>
          </article>
        `;
      } else if (drive) {
        gridHtml += renderBayCard(drive);
      } else {
        // Render empty placeholder for grid position with no drive
        gridHtml += `
          <article class="bay-card empty" data-bay="empty-${row}-${col}">
            <div class="bay-banner">EMPTY BAY</div>
            <div class="bay-header-row">
              <div class="bay-number">— Empty slot —</div>
            </div>
          </article>
        `;
      }
    }
  }

  baysGrid.innerHTML = gridHtml;
}

function getZeroCheckStateClass(drive) {
  const zc = drive.zero_check || {};
  const status = zc.status;
  if (status === "running") return "zero_check_running";
  if (status === "queued") return "zero_check_running";
  if (status === "completed") {
    if (zc.result === "zeroed") return "zero_check_zeroed";
    if (zc.result === "data_present") return "zero_check_data_present";
    if (zc.result === "inconclusive") return "zero_check_inconclusive";
  }
  if (status === "failed") return "zero_check_failed";
  if (status === "cancelled") return "zero_check_failed";
  return null;
}

function getZeroCheckBannerLabel(drive) {
  const zc = drive.zero_check || {};
  if (zc.status === "running") return "ZERO CHECK RUNNING";
  if (zc.status === "queued") return "ZERO CHECK QUEUED";
  if (zc.status === "completed") {
    if (zc.result === "zeroed") return "LIKELY ZEROED";
    if (zc.result === "data_present") return "DATA PRESENT";
    if (zc.result === "inconclusive") return "ZERO CHECK INCONCLUSIVE";
  }
  if (zc.status === "failed") return "ZERO CHECK FAILED";
  if (zc.status === "cancelled") return "ZERO CHECK CANCELLED";
  return null;
}

function renderBayCard(drive) {
  const isReady = drive.present && !drive.locked && drive.role !== "os" && drive.role !== "reserved";
  const isEmpty = !drive.present;
  const isCritical = String(drive.status).toUpperCase() === "FAILED";
  const isRunning = String(drive.status).toUpperCase() === "RUNNING";
  const isCompleted = drive.marker && drive.marker.status !== "none" && drive.marker.status !== "corrupted" && drive.marker.status !== "written_since_wipe";
  const isWrittenSinceWipe = drive.marker && drive.marker.status === "written_since_wipe";
  const isMarkerDisabled = drive.marker && (drive.marker.status === "disabled_per_request" || drive.marker.status === "disabled_by_policy");
  const isUnconfigured = isBayUnconfigured(drive);
  const isSmartTestRunning = drive.smart_test_status === "running" || drive.smart_test_status === "in_progress";
  const zeroCheckClass = (!isEmpty && !isRunning && !isSmartTestRunning && !isCompleted && !isWrittenSinceWipe && !isMarkerDisabled && !drive.locked && drive.role !== "os" && drive.role !== "reserved") ? getZeroCheckStateClass(drive) : null;
  const zeroCheckLabel = zeroCheckClass ? getZeroCheckBannerLabel(drive) : null;

  let stateClass = "healthy";
  let bannerLabel = "READY / UNPROCESSED";

  if (isEmpty) {
    stateClass = "empty";
    bannerLabel = "EMPTY BAY";
  } else if (drive.role === "os") {
    stateClass = "locked";
    bannerLabel = "OS DRIVE - PROTECTED";
  } else if (drive.locked) {
    stateClass = "locked";
    bannerLabel = "LOCKED";
  } else if (isCritical) {
    stateClass = "failed";
    bannerLabel = "⚠️ CRITICAL FAILURE";
  } else if (isRunning) {
    stateClass = "running";
    bannerLabel = "WIPING IN PROGRESS";
  } else if (isSmartTestRunning) {
    stateClass = "running";
    bannerLabel = "SMART TEST RUNNING";
  } else if (isMarkerDisabled) {
    stateClass = "completed";
    bannerLabel = "SANITIZED (NO MARKER)";
  } else if (isWrittenSinceWipe) {
    stateClass = "written-since-wipe";
    bannerLabel = "⚠️ POST-WIPE WRITES";
  } else if (isCompleted) {
    stateClass = "completed";
    bannerLabel = "SANITIZED (PRISTINE)";
  } else if (zeroCheckClass) {
    stateClass = zeroCheckClass;
    bannerLabel = zeroCheckLabel;
  } else if (isUnconfigured) {
    stateClass = "unconfigured";
    bannerLabel = "⚠️ UNCONFIGURED BAY";
  }

  // Recommendation tint: override internal card background based on recommendation status.
  // Applies to ready (healthy) and completed (sanitized) states. Does not change border color.
  // Checked before the unconfigured string mutation so stateClass is still a clean single value.
  const recStatus = drive.recommendation ? String(drive.recommendation.status).toUpperCase() : "";
  const isTintable = stateClass === "healthy" || stateClass === "completed" || stateClass === "written-since-wipe";

  if (isUnconfigured) {
    stateClass += " unconfigured";
  }
  let recClass = "";
  if (isTintable && recStatus) {
    if (recStatus === "DESTROY") recClass = "rec-destroy";
    else if (recStatus === "SCRATCH") recClass = "rec-scratch";
    else if (recStatus === "USED_HEAVY") recClass = "rec-used-heavy";
    else if (recStatus === "USED_GOOD" || recStatus === "NEW_STOCK") recClass = "rec-used-good";
  }

  // Sub-banner: shows recommendation or SMART status below the main banner.
  // Shown on all states with drive health data, except mid-operation states
  // (running, smart test, zero check running) and states with no drive data
  // (empty, OS, locked, unconfigured).
  const isZeroCheckRunning = zeroCheckClass === "zero_check_running";
  const showSubBanner = !isEmpty && drive.role !== "os" && !drive.locked &&
    !isRunning && !isSmartTestRunning && !isUnconfigured && !isZeroCheckRunning;

  let subBannerHtml = "";
  if (showSubBanner) {
    const smartPolling = drive.smart && drive.smart.smart_polling;
    const smartFailed = drive.smart && String(drive.smart.status).toUpperCase() === "FAILED";
    let subLabel = "";
    let subClass = "";
    if (smartPolling) {
      subLabel = "⏳ SMART LOADING...";
      subClass = "sub-banner-info";
    } else if (smartFailed) {
      subLabel = "⚠️ SMART FAILED";
      subClass = "sub-banner-danger";
    } else if (recStatus === "UNKNOWN") {
      subLabel = "⚠️ SMART UNAVAILABLE";
      subClass = "sub-banner-neutral";
    } else if (recStatus === "DESTROY") {
      subLabel = "⚠️ DESTROY RECOMMENDED";
      subClass = "sub-banner-danger";
    } else if (recStatus === "SCRATCH") {
      subLabel = "⚠️ SCRATCH RECOMMENDED";
      subClass = "sub-banner-warning";
    } else if (recStatus === "USED_HEAVY") {
      subLabel = "USED HEAVY";
      subClass = "sub-banner-warning";
    } else if (recStatus === "USED_GOOD") {
      subLabel = "USED GOOD";
      subClass = "sub-banner-success";
    } else if (recStatus === "NEW_STOCK") {
      subLabel = "NEW STOCK";
      subClass = "sub-banner-success";
    }
    if (subLabel) {
      subBannerHtml = `<div class="bay-sub-banner ${subClass}">${escapeHtml(subLabel)}</div>`;
    }
  }

  const healthScore = calculateDriveHealthScore(drive);
  const classes = ["bay-card", stateClass];
  if (recClass) classes.push(recClass);
  if (subBannerHtml) classes.push("has-sub-banner");
  if (selectedBays.has(drive.bay)) classes.push("selected");

  const ifaceLabel = drive.interface_type ? drive.interface_type.toUpperCase() : "SATA";
  const badgeClass = ifaceLabel.includes("NVME") ? "badge-nvme" : ifaceLabel.includes("SAS") ? "badge-sas" : "badge-sata";
  const driveTypeLabel = drive.drive_type && (drive.drive_type === "ssd" || drive.drive_type === "hdd") ? drive.drive_type.toUpperCase() : "";
  const driveTypeClass = drive.drive_type === "ssd" ? "badge-ssd" : "badge-hdd";

  const progressPercent = drive.progress_percent !== undefined ? drive.progress_percent : 0.0;
  const phaseLabel = drive.current_phase || "Sanitizing...";

  // ──────────────────────────────────────────────────────────────────────
  // REGRESSION GUARD: Workbench cards must show EITHER the label OR the
  // bay number — NEVER both concatenated. The label (e.g. "BAY 5") is the
  // primary display text. If no label exists, fall back to "BAY {n}" or
  // the raw bay id. Past agents have repeatedly added ` - ${drive.label}`
  // alongside the bay number, causing cards to display "BAY 17 - BAY 5".
  // This is documented in lessons-learned.md Rule 108 and will fail code
  // audit. Do NOT concatenate label with bay number using " - " or any
  // other separator.
  // ──────────────────────────────────────────────────────────────────────
  let bayPrimaryText;
  if (drive.label && String(drive.label).trim()) {
    bayPrimaryText = String(drive.label).trim();
  } else if (drive.display_number != null) {
    bayPrimaryText = `BAY ${drive.display_number}`;
  } else {
    bayPrimaryText = (drive.bay && drive.bay.toLowerCase().startsWith('bay') ? drive.bay.toUpperCase() : 'Bay');
  }

  // Display MPIO device path if available
  const devicePath = drive.mpio_device || drive.device || "-";

  return `
    <article class="${classes.join(" ")}" data-bay="${escapeHtml(drive.bay)}">
      <input type="checkbox" class="card-checkbox" data-checkbox-bay="${escapeHtml(drive.bay)}" ${selectedBays.has(drive.bay) ? "checked" : ""} ${isBatchMode && isReady ? 'style="display: block;"' : ""}>
      <div class="bay-banner">${escapeHtml(bannerLabel)}</div>
      ${subBannerHtml}
      <div class="bay-header-row">
        <div class="bay-number">
          ${escapeHtml(bayPrimaryText)}
        </div>
        ${isEmpty ? "" : `
          <div style="display: flex; gap: 4px; align-items: center;">
            <div class="drive-type-badge ${badgeClass}">${escapeHtml(ifaceLabel)}</div>
            ${driveTypeLabel ? `<div class="drive-type-badge ${driveTypeClass}" style="font-size: 0.65rem;">${escapeHtml(driveTypeLabel)}</div>` : ""}
            ${isUnconfigured ? `<div class="unconfigured-badge" title="This bay has no device path configured in bay_map.json">⚠️ Unconfigured</div>` : ""}
          </div>
        `}
      </div>
      ${isEmpty ? `<div class="empty-label">${isUnconfigured ? "— UNCONFIGURED —" : "— Empty slot —"}</div>` : `
        <div class="drive-model">${escapeHtml(drive.model || "Generic Drive")}</div>
        <div class="drive-serial">S/N: ${escapeHtml(drive.serial || "-")}</div>

        ${isRunning ? `
          <div class="health-label">
            <span style="color: var(--color-primary); font-weight: bold;">${escapeHtml(phaseLabel)}</span>
            <span style="color: var(--color-primary); font-weight: bold;">${progressPercent}%</span>
          </div>
          <div class="health-bar-track">
            <div class="health-bar-fill fill-blue" style="width: ${progressPercent}%"></div>
          </div>
        ` : drive.smart && drive.smart.smart_polling ? `
          <div class="health-label">
            <span style="color: var(--color-warning);">Loading SMART...</span>
            <span style="color: var(--color-warning);">⏳</span>
          </div>
          <div class="health-bar-track">
            <div class="health-bar-fill fill-gray" style="width: 100%"></div>
          </div>
        ` : drive.health_score === null && drive.smart && String(drive.smart.status).toUpperCase() === "UNKNOWN" ? `
          <div class="health-label">
            <span style="color: var(--color-text-muted);">Life Expectancy</span>
            <span style="color: var(--color-text-muted);">N/A</span>
          </div>
          <div class="health-bar-track">
            <div class="health-bar-fill fill-gray" style="width: 100%"></div>
          </div>
        ` : `
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
          <span>${escapeHtml(devicePath)}</span>
        </div>
      `}
    </article>
  `;
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
    currentDetailDrive = drive;
    renderLiveDetails(drive);
    openModal(bayDetailModal);
  }
});

async function handleZeroCheckAction(bay, action) {
  try {
    const method = action === "start" ? "POST" : "DELETE";
    const response = await safeFetch(`/api/drives/${encodeURIComponent(bay)}/zero-check`, { method });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      console.error(`Zero-check ${action} failed for ${bay}:`, data.error || response.status);
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
  }
}

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

openBatchWipeModalBtn.addEventListener("click", async () => {
  await renderBatchModalForm();
  openModal(batchWipeModal);
});

async function renderBatchModalForm() {
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

    const displayLabel = drive?.display_number ? `BAY ${drive.display_number}` : bay.toUpperCase();
    return `
      <div class="batch-config-row">
        <span>${escapeHtml(displayLabel)}</span>
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

  // Load global verification policy to default the per-drive dropdown
  let defaultVerificationMode = "sampled";
  let secondaryVerificationDisabled = false;
  try {
    const response = await safeFetch("/api/admin/policy");
    if (response.ok) {
      const policy = await response.json();
      const effectiveMode = policy.secondary_verification_mode || policy.crypto_verification_mode;
      if (effectiveMode === "disabled") {
        secondaryVerificationDisabled = true;
      } else if (effectiveMode === "full_verify") {
        defaultVerificationMode = "full";
      }
    }
  } catch (e) {
    console.error("Failed to load policy for verification default:", e);
  }

  const perDriveVerificationMode = document.getElementById("perDriveVerificationMode");
  if (perDriveVerificationMode) {
    perDriveVerificationMode.value = defaultVerificationMode;
    perDriveVerificationMode.disabled = secondaryVerificationDisabled;
  }

  const secondaryVerificationDisabledNote = document.getElementById("secondaryVerificationDisabledNote");
  if (secondaryVerificationDisabledNote) {
    secondaryVerificationDisabledNote.classList.toggle("hidden", !secondaryVerificationDisabled);
  }
  
  const count = selectedBays.size;
  let hintText;
  if (count === 1) {
    const bay = Array.from(selectedBays)[0];
    const drive = currentDrives.find(d => d.bay === bay);
    const displayLabel = drive?.display_number ? `BAY ${drive.display_number}` : bay.toUpperCase();
    hintText = `Type "erase ${displayLabel}" to confirm:`;
  } else {
    hintText = `Type "erase ${count} drives" to confirm:`;
  }
  dynamicConfirmationHint.textContent = hintText;
  confirmationText.value = "";

  // Informational warning for drives that appear zeroed
  const zeroedBays = [];
  for (const bay of selectedBays) {
    const drive = currentDrives.find(d => d.bay === bay);
    if (drive?.zero_check?.status === "completed" && drive.zero_check.result === "zeroed") {
      const label = drive.display_number ? `BAY ${drive.display_number}` : bay.toUpperCase();
      zeroedBays.push(label);
    }
  }
  if (zeroCheckWarning && zeroCheckWarningList) {
    const hasZeroed = zeroedBays.length > 0;
    zeroCheckWarning.classList.toggle("hidden", !hasZeroed);
    zeroCheckWarningList.textContent = hasZeroed ? zeroedBays.join(", ") : "";
  }
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

  // Check if secure mode is enabled
  const securityBadge = document.getElementById("securityBadge");
  const isSecureMode = securityBadge && securityBadge.classList.contains("secure");

  if (!tech || !ticket) {
    if (isSecureMode) {
      // In secure mode, require actual values - no defaults allowed
      let missingInfo = [];
      if (!tech) missingInfo.push("Technician Name");
      if (!ticket) missingInfo.push("Ticket Number");
      alert(`Secure Mode requires valid audit information.\n\nPlease provide:\n- ${missingInfo.join("\n- ")}`);
      return;
    } else {
      // In unsecured mode, allow defaults with confirmation
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
  }

  const perDriveVerificationMode = document.getElementById("perDriveVerificationMode");
  const payload = {
    technician: tech,
    ticket_number: ticket,
    bays: Array.from(selectedBays),
    confirmation_text: confirmTextVal,
    methods: {},
    disable_marker: !document.getElementById("writeMarkerCheckbox").checked,
    full_verification: perDriveVerificationMode ? perDriveVerificationMode.value === "full" : false
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
    let result;
    try {
      result = await response.json();
    } catch (e) {
      console.error("Failed to parse batch erase response JSON:", e);
      alert("Failed to process server response");
      return;
    }
    if (!response.ok) {
      const error = result.error || "Unknown Error";
      
      // Check if error is from health gate
      if (error.includes("pre_wipe_health_check_failed")) {
        // Parse health gate details if available
        const isOverrideAvailable = error.includes("override_available");
        const blockReason = error.split(":")[1]?.trim() || "Unknown health issue";
        
        // Show health gate warning modal
        showHealthGateWarning(blockReason, isOverrideAvailable, payload);
        return;
      }
      
      alert(`Wipe Rejected: ${error}`);
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

refreshButton.addEventListener("click", () => loadDrives(false, true));

// Health gate warning modal handlers
function showHealthGateWarning(blockReason, isOverrideAvailable, payload) {
  const reasonText = blockReason.replace(/_/g, " ").replace(/\b\w/g, l => l.toUpperCase());
  
  // Clear existing content
  healthGateWarningContent.innerHTML = "";
  
  // Create warning box
  const warningBox = document.createElement("div");
  warningBox.style.padding = "12px";
  warningBox.style.background = "var(--color-surface-2)";
  warningBox.style.borderRadius = "4px";
  warningBox.style.marginBottom = "12px";
  
  const warningTitle = document.createElement("div");
  warningTitle.style.color = "var(--color-warning)";
  warningTitle.style.fontWeight = "bold";
  warningTitle.style.marginBottom = "8px";
  warningTitle.textContent = "⚠️ Health Check Blocked";
  
  const reasonDiv = document.createElement("div");
  reasonDiv.style.fontSize = "0.9rem";
  const reasonLabel = document.createElement("strong");
  reasonLabel.textContent = "Reason: ";
  const reasonSpan = document.createElement("span");
  reasonSpan.textContent = reasonText;
  reasonDiv.appendChild(reasonLabel);
  reasonDiv.appendChild(reasonSpan);
  
  warningBox.appendChild(warningTitle);
  warningBox.appendChild(reasonDiv);
  
  // Create description
  const descriptionDiv = document.createElement("div");
  descriptionDiv.style.fontSize = "0.85rem";
  descriptionDiv.style.color = "var(--color-text-muted)";
  descriptionDiv.textContent = "The pre-wipe health gate detected a critical health issue that may cause the wipe to fail or waste time.";
  
  healthGateWarningContent.appendChild(warningBox);
  healthGateWarningContent.appendChild(descriptionDiv);
  
  if (isOverrideAvailable) {
    healthGateOverrideSection.classList.remove("hidden");
    healthGateOverrideJustification.value = "";
  } else {
    healthGateOverrideSection.classList.add("hidden");
  }
  
  pendingHealthGatePayload = payload;
  openModal(healthGateWarningModal);
}

// Health gate modal event handlers
if (healthGateOverrideBtn) {
  healthGateOverrideBtn.addEventListener("click", async () => {
    const justification = healthGateOverrideJustification.value.trim();
    if (!justification) {
      alert("Please provide a justification for overriding the health check.");
      return;
    }
    
    // Add justification to payload
    if (pendingHealthGatePayload) {
      pendingHealthGatePayload.health_gate_override_justification = justification;
    }
    
    closeModal(healthGateWarningModal);
    
    // Retry the wipe request with override flag
    try {
      const response = await safeFetch("/api/erase/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...pendingHealthGatePayload, health_gate_override: true })
      });
      let result;
      try {
        result = await response.json();
      } catch (e) {
        console.error("Failed to parse batch erase response JSON:", e);
        alert("Failed to process server response");
        return;
      }
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
      
      alert("Sanitization batch successfully initiated with health gate override.");
      
      loadDrives();
      loadHistoryIndex();
    } catch (err) {
      alert(`Failed to launch batch process: ${err.message}`);
    }
  });
}

if (healthGateCancelBtn || healthGateWarningClose) {
  const cancelHandler = () => {
    closeModal(healthGateWarningModal);
    pendingHealthGatePayload = null;
  };
  if (healthGateCancelBtn) healthGateCancelBtn.addEventListener("click", cancelHandler);
  if (healthGateWarningClose) healthGateWarningClose.addEventListener("click", cancelHandler);
}

// --- END OF FILE frontend/driveManagement.js ---
