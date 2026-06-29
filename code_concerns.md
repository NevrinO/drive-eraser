# Code Concerns — Consolidated Review Tracker

Running document for findings across the codebase that need a deeper look before fixing. Items are grouped by file and tagged with severity: **[Critical]** or **[Advisory]**. Some critical issues may be listed here when they require investigation or design decisions before implementing a fix.

---

## backend/app.py

### A1: [Advisory] `load_policy()` Called on Every API Request — Difficulty: Medium — Category: Performance
- **Line**: 32 (inside `security_gate` before_request hook)
- **Issue**: `load_policy()` reads `policy.json` from disk, parses JSON, validates against JSON schema, and merges with defaults on every non-localhost API request. No caching layer.
- **Impact**: Unnecessary disk I/O on the hot path. Tolerable for a LAN tool with limited users, but scales poorly.
- **Suggestion**: Add a TTL cache (e.g., `functools.lru_cache` with a TTL wrapper) or a file-mtime-based cache to avoid re-reading unchanged policy.
- **Depends-on**: none
- **Related**: none

### A6: [Advisory] Module-Level Side Effects Make Testing Fragile — Difficulty: Medium — Category: Architecture
- **Lines**: 97, 100, 103, 109, 112, 228
- **Issue**: `init_wipe_db()`, `udev_listener.start_udev_listener()`, `start_smart_test_update_thread()`, and `get_zero_check_manager()` all execute at import time. Any test importing `app.py` triggers database initialization, starts a udev listener (fails on Windows), and spawns a background thread.
- **Impact**: Test suite fragility, especially cross-platform. Documented as necessary for WSGI deployment (line 96).
- **Suggestion**: Consider a `create_app()` factory pattern or guard behind `if __name__ == "__main__"` with a separate WSGI entry point.
- **Depends-on**: none
- **Related**: none

---

## backend/common.py

---

## backend/crypto_verification.py

---

## backend/disk_ops.py

### A21: [Advisory] `get_discovery_max_workers` / `get_background_smart_max_workers` Load Policy on Every Call — Difficulty: Medium — Category: Performance
- **Line**: 128-147
- **Issue**: Both functions call `load_policy(get_config_dir())` on every invocation, reading and parsing `policy.json` from disk.
- **Impact**: Low — 1-2 extra disk reads per discovery batch. Acceptable for a LAN tool.
- **Suggestion**: Consider caching with short TTL or file-mtime check if discovery frequency increases.
- **Depends-on**: none
- **Related**: none

### A70: [Advisory] File Size 1148 Lines Exceeds 800-Line Threshold — Difficulty: High — Category: Organization
- **Lines**: 1-1148
- **Issue**: File is 1148 lines, exceeding the 800-line threshold. Contains OS drive detection, discovery engine (enclosure + legacy schemas), parallel drive data collection, background extended SMART pool management, zero-check eligibility, per-device caching, device resolution from hardware identifiers, and dual-port deduplication — all in one file.
- **Impact**: Medium — difficult to navigate and maintain. AI agents with ~200k context windows must read the full file plus all context (callers, lessons-learned, security deviations) within a single generation.
- **Suggestion**: Split into topic-specific modules:
  - `os_detection.py` — `get_os_parent_device`, `get_os_by_path`, `_get_os_by_path_cached`, OS cache variables/locks
  - `device_resolution.py` — `_resolve_device_from_enclosure_slot`, `_resolve_device_from_hardware_identifier`
  - `drive_collection.py` — `_collect_drive_data`, `_collect_pending_parallel`, `_collect_pending_serial`, `_apply_drive_payload`, `_apply_collection_failure`, `_store_drive_payload`, `_get_cached_drive_payload`, cache variables/locks
  - `extended_smart.py` — `_get_extended_smart_executor`, `stop_extended_smart_pool`, `_process_single_drive_extended_smart`, `_submit_drive_for_extended_smart`, extended SMART globals
  - `discovery.py` — `discover_drives`, `_discover_drives_enclosure`, `_discover_drives_legacy`, `_audit_dual_port_deduplication`, `_auto_enqueue_zero_checks`, signal handling, `invalidate_drive_cache`
  - `disk_ops.py` — re-exports public API (`discover_drives`, `invalidate_drive_cache`, `set_websocket_manager`, `stop_extended_smart_pool`) for backward compatibility
- **Depends-on**: none
- **Related**: A52 (same issue in `styles.css`), A64 (same issue in `device_discovery.py`)

---


## backend/job_management.py

---

## backend/udev_listener.py

### A31: [Advisory] `bay_map.json` reloaded on every udev event — Difficulty: Medium — Category: Performance
- **Lines**: 212-216
- **Issue**: `json.load(f)` called for every udev hot-plug event. Unnecessary disk I/O for frequent device changes.
- **Impact**: Low — performance concern only on systems with rapid hot-plug cycles.
- **Suggestion**: Cache with file mtime check or use inotify-based file watcher.
- **Depends-on**: none
- **Related**: none

---

## backend/zero_check_manager.py

---

## backend/routes/admin_routes.py

---

## backend/routes/drive_routes.py

---

## frontend/styles.css

### A52: [Advisory] File size 1732 lines exceeds 800-line threshold — Difficulty: High — Category: Organization
- **Lines**: 1-1732
- **Issue**: File is 1732 lines, more than double the 800-line threshold. Contains all CSS for the entire application: base/reset, layout, bay cards, buttons, audit ledger, modals, admin panel, auth overlay, utilities, enclosure management, triage table, print styles.
- **Impact**: Medium — difficult to navigate and maintain. AI agents with ~200k context windows may struggle to read the file plus all necessary context (HTML, JS callers, lessons-learned).
- **Suggestion**: Split into topic-specific files: `base.css` (variables, reset, typography), `layout.css` (app, header, tabs, grids), `bay-card.css` (bay card states, banners, badges, health), `buttons.css` (btn system, animations), `audit.css` (audit rows, status badges), `modal.css` (modal system, forms), `admin.css` (admin grid, metrics, mapping), `auth.css` (auth overlay), `utilities.css` (utility classes), `triage.css` (triage table, print). Import via `<link>` tags or CSS `@import`.
- **Depends-on**: none
- **Related**: none

---

## backend/device_discovery.py

### A64: [Advisory] File size 1562 lines exceeds 800-line threshold — Difficulty: High — Category: Organization
- **Lines**: 1-1562
- **Issue**: File is 1562 lines, nearly double the 800-line threshold. Contains PCI scanning, NVMe info, enclosure hardware info, SAS expander detection, master slot map generation, SCSI host slot projections, multipath resolution, and 8 separate cache invalidation/management sections — all in one file.
- **Impact**: Medium — difficult to navigate and maintain. AI agents with ~200k context windows must read the full file plus all context (callers, lessons-learned, security deviations) within a single generation.
- **Suggestion**: Split into topic-specific modules:
  - `pci_controllers.py` — `scan_pci_controllers`, `_map_pci_class_to_type`, `get_controller_for_device`, `validate_pci_address`
  - `enclosure_discovery.py` — `get_enclosure_hardware_info`, `get_max_slot_from_enclosure`, `is_enclosure_device`
  - `sas_expander.py` — `detect_sas_expander`, `get_parent_pci`
  - `slot_mapping.py` — `generate_master_slot_map`, `get_scsi_host_slot_projections`, `resolve_multipath_parent`
  - `device_discovery.py` — `validate_device_path`, `discover_controllers_and_devices`, cache management functions
  - Move cache variables and locks to their respective modules.
- **Depends-on**: none
- **Related**: A52 (same issue in `styles.css`)

---

## backend/smart_parsing.py

### A78: [Advisory] File size 1227 lines exceeds 800-line threshold — Difficulty: High — Category: Organization
- **Lines**: 1-1227
- **Issue**: File is 1227 lines, exceeding the 800-line threshold. Contains SMART data parsing, interface detection, health scoring, drive recommendation, SMART test execution, SMART test status polling with rollover correction, pre-wipe health gate, and device path validation — 8 distinct responsibilities in one file.
- **Impact**: Medium — difficult to navigate and maintain. AI agents with ~200k context windows must read the full file plus all context (callers, lessons-learned, security deviations) within a single generation.
- **Suggestion**: Split into topic-specific modules:
  - `smart_data_parsing.py` — `get_smart_data`, `get_smart_identity`, `get_triage_thresholds`, SATA/NVMe/SAS attribute parsing
  - `smart_health.py` — `calculate_drive_health_score`, `get_drive_recommendation`, `is_drive_ssd`
  - `smart_test_runner.py` — `run_smart_test`, `get_smart_test_status`, self-test log rollover correction
  - `smart_health_gate.py` — `pre_wipe_health_gate`
  - `smart_utils.py` — `detect_interface_type`, `classify_interface_from_smart`, `validate_device_path`, `get_raw_smart_diagnostics`
- **Depends-on**: none
- **Related**: A52, A64 (same issue in other files)

---

## frontend/admin/enclosureManagement.js

### A90: [Advisory] File size 1094 lines exceeds 800-line threshold — Difficulty: High — Category: Organization
- **Lines**: 1-1094
- **Issue**: File is 1094 lines, exceeding the 800-line threshold. Contains enclosure CRUD, wizard state management, configuration rendering (controller/template selection with hardware info), slot assignment rendering with traversal logic, HW identifier validation, and save/edit handlers — 6+ distinct responsibilities in one file.
- **Impact**: Medium — difficult to navigate and maintain. AI agents with ~200k context windows must read the full file plus all context within a single generation.
- **Suggestion**: Split into topic-specific modules:
  - `enclosureList.js` — `loadEnclosures`, `renderEnclosureList`, `deleteEnclosure`, `editEnclosure`, `initializeEnclosureManagement`, `attachEnclosureManagementListeners`
  - `enclosureWizard.js` — Wizard state, `openNewEnclosureWizard`, `renderWizardStep`, `renderConfiguration`, `renderSlotAssignment`, `buildTraversalPositions`, `SUPPORTED_TRAVERSALS`
  - `enclosureSave.js` — `handleSaveEnclosure`, `handleEditEnclosure`, shared `collectSlotMappingsFromDOM` helper
- **Depends-on**: none
- **Related**: A70, A78 (same issue in other files)
