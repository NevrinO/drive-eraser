# Runbook

## Purpose
Operational commands and procedures for running and supporting Drive Wipe Station on a server.

## Service Operations

### Check status
```bash
sudo systemctl status drive-eraser
```

### Start/stop/restart
```bash
sudo systemctl start drive-eraser
sudo systemctl stop drive-eraser
sudo systemctl restart drive-eraser
```

### View logs
```bash
sudo journalctl -u drive-eraser -n 200 --no-pager
sudo journalctl -u drive-eraser -f
```

## Configuration Paths
- Runtime config directory: `/opt/drive-eraser/config`
- Bay map: `/opt/drive-eraser/config/bay_map.json`
- Policy: `/opt/drive-eraser/config/policy.json`
- Command paths: `/opt/drive-eraser/config/command_paths.json`

## API Smoke Checks

### Discovery and Health Check
Run a quick query to confirm the JSON payload contains our new `health_score` and byte-accurate traffic parameters:
```bash
curl -sS http://127.0.0.1:5000/api/drives | jq '.[].health_score'
```

### Checking Byte-Accurate Traffic Fields
```bash
curl -sS http://127.0.0.1:5000/api/drives | jq '.[].smart | {data_read_bytes, data_written_bytes}'
```

### Start erase job
```bash
curl -sS -X POST http://127.0.0.1:5000/api/erase/start \
  -H 'Content-Type: application/json' \
  -d '{
    "technician":"test",
    "ticket_number":"INC-1001",
    "bay":"bay3",
    "confirmation_text":"erase bay3"
  }'
```

### Poll erase job
```bash
curl -sS http://127.0.0.1:5000/api/erase/jobs/<job_id>
```

## Job State Semantics
- `queued`: accepted, waiting to run
- `running`: erase method command running
- `completed`: command finished successfully
- `failed`: command failed or unsupported for interface/method

## Install and Update

### Fresh install
```bash
sudo bash scripts/install.sh
```

### Update existing install
```bash
sudo bash scripts/update.sh
```

## Common Operational Tasks

### Confirm bay mapping resolves
1. Check `by_path` entries in `bay_map.json`.
2. Call `/api/drives`.
3. Confirm each wipe bay has:
   - `present: true` when populated
   - `device` resolved
   - `diagnostics.mapping.ok: true`

### Confirming Sudo command path alignment
If any command-line disk tool updates or changes paths (e.g., `nvme` moving on a new host build):
1. Re-run `scripts/update.sh` to automatically regenerate `/opt/drive-eraser/config/command_paths.json` and its corresponding secure `sudoers` rule file.
```bash
sudo bash scripts/update.sh
```

### Verifying GitIgnore Local DB Protections
Ensure that the local SQLite database and write-ahead logs do not accidentally get staged to your development branch:
```bash
git status --ignored
```
You should see `/data/wipes.db`, `/data/wipes.db-shm`, `/data/wipes.db-wal`, and `/data/certs/` contents under the ignored files list.

### Verify interface classification
Use `/api/drives` and confirm `interface_type` is based on smart data behavior for the inserted drive.

## Safety Notes
- Never wipe locked, OS, or reserved bays.
- Always require typed confirmation format: `erase <bay>`.
- Treat transport details as troubleshooting context, not erase-method policy authority.

## Escalation Trigger
Escalate if any of the following occur:
- service cannot start after install/update
- all destructive commands return permission errors
- bay mapping is unresolved for known good `by_path` values
- jobs remain `running` unexpectedly long without command output growth