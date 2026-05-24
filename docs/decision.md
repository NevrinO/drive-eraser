# Architecture Decisions

## Product Direction
- Build a local web-based drive sanitization station for Linux
- Access pattern: local browser / KVM on the wipe server
- Multi-technician environment, but no login system in the first release
- Safety and auditability are more important than speed or visual polish

## Backend Choice
- Backend framework is now effectively **Flask** for the current implementation path
- Current codebase already uses `backend/app.py` with Flask
- Keep architecture simple until core drive operations are proven

## Deployment Model
- Official deployment path should use **HTTPS Git clone**, not personal SSH keys
- Reason:
  - any technician should be able to rebuild a server
  - deployment must not depend on one person's GitHub SSH setup
  - supports disaster recovery and handoff better

## Service / Privilege Model
- Run application as dedicated service user: `wipestation`
- Application itself should not run as full root
- Privileged disk operations are performed through a tightly scoped `sudoers` allowlist
- Command paths should be resolved at install/update time and persisted for runtime use
- Systemd must allow controlled sudo escalation for this architecture:
  - `NoNewPrivileges=false` in service unit
- Current real-world path validation matters more than assumptions:
  - `smartctl` path confirmed as `/usr/sbin/smartctl` on test Ubuntu 26.04
  - `nvme` path also confirmed under `/usr/sbin/` on the test system

## Backend Runtime Configuration
- Backend should resolve config directory in this order:
  1. `DRIVE_ERASER_CONFIG_DIR`
  2. local repo `config/` during development
  3. `/opt/drive-eraser/config` in deployed service mode
- API bind address and port should come from `policy.json`, not hardcoded defaults

## Command Path Handling
- Installer should discover command paths dynamically (`command -v`) after package install
- Installer should write `config/command_paths.json` as runtime path source of truth
- Backend command resolution should support:
  1. explicit env var override
  2. installer-generated command path file
  3. known path fallbacks
  4. `which` fallback

## Drive Discovery Mapping
- Linux does not reliably provide a human-safe physical bay mapping by default
- Use `/dev/disk/by-path/` plus explicit configuration in `config/bay_map.json`
- Mapping is machine-specific and should be edited locally per server
- Discovery should accept flexible mapping input forms for resilience:
  - full `/dev/disk/by-path/...`
  - bare by-path entry name
  - direct `/dev/...` path with reverse resolution
- Discovery payload should expose mapping and command diagnostics for operator troubleshooting
- Discovery payload includes a normalized SMART essentials object (`smart`) per bay for frontend detail rendering
- Protected bays remain explicitly modeled in config

## Capability Detection Scope (Current Phase)
- Discovery endpoint should expose capability fields directly in drive JSON
- Interface classification policy is now:
  - use `smartctl -i` as primary classifier for NVMe/SATA/SAS
  - apply fallback hints only when smartctl data is unavailable
  - avoid using host transport (`lsblk -S` `TRAN`) as primary protocol classifier on SAS-backplane systems
- Current capability probes are interface-aware:
  - NVMe capabilities from `nvme id-ctrl` (`sanicap`)
  - SATA secure erase capabilities from `hdparm -I`
  - SAS block capability probing from `sg_sanitize --status` command behavior/output

## API and Safety Enforcement
- `/api/erase/start` validates request safety and starts asynchronous erase jobs
- Added `/api/erase/jobs/<job_id>` to query job status and execution result
- Added `/api/erase/history` to return recent persisted erase records for audit browsing
- Added `/api/certificates/<job_id>` to retrieve generated certificate payloads for completed jobs
- Required fields:
  - technician
  - ticket number
  - bay
  - typed confirmation (`erase {bay}`)
- Hard blocks:
  - `locked` bays
  - `os` and `reserved` roles
  - absent or unresolved devices
- Method selection is constrained by detected capabilities and policy override settings
- Job states are `queued`, `running`, `completed`, `failed`
- Erase jobs are persisted to SQLite (`data/wipes.db`) so job status survives process restart and can be queried by job id
- Job persistence includes verification, marker, and certificate payloads for audit continuity

## Bay Safety Model
- Bay 1 = OS drive = locked / view only
- Bay 2 = reserved / locked
- Remaining work bays are wipe-capable based on platform
- On the 4-bay test server, bays 3-4 are active wipe bays
- Protected bays must never be wipe targets even if detected and healthy

## Erase Workflow Requirements
- Operator must supply:
  - technician name
  - ticket number
  - typed confirmation
- OS and reserved drives are hard blocked
- Poor drive health is a soft stop, not an absolute stop

## Erase Method Policy
- NVMe: Crypto > Block > Overwrite
- SAS: Crypto > Block > Overwrite
- SATA: Enhanced Secure Erase > Secure Erase > Overwrite
- Allow operator override only within methods actually supported by the detected device
- Current execution mapping in backend:
  - overwrite: `dd`
  - SATA secure erase/enhanced secure erase: `hdparm`
  - NVMe block/crypto: `nvme sanitize`
  - SAS block: `sg_sanitize --block`

## Frontend Operator Flow
- A major visual redesign is approved for the current frontend pass, while preserving all safety and method-policy guardrails
- Navigation model is top tabs for operator workflow, with modal drilldown for fast detail access
- First-pass mandatory scope:
  - SOP-aligned status label/color mapping across bays/jobs/detail surfaces
  - top tabs + bay detail modal with quick-scan dashboard behavior retained
- Erase method selection remains bay-aware and recommendation-aware:
  - default selection is `Use recommended`
  - method options are constrained to the selected bay `supported_methods`
  - recommendation hint is shown to the operator and updates when bay changes
- Frontend recommendation order aligns with policy precedence:
  - `crypto` > `block` > `enhanced_secure_erase` > `secure_erase` > `overwrite`
- Protected bay visibility is explicit in cards (`locked`/`os`/`reserved`) and protected bays remain non-actionable
- Inspection modal must surface:
  - SMART essentials (temperature, reallocated/pending sectors, wear, power-on hours when available)
  - marker/prior-wipe state details
- Erase submission transitions into job tracking mode:
  - receives job id from `/api/erase/start`
  - polls `/api/erase/jobs/<job_id>` for status updates
  - supports manual tracking refresh by job id
  - uses a polling timeout guard to avoid indefinite waiting while preserving job id for resume
- Completion actions are implemented in Jobs view (download JSON/HTML cert, print cert view, copy audit fields)
- History and certificates tab surfaces now include quick actions for download/print/copy and remain in polish phase

## Verification Strategy
- Verification is method-aware
- Current backend verification behavior:
  - `overwrite`: sampled post-erase zero checks via `dd` block reads
  - NVMe `crypto`/`block`: sanitize-log status check via `nvme sanitize-log`
  - SATA `secure_erase`/`enhanced_secure_erase`: post-erase security state check via `hdparm -I`
  - SAS `block`: sanitize status check via `sg_sanitize --status`
- Verification parser strictness is finalized for command output variants:
  - NVMe parser uses `SPROG`/`SSTAT` fields plus status phrases to classify completed/in-progress/failed/unrecognized states
  - SATA parser requires a recognizable security section, scopes parsing to that section, and handles enabled/disabled wording variants conservatively
  - SAS parser recognizes in-progress/failure/completion markers, gives completion precedence in mixed phrases, and treats unknown status text as verification error
- Crypto erase must **not** be validated by expecting zeroed sectors
- Crypto erase should be validated through controller / sanitize status where possible
- Block / overwrite methods can use sampling and/or zero checks where appropriate

## Marker Strategy
- Marker is written only after erase + verification succeed
- Marker write/read is best-effort and warning-scoped:
  - marker result is captured in job payload for audit visibility
  - marker failures do not invalidate a successful erase+verification outcome
- Current marker implementation writes a structured JSON marker at LBA0 using `dd`
- Marker readback parsing should extract the first marker payload line, isolate JSON object boundaries, then validate marker signature

## Certificate Strategy
- Certificates are only generated after successful completion of erase + verification flow
- Current implementation generates JSON and HTML certificate artifacts under `data/certs/` and persists certificate payload in SQLite job records
- Certificate retrieval supports format selection via `GET /api/certificates/<job_id>?format=json|html`
- Certificates should capture at minimum:
  - technician
  - ticket number
  - bay / device identity
  - serial
  - method used
  - verification outcome
  - timestamps

## Operational Test Utilities
- A dedicated test seeding script is available at `scripts/seed_test_data.sh` for controlled wipe-validation preparation on non-production test targets.
- Script policy is safety-first: explicit confirmation text, mounted-device refusal, and root-disk refusal are mandatory.

## Installer and Updater Philosophy
- Installer should be the single source of truth for building a fresh server
- A clean Ubuntu machine should be convertible into a working wipe station with minimal manual steps
- Real bring-up findings from the test server should feed back into installer/update scripts immediately
- Update script should preserve machine-specific config while refreshing code/runtime components
- `scripts/update.sh` now supports:
  - optional `--no-restart` mode for staged deployment
  - optional `--dry-run` mode for impact preview

## Multi-Vector Health Scoring (SATA/NVMe/SAS)
- **Problem**: Simple threshold checks or linear raw bad sector counts caused inaccurate drive life expectancy bars (e.g., used SSDs with harmless retired blocks showing 60% health).
- **Decision**: Implemented a comprehensive multi-vector scoring engine in `backend/disk_ops.py` to calculate a single `health_score` (0-100) returned via `/api/drives`:
  - **Mutually Exclusive Wear-Life**: SSD base health starts at remaining flash wear life. HDD base health starts at 100% and ages gracefully via Power-On Hours (POH) and workload Full Drive Writes (FDW).
  - **Differentiated Bad Sectors**: HDDs are penalized strictly on raw bad sector counts (immediate warning). SSDs are ignored for raw bad blocks and are only penalized if their over-provisioned "Available Spare" pool deplets below 100%.
  - **Pre-Fail Overrides**: Bit 3 and Bit 4 of `smartctl`'s exit status immediately force the health score to a maximum of 5%. Bit 5 (usage warnings) is explicitly ignored to prevent POH double-dipping.

## Byte-Accurate Disk Traffic Calculation
- **Problem**: Chained attributes 241/242 often report in units of 32 MiB on enterprise drives (e.g., Intel/Solidigm), causing traffic under-reporting by a factor of 65,536 when assuming standard 512-byte sectors.
- **Decision**: The backend `get_smart_data` now reads the raw JSON of `smartctl` and inspects the friendly attribute name (e.g., `Host_Writes_32MiB`, `Host_Writes_GiB`). It dynamically applies the correct byte multiplier and exposes `data_written_bytes` and `data_read_bytes` alongside raw fields, ensuring accuracy.

## SAS G-List (Grown Defect) Bad Sector Tracking
- **Problem**: Fallback parsing of SCSI errors frequently inspected ECC correction logs, reporting millions of false bad sectors on healthy SAS drives.
- **Decision**: For SAS/SCSI interface types, the bad sector parser strictly reads the `scsi_grown_defect_list` (G-list) to represent actual physical bad blocks safely.

## SATA Sanitize Polling Resilience
- **Problem**: Standard `hdparm` commands executing modern hardware-level block or crypto erase routines can reset the drive's SATA link, causing the initialization CLI process to exit with error code `5` / input-output warnings. This is often followed by temporary unresponsiveness as the drive initializes its wipe.
- **Decision**: The backend polling engine now integrates a 5-second initial delay to allow SATA controller negotiation to settle. It supports a consecutive-error tolerance threshold of up to 15 retries (approximately 60 seconds) before concluding a polling connection is dead.

## Active Query Lockup Bypass
- **Problem**: Performing query-intensive commands (`smartctl`, `hdparm`, or LBA check reads) on drives actively performing hardware sanitization resets can block the Flask worker threads, causing API hangs and browser timeouts.
- **Decision**: Implemented strict runtime state tracking in both `/api/erase/start` and `/api/drives`. Devices undergoing background `RUNNING` or `QUEUED` tasks bypass physical probes entirely. Their operational details are populated from the job's request cache.