// --- START OF FILE frontend/admin/discoveryModal.js ---
// Discovery modal: slot discovery, controller grouping, pattern/manual mapping, validation, undo

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

// Discovery state management (Task 4.3)
// Lifecycle: controllers, devicesByType, enclosureSlots, totalDevices, lastDiscovered persist across modal sessions
// Lifecycle: groupingMode resets to 'none' on modal close
let discoveryState = {
  controllers: [],
  devicesByType: {},
  enclosureSlots: [],
  totalDevices: 0,
  lastDiscovered: null,
  groupingMode: 'none' // 'none', 'type', 'pci'
};

// Pattern mapping state (Task 4.4)
let currentMappingPreview = null;

// Manual mapping state (Task 4.5)
let mappingMode = 'pattern'; // 'pattern' or 'manual'
let manualMappings = {}; // { bayId: { device_path, device_name, controller_pci, type } }
let selectedDevice = null; // Currently selected device for manual mapping

// Undo state (Task 4.8)
let previousBayMapState = null; // Stores bay map before applying mapping for undo functionality

// Discovery modal functions
function openDiscoveryModal() {
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

  // Initialize manual mapping state (Task 4.5)
  setMappingMode('pattern');
  manualMappings = {};
  selectedDevice = null;
  populateManualBaySelect();
  renderAvailableDevices();
  renderManualMappingPreview();

  // Reset mapping preview state (Task 4.4)
  if (mappingPreview) {
    mappingPreview.style.display = 'none';
    mappingPreview.innerHTML = '';
  }
  currentMappingPreview = null;

  // Reset undo state (Task 4.8)
  previousBayMapState = null;
  undoMappingBtn.disabled = true;
  hideMappingValidationError();

  discoveryModal.classList.remove("hidden");
  discoveryModal.setAttribute("aria-hidden", "false");
}

function closeDiscoveryModal() {
  discoveryModal.classList.add("hidden");
  discoveryModal.setAttribute("aria-hidden", "true");
  // Reset grouping mode to default (CRITIQUE.md #2, #3)
  setGroupingMode('none');
  // Reset mapping preview state (Task 4.4)
  currentMappingPreview = null;
  if (mappingPreview) {
    mappingPreview.style.display = 'none';
    mappingPreview.innerHTML = '';
  }
  // Reset manual mapping state (Task 4.5)
  manualMappings = {};
  selectedDevice = null;
  deviceSearchInput.value = '';
  // Reset undo state (Task 4.8)
  previousBayMapState = null;
  undoMappingBtn.disabled = true;
  hideMappingValidationError();
}

function groupControllersByType(controllers) {
  // Rule #4: Use proper object comparison with Set for deduplication
  const grouped = {};
  controllers.forEach(controller => {
    const type = controller.controller_type || "unknown";
    if (!grouped[type]) {
      grouped[type] = [];
    }
    grouped[type].push(controller);
  });
  return grouped;
}

function groupControllersByPCI(controllers) {
  // Group by PCI address prefix (bus:device.function)
  const grouped = {};
  controllers.forEach(controller => {
    const pciAddr = controller.pci_address || "unknown";
    const prefix = pciAddr.substring(0, pciAddr.lastIndexOf('.')) || pciAddr;
    if (!grouped[prefix]) {
      grouped[prefix] = [];
    }
    grouped[prefix].push(controller);
  });
  return grouped;
}

function renderControllers(controllers) {
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

  // Apply grouping based on mode (Task 4.3)
  let controllerGroups = {};
  if (discoveryState.groupingMode === 'type') {
    controllerGroups = groupControllersByType(controllers);
  } else if (discoveryState.groupingMode === 'pci') {
    controllerGroups = groupControllersByPCI(controllers);
  } else {
    // No grouping - single group with all controllers
    controllerGroups = { 'all': controllers };
  }

  let html = "";
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
      const type = controller.controller_type || "unknown";
      const desc = controller.description || "Unknown Controller";
      const vendorId = controller.vendor_id || "Unknown";
      const deviceId = controller.device_id || "Unknown";

      return `
        <div style="padding: 8px; margin-bottom: 8px; background: #333; border-radius: 4px; border-left: 3px solid var(--color-primary);">
          <div style="font-weight: bold; color: var(--color-primary);">${escapeHtml(desc)}</div>
          <div style="font-size: 0.75rem; color: #888; margin-top: 4px;">
            <div>Type: ${escapeHtml(type.toUpperCase())}</div>
            <div>PCI: ${escapeHtml(pciAddr)}</div>
            <div>Vendor ID: ${escapeHtml(vendorId)} | Device ID: ${escapeHtml(deviceId)}</div>
          </div>
        </div>
      `;
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
      const path = device.device_path || "Unknown";
      const name = device.device_name || "Unknown";
      const controllerPci = device.controller_pci || "Unknown";
      const smart = device.smart || {};

      html += `
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

  enclosureSlotsList.innerHTML = slots.map(slot => {
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
  }).join("");
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

    // Store discovery data in state (Task 4.3)
    discoveryState.controllers = data.controllers;
    discoveryState.devicesByType = data.devices_by_type;
    discoveryState.enclosureSlots = data.enclosure_slots;
    discoveryState.totalDevices = data.total_devices || 0;
    discoveryState.lastDiscovered = new Date().toISOString();

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

// Pattern mapping event listeners (Task 4.4)
if (previewMappingBtn) {
  previewMappingBtn.addEventListener("click", generateMappingPreview);
}

if (applyMappingBtn) {
  applyMappingBtn.addEventListener("click", async () => {
    try {
      await applyMappingToBayConfig();
    } catch (err) {
      alert(`Error applying mapping: ${err.message}`);
    }
  });
}

// Pattern mapping functions (Task 4.4)

// Use strict full-string anchors for validation regexes
function validateMappingPattern(pattern) {
  if (typeof pattern !== 'string') return false;
  // Explicitly reject newlines for strict end-of-string matching in JavaScript
  if (pattern.includes('\n') || pattern.includes('\r')) return false;
  const patternRegex = /^(sequential|controller_sequential|pci_sequential)$/;
  return patternRegex.test(pattern);
}

// Use strict full-string anchors for validation regexes
function validateStartBay(startBay) {
  const startBayNum = parseInt(startBay, 10);
  if (isNaN(startBayNum) || startBayNum < 0 || startBayNum > 127) {
    return false;
  }
  return true;
}

// Use strict full-string anchors for validation regexes
function validateDeviceFilter(filter) {
  if (typeof filter !== 'string') return false;
  // Explicitly reject newlines for strict end-of-string matching in JavaScript
  if (filter.includes('\n') || filter.includes('\r')) return false;
  const filterRegex = /^(all|sas_sata|nvme)$/;
  return filterRegex.test(filter);
}

// Rule #9: Device Path Validation - strict regex whitelist
function validateDevicePath(devicePath) {
  if (typeof devicePath !== 'string') return false;
  // Explicitly reject newlines for strict end-of-string matching in JavaScript
  if (devicePath.includes('\n') || devicePath.includes('\r')) return false;
  // Whitelist for Linux device paths with limited depth for DoS prevention:
  // /dev/sd[a-z]+[0-9]*, /dev/nvme[0-9]+n[0-9]+, /dev/bus/usb/* (max 6 segments), /dev/sg[0-9]+, /dev/hd[a-z]+[0-9]*
  const devicePathRegex = /^\/dev\/(sd[a-z]+[0-9]*|nvme[0-9]+n[0-9]+|bus\/usb[0-9]+(?:\/[0-9]+){0,5}|sg[0-9]+|hd[a-z]+[0-9]*)$/;
  return devicePathRegex.test(devicePath);
}

// Comprehensive mapping validation (Task 4.8)
function validateMapping(mapping) {
  const errors = [];

  if (!mapping || typeof mapping !== 'object') {
    errors.push('Mapping is not a valid object');
    return { valid: false, errors };
  }

  const mappingKeys = Object.keys(mapping);

  // Rule #5: DoS prevention - limit mapping size
  if (mappingKeys.length === 0) {
    errors.push('Mapping is empty');
  }
  if (mappingKeys.length > 128) {
    errors.push('Mapping exceeds maximum of 128 bays');
  }

  // Rule #4: Check for duplicate device paths using proper object comparison
  const devicePathSet = new Set();
  const duplicatePaths = [];
  mappingKeys.forEach(bayId => {
    const device = mapping[bayId];
    if (device && device.device_path) {
      // Validate device path format (Rule #9, #15)
      if (!validateDevicePath(device.device_path)) {
        errors.push(`Invalid device path for ${bayId}: ${device.device_path}`);
      }
      // Check for duplicates
      if (devicePathSet.has(device.device_path)) {
        duplicatePaths.push(device.device_path);
      }
      devicePathSet.add(device.device_path);
    }
  });

  if (duplicatePaths.length > 0) {
    errors.push(`Duplicate device paths detected: ${duplicatePaths.join(', ')}`);
  }

  // Validate bay IDs exist in localBayMapCopy
  if (localBayMapCopy && typeof localBayMapCopy === 'object') {
    const missingBays = mappingKeys.filter(bayId => !(bayId in localBayMapCopy));
    if (missingBays.length > 0) {
      errors.push(`Bays do not exist in configuration: ${missingBays.join(', ')}`);
    }
  }

  // Rule #15: Validate bay ID format (strict regex with explicit newline rejection)
  const invalidBayIds = mappingKeys.filter(bayId => {
    if (typeof bayId !== 'string') return true;
    if (bayId.includes('\n') || bayId.includes('\r')) return true;
    // Expected format: bay followed by number (e.g., bay0, bay1, bay127)
    const bayIdRegex = /^bay[0-9]+$/;
    return !bayIdRegex.test(bayId);
  });

  if (invalidBayIds.length > 0) {
    errors.push(`Invalid bay ID format: ${invalidBayIds.join(', ')}`);
  }

  return {
    valid: errors.length === 0,
    errors
  };
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

// Undo functionality (Task 4.8)
function savePreviousBayMapState() {
  // Rule #4: Deep copy to prevent reference sharing
  if (!localBayMapCopy || typeof localBayMapCopy !== 'object') {
    previousBayMapState = null;
    return;
  }
  
  previousBayMapState = {};
  Object.keys(localBayMapCopy).forEach(bayId => {
    const conf = localBayMapCopy[bayId];
    if (conf) {
      previousBayMapState[bayId] = {
        role: conf.role,
        locked: conf.locked,
        label: conf.label,
        type: conf.type,
        by_path: conf.by_path,
        by_path_nvme: conf.by_path_nvme,
        display_number: conf.display_number,
        physical_position: conf.physical_position
      };
    }
  });
}

function restorePreviousBayMapState() {
  if (!previousBayMapState || typeof previousBayMapState !== 'object') {
    alert('No previous state to restore');
    return;
  }

  // Restore the previous state
  localBayMapCopy = {};
  Object.keys(previousBayMapState).forEach(bayId => {
    const conf = previousBayMapState[bayId];
    if (conf) {
      localBayMapCopy[bayId] = {
        role: conf.role,
        locked: conf.locked,
        label: conf.label,
        type: conf.type,
        by_path: conf.by_path,
        by_path_nvme: conf.by_path_nvme,
        display_number: conf.display_number,
        physical_position: conf.physical_position
      };
    }
  });

  // Clear undo state after restore (Rule #26: complete security - don't leave stale state)
  previousBayMapState = null;
  undoMappingBtn.disabled = true;

  // Re-render the bay mapping
  renderBayMappingConfig();
  showUnsavedChangesIndicator();
  hideMappingValidationError();
}

function flattenDevices(devicesByType, filter = 'all') {
  const flattened = [];

  for (const [type, devices] of Object.entries(devicesByType)) {
    if (!Array.isArray(devices)) continue;

    // Apply device filter
    if (filter === 'sas_sata' && type === 'nvme') continue;
    if (filter === 'nvme' && type !== 'nvme') continue;

    devices.forEach(device => {
      if (device && device.device_path) {
        // Rule #9: Validate device path against whitelist before using
        if (!validateDevicePath(device.device_path)) {
          console.warn(`Skipping invalid device path: ${device.device_path}`);
          return;
        }
        flattened.push({
          device_path: device.device_path,
          device_name: device.device_name || 'Unknown',
          controller_pci: device.controller_pci || 'Unknown',
          type: type
        });
      }
    });
  }

  return flattened;
}

function applySequentialPattern(devices, startBay) {
  const mapping = {};
  let bayNum = startBay;
  
  devices.forEach(device => {
    const bayId = `bay${bayNum}`;
    mapping[bayId] = {
      device_path: device.device_path,
      device_name: device.device_name,
      controller_pci: device.controller_pci
    };
    bayNum++;
  });
  
  return mapping;
}

function applyControllerSequentialPattern(devices, startBay) {
  // Group devices by controller PCI address
  const controllerGroups = {};
  devices.forEach(device => {
    const pci = device.controller_pci || 'unknown';
    if (!controllerGroups[pci]) {
      controllerGroups[pci] = [];
    }
    controllerGroups[pci].push(device);
  });
  
  // Sort groups by PCI address for deterministic ordering (Rule #4: consistent ordering)
  const sortedPciKeys = Object.keys(controllerGroups).sort();
  
  const mapping = {};
  let bayNum = startBay;
  
  sortedPciKeys.forEach(pci => {
    const groupDevices = controllerGroups[pci];
    // Sort devices within each group by device_path for deterministic ordering
    groupDevices.sort((a, b) => (a.device_path || '').localeCompare(b.device_path || ''));
    
    groupDevices.forEach(device => {
      const bayId = `bay${bayNum}`;
      mapping[bayId] = {
        device_path: device.device_path,
        device_name: device.device_name,
        controller_pci: device.controller_pci
      };
      bayNum++;
    });
  });
  
  return mapping;
}

function applyPciSequentialPattern(devices, startBay) {
  // Group by PCI address prefix (bus:device.function prefix)
  const pciGroups = {};
  devices.forEach(device => {
    const pciAddr = device.controller_pci || 'unknown';
    const prefix = pciAddr.substring(0, pciAddr.lastIndexOf('.')) || pciAddr;
    if (!pciGroups[prefix]) {
      pciGroups[prefix] = [];
    }
    pciGroups[prefix].push(device);
  });
  
  // Sort groups by PCI prefix for deterministic ordering (Rule #4: consistent ordering)
  const sortedPciPrefixes = Object.keys(pciGroups).sort();
  
  const mapping = {};
  let bayNum = startBay;
  
  sortedPciPrefixes.forEach(prefix => {
    const groupDevices = pciGroups[prefix];
    // Sort devices within each group by device_path for deterministic ordering
    groupDevices.sort((a, b) => (a.device_path || '').localeCompare(b.device_path || ''));
    
    groupDevices.forEach(device => {
      const bayId = `bay${bayNum}`;
      mapping[bayId] = {
        device_path: device.device_path,
        device_name: device.device_name,
        controller_pci: device.controller_pci
      };
      bayNum++;
    });
  });
  
  return mapping;
}

function generateMappingPreview() {
  // Clear previous validation errors
  hideMappingValidationError();
  
  if (!discoveryState.devicesByType || Object.keys(discoveryState.devicesByType).length === 0) {
    mappingPreview.innerHTML = '<div style="color: var(--color-danger);">No devices discovered. Click "Discover Slots" first.</div>';
    mappingPreview.style.display = 'block';
    return null;
  }
  
  const pattern = mappingPattern.value;
  const startBay = parseInt(mappingStartBay.value, 10);
  const filter = mappingDeviceFilter.value;
  
  // Validate inputs
  if (!validateMappingPattern(pattern)) {
    mappingPreview.innerHTML = '<div style="color: var(--color-danger);">Invalid mapping pattern selected.</div>';
    mappingPreview.style.display = 'block';
    return null;
  }
  
  if (!validateStartBay(startBay)) {
    mappingPreview.innerHTML = '<div style="color: var(--color-danger);">Starting bay must be between 0 and 127.</div>';
    mappingPreview.style.display = 'block';
    return null;
  }
  
  if (!validateDeviceFilter(filter)) {
    mappingPreview.innerHTML = '<div style="color: var(--color-danger);">Invalid device filter selected.</div>';
    mappingPreview.style.display = 'block';
    return null;
  }
  
  // Flatten devices with filter
  const devices = flattenDevices(discoveryState.devicesByType, filter);
  
  if (devices.length === 0) {
    mappingPreview.innerHTML = '<div style="color: var(--color-warning);">No devices match the selected filter.</div>';
    mappingPreview.style.display = 'block';
    return null;
  }
  
  // Apply pattern
  let mapping;
  switch (pattern) {
    case 'sequential':
      mapping = applySequentialPattern(devices, startBay);
      break;
    case 'controller_sequential':
      mapping = applyControllerSequentialPattern(devices, startBay);
      break;
    case 'pci_sequential':
      mapping = applyPciSequentialPattern(devices, startBay);
      break;
    default:
      mappingPreview.innerHTML = '<div style="color: var(--color-danger);">Unknown pattern type.</div>';
      mappingPreview.style.display = 'block';
      return null;
  }
  
  // Comprehensive validation (Task 4.8)
  const validation = validateMapping(mapping);
  if (!validation.valid) {
    showMappingValidationError(validation.errors.join('; '));
    mappingPreview.innerHTML = '<div style="color: var(--color-danger);">Mapping validation failed. See error message above.</div>';
    mappingPreview.style.display = 'block';
    applyMappingBtn.disabled = true;
    return null;
  }
  
  // Render preview (Rule #5: DoS prevention - limit preview size)
  const mappingKeys = Object.keys(mapping);
  if (mappingKeys.length > 128) {
    mappingPreview.innerHTML = '<div style="color: var(--color-danger);">Mapping exceeds maximum of 128 bays.</div>';
    mappingPreview.style.display = 'block';
    return null;
  }
  
  let html = `<div style="font-size: 0.75rem; color: #888; margin-bottom: 8px;">${mappingKeys.length} device(s) will be mapped:</div>`;
  html += mappingKeys.slice(0, 100).map(bayId => {
    const device = mapping[bayId];
    return `
      <div style="padding: 4px; background: #333; border-radius: 2px; margin-bottom: 4px; font-size: 0.75rem;">
        <strong style="color: var(--color-primary);">${escapeHtml(bayId)}</strong> → ${escapeHtml(device.device_name)} (${escapeHtml(device.device_path)})
      </div>
    `;
  }).join('');
  
  if (mappingKeys.length > 100) {
    html += `<div style="color: #888; font-size: 0.7rem; margin-top: 4px;">... and ${mappingKeys.length - 100} more</div>`;
  }
  
  mappingPreview.innerHTML = html;
  mappingPreview.style.display = 'block';
  
  currentMappingPreview = mapping;
  applyMappingBtn.disabled = false;
  
  return mapping;
}

async function applyMappingToBayConfig() {
  // Clear previous validation errors
  hideMappingValidationError();
  
  if (!currentMappingPreview || Object.keys(currentMappingPreview).length === 0) {
    alert('No valid mapping to apply. Generate a preview first.');
    return;
  }

  // Comprehensive validation before applying (Task 4.8)
  const validation = validateMapping(currentMappingPreview);
  if (!validation.valid) {
    showMappingValidationError(validation.errors.join('; '));
    return;
  }

  // Save previous state for undo (Task 4.8)
  savePreviousBayMapState();

  try {
    const response = await safeFetch('/api/admin/apply-slot-mapping', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(currentMappingPreview)
    });

    const result = await response.json();

    if (!response.ok) {
      const errorMsg = result.error || 'Failed to apply mapping';
      const details = Array.isArray(result.details) ? `\nDetails: ${result.details.join(', ')}` : '';
      showMappingValidationError(`${errorMsg}${details}`);
      // Restore previous state on error (Rule #26: complete security)
      restorePreviousBayMapState();
      return;
    }

    // Enable undo button after successful apply (Task 4.8)
    undoMappingBtn.disabled = false;
    
    // Reload bay map from backend to get updated state
    await loadBayMappingConfig();
    closeDiscoveryModal();
    alert(`Mapping applied successfully to ${result.updated_bays} bay(s).`);
  } catch (error) {
    console.error('Error applying slot mapping:', error);
    showMappingValidationError('Failed to apply mapping. Please try again.');
    // Restore previous state on error (Rule #26: complete security)
    restorePreviousBayMapState();
  }
}

// Modify applyMappingToBayConfig to handle manual mappings
const originalApplyMappingToBayConfig = applyMappingToBayConfig;
applyMappingToBayConfig = async function() {
  if (mappingMode === 'manual') {
    if (Object.keys(manualMappings).length === 0) {
      alert('No manual mappings to apply.');
      return;
    }
    currentMappingPreview = manualMappings;
  }

  // Call original function
  await originalApplyMappingToBayConfig();
};

// Manual mapping functions (Task 4.5)

function setMappingMode(mode) {
  mappingMode = mode;
  if (mode === 'pattern') {
    patternModeBtn.style.background = 'var(--color-primary)';
    manualModeBtn.style.background = '';
    patternMappingControls.style.display = 'block';
    manualMappingControls.style.display = 'none';
    currentMappingPreview = null;
    applyMappingBtn.disabled = true;
  } else {
    patternModeBtn.style.background = '';
    manualModeBtn.style.background = 'var(--color-primary)';
    patternMappingControls.style.display = 'none';
    manualMappingControls.style.display = 'block';
    currentMappingPreview = null;
    applyMappingBtn.disabled = Object.keys(manualMappings).length === 0;
    renderAvailableDevices();
  }
}

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

  const sortedBayKeys = Object.keys(localBayMapCopy).sort((a, b) => {
    const numA = parseInt(a.replace(/\D/g, ""), 10) || 0;
    const numB = parseInt(b.replace(/\D/g, ""), 10) || 0;
    return numA - numB;
  });

  sortedBayKeys.forEach(bayId => {
    const option = document.createElement('option');
    option.value = bayId;
    option.textContent = `${bayId} (${localBayMapCopy[bayId]?.label || bayId})`;
    manualBaySelect.appendChild(option);
  });
}

function filterDevices(devices, searchTerm, filterType) {
  const term = searchTerm.toLowerCase().trim();
  
  return devices.filter(device => {
    // Apply type filter
    if (filterType === 'sas_sata' && device.type === 'nvme') return false;
    if (filterType === 'nvme' && device.type !== 'nvme') return false;
    
    // Apply search term filter (Rule #5: DoS prevention - limit search complexity)
    if (term === '') return true;
    
    const path = (device.device_path || '').toLowerCase();
    const name = (device.device_name || '').toLowerCase();
    const controller = (device.controller_pci || '').toLowerCase();
    const model = (device.smart?.model || '').toLowerCase();
    const serial = (device.smart?.serial || '').toLowerCase();
    
    return path.includes(term) || name.includes(term) || controller.includes(term) ||
           model.includes(term) || serial.includes(term);
  });
}

function renderAvailableDevices() {
  if (!discoveryState.devicesByType || Object.keys(discoveryState.devicesByType).length === 0) {
    availableDevicesList.innerHTML = '<div style="color: #666; font-style: italic; font-size: 0.75rem;">No devices discovered. Click "Discover Slots" first.</div>';
    return;
  }

  const searchTerm = deviceSearchInput.value;
  const filterType = manualDeviceFilter.value;
  const allDevices = flattenDevices(discoveryState.devicesByType, 'all');
  const filteredDevices = filterDevices(allDevices, searchTerm, filterType);

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
        selectedDevice = device;
        updateSelectedDeviceInfo();
        renderAvailableDevices();
      }
    });
  });
}

function updateSelectedDeviceInfo() {
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

function addManualMapping() {
  // Clear previous validation errors
  hideMappingValidationError();
  
  if (!selectedDevice) {
    alert('Please select a device first.');
    return;
  }

  const bayId = manualBaySelect.value;
  if (!bayId) {
    alert('Please select a bay.');
    return;
  }

  // Validate device path (Rule #9, #15)
  if (!validateDevicePath(selectedDevice.device_path)) {
    showMappingValidationError(`Invalid device path: ${selectedDevice.device_path}`);
    return;
  }

  // Check if device is already mapped
  const existingMapping = Object.entries(manualMappings).find(([_, m]) => m.device_path === selectedDevice.device_path);
  if (existingMapping) {
    showMappingValidationError(`Device is already mapped to ${existingMapping[0]}. Remove that mapping first.`);
    return;
  }

  // Validate bay ID format (Rule #15)
  if (typeof bayId !== 'string' || bayId.includes('\n') || bayId.includes('\r')) {
    showMappingValidationError('Invalid bay ID format');
    return;
  }
  const bayIdRegex = /^bay[0-9]+$/;
  if (!bayIdRegex.test(bayId)) {
    showMappingValidationError(`Invalid bay ID format: ${bayId}`);
    return;
  }

  // Add mapping
  manualMappings[bayId] = {
    device_path: selectedDevice.device_path,
    device_name: selectedDevice.device_name,
    controller_pci: selectedDevice.controller_pci,
    type: selectedDevice.type
  };

  // Clear selection
  selectedDevice = null;
  updateSelectedDeviceInfo();
  renderAvailableDevices();
  renderManualMappingPreview();
  applyMappingBtn.disabled = false;
}

function clearManualMappings() {
  if (Object.keys(manualMappings).length === 0) {
    return;
  }

  if (confirm('Are you sure you want to clear all manual mappings?')) {
    manualMappings = {};
    selectedDevice = null;
    updateSelectedDeviceInfo();
    renderAvailableDevices();
    renderManualMappingPreview();
    applyMappingBtn.disabled = true;
  }
}

function removeManualMapping(bayId) {
  delete manualMappings[bayId];
  renderAvailableDevices();
  renderManualMappingPreview();
  applyMappingBtn.disabled = Object.keys(manualMappings).length === 0;
}

function renderManualMappingPreview() {
  const mappingKeys = Object.keys(manualMappings).sort((a, b) => {
    const numA = parseInt(a.replace(/\D/g, ""), 10) || 0;
    const numB = parseInt(b.replace(/\D/g, ""), 10) || 0;
    return numA - numB;
  });

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
      removeManualMapping(bayId);
    });
  });
}

// Manual mapping event listeners (Task 4.5)
patternModeBtn.addEventListener('click', () => setMappingMode('pattern'));
manualModeBtn.addEventListener('click', () => setMappingMode('manual'));
deviceSearchInput.addEventListener('input', () => renderAvailableDevices());
manualDeviceFilter.addEventListener('change', () => renderAvailableDevices());
addManualMappingBtn.addEventListener('click', addManualMapping);
clearManualMappingsBtn.addEventListener('click', clearManualMappings);

// Undo button event listener (Task 4.8)
if (undoMappingBtn) {
  undoMappingBtn.addEventListener('click', () => {
    if (confirm('Are you sure you want to undo the last mapping change? This will restore the previous bay mapping state.')) {
      restorePreviousBayMapState();
    }
  });
}
// --- END OF FILE frontend/admin/discoveryModal.js ---
