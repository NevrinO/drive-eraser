# Admin Config Review — Implementation Plan

## Background

Review of the admin configuration UI (triage thresholds and system settings) identified validation mismatches, missing UI controls, dead config keys, and keys consumed by backend but not exposed via API/UI.

## Item 5 Answer — Reallocated Sectors

No, an HDD cannot have >1000 reallocated sectors without severe degradation. Most healthy drives have 0. Even 50–100 is concerning. 1000 indicates extensive surface damage — the drive is failing. The current max of 1000 in the schema is already very generous as a threshold upper bound. No change needed.

---

## Phase 1 — Fix backend validation mismatches (`backend/routes/policy_routes.py`)

Single `multi_edit` on the `numeric_policy_fields` dict at line 61-72:

| Field | Current | New |
|---|---|---|
| `max_concurrent_wipes` | (int, 1, 32) | (int, 1, 256) |
| `discovery_max_workers` | (int, 1, 64) | (int, 1, 32) |
| `blockdev_post_wipe_retry_delay` | (int, 0, 300) | (int, 0, 60) |
| `prewipe_health_gate_max_pending_sectors` | (int, 0, 100000) | (int, 0, 1000) |
| `prewipe_health_gate_max_reallocated_sectors` | (int, 0, 100000) | (int, 0, 1000) |
| `background_smart_max_workers` | (int, 1, 32) | (int, 1, 32) — already correct per spec |

## Phase 2 — Update defaults & schema (`backend/common.py`)

**DEFAULT_POLICY changes** (line 52-83):
- `max_concurrent_wipes`: 64 → **34**
- `discovery_max_workers`: 8 → **16**
- `background_smart_max_workers`: 4 → **8**
- `discovery_diag`: True → **False**
- **Remove** `crypto_fail_retry_block`
- **Remove** `health_soft_stop`
- **Remove** `certificate_retention_days`

**POLICY_SCHEMA changes** (line 86-159):
- `background_smart_max_workers`: maximum 8 → **32**
- **Remove** `crypto_fail_retry_block` property
- **Remove** `health_soft_stop` property
- **Remove** `certificate_retention_days` property

## Phase 3 — Update extended_smart.py clamp (`backend/extended_smart.py`)

Line 41: `return max(1, min(workers, 8))` → `return max(1, min(workers, 32))`

## Phase 4 — Add API support for 3 new writable keys (`backend/routes/policy_routes.py`)

**Add to `boolean_policy_fields`** (line 75-80):
- `discovery_diag`

**Add to `numeric_policy_fields`** (line 61-72):
- `max_logo_size_mb`: (float, 0.1, 50)
- `max_bulk_cert_batch_size`: (int, 1, 1000)

## Phase 5 — Add 7 new UI controls (`frontend/index.html`)

**Notifications section** (after Slack Webhook URL, ~line 1017):
- `station_id` — text input, max 100 chars, label "Station ID"

**Performance Settings section** (after Max Concurrent Wipes, ~line 1044):
- `background_smart_max_workers` — number input, min 1, max 32, step 1, label "Background SMART Max Workers"

**Audit Mode section** (after Wipe Passphrase, ~line 1076):
- `post_erase_marker` — select toggle, label "Post-Erase Marker"
- `allow_method_override` — select toggle, label "Allow Method Override"

**New "Certificate Settings" section** (after Audit Mode, before Health Gate):
- `max_logo_size_mb` — number input, min 0.1, max 50, step 0.1, label "Max Logo Size (MB)"
- `max_bulk_cert_batch_size` — number input, min 1, max 1000, step 1, label "Max Bulk Certificate Batch Size"

**New "Diagnostics" section** (after Zero Detection, before submit button):
- `discovery_diag` — select toggle, label "Discovery Diagnostics", default Disabled

## Phase 6 — Add load/validate logic (`frontend/admin/systemConfig.js`)

**In `loadSystemConfig()`** (after existing fields):
- Populate `station_id` from `policy.station_id`
- Populate `post_erase_marker` from `policy.post_erase_marker`
- Populate `allow_method_override` from `policy.allow_method_override`
- Populate `background_smart_max_workers` from `policy.background_smart_max_workers`
- Populate `discovery_diag` from `policy.discovery_diag`
- Populate `max_logo_size_mb` from `policy.max_logo_size_mb`
- Populate `max_bulk_cert_batch_size` from `policy.max_bulk_cert_batch_size`

**In `validateForm()`** (after existing fields):
- `station_id`: string, trim, max 100 chars
- `post_erase_marker`: boolean from select
- `allow_method_override`: boolean from select
- `background_smart_max_workers`: parseInt, 1–32
- `discovery_diag`: boolean from select
- `max_logo_size_mb`: parseFloat, 0.1–50
- `max_bulk_cert_batch_size`: parseInt, 1–1000

**Update existing field defaults in `loadSystemConfig()`**:
- `discovery_max_workers` fallback: 8 → **16**
- `max_concurrent_wipes` fallback: 64 → **34**

## Phase 7 — Update `config/policy.json`

- **Remove**: `crypto_fail_retry_block`, `health_soft_stop`, `certificate_retention_days`
- **Add**: `discovery_diag: false`, `background_smart_max_workers: 8`, `discovery_max_workers: 16`, `max_concurrent_wipes: 34`
- Existing keys for `max_logo_size_mb` (1) and `max_bulk_cert_batch_size` (100) already present — keep

## Phase 8 — Update `scripts/install.sh`

In the Python policy generation block (lines 399-443):
- **Remove**: `'crypto_fail_retry_block': True,`
- **Remove**: `'health_soft_stop': True,`
- **Change**: `'discovery_diag': True,` → `'discovery_diag': False,`
- **Add**: `'discovery_max_workers': 16,`
- **Add**: `'background_smart_max_workers': 8,`
- **Add**: `'max_concurrent_wipes': 34,`
- **Add**: `'max_logo_size_mb': 1,`
- **Add**: `'max_bulk_cert_batch_size': 100,`

## Phase 9 — Update tests

**`tests/test_disk_ops.py`** (lines 677-691):
- `test_get_background_smart_max_workers_default`: expected 4 → **8**
- `test_get_background_smart_max_workers_clamped`: input 50 → expected 8 → **32**; input 0 → expected 1 (unchanged)

**`tests/test_common_extended.py`**:
- Any tests referencing `crypto_fail_retry_block`, `health_soft_stop`, or `certificate_retention_days` in DEFAULT_POLICY need those references removed
- Any schema validation tests that include these keys need updating

**`tests/test_admin_routes.py`**:
- The `background_smart_max_workers` test (line 227) uses value 6 — still valid (within 1-32)
- Add test for `discovery_diag` POST
- Add test for `max_logo_size_mb` POST
- Add test for `max_bulk_cert_batch_size` POST

## Phase 10 — Verify

Run full test suite on the Ubuntu server via SSH:
```
cd /opt/drive-eraser && sudo /opt/drive-eraser/venv/bin/python -m pytest tests/ -x -q
```

---

## Files Touched (9 total)

| File | Phases |
|---|---|
| `backend/routes/policy_routes.py` | 1, 4 |
| `backend/common.py` | 2 |
| `backend/extended_smart.py` | 3 |
| `frontend/index.html` | 5 |
| `frontend/admin/systemConfig.js` | 6 |
| `config/policy.json` | 7 |
| `scripts/install.sh` | 8 |
| `tests/test_disk_ops.py` | 9 |
| `tests/test_common_extended.py` | 9 |
| `tests/test_admin_routes.py` | 9 (optional) |
