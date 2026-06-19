# Roadmap: Future Enhancements

This document outlines planned future enhancements and features for the Drive Eraser project.

---

## Live Testing Fixes & Post-Wipe Verification Resilience

**Status**: In Progress
**Priority**: High
**Related Plan**: `c:\Users\BStra\.windsurf\plans\live-testing-fixes-53d81a.md`

### Summary
A coordinated set of fixes and improvements driven by live testing observations. The work covers UX confusion (secure-mode badge, confirmation labels, sanitize button visibility), post-wipe reliability (blockdev retry after transient bus resets, marker write tolerance), and operational policy exposure (admin UI for system configuration).

### Key Deliverables
- Secure-mode badge reflects `strict_audit_mode` instead of `wipe_passphrase`
- Post-wipe `blockdev --getsize64` retry policy with distinct `drive_detached_post_wipe` error code
- System Configuration admin panel exposing station ID, Slack webhook, crypto verification mode, discovery workers, max concurrent wipes, and blockdev retry settings
- Pre-wipe failure detection / fail-fast health gate
- Improved overwrite marker diagnostics and deep-dive process review

### Documentation
- `api-contract.md`, `lifecycle.md`, `test-plan.md`, `troubleshooting.md`, `SOP_technician_guide.md`, `change-log.md`, `CODE_MAP.md`

---

## Offline Queueing for Air-Gapped Deployments

**Status**: Future Enhancement
**Priority**: Medium
**Use Case**: Air-gapped deployments where network connectivity is unavailable or restricted

### Description
In air-gapped environments (e.g., secure facilities, SCADA systems, isolated networks), the Drive Eraser server may not have network access to external systems. Currently, the system requires real-time network connectivity for certain operations (e.g., webhook notifications, certificate distribution).

### Proposed Implementation
- Implement a local job queue that persists erase requests to disk
- Allow technicians to queue jobs without immediate network connectivity
- Add a "sync" mechanism to export queued jobs and certificates to portable media (USB, external drive)
- Support batch import/export of job records and certificates for air-gapped audit trails
- Add offline mode detection and UI indicators when network is unavailable

### Technical Considerations
- Queue persistence in SQLite database (already implemented for job history)
- Export format: JSON bundles with job metadata, certificates, and audit trails
- Import format: Validation and merge of external job records
- Conflict resolution: Handle duplicate job IDs when syncing between systems
- Security: Validate and sign exported bundles to prevent tampering

### Dependencies
- None (can be implemented independently)
- Would enhance existing job persistence infrastructure

---

## Additional Security Hardening

**Status**: Future Enhancement
**Priority**: High
**Related**: See docs/SECURITY_DEVIATIONS.md entry for systemd NoNewPrivileges

### Proposed Enhancements
- Enable `NoNewPrivileges=true` in systemd service file after testing
- Add `PrivateDevices=true` to restrict access to hardware devices
- Add `ProtectHome=true` to restrict access to user home directories
- Add `RestrictAddressFamilies=AF_UNIX AF_INET` to limit socket families
- Add `SystemCallFilter=@system-service` to restrict system calls
- Implement AppArmor or SELinux profiles for additional containment

---

## Additional Future Enhancements

This section will be updated as new enhancement requests are identified and prioritized.
