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
- `bay` string required
- `confirmation_text` string required, exact format: `erase <bay>`
- `method` string optional

### Success 202
```json
{
  "status": "accepted",
  "message": "erase job started",
  "job": {
    "id": "uuid",
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
```json
{
  "id": "uuid",
  "status": "running",
  "created_at": "ISO-8601",
  "started_at": "ISO-8601|null",
  "finished_at": "ISO-8601|null",
  "error": null,
  "result": {
    "command": "...",
    "stdout": "...",
    "stderr": "...",
    "exit_code": 0
  },
  "verification": {
    "ok": true,
    "status": "verified",
    "error": null,
    "details": {}
  },
  "certificate": {
    "id": "cert-uuid",
    "job_id": "uuid",
    "issued_at": "ISO-8601",
    "finished_at": "ISO-8601",
    "technician": "...",
    "ticket_number": "...",
    "bay": "bay3",
    "device": "/dev/sdX",
    "serial": "...",
    "model": "...",
    "interface_type": "sata",
    "method": "overwrite",
    "verification": {
      "ok": true,
      "status": "verified",
      "error": null,
      "details": {}
    },
    "path": ".../data/certs/cert-uuid.json",
    "filename": "cert-uuid.json"
  },
  "request": {
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
}
```

### Not found 404
```json
{
  "error": "job not found: <job_id>"
}
```

## GET /api/erase/history
Returns recent persisted erase jobs.

### Query params
- `limit` integer optional, default `50`, range `1..500`

### Success 200
```json
{
  "jobs": [
    {
      "id": "uuid",
      "status": "completed",
      "created_at": "ISO-8601",
      "started_at": "ISO-8601|null",
      "finished_at": "ISO-8601|null",
      "error": null,
      "request": {},
      "result": {},
      "verification": {},
      "certificate": {}
    }
  ],
  "count": 1
}
```

### Error 400
```json
{
  "error": "limit must be an integer"
}
```

```json
{
  "error": "limit must be between 1 and 500"
}
```

## GET /api/certificates/<job_id>
Returns certificate payload for a completed job.

### Success 200
```json
{
  "id": "cert-uuid",
  "job_id": "uuid",
  "issued_at": "ISO-8601",
  "finished_at": "ISO-8601",
  "technician": "...",
  "ticket_number": "...",
  "bay": "bay3",
  "device": "/dev/sdX",
  "serial": "...",
  "model": "...",
  "interface_type": "sata",
  "method": "overwrite",
  "verification": {
    "ok": true,
    "status": "verified",
    "error": null,
    "details": {}
  },
  "path": ".../data/certs/cert-uuid.json",
  "filename": "cert-uuid.json"
}
```

### Not found 404
```json
{
  "error": "job not found: <job_id>"
}
```

```json
{
  "error": "certificate not found for job: <job_id>"
}
```

## Job Status Values
- `queued`
- `running`
- `completed`
- `failed`

## Notes
- A frontend polling timeout does not stop backend execution.
- Job ID is the authoritative tracking handle.