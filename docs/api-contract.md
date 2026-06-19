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
      "bay": "bay2",
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
Exposes and safely updates system rules and writes changes back to `/config/policy.json`.

Writable operational fields include:
- `station_id` — station identifier used in notifications and certificates
- `slack_webhook_url` — Slack webhook URL for notifications
- `prewipe_spot_check` — reserved policy toggle (currently not implemented in the wipe workflow)
- `post_erase_marker` — enable/disable post-erase marker writing
- `allow_method_override` — allow technicians to override the recommended erase method
- `crypto_verification_mode` — `conservative_probe`, `full_verify`, or `disabled`
- `discovery_max_workers` — parallel SMART query threads during discovery
- `max_concurrent_wipes` — maximum simultaneous erase jobs
- `blockdev_post_wipe_retries` — retry attempts for post-wipe `blockdev --getsize64`
- `blockdev_post_wipe_retry_delay` — seconds between post-wipe blockdev retries

**Note on GET requests:** The backend currently redacts `"lan_passphrase"` from the payload. `"wipe_passphrase"` and `"slack_webhook_url"` are included in the response and should be treated as sensitive values by the admin UI.

## GET /api/status
Returns system status information. Currently exposes the security/audit configuration used by the frontend badge.

### Success 200
```json
{
  "passphrase_enabled": true
}
```

**Note:** `strict_audit_mode` will be added to this response as part of the secure-mode badge fix (Issue 10). The badge will reflect `strict_audit_mode` rather than `passphrase_enabled`.

## POST /api/erase/jobs/<job_id>/cancel
Cancels a running or queued erase job.

### Success 200
```json
{
  "status": "cancelled",
  "message": "Job cancelled successfully"
}
```

### Error responses
- `404` job not found
- `409` job cannot be cancelled (already completed or failed)

## POST /api/certificates/bulk-html
Generates a bulk HTML file containing multiple certificates for printing.

### Request body
- `job_ids` string[] required - List of job IDs to include in bulk certificate

### Success 200
```json
{
  "status": "success",
  "bulk_html_path": "/path/to/bulk-cert.html",
  "total_certificates": 10
}
```

## POST /api/admin/bulk-cert/create
Creates a bulk certificate generation job for multiple completed erase jobs.

### Request body
- `job_ids` string[] required - List of job IDs to generate certificates for

### Success 200
```json
{
  "id": "uuid",
  "friendly_id": "BULK-20250107-ABC123",
  "status": "queued",
  "total_jobs": 10
}
```

### Error responses
- `400` invalid job_ids list or exceeds maximum batch size
- `404` one or more jobs not found

## GET /api/admin/discover-slots
Discovers available drive slots and their current device mappings.

### Success 200
```json
[
  {
    "bay": "bay1",
    "device": "/dev/sda",
    "present": true
  }
]
```

## POST /api/admin/apply-slot-mapping
Applies a slot mapping configuration to the system.

### Request body
- Mapping object representing slot to device assignments

### Success 200
```json
{
  "status": "success",
  "message": "Slot mapping applied successfully"
}
```

## GET /api/admin/bay-map
Returns the current bay mapping configuration.

### Success 200
```json
{
  "bay1": {
    "label": "Bay 1",
    "by_path": "/dev/disk/by-path/pci-0000:01:00.0-scsi-0:0:0:0"
  }
}
```

## POST /api/admin/auto-detect-bays
Automatically detects and maps bays based on connected devices.

### Success 200
```json
{
  "status": "success",
  "detected_bays": 8,
  "mapped_bays": 8
}
```

## GET /api/admin/export-csv
Exports job history as a CSV file.

### Success 200
Returns CSV file with job history data.

## GET /api/admin/support-bundle
Generates a support bundle containing logs and diagnostics.

### Success 200
```json
{
  "status": "success",
  "bundle_path": "/path/to/support-bundle.tar.gz"
}
```

## GET /api/admin/triage-config
Returns the current triage configuration thresholds.

### Success 200
```json
{
  "ssd_new_poh_threshold": 500,
  "hdd_new_poh_threshold": 500,
  "health_score_destroy_threshold": 20
}
```

## POST /api/admin/triage-config
Updates the triage configuration thresholds.

### Request body
- Triage threshold configuration object

### Success 200
```json
{
  "status": "success",
  "message": "Triage configuration updated"
}
```

## GET /api/admin/logo
Returns the current custom logo (if configured).

### Success 200
Returns logo image file or 404 if not configured.

## POST /api/admin/logo
Uploads a custom logo for certificates.

### Request body
- Multipart form data with logo file

### Success 200
```json
{
  "status": "success",
  "message": "Logo uploaded successfully"
}
```

### Error responses
- `400` invalid file format or size exceeds limit

## DELETE /api/admin/logo
Removes the custom logo, reverting to default.

### Success 200
```json
{
  "status": "success",
  "message": "Logo removed successfully"
}
```

## GET /api/admin/layout-templates
Returns available certificate layout templates.

### Success 200
```json
[
  {
    "id": "template1",
    "name": "Standard Layout",
    "description": "Default certificate layout"
  }
]
```

## POST /api/admin/layout-templates
Creates a new certificate layout template.

### Request body
- Template configuration object

### Success 200
```json
{
  "status": "success",
  "template_id": "new-template-id"
}
```

## PUT /api/admin/layout-templates/<template_id>
Updates an existing certificate layout template.

### Request body
- Updated template configuration object

### Success 200
```json
{
  "status": "success",
  "message": "Template updated"
}
```

## DELETE /api/admin/layout-templates/<template_id>
Deletes a certificate layout template.

### Success 200
```json
{
  "status": "success",
  "message": "Template deleted"
}
```

## GET /api/admin/layout-templates/export
Exports a certificate layout template.

### Success 200
Returns template file.

## POST /api/admin/layout-templates/import
Imports a certificate layout template.

### Request body
- Template file

### Success 200
```json
{
  "status": "success",
  "template_id": "imported-template-id"
}
```

## POST /api/admin/apply-template
Applies a certificate layout template to a job.

### Request body
- `template_id` string required
- `job_id` string required

### Success 200
```json
{
  "status": "success",
  "message": "Template applied successfully"
}
```

## GET /docs/<path:path>
Serves documentation files from the docs directory.

### Frontend Help Modal
The frontend includes a Help modal (accessed via the Help button in the header) that provides:
- Quick start guide for common tasks
- Links to documentation files served via `/docs/`:
  - `/docs/SOP_technician_guide.md` - Technician SOP
  - `/docs/troubleshooting.md` - Troubleshooting guide
  - `/docs/runbook.md` - Operational runbook
- Common system administration commands

The Help modal is a purely frontend UI element and does not require a separate API endpoint.

## GET /
Serves the frontend application (index.html).

## GET /<path:path>
Serves static frontend assets (CSS, JS, images).

---

## Job Status Values
- `queued`
- `running`
- `completed`
- `failed`