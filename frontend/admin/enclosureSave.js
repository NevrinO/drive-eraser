// Enclosure save: collect slot mappings, save/edit handlers, module-level save listener
// Load order: enclosureList.js -> enclosureWizard.js -> enclosureSave.js

function collectSlotMappingsFromDOM(container) {
  const slotMappings = {};

  container.querySelectorAll('.slot-label-input').forEach(input => {
    const slotIndex = parseInt(input.dataset.slotIndex, 10);
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
    const slotIndex = parseInt(select.dataset.slotIndex, 10);
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

  container.querySelectorAll('.hw-id-input').forEach(input => {
    const slotIndex = parseInt(input.dataset.slotIndex, 10);
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

  return slotMappings;
}

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
    const slotMappings = collectSlotMappingsFromDOM(container);

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
    const slotMappings = collectSlotMappingsFromDOM(container);

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

