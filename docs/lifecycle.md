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

DETECTED → IDENTIFIED → INSPECTED → HEALTH_GATE → WIPE_READY → ERASING → VERIFYING → MARKING → CERTIFIED → COMPLETE

Possible branch states:

- INSPECTED → REJECTED
- HEALTH_GATE → REJECTED (health gate failure)
- ERASING → ERASE_FAILED
- ERASING → CANCELLED (technician-initiated cancellation)
- VERIFYING → VERIFY_FAILED
- MARKING → MARK_FAILED_WARNING → CERTIFIED

This means a drive can complete successfully even if marker writing fails, as long as erase and verification succeeded.

A drive can also be cancelled by the technician while in the `ERASING` state, which is a terminal state — the job is marked as cancelled and the drive must be re-erased from scratch if needed.

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

### 4.5 HEALTH_GATE

#### Definition
The system has evaluated the drive's SMART health and I/O error status to determine if it is safe to proceed with wiping.

#### Entry Conditions
- Drive has been inspected successfully
- Pre-wipe health gate is enabled in policy (`prewipe_health_gate_*` keys)

#### System Actions
- evaluate drive health score against configured thresholds
- check for critical SMART pre-fail attributes (Bit 3/Bit 4)
- check for excessive I/O errors or grown defect lists
- determine whether the drive is stable enough for a potentially hours-long wipe

#### Possible Outcomes
- Proceed to `WIPE_READY` (health is acceptable)
- Proceed to `REJECTED` (health is too poor — fail fast to avoid wasting time on a dying drive)

#### Technician View
- If passed: no visible interruption, proceeds to wipe-ready state
- If failed: job is immediately marked as failed with a health-gate rejection reason

#### Implementation
- `backend/smart_health_gate.py` — `pre_wipe_health_gate()`
- Called from `backend/job_management.py` — `check_health_gate_sync()`

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
- typed confirmation string: `erase BAY <display_number>` (single drive) or `erase <count> drives` (multiple drives)
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

### 7.5 CANCELLED

#### Definition
The technician manually cancelled the erase job while it was running or queued.

#### Entry Conditions
- Erase job was in `running` or `queued` state
- Technician issued cancel via `POST /api/erase/jobs/<job_id>/cancel`

#### System Actions
- terminate the running erase process if applicable
- mark job as `cancelled` in database
- release the wipe semaphore slot
- log the cancellation with timestamp and technician context

#### Technician View
- status shows `Cancelled`
- drive may need to be re-erased from scratch if the wipe was partially complete

#### Notes
This is a terminal state. The job cannot be resumed — a new erase job must be started if the drive needs to be wiped.

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

```text
DETECTED
    ↓
IDENTIFIED
    ↓
INSPECTED → REJECTED
    ↓
HEALTH_GATE → REJECTED (health gate failure)
    ↓
WIPE_READY
    ↓
ERASING → ERASE_FAILED
    |       → CANCELLED
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

**Core Policy**:
- `strict_audit_mode`: Requires non-empty wipe_passphrase (≥ 8 chars) and enforces verification
- `prewipe_zero_detection_enabled`: Enable/disable automatic pre-wipe zero detection (runs as a background visual-only check before a wipe)
- `post_erase_marker`: Enables post-erase marker writing
- `allow_method_override`: Allow technicians to override the recommended erase method
- `method_priority`: Object mapping interface types (`nvme`, `sas`, `sata`) to ordered method arrays (e.g., `["crypto", "block", "overwrite"]`)

**Verification Policy**:
- `secondary_verification_mode`: `conservative_probe`, `full_verify`, or `disabled` (deprecated alias `crypto_verification_mode` still accepted)

**Pre-Wipe Health Gate**:
- `prewipe_health_gate_enabled`: Enable/disable the pre-wipe health gate check
- `prewipe_health_gate_min_score`: Minimum health score required to proceed (0-100)
- `prewipe_health_gate_max_reallocated`: Maximum reallocated sectors allowed
- `prewipe_health_gate_max_pending`: Maximum pending sectors allowed
- Additional `prewipe_health_gate_*` keys for SAS defect thresholds, power-on hours, etc.

**Zero-Check Configuration**:
- `zero_detection_concurrency_limit`: Maximum concurrent zero-check jobs
- `zero_check_sample_size`: Sample size for zero-check reads
- `zero_check_timeout_seconds`: Hard timeout for zero-check operations

**Discovery & SMART**:
- `discovery_max_workers`: Parallel SMART query threads during discovery
- `background_smart_max_workers`: Maximum workers for background extended SMART collection
- `discovery_diag`: Enable/disable discovery diagnostics

**Job Management**:
- `max_concurrent_wipes`: Maximum simultaneous erase jobs
- `blockdev_post_wipe_retries`: Retry attempts for post-wipe `blockdev --getsize64`
- `blockdev_post_wipe_retry_delay`: Seconds between post-wipe blockdev retries

**Triage Thresholds** (nested object under `triage_thresholds`):
- `ssd_new_poh_threshold`, `ssd_high_poh_threshold`: SSD power-on hour thresholds
- `hdd_new_poh_threshold`: HDD power-on hour threshold
- `health_score_destroy_threshold`, `health_score_scratch_threshold`, `health_score_good_threshold`: Health score action thresholds
- `ssd_new_fdw_threshold`, `hdd_new_fdw_threshold`: Full drive write thresholds
- `realloc_raw_new_threshold`: Reallocated sector threshold
- `sas_grown_defect_fail_threshold`, `sas_nme_advisory_threshold`, `sas_nme_penalty_threshold`, `sas_sticky_lba_threshold`, `sas_high_poh_threshold`: SAS-specific thresholds

**Certificate Settings**:
- `max_logo_size_mb`: Maximum logo file size for certificates
- `max_bulk_cert_batch_size`: Maximum batch size for bulk certificate generation

---

## Physical Slot Mapping Configuration

The drive discovery and bay mapping system uses an enclosure-based physical slot architecture configured via `config/bay_map.json`. The system supports:

- **Enclosure templates** with configurable slot counts, hybrid slots, and traversal presets
- **Multiple slot types**: SAS expander, SAS direct, motherboard SATA, PCIe NVMe
- **Master slot map** auto-generated from sysfs (cached 60 seconds)
- **MPIO resolution** for dual-ported SAS drives
- **Hybrid bays** supporting both SAS/SATA and NVMe in the same physical slot

For detailed schema reference, setup instructions, hybrid configuration, and worked examples, see **[enclosure-mapping-guide.md](enclosure-mapping-guide.md)**.

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