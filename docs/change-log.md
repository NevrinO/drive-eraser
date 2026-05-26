# Change Log

## v0.25 - Hybrid Logging, Diagnostics Support Bundles, Remote UI Gates, & Interactive Bay Mapping
- **Hybrid Logging Subsystem**: Segregated logging boundaries. Technical runtime alerts go to `app.log` (rotating at 10MB). Active subprocess `stdout`/`stderr` pipes write progressively to ephemeral `data/logs/active/job-{id}.log` streams. Failed wipes are closed and relocated to `data/logs/failed/` with complete raw `smartctl -a` attributes appended for post-mortem forensics. Successful runs are cleanly expunged to preserve disk space.
- **Auto-Purge Garbage Collection**: Integrated a deterministic retention cleaner executing synchronously on the completion of any wipe. Deletes any active or failed logs whose modification age exceeds 30 days.
- **Localhost-Bypassed Security Gate**: Implemented client network IP evaluation. Bypasses password gates for local TTY touchscreens (originating on `127.0.0.1` or `::1`). Enforces secure HTTP-Only cookie verification (`admin_session`) for any network-based LAN requests, prompting remote operators with a passcode overlay matched against the `"lan_passphrase"` configured inside `policy.json`.
- **System Administration UI (Tab 3)**: Added an adjacent separate administration tab containing:
  - **Host Resource Telemetry**: Real-time polling monitoring host CPU load, RAM usage, OS partition capacity, and uptime.
  - **Webhook Alerts Testing**: A native loop check dispatching test payloads to Slack to diagnose network isolation blocks.
  - **Support Bundle Compiler**: Packs system hardware mappings (`lsblk`, `lshw`), redacted configurations, system health metrics, and failed logs into a single compressed `support-bundle-{hostname}-{timestamp}.tar.gz` directly in the browser.
  - **CLI Diagnostics Fallback**: Written `scripts/export-logs.sh` to package diagnostic bundles directly onto connected USB storage sticks or user home folders when headless or offline.
  - **Interactive Bay Mapping**: Visualized configuration links. Added staged UI controls to append, delete, and modify physical drive bays (bays bound from 1 to 128) and map them to unassigned system controllers, reloading configurations on save.
- **Workbench Layout Optimization**: Standardized the card rendering layout to display exactly **4 columns per row** on desktop viewports, with clean, smaller display labels embedded inside headers.
- **Sudoers Expansion**: Updated both `install.sh` and `update.sh` to append passwordless `sudo` rights for `lshw` and `systemctl` to the restricted `wipestation` account.

## v0.24 - Resilient SATA Sanitize Polling, Lockup Bypass & Metadata Recovery
- **Resilient Polling Loop**: Implemented a 5-second initial delay (settling time) inside Stage 3 polling to allow SATA host controller link resets to resolve. Added consecutive failure tolerance (up to 15 retries / 60 seconds) during the firmware status check loop. This accommodates immediate drive resets that cause `hdparm` to return exit code `5` / input-output errors during the initiation of block or crypto erase methods.
- **Active Query Lockup Bypass**: Configured both `/api/drives` and `/api/erase/start` in `backend/app.py` to compile and pass the list of `running_devices` to `discover_drives()`. This bypasses physical drive scanning on busy devices, preventing API hangs when operators or frontend refresh timers poll the system during active runs.
- **Wiping Metadata Retention**: Restored original drive metadata (`serial`, `model`, and capacity) to `/api/drives` from the active job's in-memory data cache when a bay is in a `RUNNING` or `QUEUED` state. This prevents UI card blackouts (such as "Generic Drive") while a wipe is in progress.
- **Frontend Optimization**: Removed the blocking UI `await` call on `loadDrives()` inside the form submit listener in `frontend/app.js`. This makes the user-facing confirmation alert instantaneous while refreshing card statuses in the background.

## v0.23 - Multi-Vector Health, Accurate SAS Bad Sectors & SSD Traffic Scaling
- Implemented Multi-Vector Health Scoring in `backend/disk_ops.py` and `frontend/app.js`:
  - Differentiated bad sector rules: strict raw counts on mechanical HDDs, and reserve depletion scaling (Available Spare) on SSDs.
  - Implemented mutually exclusive SSD flash wear vs. HDD mechanical age (Power-On Hours + workload Full Drive Writes) to prevent double-dipping.
  - Added gentle operational runtime decay for SSD controllers above 40,000 POH.
  - Leveraged `smartctl` exit-status Bit 3 and Bit 4 for pre-failure overrides, while explicitly ignoring Bit 5 (usage limits).
- Resolved SSD Read/Write Traffic Under-reporting:
  - Added dynamic attribute name parsing (e.g., `Host_Writes_32MiB`, `Host_Writes_GiB`) to map sector blocks correctly.
  - Exposed pre-calculated `data_written_bytes` and `data_read_bytes` in API response payload.
- Fixed SAS Bad Sector Reporting:
  - Replaced soft background ECC fallbacks with direct, strict parsing of `scsi_grown_defect_list` (G-list).

## v0.22 - Known Issue Update: Bay Detail SMART/Marker Not Functioning
- Updated project docs to reflect current operator-visible behavior in bay detail modal:
  - SMART essentials are currently not populating correctly in the modal
  - marker validity is currently not displaying expected valid state in the modal
- Marked these as active known issues for next implementation/debug pass.

## v0.21 - SMART Essentials Population + Marker Readback Validity Fix
- Fixed SMART essentials data flow from backend to frontend bay detail modal:
  - expanded `backend/disk_ops.py:get_smart_data` to parse key SMART values from `smartctl` output (temperature, reallocated sectors, pending sectors, wear level, power-on hours)
  - switched SMART command invocation to include full attributes (`smartctl -a -i -H`)
  - added normalized `smart` object into `/api/drives` bay payloads for both present and empty-bay shapes
  - updated `frontend/app.js:parseSmartEssentials` to consume `drive.smart` instead of placeholder-only values
- Fixed marker readback false-invalid condition in `backend/app.py:read_marker_status`:
  - readback now extracts first marker payload line, trims null bytes, isolates JSON boundaries (`{...}`), and parses full payload before signature validation
  - prevents parse failures caused by attempting JSON parse from signature byte offset
- Updated frontend marker display normalization in bay detail modal:
  - maps job marker state `marked` to operator-facing `valid`
- Validation run:
  - `python -m py_compile backend/app.py backend/disk_ops.py`
  - diagnostics check clean for `backend/app.py`, `backend/disk_ops.py`, `frontend/app.js`
  - `/api/drives` smoke check confirms `smart` payload fields are present in response shape

- Expanded Certificates tab with quick actions tied to job id input:
  - Download JSON
  - Download HTML
  - Print certificate view
  - Copy audit fields
- Expanded History job detail modal with completion actions for completed jobs:
  - Download JSON/HTML certificate
  - Print certificate view
  - Copy audit fields
- Added certificate action status messaging and widened certificate-controls layout for the new operator actions.
- Updated frontend files:
  - `frontend/index.html`
  - `frontend/app.js`
  - `frontend/styles.css`
- Documentation sync updates applied to:
  - `docs/handoff_prompt.md`
  - `docs/decision.md`
  - `docs/current_state.md`

## v0.19 - Phase 3 Completion Actions in Jobs View
- Implemented immediate completion actions in the Jobs tab after a job reaches `completed`:
  - Download certificate JSON
  - Download certificate HTML
  - Print certificate view from HTML artifact
  - Copy audit fields (job id, ticket, technician, bay, serial, method, status, timestamps, verification, marker)
- Added completion-actions UI container and status messaging in frontend:
  - `frontend/index.html`
  - `frontend/styles.css`
  - `frontend/app.js`
- Wired job-tracking flow to surface/hide completion actions based on terminal job state.
- Documentation sync updates applied to:
  - `docs/handoff_prompt.md`
  - `docs/decision.md`
  - `docs/current_state.md`

- Synced project documentation to the approved UI implementation baseline before coding.
- Confirmed implementation order:
  1. SOP-aligned status label/color mapping across bays/jobs/detail surfaces
  2. top-tab workflow + bay-detail modal
  3. completion actions (certificate download/print + copy audit fields)
  4. history and certificates tab surfaces
- Confirmed redesign scope and guardrails:
  - major visual redesign is approved for this pass
  - safety behavior for protected bays remains unchanged
  - erase-method policy and recommendation precedence remain unchanged
- Updated docs:
  - `docs/handoff_prompt.md`
  - `docs/decision.md`
  - `docs/current_state.md`

## v0.17 - Frontend Safety/Recommendation UX Pass
- Updated erase method selection UI in `frontend/index.html` + `frontend/app.js`:
  - method dropdown now defaults to `Use recommended`
  - method options are dynamically scoped to selected bay `supported_methods`
  - recommended method hint is shown next to selector and updates on bay change
- Added deterministic frontend recommendation ordering in `frontend/app.js`:
  - `crypto` > `block` > `enhanced_secure_erase` > `secure_erase` > `overwrite`
  - keeps SATA recommendation priority aligned (`enhanced_secure_erase` before `secure_erase`)
- Improved bay safety visibility in UI:
  - bay cards marked as protected (`locked`/`os`/`reserved`) use dedicated warning styling
  - bay cards now display computed recommended method directly in card details
- Styling updates in `frontend/styles.css`:
  - `.bay-card.protected`
  - `.bay-card.empty`
  - `.form-hint`
- Validation run:
  - `python -m py_compile backend/app.py backend/disk_ops.py`

## v0.16 - Test Data Seeding Script for Wipe Validation
- Added `scripts/seed_test_data.sh` to seed known data patterns onto a selected disk for wipe verification testing.
- Safety controls in script:
  - requires explicit root execution
  - requires explicit device path and exact typed confirmation (`SEED <device>`)
  - refuses mounted targets
  - refuses detected root OS disk target
  - supports bounded write size (`--size-mb`, default 256 MiB)
- Pattern options:
  - `--pattern random` writes random bytes
  - `--pattern zero` writes zero bytes
- Script writes bulk pattern data first, then rewrites the first block with metadata header to preserve a visible test marker while keeping seeded payload behavior correct.
- Validation run:
  - `python -m py_compile backend/app.py backend/disk_ops.py`
  - script syntax check could not be executed in this Windows shell because `bash` was unavailable

## v0.15 - Certificate Export-Ready Output (JSON + HTML)
- Expanded certificate generation in `backend/app.py` beyond JSON-only artifacts:
  - retained JSON certificate output under `data/certs/`
  - added HTML certificate artifact generation using escaped, structured certificate content
  - certificate payload now includes `formats` metadata with JSON/HTML file paths and filenames
- Extended certificate retrieval API in `backend/app.py`:
  - `GET /api/certificates/<job_id>` keeps JSON response default
  - added format selection with `GET /api/certificates/<job_id>?format=html` for downloadable HTML artifact
  - invalid format values now return `400` with explicit allowed values
- Validation run:
  - `python -m py_compile backend/app.py backend/disk_ops.py`
  - targeted Flask test-client smoke checks for certificate retrieval:
    - default JSON response returned 200
    - HTML format response returned 200 with `text/html`
    - unsupported format returned 400
  - diagnostics check for `backend/app.py` reported no issues

## v0.14 - Verification Strictness Finalization Pass (Ubuntu Output Variants)
- Finalized parser hardening in `backend/app.py` for method-aware verification status interpretation:
  - added command-output fallback helper to prefer `stdout` and safely use `stderr` when needed
  - constrained SATA parsing to the `Security:` section to avoid false matches from unrelated lines
  - expanded NVMe sanitize completion/failure detection using `SPROG`/`SSTAT` plus broader status markers
  - corrected SAS status precedence so completion markers override ambiguous `in progress` phrases when both appear
- Preserved fail-closed behavior for ambiguous output by returning explicit verification errors for unrecognized states
- Validation run:
  - `python -m py_compile backend/app.py backend/disk_ops.py`
  - targeted parser smoke checks (mocked command outputs):
    - NVMe failed-state output -> `nvme_sanitize_failed_state`
    - SATA secure-state output -> `verified`
    - SAS in-progress output -> `sas_sanitize_still_in_progress`
    - SAS idle/not-in-progress output -> `verified`
  - diagnostics check for `backend/app.py` reported no issues

## v0.13 - SAS Capability Probing via SG Command Behavior
- Updated SAS capability detection in `backend/disk_ops.py`:
  - added `detect_sas_capabilities` helper
  - SAS block support now derives from `sg_sanitize --status` command behavior/output instead of unconditional default support
  - keeps SAS crypto capability disabled in current implementation
- Updated capability aggregation flow in `detect_drive_capabilities` to call SAS-specific probe logic
- Validation run:
  - `python -m py_compile backend/app.py backend/disk_ops.py`
  - targeted smoke check of `detect_sas_capabilities` function path

## v0.12 - Marker Write/Read Lifecycle Implemented
- Implemented post-erase marker lifecycle in `backend/app.py`:
  - added marker payload builder using a fixed signature (`DWS_MARKER_V1`) and structured JSON metadata
  - added marker write path to LBA0 block via `dd` after erase+verification success
  - added marker readback validation to confirm marker signature and payload integrity
  - added marker state classification on readback (`valid`, `invalid`, `none`)
- Kept lifecycle safety behavior aligned with policy:
  - marker write/read outcome is captured in job payload as `marker`
  - marker failure does not block certificate generation when erase+verification succeeded
- Extended SQLite persistence in `backend/app.py`:
  - added `marker_json` column to `erase_jobs` with migration guard
  - persisted marker results for queued/running/final job records
  - included marker payload in `GET /api/erase/jobs/<job_id>` and `GET /api/erase/history`
- Validation run:
  - `python -m py_compile backend/app.py backend/disk_ops.py`
  - Flask test-client smoke check for `GET /api/erase/history` returned HTTP 200

## v0.11 - Verification Parser Strictness Hardening (NVMe/SATA/SAS)
- Hardened method-aware verification parsing in `backend/app.py` for real-world Ubuntu output variants:
  - added numeric field parsing helper for sanitize logs (`SPROG`, `SSTAT`)
  - NVMe verification now classifies empty/unrecognized/failed/in-progress/completed output states conservatively
  - SATA verification now validates security section presence and handles enabled/disabled wording variants more safely
  - SAS verification now classifies in-progress/failed/completed/unrecognized sanitize status outputs
- Kept safety-first behavior: ambiguous or unrecognized verification output now fails closed with explicit verification errors
- Verified backend compile checks and diagnostics are clean after changes
- Ran targeted parser smoke checks with mocked verification command outputs for NVMe/SATA/SAS branches

- Extended `backend/app.py` erase job persistence model to include `certificate_json` in SQLite (`data/wipes.db`)
- Added SQLite schema migration guards for new/legacy databases using startup column checks
- Added certificate build/write flow in erase lifecycle:
  - certificate payload generated only after erase+verification success
  - certificate JSON artifact written under `data/certs/`
  - certificate payload stored in memory and persisted with job record
- Added `GET /api/erase/history` in `backend/app.py`:
  - returns recent persisted jobs with request/result/verification/certificate payloads
  - supports bounded `limit` query parameter validation
- Added `GET /api/certificates/<job_id>` in `backend/app.py`:
  - returns in-memory certificate when available
  - falls back to persisted SQLite certificate payload
- Updated `GET /api/erase/jobs/<job_id>` response contract to include `certificate`
- Verified compile checks, diagnostics, and targeted Flask test-client smoke checks for history/certificate paths

- Enhanced `scripts/update.sh` with optional runtime control flags:
  - added `--no-restart` to skip service restart and post-restart verification for staged rollouts
  - added `--dry-run` to preview update actions without mutating system state
  - added `--help` usage output for operator clarity
- Added SQLite persistence in `backend/app.py` for erase jobs (`data/wipes.db`):
  - initializes `erase_jobs` table at backend startup
  - persists jobs at queued/running/final states
  - stores request/result/verification payloads as JSON
- Updated `GET /api/erase/jobs/<job_id>` in `backend/app.py`:
  - returns in-memory job when present
  - falls back to persisted SQLite record when job is no longer in memory
- Added method-aware verification handlers in `backend/app.py` and wired into erase lifecycle:
  - overwrite verification via sampled zero-check reads using `dd`
  - NVMe `crypto`/`block` verification via `nvme sanitize-log`
  - SATA `secure_erase`/`enhanced_secure_erase` verification via `hdparm -I` security-state check
  - SAS `block` verification via `sg_sanitize --status`
- Job completion now requires both erase command success and verification success; verification outcomes are persisted and returned in job payload
- Verified backend compile checks pass and diagnostics are clean for changed files

## v0.8 - Destructive Erase Job Execution, Job Tracking API, Smartctl-First Classification, and Frontend Tracking Controls
- Updated interface detection in `backend/disk_ops.py` to prioritize `smartctl -i` signatures for NVMe/SATA/SAS classification with fallback heuristics only when smartctl data is unavailable
- Added destructive execution plumbing in `backend/disk_ops.py`:
  - added command resolution for `sg_sanitize` and `dd`
  - added structured destructive command runner with stdout/stderr/exit-code capture
  - added erase method execution mapping:
    - `overwrite` via `dd`
    - `secure_erase` / `enhanced_secure_erase` (SATA) via `hdparm`
    - NVMe `block` / `crypto` via `nvme sanitize`
    - SAS `block` via `sg_sanitize --block`
- Upgraded `POST /api/erase/start` in `backend/app.py`:
  - retains strict request validation and safety blocks
  - now creates asynchronous erase jobs and starts background execution threads
  - returns accepted job metadata including `job.id`
- Added `GET /api/erase/jobs/<job_id>` in `backend/app.py` for polling job lifecycle and command result payloads
- Added in-memory job state model with statuses: `queued`, `running`, `completed`, `failed`
- Updated frontend tracking flow:
  - automatic polling of `/api/erase/jobs/<job_id>` after job acceptance
  - live status output in result panel
  - polling timeout guard to avoid indefinite UI wait
  - manual “Refresh Job Tracking” by job id for resume behavior
- Verified touched backend files compile and frontend/backend diagnostics are clean

## v0.7 - Erase Validation Endpoint, Basic Frontend, and Full Update Script
- Added `POST /api/erase/start` validation endpoint in `backend/app.py`:
  - validates technician, ticket number, bay selection, and typed confirmation
  - hard-blocks protected bays (`locked`, `os`, `reserved`)
  - validates selected method against detected drive capabilities and policy override rules
  - returns accepted validation payload for testing without starting destructive erase operations
- Added basic frontend for backend testing:
  - `frontend/index.html` dashboard shell with erase form
  - `frontend/app.js` live calls to `/api/drives` and `/api/erase/start`
  - `frontend/styles.css` baseline visual styling for bay cards and form flow
- Updated backend static serving path in `backend/app.py`:
  - `/` now serves frontend when available
  - static asset route added for frontend files while keeping `/api/*` reserved for API
- Implemented `scripts/update.sh` end-to-end update workflow:
  - preflight checks for root, install directory, and source repo
  - timestamped config backup (`bay_map.json`, `policy.json`, `command_paths.json`)
  - rsync app update preserving config/data/logs/venv/backups
  - command path re-resolution and `command_paths.json` rewrite
  - virtualenv package refresh and import validation
  - sudoers regeneration with `visudo -cf` validation
  - service unit refresh, daemon-reload, restart, and health verification

## v0.6 - Discovery Diagnostics, Flexible Bay Mapping, and Systemd Sudo Fix
- Enhanced `backend/disk_ops.py` bay resolution:
  - supports `/dev/disk/by-path/...` full-path input
  - supports bare by-path entry-name input
  - supports direct `/dev/...` device-path input with reverse mapping back to by-path
- Added discovery output metadata:
  - `resolved_by_path` for matched by-path identity
  - per-bay `diagnostics` object for root-cause visibility
- Added per-command execution diagnostics in `/api/drives` payload:
  - command status and failure reason for `smartctl`, `lsblk`, `nvme`, `hdparm`
  - mapping diagnostics for missing or unmatched `by_path` configuration
- Identified and corrected service privilege escalation blocker under systemd:
  - updated `systemd/drive-eraser.service` to `NoNewPrivileges=false`
  - fixed runtime error: `sudo: The "no new privileges" flag is set`

## v0.5 - API Stabilization, Installer Hardening, and Capability Detection Start
- Stabilized `backend/app.py` config handling:
  - added config directory resolution with fallback order
  - added `policy.json` loading for bind address and port
  - removed hardcoded `/opt/drive-eraser/config/bay_map.json` dependency from API route logic
- Verified `/api/drives` returns valid JSON locally through Flask test client
- Hardened `backend/disk_ops.py`:
  - standardized empty-field outputs and added consistent bay metadata
  - added interface detection (`nvme`, `sas`, `sata`, `unknown`)
  - added capacity detection (`lsblk -b`)
  - added capability detection fields and supported method derivation
  - aligned command path resolution with environment overrides + installer-generated command path config
- Hardened `scripts/install.sh`:
  - runtime command path discovery via `command -v`
  - safer sudoers generation/validation via temporary file + `visudo -cf`
  - virtualenv package refresh and import validation
  - writes `config/command_paths.json` for backend/runtime command alignment
- Verified updated backend module compiles and `/api/drives` includes `capabilities` + `supported_methods`

## v0.4 - Infrastructure Bring-Up and Early Backend Validation
- Established working GitHub repository and scaffolded project structure
- Validated Windows + VSCode + Git Bash development workflow
- Confirmed technician-friendly deployment should use HTTPS clone instead of personal SSH keys
- Performed first clean-server bring-up on fresh Ubuntu 26.04
- Corrected installer package assumptions (`lsblk` via `util-linux`)
- Validated and corrected privileged command paths during real install testing
  - `smartctl` confirmed under `/usr/sbin/`
  - `nvme` confirmed under `/usr/sbin/` on the test system
- Validated dedicated service-user model with `wipestation`
- Added and tested early backend discovery module: `backend/disk_ops.py`
- Confirmed 4-bay test server can be mapped correctly through `bay_map.json`
- Confirmed direct Python discovery flow reads configured bays correctly
- Began Flask `/api/drives` integration
- Identified current bring-up blockers in service/API layer:
  - /api/drives loads but is missing all the drive info. suspect its not updating after service restart
  - service assumptions around logs directory / virtual environment / permissions

## v0.3 - Erase Logic & Policy Finalized
- Defined full lifecycle from detection to certification
- Implemented method-aware verification rules
- Added structured post-erase marker design
- Marker placed at beginning of disk (LBA 0 region)
- Defined marker states (valid, stale, invalid, none)
- Established soft-stop policy for degraded drives
- Added technician override capability for wipe method
- Defined retry behavior for crypto erase failure (prompt → block erase)
- Added optional pre-wipe spot check (default ON, configurable)
- Clarified marker failure as warning (not failure)

## v0.2 - UI Finalized
- Standardized 8-bay layout (4x2 grid)
- Added consistent safety banners
- Unified banner styling
- Fixed NVMe labeling issues
- Improved technician clarity and flow
- Added certificate viewing concept
- Added ticket number to erase flow
- Clarified verification pass/fail styling

## v0.1 - Initial Prototype
- Basic dashboard layout
- SMART data display
- Erase modal
- Certificate view

## Notes
- The project has now moved from pure design into real deployment and backend validation
- The current 4-bay test server is a proving ground, not a change in final product direction
- Major focus remains safety, auditability, rebuildability, and technician confidence