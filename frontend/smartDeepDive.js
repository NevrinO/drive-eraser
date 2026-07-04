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

  // Attributes tab (only for non-SAS drives - SAS uses different log structure)
  if (!isSas) {
    html += `
      <div class="detail-section">
        <h4>SMART Attributes</h4>
        ${renderAttributesTable(data.attributes)}
      </div>
    `;
  }

  // Self-test logs tab
  html += `
    <div class="detail-section">
      <h4>Self-Test History</h4>
      ${renderAuditHistory(data.self_test_logs, data.current_power_on_hours)}
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
    const rowClass = (attr.thresh !== undefined && attr.value !== undefined && attr.value < attr.thresh) ? 'row-warning' : '';

    return `
      <tr class="${rowClass}">
        <td>${escapeHtml(attr.id)}</td>
        <td>${escapeHtml(name)}</td>
        <td>${escapeHtml(value)}</td>
        <td>${escapeHtml(worst)}</td>
        <td>${escapeHtml(thresh)}</td>
        <td>${escapeHtml(rawValue)}</td>
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

function renderAuditHistory(liveSelfTestLogs, currentPoh) {
  let html = '';

  // Display current POH for context
  if (currentPoh !== undefined && currentPoh !== null) {
    html += `<p class="info-text">Current Power-On Hours: ${escapeHtml(currentPoh.toLocaleString())}</p>`;
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
            displayStr += ` <span class="info-text">(${escapeHtml(adjustedValue.toLocaleString())})</span>`;
          }
        }
        
        hoursDisplay = displayStr;
      } else {
        hoursDisplay = '-';
      }

      // Format test type for better readability
      let testType = test.type || 'Unknown';
      if (test.type === 'scsi_ie') {
        testType = 'SCSI Informational Exceptions';
      } else if (typeof test.type === 'number') {
        // NVMe self-test_num values
        const nvmeTestTypes = {
          0: 'Short Operation',
          1: 'Extended Operation',
          2: 'Vendor Specific'
        };
        testType = nvmeTestTypes[test.type] || `Test ${test.type}`;
      }

      return `
        <tr>
          <td>${escapeHtml(testType)}</td>
          <td><span class="status-chip ${statusClass}">${escapeHtml(status)}</span></td>
          <td>${test.remaining !== undefined && test.remaining !== null && String(test.remaining).toLowerCase() !== 'null' ? escapeHtml(test.remaining) + '%' : '-'}</td>
          <td>${test.lba !== undefined && test.lba !== null ? escapeHtml(test.lba) : '-'}</td>
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
    html += `<div class="kv"><span>Grown Defect List:</span><span>${escapeHtml(sasData.grown_defect_list)}</span></div>`;
  }

  if (sasData.non_medium_errors !== undefined) {
    html += `<div class="kv"><span>Non-Medium Errors:</span><span>${escapeHtml(sasData.non_medium_errors)}</span></div>`;
  }

  // Display start/stop cycle counter
  if (sasData.start_stop_cycle_counter) {
    html += renderSasStartStopCounter(sasData.start_stop_cycle_counter);
  }

  // Display SAS port PHY information
  if (sasData.sas_port_0) {
    html += renderSasPortInfo(sasData.sas_port_0, "Port 0");
  }
  if (sasData.sas_port_1) {
    html += renderSasPortInfo(sasData.sas_port_1, "Port 1");
  }

  // Parse and display error counter log as a table
  if (sasData.error_counter_log) {
    html += renderSasErrorCounterLog(sasData.error_counter_log);
  }

  // Parse and display background scan log
  if (sasData.background_scan_log) {
    html += renderSasBackgroundScanLog(sasData.background_scan_log);
  }

  return html || '<p>No SAS-specific data available.</p>';
}

function renderSasStartStopCounter(counterData) {
  if (!counterData || typeof counterData !== 'object') {
    return '<p class="info-text">Start/Stop Cycle Counter: Not available</p>';
  }

  let html = '<h5>Start/Stop Cycle Counter</h5>';

  const year = counterData.year_of_manufacture;
  const week = counterData.week_of_manufacture;
  if (year && week) {
    html += `<div class="kv"><span>Manufactured:</span><span>Week ${escapeHtml(week)} of ${escapeHtml(year)}</span></div>`;
  }

  const specifiedCycles = counterData.specified_cycle_count_over_device_lifetime;
  const accumulatedCycles = counterData.accumulated_start_stop_cycles;
  if (specifiedCycles !== undefined && accumulatedCycles !== undefined) {
    const percentage = specifiedCycles > 0 ? ((accumulatedCycles / specifiedCycles) * 100).toFixed(1) : 'N/A';
    html += `<div class="kv"><span>Start/Stop Cycles:</span><span>${escapeHtml(accumulatedCycles.toLocaleString())} / ${escapeHtml(specifiedCycles.toLocaleString())} (${escapeHtml(percentage)}%)</span></div>`;
  }

  const specifiedLoadUnload = counterData.specified_load_unload_count_over_device_lifetime;
  const accumulatedLoadUnload = counterData.accumulated_load_unload_cycles;
  if (specifiedLoadUnload !== undefined && accumulatedLoadUnload !== undefined) {
    const percentage = specifiedLoadUnload > 0 ? ((accumulatedLoadUnload / specifiedLoadUnload) * 100).toFixed(1) : 'N/A';
    html += `<div class="kv"><span>Load/Unload Cycles:</span><span>${escapeHtml(accumulatedLoadUnload.toLocaleString())} / ${escapeHtml(specifiedLoadUnload.toLocaleString())} (${escapeHtml(percentage)}%)</span></div>`;
  }

  return html;
}

function renderSasPortInfo(portData, portName) {
  if (!portData || typeof portData !== 'object') {
    return '';
  }

  let html = `<h5>SAS ${escapeHtml(portName)}</h5>`;

  const phyData = portData.phy_0;
  if (!phyData) {
    html += '<p class="info-text">No PHY data available</p>';
    return html;
  }

  const linkRate = phyData.negotiated_logical_link_rate || 'Unknown';
  const deviceType = phyData.attached_device_type || 'Unknown';
  const sasAddress = phyData.sas_address || 'Unknown';
  const invalidDwordCount = phyData.invalid_dword_count !== undefined ? phyData.invalid_dword_count : '-';
  const runningDisparityErrorCount = phyData.running_disparity_error_count !== undefined ? phyData.running_disparity_error_count : '-';
  const lossOfDwordSyncCount = phyData.loss_of_dword_synchronization_count !== undefined ? phyData.loss_of_dword_synchronization_count : '-';
  const phyResetProblemCount = phyData.phy_reset_problem_count !== undefined ? phyData.phy_reset_problem_count : '-';

  html += `
    <div class="kv"><span>Link Rate:</span><span>${escapeHtml(linkRate)}</span></div>
    <div class="kv"><span>Attached Device:</span><span>${escapeHtml(deviceType)}</span></div>
    <div class="kv"><span>SAS Address:</span><span>${escapeHtml(sasAddress)}</span></div>
  `;

  html += '<h6>PHY Error Counters</h6>';
  html += `
    <table class="data-table">
      <thead>
        <tr>
          <th>Invalid DWords</th>
          <th>Running Disparity Errors</th>
          <th>Loss of DWord Sync</th>
          <th>PHY Reset Problems</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td class="${invalidDwordCount > 0 ? 'row-warning' : ''}">${escapeHtml(invalidDwordCount)}</td>
          <td class="${runningDisparityErrorCount > 0 ? 'row-warning' : ''}">${escapeHtml(runningDisparityErrorCount)}</td>
          <td class="${lossOfDwordSyncCount > 0 ? 'row-warning' : ''}">${escapeHtml(lossOfDwordSyncCount)}</td>
          <td class="${phyResetProblemCount > 0 ? 'row-warning' : ''}">${escapeHtml(phyResetProblemCount)}</td>
        </tr>
      </tbody>
    </table>
  `;

  return html;
}

function renderSasErrorCounterLog(errorLog) {
  if (!errorLog || typeof errorLog !== 'object') {
    return '<p class="info-text">Error Counter Log: Not available</p>';
  }

  const sections = ['read', 'write', 'verify'];
  let hasData = false;
  let rows = '';

  sections.forEach(section => {
    const data = errorLog[section];
    if (data && typeof data === 'object') {
      hasData = true;
      const errorsEccFast = data.errors_corrected_by_eccfast !== undefined ? data.errors_corrected_by_eccfast : '-';
      const errorsEccDelayed = data.errors_corrected_by_eccdelayed !== undefined ? data.errors_corrected_by_eccdelayed : '-';
      const errorsRereadsRewrites = data.errors_corrected_by_rereads_rewrites !== undefined ? data.errors_corrected_by_rereads_rewrites : '-';
      const totalErrorsCorrected = data.total_errors_corrected !== undefined ? data.total_errors_corrected : '-';
      const gigabytesProcessed = data.gigabytes_processed !== undefined ? data.gigabytes_processed : '-';
      const totalUncorrectable = data.total_uncorrectable_errors !== undefined ? data.total_uncorrectable_errors : '-';

      rows += `
        <tr>
          <td><strong>${section.toUpperCase()}</strong></td>
          <td>${escapeHtml(errorsEccFast)}</td>
          <td>${escapeHtml(errorsEccDelayed)}</td>
          <td>${escapeHtml(errorsRereadsRewrites)}</td>
          <td>${escapeHtml(totalErrorsCorrected)}</td>
          <td>${escapeHtml(gigabytesProcessed)}</td>
          <td class="${totalUncorrectable > 0 ? 'row-warning' : ''}">${escapeHtml(totalUncorrectable)}</td>
        </tr>
      `;
    }
  });

  if (!hasData) {
    return '<p class="info-text">Error Counter Log: No data available</p>';
  }

  return `
    <h5>Error Counter Log</h5>
    <table class="data-table">
      <thead>
        <tr>
          <th>Operation</th>
          <th>Errors Corrected (ECC Fast)</th>
          <th>Errors Corrected (ECC Delayed)</th>
          <th>Errors Corrected (Rereads/Rewrites)</th>
          <th>Total Errors Corrected</th>
          <th>GB Processed</th>
          <th>Total Uncorrectable Errors</th>
        </tr>
      </thead>
      <tbody>
        ${rows}
      </tbody>
    </table>
  `;
}

function renderSasBackgroundScanLog(scanLog) {
  if (!scanLog || typeof scanLog !== 'object') {
    return '<p class="info-text">Background Scan Log: Not available</p>';
  }

  let html = '<h5>Background Scan Log</h5>';

  // Parse scan status
  const statusObj = scanLog.status;
  let scanStatus = 'Unknown';
  let scanProgress = '-';
  let numScans = '-';
  let numMediumScans = '-';

  if (typeof statusObj === 'string') {
    scanStatus = statusObj;
  } else if (statusObj && typeof statusObj === 'object') {
    scanStatus = statusObj.string || 'Unknown';
    scanProgress = statusObj.scan_progress !== undefined ? statusObj.scan_progress : '-';
    numScans = statusObj.number_scans_performed !== undefined ? statusObj.number_scans_performed : '-';
    numMediumScans = statusObj.number_medium_scans_performed !== undefined ? statusObj.number_medium_scans_performed : '-';
  }

  // Also check for these fields at the top level (some smartctl versions put them there)
  if (scanProgress === '-' && scanLog.scan_progress !== undefined) {
    scanProgress = scanLog.scan_progress;
  }
  if (numScans === '-' && scanLog.number_scans_performed !== undefined) {
    numScans = scanLog.number_scans_performed;
  }

  // Format progress as percentage if it's a number
  if (typeof scanProgress === 'number') {
    scanProgress = scanProgress + '%';
  }

  html += `
    <div class="kv"><span>Scan Status:</span><span>${escapeHtml(scanStatus)}</span></div>
    <div class="kv"><span>Scan Progress:</span><span>${escapeHtml(scanProgress)}</span></div>
    <div class="kv"><span>Total Scans Performed:</span><span>${escapeHtml(numScans)}</span></div>
  `;
  if (numMediumScans !== '-') {
    html += `<div class="kv"><span>Medium Scans Performed:</span><span>${escapeHtml(numMediumScans)}</span></div>`;
  }

  // Parse scan event table if present
  const scanTable = scanLog.table;
  if (scanTable && Array.isArray(scanTable) && scanTable.length > 0) {
    html += '<h6>Scan Events</h6>';
    html += '<table class="data-table">';
    html += '<thead><tr><th>LBA</th><th>Status</th></tr></thead>';
    html += '<tbody>';

    scanTable.forEach(entry => {
      const lba = entry.lba !== undefined ? entry.lba : '-';
      const status = entry.status || 'Unknown';
      const statusLower = String(status).toLowerCase();
      const rowClass = (statusLower.includes('failed') || statusLower.includes('error')) ? 'row-warning' : '';

      html += `
        <tr class="${rowClass}">
          <td>${escapeHtml(lba)}</td>
          <td>${escapeHtml(status)}</td>
        </tr>
      `;
    });

    html += '</tbody></table>';
  }

  return html;
}

function renderNvmeSpecific(nvmeData) {
  let html = '';

  // Parse and display health information log
  if (nvmeData.health_log) {
    html += renderNvmeHealthLog(nvmeData.health_log);
  }

  // Parse and display error log
  if (nvmeData.error_log) {
    html += renderNvmeErrorLog(nvmeData.error_log);
  }

  return html || '<p>No NVMe-specific data available.</p>';
}

function renderNvmeHealthLog(healthLog) {
  if (!healthLog || typeof healthLog !== 'object') {
    return '<p class="info-text">NVMe Health Log: Not available</p>';
  }

  let html = '<h5>NVMe Health Information</h5>';

  // Critical warning is a bitmask
  const criticalWarning = healthLog.critical_warning !== undefined ? healthLog.critical_warning : 0;
  const warningBits = [];
  if (criticalWarning & 0x01) warningBits.push('Available Spare Space');
  if (criticalWarning & 0x02) warningBits.push('Temperature');
  if (criticalWarning & 0x04) warningBits.push('Device Reliability');
  if (criticalWarning & 0x08) warningBits.push('Read-Only');
  if (criticalWarning & 0x10) warningBits.push('Volatile Memory Backup');
  
  const warningDisplay = warningBits.length > 0 ? warningBits.join(', ') : 'None';
  const warningClass = criticalWarning > 0 ? 'row-warning' : '';

  html += `<div class="kv"><span>Critical Warning:</span><span class="${warningClass}">${escapeHtml(warningDisplay)} (0x${escapeHtml(criticalWarning.toString(16).padStart(2, '0'))})</span></div>`;

  // Temperature sensors
  const temperature = healthLog.temperature !== undefined ? healthLog.temperature + '°C' : '-';
  const tempSensors = healthLog.temperature_sensors;
  let tempSensorDisplay = '-';
  if (tempSensors && Array.isArray(tempSensors) && tempSensors.length > 0) {
    tempSensorDisplay = tempSensors.map(s => s + '°C').join(', ');
  }
  html += `<div class="kv"><span>Temperature:</span><span>${escapeHtml(temperature)}</span></div>`;
  if (tempSensorDisplay !== '-') {
    html += `<div class="kv"><span>Temperature Sensors:</span><span>${escapeHtml(tempSensorDisplay)}</span></div>`;
  }

  // Available spare
  const availableSpare = healthLog.available_spare !== undefined ? healthLog.available_spare + '%' : '-';
  const availableSpareThreshold = healthLog.available_spare_threshold !== undefined ? healthLog.available_spare_threshold + '%' : '-';
  const spareClass = (healthLog.available_spare !== undefined && healthLog.available_spare_threshold !== undefined && 
                      healthLog.available_spare < healthLog.available_spare_threshold) ? 'row-warning' : '';
  html += `<div class="kv"><span>Available Spare:</span><span class="${spareClass}">${escapeHtml(availableSpare)} (threshold: ${escapeHtml(availableSpareThreshold)})</span></div>`;

  // Percentage used
  const percentageUsed = healthLog.percentage_used !== undefined ? healthLog.percentage_used + '%' : '-';
  const usedClass = (healthLog.percentage_used !== undefined && healthLog.percentage_used > 90) ? 'row-warning' : '';
  html += `<div class="kv"><span>Percentage Used:</span><span class="${usedClass}">${escapeHtml(percentageUsed)}</span></div>`;

  // Data units read/written
  const dataUnitsRead = healthLog.data_units_read !== undefined ? formatDataUnits(healthLog.data_units_read) : '-';
  const dataUnitsWritten = healthLog.data_units_written !== undefined ? formatDataUnits(healthLog.data_units_written) : '-';
  html += `<div class="kv"><span>Data Read:</span><span>${escapeHtml(dataUnitsRead)}</span></div>`;
  html += `<div class="kv"><span>Data Written:</span><span>${escapeHtml(dataUnitsWritten)}</span></div>`;

  // Media and data integrity errors
  const mediaErrors = healthLog.media_errors !== undefined ? healthLog.media_errors.toLocaleString() : '-';
  const numErrLogEntries = healthLog.num_err_log_entries !== undefined ? healthLog.num_err_log_entries.toLocaleString() : '-';
  const mediaErrorsClass = (healthLog.media_errors !== undefined && healthLog.media_errors > 0) ? 'row-warning' : '';
  html += `<div class="kv"><span>Media Errors:</span><span class="${mediaErrorsClass}">${escapeHtml(mediaErrors)}</span></div>`;
  html += `<div class="kv"><span>Error Log Entries:</span><span>${escapeHtml(numErrLogEntries)}</span></div>`;

  // Power cycles and power on hours
  const powerCycles = healthLog.power_cycles !== undefined ? healthLog.power_cycles.toLocaleString() : '-';
  const powerOnHours = healthLog.power_on_hours !== undefined ? healthLog.power_on_hours.toLocaleString() : '-';
  html += `<div class="kv"><span>Power Cycles:</span><span>${escapeHtml(powerCycles)}</span></div>`;
  html += `<div class="kv"><span>Power-On Hours:</span><span>${escapeHtml(powerOnHours)}</span></div>`;

  // Controller busy time
  const controllerBusyTime = healthLog.controller_busy_time !== undefined ? formatMinutes(healthLog.controller_busy_time) : '-';
  html += `<div class="kv"><span>Controller Busy Time:</span><span>${escapeHtml(controllerBusyTime)}</span></div>`;

  return html;
}

function renderNvmeErrorLog(errorLog) {
  if (!errorLog || typeof errorLog !== 'object') {
    return '<p class="info-text">NVMe Error Log: Not available</p>';
  }

  // Check if it's an array of error entries
  if (Array.isArray(errorLog)) {
    if (errorLog.length === 0) {
      return '<p class="info-text">NVMe Error Log: No entries</p>';
    }

    let html = '<h5>NVMe Error Log Entries</h5>';
    html += '<table class="data-table">';
    html += '<thead><tr><th>Entry</th><th>Error Count</th><th>SQID</th><th>CID</th><th>Status</th><th>LBA</th><th>NSID</th><th>Command</th></tr></thead>';
    html += '<tbody>';

    errorLog.forEach((entry, idx) => {
      const errorCount = entry.error_count !== undefined ? entry.error_count.toLocaleString() : '-';
      const sqid = entry.sqid !== undefined ? entry.sqid : '-';
      const cid = entry.cid !== undefined ? entry.cid : '-';
      const status = entry.status !== undefined ? '0x' + entry.status.toString(16) : '-';
      const lba = entry.lba !== undefined ? entry.lba : '-';
      const nsid = entry.nsid !== undefined ? entry.nsid : '-';
      const command = entry.command_name || entry.command || '-';

      html += `
        <tr>
          <td>${idx + 1}</td>
          <td>${escapeHtml(errorCount)}</td>
          <td>${escapeHtml(sqid)}</td>
          <td>${escapeHtml(cid)}</td>
          <td>${escapeHtml(status)}</td>
          <td>${escapeHtml(lba)}</td>
          <td>${escapeHtml(nsid)}</td>
          <td>${escapeHtml(command)}</td>
        </tr>
      `;
    });

    html += '</tbody></table>';
    return html;
  }

  // If it's a single object or unknown format, display as raw JSON
  return `
    <h5>NVMe Error Log</h5>
    <pre class="terminal-pre">${escapeHtml(JSON.stringify(errorLog, null, 2))}</pre>
  `;
}

function formatDataUnits(units) {
  // smartctl reports data units in 512-byte units
  // Convert to human-readable format
  if (units === undefined || units === null) return '-';
  
  const bytes = units * 512;
  const gb = bytes / (1024 * 1024 * 1024);
  const tb = gb / 1024;
  
  if (tb >= 1) {
    return tb.toFixed(2) + ' TB';
  } else if (gb >= 1) {
    return gb.toFixed(2) + ' GB';
  } else {
    return bytes.toLocaleString() + ' bytes';
  }
}

function formatMinutes(minutes) {
  if (minutes === undefined || minutes === null) return '-';
  
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);
  const remainingHours = hours % 24;
  
  if (days > 0) {
    return `${days}d ${remainingHours}h`;
  } else if (hours > 0) {
    return `${hours}h`;
  } else {
    return `${minutes}m`;
  }
}

function renderDeviceStatistics(deviceStats) {
  let html = '';

  deviceStats.forEach(page => {
    html += `<h5>Page ${escapeHtml(page.number)}</h5>`;
    html += '<table class="data-table">';
    html += '<thead><tr><th>Name</th><th>Value</th><th>Offset</th></tr></thead>';
    html += '<tbody>';

    if (page.table && Array.isArray(page.table)) {
      page.table.forEach(item => {
        html += `
          <tr>
            <td>${escapeHtml(item.name)}</td>
            <td>${escapeHtml(item.value)}</td>
            <td>${escapeHtml(item.offset)}</td>
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
// --- END OF FILE frontend/smartDeepDive.js ---
