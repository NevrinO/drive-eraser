# Operations Guide

This guide covers day-to-day operations, service management, common tasks, and troubleshooting for the Drive Wipe Station.

**Related docs**:
- [deployment.md](deployment.md) — Installation, updates, and rollback
- [admin-guide.md](admin-guide.md) — Admin panel configuration
- [lifecycle.md](lifecycle.md) — Drive wipe lifecycle and states
- [enclosure-mapping-guide.md](enclosure-mapping-guide.md) — Enclosure and bay mapping setup

---

## 1. Service Operations

### Start / Stop / Restart

```bash
sudo systemctl start drive-eraser
sudo systemctl stop drive-eraser
sudo systemctl restart drive-eraser
```

### Check Status

```bash
sudo systemctl status drive-eraser --no-pager -l
```

### View Logs

```bash
# Live follow
sudo journalctl -u drive-eraser -f

# Last 200 lines
sudo journalctl -u drive-eraser -n 200 --no-pager

# Since specific time
sudo journalctl -u drive-eraser --since "2024-01-01 12:00:00"
```

### Application Logs

Application-level logs are stored in `/opt/drive-eraser/data/logs/`:
- `app.log` — Main application log
- `discovery_diag.log` — Discovery diagnostic log (when enabled in policy)
- `active/` — Logs for active wipe jobs
- `failed/` — Logs for failed wipe jobs

---

## 2. Configuration Paths

| Path | Purpose |
|------|---------|
| `/opt/drive-eraser/config/policy.json` | Operational policy (verification, audit, health gate, triage) |
| `/opt/drive-eraser/config/bay_map.json` | Bay-to-physical-drive mapping |
| `/opt/drive-eraser/config/layout_templates.json` | Physical bay layout templates |
| `/opt/drive-eraser/config/command_paths.json` | Resolved paths to system utilities (smartctl, hdparm, etc.) |
| `/opt/drive-eraser/config/drive_models.json` | Drive model risk profiles |
| `/opt/drive-eraser/data/wipes.db` | SQLite wipe history database |
| `/opt/drive-eraser/data/certs/` | Generated certificates |
| `/opt/drive-eraser/data/logo.png` | Custom certificate logo (if uploaded) |
| `/opt/drive-eraser/data/logs/` | Application and job logs |
| `/opt/drive-eraser/backups/` | Config backups from update script |

### Editing Configuration

Config files can be edited directly on the server or via the admin panel UI. After editing `policy.json` or `bay_map.json` directly, restart the service for changes to take effect:

```bash
sudo systemctl restart drive-eraser
```

Changes made via the admin panel UI are applied immediately without requiring a restart.

---

## 3. API Smoke Checks

### Discovery

```bash
curl -sS http://127.0.0.1:5000/api/drives | jq '.[] | {bay, device, model, serial, present}'
```

### Service Status

```bash
curl -sS http://127.0.0.1:5000/api/status | jq .
```

### Erase Job Lifecycle

Start a wipe:
```bash
curl -sS -X POST http://127.0.0.1:5000/api/erase/start \
  -H 'Content-Type: application/json' \
  -d '{"technician":"smoke","ticket_number":"TEST-1","bays":["bay2"],"confirmation_text":"erase BAY 2"}'
```

Check job status:
```bash
curl -sS http://127.0.0.1:5000/api/erase/jobs/<job_id> | jq .
```

### Admin Endpoints

```bash
# System metrics
curl -sS http://127.0.0.1:5000/api/admin/metrics | jq .

# Current policy
curl -sS http://127.0.0.1:5000/api/admin/policy | jq .

# Bay map
curl -sS http://127.0.0.1:5000/api/admin/bay-map | jq .

# Master slot map (hardware topology)
curl -sS http://127.0.0.1:5000/api/admin/master-slot-map | jq .
```

---

## 4. Common Tasks

### Confirm Bay Mapping

1. Go to the System Administration tab (Tab 4) in the web UI
2. Check the Enclosure Management section for configured enclosures
3. Insert a test drive into each bay and verify it appears in the correct slot
4. Use Auto-Detect in the Interactive Bay Mapping section as a fallback

### Align Sudo Command Paths

If disk commands fail with permission errors:

1. Confirm sudoers file is valid:
```bash
sudo visudo -cf /etc/sudoers.d/drive-eraser
```

2. Confirm command paths in `/opt/drive-eraser/config/command_paths.json` match actual binary locations:
```bash
cat /opt/drive-eraser/config/command_paths.json
```

3. Re-run the update script to re-resolve paths and refresh sudo rules:
```bash
sudo bash scripts/update.sh
```

### Export Wipe Ledger

Via the admin panel (CSV Export button) or via API:
```bash
curl -sS -O -J http://127.0.0.1:5000/api/admin/export-csv
```

### Download Support Bundle

Via the admin panel (Support Bundle button) or via API:
```bash
curl -sS -O -J http://127.0.0.1:5000/api/admin/support-bundle
```

### Kill All Jobs (Emergency Stop)

Via the admin panel (Kill All Jobs button) or via API:
```bash
curl -sS -X POST http://127.0.0.1:5000/api/admin/jobs/kill-all
```

This checks each running job's hardware status before killing. Jobs actively sanitizing at the hardware level are skipped (cannot be safely interrupted).

### Access Documentation

- Click the **Help** button in the UI header for in-app documentation access
- Documentation files are in `/opt/drive-eraser/docs/` on the server
- The `/docs/` route serves documentation through the web UI

---

## 5. Troubleshooting by Symptom

### Service Won't Start

**Symptoms**: `systemctl status drive-eraser` shows failed

**Checks**:
1. Review logs: `sudo journalctl -u drive-eraser -n 200 --no-pager`
2. Validate config JSON files for syntax errors
3. Verify venv and dependencies are present

**Fixes**:
- Re-run update script: `sudo bash scripts/update.sh`
- Correct malformed config files and restart service

### Permission Errors on Disk Commands

**Symptoms**: Job fails with sudo/permission command errors

**Checks**:
1. Confirm sudoers file: `sudo visudo -cf /etc/sudoers.d/drive-eraser`
2. Confirm command paths in `config/command_paths.json` are correct
3. Confirm service unit allows controlled sudo model

**Fixes**:
- Run update to regenerate sudoers and command paths
- Ensure required utilities are installed

### /api/drives Missing Device Details

**Symptoms**: Bay shows present false or missing device unexpectedly

**Checks**:
1. Verify bay mapping configuration in the System Administration tab
2. Use Auto-Detect to automatically map physical bays to device paths
3. Confirm `/dev/disk/by-path/` entries exist for attached drives
4. Inspect `diagnostics.mapping` and command diagnostics from `/api/drives`

**Fixes**:
- Use the System Administration tab to correct bay mapping values
- Re-seat drive and re-check by-path links
- Click Help button in header for bay mapping guidance

### Incorrect Interface Classification

**Symptoms**: Drive protocol classification seems wrong

**Checks**:
1. Compare `/api/drives` `interface_type` to `smartctl -i` output
2. Confirm smart data is accessible under service execution
3. Use fallback behavior only when smart data unavailable

**Fixes**:
- Ensure smartctl works via service sudo model
- Validate device-specific smart output format on that hardware

### Erase Job Stuck in Running

**Symptoms**: Job remains `running` for longer than expected

**Checks**:
1. Poll job endpoint and inspect `result` growth
2. Inspect journal logs during run
3. Validate command type and drive size expectations

**Fixes**:
- Wait for long overwrite jobs when expected
- If clearly hung, investigate command-level failure in logs and restart workflow safely

### Frontend Tracking Timeout

**Symptoms**: UI reports tracking timed out

**Meaning**: Frontend polling stopped; backend job may still be active.

**Recovery**:
1. Use job ID in the UI tracking field and click refresh
2. Or call job endpoint directly: `curl -sS http://127.0.0.1:5000/api/erase/jobs/<job_id>`

### Exit Code 5 during SATA Sanitize (Link Drops)

**Symptoms**: Command initiation records exit code 5 / "Input/output error"

**Root Cause**: Modern SATA SSDs frequently drop or reset their SATA bus link immediately upon accepting an asynchronous firmware sanitization command. The command-line utility records this as an I/O error.

**Mitigation**: This is normal and expected. The backend delays status checking by 5 seconds post-initiation and tolerates up to 60 seconds of consecutive query errors. If a subsequent status check retrieves `sata_sanitize_still_in_progress`, the backend recognizes the initiation was successful and monitors it to completion.

### UI Hangs or Freezes After Batch Initiation

**Symptoms**: Initiating a wipe causes the browser interface to hang

**Root Cause**: Concurrent physical disk scans on active running devices can block the SATA bus/controller thread.

**Mitigation**: The backend skips physical probes on active `running_devices`, rendering cached values instead. Frontend form submission is decoupled from the polling refresh loop.

### Drive Shows "Written Since Wipe" After Overwrite

**Symptoms**: A drive just overwritten with zeroes shows marker status `written_since_wipe` instead of `pristine_*`

**Checks**:
1. Check `data/logs/active/` or `data/logs/failed/` for the job log
2. Look for `data_written_at_wipe` (captured before marker write) and post-marker `data_written_raw` value
3. Note the drive interface (SATA: SMART attr 241; SAS: scsi_error_counter_log; NVMe: data_units_written)

**Common causes**:
- SMART write counters have coarse granularity; a small marker write can push the reported counter past the 2 MiB tolerance
- Drive firmware may cache SMART data, so the post-marker read returns a pre-wipe value

**Fixes**:
- Wait for the next discovery cycle and re-check the marker status
- If the issue persists, increase diagnostic logging around the marker write and compare raw values before changing tolerances

### Post-Wipe Verification Failed with `drive_detached_post_wipe`

**Symptoms**: Job fails with error `drive_detached_post_wipe` during verification

**Meaning**: The drive temporarily dropped off the bus after erase. The backend retried `blockdev --getsize64` but the device never reappeared.

**Fixes**:
- Reseat the drive or the SAS/SATA cable
- Try a different bay/port
- Increase `blockdev_post_wipe_retry_delay` in policy to give the controller more time
- If the drive repeatedly drops off, consider it a hardware failure

### Secure Mode Badge Does Not Match Strict Audit Setting

**Symptoms**: Header badge shows "SECURE MODE" even though strict audit is disabled, or vice versa

**Checks**:
1. Open `/api/status` in the browser and confirm `strict_audit_mode` value
2. Verify the badge rendering code in `frontend/auth.js` uses `strict_audit_mode`, not `passphrase_enabled`

**Fixes**:
- The badge should reflect `strict_audit_mode`. `wipe_passphrase` is only used for marker signing
- Update via the System Administration panel or edit `config/policy.json`

### Drives Not Detected in Expected Slots

**Symptoms**: Drive shows in wrong physical slot or not detected at all

**Checks**:
1. Verify enclosure configuration in System Administration panel
2. Check that PCI controller address matches actual hardware: `lspci | grep -i sas`
3. Confirm expander SAS address is correct for multi-expander setups
4. Inspect master map: `GET /api/admin/master-slot-map`
5. Verify slot type matches physical connection (SAS expander vs direct)
6. Check sysfs: `ls -la /sys/class/sas_phy/` for SAS, `ls -la /sys/bus/pci/slots/` for NVMe

**Fixes**:
- Re-run auto-mapping with correct controller selection
- Manually override hardware identifiers if auto-detection fails
- Force master map refresh: `GET /api/admin/master-slot-map?force_refresh=true`

### Hybrid Slot NVMe Mapping Incorrect

**Symptoms**: NVMe drives in hybrid bays not detected or sequence is wrong

**Checks**:
1. Verify template has correct `hybrid_slots` array
2. Check starting PCIe NVMe slot selected during enclosure creation
3. Confirm `/sys/bus/pci/slots/` folder numbers match expected sequence

**Fixes**:
- Re-create enclosure with correct starting NVMe slot number
- Manually edit NVMe hardware identifiers for affected slots

### Multipath Device Shows as Two Separate Drives

**Symptoms**: Same physical drive appears twice with different device paths

**Checks**:
1. Check if drive is dual-ported SAS: `multipath -ll`
2. Verify `/sys/block/<dev>/holders` contains dm-* entries
3. Confirm MPIO resolution is working in discovery logs

**Fixes**:
- Verify `device-mapper-multipath` package is installed
- Verify multipathd service is running: `systemctl status multipathd`
- Check `/etc/multipath.conf` configuration
- Restart multipathd: `sudo systemctl restart multipathd`

### Enclosure Creation Fails with Invalid Controller

**Symptoms**: Cannot create enclosure, controller selection shows no options

**Checks**:
1. Verify master map is populated: `GET /api/admin/master-slot-map`
2. Check that sysfs directories are accessible
3. Confirm HBA hardware is detected by OS

**Fixes**:
- Rescan PCI bus: `echo 1 > /sys/bus/pci/rescan`
- Check HBA driver is loaded: `lsmod | grep <driver_name>`
- Verify physical HBA is seated properly

### Stale Drive Detection After Hot-Swap

**Symptoms**: Old drive information persists after hot-swap; new drive not detected

**Checks**:
1. The udev listener should automatically detect hot-swap events
2. If stale data persists, force master map refresh: `GET /api/admin/master-slot-map?force_refresh=true`
3. Wait for cache TTL to expire (1 hour for topology caches)
4. Re-seat drive and wait for kernel detection

### Multi-Enclosure Collision

**Symptoms**: Two enclosures show same drives; slot mapping conflicts

**Checks**:
1. Verify each enclosure has unique expander SAS address
2. Confirm PCI controllers are different for each enclosure (or expander addresses differ)
3. Check display order doesn't cause confusion

**Fixes**:
- Ensure expander SAS addresses are correctly identified during enclosure creation
- Use different PCI controllers for physically separate enclosures
- Re-configure enclosures with correct hardware bindings

### Health Gate Rejects Healthy Drive

**Symptoms**: Drive is rejected by the pre-wipe health gate despite appearing healthy

**Checks**:
1. Review the health gate rejection reason in the job details
2. Check the drive's SMART attributes via the deep-dive modal
3. Verify `prewipe_health_gate_*` thresholds in `policy.json` are not too aggressive
4. Check for SAS-specific overrides (grown defect list, background scan status, uncorrectable errors)

**Fixes**:
- Adjust health gate thresholds via the System Administration panel
- If the drive is genuinely healthy, override the soft stop and proceed with wipe (logged in audit trail)
- Disable the health gate temporarily via `prewipe_health_gate_enabled: false` in policy

### SMART Self-Test Fails to Start

**Symptoms**: SMART self-test returns error or does not progress

**Checks**:
1. Verify the device path is correct and the drive is present
2. Check for concurrent test locks: only one test per device at a time
3. Confirm `smartctl` can communicate with the drive: `sudo smartctl -i /dev/sdX`
4. Check if the drive supports the requested test type (short/long/conveyance)

**Fixes**:
- Wait for any existing test to complete before starting a new one
- Try a different test type (short instead of long)
- Re-seat the drive if smartctl cannot communicate

### Triage Report Shows Incorrect Recommendations

**Symptoms**: Triage report recommends wrong action (Wipe/Scratch/Destroy) for a drive

**Checks**:
1. Review the drive's health score in the detail modal
2. Verify triage thresholds in `policy.json` under `triage_thresholds`
3. Check for SAS-specific attributes affecting the score (grown defects, NME, sticky LBA)
4. Verify drive model risk profiles in `config/drive_models.json` are not overriding thresholds incorrectly

**Fixes**:
- Adjust triage thresholds via the System Administration panel (Tab 4)
- Add or correct drive model profiles for vendor-specific behavior
- Use the health score explainer (if available) to understand the penalty breakdown

### WebSocket Connection Fails

**Symptoms**: UI does not update in real-time; requires manual refresh

**Checks**:
1. Check browser console for WebSocket connection errors
2. Verify the backend is running Flask-SocketIO
3. Confirm the WebSocket endpoint is accessible (same host/port as the web UI)
4. Check for proxy or firewall blocking WebSocket upgrade requests

**Fixes**:
- The frontend automatically falls back to polling — functionality is preserved
- Restart the service if SocketIO is not initializing
- Check `journalctl` for SocketIO-related errors
- If behind a reverse proxy, ensure WebSocket upgrade headers are forwarded

### Drive Model Profile Not Applied

**Symptoms**: Drive model thresholds from `drive_models.json` not affecting health score

**Checks**:
1. Verify the drive's vendor/product/revision matches the key in `drive_models.json`
2. Check `GET /api/admin/drive-models` to confirm the profile is loaded
3. Compare the drive's `smartctl -i` output with the profile key format

**Fixes**:
- Ensure the key format matches exactly: `VENDOR,PRODUCT,REVISION` (comma-separated, no spaces)
- Add a new profile entry for unrecognized drive models
- Restart the service after editing `drive_models.json` directly

### Can't Access Documentation

**Symptoms**: Documentation links return 404 or errors

**Checks**:
1. Confirm backend is running and serving `/docs/` route
2. Check that documentation files exist in `/opt/drive-eraser/docs/`

**Fixes**:
- Click the Help button in the UI header for in-app documentation access
- Access documentation directly from `/opt/drive-eraser/docs/`
- Restart the service if the `/docs/` route is not responding

---

## 6. Escalation

### Quick Evidence Bundle

Collect before escalating:
- Failing request payload (redacted if needed)
- Response body/status code
- `journalctl` excerpt around event time
- Relevant bay map entries
- `/api/drives` snapshot for affected bay

### Support Bundle

Download a full diagnostic bundle via the admin panel (Support Bundle button) or API:

```bash
curl -sS -O -J http://127.0.0.1:5000/api/admin/support-bundle
```

The bundle includes:
- Hardware environment (`lsblk -J`, `lshw` output)
- Per-device `smartctl -x` output (up to 50 devices, collected in parallel)
- System metrics (hostname, uptime, CPU, RAM, disk space)
- Redacted `policy.json` (passphrases and webhook URL redacted)
- Application logs (`app.log`, `discovery_diag.log`)
- Failed job logs
- Diagnostic snapshot

### Escalation Triggers

Escalate to engineering if:
- Service fails to start reliably after update and rollback
- Discovery payload is invalid/unusable across all bays
- Erase jobs fail due to systemic command/permission issues not resolved by update
- Frontend cannot track jobs after service restart
- Hardware-level sanitize operations leave drives in indeterminate state
