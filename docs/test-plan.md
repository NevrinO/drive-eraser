# Test Plan

## Purpose
Repeatable validation plan for backend, frontend, and operational behaviors, updated to cover multi-vector health scoring and byte-accurate traffic units.

## Preconditions
- Service installed and running
- `bay_map.json` configured for current hardware
- At least one wipe bay with inserted test drive
- API reachable on configured bind/port

---

## Technical Validation Scenarios

### Test Case 1: SSD Wear Baseline and POH Controller Fatigue
* **Objective**: Confirm SSD base health starts at remaining flash cell endurance and applies a gentle penalty for extreme operational runtime.
* **Procedure**:
  1. Insert an NVMe or SATA SSD with known wear (e.g., 11% wear, which means 89% life remaining).
  2. Verify that `api/drives` returns `health_score` around `89`.
  3. Insert/simulate an SSD with > 40,000 Power-On Hours.
  4. Verify that a progressive penalty (max 20% at 80,000 hours) is applied to the SSD base health, with a hard floor of 10%.
* **Pass Criteria**: SSD health successfully reflects remaining flash wear and accounts for extreme electrical fatigue without double-dipping.

### Test Case 2: HDD Mechanical Aging (POH & Workload FDW)
* **Objective**: Confirm HDD base health ages gracefully based on motor/bearing runtime and active head write workload.
* **Procedure**:
  1. Insert a mechanical HDD with 30,000 Power-On Hours and low Full Drive Writes (FDW < 5). Confirm health is ~91%.
  2. Insert a mechanical HDD with 30,000 Power-On Hours and high Full Drive Writes (FDW >= 150). Confirm health is ~62%.
* **Pass Criteria**: Spinning disks degrade non-linearly based on both run-time stress and write-head wear.

### Test Case 3: Reallocated Sectors (HDD Strict vs. SSD Spare Reserve)
* **Objective**: Validate that HDDs are strictly penalized for bad sectors while SSDs are only penalized if their available spare pool depletes.
* **Procedure**:
  1. **HDD Check**: Insert/simulate an HDD with 1 bad sector. Verify its health score immediately drops by `10%`. Insert an HDD with 6 bad sectors, verify a `40%` flat health reduction.
  2. **SSD Check**: Insert an SSD with a non-zero reallocated sector count (e.g., 4 blocks) but where the `Available Spare` attribute remains at `100%`. Verify that the reallocated sector penalty is `0%`.
* **Pass Criteria**: Minor, quarantined bad sectors on SSDs do not lower the life expectancy bar, while any physical HDD platter wounds are flagged immediately.

### Test Case 4: Byte-Accurate Traffic Scaling
* **Objective**: Verify that SSDs reporting reads and writes in 32 MiB blocks (such as Intel/Solidigm drives) are parsed correctly and do not under-report.
* **Procedure**:
  1. Insert an Intel SSD reporting `Host_Writes_32MiB` in Attribute 241 (e.g., raw value `22,918,755`).
  2. Confirm `/api/drives` payload returns a high `data_written_bytes` value (~769 TB / 699.4 TiB) instead of a low sector-based value (~10.9 GiB).
  3. Confirm the frontend "Details Viewer" modal displays the volume in `TiB` or `PiB` correctly.
* **Pass Criteria**: Read/write metrics display actual byte volumes, scaling dynamically by attribute name.

### Test Case 5: SAS G-List Integrity Check
* **Objective**: Confirm SAS bad sectors are read from the physical Grown Defect List (G-list) and are not fouled by background soft ECC correction logs.
* **Procedure**:
  1. Insert a SAS HDD (e.g., Seagate ST4000NM0023) with a non-zero soft ECC error count but `0` grown defects.
  2. Confirm the details modal reports exactly `0` reallocated sectors, and overall health remains unimpaired.
* **Pass Criteria**: Standard soft ECC adjustments are ignored; only actual physical defects affect the health score.

### Test Case 6: Post-Wipe `blockdev` Retry and Detached-Drive Error Code
* **Objective**: Verify that a transient post-wipe `blockdev --getsize64` failure is retried before failing, and that a detached drive produces a distinct error code.
* **Procedure**:
  1. Configure `blockdev_post_wipe_retries` to `2` and `blockdev_post_wipe_retry_delay` to `1`.
  2. Mock `subprocess.run` for `blockdev --getsize64` to fail twice with `Inappropriate ioctl for device`, then succeed on the third attempt.
  3. Verify the sampled zero check succeeds and logs the retry attempts.
  4. Mock `blockdev` to fail every attempt with `No such device`.
  5. Verify the returned error is `drive_detached_post_wipe`, not `secondary_capacity_check_failed`.
* **Pass Criteria**: Retries are exhausted correctly; detached drives are distinguished from other blockdev failures.

### Test Case 7: Overwrite Marker "Written Since Wipe" Diagnosis
* **Objective**: Confirm that the marker write-tolerance check reports `pristine` after an overwrite, or that the diagnostic logs show the exact SMART diff.
* **Procedure**:
  1. Run an overwrite wipe on a test SATA/SAS drive.
  2. Inspect the marker write logs for the captured `data_written_at_wipe` and post-marker `data_written_raw` values.
  3. Verify the resulting marker status is `pristine_secure` or `pristine_insecure`.
  4. If the status is `written_since_wipe`, verify the log shows the diff and the tolerance used.
* **Pass Criteria**: The marker status is consistent with the SMART counter behavior; if it is not, logs provide enough data to diagnose the unit/granularity issue.

### Test Case 8: Secure Mode Badge Reflects `strict_audit_mode`
* **Objective**: Verify that the header badge reflects the `strict_audit_mode` policy value, not the `wipe_passphrase` presence.
* **Procedure**:
  1. Set `strict_audit_mode=true` and a non-empty `wipe_passphrase`; confirm badge shows "SECURE MODE".
  2. Set `strict_audit_mode=false` with a non-empty `wipe_passphrase`; confirm badge shows "UNSECURED MODE".
  3. Verify `/api/status` returns both `passphrase_enabled` and `strict_audit_mode`.
* **Pass Criteria**: Badge color/text tracks `strict_audit_mode`; passphrase presence only affects marker signing.

---

## Protocol-Specific Matrix

| Protocol | Detection Expectation | Expected Preferred Methods |
|---|---|---|
| NVMe | `interface_type=nvme` | `crypto`, `block`, `overwrite` |
| SATA | `interface_type=sata` | `crypto`, `block`, `overwrite` |
| SAS | `interface_type=sas` | `crypto`, `block`, `overwrite` |

---

## New Feature Test Cases

### Test Case 9: Pre-Wipe Health Gate
* **Objective**: Verify the health gate rejects drives with critical SMART failures before starting a wipe.
* **Procedure**:
  1. Insert/simulate a drive with health score below the `prewipe_health_gate_min_score` threshold.
  2. Attempt to start an erase job.
  3. Verify the job is immediately marked as `failed` with a health-gate rejection reason.
  4. Insert/simulate a healthy drive and verify the job proceeds normally.
* **Pass Criteria**: Unhealthy drives are rejected before the erase command is issued; healthy drives proceed to wipe.

### Test Case 10: SMART Self-Test Runner
* **Objective**: Verify SMART self-tests can be started, polled, and cancelled.
* **Procedure**:
  1. Start a short SMART self-test via `POST /api/admin/drives/<device>/smart-test`.
  2. Poll `GET /api/admin/drives/<device>/smart-test-status` and verify progress is reported.
  3. Wait for completion and verify the final status is reported correctly.
  4. Start a long test and verify it can be cancelled.
* **Pass Criteria**: Self-test lifecycle (start → running → completed/cancelled) works correctly.

### Test Case 11: Batch Intake Triage Report
* **Objective**: Verify the triage report displays all connected drives with correct recommendations.
* **Procedure**:
  1. Insert multiple drives with varying health levels (healthy, degraded, failing).
  2. Navigate to the Batch Intake Triage tab (Tab 2).
  3. Verify each drive appears with correct serial, model, health score, and recommendation.
  4. Verify the flag column highlights anomalous signals (FAILED status, halted scan, uncorrectable errors).
* **Pass Criteria**: Triage report accurately reflects drive health and recommendations for all connected drives.

### Test Case 12: Enclosure CRUD Operations
* **Objective**: Verify enclosure creation, update, deletion, and slot management via the admin API.
* **Procedure**:
  1. Create a new enclosure via `POST /api/admin/enclosures`.
  2. Add slots via `POST /api/admin/enclosures/<id>/slots`.
  3. Update slot mappings via `PUT /api/admin/enclosures/<id>/slots/<num>/mappings/<type>`.
  4. Retrieve the enclosure via `GET /api/admin/enclosures/<id>` and verify all changes.
  5. Delete a slot and verify it is removed.
  6. Delete the enclosure and verify it is removed.
* **Pass Criteria**: Full CRUD lifecycle for enclosures and slots works correctly via API.

### Test Case 13: Zero-Check Manager
* **Objective**: Verify zero-check jobs can be started, polled, and cancelled.
* **Procedure**:
  1. Start a zero-check via `POST /api/drives/<bay>/zero-check`.
  2. Verify the zero-check status is reported in the drive payload.
  3. Cancel the zero-check via `DELETE /api/drives/<bay>/zero-check`.
  4. Verify the zero-check state is cleared.
* **Pass Criteria**: Zero-check lifecycle (start → running → cancelled) works correctly.

### Test Case 14: WebSocket Real-Time Updates
* **Objective**: Verify WebSocket events are pushed to the frontend for drive discovery and SMART updates.
* **Procedure**:
  1. Open the web UI and verify WebSocket connection is established.
  2. Insert a drive and verify the drive appears without a manual page refresh.
  3. Wait for background SMART collection and verify SMART data updates are pushed.
  4. Disconnect the WebSocket and verify the frontend falls back to polling.
* **Pass Criteria**: Real-time updates work via WebSocket; polling fallback activates when WebSocket is unavailable.

### Test Case 15: Drive Model Risk Profiles
* **Objective**: Verify per-model thresholds are applied during health scoring.
* **Procedure**:
  1. Configure a drive model profile in `config/drive_models.json` with a specific `trip_temperature`.
  2. Insert a drive matching that model and verify the trip temperature threshold is applied.
  3. Insert a drive not in the profiles and verify generic thresholds are used.
  4. Verify `GET /api/admin/drive-models` returns all configured profiles.
* **Pass Criteria**: Model-specific thresholds are applied when available; generic thresholds are used as fallback.

### Test Case 16: Kill-All-Jobs
* **Objective**: Verify the kill-all-jobs endpoint terminates all running erase jobs.
* **Procedure**:
  1. Start multiple erase jobs across different bays.
  2. Call `POST /api/admin/jobs/kill-all`.
  3. Verify all running jobs are terminated and marked as failed.
  4. Verify the wipe semaphore slots are released.
* **Pass Criteria**: All running jobs are terminated; system returns to a clean state.