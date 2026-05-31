// --- START OF FILE frontend/app.js ---
// Main entry point for Drive Eraser frontend application
// This file imports and initializes all modular components

// DOM Elements
const mainTabs = document.getElementById("mainTabs");

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

// Tab switching
mainTabs.addEventListener("click", (event) => {
  const btn = event.target.closest(".tab-button");
  if (!btn) return;
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

// Application initialization
(async () => {
  await loadSecurityStatus();
  await loadLayoutTemplates();
  await loadBayMappingConfig();
  await loadDrives(false);
  pollActiveWipes();
})();
