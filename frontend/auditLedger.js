// --- START OF FILE frontend/auditLedger.js ---
// Audit history and certificate management

// These elements are defined in the main app.js file
const historyList = document.getElementById("historyList");
const historyQuery = document.getElementById("historyQuery");
const historyStatusFilter = document.getElementById("historyStatusFilter");
const historyRefreshButton = document.getElementById("historyRefreshButton");
const bulkSelectToggleBtn = document.getElementById("bulkSelectToggleBtn");
const bulkCertActionFooter = document.getElementById("bulkCertActionFooter");
const bulkSelectedCountLabel = document.getElementById("bulkSelectedCountLabel");
const bulkCertDownloadBtn = document.getElementById("bulkCertDownloadBtn");

if (!historyList || !historyQuery || !historyStatusFilter || !historyRefreshButton ||
    !bulkSelectToggleBtn || !bulkCertActionFooter || !bulkSelectedCountLabel || !bulkCertDownloadBtn) {
  console.error("Critical: One or more audit ledger elements not found in DOM");
}

// Bulk selection state
let bulkSelectMode = false;
const bulkSelectedJobs = new Set();

// Clear bulk selection state (called when switching away from audit tab)
function clearBulkSelectionState() {
  bulkSelectMode = false;
  bulkSelectedJobs.clear();
  if (bulkSelectToggleBtn) {
    bulkSelectToggleBtn.textContent = "Bulk Select: OFF";
  }
  updateBulkFooter();
}

async function loadHistoryIndex() {
  const query = historyQuery.value.trim();
  const filter = historyStatusFilter.value;
  try {
    const response = await safeFetch(`/api/erase/history?query=${encodeURIComponent(query)}&limit=100`);
    if (!response.ok) throw new Error("HTTP " + response.status);
    let data;
    try {
      data = await response.json();
    } catch (e) {
      console.error("Failed to parse history JSON:", e);
      throw new Error("Invalid JSON response from history API");
    }
    currentHistoryJobs = Array.isArray(data?.jobs) ? data.jobs : [];
    
    let filtered = currentHistoryJobs;
    if (filter !== "all") {
      filtered = filtered.filter(j => j.status === filter);
    }
    
    // Preserve selection state: only remove from bulkSelectedJobs if jobs no longer exist in currentHistoryJobs
    const validJobIds = new Set(currentHistoryJobs.map(j => j.id));
    const removedCount = bulkSelectedJobs.size - [...bulkSelectedJobs].filter(id => validJobIds.has(id)).length;
    if (removedCount > 0) {
      // Remove job IDs that no longer exist in the dataset
      for (const jobId of bulkSelectedJobs) {
        if (!validJobIds.has(jobId)) {
          bulkSelectedJobs.delete(jobId);
        }
      }
    }
    
    renderAuditLedger(filtered);
    updateBulkFooter();
  } catch (err) {
    historyList.innerHTML = `<div class="history-empty">Failed to load records: ${escapeHtml(err.message)}</div>`;
  }
}

function renderAuditLedger(jobs) {
  if (!jobs.length) {
    historyList.innerHTML = '<div class="history-empty">No audit matching database entries found.</div>';
    return;
  }

  historyList.innerHTML = jobs.map(job => {
    const isExpanded = ledgerExpandedJobs.has(job.id);
    const detailsHtml = isExpanded ? renderExpandedAuditRow(job) : "";
    const isSelected = bulkSelectedJobs.has(job.id);
    
    const uiBadge = job.status === "completed" ? "status-badge--complete" : job.status === "failed" ? "status-badge--failed" : job.status === "running" ? "status-badge--running" : "status-badge--queued";
    const statusLabel = job.status === "completed" ? "PASSED" : job.status.toUpperCase();

    const checkboxHtml = bulkSelectMode 
      ? `<input type="checkbox" class="bulk-checkbox" data-job-id="${escapeHtml(job.id)}" ${isSelected ? "checked" : ""}>` 
      : "";

    return `
      <article class="audit-row" data-audit-job-id="${escapeHtml(job.id)}">
        <div class="audit-summary-line ${bulkSelectMode ? 'bulk-mode' : ''}">
          ${checkboxHtml}
          <div class="job-id-text">${escapeHtml(job.friendly_id || "CERT-************")}</div>
          <div class="ticket-text">${escapeHtml(job.request?.ticket_number || "-")}</div>
          <div style="font-weight: 700;">${escapeHtml(job.request?.model || "Generic")}</div>
          <div style="font-size: 0.8rem; font-family: monospace;">S/N: ${escapeHtml(job.request?.serial || "-")}</div>
          <div class="audit-status-chip">
            <span class="status-badge ${uiBadge}">${escapeHtml(statusLabel)}</span>
          </div>
        </div>
        ${detailsHtml}
      </article>
    `;
  }).join("");
}

function renderExpandedAuditRow(job) {
  const isCompleted = job.status === "completed";
  const isFailed = job.status === "failed";
  const isBulkCert = job.job_type === "bulk_cert";
  
  let diagnosticsHtml = "";
  if (isFailed) {
    const errText = job.error || "Unknown Error";
    const stderrText = job.result?.stderr || job.result?.stdout || "No console stderr captured.";
    const exitCode = job.result?.exit_code !== undefined ? job.result.exit_code : "N/A";
    
    diagnosticsHtml = `
      <div class="detail-section" style="grid-column: span 2; border-color: var(--color-danger); background: #220a0d; margin-top: 12px; padding: 14px;">
        <h4 style="color: var(--color-danger); margin-bottom: 6px; font-weight: 800; font-size: 0.75rem; letter-spacing: 0.5px;">⚠️ OPERATION FAILURE DIAGNOSTICS</h4>
        <div class="kv"><span>System Error Code:</span><span style="color: var(--color-danger) !important; font-weight: 800;">${escapeHtml(errText)}</span></div>
        <div class="kv"><span>Process Exit Code:</span><span>${escapeHtml(exitCode)}</span></div>
        <div style="margin-top: 10px;">
          <div style="font-size: 0.65rem; font-weight: 800; text-transform: uppercase; color: var(--color-text-muted); margin-bottom: 4px; letter-spacing: 0.5px;">Raw Disk Controller Console Output (stderr)</div>
          <pre class="terminal-pre" style="background: #000; border-color: #4c1d1d; max-height: 180px; color: #fecaca; white-space: pre-wrap; font-size: 11px;">${escapeHtml(stderrText)}</pre>
        </div>
      </div>
    `;
  }

  const isPrintable = job.status === "completed" || job.status === "failed";

  // Bulk cert jobs have different metadata and actions
  if (isBulkCert) {
    const targetCount = job.result?.total_jobs || job.request?.total_jobs || job.request?.target_job_ids?.length || 0;
    return `
      <div class="expanded-audit-details">
        <div class="audit-meta-col">
          <div class="kv"><span>Job Type:</span><span style="color: var(--color-primary); font-weight: 800;">Bulk Certificate Generation</span></div>
          <div class="kv"><span>Target Certificates:</span><span>${escapeHtml(targetCount)}</span></div>
          <div class="kv"><span>Created At:</span><span>${escapeHtml(formatIsoDate(job.created_at))}</span></div>
          <div class="kv"><span>Finished At:</span><span>${escapeHtml(formatIsoDate(job.finished_at))}</span></div>
        </div>
        <div class="audit-actions-col">
          <div style="font-size: 0.72rem; font-weight: 800; text-transform: uppercase; color: var(--color-text-muted); text-align: center;">Distribution Actions</div>
          <div class="audit-actions-grid">
            <button type="button" data-bulk-cert-id="${escapeHtml(job.friendly_id)}" data-action="print" ${isCompleted ? "" : "disabled"} style="padding: 6px;">Print Bulk</button>
            <button type="button" data-bulk-cert-id="${escapeHtml(job.friendly_id)}" data-action="download" ${isCompleted ? "" : "disabled"} style="padding: 6px;">Download Bulk HTML</button>
          </div>
        </div>
        ${diagnosticsHtml}
      </div>
    `;
  }

  // Regular erase jobs
  // Phase 6 Feature C: Pre/Post-Wipe Diff UI
  let smartDiffHtml = "";
  if (isCompleted && job.has_pre_wipe_snapshot && job.has_post_wipe_snapshot) {
    const smartDiff = job.smart_diff || {};
    const worsenedMetrics = smartDiff.worsened_metrics || [];
    
    let metricsHtml = "";
    if (worsenedMetrics.length > 0) {
      metricsHtml = worsenedMetrics.map(metric => `
        <div class="kv" style="margin-top: 4px;">
          <span style="color: var(--color-error);">${escapeHtml(metric.metric || "Unknown")}:</span>
          <span>${escapeHtml(String(metric.pre_value || "N/A"))} → ${escapeHtml(String(metric.post_value || "N/A"))} (Δ${escapeHtml(String(metric.delta || "N/A"))})</span>
        </div>
      `).join("");
    } else {
      metricsHtml = `<div style="margin-top: 8px; font-size: 0.7rem; color: var(--color-success);">No significant SMART metric degradation detected</div>`;
    }
    
    smartDiffHtml = `
      <div class="detail-section" style="grid-column: span 2; margin-top: 12px; padding: 14px; background: var(--color-surface-2); border: 1px solid var(--color-border);">
        <h4 style="margin-bottom: 8px; font-weight: 800; font-size: 0.75rem; letter-spacing: 0.5px;">SMART Data Comparison (Pre-Wipe vs Post-Wipe)</h4>
        <div style="font-size: 0.7rem; color: var(--color-text-muted); margin-bottom: 8px;">SMART snapshots captured before and after sanitization. Worsened metrics are flagged.</div>
        <div class="kv"><span>Pre-Wipe Snapshot:</span><span style="color: var(--color-success);">✓ Captured</span></div>
        <div class="kv"><span>Post-Wipe Snapshot:</span><span style="color: var(--color-success);">✓ Captured</span></div>
        ${metricsHtml}
      </div>
    `;
  }

  return `
    <div class="expanded-audit-details">
      <div class="audit-meta-col">
        <div class="kv"><span>Technician:</span><span>${escapeHtml(job.request?.technician || "-")}</span></div>
        <div class="kv"><span>Target Device:</span><span>${escapeHtml(job.request?.device || "-")}</span></div>
        <div class="kv"><span>Wipe Method:</span><span>${escapeHtml(job.request?.method || "-")}</span></div>
        <div class="kv"><span>Created At:</span><span>${escapeHtml(formatIsoDate(job.created_at))}</span></div>
        <div class="kv"><span>Finished At:</span><span>${escapeHtml(formatIsoDate(job.finished_at))}</span></div>
      </div>
      <div class="audit-actions-col">
        <div style="font-size: 0.72rem; font-weight: 800; text-transform: uppercase; color: var(--color-text-muted); text-align: center;">Distribution Actions</div>
        <div class="audit-actions-grid">
          <button type="button" data-cert-id="${escapeHtml(job.friendly_id)}" data-action="print" ${isPrintable ? "" : "disabled"} style="padding: 6px;">Print Certificate</button>
          <button type="button" data-cert-id="${escapeHtml(job.friendly_id)}" data-action="html" ${isPrintable ? "" : "disabled"} style="padding: 6px;">HTML Download</button>
          <button type="button" data-cert-id="${escapeHtml(job.friendly_id)}" data-action="json" ${isPrintable ? "" : "disabled"} style="padding: 6px;">JSON Download</button>
          <button type="button" class="copy-fields-btn" data-job-index="${escapeHtml(job.id)}" style="padding: 6px;">Copy Fields</button>
        </div>
      </div>
      ${smartDiffHtml}
      ${diagnosticsHtml}
    </div>
  `;
}

historyList.addEventListener("click", async (event) => {
  const checkbox = event.target.closest(".bulk-checkbox");
  if (checkbox && bulkSelectMode) {
    event.stopPropagation();
    const jobId = checkbox.getAttribute("data-job-id");
    if (checkbox.checked) {
      // Client-side validation: max 100 items (CRITIQUE.md #5)
      if (bulkSelectedJobs.size >= 100) {
        checkbox.checked = false;
        alert("Maximum 100 certificates can be selected at once.");
        return;
      }
      bulkSelectedJobs.add(jobId);
    } else {
      bulkSelectedJobs.delete(jobId);
    }
    updateBulkFooter();
    return;
  }

  const certButton = event.target.closest("[data-cert-id]");
  if (certButton) {
    event.stopPropagation();
    const id = certButton.getAttribute("data-cert-id");
    const act = certButton.getAttribute("data-action");
    
    if (act === "print") {
      openPrintWindow(id);
    } else {
      triggerCertDownload(id, act);
    }
    return;
  }

  // Bulk cert download button handler
  const bulkCertButton = event.target.closest("[data-bulk-cert-id]");
  if (bulkCertButton) {
    event.stopPropagation();
    const friendlyId = bulkCertButton.getAttribute("data-bulk-cert-id");
    const action = bulkCertButton.getAttribute("data-action");
    
    if (action === "print") {
      openBulkPrintWindow(friendlyId);
    } else {
      triggerBulkCertDownload(friendlyId);
    }
    return;
  }

  const copyBtn = event.target.closest(".copy-fields-btn");
  if (copyBtn) {
    event.stopPropagation();
    const jobId = copyBtn.getAttribute("data-job-index");
    const targetJob = currentHistoryJobs.find(j => j.id === jobId);
    if (targetJob) {
      const payload = JSON.stringify({
        job_id: targetJob.friendly_id || targetJob.id,
        technician: targetJob.request?.technician,
        ticket_number: targetJob.request?.ticket_number,
        serial: targetJob.request?.serial,
        status: targetJob.status,
        sha256_hash: targetJob.certificate?.signature
      }, null, 2);
      
      copyTextToClipboard(payload);
    }
    return;
  }

  const row = event.target.closest("[data-audit-job-id]");
  if (!row) return;
  const id = row.getAttribute("data-audit-job-id");
  if (ledgerExpandedJobs.has(id)) {
    ledgerExpandedJobs.delete(id);
  } else {
    ledgerExpandedJobs.add(id);
  }
  renderAuditLedger(currentHistoryJobs);
});

function updateBulkFooter() {
  const count = bulkSelectedJobs.size;
  bulkSelectedCountLabel.textContent = `${count} Certificate${count !== 1 ? "s" : ""} Selected`;
  
  if (count > 0 && bulkSelectMode) {
    bulkCertActionFooter.classList.remove("hidden");
  } else {
    bulkCertActionFooter.classList.add("hidden");
  }
}

if (bulkSelectToggleBtn) {
  bulkSelectToggleBtn.addEventListener("click", () => {
    bulkSelectMode = !bulkSelectMode;
    bulkSelectToggleBtn.textContent = `Bulk Select: ${bulkSelectMode ? "ON" : "OFF"}`;
    
    if (!bulkSelectMode) {
      bulkSelectedJobs.clear();
      updateBulkFooter();
    }
    
    renderAuditLedger(currentHistoryJobs);
  });
}

// Bulk download button handler (CRITIQUE.md #2)
if (bulkCertDownloadBtn) {
  bulkCertDownloadBtn.addEventListener("click", async () => {
    if (bulkSelectedJobs.size === 0) {
      alert("No certificates selected.");
      return;
    }
    
    // Convert job IDs to friendly_ids for API (CRITIQUE.md #4)
    const selectedJobs = currentHistoryJobs.filter(j => bulkSelectedJobs.has(j.id));
    const friendlyIds = selectedJobs.map(j => j.friendly_id).filter(id => id);
    
    if (friendlyIds.length === 0) {
      alert("Selected jobs have no friendly IDs.");
      return;
    }
    
    try {
      const response = await safeFetch("/api/admin/bulk-cert/create", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_ids: friendlyIds })
      });
      
      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.error || "Failed to create bulk certificate job");
      }
      
      const result = await response.json();
      alert(`Bulk certificate job started (Job ID: ${result.job_id}). Check the audit ledger for completion.`);
      
      // Clear selection after successful submission
      bulkSelectedJobs.clear();
      updateBulkFooter();
      
      // Refresh the ledger to show the new bulk cert job (CRITIQUE.md #6 - removed redundant re-render)
      await loadHistoryIndex();
    } catch (err) {
      alert(`Failed to create bulk certificate job: ${err.message}`);
    }
  });
}

function triggerCertDownload(friendlyId, format) {
  const url = `/api/certificates/${encodeURIComponent(friendlyId)}?format=${encodeURIComponent(format)}`;
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.target = "_blank";
  anchor.rel = "noopener";
  anchor.click();
}

function triggerBulkCertDownload(friendlyId) {
  // Bulk cert files are stored in the certificate result
  const targetJob = currentHistoryJobs.find(j => j.friendly_id === friendlyId);
  if (!targetJob) {
    alert("Job not found.");
    return;
  }
  
  // Check if job has a certificate at all (CRITIQUE.md #4)
  if (!targetJob.certificate) {
    alert("This job does not have a certificate. Only completed bulk certificate generation jobs can be downloaded.");
    return;
  }
  
  const bulkPath = targetJob.certificate.bulk_html_path;
  if (!bulkPath) {
    alert("Bulk certificate file not available. The job may still be processing or failed to generate the file.");
    return;
  }
  
  // Use the bulk-html endpoint with the job's friendly_id to download the pre-generated file
  const url = `/api/certificates/${encodeURIComponent(friendlyId)}?format=html&bulk=true`;
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.target = "_blank";
  anchor.rel = "noopener";
  anchor.click();
}

async function openPrintWindow(friendlyId) {
  const printWindow = window.open("", "_blank");
  if (!printWindow) {
    alert("Popup blocked! Enable popups to allow certificate printing.");
    return;
  }

  printWindow.document.documentElement.innerHTML = `
    <!doctype html>
    <html lang="en">
    <head><title>Loading Certificate...</title></head>
    <body style="font-family: Arial, sans-serif; padding: 32px; text-align: center; color: #555;">
      <h2 style="margin-bottom: 8px;">Retrieving compliance record...</h2>
      <p>Fetching the HTML certificate layout from the station.</p>
    </body>
    </html>
  `;

  try {
    const response = await safeFetch(`/api/certificates/${encodeURIComponent(friendlyId)}?format=html`);
    if (!response.ok) throw new Error("HTTP " + response.status);
    const htmlContent = await response.text();

    printWindow.document.documentElement.innerHTML = htmlContent;
    printWindow.focus();
    printWindow.print();
  } catch (err) {
    printWindow.document.documentElement.innerHTML = `
      <!doctype html>
      <html lang="en">
      <head><title>Error Retreiving Certificate</title></head>
      <body style="font-family: Arial, sans-serif; padding: 32px; text-align: center; color: #dc2626;">
        <h2>Retrieval failure occurred</h2>
        <p style="color: #555;">Error details: ${err.message}</p>
      </body>
      </html>
    `;
  }
}

async function openBulkPrintWindow(friendlyId) {
  const printWindow = window.open("", "_blank");
  if (!printWindow) {
    alert("Popup blocked! Enable popups to allow certificate printing.");
    return;
  }

  printWindow.document.documentElement.innerHTML = `
    <!doctype html>
    <html lang="en">
    <head><title>Loading Bulk Certificate...</title></head>
    <body style="font-family: Arial, sans-serif; padding: 32px; text-align: center; color: #555;">
      <h2 style="margin-bottom: 8px;">Retrieving bulk compliance records...</h2>
      <p>Fetching the bulk HTML certificate layout from the station.</p>
    </body>
    </html>
  `;

  try {
    const response = await safeFetch(`/api/certificates/${encodeURIComponent(friendlyId)}?format=html&bulk=true`);
    if (!response.ok) throw new Error("HTTP " + response.status);
    const htmlContent = await response.text();

    printWindow.document.documentElement.innerHTML = htmlContent;
    printWindow.focus();
    printWindow.print();
  } catch (err) {
    printWindow.document.documentElement.innerHTML = `
      <!doctype html>
      <html lang="en">
      <head><title>Error Retrieving Bulk Certificate</title></head>
      <body style="font-family: Arial, sans-serif; padding: 32px; text-align: center; color: #dc2626;">
        <h2>Retrieval failure occurred</h2>
        <p style="color: #555;">Error details: ${err.message}</p>
      </body>
      </html>
    `;
  }
}

historyQuery.addEventListener("input", loadHistoryIndex);
historyStatusFilter.addEventListener("change", loadHistoryIndex);
historyRefreshButton.addEventListener("click", loadHistoryIndex);
// --- END OF FILE frontend/auditLedger.js ---
