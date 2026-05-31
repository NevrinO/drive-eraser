// --- START OF FILE frontend/auth.js ---
// Authentication handling

// These elements are defined in the main app.js file
const authOverlay = document.getElementById("authOverlay");
const authForm = document.getElementById("authForm");
const authPassphrase = document.getElementById("authPassphrase");
const authErrorMsg = document.getElementById("authErrorMsg");

if (!authOverlay || !authForm || !authPassphrase || !authErrorMsg) {
  console.error("Critical: One or more auth elements not found in DOM");
}

async function safeFetch(url, options = {}) {
  const response = await fetch(url, options);
  if (response.status === 401) {
    if (!url.includes("/api/auth/verify")) {
      showAuthOverlay();
      throw new Error("Authorization Required");
    }
  }
  return response;
}

function showAuthOverlay() {
  authOverlay.classList.remove("hidden");
  authPassphrase.focus();
}

function hideAuthOverlay() {
  authOverlay.classList.add("hidden");
  authErrorMsg.classList.add("hidden");
  authPassphrase.value = "";
}

authForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const passphrase = authPassphrase.value;
  try {
    const response = await fetch("/api/auth/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ passphrase })
    });
    if (response.ok) {
      hideAuthOverlay();
      loadSecurityStatus();
      loadDrives(false);
    } else {
      authErrorMsg.classList.remove("hidden");
    }
  } catch (err) {
    authErrorMsg.classList.remove("hidden");
  }
});

async function loadSecurityStatus() {
  const badge = document.getElementById("securityBadge");
  if (!badge) return;
  try {
    const response = await safeFetch("/api/status");
    if (!response.ok) throw new Error();
    let data;
    try {
      data = await response.json();
    } catch (e) {
      console.error("Failed to parse security status JSON:", e);
      throw new Error("Invalid JSON response from status API");
    }
    if (data.passphrase_enabled) {
      badge.textContent = "SECURE MODE";
      badge.className = "reliability-badge secure";
    } else {
      badge.textContent = "UNSECURED MODE";
      badge.className = "reliability-badge unsecured";
    }
  } catch (error) {
    badge.textContent = "UNSECURED MODE";
    badge.className = "reliability-badge unsecured";
  }
}
// --- END OF FILE frontend/auth.js ---
