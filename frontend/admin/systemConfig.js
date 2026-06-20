// --- START OF FILE frontend/admin/systemConfig.js ---
// System configuration modal logic

const systemConfigModal = document.getElementById("systemConfigModal");
const openSystemConfigBtn = document.getElementById("openSystemConfigBtn");
const systemConfigClose = document.getElementById("systemConfigClose");
const systemConfigForm = document.getElementById("systemConfigForm");
const systemConfigError = document.getElementById("systemConfigError");
const passphraseConfirmModal = document.getElementById("passphraseConfirmModal");
const passphraseConfirmYes = document.getElementById("passphraseConfirmYes");
const passphraseConfirmNo = document.getElementById("passphraseConfirmNo");
const passphraseConfirmClose = document.getElementById("passphraseConfirmClose");
const strictAuditConfirmModal = document.getElementById("strictAuditConfirmModal");
const strictAuditConfirmYes = document.getElementById("strictAuditConfirmYes");
const strictAuditConfirmNo = document.getElementById("strictAuditConfirmNo");
const strictAuditConfirmClose = document.getElementById("strictAuditConfirmClose");

let pendingFormData = null;
let currentStrictAuditMode = false;

async function loadSystemConfig() {
  try {
    const response = await safeFetch("/api/admin/policy");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const policy = await response.json();
    
    // Populate form fields with current values
    const slackWebhookInput = document.getElementById("slack_webhook_url");
    if (slackWebhookInput) {
      slackWebhookInput.value = policy.slack_webhook_url || "";
    }
    
    const cryptoModeInput = document.getElementById("crypto_verification_mode");
    if (cryptoModeInput) {
      cryptoModeInput.value = policy.crypto_verification_mode || "conservative_probe";
    }
    
    const discoveryWorkersInput = document.getElementById("discovery_max_workers");
    if (discoveryWorkersInput) {
      discoveryWorkersInput.value = policy.discovery_max_workers || 8;
    }
    
    const maxConcurrentInput = document.getElementById("max_concurrent_wipes");
    if (maxConcurrentInput) {
      maxConcurrentInput.value = policy.max_concurrent_wipes || 64;
    }
    
    const blockdevRetriesInput = document.getElementById("blockdev_post_wipe_retries");
    if (blockdevRetriesInput) {
      blockdevRetriesInput.value = policy.blockdev_post_wipe_retries || 3;
    }
    
    const blockdevDelayInput = document.getElementById("blockdev_post_wipe_retry_delay");
    if (blockdevDelayInput) {
      blockdevDelayInput.value = policy.blockdev_post_wipe_retry_delay || 5;
    }
    
    const strictAuditModeInput = document.getElementById("strict_audit_mode");
    if (strictAuditModeInput) {
      strictAuditModeInput.value = policy.strict_audit_mode ? "true" : "false";
      currentStrictAuditMode = policy.strict_audit_mode || false;
    }
    
    const wipePassphraseInput = document.getElementById("wipe_passphrase");
    if (wipePassphraseInput) {
      wipePassphraseInput.value = ""; // Never populate the passphrase field for security
    }
    
    hideError(systemConfigError);
  } catch (error) {
    showError(systemConfigError, `Failed to load system configuration: ${error.message}`);
  }
}

async function saveSystemConfig(formData) {
  try {
    const response = await safeFetch("/api/admin/policy", {
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
  
  // Slack webhook URL (optional string)
  const slackWebhookInput = document.getElementById("slack_webhook_url");
  if (slackWebhookInput) {
    formData.slack_webhook_url = slackWebhookInput.value.trim();
  }
  
  // Crypto verification mode (enum)
  const cryptoModeInput = document.getElementById("crypto_verification_mode");
  if (cryptoModeInput) {
    const validModes = ["conservative_probe", "full_verify", "disabled"];
    if (!validModes.includes(cryptoModeInput.value)) {
      isValid = false;
      cryptoModeInput.style.borderColor = "var(--color-danger)";
    } else {
      cryptoModeInput.style.borderColor = "";
      formData.crypto_verification_mode = cryptoModeInput.value;
    }
  }
  
  // Discovery max workers (integer 1-32)
  const discoveryWorkersInput = document.getElementById("discovery_max_workers");
  if (discoveryWorkersInput) {
    const value = parseInt(discoveryWorkersInput.value, 10);
    if (isNaN(value) || value < 1 || value > 32) {
      isValid = false;
      discoveryWorkersInput.style.borderColor = "var(--color-danger)";
    } else {
      discoveryWorkersInput.style.borderColor = "";
      formData.discovery_max_workers = value;
    }
  }
  
  // Max concurrent wipes (integer 1-256)
  const maxConcurrentInput = document.getElementById("max_concurrent_wipes");
  if (maxConcurrentInput) {
    const value = parseInt(maxConcurrentInput.value, 10);
    if (isNaN(value) || value < 1 || value > 256) {
      isValid = false;
      maxConcurrentInput.style.borderColor = "var(--color-danger)";
    } else {
      maxConcurrentInput.style.borderColor = "";
      formData.max_concurrent_wipes = value;
    }
  }
  
  // Blockdev post-wipe retries (integer 0-10)
  const blockdevRetriesInput = document.getElementById("blockdev_post_wipe_retries");
  if (blockdevRetriesInput) {
    const value = parseInt(blockdevRetriesInput.value, 10);
    if (isNaN(value) || value < 0 || value > 10) {
      isValid = false;
      blockdevRetriesInput.style.borderColor = "var(--color-danger)";
    } else {
      blockdevRetriesInput.style.borderColor = "";
      formData.blockdev_post_wipe_retries = value;
    }
  }
  
  // Blockdev post-wipe retry delay (integer 0-60)
  const blockdevDelayInput = document.getElementById("blockdev_post_wipe_retry_delay");
  if (blockdevDelayInput) {
    const value = parseInt(blockdevDelayInput.value, 10);
    if (isNaN(value) || value < 0 || value > 60) {
      isValid = false;
      blockdevDelayInput.style.borderColor = "var(--color-danger)";
    } else {
      blockdevDelayInput.style.borderColor = "";
      formData.blockdev_post_wipe_retry_delay = value;
    }
  }
  
  // Strict audit mode (boolean)
  const strictAuditModeInput = document.getElementById("strict_audit_mode");
  if (strictAuditModeInput) {
    const value = strictAuditModeInput.value === "true";
    formData.strict_audit_mode = value;
  }
  
  // Wipe passphrase (optional string, but required if strict mode is enabled)
  const wipePassphraseInput = document.getElementById("wipe_passphrase");
  if (wipePassphraseInput) {
    const value = wipePassphraseInput.value.trim();
    if (value) {
      // Only include in payload if user provided a new value
      formData.wipe_passphrase = value;
    }
    // Client-side validation: strict mode requires passphrase of at least 8 characters
    if (formData.strict_audit_mode && (!value || value.length < 8)) {
      isValid = false;
      wipePassphraseInput.style.borderColor = "var(--color-danger)";
    } else {
      wipePassphraseInput.style.borderColor = "";
    }
  }
  
  if (!isValid) {
    showError(systemConfigError, "Please enter valid values for all fields.");
    return null;
  }
  
  return formData;
}

// Wait for DOM to be ready
document.addEventListener("DOMContentLoaded", () => {
  // Open modal and load current config
  openSystemConfigBtn.addEventListener("click", async () => {
    await loadSystemConfig();
    openModal(systemConfigModal);
  });

  // Close modal
  systemConfigClose.addEventListener("click", () => {
    closeModal(systemConfigModal);
  });

  // Form submission
  systemConfigForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    
    const formData = validateForm();
    if (!formData) return;
    
    // Check if passphrase is being changed
    if (formData.wipe_passphrase) {
      // Show confirmation dialog
      pendingFormData = formData;
      openModal(passphraseConfirmModal);
      return;
    }
    
    // Check if strict audit mode is being enabled
    if (formData.strict_audit_mode && !currentStrictAuditMode) {
      // Show confirmation dialog
      pendingFormData = formData;
      openModal(strictAuditConfirmModal);
      return;
    }
    
    // No passphrase change or strict mode enablement, proceed directly
    await submitForm(formData);
  });

  // Passphrase confirmation handlers
  if (passphraseConfirmYes) {
    passphraseConfirmYes.addEventListener("click", async () => {
      closeModal(passphraseConfirmModal);
      if (pendingFormData) {
        await submitForm(pendingFormData);
        pendingFormData = null;
      }
    });
  }

  if (passphraseConfirmNo || passphraseConfirmClose) {
    const cancelHandler = () => {
      closeModal(passphraseConfirmModal);
      pendingFormData = null;
    };
    if (passphraseConfirmNo) passphraseConfirmNo.addEventListener("click", cancelHandler);
    if (passphraseConfirmClose) passphraseConfirmClose.addEventListener("click", cancelHandler);
  }

  // Strict audit mode confirmation handlers
  if (strictAuditConfirmYes) {
    strictAuditConfirmYes.addEventListener("click", async () => {
      closeModal(strictAuditConfirmModal);
      if (pendingFormData) {
        await submitForm(pendingFormData);
        pendingFormData = null;
      }
    });
  }

  if (strictAuditConfirmNo || strictAuditConfirmClose) {
    const cancelHandler = () => {
      closeModal(strictAuditConfirmModal);
      pendingFormData = null;
    };
    if (strictAuditConfirmNo) strictAuditConfirmNo.addEventListener("click", cancelHandler);
    if (strictAuditConfirmClose) strictAuditConfirmClose.addEventListener("click", cancelHandler);
  }
});

async function submitForm(formData) {
  try {
    const result = await saveSystemConfig(formData);
    hideError(systemConfigError);
    closeModal(systemConfigModal);
    alert("System configuration saved successfully.");
  } catch (error) {
    showError(systemConfigError, `Failed to save system configuration: ${error.message}`);
  }
}
// --- END OF FILE frontend/admin/systemConfig.js ---
