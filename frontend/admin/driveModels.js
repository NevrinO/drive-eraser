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
      <div style="padding: 20px; text-align: center; color: var(--color-text-muted);">
        No drive models configured. Edit <code>config/drive_models.json</code> to add entries.
      </div>
    `;
    return;
  }
  
  const modelsHtml = modelKeys.map(key => {
    const model = driveModels[key];
    const [vendor, product, revision] = key.split(",");
    
    return `
      <div style="padding: 12px; margin-bottom: 8px; background: var(--color-surface-2); border: 1px solid var(--color-border); border-radius: 4px;">
        <div style="font-weight: bold; color: var(--color-primary); margin-bottom: 8px; font-size: 0.85rem;">
          ${escapeHtml(vendor)} ${escapeHtml(product)} (Rev: ${escapeHtml(revision)})
        </div>
        <div class="kv"><span>Vendor:</span><span>${escapeHtml(model.vendor || "-")}</span></div>
        <div class="kv"><span>Product:</span><span>${escapeHtml(model.product || "-")}</span></div>
        <div class="kv"><span>Revision:</span><span>${escapeHtml(model.revision || "-")}</span></div>
        ${model.trip_temperature !== undefined ? `<div class="kv"><span>Trip Temperature:</span><span>${model.trip_temperature}°C</span></div>` : ""}
        ${model.nme_normal_range_max !== undefined ? `<div class="kv"><span>NME Normal Range Max:</span><span>${model.nme_normal_range_max.toLocaleString()}</span></div>` : ""}
        ${model.notes ? `<div style="margin-top: 8px; font-size: 0.75rem; color: var(--color-text-muted); padding: 8px; background: var(--color-surface-3); border-radius: 4px;">${escapeHtml(model.notes)}</div>` : ""}
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
