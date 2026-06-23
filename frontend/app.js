// --- START OF FILE frontend/app.js ---
// Main entry point for Drive Eraser frontend application
// This file imports and initializes all modular components

// DOM Elements
const mainTabs = document.getElementById("mainTabs");
const helpButton = document.getElementById("helpButton");
const helpModal = document.getElementById("helpModal");
const helpClose = document.getElementById("helpClose");

// State variables
let currentDrives = [];
let currentHistoryJobs = [];
let selectedBays = new Set();
let isBatchMode = false;
let ledgerExpandedJobs = new Set();
let localBayMapCopy = {};
let localLayoutMetadata = {};
let availableLayoutTemplates = [];
let hasUnsavedBayMapChanges = false;
let socket = null;
let currentDetailDrive = null;

// Tab switching
mainTabs.addEventListener("click", (event) => {
  const btn = event.target.closest(".tab-button");
  if (!btn) return;
  
  // Clear bulk selection when leaving audit tab (CRITIQUE.md #6)
  if (btn.dataset.tab !== "auditPanel" && typeof clearBulkSelectionState === "function") {
    clearBulkSelectionState();
  }
  
  document.querySelectorAll(".tab-button").forEach(b => b.classList.remove("active"));
  document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
  btn.classList.add("active");
  document.getElementById(btn.dataset.tab).classList.add("active");
  
  if (btn.dataset.tab === "auditPanel") {
    loadHistoryIndex();
  } else if (btn.dataset.tab === "adminPanel") {
    loadAdminMetrics();
    loadBayMappingConfig();
  }
});

// Help modal
if (helpButton && helpModal) {
  helpButton.addEventListener("click", () => {
    openModal(helpModal);
  });
}

if (helpClose && helpModal) {
  helpClose.addEventListener("click", () => {
    closeModal(helpModal);
  });
}

// Initialize WebSocket connection
function initWebSocket() {
  if (typeof io === 'undefined') {
    console.warn('Socket.IO library not loaded, WebSocket features disabled');
    return;
  }

  socket = io();

  socket.on('connect', () => {
    console.log('WebSocket connected');
  });

  socket.on('disconnect', () => {
    console.log('WebSocket disconnected');
  });

  socket.on('smart_data_updated', (data) => {
    handleSmartDataUpdate(data);
  });

  socket.on('connect_error', (error) => {
    console.warn('WebSocket connection error:', error);
  });
}

// Handle SMART data update from WebSocket
function handleSmartDataUpdate(data) {
  const { device, enclosure_id, slot_number, smart, health_score, recommendation } = data || {};

  // Find and update the drive in currentDrives
  const driveIndex = currentDrives.findIndex(d => d.device === device);
  if (driveIndex !== -1) {
    currentDrives[driveIndex].smart = smart;
    currentDrives[driveIndex].health_score = health_score;
    currentDrives[driveIndex].recommendation = recommendation;

    // Re-render the workbench if on workbench tab
    if (document.getElementById('workbenchPanel').classList.contains('active')) {
      renderBaysGrid();
    }

    // Re-render triage table if on triage tab
    if (document.getElementById('triagePanel').classList.contains('active')) {
      renderTriageTable();
    }

    // Update detail modal if currently open for this drive
    const modal = document.getElementById('bayDetailModal');
    if (modal.classList.contains('open') && currentDetailDrive && currentDetailDrive.device === device) {
      renderLiveDetails(currentDrives[driveIndex]);
    }
  }
}

// Application initialization
(async () => {
  setupKeyboardNavigation();
  initWebSocket();
  await loadSecurityStatus();
  await loadLayoutTemplates();
  await loadBayMappingConfig();
  await loadDrives(false);
  pollActiveWipes();
})();

// Cleanup on page unload to prevent memory leaks
window.addEventListener("beforeunload", () => {
  cleanupAllEventListeners();
  if (typeof stopPolling === "function") {
    stopPolling();
  }
  if (socket) socket.disconnect();
});
