// --- START OF FILE frontend/admin/adminUtilities.js ---
// Admin panel DOM references, state, metrics, webhook, CSV export, support bundle

// These elements are defined in the main app.js file
const testWebhookBtn = document.getElementById("testWebhookBtn");
const webhookTestResult = document.getElementById("webhookTestResult");
const exportCsvBtn = document.getElementById("exportCsvBtn");
const downloadBundleBtn = document.getElementById("downloadBundleBtn");
const viewLogsBtn = document.getElementById("viewLogsBtn");
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
  console.error("Critical: One or more admin utility elements not found in DOM");
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
      webhookTestResult.className = "test-result-label test-result-label--error";
      webhookTestResult.textContent = "Error: Invalid server response";
      return;
    }
    webhookTestResult.classList.remove("hidden");
    if (response.ok) {
      webhookTestResult.className = "test-result-label test-result-label--success";
      webhookTestResult.textContent = data.message || "Test Notification Sent!";
    } else {
      webhookTestResult.className = "test-result-label test-result-label--error";
      webhookTestResult.textContent = `Failure: ${data.error || "Unknown response"}`;
    }
  } catch (err) {
    webhookTestResult.classList.remove("hidden");
    webhookTestResult.className = "test-result-label test-result-label--error";
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

if (viewLogsBtn) {
  viewLogsBtn.addEventListener("click", () => {
    if (window.LogViewer && window.LogViewer.openLogViewer) {
      window.LogViewer.openLogViewer();
    }
  });
}

function showLayoutStatus(message, isError = false) {
  if (!bayLayoutStatus) return;
  bayLayoutStatus.classList.remove("hidden", "status-badge--complete", "status-badge--failed");
  bayLayoutStatus.classList.add(isError ? "status-badge--failed" : "status-badge--complete");
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
// --- END OF FILE frontend/admin/adminUtilities.js ---
