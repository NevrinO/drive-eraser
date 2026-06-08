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
