# Release Checklist

## Pre-Update
- Confirm maintenance window or operator readiness.
- Confirm recent config backup exists.
- Record current service status and running version context.
- Confirm target repo branch/commit to deploy.

## Deploy
1. Run update script:
```bash
sudo bash scripts/update.sh
```
2. Confirm script reports success.
3. Confirm service active:
```bash
sudo systemctl status drive-eraser
```

## Post-Deploy Smoke Tests
1. Discovery API:
```bash
curl -sS http://127.0.0.1:5000/api/drives
```
2. Frontend load check in browser.
3. Erase validation + job creation check:
```bash
curl -sS -X POST http://127.0.0.1:5000/api/erase/start \
  -H 'Content-Type: application/json' \
  -d '{"technician":"release","ticket_number":"REL-1","bay":"bay3","confirmation_text":"erase bay3"}'
```
4. Job status check:
```bash
curl -sS http://127.0.0.1:5000/api/erase/jobs/<job_id>
```

## Protocol Validation Targets
- SATA behavior confirmed
- SAS behavior confirmed
- NVMe behavior confirmed when hardware available

## Rollback Triggers
Rollback if any are true:
- service fails to start reliably
- discovery payload is invalid/unusable
- erase jobs fail due to systemic command/permission issues
- frontend cannot track jobs

## Rollback Steps
1. Stop service.
2. Restore previous known-good code snapshot.
3. Restore config backup if changed.
4. Re-run update/install alignment as needed.
5. Start service and re-run smoke tests.

## Sign-Off
- [ ] Service healthy
- [ ] API smoke checks pass
- [ ] Frontend smoke checks pass
- [ ] One erase job lifecycle validated
- [ ] Logs reviewed for critical errors
