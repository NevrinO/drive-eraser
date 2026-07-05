// Log viewer modal: browse, preview, search, and download log files
(function () {
  const logViewerModal = document.getElementById("logViewerModal");
  const logTableBody = document.getElementById("logTableBody");
  const logPreviewPane = document.getElementById("logPreviewPane");
  const logCategoryFilter = document.getElementById("logCategoryFilter");
  const logNameFilter = document.getElementById("logNameFilter");
  const logRefreshBtn = document.getElementById("logRefreshBtn");
  const logSearchInput = document.getElementById("logSearchInput");
  const logCaseSensitive = document.getElementById("logCaseSensitive");
  const logSearchBtn = document.getElementById("logSearchBtn");
  const logClearSearchBtn = document.getElementById("logClearSearchBtn");

  let allLogFiles = [];
  let currentPathKey = null;

  function openLogViewer() {
    if (!logViewerModal) return;
    openModal(logViewerModal);
    loadLogList();
  }

  async function loadLogList() {
    if (!logTableBody) return;
    logTableBody.innerHTML = '<tr><td colspan="5" class="text-muted-xs">Loading...</td></tr>';
    try {
      const response = await safeFetch("/api/admin/logs");
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      allLogFiles = Array.isArray(data) ? data : [];
      renderLogTable();
    } catch (err) {
      logTableBody.innerHTML = `<tr><td colspan="5" class="text-muted-xs">Failed to load logs: ${escapeHtml(err.message)}</td></tr>`;
    }
  }

  function renderLogTable() {
    if (!logTableBody) return;
    const category = logCategoryFilter ? logCategoryFilter.value : "all";
    const nameFilter = logNameFilter ? logNameFilter.value.toLowerCase().trim() : "";

    const filtered = allLogFiles.filter(function (f) {
      if (category !== "all" && f.category !== category) return false;
      if (nameFilter && !f.name.toLowerCase().includes(nameFilter)) return false;
      return true;
    });

    if (filtered.length === 0) {
      logTableBody.innerHTML = '<tr><td colspan="5" class="text-muted-xs">No log files found.</td></tr>';
      return;
    }

    logTableBody.innerHTML = filtered.map(function (f) {
      const sizeStr = formatSize(f.size_bytes);
      const modStr = formatModifiedDate(f.modified_at);
      return `<tr class="log-file-row">
        <td>${escapeHtml(f.name)}</td>
        <td>${escapeHtml(f.category)}</td>
        <td>${sizeStr}</td>
        <td>${modStr}</td>
        <td>
          <button type="button" class="btn btn--secondary btn-sm" data-log-preview="${escapeHtml(f.path_key)}">Preview</button>
          <button type="button" class="btn btn--secondary btn-sm" data-log-download="${escapeHtml(f.path_key)}">Download</button>
        </td>
      </tr>`;
    }).join("");
  }

  function formatSize(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  }

  function formatModifiedDate(isoStr) {
    try {
      const d = new Date(isoStr);
      return d.toLocaleString();
    } catch {
      return isoStr || "-";
    }
  }

  async function previewLog(pathKey) {
    if (!logPreviewPane) return;
    currentPathKey = pathKey;
    logPreviewPane.textContent = "Loading...";
    try {
      const response = await safeFetch(`/api/admin/logs/${encodeURIComponent(pathKey)}/preview?lines=200`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      logPreviewPane.textContent = data.content || "(empty file)";
      autoScrollPreview();
    } catch (err) {
      logPreviewPane.textContent = `Failed to load preview: ${err.message}`;
    }
  }

  async function searchLogContent(pathKey, query, caseSensitive) {
    if (!logPreviewPane) return;
    currentPathKey = pathKey;
    logPreviewPane.textContent = "Searching...";
    try {
      const params = new URLSearchParams({ lines: "1000", q: query });
      if (caseSensitive) params.set("case_sensitive", "true");
      const response = await safeFetch(`/api/admin/logs/${encodeURIComponent(pathKey)}/preview?${params.toString()}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      let header = `Search for "${query}" — ${data.lines_returned} match(es)`;
      if (data.regex_fallback) header += " (invalid regex, used literal search)";
      header += "\n\n";
      logPreviewPane.textContent = header + (data.content || "(no matches)");
      autoScrollPreview();
    } catch (err) {
      logPreviewPane.textContent = `Search failed: ${err.message}`;
    }
  }

  function downloadLog(pathKey) {
    window.location.href = `/api/admin/logs/${encodeURIComponent(pathKey)}/download`;
  }

  function autoScrollPreview() {
    if (logPreviewPane) {
      logPreviewPane.scrollTop = logPreviewPane.scrollHeight;
    }
  }

  // Event delegation for preview and download buttons in the table
  document.addEventListener("click", function (event) {
    const previewBtn = event.target.closest("[data-log-preview]");
    if (previewBtn) {
      previewLog(previewBtn.getAttribute("data-log-preview"));
      return;
    }
    const downloadBtn = event.target.closest("[data-log-download]");
    if (downloadBtn) {
      downloadLog(downloadBtn.getAttribute("data-log-download"));
      return;
    }
  });

  // Filter controls
  if (logCategoryFilter) {
    logCategoryFilter.addEventListener("change", renderLogTable);
  }
  if (logNameFilter) {
    logNameFilter.addEventListener("input", renderLogTable);
  }
  if (logRefreshBtn) {
    logRefreshBtn.addEventListener("click", loadLogList);
  }

  // Search controls
  if (logSearchBtn) {
    logSearchBtn.addEventListener("click", function () {
      if (!currentPathKey) {
        if (logPreviewPane) logPreviewPane.textContent = "Select a log file first, then search.";
        return;
      }
      const query = logSearchInput ? logSearchInput.value.trim() : "";
      if (!query) return;
      const cs = logCaseSensitive ? logCaseSensitive.checked : false;
      searchLogContent(currentPathKey, query, cs);
    });
  }
  if (logClearSearchBtn) {
    logClearSearchBtn.addEventListener("click", function () {
      if (logSearchInput) logSearchInput.value = "";
      if (currentPathKey) {
        previewLog(currentPathKey);
      }
    });
  }

  // Expose for external access
  window.LogViewer = { openLogViewer: openLogViewer };
})();
