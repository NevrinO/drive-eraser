# Roadmap: Future Enhancements

This document outlines planned future enhancements and features for the Drive Eraser project.

---

## Live Testing Fixes & Post-Wipe Verification Resilience

**Status**: In Progress
**Priority**: High
**Related Plan**: `c:\Users\BStra\.windsurf\plans\live-testing-fixes-53d81a.md`

### Summary
A coordinated set of fixes and improvements driven by live testing observations. The work covers UX confusion (secure-mode badge, confirmation labels, sanitize button visibility), post-wipe reliability (blockdev retry after transient bus resets, marker write tolerance), and operational policy exposure (admin UI for system configuration).

### Key Deliverables
- Secure-mode badge reflects `strict_audit_mode` instead of `wipe_passphrase`
- Post-wipe `blockdev --getsize64` retry policy with distinct `drive_detached_post_wipe` error code
- System Configuration admin panel exposing station ID, Slack webhook, crypto verification mode, discovery workers, max concurrent wipes, and blockdev retry settings
- Pre-wipe failure detection / fail-fast health gate
- Improved overwrite marker diagnostics and deep-dive process review

### Documentation
- `api-contract.md`, `lifecycle.md`, `test-plan.md`, `troubleshooting.md`, `SOP_technician_guide.md`, `change-log.md`, `CODE_MAP.md`

---

## Offline Queueing for Air-Gapped Deployments

**Status**: Future Enhancement
**Priority**: Medium
**Use Case**: Air-gapped deployments where network connectivity is unavailable or restricted

### Description
In air-gapped environments (e.g., secure facilities, SCADA systems, isolated networks), the Drive Eraser server may not have network access to external systems. Currently, the system requires real-time network connectivity for certain operations (e.g., webhook notifications, certificate distribution).

### Proposed Implementation
- Implement a local job queue that persists erase requests to disk
- Allow technicians to queue jobs without immediate network connectivity
- Add a "sync" mechanism to export queued jobs and certificates to portable media (USB, external drive)
- Support batch import/export of job records and certificates for air-gapped audit trails
- Add offline mode detection and UI indicators when network is unavailable

### Technical Considerations
- Queue persistence in SQLite database (already implemented for job history)
- Export format: JSON bundles with job metadata, certificates, and audit trails
- Import format: Validation and merge of external job records
- Conflict resolution: Handle duplicate job IDs when syncing between systems
- Security: Validate and sign exported bundles to prevent tampering

### Dependencies
- None (can be implemented independently)
- Would enhance existing job persistence infrastructure

---

## Additional Security Hardening

**Status**: Future Enhancement
**Priority**: High
**Related**: See docs/SECURITY_DEVIATIONS.md entry for systemd NoNewPrivileges

### Proposed Enhancements
- Enable `NoNewPrivileges=true` in systemd service file after testing
- Add `PrivateDevices=true` to restrict access to hardware devices
- Add `ProtectHome=true` to restrict access to user home directories
- Add `RestrictAddressFamilies=AF_UNIX AF_INET` to limit socket families
- Add `SystemCallFilter=@system-service` to restrict system calls
- Implement AppArmor or SELinux profiles for additional containment

---

## SMART Tracking & Health Assessment Improvements

**Status**: Planned
**Priority**: High
**Source**: Analysis of live support bundle `support-bundle-kill-a-ssd-20260619-132119` containing 18 unique SAS HDDs across 37 dual-port device nodes, including one drive with 16,396 grown defects that the system currently reports as healthy.

### Background

Real-world SAS drive data exposed several gaps in the current `smart_parsing.py` health model. The most critical: a physically dead SAS HDD (`Elements in grown defect list: 16396`, background scan `halted due to fatal error`, 6 uncorrectable verify errors) would pass through the current health gate with `status = "PASSED"` because the SAS SMART health byte is unreliable and none of the SAS-specific failure signals are parsed or acted upon.

Two drives from support bundle `support-bundle-kill-a-ssd-20260619-132119` are documented below as concrete case studies demonstrating both failure modes: a dead drive the system considers healthy, and a healthy drive the system scores near-zero.

---

### Case Study 1 — `/dev/sdm` (Serial: `Z1Z3MFCJ0000C436C611`)
**Failure mode: Dead drive reported as healthy**

#### Raw SMART facts
| Field | Value |
|-------|-------|
| Model | Seagate ST4000NM0023 Rev 0003 |
| Manufactured | Week 13, 2014 |
| Power-on hours | 8,831 |
| SMART Health Status (device byte) | **OK** |
| Elements in grown defect list | **16,396** |
| Background scan status | **halted due to fatal error** |
| Background scan log entries | 457+ entries, nearly all `[4,32,0]` "Reassignment by disk failed" |
| Verify uncorrectable errors | **6** |
| Read delayed ECC corrections | 2 |
| NME | 19 (low, unremarkable) |

#### What the current system produces
- `status = "PASSED"` — the SAS SMART health byte reports `OK` and the code trusts it unconditionally
- `reallocated_sectors = 16396` → `realloc_penalty = min(40, 30 + (16396 - 5) * 10)` → capped at **40** — the penalty formula caps out after just 5 defects, so 16,396 and 5 are treated identically
- `verify: 6 uncorrectable` — not parsed, completely invisible
- `background scan: halted due to fatal error` — not parsed, completely invisible
- `failed_override = False` — SMART health byte is OK, no exit status flags set
- **Resulting health score: ~57 (SCRATCH or borderline USED_GOOD)**
- **Resulting recommendation: likely USED_GOOD or SCRATCH** — a technician could wipe and redeploy this drive

#### What the correct outcome is
This drive is **physically dead**. The spare sector pool is exhausted (16,396 grown defects), the drive's own background scanner halted with a fatal error, and there are 6 verify-level uncorrectable errors representing permanent unreadable data regions. The correct outcome is `status = FAILED`, health score ≤ 5, recommendation = DESTROY. **Under no circumstances should this drive be wiped and returned to service.**

#### Which roadmap items fix this
- **Item 1** (SAS status override): grown defect list > 1000 → force `FAILED`
- **Item 3** (parse background scan status): `"halted due to fatal error"` → force `FAILED`
- **Item 5** (parse uncorrectable errors): 6 verify uncorrectable → force `FAILED` + DESTROY recommendation
- **Item 2** (logarithmic grown defect scaling): even without the override, a score of ~5 rather than ~57 for 16,396 defects

---

### Case Study 2 — `/dev/sdp` (Serial: `S1Z1M0YR0000K617GQC4`)
**Failure mode: Healthy drive scored near-zero by SATA-tuned formula**

#### Raw SMART facts
| Field | Value |
|-------|-------|
| Model | Seagate ST4000NM0023 Rev D007 |
| Manufactured | Week 42, 2017 |
| Power-on hours | 45,390 |
| SMART Health Status | OK |
| Elements in grown defect list | **6** (minor, all successfully reassigned) |
| Background scan status | waiting (normal) |
| Scan log entries | 6 entries, all `[1,x,x]` successfully reassigned — no failures |
| Verify uncorrectable errors | 0 |
| Read uncorrectable errors | 0 |
| NME | **57,077,439** |
| Write GB processed | 467,888 GB (~116 full-drive writes) |

#### What the current system produces
Step-by-step score trace:

```
poh_penalty  = min(30, (45390 - 20000) / 40000 * 30) = min(30, 19.04) = 19.04
fdw          = 467,888,000,000,000 / 4,000,787,030,016 = ~116.9x
base_score   = max(40, 100 - 19.04 - min(30, (116.9/150)*30))
             = max(40, 100 - 19.04 - 23.38) = 57.58

realloc_penalty = min(40, 30 + (6-5)*10) = 40   ← capped at max after just 6 defects
nme_penalty     = 10                              ← errs=57M > 50, flat penalty applied
pending_penalty = 0

score = max(0, 57.58 - 40 - 0 - 0 - 10) = 7.58 → 8
```

- **Resulting health score: ~8 (displayed as ~18% in UI)**
- **Resulting recommendation: DESTROY** (score ≤ 30 threshold)

#### What the correct outcome is
This is a **healthy server drive with a workload-appropriate history**. 6 grown defects — all successfully reassigned with no failures — is essentially clean on a SAS HDD. 57 million NME on a Seagate SAS drive at 45,000 hours is normal bus-layer activity, not a media error. The drive completed a background short self-test at 45,390 hours with no errors. The correct health score accounting for legitimate penalties (POH and write workload) is approximately **53–58**, and the correct recommendation is **SCRATCH** (high runtime, heavy workload) — not DESTROY.

A technician acting on the current score would physically destroy a functional 4TB enterprise SAS drive that still has usable life.

#### Which roadmap items fix this
- **Item 2** (logarithmic grown defect scaling): 6 defects → penalty of ~3–5 points, not 40
- **Item 4** (NME mis-threshold): 57M NME on SAS → 0 penalty (below advisory threshold), not -10
- **Corrected score**: `57.58 - ~4 - 0 = ~54` → SCRATCH — accurate and defensible

---

### Item 1 — SAS SMART Status Override (Critical)

**Problem**: `calculate_drive_health_score` and `get_drive_recommendation` both trust `smart_status.passed` for SAS drives. The SAS SMART health byte does not reflect grown defect exhaustion or a halted background scanner — both of which represent physical drive death.

**Required changes**:
- For SAS interface drives, override `status` to `FAILED` before the health byte check if any of the following are true:
  - `scsi_grown_defect_list > 1000`
  - `scsi_background_scan_log.status` contains `"halted"` (any form)
  - `scsi_error_counter_log.verify.total_uncorrectable_errors > 0`
- These overrides must apply in both `get_smart_data` (set `status = "FAILED"`) and as guards in `calculate_drive_health_score`

**Policy config additions**:
- `sas_grown_defect_fail_threshold` (default: `10000`) — grown defect count that forces a FAILED override

---

### Item 2 — SAS Grown Defect List Logarithmic Scaling (High)

**Problem**: The `realloc_penalty` branch for non-SSD drives at line 294–296 uses a linear formula capping at 40 points after 5+ reallocations. SAS grown defect counts operate on a completely different scale (0–16,000+ observed in production) — the current formula is meaningless above single digits.

**Observed scale from live data**:
- 0: clean (majority of drives)
- 1–10: minor wear (e.g., `sdap` at 1, `sde/sdf` at 7)
- 11–100: notable (e.g., `sdb/sdd` at 110 — classified as marginal)
- 101–1000: significant degradation (e.g., `sdq/sdr` at 267)
- 1,000+: severe (candidate for DESTROY)
- 10,000+: dead (`sdm/sdn` at 16,396)

**Required changes**:
- Add a SAS-specific `realloc_penalty` formula in `calculate_drive_health_score` using logarithmic scaling, separate from the SATA linear branch
- Penalty should reach ~40 at count=100, ~70 at count=1000, and ~100 at count=10,000+
- Add `sas_grown_defect_list` as a distinct field in the `get_smart_data` return dict alongside the existing `reallocated_sectors` (which currently aliases it)

---

### Item 3 — Parse and Use Background Scan Status (High)

**Problem**: `scsi_background_scan_log` is available in `smartctl -j` JSON output but is never parsed. The `status` field within it (`"waiting until BMS interval timer expires"`, `"halted due to fatal error"`, `"no scans active"`) is a critical health signal that does not appear anywhere else.

**Required changes**:
- Parse `data["scsi_background_scan_log"]["status"]["string"]` from the JSON
- Add `sas_scan_status` to the `get_smart_data` return dict
- Propagate `sas_scan_status` into the `smart` dict in `_collect_drive_data`
- Treat `"halted due to fatal error"` as a hard FAILED override (feeds into Item 1)
- Surface `sas_scan_status` in the UI drive detail panel as a distinct indicator rather than silently absorbing it into the health score

---

### Item 4 — SAS Non-Medium Error Count (NME) Mis-Threshold (Medium)

**Problem**: `scsi_non_medium_error_count` is assigned to `interface_errors` and a flat `-10` penalty applies if `errs > 50`. This threshold was designed for SATA CRC errors (where `> 50` is genuinely alarming). SAS NME counts bus-layer events (timeouts, aborts, resets) and regularly reaches tens of millions on healthy drives. Two drives in this bundle had 42M and 57M NME with zero media errors — both receive the same `-10` as a drive with 51.

**Required changes**:
- Rename the field from `interface_errors` to a type-appropriate name, or add a separate `sas_non_medium_errors` field
- Change the NME penalty for SAS drives: no penalty below 1,000,000; advisory flag between 1M–100M; health penalty only above 100M (indicating a genuinely abnormal SAS bus condition)
- Add a UI advisory indicator "Possible SAS cable/expander issue" when NME > 1,000,000 rather than silently deducting from health score

---

### Item 5 — Parse SAS Uncorrectable Read/Write/Verify Errors (Critical)

**Problem**: `scsi_error_counter_log` is only read for `gigabytes_processed` (throughput data). The `total_uncorrectable_errors` fields for `read`, `write`, and `verify` rows are ignored. These represent actual data integrity failures.

**Observed in live data**:
- `sdm/sdn` (`Z1Z3MFCJ`, dead drive): `verify: 6 uncorrectable` — invisible to the current system
- `sdap/sdas` (`Z1Z3T8B4`): `read: 4 delayed ECC corrections` — softer warning, also invisible

**Required changes**:
- Parse and return from `get_smart_data`:
  - `sas_uncorrectable_read_errors` from `scsi_error_counter_log.read.total_uncorrectable_errors`
  - `sas_uncorrectable_write_errors` from `scsi_error_counter_log.write.total_uncorrectable_errors`
  - `sas_uncorrectable_verify_errors` from `scsi_error_counter_log.verify.total_uncorrectable_errors`
- In `get_drive_recommendation`:
  - Any `sas_uncorrectable_verify_errors >= 1` → DESTROY
  - Any `sas_uncorrectable_write_errors >= 1` → DESTROY
  - `sas_uncorrectable_read_errors >= 10` → DESTROY; `>= 1` → SCRATCH
- In `calculate_drive_health_score`: subtract significant points for any non-zero uncorrectable count

---

### Item 6 — SAS Dual-Port Drive Deduplication (Medium)

**Problem**: Every SAS HDD with dual-port connectivity appears on two device nodes (e.g., `/dev/sdv` and `/dev/sdw` are the same physical drive `S1Z19X9R`). If the bay mapping or discovery pipeline does not already deduplicate by serial number, the system may display, health-check, and potentially attempt to wipe the same physical drive twice.

**Required changes**:
- Audit `_collect_drive_data` and the bay mapping pipeline for serial-number deduplication
- If two active device nodes resolve to the same serial number, mark one as the primary path and the other as `sas_secondary_path: true`
- The UI should show one entry per physical drive, with a secondary path indicator if dual-port is detected
- The wipe job scheduler must prevent issuing concurrent jobs to two paths that resolve to the same serial

---

### Item 7 — HDD POH Threshold Inappropriate for Server SAS Drives (Low)

**Problem**: `hdd_high_poh_threshold` defaults to `40,000` hours. `get_drive_recommendation` at line 380 issues a hard `SCRATCH` for any drive exceeding this, regardless of media health. Server-grade SAS drives are designed for 5-year continuous operation (~43,800 hours). A drive at 40,001 hours with zero grown defects auto-classifies as SCRATCH while a drive at 39,999 hours with 200 defects may not.

**Required changes**:
- Raise the default `hdd_high_poh_threshold` for SAS drives to `50,000` (or make it interface-specific with a separate `sas_high_poh_threshold`)
- Remove the hard POH cutoff as a standalone SCRATCH trigger; instead, weight POH into the existing `poh_penalty` base score calculation and let the health score thresholds do the classification
- The recommendation text should mention high runtime when POH is notable, even if the final classification is not SCRATCH

---

### Item 8 — Sticky LBA / Recurring Scan Event Detection (Medium)

**Problem**: `scsi_background_scan_log.table` (the list of individual scan events with LBA addresses and error codes) is never parsed. This log contains early-warning signals. In live data, drive `sdaq/sdar` (`Z1Z38FGH`) showed the same LBA (`0x1c3aaa01f`) appearing **25 times across 33,000 hours** — consistently recovered but never permanently reassigned. This is a known precursor to eventual sector loss and is invisible to the current system.

**Required changes**:
- Parse `scsi_background_scan_log.table` entries from the JSON output
- Compute and return:
  - `sas_scan_event_count`: total number of entries in the scan log
  - `sas_scan_unique_lbas`: count of distinct LBAs that have appeared
  - `sas_sticky_lba_detected`: boolean, true if any single LBA appears ≥ 3 times in the log
- In `get_drive_recommendation`: `sas_sticky_lba_detected = true` → SCRATCH (at minimum), even if grown defect count is 0
- In health score: apply a moderate penalty for sticky LBA detection

---

### Empty Template Update

`get_smart_data` at line 86–92 defines the `empty_template` dict. All new SAS-specific fields must be added to it with `None` defaults to maintain a consistent return contract across all interface types:
- `sas_grown_defect_list`
- `sas_scan_status`
- `sas_non_medium_errors`
- `sas_uncorrectable_read_errors`
- `sas_uncorrectable_write_errors`
- `sas_uncorrectable_verify_errors`
- `sas_scan_event_count`
- `sas_scan_unique_lbas`
- `sas_sticky_lba_detected`

---

### Implementation Order

| # | Item | Priority | Dependency |
|---|------|----------|------------|
| 1 | SAS SMART status override | Critical | Items 3, 5 must be parsed first to feed overrides |
| 5 | Parse SAS uncorrectable errors | Critical | None |
| 3 | Parse background scan status | High | None |
| 2 | Grown defect logarithmic scaling | High | Item 1 (uses same field) |
| 4 | NME mis-threshold fix | Medium | None |
| 8 | Sticky LBA detection | Medium | None |
| 6 | Dual-port deduplication | Medium | Audit bay mapping first |
| 7 | POH threshold nuance | Low | None |

---

## SMART UX Feature Enhancements

**Status**: Planned
**Priority**: High
**Context**: Drive Eraser is a decommission workflow tool — drives arrive in batches from retired systems, are wiped, assessed, and leave. Drives may occasionally return for a subsequent wipe cycle but do not reside in the system long-term. All features below are designed for this point-in-time, high-throughput intake context.

---

### Feature A — Health Score Explainer

**What**: An expandable breakdown in the drive detail panel showing exactly how the health score was calculated — which components contributed which penalty. Example: `Base: 85 | POH penalty: -5 | Grown defects (267): -32 | Total: 48 → SCRATCH`.

**Why**: Technicians make irreversible disposition decisions (DESTROY vs wipe) based on this score. A black-box number is not defensible. The explainer is also the evidence if a disposition is later disputed. All the data required already exists in the return value of `calculate_drive_health_score` — it just needs to be exposed rather than discarded.

**Implementation notes**:
- `calculate_drive_health_score` needs to return a penalty breakdown dict alongside the score integer
- The breakdown should be passed through `_collect_drive_data` into the `smart` payload
- UI: collapsible section in the drive detail modal, not shown by default

---

### Feature B — Batch Intake Triage Report

**What**: A single-page view (printable/exportable) showing all currently connected drives ranked by health score, with a summary table: serial, model, manufacture year, POH, grown defects, recommendation, and a flag column for any anomalous signals (halted scan, uncorrectable errors, sticky LBA).

**Why**: During bulk intake — the primary use case for this system — a technician should not need to open 18 individual drive detail modals to sort good from bad. The triage report is the first thing needed at the start of a session: identify the dead drives, identify the marginals, route them, then start wipes on the rest.

**Implementation notes**:
- Populated from the same discovery payload already returned by the bay listing API
- Flag column should highlight: `status = FAILED`, `sas_scan_status = halted`, `sas_sticky_lba_detected`, any uncorrectable error count > 0
- Printable layout (CSS print media query) sufficient; PDF export optional but not required for v1

---

### Feature C — Pre/Post-Wipe SMART Diff

**What**: Capture a SMART snapshot immediately before a wipe job starts and another immediately after it completes. Display a diff in the drive detail panel showing any metrics that changed — grown defects, uncorrectable errors, scan events — between the two snapshots.

**Why**: The wipe process itself, particularly multi-pass overwrite on degraded media, can accelerate sector failures. If a drive enters with 7 grown defects and exits with 12, that is material information for the wipe certificate and for the final disposition decision. This closes an audit gap: the certificate currently proves erasure but says nothing about condition change during erasure.

**Implementation notes**:
- Pre-wipe snapshot: captured at job creation time, stored against the job record in the database
- Post-wipe snapshot: captured at job completion, stored against the same job record
- Diff logic: compare `sas_grown_defect_list`, `sas_uncorrectable_*`, `sas_scan_event_count`, `health_score` between the two
- If any metric worsened during wipe, flag it on the certificate and in the job detail view
- Snapshot storage is a small JSON blob attached to the existing job record — no new table required

---

### Feature D — Intake Snapshot & Prior Visit Lookup

**What**: Two related capabilities built on the same data:

1. **Intake snapshot**: At the moment a drive is assessed (discovery + health score), persist a timestamped record keyed by serial number containing: health score, grown defect count, scan status, uncorrectable error counts, recommendation, POH, and manufacture date.

2. **Prior visit lookup**: When a drive is scanned, query the intake snapshot history for that serial number. If a prior record exists, display it in the drive detail panel alongside the current reading — e.g., "Last seen: 2025-10-14 | Grown defects then: 7 | Now: 267 | Change: +260."

**Why**: The intake snapshot alone is the evidence of condition at intake — distinct from the wipe certificate which proves erasure. The prior visit lookup makes the occasional repeat-visit drive immediately actionable: a jump from 7 to 267 grown defects between visits is a clear DESTROY signal even if the current count alone might only score SCRATCH.

**Implementation notes**:
- These two features share the same underlying storage — write the intake snapshot, read it back for the prior visit comparison
- Storage: a new `drive_intake_history` table in the existing SQLite database, keyed by serial number with a timestamp. Lightweight — one row per intake event per drive.
- Prior visit UI: a collapsible "Previous visit" section in the drive detail modal, only shown if a prior record exists
- Do not attempt to build a full timeline UI for v1 — prior single record comparison is sufficient

---

### Feature E — Per-Drive Raw SMART Export

**What**: A "Download SMART data" button in the drive detail modal that downloads the raw `smartctl -j -x` JSON output for that specific drive, without generating a full support bundle.

**Why**: The support bundle captures raw SMART for all drives and is the right tool for full system diagnostics. But when a technician encounters one anomalous drive mid-batch and needs to escalate or document it, generating a full bundle is heavyweight and captures unrelated data. A per-drive download is faster and more targeted for single-drive escalation or documentation.

**Implementation notes**:
- `get_raw_smart_diagnostics()` already exists in the backend — this is a UI surface and a thin API endpoint wrapping it
- Endpoint: `GET /api/admin/drives/<device>/smart-export` returning the raw JSON as a file download
- Filename: `smartctl-<serial>-<timestamp>.json`
- The raw JSON is already stored in `smart.raw` in the discovery payload — for cached drives this can be served directly without re-running smartctl

---

### Feature F — Model Risk Profile (Static Lookup)

**What**: A built-in read-only lookup table of known drive model/firmware quirks that adjusts how the health parser interprets certain metrics. Example entries:
- `ST4000NM0023 Rev 0003`: trip temperature 40°C (not 60°C), high NME counts normal for age
- `ST4000NM0023 Rev D007`: trip temperature 60°C, NME in tens of millions normal
- `ST4000NM0025 Rev N004`: newer generation, background scan may show 0 scans if recently installed

**Why**: The support bundle showed two hardware revisions of the same model family with completely different normal operating envelopes. The 40°C trip drives were running at 34–37°C in the bundle — near their limit — while the 60°C trip drives at the same temperature are well within spec. Treating them identically produces misleading temperature assessments. A static lookup requires no user maintenance and prevents model-specific false positives/negatives.

**Implementation notes**:
- Stored as a JSON file in `config/` (e.g., `drive_models.json`), not hardcoded
- Keyed by `(vendor, product, revision)` tuple matching the `smartctl` information section fields
- Fields per entry: `trip_temperature`, `nme_normal_range_max`, `notes`
- Parser consults the lookup during `get_smart_data` to adjust thresholds before scoring
- User-editable via the admin UI in a future iteration; read-only file for v1

---

### Dependency & Build Order

Feature D (intake snapshot) should be built first as it is a prerequisite for the prior visit lookup component of itself, and its data model (job-attached snapshots) overlaps with Feature C.

| Feature | Depends On | Effort Estimate |
|---------|-----------|-----------------|
| A — Health score explainer | None — data already exists | Low |
| E — Per-drive SMART export | None — backend already exists | Low |
| B — Batch triage report | None — data already exists | Low–Medium |
| C — Pre/post-wipe diff | Feature A snapshot data model | Medium |
| D — Intake snapshot + prior visit | None (new DB table) | Medium |
| F — Model risk profile | None (new config file) | Medium |

---

## Additional Future Enhancements

This section will be updated as new enhancement requests are identified and prioritized.
