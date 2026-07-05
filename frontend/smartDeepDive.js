// --- START OF FILE frontend/smartDeepDive.js ---
// Phase 7 Feature G: SMART Deep Dive Viewer and Test Runner

let smartDeepDiveModal = null;
let smartDeepDiveContent = null;
let smartTestPollingInterval = null;

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

  // Event delegation for SMART test and refresh buttons
  smartDeepDiveModal.addEventListener('click', (event) => {
    if (event.target.matches('[data-start-smart-test]')) {
      startSmartTest(event.target.dataset.device);
    } else if (event.target.matches('[data-refresh-smart-details]')) {
      loadSmartDetails(event.target.dataset.device, '', '');
    }
  });
});

async function openSmartDeepDiveModal(device, serial, interfaceType) {
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
      const pct = typeof data.percentage === 'number' && isFinite(data.percentage) ? data.percentage : 0;
      testStatusDiv.innerHTML = `
        <p>Test in progress (resumed)...</p>
        <div class="progress-bar">
          <div class="progress-bar-fill" id="testProgressBar"></div>
        </div>
        <p id="testProgressText">${escapeHtml(Math.round(pct))}%</p>
      `;
      const resumedBar = document.getElementById('testProgressBar');
      if (resumedBar) resumedBar.style.width = `${pct}%`;
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
      const errorMessage = await parseErrorResponse(response);
      smartDeepDiveContent.innerHTML = `<p class="error">Failed to load SMART data: ${escapeHtml(errorMessage)}</p>`;
      return;
    }

    const data = await response.json();
    renderSmartDetails(data, device, serial, interfaceType);
  } catch (error) {
    console.error('Failed to load SMART details:', error);
    smartDeepDiveContent.innerHTML = `<p class="error">Failed to load SMART data: ${escapeHtml(error.message)}</p>`;
  }
}

// Rendering functions (renderSmartDetails, renderAttributesTable, renderAuditHistory,
// renderSasSpecific, renderSasStartStopCounter, renderSasPortInfo, renderSasErrorCounterLog,
// renderSasBackgroundScanLog, renderNvmeSpecific, renderNvmeHealthLog, renderNvmeErrorLog,
// formatDataUnits, formatMinutes, renderDeviceStatistics) have been extracted to smartRenderers.js

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
      testStatusDiv.innerHTML = `<p class="error">Failed to start test: ${escapeHtml(errorMessage)}</p>`;
      return;
    }

    const result = await response.json();

    if (!result || result.status !== 'started') {
      testStatusDiv.innerHTML = `<p class="error">Unexpected response from server: ${escapeHtml(JSON.stringify(result))}</p>`;
      return;
    }

    testStatusDiv.innerHTML = `
      <p>Test started successfully!</p>
      <p>Estimated time: ${escapeHtml(result.estimated_minutes || 'unknown')} minutes</p>
      <div class="progress-bar">
        <div class="progress-bar-fill" id="testProgressBar"></div>
      </div>
      <p id="testProgressText">0%</p>
    `;

    // Start polling for status
    pollSmartTestStatus(device, testType);
  } catch (error) {
    console.error('Failed to start SMART test:', error);
    testStatusDiv.innerHTML = `<p class="error">Failed to start test: ${escapeHtml(error.message)}</p>`;
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
        if (!gracePeriodElapsed) {
          console.log('Grace period not elapsed, ignoring aborted status from stale log entry');
          return;
        }
        clearInterval(smartTestPollingInterval);
        smartTestPollingInterval = null;
        testStatusDiv.innerHTML = `
          <p class="status-failed">Test was aborted.</p>
          <button type="button" class="btn btn--secondary" data-refresh-smart-details data-device="${escapeHtml(device)}">Refresh Data</button>
        `;
      } else if (data.status === 'no_tests' || data.status === 'unknown') {
        // Grace period: the drive may not have registered the test yet.
        // smartctl's status register can take a few seconds to update after
        // a test is initiated, causing a false "no_tests" or "unknown" reading.
        if (!gracePeriodElapsed) {
          console.log('Grace period not elapsed, ignoring no_tests/unknown status - test may not have registered yet');
          return;
        }
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
// --- END OF FILE frontend/smartDeepDive.js ---
