# AI Context Map: Drive Sanitization Station

This document provides a high-level architectural index and dependency map of the Drive Sanitization Station. Use this file to identify which module to inspect or modify without loading the entire program codebase.

---

## 1. Directory & File Index

All core Python logic resides in the modular `/backend` directory. Frontend files reside in `/frontend`. Automated administration scripts are mapped in `/scripts`.

```text
./
├── backend/                    # Core Python application logic (modular)
│   ├── app.py                  # Application entry point, imports all modules
│   ├── app_config.py            # Flask app initialization, logging, security middleware
│   ├── system_metrics.py        # System monitoring (RAM, CPU, uptime)
│   ├── job_management.py       # Erase job lifecycle management
│   ├── api_routes.py           # Flask route handlers
│   ├── disk_utils.py           # Command resolution, disk utilities, marker operations
│   ├── smart_parsing.py        # SMART data parsing, health scoring, recommendations
│   ├── disk_capabilities.py    # Drive capability detection (SATA/NVMe/SAS)
│   ├── disk_ops.py             # OS drive detection, discovery engine
│   ├── certificates.py         # Render engine & HMAC-SHA256 signature generator
│   ├── common.py               # Shared path resolvers, JSON policy loader
│   ├── database.py             # Schema initialization, PRAGMA alterations, SQLite writes
│   ├── notifier.py             # Webhook alerting dispatcher
│   └── verification.py         # Resilient firmware sanitize status checkers & marker logic
├── config/                     # Static operational profiles
│   ├── bay_map.json            # Mapping of physical bays to dev-by-path values
│   └── policy.json             # System rule configurations, methods priority, passphrase
├── data/                       # Persistent runtime assets (Ignored by Git except .gitkeep)
│   ├── wipes.db                # SQLite database (stores all jobs, results, certificates)
│   └── certs/                  # Generated JSON and HTML certificates
├── docs/                       # Technical runbooks, SOPs, and design specifications
│   ├── api-contract.md         # Endpoint input/output shapes
│   ├── change-log.md           # Engineering development timeline
│   ├── runbook.md              # Deployment and operational instructions
│   ├── SOP_technician_guide.md # Step-by-step physical drive handling guidelines
│   └── troubleshooting.md      # Hardware error codes and debug workflows
├── frontend/                   # UI Assets (modular)
│   ├── app.js                  # Frontend entry point, imports all modules
│   ├── utils.js                # Utility functions (escapeHtml, formatting, clipboard)
│   ├── auth.js                 # Authentication overlay and passphrase verification
│   ├── driveManagement.js      # Drive discovery, rendering, batch operations
│   ├── modals.js               # Modal dialog rendering and controls
│   ├── auditLedger.js          # Audit history display, certificate management
│   ├── adminPanel.js           # Admin panel and bay mapping configuration
│   ├── index.html              # UI Template
│   └── styles.css              # 15-foot state colors, dashboard grids
├── scripts/                    # Automation and lifecycle shell wrappers
│   ├── install.sh              # Host setup and package requirements script
│   ├── seed_test_data.sh       # Mock population helper for offline staging
│   ├── start.sh                # Local manual daemon run wrapper
│   └── export-logs.sh          # Log export utility
├── systemd/                    # Service manager configuration
│   └── drive-eraser.service    # Systemd unit definition for background execution
├── .gitignore                  # Environment-specific exclude profiles
├── requirements.txt            # Python dependencies index
├── AGENTS.md                   # Multi-agent collaboration manifest
├── AI_CONTEXT.md               # High-level architectural index and dependency map
└── README.md                   # Quickstart installation instructions

```

---

## 2. Module Responsibilities Matrix

| If you want to modify... | Look in this file | Key Functions / Definitions to Inspect |
| :--- | :--- | :--- |
| **Application Entry Point** | `backend/app.py` | Imports all modules, initializes Flask app |
| **Flask App Configuration** | `backend/app_config.py` | `app`, `logger`, `get_config_dir()`, `load_policy()` |
| **System Monitoring** | `backend/system_metrics.py` | `get_ram_usage()`, `get_cpu_usage()`, `get_system_uptime()` |
| **Job Lifecycle Management** | `backend/job_management.py` | `validate_single_bay()`, `create_erase_job()`, `run_erase_job()`, `prepare_erase_command()` |
| **HTTP Route Handlers** | `backend/api_routes.py` | All `@app.route()` definitions, API endpoints |
| **Command Resolution & Utilities** | `backend/disk_utils.py` | `resolve_command_path()`, `run_command()`, `execute_erase_method()`, `read_marker_status()` |
| **SMART Data Parsing** | `backend/smart_parsing.py` | `get_smart_data()`, `classify_interface_from_smart()`, `calculate_drive_health_score()`, `get_drive_recommendation()` |
| **Drive Capability Detection** | `backend/disk_capabilities.py` | `detect_drive_capabilities()`, `detect_sata_capabilities()`, `detect_nvme_capabilities()`, `detect_sas_capabilities()` |
| **OS Drive Detection & Discovery** | `backend/disk_ops.py` | `get_os_parent_device()`, `get_os_by_path()`, `discover_drives()` |
| **CLI Progress Telemetry (Pollers)** | `backend/job_management.py` | `poll_nvme_sanitize_progress()`, `poll_sata_sanitize_progress()`, `poll_sas_sanitize_progress()` |
| **Common Directory Paths** | `backend/common.py` | `get_data_dir()`, `get_db_path()`, `get_cert_dir()`, `get_config_dir()` |
| **Policy JSON Loader** | `backend/common.py` | `load_policy()` |
| **SQLite Schema & DB Writes** | `backend/database.py` | `init_wipe_db()`, `persist_job()`, `ensure_column()` |
| **Direct Command Verification** | `backend/verification.py` | `verify_overwrite()`, `verify_nvme_sanitize()`, `verify_sata_sanitize()`, `verify_sas_block()`, `verify_sata_secure_erase()` |
| **Command Verification Orchestrator**| `backend/verification.py` | `verification_for_method()`, `run_verification_command()` |
| **Post-wipe Disk Markers** | `backend/verification.py` | `write_marker_and_verify()`, `build_marker_payload()` |
| **Cryptographic Certificates** | `backend/certificates.py` | `build_certificate()`, `build_certificate_html()`, `calculate_certificate_hash()` |
| **Slack Webhooks / Chat Alerts** | `backend/notifier.py` | `send_slack_notification()` |
| **Frontend Entry Point** | `frontend/app.js` | Imports all modules, tab switching, initialization |
| **Frontend Utilities** | `frontend/utils.js` | `escapeHtml()`, `formatIsoDate()`, `calculateDriveHealthScore()`, `copyTextToClipboard()` |
| **Authentication** | `frontend/auth.js` | `showAuthOverlay()`, `hideAuthOverlay()`, `loadSecurityStatus()` |
| **Drive Management** | `frontend/driveManagement.js` | `loadDrives()`, `renderBays()`, `pollActiveWipes()`, `toggleBaySelection()` |
| **Modal Controls** | `frontend/modals.js` | `openModal()`, `closeModal()`, `renderLiveDetails()` |
| **Audit Ledger** | `frontend/auditLedger.js` | `loadHistoryIndex()`, `renderAuditLedger()`, `renderExpandedAuditRow()` |
| **Admin Panel** | `frontend/adminPanel.js` | `loadAdminMetrics()`, `loadBayMappingConfig()`, `saveBayMappingConfiguration()` |

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
4. [job_management.py] Phase 1: `prepare_erase_command` builds CLI invocation
    │
5. [job_management.py] Phase 2: Starts Popen process, monitors Progress Telemetry (e.g. `poll_sata_sanitize_progress`)
    │
6. [job_management.py] Phase 3 (Asynchronous Only): Enters status check loop waiting for firmware transition (e.g. `verify_sata_sanitize` is completed)
    │
7. [verification.py] `verification_for_method` evaluates hardware logs (hdparm, nvme-cli)
    │
8. [verification.py] If verified successfully, `write_marker_and_verify` writes the checksum/HMAC marker block
    │
9. [certificates.py] `build_certificate` compiles JSON report and signs audit payload using HMAC-SHA256
    │
10. [database.py] `persist_job` commits final status and results block to SQLite
    │
11. [notifier.py] `send_slack_notification` dispatches final webhook payload
```

---

## 4. Module Dependency Graph

```
backend/app.py (entry point)
├── app_config.py
├── system_metrics.py
├── job_management.py
│   ├── disk_utils.py
│   ├── smart_parsing.py
│   └── verification.py
├── api_routes.py
│   ├── app_config.py
│   ├── system_metrics.py
│   ├── job_management.py
│   ├── common.py
│   ├── database.py
│   ├── disk_ops.py
│   ├── disk_utils.py
│   ├── smart_parsing.py
│   └── layout_templates.py
└── disk_ops.py
    ├── disk_utils.py
    ├── smart_parsing.py
    └── disk_capabilities.py

frontend/app.js (entry point)
├── utils.js
├── auth.js
├── driveManagement.js
│   └── utils.js
├── modals.js
│   └── utils.js
├── auditLedger.js
│   └── utils.js
└── adminPanel.js
    └── utils.js
```

---

## 5. Instructions for Future AI Assistants

1. **Context Economy**: Do not read the entire directory unless a system-wide structural change is explicitly requested. Read this file first, choose the target module, and request only that file from the user.
2. **Backward Compatibility**: Ensure any modifications to the output shapes or endpoints inside `api_routes.py` preserve compatibility with the payload keys expected by the frontend modules (specifically during bay card mapping and ledger expansions).
3. **Paths Resolution**: Always use the path helper utility functions defined in `backend/common.py` to prevent hardcoded directory conflicts when working on Ubuntu 26.04 environments.
4. **Module Boundaries**: When adding new functionality, place it in the appropriate module based on the responsibilities matrix above. Keep modules focused on their primary purpose.
5. **Import Patterns**: Backend modules should import from each other using relative imports (e.g., `from disk_utils import ...`). Frontend modules are loaded via script tags in index.html in dependency order.