// Unit tests for discoveryMapping.js
// Run with: node tests/test_discovery_mapping.js

// Mock the window object for Node.js environment
if (typeof window === 'undefined') {
  global.window = {};
}

// Load modules in dependency order
const fs = require('fs');
const path = require('path');

// Load validation module first
const validationCode = fs.readFileSync(path.join(__dirname, '../frontend/admin/discoveryValidation.js'), 'utf8');
const sandbox1 = { window: {} };
const vm = require('vm');
vm.createContext(sandbox1);
vm.runInContext(validationCode, sandbox1);

// Load state module
const stateCode = fs.readFileSync(path.join(__dirname, '../frontend/admin/discoveryState.js'), 'utf8');
const sandbox2 = { window: { DiscoveryValidation: sandbox1.window.DiscoveryValidation } };
vm.createContext(sandbox2);
vm.runInContext(stateCode, sandbox2);

// Load mapping module with dependencies
const mappingCode = fs.readFileSync(path.join(__dirname, '../frontend/admin/discoveryMapping.js'), 'utf8');
const sandbox3 = { 
  window: { 
    DiscoveryValidation: sandbox1.window.DiscoveryValidation,
    DiscoveryState: sandbox2.window.DiscoveryState
  } 
};
vm.createContext(sandbox3);
vm.runInContext(mappingCode, sandbox3);

const DiscoveryMapping = sandbox3.window.DiscoveryMapping;
const DiscoveryState = sandbox2.window.DiscoveryState;

// Test suite
let testsPassed = 0;
let testsFailed = 0;

function assert(condition, message) {
  if (condition) {
    console.log(`✓ ${message}`);
    testsPassed++;
  } else {
    console.error(`✗ ${message}`);
    testsFailed++;
  }
}

function assertEquals(actual, expected, message) {
  if (JSON.stringify(actual) === JSON.stringify(expected)) {
    console.log(`✓ ${message}`);
    testsPassed++;
  } else {
    console.error(`✗ ${message} - Expected: ${JSON.stringify(expected)}, Got: ${JSON.stringify(actual)}`);
    testsFailed++;
  }
}

console.log('Running DiscoveryMapping tests...\n');

// Test groupBy
console.log('Testing groupBy:');
const testItems = [
  { id: 1, type: 'sata' },
  { id: 2, type: 'nvme' },
  { id: 3, type: 'sata' }
];
const grouped = DiscoveryMapping.groupBy(testItems, item => item.type);
assert(grouped.sata.length === 2, 'SATA group has 2 items');
assert(grouped.nvme.length === 1, 'NVMe group has 1 item');
console.log();

// Test groupControllersByType
console.log('Testing groupControllersByType:');
const controllers = [
  { pci_address: '0000:00:1f.2', controller_type: 'sata' },
  { pci_address: '0000:01:00.0', controller_type: 'nvme' },
  { pci_address: '0000:02:00.0', controller_type: 'sata' }
];
const typeGroups = DiscoveryMapping.groupControllersByType(controllers);
assert(typeGroups.sata.length === 2, 'SATA controllers grouped correctly');
assert(typeGroups.nvme.length === 1, 'NVMe controllers grouped correctly');
console.log();

// Test groupControllersByPCI
console.log('Testing groupControllersByPCI:');
const pciControllers = [
  { pci_address: '0000:00:1f.2' },
  { pci_address: '0000:00:1f.3' },
  { pci_address: '0000:01:00.0' }
];
const pciGroups = DiscoveryMapping.groupControllersByPCI(pciControllers);
assert(pciGroups['0000:00:1f'].length === 2, 'PCI prefix grouping works');
assert(pciGroups['0000:01:00'].length === 1, 'Different PCI prefix grouped separately');
console.log();

// Test sortBayIds
console.log('Testing sortBayIds:');
const unsortedBays = ['bay10', 'bay2', 'bay1', 'bay20'];
const sortedBays = DiscoveryMapping.sortBayIds(unsortedBays);
assertEquals(sortedBays, ['bay1', 'bay2', 'bay10', 'bay20'], 'Bay IDs sorted numerically');
console.log();

// Test flattenDevices
console.log('Testing flattenDevices:');
const devicesByType = {
  sata: [
    { device_path: '/dev/sda', device_name: 'Drive A', controller_pci: '0000:00:1f.2', by_path: '/dev/sda' },
    { device_path: '/dev/sdb', device_name: 'Drive B', controller_pci: '0000:00:1f.2', by_path: '/dev/sdb' }
  ],
  nvme: [
    { device_path: '/dev/nvme0n1', device_name: 'NVMe Drive', controller_pci: '0000:01:00.0', by_path: '/dev/nvme0n1' }
  ]
};
const flattened = DiscoveryMapping.flattenDevices(devicesByType, 'all');
assert(flattened.length === 3, 'All devices flattened');
assert(flattened[0].type === 'sata', 'Type preserved');
assert(flattened[2].type === 'nvme', 'NVMe type preserved');

const sataOnly = DiscoveryMapping.flattenDevices(devicesByType, 'sas_sata');
assert(sataOnly.length === 2, 'Filter by sas_sata works');

const nvmeOnly = DiscoveryMapping.flattenDevices(devicesByType, 'nvme');
assert(nvmeOnly.length === 1, 'Filter by nvme works');
console.log();

// Test filterDevices
console.log('Testing filterDevices:');
const testDevices = [
  { device_path: '/dev/sda', device_name: 'Samsung SSD', controller_pci: '0000:00:1f.2', type: 'sata', smart: { model: 'Samsung' } },
  { device_path: '/dev/nvme0n1', device_name: 'Intel NVMe', controller_pci: '0000:01:00.0', type: 'nvme', smart: { model: 'Intel' } }
];
const filtered = DiscoveryMapping.filterDevices(testDevices, 'samsung', 'all');
assert(filtered.length === 1, 'Search term filter works');
assert(filtered[0].device_path === '/dev/sda', 'Correct device matched');

const typeFiltered = DiscoveryMapping.filterDevices(testDevices, '', 'nvme');
assert(typeFiltered.length === 1, 'Type filter works');
assert(typeFiltered[0].type === 'nvme', 'Correct type filtered');
console.log();

// Test manual mapping state functions
console.log('Testing manual mapping state:');
assert(DiscoveryMapping.getMappingMode() === 'pattern', 'Default mode is pattern');
assertEquals(DiscoveryMapping.getManualMappings(), {}, 'Manual mappings empty by default');
assert(DiscoveryMapping.getSelectedDevice() === null, 'No device selected by default');
assert(DiscoveryMapping.hasManualMappings() === false, 'Has no mappings by default');

const testDevice = { device_path: '/dev/sda', device_name: 'Test', controller_pci: '0000:00:1f.2', type: 'sata', by_path: '/dev/sda' };
DiscoveryMapping.setSelectedDevice(testDevice);
assertEquals(DiscoveryMapping.getSelectedDevice(), testDevice, 'Device selected correctly');

const testMappings = { bay0: { device_path: '/dev/sda' } };
DiscoveryMapping.setManualMappings(testMappings);
assertEquals(DiscoveryMapping.getManualMappings(), testMappings, 'Manual mappings set correctly');
assert(DiscoveryMapping.hasManualMappings() === true, 'Has mappings after setting');

DiscoveryMapping.clearManualMappings();
assertEquals(DiscoveryMapping.getManualMappings(), {}, 'Manual mappings cleared');
assert(DiscoveryMapping.getSelectedDevice() === null, 'Selected device cleared');
console.log();

// Test removeManualMapping
console.log('Testing removeManualMapping:');
const mappingsWithRemove = {
  bay0: { device_path: '/dev/sda' },
  bay1: { device_path: '/dev/sdb' }
};
DiscoveryMapping.setManualMappings(mappingsWithRemove);
DiscoveryMapping.removeManualMapping('bay0');
const afterRemove = DiscoveryMapping.getManualMappings();
assert(afterRemove.bay0 === undefined, 'Mapping removed');
assert(afterRemove.bay1 !== undefined, 'Other mapping preserved');
console.log();

// Test sortBayIds with various inputs
console.log('Testing sortBayIds edge cases:');
const singleBay = ['bay5'];
assertEquals(DiscoveryMapping.sortBayIds(singleBay), ['bay5'], 'Single bay sorted');

const alreadySorted = ['bay1', 'bay2', 'bay3'];
assertEquals(DiscoveryMapping.sortBayIds(alreadySorted), ['bay1', 'bay2', 'bay3'], 'Already sorted stays sorted');

const reverseSorted = ['bay100', 'bay50', 'bay1'];
assertEquals(DiscoveryMapping.sortBayIds(reverseSorted), ['bay1', 'bay50', 'bay100'], 'Reverse sorted corrected');
console.log();

// Test groupControllersByType with invalid input
console.log('Testing groupControllersByType edge cases:');
assert(JSON.stringify(DiscoveryMapping.groupControllersByType(null)) === '{}', 'Null input returns empty object');
assert(JSON.stringify(DiscoveryMapping.groupControllersByType('string')) === '{}', 'String input returns empty object');
assert(JSON.stringify(DiscoveryMapping.groupControllersByType(123)) === '{}', 'Number input returns empty object');
console.log();

// Test groupControllersByPCI with invalid input
console.log('Testing groupControllersByPCI edge cases:');
assert(JSON.stringify(DiscoveryMapping.groupControllersByPCI(null)) === '{}', 'Null input returns empty object');
assert(JSON.stringify(DiscoveryMapping.groupControllersByPCI('string')) === '{}', 'String input returns empty object');
console.log();

// Test flattenDevices with invalid input
console.log('Testing flattenDevices edge cases:');
assert(DiscoveryMapping.flattenDevices({}, 'all').length === 0, 'Empty object returns empty array');
assert(DiscoveryMapping.flattenDevices({ sata: 'not array' }, 'all').length === 0, 'Non-array value returns empty array');
// Note: null input will throw in current implementation - this is expected behavior
console.log();

// Test filterDevices with invalid input
console.log('Testing filterDevices edge cases:');
assert(DiscoveryMapping.filterDevices([], '', 'all').length === 0, 'Empty array returns empty array');
// Note: null input will throw in current implementation - this is expected behavior
console.log();

// Summary
console.log('\n' + '='.repeat(50));
console.log(`Tests passed: ${testsPassed}`);
console.log(`Tests failed: ${testsFailed}`);
console.log('='.repeat(50));

process.exit(testsFailed > 0 ? 1 : 0);
