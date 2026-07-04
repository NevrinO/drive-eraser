// Unit tests for smartDeepDive.js rendering functions
// Run with: node tests/test_smart_deep_dive.js

const fs = require('fs');
const path = require('path');
const vm = require('vm');

// --- Sandbox Setup ---

const sandbox = {
  window: {},
  document: {
    addEventListener: () => {},
    getElementById: () => null,
    querySelectorAll: () => [],
    body: { insertAdjacentHTML: () => {} }
  },
  console: { error: () => {}, log: () => {}, warn: () => {} },
  clearInterval: () => {},
  setInterval: () => {}
};

vm.createContext(sandbox);

// Load utils.js first (provides escapeHtml)
const utilsCode = fs.readFileSync(path.join(__dirname, '../frontend/utils.js'), 'utf8');
vm.runInContext(utilsCode, sandbox);

// Load smartDeepDive.js
const smartCode = fs.readFileSync(path.join(__dirname, '../frontend/smartDeepDive.js'), 'utf8');
vm.runInContext(smartCode, sandbox);

// Extract functions from sandbox
const {
  formatDataUnits,
  formatMinutes,
  renderAttributesTable,
  renderAuditHistory,
  renderSasSpecific,
  renderNvmeHealthLog
} = sandbox;

// --- Test Helpers ---

let testsPassed = 0;
let testsFailed = 0;

function assert(condition, message) {
  if (condition) {
    console.log(`  \u2713 ${message}`);
    testsPassed++;
  } else {
    console.error(`  \u2717 ${message}`);
    testsFailed++;
  }
}

function assertEquals(actual, expected, message) {
  if (actual === expected) {
    console.log(`  \u2713 ${message}`);
    testsPassed++;
  } else {
    console.error(`  \u2717 ${message} - Expected: "${expected}", Got: "${actual}"`);
    testsFailed++;
  }
}

function assertIncludes(haystack, needle, message) {
  if (String(haystack).includes(needle)) {
    console.log(`  \u2713 ${message}`);
    testsPassed++;
  } else {
    console.error(`  \u2717 ${message} - Expected output to include "${needle}"`);
    testsFailed++;
  }
}

function assertNotIncludes(haystack, needle, message) {
  if (!String(haystack).includes(needle)) {
    console.log(`  \u2713 ${message}`);
    testsPassed++;
  } else {
    console.error(`  \u2717 ${message} - Output should NOT include "${needle}"`);
    testsFailed++;
  }
}

// --- Tests ---

console.log('Running smartDeepDive.js tests...\n');

// === formatDataUnits ===
console.log('Testing formatDataUnits:');
assertEquals(formatDataUnits(undefined), '-', 'undefined returns "-"');
assertEquals(formatDataUnits(null), '-', 'null returns "-"');
assertEquals(formatDataUnits(0), '0 bytes', '0 returns "0 bytes"');
assertEquals(formatDataUnits(1), '512 bytes', '1 unit returns "512 bytes"');
assertIncludes(formatDataUnits(2048), 'bytes', '2048 units returns bytes (1MB)');
assert(formatDataUnits(2048).replace(/,/g, '').includes('1048576'), '2048 units = 1,048,576 bytes');
assertEquals(formatDataUnits(2097152), '1.00 GB', '2097152 units = 1.00 GB');
assertEquals(formatDataUnits(2147483648), '1.00 TB', '2147483648 units = 1.00 TB');
console.log();

// === formatMinutes ===
console.log('Testing formatMinutes:');
assertEquals(formatMinutes(undefined), '-', 'undefined returns "-"');
assertEquals(formatMinutes(null), '-', 'null returns "-"');
assertEquals(formatMinutes(0), '0m', '0 returns "0m"');
assertEquals(formatMinutes(30), '30m', '30 returns "30m"');
assertEquals(formatMinutes(60), '1h', '60 returns "1h"');
assertEquals(formatMinutes(90), '1h', '90 returns "1h" (floor)');
assertEquals(formatMinutes(1440), '1d 0h', '1440 returns "1d 0h"');
assertEquals(formatMinutes(1500), '1d 1h', '1500 returns "1d 1h"');
console.log();

// === renderAttributesTable ===
console.log('Testing renderAttributesTable:');
assertEquals(renderAttributesTable([]), '<p>No SMART attributes available.</p>', 'empty array returns placeholder');
assertEquals(renderAttributesTable(undefined), '<p>No SMART attributes available.</p>', 'undefined returns placeholder');

(function() {
  const warningAttr = [{ id: 5, name: 'Reallocated_Sector_Ct', value: 5, worst: 5, thresh: 10, raw: 100 }];
  const html = renderAttributesTable(warningAttr);
  assertIncludes(html, 'row-warning', 'value < thresh triggers row-warning class');

  const healthyAttr = [{ id: 5, name: 'Reallocated_Sector_Ct', value: 100, worst: 100, thresh: 10, raw: 0 }];
  const html2 = renderAttributesTable(healthyAttr);
  assertNotIncludes(html2, 'row-warning', 'value > thresh does not trigger row-warning');
})();

(function() {
  const missingRaw = [{ id: 5, name: 'Test', value: 100, worst: 100, thresh: 10 }];
  const html = renderAttributesTable(missingRaw);
  assertIncludes(html, '>-<', 'missing raw shows "-" in raw column');
})();

(function() {
  const missingName = [{ id: 5, value: 100, worst: 100, thresh: 10, raw: 0 }];
  const html = renderAttributesTable(missingName);
  assertIncludes(html, 'Unknown', 'missing name shows "Unknown"');
})();

(function() {
  const html = renderAttributesTable([{ id: 5, name: 'Test', value: 100, worst: 100, thresh: 10, raw: 0 }]);
  assertIncludes(html, '<table', 'output contains <table');
  assertIncludes(html, '<thead>', 'output contains <thead>');
  assertIncludes(html, '<tbody>', 'output contains <tbody>');
  assertIncludes(html, 'ID', 'output contains ID column header');
  assertIncludes(html, 'Attribute', 'output contains Attribute column header');
  assertIncludes(html, 'Value', 'output contains Value column header');
  assertIncludes(html, 'Worst', 'output contains Worst column header');
  assertIncludes(html, 'Threshold', 'output contains Threshold column header');
  assertIncludes(html, 'Raw', 'output contains Raw column header');
})();
console.log();

// === renderAuditHistory ===
console.log('Testing renderAuditHistory:');

(function() {
  const html = renderAuditHistory([], undefined);
  assertIncludes(html, 'No drive self-test log available.', 'empty logs + undefined POH shows no log message');
  assertNotIncludes(html, 'Current Power-On Hours:', 'undefined POH does not show POH line');
})();

(function() {
  const html = renderAuditHistory([], 5000);
  assertIncludes(html, 'Current Power-On Hours:', 'empty logs + defined POH shows POH line');
})();

(function() {
  const logs = [{ type: 'Short offline', status: 'Test passed', passed: false, remaining: 0, lba: null, hours: 100 }];
  const html = renderAuditHistory(logs, 5000);
  assertIncludes(html, 'status-complete', 'status containing "passed" gets status-complete class');
})();

(function() {
  const logs = [{ type: 'Short offline', status: 'Self-test routine failed', passed: false, remaining: 0, lba: null, hours: 100 }];
  const html = renderAuditHistory(logs, 5000);
  assertIncludes(html, 'status-failed', 'status containing "failed" gets status-failed class');
})();

(function() {
  const logs = [{ type: 'Short offline', status: 'Self-test routine in progress', passed: false, remaining: 50, lba: null, hours: 100 }];
  const html = renderAuditHistory(logs, 5000);
  assertIncludes(html, 'status-ready', 'status containing "in progress" gets status-ready class');
})();

(function() {
  const logs = [{ type: 'Short offline', status: 'Completed without error', passed: true, remaining: 'null', lba: null, hours: 100 }];
  const html = renderAuditHistory(logs, 5000);
  assertIncludes(html, '>-<', 'string "null" remaining shows "-"');
  assertNotIncludes(html, 'null%', 'string "null" remaining does not show "null%"');
})();

(function() {
  const logs = [{ type: 'scsi_ie', status: 'Hard Drive Healthy', passed: true, remaining: 0, lba: null }];
  const html = renderAuditHistory(logs, 5000);
  assertIncludes(html, 'SCSI Informational Exceptions', 'scsi_ie type renders as "SCSI Informational Exceptions"');
})();

(function() {
  const logs = [{ type: 1, status: 'Completed without error', passed: true, remaining: 0, lba: null }];
  const html = renderAuditHistory(logs, 5000);
  assertIncludes(html, 'Extended Operation', 'NVMe type=1 renders as "Extended Operation"');
})();

(function() {
  const logs = [{ type: 'Short offline', status: 'Completed without error', passed: true, remaining: 0, lba: null, hours: 5000 }];
  const html = renderAuditHistory(logs, 70000);
  assert(html.replace(/,/g, '').includes('70535'), 'POH=70000 + hours=5000 shows rollover adjusted 70535');
})();
console.log();

// === renderSasSpecific ===
console.log('Testing renderSasSpecific:');
assertEquals(renderSasSpecific({}), '<p>No SAS-specific data available.</p>', 'empty object returns placeholder');

(function() {
  const html = renderSasSpecific({ grown_defect_list: 5 });
  assertIncludes(html, 'Grown Defect List:', 'grown_defect_list renders label');
})();

(function() {
  const sasData = {
    error_counter_log: {
      read: { errors_corrected_by_eccfast: 100, total_uncorrectable_errors: 0 },
      write: { errors_corrected_by_eccfast: 50, total_uncorrectable_errors: 0 },
      verify: { errors_corrected_by_eccfast: 0, total_uncorrectable_errors: 0 }
    }
  };
  const html = renderSasSpecific(sasData);
  assertIncludes(html, 'Error Counter Log', 'error_counter_log renders section header');
  assertIncludes(html, 'READ', 'error_counter_log renders READ row');
  assertIncludes(html, 'WRITE', 'error_counter_log renders WRITE row');
  assertIncludes(html, 'VERIFY', 'error_counter_log renders VERIFY row');
})();

(function() {
  const sasData = {
    background_scan_log: {
      status: 'Background scan completed successfully'
    }
  };
  const html = renderSasSpecific(sasData);
  assertIncludes(html, 'Background Scan Log', 'background_scan_log renders section header');
  assertIncludes(html, 'Scan Status:', 'background_scan_log renders Scan Status label');
})();

(function() {
  const sasData = {
    background_scan_log: {
      status: 'Active',
      table: [
        { lba: 1000, status: 'Scan failed' }
      ]
    }
  };
  const html = renderSasSpecific(sasData);
  assertIncludes(html, 'row-warning', 'background_scan_log table entry with "failed" status gets row-warning');
})();

(function() {
  const sasData = {
    start_stop_cycle_counter: {
      year_of_manufacture: '2020',
      week_of_manufacture: '15',
      specified_cycle_count_over_device_lifetime: 10000,
      accumulated_start_stop_cycles: 500
    }
  };
  const html = renderSasSpecific(sasData);
  assertIncludes(html, 'Start/Stop Cycle Counter', 'start_stop_cycle_counter renders section header');
})();

(function() {
  const sasData = {
    sas_port_0: {
      phy_0: {
        negotiated_logical_link_rate: '12.0 Gb/s',
        attached_device_type: 'SAS device',
        sas_address: '0x5000cca01a123456'
      }
    }
  };
  const html = renderSasSpecific(sasData);
  assertIncludes(html, 'SAS Port 0', 'sas_port_0 renders port header');
  assertIncludes(html, 'Link Rate:', 'sas_port_0 renders Link Rate label');
})();
console.log();

// === renderNvmeHealthLog ===
console.log('Testing renderNvmeHealthLog:');
assertEquals(renderNvmeHealthLog(null), '<p class="info-text">NVMe Health Log: Not available</p>', 'null returns Not available');
assertEquals(renderNvmeHealthLog(undefined), '<p class="info-text">NVMe Health Log: Not available</p>', 'undefined returns Not available');

(function() {
  const html = renderNvmeHealthLog({});
  assertIncludes(html, 'NVMe Health Information', 'empty object renders health info header');
  assertIncludes(html, 'None', 'empty object defaults critical_warning to None');
})();

(function() {
  const html = renderNvmeHealthLog({ critical_warning: 3 });
  assertIncludes(html, 'Available Spare Space', 'critical_warning=0x03 includes Available Spare Space bit');
  assertIncludes(html, 'Temperature', 'critical_warning=0x03 includes Temperature bit');
  assertIncludes(html, '0x03', 'critical_warning=0x03 displays hex 0x03');
})();

(function() {
  const html = renderNvmeHealthLog({ critical_warning: 0 });
  assertIncludes(html, 'None', 'critical_warning=0 displays None');
  assertNotIncludes(html, '0x01', 'critical_warning=0 does not show 0x01');
})();

(function() {
  const html = renderNvmeHealthLog({ critical_warning: 0, available_spare: 5, available_spare_threshold: 10, percentage_used: 50, media_errors: 0 });
  assertIncludes(html, 'row-warning', 'available_spare < threshold triggers row-warning');
})();

(function() {
  const html = renderNvmeHealthLog({ critical_warning: 0, available_spare: 50, available_spare_threshold: 10, percentage_used: 95, media_errors: 0 });
  assertIncludes(html, 'row-warning', 'percentage_used > 90 triggers row-warning');
})();

(function() {
  const html = renderNvmeHealthLog({ critical_warning: 0, available_spare: 50, available_spare_threshold: 10, percentage_used: 50, media_errors: 2 });
  assertIncludes(html, 'row-warning', 'media_errors > 0 triggers row-warning');
})();

(function() {
  const html = renderNvmeHealthLog({ critical_warning: 0, temperature_sensors: [45, 50, 40] });
  assertIncludes(html, 'Temperature Sensors:', 'temperature_sensors array renders Temperature Sensors label');
})();

(function() {
  const html = renderNvmeHealthLog({ critical_warning: 0, data_units_read: 2147483648 });
  assertIncludes(html, 'TB', 'data_units_read calls formatDataUnits and shows TB');
})();

// --- Summary ---
console.log('\n' + '='.repeat(50));
console.log(`Tests passed: ${testsPassed}`);
console.log(`Tests failed: ${testsFailed}`);
console.log('='.repeat(50));

process.exit(testsFailed > 0 ? 1 : 0);
