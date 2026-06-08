# Skipped Tests - Integration Test Plan

## Overview

Five tests in `test_device_discovery_extended.py` are currently skipped with `pytest.skip()` calls. These are **not intentionally skipped working tests** - they are **incomplete test stubs** that previous agents abandoned rather than implementing.

## Current Skipped Tests

All five tests have empty function bodies with only imports and skip statements:

1. **test_successful_slot_enumeration** (line 473)
   - Function: `get_max_slot_from_enclosure()`
   - Skip reason: "Test needs refactoring due to complex sysfs path mocking requirements"
   - Status: Empty stub - no test logic implemented

2. **test_fallback_to_slot_id_parsing** (line 478)
   - Function: `get_max_slot_from_enclosure()`
   - Skip reason: "Test needs refactoring due to infinite recursion in mock setup"
   - Status: Empty stub - no test logic implemented

3. **test_invalid_slot_number_ignored** (line 484)
   - Function: `get_max_slot_from_enclosure()`
   - Skip reason: "Test needs refactoring due to infinite recursion in mock setup"
   - Status: Empty stub - no test logic implemented

4. **test_successful_projection** (line 511)
   - Function: `get_scsi_host_slot_projections()`
   - Skip reason: "Test needs refactoring due to complex sysfs path mocking requirements"
   - Status: Empty stub - no test logic implemented

5. **test_detects_occupied_slots** (line 542)
   - Function: `get_scsi_host_slot_projections()`
   - Skip reason: "Test needs refactoring due to complex sysfs path mocking requirements"
   - Status: Empty stub - no test logic implemented

## Decision: Convert to Integration Tests

**Chosen Approach:** Option 3 - Convert to integration tests on actual hardware

### Rationale

1. **Hardware-dependent functionality**: These tests cover enclosure slot detection and SCSI host projections, which interact directly with Linux sysfs structures (`/sys/class/...`, `/sys/devices/...`). Mock-based unit tests cannot reliably replicate the complexity of real hardware sysfs hierarchies.

2. **Production reliability**: The drive eraser runs on Ubuntu 26.04 with actual storage hardware. Testing against real hardware is the only way to validate:
   - Real driver/kernel interactions
   - Actual PCI controller enumeration
   - True enclosure device detection
   - Hardware-specific edge cases

3. **Mock complexity**: Previous agents attempted mock-based approaches but encountered infinite recursion and complex sysfs path mocking issues. The maintenance burden of such mocks outweighs their value.

4. **Critical features**: Enclosure slot detection and SCSI projections are core to the drive eraser's hardware management. Losing test coverage for these features is unacceptable.

## Implementation Plan

### Prerequisites

- Ubuntu 26.04 server with actual storage hardware (SATA/NVMe controllers, enclosures if available)
- SSH access or direct terminal access to the server
- Test environment with the drive-eraser codebase deployed

### Test Structure

Create a new integration test file: `tests/integration/test_hardware_discovery.py`

```python
# Integration tests for hardware-dependent discovery features
# These tests require actual hardware and must run on Ubuntu server

import pytest
import os

@pytest.mark.integration
@pytest.mark.skipif(not os.path.exists('/sys/class/block'), reason="Requires Linux sysfs")
def test_successful_slot_enumeration():
    """Test successful slot enumeration on real hardware."""
    from device_discovery import get_max_slot_from_enclosure
    result = get_max_slot_from_enclosure(use_cache=False)
    # Validate against actual hardware state
    assert result >= 0  # Should return non-negative slot count

@pytest.mark.integration
@pytest.mark.skipif(not os.path.exists('/sys/class/block'), reason="Requires Linux sysfs")
def test_successful_projection():
    """Test successful slot projection on real hardware."""
    from device_discovery import get_scsi_host_slot_projections
    result = get_scsi_host_slot_projections()
    # Validate against actual hardware state
    assert isinstance(result, list)
    # Additional hardware-specific assertions
```

### Execution

Integration tests should:
1. Run separately from unit tests (different pytest marker)
2. Be executed on the Ubuntu server before production deployments
3. Be optional for development on Windows (skip gracefully)
4. Be documented in CI/CD pipeline as hardware-dependent

### Cleanup

Once integration tests are implemented:
1. Remove the 5 empty stub tests from `test_device_discovery_extended.py`
2. Update test count documentation
3. Add integration test execution instructions to deployment documentation

## Timeline

- [ ] Design integration test structure
- [ ] Implement `test_hardware_discovery.py` on Ubuntu server
- [ ] Validate tests pass on actual hardware
- [ ] Remove empty stubs from `test_device_discovery_extended.py`
- [ ] Update CI/CD documentation
- [ ] Add integration test execution to deployment checklist

## Notes

- These tests cannot run on Windows development machines
- They require actual hardware with storage controllers
- They should be part of pre-deployment validation, not every commit
- Consider adding hardware simulation (QEMU) for CI/CD if feasible
