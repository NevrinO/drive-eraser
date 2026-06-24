# Drive Wipe Station Lifecycle

## Purpose

This document defines the full operational lifecycle for a drive processed by the wipe station. It is intended to standardize technician workflow, guide backend implementation, and ensure that erase operations are safe, auditable, and understandable.

This lifecycle applies to enterprise drives connected through the wipe station, including:

- SATA
- SAS
- NVMe / U.2 NVMe

The lifecycle is designed to support:

- technician clarity
- safety controls
- method-aware erase behavior
- verification consistency
- audit logging
- post-erase marker handling
- certificate generation

---

## Lifecycle Overview

The primary lifecycle is:

DETECTED → IDENTIFIED → INSPECTED → WIPE_READY → ERASING → VERIFYING → MARKING → CERTIFIED → COMPLETE

Possible branch states:

- INSPECTED → REJECTED
- ERASING → ERASE_FAILED
- VERIFYING → VERIFY_FAILED
- MARKING → MARK_FAILED_WARNING → CERTIFIED

This means a drive can complete successfully even if marker writing fails, as long as erase and verification succeeded.

---

## Lifecycle Goals

The lifecycle exists to solve the following operational problems:

1. Prevent accidental wiping of protected drives
2. Reduce dependence on `/dev/sdX` naming and manual comparison
3. Standardize method selection across interfaces
4. Separate "erase command completed" from "erase trusted"
5. Preserve technician visibility into what the system is doing
6. Produce a repeatable audit trail
7. Provide future confidence using a post-erase marker

---

## State Definitions

### 1. DETECTED

#### Definition
A drive is physically present in a bay and the operating system detects a device associated with that bay.

#### Entry Conditions
- A drive is inserted into a hot swap bay
- The server detects a new block or NVMe device
- The application observes the presence of the new drive

#### System Actions
- associate the device with a physical bay if possible
- capture basic information such as:
  - bay number
  - device path
  - preliminary interface type
- mark the bay as populated

#### Technician View
- bay changes from empty to occupied
- status may show:
  - `Drive Detected`
  - `Scanning...`

#### Notes
This state does not imply the drive is trusted, healthy, or ready for wiping. It only means a device is present.

---

### 2. IDENTIFIED

#### Definition
The system has successfully read enough identity data from the drive to know what it is.

#### Entry Conditions
- Drive was detected
- System can communicate with the device

#### System Actions
- query drive identity information
- gather:
  - serial number
  - model
  - size
  - protocol/interface
- evaluate bay protection rules
- determine whether this appears to be:
  - a protected OS drive
  - a reserved slot
  - a wipe candidate

#### Technician View
- full drive details become visible
- serial, model, capacity, and interface badges display
- protected drives appear clearly non-wipeable

#### Notes
This state replaces the current manual before/after `lsblk` comparison and reduces reliance on device naming.

---

### 3. INSPECTED

#### Definition
The system has evaluated the drive for health, accessibility, and supported erase methods.

#### Entry Conditions
- Drive has been identified successfully
- Basic communication is stable

#### System Actions
- perform health and status checks
- query supported sanitize / erase capabilities
- determine:
  - whether the drive is stable enough for software wipe
  - which erase methods are supported
  - which erase method is recommended
- inspect for a previously written station marker

#### Outputs
This state should produce structured inspection data including:
- supported methods
- recommended method
- health classification
- marker status
- warnings
- rejection reason if applicable

#### Technician View
- health summary
- key SMART or controller attributes
- wipe method recommendation
- warnings if the drive is degraded

#### Possible Outcomes
- Proceed to `WIPE_READY`
- Proceed to `REJECTED`

---

### 4. REJECTED

#### Definition
The drive should not be wiped through this station under normal workflow.

#### Common Reasons
- drive is in a protected or reserved bay
- serial or identity cannot be read reliably
- drive disappears during inspection
- no supported wipe method is available
- controller/media behavior makes trusted erase unlikely
- required command path is unavailable
- health is too poor for trusted software wipe

#### Policy Type
For protected and reserved bays, this is a hard stop.

For poor drive health, this is currently a soft stop:
- system strongly recommends destruction
- technician may be allowed to override based on policy

#### Technician View
- strong warning such as:
  - `Do Not Wipe in Station`
  - `Recommend Physical Destruction`
- reason displayed clearly

#### Logging Requirements
- serial if available
- model if available
- reason for rejection
- bay
- technician interaction if override is permitted

---

### 5. WIPE_READY

#### Definition
The drive is approved for wiping and waiting for technician confirmation.

#### Entry Conditions
- inspection completed
- at least one supported wipe method exists
- no hard stop prevents wiping

#### System Actions
- preselect the best supported wipe method
- populate wipe method dropdown
- allow override to another supported method
- show warnings if:
  - drive is degraded
  - marker state is stale or invalid

#### Technician Inputs
- technician name
- ticket number
- typed confirmation string, such as `ERASE`
- optional method override

#### Technician View
- recommended wipe method selected by default
- supported alternatives available
- any warnings clearly shown before starting

#### Policy Notes
- app auto-selects the best method
- technician may override only to another supported method
- override should be logged

---

### 6. ERASING

#### Definition
The selected wipe command has been issued and the drive is actively being sanitized.

#### Entry Conditions
- technician confirmed wipe
- required fields completed
- selected method is valid and supported

#### System Actions
- capture final pre-wipe metadata:
  - serial
  - model
  - bay
  - selected method
  - recommended method
  - technician
  - ticket
  - start time
- execute the erase command
- poll for progress or status when possible
- prevent duplicate or conflicting actions

#### Technician View
- status like:
  - `Erasing`
  - `Sanitize in Progress`
  - `Waiting for Completion`
- progress indicator if supported
- otherwise stage/status messaging

#### Notes
At this point the drive is considered in-process and should not be reconfigured until erase completes or fails.

---

### 7. ERASE_FAILED

#### Definition
The erase command failed, aborted, or returned an error before completion.

#### Entry Conditions
- Erase command was initiated
- Command returned non-zero exit code
- Command timed out
- Device became inaccessible during erase

#### System Actions
- capture failure details:
  - exit code if available
  - error message from command
  - device state at failure
- log the failure with full context
- mark job as failed in database
- preserve partial results for debugging

#### Technician View
- status like:
  - `Erase Failed`
  - `Sanitize Error`
- error message displayed
- drive may be retried if appropriate

#### Logging Requirements
- serial
- model
- selected method
- failure reason
- exit code or error details
- timestamp

---

### 8. VERIFYING

#### Definition
The system is verifying that the erase operation was successful and the drive is now in a sanitized state.

#### Entry Conditions
- Erase command completed successfully
- Device is still accessible
- Verification is enabled in policy

#### System Actions
- perform verification appropriate to the erase method:
  - for overwrite: read back sectors and verify zeros/random pattern
  - for sanitize: check sanitize status via ATA/NVMe commands
  - for crypto: verify secure erase completion
- capture verification results
- check for bad sectors if present during erase

#### Outputs
- verification result (pass/fail)
- verification method used
- sectors verified (if applicable)
- bad sector count (if applicable)

#### Technician View
- status like:
  - `Verifying`
  - `Confirming Sanitization`
- progress indicator if applicable

#### Notes
Verification is a critical trust step. Even if the erase command reported success, verification confirms the actual state of the media.

---

### 9. VERIFY_FAILED

#### Definition
Verification indicated that the erase may not have been successful or the drive state cannot be confirmed.

#### Entry Conditions
- Erase command completed
- Verification check failed
- Verification could not be performed

#### System Actions
- log verification failure details
- preserve job record with verification status
- flag for technician review
- do not generate certificate

#### Technician View
- status like:
  - `Verification Failed`
  - `Unable to Confirm Sanitization`
- warning that certificate cannot be generated
- recommendation for physical destruction or re-attempt

#### Policy Notes
- This is a hard stop for certificate generation
- Drive may be re-erased if appropriate
- Physical destruction may be recommended

---

### 10. MARKING

#### Definition
The system is writing a post-erase marker to the drive to indicate it has been processed by this station.

#### Entry Conditions
- Erase completed successfully
- Verification passed (if enabled)
- Marker writing is enabled in policy

#### System Actions
- write station marker to drive:
  - for SATA: write to last sector or HPA/DCO area
  - for NVMe: write to namespace or reserved area
- marker includes:
  - station ID
  - timestamp
  - technician (if applicable)
  - method used
- verify marker was written successfully

#### Technician View
- status like:
  - `Writing Marker`
  - `Tagging Drive`

#### Notes
Marker writing is optional and may fail without affecting the overall success of the erase operation.

---

### 11. MARK_FAILED_WARNING

#### Definition
The erase and verification succeeded, but marker writing failed.

#### Entry Conditions
- Erase completed successfully
- Verification passed (if enabled)
- Marker writing failed

#### System Actions
- log marker failure as warning
- proceed to certificate generation
- note marker status in certificate

#### Technician View
- status like:
  - `Marker Write Failed`
  - `Proceeding to Certificate`
- warning that marker was not written
- certificate will still be generated

#### Notes
This is a non-critical failure. The drive is still considered successfully erased, but future stations will not be able to read the marker.

---

### 12. CERTIFIED

#### Definition
The system has generated a certificate documenting the successful erase operation.

#### Entry Conditions
- Erase completed successfully
- Verification passed (if enabled)
- All required data captured

#### System Actions
- generate certificate with:
  - serial
  - model
  - capacity
  - method used
  - start/end time
  - verification result
  - technician
  - ticket number
  - station ID
- save certificate to database
- make certificate available for download/print
- log certificate generation

#### Technician View
- status like:
  - `Certified`
  - `Complete`
- certificate available in audit vault
- option to print or download

#### Notes
The certificate is the official record of the erase operation and may be required for compliance or audit purposes.

---

### 13. COMPLETE

#### Definition
The drive has completed the full lifecycle and is ready for removal or next steps.

#### Entry Conditions
- Certificate generated (or certificate generation skipped by policy)
- All logging complete
- Job record finalized

#### System Actions
- mark job as complete in database
- clear drive from active processing
- return bay to ready state
- archive job data for retention period

#### Technician View
- status like:
  - `Complete`
  - `Ready for Removal`
- drive can be removed from bay
- certificate available in history

#### Notes
This is the terminal state for a successful lifecycle. The drive may now be removed and the bay used for the next drive.

---

## Lifecycle Diagram

```
DETECTED
    ↓
IDENTIFIED
    ↓
INSPECTED → REJECTED
    ↓
WIPE_READY
    ↓
ERASING → ERASE_FAILED
    ↓
VERIFYING → VERIFY_FAILED
    ↓
MARKING → MARK_FAILED_WARNING
    ↓
CERTIFIED
    ↓
COMPLETE
```

---

## Policy Configuration

The lifecycle behavior can be configured via `config/policy.json`:

- `strict_audit_mode`: Requires non-empty wipe_passphrase and enforces verification
- `prewipe_spot_check`: **Not currently implemented** - Configuration option exists in policy.json but the pre-wipe spot check feature is not yet executed in the wipe workflow
- `post_erase_marker`: Enables post-erase marker writing
- `allow_method_override`: Allow technicians to override the recommended erase method
- `crypto_verification_mode`: `conservative_probe`, `full_verify`, or `disabled`
- `discovery_max_workers`: Parallel SMART query threads during discovery
- `max_concurrent_wipes`: Maximum simultaneous erase jobs
- `blockdev_post_wipe_retries`: Retry attempts for post-wipe `blockdev --getsize64`
- `blockdev_post_wipe_retry_delay`: Seconds between post-wipe blockdev retries
- `certificate_retention_days`: How long to keep certificates in database
- `log_retention_days`: How long to keep operational logs

---

## Physical Slot Mapping Configuration

The drive discovery and bay mapping system uses an enclosure-based physical slot architecture configured via `config/bay_map.json`:

### Schema Structure

**Templates**: Define physical layout independent of hardware
- `id`: Template identifier (e.g., "dell_r440_10bay")
- `name`: Human-readable template name
- `vendor`: Hardware vendor
- `slot_count`: Total number of physical slots
- `hybrid_slots`: Array of physical slot numbers that support multiple interface types
- `traversal_preset`: Slot traversal order for workbench display
- `default_role`: Default role for slots (wipe, os, reserved)

**Enclosures**: Physical chassis/backplanes with hardware bindings
- `id`: Enclosure identifier
- `name`: Human-readable enclosure name
- `template_id`: Reference to template definition
- `pci_controller`: PCI address of HBA/controller (e.g., "0000:af:00.0")
- `expander_sas_address`: SAS address of expander (null for direct AHCI/NVMe)
- `display_order`: Display order in UI
- `slots`: Physical slot mappings
  - `physical_slot_number`: 0-indexed slot number from hardware
  - `label`: Custom display label (e.g., "Bay 1")
  - `role`: Slot role (wipe, os, reserved)
  - `locked`: Whether slot is locked from modification
  - `mappings`: Interface type mappings
    - `sas_sata`: {slot_type, hardware_identifier, auto_detected}
    - `nvme`: {slot_type, hardware_identifier, auto_detected}

### Supported Slot Types

1. **sas_expander**: HBA + SAS expander topology (`phy-X:Y:Z`)
2. **sas_direct**: Direct HBA connection (`phy-X:Y`)
3. **motherboard_sata**: Onboard AHCI ports (`ataX`)
4. **pcie_nvme**: U.2/U.3 hotplug bays (PCIe slots from `/sys/bus/pci/slots/`)

### Master Slot Map

The system automatically generates a master slot map by scanning sysfs:
- PCI controller addresses
- Slot types (SAS expander, SAS direct, motherboard SATA, PCIe NVMe)
- Expander SAS addresses (for collision prevention in multi-expander setups)
- Physical slot numbers
- Hardware identifiers (phy paths, PCIe slot numbers)

This map is cached for 60 seconds to reduce sysfs overhead while keeping drive presence detection real-time for hot-swapping.

### MPIO (Multipath I/O) Resolution

When a drive is dual-ported under MPIO, the system automatically resolves both paths to a single Device Mapper node:
- Checks `/sys/block/<dev>/holders` for dm-* entries
- Maps to `/dev/mapper/mpathX` or `/dev/dm-X` as the unified device path
- Presents a single device to the UI and wipe operations

### Setup Instructions

1. **Initial Setup**: Use the System Administration panel to create enclosures
2. **Controller Selection**: Select PCI controller and expander SAS address from master map
3. **Template Selection**: Choose a template matching your physical chassis
4. **Auto-Mapping**: System auto-detects slot mappings (0→0, 1→1, etc.)
5. **Hybrid Configuration**: For hybrid templates, select starting PCIe NVMe slot for auto-incrementing
6. **Verification**: Review auto-detected mappings against actual drive presence
7. **Manual Override**: Correct any incorrect mappings before saving
8. **Multi-Enclosure**: Repeat for additional enclosures as needed

### Hybrid NVMe Bay Configuration

Some enclosures support both SAS/SATA and NVMe drives in the same physical slot (hybrid bays). To configure hybrid bays:

1. **Template Configuration**: In the Template Management panel, specify which physical slots are hybrid using the `hybrid_slots` field (comma-separated bay numbers, e.g., "1,5,9").
2. **Enclosure Mapping**: When creating an enclosure from a hybrid template, the system will:
   - Display both SAS/SATA and NVMe mapping options for hybrid slots
   - Allow auto-incrementing NVMe slot numbers for sequential PCIe slot mapping
   - Store separate hardware identifiers for each interface type
3. **Drive Detection**: During discovery, the system:
   - Detects which interface type is present in each hybrid slot
   - Updates the `auto_detected` flag for the active interface
   - Falls back to the configured mapping if auto-detection fails
4. **UI Display**: Hybrid slots show the active interface type based on the detected drive

**Example**: A 16-bay enclosure with slots 1, 5, 9, and 13 configured as hybrid:
- Slot 1 can accept either a SAS drive (via SAS expander) or an NVMe drive (via PCIe slot 0)
- When an NVMe drive is inserted in slot 1, the system detects it at `/sys/bus/pci/slots/0` and maps it accordingly
- When a SAS drive is inserted, the system detects it via the SAS expander phy path

---

## Error Handling

### Transient Errors
- Device temporarily unavailable: Retry with backoff
- Command timeout: Increase timeout or abort based on severity
- Intermittent communication: Retry limited number of times
- `drive_detached_post_wipe`: The drive dropped off the bus after erase; retried according to `blockdev_post_wipe_retries`/`blockdev_post_wipe_retry_delay`. If the device reappears, verification continues; otherwise it fails with this distinct code.

### Permanent Errors
- Device failure: Mark as failed, recommend physical destruction
- Unsupported method: Reject drive for that method
- Protected bay: Hard stop, do not allow wipe

### Warnings
- Marker write failure: Continue to certificate, note in record
- Degraded health: Allow with technician override if policy permits
- Verification skipped: Note in certificate if verification disabled

---

## Audit Trail

Every lifecycle transition should be logged with:

- Timestamp
- Serial number
- Model
- Bay
- Previous state
- New state
- Technician (if applicable)
- Context data (method, error details, etc.)

This audit trail provides a complete history of each drive's processing and is essential for compliance and troubleshooting.