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
    
    const secondaryVerificationModeInput = document.getElementById("secondary_verification_mode");
    if (secondaryVerificationModeInput) {
      secondaryVerificationModeInput.value = policy.secondary_verification_mode || policy.crypto_verification_mode || "conservative_probe";
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

    // Zero detection settings
    const zeroDetectionEnabledInput = document.getElementById("prewipe_zero_detection_enabled");
    if (zeroDetectionEnabledInput) {
      zeroDetectionEnabledInput.value = policy.prewipe_zero_detection_enabled ? "true" : "false";
    }

    const zeroDetectionConcurrencyInput = document.getElementById("zero_detection_concurrency_limit");
    if (zeroDetectionConcurrencyInput) {
      zeroDetectionConcurrencyInput.value = policy.zero_detection_concurrency_limit || 8;
    }

    // Health gate settings
    const healthGateEnabledInput = document.getElementById("prewipe_health_gate_enabled");
    if (healthGateEnabledInput) {
      healthGateEnabledInput.value = policy.prewipe_health_gate_enabled ? "true" : "false";
    }

    const healthGateStrictModeInput = document.getElementById("prewipe_health_gate_strict_mode");
    if (healthGateStrictModeInput) {
      healthGateStrictModeInput.value = policy.prewipe_health_gate_strict_mode ? "true" : "false";
    }

    const healthGateBlockDestroyInput = document.getElementById("prewipe_health_gate_block_destroy");
    if (healthGateBlockDestroyInput) {
      healthGateBlockDestroyInput.value = policy.prewipe_health_gate_block_destroy ? "true" : "false";
    }

    const healthGateBlockScratchInput = document.getElementById("prewipe_health_gate_block_scratch");
    if (healthGateBlockScratchInput) {
      healthGateBlockScratchInput.value = policy.prewipe_health_gate_block_scratch ? "true" : "false";
    }

    const healthGateBlockFailedSmartInput = document.getElementById("prewipe_health_gate_block_failed_smart");
    if (healthGateBlockFailedSmartInput) {
      healthGateBlockFailedSmartInput.value = policy.prewipe_health_gate_block_failed_smart ? "true" : "false";
    }

    const healthGateMaxPendingInput = document.getElementById("prewipe_health_gate_max_pending_sectors");
    if (healthGateMaxPendingInput) {
      healthGateMaxPendingInput.value = policy.prewipe_health_gate_max_pending_sectors || 10;
    }

    const healthGateMaxReallocatedInput = document.getElementById("prewipe_health_gate_max_reallocated_sectors");
    if (healthGateMaxReallocatedInput) {
      healthGateMaxReallocatedInput.value = policy.prewipe_health_gate_max_reallocated_sectors || 5;
    }

    const healthGateMaxInterfaceErrorsInput = document.getElementById("prewipe_health_gate_max_interface_errors");
    if (healthGateMaxInterfaceErrorsInput) {
      healthGateMaxInterfaceErrorsInput.value = policy.prewipe_health_gate_max_interface_errors || 100;
    }

    const healthGateMaxHealthScoreDropInput = document.getElementById("prewipe_health_gate_max_health_score_drop");
    if (healthGateMaxHealthScoreDropInput) {
      healthGateMaxHealthScoreDropInput.value = policy.prewipe_health_gate_max_health_score_drop || 20;
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
  
  // Secondary verification mode (enum)
  const secondaryVerificationModeInput = document.getElementById("secondary_verification_mode");
  if (secondaryVerificationModeInput) {
    const validModes = ["conservative_probe", "full_verify", "disabled"];
    if (!validModes.includes(secondaryVerificationModeInput.value)) {
      isValid = false;
      secondaryVerificationModeInput.style.borderColor = "var(--color-danger)";
    } else {
      secondaryVerificationModeInput.style.borderColor = "";
      formData.secondary_verification_mode = secondaryVerificationModeInput.value;
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

  // Zero detection settings
  const zeroDetectionEnabledInput = document.getElementById("prewipe_zero_detection_enabled");
  if (zeroDetectionEnabledInput) {
    formData.prewipe_zero_detection_enabled = zeroDetectionEnabledInput.value === "true";
  }

  const zeroDetectionConcurrencyInput = document.getElementById("zero_detection_concurrency_limit");
  if (zeroDetectionConcurrencyInput) {
    const value = parseInt(zeroDetectionConcurrencyInput.value, 10);
    if (isNaN(value) || value < 1 || value > 32) {
      isValid = false;
      zeroDetectionConcurrencyInput.style.borderColor = "var(--color-danger)";
    } else {
      zeroDetectionConcurrencyInput.style.borderColor = "";
      formData.zero_detection_concurrency_limit = value;
    }
  }

  // Health gate settings
  const healthGateEnabledInput = document.getElementById("prewipe_health_gate_enabled");
  if (healthGateEnabledInput) {
    formData.prewipe_health_gate_enabled = healthGateEnabledInput.value === "true";
  }

  const healthGateStrictModeInput = document.getElementById("prewipe_health_gate_strict_mode");
  if (healthGateStrictModeInput) {
    formData.prewipe_health_gate_strict_mode = healthGateStrictModeInput.value === "true";
  }

  const healthGateBlockDestroyInput = document.getElementById("prewipe_health_gate_block_destroy");
  if (healthGateBlockDestroyInput) {
    formData.prewipe_health_gate_block_destroy = healthGateBlockDestroyInput.value === "true";
  }

  const healthGateBlockScratchInput = document.getElementById("prewipe_health_gate_block_scratch");
  if (healthGateBlockScratchInput) {
    formData.prewipe_health_gate_block_scratch = healthGateBlockScratchInput.value === "true";
  }

  const healthGateBlockFailedSmartInput = document.getElementById("prewipe_health_gate_block_failed_smart");
  if (healthGateBlockFailedSmartInput) {
    formData.prewipe_health_gate_block_failed_smart = healthGateBlockFailedSmartInput.value === "true";
  }

  const healthGateMaxPendingInput = document.getElementById("prewipe_health_gate_max_pending_sectors");
  if (healthGateMaxPendingInput) {
    const value = parseInt(healthGateMaxPendingInput.value, 10);
    if (isNaN(value) || value < 0 || value > 1000) {
      isValid = false;
      healthGateMaxPendingInput.style.borderColor = "var(--color-danger)";
    } else {
      healthGateMaxPendingInput.style.borderColor = "";
      formData.prewipe_health_gate_max_pending_sectors = value;
    }
  }

  const healthGateMaxReallocatedInput = document.getElementById("prewipe_health_gate_max_reallocated_sectors");
  if (healthGateMaxReallocatedInput) {
    const value = parseInt(healthGateMaxReallocatedInput.value, 10);
    if (isNaN(value) || value < 0 || value > 1000) {
      isValid = false;
      healthGateMaxReallocatedInput.style.borderColor = "var(--color-danger)";
    } else {
      healthGateMaxReallocatedInput.style.borderColor = "";
      formData.prewipe_health_gate_max_reallocated_sectors = value;
    }
  }

  const healthGateMaxInterfaceErrorsInput = document.getElementById("prewipe_health_gate_max_interface_errors");
  if (healthGateMaxInterfaceErrorsInput) {
    const value = parseInt(healthGateMaxInterfaceErrorsInput.value, 10);
    if (isNaN(value) || value < 0 || value > 100000) {
      isValid = false;
      healthGateMaxInterfaceErrorsInput.style.borderColor = "var(--color-danger)";
    } else {
      healthGateMaxInterfaceErrorsInput.style.borderColor = "";
      formData.prewipe_health_gate_max_interface_errors = value;
    }
  }

  const healthGateMaxHealthScoreDropInput = document.getElementById("prewipe_health_gate_max_health_score_drop");
  if (healthGateMaxHealthScoreDropInput) {
    const value = parseInt(healthGateMaxHealthScoreDropInput.value, 10);
    if (isNaN(value) || value < 0 || value > 100) {
      isValid = false;
      healthGateMaxHealthScoreDropInput.style.borderColor = "var(--color-danger)";
    } else {
      healthGateMaxHealthScoreDropInput.style.borderColor = "";
      formData.prewipe_health_gate_max_health_score_drop = value;
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
