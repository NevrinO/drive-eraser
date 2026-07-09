// Enclosure list: data state, rendering, delete, edit, initialization
// Load order: enclosureList.js -> enclosureWizard.js -> enclosureSave.js

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
          <small class="enclosure-id-text">ID: ${escapeHtml(enc.id)}</small>
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

// Delete enclosure
async function deleteEnclosure(enclosureId) {
  if (!confirm(`Are you sure you want to delete enclosure ${enclosureId}?`)) return;

  try {
    const response = await safeFetch(`/api/admin/enclosures/${enclosureId}`, {
      method: "DELETE"
    });

    if (!response.ok) {
      let data;
      try {
        data = await response.json();
      } catch {
        throw new Error("Failed to delete enclosure");
      }
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

  attachWizardSaveListener();

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

  // Load wizard data lazily (templates + master slot map needed for rendering)
  await ensureWizardDataLoaded();

  await renderWizardStep();
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

  // Only load enclosures — templates and master slot map are loaded lazily
  // when the wizard opens, since they involve a slow sysfs scan
  await loadEnclosures();
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

// Always wait for DOMContentLoaded — with defer scripts, readyState is "interactive"
// when this file executes, but enclosureWizard.js and enclosureSave.js (which define
// openNewEnclosureWizard and handleSaveEnclosure) may not have loaded yet.
// DOMContentLoaded fires after ALL deferred scripts have executed.
document.addEventListener("DOMContentLoaded", attachEnclosureManagementListeners);
