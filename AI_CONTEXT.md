Here is a structured architectural map designed specifically for AI models. You can save this as a file named `AI_CONTEXT.md` in the root of your project directory (`/opt/drive-eraser/AI_CONTEXT.md`). 

If you hand this document to an AI in a future session, it can read this file first and pinpoint exactly which module to request or inspect, saving context window space and avoiding the need to process the entire codebase.

---

```markdown
# AI Context Map: Drive Sanitization Station

This document provides a high-level architectural index and dependency map of the Drive Sanitization Station. Use this file to identify which module to inspect or modify without loading the entire program codebase.

---

## 1. Directory & File Index

All core Python logic resides in the flat `/backend` directory. Frontend files reside in `/frontend`. Automated administration scripts are mapped in `/scripts`.

```text
./
├── backend/                    # Core Python application logic
│   ├── app.py                  # API Router, job queue thread manager, CLI pollers
│   ├── certificates.py         # Render engine & HMAC-SHA256 signature generator
│   ├── common.py               # Shared path resolvers, JSON policy loader
│   ├── database.py             # Schema initialization, PRAGMA alterations, SQLite writes
│   ├── disk_ops.py             # Pure wrappers for smartctl, hdparm, and health math
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
├── frontend/                   # UI Assets
│   ├── app.js                  # Frontend state machine, DOM rendering, polling loop
│   ├── index.html              # UI Template
│   └── styles.css              # 15-foot state colors, dashboard grids
├── scripts/                    # Automation and lifecycle shell wrappers
│   ├── install.sh              # Host setup and package requirements script
│   ├── seed_test_data.sh       # Mock population helper for offline staging
│   ├── start.sh                # Local manual daemon run wrapper
│   └── update.sh               # Safe git pull and service reload utility
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
| **HTTP Routing, Server Bindings** | `backend/app.py` | `@app.route()`, `get_drives()`, `start_erase()` |
| **Live Job Orchestration / Threading** | `backend/app.py` | `run_erase_job()`, `finalize_failed_job()`, `prepare_erase_command()` |
| **CLI Progress Telemetry (Pollers)** | `backend/app.py` | `poll_nvme_sanitize_progress()`, `poll_sata_sanitize_progress()`, `poll_sas_sanitize_progress()` |
| **SMART attributes parsing** | `backend/disk_ops.py` | `get_smart_data()`, `classify_interface_from_smart()` |
| **Hardware Capabilities/Triage** | `backend/disk_ops.py` | `detect_drive_capabilities()`, `get_drive_recommendation()`, `discover_drives()` |
| **Common Directory Paths** | `backend/common.py` | `get_data_dir()`, `get_db_path()`, `get_cert_dir()`, `get_config_dir()` |
| **Policy JSON Loader** | `backend/common.py` | `load_policy()` |
| **SQLite Schema & DB Writes** | `backend/database.py` | `init_wipe_db()`, `persist_job()`, `ensure_column()` |
| **Direct Command Verification** | `backend/verification.py` | `verify_overwrite()`, `verify_nvme_sanitize()`, `verify_sata_sanitize()`, `verify_sas_block()`, `verify_sata_secure_erase()` |
| **Command Verification Orchestrator**| `backend/verification.py` | `verification_for_method()`, `run_verification_command()` |
| **Post-wipe Disk Markers** | `backend/verification.py` | `write_marker_and_verify()`, `build_marker_payload()` |
| **Cryptographic Certificates** | `backend/certificates.py` | `build_certificate()`, `build_certificate_html()`, `calculate_certificate_hash()` |
| **Slack Webhooks / Chat Alerts** | `backend/notifier.py` | `send_slack_notification()` |
| **UI Event bindings, State, Polling** | `frontend/app.js` | `pollActiveWipes()`, `loadDrives()`, `renderBays()`, `toggleBaySelection()` |
| **Audit Ledger rendering** | `frontend/app.js` | `loadHistoryIndex()`, `renderAuditLedger()`, `renderExpandedAuditRow()` |

---

## 3. Data Flow: Lifecycle of a Sanitization Job

When an AI is modifying the job pipeline, trace your changes through this sequence:

```text
1. [UI Dashboard] User clicks "Execute Sanitization" 
    │
2. [app.py] POST /api/erase/start ────> validates inputs against `validate_single_bay` and `create_erase_job`
    │
3. [app.py] Spawns daemon Thread to run `run_erase_job(job_id)`
    │
4. [app.py] Phase 1: `prepare_erase_command` builds CLI invocation
    │
5. [app.py] Phase 2: Starts Popen process, monitors Progress Telemetry (e.g. `poll_sata_sanitize_progress`)
    │
6. [app.py] Phase 3 (Asynchronous Only): Enters status check loop waiting for firmware transition (e.g. `verify_sata_sanitize` is completed)
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

## 4. Instructions for Future AI Assistants

1. **Context Economy**: Do not read the entire directory unless a system-wide structural change is explicitly requested. Read this file first, choose the target module, and request only that file from the user.
2. **Backward Compatibility**: Ensure any modifications to the output shapes or endpoints inside `app.py` preserve compatibility with the payload keys expected by `frontend/app.js` (specifically during bay card mapping and ledger expansions).
3. **Paths Resolution**: Always use the path helper utility functions defined in `backend/common.py` to prevent hardcoded directory conflicts when working on Ubuntu 26.04 environments.
```