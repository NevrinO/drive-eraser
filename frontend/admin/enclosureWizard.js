// Enclosure wizard: wizard state, step rendering, configuration, slot assignment
// Load order: enclosureList.js -> enclosureWizard.js -> enclosureSave.js

// Lazily load templates and master slot map (needed for wizard rendering)
// These involve a slow sysfs scan so we defer them until the wizard is opened
let _wizardDataLoaded = false;
let _wizardDataPromise = null;

async function ensureWizardDataLoaded() {
  if (_wizardDataLoaded) return;
  if (_wizardDataPromise) return _wizardDataPromise;
  _wizardDataPromise = (async () => {
    try {
      await Promise.all([loadTemplates(), loadMasterSlotMap()]);
      _wizardDataLoaded = true;
    } finally {
      _wizardDataPromise = null;
    }
  })();
  return _wizardDataPromise;
}

// Open new enclosure wizard
async function openNewEnclosureWizard() {
  const modal = document.getElementById("enclosureWizardModal");
  if (!modal) return;

  // Ensure save button listener is attached (modal may not exist at module load)
  const saveBtn = document.getElementById("wizardSaveBtn");
  if (saveBtn && !saveBtn.dataset.enclosureListener) {
    saveBtn.addEventListener("click", () => {
      if (isEditMode) {
        handleEditEnclosure();
      } else {
        handleSaveEnclosure();
      }
    });
    saveBtn.dataset.enclosureListener = "true";
  }

  // Reset wizard to step 1
  currentWizardStep = 1;
  isEditMode = false;
  editEnclosureId = null;
  wizardData = {
    name: "",
    template_id: "",
    pci_controller: "",
    expander_sas_address: null,
    display_order: 0,
    nvme_starting_slot: null,
    starting_slot_number: null
  };

  // Update modal title
  const modalTitle = modal.querySelector(".modal-title");
  if (modalTitle) {
    modalTitle.textContent = "Add New Enclosure";
  }

  // Load wizard data lazily (templates + master slot map needed for rendering)
  await ensureWizardDataLoaded();

  await renderWizardStep();
  openModal(modal);
}

// Wizard state
let currentWizardStep = 1;
let isEditMode = false;
let editEnclosureId = null;
let wizardData = {
  name: "",
  template_id: "",
  pci_controller: "",
  expander_sas_address: null,
  display_order: 0,
  nvme_starting_slot: null,
  starting_slot_number: null
};

// Render current wizard step
async function renderWizardStep() {
  const step1 = document.getElementById("wizardStep1");
  const step2 = document.getElementById("wizardStep2");
  const prevBtn = document.getElementById("wizardPrevBtn");
  const nextBtn = document.getElementById("wizardNextBtn");
  const saveBtn = document.getElementById("wizardSaveBtn");

  if (!step1 || !step2 || !prevBtn || !nextBtn || !saveBtn) {
    console.error("renderWizardStep: required wizard DOM elements not found");
    return;
  }

  step1.classList.add("hidden");
  step2.classList.add("hidden");
  prevBtn.classList.add("hidden");
  nextBtn.classList.add("hidden");
  saveBtn.classList.add("hidden");

  if (currentWizardStep === 1) {
    step1.classList.remove("hidden");
    nextBtn.classList.remove("hidden");
    nextBtn.textContent = "Next Step >";
    await renderConfiguration();
  } else if (currentWizardStep === 2) {
    step2.classList.remove("hidden");
    prevBtn.classList.remove("hidden");
    saveBtn.classList.remove("hidden");
    renderSlotAssignment();
  }
}

// Render configuration step (combines controller and template selection)
async function renderConfiguration() {
  const container = document.getElementById("configurationContainer");
  if (!container) return;

  // Load hardware enclosure info for human-readable controller names
  let hardwareInfo = [];
  try {
    const response = await safeFetch(`/api/admin/hardware-enclosure-info?_t=${Date.now()}`);
    if (response.ok) {
      const data = await response.json();
      hardwareInfo = data.hardware_info || [];
    }
  } catch (e) {
    console.error("Failed to load hardware enclosure info:", e);
  }

  // Group master slot map by PCI controller (exclude PCIe NVMe slots from controller selection)
  const controllerGroups = {};
  masterSlotMap.forEach(entry => {
    // Skip PCIe NVMe slots - they're not SAS controllers
    if (entry.slot_type === 'pcie_nvme') return;
    
    const key = entry.pci_controller;
    if (!controllerGroups[key]) {
      controllerGroups[key] = {
        pci_controller: key,
        slot_type: entry.slot_type,
        expanders: new Set()
      };
    }
    if (entry.expander_sas_address) {
      controllerGroups[key].expanders.add(entry.expander_sas_address);
    }
  });

  let html = `
    <div class="form-group">
      <label>Enclosure Name</label>
      <input type="text" id="wizardEnclosureName" value="${escapeHtml(wizardData.name)}" placeholder="e.g., Front Bay Array">
    </div>
    <div class="form-group">
      <label>Enclosure Display Order</label>
      <input type="number" id="wizardDisplayOrder" value="${wizardData.display_order}" min="0">
    </div>
    <div class="form-group">
      <label>Select Physical Controller / Connection</label>
  `;

  Object.values(controllerGroups).forEach(group => {
    const expanders = Array.from(group.expanders);
    // Find hardware info for this controller
    const hwInfo = hardwareInfo.find(h => h.pci_controller === group.pci_controller);
    const controllerLabel = hwInfo && hwInfo.vendor && hwInfo.model
      ? `${hwInfo.vendor} ${hwInfo.model}`
      : group.pci_controller;
    const occupiedBadge = hwInfo ? ` <span class="wizard-badge-occupied">${hwInfo.occupied_slots} drives</span>` : '';

    // Use hardware_info total_slots (master slot map doesn't exist until after enclosures are configured)
    const totalSlots = hwInfo && hwInfo.total_slots ? hwInfo.total_slots : 0;
    const totalBadge = totalSlots > 0 ? ` <span class="wizard-badge-total">${totalSlots} slots</span>` : '';

    if (expanders.length > 0) {
      expanders.forEach(expander => {
        const isSelected = wizardData.pci_controller === group.pci_controller && wizardData.expander_sas_address === expander;
        const disabled = isEditMode ? 'disabled' : '';
        html += `
          <label class="radio-option">
            <input type="radio" name="controller" value="${escapeHtml(group.pci_controller)}" data-expander="${escapeHtml(expander)}" ${isSelected ? 'checked' : ''} ${disabled}>
            <span>${escapeHtml(controllerLabel)} — SAS Expander (${escapeHtml(expander)})${occupiedBadge}${totalBadge}${isEditMode ? ' <em>(cannot change in edit mode)</em>' : ''}</span>
          </label>
        `;
      });
    } else {
      const isSelected = wizardData.pci_controller === group.pci_controller && !wizardData.expander_sas_address;
      const disabled = isEditMode ? 'disabled' : '';
      html += `
        <label class="radio-option">
          <input type="radio" name="controller" value="${escapeHtml(group.pci_controller)}" data-expander="" ${isSelected ? 'checked' : ''} ${disabled}>
          <span>${escapeHtml(controllerLabel)} — Direct-attached (${group.slot_type})${occupiedBadge}${totalBadge}${isEditMode ? ' <em>(cannot change in edit mode)</em>' : ''}</span>
        </label>
      `;
    }
  });

  html += `</div>`;

  // Add hint about controller identification
  html += `
    <div class="wizard-hint-box">
      <p class="wizard-hint-text">
        <strong>Tip:</strong> To identify which controller corresponds to your physical enclosure, insert a test drive into a bay and check which controller shows an increase in the "drives" count above.
      </p>
      <button type="button" id="refreshHardwareInfo" class="wizard-refresh-btn">Refresh Drive Counts</button>
    </div>
  `;

  html += `
    <div class="form-group">
      <label>Layout Template${isEditMode ? ' <em>(cannot change in edit mode)</em>' : ''}</label>
      <select id="wizardTemplateSelect" ${isEditMode ? 'disabled' : ''}>
        <option value="">-- Select Template --</option>
  `;

  availableTemplates.forEach(tpl => {
    const selected = wizardData.template_id === tpl.id ? 'selected' : '';
    html += `<option value="${escapeHtml(tpl.id)}" ${selected}>${escapeHtml(tpl.name)} (${tpl.vendor || 'Generic'})</option>`;
  });

  html += `</select></div>`;

  // Check if selected template has hybrid slots
  const selectedTemplate = availableTemplates.find(t => t.id === wizardData.template_id);
  if (selectedTemplate && selectedTemplate.hybrid_slots && selectedTemplate.hybrid_slots.length > 0) {
    html += `
      <div class="form-group wizard-hybrid-section">
        <label class="wizard-hybrid-label">Hybrid Slot Configuration</label>
        <p class="wizard-hybrid-desc">
          This template contains ${selectedTemplate.hybrid_slots.length} Hybrid slots (Slots ${selectedTemplate.hybrid_slots.join(', ')}).
          Select starting PCIe NVMe Slot folder to auto-populate hardware paths:
        </p>
        <label>Starting NVMe Port</label>
        <select id="wizardNvmeStartSlot">
          <option value="">-- Select Starting Slot --</option>
    `;

    // Get available NVMe slot folders from master map
    const nvmeSlots = masterSlotMap
      .filter(entry => entry.slot_type === 'pcie_nvme')
      .map(entry => entry.hardware_identifier)
      .sort((a, b) => parseInt(a, 10) - parseInt(b, 10));

    if (nvmeSlots.length === 0) {
      // Fallback: try to get NVMe drives from unmapped drives API
      try {
        const unmappedResponse = await safeFetch("/api/admin/unmapped-drives");
        if (unmappedResponse.ok) {
          const unmappedData = await unmappedResponse.json();
          const nvmeDrives = unmappedData.filter(d => d.by_path && d.by_path.includes("nvme"));
          
          if (nvmeDrives.length > 0) {
            // Use NVMe device paths as fallback identifiers
            nvmeDrives.forEach(drive => {
              const selected = wizardData.nvme_starting_slot === drive.by_path ? 'selected' : '';
              html += `<option value="${escapeHtml(drive.by_path)}" ${selected}>${escapeHtml(drive.by_path)} [${drive.model}]</option>`;
            });
            html += `<small class="wizard-form-hint">Using detected NVMe drives (no hot-plug slots found)</small>`;
          } else {
            html += `<option value="" disabled>No NVMe drives detected in system</option>`;
          }
        } else {
          html += `<option value="" disabled>No NVMe slots detected in system</option>`;
        }
      } catch (e) {
        console.error("Failed to load unmapped drives for NVMe fallback:", e);
        html += `<option value="" disabled>No NVMe slots detected in system</option>`;
      }
    } else {
      nvmeSlots.forEach(slot => {
        const selected = wizardData.nvme_starting_slot === slot ? 'selected' : '';
        html += `<option value="${escapeHtml(slot)}" ${selected}>Slot ${escapeHtml(slot)}</option>`;
      });
    }

    html += `
        </select>
        <small class="wizard-form-hint">
          System will auto-map hybrid slots using the starting slot as a base, incrementing by hybrid slot index.
        </small>
      </div>
    `;
  }

  container.innerHTML = html;

  // Bind events
  document.getElementById("wizardEnclosureName").addEventListener("input", (e) => {
    wizardData.name = e.target.value;
  });
  document.getElementById("wizardDisplayOrder").addEventListener("input", (e) => {
    wizardData.display_order = parseInt(e.target.value, 10) || 0;
  });
  container.querySelectorAll("input[name='controller']").forEach(radio => {
    radio.addEventListener("change", (e) => {
      wizardData.pci_controller = e.target.value;
      wizardData.expander_sas_address = e.target.dataset.expander || null;
    });
  });
  document.getElementById("wizardTemplateSelect").addEventListener("change", (e) => {
    wizardData.template_id = e.target.value;
    renderConfiguration().catch(e => console.error("Failed to re-render configuration:", e));
  });

  // Bind refresh button to update drive counts
  const refreshBtn = document.getElementById("refreshHardwareInfo");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", async () => {
      refreshBtn.disabled = true;
      refreshBtn.textContent = "Refreshing...";
      try {
        const response = await safeFetch(`/api/admin/hardware-enclosure-info?_t=${Date.now()}`);
        if (response.ok) {
          const data = await response.json();
          // Update the hardware info in the current render context
          // Re-render to show updated drive counts
          await renderConfiguration();
        }
      } catch (e) {
        console.error("Failed to refresh hardware info:", e);
      } finally {
        refreshBtn.disabled = false;
        refreshBtn.textContent = "Refresh Drive Counts";
      }
    });
  }

  const nvmeSelect = document.getElementById("wizardNvmeStartSlot");
  if (nvmeSelect) {
    nvmeSelect.addEventListener("change", (e) => {
      wizardData.nvme_starting_slot = e.target.value;
    });
  }
}

// Supported traversal presets (mirrors backend SUPPORTED_TRAVERSALS)
const SUPPORTED_TRAVERSALS = [
  "top_left_down_then_across",
  "bottom_left_up_then_across",
  "top_left_across_then_down",
  "bottom_left_across_then_up"
];

// Build traversal positions (mirrors backend build_traversal_positions function)
function buildTraversalPositions(rows, cols, traversal, slotCount) {
  const positions = [];
  const r = Math.max(1, rows || 1);
  const c = Math.max(1, cols || 1);
  // Respect provided slotCount; only fall back to rows * cols if not provided
  const count = (slotCount !== null && slotCount !== undefined && slotCount > 0) ? slotCount : (r * c);

  if (traversal === "bottom_left_up_then_across") {
    for (let col = 0; col < c; col++) {
      for (let row = r - 1; row >= 0; row--) {
        positions.push({ row, col });
        if (positions.length >= count) return positions;
      }
    }
  } else if (traversal === "top_left_across_then_down") {
    for (let row = 0; row < r; row++) {
      for (let col = 0; col < c; col++) {
        positions.push({ row, col });
        if (positions.length >= count) return positions;
      }
    }
  } else if (traversal === "bottom_left_across_then_up") {
    for (let row = r - 1; row >= 0; row--) {
      for (let col = 0; col < c; col++) {
        positions.push({ row, col });
        if (positions.length >= count) return positions;
      }
    }
  } else {
    // top_left_down_then_across (default)
    for (let col = 0; col < c; col++) {
      for (let row = 0; row < r; row++) {
        positions.push({ row, col });
        if (positions.length >= count) return positions;
      }
    }
  }

  return positions;
}

// Render slot validation (Step 3)
// Render slot assignment step with live recalc and editable HW identifiers
function renderSlotAssignment() {
  const container = document.getElementById("slotAssignmentContainer");
  if (!container) return;

  const template = availableTemplates.find(t => t.id === wizardData.template_id);
  if (!template) {
    container.innerHTML = "<p>Please select a template first.</p>";
    return;
  }

  // Initialize starting slot if not set
  if (wizardData.starting_slot_number === null || wizardData.starting_slot_number === undefined) {
    wizardData.starting_slot_number = 0;
  }

  // Build slot mapping with arithmetic HW identifier computation
  const slots = [];
  const startingSlot = parseInt(wizardData.starting_slot_number, 10) || 0;
  const rows = template.rows || 1;
  const cols = template.cols || 1;
  const traversal = template.traversal_preset || "top_left_down_then_across";

  // Build traversal positions
  let positions;
  if (rows > 0 && cols > 0 && SUPPORTED_TRAVERSALS.includes(traversal)) {
    positions = buildTraversalPositions(rows, cols, traversal, template.slot_count);
  } else {
    positions = Array.from({ length: template.slot_count || template.bay_count || (rows * cols) }, (_, i) => ({ row: i, col: 0 }));
  }

  // When editing an existing enclosure, load saved slot data so custom HW IDs are preserved
  let savedSlots = null;
  if (isEditMode && editEnclosureId && adminEnclosures[editEnclosureId]) {
    savedSlots = adminEnclosures[editEnclosureId].slots || null;
  }

  // Collect current label and role values from DOM to preserve user modifications
  // across re-renders (e.g., when starting slot number changes)
  let domLabels = {};
  let domRoles = {};
  const existingTable = container.querySelector('.slot-validation-table');
  if (existingTable) {
    container.querySelectorAll('.slot-label-input').forEach(input => {
      const slotIndex = parseInt(input.dataset.slotIndex, 10);
      if (!isNaN(slotIndex)) {
        domLabels[String(slotIndex)] = input.value;
      }
    });
    container.querySelectorAll('.slot-role-select').forEach(select => {
      const slotIndex = parseInt(select.dataset.slotIndex, 10);
      if (!isNaN(slotIndex)) {
        domRoles[String(slotIndex)] = select.value;
      }
    });
  }

  for (let slotIndex = 0; slotIndex < positions.length; slotIndex++) {
    const { row, col } = positions[slotIndex];
    const isHybrid = template.hybrid_slots && template.hybrid_slots.includes(slotIndex);
    const physicalSlot = startingSlot + slotIndex;

    // Compute HW identifiers arithmetically (mirrors backend logic)
    let sasHwId, sasSlotType;
    if (wizardData.expander_sas_address) {
      sasHwId = `phy-0:0:${physicalSlot}`;
      sasSlotType = "sas_expander";
    } else {
      // Direct SAS (backplane without expander) - default to sas_direct
      // motherboard_sata is only for actual motherboard SATA ports
      sasHwId = `phy-0:0:${physicalSlot}`;
      sasSlotType = "sas_direct";
    }

    // Compute NVMe HW identifier for hybrid slots
    let nvmeHwId = null;
    if (isHybrid && wizardData.nvme_starting_slot) {
      const nvmeStartingSlot = parseInt(wizardData.nvme_starting_slot, 10);
      if (!isNaN(nvmeStartingSlot)) {
        const nvmeOffset = template.hybrid_slots.indexOf(slotIndex);
        const nvmeSlotNum = nvmeStartingSlot + nvmeOffset;
        nvmeHwId = String(nvmeSlotNum);
      } else {
        nvmeHwId = wizardData.nvme_starting_slot;
      }
    }

    const slotKey = String(slotIndex);
    const savedSlot = savedSlots && savedSlots[slotKey] ? savedSlots[slotKey] : null;
    const savedSasMapping = savedSlot && savedSlot.mappings && savedSlot.mappings.sas_sata;
    const savedNvmeMapping = savedSlot && savedSlot.mappings && savedSlot.mappings.nvme;

    slots.push({
      physical_slot_number: physicalSlot,
      label: domLabels[slotKey] !== undefined ? domLabels[slotKey] : (savedSlot ? savedSlot.label : `Bay ${slotIndex}`),
      role: domRoles[slotKey] !== undefined ? domRoles[slotKey] : (savedSlot ? savedSlot.role : (template.default_role || "wipe")),
      locked: domRoles[slotKey] !== undefined ? (domRoles[slotKey] === 'os') : (savedSlot ? savedSlot.locked : false),
      mappings: {
        sas_sata: savedSasMapping ? {
          slot_type: savedSasMapping.slot_type || sasSlotType,
          hardware_identifier: savedSasMapping.hardware_identifier || sasHwId,
          auto_detected: savedSasMapping.auto_detected !== undefined ? savedSasMapping.auto_detected : true
        } : {
          slot_type: sasSlotType,
          hardware_identifier: sasHwId,
          auto_detected: true
        },
        nvme: savedNvmeMapping ? {
          slot_type: savedNvmeMapping.slot_type || "pcie_nvme",
          hardware_identifier: savedNvmeMapping.hardware_identifier || nvmeHwId,
          auto_detected: savedNvmeMapping.auto_detected !== undefined ? savedNvmeMapping.auto_detected : true
        } : (nvmeHwId ? {
          slot_type: "pcie_nvme",
          hardware_identifier: nvmeHwId,
          auto_detected: true
        } : null)
      }
    });
  }

  const nvmeSlotIds = masterSlotMap
    .filter(e => e.slot_type === 'pcie_nvme')
    .map(e => e.hardware_identifier)
    .sort((a, b) => parseInt(a, 10) - parseInt(b, 10));

  let html = `
    <div class="form-group wizard-slot-config-section">
      <label class="wizard-slot-config-label">Starting Slot Number</label>
      <input type="number" id="wizardStartingSlotLive" value="${wizardData.starting_slot_number}" min="0" class="wizard-slot-number-input">
      <small class="wizard-form-hint">Changing this will live-update the slot table below.</small>
    </div>
    <table class="slot-validation-table">
      <thead>
        <tr>
          <th>Slot #</th>
          <th>Label</th>
          <th>Role</th>
          <th>HW Identifier</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
  `;

  slots.forEach((slot, index) => {
    const sasMapping = slot.mappings.sas_sata;
    const nvmeMapping = slot.mappings.nvme;
    const hwId = sasMapping ? sasMapping.hardware_identifier : null;
    const slotType = sasMapping ? sasMapping.slot_type : null;

    // Check if drive is present by matching against master slot map
    let status = '<span class="slot-status--unconfigured">Unconfigured</span>';
    if (hwId && slotType) {
      const masterEntry = masterSlotMap.find(e =>
        e.hardware_identifier === hwId &&
        e.slot_type === slotType &&
        e.pci_controller === wizardData.pci_controller &&
        (wizardData.expander_sas_address ? e.expander_sas_address === wizardData.expander_sas_address : !e.expander_sas_address)
      );
      if (masterEntry) {
        status = '<span class="slot-status--present">Drive Present</span>';
      } else {
        status = '<span class="slot-status--empty">Empty Bay</span>';
      }
    }

    html += `
      <tr>
        <td rowspan="${nvmeMapping ? 2 : 1}"><strong>${slot.physical_slot_number}</strong></td>
        <td rowspan="${nvmeMapping ? 2 : 1}">
          <input type="text" class="slot-label-input wizard-form-input" data-slot-index="${index}" value="${escapeHtml(slot.label)}">
        </td>
        <td rowspan="${nvmeMapping ? 2 : 1}">
          <select class="slot-role-select wizard-form-select" data-slot-index="${index}">
            <option value="wipe" ${slot.role === 'wipe' ? 'selected' : ''}>Wipe</option>
            <option value="os" ${slot.role === 'os' ? 'selected' : ''}>OS Drive</option>
            <option value="reserved" ${slot.role === 'reserved' ? 'selected' : ''}>Reserved</option>
          </select>
        </td>
        <td>
          <input type="text" class="hw-id-input wizard-form-input" data-slot-index="${index}" data-interface="sas_sata" data-slot-type="${sasMapping ? escapeHtml(sasMapping.slot_type) : ''}" value="${sasMapping ? escapeHtml(sasMapping.hardware_identifier) : ''}">
        </td>
        <td>${status}</td>
      </tr>
    `;

    if (nvmeMapping) {
      const nvmeHwId = nvmeMapping.hardware_identifier;
      const nvmeSlotType = nvmeMapping.slot_type;
      let nvmeStatus = '<span class="slot-status--unconfigured">Unconfigured</span>';
      if (nvmeHwId && nvmeSlotType) {
        // Check against master slot map (PCIe hot-plug slots)
        const nvmeMasterEntry = masterSlotMap.find(e =>
          e.hardware_identifier === nvmeHwId &&
          e.slot_type === nvmeSlotType
        );
        
        // If slot exists in master map, show as configured
        // We can't easily determine drive presence without scanning devices
        if (nvmeMasterEntry) {
          nvmeStatus = '<span class="slot-status--configured">Configured</span>';
        } else {
          nvmeStatus = '<span class="slot-status--empty">Unconfigured</span>';
        }
      }

      let nvmeOptions = '<option value="">-- Select NVMe Slot --</option>';
      
      nvmeSlotIds.forEach(slot => {
        const selected = nvmeHwId === slot ? 'selected' : '';
        nvmeOptions += `<option value="${escapeHtml(slot)}" ${selected}>Slot ${escapeHtml(slot)}</option>`;
      });

      html += `
        <tr>
          <td>
            <select class="hw-id-input wizard-form-select" data-slot-index="${index}" data-interface="nvme" data-slot-type="${nvmeMapping ? escapeHtml(nvmeMapping.slot_type) : ''}">
              ${nvmeOptions}
            </select>
          </td>
          <td>${nvmeStatus}</td>
        </tr>
      `;
    }
  });

  html += `</tbody></table>`;
  container.innerHTML = html;

  // Bind starting slot input for live recalc
  // Use 'change' instead of 'input' to prevent re-rendering while typing multi-digit numbers
  document.getElementById("wizardStartingSlotLive").addEventListener("change", (e) => {
    wizardData.starting_slot_number = parseInt(e.target.value, 10) || 0;
    renderSlotAssignment(); // Re-render with new starting slot
  });

  // Bind label input events
  container.querySelectorAll('.slot-label-input').forEach(input => {
    input.addEventListener('input', (e) => {
      const slotIndex = parseInt(e.target.dataset.slotIndex, 10);
      if (slots[slotIndex]) {
        slots[slotIndex].label = e.target.value;
      }
    });
  });

  // Bind role select events
  container.querySelectorAll('.slot-role-select').forEach(select => {
    select.addEventListener('change', (e) => {
      const slotIndex = parseInt(e.target.dataset.slotIndex, 10);
      if (slots[slotIndex]) {
        slots[slotIndex].role = e.target.value;
        slots[slotIndex].locked = e.target.value === 'os';
      }
    });
  });

  // Bind HW ID input events with format validation (for text inputs)
  container.querySelectorAll('.hw-id-input').forEach(input => {
    if (input.tagName === 'INPUT') {
      input.addEventListener('input', (e) => {
        const slotIndex = parseInt(e.target.dataset.slotIndex, 10);
        const interfaceType = e.target.dataset.interface;
        const slotType = e.target.dataset.slotType;
        const value = e.target.value;

        // Validate format based on slot type
        let isValid = true;
        if (value) {
          if (slotType === 'sas_expander') {
            // Should match phy-0:0:N pattern
            isValid = /^phy-0:0:\d+$/.test(value);
          } else if (slotType === 'motherboard_sata') {
            // Should match ataN pattern
            isValid = /^ata\d+$/.test(value);
          } else if (slotType === 'pcie_nvme') {
            // Should be a number (slot folder name)
            isValid = /^\d+$/.test(value);
          }
        }

        // Update visual feedback
        if (value && !isValid) {
          input.style.borderColor = '#e74c3c';
          input.title = `Invalid format for ${slotType}. Expected: ${
            slotType === 'sas_expander' ? 'phy-0:0:N (e.g., phy-0:0:0)' :
            slotType === 'motherboard_sata' ? 'ataN (e.g., ata1)' :
            'number (e.g., 1)'
          }`;
        } else {
          input.style.borderColor = '#444';
          input.title = '';
        }

        if (slots[slotIndex] && slots[slotIndex].mappings[interfaceType]) {
          slots[slotIndex].mappings[interfaceType].hardware_identifier = value;
        }
      });
    } else if (input.tagName === 'SELECT') {
      // For NVMe dropdown, use 'change' event
      input.addEventListener('change', (e) => {
        const slotIndex = parseInt(e.target.dataset.slotIndex, 10);
        const interfaceType = e.target.dataset.interface;
        const value = e.target.value;

        if (slots[slotIndex] && slots[slotIndex].mappings[interfaceType]) {
          slots[slotIndex].mappings[interfaceType].hardware_identifier = value;
        }
      });
    }
  });
}

// Wizard navigation
document.getElementById("wizardNextBtn")?.addEventListener("click", () => {
  if (currentWizardStep === 1) {
    const trimmedName = (wizardData.name || "").trim();
    if (!trimmedName) {
      alert("Please enter an enclosure name.");
      return;
    }
    if (trimmedName.length < 2) {
      alert("Enclosure name must be at least 2 characters.");
      return;
    }
    if (trimmedName.length > 100) {
      alert("Enclosure name must be 100 characters or less.");
      return;
    }
    if (!wizardData.pci_controller) {
      alert("Please select a controller.");
      return;
    }
    if (!wizardData.template_id) {
      alert("Please select a template.");
      return;
    }
  }
  currentWizardStep++;
  renderWizardStep();
});

document.getElementById("wizardPrevBtn")?.addEventListener("click", () => {
  currentWizardStep--;
  renderWizardStep();
});
