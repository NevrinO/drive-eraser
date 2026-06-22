// --- START OF FILE frontend/admin/discoveryModal.js ---
// Discovery modal UI and event handlers
// This file contains only UI-related code. Business logic is in:
// - discoveryValidation.js (validation functions)
// - discoveryState.js (state management)
// - discoveryMapping.js (mapping business logic)

// Defensive namespace checks (CRITIQUE.md #2)
// Throw errors to halt execution if required modules are not initialized
if (typeof window.DiscoveryValidation === 'undefined') {
  throw new Error('Critical: window.DiscoveryValidation not initialized. discoveryValidation.js may have failed to load.');
}
if (typeof window.DiscoveryState === 'undefined') {
  throw new Error('Critical: window.DiscoveryState not initialized. discoveryState.js may have failed to load.');
}
if (typeof window.DiscoveryMapping === 'undefined') {
  throw new Error('Critical: window.DiscoveryMapping not initialized. discoveryMapping.js may have failed to load.');
}

// Discovery modal elements
const discoveryModal = document.getElementById("discoveryModal");
const discoveryClose = document.getElementById("discoveryClose");
const discoverSlotsBtn = document.getElementById("discoverSlotsBtn");
const discoveryStatus = document.getElementById("discoveryStatus");
const discoveryResults = document.getElementById("discoveryResults");
const controllersList = document.getElementById("controllersList");
const devicesList = document.getElementById("devicesList");
const enclosureSlotsSection = document.getElementById("enclosureSlotsSection");
const enclosureSlotsList = document.getElementById("enclosureSlotsList");
const applyMappingBtn = document.getElementById("applyMappingBtn");
const cancelMappingBtn = document.getElementById("cancelMappingBtn");
const groupNoneBtn = document.getElementById("groupNoneBtn");
const groupTypeBtn = document.getElementById("groupTypeBtn");
const groupPciBtn = document.getElementById("groupPciBtn");

// Pattern mapping elements
const mappingPattern = document.getElementById("mappingPattern");
const mappingStartBay = document.getElementById("mappingStartBay");
const mappingDeviceFilter = document.getElementById("mappingDeviceFilter");
const previewMappingBtn = document.getElementById("previewMappingBtn");
const mappingPreview = document.getElementById("mappingPreview");

// Manual mapping elements (Task 4.5)
const patternModeBtn = document.getElementById("patternModeBtn");
const manualModeBtn = document.getElementById("manualModeBtn");
const patternMappingControls = document.getElementById("patternMappingControls");
const manualMappingControls = document.getElementById("manualMappingControls");
const deviceSearchInput = document.getElementById("deviceSearchInput");
const manualDeviceFilter = document.getElementById("manualDeviceFilter");
const availableDevicesList = document.getElementById("availableDevicesList");
const manualBaySelect = document.getElementById("manualBaySelect");
const selectedDeviceInfo = document.getElementById("selectedDeviceInfo");
const addManualMappingBtn = document.getElementById("addManualMappingBtn");
const clearManualMappingsBtn = document.getElementById("clearManualMappingsBtn");
const manualMappingPreview = document.getElementById("manualMappingPreview");
const undoMappingBtn = document.getElementById("undoMappingBtn");
const mappingValidationError = document.getElementById("mappingValidationError");

if (!discoveryModal || !discoverSlotsBtn || !discoveryStatus ||
    !discoveryResults || !controllersList || !devicesList ||
    !enclosureSlotsSection || !enclosureSlotsList || !applyMappingBtn ||
    !groupNoneBtn || !groupTypeBtn || !groupPciBtn ||
    !mappingPattern || !mappingStartBay || !mappingDeviceFilter || !previewMappingBtn || !mappingPreview ||
    !patternModeBtn || !manualModeBtn || !patternMappingControls || !manualMappingControls ||
    !deviceSearchInput || !manualDeviceFilter || !availableDevicesList ||
    !manualBaySelect || !selectedDeviceInfo || !addManualMappingBtn ||
    !clearManualMappingsBtn || !manualMappingPreview || !undoMappingBtn || !mappingValidationError) {
  console.error("Critical: One or more discovery modal elements not found in DOM");
}

// Resets pattern mapping preview and undo state - called on modal open and close
function resetDiscoveryPreview() {
  if (mappingPreview) {
    mappingPreview.style.display = 'none';
    mappingPreview.innerHTML = '';
  }
  window.DiscoveryState.resetDiscoveryPreview();
  undoMappingBtn.disabled = true;
  hideMappingValidationError();
}

// Discovery modal functions
function openDiscoveryModal() {
  const discoveryState = window.DiscoveryState.getDiscoveryState();
  
  // Restore state if previously discovered (Task 4.3)
  if (discoveryState.lastDiscovered && discoveryState.controllers.length > 0) {
    discoveryResults.style.display = "block";
    renderControllers(discoveryState.controllers);
    renderDevices(discoveryState.devicesByType);
    renderEnclosureSlots(discoveryState.enclosureSlots);
    const timeAgo = new Date(discoveryState.lastDiscovered).toLocaleTimeString();
    discoveryStatus.textContent = `Last discovered: ${timeAgo} (${discoveryState.totalDevices} devices, ${discoveryState.controllers.length} controllers)`;
    discoveryStatus.style.color = "#888";
  } else {
    discoveryResults.style.display = "none";
    discoveryStatus.textContent = "";
    controllersList.innerHTML = "";
    devicesList.innerHTML = "";
    enclosureSlotsList.innerHTML = "";
    enclosureSlotsSection.style.display = "none";
  }

  applyMappingBtn.disabled = true;

  // Initialize grouping button styles to reflect default mode (CRITIQUE.md #4)
  setGroupingMode(discoveryState.groupingMode);

  // Reset controller selection (default none selected)
  discoveryState.selectedControllers.clear();

  // Initialize manual mapping state (Task 4.5)
  window.DiscoveryMapping.setMappingMode('pattern', patternModeBtn, manualModeBtn, patternMappingControls, manualMappingControls, applyMappingBtn);
  window.DiscoveryMapping.setManualMappings({});
  window.DiscoveryMapping.setSelectedDevice(null);
  populateManualBaySelect();
  renderAvailableDevices();
  renderManualMappingPreview();

  // Reset mapping preview and undo state (Task 4.4, 4.8)
  resetDiscoveryPreview();

  discoveryModal.classList.add("open");
  discoveryModal.setAttribute("aria-hidden", "false");
}

function closeDiscoveryModal() {
  discoveryModal.classList.remove("open");
  discoveryModal.setAttribute("aria-hidden", "true");
  // Reset grouping mode to default (CRITIQUE.md #2, #3)
  setGroupingMode('none');
  // Reset mapping preview and undo state (Task 4.4, 4.8)
  resetDiscoveryPreview();
  // Reset manual mapping state (Task 4.5)
  window.DiscoveryMapping.setManualMappings({});
  window.DiscoveryMapping.setSelectedDevice(null);
  deviceSearchInput.value = '';
}

// HTML template helpers for render functions
const CONTROLLER_CARD_TEMPLATE = (controller, isSelected) => {
  const pciAddr = controller.pci_address || "Unknown";
  const type = controller.controller_type || "unknown";
  const desc = controller.description || "Unknown Controller";
  const vendorId = controller.vendor_id || "Unknown";
  const deviceId = controller.device_id || "Unknown";

  return `
    <div style="padding: 8px; margin-bottom: 8px; background: #333; border-radius: 4px; border-left: 3px solid ${isSelected ? 'var(--color-primary)' : '#555'};">
      <div style="display: flex; align-items: center; gap: 8px;">
        <input type="checkbox" class="controller-checkbox" data-pci-address="${escapeHtml(pciAddr)}" ${isSelected ? 'checked' : ''} style="cursor: pointer;">
        <div style="flex: 1;">
          <div style="font-weight: bold; color: var(--color-primary);">${escapeHtml(desc)}</div>
          <div style="font-size: 0.75rem; color: #888; margin-top: 4px;">
            <div>Type: ${escapeHtml(type.toUpperCase())}</div>
            <div>PCI: ${escapeHtml(pciAddr)}</div>
            <div>Vendor ID: ${escapeHtml(vendorId)} | Device ID: ${escapeHtml(deviceId)}</div>
          </div>
        </div>
      </div>
    </div>
  `;
};

const DEVICE_CARD_TEMPLATE = (device) => {
  const path = device.device_path || "Unknown";
  const name = device.device_name || "Unknown";
  const controllerPci = device.controller_pci || "Unknown";
  const smart = device.smart || {};

  return `
    <div style="padding: 8px; margin-bottom: 8px; background: #333; border-radius: 4px;">
      <div style="font-weight: bold;">${escapeHtml(name)}</div>
      <div style="font-size: 0.75rem; color: #888; margin-top: 4px;">
        <div>Path: ${escapeHtml(path)}</div>
        <div>Controller: ${escapeHtml(controllerPci)}</div>
        ${smart.model ? `<div>Model: ${escapeHtml(smart.model)}</div>` : ""}
        ${smart.serial ? `<div>Serial: ${escapeHtml(smart.serial)}</div>` : ""}
        ${smart.capacity_str ? `<div>Capacity: ${escapeHtml(smart.capacity_str)}</div>` : ""}
      </div>
    </div>
  `;
};

const SLOT_CARD_TEMPLATE = (slot) => {
  const encId = slot.enclosure_id || "Unknown";
  const slotId = slot.slot_id || "Unknown";
  const slotNum = slot.slot_number !== null ? slot.slot_number : "N/A";
  const device = slot.device || "Empty";
  const smart = slot.smart || {};

  return `
    <div style="padding: 8px; margin-bottom: 8px; background: #333; border-radius: 4px;">
      <div style="font-weight: bold;">Enclosure ${escapeHtml(encId)} - Slot ${escapeHtml(slotId)} (#${slotNum})</div>
      <div style="font-size: 0.75rem; color: #888; margin-top: 4px;">
        <div>Device: ${escapeHtml(device)}</div>
        ${smart.model ? `<div>Model: ${escapeHtml(smart.model)}</div>` : ""}
        ${smart.serial ? `<div>Serial: ${escapeHtml(smart.serial)}</div>` : ""}
        ${smart.capacity_str ? `<div>Capacity: ${escapeHtml(smart.capacity_str)}</div>` : ""}
      </div>
    </div>
  `;
};

function renderControllers(controllers) {
  const discoveryState = window.DiscoveryState.getDiscoveryState();
  
  // Type validation (CRITIQUE.md #3)
  if (!Array.isArray(controllers)) {
    console.error("renderControllers: expected array, got", typeof controllers);
    controllersList.innerHTML = "<div style='color: var(--color-danger);'>Error: Invalid controller data</div>";
    return;
  }

  if (controllers.length === 0) {
    controllersList.innerHTML = "<div style='color: #666; font-style: italic;'>No controllers detected</div>";
    return;
  }

  // Add Select All / Deselect All buttons
  let html = `
    <div style="margin-bottom: 12px; display: flex; gap: 8px;">
      <button type="button" id="selectAllControllersBtn" style="padding: 4px 12px; font-size: 0.75rem; background: var(--color-primary); border: none; color: #fff; border-radius: 2px; cursor: pointer;">Select All</button>
      <button type="button" id="deselectAllControllersBtn" style="padding: 4px 12px; font-size: 0.75rem; background: #444; border: none; color: #fff; border-radius: 2px; cursor: pointer;">Deselect All</button>
    </div>
  `;

  // Apply grouping based on mode (Task 4.3)
  let controllerGroups = {};
  if (discoveryState.groupingMode === 'type') {
    controllerGroups = window.DiscoveryMapping.groupControllersByType(controllers);
  } else if (discoveryState.groupingMode === 'pci') {
    controllerGroups = window.DiscoveryMapping.groupControllersByPCI(controllers);
  } else {
    // No grouping - single group with all controllers
    controllerGroups = { 'all': controllers };
  }

  for (const [groupName, groupControllers] of Object.entries(controllerGroups)) {
    // Rule #5: DoS prevention - limit group size
    if (groupControllers.length > 100) {
      html += `<div style="color: var(--color-danger); padding: 8px;">Group "${escapeHtml(groupName)}" exceeds maximum display limit (100 controllers)</div>`;
      continue;
    }

    if (discoveryState.groupingMode !== 'none') {
      html += `<div style="margin-bottom: 12px;"><strong style="color: var(--color-primary);">${escapeHtml(groupName.toUpperCase())} (${groupControllers.length})</strong></div>`;
    }

    html += groupControllers.map(controller => {
      const pciAddr = controller.pci_address || "Unknown";
      const isSelected = discoveryState.selectedControllers.has(pciAddr);
      return CONTROLLER_CARD_TEMPLATE(controller, isSelected);
    }).join("");
  }

  controllersList.innerHTML = html;
}

function renderDevices(devicesByType) {
  // Type validation (CRITIQUE.md #3)
  if (!devicesByType || typeof devicesByType !== "object") {
    console.error("renderDevices: expected object, got", typeof devicesByType);
    devicesList.innerHTML = "<div style='color: var(--color-danger);'>Error: Invalid device data</div>";
    return;
  }

  if (Object.keys(devicesByType).length === 0) {
    devicesList.innerHTML = "<div style='color: #666; font-style: italic;'>No devices detected</div>";
    return;
  }

  let html = "";
  for (const [type, devices] of Object.entries(devicesByType)) {
    if (!devices || devices.length === 0) continue;

    html += `<div style="margin-bottom: 12px;"><strong style="color: var(--color-primary);">${escapeHtml(type.toUpperCase())} (${devices.length})</strong></div>`;

    devices.forEach(device => {
      html += DEVICE_CARD_TEMPLATE(device);
    });
  }

  devicesList.innerHTML = html;
}

function renderEnclosureSlots(slots) {
  // Type validation (CRITIQUE.md #3)
  if (!Array.isArray(slots)) {
    console.error("renderEnclosureSlots: expected array, got", typeof slots);
    enclosureSlotsSection.style.display = "none";
    return;
  }

  if (slots.length === 0) {
    enclosureSlotsSection.style.display = "none";
    return;
  }

  enclosureSlotsSection.style.display = "block";

  enclosureSlotsList.innerHTML = slots.map(slot => SLOT_CARD_TEMPLATE(slot)).join("");
}

async function discoverSlots() {
  discoverSlotsBtn.disabled = true;
  discoverSlotsBtn.textContent = "Discovering...";
  discoveryStatus.textContent = "Scanning system for controllers and devices...";

  try {
    const response = await safeFetch("/api/admin/discover-slots?include_smart=true");
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    let data;
    try {
      data = await response.json();
    } catch (e) {
      console.error("Failed to parse discovery response JSON:", e);
      throw new Error("Invalid server response");
    }

    if (data.error) {
      throw new Error(data.error);
    }

    // Validate response data structure (CRITIQUE.md #2)
    if (!data.controllers || !Array.isArray(data.controllers)) {
      throw new Error("Invalid response: missing or invalid controllers array");
    }
    if (!data.devices_by_type || typeof data.devices_by_type !== "object") {
      throw new Error("Invalid response: missing or invalid devices_by_type object");
    }
    if (!data.enclosure_slots || !Array.isArray(data.enclosure_slots)) {
      throw new Error("Invalid response: missing or invalid enclosure_slots array");
    }
    if (!data.scsi_slot_projections || !Array.isArray(data.scsi_slot_projections)) {
      throw new Error("Invalid response: missing or invalid scsi_slot_projections array");
    }

    // Store discovery data in state (Task 4.3)
    window.DiscoveryState.setDiscoveryState({
      controllers: data.controllers,
      devicesByType: data.devices_by_type,
      enclosureSlots: data.enclosure_slots,
      scsiSlotProjections: data.scsi_slot_projections,
      totalDevices: data.total_devices || 0,
      lastDiscovered: new Date().toISOString()
    });

    // Display results
    discoveryResults.style.display = "block";
    renderControllers(data.controllers);
    renderDevices(data.devices_by_type);
    renderEnclosureSlots(data.enclosure_slots);

    discoveryStatus.textContent = `Found ${data.total_devices || 0} device(s) across ${data.controllers?.length || 0} controller(s)`;
    discoveryStatus.style.color = "var(--color-success)";

  } catch (err) {
    discoveryStatus.textContent = `Error: ${err.message}`;
    discoveryStatus.style.color = "var(--color-danger)";
    discoveryResults.style.display = "none";
  } finally {
    discoverSlotsBtn.disabled = false;
    discoverSlotsBtn.textContent = "⚡ Discover Slots";
  }
}

// Discovery modal event listeners
// Note: discoveryClose and cancelMappingBtn are handled by global data-close-modal handler in modals.js
if (discoverSlotsBtn) {
  discoverSlotsBtn.addEventListener("click", discoverSlots);
}

// Grouping mode buttons (Task 4.3)
function setGroupingMode(mode) {
  const discoveryState = window.DiscoveryState.getDiscoveryState();
  discoveryState.groupingMode = mode;
  // Update button styles
  groupNoneBtn.style.background = mode === 'none' ? 'var(--color-primary)' : '';
  groupTypeBtn.style.background = mode === 'type' ? 'var(--color-primary)' : '';
  groupPciBtn.style.background = mode === 'pci' ? 'var(--color-primary)' : '';
  // Re-render controllers with new grouping
  if (discoveryState.controllers.length > 0) {
    renderControllers(discoveryState.controllers);
  }
}

groupNoneBtn.addEventListener("click", () => setGroupingMode('none'));
groupTypeBtn.addEventListener("click", () => setGroupingMode('type'));
groupPciBtn.addEventListener("click", () => setGroupingMode('pci'));

// Controller selection event listeners
document.addEventListener('click', (e) => {
  const discoveryState = window.DiscoveryState.getDiscoveryState();
  
  // Handle controller checkbox clicks
  if (e.target.classList.contains('controller-checkbox')) {
    const pciAddr = e.target.getAttribute('data-pci-address');
    // Browser has already toggled the checkbox, read the NEW state
    if (e.target.checked) {
      // Validate PCI address before adding to selection
      if (window.DiscoveryValidation.validatePciAddress(pciAddr)) {
        discoveryState.selectedControllers.add(pciAddr);
      }
    } else {
      discoveryState.selectedControllers.delete(pciAddr);
    }
    // Only update the border color of the specific controller card
    const controllerCard = e.target.closest('div[style*="border-left"]');
    if (controllerCard) {
      controllerCard.style.borderLeftColor = e.target.checked ? 'var(--color-primary)' : '#555';
    }
  }

  // Handle Select All button
  if (e.target.id === 'selectAllControllersBtn') {
    discoveryState.controllers.forEach(controller => {
      if (controller.pci_address && window.DiscoveryValidation.validatePciAddress(controller.pci_address)) {
        discoveryState.selectedControllers.add(controller.pci_address);
      }
    });
    renderControllers(discoveryState.controllers);
  }

  // Handle Deselect All button
  if (e.target.id === 'deselectAllControllersBtn') {
    discoveryState.selectedControllers.clear();
    renderControllers(discoveryState.controllers);
  }
});

// Pattern mapping event listeners (Task 4.4)
if (previewMappingBtn) {
  previewMappingBtn.addEventListener("click", () => {
    const discoveryState = window.DiscoveryState.getDiscoveryState();
    window.DiscoveryMapping.generateMappingPreview(
      discoveryState,
      localBayMapCopy,
      mappingPattern,
      mappingStartBay,
      mappingDeviceFilter,
      mappingPreview,
      applyMappingBtn,
      showMappingValidationError,
      hideMappingValidationError,
      setPreviewMessage,
      window.escapeHtml,
      mappingValidationError
    );
  });
}

if (applyMappingBtn) {
  applyMappingBtn.addEventListener("click", async () => {
    try {
      await window.DiscoveryMapping.applyMappingToBayConfig(
        localBayMapCopy,
        loadBayMappingConfig,
        closeDiscoveryModal,
        showMappingValidationError,
        hideMappingValidationError,
        window.safeFetch,
        renderBayMappingConfig,
        showUnsavedChangesIndicator
      );
    } catch (err) {
      alert(`Error applying mapping: ${err.message}`);
    }
  });
}

// Sets the mapping preview panel to an error or warning message and shows it
function setPreviewMessage(message, isWarning = false) {
  mappingPreview.innerHTML = `<div style="color: ${isWarning ? 'var(--color-warning)' : 'var(--color-danger)'}">${message}</div>`;
  mappingPreview.style.display = 'block';
}

// Display validation errors in modal (Task 4.8)
function showMappingValidationError(message) {
  if (!mappingValidationError) return;
  mappingValidationError.textContent = message;
  mappingValidationError.style.display = 'block';
}

function hideMappingValidationError() {
  if (!mappingValidationError) return;
  mappingValidationError.style.display = 'none';
  mappingValidationError.textContent = '';
}

// Manual mapping UI functions (Task 4.5)

function populateManualBaySelect() {
  manualBaySelect.innerHTML = '';
  
  // Safety check: ensure localBayMapCopy is populated
  if (!localBayMapCopy || Object.keys(localBayMapCopy).length === 0) {
    const option = document.createElement('option');
    option.value = '';
    option.textContent = 'No bays available';
    manualBaySelect.appendChild(option);
    return;
  }

  const sortedBayKeys = window.DiscoveryMapping.sortBayIds(Object.keys(localBayMapCopy));

  sortedBayKeys.forEach(bayId => {
    const option = document.createElement('option');
    option.value = bayId;
    option.textContent = `${bayId} (${localBayMapCopy[bayId]?.label || bayId})`;
    manualBaySelect.appendChild(option);
  });
}

function renderAvailableDevices() {
  const discoveryState = window.DiscoveryState.getDiscoveryState();
  
  if (!discoveryState.devicesByType || Object.keys(discoveryState.devicesByType).length === 0) {
    availableDevicesList.innerHTML = '<div style="color: #666; font-style: italic; font-size: 0.75rem;">No devices discovered. Click "Discover Slots" first.</div>';
    return;
  }

  const searchTerm = deviceSearchInput.value;
  const filterType = manualDeviceFilter.value;
  const allDevices = window.DiscoveryMapping.flattenDevices(discoveryState.devicesByType, 'all');
  const filteredDevices = window.DiscoveryMapping.filterDevices(allDevices, searchTerm, filterType);
  const selectedDevice = window.DiscoveryMapping.getSelectedDevice();
  const manualMappings = window.DiscoveryMapping.getManualMappings();

  // Rule #5: DoS prevention - limit display size
  if (filteredDevices.length > 200) {
    availableDevicesList.innerHTML = '<div style="color: var(--color-danger); font-size: 0.75rem;">Too many devices to display. Use search/filter to narrow results.</div>';
    return;
  }

  if (filteredDevices.length === 0) {
    availableDevicesList.innerHTML = '<div style="color: #666; font-style: italic; font-size: 0.75rem;">No devices match search/filter criteria.</div>';
    return;
  }

  availableDevicesList.innerHTML = filteredDevices.map(device => {
    const isSelected = selectedDevice && selectedDevice.device_path === device.device_path;
    const isMapped = Object.values(manualMappings).some(m => m.device_path === device.device_path);
    
    return `
      <div class="device-item" 
           data-device-path="${escapeHtml(device.device_path)}"
           style="padding: 6px; margin-bottom: 4px; background: ${isSelected ? 'var(--color-primary)' : (isMapped ? '#2a2a2a' : '#333')}; 
                  border-radius: 2px; cursor: pointer; font-size: 0.75rem; border: 1px solid ${isSelected ? 'var(--color-primary)' : '#444'};">
        <div style="font-weight: bold; color: ${isMapped ? '#666' : '#fff'};">${escapeHtml(device.device_name)}</div>
        <div style="color: ${isMapped ? '#555' : '#888'}; font-size: 0.7rem;">${escapeHtml(device.device_path)}</div>
        ${device.smart?.model ? `<div style="color: ${isMapped ? '#555' : '#888'}; font-size: 0.7rem;">${escapeHtml(device.smart.model)}</div>` : ''}
        ${isMapped ? '<div style="color: var(--color-warning); font-size: 0.7rem;">Already mapped</div>' : ''}
      </div>
    `;
  }).join('');

  // Add click handlers
  availableDevicesList.querySelectorAll('.device-item').forEach(item => {
    item.addEventListener('click', () => {
      const devicePath = item.getAttribute('data-device-path');
      const device = allDevices.find(d => d.device_path === devicePath);
      if (device && !Object.values(manualMappings).some(m => m.device_path === devicePath)) {
        window.DiscoveryMapping.setSelectedDevice(device);
        updateSelectedDeviceInfo();
        renderAvailableDevices();
      }
    });
  });
}

function updateSelectedDeviceInfo() {
  const selectedDevice = window.DiscoveryMapping.getSelectedDevice();
  
  if (!selectedDevice) {
    selectedDeviceInfo.innerHTML = 'No device selected';
    selectedDeviceInfo.style.color = '#888';
    return;
  }

  const smart = selectedDevice.smart || {};
  selectedDeviceInfo.innerHTML = `
    <div style="font-weight: bold; color: #fff;">${escapeHtml(selectedDevice.device_name)}</div>
    <div style="color: #888;">${escapeHtml(selectedDevice.device_path)}</div>
    ${smart.model ? `<div style="color: #888;">${escapeHtml(smart.model)}</div>` : ''}
    ${smart.serial ? `<div style="color: #888;">S/N: ${escapeHtml(smart.serial)}</div>` : ''}
  `;
  selectedDeviceInfo.style.color = '#fff';
}

function renderManualMappingPreview() {
  const manualMappings = window.DiscoveryMapping.getManualMappings();
  const mappingKeys = window.DiscoveryMapping.sortBayIds(Object.keys(manualMappings));

  if (mappingKeys.length === 0) {
    manualMappingPreview.innerHTML = '<div style="color: #666; font-style: italic; font-size: 0.75rem;">No manual mappings created yet.</div>';
    return;
  }

  manualMappingPreview.innerHTML = mappingKeys.map(bayId => {
    const mapping = manualMappings[bayId];
    return `
      <div style="padding: 4px; background: #333; border-radius: 2px; margin-bottom: 4px; font-size: 0.75rem; display: flex; justify-content: space-between; align-items: center;">
        <div>
          <strong style="color: var(--color-primary);">${escapeHtml(bayId)}</strong> → ${escapeHtml(mapping.device_name)} (${escapeHtml(mapping.device_path)})
        </div>
        <button type="button" class="btn-remove-mapping" data-bay-id="${escapeHtml(bayId)}" 
                style="padding: 2px 8px; font-size: 0.7rem; background: var(--color-danger); border: none; color: #fff; cursor: pointer; border-radius: 2px;">×</button>
      </div>
    `;
  }).join('');

  // Add remove handlers
  manualMappingPreview.querySelectorAll('.btn-remove-mapping').forEach(btn => {
    btn.addEventListener('click', () => {
      const bayId = btn.getAttribute('data-bay-id');
      window.DiscoveryMapping.removeManualMapping(bayId);
      renderAvailableDevices();
      renderManualMappingPreview();
      applyMappingBtn.disabled = Object.keys(window.DiscoveryMapping.getManualMappings()).length === 0;
    });
  });
}

// Manual mapping event listeners (Task 4.5)
patternModeBtn.addEventListener('click', () => {
  window.DiscoveryMapping.setMappingMode('pattern', patternModeBtn, manualModeBtn, patternMappingControls, manualMappingControls, applyMappingBtn);
});
manualModeBtn.addEventListener('click', () => {
  window.DiscoveryMapping.setMappingMode('manual', patternModeBtn, manualModeBtn, patternMappingControls, manualMappingControls, applyMappingBtn);
  renderAvailableDevices();
});
deviceSearchInput.addEventListener('input', () => renderAvailableDevices());
manualDeviceFilter.addEventListener('change', () => renderAvailableDevices());
addManualMappingBtn.addEventListener('click', () => {
  const success = window.DiscoveryMapping.addManualMapping(manualBaySelect, showMappingValidationError, hideMappingValidationError);
  if (success) {
    updateSelectedDeviceInfo();
    renderAvailableDevices();
    renderManualMappingPreview();
    applyMappingBtn.disabled = false;
  }
});
clearManualMappingsBtn.addEventListener('click', () => {
  if (window.DiscoveryMapping.hasManualMappings()) {
    if (confirm('Are you sure you want to clear all manual mappings?')) {
      window.DiscoveryMapping.clearManualMappings();
      updateSelectedDeviceInfo();
      renderAvailableDevices();
      renderManualMappingPreview();
      applyMappingBtn.disabled = true;
    }
  }
});

// Undo button event listener (Task 4.8)
if (undoMappingBtn) {
  undoMappingBtn.addEventListener('click', () => {
    if (confirm('Are you sure you want to undo the last mapping change? This will restore the previous bay mapping state.')) {
      window.DiscoveryState.restorePreviousBayMapState(localBayMapCopy, renderBayMappingConfig, showUnsavedChangesIndicator);
      hideMappingValidationError();
    }
  });
}
// --- END OF FILE frontend/admin/discoveryModal.js ---
