# Project Decisions (Critic-Actor Protocol)

**Purpose**: This file documents intentional deviations from lessons-learned rules per the critic-actor protocol. These are deliberate architectural or security decisions that bypass standard patterns for specific reasons. The Critic Agent checks this file before flagging changes as flaws.

**For general architectural decisions**, see `docs/ARCHITECTURE.md`.

## [2025-06-07] - Weak Default LAN Passphrase
- **Deviation**: Using weak default credentials (lan_passphrase: "change_me") in config/policy.json instead of enforcing strong passphrase generation
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
- **Reason**: Requires separate security hardening effort and testing
- **Context**: The systemd service file (systemd/drive-eraser.service) currently has NoNewPrivileges=false on line 30. Setting NoNewPrivileges=true would prevent the process from gaining new privileges through setuid/setgid binaries or file capabilities, which is a security best practice. However, enabling this requires:
  1. Testing to ensure the application doesn't require privilege escalation for any operations
  2. Verifying that all subprocess calls (nwipe, smartctl, etc.) work correctly without privilege escalation
  3. Potentially adjusting file permissions and capabilities for required binaries
  Additional hardening directives to consider for future implementation:
  - PrivateDevices=true (restrict access to hardware devices)
  - ProtectHome=true (restrict access to user home directories)
  - RestrictAddressFamilies=AF_UNIX AF_INET (limit socket families)
  - SystemCallFilter=@system-service (restrict system calls)
  This is deferred to a dedicated security hardening phase rather than the current remediation effort.

## [2025-06-08] - Deferred Blueprint Registration to Break Circular Imports
- **Deviation**: Blueprint registration moved from app_config.py module level to app.py
- **Reason**: Required to break circular import dependency during test collection
- **Context**: The circular import chain is: test_job_management → job_management → certificates → app_config → register_blueprints → routes.certificate_routes → certificates (build_bulk_certificate_html). Since certificates.py is still being initialized when certificate_routes.py tries to import from it, Python raises ImportError. The proper fix is to defer blueprint registration to app.py (the Flask application entry point) rather than using lazy imports. This follows Python best practices by:
  1. Keeping all imports at module level (standard Python convention)
  2. Deferring the actual blueprint registration to runtime when the app starts
  3. Avoiding lazy imports which are a workaround rather than a proper architectural fix
  4. Making the dependency flow explicit: app_config provides the app object, app.py registers blueprints, routes use app_config exports

## [2026-06-19] - Werkzeug Development Server in Production Mode
- **Deviation**: `allow_unsafe_werkzeug=True` is passed to `socketio.run()` in `backend/app.py`, allowing Flask's built-in Werkzeug development server to run in production mode.
- **Reason**: Development and testing convenience. This is a temporary workaround after a Flask-SocketIO upgrade began raising `RuntimeError` when Werkzeug is detected in production mode.
- **Context**: The proper production replacement is **Gunicorn with a gevent or eventlet worker** (or uWSGI with gevent/Hypercorn). The current setup is acceptable for internal testing on a LAN, but the systemd service should be changed to run Gunicorn before any production deployment. The typical command would be: `/opt/drive-eraser/venv/bin/gunicorn -k gevent -w 1 --bind 0.0.0.0:5000 app:app`. This deviation should be revisited before the project is deployed to any production environment.

## [2026-06-21] - SMART Data Endpoints Without Admin Authentication
- **Deviation**: `export_smart_data` and `get_smart_details` endpoints in `backend/routes/admin_routes.py` do not require `@require_admin_auth` authentication.
- **Reason**: SMART data is not considered sensitive in this environment. It is diagnostic hardware information (model, serial, capacity, attributes, error logs, self-test logs) similar to `smartctl -x` output.
- **Context**: These endpoints expose drive diagnostic information only, not user data, credentials, or PII. In this environment, operators/technicians may need access to SMART data without requiring full admin privileges. Rate limiting (30 per minute) is in place to prevent abuse. Future Critic Agents should not flag the absence of authentication on SMART data endpoints as a security flaw.

## [2026-06-27] - Exception Details in API Error Responses (str(e))
- **Deviation**: Exception messages are returned directly to API clients via `jsonify({"error": str(e)})` or `jsonify({"error": f"... {str(e)}"})` throughout `admin_routes.py`, `drive_routes.py`, and `smart_parsing.py` (issues A34, A40, A70).
- **Reason**: The application is LAN-only with no internet exposure. Operators are also the system administrators. Exposing exception details in API responses significantly eases troubleshooting — operators can see errors directly in the browser without needing to access server logs.
- **Context**: A fix was implemented that replaced `str(e)` with generic messages ("Internal server error") and relied on server-side `logger.error()` calls for details. The user explicitly reverted this fix because the generic messages made troubleshooting more difficult for a LAN-only tool where the info disclosure risk is negligible. All exception details are also logged server-side. This decision should be revisited if the application is ever exposed to the internet or untrusted networks.

## [2026-06-25] - Deferred Cache-Invalidation Race in Background SMART Collection
- **Deviation**: Background extended SMART collection (`_process_single_drive_extended_smart` in `backend/disk_ops.py`) does not use a generation counter to prevent stale writes after cache invalidation.
- **Reason**: User explicitly deferred this work to a future pass; accepted as a known limitation at this stage.
- **Context**: The race occurs when `invalidate_drive_cache()` is called while a `smartctl -x` task is in flight. The task reads the cache at the start, performs the long-running call, and then writes the result. If the cache is invalidated between the read and the write, the stale result can remain in the cache for the remainder of the TTL. The pending-set fixes implemented on 2026-06-25 allow re-enqueueing after invalidation, but do not close the read-modify-write race. A generation counter (or per-key version) will be added in a later refactor. See `docs/ARCHITECTURE.md` and `CRITIQUE.md` for the original deferred note.
