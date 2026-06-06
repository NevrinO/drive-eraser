// --- START OF FILE frontend/admin/logoManagement.js ---
// Logo upload, delete, and preview management

// Logo management elements
const uploadLogoBtn = document.getElementById("uploadLogoBtn");
const deleteLogoBtn = document.getElementById("deleteLogoBtn");
const logoFileInput = document.getElementById("logoFileInput");
const logoPreview = document.getElementById("logoPreview");
const noLogoText = document.getElementById("noLogoText");
const logoStatus = document.getElementById("logoStatus");
const logoConfirmModal = document.getElementById("logoConfirmModal");
const logoConfirmYes = document.getElementById("logoConfirmYes");
const logoConfirmNo = document.getElementById("logoConfirmNo");
const logoConfirmClose = document.getElementById("logoConfirmClose");

// Logo management functions
let pendingLogoFile = null;

async function loadLogoStatus() {
  try {
    const response = await safeFetch("/api/admin/logo");
    if (!response.ok) throw new Error();
    let data;
    try {
      data = await response.json();
    } catch (e) {
      console.error("Failed to parse logo status JSON:", e);
      return;
    }

    if (data.has_logo && data.base64) {
      logoPreview.src = `data:image/png;base64,${data.base64}`;
      logoPreview.style.display = "block";
      noLogoText.style.display = "none";
    } else {
      logoPreview.style.display = "none";
      noLogoText.style.display = "block";
    }
  } catch (err) {
    console.error("Failed to load logo status:", err);
  }
}

function showLogoStatus(message, isError = false) {
  if (!logoStatus) return;
  logoStatus.classList.remove("hidden");
  logoStatus.className = `test-result-label ${isError ? "test-result-error" : "test-result-success"}`;
  logoStatus.textContent = message;
  setTimeout(() => {
    logoStatus.classList.add("hidden");
  }, 5000);
}

uploadLogoBtn.addEventListener("click", () => {
  logoFileInput.click();
});

logoFileInput.addEventListener("change", (e) => {
  const file = e.target.files[0];
  if (!file) return;

  // Validate file size (max 1MB)
  if (file.size > 1024 * 1024) {
    showLogoStatus("File exceeds 1MB limit", true);
    logoFileInput.value = "";
    return;
  }

  // Validate file type
  const validTypes = ["image/png", "image/jpeg", "image/jpg"];
  if (!validTypes.includes(file.type)) {
    showLogoStatus("Invalid file type. Only PNG, JPG, JPEG allowed.", true);
    logoFileInput.value = "";
    return;
  }

  pendingLogoFile = file;

  // Check if logo already exists and show confirmation
  safeFetch("/api/admin/logo")
    .then(res => res.json())
    .then(data => {
      if (data.has_logo) {
        logoConfirmModal.classList.add("open");
        logoConfirmModal.setAttribute("aria-hidden", "false");
      } else {
        uploadLogoFile();
      }
    })
    .catch(err => {
      console.error("Failed to check logo status:", err);
      uploadLogoFile();
    });
});

logoConfirmYes.addEventListener("click", () => {
  logoConfirmModal.classList.remove("open");
  logoConfirmModal.setAttribute("aria-hidden", "true");
  uploadLogoFile();
});

logoConfirmNo.addEventListener("click", () => {
  logoConfirmModal.classList.remove("open");
  logoConfirmModal.setAttribute("aria-hidden", "true");
  pendingLogoFile = null;
  logoFileInput.value = "";
});

logoConfirmClose.addEventListener("click", () => {
  logoConfirmModal.classList.remove("open");
  logoConfirmModal.setAttribute("aria-hidden", "true");
  pendingLogoFile = null;
  logoFileInput.value = "";
});

async function uploadLogoFile() {
  if (!pendingLogoFile) return;

  const formData = new FormData();
  formData.append("logo", pendingLogoFile);

  try {
    const response = await safeFetch("/api/admin/logo?confirm=true", {
      method: "POST",
      body: formData
    });

    let data;
    try {
      data = await response.json();
    } catch (e) {
      console.error("Failed to parse logo upload response JSON:", e);
      showLogoStatus("Error: Invalid server response", true);
      return;
    }

    if (response.ok) {
      showLogoStatus("Logo uploaded successfully");
      await loadLogoStatus();
    } else {
      showLogoStatus(data.error || "Upload failed", true);
    }
  } catch (err) {
    showLogoStatus(`Error: ${err.message}`, true);
  } finally {
    pendingLogoFile = null;
    logoFileInput.value = "";
  }
}

deleteLogoBtn.addEventListener("click", async () => {
  if (!confirm("Are you sure you want to remove the custom logo?")) return;

  try {
    const response = await safeFetch("/api/admin/logo", { method: "DELETE" });
    let data;
    try {
      data = await response.json();
    } catch (e) {
      console.error("Failed to parse logo delete response JSON:", e);
      showLogoStatus("Error: Invalid server response", true);
      return;
    }

    if (response.ok) {
      showLogoStatus("Logo removed successfully");
      await loadLogoStatus();
    } else {
      showLogoStatus(data.error || "Delete failed", true);
    }
  } catch (err) {
    showLogoStatus(`Error: ${err.message}`, true);
  }
});

// Load logo status when admin tab is activated
const adminTab = document.querySelector('[data-tab="adminPanel"]');
if (adminTab) {
  adminTab.addEventListener("click", loadLogoStatus);
}

// --- END OF FILE frontend/admin/logoManagement.js ---
