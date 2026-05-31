# Troubleshooting

## Service Won't Start
### Symptoms
- `systemctl status drive-eraser` shows failed

### Checks
1. Review logs:
```bash
sudo journalctl -u drive-eraser -n 200 --no-pager
```
2. Validate config JSON files for syntax errors.
3. Verify venv and dependencies are present.

### Common fixes
- Re-run update script:
```bash
sudo bash scripts/update.sh
```
- Correct malformed config files and restart service.

## Permission Errors on Disk Commands
### Symptoms
- Job fails with sudo/permission command errors

### Checks
1. Confirm sudoers file exists and is valid:
```bash
sudo visudo -cf /etc/sudoers.d/drive-eraser
```
2. Confirm command paths in `/opt/drive-eraser/config/command_paths.json` are correct.
3. Confirm service unit allows controlled sudo model.

### Common fixes
- Run update to regenerate sudoers and command paths.
- Ensure required utilities are installed.

## /api/drives Missing Device Details
### Symptoms
- Bay shows present false or missing device unexpectedly

### Checks
1. Verify bay mapping configuration in the **System Administration** tab (Tab 3).
2. Use "Auto-Detect" to automatically map physical bays to device paths.
3. Confirm `/dev/disk/by-path/` entries exist for attached drives.
4. Inspect `diagnostics.mapping` and command diagnostics from `/api/drives`.

### Common fixes
- Use the System Administration tab to correct bay mapping values.
- Re-seat drive and re-check by-path links.
- Click "Help" button in header for bay mapping guidance.

## Incorrect Interface Classification
### Symptoms
- Drive protocol classification seems wrong

### Checks
1. Compare `/api/drives` `interface_type` to `smartctl -i` output.
2. Confirm smart data is accessible under service execution.
3. Use fallback behavior only when smart data unavailable.

### Common fixes
- Ensure smartctl works via service sudo model.
- Validate device-specific smart output format on that hardware.

## Erase Job Stuck in Running
### Symptoms
- Job remains `running` for longer than expected

### Checks
1. Poll job endpoint and inspect `result` growth.
2. Inspect journal logs during run.
3. Validate command type and drive size expectations.

### Common fixes
- Wait for long overwrite jobs when expected.
- If clearly hung, investigate command-level failure in logs and restart workflow safely.

## Frontend Tracking Timeout
### Symptoms
- UI reports tracking timed out

### Meaning
- Frontend polling stopped; backend job may still be active.

### Recovery
1. Use job id in the UI tracking field and click refresh.
2. Or call job endpoint directly:
```bash
curl -sS http://127.0.0.1:5000/api/erase/jobs/<job_id>
```

## Exit Code 5 during SATA Sanitize (Link Drops)
### Symptoms
- Command initiation records exit code `5` / standard error reports "Input/output error".
- The drive enters an internal sanitize state, but the host software originally crashed or skipped.

### Root Cause
- Modern SATA SSDs frequently drop or reset their SATA bus link immediately upon accepting an asynchronous firmware sanitization command. The command-line utility (e.g., `hdparm`) records this sudden link disconnection as an I/O error (`exit code 5`).

### Operational Mitigation
- This behavior is normal and expected for hardware-level sanitization. The backend has been reinforced to delay status checking by 5 seconds post-initiation and tolerate up to 60 seconds of consecutive query errors. If a subsequent status check retrieves `sata_sanitize_still_in_progress`, the backend recognizes the initiation was successful and monitors it to completion.

## UI Hangs or Freezes After Batch Initiation
### Symptoms
- Initiating a wipe causes the entire browser interface or individual buttons to hang. The success notification does not display until the wipe completes.

### Root Cause
- If active running devices are queried concurrently via physical disk scans (like `smartctl` or `dd`), the SATA bus / controller blocks the thread until the busy drive completes its cycle. This hung the `/api/drives` endpoint.

### Operational Mitigation
- The backend now skips physical probes on active `running_devices`, rendering cached values instead. Additionally, the frontend form submission has been decoupled from the polling refresh loop to make success notifications instantaneous.

## Quick Evidence Bundle for Escalation
Collect:
- failing request payload (redacted if needed)
- response body/status code
- `journalctl` excerpt around event time
- relevant bay map entries
- `/api/drives` snapshot for affected bay

## Can't Access Documentation
### Symptoms
- Documentation links return 404 or errors

### Checks
1. Confirm backend is running and serving `/docs/` route.
2. Check that documentation files exist in `/opt/drive-eraser/docs/`.

### Common fixes
- Click the **Help** button in the UI header for in-app documentation access.
- Access documentation directly from the server's `/opt/drive-eraser/docs/` folder.
- Restart the service if the `/docs/` route is not responding.