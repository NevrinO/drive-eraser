// --- START OF FILE frontend/admin/templateManagement.js ---
// Template CRUD, import/export, visual preview and traversal animation

// Template management elements
let createTemplateBtn, exportTemplatesBtn, importTemplatesBtn, templateImportFile;
let templateList, templateStatus, templateModal, templateModalTitle, templateModalClose, templateModalError;
let templateForm, templateFormSubmit, templateId, templateName, templateVendor;
let templateBayCount, templateRows, templateCols, templateTraversal, templateSkipPositions;

// Template preview elements
let templatePreviewModal, templatePreviewTitle, templatePreviewClose;
let templatePreviewInfo, templatePreviewGrid, previewAnimateBtn, previewResetBtn;

// Template management functions
let editingTemplateId = null;

// Initialize elements and event listeners when DOM is ready
function initializeTemplateManagement() {
  // Template management elements
  createTemplateBtn = document.getElementById("createTemplateBtn");
  exportTemplatesBtn = document.getElementById("exportTemplatesBtn");
  importTemplatesBtn = document.getElementById("importTemplatesBtn");
  templateImportFile = document.getElementById("templateImportFile");
  templateList = document.getElementById("templateList");
  templateStatus = document.getElementById("templateStatus");
  templateModal = document.getElementById("templateModal");
  templateModalTitle = document.getElementById("templateModalTitle");
  templateModalClose = document.getElementById("templateModalClose");
  templateModalError = document.getElementById("templateModalError");
  templateForm = document.getElementById("templateForm");
  templateFormSubmit = document.getElementById("templateFormSubmit");
  templateId = document.getElementById("templateId");
  templateName = document.getElementById("templateName");
  templateVendor = document.getElementById("templateVendor");
  templateBayCount = document.getElementById("templateBayCount");
  templateRows = document.getElementById("templateRows");
  templateCols = document.getElementById("templateCols");
  templateTraversal = document.getElementById("templateTraversal");
  templateSkipPositions = document.getElementById("templateSkipPositions");

  // Template preview elements
  templatePreviewModal = document.getElementById("templatePreviewModal");
  templatePreviewTitle = document.getElementById("templatePreviewTitle");
  templatePreviewClose = document.getElementById("templatePreviewClose");
  templatePreviewInfo = document.getElementById("templatePreviewInfo");
  templatePreviewGrid = document.getElementById("templatePreviewGrid");
  previewAnimateBtn = document.getElementById("previewAnimateBtn");
  previewResetBtn = document.getElementById("previewResetBtn");

  if (!createTemplateBtn || !exportTemplatesBtn || !importTemplatesBtn || !templateImportFile ||
      !templateList || !templateStatus ||
      !templateModal || !templateModalTitle || !templateModalClose || !templateModalError ||
      !templateForm || !templateFormSubmit || !templateId || !templateName ||
      !templateVendor || !templateBayCount || !templateRows || !templateCols ||
      !templateTraversal || !templateSkipPositions ||
      !templatePreviewModal || !templatePreviewTitle || !templatePreviewClose ||
      !templatePreviewInfo || !templatePreviewGrid || !previewAnimateBtn || !previewResetBtn) {
    console.error("Critical: One or more template management elements not found in DOM");
    console.error("createTemplateBtn:", createTemplateBtn);
    console.error("templateModal:", templateModal);
    console.error("templateList:", templateList);
    return;
  }

  // Attach event listeners
  if (createTemplateBtn) {
    createTemplateBtn.addEventListener("click", () => {
      openTemplateModal();
    });
  }
  if (templateModalClose) {
    templateModalClose.addEventListener("click", closeTemplateModal);
  }
  if (templateForm) {
    templateForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      templateFormSubmit.disabled = true;
      templateFormSubmit.textContent = "Saving...";

      try {
        const skipPositionsStr = templateSkipPositions.value.trim();
        const skipBayNumbers = skipPositionsStr ? skipPositionsStr.split(",").map(s => parseInt(s.trim(), 10)).filter(n => !isNaN(n)) : [];
        const cols = parseInt(templateCols.value, 10);
        const skipPositions = skipBayNumbers.length > 0 ? bayNumbersToRowCol(skipBayNumbers, cols) : [];

        const templateData = {
          id: templateId.value.trim(),
          name: templateName.value.trim(),
          vendor: templateVendor.value.trim(),
          bay_count: parseInt(templateBayCount.value, 10),
          rows: parseInt(templateRows.value, 10),
          cols: parseInt(templateCols.value, 10),
          traversal_preset: templateTraversal.value,
          skip_positions: skipPositions
        };

        if (!templateData.id) {
          throw new Error("Template ID is required");
        }
        // CRITIQUE.md #1: Validate template ID format (lowercase, numbers, hyphens only)
        const idRegex = /^[a-z0-9-]+$/;
        if (!idRegex.test(templateData.id)) {
          throw new Error("Template ID must contain only lowercase letters, numbers, and hyphens");
        }
        if (!templateData.name) {
          throw new Error("Template name is required");
        }
        if (templateData.bay_count < 1 || templateData.bay_count > 128) {
          throw new Error("Bay count must be between 1 and 128");
        }
        if (templateData.rows < 1 || templateData.rows > 16) {
          throw new Error("Grid rows must be between 1 and 16");
        }
        if (templateData.cols < 1 || templateData.cols > 5) {
          throw new Error("Grid columns must be between 1 and 5");
        }
        if (templateData.bay_count > templateData.rows * templateData.cols) {
          throw new Error("Bay count cannot exceed rows × columns");
        }
        // CRITIQUE.md #2: Validate skip_positions range (must be 1 to grid size)
        if (skipBayNumbers.some(bayNum => bayNum < 1 || bayNum > (templateData.rows * templateData.cols))) {
          throw new Error("Skip positions must be between 1 and grid size (rows × columns)");
        }
        // CRITIQUE.md #3: Validate skip_positions array size limit
        if (skipBayNumbers.length > templateData.bay_count) {
          throw new Error("Cannot skip more positions than total bays");
        }
        if (skipBayNumbers.length > 128) {
          throw new Error("Skip positions cannot exceed 128 items");
        }

        await saveTemplate(templateData);
      } catch (err) {
        showTemplateModalError(`Error: ${err.message}`);
      } finally {
        templateFormSubmit.disabled = false;
        templateFormSubmit.textContent = editingTemplateId ? "Update Template" : "Create Template";
      }
    });
  }
  if (templateList) {
    templateList.addEventListener("click", async (e) => {
      const btn = e.target.closest(".btn-template-action");
      if (!btn) return;

      const templateId = btn.getAttribute("data-template-id");
      const action = btn.getAttribute("data-action");

      if (action === "preview") {
        const template = availableLayoutTemplates.find(t => t.id === templateId);
        if (template) {
          openTemplatePreview(template);
        }
      } else if (action === "edit") {
        const template = availableLayoutTemplates.find(t => t.id === templateId);
        if (template) {
          openTemplateModal(template);
        }
      } else if (action === "delete") {
        await deleteTemplate(templateId);
      }
    });
  }
  // Load template list when admin tab is activated
  const templateAdminTab = document.querySelector('[data-tab="adminPanel"]');
  if (templateAdminTab) {
    templateAdminTab.addEventListener("click", loadTemplateList);
  }
  // Event listeners for import/export
  if (exportTemplatesBtn) {
    exportTemplatesBtn.addEventListener("click", exportTemplates);
  }
  if (importTemplatesBtn) {
    importTemplatesBtn.addEventListener("click", () => {
      templateImportFile.click();
    });
  }
  if (templateImportFile) {
    templateImportFile.addEventListener("change", importTemplates);
  }

  // Template preview event listeners
  if (templatePreviewClose) {
    templatePreviewClose.addEventListener("click", closeTemplatePreview);
  }
  if (previewAnimateBtn) {
    previewAnimateBtn.addEventListener("click", () => {
      animateTraversal();
    });
  }
  if (previewResetBtn) {
    previewResetBtn.addEventListener("click", () => {
      resetPreviewGrid();
    });
  }
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initializeTemplateManagement);
} else {
  initializeTemplateManagement();
}

function showTemplateStatus(message, isError = false, isWarning = false) {
  if (!templateStatus) return;
  templateStatus.classList.remove("hidden");
  if (isWarning) {
    templateStatus.className = "test-result-label test-result-warning";
  } else {
    templateStatus.className = `test-result-label ${isError ? "test-result-error" : "test-result-success"}`;
  }
  templateStatus.textContent = message;
  setTimeout(() => {
    templateStatus.classList.add("hidden");
  }, 5000);
}

function showTemplateModalError(message) {
  if (!templateModalError) return;
  templateModalError.classList.remove("hidden");
  templateModalError.className = "test-result-label test-result-error";
  templateModalError.textContent = message;
}

function clearTemplateModalError() {
  if (!templateModalError) return;
  templateModalError.classList.add("hidden");
  templateModalError.textContent = "";
}

// Convert bay numbers (1-indexed) to row/col coordinates (0-indexed)
function bayNumbersToRowCol(bayNumbers, cols) {
  if (cols <= 0) {
    throw new Error("Columns must be greater than 0");
  }
  return bayNumbers.map(bayNum => {
    const bayIndex = bayNum - 1; // Convert to 0-indexed
    const row = Math.floor(bayIndex / cols);
    const col = bayIndex % cols;
    return { row, col };
  });
}

// Convert row/col coordinates (0-indexed) to bay numbers (1-indexed) based on traversal order
function rowColToBayNumbers(skipPositions, cols, traversal = "top_left_down_then_across", rows = null) {
  if (cols <= 0) {
    throw new Error("Columns must be greater than 0");
  }
  // Build a map of (row,col) -> bay number based on traversal order
  // Include ALL positions to determine what bay number skipped positions would have had
  const bayNumberMap = new Map();
  let bayCounter = 1;

  // Determine grid dimensions: use provided rows, or derive from skip positions if not provided
  const maxRow = rows !== null ? rows - 1 : (skipPositions.length > 0 ? Math.max(...skipPositions.map(p => p.row)) : 0);

  if (traversal === "bottom_left_up_then_across") {
    for (let col = 0; col < cols; col++) {
      for (let row = maxRow; row >= 0; row--) {
        const posKey = `${row},${col}`;
        bayNumberMap.set(posKey, bayCounter++);
      }
    }
  } else if (traversal === "top_left_across_then_down") {
    for (let row = 0; row <= maxRow; row++) {
      for (let col = 0; col < cols; col++) {
        const posKey = `${row},${col}`;
        bayNumberMap.set(posKey, bayCounter++);
      }
    }
  } else if (traversal === "bottom_left_across_then_up") {
    for (let row = maxRow; row >= 0; row--) {
      for (let col = 0; col < cols; col++) {
        const posKey = `${row},${col}`;
        bayNumberMap.set(posKey, bayCounter++);
      }
    }
  } else {
    // top_left_down_then_across (default)
    for (let col = 0; col < cols; col++) {
      for (let row = 0; row <= maxRow; row++) {
        const posKey = `${row},${col}`;
        bayNumberMap.set(posKey, bayCounter++);
      }
    }
  }

  return skipPositions.map(({ row, col }) => {
    const posKey = `${row},${col}`;
    return bayNumberMap.get(posKey) || 0;
  });
}

async function loadTemplateList() {
  templateList.innerHTML = '<div style="color: #888; font-size: 0.8rem; padding: 8px;">Loading templates...</div>';

  // Ensure availableLayoutTemplates is loaded before rendering
  if (availableLayoutTemplates.length === 0) {
    await loadLayoutTemplates();
  }

  try {
    const response = await safeFetch("/api/admin/layout-templates");
    if (!response.ok) throw new Error("Failed to load templates");
    let data;
    try {
      data = await response.json();
    } catch (e) {
      console.error("Failed to parse templates JSON:", e);
      throw new Error("Invalid JSON response");
    }

    // Show warning if templates are from fallback (missing/corrupted file)
    if (data.source === "fallback") {
      showTemplateStatus("Warning: Template file missing or corrupted. Using default templates.", false, true);
    }

    const templates = Array.isArray(data.templates) ? data.templates : [];
    templateList.innerHTML = "";

    if (templates.length === 0) {
      templateList.innerHTML = '<div style="color: #888; font-size: 0.8rem; padding: 8px;">No custom templates found. Create one to get started.</div>';
      return;
    }

    templates.forEach(template => {
      const item = document.createElement("div");
      item.className = "template-item";
      item.style.cssText = "display: flex; justify-content: space-between; align-items: center; padding: 8px; border-bottom: 1px solid #333; background: #222;";
      item.innerHTML = `
        <div>
          <div style="font-weight: bold; color: var(--color-primary);">${escapeHtml(template.name)}</div>
          <div style="font-size: 0.7rem; color: #888;">ID: ${escapeHtml(template.id)} | Bays: ${template.bay_count || 0} | Vendor: ${escapeHtml(template.vendor || "Generic")}</div>
        </div>
        <div style="display: flex; gap: 8px;">
          <button type="button" data-template-id="${escapeHtml(template.id)}" data-action="preview" class="btn-template-action" style="padding: 4px 8px; font-size: 0.7rem;">Preview</button>
          <button type="button" data-template-id="${escapeHtml(template.id)}" data-action="edit" class="btn-template-action" style="padding: 4px 8px; font-size: 0.7rem;">Edit</button>
          <button type="button" data-template-id="${escapeHtml(template.id)}" data-action="delete" class="btn-template-action" style="padding: 4px 8px; font-size: 0.7rem; background: var(--color-danger); border-color: var(--color-danger);">Delete</button>
        </div>
      `;
      templateList.appendChild(item);
    });
  } catch (err) {
    templateList.innerHTML = `<div style="color: var(--color-danger); font-size: 0.8rem; padding: 8px;">Failed to load templates: ${err.message}</div>`;
  }
}

function openTemplateModal(template = null) {
  // Clear any previous errors first
  clearTemplateModalError();
  editingTemplateId = template ? template.id : null;
  templateModalTitle.textContent = template ? "Edit Layout Template" : "Create Layout Template";
  templateFormSubmit.textContent = template ? "Update Template" : "Create Template";

  if (template) {
    templateId.value = template.id;
    templateId.disabled = true;
    templateName.value = template.name || "";
    templateVendor.value = template.vendor || "";
    templateBayCount.value = template.bay_count || 1;
    templateRows.value = template.rows || 1;
    templateCols.value = template.cols || 1;
    templateTraversal.value = template.traversal_preset || "top_left_down_then_across";
    // Convert row/col objects back to bay numbers for display (row-major order)
    const cols = template.cols || 1;
    const rows = template.rows || 1;
    const skipBayNumbers = template.skip_positions ? rowColToBayNumbers(template.skip_positions, cols, "top_left_across_then_down", rows) : [];
    templateSkipPositions.value = skipBayNumbers.length > 0 ? skipBayNumbers.join(",") : "";
  } else {
    templateId.value = "";
    templateId.disabled = false;
    templateName.value = "";
    templateVendor.value = "";
    templateBayCount.value = 16;
    templateRows.value = 4;
    templateCols.value = 4;
    templateTraversal.value = "top_left_down_then_across";
    templateSkipPositions.value = "";
  }

  templateModal.classList.add("open");
  templateModal.setAttribute("aria-hidden", "false");
}

function closeTemplateModal() {
  templateModal.classList.remove("open");
  templateModal.setAttribute("aria-hidden", "true");
  templateForm.reset();
  editingTemplateId = null;
}

async function saveTemplate(templateData) {
  const method = editingTemplateId ? "PUT" : "POST";
  const response = await safeFetch("/api/admin/layout-templates", {
    method: method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(templateData)
  });

  let data;
  try {
    data = await response.json();
  } catch (e) {
    console.error("Failed to parse template save response JSON:", e);
    throw new Error("Invalid server response");
  }

  if (!response.ok) {
    throw new Error(data.error || "Failed to save template");
  }

  showTemplateStatus(editingTemplateId ? "Template updated successfully" : "Template created successfully");
  closeTemplateModal();
  await loadTemplateList();
  await loadLayoutTemplates();
}

async function deleteTemplate(templateId) {
  if (!confirm(`Are you sure you want to delete the template "${templateId}"? This action cannot be undone.`)) {
    return;
  }

  const response = await safeFetch("/api/admin/layout-templates", {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: templateId })
  });

  let data;
  try {
    data = await response.json();
  } catch (e) {
    console.error("Failed to parse template delete response JSON:", e);
    throw new Error("Invalid server response");
  }

  if (!response.ok) {
    throw new Error(data.error || "Failed to delete template");
  }

  showTemplateStatus("Template deleted successfully");
  await loadTemplateList();
  await loadLayoutTemplates();
}

// Template import/export functions
async function exportTemplates() {
  try {
    const response = await safeFetch("/api/admin/layout-templates/export");
    if (!response.ok) {
      let errorData;
      try {
        errorData = await response.json();
      } catch (e) {
        throw new Error("Export failed");
      }
      throw new Error(errorData.error || "Export failed");
    }

    const blob = await response.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = response.headers.get("Content-Disposition")?.match(/filename="(.+)"/)?.[1] || "layout_templates.json";
    document.body.appendChild(a);
    a.click();
    window.URL.revokeObjectURL(url);
    document.body.removeChild(a);

    showTemplateStatus("Templates exported successfully", false);
  } catch (err) {
    showTemplateStatus(`Export failed: ${err.message}`, true);
  }
}

async function importTemplates() {
  const file = templateImportFile.files[0];
  if (!file) {
    showTemplateStatus("Please select a file to import", true);
    return;
  }

  const formData = new FormData();
  formData.append("file", file);

  try {
    const response = await safeFetch("/api/admin/layout-templates/import", {
      method: "POST",
      body: formData
    });
    if (!response.ok) {
      let errorData;
      try {
        errorData = await response.json();
      } catch (e) {
        throw new Error("Import failed");
      }
      throw new Error(errorData.error || "Import failed");
    }

    const result = await response.json();
    let statusMsg = `Imported ${result.imported_count} templates successfully`;
    if (result.overwritten_count > 0) {
      statusMsg += ` (${result.overwritten_count} overwritten)`;
    }
    showTemplateStatus(statusMsg, false);
    
    // Reload template list to show imported templates
    await loadLayoutTemplates();
    await loadTemplateList();
  } catch (err) {
    showTemplateStatus(`Import failed: ${err.message}`, true);
  }

  // Clear file input
  templateImportFile.value = "";
}

// Template preview functions
let previewAnimationInterval = null;
let previewCurrentIndex = 0;
let previewTraversalOrder = [];

function buildTraversalOrder(template) {
  const rows = template.rows || 1;
  const cols = template.cols || 1;
  const bayCount = template.bay_count || (rows * cols);
  const traversal = template.traversal_preset || "top_left_down_then_across";
  const skipPositions = template.skip_positions || [];

  const skipSet = new Set(skipPositions.map(p => `${p.row},${p.col}`));
  const positions = [];

  // Build traversal order - bayNum is not used for display, only for tracking
  if (traversal === "bottom_left_up_then_across") {
    for (let col = 0; col < cols; col++) {
      for (let row = rows - 1; row >= 0; row--) {
        const posKey = `${row},${col}`;
        if (!skipSet.has(posKey) && positions.length < bayCount) {
          positions.push({ row, col });
        }
      }
    }
  } else if (traversal === "top_left_across_then_down") {
    for (let row = 0; row < rows; row++) {
      for (let col = 0; col < cols; col++) {
        const posKey = `${row},${col}`;
        if (!skipSet.has(posKey) && positions.length < bayCount) {
          positions.push({ row, col });
        }
      }
    }
  } else if (traversal === "bottom_left_across_then_up") {
    for (let row = rows - 1; row >= 0; row--) {
      for (let col = 0; col < cols; col++) {
        const posKey = `${row},${col}`;
        if (!skipSet.has(posKey) && positions.length < bayCount) {
          positions.push({ row, col });
        }
      }
    }
  } else {
    // top_left_down_then_across (default)
    for (let col = 0; col < cols; col++) {
      for (let row = 0; row < rows; row++) {
        const posKey = `${row},${col}`;
        if (!skipSet.has(posKey) && positions.length < bayCount) {
          positions.push({ row, col });
        }
      }
    }
  }

  return positions;
}

function renderTemplatePreviewGrid(template) {
  const rows = template.rows || 1;
  const cols = template.cols || 1;

  // Validate grid dimensions to prevent browser crash (CRITIQUE.md #3)
  if (rows < 1 || rows > 32 || cols < 1 || cols > 32) {
    templatePreviewGrid.innerHTML = `<div style="color: var(--color-danger); padding: 16px;">Invalid grid dimensions: ${rows} × ${cols}. Maximum is 32 × 32.</div>`;
    return;
  }

  const skipPositions = template.skip_positions || [];
  const traversal = template.traversal_preset || "top_left_down_then_across";
  const skipPosSet = new Set(skipPositions.map(p => `${p.row},${p.col}`));

  // Build reference bay number map (fixed order: top-left, left-to-right, then down)
  // This is used for skip position input in the edit form
  // Reference numbers count ALL positions (including skipped) to remain consistent
  const refBayNumberMap = new Map();
  let refBayCounter = 1;
  for (let row = 0; row < rows; row++) {
    for (let col = 0; col < cols; col++) {
      const posKey = `${row},${col}`;
      refBayNumberMap.set(posKey, refBayCounter++);
    }
  }

  // Build traversal order number map (based on traversal_preset)
  // This shows the actual order drives will be erased
  const travBayNumberMap = new Map();
  let travBayCounter = 1;

  if (traversal === "bottom_left_up_then_across") {
    for (let col = 0; col < cols; col++) {
      for (let row = rows - 1; row >= 0; row--) {
        const posKey = `${row},${col}`;
        if (!skipPosSet.has(posKey)) {
          travBayNumberMap.set(posKey, travBayCounter++);
        }
      }
    }
  } else if (traversal === "top_left_across_then_down") {
    for (let row = 0; row < rows; row++) {
      for (let col = 0; col < cols; col++) {
        const posKey = `${row},${col}`;
        if (!skipPosSet.has(posKey)) {
          travBayNumberMap.set(posKey, travBayCounter++);
        }
      }
    }
  } else if (traversal === "bottom_left_across_then_up") {
    for (let row = rows - 1; row >= 0; row--) {
      for (let col = 0; col < cols; col++) {
        const posKey = `${row},${col}`;
        if (!skipPosSet.has(posKey)) {
          travBayNumberMap.set(posKey, travBayCounter++);
        }
      }
    }
  } else {
    // top_left_down_then_across (default)
    for (let col = 0; col < cols; col++) {
      for (let row = 0; row < rows; row++) {
        const posKey = `${row},${col}`;
        if (!skipPosSet.has(posKey)) {
          travBayNumberMap.set(posKey, travBayCounter++);
        }
      }
    }
  }

  templatePreviewGrid.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
  templatePreviewGrid.innerHTML = "";

  for (let row = 0; row < rows; row++) {
    for (let col = 0; col < cols; col++) {
      const posKey = `${row},${col}`;
      const isSkipped = skipPosSet.has(posKey);
      const refBayNum = refBayNumberMap.get(posKey);
      const travBayNum = travBayNumberMap.get(posKey);

      const cell = document.createElement("div");
      cell.className = "preview-cell";
      cell.id = `preview-cell-${row}-${col}`;
      if (isSkipped) {
        cell.setAttribute("data-skipped", "true");
      }
      cell.style.cssText = `
        aspect-ratio: 1;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        background: ${isSkipped ? "#111" : "#333"};
        border: 1px solid #444;
        border-radius: 4px;
        font-size: 0.7rem;
        color: ${isSkipped ? "#444" : "#888"};
        transition: all 0.3s ease;
        gap: 3px;
        padding: 2px;
      `;

      if (isSkipped) {
        cell.textContent = "×";
      } else {
        // Show both numbers with labels
        cell.innerHTML = `
          <div style="font-size: 0.6rem; color: #666; line-height: 1.1;">Ref: ${refBayNum || "-"}</div>
          <div style="font-size: 0.8rem; color: var(--color-primary); font-weight: bold; line-height: 1.1;">Tr: ${travBayNum || "-"}</div>
        `;
      }
      templatePreviewGrid.appendChild(cell);
    }
  }

  // Add legend below the grid (append to parent, not grid itself)
  // Remove existing legend if present to avoid duplicates
  const existingLegend = templatePreviewGrid.parentNode.querySelector(".preview-legend");
  if (existingLegend) {
    existingLegend.remove();
  }

  const legend = document.createElement("div");
  legend.className = "preview-legend";
  legend.style.cssText = `
    display: flex;
    justify-content: center;
    align-items: center;
    gap: 32px;
    margin-top: 12px;
    font-size: 0.75rem;
    color: #888;
  `;
  legend.innerHTML = `
    <div><span style="color: #666;">Ref</span> = Reference number (for skip position input)</div>
    <div><span style="color: var(--color-primary); font-weight: bold;">Tr</span> = Traversal order (actual erasure)</div>
  `;
  templatePreviewGrid.parentNode.appendChild(legend);
}

function highlightPreviewCell(row, col, isActive) {
  const cell = document.getElementById(`preview-cell-${row}-${col}`);
  if (cell) {
    const isSkipped = cell.getAttribute("data-skipped") === "true";
    if (isActive) {
      cell.style.background = "var(--color-primary)";
      cell.style.color = "#fff";
      cell.style.transform = "scale(1.1)";
      cell.style.boxShadow = "0 0 10px var(--color-primary)";
    } else {
      cell.style.background = "#4a90e2";
      cell.style.color = "#fff";
      cell.style.transform = "scale(1)";
      cell.style.boxShadow = "none";
    }
  }
}

function resetPreviewGrid() {
  if (previewAnimationInterval) {
    clearInterval(previewAnimationInterval);
    previewAnimationInterval = null;
  }
  previewCurrentIndex = 0;

  const cells = templatePreviewGrid.querySelectorAll(".preview-cell");
  cells.forEach(cell => {
    const isSkipped = cell.getAttribute("data-skipped") === "true";
    cell.style.background = isSkipped ? "#111" : "#333";
    cell.style.color = isSkipped ? "#444" : "#888";
    cell.style.transform = "scale(1)";
    cell.style.boxShadow = "none";
  });
}

function animateTraversal() {
  if (previewAnimationInterval) {
    return; // Animation already running, prevent concurrent animations
  }
  resetPreviewGrid();

  if (previewTraversalOrder.length === 0) {
    return;
  }

  previewAnimationInterval = setInterval(() => {
    if (previewCurrentIndex >= previewTraversalOrder.length) {
      clearInterval(previewAnimationInterval);
      previewAnimationInterval = null;
      return;
    }

    const prevPos = previewCurrentIndex > 0 ? previewTraversalOrder[previewCurrentIndex - 1] : null;
    const currentPos = previewTraversalOrder[previewCurrentIndex];

    if (prevPos) {
      highlightPreviewCell(prevPos.row, prevPos.col, false);
    }

    highlightPreviewCell(currentPos.row, currentPos.col, true);
    previewCurrentIndex++;
  }, 300);
}

function openTemplatePreview(template) {
  templatePreviewTitle.textContent = `Preview: ${template.name}`;
  // Convert row/col objects to bay numbers for display (row-major order)
  const cols = template.cols || 1;
  const rows = template.rows || 1;
  const skipBayNumbers = template.skip_positions ? rowColToBayNumbers(template.skip_positions, cols, "top_left_across_then_down", rows) : [];
  templatePreviewInfo.innerHTML = `
    <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; font-size: 0.8rem;">
      <div><strong>Vendor:</strong> ${escapeHtml(template.vendor || "Generic")}</div>
      <div><strong>Bays:</strong> ${template.bay_count || 0}</div>
      <div><strong>Grid:</strong> ${template.rows || 1} × ${template.cols || 1}</div>
      <div><strong>Traversal:</strong> ${escapeHtml(template.traversal_preset || "top_left_down_then_across")}</div>
      <div style="grid-column: span 2;"><strong>Skip Positions:</strong> ${skipBayNumbers.length > 0 ? skipBayNumbers.join(", ") : "None"}</div>
    </div>
  `;

  previewTraversalOrder = buildTraversalOrder(template);
  renderTemplatePreviewGrid(template);
  resetPreviewGrid();

  templatePreviewModal.classList.add("open");
  templatePreviewModal.setAttribute("aria-hidden", "false");
}

function closeTemplatePreview() {
  templatePreviewModal.classList.remove("open");
  templatePreviewModal.setAttribute("aria-hidden", "true");
  resetPreviewGrid();
}
// --- END OF FILE frontend/admin/templateManagement.js ---
