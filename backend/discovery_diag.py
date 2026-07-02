# Diagnostic snapshot module for drive disappearance investigation.
# Captures system state (by-path, sysfs, PHY counters, kernel logs) at key
# moments and writes to data/logs/discovery_diag.log.
#
# Enabled when policy.json has "discovery_diag": true or env var DISCOVERY_DIAG=1.

import os
import re
import time
import json
import subprocess
import threading
from datetime import datetime, timezone

from common import get_logs_dir, load_policy, get_config_dir

_DIAG_LOG_PATH = None
_DIAG_LOCK = threading.Lock()
_DIAG_ENABLED = None
_DIAG_CHECKED_AT = 0.0
_MAX_LOG_SIZE = 10 * 1024 * 1024  # 10 MB — rotate when exceeded
_MAX_KERNEL_LINES = 100

_PHY_COUNTER_FILES = [
    "invalid_dword_count",
    "loss_of_dword_synchronization_count",
    "phy_reset_problem_count",
    "running_disparity_error_count",
]


def _is_diag_enabled():
    """Check if diagnostics are enabled. Cached for 30 seconds to avoid re-reading policy."""
    global _DIAG_ENABLED, _DIAG_CHECKED_AT
    now = time.monotonic()
    if _DIAG_ENABLED is not None and (now - _DIAG_CHECKED_AT) < 30.0:
        return _DIAG_ENABLED

    env_flag = os.environ.get("DISCOVERY_DIAG", "").lower() in ("1", "true", "yes")
    policy_flag = False
    try:
        policy = load_policy()
        policy_flag = bool(policy.get("discovery_diag", False))
    except Exception:
        pass

    _DIAG_ENABLED = env_flag or policy_flag
    _DIAG_CHECKED_AT = now
    return _DIAG_ENABLED


def _get_log_path():
    """Get the diagnostic log file path, creating the logs dir if needed."""
    global _DIAG_LOG_PATH
    if _DIAG_LOG_PATH is None:
        _DIAG_LOG_PATH = os.path.join(get_logs_dir(), "discovery_diag.log")
    return _DIAG_LOG_PATH


def _rotate_if_needed():
    """Rotate the diagnostic log if it exceeds the max size."""
    path = _get_log_path()
    try:
        if os.path.getsize(path) > _MAX_LOG_SIZE:
            rotated = path + ".1"
            if os.path.exists(rotated):
                os.remove(rotated)
            os.rename(path, rotated)
    except (OSError, IOError):
        pass


def _write_log(text):
    """Write text to the diagnostic log with thread safety."""
    with _DIAG_LOCK:
        _rotate_if_needed()
        path = _get_log_path()
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(text)
        except (OSError, IOError):
            pass


def _read_sysfs_file(path):
    """Read a sysfs file and return stripped content, or None on error."""
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except (OSError, IOError):
        return None


def _list_dir_safe(path):
    """List a directory safely, returning empty list on error."""
    try:
        return os.listdir(path)
    except (OSError, IOError):
        return []


def _capture_by_path():
    """Capture /dev/disk/by-path/ listing."""
    entries = _list_dir_safe("/dev/disk/by-path/")
    lines = [f"  by-path entries ({len(entries)}):"]
    for entry in sorted(entries):
        full_path = os.path.join("/dev/disk/by-path/", entry)
        try:
            target = os.path.realpath(full_path)
        except (OSError, IOError):
            target = "?"
        lines.append(f"    {entry} -> {target}")
    return "\n".join(lines)


def _capture_block_devices():
    """Capture /sys/class/block/ listing."""
    entries = _list_dir_safe("/sys/class/block/")
    # Filter to disk-like devices (sd*, nvme*, dm-*)
    disks = [e for e in sorted(entries) if re.match(r'^(sd[a-z]+|nvme\d+n\d+|dm-\d+)', e)]
    lines = [f"  block devices ({len(disks)}):"]
    for dev in disks:
        lines.append(f"    {dev}")
    return "\n".join(lines)


def _capture_scsi_devices():
    """Capture /sys/class/scsi_device/ listing with block device mapping."""
    entries = _list_dir_safe("/sys/class/scsi_device/")
    lines = [f"  scsi devices ({len(entries)}):"]
    for entry in sorted(entries):
        scsi_dev_path = os.path.join("/sys/class/scsi_device", entry)
        block_dir = os.path.join(scsi_dev_path, "device", "block")
        block_devs = _list_dir_safe(block_dir)
        block_str = ", ".join(block_devs) if block_devs else "(no block device)"
        lines.append(f"    {entry} -> {block_str}")
    return "\n".join(lines)


def _capture_scsi_device_realpaths():
    """Capture realpaths of SCSI device 'device' symlinks for debugging Strategy 1.

    Shows the actual sysfs tree path for each SCSI device that has a block device.
    This reveals whether the path contains expander/port/end_device info that
    _resolve_via_sysfs_scsi can parse for PCI/expander/PHY matching.
    """
    entries = _list_dir_safe("/sys/class/scsi_device/")
    lines = ["  scsi device realpaths (for sysfs fallback debugging):"]
    for entry in sorted(entries):
        scsi_dev_path = os.path.join("/sys/class/scsi_device", entry)
        block_dir = os.path.join(scsi_dev_path, "device", "block")
        block_devs = _list_dir_safe(block_dir)
        if not block_devs:
            continue
        device_link = os.path.join(scsi_dev_path, "device")
        try:
            real_path = os.path.realpath(device_link)
        except (OSError, IOError):
            real_path = "(error reading realpath)"
        block_str = ", ".join(block_devs)
        lines.append(f"    {entry} block={block_str}")
        lines.append(f"      realpath={real_path}")
    return "\n".join(lines)


def _capture_sas_phy_state():
    """Capture SAS PHY error counters and enable state."""
    phy_base = "/sys/class/sas_phy"
    entries = _list_dir_safe(phy_base)
    phy_names = [e for e in sorted(entries) if e.startswith("phy-")]
    lines = [f"  SAS PHY state ({len(phy_names)} PHYs):"]
    for phy_name in phy_names:
        phy_path = os.path.join(phy_base, phy_name)
        enable = _read_sysfs_file(os.path.join(phy_path, "enable"))
        link_rate = _read_sysfs_file(os.path.join(phy_path, "negotiated_logical_link_rate"))
        counters = {}
        for counter_file in _PHY_COUNTER_FILES:
            val = _read_sysfs_file(os.path.join(phy_path, counter_file))
            counters[counter_file] = val if val is not None else "-"
        lines.append(
            f"    {phy_name}: enable={enable}, link_rate={link_rate}, "
            f"invalid_dword={counters['invalid_dword_count']}, "
            f"loss_of_sync={counters['loss_of_dword_synchronization_count']}, "
            f"phy_reset_problems={counters['phy_reset_problem_count']}, "
            f"disparity_errors={counters['running_disparity_error_count']}"
        )
    return "\n".join(lines)


def _capture_orphaned_block_devices(by_path_entries=None):
    """Identify block devices that have no by-path symlink (orphaned)."""
    if by_path_entries is None:
        by_path_entries = _list_dir_safe("/dev/disk/by-path/")

    # Build set of block device names that have by-path symlinks
    mapped_devs = set()
    for entry in by_path_entries:
        full_path = os.path.join("/dev/disk/by-path/", entry)
        try:
            target = os.path.realpath(full_path)
            dev_name = os.path.basename(target)
            if dev_name:
                mapped_devs.add(dev_name)
        except (OSError, IOError):
            pass

    # Get all block devices
    block_entries = _list_dir_safe("/sys/class/block/")
    disk_devs = [e for e in block_entries if re.match(r'^(sd[a-z]+|nvme\d+n\d+)', e)]

    orphans = [d for d in sorted(disk_devs) if d not in mapped_devs]
    lines = [f"  orphaned block devices (in /sys/class/block but no by-path symlink) ({len(orphans)}):"]
    for dev in orphans:
        # Check if it's in scsi_device
        in_scsi = False
        scsi_device_base = "/sys/class/scsi_device"
        for scsi_entry in _list_dir_safe(scsi_device_base):
            block_dir = os.path.join(scsi_device_base, scsi_entry, "device", "block")
            if dev in _list_dir_safe(block_dir):
                in_scsi = True
                break
        lines.append(f"    {dev} (in scsi_device: {in_scsi})")
    return "\n".join(lines)


def _capture_kernel_scsi_logs():
    """Capture recent kernel SCSI error/reset messages."""
    lines = ["  recent kernel SCSI messages:"]
    try:
        proc = subprocess.run(
            ["journalctl", "-k", "--since", "5 minutes ago", "--no-pager"],
            capture_output=True, text=True, timeout=10
        )
        output = proc.stdout or ""
        # Filter to SCSI-related lines and limit count
        scsi_lines = [l for l in output.splitlines() if re.search(r'scsi|sas|ata|link|reset|error|timeout', l, re.IGNORECASE)]
        if scsi_lines:
            for line in scsi_lines[-_MAX_KERNEL_LINES:]:
                lines.append(f"    {line}")
        else:
            lines.append("    (no SCSI-related kernel messages in last 5 minutes)")
    except Exception as e:
        lines.append(f"    (failed to read kernel logs: {e})")
    return "\n".join(lines)


def _capture_running_wipes():
    """Capture currently running wipe jobs."""
    lines = ["  running wipe jobs:"]
    try:
        from app_config import ERASE_JOBS, ERASE_JOBS_LOCK
        with ERASE_JOBS_LOCK:
            for job_id, job in ERASE_JOBS.items():
                status = job.get("status", "?")
                dev = job.get("request", {}).get("device", "?")
                method = job.get("request", {}).get("method", "?")
                lines.append(f"    job_id={job_id}, device={dev}, method={method}, status={status}")
        if len(lines) == 1:
            lines.append("    (none)")
    except Exception as e:
        lines.append(f"    (failed to read wipe jobs: {e})")
    return "\n".join(lines)


def _capture_discovery_results(results):
    """Capture per-bay discovery results, focusing on missing/failed bays."""
    lines = [f"  discovery results ({len(results)} bays):"]
    for bay in results:
        bay_id = bay.get("bay", "?")
        present = bay.get("present", False)
        device = bay.get("device")
        status = bay.get("status", "?")
        mapping_ok = bay.get("diagnostics", {}).get("mapping", {}).get("ok", "?")
        mapping_reason = bay.get("diagnostics", {}).get("mapping", {}).get("reason", "?")
        collection_reason = bay.get("diagnostics", {}).get("commands", {}).get("collection", {}).get("reason")

        marker = ""
        if not present:
            marker = " *** MISSING ***"
        elif collection_reason:
            marker = f" *** COLLECTION ISSUE: {collection_reason} ***"

        lines.append(
            f"    bay={bay_id}, present={present}, device={device}, "
            f"status={status}, mapping_ok={mapping_ok}, "
            f"mapping_reason={mapping_reason}{marker}"
        )
    return "\n".join(lines)


def capture_snapshot(label, discovery_results=None, extra_info=None):
    """Capture a full diagnostic snapshot and write to the log.

    Args:
        label: Short label for this snapshot (e.g., "pre_discovery", "post_discovery").
        discovery_results: Optional list of bay_info dicts from discovery.
        extra_info: Optional dict of additional key-value pairs to log.
    """
    if not _is_diag_enabled():
        return

    ts = datetime.now(timezone.utc).isoformat()
    monotonic = time.monotonic()

    sections = [
        f"\n{'='*80}",
        f"[{ts}] monotonic={monotonic:.3f} label={label}",
        f"{'='*80}",
        _capture_by_path(),
        _capture_block_devices(),
        _capture_scsi_devices(),
        _capture_scsi_device_realpaths(),
        _capture_sas_phy_state(),
        _capture_orphaned_block_devices(),
        _capture_kernel_scsi_logs(),
        _capture_running_wipes(),
    ]

    if discovery_results is not None:
        sections.append(_capture_discovery_results(discovery_results))

    if extra_info:
        lines = ["  extra info:"]
        for k, v in extra_info.items():
            lines.append(f"    {k}: {v}")
        sections.append("\n".join(lines))

    _write_log("\n".join(sections) + "\n")


def capture_snapshot_text(label="point-in-time"):
    """Capture a full diagnostic snapshot and return it as a string.

    Unlike capture_snapshot(), this does not require diagnostics to be enabled
    and does not write to the log file. Used by the support bundle to always
    include a snapshot regardless of policy setting.

    Args:
        label: Short label for this snapshot.

    Returns:
        String containing the full diagnostic snapshot.
    """
    ts = datetime.now(timezone.utc).isoformat()
    sections = [
        f"Diagnostic snapshot: {ts} (label={label})",
        _capture_by_path(),
        _capture_block_devices(),
        _capture_scsi_devices(),
        _capture_scsi_device_realpaths(),
        _capture_sas_phy_state(),
        _capture_orphaned_block_devices(),
        _capture_kernel_scsi_logs(),
        _capture_running_wipes(),
    ]
    return "\n".join(sections)


def log_udev_event(action, dev_node, sys_path, extra_info=None):
    """Log a udev event with relevant context.

    Args:
        action: "add" or "remove"
        dev_node: Device node path (e.g., /dev/sda)
        sys_path: Sysfs path of the device
        extra_info: Optional dict of additional fields
    """
    if not _is_diag_enabled():
        return

    ts = datetime.now(timezone.utc).isoformat()
    dev_name = os.path.basename(dev_node) if dev_node else "?"

    # Check if block device still exists in /sys/class/block/
    block_exists = os.path.exists(os.path.join("/sys/class/block", dev_name)) if dev_name else False

    # Check if by-path symlink exists for this device
    by_path_entries = _list_dir_safe("/dev/disk/by-path/")
    by_path_for_dev = []
    for entry in by_path_entries:
        full_path = os.path.join("/dev/disk/by-path/", entry)
        try:
            if os.path.realpath(full_path) == dev_node:
                by_path_for_dev.append(entry)
        except (OSError, IOError):
            pass

    sections = [
        f"\n--- UDEV EVENT [{ts}] ---",
        f"  action={action}, dev_node={dev_node}, sys_path={sys_path}",
        f"  block_exists_in_sysfs={block_exists}",
        f"  by_path_symlinks={by_path_for_dev if by_path_for_dev else '(none)'}",
    ]

    if extra_info:
        for k, v in extra_info.items():
            sections.append(f"  {k}: {v}")

    # On remove events, capture PHY state to see if a PHY was disabled
    if action == "remove":
        sections.append(_capture_sas_phy_state())

    _write_log("\n".join(sections) + "\n")


def log_device_resolution_failure(bay_id, slot_config, pci_controller, physical_slot,
                                   slot_type, by_path_checked, sysfs_fallback_result=None):
    """Log details when a device resolution fails for a specific bay.

    Args:
        bay_id: Bay identifier
        slot_config: Slot configuration dict
        pci_controller: PCI controller address
        physical_slot: Physical slot number
        slot_type: Slot type (sas_expander, sas_direct, etc.)
        by_path_checked: String describing the by-path pattern(s) that were checked
        sysfs_fallback_result: Result of sysfs fallback attempt, or None if not attempted
    """
    if not _is_diag_enabled():
        return

    ts = datetime.now(timezone.utc).isoformat()

    # Check if any block device exists that might correspond to this slot
    block_entries = _list_dir_safe("/sys/class/block/")
    disk_devs = [e for e in block_entries if re.match(r'^(sd[a-z]+|nvme\d+n\d+)', e)]

    sections = [
        f"\n--- DEVICE RESOLUTION FAILURE [{ts}] ---",
        f"  bay_id={bay_id}",
        f"  pci_controller={pci_controller}, physical_slot={physical_slot}, slot_type={slot_type}",
        f"  by_path_patterns_checked={by_path_checked}",
        f"  sysfs_fallback_result={sysfs_fallback_result}",
        f"  available_block_devices={sorted(disk_devs)}",
    ]

    _write_log("\n".join(sections) + "\n")
