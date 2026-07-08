# Project Decisions (Critic-Actor Protocol)

**Purpose**: This file documents intentional deviations from lessons-learned rules per the critic-actor protocol. These are deliberate architectural or security decisions that bypass standard patterns for specific reasons. The Critic Agent checks this file before flagging changes as flaws.

**For general architectural decisions**, see `docs/ARCHITECTURE.md`.

## [2025-06-07] - Weak Default LAN Passphrase
- **Deviation**: Using weak default credentials (lan_passphrase: "eraser123") in config/policy.json instead of enforcing strong passphrase generation
- **Reason**: Development convenience and ease of initial setup for testing purposes
- **Context**: The default LAN passphrase is intentionally weak to facilitate quick development and testing workflows. This is a known security deviation that must be addressed during production deployment. The install.sh script and documentation should clearly instruct administrators to change this passphrase to a strong value (minimum 12 characters, mixed case, numbers, symbols) before production use. The application does not enforce passphrase strength at runtime to avoid blocking development, but production deployments must manually update this configuration.

## [2025-06-07] - Device-Level Locking Skipped for Discovery Operations
- **Deviation**: Discovery operations (discover_drives in backend/disk_ops.py) do not use device-level locks, unlike verification operations which do
- **Reason**: Discovery is read-only and locking would block concurrent verification operations unnecessarily
- **Context**: Discovery operations only read device information (SMART data, capabilities, controller info) and do not perform any destructive writes or modifications. Adding device-level locks would prevent verification operations from running concurrently with discovery, reducing system throughput. Since discovery is purely read-only, concurrent discovery calls are safe without locks. This is an intentional architectural decision to maximize parallelism for the common case where users want to verify drives while also discovering new devices.

## [2025-06-07] - Certificate Chain Validation Not Required
- **Deviation**: No certificate chain validation is implemented for data erasure certificates
- **Reason**: Certificates are standalone attestations, not part of a PKI hierarchy
- **Context**: The data erasure certificates are self-contained documents signed with HMAC-SHA256 using a shared passphrase (if configured). They are not issued by a certificate authority, do not have intermediate certificates, and are not part of a chain of trust. The signature integrity is verified by recomputing the HMAC with the known passphrase and comparing it to the stored signature value. This is a simple integrity check mechanism, not a PKI chain validation. Implementing chain validation would be unnecessary complexity for this use case.

## [2025-06-07] - systemd NoNewPrivileges Not Enabled
- **Deviation**: systemd service file has NoNewPrivileges=false instead of true
- **Reason**: Enabling NoNewPrivileges=true breaks sudo, which the application relies on for all disk operations. Requires architectural rework of how disk commands are executed.
- **Context**: `NoNewPrivileges` is a systemd security directive that, when `true`, sets the `PR_SET_NO_NEW_PRIVS` kernel flag on the process and all its children. This prevents privilege escalation via SUID/SGID binaries and file capabilities. This is a security best practice because it limits the blast radius of a compromised process.

  **Why it matters**: The app runs as `wipestation` user and calls `sudo` for every disk operation (smartctl, hdparm, nvme, sg_sanitize, dd, etc.) via sudoers rules granting `NOPASSWD` access to specific binaries. If an attacker compromises the Flask app (e.g., via a dependency vulnerability), `NoNewPrivileges=false` means they could potentially execute other SUID binaries on the system that aren't in the sudoers allowlist, exploit file capabilities on system binaries, or chain privilege escalation in ways that `NoNewPrivileges=true` would block.

  **Why it can't be trivially fixed**: `sudo` itself is a SUID binary (`/usr/bin/sudo` has the setuid bit). When `NoNewPrivileges=true` is set, the kernel ignores SUID bits — so `sudo` would fail to escalate to root, breaking every disk command in the application. The sudoers `NOPASSWD` allowlist is already a reasonable security boundary (only specific binaries, no shell access), so the risk is post-compromise privilege escalation, not direct attack.

  **Fix options** (all require significant rework):
  1. **Run the app as root** — eliminates need for sudo, but worse security posture (compromised app has full root)
  2. **Use Linux capabilities instead of sudo** — grant `CAP_SYS_RAWIO` + `CAP_SYS_ADMIN` to the python binary via `setcap`, then use `NoNewPrivileges=true`. Requires testing that all disk operations work with capabilities alone.
  3. **Use a privileged helper daemon** — a small root-running service that receives commands via Unix socket. The Flask app talks to it without needing sudo. Most secure but highest implementation effort.

  **Risk level**: Low (post-compromise only, sudoers allowlist is tight, LAN-only deployment)
  **Fix complexity**: High (requires rearchitecting disk command execution)
  **Recommendation**: Defer to v2.0.0. Current sudoers allowlist provides adequate security boundary for LAN-only tool.

  Additional hardening directives to consider alongside this fix:
  - PrivateDevices=true (restrict access to hardware devices)
  - ProtectHome=true (restrict access to user home directories)
  - RestrictAddressFamilies=AF_UNIX AF_INET (limit socket families)
  - SystemCallFilter=@system-service (restrict system calls)

## [2025-06-08] - Deferred Blueprint Registration to Break Circular Imports
- **Deviation**: Blueprint registration moved from app_config.py module level to app.py
- **Reason**: Required to break circular import dependency during test collection
- **Context**: The circular import chain is: test_job_management → job_management → certificates → app_config → register_blueprints → routes.certificate_routes → certificates (build_bulk_certificate_html). Since certificates.py is still being initialized when certificate_routes.py tries to import from it, Python raises ImportError. The proper fix is to defer blueprint registration to app.py (the Flask application entry point) rather than using lazy imports. This follows Python best practices by:
  1. Keeping all imports at module level (standard Python convention)
  2. Deferring the actual blueprint registration to runtime when the app starts
  3. Avoiding lazy imports which are a workaround rather than a proper architectural fix
  4. Making the dependency flow explicit: app_config provides the app object, app.py registers blueprints, routes use app_config exports

## [2026-06-19] - Werkzeug Development Server in Production Mode
- **Deviation**: `allow_unsafe_werkzeug=True` is passed to `socketio.run()` in `backend/app.py` (line 269) and `backend/wsgi.py` (line 12), allowing Flask's built-in Werkzeug development server to run in production mode.
- **Reason**: Development and testing convenience. Flask-SocketIO raises `RuntimeError` when Werkzeug is detected in production mode; this flag suppresses that error. The proper production replacement requires testing gevent compatibility with the SocketIO setup.
- **Context**: The app uses Flask-SocketIO's `socketio.run()` which, without a real WSGI server, falls back to Werkzeug's built-in development server. This has three real issues for production:

  1. **Single-threaded request handling** (by default): One slow request blocks all others. With SocketIO long-polling fallback, this can deadlock the UI while a wipe is running.
  2. **No worker process management**: If the process crashes, systemd restarts it (good), but there's no graceful worker recycling, zero-downtime restarts, or memory leak mitigation that Gunicorn provides.
  3. **Not hardened for network exposure**: Werkzeug's dev server was never designed to face network traffic. It lacks connection limits, slow-client timeouts, and request body size controls that production servers enforce.

  **Fix path** (all components already exist):
  1. Install gunicorn + gevent in the venv: `/opt/drive-eraser/venv/bin/pip install gunicorn gevent`
  2. Add to `requirements.txt`: `gunicorn==23.0.0` and `gevent==24.11.0`
  3. Change systemd `ExecStart` to: `/opt/drive-eraser/venv/bin/gunicorn -k gevent -w 1 --bind 0.0.0.0:5000 --worker-tmp-dir /dev/shm wsgi:app`
     - Single worker with gevent because SocketIO requires sticky sessions. Multiple workers require a message queue like Redis.
  4. Update `install.sh` generated service file to use the same ExecStart
  5. Remove `allow_unsafe_werkzeug=True` from both `backend/app.py` and `backend/wsgi.py`
  6. The `backend/wsgi.py` entry point already exists and is ready for Gunicorn deployment

  **Risk level**: Medium (stability under concurrent load + security hardening)
  **Fix complexity**: Low (wsgi.py already exists, just needs gunicorn install + systemd change + testing)
  **Recommendation**: Fix for v1.2.0. The app runs 34 concurrent wipes with SocketIO connections — Werkzeug is the weakest link. If testing under load has been stable, acceptable for v1.1.0 LAN-only deployment.

## [2026-06-21] - SMART Data Endpoints Without Admin Authentication
- **Deviation**: `export_smart_data` and `get_smart_details` endpoints in `backend/routes/admin_routes.py` do not require `@require_admin_auth` authentication.
- **Reason**: SMART data is not considered sensitive in this environment. It is diagnostic hardware information (model, serial, capacity, attributes, error logs, self-test logs) similar to `smartctl -x` output.
- **Context**: These endpoints expose drive diagnostic information only, not user data, credentials, or PII. In this environment, operators/technicians may need access to SMART data without requiring full admin privileges. Rate limiting (30 per minute) is in place to prevent abuse. Future Critic Agents should not flag the absence of authentication on SMART data endpoints as a security flaw.

## [2026-06-27] - Exception Details in API Error Responses (str(e))
- **Deviation**: Exception messages are returned directly to API clients via `jsonify({"error": str(e)})` or `jsonify({"error": f"... {str(e)}"})` throughout `admin_routes.py`, `drive_routes.py`, `smart_parsing.py`, `template_routes.py`, `discovery_routes.py`, and `certificate_routes.py` (issues A34, A40, A70).
- **Reason**: The application is LAN-only with no internet exposure. Operators are also the system administrators. Exposing exception details in API responses significantly eases troubleshooting — operators can see errors directly in the browser without needing to access server logs.
- **Context**: A fix was implemented that replaced `str(e)` with generic messages ("Internal server error") and relied on server-side `logger.error()` calls for details. The user explicitly reverted this fix because the generic messages made troubleshooting more difficult for a LAN-only tool where the info disclosure risk is negligible. All exception details are also logged server-side. This decision should be revisited if the application is ever exposed to the internet or untrusted networks.

## [2026-06-25] - Deferred Cache-Invalidation Race in Background SMART Collection
- **Deviation**: Background extended SMART collection (`_process_single_drive_extended_smart` in `backend/disk_ops.py`) does not use a generation counter to prevent stale writes after cache invalidation.
- **Reason**: User explicitly deferred this work to a future pass; accepted as a known limitation at this stage.
- **Context**: The race occurs when `invalidate_drive_cache()` is called while a `smartctl -x` task is in flight. The task reads the cache at the start, performs the long-running call, and then writes the result. If the cache is invalidated between the read and the write, the stale result can remain in the cache for the remainder of the TTL. The pending-set fixes implemented on 2026-06-25 allow re-enqueueing after invalidation, but do not close the read-modify-write race. A generation counter (or per-key version) will be added in a later refactor. See `docs/ARCHITECTURE.md` and `CRITIQUE.md` for the original deferred note.

## [2026-06-30] - DESTROY Recommendation Tint (Updated)
- **Deviation**: None — DESTROY now receives a `rec-destroy` tint (dark red background `#2a0a0a`) like other recommendation statuses.
- **Reason**: User initially requested DESTROY keep current behavior, but after clarification that DESTROY drives can show green/healthy cards (SAS verify errors with high health_score, SSD life depletion with PASSED SMART), user confirmed a visual tint is needed.
- **Context**: DESTROY is the most critical recommendation. Without a tint, operators could miss a drive marked for destruction because the card's `stateClass` is driven by operational status (healthy/completed), not recommendation. The `rec-destroy` class uses a darker red (`#2a0a0a`) than `rec-scratch` (`--color-bay-failed: #3c0f12`) to visually distinguish severity.

## [2026-07-04] - CSP style-src 'unsafe-inline' Retained for Certificate Printing — RESOLVED
- **Deviation**: ~~`style-src` in the Content-Security-Policy includes `'unsafe-inline'` rather than being restricted to `'self'` only.~~ **Resolved.**
- **Reason**: Certificate printing relied on inline styles and `<style>` blocks that were subject to the opener window's CSP. Print windows created via `window.open("", "_blank")` inherit CSP from the opener in modern browsers.
- **Context**: This deviation has been resolved. Certificate CSS was externalized to `frontend/css/certificate.css` and print window loading/error styles to `frontend/css/print-window.css`. Certificate HTML templates now use `<link rel="stylesheet" href="/css/certificate.css">` instead of `<style>` blocks. Print window HTML in `auditLedger.js` uses `<link>` with absolute URLs (`window.location.origin`) and injects a `<base>` tag into certificate HTML so relative CSS links resolve in `about:blank` windows. The `'unsafe-inline'` directive has been removed from both the CSP meta tag in `index.html` and the HTTP header in `app.py`.
