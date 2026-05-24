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
- optionally perform pre-wipe spot check if enabled

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
  - pre-wipe spot check showed issues
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
The erase command failed, aborte