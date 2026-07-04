# System Administration Guide

This guide covers all features available in the **System Administration** tab (Tab 4) of the Drive Wipe Station. This tab is the primary configuration interface for administrators.

**Related docs**:
- [enclosure-mapping-guide.md](enclosure-mapping-guide.md) — Detailed enclosure setup and slot mapping
- [lifecycle.md](lifecycle.md) — Drive wipe lifecycle and policy configuration
- [api-contract.md](api-contract.md) — API endpoint specifications
- [operations.md](operations.md) — Service operations and troubleshooting

---

## Access Control

Admin panel access depends on the request origin:

- **Local requests** (localhost, `127.0.0.1`, `::1`, private LAN): No authentication required
- **Remote requests**: Requires a signed `admin_session` HTTP-Only cookie matching the `lan_passphrase` in `config/policy.json`

This is enforced by the `require_admin_auth` decorator on all `/api/admin/*` endpoints.

---

## Admin Panel Layout

The admin panel is organized into card-based zones:

| Zone | Feature | Description |
|------|---------|-------------|
| A | System Metrics | Real-time CPU, RAM, disk usage, uptime, IP address |
| B | System Configuration | Modal for policy settings (verification, performance, audit, health gate, zero detection) |
| C | Triage Configuration | Modal for triage threshold tuning |
| D | Template Management | CRUD for physical bay layout templates |
| E | Certificate Logo | Upload/remove custom logo for certificate headers |
| F | Enclosure Management | CRUD for physical enclosures and slot mappings |
| G | Interactive Bay Mapping | Legacy bay-by-bay mapping with traversal templates |
| H | Drive Model Profiles | View known drive model risk profiles |
| I | Support Bundle | Download diagnostic bundle for escalation |
| J | CSV Export | Export wipe ledger as CSV |
| K | Kill All Jobs | Emergency stop for all running/queued jobs |
| L | SMART Self-Test Runner | Run short/extended/conveyance SMART tests on drives |
| M | Unmapped Drive Discovery | View drives not yet assigned to a bay |

---

## System Metrics

**API**: `GET /api/admin/metrics`

Displays real-time system health:
- **Disk usage**: Used vs total for the data partition
- **RAM utilization**: Percentage of system memory in use
- **CPU utilization**: Current CPU load percentage
- **System uptime**: Since last boot
- **IP address**: Server's local network address

---

## System Configuration

**API**: `GET /api/admin/policy` (read), `POST /api/admin/policy` (update)

**Frontend module**: `frontend/admin/systemConfig.js`

The System Configuration modal exposes all operational policy settings. Changes are saved to `config/policy.json`.

### Notifications

| Setting | Description | Default |
|---------|-------------|---------|
| `slack_webhook_url` | Slack webhook URL for wipe completion/failure notifications | `""` (disabled) |

Use the **Test Webhook** button (`POST /api/admin/test-webhook`) to send a test notification and verify connectivity.

### Verification Mode

| Setting | Description | Default |
|---------|-------------|---------|
| `secondary_verification_mode` | Post-wipe secondary verification: `conservative_probe` (10% sampled), `full_verify` (100%), `disabled` | `conservative_probe` |

The deprecated alias `crypto_verification_mode` is still accepted for backward compatibility.

### Performance Settings

| Setting | Description | Default |
|---------|-------------|---------|
| `discovery_max_workers` | Maximum parallel threads for drive discovery (1-32) | `8` |
| `max_concurrent_wipes` | Maximum simultaneous erase jobs (1-256) | `64` |

### Post-Wipe Retry Settings

| Setting | Description | Default |
|---------|-------------|---------|
| `blockdev_post_wipe_retries` | Retry attempts for post-wipe `blockdev --getsize64` (0-10) | `3` |
| `blockdev_post_wipe_retry_delay` | Seconds between retries (0-60) | `5` |

These settings handle transient bus resets after hardware-level sanitize commands (e.g., SATA SSDs that drop the link during secure erase).

### Audit Mode

| Setting | Description | Default |
|---------|-------------|---------|
| `strict_audit_mode` | When enabled, requires technician name and ticket number for all wipes | `true` |
| `wipe_passphrase` | Passphrase for marker HMAC signing (min 8 characters when strict mode enabled). Leave blank to keep current. | `""` |

**Important**: Changing the wipe passphrase invalidates existing marker verification results. A confirmation modal warns before applying this change.

The header "SECURE MODE" badge reflects `strict_audit_mode`, not the passphrase setting.

### Pre-Wipe Health Gate

The health gate prevents starting wipes on drives with critical health issues. When enabled, drives are checked against configured thresholds before a wipe job is accepted.

| Setting | Description | Default |
|---------|-------------|---------|
| `prewipe_health_gate_enabled` | Enable/disable the health gate | `false` |
| `prewipe_health_gate_strict_mode` | When enabled, blocks cannot be overridden even in non-strict audit mode | `false` |
| `prewipe_health_gate_block_destroy` | Block wipes on drives with DESTROY recommendation | `true` |
| `prewipe_health_gate_block_scratch` | Block wipes on drives with SCRATCH recommendation | `false` (warning only) |
| `prewipe_health_gate_block_failed_smart` | Block wipes on drives with SMART status FAILED | `true` |
| `prewipe_health_gate_max_pending_sectors` | Threshold for pending sectors (0-1000) | `10` |
| `prewipe_health_gate_max_reallocated_sectors` | Threshold for reallocated sectors (0-1000) | `5` |
| `prewipe_health_gate_max_interface_errors` | Threshold for interface errors (0-100000) | `100` |
| `prewipe_health_gate_max_health_score_drop` | Health score drop threshold for intake history comparison (0-100) | `20` |

### Pre-Wipe Zero Detection

Background, non-blocking, visual-only check that runs after discovery for internal (SATA/SAS/NVMe) drives. It does not block or gate wipe operations.

| Setting | Description | Default |
|---------|-------------|---------|
| `prewipe_zero_detection_enabled` | Enable/disable automatic pre-wipe zero detection | `true` |
| `zero_detection_concurrency_limit` | Maximum parallel zero-check reads (1-32) | `8` |

### Additional Policy Fields (not in UI, editable via API)

| Setting | Description | Default |
|---------|-------------|---------|
| `post_erase_marker` | Enables post-erase marker writing | `true` |
| `allow_method_override` | Allow technicians to override recommended erase method | `true` |
| `method_priority` | Per-interface method priority arrays | See `config/policy.json` |
| `crypto_fail_retry_block` | Retry with block method if crypto erase fails | `true` |
| `health_soft_stop` | Poor health is a soft stop (warning), not a hard block | `true` |
| `station_id` | Station identifier for certificates and markers | `wipe-station-01` |
| `certificate_retention_days` | How long to keep certificates in database | `365` |
| `log_retention_days` | How long to keep operational logs | — |
| `max_logo_size_mb` | Maximum logo file size | `1` |
| `max_bulk_cert_batch_size` | Maximum certificates per bulk export | `100` |
| `discovery_diag` | Enable discovery diagnostic logging | `false` |
| `background_smart_max_workers` | Maximum parallel workers for background SMART collection | — |

---

## Triage Configuration

**API**: `GET /api/admin/triage-config` (read), `POST /api/admin/triage-config` (update)

**Frontend module**: `frontend/admin/triageConfig.js`

The triage system classifies drives into categories (NEW, USED_GOOD, SCRATCH, DESTROY) based on health score and SMART attributes. Thresholds are configurable.

### Power-On Hours Thresholds

| Setting | Description | Default |
|---------|-------------|---------|
| `ssd_new_poh_threshold` | Below this = "new stock" for SSDs | `720` |
| `ssd_high_poh_threshold` | Above this = "heavily used" for SSDs | `43800` |
| `hdd_new_poh_threshold` | Below this = "new stock" for HDDs | `720` |

### Health Score Thresholds

| Setting | Description | Default |
|---------|-------------|---------|
| `health_score_destroy_threshold` | At or below = DESTROY | `25` |
| `health_score_scratch_threshold` | At or below = SCRATCH | `50` |
| `health_score_good_threshold` | At or above = USED_GOOD | `75` |

### Workload (FDW) Thresholds

| Setting | Description | Default |
|---------|-------------|---------|
| `ssd_new_fdw_threshold` | Below this = "new stock" for SSDs | `0.06` |
| `hdd_new_fdw_threshold` | Below this = "new stock" for HDDs | `2.0` |

### Sector Thresholds

| Setting | Description | Default |
|---------|-------------|---------|
| `realloc_raw_new_threshold` | This many reallocated sectors = still "new" | `0` |

### SAS-Specific Thresholds

| Setting | Description | Default |
|---------|-------------|---------|
| `sas_grown_defect_fail_threshold` | Above = force FAILED override | `10000` |
| `sas_nme_advisory_threshold` | Above = advisory flag | `1000000` |
| `sas_nme_penalty_threshold` | Above = health score penalty | `100000000` |
| `sas_sticky_lba_threshold` | Events above = sticky LBA detected | `3` |
| `sas_high_poh_threshold` | Above = heavily used for SAS | `50000` |

---

## Template Management

**API**: `GET/POST /api/admin/templates`, `PUT/DELETE /api/admin/templates/<template_id>`

**Frontend module**: `frontend/admin/templateManagement.js`

Templates define physical bay layouts independent of hardware. They specify the grid dimensions, traversal order, and which slots are hybrid (supporting both SAS/SATA and NVMe).

### Template Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Template identifier (lowercase, alphanumeric, hyphens, underscores only) |
| `name` | string | Human-readable template name |
| `vendor` | string | Hardware vendor (e.g., "Dell", "Supermicro") |
| `bay_count` | integer | Total number of usable bays (1-128) |
| `rows` | integer | Grid rows (1-16) |
| `cols` | integer | Grid columns (1-5) |
| `traversal_preset` | string | Slot traversal order (see below) |
| `skip_positions` | array | Grid positions to skip (row/col objects, max 100) |
| `hybrid_slots` | array | Physical slot numbers that support multiple interface types (max 128) |

### Supported Traversal Presets

| Preset | Description |
|--------|-------------|
| `top_left_down_then_across` | Top-Left Down Then Across (Dell pattern) |
| `bottom_left_up_then_across` | Bottom-Left Up Then Across (Supermicro pattern) |
| `top_left_across_then_down` | Top-Left Across Then Down |
| `bottom_left_across_then_up` | Bottom-Left Across Then Up |

### Built-in Templates

- **Dell R320 4-Bay (3.5")**: 1 row, 4 cols, 4 bays, top-left-down-then-across
- **Dell R440 10-Bay (2.5")**: 2 rows, 5 cols, 10 bays, top-left-down-then-across

### Template Preview

The template preview modal shows a visual grid of the layout with an **Animate Traversal** button that highlights the slot numbering sequence step by step.

### Import/Export

Templates can be exported to JSON and imported on another system, enabling configuration sharing between wipe stations.

---

## Certificate Logo Management

**API**: `GET /api/admin/logo` (status/preview), `POST /api/admin/logo` (upload), `DELETE /api/admin/logo` (remove)

**Frontend module**: `frontend/admin/logoManagement.js`

Upload a custom logo that appears in the header of generated certificates.

- **Accepted formats**: PNG, JPG, JPEG
- **Maximum file size**: 1MB (configurable via `max_logo_size_mb`)
- **Scaling**: Logo is scaled to fit within 500px x 500px

When uploading a replacement logo, a confirmation modal warns that the existing logo will be overwritten.

---

## Enclosure Management

**API**: `GET/POST /api/admin/enclosures`, `GET/PUT/DELETE /api/admin/enclosures/<enclosure_id>`, `POST /api/admin/enclosures/<enclosure_id>/slots`, `PUT/DELETE /api/admin/enclosures/<enclosure_id>/slots/<slot_num>`, `PUT/DELETE /api/admin/enclosures/<enclosure_id>/slots/<slot_num>/mappings/<mapping_type>`

**Frontend modules**: `frontend/admin/enclosureList.js`, `frontend/admin/enclosureWizard.js`, `frontend/admin/enclosureSave.js`

Enclosures are physical chassis/backplanes with hardware bindings. Each enclosure references a template and binds to a specific PCI controller and optional SAS expander.

For detailed setup instructions, see [enclosure-mapping-guide.md](enclosure-mapping-guide.md).

### Enclosure Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Enclosure identifier (auto-generated from name) |
| `name` | string | Human-readable name (min 2 characters) |
| `template_id` | string | Reference to template definition |
| `pci_controller` | string | PCI address of HBA/controller (e.g., `0000:af:00.0`) |
| `expander_sas_address` | string\|null | SAS expander WWN (null for direct AHCI/NVMe) |
| `display_order` | integer | Display order in UI |
| `slots` | object | Slot mappings keyed by slot number string |

### Slot Deletion Protection

A slot cannot be deleted if a sanitization job is currently running or queued on it. This is enforced server-side.

### Hardware Enclosure Info

**API**: `GET /api/admin/hardware-enclosure-info`

Returns hardware-detected enclosure information from `/sys/class/enclosure`, used to assist with enclosure configuration.

---

## Interactive Bay Mapping

**API**: `GET /api/admin/bay-map`, `POST /api/admin/save-bay-map`, `POST /api/admin/auto-detect-bays`

**Frontend module**: `frontend/admin/bayMapping.js`

The legacy bay mapping interface allows direct bay-by-bay configuration. This is the original mapping method before enclosure-based configuration was introduced.

### Auto-Detect Bays

The auto-detect feature scans the hardware for physical backplane slots using two methods:

1. **Method A — SCSI Enclosure Services (SES)**: Scans `/sys/class/enclosure` for slot-to-device mappings
2. **Method B — SAS Transport bay_identifier**: Fallback for passive direct-attach backplanes, reads `bay_identifier` from SAS transport sysfs

Auto-detect maps physical slots to `/dev/disk/by-path/` entries and updates `config/bay_map.json`.

### Layout Templates in Bay Mapping

The bay mapping panel includes a template selector and traversal preset dropdown. Applying a template re-numbers the bays according to the template's grid layout and traversal order.

### Memory-Safe Staging

All bay mapping changes (add, delete, edit) happen in-memory in the browser first. Changes are only committed to `config/bay_map.json` when the technician clicks **Save Mapping Configuration**. This prevents active background UI refresh pollers from wiping client-side edits during long async operations.

---

## Drive Model Profiles

**API**: `GET /api/admin/drive-models`

**Frontend module**: `frontend/admin/driveModels.js`

Displays known drive model risk profiles from `config/drive_models.json`. These profiles contain model-specific thresholds that adjust how the health parser interprets certain metrics (e.g., trip temperature, NME normal range).

### Profile Fields

| Field | Description |
|-------|-------------|
| `vendor` | Drive vendor (e.g., SEAGATE) |
| `product` | Drive product model (e.g., ST4000NM0023) |
| `revision` | Firmware revision (e.g., 0003, D007) |
| `trip_temperature` | Temperature trip point in Celsius |
| `nme_normal_range_max` | Maximum normal non-medium error count for this model |
| `notes` | Free-text notes about model-specific behavior |

Profiles are keyed by `(vendor, product, revision)` tuple. To add or modify entries, edit `config/drive_models.json` directly — the UI is read-only.

---

## Support Bundle

**API**: `GET /api/admin/support-bundle`

Downloads a `.tar.gz` archive containing diagnostic information for escalation:

- **Hardware environment**: `lsblk -J` and `lshw -class storage -class disk` output
- **smartctl -x output**: Per-device SMART data (up to 50 devices, collected in parallel)
- **System metrics**: Hostname, date, uptime, CPU, RAM, disk space
- **Redacted policy.json**: With `wipe_passphrase`, `slack_webhook_url`, and `lan_passphrase` redacted
- **Application logs**: `app.log` and `discovery_diag.log` (including rotated logs)
- **Failed job logs**: Contents of `data/logs/failed/`
- **Diagnostic snapshot**: Point-in-time discovery diagnostic capture

The bundle is created in `/tmp`, streamed as a download, and automatically deleted after sending.

---

## CSV Export

**API**: `GET /api/admin/export-csv`

Exports the complete wipe ledger from the SQLite database as a CSV file. Columns include:

- Job ID, Friendly ID, Status
- Created/Started/Finished timestamps
- Technician, Ticket Number
- Bay, Serial, Model, Capacity, Method
- Verification Status, Error

The filename includes a timestamp: `wipe-ledger-YYYYMMDD-HHMMSS.csv`

---

## Kill All Jobs

**API**: `POST /api/admin/jobs/kill-all`

Emergency stop for all running and queued wipe jobs. The endpoint checks actual drive hardware status before killing:

- **If the drive reports still wiping**: The job is skipped with detailed diagnostics (the hardware is actively sanitizing and cannot be safely interrupted)
- **If the drive reports idle/complete but the subprocess is stuck**: The job is killed

This distinction prevents interrupting hardware-level sanitize operations (NVMe sanitize, SATA secure erase) which can leave drives in an indeterminate state.

---

## SMART Self-Test Runner

**API**: `POST /api/admin/drives/<device>/smart-test`, `GET /api/admin/drives/<device>/smart-test-status`

**Frontend module**: `frontend/admin/smartDeepDive.js`

Runs SMART self-tests on drives from the admin panel. Supported test types:

- **Short**: Quick surface scan (typically 2-10 minutes)
- **Extended**: Full media scan (can take hours on large drives)
- **Conveyance**: Manufacturing/transport test (SATA only)

### Safety Guardrails

- Cannot run SMART test while a wipe is in progress on the same device
- Cannot run SMART test on the OS drive
- Cannot run SMART test on mounted drives
- Cannot run SMART test on dual-port secondary SAS paths (use primary path instead)
- Only one SMART test per device at a time (enforced via in-memory locks)

### Smart Test Status

The status endpoint returns the current test state, progress percentage (when available), and self-test log entries. A grace period (configurable via `SMART_TEST_GRACE_PERIOD_SECONDS` in `smart_constants.py`) prevents false completion/failure detection immediately after test start.

---

## SMART Deep-Dive

**API**: `GET /api/admin/drives/<device>/smart-details`

Provides detailed SMART attribute data for a specific drive, including parsed health metrics, raw smartctl JSON, and historical context. Used by the drive detail modal's SMART deep-dive view.

---

## Per-Drive SMART Export

**API**: `GET /api/admin/drives/<device>/smart-export`

Downloads raw `smartctl -j -x` JSON output for a specific drive as a file. The filename format is `smartctl-<serial>-<timestamp>.json`. For cached drives, the raw JSON is served from cache without re-running smartctl.

This is lighter than a full support bundle when only one drive needs escalation.

---

## Unmapped Drive Discovery

**API**: `GET /api/admin/unmapped-drives`

**Frontend module**: `frontend/admin/discoveryMapping.js`, `frontend/admin/discoveryModal.js`

Displays drives that are physically present but not yet assigned to a bay in the enclosure configuration. Uses `smartctl -j -i` (identity-only) with a 10-minute per-device cache for fast response.

Each unmapped drive shows:
- Device path (e.g., `/dev/sdb`)
- Model, serial, capacity
- Interface type
- Suggested bay assignment based on hardware topology

Administrators can assign unmapped drives to enclosure slots from this view.

---

## Master Slot Map

**API**: `GET /api/admin/master-slot-map`

Returns the hardware topology inventory generated by scanning sysfs. Each entry contains:

- `pci_controller`: PCI address of the HBA/controller
- `slot_type`: One of `sas_expander`, `sas_direct`, `motherboard_sata`, `pcie_nvme`
- `expander_sas_address`: SAS expander WWN (null for non-expander types)
- `physical_slot_number`: 0-indexed slot number from hardware
- `hardware_identifier`: Hardware path (e.g., `phy-0:0:5`, `ata3`, `101`)

The map is cached for 1 hour (3600 seconds). Use `?force_refresh=true` to bypass cache. The cache is automatically invalidated when enclosures are created, edited, or deleted, and when bay mappings are saved or auto-detected.

---

## WebSocket Real-Time Updates

The admin panel receives real-time updates via Socket.IO when drives are hot-plugged:

- **slot_update**: Emitted when a drive is added or removed from an enclosure slot. Contains enclosure ID, slot number, logical device path, and status (Active/Empty).

This is powered by the udev event listener thread (`backend/udev_listener.py`) which monitors block device events and resolves them to enclosure slots using the configured bay map.
