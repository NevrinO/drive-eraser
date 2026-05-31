// --- START OF FILE frontend/utils.js ---
// Utility functions and helpers

const METHOD_ORDER = ["crypto", "block", "enhanced_secure_erase", "secure_erase", "overwrite"];

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
  return parseFloat((totalBytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
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

  if (drive.health_score !== undefined && drive.health_score !== null) {
    return drive.health_score;
  }

  const smart = drive.smart || {};
  if (Object.keys(smart).length === 0) return 0;

  let health = 100;
  const iface = String(drive.interface_type || "").toLowerCase();
  const isSsd = String(drive.model || "").toLowerCase().includes("ssd") || iface.includes("nvme") || smart.wear_level !== null;

  if (isSsd && smart.wear_level !== null) {
    let base = iface.includes("nvme") || iface.includes("sas") ? 100 - smart.wear_level : smart.wear_level;
    
    const poh = smart.power_on_hours || 0;
    if (poh > 40000) {
      const ssdPohPenalty = Math.min(20, ((poh - 40000) / 40000) * 20);
      base = Math.max(10, base - ssdPohPenalty);
    }
    health = base;
  } else {
    const poh = smart.power_on_hours || 0;
    let pohPenalty = 0;
    if (poh > 20000) {
      pohPenalty = Math.min(30, ((poh - 20000) / 40000) * 30);
    }
    
    const rawWritten = smart.data_written_raw || 0;
    const capacityBytes = smart.capacity_bytes || 1;
    const writtenBytes = rawWritten * 512;
    const fdw = writtenBytes / capacityBytes;
    const fdwPenalty = Math.min(30, (fdw / 150.0) * 30);

    health = Math.max(40, 100 - pohPenalty - fdwPenalty);
  }

  const reallocated = smart.reallocated_sectors || 0;
  const pending = smart.pending_sectors || 0;

  if (isSsd) {
    const reallocNorm = smart.reallocated_normalized;
    if (reallocNorm !== undefined && reallocNorm !== null && reallocNorm < 100) {
      health -= Math.min(40, (100 - reallocNorm) * 1.0);
    }
  } else {
    if (reallocated > 0) {
      let penalty = 0;
      if (reallocated === 1) {
        penalty = 10;
      } else if (reallocated <= 5) {
        penalty = 10 + (reallocated - 1) * 5;
      } else {
        penalty = 10 + 20 + (reallocated - 5) * 10;
      }
      health -= Math.min(40, penalty);
    }
  }

  health -= Math.min(60, pending * 15);

  if (smart.interface_errors > 50) {
    health -= 10;
  }

  if (String(drive.status).toUpperCase() === "FAILED") {
    health = Math.min(health, 5);
  }

  return Math.max(0, Math.min(100, Math.round(health)));
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
// --- END OF FILE frontend/utils.js ---
