# API Contract

## Base
- Service: Drive Wipe Station backend
- Content type: `application/json`

## GET /api/drives

### Response 200
Returns array of bay objects.

**Note on Sanitizing States (`RUNNING`, `QUEUED`):**
To prevent physical disk controller and SATA bus locks, the backend bypasses all physical `smartctl`, `hdparm`, and `dd` queries on active drives. During these states, the response automatically restores the drive's original metadata (`serial`, `model`, and capacity details) from the cached active job payload to prevent UI blackouts.

Representative fields per bay:
- `bay` string
- `label` string
- `role` string (`os`, `reserved`, `wipe`)
- `locked` boolean
- `configured_by_path` string|null
- `resolved_by_path` string|null
- `present` boolean
- `device` string|null
- `serial` string|null (restored from job cache if busy)
- `model` string|null (restored from job cache if busy)
- `status` string
- `interface_type` string (`nvme`, `sata`, `sas`, `unknown`)
- `capacity_bytes` number|null
- `health_score` number (0..100)
- `capabilities` object
- `supported_methods` string[]
- `diagnostics` object
- `smart` object:
  - `temperature` number|null
  - `reallocated_sectors` number|null
  - `reallocated_normalized` number|null
  - `reallocated_threshold` number|null
  - `pending_sectors` number|null
  - `power_on_hours` number|null
  - `power_on_days` number|null
  - `interface_errors` number|null
  - `data_read_raw` number|null
  - `data_read_bytes` number|null
  - `data_written_raw` number|null
  - `data_written_bytes` number|null
  - `raw` string (raw smartctl output; null or skipped if busy)

## POST /api/erase/start
Starts validated asynchronous erase job.

### Request body
- `technician` string required
- `ticket_number` string required
- `bays` string[] required
- `confirmation_text` string required, format: `erase <bay>` or `erase <count> drives`
- `methods` object optional (map of bay IDs to selected wipe methods)

### Success 202
```json
{
  "status": "accepted",
  "message": "started 1 concurrent wipe process(es)",
  "jobs": [
    {
      "id": "uuid",
      "friendly_id": null,
      "status": "queued",
      "created_at": "ISO-8601",
      "technician": "...",
      "ticket_number": "...",
      "bay": "bay3",
      "device": "/dev/sdX",
      "method": "overwrite",
      "recommended_method": "overwrite",
      "supported_methods": ["overwrite"],
      "interface_type": "sata",
      "serial": "...",
      "model": "..."
    }
  ]
}
```

### Error responses
- `400` invalid/missing request data
- `403` protected bay or forbidden method override
- `404` bay not found
- `409` no drive present or no usable method
- `500` internal error

## GET /api/erase/jobs/<job_id>
Returns job state and execution result.

### Success 200
*(Payload shape contains verification and certificate attributes)*

## GET /api/erase/history
Returns recent persisted erase jobs.

*(Payload shape contains historical lists of completed jobs)*

## GET /api/certificates/<job_id>
Returns certificate payload for a completed job.

*(Payload shape accepts query param `?format=html` to fetch plain HTML files)*

## POST /api/auth/verify
Validates network access passphrase and sets secure browser session cookie.

### Request body
- `passphrase` string required

### Success 200
Returns a secure `HTTP-Only` cookie named `admin_session`.
```json
{
  "status": "authenticated"
}
```

### Error 401
```json
{
  "error": "Invalid passphrase"
}
```

## GET /api/admin/metrics
Returns real-time host hardware diagnostics (Disk space, RAM, CPU load, system uptime).

### Success 200
```json
{
  "disk_pct": 2.2,
  "disk_str": "11 GB / 944 GB",
  "ram_pct": 13.1,
  "cpu_pct": 1.2,
  "uptime": "5h 5m",
  "ip_address": "192.168.2.111"
}
```

## POST /api/admin/test-webhook
Dispatches an immediate, timestamped connectivity test alert to the Slack Webhook URL defined in `policy.json`.

### Success 200
```json
{
  "status": "success",
  "message": "Test webhook dispatched successfully."
}
```

### Error 400 / 500
```json
{
  "error": "Failed to send webhook: <detailed connection error description>"
}
```

## GET /api/admin/unmapped-drives
Scans `/dev/disk/by-path/` to locate physically connected drives that are not registered in the active `bay_map.json` configuration.

### Success 200
```json
[
  {
    "by_path": "pci-0000:01:00.0-scsi-0:0:4:0",
    "device": "/dev/sdc",
    "model": "Seagate ST4000NM0023",
    "serial": "W1F0ABCD",
    "capacity_str": "4 TB",
    "capacity_bytes": 4000787030016
  }
]
```

## POST /api/admin/save-bay-map
Overwrites `/opt/drive-eraser/config/bay_map.json` with the updated dictionary sent by the client.

### Request body
- Map object representing full, validated `bay_map.json` structure.

### Success 200
```json
{
  "status": "success",
  "message": "Bay mapping configuration updated successfully."
}
```

### Error 400 / 500
```json
{
  "error": "Payload must be a dictionary map."
}
```

## GET / POST /api/admin/policy
Exposes and safely updates system rules (Station ID, Webhook URLs, Pre-wipe check states) and writes changes back to `/config/policy.json`.

**Note on GET requests:** The backend automatically redacts `"lan_passphrase"` values from the payload to prevent browser-side credential leaking.

---

## Job Status Values
- `queued`
- `running`
- `completed`
- `failed`