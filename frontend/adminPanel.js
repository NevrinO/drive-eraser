// --- START OF FILE frontend/adminPanel.js ---
// Admin panel and bay mapping configuration

// These elements are defined in the main app.js file
const testWebhookBtn = document.getElementById("testWebhookBtn");
const webhookTestResult = document.getElementById("webhookTestResult");
const exportCsvBtn = document.getElementById("exportCsvBtn");
const downloadBundleBtn = document.getElementById("downloadBundleBtn");
const bayMappingContainer = document.getElementById("bayMappingContainer");
const saveBayMapBtn = document.getElementById("saveBayMapBtn");
const saveBayMapBtnTop = document.getElementById("saveBayMapBtnTop");
const addBayBtn = document.getElementById("addBayBtn");
const layoutTemplateSelect = document.getElementById("layoutTemplateSelect");
const traversalPresetSelect = document.getElementById("traversalPresetSelect");
const applyLayoutTemplateBtn = document.getElementById("applyLayoutTemplateBtn");
const bayLayoutStatus = document.getElementById("bayLayoutStatus");
const unsavedChangesIndicator = document.getElementById("unsavedChangesIndicator");
const metricDiskBar = document.getElementById("metricDiskBar");
const metricDiskText = document.getElementById("metricDiskText");
const metricRamBar = document.getElementById("metricRamBar");
const metricRamText = document.getElementById("metricRamText");
const metricCpuBar = document.getElementById("metricCpuBar");
const metricCpuText = document.getElementById("metricCpuText");
const metricUptimeText = document.getElementById("metricUptimeText");

if (!testWebhookBtn || !webhookTestResult || !exportCsvBtn || !downloadBundleBtn ||
    !bayMappingContainer || !saveBayMapBtn || !addBayBtn ||
    !layoutTemplateSelect || !traversalPresetSelect || !applyLayoutTemplateBtn ||
    !metricDiskBar || !metricDiskText || !metricRamBar || !metricRamText ||
    !metricCpuBar || !metricCpuText || !metricUptimeText) {
  console.error("Critical: One or more admin panel elements not found in DOM");
}

async function loadAdminMetrics() {
  const adminTab = document.querySelector('[data-tab="adminPanel"]');
  if (!adminTab || !adminTab.classList.contains("active")) return;
  
  try {
    const response = await safeFetch("/api/admin/metrics");
    if (!response.ok) throw new Error();
    let data;
    try {
      data = await response.json();
    } catch (e) {
      console.error("Failed to parse admin metrics JSON:", e);
      throw new Error("Invalid JSON response from metrics API");
    }
    
    metricDiskBar.style.width = `${data.disk_pct}%`;
    metricDiskText.textContent = `${data.disk_pct}% (${data.disk_str})`;
    
    metricRamBar.style.width = `${data.ram_pct}%`;
    metricRamText.textContent = `${data.ram_pct}%`;
    
    metricCpuBar.style.width = `${data.cpu_pct}%`;
    metricCpuText.textContent = `${data.cpu_pct}%`;
    
    metricUptimeText.textContent = data.uptime;
    
    const ipLabel = document.getElementById("metricIpText");
    if (ipLabel) {
      ipLabel.textContent = data.ip_address || "Unknown";
    }
  } catch (err) {
    // Suppress background poll failures quietly
  }
}

testWebhookBtn.addEventListener("click", async () => {
  testWebhookBtn.disabled = true;
  testWebhookBtn.textContent = "Testing...";
  webhookTestResult.classList.add("hidden");
  
  try {
    const response = await safeFetch("/api/admin/test-webhook", { method: "POST" });
    let data;
    try {
      data = await response.json();
    } catch (e) {
      console.error("Failed to parse webhook test response JSON:", e);
      webhookTestResult.classList.remove("hidden");
      webhookTestResult.className = "test-result-label test-result-error";
      webhookTestResult.textContent = "Error: Invalid server response";
      return;
    }
    webhookTestResult.classList.remove("hidden");
    if (response.ok) {
      webhookTestResult.className = "test-result-label test-result-success";
      webhookTestResult.textContent = data.message || "Test Notification Sent!";
    } else {
      webhookTestResult.className = "test-result-label test-result-error";
      webhookTestResult.textContent = `Failure: ${data.error || "Unknown response"}`;
    }
  } catch (err) {
    webhookTestResult.classList.remove("hidden");
    webhookTestResult.className = "test-result-label test-result-error";
    webhookTestResult.textContent = `Error: ${err.message}`;
  } finally {
    testWebhookBtn.disabled = false;
    testWebhookBtn.textContent = "Test Alert Notification";
  }
});

exportCsvBtn.addEventListener("click", () => {
  window.location.href = "/api/admin/export-csv";
});

downloadBundleBtn.addEventListener("click", () => {
  window.location.href = "/api/admin/support-bundle";
});

function showLayoutStatus(message, isError = false) {
  if (!bayLayoutStatus) return;
  bayLayoutStatus.classList.remove("hidden", "status-ok", "status-error");
  bayLayoutStatus.classList.add(isError ? "status-error" : "status-ok");
  bayLayoutStatus.textContent = message;
}

function showUnsavedChangesIndicator() {
  if (!unsavedChangesIndicator) return;
  unsavedChangesIndicator.classList.remove("hidden");
  hasUnsavedBayMapChanges = true;
}

function hideUnsavedChangesIndicator() {
  if (!unsavedChangesIndicator) return;
  unsavedChangesIndicator.classList.add("hidden");
  hasUnsavedBayMapChanges = false;
}

async function loadLayoutTemplates() {
  const response = await safeFetch("/api/admin/layout-templates");
  if (!response.ok) throw new Error("Failed to load layout templates");
  let data;
  try {
    data = await response.json();
  } catch (e) {
    console.error("Failed to parse layout templates JSON:", e);
    availableLayoutTemplates = [];
    return;
  }
  availableLayoutTemplates = Array.isArray(data.templates) ? data.templates : [];

  if (layoutTemplateSelect) {
    const currentValue = layoutTemplateSelect.value;
    layoutTemplateSelect.innerHTML = '<option value="">-- Select Template --</option>';
    availableLayoutTemplates.forEach((template) => {
      const option = document.createElement("option");
      option.value = template.id;
      option.textContent = `${template.name} (${template.vendor || "Generic"})`;
      layoutTemplateSelect.appendChild(option);
    });
    if (currentValue && Array.from(layoutTemplateSelect.options).some(opt => opt.value === currentValue)) {
      layoutTemplateSelect.value = currentValue;
    }
  }
}

function applyLayoutMetadataToControls() {
  if (layoutTemplateSelect && localLayoutMetadata.template_id) {
    if (layoutTemplateSelect.value !== localLayoutMetadata.template_id) {
      layoutTemplateSelect.value = localLayoutMetadata.template_id;
    }
  }
  if (traversalPresetSelect) {
    traversalPresetSelect.value = localLayoutMetadata.traversal_preset || "top_left_down_then_across";
  }
}

async function fetchCurrentBayMapDocument() {
  const response = await safeFetch("/api/admin/bay-map");
  if (!response.ok) throw new Error("Failed to load bay map");
  let payload;
  try {
    payload = await response.json();
  } catch (e) {
    console.error("Failed to parse bay map JSON:", e);
    throw new Error("Invalid JSON response from bay map API");
  }

  if (payload && payload.bays && typeof payload.bays === "object") {
    return {
      bays: payload.bays,
      layout_metadata: payload.layout_metadata || {}
    };
  }

  const bays = {};
  Object.keys(payload || {}).forEach((key) => {
    const val = payload[key];
    if (key !== "layout_metadata" && val && typeof val === "object") {
      bays[key] = val;
    }
  });

  return {
    bays,
    layout_metadata: payload?.layout_metadata || {}
  };
}

async function loadBayMappingConfig() {
  try {
    await loadLayoutTemplates();
    const bayMapDoc = await fetchCurrentBayMapDocument();
    localLayoutMetadata = bayMapDoc.layout_metadata || {};
    applyLayoutMetadataToControls();
    hideUnsavedChangesIndicator();

    const unmappedResponse = await safeFetch("/api/admin/unmapped-drives");
    if (!unmappedResponse.ok) throw new Error();
    let unmappedDrives;
    try {
      unmappedDrives = await unmappedResponse.json();
    } catch (e) {
      console.error("Failed to parse unmapped drives JSON:", e);
      unmappedDrives = [];
    }

    bayMappingContainer.innerHTML = "";

    localBayMapCopy = {};
    Object.keys(bayMapDoc.bays || {}).forEach((bayId) => {
      const conf = bayMapDoc.bays[bayId] || {};
      localBayMapCopy[bayId] = {
        role: conf.role,
        locked: conf.locked,
        label: conf.label,
        type: conf.type || "sas_sata",
        by_path: conf.by_path || "",
        by_path_nvme: conf.by_path_nvme || "",
        display_number: conf.display_number || "",
        physical_position: conf.physical_position || null
      };
    });

    if (Object.keys(localBayMapCopy).length === 0) {
      currentDrives.forEach(drive => {
        let localType = drive.type;
        if (!localType) {
            localType = (drive.interface_type === "nvme") ? "u2" : "sas_sata";
        }

        localBayMapCopy[drive.bay] = {
          role: drive.role,
          locked: drive.locked,
          label: drive.label,
          type: localType,
          by_path: drive.configured_by_path || "",
          by_path_nvme: drive.configured_by_path_nvme || "",
          display_number: drive.display_number || "",
          physical_position: drive.physical_position || null
        };
      });
    }

    const sortedBayKeys = Object.keys(localBayMapCopy).sort((a, b) => {
      const numA = parseInt(a.replace(/\D/g, ""), 10) || 0;
      const numB = parseInt(b.replace(/\D/g, ""), 10) || 0;
      return numA - numB;
    });

    sortedBayKeys.forEach(bayKey => {
      const conf = localBayMapCopy[bayKey];
      if (!conf) return;

      const rowElement = renderBayConfigurationRow(bayKey, conf, unmappedDrives);
      bayMappingContainer.appendChild(rowElement);
    });

    bindDeleteBayButtons();
  } catch (err) {
    bayMappingContainer.innerHTML = `<div style="color: var(--color-danger); font-size: 0.8rem; padding: 12px;">Failed to load mapping configurations: ${err.message}</div>`;
  }
}

async function renderBayMappingConfig() {
  try {
    const unmappedResponse = await safeFetch("/api/admin/unmapped-drives");
    if (!unmappedResponse.ok) throw new Error();
    let unmappedDrives;
    try {
      unmappedDrives = await unmappedResponse.json();
    } catch (e) {
      console.error("Failed to parse unmapped drives JSON:", e);
      unmappedDrives = [];
    }

    bayMappingContainer.innerHTML = "";

    const sortedBayKeys = Object.keys(localBayMapCopy).sort((a, b) => {
      const numA = parseInt(a.replace(/\D/g, ""), 10) || 0;
      const numB = parseInt(b.replace(/\D/g, ""), 10) || 0;
      return numA - numB;
    });

    sortedBayKeys.forEach(bayKey => {
      const conf = localBayMapCopy[bayKey];
      if (!conf) return;

      const rowElement = renderBayConfigurationRow(bayKey, conf, unmappedDrives);
      bayMappingContainer.appendChild(rowElement);
    });

    bindDeleteBayButtons();
  } catch (err) {
    bayMappingContainer.innerHTML = `<div style="color: var(--color-danger); font-size: 0.8rem; padding: 12px;">Failed to render mapping configurations: ${err.message}</div>`;
  }
}

document.getElementById('btn-auto-detect').addEventListener('click', async () => {
    if (!confirm("Are you sure you want to scan and auto-detect your physical SAS/SATA backplane bays? This will match any populated slots with your config automatically.")) {
        return;
    }
    
    try {
        const response = await fetch('/api/admin/auto-detect-bays', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        let data;
        try {
            data = await response.json();
        } catch (e) {
            console.error("Failed to parse auto-detect response JSON:", e);
            alert("Error: Invalid server response");
            return;
        }
        if (response.ok) {
            alert(`Success! ${data.message}`);
            location.reload();
        } else {
            alert(`Error running scan: ${data.error}`);
        }
    } catch (err) {
        alert(`Failed to communicate with backplane auto-detector: ${err}`);
    }
});

function populatePathDropdown(selectElement, unmappedDrives, currentValue, filterType) {
    selectElement.innerHTML = '<option value="">-- Select Drive Path (Empty Slot) --</option>';
    
    if (currentValue) {
        const opt = document.createElement('option');
        opt.value = currentValue;
        opt.textContent = `${currentValue} (Current)`;
        opt.selected = true;
        selectElement.appendChild(opt);
    }

    unmappedDrives.forEach(drive => {
        if (drive.by_path === currentValue) return;

        const isNvme = drive.by_path.includes("nvme") || drive.device.includes("nvme");
        
        if (filterType === "nvme" && !isNvme) return;
        if (filterType === "sas_sata" && isNvme) return;

        const opt = document.createElement('option');
        opt.value = drive.by_path;
        opt.textContent = `${drive.by_path} [${drive.model} S/N: ${drive.serial} - ${drive.capacity_str}]`;
        selectElement.appendChild(opt);
    });
}

function renderBayConfigurationRow(bayId, bayConfig, unmappedDrives) {
    const container = document.createElement('div');
    container.className = 'bay-config-row';
    container.id = `config-row-${bayId}`;
    container.style.marginBottom = "20px";
    
    const isU2 = bayConfig.type === 'u2';
    const lockStatusText = bayConfig.locked ? "Locked" : "Editable";
    const hasOverride = !!String(bayConfig.display_number || "").trim();
    
    const deleteBtnHtml = bayConfig.locked ? "" : `
        <button type="button" class="btn-delete-bay" data-delete-bay-id="${escapeHtml(bayId)}" style="padding: 4px 10px; font-size: 0.7rem; background: var(--color-danger); border-color: var(--color-danger); margin-left: 12px; color: #fff;">
          Delete
        </button>
    `;
    
    container.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
            <h3 style="margin: 0; font-size: 1rem; color: var(--color-primary);">${escapeHtml(bayConfig.label || bayId)}</h3>
            <div style="display: flex; align-items: center;">
                <small style="font-size: 0.7rem; color: #888;">${lockStatusText}</small>
                ${deleteBtnHtml}
            </div>
        </div>

        <div class="form-group" style="margin-bottom: 8px; display: grid; grid-template-columns: 180px 1fr 1fr; gap: 8px; align-items: end;">
            <label style="font-size: 0.8rem; font-weight: bold; display: block; margin-bottom: 4px;">Display Number</label>
            <input id="display-number-${bayId}" class="display-number-input" data-bay="${bayId}" type="text" value="${escapeHtml(bayConfig.display_number || "")}" ${hasOverride ? "" : "disabled"} style="width: 100%; padding: 6px; background: #222; border: 1px solid #444; color: #fff;" />
            <label style="font-size: 0.75rem; color: #aaa; text-transform: none; letter-spacing: 0; display: flex; align-items: center; gap: 6px;">
              <input id="override-number-${bayId}" class="override-number-toggle" data-bay="${bayId}" type="checkbox" ${hasOverride ? "checked" : ""} />
              Manual Override
            </label>
        </div>
        
        <div class="form-group" style="margin-bottom: 8px;">
            <label style="font-size: 0.8rem; font-weight: bold; display: block; margin-bottom: 4px;">Drive Interface Type</label>
            <select id="type-${bayId}" class="bay-type-selector" data-bay="${bayId}" style="width: 100%; padding: 6px; background: #222; border: 1px solid #444; color: #fff;">
                <option value="sas_sata" ${!isU2 ? 'selected' : ''}>SAS / SATA</option>
                <option value="u2" ${isU2 ? 'selected' : ''}>U.2 / U.3 / Hybrid (NVMe capable)</option>
            </select>
        </div>

        <div class="form-group" style="margin-bottom: 8px;">
            <label id="primary-label-${bayId}" style="font-size: 0.8rem; font-weight: bold; display: block; margin-bottom: 4px;">Primary SAS/SATA Controller Port Path</label>
            <select id="path-${bayId}" class="by-path-select" data-bay="${bayId}" style="width: 100%; padding: 6px; background: #222; border: 1px solid #444; color: #fff;">
            </select>
        </div>

        <div class="form-group nvme-group" id="nvme-group-${bayId}" style="${isU2 ? 'display: block;' : 'display: none;'} margin-bottom: 8px;">
            <label style="font-size: 0.8rem; font-weight: bold; display: block; margin-bottom: 4px; color: #4a90e2;">Motherboard NVMe direct-attach Path (Optional)</label>
            <select id="path-nvme-${bayId}" class="by-path-nvme-select" data-bay="${bayId}" style="width: 100%; padding: 6px; background: #222; border: 1px solid #444; color: #fff;">
            </select>
        </div>
        <hr style="border: 0; border-top: 1px solid #333; margin: 16px 0;">
    `;

    const primarySelect = container.querySelector(`#path-${bayId}`);
    populatePathDropdown(primarySelect, unmappedDrives, bayConfig.by_path);

    const nvmeSelect = container.querySelector(`#path-nvme-${bayId}`);
    populatePathDropdown(nvmeSelect, unmappedDrives, bayConfig.by_path_nvme);

    const overrideToggle = container.querySelector(`#override-number-${bayId}`);
    const displayInput = container.querySelector(`#display-number-${bayId}`);
    overrideToggle.addEventListener("change", (e) => {
      displayInput.disabled = !e.target.checked;
      if (!e.target.checked) {
        displayInput.value = "";
      }
      showUnsavedChangesIndicator();
    });

    displayInput.addEventListener("input", () => {
      showUnsavedChangesIndicator();
    });

    const typeSelector = container.querySelector(`#type-${bayId}`);
    typeSelector.addEventListener('change', (e) => {
        const nvmeGroup = container.querySelector(`#nvme-group-${bayId}`);
        const primaryLabel = container.querySelector(`#primary-label-${bayId}`);

        if (e.target.value === 'u2') {
            nvmeGroup.style.display = 'block';
            primaryLabel.textContent = 'Primary SAS/SATA Controller Port Path (SATA Mode)';
        } else {
            nvmeGroup.style.display = 'none';
            primaryLabel.textContent = 'Primary SAS/SATA Controller Port Path';
            nvmeSelect.value = "";
        }
        showUnsavedChangesIndicator();
    });

    primarySelect.addEventListener("change", () => {
      showUnsavedChangesIndicator();
    });

    nvmeSelect.addEventListener("change", () => {
      showUnsavedChangesIndicator();
    });

    return container;
}

async function applyLayoutTemplate() {
  const templateId = layoutTemplateSelect?.value;
  const traversalPreset = traversalPresetSelect?.value;
  if (!templateId) {
    showLayoutStatus("Select a layout template first.", true);
    return;
  }

  const customOverrides = {};
  Object.keys(localBayMapCopy).forEach((bayId) => {
    const conf = localBayMapCopy[bayId] || {};
    if (conf.display_number && String(conf.display_number).trim() !== "") {
      customOverrides[bayId] = { display_number: String(conf.display_number).trim() };
    }
  });

  const response = await safeFetch("/api/admin/apply-template", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      template_id: templateId,
      traversal_preset: traversalPreset,
      custom_overrides: customOverrides
    })
  });

  let data;
  try {
    data = await response.json();
  } catch (e) {
    console.error("Failed to parse apply template response JSON:", e);
    throw new Error("Invalid JSON response from apply template API");
  }

  if (!response.ok) {
    throw new Error(data.error || "Template apply failed");
  }

  const bayMapDoc = data.bay_map || {};
  const newBays = bayMapDoc.bays || {};
  localLayoutMetadata = bayMapDoc.layout_metadata || {};
  localBayMapCopy = {};

  Object.keys(newBays).forEach((bayId) => {
    const conf = newBays[bayId] || {};
    localBayMapCopy[bayId] = {
      role: conf.role,
      locked: conf.locked,
      label: conf.label,
      type: conf.type || "sas_sata",
      by_path: conf.by_path || "",
      by_path_nvme: conf.by_path_nvme || "",
      display_number: conf.display_number || "",
      physical_position: conf.physical_position || null
    };
  });

  applyLayoutMetadataToControls();

  const existingDriveMap = {};
  currentDrives.forEach(drive => {
    existingDriveMap[drive.bay] = drive;
  });

  currentDrives = Object.keys(localBayMapCopy).map((bayId) => {
    const conf = localBayMapCopy[bayId];
    const existingDrive = existingDriveMap[bayId];
    if (existingDrive) {
      return {
        ...existingDrive,
        label: conf.label,
        role: conf.role,
        locked: conf.locked,
        display_number: conf.display_number,
        physical_position: conf.physical_position
      };
    } else {
      return {
        bay: bayId,
        label: conf.label,
        role: conf.role,
        locked: conf.locked,
        present: false,
        status: "EMPTY",
        interface_type: conf.type === "u2" ? "nvme" : "sata",
        capacity_str: "-",
        marker: { status: "none" },
        display_number: conf.display_number,
        physical_position: conf.physical_position
      };
    }
  });

  renderBays(currentDrives);
  await renderBayMappingConfig();
  showUnsavedChangesIndicator();
  showLayoutStatus(`Template applied: ${data.template?.name || templateId}`);
}

async function saveBayMappingConfiguration() {
    const updatedBayMap = {};
    const configRows = document.querySelectorAll('.bay-config-row');
    const customOverrides = {};
    const seenDisplayNumbers = new Set();

    configRows.forEach(row => {
        const typeSelector = row.querySelector('.bay-type-selector');
        const bayId = typeSelector.getAttribute('data-bay');

        const type = typeSelector.value;
        const primaryPath = row.querySelector('.by-path-select').value || null;

        let nvmePath = null;
        if (type === 'u2') {
            const nvmeSelect = row.querySelector('.by-path-nvme-select');
            nvmePath = (nvmeSelect && nvmeSelect.value) || null;
        }

        const overrideEnabled = row.querySelector('.override-number-toggle')?.checked;
        const displayInput = row.querySelector('.display-number-input');
        const displayNumber = overrideEnabled ? (displayInput?.value || "").trim() : "";
        if (displayNumber) {
            const dedupeKey = displayNumber.toLowerCase();
            if (seenDisplayNumbers.has(dedupeKey)) {
              throw new Error(`Duplicate display number: ${displayNumber}`);
            }
            seenDisplayNumbers.add(dedupeKey);
            customOverrides[bayId] = { display_number: displayNumber };
        }

        const labelNum = bayId.startsWith("bay") ? bayId.slice(3) : bayId;
        const defaultLabel = 'Work Bay ' + (labelNum || bayId);

        updatedBayMap[bayId] = {
            "role": localBayMapCopy[bayId]?.role || "wipe",
            "locked": localBayMapCopy[bayId]?.locked || false,
            "type": type,
            "label": localBayMapCopy[bayId]?.label || defaultLabel,
            "by_path": primaryPath,
            "by_path_nvme": nvmePath,
            "display_number": displayNumber || null,
            "physical_position": localBayMapCopy[bayId]?.physical_position || null
        };
    });

    if (Object.keys(updatedBayMap).length === 0 || Object.keys(updatedBayMap).length !== Object.keys(localBayMapCopy).length) {
        Object.keys(localBayMapCopy).forEach((bayId) => {
            const conf = localBayMapCopy[bayId];
            updatedBayMap[bayId] = {
                "role": conf.role,
                "locked": conf.locked,
                "type": conf.type,
                "label": conf.label,
                "by_path": conf.by_path || "",
                "by_path_nvme": conf.by_path_nvme || "",
                "display_number": conf.display_number || null,
                "physical_position": conf.physical_position || null
            };
        });
    }

    const payload = {
      layout_metadata: {
        template_id: layoutTemplateSelect?.value || localLayoutMetadata.template_id || null,
        traversal_preset: traversalPresetSelect?.value || localLayoutMetadata.traversal_preset || "top_left_down_then_across",
        custom_overrides: customOverrides
      },
      bays: updatedBayMap
    };

    const response = await safeFetch('/api/admin/save-bay-map', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });

    if (response.ok) {
        alert("Bay mapping successfully saved!");
        hideUnsavedChangesIndicator();
        await loadDrives();
        await loadBayMappingConfig();
    } else {
        let data;
        try {
            data = await response.json();
        } catch (e) {
            console.error("Failed to parse save bay map response JSON:", e);
            throw new Error("Invalid JSON response from save bay map API");
        }
        throw new Error(data.error || "Failed to save configuration");
    }
}

function bindDeleteBayButtons() {
  document.querySelectorAll(".btn-delete-bay").forEach(button => {
    button.addEventListener("click", (event) => {
      const bayId = event.target.getAttribute("data-delete-bay-id");
      
      if (Object.keys(localBayMapCopy).length <= 1) {
        alert("Delete Blocked: A minimum threshold of 1 active bay configuration is required.");
        return;
      }

      const drive = currentDrives.find(d => d.bay === bayId);
      if (drive && (String(drive.status).toUpperCase() === "RUNNING" || String(drive.status).toUpperCase() === "QUEUED")) {
        alert(`Delete Blocked: Cannot delete ${bayId.toUpperCase()} while an active or queued job is running on it.`);
        return;
      }

      const proceed = confirm(`Are you sure you want to stage the removal of ${bayId.toUpperCase()}?\n\nThis change takes effect only after you click 'Save Mapping Configuration'.`);
      if (!proceed) return;

      delete localBayMapCopy[bayId];
      selectedBays.delete(bayId);

      currentDrives = currentDrives.filter(d => d.bay !== bayId);
      renderBays(currentDrives);
      renderBayMappingConfig();
      showUnsavedChangesIndicator();
    });
  });
}

addBayBtn.addEventListener("click", () => {
  if (Object.keys(localBayMapCopy).length >= 128) {
    alert("Add Blocked: Maximum threshold of 128 active configurations has been reached.");
    return;
  }

  const label = prompt("Enter a descriptive label for the new physical bay:");
  if (label === null) return;

  const cleanLabel = label.trim() || "Work Bay Extension";
  const typeSelection = prompt("Enter Interface Slot Type ('sas_sata' or 'u2' for hybrid NVMe):", "sas_sata");
  if (typeSelection === null) return;

  const cleanType = typeSelection.trim().toLowerCase() === "u2" ? "u2" : "sas_sata";

  const bayKeys = Object.keys(localBayMapCopy);
  let highestNum = 0;
  bayKeys.forEach(k => {
    const num = parseInt(k.replace(/\D/g, ""), 10);
    if (!isNaN(num) && num > highestNum) {
      highestNum = num;
    }
  });

  const nextBayId = `bay${highestNum + 1}`;

  localBayMapCopy[nextBayId] = {
    role: "wipe",
    locked: false,
    label: cleanLabel,
    type: cleanType,
    by_path: "",
    by_path_nvme: ""
  };

  currentDrives.push({
    bay: nextBayId,
    label: cleanLabel,
    role: "wipe",
    locked: false,
    present: false,
    status: "EMPTY",
    interface_type: cleanType === "u2" ? "nvme" : "sata",
    capacity_str: "-",
    marker: { status: "none" }
  });

  renderBays(currentDrives);
  renderBayMappingConfig();
  showUnsavedChangesIndicator();
});

saveBayMapBtn.addEventListener("click", async () => {
  saveBayMapBtn.disabled = true;
  saveBayMapBtn.textContent = "Saving...";

  try {
    await saveBayMappingConfiguration();
  } catch (err) {
    alert(`Error: ${err.message}`);
  } finally {
    saveBayMapBtn.disabled = false;
    saveBayMapBtn.textContent = "Save Mapping Configuration";
  }
});

if (saveBayMapBtnTop) {
  saveBayMapBtnTop.addEventListener("click", async () => {
    saveBayMapBtnTop.disabled = true;
    saveBayMapBtnTop.textContent = "Saving...";

    try {
      await saveBayMappingConfiguration();
    } catch (err) {
      alert(`Error: ${err.message}`);
    } finally {
      saveBayMapBtnTop.disabled = false;
      saveBayMapBtnTop.textContent = "Save Mapping";
    }
  });
}

if (layoutTemplateSelect) {
  layoutTemplateSelect.addEventListener("change", () => {
    showUnsavedChangesIndicator();
  });
}

if (traversalPresetSelect) {
  traversalPresetSelect.addEventListener("change", () => {
    showUnsavedChangesIndicator();
  });
}

if (applyLayoutTemplateBtn) {
  applyLayoutTemplateBtn.addEventListener("click", async () => {
    try {
      await applyLayoutTemplate();
    } catch (err) {
      showLayoutStatus(err.message, true);
    }
  });
}
// --- END OF FILE frontend/adminPanel.js ---
