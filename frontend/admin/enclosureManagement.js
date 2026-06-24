// --- START OF FILE frontend/admin/enclosureManagement.js ---
// Enclosure management: load, render, create, edit, delete enclosures and slots

let adminEnclosures = {};
let availableTemplates = [];
let masterSlotMap = [];

// Load all enclosures from backend
async function loadEnclosures() {
  try {
    const response = await safeFetch("/api/admin/enclosures");
    if (!response.ok) throw new Error("Failed to load enclosures");
    const data = await response.json();
    adminEnclosures = {};
    (data.enclosures || []).forEach(enc => {
      adminEnclosures[enc.id] = enc;
    });
    return data.enclosures || [];
  } catch (e) {
    console.error("Failed to load enclosures:", e);
    return [];
  }
}

// Load layout templates
async function loadTemplates() {
  try {
    const response = await safeFetch("/api/admin/layout-templates");
    if (!response.ok) throw new Error("Failed to load templates");
    const data = await response.json();
    availableTemplates = data.templates || [];
    return availableTemplates;
  } catch (e) {
    console.error("Failed to load templates:", e);
    return [];
  }
}

// Load master slot map for controller discovery
async function loadMasterSlotMap() {
  try {
    const response = await safeFetch("/api/admin/master-slot-map");
    if (!response.ok) throw new Error("Failed to load master slot map");
    const data = await response.json();
    masterSlotMap = data.master_map || [];
    return masterSlotMap;
  } catch (e) {
    console.error("Failed to load master slot map:", e);
    return [];
  }
}

// Render enclosure list in admin panel
function renderEnclosureList(enclosures) {
  const container = document.getElementById("enclosureList");
  if (!container) return;

  const sortedEnclosures = [...enclosures].sort((a, b) => (a.display_order || 0) - (b.display_order || 0));

  container.innerHTML = sortedEnclosures.map(enc => `
    <div class="enclosure-card" data-enclosure-id="${escapeHtml(enc.id)}">
      <div class="enclosure-header">
        <div class="enclosure-title">
          <strong>${escapeHtml(enc.name || enc.id)}</strong>
          <small style="color: #888;">ID: ${escapeHtml(enc.id)}</small>
        </div>
        <div class="enclosure-actions">
          <button type="button" class="btn btn--secondary btn-sm enclosure-edit-btn" data-enclosure-id="${escapeHtml(enc.id)}">Edit</button>
          <button type="button" class="btn btn--secondary btn-sm enclosure-delete-btn" data-enclosure-id="${escapeHtml(enc.id)}">Delete</button>
        </div>
      </div>
      <div class="enclosure-details">
        <div class="kv"><span>Template:</span><span>${escapeHtml(enc.template_name || 'N/A')}</span></div>
        <div class="kv"><span>PCI Controller:</span><span>${escapeHtml(enc.pci_controller || 'N/A')}</span></div>
        <div class="kv"><span>Expander SAS:</span><span>${escapeHtml(enc.expander_sas_address || 'N/A')}</span></div>
        <div class="kv"><span>Slots:</span><span>${Object.keys(enc.slots || {}).length}</span></div>
      </div>
    </div>
  `).join("");

  // Attach event listeners for edit/delete buttons
  container.querySelectorAll('.enclosure-edit-btn').forEach(btn => {
    btn.addEventListener('click', () => editEnclosure(btn.dataset.enclosureId));
  });
  container.querySelectorAll('.enclosure-delete-btn').forEach(btn => {
    btn.addEventListener('click', () => deleteEnclosure(btn.dataset.enclosureId));
  });
}

// Open new enclosure wizard
function openNewEnclosureWizard() {
  const modal = document.getElementById("enclosureWizardModal");
  if (!modal) return;

  // Ensure save button listener is attached (modal may not exist at module load)
  const saveBtn = document.getElementById("wizardSaveBtn");
  if (saveBtn && !saveBtn.dataset.enclosureListener) {
    saveBtn.addEventListener("click", handleSaveEnclosure);
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

  renderWizardStep();
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
function renderWizardStep() {
  const step1 = document.getElementById("wizardStep1");
  const step2 = document.getElementById("wizardStep2");
  const prevBtn = document.getElementById("wizardPrevBtn");
  const nextBtn = document.getElementById("wizardNextBtn");
  const saveBtn = document.getElementById("wizardSaveBtn");

  step1.classList.add("hidden");
  step2.classList.add("hidden");
  prevBtn.classList.add("hidden");
  nextBtn.classList.add("hidden");
  saveBtn.classList.add("hidden");

  if (currentWizardStep === 1) {
    step1.classList.remove("hidden");
    nextBtn.classList.remove("hidden");
    nextBtn.textContent = "Next Step >";
    renderConfiguration();
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
    const response = await safeFetch("/api/admin/hardware-enclosure-info");
    if (response.ok) {
      const data = await response.json();
      hardwareInfo = data.hardware_info || [];
    }
  } catch (e) {
    console.error("Failed to load hardware enclosure info:", e);
  }

  // Group master slot map by PCI controller
  const controllerGroups = {};
  masterSlotMap.forEach(entry => {
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
      <label>Display Order</label>
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
    const occupiedBadge = hwInfo ? ` <span style="background: #4a90e2; color: white; padding: 2px 6px; border-radius: 3px; font-size: 0.75rem;">${hwInfo.occupied_slots} drives</span>` : '';

    if (expanders.length > 0) {
      expanders.forEach(expander => {
        const isSelected = wizardData.pci_controller === group.pci_controller && wizardData.expander_sas_address === expander;
        const disabled = isEditMode ? 'disabled' : '';
        html += `
          <label class="radio-option">
            <input type="radio" name="controller" value="${escapeHtml(group.pci_controller)}" data-expander="${escapeHtml(expander)}" ${isSelected ? 'checked' : ''} ${disabled}>
            <span>${escapeHtml(controllerLabel)} — SAS Expander (${escapeHtml(expander)})${occupiedBadge}${isEditMode ? ' <em>(cannot change in edit mode)</em>' : ''}</span>
          </label>
        `;
      });
    } else {
      const isSelected = wizardData.pci_controller === group.pci_controller && !wizardData.expander_sas_address;
      const disabled = isEditMode ? 'disabled' : '';
      html += `
        <label class="radio-option">
          <input type="radio" name="controller" value="${escapeHtml(group.pci_controller)}" data-expander="" ${isSelected ? 'checked' : ''} ${disabled}>
          <span>${escapeHtml(controllerLabel)} — Direct-attached (${group.slot_type})${occupiedBadge}${isEditMode ? ' <em>(cannot change in edit mode)</em>' : ''}</span>
        </label>
      `;
    }
  });

  html += `</div>`;

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
      <div class="form-group" style="background: #2a2a2a; padding: 12px; border-radius: 4px; margin-top: 12px;">
        <label style="color: #4a90e2; font-weight: bold;">Hybrid Slot Configuration</label>
        <p style="font-size: 0.8rem; color: #aaa; margin: 8px 0;">
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
      .sort();

    nvmeSlots.forEach(slot => {
      const selected = wizardData.nvme_starting_slot === slot ? 'selected' : '';
      html += `<option value="${escapeHtml(slot)}" ${selected}>Slot ${escapeHtml(slot)}</option>`;
    });

    html += `
        </select>
        <small style="color: #888; display: block; margin-top: 4px;">
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
    wizardData.display_order = parseInt(e.target.value) || 0;
  });
  container.querySelectorAll("input[name='controller']").forEach(radio => {
    radio.addEventListener("change", (e) => {
      wizardData.pci_controller = e.target.value;
      wizardData.expander_sas_address = e.target.dataset.expander || null;
    });
  });
  document.getElementById("wizardTemplateSelect").addEventListener("change", (e) => {
    wizardData.template_id = e.target.value;
    renderConfiguration(); // Re-render to show/hide NVMe options
  });

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
    positions = Array.from({ length: template.slot_count }, (_, i) => ({ row: i, col: 0 }));
  }

  // When editing an existing enclosure, load saved slot data so custom HW IDs are preserved
  let savedSlots = null;
  if (isEditMode && editEnclosureId && adminEnclosures[editEnclosureId]) {
    savedSlots = adminEnclosures[editEnclosureId].slots || null;
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
      sasHwId = `ata${physicalSlot}`;
      sasSlotType = "motherboard_sata";
    }

    // Compute NVMe HW identifier for hybrid slots
    let nvmeHwId = null;
    if (isHybrid && wizardData.nvme_starting_slot) {
      const nvmeStartingSlot = parseInt(wizardData.nvme_starting_slot, 10);
      if (!isNaN(nvmeStartingSlot)) {
        const nvmeOffset = template.hybrid_slots.indexOf(slotIndex);
        const nvmeSlotNum = nvmeStartingSlot + nvmeOffset;
        nvmeHwId = String(nvmeSlotNum);
      }
    }

    const slotKey = String(slotIndex);
    const savedSlot = savedSlots && savedSlots[slotKey] ? savedSlots[slotKey] : null;
    const savedSasMapping = savedSlot && savedSlot.mappings && savedSlot.mappings.sas_sata;
    const savedNvmeMapping = savedSlot && savedSlot.mappings && savedSlot.mappings.nvme;

    slots.push({
      physical_slot_number: physicalSlot,
      label: savedSlot ? savedSlot.label : `Bay ${slotIndex}`,
      role: savedSlot ? savedSlot.role : (template.default_role || "wipe"),
      locked: savedSlot ? savedSlot.locked : false,
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

  let html = `
    <div class="form-group" style="background: #2a2a2a; padding: 12px; border-radius: 4px; margin-bottom: 16px;">
      <label style="font-weight: bold;">Starting Slot Number</label>
      <input type="number" id="wizardStartingSlotLive" value="${wizardData.starting_slot_number}" min="0" style="width: 100px; padding: 4px; background: #333; border: 1px solid #555; color: #fff;">
      <small style="color: #888; display: block; margin-top: 4px;">Changing this will live-update the slot table below.</small>
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
    const isConfigured = sasMapping && sasMapping.hardware_identifier;

    html += `
      <tr>
        <td rowspan="${nvmeMapping ? 2 : 1}"><strong>${slot.physical_slot_number}</strong></td>
        <td rowspan="${nvmeMapping ? 2 : 1}">
          <input type="text" class="slot-label-input" data-slot-index="${index}" value="${escapeHtml(slot.label)}" style="width: 100%; padding: 4px; background: #222; border: 1px solid #444; color: #fff;">
        </td>
        <td rowspan="${nvmeMapping ? 2 : 1}">
          <select class="slot-role-select" data-slot-index="${index}" style="width: 100%; padding: 4px; background: #222; border: 1px solid #444; color: #fff;">
            <option value="wipe" ${slot.role === 'wipe' ? 'selected' : ''}>Wipe</option>
            <option value="os" ${slot.role === 'os' ? 'selected' : ''}>OS Drive</option>
            <option value="reserved" ${slot.role === 'reserved' ? 'selected' : ''}>Reserved</option>
          </select>
        </td>
        <td>
          <input type="text" class="hw-id-input" data-slot-index="${index}" data-interface="sas_sata" data-slot-type="${sasMapping ? escapeHtml(sasMapping.slot_type) : ''}" value="${sasMapping ? escapeHtml(sasMapping.hardware_identifier) : ''}" style="width: 100%; padding: 4px; background: #222; border: 1px solid #444; color: #fff;">
        </td>
        <td>${isConfigured ? '<span style="color: #4CAF50;">Configured</span>' : '<span style="color: #888;">Unconfigured</span>'}</td>
      </tr>
    `;

    if (nvmeMapping) {
      html += `
        <tr>
          <td>
            <input type="text" class="hw-id-input" data-slot-index="${index}" data-interface="nvme" data-slot-type="${nvmeMapping ? escapeHtml(nvmeMapping.slot_type) : ''}" value="${escapeHtml(nvmeMapping.hardware_identifier)}" style="width: 100%; padding: 4px; background: #222; border: 1px solid #444; color: #fff;">
          </td>
          <td><span style="color: #4CAF50;">Configured</span></td>
        </tr>
      `;
    }
  });

  html += `</tbody></table>`;
  container.innerHTML = html;

  // Bind starting slot input for live recalc
  document.getElementById("wizardStartingSlotLive").addEventListener("input", (e) => {
    wizardData.starting_slot_number = parseInt(e.target.value) || 0;
    renderSlotAssignment(); // Re-render with new starting slot
  });

  // Bind label input events
  container.querySelectorAll('.slot-label-input').forEach(input => {
    input.addEventListener('input', (e) => {
      const slotIndex = parseInt(e.target.dataset.slotIndex);
      if (slots[slotIndex]) {
        slots[slotIndex].label = e.target.value;
      }
    });
  });

  // Bind role select events
  container.querySelectorAll('.slot-role-select').forEach(select => {
    select.addEventListener('change', (e) => {
      const slotIndex = parseInt(e.target.dataset.slotIndex);
      if (slots[slotIndex]) {
        slots[slotIndex].role = e.target.value;
        slots[slotIndex].locked = e.target.value === 'os';
      }
    });
  });

  // Bind HW ID input events with format validation
  container.querySelectorAll('.hw-id-input').forEach(input => {
    input.addEventListener('input', (e) => {
      const slotIndex = parseInt(e.target.dataset.slotIndex);
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
  });

  // Store slots in wizard data for save
  wizardData.slots = slots;
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

async function handleSaveEnclosure() {
  try {
    // Generate and validate enclosure ID from trimmed name
    const trimmedName = (wizardData.name || "").trim();
    const enclosureId = trimmedName.toLowerCase().replace(/\s+/g, '_').replace(/[^a-z0-9_-]/g, '');
    if (!enclosureId || enclosureId.length < 2) {
      alert("Invalid enclosure name. Please use at least 2 alphanumeric characters.");
      return;
    }

    // Get slot mappings from the slot assignment table
    const container = document.getElementById("slotAssignmentContainer");
    const slotMappings = {};
    
    container.querySelectorAll('.slot-label-input').forEach(input => {
      const slotIndex = parseInt(input.dataset.slotIndex);
      if (isNaN(slotIndex)) {
        console.error("Invalid slot index for label input");
        return;
      }
      const slotKey = String(slotIndex);
      if (!slotMappings[slotKey]) {
        slotMappings[slotKey] = {};
      }
      slotMappings[slotKey].label = input.value;
    });
    
    container.querySelectorAll('.slot-role-select').forEach(select => {
      const slotIndex = parseInt(select.dataset.slotIndex);
      if (isNaN(slotIndex)) {
        console.error("Invalid slot index for role select");
        return;
      }
      const slotKey = String(slotIndex);
      if (slotMappings[slotKey]) {
        slotMappings[slotKey].role = select.value;
        slotMappings[slotKey].locked = select.value === 'os';
      }
    });
    
    // Add HW identifiers to slot mappings
    container.querySelectorAll('.hw-id-input').forEach(input => {
      const slotIndex = parseInt(input.dataset.slotIndex);
      const interfaceType = input.dataset.interface;
      const slotType = input.dataset.slotType;
      if (isNaN(slotIndex)) {
        console.error("Invalid slot index for HW ID input");
        return;
      }
      const slotKey = String(slotIndex);
      if (!slotMappings[slotKey]) {
        slotMappings[slotKey] = {};
      }
      if (!slotMappings[slotKey].mappings) {
        slotMappings[slotKey].mappings = {};
      }
      slotMappings[slotKey].mappings[interfaceType] = {
        slot_type: slotType,
        hardware_identifier: input.value
      };
    });

    const payload = {
      id: enclosureId,
      name: trimmedName,
      template_id: wizardData.template_id,
      pci_controller: wizardData.pci_controller,
      expander_sas_address: wizardData.expander_sas_address,
      display_order: wizardData.display_order,
      nvme_start_slot: wizardData.nvme_starting_slot,
      starting_slot_number: wizardData.starting_slot_number,
      slot_mappings: slotMappings
    };

    const response = await safeFetch("/api/admin/enclosures", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    if (!response.ok) {
      let data;
      try {
        data = await response.json();
      } catch (e) {
        throw new Error("Failed to create enclosure");
      }
      throw new Error(data.error || "Failed to create enclosure");
    }

    alert("Enclosure created successfully!");
    closeModal(document.getElementById("enclosureWizardModal"));
    await loadEnclosures();
    renderEnclosureList(Object.values(adminEnclosures));
  } catch (e) {
    alert(`Error: ${e.message}`);
  }
}

async function handleEditEnclosure() {
  try {
    if (!editEnclosureId) {
      alert("Edit mode error: missing enclosure ID");
      return;
    }

    const trimmedName = (wizardData.name || "").trim();
    if (!trimmedName || trimmedName.length < 2) {
      alert("Enclosure name must be at least 2 characters.");
      return;
    }

    // Get slot mappings from the slot assignment table
    const container = document.getElementById("slotAssignmentContainer");
    const slotMappings = {};
    
    container.querySelectorAll('.slot-label-input').forEach(input => {
      const slotIndex = parseInt(input.dataset.slotIndex);
      if (isNaN(slotIndex)) {
        console.error("Invalid slot index for label input");
        return;
      }
      const slotKey = String(slotIndex);
      if (!slotMappings[slotKey]) {
        slotMappings[slotKey] = {};
      }
      slotMappings[slotKey].label = input.value;
    });
    
    container.querySelectorAll('.slot-role-select').forEach(select => {
      const slotIndex = parseInt(select.dataset.slotIndex);
      if (isNaN(slotIndex)) {
        console.error("Invalid slot index for role select");
        return;
      }
      const slotKey = String(slotIndex);
      if (slotMappings[slotKey]) {
        slotMappings[slotKey].role = select.value;
        slotMappings[slotKey].locked = select.value === 'os';
      }
    });
    
    // Add HW identifiers to slot mappings
    container.querySelectorAll('.hw-id-input').forEach(input => {
      const slotIndex = parseInt(input.dataset.slotIndex);
      const interfaceType = input.dataset.interface;
      const slotType = input.dataset.slotType;
      if (isNaN(slotIndex)) {
        console.error("Invalid slot index for HW ID input");
        return;
      }
      const slotKey = String(slotIndex);
      if (!slotMappings[slotKey]) {
        slotMappings[slotKey] = {};
      }
      if (!slotMappings[slotKey].mappings) {
        slotMappings[slotKey].mappings = {};
      }
      slotMappings[slotKey].mappings[interfaceType] = {
        slot_type: slotType,
        hardware_identifier: input.value
      };
    });

    const payload = {
      name: trimmedName,
      template_id: wizardData.template_id,
      pci_controller: wizardData.pci_controller,
      expander_sas_address: wizardData.expander_sas_address,
      display_order: wizardData.display_order,
      nvme_start_slot: wizardData.nvme_starting_slot,
      starting_slot_number: wizardData.starting_slot_number,
      slot_mappings: slotMappings
    };

    const response = await safeFetch(`/api/admin/enclosures/${editEnclosureId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    if (!response.ok) {
      let data;
      try {
        data = await response.json();
      } catch (e) {
        throw new Error("Failed to update enclosure");
      }
      throw new Error(data.error || "Failed to update enclosure");
    }

    alert("Enclosure updated successfully!");
    closeModal(document.getElementById("enclosureWizardModal"));
    await loadEnclosures();
    renderEnclosureList(Object.values(adminEnclosures));
  } catch (e) {
    alert(`Error: ${e.message}`);
  }
}

// Attach listener at module load for static modals; dynamic modals re-attach in openNewEnclosureWizard/editEnclosure
const _moduleSaveBtn = document.getElementById("wizardSaveBtn");
if (_moduleSaveBtn) {
  _moduleSaveBtn.addEventListener("click", () => {
    if (isEditMode) {
      handleEditEnclosure();
    } else {
      handleSaveEnclosure();
    }
  });
  _moduleSaveBtn.dataset.enclosureListener = "true";
}

// Delete enclosure
async function deleteEnclosure(enclosureId) {
  if (!confirm(`Are you sure you want to delete enclosure ${enclosureId}?`)) return;

  try {
    const response = await safeFetch(`/api/admin/enclosures/${enclosureId}`, {
      method: "DELETE"
    });

    if (!response.ok) {
      const data = await response.json();
      throw new Error(data.error || "Failed to delete enclosure");
    }

    alert("Enclosure deleted successfully!");
    await loadEnclosures();
    renderEnclosureList(Object.values(adminEnclosures));
  } catch (e) {
    alert(`Error: ${e.message}`);
  }
}

// Edit enclosure
async function editEnclosure(enclosureId) {
  const enclosure = adminEnclosures[enclosureId];
  if (!enclosure) {
    alert("Enclosure not found.");
    return;
  }

  const modal = document.getElementById("enclosureWizardModal");
  if (!modal) return;

  // Ensure save button listener is attached
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

  // Set wizard to edit mode
  currentWizardStep = 1;
  isEditMode = true;
  editEnclosureId = enclosureId;
  wizardData = {
    name: enclosure.name || "",
    template_id: enclosure.template_id || "",
    pci_controller: enclosure.pci_controller || "",
    expander_sas_address: enclosure.expander_sas_address || null,
    display_order: enclosure.display_order || 0,
    nvme_starting_slot: enclosure.nvme_start_slot || null,
    starting_slot_number: enclosure.starting_slot_number || null
  };

  // Update modal title
  const modalTitle = modal.querySelector(".modal-title");
  if (modalTitle) {
    modalTitle.textContent = "Edit Enclosure";
  }

  renderWizardStep();
  openModal(modal);
}

// Track initialization state to prevent redundant API calls
let enclosureManagementInitialized = false;

// Initialize enclosure management on admin panel load
async function initializeEnclosureManagement() {
  if (enclosureManagementInitialized) {
    // Data already loaded, just re-render
    renderEnclosureList(Object.values(adminEnclosures));
    return;
  }

  await Promise.all([
    loadEnclosures(),
    loadTemplates(),
    loadMasterSlotMap()
  ]);
  renderEnclosureList(Object.values(adminEnclosures));
  enclosureManagementInitialized = true;
}

// Wire up "Add Enclosure" button
function attachEnclosureManagementListeners() {
  const addEnclosureBtn = document.getElementById("addEnclosureBtn");
  if (addEnclosureBtn) {
    addEnclosureBtn.addEventListener("click", openNewEnclosureWizard);
  }

  const adminTab = document.querySelector('[data-tab="adminPanel"]');
  if (adminTab) {
    adminTab.addEventListener("click", () => {
      initializeEnclosureManagement();
    });
  }
}

// Attach listeners immediately if DOM is ready, otherwise wait for DOMContentLoaded
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", attachEnclosureManagementListeners);
} else {
  attachEnclosureManagementListeners();
}
// --- END OF FILE frontend/admin/enclosureManagement.js ---
