// --- START OF FILE frontend/auditLedger.js ---
// Audit history and certificate management

// These elements are defined in the main app.js file
const historyList = document.getElementById("historyList");
const historyQuery = document.getElementById("historyQuery");
const historyStatusFilter = document.getElementById("historyStatusFilter");
const historyRefreshButton = document.getElementById("historyRefreshButton");

if (!historyList || !historyQuery || !historyStatusFilter || !historyRefreshButton) {
  console.error("Critical: One or more audit ledger elements not found in DOM");
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
    
    renderAuditLedger(filtered);
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
    
    const uiBadge = job.status === "completed" ? "complete" : job.status === "failed" ? "failed" : job.status === "running" ? "running" : "queued";
    const statusLabel = job.status === "completed" ? "PASSED" : job.status.toUpperCase();

    return `
      <article class="audit-row" data-audit-job-id="${escapeHtml(job.id)}">
        <div class="audit-summary-line">
          <div class="job-id-text">${escapeHtml(job.friendly_id || "SANI-******")}</div>
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
      ${diagnosticsHtml}
    </div>
  `;
}

historyList.addEventListener("click", async (event) => {
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

function triggerCertDownload(friendlyId, format) {
  const url = `/api/certificates/${encodeURIComponent(friendlyId)}?format=${encodeURIComponent(format)}`;
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

  printWindow.document.open();
  printWindow.document.write(`
    <!doctype html>
    <html lang="en">
    <head><title>Loading Certificate...</title></head>
    <body style="font-family: Arial, sans-serif; padding: 32px; text-align: center; color: #555;">
      <h2 style="margin-bottom: 8px;">Retrieving compliance record...</h2>
      <p>Fetching the HTML certificate layout from the station.</p>
    </body>
    </html>
  `);
  printWindow.document.close();

  try {
    const response = await safeFetch(`/api/certificates/${encodeURIComponent(friendlyId)}?format=html`);
    if (!response.ok) throw new Error("HTTP " + response.status);
    const htmlContent = await response.text();

    printWindow.document.open();
    printWindow.document.write(htmlContent);
    printWindow.document.close();
    printWindow.focus();
    printWindow.print();
  } catch (err) {
    printWindow.document.open();
    printWindow.document.write(`
      <!doctype html>
      <html lang="en">
      <head><title>Error Retreiving Certificate</title></head>
      <body style="font-family: Arial, sans-serif; padding: 32px; text-align: center; color: #dc2626;">
        <h2>Retrieval failure occurred</h2>
        <p style="color: #555;">Error details: ${err.message}</p>
      </body>
      </html>
    `);
    printWindow.document.close();
  }
}

historyQuery.addEventListener("input", loadHistoryIndex);
historyStatusFilter.addEventListener("change", loadHistoryIndex);
historyRefreshButton.addEventListener("click", loadHistoryIndex);
// --- END OF FILE frontend/auditLedger.js ---
