# Change Log

## v0.27 - Live Testing Fixes & Post-Wipe Verification Resilience (Planned)
- **UX / Visibility**:
  - Secure-mode badge now reflects `strict_audit_mode` instead of `passphrase_enabled`
  - Sanitize Mode OFF state is visually distinct so users do not miss it
  - Triage comments no longer overflow their rows
  - Drives wiped without a marker now show "SANITIZED (NO MARKER)" instead of looking unprocessed
  - Wipe confirmation text uses bay display labels instead of internal IDs
  - DD/overwrite progress now includes an estimated time remaining
- **Reliability**:
  - Post-wipe `blockdev --getsize64` failures are retried with configurable policy (`blockdev_post_wipe_retries`, `blockdev_post_wipe_retry_delay`)
  - Detached drives after a wipe fail with a distinct `drive_detached_post_wipe` error code
  - Logo upload debugging improved with clearer logging around the integrity check
  - Overwrite marker "written since wipe" false positives are diagnosed with better SMART logging
- **Administration**:
  - System Configuration panel exposes operational policy settings: station ID, Slack webhook, crypto verification mode, discovery workers, max concurrent wipes, and blockdev retry settings
  - New policy keys added to `DEFAULT_POLICY` and `POLICY_SCHEMA`
- **Documentation**:
  - Updated `api-contract.md`, `lifecycle.md`, `test-plan.md`, `operations.md`, `SOP_technician_guide.md`, and `change-log.md` to cover the above changes

## v0.26 - Comprehensive Security Remediation & Hardening
- **Critical Security Fixes**:
  - Added configuration validation for strict audit mode requiring non-empty wipe passphrase
  - Implemented configurable CORS origins with local network support
  - Added Content Security Policy (CSP) headers and meta tags
  - Implemented SameSite cookie attribute for CSRF protection
  - Added SQL column validation with allowlist for DEFAULT values to prevent injection
  - Added placeholder device path detection with warning in bay map loading
  - Implemented JSON schema validation for policy.json configuration
- **Web Security**:
  - Implemented Flask-Limiter with per-endpoint rate limiting
  - Added dynamic resource limits in systemd service based on system hardware
- **Concurrency & Signal Handling**:
  - Added signal handlers (SIGTERM/SIGINT) for long-running operations
  - Implemented device-level locking for verification operations
  - Added graceful shutdown with subprocess termination
  - Fixed race condition in job status updates
- **Subprocess Security**:
  - Added explicit `shell=False` to all subprocess.run() calls
- **Frontend Improvements**:
  - Fixed memory leaks by adding event listener cleanup
  - Replaced infinite polling loops with setInterval and beforeunload cleanup
  - Replaced document.write() with DOM manipulation
  - Implemented centralized error handling utility
  - Added keyboard navigation support and ARIA live regions
  - Added focus trapping in modals and skip-to-content link
- **Certificate & Configuration**:
  - Added bad sector detection and logging in certificate generation
  - Implemented certificate retention policy with cleanup function
  - Made logo size limit configurable
  - Added bulk certificate batch size configuration
  - Moved KDF iteration count to shared constants
- **Documentation**:
  - Updated API contract with all 29 endpoints
  - Corrected docs/CODE_MAP.md to reflect actual modular admin structure
  - Completed lifecycle.md documentation with all states and diagrams
  - Added comprehensive documentation links to README.md
  - Documented systemd NoNewPrivileges hardening as future enhancement
- **Testing Infrastructure**:
  - Created test suite structure with pytest
  - Added unit tests for database SQL injection prevention
  - Added integration tests for API endpoints

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
- **Active Query Lockup Bypass**: Configured both `/api/drives` and `/api/erase/start` to compile and pass the list of `running_devices` to `discover_drives()`. This bypasses physical drive scanning on busy devices, preventing API hangs when operators or frontend refresh timers poll the system during active runs.
- **Wiping Metadata Retention**: Restored original drive metadata (`serial`, `model`, and capacity) to `/api/drives` from the active job's in-memory data cache when a bay is in a `RUNNING` or `QUEUED` state. This prevents UI card blackouts (such as "Generic Drive") while a wipe is in progress.
- **Frontend Optimization**: Removed the blocking UI `await` call on `loadDrives()` inside the form submit listener. This makes the user-facing confirmation alert instantaneous while refreshing card statuses in the background.

## v0.23 - Multi-Vector Health, Accurate SAS Bad Sectors & SSD Traffic Scaling
- Implemented Multi-Vector Health Scoring:
  - Differentiated bad sector rules: strict raw counts on mechanical HDDs, and reserve depletion scaling (Available Spare) on SSDs.
  - Implemented mutually exclusive SSD flash wear vs. HDD mechanical age (Power-On Hours + workload Full Drive Writes) to prevent double-dipping.
  - Added gentle operational runtime decay for SSD controllers above 40,000 POH.
  - Leveraged `smartctl` exit-status Bit 3 and Bit 4 for pre-failure overrides, while explicitly ignoring Bit 5 (usage limits).
- Resolved SSD Read/Write Traffic Under-reporting:
  - Added dynamic attribute name parsing (e.g., `Host_Writes_32MiB`, `Host_Writes_GiB`) to map sector blocks correctly.
  - Exposed pre-calculated `data_written_bytes` and `data_read_bytes` in API response payload.
- Fixed SAS Bad Sector Reporting:
  - Replaced soft background ECC fallbacks with direct, strict parsing of `scsi_grown_defect_list` (G-list).

---

> **Full version history**: For versions prior to v0.23, see `git log --oneline` in the repository. The change log was trimmed to remove stale entries referencing the monolithic codebase structure and deleted documentation files (`handoff_prompt.md`, `current_state.md`).