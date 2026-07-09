// --- START OF FILE frontend/admin/bayMapping.js ---
// Bay mapping configuration: load, render, save, add/delete bays, layout template application

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

// Update traversal dropdown when template is selected
function updateTraversalFromTemplate() {
  const templateId = layoutTemplateSelect?.value;
  if (!templateId || !traversalPresetSelect) return;

  const template = availableLayoutTemplates.find(t => t.id === templateId);
  if (template && template.traversal_preset) {
    traversalPresetSelect.value = template.traversal_preset;
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
        physical_position: conf.physical_position || null,
        enclosure_id: conf.enclosure_id || null
      };
    });


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
    bayMappingContainer.innerHTML = `<div class="bay-mapping-error">Failed to load mapping configurations: ${err.message}</div>`;
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
    bayMappingContainer.innerHTML = `<div class="bay-mapping-error">Failed to render mapping configurations: ${err.message}</div>`;
  }
}

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
    
    const isU2 = bayConfig.type === 'u2';
    const lockStatusText = bayConfig.locked ? "Locked" : "Editable";
    const hasOverride = !!String(bayConfig.display_number || "").trim();
    
    const deleteBtnHtml = bayConfig.locked ? "" : `
        <button type="button" class="btn-delete-bay bay-mapping-delete-btn" data-delete-bay-id="${escapeHtml(bayId)}">
          Delete
        </button>
    `;
    
    container.innerHTML = `
        <div class="bay-mapping-header">
            <h3 class="bay-mapping-title">${escapeHtml(bayConfig.label || bayId)}</h3>
            <div class="bay-mapping-actions">
                <small class="bay-mapping-lock-status">${lockStatusText}</small>
                ${deleteBtnHtml}
            </div>
        </div>

        <div class="form-group bay-mapping-form-group">
            <label class="bay-mapping-label">Bay Label</label>
            <input id="label-${bayId}" class="bay-label-input bay-mapping-input" data-bay="${bayId}" type="text" value="${escapeHtml(bayConfig.label || "")}" />
        </div>

        <div class="form-group bay-mapping-grid-3">
            <label class="bay-mapping-label">Bay Number</label>
            <input id="display-number-${bayId}" class="display-number-input input--number bay-mapping-input" data-bay="${bayId}" type="text" value="${escapeHtml(bayConfig.display_number || "")}" ${hasOverride ? "" : "disabled"} />
            <label class="bay-mapping-override-label">
              <input id="override-number-${bayId}" class="override-number-toggle" data-bay="${bayId}" type="checkbox" ${hasOverride ? "checked" : ""} />
              Manual Override
            </label>
        </div>

        <div class="form-group bay-mapping-form-group">
            <label class="bay-mapping-label">Drive Interface Type</label>
            <select id="type-${bayId}" class="bay-type-selector input--select bay-mapping-select" data-bay="${bayId}">
                <option value="sas_sata" ${!isU2 ? 'selected' : ''}>SAS / SATA</option>
                <option value="u2" ${isU2 ? 'selected' : ''}>U.2 / U.3 / Hybrid (NVMe capable)</option>
            </select>
        </div>

        <div class="form-group bay-mapping-form-group">
            <label id="primary-label-${bayId}" class="bay-mapping-label">Primary SAS/SATA Controller Port Path</label>
            <select id="path-${bayId}" class="by-path-select input--select bay-mapping-select" data-bay="${bayId}">
            </select>
        </div>

        <div class="form-group nvme-group bay-mapping-form-group ${isU2 ? '' : 'hidden'}" id="nvme-group-${bayId}">
            <label class="bay-mapping-nvme-label">Motherboard NVMe direct-attach Path (Optional)</label>
            <select id="path-nvme-${bayId}" class="by-path-nvme-select input--select bay-mapping-select" data-bay="${bayId}">
            </select>
        </div>
        <hr class="bay-mapping-divider">
    `;

    const primarySelect = container.querySelector(`#path-${bayId}`);
    const primaryFilter = isU2 ? null : "sas_sata";
    populatePathDropdown(primarySelect, unmappedDrives, bayConfig.by_path, primaryFilter);

    const nvmeSelect = container.querySelector(`#path-nvme-${bayId}`);
    populatePathDropdown(nvmeSelect, unmappedDrives, bayConfig.by_path_nvme, "nvme");

    const labelInput = container.querySelector(`#label-${bayId}`);
    labelInput.addEventListener("input", () => {
      showUnsavedChangesIndicator();
    });

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
            nvmeGroup.classList.remove('hidden');
            primaryLabel.textContent = 'Primary SAS/SATA Controller Port Path (SATA Mode)';
        } else {
            nvmeGroup.classList.add('hidden');
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
      physical_position: conf.physical_position || null,
      enclosure_id: conf.enclosure_id ?? null
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

        const labelInput = row.querySelector('.bay-label-input');
        const labelValue = (labelInput?.value || "").trim();
        const defaultLabel = 'Work Bay';

        updatedBayMap[bayId] = {
            "role": localBayMapCopy[bayId]?.role || "wipe",
            "locked": localBayMapCopy[bayId]?.locked || false,
            "type": type,
            "label": labelValue || localBayMapCopy[bayId]?.label || defaultLabel,
            "by_path": primaryPath,
            "by_path_nvme": nvmePath,
            "display_number": displayNumber || null,
            "physical_position": localBayMapCopy[bayId]?.physical_position || null,
            "enclosure_id": localBayMapCopy[bayId]?.enclosure_id ?? null
        };
    });

    let usedFallback = false;
    if (Object.keys(updatedBayMap).length === 0 || Object.keys(updatedBayMap).length !== Object.keys(localBayMapCopy).length) {
        usedFallback = true;
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
                "physical_position": conf.physical_position || null,
                "enclosure_id": conf.enclosure_id ?? null
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
        if (usedFallback) {
            alert("Warning: Some bay rows could not be read from the UI. Saved with last known configuration. Please verify your changes were applied.");
        } else {
            alert("Bay mapping successfully saved!");
        }
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

if (addBayBtn) {
  addBayBtn.addEventListener("click", () => {
  if (Object.keys(localBayMapCopy).length >= 128) {
    alert("Add Blocked: Maximum threshold of 128 active configurations has been reached.");
    return;
  }

  const label = prompt("Enter a descriptive label for the new physical bay:");
  if (label === null) return;

  const cleanLabel = label.trim() || "Work Bay";
  const cleanType = "sas_sata";

  const bayKeys = Object.keys(localBayMapCopy);
  let highestNum = -1;
  bayKeys.forEach(k => {
    const num = parseInt(k.replace(/\D/g, ""), 10);
    if (!isNaN(num) && num > highestNum) {
      highestNum = num;
    }
  });

  const nextBayId = `bay${highestNum + 1}`;
  const nextDisplayNumber = (highestNum + 1).toString();

  localBayMapCopy[nextBayId] = {
    role: "wipe",
    locked: false,
    label: cleanLabel,
    type: cleanType,
    by_path: "",
    by_path_nvme: "",
    display_number: nextDisplayNumber,
    physical_position: null
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
    marker: { status: "none" },
    display_number: nextDisplayNumber,
    physical_position: null
  });
  renderBays(currentDrives);
  renderBayMappingConfig();
  showUnsavedChangesIndicator();
  });
}

if (saveBayMapBtn) {
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
}

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
    updateTraversalFromTemplate();
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
// --- END OF FILE frontend/admin/bayMapping.js ---
