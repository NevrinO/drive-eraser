// Drive rendering functions — extracted from driveManagement.js
// All functions are global (shared script scope). Depends on globals from
// driveManagement.js (baysGrid, workbenchEnclosures, isBayUnconfigured),
// utils.js (escapeHtml), app.js (selectedBays, isBatchMode, calculateDriveHealthScore),
// and bayMapping.js (localBayMapCopy, localLayoutMetadata, availableLayoutTemplates).

// Apply dynamic styles via DOM API (CSP-safe) after innerHTML insertion
function applyDynamicStyles(container) {
  container.querySelectorAll('[data-width]').forEach(el => {
    el.style.width = el.getAttribute('data-width') + '%';
  });
  container.querySelectorAll('[data-grid-cols]').forEach(el => {
    el.style.gridTemplateColumns = `repeat(${el.getAttribute('data-grid-cols')}, minmax(0, 1fr))`;
  });
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

function _blockedCardHtml(row, col) {
  return `
    <article class="bay-card blocked" data-bay="blocked-${row}-${col}">
      <div class="bay-banner bay-banner--blocked"></div>
      <div class="bay-header-row">
        <div class="bay-number bay-number--blocked"></div>
      </div>
    </article>
  `;
}

function _emptyCardHtml(row, col) {
  return `
    <article class="bay-card empty" data-bay="empty-${row}-${col}">
      <div class="bay-banner">EMPTY BAY</div>
      <div class="bay-header-row">
        <div class="bay-number">— Empty slot —</div>
      </div>
    </article>
  `;
}

function _getLocalTemplate() {
  if (localLayoutMetadata.template_id && availableLayoutTemplates.length > 0) {
    return availableLayoutTemplates.find(t => t.id === localLayoutMetadata.template_id) || null;
  }
  return null;
}

function _buildPositionMap(items, getKey, getValue) {
  const map = new Map();
  items.forEach(item => {
    const pos = getKey(item);
    if (pos && Number.isInteger(pos.row) && Number.isInteger(pos.col)) {
      map.set(`${pos.row},${pos.col}`, getValue(item));
    }
  });
  return map;
}

function _sortByPhysicalPosition(a, b, fallbackFn) {
  const aPos = a.physical_position || {};
  const bPos = b.physical_position || {};
  const hasAPos = Number.isInteger(aPos.row) && Number.isInteger(aPos.col);
  const hasBPos = Number.isInteger(bPos.row) && Number.isInteger(bPos.col);
  if (hasAPos && hasBPos) {
    if (aPos.row !== bPos.row) return aPos.row - bPos.row;
    if (aPos.col !== bPos.col) return aPos.col - bPos.col;
  }
  return fallbackFn(a, b);
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
    const bayByPosition = _buildPositionMap(
      enclosureBays,
      ([, conf]) => conf.physical_position,
      ([bayId, conf]) => [bayId, conf]
    );

    gridHtml += `
      <div class="enclosure-section" data-enclosure-id="${escapeHtml(enclosureId)}">
        <div class="enclosure-section-header">
          <h3 class="enclosure-section-title enclosure-section-title--primary">${escapeHtml(enclosure.name || enclosureId)}</h3>
          <small class="enclosure-section-count">${enclosureBays.length} slots</small>
        </div>
        <div class="enclosure-bays-grid" data-grid-cols="${templateCols}">
    `;

    for (let row = 0; row < templateRows; row++) {
      for (let col = 0; col < templateCols; col++) {
        const posKey = `${row},${col}`;
        if (skipSet.has(posKey)) {
          gridHtml += _blockedCardHtml(row, col);
        } else {
          const entry = bayByPosition.get(posKey);
          if (entry) {
            gridHtml += _skeletonCardHtml(entry[0], entry[1]);
          } else {
            gridHtml += _emptyCardHtml(row, col);
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
          <h3 class="enclosure-section-title enclosure-section-title--warning">Unassigned Drives</h3>
          <small class="enclosure-section-count">${unassigned.length} drives</small>
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
  applyDynamicStyles(baysGrid);
}

function _renderSkeletonLegacy(bayEntries) {
  const template = _getLocalTemplate();
  const skipPositions = (template && template.skip_positions) || [];
  const skipSet = new Set(skipPositions.map(p => `${p.row},${p.col}`));

  let templateRows = 1;
  let templateCols = 4;
  if (template) {
    templateRows = template.rows || 1;
    templateCols = template.cols || 4;
  } else {
    const bayCount = bayEntries.length;
    if (bayCount <= 4) templateCols = 4;
    else if (bayCount <= 8) templateCols = 4;
    else if (bayCount <= 10) templateCols = 5;
    else templateCols = 4;
  }

  baysGrid.style.gridTemplateColumns = `repeat(${templateCols}, minmax(0, 1fr))`;

  const bayByPosition = _buildPositionMap(
    bayEntries,
    ([, conf]) => conf.physical_position,
    ([bayId, conf]) => [bayId, conf]
  );

  let gridHtml = "";
  for (let row = 0; row < templateRows; row++) {
    for (let col = 0; col < templateCols; col++) {
      const posKey = `${row},${col}`;
      if (skipSet.has(posKey)) {
        gridHtml += _blockedCardHtml(row, col);
      } else {
        const entry = bayByPosition.get(posKey);
        if (entry) {
          gridHtml += _skeletonCardHtml(entry[0], entry[1]);
        } else {
          gridHtml += _emptyCardHtml(row, col);
        }
      }
    }
  }

  baysGrid.innerHTML = gridHtml;
  applyDynamicStyles(baysGrid);
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
    const driveByPosition = _buildPositionMap(
      enclosureDrives,
      d => d.physical_position,
      d => d
    );

    // Also collect drives with invalid positions
    enclosureDrives.forEach(drive => {
      const pos = drive.physical_position;
      if (!(pos && Number.isInteger(pos.row) && Number.isInteger(pos.col))) {
        allDrivesWithInvalidPositions.push(drive);
      }
    });

    gridHtml += `
      <div class="enclosure-section" data-enclosure-id="${escapeHtml(enclosureId)}">
        <div class="enclosure-section-header">
          <h3 class="enclosure-section-title enclosure-section-title--primary">${escapeHtml(enclosure.name || enclosureId)}</h3>
          <small class="enclosure-section-count">${enclosureDrives.length} slots</small>
        </div>
        <div class="enclosure-bays-grid" data-grid-cols="${templateCols}">
    `;

    // Generate grid cells based on template dimensions
    for (let row = 0; row < templateRows; row++) {
      for (let col = 0; col < templateCols; col++) {
        const posKey = `${row},${col}`;
        const isSkipped = skipSet.has(posKey);
        const drive = driveByPosition.get(posKey);

        if (isSkipped) {
          gridHtml += _blockedCardHtml(row, col);
        } else if (drive) {
          gridHtml += renderBayCard(drive);
        } else {
          gridHtml += _emptyCardHtml(row, col);
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
          <h3 class="enclosure-section-title enclosure-section-title--warning">Unassigned Drives</h3>
          <small class="enclosure-section-count">${unassignedDrives.length} drives</small>
        </div>
        <div class="enclosure-bays-grid">
    `;

    // Sort unassigned drives by physical position (row, col) to follow traversal pattern
    unassignedDrives.sort((a, b) => _sortByPhysicalPosition(a, b, (a, b) => {
      const slotA = a.physical_slot_number || 0;
      const slotB = b.physical_slot_number || 0;
      return slotA - slotB;
    }));

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
            <h3 class="enclosure-section-title enclosure-section-title--warning">Drives with Invalid Positions</h3>
            <small class="enclosure-section-count">${filteredDrives.length} drives</small>
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
  applyDynamicStyles(baysGrid);
}

function renderBaysLegacy(drives) {
  // Get skip positions from template if available
  const template = _getLocalTemplate();
  const skipPositions = (template && template.skip_positions) || [];
  const skipSet = new Set(skipPositions.map(p => `${p.row},${p.col}`));

  const orderedDrives = [...drives].sort((a, b) => _sortByPhysicalPosition(a, b, (a, b) => {
    const aNum = parseInt(String(a.display_number || a.bay).replace(/\D/g, ""), 10) || 0;
    const bNum = parseInt(String(b.display_number || b.bay).replace(/\D/g, ""), 10) || 0;
    return aNum - bNum;
  }));

  // Determine grid columns based on template or default to 4
  let gridCols = 4;
  if (template && template.cols) {
    gridCols = template.cols;
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
  if (template) {
    templateRows = template.rows || 1;
    templateCols = template.cols || gridCols;
  }

  // Create a map of drives by their physical position
  const driveByPosition = _buildPositionMap(
    orderedDrives,
    d => d.physical_position,
    d => d
  );

  // Generate grid cells
  let gridHtml = "";
  for (let row = 0; row < templateRows; row++) {
    for (let col = 0; col < templateCols; col++) {
      const posKey = `${row},${col}`;
      const isSkipped = skipSet.has(posKey);
      const drive = driveByPosition.get(posKey);

      if (isSkipped) {
        gridHtml += _blockedCardHtml(row, col);
      } else if (drive) {
        gridHtml += renderBayCard(drive);
      } else {
        gridHtml += _emptyCardHtml(row, col);
      }
    }
  }

  baysGrid.innerHTML = gridHtml;
  applyDynamicStyles(baysGrid);
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
  const driveStatus = (drive.status || "READY").toUpperCase();
  const isCritical = driveStatus === "FAILED";
  const isRunning = driveStatus === "RUNNING";
  const isCompletedSecure = drive.marker && drive.marker.status === "pristine_secure";
  const isCompletedInsecure = drive.marker && drive.marker.status === "pristine_insecure";
  const isMarkerError = drive.marker && drive.marker.status === "marker_error";
  const isWrittenSinceWipe = drive.marker && drive.marker.status === "written_since_wipe";
  const isMarkerDisabled = drive.marker && (drive.marker.status === "disabled_per_request" || drive.marker.status === "disabled_by_policy");
  const isUnconfigured = isBayUnconfigured(drive);
  const isSmartTestRunning = drive.smart_test_status === "running" || drive.smart_test_status === "in_progress";
  const isCompleted = isCompletedSecure || isCompletedInsecure || isMarkerError;
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
  } else if (isCompletedSecure) {
    stateClass = "completed";
    bannerLabel = "SANITIZED (MARKER AUTHENTICATED)";
  } else if (isCompletedInsecure) {
    stateClass = "completed";
    bannerLabel = "SANITIZED (MARKER UNAUTHENTICATED)";
  } else if (isMarkerError) {
    stateClass = "completed";
    bannerLabel = "SANITIZED (MARKER ERROR)";
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

  const progressPercent = typeof drive.progress_percent === 'number' && isFinite(drive.progress_percent) ? drive.progress_percent : 0.0;
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
      <input type="checkbox" class="card-checkbox ${isBatchMode && isReady ? 'card-checkbox--visible' : ''}" data-checkbox-bay="${escapeHtml(drive.bay)}" ${selectedBays.has(drive.bay) ? "checked" : ""}>
      <div class="bay-banner">${escapeHtml(bannerLabel)}</div>
      ${subBannerHtml}
      <div class="bay-header-row">
        <div class="bay-number">
          ${escapeHtml(bayPrimaryText)}
        </div>
        ${isEmpty ? "" : `
          <div class="bay-card-badges">
            <div class="drive-type-badge ${badgeClass}">${escapeHtml(ifaceLabel)}</div>
            ${driveTypeLabel ? `<div class="drive-type-badge ${driveTypeClass} drive-type-badge--xs">${escapeHtml(driveTypeLabel)}</div>` : ""}
            ${isUnconfigured ? `<div class="unconfigured-badge" title="This bay has no device path configured in bay_map.json">⚠️ Unconfigured</div>` : ""}
          </div>
        `}
      </div>
      ${isEmpty ? `<div class="empty-label">${isUnconfigured ? "— UNCONFIGURED —" : "— Empty slot —"}</div>` : `
        <div class="drive-serial">S/N: ${escapeHtml(drive.serial || "-")}</div>
        <div class="drive-model">${escapeHtml(drive.model || "Generic Drive")}</div>

        ${isRunning ? `
          <div class="health-label">
            <span class="health-label-running">${escapeHtml(phaseLabel)}</span>
            <span class="health-label-running">${progressPercent}%</span>
          </div>
          <div class="health-bar-track">
            <div class="health-bar-fill fill-blue" data-width="${progressPercent}"></div>
          </div>
        ` : drive.smart && drive.smart.smart_polling ? `
          <div class="health-label">
            <span class="health-label-loading">Loading SMART...</span>
            <span class="health-label-loading">⏳</span>
          </div>
          <div class="health-bar-track">
            <div class="health-bar-fill fill-gray health-bar-fill--full"></div>
          </div>
        ` : drive.health_score == null && drive.smart && String(drive.smart.status).toUpperCase() === "UNKNOWN" ? `
          <div class="health-label">
            <span class="health-label-na">Life Expectancy</span>
            <span class="health-label-na">N/A</span>
          </div>
          <div class="health-bar-track">
            <div class="health-bar-fill fill-gray health-bar-fill--full"></div>
          </div>
        ` : healthScore === null ? `
          <div class="health-label">
            <span class="health-label-na">Life Expectancy</span>
            <span class="health-label-na">Calculating...</span>
          </div>
          <div class="health-bar-track">
            <div class="health-bar-fill fill-gray health-bar-fill--full"></div>
          </div>
        ` : `
          <div class="health-label">
            <span>Life Expectancy</span>
            <span>${healthScore}%</span>
          </div>
          <div class="health-bar-track">
            <div class="health-bar-fill ${healthScore > 75 ? 'fill-green' : healthScore > 40 ? 'fill-yellow' : 'fill-red'}" data-width="${healthScore}"></div>
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
