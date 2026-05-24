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

---

## Protocol-Specific Matrix

| Protocol | Detection Expectation | Expected Preferred Methods |
|---|---|---|
| NVMe | `interface_type=nvme` | `crypto`, `block`, `overwrite` |
| SATA | `interface_type=sata` | `enhanced_secure_erase`, `secure_erase`, `overwrite` |
| SAS | `interface_type=sas` | `block`, `overwrite` (conservative) |