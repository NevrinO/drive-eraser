# AI Context Map: Drive Sanitization Station

**Purpose**: This document provides a high-level architectural index and dependency map of the Drive Sanitization Station. Use this file to identify which module to inspect or modify without loading the entire program codebase. It answers "where" things are and "how" they connect.

**Relation to other docs**:
- `docs/ARCHITECTURE.md` - Architectural decisions and design rationale (answers "why" things are this way)
- `README.md` - Quickstart and installation instructions
- `docs/api-contract.md` - API endpoint specifications
- `docs/SOP_technician_guide.md` - Operational procedures for technicians
- `docs/admin-guide.md` - System Administration features guide
- `docs/enclosure-mapping-guide.md` - Enclosure setup and slot configuration guide
- `docs/deployment.md` - Installation, releases, validation, and rollback
- `docs/operations.md` - Service operations and troubleshooting
- `docs/lifecycle.md` - Erase job lifecycle states and transitions

---

## 1. Directory & File Index

All core Python logic resides in the modular `/backend` directory. Frontend files reside in `/frontend`. Automated administration scripts are mapped in `/scripts`.

```text
./
├── backend/                    # Core Python application logic (modular)
│   ├── app.py                  # Application entry point, create_app() factory
│   ├── app_config.py           # Flask app initialization, logging, security middleware
│   ├── wsgi.py                 # WSGI entry point for Gunicorn deployment
│   ├── api_routes.py           # Non-blueprint routes (erase, auth, static serving)
│   ├── system_metrics.py       # System monitoring (RAM, CPU, uptime)
│   ├── job_management.py       # Erase job lifecycle, health gate, progress polling
│   ├── job_validation.py       # Bay validation, method override checks
│   ├── erase_commands.py       # Erase command builders (nvme, hdparm, sg, dd)
│   ├── disk_utils.py           # Command resolution, disk utilities, marker operations
│   ├── disk_capabilities.py    # Drive capability detection (SATA/NVMe/SAS)
│   ├── disk_ops.py             # Re-export shim → os_detection, discovery, device_resolution, etc.
│   ├── os_detection.py         # OS drive detection (get_os_parent_device, get_os_by_path)
│   ├── discovery.py            # Drive discovery engine (discover_drives, enclosure/legacy modes)
│   ├── discovery_state.py      # Discovery interrupt generation, thread state, shutdown events
│   ├── discovery_diag.py       # Discovery diagnostics and troubleshooting
│   ├── device_discovery.py     # Low-level device discovery helpers
│   ├── device_resolution.py    # Enclosure slot → device path resolution
│   ├── drive_collection.py     # Drive payload building, caching, extended SMART collection
│   ├── extended_smart.py       # Background SMART pool executor, WebSocket updates
│   ├── enclosure_discovery.py  # Hardware enclosure scanning (SAS expanders, PCIe NVMe)
│   ├── pci_controllers.py      # PCI controller enumeration and mapping
│   ├── sas_expander.py         # SAS expander discovery and PHY scanning
│   ├── slot_mapping.py         # Physical slot mapping, master slot map generation
│   ├── udev_listener.py        # udev event listener for hot-plug detection
│   ├── smart_parsing.py        # Re-export shim → smart_utils, smart_data_parsing, smart_health, etc.
│   ├── smart_utils.py          # Interface classification, SSD detection, raw SMART diagnostics
│   ├── smart_constants.py      # SMART attribute ID constants, threshold defaults
│   ├── smart_data_parsing.py   # SMART data extraction, triage thresholds, drive model loading
│   ├── smart_health.py         # Health score calculation, drive recommendations
│   ├── smart_health_gate.py    # Pre-wipe health gate evaluation
│   ├── smart_test_runner.py    # SMART self-test execution and status tracking
│   ├── smart_db.py             # SMART test result persistence (SQLite)
│   ├── certificates.py         # Render engine & HMAC-SHA256 signature generator
│   ├── bulk_cert.py            # Bulk certificate generation
│   ├── layout_templates.py     # Certificate layout template engine
│   ├── common.py               # Shared path resolvers, JSON policy loader, config helpers
│   ├── crypto_verification.py  # Sampled zero check, hash comparison, crypto probe
│   ├── zero_check_manager.py   # Zero-check job lifecycle (start, cancel, progress)
│   ├── database.py             # Schema initialization, PRAGMA alterations, SQLite writes
│   ├── notifier.py             # Webhook alerting dispatcher
│   ├── verification.py         # Firmware sanitize status checkers & marker logic
│   │                           # See "NVMe Sanitize Log Reference" section below for SSTAT/SPROG values
│   └── routes/                 # Flask blueprints organized by feature area
│       ├── __init__.py         # Blueprint registration (register_blueprints)
│       ├── _shared.py          # Shared route utilities, validation helpers
│       ├── admin_routes.py     # Kill-all-jobs endpoint
│       ├── drive_routes.py     # /api/drives, /api/status, zero-check endpoints
│       ├── certificate_routes.py # Certificate retrieval, bulk HTML, bulk cert creation
│       ├── bay_mapping_routes.py # Bay map CRUD, unmapped drives, auto-detect
│       ├── discovery_routes.py # Slot discovery, slot mapping apply
│       ├── enclosure_routes.py # Enclosure CRUD, slot CRUD, templates, master slot map
│       ├── policy_routes.py    # Policy GET/POST, triage config GET/POST
│       ├── smart_routes.py     # SMART export, details, self-test, drive models
│       ├── support_routes.py   # Metrics, webhook test, CSV export, support bundle, logo
│       └── template_routes.py  # Layout template CRUD, import/export, apply
├── config/                     # Static operational profiles
│   ├── bay_map.json            # Bay configuration (roles, labels, by-path, display numbers)
│   ├── policy.json             # System rules, method priority, passphrases, triage thresholds
│   └── drive_models.json       # Per-model risk profiles (vendor, trip temp, NME thresholds)
├── data/                       # Persistent runtime assets (gitignored except .gitkeep)
│   ├── wipes.db                # SQLite database (jobs, results, certificates, SMART tests)
│   ├── certs/                  # Generated JSON and HTML certificates
│   └── logs/                   # Application logs (active/, failed/)
├── docs/                       # Technical documentation and guides
│   ├── admin-guide.md          # System Administration features guide
│   ├── api-contract.md         # Endpoint input/output shapes
│   ├── ARCHITECTURE.md         # Architectural decisions and design rationale
│   ├── change-log.md           # Engineering development timeline
│   ├── CODE_MAP.md             # This file — high-level architectural index
│   ├── deployment.md           # Installation, GitHub releases, validation, rollback
│   ├── enclosure-mapping-guide.md # Enclosure setup, slot config, worked examples
│   ├── lifecycle.md            # Erase job lifecycle states and transitions
│   ├── operations.md           # Service ops, config paths, troubleshooting by symptom
│   ├── roadmap.md              # Feature roadmap and status
│   ├── SECURITY_DEVIATIONS.md  # Documented security deviations
│   ├── SOP_technician_guide.md # Step-by-step physical drive handling guidelines
│   └── test-plan.md            # Test coverage plan
├── frontend/                   # UI Assets (modular)
│   ├── app.js                  # Frontend entry point, tab switching, initialization
│   ├── index.html              # UI template (single-page app)
│   ├── styles.css              # CSS entry point (imports all split CSS files)
│   ├── utils.js                # Utility functions (escapeHtml, formatting, clipboard)
│   ├── auth.js                 # Authentication overlay and passphrase verification
│   ├── driveManagement.js      # Drive discovery, bay selection, batch operations
│   ├── driveRendering.js       # Bay card rendering, status display, health indicators
│   ├── smartRenderers.js       # SMART data rendering, health badges, attribute tables
│   ├── smartDeepDive.js        # SMART deep-dive modal, raw attribute viewer
│   ├── auditLedger.js          # Audit history display, certificate management
│   ├── batchWipe.js            # Batch wipe UI, multi-bay selection, method assignment
│   ├── modals.js               # Modal framework, help modal, confirmation dialogs
│   ├── triageReport.js         # Batch intake triage report rendering
│   ├── socket.io.min.js        # Socket.IO client library (WebSocket real-time updates)
│   ├── favicon.ico             # Application favicon
│   ├── css/                    # Split CSS modules
│   │   ├── base.css            # Reset, variables, root element styles
│   │   ├── layout.css          # Grid layout, header, tab navigation
│   │   ├── bay-card.css        # Bay card visual states, health indicators
│   │   ├── buttons.css         # Button styles and variants
│   │   ├── audit.css           # Audit ledger table styles
│   │   ├── modal.css           # Modal overlay and dialog styles
│   │   ├── admin.css           # Admin panel layout and forms
│   │   ├── auth.css            # Authentication overlay styles
│   │   ├── enclosure.css       # Enclosure management UI styles
│   │   ├── utilities.css       # Utility classes, helpers, responsive
│   │   ├── triage.css          # Triage report and threshold styles
│   │   ├── legend.css          # Color legend, status indicators
│   │   ├── discovery.css       # Discovery modal and mapping UI styles
│   │   ├── certificate.css     # Certificate print layout styles
│   │   └── print-window.css    # Print window styles
│   └── admin/                  # Admin panel modules
│       ├── adminUtilities.js   # Shared admin utilities and helpers
│       ├── bayMapping.js       # Bay mapping configuration and management
│       ├── discoveryModal.js   # Discovery modal UI and event handlers
│       ├── discoveryMapping.js # Pattern/manual mapping business logic
│       ├── discoveryValidation.js # Validation functions (regex, device paths, mapping)
│       ├── discoveryState.js   # State management and undo functionality
│       ├── templateManagement.js # Certificate template management
│       ├── logoManagement.js   # Custom logo upload and management
│       ├── triageConfig.js     # Triage threshold configuration
│       ├── driveModels.js      # Drive model risk profile management
│       ├── enclosureList.js    # Enclosure list view and CRUD UI
│       ├── enclosureSave.js    # Enclosure save/form handling
│       ├── enclosureWizard.js  # Enclosure setup wizard, slot editor
│       └── systemConfig.js     # System config panel (policy, station ID, toggles)
├── scripts/                    # Automation and lifecycle shell wrappers
│   ├── install.sh              # Host setup and package requirements script
│   ├── update.sh               # Production deployment script (copies to /opt, restarts service)
│   ├── start.sh                # Local manual daemon run wrapper
│   ├── seed_test_data.sh       # Mock population helper for offline staging
│   ├── export-logs.sh          # Log export utility
│   ├── kill-all-jobs.sh        # Force-kill all running wipe processes
│   ├── tests-install.sh        # Install test dependencies
│   ├── tests-run.sh            # Run test suite
│   └── build-release.ps1       # Windows PowerShell release build script
├── systemd/                    # Service manager configuration
│   └── drive-eraser.service    # Systemd unit definition for background execution
├── triage/                     # Code concern triage utilities
│   └── triage_concerns.py      # Parse and group code concerns
├── tests/                      # Test suite (pytest + JS)
│   ├── conftest.py             # Pytest fixtures and configuration
│   ├── fixtures/               # Test fixtures (SMART samples, mock data)
│   ├── test_*.py               # 30+ Python test modules
│   └── test_*.js               # 4 JavaScript test modules
├── .gitignore                  # Environment-specific exclude profiles
├── .productionignore           # Production deployment exclude patterns
├── .windsurfignore             # Windsurf IDE exclude patterns
├── requirements.txt            # Python dependencies
├── requirements-test.txt       # Python test dependencies
├── LICENSE                     # MIT license
├── progress.txt                # Development progress notes
└── README.md                   # Quickstart installation instructions
```

---

## 2. Module Responsibilities Matrix

| If you want to modify... | Look in this file | Key Functions / Definitions to Inspect |
| :--- | :--- | :--- |
| **Application Entry Point** | `backend/app.py` | `create_app()` — Flask app + Socket.IO initialization |
| **WSGI Entry Point** | `backend/wsgi.py` | `app`, `socketio` — Gunicorn deployment entry point |
| **Flask App Configuration** | `backend/app_config.py` | `app`, `logger`, `get_config_dir()`, `load_policy()`, `limiter`, `calculate_session_token()` |
| **System Monitoring** | `backend/system_metrics.py` | `get_ram_usage()`, `get_cpu_usage()`, `get_system_uptime()` |
| **Job Lifecycle Management** | `backend/job_management.py` | `validate_single_bay()`, `create_erase_job()`, `run_erase_job()`, `prepare_erase_command()`, `check_health_gate_sync()` |
| **Job Validation** | `backend/job_validation.py` | Bay validation logic, method override checks |
| **Erase Command Builders** | `backend/erase_commands.py` | Command builders for nvme sanitize, hdparm, sg, dd |
| **Non-Blueprint Routes** | `backend/api_routes.py` | `register_routes()` — erase start/cancel, job status, history, auth, static serving |
| **Route Blueprints** | `backend/routes/__init__.py` | `register_blueprints()` — registers all 10 blueprints |
| **Shared Route Utilities** | `backend/routes/_shared.py` | Shared validation helpers used across route modules |
| **Drive Routes** | `backend/routes/drive_routes.py` | `/api/drives`, `/api/status`, zero-check start/cancel |
| **Admin Routes** | `backend/routes/admin_routes.py` | `/api/admin/jobs/kill-all` |
| **Certificate Routes** | `backend/routes/certificate_routes.py` | `/api/certificates/<id>`, bulk HTML, bulk cert creation |
| **Bay Mapping Routes** | `backend/routes/bay_mapping_routes.py` | Bay map CRUD, unmapped drives, auto-detect |
| **Discovery Routes** | `backend/routes/discovery_routes.py` | Slot discovery, slot mapping apply |
| **Enclosure Routes** | `backend/routes/enclosure_routes.py` | Enclosure CRUD, slot CRUD, templates, master slot map |
| **Policy Routes** | `backend/routes/policy_routes.py` | Policy GET/POST, triage config GET/POST |
| **SMART Routes** | `backend/routes/smart_routes.py` | SMART export, details, self-test, drive models |
| **Support Routes** | `backend/routes/support_routes.py` | Metrics, webhook test, CSV export, support bundle, logo |
| **Template Routes** | `backend/routes/template_routes.py` | Layout template CRUD, import/export, apply |
| **Command Resolution & Utilities** | `backend/disk_utils.py` | `resolve_command_path()`, `run_command()`, `execute_erase_method()`, `read_marker_status()` |
| **SMART Data Parsing (shim)** | `backend/smart_parsing.py` | Re-exports from `smart_utils`, `smart_data_parsing`, `smart_health`, `smart_test_runner`, `smart_health_gate` |
| **SMART Utils** | `backend/smart_utils.py` | `classify_interface_from_smart()`, `detect_interface_type()`, `is_drive_ssd()`, `get_raw_smart_diagnostics()` |
| **SMART Constants** | `backend/smart_constants.py` | SMART attribute ID constants, threshold defaults |
| **SMART Data Parsing** | `backend/smart_data_parsing.py` | `get_smart_data()`, `get_smart_identity()`, `get_triage_thresholds()`, `_load_drive_models()` |
| **SMART Health** | `backend/smart_health.py` | `calculate_drive_health_score()`, `get_drive_recommendation()` |
| **SMART Health Gate** | `backend/smart_health_gate.py` | `pre_wipe_health_gate()` — pre-wipe SMART/I/O error check |
| **SMART Test Runner** | `backend/smart_test_runner.py` | `run_smart_test()`, `get_smart_test_status()` |
| **SMART DB** | `backend/smart_db.py` | SMART test result persistence in SQLite |
| **Drive Capability Detection** | `backend/disk_capabilities.py` | `detect_drive_capabilities()`, `detect_sata_capabilities()`, `detect_nvme_capabilities()`, `detect_sas_capabilities()` |
| **Disk Ops (shim)** | `backend/disk_ops.py` | Re-exports from `os_detection`, `discovery`, `device_resolution`, `drive_collection`, `extended_smart`, `discovery_state` |
| **OS Drive Detection** | `backend/os_detection.py` | `get_os_parent_device()`, `get_os_by_path()` |
| **Drive Discovery Engine** | `backend/discovery.py` | `discover_drives()`, `invalidate_drive_cache()`, `get_discovery_max_workers()` |
| **Discovery State** | `backend/discovery_state.py` | Discovery interrupt generation, thread state, shutdown events |
| **Discovery Diagnostics** | `backend/discovery_diag.py` | Discovery troubleshooting and diagnostics |
| **Device Discovery** | `backend/device_discovery.py` | Low-level device discovery helpers |
| **Device Resolution** | `backend/device_resolution.py` | Enclosure slot → device path resolution |
| **Drive Collection** | `backend/drive_collection.py` | Drive payload building, caching, extended SMART collection |
| **Extended SMART Pool** | `backend/extended_smart.py` | Background SMART executor, WebSocket updates, `set_websocket_manager()` |
| **Enclosure Discovery** | `backend/enclosure_discovery.py` | Hardware enclosure scanning (SAS expanders, PCIe NVMe) |
| **PCI Controllers** | `backend/pci_controllers.py` | PCI controller enumeration and mapping |
| **SAS Expander** | `backend/sas_expander.py` | SAS expander discovery and PHY scanning |
| **Slot Mapping** | `backend/slot_mapping.py` | Physical slot mapping, `generate_master_slot_map()` |
| **udev Listener** | `backend/udev_listener.py` | udev event listener for hot-plug detection |
| **CLI Progress Telemetry (Pollers)** | `backend/job_management.py` | `poll_nvme_sanitize_progress()`, `poll_sata_sanitize_progress()`, `poll_sas_sanitize_progress()` |
| **Common Directory Paths** | `backend/common.py` | `get_data_dir()`, `get_db_path()`, `get_cert_dir()`, `get_config_dir()` |
| **Policy JSON Loader** | `backend/common.py` | `load_policy()`, `save_policy()` |
| **SQLite Schema & DB Writes** | `backend/database.py` | `init_wipe_db()`, `persist_job()`, `ensure_column()` |
| **Direct Command Verification** | `backend/verification.py` | `verify_overwrite()`, `verify_nvme_sanitize()`, `verify_sata_sanitize()`, `verify_sas_block()`, `verify_sata_secure_erase()` |
| **Command Verification Orchestrator**| `backend/verification.py` | `verification_for_method()`, `run_verification_command()` |
| **Post-wipe Disk Markers** | `backend/verification.py` | `write_marker_and_verify()`, `build_marker_payload()` |
| **Sampled Zero / Hash Comparison** | `backend/crypto_verification.py` | `verify_sampled_zero_check()`, `verify_crypto_hash_comparison()`, `verify_crypto_probe()` |
| **Zero-Check Manager** | `backend/zero_check_manager.py` | Zero-check job lifecycle (start, cancel, progress tracking) |
| **Cryptographic Certificates** | `backend/certificates.py` | `build_certificate()`, `build_certificate_html()`, `calculate_certificate_hash()` |
| **Bulk Certificates** | `backend/bulk_cert.py` | Bulk certificate generation logic |
| **Layout Templates** | `backend/layout_templates.py` | Certificate layout template engine |
| **Slack Webhooks / Chat Alerts** | `backend/notifier.py` | `send_slack_notification()` |
| **Frontend Entry Point** | `frontend/app.js` | Tab switching, initialization, Socket.IO setup |
| **Frontend Utilities** | `frontend/utils.js` | `escapeHtml()`, `formatIsoDate()`, `calculateDriveHealthScore()`, `copyTextToClipboard()`, `classifyError()`, `handleError()` |
| **Authentication** | `frontend/auth.js` | `showAuthOverlay()`, `hideAuthOverlay()`, `loadSecurityStatus()` |
| **Drive Management** | `frontend/driveManagement.js` | `loadDrives()`, `renderBays()`, `pollActiveWipes()`, `toggleBaySelection()` |
| **Drive Rendering** | `frontend/driveRendering.js` | Bay card rendering, status display, health indicators |
| **SMART Renderers** | `frontend/smartRenderers.js` | SMART data rendering, health badges, attribute tables |
| **SMART Deep-Dive** | `frontend/smartDeepDive.js` | SMART deep-dive modal, raw attribute viewer |
| **Audit Ledger** | `frontend/auditLedger.js` | `loadHistoryIndex()`, `renderAuditLedger()`, `renderExpandedAuditRow()` |
| **Batch Wipe** | `frontend/batchWipe.js` | Batch wipe UI, multi-bay selection, method assignment |
| **Modals** | `frontend/modals.js` | Modal framework, help modal, confirmation dialogs |
| **Triage Report** | `frontend/triageReport.js` | Batch intake triage report rendering |
| **Admin Utilities** | `frontend/admin/adminUtilities.js` | Shared admin helpers, modal management, common admin functions |
| **Bay Mapping** | `frontend/admin/bayMapping.js` | `loadBayMappingConfig()`, `saveBayMappingConfiguration()`, `renderBayMappingUI()`, enclosure management, slot mapping editor |
| **Discovery Modal UI** | `frontend/admin/discoveryModal.js` | `openDiscoveryModal()`, `renderControllers()`, `renderDevices()` |
| **Discovery Mapping** | `frontend/admin/discoveryMapping.js` | `applyPatternMapping()`, `applyManualMapping()`, `generateMappingPreview()` |
| **Discovery Validation** | `frontend/admin/discoveryValidation.js` | `validateMapping()`, `validateDevicePath()`, `validatePciAddress()` |
| **Discovery State** | `frontend/admin/discoveryState.js` | `savePreviousBayMapState()`, `restorePreviousBayMapState()`, `deepCopyBayMap()` |
| **Template Management** | `frontend/admin/templateManagement.js` | `loadTemplates()`, `createTemplate()`, `applyTemplate()`, `exportTemplate()`, `importTemplate()` |
| **Logo Management** | `frontend/admin/logoManagement.js` | `uploadLogo()`, `deleteLogo()`, `previewLogo()` |
| **Triage Config** | `frontend/admin/triageConfig.js` | `loadTriageConfig()`, `saveTriageConfig()`, `renderTriageThresholds()` |
| **Drive Models** | `frontend/admin/driveModels.js` | Drive model risk profile management UI |
| **Enclosure List** | `frontend/admin/enclosureList.js` | Enclosure list view and CRUD UI |
| **Enclosure Save** | `frontend/admin/enclosureSave.js` | Enclosure save/form handling |
| **Enclosure Wizard** | `frontend/admin/enclosureWizard.js` | Enclosure setup wizard, slot editor |
| **System Config** | `frontend/admin/systemConfig.js` | System config panel (policy, station ID, toggles) |

---

## 3. Data Flow: Lifecycle of a Sanitization Job

When an AI is modifying the job pipeline, trace your changes through this sequence:

```text
1. [UI Dashboard] User clicks "Execute Sanitization"
    │
2. [api_routes.py] POST /api/erase/start ────> validates inputs against `validate_single_bay` and `create_erase_job`
    │
3. [job_management.py] Spawns daemon Thread to run `run_erase_job(job_id)`
    │
4. [job_management.py] Phase 1: Optional pre-wipe health gate (SMART / I/O error check) decides whether to proceed or fail fast
    │
5. [job_management.py] Phase 2: `prepare_erase_command` builds CLI invocation
    │
6. [job_management.py] Phase 3: Starts Popen process, monitors Progress Telemetry (e.g. `poll_sata_sanitize_progress`)
    │
7. [job_management.py] Phase 4 (Asynchronous Only): Enters status check loop waiting for firmware transition (e.g. `verify_sata_sanitize` is completed)
    │
8. [verification.py] `verification_for_method` evaluates hardware logs (hdparm, nvme-cli)
    │
9. [verification.py] If verified successfully, `write_marker_and_verify` writes the checksum/HMAC marker block
    │
10. [certificates.py] `build_certificate` compiles JSON report and signs audit payload using HMAC-SHA256
    │
11. [database.py] `persist_job` commits final status and results block to SQLite
    │
12. [notifier.py] `send_slack_notification` dispatches final webhook payload
```

---

## 3.1 Data Flow: Physical Slot Discovery

When an AI is modifying the discovery system, trace your changes through this sequence:

```text
1. [slot_mapping.py] `generate_master_slot_map(force_refresh=False)` scans sysfs
    │
2. [sas_expander.py] Parses SAS phy links, saves HBA addresses and expander WWNs
    │
3. [pci_controllers.py] Reads `/sys/bus/pci/slots/` for PCIe NVMe mapping
    │
4. [slot_mapping.py] Caches topology mappings for 60 seconds with thread-safe lock
    │
5. [discovery.py] `discover_drives()` resolves logical drive paths from physical slot mappings
    │
6. [device_resolution.py] Resolves enclosure slot → device path, consolidates multipath
    │
7. [routes/drive_routes.py] GET /api/drives returns enclosure-grouped drive inventory
    │
8. [frontend/admin/bayMapping.js] Enclosure management UI uses master map for auto-detection
```

---

## 4. Module Dependency Graph

```
backend/app.py (entry point — create_app())
├── app_config.py
├── system_metrics.py
├── job_management.py
│   ├── disk_utils.py
│   ├── erase_commands.py
│   ├── smart_health_gate.py
│   ├── verification.py
│   └── zero_check_manager.py
├── api_routes.py (non-blueprint routes)
│   ├── app_config.py
│   ├── job_management.py
│   ├── common.py
│   ├── database.py
│   ├── discovery.py (via disk_ops shim)
│   └── routes/admin_routes.py (require_admin_auth)
├── routes/ (10 blueprints via register_blueprints)
│   ├── _shared.py
│   ├── admin_routes.py       # Kill-all-jobs
│   ├── drive_routes.py       # /api/drives, /api/status, zero-check
│   ├── certificate_routes.py # Certificates, bulk HTML, bulk cert
│   ├── bay_mapping_routes.py # Bay map CRUD, unmapped drives, auto-detect
│   ├── discovery_routes.py   # Slot discovery, slot mapping apply
│   ├── enclosure_routes.py   # Enclosure CRUD, slot CRUD, templates, master slot map
│   ├── policy_routes.py      # Policy GET/POST, triage config
│   ├── smart_routes.py       # SMART export, details, self-test, drive models
│   ├── support_routes.py     # Metrics, webhook, CSV, support bundle, logo
│   └── template_routes.py    # Layout template CRUD, import/export, apply
├── discovery.py (drive discovery engine)
│   ├── os_detection.py
│   ├── device_resolution.py
│   ├── drive_collection.py
│   ├── extended_smart.py
│   ├── enclosure_discovery.py
│   └── slot_mapping.py
│       ├── sas_expander.py
│       └── pci_controllers.py
├── smart_data_parsing.py
│   ├── smart_utils.py
│   ├── smart_constants.py
│   └── smart_health.py
├── certificates.py
│   └── layout_templates.py
├── crypto_verification.py
├── database.py
├── common.py
└── notifier.py

frontend/app.js (entry point)
├── utils.js
├── auth.js
├── driveManagement.js
│   ├── driveRendering.js
│   └── utils.js
├── smartRenderers.js
│   └── smartDeepDive.js
├── auditLedger.js
│   └── utils.js
├── batchWipe.js
├── modals.js
├── triageReport.js
├── socket.io.min.js
└── admin/
    ├── adminUtilities.js
    │   └── utils.js
    ├── bayMapping.js
    │   └── adminUtilities.js
    ├── discoveryModal.js
    │   ├── adminUtilities.js
    │   ├── discoveryValidation.js
    │   ├── discoveryState.js
    │   └── discoveryMapping.js
    ├── templateManagement.js
    │   └── adminUtilities.js
    ├── logoManagement.js
    │   └── adminUtilities.js
    ├── triageConfig.js
    │   └── adminUtilities.js
    ├── driveModels.js
    ├── enclosureList.js
    ├── enclosureSave.js
    ├── enclosureWizard.js
    │   └── adminUtilities.js
    └── systemConfig.js
        └── adminUtilities.js
```

---

## 5. NVMe Sanitize Log Reference (verification.py)

**Critical**: The NVMe Sanitize Log Status Field (SSTAT) values are often misunderstood. Below are the correct values per the NVMe specification:

| SSTAT Value | Status | Meaning |
|-------------|--------|---------|
| `0x000` | Never Sanitized | No sanitize operation has been performed since manufacture |
| `0x101` | Completed Successfully | Sanitize finished without errors (Status=1 + Global Data Erased bit) |
| `0x002` | In Progress | Drive is actively wiping; track SPROG 0-65535 for percentage |
| `0x003` | Completed with Failure | Operation failed (power loss or hardware issue) |
| `0x102` | No-Deallocate Completed | Finished successfully but blocks not deallocated (allows forensic verification) |

**Field breakdown**:
- Bits 0-7: Status Code (the values above)
- Bit 8 (0x100): Global Data Erased bit - set when all namespaces have been erased

**SPROG values** (uint16, range 0-65535):
- 0 (0x0000): 0% - Operation initialized / NAND block preparation
- 1-65534: In progress (percentage = SPROG/65535 * 100)
- 65535 (0xFFFF): 100% - Finalized/completed (triggers sstat 0x101 or 0x102)

**SSTAT + SPROG Relationship Table**:

| SSTAT | SPROG | State | Meaning |
|-------|-------|-------|---------|
| `0x000` | 0 | Never Sanitized | No sanitize operation has been performed |
| `0x002` | 0-65534 | In Progress | Drive is actively wiping (percentage = SPROG/65535*100) |
| `0x003` | 65535 | Failed | Operation failed at completion point |
| `0x101` | 65535 | Completed Successfully | Operation finished, all data destroyed |
| `0x102` | 65535 | No-Deallocate Completed | Operation finished, blocks not deallocated |

**Critical**: 
- SPROG=65535 means **100% complete**, not "never executed"
- SPROG=0 means **0% / initialization phase**, not "completed"
- When sstat=0x101 or 0x102 (Completed), SPROG should always be 65535

**Important**: Do NOT treat specific sstat values as drive-specific "quirks" - these are standard NVMe spec values applicable to all drives.

---

## 6. Vendor-Specific NVMe Sanitize Behaviors (Future Implementation)

**Status**: Documented for troubleshooting reference. Not yet implemented in verification logic.

The following vendor-specific behaviors have been observed in the field and may require future handling if verification issues arise. Detection can be implemented via `nvme id-ctrl` model string matching or PCI VID lookup.

### 6.1 Samsung Enterprise (PM/SM Series)

| Behavior | Impact |
|----------|--------|
| Post-sanitize GC delay | After `sstat=0x101`, drive returns busy for ~180s while garbage collection completes |
| "Instant" crypto erase | SPROG jumps 0→65535 instantly for crypto erase (normal, key destruction is fast) |
| Bus reset required | May require PCIe endpoint reset to accept new namespaces post-sanitize |

**Detection**: Model strings containing `Samsung`, `MZ`, `PM`, `SM` prefixes.

**Future Fix**: If verification fails with "device busy" after `sstat=0x101` on Samsung drives, add retry-with-delay logic (180s max).

### 6.2 Solidigm / Intel Data Center (D7/D5 Series)

| Behavior | Impact |
|----------|--------|
| Auto-namespace creation | Creates blank default namespace automatically post-wipe |
| Linear SPROG progress | SPROG increments predictably across all sanitize methods |
| Extended SMART log | Additional SMART log (0xCA) contains physical erase counts for verification |

**Detection**: Model strings containing `Intel`, `Solidigm`, or PCI VID `0x8086` (Intel).

**Future Fix**: For physical verification of block erase, query Intel/Solidigm Additional SMART Log:
```bash
sudo nvme intel smart-log-add /dev/nvme0
```

### 6.3 Micron (Enterprise)

| Behavior | Impact |
|----------|--------|
| 99% pause | SPROG pauses at ~99% while internal capacitor banks verify flash voltage states |
| Power-loss protection errors | May throw PCIe vendor error bits if capacitors lack charge to commit the wipe |

**Detection**: Model strings containing `Micron`, `MT`, or PCI VID `0x1344`.

**Future Fix**: Extended timeout (>5min) when `sstat=0x002` and `SPROG` stuck near 65535 on Micron drives.

### 6.4 General Vendor Detection Approach

**Option A: Model String Matching** (Simple)
```python
result = run_verification_command([nvme_cmd, "id-ctrl", device], text=True)
model = parse_model_from_id_ctrl(result["stdout"])
vendor = detect_vendor_from_model(model)  # "Samsung", "Intel", "Micron", etc.
```

**Option B: PCI VID Lookup** (Reliable)
```python
# Read /sys/bus/pci/devices/XXXX:XX:XX.X/vendor
vid = read_pci_vendor_id(device)  # 0x144d=Samsung, 0x8086=Intel, 0x1344=Micron
```

### 6.5 Warning Signs to Monitor

If future NVMe verification issues occur, check for these vendor-specific patterns:

| Symptom | Likely Vendor | Likely Cause |
|---------|---------------|--------------|
| `sstat=0x101` but "device busy" errors | Samsung | Post-sanitize GC in progress |
| Stuck at 99% for >5min | Micron | Capacitor verification phase |
| Instant completion (<5s) for block erase | Any | Likely crypto erase instead of block erase |
| No namespace after `sstat=0x101` | Samsung | Requires bus reset to recreate |

---

## 7. Instructions for Future AI Assistants

1. **Context Economy**: Do not read the entire directory unless a system-wide structural change is explicitly requested. Read this file first, choose the target module, and request only that file from the user.
2. **Backward Compatibility**: Ensure any modifications to the output shapes or endpoints inside `api_routes.py` preserve compatibility with the payload keys expected by the frontend modules (specifically during bay card mapping and ledger expansions).
3. **Paths Resolution**: Always use the path helper utility functions defined in `backend/common.py` to prevent hardcoded directory conflicts when working on Ubuntu 26.04 environments.
4. **Module Boundaries**: When adding new functionality, place it in the appropriate module based on the responsibilities matrix above. Keep modules focused on their primary purpose.
5. **Import Patterns**: Backend modules should import from each other using relative imports (e.g., `from disk_utils import ...`). Frontend modules are loaded via script tags in index.html in dependency order.