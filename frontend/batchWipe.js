// Batch wipe and health gate functions — extracted from driveManagement.js
// All functions are global (shared script scope). Depends on globals from
// driveManagement.js (baysGrid, batchSelectToggleBtn, batchActionFooter,
// selectedCountLabel, openBatchWipeModalBtn, batchWipeModal, batchEraseForm,
// selectedDrivesConfigList, dynamicConfirmationHint, confirmationText,
// zeroCheckWarning, zeroCheckWarningList, healthGateWarningModal,
// healthGateWarningContent, healthGateOverrideSection,
// healthGateOverrideJustification, healthGateOverrideBtn,
// healthGateCancelBtn, healthGateWarningClose),
// utils.js (escapeHtml, safeFetch, openModal, closeModal),
// app.js (selectedBays, isBatchMode, currentDrives, loadDrives, loadHistoryIndex),
// and driveRendering.js (renderBays).

let pendingHealthGatePayload = null;

function toggleBaySelection(bay) {
  if (selectedBays.has(bay)) {
    selectedBays.delete(bay);
  } else {
    selectedBays.add(bay);
  }
  selectedCountLabel.textContent = `${selectedBays.size} Bay(s) Staged`;
  batchActionFooter.classList.toggle("hidden", selectedBays.size === 0);
  const card = baysGrid.querySelector(`article[data-bay="${CSS.escape(bay)}"]`);
  if (card) {
    card.classList.toggle("selected", selectedBays.has(bay));
    const checkbox = card.querySelector(".card-checkbox");
    if (checkbox) checkbox.checked = selectedBays.has(bay);
  }
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
        <small class="batch-config-drive-info">
          ${escapeHtml(drive?.model || "Generic")} (S/N: ${escapeHtml(drive?.serial || "-")})
        </small>
        <select class="batch-drive-method-select" data-bay="${escapeHtml(bay)}">
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
      
      // Check if error is from health gate (structured response)
      if (result.error_code === "pre_wipe_health_check_failed") {
        const blockReason = result.block_reason || "Unknown health issue";
        const isOverrideAvailable = result.override_available === true;
        
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

const healthGateBackdrop = healthGateWarningModal.querySelector(".modal-backdrop");
if (healthGateBackdrop) {
  healthGateBackdrop.addEventListener("click", () => {
    pendingHealthGatePayload = null;
  });
}
