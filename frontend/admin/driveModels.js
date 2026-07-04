// --- START OF FILE frontend/admin/driveModels.js ---
// Drive Model Risk Profiles viewer (Phase 6 Feature F)

const openDriveModelsBtn = document.getElementById("openDriveModelsBtn");
const driveModelsModal = document.getElementById("driveModelsModal");
const driveModelsClose = document.getElementById("driveModelsClose");
const driveModelsList = document.getElementById("driveModelsList");
const driveModelsError = document.getElementById("driveModelsError");

async function loadDriveModels() {
  try {
    const response = await safeFetch("/api/admin/drive-models");
    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.error || "Failed to load drive models");
    }
    const data = await response.json();
    renderDriveModels(data);
  } catch (error) {
    console.error("Failed to load drive models:", error);
    driveModelsError.textContent = `Failed to load drive models: ${error.message}`;
    driveModelsError.classList.remove("hidden");
    driveModelsList.innerHTML = "";
  }
}

function renderDriveModels(data) {
  const driveModels = data.drive_models || {};
  const modelKeys = Object.keys(driveModels);
  
  if (modelKeys.length === 0) {
    driveModelsList.innerHTML = `
      <div class="drive-models-empty">
        No drive models configured. Edit <code>config/drive_models.json</code> to add entries.
      </div>
    `;
    return;
  }
  
  const modelsHtml = modelKeys.map(key => {
    const model = driveModels[key];
    const [vendor, product, revision] = key.split(",");
    
    return `
      <div class="drive-model-entry">
        <div class="drive-model-entry-title">
          ${escapeHtml(vendor)} ${escapeHtml(product)} (Rev: ${escapeHtml(revision)})
        </div>
        <div class="kv"><span>Vendor:</span><span>${escapeHtml(model.vendor || "-")}</span></div>
        <div class="kv"><span>Product:</span><span>${escapeHtml(model.product || "-")}</span></div>
        <div class="kv"><span>Revision:</span><span>${escapeHtml(model.revision || "-")}</span></div>
        ${model.trip_temperature !== undefined ? `<div class="kv"><span>Trip Temperature:</span><span>${model.trip_temperature}°C</span></div>` : ""}
        ${model.nme_normal_range_max !== undefined ? `<div class="kv"><span>NME Normal Range Max:</span><span>${model.nme_normal_range_max.toLocaleString()}</span></div>` : ""}
        ${model.notes ? `<div class="drive-model-entry-notes">${escapeHtml(model.notes)}</div>` : ""}
      </div>
    `;
  }).join("");
  
  driveModelsList.innerHTML = modelsHtml;
}

// Event listeners
if (openDriveModelsBtn) {
  openDriveModelsBtn.addEventListener("click", () => {
    loadDriveModels();
    openModal(driveModelsModal);
  });
}

if (driveModelsClose) {
  driveModelsClose.addEventListener("click", () => {
    closeModal(driveModelsModal);
  });
}

// --- END OF FILE frontend/admin/driveModels.js ---
