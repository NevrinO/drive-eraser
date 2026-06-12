# AI Context Map: Drive Sanitization Station

**Purpose**: This document provides a high-level architectural index and dependency map of the Drive Sanitization Station. Use this file to identify which module to inspect or modify without loading the entire program codebase. It answers "where" things are and "how" they connect.

**Relation to other docs**:
- `docs/ARCHITECTURE.md` - Architectural decisions and design rationale (answers "why" things are this way)
- `README.md` - Quickstart and installation instructions
- `docs/api-contract.md` - API endpoint specifications
- `docs/SOP_technician_guide.md` - Operational procedures for technicians

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
│                                 # See "NVMe Sanitize Log Reference" section below for SSTAT/SPROG values
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
│   ├── auditLedger.js          # Audit history display, certificate management
│   ├── admin/                  # Admin panel modules (modular)
│   │   ├── adminUtilities.js   # Shared admin utilities and helpers
│   │   ├── bayMapping.js       # Bay mapping configuration and management
│   │   ├── discoveryModal.js   # Drive discovery modal and slot mapping
│   │   ├── templateManagement.js # Certificate template management
│   │   ├── logoManagement.js   # Custom logo upload and management
│   │   └── triageConfig.js     # Triage threshold configuration
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
├── docs/CODE_MAP.md            # High-level architectural index and dependency map
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
| **Frontend Utilities** | `frontend/utils.js` | `escapeHtml()`, `formatIsoDate()`, `calculateDriveHealthScore()`, `copyTextToClipboard()`, `classifyError()`, `handleError()` |
| **Authentication** | `frontend/auth.js` | `showAuthOverlay()`, `hideAuthOverlay()`, `loadSecurityStatus()` |
| **Drive Management** | `frontend/driveManagement.js` | `loadDrives()`, `renderBays()`, `pollActiveWipes()`, `toggleBaySelection()` |
| **Audit Ledger** | `frontend/auditLedger.js` | `loadHistoryIndex()`, `renderAuditLedger()`, `renderExpandedAuditRow()` |
| **Admin Utilities** | `frontend/admin/adminUtilities.js` | Shared admin helpers, modal management, common admin functions |
| **Bay Mapping** | `frontend/admin/bayMapping.js` | `loadBayMappingConfig()`, `saveBayMappingConfiguration()`, `renderBayMappingUI()` |
| **Discovery Modal** | `frontend/admin/discoveryModal.js` | `openDiscoveryModal()`, `discoverSlots()`, `applySlotMapping()` |
| **Template Management** | `frontend/admin/templateManagement.js` | `loadTemplates()`, `createTemplate()`, `applyTemplate()`, `exportTemplate()`, `importTemplate()` |
| **Logo Management** | `frontend/admin/logoManagement.js` | `uploadLogo()`, `deleteLogo()`, `previewLogo()` |
| **Triage Config** | `frontend/admin/triageConfig.js` | `loadTriageConfig()`, `saveTriageConfig()`, `renderTriageThresholds()` |

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
├── auditLedger.js
│   └── utils.js
└── admin/
    ├── adminUtilities.js
    │   └── utils.js
    ├── bayMapping.js
    │   └── adminUtilities.js
    ├── discoveryModal.js
    │   └── adminUtilities.js
    ├── templateManagement.js
    │   └── adminUtilities.js
    ├── logoManagement.js
    │   └── adminUtilities.js
    └── triageConfig.js
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