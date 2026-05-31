// --- START OF FILE frontend/driveManagement.js ---
// Drive discovery, rendering, and batch operations

// These elements are defined in the main app.js file
const baysGrid = document.getElementById("baysGrid");
const refreshButton = document.getElementById("refreshButton");
const apiStatus = document.getElementById("apiStatus");
const lastUpdated = document.getElementById("lastUpdated");

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

const POLL_INTERVAL_MS = 2000;

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

function renderBays(drives) {
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

  baysGrid.innerHTML = orderedDrives.map((drive) => {
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

    const bayPrimaryText = (drive.display_number ? `BAY ${drive.display_number}` : drive.bay.toUpperCase());
    const baySecondaryText = (drive.display_number ? drive.bay.toUpperCase() : "");
    const displayLabel = drive.label && drive.label !== drive.bay ? ` (${drive.label})` : "";

    return `
      <article class="${classes.join(" ")}" data-bay="${escapeHtml(drive.bay)}">
        <input type="checkbox" class="card-checkbox" data-checkbox-bay="${escapeHtml(drive.bay)}" ${selectedBays.has(drive.bay) ? "checked" : ""} ${isBatchMode && isReady ? 'style="display: block;"' : ""}>
        <div class="bay-banner">${escapeHtml(bannerLabel)}</div>
        <div class="bay-header-row">
          <div class="bay-number">
            ${escapeHtml(bayPrimaryText)}
            <span style="font-size: 0.72rem; font-weight: normal; opacity: 0.7;">${escapeHtml(baySecondaryText ? `${baySecondaryText}${displayLabel}` : displayLabel)}</span>
          </div>
          ${isEmpty ? "" : `<div class="drive-type-badge ${badgeClass}">${escapeHtml(ifaceLabel)}</div>`}
        </div>
        ${isEmpty ? `<div class="empty-label">— Empty slot —</div>` : `
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
    
    alert("Sanitization batch successfully initiated.");
    
    loadDrives();
    loadHistoryIndex();
  } catch (err) {
    alert(`Failed to launch batch process: ${err.message}`);
  }
});

refreshButton.addEventListener("click", () => loadDrives(false));
// --- END OF FILE frontend/driveManagement.js ---
