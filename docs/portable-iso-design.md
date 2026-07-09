# Portable ISO Edition — Design Document

**Status**: Concept / Planning
**Date**: 2026-07-05

---

## Purpose

A fully portable, bootable ISO version of Drive Eraser for situations where drives cannot be brought back to a dedicated wipe station. An operator boots any server from a USB drive, the app auto-starts from RAM, detects all attached drives, allows health inspection and wiping, and stores certificates back on the USB drive.

The portable edition is **air-gapped** — no network access required, no external API calls, no Slack webhooks. Everything runs locally on the booted server.

---

## Operational Flow

1. Operator writes ISO to USB drive (using `dd`, Rufus, or balenaEtcher)
2. Boots target server from USB
3. App auto-starts, browser opens in kiosk mode pointing at `localhost:5000`
4. **Assessment mode** — all detected drives shown with health status in a table; no wipe controls visible yet
5. Operator reviews drives, clicks rows for SMART details
6. Operator explicitly enters **wipe mode** to unlock wipe controls
7. Operator selects drives (individually or "select all"), enters technician name + ticket number, confirms wipe
8. Wipe runs with the same erase engine, method priority, and verification as the main app
9. Certificate is generated and **auto-saved to USB cert partition** as each job completes
10. Operator repeats for all drives, then shuts down and removes USB
11. Operator plugs USB into any machine to access certificates (FAT32 partition)

---

## What Can Be Reused (Unchanged)

The following components are pure Python with no external dependencies beyond standard Linux tools. They work identically in a live ISO environment:

- **Erase engine** — `dd`, `hdparm`, `nvme sanitize`, `sg_sanitize` execution logic
- **SMART health scoring** — `backend/smart_health.py`, `smart_data_parsing.py`, drive model risk profiles
- **Verification logic** — all sanitize-log / hdparm / sg_sanitize status parsers
- **Certificate generation** — JSON + HTML certificate templates
- **Pre-wipe health gate** — same logic, same thresholds
- **Method priority / policy** — same `policy.json` structure with portable-specific defaults
- **Flask + Socket.IO** — runs fine in a live environment

---

## What Changes

### 1. Discovery — Auto-Detect All Drives

The enclosure/slot/bay mapping system is the most complex part of the main app and is **completely unnecessary** in the portable scenario. There is no dedicated backplane — drives are whatever is plugged into the server.

**Replace with**: Simple `lsblk` / sysfs enumeration. Show every block device that is not:
- The USB boot drive (identified at boot time from `/proc/cmdline` or initramfs `root` parameter)
- A loopback / RAM disk / overlay device
- A partition of any protected device

Each detected drive gets a row in the table. No bay numbers, no enclosure templates, no slot mapping UI.

### 2. Safety — Boot USB Protection

In the dedicated station, bay 0 is the OS drive and is hard-locked. In the portable ISO, **the boot USB is the critical device to protect**. The operator is plugging into an unfamiliar server — they must be 100% certain they cannot wipe the USB they booted from.

**Approach**:
- At boot, the init system records the USB device the ISO was loaded from
- That device (and all its partitions) is hard-blocked from wipe operations (same as `role: "os"` in the main app)
- Protected drives are shown in the table with a **PROTECTED** status and cannot be selected

### 3. Persistence — RAM + USB Cert Storage

- SQLite DB lives in tmpfs (RAM) — fine for the session, lost on shutdown
- Certificates must go to **persistent storage** before power-off
- **Strategy**: Write certs to the USB drive's cert partition as each job completes (not at shutdown — sudden power loss would lose everything)
- Belt and suspenders: auto-save on completion + a "Save All Certs to USB" button

### 4. Network / Auth — Fully Local

- Bind to `127.0.0.1` only
- No `lan_passphrase`, no session cookie auth, no CORS config
- Browser auto-launches in kiosk mode (no desktop environment needed — just X + Chromium/Firefox)
- No admin panel — no enclosure mapping, no bay config, no policy tuning UI

### 5. Frontend — Table UI

Strip down to essentials. The card grid in the main app exists because of a fixed physical bay layout. In the portable scenario, drives are discovered dynamically and the count is unknown — a table scales better.

**Table columns**:

| Column | Source |
|--------|--------|
| Device | `/dev/sdX` or `/dev/nvmeXn1` |
| Model | `smartctl -i` |
| Serial | `smartctl -i` |
| Capacity | `lsblk` / `smartctl -i` |
| Interface | NVMe / SAS / SATA |
| Form Factor | 3.5", 2.5", E3.S, U.2, U.3 (from sysfs / smartctl) |
| Health | `smart_health.py` score (0-100%) |
| Status | Ready / Wiping / Completed / Failed / **PROTECTED** |

**Features**:
- **Select all** (excludes protected drives automatically)
- **Sort by** any column (health, capacity, interface)
- **Filter** by interface type (NVMe / SAS / SATA) — useful for mixed-drive servers
- **Click row** → detail modal with full SMART, capabilities, recommended method
- **Bulk wipe** — select multiple drives, one confirmation flow, one technician/ticket entry for the batch
- **Wipe all** button — selects all non-protected drives, presents confirmation showing full list with serials for visual verification

### 6. No systemd Service

In a live ISO, the app starts directly from the init script. No systemd service management needed. Simpler startup, faster boot.

### 7. Assessment-First Mode

The ISO defaults to an assessment-first flow:
- On boot, all drives shown with health status — **no wipe controls visible**
- Operator explicitly enters "wipe mode" (toggle or button) to unlock wipe controls
- Prevents accidental wipes from a misclick on first boot
- Makes the ISO useful as a **drive health assessment tool** even when wiping isn't the goal

**Future automation potential**: A config option that auto-starts wipes on all detected drives after a countdown, for unattended bulk operations. Deferred — different safety implications.

---

## E3.S / EDSFF NVMe Support

E3.S drives appear identically to U.2/U.3 NVMe in Linux. They are all EDSFF form factors, but the kernel's NVMe subsystem is form-factor agnostic:

- Appear as `/dev/nvme0n1`, `/dev/nvme1n1`, etc.
- `nvme id-ctrl` works the same
- `smartctl -j -x` works the same
- `nvme sanitize` works the same
- `sg_sanitize` is **not** needed — these are PCIe NVMe, not SCSI/SAS

The only physical difference is the connector (MCIO or SlimSAS x8 instead of SFF-8643/SFF-8639), handled by the PCIe controller driver, not by the app. Current NVMe code paths handle E3.S out of the box.

E3.S drives are typically in direct PCIe slots (not behind a SAS expander), so no multipath/MPIO complexity.

---

## Codebase Strategy

### Decision: Shared codebase with mode flag (Option B)

One codebase, one repo. A `portable_mode` flag (in `policy.json` or env var) changes behavior at runtime.

**Rationale**:
- The core logic (erase engine, SMART parsing, health scoring, verification, cert generation) is complex and gets bug fixes regularly. Duplicating it across two codebases (fork) means double maintenance or silent divergence.
- The changes needed for portable mode are concentrated in a few areas, not scattered throughout:
  - **Discovery**: New `portable_discovery()` function — additive, doesn't modify existing bay-mapping path
  - **Safety**: New boot-USB protection detection — additive concept
  - **Auth**: Already config-driven; portable mode forces localhost-only (already the bypass behavior)
  - **Cert storage**: Config-driven path; point `cert_output_dir` at USB mount
  - **Frontend**: Serve different static directory based on mode (`frontend/portable/` vs `frontend/`)

**Migration path**: Once both editions are stable and validated, refactor toward Option C (shared core library + separate thin apps). Extract core logic into `drive_eraser_core` package with two Flask apps on top. The mode flag approach doesn't prevent this — it delays it.

### Alternatives Considered

**Option A — Separate Fork**:
- Pros: Dead simple, no conditional logic, smaller ISO payload
- Cons: Bug fixes applied twice, inevitable divergence, duplicated core logic
- Rejected: SMART/verification/cert logic is too complex and bug-prone to maintain two copies

**Option C — Shared Core Library + Separate Apps**:
- Pros: Cleanest architecture, no conditionals in core, each app is thin
- Cons: Significant upfront refactoring (extract core from monolithic Flask app), route handlers currently intertwine logic with Flask
- Deferred: Right architecture long-term, but too much upfront cost before validating the portable concept

---

## ISO Build Approach

**Status**: No final decision yet. Ubuntu is the safe default; Alpine is a strong candidate for the portable edition. Both are analyzed in detail below.

### Option 1: Ubuntu-based Live ISO

**Rationale**: Identical environment to production, all tool dependencies are the same, current code works unchanged. Lowest risk for first version.

**Build steps**:

1. Start with a base Ubuntu rootfs (debootstrap or cloud image)
2. Install dependencies: `python3`, `python3-flask`, `python3-socketio`, `smartmontools`, `hdparm`, `nvme-cli`, `sg3-utils`, `dd`, `lsblk`
3. Copy the Drive Eraser app into the rootfs
4. Configure auto-start: init script that launches Flask + kiosk browser (X + Chromium/Firefox in kiosk mode at `localhost:5000`)
5. Package as squashfs + overlayfs for live filesystem
6. Add bootloader (GRUB or isolinux)
7. Generate ISO with `xorriso` or `genisoimage`

**Build tooling**: `live-build` (Debian/Ubuntu), `mkosi`, or manual squashfs+initramfs assembly.

**Pros**:
- Identical environment to production — all current code works unchanged
- Large package ecosystem, well-documented
- All Python dependencies have pre-built wheels (glibc)
- systemd available (if needed for the portable version)

**Cons**:
- ISO size: 1-2 GB
- Boot time: 30-60 seconds
- Larger attack surface (more running services, packages)

### Option 2: Alpine-based Live ISO

**Rationale**: Alpine is dramatically lighter than Ubuntu. For a USB-bootable tool where size and boot time directly affect operator experience, this is a significant advantage. The portable edition is also simpler than the main app (no bay mapping, no admin panel, no systemd service), which minimizes the challenges that Alpine would otherwise introduce.

#### Weight Comparison

| Metric | Ubuntu Server | Alpine |
|--------|--------------|--------|
| Base install | ~1.5-2 GB | ~5-8 MB |
| With all app packages | ~2.5-3 GB | ~150-300 MB |
| RAM at idle | ~200-400 MB | ~30-80 MB |
| Boot time | 30-60s | 10-20s |
| Resulting ISO size | 1-2 GB | 200-400 MB |

#### Advantages Beyond Weight

- **Fast package installs**: `apk` is significantly faster than `apt-get`. Matters for ISO builds and rebuilds.
- **Simpler init system**: OpenRC is more transparent and easier to debug than systemd for simple services. Logs go to standard files, no journalctl needed.
- **Better for containers**: If the app is ever containerized (e.g., for CI testing), Alpine is the standard base for Docker images.
- **Predictable releases**: Clear edge/stable channel. No Ubuntu LTS upgrade treadmill every 2 years.
- **Minimal attack surface**: Far fewer running services and packages by default. Relevant for a tool that handles sensitive data, even though the app is localhost-only.
- **Fast boot**: 10-20 seconds vs 30-60 seconds. Operators booting unfamiliar servers will appreciate this.

#### Challenges

**1. pyudev / Hot-Plug Detection — Potential Blocker**

The main app uses `pyudev` for hot-plug drive detection via udev events. pyudev depends on libudev, which is part of systemd. Alpine uses OpenRC, not systemd.

- **Potential fix**: Alpine has `eudev` — a standalone udev implementation that provides libudev without requiring full systemd. Install `apk add eudev eudev-libs py3-pyudev` (or build pyudev from pip against eudev headers). This *should* work but needs validation before committing.
- **Portable-specific mitigation**: The portable edition may not need hot-plug detection at all. Drives are present at boot, and the operator can click "rescan" which does a fresh `lsblk` enumeration. This sidesteps the pyudev question entirely for the portable ISO.
- **Fallback**: If eudev doesn't work with pyudev and hot-plug is needed, replace with a polling approach (periodic `lsblk` comparison) or raw netlink uevent monitoring. This is a code change, not just a packaging change.

**2. Pillow — C Extension Compilation**

Pillow 11.1.0 has no pre-built wheels for musl libc, so it always compiles from source on Alpine.

- Install build dependencies: `apk add jpeg-dev zlib-dev freetype-dev lcms2-dev libwebp-dev openjpeg-dev tiff-dev python3-dev build-base`
- Build time adds ~2-3 minutes to ISO generation
- Not a blocker — just slower builds and requires build tooling in the image

**3. Python Wheel Availability**

Most Python deps are pure Python (Flask, flask-cors, flask-limiter, flask-socketio, python-socketio, beautifulsoup4, jsonschema) — these install fine via pip on Alpine. Pillow is the only C-extension package.

**4. Init System — OpenRC Instead of systemd**

The portable edition doesn't need systemd service management (the app starts directly from an init script), so this is minimal. An OpenRC init script for Alpine is ~30-40 lines. No resource limits (cgroups) needed for the portable scenario — it's a live ISO with a single purpose.

**5. Package Name Differences**

Some package names differ between Ubuntu and Alpine:

| Ubuntu | Alpine |
|--------|--------|
| `sg3-utils` | `sg3_utils` |
| `smartmontools` | `smartmontools` |
| `nvme-cli` | `nvme-cli` |
| `hdparm` | `hdparm` |
| `python3-pyudev` | `py3-pyudev` (needs eudev) |
| `python3-dev` | `python3-dev` |
| `libjpeg-dev` | `jpeg-dev` |

These are straightforward mappings but need to be documented in the build script.

#### Alpine Build Steps

1. Start with Alpine `linux-virt` or `linux-lts` base
2. Install dependencies: `apk add python3 python3-dev py3-pip build-base smartmontools hdparm nvme-cli sg3_utils util-linux lshw eudev eudev-libs jpeg-dev zlib-dev freetype-dev lcms2-dev libwebp-dev openjpeg-dev tiff-dev`
3. Create Python venv, install pip dependencies (Pillow compiles from source)
4. Copy the Drive Eraser app into the rootfs
5. Configure auto-start: OpenRC init script that launches Flask + kiosk browser
6. Package as squashfs + initramfs
7. Add bootloader (GRUB or isolinux)
8. Generate ISO with `xorriso` or `genisoimage`

**Build tooling**: `mkosi` (supports Alpine), manual squashfs+initramfs, or Alpine's own `mkinitfs` + `setup-disk` tooling.

#### When Alpine Makes Sense for the Portable Edition

- The portable edition is simpler (no bay mapping, no admin panel, no systemd service)
- Hot-plug detection may not be needed (drives present at boot, manual rescan available)
- ISO size and boot time directly affect operator experience in the field
- The app is localhost-only, so the smaller attack surface is a bonus

#### When Ubuntu Still Makes Sense

- If hot-plug detection via pyudev is required in the portable edition
- If you want zero environment differences between main app and portable
- For a first prototype to validate the concept quickly (Ubuntu cloud images are well-documented for live ISO conversion)

### Option 3: Buildroot / Custom Initramfs

| Metric | Value |
|--------|-------|
| ISO Size | 100-200 MB |
| Boot Time | Very fast |

**Pros**: Complete control, tiny image.
**Cons**: Significant build complexity, every package configured manually.

**Assessment**: Too much build complexity for the current stage. Could be revisited if extreme minimization is needed later.

### Summary

| Base | ISO Size | Boot Time | Complexity | Risk |
|------|----------|-----------|------------|------|
| Ubuntu | 1-2 GB | 30-60s | Lowest | Lowest |
| Alpine | 200-400 MB | 10-20s | Medium | Medium (pyudev/eudev validation needed) |
| Buildroot | 100-200 MB | Very fast | High | High |

**No final decision yet.** Ubuntu is the safe default for a first prototype. Alpine is a strong candidate for the portable edition given the size and boot time advantages, and the portable edition's simpler scope minimizes the challenges. The pyudev/eudev compatibility question should be validated early if Alpine is pursued.

---

## Certificate Storage Design

### Decision: Single USB, two partitions

- **Partition 1**: Bootable ISO (read-only after boot)
- **Partition 2**: FAT32 cert storage (mounted at `/mnt/certs`)
- App writes certs to `/mnt/certs/<job_id>/` as jobs complete
- FAT32 for maximum portability — operator can plug USB into any machine to read certs
- Boot partition is identified and hard-locked at boot time

### Alternatives Considered

**Single USB, same partition**: Riskier — if wipe somehow targets the USB, certs are gone. Needs very careful device identification.

**Two USB drives**: Safest (boot USB + cert USB), but operator manages two devices. More complex operationally.

---

## Portable Mode Config Defaults

The portable edition would ship with a `policy.json` tuned for the portable scenario:

```json
{
  "portable_mode": true,
  "bind_address": "127.0.0.1",
  "port": 5000,
  "station_id": "portable-iso",
  "cert_output_dir": "/mnt/certs",
  "lan_passphrase": null,
  "slack_webhook_url": null,
  "allowed_cors_origins": ["http://127.0.0.1:5000"],
  "post_erase_marker": true,
  "allow_method_override": true,
  "method_priority": {
    "nvme": ["crypto", "block", "overwrite"],
    "sas": ["crypto", "block", "overwrite"],
    "sata": ["crypto", "block", "overwrite"]
  },
  "prewipe_health_gate_enabled": true,
  "assessment_mode_default": true
}
```

Key differences from main app defaults:
- `portable_mode: true` — activates portable code paths
- `bind_address: "127.0.0.1"` — localhost only, no network exposure
- `cert_output_dir: "/mnt/certs"` — USB cert partition instead of local `data/certs/`
- `lan_passphrase: null` — no LAN auth needed
- `slack_webhook_url: null` — air-gapped, no external calls
- `assessment_mode_default: true` — start in assessment mode, require explicit wipe mode entry

---

## Additional Features & Considerations

Features are tagged with priority: **[v1]** (target for first version), **[future]** (deferred), **[maybe]** (under consideration), **[unlikely]** (noted but improbable), **[idea]** (needs fleshing out).

### Safety & Identification

#### [v1] Drive Locate LED Support

On a dedicated wipe station, bay numbers tell you which drive is which. In the field, you're staring at a server full of drives and `/dev/sda` means nothing physically.

Most enterprise backplanes and HBA controllers support **SCSI Enclosure Services (SES)** or **sgpio** for drive locate/identify LEDs. The app could:
- Send a locate command when the operator hovers over or clicks a table row — the corresponding drive's LED blinks
- Use `sgpio`, `ses`, `ledctl`, or `sg_ses` commands
- This is a **massive safety improvement** — the operator can visually confirm "yes, that's the drive I'm about to wipe"

This alone could justify the portable edition existing. Without it, there's a real risk of wiping the wrong drive on an unfamiliar server.

**Implementation note**: This feature should also be added to the main app — it's valuable in both contexts. On the main app with a known bay mapping, the LED command would target the bay's mapped device. On the portable edition, it would target the device directly.

#### [maybe] Pre-Wipe Drive Content Summary

Before wiping, show what's on the drive — not data, just metadata:
- Filesystem type(s) detected on partitions
- OS detection (e.g., "Linux ext4", "Windows NTFS", "VMware VMFS")
- Partition table type (GPT/MBR)
- Whether the drive appears to be a boot drive

This gives the operator a last-chance sanity check: "I'm about to wipe a 4TB drive that has Windows Server on it — is that right?"

#### [unlikely] Boot Drive Detection Beyond USB

The current design protects the boot USB. But what about the server's own OS drives? If someone boots the ISO on a server that's still running its OS from internal storage, those OS drives should be flagged.

In practice, this scenario is unlikely — the operator is at a server specifically to wipe its drives, and the server would typically be powered off or its OS drives are the targets. Noted for completeness but not expected to be a real-world concern.

### Operational Workflow

#### [v1] Hot-Swap Continuous Mode

If the server has hot-swap bays, the operator might want to wipe a batch of drives one at a time, swapping each out after completion. The app should support a continuous workflow:
- Wipe drive → cert saved → "Ready for next drive" → operator swaps → click rescan → wipe next
- No need to reboot between drives
- Keep a running session summary of all drives wiped in this session

This is probably the most common field use pattern — you have a stack of drives and one server.

#### [v1] Wipe Time Estimation

Show estimated completion time based on drive capacity and selected method:
- Overwrite (`dd`): roughly capacity / write speed
- Crypto/block erase: typically minutes regardless of capacity (firmware-managed)
- This helps the operator plan — "I can get through 8 drives in 2 hours if I use crypto erase"

#### [v1] Concurrent Wipe Throttling

On an unfamiliar server, you don't know the HBA/PCIe bandwidth or thermal constraints. The portable edition should default to **conservative concurrent wipe limits** — maybe 2-4 at a time instead of the main app's 34. The operator can increase if they're confident the hardware can handle it.

#### [v1] Failure Recovery

If a wipe fails midway, the drive is in an unknown state. The app should:
- Detect partial wipe state on rescan
- Offer retry with same method or fallback to a different method
- Log the failure reason clearly in the audit trail
- Never silently leave a drive in a half-wiped state without flagging it

**Open implementation questions**:
- **How to detect where it left off without taking nearly as long?** A full scan of a partially-wiped 4TB drive to find the last written sector would take significant time. Options to explore:
  - Check the job log / SQLite record for the last known progress percentage (if the process was interrupted, the last reported progress is stored)
  - For overwrite: read a few sampled sectors at known progress checkpoints to estimate where writing stopped
  - For crypto/block erase: these are atomic firmware operations — they either completed or didn't. A partial state means the controller didn't finish, and the drive should be re-sanitized from scratch, not resumed
  - For overwrite specifically: the `dd` process writes sequentially, so checking the first non-zero sector after the last known checkpoint gives a rough resume point
- **How to verify that is truly the correct point to pick up from?** This is the harder question. If the drive was removed and reinserted, or if a different drive is now in the slot, resuming from a checkpoint would be catastrophic. Safeguards needed:
  - Match serial number before considering resume
  - Match capacity and model
  - If any mismatch, treat as a new drive — no resume
  - Even with a match, the safest approach may be to **restart from scratch** rather than resume, since the time saved by resuming isn't worth the risk of an incomplete wipe going undetected
  - Resume should be opt-in (operator explicitly chooses "resume" vs "start over"), not automatic

**Recommendation**: For v1, detect partial wipe state and flag it, but **default to starting over** rather than resuming. Resume from checkpoint is a future optimization that requires careful validation.

### Compliance & Audit

#### [v1] Full Audit Log on USB

Don't just store certificates — store a **complete audit trail** on the USB:
- Every drive detected (even if not wiped)
- Every wipe attempt (including failures)
- Technician name, ticket number, timestamps
- Method used, verification result
- Drive identity (model, serial, capacity, firmware)
- Session metadata (ISO version, boot time, server hardware info)

This is critical for compliance. If an auditor asks "prove you wiped these drives," the cert is good. If they ask "what happened to the drives you couldn't wipe," you need the audit log.

**USB wear consideration**: USB flash drives wear out faster than SSDs or HDDs from constant writes. The audit log should **not** write continuously during wipe operations. Instead:
- Accumulate audit entries in RAM (tmpfs) during the session
- Flush to USB at key milestones: after each job completes, after each batch, and on explicit "save audit log" action
- This balances durability (certs and audit entries survive if the app crashes between jobs) with USB longevity (not writing log lines every few seconds)
- The certificate for each completed job is written immediately (single file, one write) — this is acceptable wear
- The audit log is the concern — batch it

#### [idea] Certificate of Destruction Format

The existing certificates are already quite formal. They include:
- NIST SP 800-88 category (Clear/Purge/Unclassified) with basis text
- DoD 5220.22-M compliance text
- HMAC-SHA256 signature with PBKDF2 key derivation
- Verification evidence (primary, secondary, supplemental marker)
- Software versions, SMART diffs, bad sector info
- Claim limitations disclaimer

What a "formal Certificate of Destruction" format would add beyond this:
- **Physical disposition field** — what happened to the drive after wiping (returned to service, sent to recycler, physically destroyed)
- **Operator signature/attestation** — a field where the technician acknowledges the wipe was performed under their authority
- **Witness signature field** — optional second-person verification for high-security contexts
- **Formal letterhead/layout** — the current HTML cert is functional but could be formatted as a more formal document for printing and signing
- **Chain of custody** — who handled the drive from intake to wipe to disposition

This is a presentation/extension layer on top of the existing cert data, not a fundamental change to what's captured. Worth fleshing out as a feature for both the main app and portable edition.

#### [unlikely] QR Code on Certificates

Add a QR code to HTML certificates that encodes the job ID + key verification fields. An auditor can scan it with a phone to quickly verify authenticity against a database later.

**Limitation**: This requires an externally accessible database to verify against, which conflicts with the air-gapped design and the organization's small external volume. The certs are mostly for internal drives. Noted for completeness but probably not going to be implemented.

#### [future] ISO Integrity Verification

How does an auditor know the ISO wasn't tampered with? Options to explore for future discussion:

1. **GPG-signed ISO**: Sign the ISO image with a GPG key. The boot process verifies the signature before proceeding. Requires GPG key management infrastructure.
2. **SHA-256 checksum display**: Display the ISO version + checksum prominently on the UI. The operator can manually verify against a known-good checksum. Simple but relies on operator diligence.
3. **Embedded build hash in certificates**: Include the ISO build hash in every certificate generated. This extends the chain of trust from the tool itself to the certificates it produces — an auditor can verify that the cert was generated by a known version of the ISO.
4. **Secure boot integration**: Use UEFI Secure Boot with a custom signing key. The ISO only boots on systems that trust the key. Most restrictive but highest assurance.
5. **Tamper-evident initramfs**: Include a checksum of the rootfs in the initramfs (which is loaded by the bootloader). Boot fails if the rootfs has been modified after ISO creation.

All options noted for future discussion. The right approach depends on the threat model and how much friction is acceptable for the operator.

### Hardware & Environment

#### [idea] IPMI / iLO Virtual Media Scenario

Operators might mount the ISO via **IPMI/iLO virtual media** instead of a physical USB. This changes the cert storage situation — there's no physical USB to write back to. Options to explore:

1. **Second virtual media mount**: Mount a disk image as a second virtual media device. The app writes certs to this mounted image. Operator downloads the image file afterward via the iLO web interface.
2. **Browser download**: The operator is connected via iLO web console. The app offers a "download all certs" button that sends a ZIP file through the browser. Works but requires the operator to actively download before shutting down.
3. **Network export to specified IP**: After wipes complete, the app pushes certs to a specified IP on the management network. Technically network access but on a management LAN, not the internet.
4. **Hybrid**: Support both physical USB and virtual media. Detect at boot which mode is active and adjust cert storage strategy accordingly.

This is a real use case in data centers where physical access is limited. Worth fleshing out the implementation details as the portable edition develops.

#### [future] Hardware Inventory Export

Before wiping anything, export a full hardware inventory report:
- All detected drives with model, serial, capacity, interface, health score
- Server hardware info (manufacturer, model, serial, CPU, RAM)
- HBA/PCIe controller info
- This report could be saved to USB as a pre-wipe record

Useful for asset tracking, disposal documentation, and deciding what to do with drives before committing to wipes.

#### [future] Server Power Control via IPMI

If the operator has IPMI access, the app could:
- Power off the server after all wipes complete (unattended operation)
- This enables a "start wipes, walk away, come back to a powered-off server with certs on USB" workflow
- Ties into the deferred auto-wipe concept — future automation path

### Certificate Management

#### [v1] Multi-Session Cert Accumulation

If the operator does multiple server visits without clearing the USB, certs accumulate. Need:
- Organize certs by session/date/server
- A cert browser UI within the app (view certs from previous sessions on this USB)
- A "clear certs" function (with confirmation) to free up USB space
- Disk space monitoring on the cert partition — warn if USB is getting full

#### [v1] Certificate Signing Key on USB

The main app has `strict_audit_mode` with HMAC-SHA256 signing. The portable edition should support this too:
- Pre-load a signing key onto the USB cert partition
- App reads the key at boot, signs every certificate
- This ensures certs from the portable edition are cryptographically verifiable, not just plain JSON/HTML

### Niche but Valuable

#### [v1] NVMe Format (Quick Erase)

In addition to `nvme sanitize`, NVMe drives support `nvme format` which does a quick logical format. It's faster than sanitize but less thorough. Useful when:
- Time is constrained and the highest security level isn't required
- The operator wants to quickly "clean" drives for reuse rather than destruction
- Could be offered as a "quick wipe" vs "secure wipe" option

#### [future] PXE Boot Option

For data centers with PXE infrastructure, network-boot the tool instead of USB. No physical media to manage, and the ISO image lives on a deployment server. Cert storage would need to go to a network share or local download. More specialized use case but worth noting.

#### [future] Write-Protect USB Hardware

Recommend (or require) USB drives with a **physical write-protect switch** for the boot partition. This eliminates the risk of the boot partition being corrupted by a misdirected wipe command. Some industrial USB drives have this. The app could detect whether the boot partition is read-only and warn if it isn't.

---

## Open Questions / Future Considerations

- **Triage features**: Should the portable table include triage-style action recommendations (Wipe / Scratch / Destroy) based on health scoring? The triage engine already exists in the main app and could be reused.
- **Bulk certificate export**: Should there be a "export all certs as ZIP" button for easy transfer off the USB?
- **Multi-pass wiping**: Should the portable edition support multi-pass overwrite methods (DoD 3-pass, etc.) for high-security scenarios? Currently the main app does single-pass only.
- **ISO update mechanism**: How does the operator get a new ISO version? Re-download and re-flash, or an in-place update mechanism?
- **Hardware compatibility testing**: Need to validate on various server hardware (Dell, HP, Supermicro) with different HBA/PCIe controllers
- **Secure boot**: Should the ISO support UEFI Secure Boot, or is legacy BIOS / unsigned UEFI sufficient for the target environments?
- **Drive Locate LED implementation**: Which tools (`sgpio`, `ses`, `ledctl`, `sg_ses`) are most broadly compatible across server vendors? Need to test on Dell, HP, Supermicro hardware.
- **Failure recovery resume vs restart**: Validate that "default to starting over" is the right call. Profile how long partial-wipe detection takes on large drives.
- **IPMI virtual media cert storage**: Which of the four options (second virtual media, browser download, network export, hybrid) best fits the operational workflow?
- **Audit log flush frequency**: What's the right balance between USB wear and audit durability? Per-job flush seems right but needs validation with real USB hardware.
- **Certificate of Destruction format**: Flesh out the formal CoD template — physical disposition, operator attestation, witness signature, chain of custody fields.

---

## Prototyping Path

To validate the concept quickly before building a full ISO pipeline:

1. Take an Ubuntu Server cloud image (qcow2)
2. Install the current app + dependencies
3. Add a simple init script that starts Flask + kiosk browser
4. Convert to a live ISO using `cloud-localds` + `mkisofs`
5. Test in a VM (QEMU/VirtualBox) with virtual disks
6. Once validated in VM, refine the ISO build process for production use
