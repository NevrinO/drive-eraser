// --- START OF FILE frontend/app.js ---
// Main entry point for Drive Eraser frontend application
// This file imports and initializes all modular components

// DOM Elements
const mainTabs = document.getElementById("mainTabs");
const helpButton = document.getElementById("helpButton");
const helpModal = document.getElementById("helpModal");
const helpClose = document.getElementById("helpClose");
const legendButton = document.getElementById("legendButton");
const legendModal = document.getElementById("legendModal");
const legendClose = document.getElementById("legendClose");
const docViewerModal = document.getElementById("docViewerModal");
const docViewerContent = document.getElementById("docViewerContent");
const docViewerTitle = document.getElementById("docViewerTitle");

// State variables
let currentDrives = [];
let currentHistoryJobs = [];
let selectedBays = new Set();

function sanitizeHtml(html) {
  const temp = document.createElement('div');
  temp.innerHTML = html;
  temp.querySelectorAll('script, style, iframe, object, embed, link, meta, base').forEach(el => el.remove());
  temp.querySelectorAll('*').forEach(el => {
    [...el.attributes].forEach(attr => {
      if (attr.name.startsWith('on') ||
          (attr.name === 'href' && (attr.value || '').trim().toLowerCase().startsWith('javascript:'))) {
        el.removeAttribute(attr.name);
      }
    });
  });
  return temp.innerHTML;
}
let isBatchMode = false;
let ledgerExpandedJobs = new Set();
let localBayMapCopy = {};
let localLayoutMetadata = {};
let availableLayoutTemplates = [];
let hasUnsavedBayMapChanges = false;
let socket = null;
let currentDetailDrive = null;

// Tab switching
function switchTab(btn) {
  // Clear bulk selection when leaving audit tab (CRITIQUE.md #6)
  if (btn.dataset.tab !== "auditPanel" && typeof clearBulkSelectionState === "function") {
    clearBulkSelectionState();
  }

  document.querySelectorAll(".tab-button").forEach(b => {
    b.classList.remove("active");
    b.setAttribute("aria-selected", "false");
    b.setAttribute("tabindex", "-1");
  });
  document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));

  btn.classList.add("active");
  btn.setAttribute("aria-selected", "true");
  btn.setAttribute("tabindex", "0");
  document.getElementById(btn.dataset.tab).classList.add("active");

  if (btn.dataset.tab === "auditPanel") {
    loadHistoryIndex();
  } else if (btn.dataset.tab === "adminPanel") {
    loadAdminMetrics();
    loadBayMappingConfig();
  }
}

mainTabs.addEventListener("click", (event) => {
  const btn = event.target.closest(".tab-button");
  if (!btn) return;
  switchTab(btn);
});

mainTabs.addEventListener("keydown", (event) => {
  if (!event.target.classList.contains("tab-button")) return;
  const tabs = Array.from(mainTabs.querySelectorAll(".tab-button"));
  const currentIndex = tabs.indexOf(event.target);
  let newIndex;

  if (event.key === "ArrowRight") {
    newIndex = (currentIndex + 1) % tabs.length;
  } else if (event.key === "ArrowLeft") {
    newIndex = (currentIndex - 1 + tabs.length) % tabs.length;
  } else {
    return;
  }

  event.preventDefault();
  tabs[newIndex].focus();
  switchTab(tabs[newIndex]);
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

// Legend modal
if (legendButton && legendModal) {
  legendButton.addEventListener("click", () => {
    openModal(legendModal);
  });
}

if (legendClose && legendModal) {
  legendClose.addEventListener("click", () => {
    closeModal(legendModal);
  });
}

// Doc viewer modal — fetch and render markdown in-app
document.addEventListener("click", async (event) => {
  const btn = event.target.closest(".doc-link-btn");
  if (!btn) return;
  const docFile = btn.dataset.doc;
  if (!docFile) return;

  const docTitle = btn.textContent.trim();
  docViewerTitle.textContent = docTitle;
  docViewerContent.innerHTML = '<p>Loading...</p>';
  openModal(docViewerModal);

  try {
    const resp = await fetch(`/docs/${docFile}`);
    if (!resp.ok) {
      docViewerContent.innerHTML = `<p>Failed to load document: ${resp.status} ${resp.statusText}</p>`;
      return;
    }
    const md = await resp.text();
    if (typeof marked !== "undefined" && marked.parse) {
      docViewerContent.innerHTML = `<div class="markdown-body">${sanitizeHtml(marked.parse(md))}</div>`;
    } else {
      docViewerContent.innerHTML = `<pre class="terminal-pre">${escapeHtml(md)}</pre>`;
    }
  } catch (err) {
    docViewerContent.innerHTML = `<p>Error loading document: ${escapeHtml(err.message)}</p>`;
  }
});

// Initialize WebSocket connection
function initWebSocket() {
  if (typeof io === 'undefined') {
    console.warn('Socket.IO library not loaded, WebSocket features disabled');
    return;
  }

  // Polling-only: Flask-SocketIO with async_mode='threading' doesn't support
  // WebSocket transport. Without this, the client attempts WebSocket first,
  // gets a 400, then falls back to polling — adding latency on each reconnect.
  socket = io({ transports: ['polling'] });

  socket.on('connect', () => {
    console.log('WebSocket connected');
  });

  socket.on('disconnect', () => {
    console.log('WebSocket disconnected');
  });

  socket.on('smart_data_updated', (data) => {
    handleSmartDataUpdate(data);
  });

  socket.on('zero_check_updated', (data) => {
    if (typeof handleZeroCheckUpdate === 'function') {
      handleZeroCheckUpdate(data);
    }
  });

  socket.on('connect_error', (error) => {
    console.warn('WebSocket connection error:', error);
  });
}

// Handle SMART data update from WebSocket
function handleSmartDataUpdate(data) {
  const { device, enclosure_id, slot_number, smart, health_score, recommendation, marker, status } = data || {};

  // Find and update the drive in currentDrives
  const driveIndex = currentDrives.findIndex(d => d.device === device);
  if (driveIndex !== -1) {
    currentDrives[driveIndex].smart = smart;
    currentDrives[driveIndex].health_score = health_score;
    currentDrives[driveIndex].recommendation = recommendation;
    if (status) currentDrives[driveIndex].status = status;
    if (marker !== undefined) {
      currentDrives[driveIndex].marker = marker;
    }

    // Re-render the workbench if on workbench tab
    if (document.getElementById('workbenchPanel').classList.contains('active')) {
      renderBays(currentDrives);
    }

    // Update triage data and re-render if on triage tab
    if (typeof updateTriageDriveData === 'function') {
      updateTriageDriveData(data);
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
  try {
    setupKeyboardNavigation();
    initWebSocket();
    loadSecurityStatus(); // fire-and-forget — updates badge only, no downstream dependency

    // Start drives fetch in background — overlaps with bay map config + enclosure loading
    const drivesPromise = loadDrives(false);

    // loadBayMappingConfig internally calls loadLayoutTemplates, so no need to call it separately
    // loadEnclosuresForWorkbench is needed for skeleton grouping — run in parallel
    await Promise.all([
      loadBayMappingConfig(),
      loadEnclosuresForWorkbench()
    ]);

    // Render skeleton cards from bay map config if drives haven't arrived yet
    if (currentDrives.length === 0) {
      renderSkeletonBays();
    }

    await drivesPromise;
    pollActiveWipes();
  } catch (err) {
    console.error("Initialization failed:", err);
    const apiStatusEl = document.getElementById("apiStatus");
    if (apiStatusEl) apiStatusEl.textContent = `API Status: Initialization failed (${err.message})`;
  }
})();

// Cleanup on page unload to prevent memory leaks
window.addEventListener("beforeunload", () => {
  cleanupAllEventListeners();
  if (typeof stopPolling === "function") {
    stopPolling();
  }
  if (socket) socket.disconnect();
});
