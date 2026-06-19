// Unit tests for discoveryValidation.js
// Run with: node tests/test_discovery_validation.js

// Mock the window object for Node.js environment
if (typeof window === 'undefined') {
  global.window = {};
}

// Load the validation module
const fs = require('fs');
const path = require('path');

// Read and execute the validation module
const validationCode = fs.readFileSync(path.join(__dirname, '../frontend/admin/discoveryValidation.js'), 'utf8');

// Execute in a sandbox to capture the namespace
const sandbox = { window: {} };
const vm = require('vm');
vm.createContext(sandbox);
vm.runInContext(validationCode, sandbox);

const DiscoveryValidation = sandbox.window.DiscoveryValidation;

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
  if (actual === expected) {
    console.log(`✓ ${message}`);
    testsPassed++;
  } else {
    console.error(`✗ ${message} - Expected: ${expected}, Got: ${actual}`);
    testsFailed++;
  }
}

console.log('Running DiscoveryValidation tests...\n');

// Test validatePciAddress
console.log('Testing validatePciAddress:');
assert(DiscoveryValidation.validatePciAddress('0000:00:1f.2') === true, 'Valid PCI address');
assert(DiscoveryValidation.validatePciAddress('0000:01:00.0') === true, 'Valid PCI address with different bus');
assert(DiscoveryValidation.validatePciAddress('00:1f.2') === false, 'Invalid PCI address - missing domain');
assert(DiscoveryValidation.validatePciAddress('0000:00:1f') === false, 'Invalid PCI address - missing function');
assert(DiscoveryValidation.validatePciAddress('0000:00:1g.2') === false, 'Invalid PCI address - invalid hex');
assert(DiscoveryValidation.validatePciAddress(null) === false, 'Null PCI address rejected');
assert(DiscoveryValidation.validatePciAddress('') === false, 'Empty PCI address rejected');
assert(DiscoveryValidation.validatePciAddress('0000:00:1f.2\n') === false, 'PCI address with newline rejected');
console.log();

// Test validateMappingPattern
console.log('Testing validateMappingPattern:');
assert(DiscoveryValidation.validateMappingPattern('sequential') === true, 'Valid pattern: sequential');
assert(DiscoveryValidation.validateMappingPattern('controller_sequential') === true, 'Valid pattern: controller_sequential');
assert(DiscoveryValidation.validateMappingPattern('pci_sequential') === true, 'Valid pattern: pci_sequential');
assert(DiscoveryValidation.validateMappingPattern('invalid') === false, 'Invalid pattern rejected');
assert(DiscoveryValidation.validateMappingPattern('') === false, 'Empty pattern rejected');
assert(DiscoveryValidation.validateMappingPattern('sequential\n') === false, 'Pattern with newline rejected');
console.log();

// Test validateStartBay
console.log('Testing validateStartBay:');
assert(DiscoveryValidation.validateStartBay(0) === true, 'Valid start bay: 0');
assert(DiscoveryValidation.validateStartBay(50) === true, 'Valid start bay: 50');
assert(DiscoveryValidation.validateStartBay(127) === true, 'Valid start bay: 127');
assert(DiscoveryValidation.validateStartBay(-1) === false, 'Invalid start bay: negative');
assert(DiscoveryValidation.validateStartBay(128) === false, 'Invalid start bay: > 127');
assert(DiscoveryValidation.validateStartBay('50') === true, 'Valid start bay as string');
assert(DiscoveryValidation.validateStartBay('invalid') === false, 'Invalid start bay: non-numeric');
console.log();

// Test validateDeviceFilter
console.log('Testing validateDeviceFilter:');
assert(DiscoveryValidation.validateDeviceFilter('all') === true, 'Valid filter: all');
assert(DiscoveryValidation.validateDeviceFilter('sas_sata') === true, 'Valid filter: sas_sata');
assert(DiscoveryValidation.validateDeviceFilter('nvme') === true, 'Valid filter: nvme');
assert(DiscoveryValidation.validateDeviceFilter('invalid') === false, 'Invalid filter rejected');
assert(DiscoveryValidation.validateDeviceFilter('') === false, 'Empty filter rejected');
console.log();

// Test validateDevicePath
console.log('Testing validateDevicePath:');
assert(DiscoveryValidation.validateDevicePath('/dev/sda') === true, 'Valid SATA device path');
assert(DiscoveryValidation.validateDevicePath('/dev/sdb1') === true, 'Valid SATA partition');
assert(DiscoveryValidation.validateDevicePath('/dev/nvme0n1') === true, 'Valid NVMe device path');
assert(DiscoveryValidation.validateDevicePath('/dev/nvme0n1p1') === false, 'NVMe partition rejected (partitions filtered)');
assert(DiscoveryValidation.validateDevicePath('/dev/sg0') === true, 'Valid SCSI generic device');
assert(DiscoveryValidation.validateDevicePath('/dev/hda') === true, 'Valid IDE device');
assert(DiscoveryValidation.validateDevicePath('/dev/sda\n') === false, 'Device path with newline rejected');
assert(DiscoveryValidation.validateDevicePath('/dev/../sda') === false, 'Path traversal rejected');
assert(DiscoveryValidation.validateDevicePath('sda') === false, 'Missing /dev prefix rejected');
assert(DiscoveryValidation.validateDevicePath('/etc/passwd') === false, 'Non-/dev path rejected');
assert(DiscoveryValidation.validateDevicePath(null) === false, 'Null device path rejected');
assert(DiscoveryValidation.validateDevicePath('') === false, 'Empty device path rejected');
console.log();

// Test validateProjectedByPath
console.log('Testing validateProjectedByPath:');
assert(DiscoveryValidation.validateProjectedByPath('pci-0000:01:00.0-scsi-0:0:0:0') === true, 'Valid SCSI projected path');
assert(DiscoveryValidation.validateProjectedByPath('pci-0000:01:00.0-sas-exp0x500056b3059bdcff-phy0-lun-0') === true, 'Valid SAS expander path');
assert(DiscoveryValidation.validateProjectedByPath('invalid-path') === false, 'Invalid projected path rejected');
assert(DiscoveryValidation.validateProjectedByPath('') === false, 'Empty projected path rejected');
console.log();

// Test validateUdevByPath
console.log('Testing validateUdevByPath:');
assert(DiscoveryValidation.validateUdevByPath('pci-0000:01:00.0-scsi-0:0:0:0') === true, 'Valid PCI udev path');
assert(DiscoveryValidation.validateUdevByPath('pci-0000:01:00.0-ata-1') === true, 'Valid ATA udev path');
assert(DiscoveryValidation.validateUdevByPath('usb-1:1.2') === true, 'Valid USB udev path');
assert(DiscoveryValidation.validateUdevByPath('ieee1394-0') === true, 'Valid FireWire udev path');
assert(DiscoveryValidation.validateUdevByPath('virtio-0') === true, 'Valid virtio udev path');
assert(DiscoveryValidation.validateUdevByPath('platform-foo') === true, 'Valid platform udev path');
assert(DiscoveryValidation.validateUdevByPath('invalid-foo') === false, 'Invalid udev path prefix rejected');
assert(DiscoveryValidation.validateUdevByPath('') === false, 'Empty udev path rejected');
console.log();

// Test validateMapping
console.log('Testing validateMapping:');
const localBayMapCopy = {
  bay0: { role: 'source' },
  bay1: { role: 'target' },
  bay2: { role: 'target' }
};

const validMapping = {
  bay0: { device_path: '/dev/sda', device_name: 'Test Drive' },
  bay1: { device_path: '/dev/sdb', device_name: 'Test Drive 2' }
};
const validResult = DiscoveryValidation.validateMapping(validMapping, localBayMapCopy);
assert(validResult.valid === true, 'Valid mapping passes validation');

const emptyMapping = {};
const emptyResult = DiscoveryValidation.validateMapping(emptyMapping, localBayMapCopy);
assert(emptyResult.valid === false, 'Empty mapping fails validation');

const oversizedMapping = {};
for (let i = 0; i < 129; i++) {
  oversizedMapping[`bay${i}`] = { device_path: '/dev/sda', device_name: 'Test' };
}
const oversizedResult = DiscoveryValidation.validateMapping(oversizedMapping, localBayMapCopy);
assert(oversizedResult.valid === false, 'Oversized mapping fails validation');

const duplicateMapping = {
  bay0: { device_path: '/dev/sda', device_name: 'Test' },
  bay1: { device_path: '/dev/sda', device_name: 'Test' }
};
const duplicateResult = DiscoveryValidation.validateMapping(duplicateMapping, localBayMapCopy);
assert(duplicateResult.valid === false, 'Mapping with duplicate device paths fails validation');

const invalidBayIdMapping = {
  invalid_bay: { device_path: '/dev/sda', device_name: 'Test' }
};
const invalidBayIdResult = DiscoveryValidation.validateMapping(invalidBayIdMapping, localBayMapCopy);
assert(invalidBayIdResult.valid === false, 'Mapping with invalid bay ID fails validation');

const invalidDevicePathMapping = {
  bay0: { device_path: 'invalid-path', device_name: 'Test' }
};
const invalidDevicePathResult = DiscoveryValidation.validateMapping(invalidDevicePathMapping, localBayMapCopy);
assert(invalidDevicePathResult.valid === false, 'Mapping with invalid device path fails validation');

const emptySlotMapping = {
  bay0: {
    is_empty: true,
    projected_by_path: 'pci-0000:01:00.0-scsi-0:0:0:0',
    device_name: 'Empty Slot'
  }
};
const emptySlotResult = DiscoveryValidation.validateMapping(emptySlotMapping, localBayMapCopy);
assert(emptySlotResult.valid === true, 'Valid empty slot with projected path passes validation');

const invalidProjectedPathMapping = {
  bay0: {
    is_empty: true,
    projected_by_path: 'invalid-path',
    device_name: 'Empty Slot'
  }
};
const invalidProjectedPathResult = DiscoveryValidation.validateMapping(invalidProjectedPathMapping, localBayMapCopy);
assert(invalidProjectedPathResult.valid === false, 'Empty slot with invalid projected path fails validation');
console.log();

// Test getDevicePathType
console.log('Testing getDevicePathType:');
assertEquals(DiscoveryValidation.getDevicePathType('pci-0000:01:00.0-scsi-0:0:0:0'), 'projected', 'Projected SCSI path detected');
assertEquals(DiscoveryValidation.getDevicePathType('pci-0000:01:00.0-ata-1'), 'udev', 'Udev by-path detected');
assertEquals(DiscoveryValidation.getDevicePathType('/dev/sda'), 'standard', 'Standard device path detected');
assertEquals(DiscoveryValidation.getDevicePathType(''), 'unknown', 'Empty string returns unknown');
assertEquals(DiscoveryValidation.getDevicePathType(null), 'unknown', 'Null returns unknown');
assertEquals(DiscoveryValidation.getDevicePathType(123), 'unknown', 'Non-string returns unknown');
console.log();

// Summary
console.log('\n' + '='.repeat(50));
console.log(`Tests passed: ${testsPassed}`);
console.log(`Tests failed: ${testsFailed}`);
console.log('='.repeat(50));

process.exit(testsFailed > 0 ? 1 : 0);
