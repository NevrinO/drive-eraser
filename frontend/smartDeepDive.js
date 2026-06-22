// --- START OF FILE frontend/smartDeepDive.js ---
// Phase 7 Feature G: SMART Deep Dive Viewer and Test Runner

let smartDeepDiveModal = null;
let smartDeepDiveContent = null;
let smartTestPollingInterval = null;
let currentTestDevice = null;

// Initialize the modal on load
document.addEventListener('DOMContentLoaded', () => {
  // Create modal elements if they don't exist
  if (!document.getElementById('smartDeepDiveModal')) {
    const modalHtml = `
      <div id="smartDeepDiveModal" class="modal modal--nested" aria-hidden="true">
        <div class="modal-backdrop" data-close-modal="true"></div>
        <div class="modal-dialog modal-dialog--wide" role="dialog" aria-modal="true" aria-labelledby="smartDeepDiveTitle">
          <header class="modal-header">
            <h3 id="smartDeepDiveTitle">SMART Deep Dive Viewer</h3>
            <button type="button" class="close-button" data-close-modal="true">Close</button>
          </header>
          <div id="smartDeepDiveContent" class="modal-content">
            <p>Loading SMART data...</p>
          </div>
        </div>
      </div>
    `;
    document.body.insertAdjacentHTML('beforeend', modalHtml);
  }

  smartDeepDiveModal = document.getElementById('smartDeepDiveModal');
  smartDeepDiveContent = document.getElementById('smartDeepDiveContent');

  // Setup close handlers
  document.querySelectorAll('#smartDeepDiveModal [data-close-modal="true"]').forEach(elem => {
    elem.addEventListener('click', closeSmartDeepDive);
  });

  // Event delegation for SMART test buttons
  smartDeepDiveModal.addEventListener('click', (event) => {
    if (event.target.matches('[data-start-smart-test]')) {
      const button = event.target;
      const device = button.dataset.device;
      startSmartTest(device);
    }
  });

  // Event delegation for refresh SMART details buttons
  smartDeepDiveModal.addEventListener('click', (event) => {
    if (event.target.matches('[data-refresh-smart-details]')) {
      const button = event.target;
      const device = button.dataset.device;
      loadSmartDetails(device, '', '');
    }
  });
});

async function openSmartDeepDiveModal(device, serial, interfaceType) {
  currentTestDevice = device;
  smartDeepDiveContent.innerHTML = '<p>Loading SMART data...</p>';
  openModal(smartDeepDiveModal);
  await loadSmartDetails(device, serial, interfaceType);
  
  // Check if a test is currently running and resume polling if needed
  await checkAndResumeTestPolling(device);
}

function closeSmartDeepDive() {
  // Stop any polling
  if (smartTestPollingInterval) {
    clearInterval(smartTestPollingInterval);
    smartTestPollingInterval = null;
  }
  closeModal(smartDeepDiveModal);
  currentTestDevice = null;
}

async function checkAndResumeTestPolling(device) {
  try {
    const response = await safeFetch(`/api/admin/drives/${device}/smart-test-status`);
    if (!response.ok) return;

    const data = await response.json();
    const testStatusDiv = document.getElementById('testStatus');

    if (!testStatusDiv) return;

    if (data.status === 'in_progress') {
      // Test is still running, resume polling
      const testType = data.test_type || 'short';
      testStatusDiv.innerHTML = `
        <p>Test in progress (resumed)...</p>
        <div class="progress-bar">
          <div class="progress-bar-fill" id="testProgressBar" style="width: ${data.percentage || 0}%"></div>
        </div>
        <p id="testProgressText">${Math.round(data.percentage || 0)}%</p>
      `;
      pollSmartTestStatus(device, testType);
    } else if (data.status === 'completed') {
      testStatusDiv.innerHTML = `
        <p class="status-complete">Test completed!</p>
        <button type="button" class="btn btn--secondary" data-refresh-smart-details data-device="${escapeHtml(device)}">Refresh Data</button>
      `;
    } else if (data.status === 'failed') {
      testStatusDiv.innerHTML = `
        <p class="status-failed">Test failed!</p>
        <button type="button" class="btn btn--secondary" data-refresh-smart-details data-device="${escapeHtml(device)}">Refresh Data</button>
      `;
    }
  } catch (error) {
    console.error('Failed to check test status:', error);
  }
}

async function loadSmartDetails(device, serial, interfaceType) {
  try {
    const response = await safeFetch(`/api/admin/drives/${device}/smart-details`);
    if (!response.ok) {
      const error = await response.json();
      smartDeepDiveContent.innerHTML = `<p class="error">Failed to load SMART data: ${error.error || 'Unknown error'}</p>`;
      return;
    }

    const data = await response.json();
    renderSmartDetails(data, device, serial, interfaceType);
  } catch (error) {
    console.error('Failed to load SMART details:', error);
    smartDeepDiveContent.innerHTML = `<p class="error">Failed to load SMART data: ${error.message}</p>`;
  }
}

function renderSmartDetails(data, device, serial, interfaceType) {
  const isSas = interfaceType && interfaceType.toLowerCase() === 'sas';
  const isNvme = interfaceType && interfaceType.toLowerCase() === 'nvme';

  let html = `
    <div class="detail-section">
      <div class="detail-head">
        <strong>${escapeHtml(serial || 'Unknown')}</strong>
        <span class="status-chip status-view-only">${escapeHtml(interfaceType?.toUpperCase() || 'UNKNOWN')}</span>
      </div>
      <div class="kv"><span>Device:</span><span>${escapeHtml(device)}</span></div>
    </div>
  `;

  // Attributes tab
  html += `
    <div class="detail-section">
      <h4>SMART Attributes</h4>
      ${renderAttributesTable(data.attributes)}
    </div>
  `;

  // Self-test logs tab
  html += `
    <div class="detail-section">
      <h4>Self-Test History</h4>
      ${renderAuditHistory(data.audit_history, data.self_test_logs, data.current_power_on_hours, device)}
    </div>
  `;

  // Test runner section
  html += `
    <div class="detail-section">
      <h4>SMART Test Runner</h4>
      <div class="test-runner-controls">
        <select id="testTypeSelect">
          <option value="short">Short Test (~2 min)</option>
          <option value="extended">Extended Test (~2 hours)</option>
          <option value="offline">Offline Immediate Test (~5 min)</option>
          ${!isSas && !isNvme ? '<option value="conveyance">Conveyance Test (~5 min)</option>' : ''}
        </select>
        <button type="button" class="btn btn--primary" data-start-smart-test data-device="${escapeHtml(device)}">Run Test</button>
      </div>
      <div id="testStatus" class="test-status"></div>
    </div>
  `;

  // SAS-specific section
  if (isSas && data.sas_specific) {
    html += `
      <div class="detail-section">
        <h4>SAS-Specific Logs</h4>
        ${renderSasSpecific(data.sas_specific)}
      </div>
    `;
  }

  // NVMe-specific section
  if (isNvme && data.nvme_specific) {
    html += `
      <div class="detail-section">
        <h4>NVMe-Specific Logs</h4>
        ${renderNvmeSpecific(data.nvme_specific)}
      </div>
    `;
  }

  // Device statistics
  if (data.device_statistics && data.device_statistics.length > 0) {
    html += `
      <div class="detail-section">
        <h4>Device Statistics</h4>
        ${renderDeviceStatistics(data.device_statistics)}
      </div>
    `;
  }

  // Error logs
  if (data.error_logs) {
    html += `
      <div class="detail-section">
        <h4>Error Logs</h4>
        <pre class="terminal-pre">${escapeHtml(JSON.stringify(data.error_logs, null, 2))}</pre>
      </div>
    `;
  }

  smartDeepDiveContent.innerHTML = html;
}

function renderAttributesTable(attributes) {
  if (!attributes || attributes.length === 0) {
    return '<p>No SMART attributes available.</p>';
  }

  let rows = attributes.map(attr => {
    const rawValue = attr.raw !== undefined ? attr.raw : '-';
    const value = attr.value !== undefined ? attr.value : '-';
    const worst = attr.worst !== undefined ? attr.worst : '-';
    const thresh = attr.thresh !== undefined ? attr.thresh : '-';
    const name = attr.name || 'Unknown';

    // Flag attributes with concerning values
    const rowClass = (attr.thresh && attr.value && attr.value < attr.thresh) ? 'row-warning' : '';

    return `
      <tr class="${rowClass}">
        <td>${attr.id}</td>
        <td>${escapeHtml(name)}</td>
        <td>${value}</td>
        <td>${worst}</td>
        <td>${thresh}</td>
        <td>${rawValue}</td>
      </tr>
    `;
  }).join('');

  return `
    <table class="data-table">
      <thead>
        <tr>
          <th>ID</th>
          <th>Attribute</th>
          <th>Value</th>
          <th>Worst</th>
          <th>Threshold</th>
          <th>Raw</th>
        </tr>
      </thead>
      <tbody>
        ${rows}
      </tbody>
    </table>
  `;
}

function renderAuditHistory(auditHistory, liveSelfTestLogs, currentPoh, device) {
  let html = '';

  // Display current POH for context
  if (currentPoh !== undefined && currentPoh !== null) {
    html += `<p class="info-text">Current Power-On Hours: ${currentPoh.toLocaleString()}</p>`;
  }

  // Display live drive self-test logs
  html += '<h5>Self-Test Log (Drive)</h5>';
  if (liveSelfTestLogs && Array.isArray(liveSelfTestLogs) && liveSelfTestLogs.length > 0) {
    let liveRows = liveSelfTestLogs.map(test => {
      const status = test.status || 'Unknown';
      const statusLower = status.toLowerCase();
      const passed = test.passed === true;
      const statusClass = passed || statusLower.includes('passed') || statusLower.includes('completed without error') ? 'status-complete' :
                         statusLower.includes('failed') ? 'status-failed' :
                         statusLower.includes('in progress') ? 'status-ready' : 'status-view-only';

      // Display hours with rollover thresholds based on current POH
      let hoursDisplay;
      if (test.hours !== undefined) {
        const hours = test.hours;
        const ROLLOVER_LIMIT = 65535;
        
        // Start with the raw hours value
        let displayStr = hours.toLocaleString();
        
        // Add rollover thresholds in parentheses if current POH exceeds them
        if (currentPoh !== undefined && currentPoh !== null) {
          const rolloverCount = Math.floor(currentPoh / ROLLOVER_LIMIT);
          
          for (let i = 1; i <= rolloverCount; i++) {
            const threshold = ROLLOVER_LIMIT * i;
            const adjustedValue = threshold + hours;
            displayStr += ` <span class="info-text">(${adjustedValue.toLocaleString()})</span>`;
          }
        }
        
        hoursDisplay = displayStr;
      } else {
        hoursDisplay = '-';
      }

      return `
        <tr>
          <td>${escapeHtml(test.type || 'Unknown')}</td>
          <td><span class="status-chip ${statusClass}">${escapeHtml(status)}</span></td>
          <td>${test.remaining !== undefined && test.remaining !== null && test.remaining !== 'null' ? test.remaining + '%' : '-'}</td>
          <td>${test.lba || '-'}</td>
          <td>${hoursDisplay}</td>
        </tr>
      `;
    }).join('');

    html += `
      <table class="data-table">
        <thead>
          <tr>
            <th>Type</th>
            <th>Status</th>
            <th>Remaining</th>
            <th>LBA</th>
            <th>Hours</th>
          </tr>
        </thead>
        <tbody>
          ${liveRows}
        </tbody>
      </table>
    `;
  } else {
    html += '<p class="info-text">No drive self-test log available.</p>';
  }

  return html;
}

function renderSasSpecific(sasData) {
  let html = '';

  if (sasData.grown_defect_list !== undefined) {
    html += `<div class="kv"><span>Grown Defect List:</span><span>${sasData.grown_defect_list}</span></div>`;
  }

  if (sasData.non_medium_errors !== undefined) {
    html += `<div class="kv"><span>Non-Medium Errors:</span><span>${sasData.non_medium_errors}</span></div>`;
  }

  if (sasData.background_scan_log) {
    html += `
      <div class="kv"><span>Background Scan Status:</span><span>${escapeHtml(JSON.stringify(sasData.background_scan_log, null, 2))}</span></div>
    `;
  }

  if (sasData.error_counter_log) {
    html += `
      <div class="kv"><span>Error Counter Log:</span><span>${escapeHtml(JSON.stringify(sasData.error_counter_log, null, 2))}</span></div>
    `;
  }

  return html || '<p>No SAS-specific data available.</p>';
}

function renderNvmeSpecific(nvmeData) {
  let html = '';

  if (nvmeData.health_log) {
    html += `
      <div class="kv"><span>Health Log:</span><span>${escapeHtml(JSON.stringify(nvmeData.health_log, null, 2))}</span></div>
    `;
  }

  if (nvmeData.error_log) {
    html += `
      <div class="kv"><span>Error Log:</span><span>${escapeHtml(JSON.stringify(nvmeData.error_log, null, 2))}</span></div>
    `;
  }

  return html || '<p>No NVMe-specific data available.</p>';
}

function renderDeviceStatistics(deviceStats) {
  let html = '';

  deviceStats.forEach(page => {
    html += `<h5>Page ${page.number}</h5>`;
    html += '<table class="data-table">';
    html += '<thead><tr><th>Name</th><th>Value</th><th>Offset</th></tr></thead>';
    html += '<tbody>';

    if (page.table && Array.isArray(page.table)) {
      page.table.forEach(item => {
        html += `
          <tr>
            <td>${escapeHtml(item.name)}</td>
            <td>${item.value}</td>
            <td>${item.offset}</td>
          </tr>
        `;
      });
    } else {
      html += '<tr><td colspan="3">No table data available</td></tr>';
    }

    html += '</tbody></table>';
  });

  return html;
}

async function parseErrorResponse(response) {
  let errorMessage = 'Unknown error';
  try {
    const contentType = response.headers.get('content-type');
    if (contentType && contentType.includes('application/json')) {
      const error = await response.json();
      errorMessage = error.error || error.message || 'Unknown error';
    } else {
      errorMessage = await response.text() || `HTTP ${response.status}`;
    }
  } catch (parseError) {
    errorMessage = `HTTP ${response.status} - Failed to parse error response`;
  }
  return errorMessage;
}

async function startSmartTest(device) {
  const testTypeSelect = document.getElementById('testTypeSelect');
  const testType = testTypeSelect ? testTypeSelect.value : 'short';
  const testStatusDiv = document.getElementById('testStatus');

  if (!testStatusDiv) return;

  // Guard: prevent starting a new test if one is already in progress
  if (smartTestPollingInterval) {
    testStatusDiv.innerHTML = '<p class="error">A test is already in progress. Please wait for it to complete.</p>';
    return;
  }

  testStatusDiv.innerHTML = '<p>Starting test...</p>';

  try {
    const response = await safeFetch(`/api/admin/drives/${device}/smart-test`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ test_type: testType })
    });

    if (!response.ok) {
      const errorMessage = await parseErrorResponse(response);
      testStatusDiv.innerHTML = `<p class="error">Failed to start test: ${errorMessage}</p>`;
      return;
    }

    const result = await response.json();

    if (!result || result.status !== 'started') {
      testStatusDiv.innerHTML = `<p class="error">Unexpected response from server: ${JSON.stringify(result)}</p>`;
      return;
    }

    testStatusDiv.innerHTML = `
      <p>Test started successfully!</p>
      <p>Estimated time: ${result.estimated_minutes || 'unknown'} minutes</p>
      <div class="progress-bar">
        <div class="progress-bar-fill" id="testProgressBar" style="width: 0%"></div>
      </div>
      <p id="testProgressText">0%</p>
    `;

    // Start polling for status
    pollSmartTestStatus(device, testType);
  } catch (error) {
    console.error('Failed to start SMART test:', error);
    testStatusDiv.innerHTML = `<p class="error">Failed to start test: ${error.message}</p>`;
  }
}

async function pollSmartTestStatus(device, testType) {
  // Clear any existing polling
  if (smartTestPollingInterval) {
    clearInterval(smartTestPollingInterval);
  }

  // Set maximum polling duration based on test type (120 minutes for extended, 10 minutes for others)
  const maxPollingDuration = testType === 'extended' ? 120 * 60 * 1000 : 10 * 60 * 1000;
  const pollingStartTime = Date.now();

  // Track consecutive errors to stop polling on persistent failures
  let consecutiveErrors = 0;
  const maxConsecutiveErrors = 6; // Stop after 6 consecutive errors (30 seconds)

  // Grace period to avoid false completion/failure detection from stale drive log entries
  // The drive's self-test log may not update immediately after starting a test
  // This must match SMART_TEST_GRACE_PERIOD_SECONDS in backend/smart_constants.py (10 seconds)
  const GRACE_PERIOD_MS = 10000;
  let testStartedAt = null; // Will be set from backend response

  const updateStatus = async () => {
    // Check if we've exceeded maximum polling duration
    if (Date.now() - pollingStartTime > maxPollingDuration) {
      clearInterval(smartTestPollingInterval);
      smartTestPollingInterval = null;
      const testStatusDiv = document.getElementById('testStatus');
      if (testStatusDiv) {
        testStatusDiv.innerHTML = '<p class="error">Polling timeout: test did not complete within expected time.</p>';
      }
      return;
    }

    try {
      const response = await safeFetch(`/api/admin/drives/${device}/smart-test-status`);

      if (!response.ok) {
        consecutiveErrors++;
        if (consecutiveErrors >= maxConsecutiveErrors) {
          clearInterval(smartTestPollingInterval);
          smartTestPollingInterval = null;
          const testStatusDiv = document.getElementById('testStatus');
          if (testStatusDiv) {
            testStatusDiv.innerHTML = '<p class="error">Failed to poll test status after multiple attempts. Please refresh the page.</p>';
          }
        }
        return;
      }

      // Reset error counter on successful request
      consecutiveErrors = 0;

      const data = await response.json();

      const testStatusDiv = document.getElementById('testStatus');
      const progressBar = document.getElementById('testProgressBar');
      const progressText = document.getElementById('testProgressText');

      if (!testStatusDiv) {
        clearInterval(smartTestPollingInterval);
        smartTestPollingInterval = null;
        return;
      }

      // Capture started_at timestamp from first response
      if (data.started_at && !testStartedAt) {
        testStartedAt = new Date(data.started_at).getTime();
      }

      // Check if grace period has elapsed
      const gracePeriodElapsed = testStartedAt ? (Date.now() - testStartedAt) >= GRACE_PERIOD_MS : true;

      if (data.status === 'in_progress') {
        const percentage = data.percentage || 0;
        if (progressBar) progressBar.style.width = `${percentage}%`;
        if (progressText) progressText.textContent = `${Math.round(percentage).toString().padStart(2, '0')}%`;
      } else if (data.status === 'completed') {
        // Only accept completed status if grace period has elapsed
        if (!gracePeriodElapsed) {
          // Grace period not elapsed - ignore stale completed status and continue polling
          console.log('Grace period not elapsed, ignoring completed status from stale log entry');
          return;
        }
        clearInterval(smartTestPollingInterval);
        smartTestPollingInterval = null;

        testStatusDiv.innerHTML = `
          <p class="status-complete">Test completed successfully!</p>
          <button type="button" class="btn btn--secondary" data-refresh-smart-details data-device="${escapeHtml(device)}">Refresh Data</button>
        `;
      } else if (data.status === 'failed') {
        // Only accept failed status if grace period has elapsed
        if (!gracePeriodElapsed) {
          // Grace period not elapsed - ignore stale failed status and continue polling
          console.log('Grace period not elapsed, ignoring failed status from stale log entry');
          return;
        }
        clearInterval(smartTestPollingInterval);
        smartTestPollingInterval = null;
        testStatusDiv.innerHTML = `
          <p class="status-failed">Test failed!</p>
          <button type="button" class="btn btn--secondary" data-refresh-smart-details data-device="${escapeHtml(device)}">Refresh Data</button>
        `;
      } else if (data.status === 'aborted') {
        clearInterval(smartTestPollingInterval);
        smartTestPollingInterval = null;
        testStatusDiv.innerHTML = `
          <p class="status-failed">Test was aborted.</p>
          <button type="button" class="btn btn--secondary" data-refresh-smart-details data-device="${escapeHtml(device)}">Refresh Data</button>
        `;
      } else if (data.status === 'no_tests') {
        clearInterval(smartTestPollingInterval);
        smartTestPollingInterval = null;
        testStatusDiv.innerHTML = '<p>No test in progress.</p>';
      }
    } catch (error) {
      console.error('Failed to poll test status:', error);
      consecutiveErrors++;
      if (consecutiveErrors >= maxConsecutiveErrors) {
        clearInterval(smartTestPollingInterval);
        smartTestPollingInterval = null;
        const testStatusDiv = document.getElementById('testStatus');
        if (testStatusDiv) {
          testStatusDiv.innerHTML = '<p class="error">Network error: Failed to poll test status after multiple attempts. Please refresh the page.</p>';
        }
      }
    }
  };

  // Initial status check
  await updateStatus();

  // Poll every 5 seconds
  smartTestPollingInterval = setInterval(updateStatus, 5000);
}

// Helper function to escape HTML
function escapeHtml(text) {
  if (text === null || text === undefined) return '';
  const div = document.createElement('div');
  div.textContent = String(text);
  return div.innerHTML;
}
// --- END OF FILE frontend/smartDeepDive.js ---
