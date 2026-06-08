// --- START OF FILE frontend/admin/triageConfig.js ---
// Triage configuration modal logic

const triageConfigModal = document.getElementById("triageConfigModal");
const openTriageConfigBtn = document.getElementById("openTriageConfigBtn");
const triageConfigClose = document.getElementById("triageConfigClose");
const triageConfigForm = document.getElementById("triageConfigForm");
const triageConfigError = document.getElementById("triageConfigError");

// Threshold field IDs with their expected types
const thresholdFields = [
  "ssd_new_poh_threshold",
  "ssd_high_poh_threshold",
  "hdd_new_poh_threshold",
  "hdd_high_poh_threshold",
  "health_score_destroy_threshold",
  "health_score_scratch_threshold",
  "ssd_remaining_life_destroy_threshold",
  "ssd_remaining_life_scratch_threshold",
  "ssd_remaining_life_good_threshold",
  "ssd_new_fdw_threshold",
  "hdd_new_fdw_threshold",
  "hdd_heavy_fdw_threshold",
  "realloc_raw_new_threshold",
  "pending_sectors_destroy_threshold",
  "pending_sectors_scratch_threshold"
];

// Type mapping for threshold fields (matches backend validation)
const thresholdFieldTypes = {
  "ssd_new_poh_threshold": "int",
  "ssd_high_poh_threshold": "int",
  "hdd_new_poh_threshold": "int",
  "hdd_high_poh_threshold": "int",
  "health_score_destroy_threshold": "int",
  "health_score_scratch_threshold": "int",
  "ssd_remaining_life_destroy_threshold": "int",
  "ssd_remaining_life_scratch_threshold": "int",
  "ssd_remaining_life_good_threshold": "int",
  "ssd_new_fdw_threshold": "float",
  "hdd_new_fdw_threshold": "float",
  "hdd_heavy_fdw_threshold": "float",
  "realloc_raw_new_threshold": "int",
  "pending_sectors_destroy_threshold": "int",
  "pending_sectors_scratch_threshold": "int"
};

async function loadTriageConfig() {
  try {
    const response = await safeFetch("/api/admin/triage-config");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const thresholds = await response.json();
    
    // Populate form fields with current values
    thresholdFields.forEach(fieldId => {
      const input = document.getElementById(fieldId);
      if (input && thresholds[fieldId] !== undefined) {
        input.value = thresholds[fieldId];
      }
    });
    
    hideError(triageConfigError);
  } catch (error) {
    showError(triageConfigError, `Failed to load triage configuration: ${error.message}`);
  }
}

async function saveTriageConfig(formData) {
  try {
    const response = await safeFetch("/api/admin/triage-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(formData)
    });
    
    if (!response.ok) {
      const errorData = await response.json();
      throw new Error(errorData.error || `HTTP ${response.status}`);
    }
    
    const result = await response.json();
    return result;
  } catch (error) {
    throw error;
  }
}

function showError(element, message) {
  if (element) {
    element.textContent = message;
    element.classList.remove("hidden");
    element.style.color = "var(--color-danger)";
  }
}

function hideError(element) {
  if (element) {
    element.classList.add("hidden");
  }
}

function validateForm() {
  const formData = {};
  let isValid = true;
  
  thresholdFields.forEach(fieldId => {
    const input = document.getElementById(fieldId);
    if (!input) return;
    
    const fieldType = thresholdFieldTypes[fieldId] || "int";
    const value = fieldType === "int" ? parseInt(input.value, 10) : parseFloat(input.value);
    
    if (isNaN(value)) {
      isValid = false;
      input.style.borderColor = "var(--color-danger)";
    } else {
      input.style.borderColor = "";
      formData[fieldId] = value;
    }
  });
  
  if (!isValid) {
    showError(triageConfigError, "Please enter valid numeric values for all fields.");
    return null;
  }
  
  return formData;
}

// Wait for DOM to be ready
document.addEventListener("DOMContentLoaded", () => {
  // Open modal and load current config
  openTriageConfigBtn.addEventListener("click", async () => {
    await loadTriageConfig();
    openModal(triageConfigModal);
  });

  // Close modal
  triageConfigClose.addEventListener("click", () => {
    closeModal(triageConfigModal);
  });

  // Form submission
  triageConfigForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    
    const formData = validateForm();
    if (!formData) return;
    
    try {
      const result = await saveTriageConfig(formData);
      hideError(triageConfigError);
      closeModal(triageConfigModal);
      alert("Triage thresholds saved successfully.");
    } catch (error) {
      showError(triageConfigError, `Failed to save triage configuration: ${error.message}`);
    }
  });
});
// --- END OF FILE frontend/admin/triageConfig.js ---
