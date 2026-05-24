# Current State

## Status: Repository + Installer Bring-Up Complete ✅
## Status: Test Server Provisioned and Validated ✅
## Status: `/api/drives` Endpoint Functional and Unlocked ✅
## Status: Installer Hardening and Runtime Path Alignment Implemented ✅
## Status: Interface & Capability Detection Stable ✅
## Status: `/api/erase/start` Validation Endpoint and Execution Implemented ✅
## Status: Real Erase Job Execution and Background Orchestration Functional ✅
## Status: Resilient SATA Sanitize Polling and Exit-5 Link Drop Mitigation Complete ✅
## Status: Active Disk Scan Sudo lockup mitigation Complete ✅
## Status: metadata retention during wiping fully operational ✅

## Environment in Use

### Development Environment
- Primary editing environment: VSCode on Windows PC
- Source control: GitHub repository established and pushing correctly
- Recommended Windows workflow: Git Bash inside VSCode

### Test Server
- Fresh Ubuntu 26.04 used as clean bring-up target
- 4 physical bays available
- Production target remains the 8-bay wipe station design

## Completed Since Previous Handoff

### Backend API / Discovery / Orchestration
- Resolved the SATA block/crypto sanitize immediate link drop issue. The orchestrator now pauses for 5 seconds post-initiation and tolerates up to 15 consecutive query failures (approx. 60 seconds) during background polling, which handles drive resets gracefully.
- Resolved active query lockups. Both `/api/drives` and `/api/erase/start` pass the compiled list of active/queued `running_devices` to `discover_drives()`, preventing concurrent diagnostic scans from hanging the Flask server.
- Resolved drive detail wipe blackouts. While a drive is running or queued, `/api/drives` intercepts empty model, serial, and capacity outputs from physical skip-checks and populates them using cached properties.
- Implemented multi-vector health scoring (0-100) on the backend, resolving linear bad-sector anomalies on SSD structures.
- Hardened raw attribute read/write traffic parsing with dynamic 32MiB and GB multiplier support.

### Frontend
- Decoupled `loadDrives()` refreshing from the UI form submission promise chain. Operator alerts are now instant, with card state updating asynchronously in the background.
- UI elements successfully capture and present metadata-retained status boxes during wipes.

## Current Known Gaps / Issues
- None. Core operational and visual behaviors on the backend and frontend are performing as intended.

---

## 🔒 STRICT ARCHITECTURAL LOCK: AI ASSISTANT INSTRUCTIONS
The current functional state of the application's backend architecture (including thread managers, SQLite database routines, device discovery routines, and verification parser/marker handlers) is **fully locked and validated**.

For all future interactions, AI models must operate under these strict guardrails:
1. **Change Prohibition**: You are strictly prohibited from changing, refactoring, or optimizing any backend Python functions (specifically inside `backend/app.py`, `backend/verification.py`, `backend/disk_ops.py`, or `backend/database.py`) without the operator's explicit, typed authorization.
2. **Explicit Consent Workflow**: If a change to backend logic is deemed strictly necessary, the AI **must explicitly ask the operator for permission** to modify the specific function, detailing the exact lines to change and why, before providing any updated code blocks.
3. **Frontend Presentation Scope**: The next phase of development is strictly limited to styling, data display placement, and which information to render on the frontend interface (`frontend/app.js`, `frontend/index.html`, or `frontend/styles.css`).