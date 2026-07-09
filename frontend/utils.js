// --- START OF FILE frontend/utils.js ---
// Utility functions and helpers

const METHOD_ORDER = ["crypto", "block", "enhanced_secure_erase", "secure_erase", "overwrite"];

// Event listener cleanup tracking
const registeredEventListeners = new Map();

function addTrackedEventListener(element, event, handler, options) {
  if (!element) return;
  
  element.addEventListener(event, handler, options);
  
  // Track the listener for cleanup
  const key = `${event}-${Date.now()}-${Math.random()}`;
  registeredEventListeners.set(key, { element, event, handler, options });
  return key;
}

function removeTrackedEventListener(key) {
  const entry = registeredEventListeners.get(key);
  if (entry) {
    entry.element.removeEventListener(entry.event, entry.handler, entry.options);
    registeredEventListeners.delete(key);
  }
}

function cleanupAllEventListeners() {
  registeredEventListeners.forEach((entry, key) => {
    entry.element.removeEventListener(entry.event, entry.handler, entry.options);
  });
  registeredEventListeners.clear();
}

function cleanupEventListenersByElement(element) {
  const keysToRemove = [];
  registeredEventListeners.forEach((entry, key) => {
    if (entry.element === element) {
      entry.element.removeEventListener(entry.event, entry.handler, entry.options);
      keysToRemove.push(key);
    }
  });
  keysToRemove.forEach(key => registeredEventListeners.delete(key));
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatIsoDate(value) {
  if (!value) return "-";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
}

function formatTraffic(drive, type) {
  const smart = drive.smart || {};
  let totalBytes = type === 'read' ? smart.data_read_bytes : smart.data_written_bytes;
  
  if (totalBytes === null || totalBytes === undefined || isNaN(totalBytes)) {
    const raw = type === 'read' ? smart.data_read_raw : smart.data_written_raw;
    if (raw === null || raw === undefined || isNaN(raw)) return "N/A";
    const iface = String(drive.interface_type || "sata").toLowerCase();
    totalBytes = iface.includes("nvme") ? raw * 512000 : raw * 512;
  }
  
  if (totalBytes === 0) return "0 B";
  const k = 1024;
  const sizes = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB'];
  const i = Math.floor(Math.log(totalBytes) / Math.log(k));
  const formatted = parseFloat((totalBytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  // Prefix ~ for approximate (drift-prone) write counters
  if (type === 'written' && smart.write_counter_source === 'gigabytes_processed') {
    return '~' + formatted;
  }
  return formatted;
}

function formatPowerOnTime(hours) {
  if (hours === null || hours === undefined || isNaN(hours) || hours === 0) return "-";
  const h = Number(hours);
  const days = (h / 24).toFixed(1);
  return `${h.toLocaleString()} hrs (${days} days)`;
}

function computeRecommendedMethod(drive) {
  const supported_methods = Array.isArray(drive?.supported_methods) ? drive.supported_methods : [];
  for (const method of METHOD_ORDER) {
    if (supported_methods.includes(method)) return method;
  }
  return "overwrite";
}

function calculateDriveHealthScore(drive) {
  if (!drive || !drive.present) return 0;
  const val = drive.health_score;
  return typeof val === 'number' && isFinite(val) ? val : null;
}

async function copyTextToClipboard(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      alert("Compliance fields copied to clipboard.");
      return;
    } catch (err) {
      // Fallback
    }
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.style.top = "0";
  textarea.style.left = "0";
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();

  try {
    const successful = document.execCommand("copy");
    if (successful) {
      alert("Compliance fields copied to clipboard.");
    } else {
      alert("Failed to copy compliance fields automatically.");
    }
  } catch (err) {
    alert("Copy failed. Please manually select and copy fields.");
  }

  document.body.removeChild(textarea);
}

// --- Centralized Error Handling ---

const ErrorType = {
  NETWORK: 'network',
  VALIDATION: 'validation',
  PARSE: 'parse',
  PERMISSION: 'permission',
  UNKNOWN: 'unknown'
};

function classifyError(error) {
  if (!error) return ErrorType.UNKNOWN;
  
  const message = error.message || String(error);
  
  if (message.includes('Failed to fetch') || message.includes('NetworkError') || message.includes('HTTP')) {
    return ErrorType.NETWORK;
  }
  if (message.includes('Invalid JSON') || message.includes('parse')) {
    return ErrorType.PARSE;
  }
  if (message.includes('validation') || message.includes('required') || message.includes('invalid')) {
    return ErrorType.VALIDATION;
  }
  if (message.includes('permission') || message.includes('unauthorized') || message.includes('forbidden')) {
    return ErrorType.PERMISSION;
  }
  
  return ErrorType.UNKNOWN;
}

function handleError(error, options = {}) {
  const {
    context = '',
    showAlert = true,
    alertMessage = null,
    logToConsole = true,
    fallbackAction = null,
    uiElement = null,
    uiErrorClass = 'error',
    uiSuccessClass = 'success'
  } = options;
  
  const errorType = classifyError(error);
  const errorMessage = error?.message || String(error);
  
  // Log to console
  if (logToConsole) {
    console.error(`[Error${context ? ` - ${context}` : ''}]`, {
      type: errorType,
      message: errorMessage,
      error: error
    });
  }
  
  // Show alert if requested
  if (showAlert) {
    const message = alertMessage || `Error: ${errorMessage}`;
    alert(message);
  }
  
  // Update UI element if provided
  if (uiElement) {
    uiElement.classList.remove(uiSuccessClass);
    uiElement.classList.add(uiErrorClass);
  }
  
  // Execute fallback action if provided
  if (fallbackAction && typeof fallbackAction === 'function') {
    try {
      fallbackAction(error);
    } catch (fallbackError) {
      console.error('[Fallback action failed]', fallbackError);
    }
  }
  
  return { errorType, errorMessage };
}

async function safeJsonParse(response, context = '') {
  try {
    return await response.json();
  } catch (error) {
    handleError(error, {
      context: context || 'JSON parsing',
      showAlert: false,
      logToConsole: true
    });
    throw new Error('Invalid JSON response from API');
  }
}

async function safeApiCall(url, options = {}, errorOptions = {}) {
  try {
    const response = await fetch(url, options);

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }

    return response;
  } catch (error) {
    handleError(error, {
      context: errorOptions.context || `API call to ${url}`,
      showAlert: errorOptions.showAlert !== false,
      alertMessage: errorOptions.alertMessage,
      logToConsole: errorOptions.logToConsole !== false,
      fallbackAction: errorOptions.fallbackAction,
      uiElement: errorOptions.uiElement
    });
    throw error;
  }
}

// --- Accessibility Utilities ---

// Focus trap for modals
let focusTrapElements = [];
let focusTrapPreviousFocus = null;

function trapFocus(modal) {
  const focusableElements = modal.querySelectorAll(
    'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
  );
  
  if (focusableElements.length === 0) {
    console.warn("Modal has no focusable elements, skipping focus trap");
    return;
  }
  
  const firstFocusable = focusableElements[0];
  const lastFocusable = focusableElements[focusableElements.length - 1];

  focusTrapPreviousFocus = document.activeElement;

  const trapHandler = (e) => {
    if (e.key !== 'Tab') return;

    if (e.shiftKey) {
      if (document.activeElement === firstFocusable) {
        e.preventDefault();
        lastFocusable.focus();
      }
    } else {
      if (document.activeElement === lastFocusable) {
        e.preventDefault();
        firstFocusable.focus();
      }
    }
  };

  modal.addEventListener('keydown', trapHandler);
  focusTrapElements.push({ modal, handler: trapHandler });

  // Focus first element
  if (firstFocusable) {
    firstFocusable.focus();
  }
}

function releaseFocusTrap(modal) {
  const index = focusTrapElements.findIndex(item => item.modal === modal);
  if (index !== -1) {
    const { modal: trappedModal, handler } = focusTrapElements[index];
    trappedModal.removeEventListener('keydown', handler);
    focusTrapElements.splice(index, 1);
  }

  // Restore previous focus
  if (focusTrapPreviousFocus) {
    focusTrapPreviousFocus.focus();
    focusTrapPreviousFocus = null;
  }
}

// Global keyboard navigation
function setupKeyboardNavigation() {
  // Escape key to close modals
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      const openModal = document.querySelector('.modal.open');
      if (openModal) {
        closeModal(openModal);
      }
    }
  });

  // Tab navigation for main tabs
  const tabs = document.querySelectorAll('.tab-button');
  tabs.forEach((tab, index) => {
    tab.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
        e.preventDefault();
        const direction = e.key === 'ArrowRight' ? 1 : -1;
        const nextIndex = (index + direction + tabs.length) % tabs.length;
        tabs[nextIndex].focus();
        tabs[nextIndex].click();
      }
    });
  });
}
// --- END OF FILE frontend/utils.js ---
