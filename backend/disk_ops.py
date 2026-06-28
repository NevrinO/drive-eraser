# --- START OF FILE backend/disk_ops.py ---
# OS drive detection and discovery engine

import os
import copy
import json
import re
import time
import logging
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError

from common import get_config_dir, load_policy, DRIVE_DATA_CACHE_TTL
from disk_utils import resolve_bay_device, check_write_tolerance, read_marker_status
from smart_parsing import get_smart_data, get_smart_identity, detect_interface_type, calculate_drive_health_score, get_drive_recommendation, is_drive_ssd, get_triage_thresholds
from disk_capabilities import detect_drive_capabilities
from device_discovery import get_controller_for_device, scan_pci_controllers, generate_master_slot_map, resolve_multipath_parent
from database import record_intake_snapshot
from zero_check_manager import get_manager as get_zero_check_manager

# Performance: per-device cache for expensive drive data (SMART, capabilities, marker).
# Presence detection (by-path resolution) is intentionally NOT cached so drive
# insertion/removal is still detected in near real time on every discovery call.
_DRIVE_DATA_CACHE = {}  # cache_key -> {'data': payload, 'timestamp': ts}
_DRIVE_DATA_CACHE_LOCK = threading.Lock()

# Performance: cached OS drive lookup (OS drive cannot change while the service runs)
# Cached indefinitely until service restart since the OS drive is a static property
_OS_BY_PATH_CACHE = {'data': None}
_OS_BY_PATH_LOCK = threading.Lock()

# WebSocket manager reference (set at startup)
_websocket_manager = None

def set_websocket_manager(ws_manager):
    """Set the WebSocket manager for broadcasting SMART data updates."""
    global _websocket_manager
    _websocket_manager = ws_manager
    logging.getLogger(__name__).info("WebSocket manager set for disk_ops")


def _is_eligible_for_zero_check(drive, manager=None, allow_completed=False):
    """Return (eligible, reason) for starting a zero-check on a drive dict.

    Mirrors the filters used by the auto-enqueue path so the manual endpoint
    cannot start a zero-check on an OS/reserved, locked, currently-wiping,
    USB, or already-marked drive. When allow_completed is True, a previously
    completed check does not block re-checks (used by the manual endpoint).
    """
    if not drive or not drive.get("present"):
        return False, "no drive present"
    if drive.get("locked"):
        return False, "bay is locked"
    role = drive.get("role", "wipe")
    if role in ("os", "reserved"):
        return False, "bay is OS/reserved"
    if drive.get("status") == "RUNNING":
        return False, "wipe is running"
    iface = (drive.get("interface_type") or "").lower()
    if iface not in ("sata", "sas", "nvme"):
        return False, "unsupported interface"
    marker = drive.get("marker") or {}
    if marker.get("status") not in ("none", "corrupted", None):
        return False, "post-erase marker present"
    if manager is not None:
        bay = drive.get("bay")
        if bay:
            status = manager.get_status(bay)
            if allow_completed:
                blocked = ("queued", "running")
            else:
                blocked = ("queued", "running", "completed", "failed", "cancelled")
            if status.get("status") in blocked:
                return False, "zero check already in progress or completed"
    if not drive.get("device"):
        return False, "device not resolved"
    return True, None


def _auto_enqueue_zero_checks(results):
    """After discovery, queue background zero-checks for eligible internal drives.

    Skips drives that are absent, locked, OS/reserved, running a wipe, USB, have a
    valid marker, or already have a zero-check state. Clears state for bays that are
    no longer present.
    """
    if not results or not isinstance(results, list):
        return
    try:
        policy = load_policy(get_config_dir())
    except Exception as e:
        logging.getLogger(__name__).warning(f"Failed to load policy for zero-check enqueue: {e}")
        return
    if not policy.get("prewipe_zero_detection_enabled", True):
        return

    manager = get_zero_check_manager()
    present_bays = set()
    for bay_info in results:
        bay = bay_info.get("bay")
        if not bay:
            continue
        present_bays.add(bay)
        if not bay_info.get("present"):
            manager.clear_state(bay)
            continue

        # If the drive identity in this bay has changed, clear stale completed state
        # so the new drive is not suppressed by a check from the previous drive.
        current_serial = bay_info.get("serial")
        stored_serial = manager.get_status(bay).get("serial")
        if current_serial and stored_serial and current_serial != stored_serial:
            manager.clear_state(bay)

        eligible, _ = _is_eligible_for_zero_check(bay_info, manager)
        if not eligible:
            continue
        manager.start_check(bay, bay_info.get("device"), serial=current_serial)

    # Clear stale state for any bays that disappeared from the results entirely
    for bay in list(manager.get_all_status().keys()):
        if bay not in present_bays:
            manager.clear_state(bay)

# Performance: parallel collection settings
_DISCOVERY_MAX_WORKERS = min(8, os.cpu_count() or 4)
_DISCOVERY_OVERALL_TIMEOUT = 120  # seconds for the whole parallel collection batch

def get_discovery_max_workers():
    """Get the maximum number of workers for parallel drive discovery from policy."""
    try:
        policy = load_policy(get_config_dir())
        workers = policy.get("discovery_max_workers", 8)
        # Clamp to reasonable bounds
        return max(1, min(workers, 32))
    except Exception:
        return _DISCOVERY_MAX_WORKERS


def get_background_smart_max_workers():
    """Get the maximum number of workers for background extended SMART collection."""
    try:
        policy = load_policy(get_config_dir())
        workers = policy.get("background_smart_max_workers", 4)
        # Clamp to reasonable bounds for background load
        return max(1, min(workers, 8))
    except Exception:
        return 4


# Medium #34: Global generation counter for discovery interruption.
# Uses a monotonically increasing counter instead of a boolean flag to avoid
# the cross-operation reset race (Lesson #101). Each discovery captures the
# generation in a thread-local and compares it to detect signals since then.
_discovery_interrupt_generation = 0
_discovery_thread_state = threading.local()

# Phase 1: Persistent background extended SMART collection worker pool
_EXTENDED_SMART_EXECUTOR = None
_EXTENDED_SMART_LOCK = threading.Lock()
_EXTENDED_SMART_PENDING = set()
_shutdown_event = threading.Event()

def _handle_discovery_signal(signum, frame):
    """Signal handler for SIGTERM/SIGINT during discovery operations.

    Uses lock-free atomic increment (safe under CPython GIL) to avoid
    deadlock if signal arrives while _check_discovery_interrupted() is reading.
    """
    global _discovery_interrupt_generation
    _discovery_interrupt_generation += 1
    _shutdown_event.set()

def _check_discovery_interrupted():
    """Check if discovery was interrupted by signal since this thread's operation started."""
    gen = getattr(_discovery_thread_state, 'generation', None)
    if gen is None:
        return False
    return _discovery_interrupt_generation != gen

def invalidate_drive_cache(device=None):
    """Invalidate cached per-device drive data and pending background SMART tasks.

    Args:
        device: Specific device node (e.g. /dev/sda) to invalidate, or None to clear all.
    """
    with _DRIVE_DATA_CACHE_LOCK:
        if device is None:
            _DRIVE_DATA_CACHE.clear()
        else:
            for key in [k for k in _DRIVE_DATA_CACHE if k[1] == device]:
                del _DRIVE_DATA_CACHE[key]
    # Clear pending background SMART keys so the affected drives can be re-enqueued
    with _EXTENDED_SMART_LOCK:
        if device is None:
            _EXTENDED_SMART_PENDING.clear()
        else:
            for key in [k for k in _EXTENDED_SMART_PENDING if k[1] == device]:
                _EXTENDED_SMART_PENDING.discard(key)

# --- PROGRAMMATIC OS DRIVE DETECTION AND OVERRIDES ---

def get_os_parent_device():
    try:
        st = os.stat("/")
        major = os.major(st.st_dev)
        minor = os.minor(st.st_dev)
        
        uevent_path = f"/sys/dev/block/{major}:{minor}/uevent"
        devname = None
        try:
            with open(uevent_path, "r") as f:
                for line in f.read().splitlines():
                    if line.startswith("DEVNAME="):
                        devname = line.strip().split("=")[1]
                        break
        except (FileNotFoundError, OSError):
            pass
                        
        if not devname:
            try:
                res = subprocess.run(["findmnt", "-n", "-o", "SOURCE", "/"], capture_output=True, text=True, timeout=5, shell=False)
                if res.returncode == 0 and res.stdout.strip():
                    src = res.stdout.strip()
                    if src.startswith("/dev/"):
                        devname = src[5:]
            except Exception:
                pass
                
        if not devname:
            try:
                with open("/proc/mounts", "r") as f:
                    for line in f.read().splitlines():
                        parts = line.split()
                        if len(parts) >= 2 and parts[1] == "/":
                            src = parts[0]
                            if src.startswith("/dev/"):
                                devname = src[5:]
                                break
            except (FileNotFoundError, OSError):
                pass

        if not devname:
            return None
            
        def resolve_leaf_parent(name):
            sys_path = f"/sys/class/block/{name}"
            real_path = os.path.realpath(sys_path)
            if "/block/" in real_path:
                parts = real_path.split("/block/")
                if len(parts) > 1:
                    subparts = parts[1].split("/")
                    if len(subparts) > 0:
                        return subparts[0]
            return name

        if devname.startswith("dm-"):
            slaves_dir = f"/sys/class/block/{devname}/slaves"
            if os.path.isdir(slaves_dir):
                slaves = os.listdir(slaves_dir)
                if slaves:
                    return resolve_leaf_parent(slaves[0])
                    
        return resolve_leaf_parent(devname)
    except Exception:
        return None

def get_os_by_path():
    parent_name = get_os_parent_device()
    if not parent_name:
        return None, None
        
    dev_node = f"/dev/{parent_name}"
    by_path_dir = "/dev/disk/by-path/"
    try:
        for entry in os.listdir(by_path_dir):
            full_path = os.path.join(by_path_dir, entry)
            if os.path.islink(full_path):
                if "-part" in entry:
                    continue
                if os.path.realpath(full_path) == os.path.realpath(dev_node):
                    return dev_node, entry
    except (FileNotFoundError, OSError):
        pass

    return dev_node, None

def _get_os_by_path_cached():
    """Cached wrapper around get_os_by_path(). Cached indefinitely until service restart since the OS drive is a static property."""
    with _OS_BY_PATH_LOCK:
        if _OS_BY_PATH_CACHE['data'] is not None:
            return _OS_BY_PATH_CACHE['data']
        data = get_os_by_path()
        if data and data[0]:
            _OS_BY_PATH_CACHE['data'] = data
        return data

# --- DISCOVERY ENGINE ---

def _process_marker_status(marker_status, interface_type, smart):
    """Check marker status and update is_pristine/state fields in-place.

    Shared by _collect_drive_data and _process_single_drive_extended_smart.
    """
    if marker_status.get("status") == "checksum_valid":
        is_pristine = check_write_tolerance(interface_type, smart.get("data_written_raw"), marker_status.get("details", {}).get("data_written_at_wipe"))
        marker_status["is_pristine"] = is_pristine
        marker_status["status"] = "written_since_wipe" if not is_pristine else ("pristine_secure" if marker_status.get("hmac_verified") else "pristine_insecure")


def _build_drive_payload(dev_node, smart, interface_type, capabilities, marker_status, recommendation,
                         health_score, penalty_breakdown, drive_type, command_diagnostics, smart_polling):
    """Build the standardized drive payload dict from collected data.

    Shared by _collect_drive_data and _process_single_drive_extended_smart.
    """
    return {
        "present": True, "device": dev_node, "serial": smart.get("serial"), "model": smart.get("model"), "status": smart.get("status", "UNKNOWN"), "interface_type": interface_type, "drive_type": drive_type, "capacity_str": smart.get("capacity_str", "-"),
        "capabilities": capabilities, "marker": marker_status, "recommendation": recommendation, "health_score": health_score,
        "supported_methods": [m for m, s in {"crypto": capabilities.get("supports_crypto_erase", False), "block": capabilities.get("supports_block_erase", False), "secure_erase": capabilities.get("supports_secure_erase", False), "enhanced_secure_erase": capabilities.get("supports_enhanced_secure_erase", False), "overwrite": capabilities.get("supports_overwrite", False)}.items() if s],
        "diagnostics": {"mapping": {"ok": True, "reason": None}, "commands": command_diagnostics},
        "smart": {
            "temperature": smart.get("temperature"), "reallocated_sectors": smart.get("reallocated_sectors"), "pending_sectors": smart.get("pending_sectors"), "wear_level": smart.get("wear_level"), "power_on_hours": smart.get("power_on_hours"), "power_on_days": smart.get("power_on_days"),
            "interface_errors": smart.get("interface_errors"), "data_read_raw": smart.get("data_read_raw"), "data_read_bytes": smart.get("data_read_bytes"), "data_written_raw": smart.get("data_written_raw"), "data_written_bytes": smart.get("data_written_bytes"),
            "reallocated_normalized": smart.get("reallocated_normalized"), "reallocated_threshold": smart.get("reallocated_threshold"), "capacity_bytes": smart.get("capacity_bytes"), "raw": smart.get("raw"),
            "penalty_breakdown": penalty_breakdown, "smart_polling": smart_polling
        }
    }


def _collect_drive_data(dev_node, resolved_active_path, configured_active_path, configured_type, passphrase, use_identity_only=False):
    """Collect all expensive per-drive data (SMART, capabilities, marker) for one device.

    Runs in a worker thread during parallel discovery. Returns a payload dict that is
    merged into the bay record and cached for DRIVE_DATA_CACHE_TTL seconds.

    Args:
        dev_node: Device path (e.g., "/dev/sda")
        resolved_active_path: Resolved by-path symlink
        configured_active_path: Configured by-path from bay map
        configured_type: Configured interface type
        passphrase: Wipe passphrase for marker verification
        use_identity_only: If True, use fast identity-only SMART collection (smartctl -j -i)
    """
    command_diagnostics = {}
    if use_identity_only:
        smart = get_smart_identity(dev_node, command_diagnostics)
    else:
        smart = get_smart_data(dev_node, command_diagnostics)
    interface_type = detect_interface_type(resolved_active_path or configured_active_path, dev_node, configured_type, smart.get("raw"))
    capabilities = detect_drive_capabilities(interface_type, dev_node, command_diagnostics)
    marker_status = read_marker_status(dev_node, interface_type, passphrase)
    _process_marker_status(marker_status, interface_type, smart)

    # Set health score to null when using identity-only (polling in background)
    if smart.get("smart_polling"):
        health_score = None
        penalty_breakdown = None
        recommendation = {"status": "UNKNOWN", "comment": "SMART data collection in progress"}
    else:
        thresholds = get_triage_thresholds()
        health_score, penalty_breakdown = calculate_drive_health_score(interface_type, smart, smart.get("raw"), thresholds=thresholds)
        recommendation = get_drive_recommendation(interface_type, smart, health_score=health_score, thresholds=thresholds)

    drive_type = "ssd" if is_drive_ssd(interface_type, smart) else "hdd"

    # Phase 4: Record intake snapshot for tracking drive history
    # Skip recording when using identity-only (will record after full collection)
    serial = smart.get("serial")
    if serial and not smart.get("smart_polling"):
        try:
            record_intake_snapshot(serial, smart, recommendation, health_score=health_score)
        except Exception as e:
            logging.getLogger(__name__).warning(f"Failed to record intake snapshot for {serial}: {e}")

    return _build_drive_payload(dev_node, smart, interface_type, capabilities, marker_status, recommendation,
                                health_score, penalty_breakdown, drive_type, command_diagnostics, smart.get("smart_polling", False))

def _apply_drive_payload(bay_info, payload, is_os_drive):
    """Merge a (possibly cached) drive data payload into a bay record.

    Deep-copies the payload so callers mutating the returned bay record can never
    corrupt cached entries shared across requests.
    """
    bay_info.update(copy.deepcopy(payload))
    if is_os_drive:
        bay_info["role"] = "os"
        bay_info["locked"] = True
        bay_info["supported_methods"] = []
        bay_info["recommendation"] = {"status": "LOCKED", "comment": "Active Operating System Disk. Sanitization strictly blocked."}
        if not bay_info["capacity_str"].endswith(" [OS]"):
            bay_info["capacity_str"] = f"{bay_info['capacity_str']} [OS]"

def _apply_collection_failure(bay_info, dev_node, reason):
    """Mark a present drive whose data collection failed/timed out. Not cached, so the next discovery retries."""
    bay_info.update({"present": True, "device": dev_node, "status": "UNKNOWN"})
    bay_info.setdefault("diagnostics", {}).setdefault("commands", {})["collection"] = {"ok": False, "reason": reason}

def _audit_dual_port_deduplication(results):
    """Audit for dual-port SAS drives: if two device nodes share a serial, mark one as secondary path.
    
    This prevents concurrent wipe jobs from being issued to both paths of the same physical drive.
    The first occurrence (by bay order) is considered primary, subsequent duplicates are marked secondary.
    """
    serial_to_devices = {}
    # First pass: collect all serials and their device nodes
    for bay_info in results:
        serial = bay_info.get("serial")
        device = bay_info.get("device")
        if serial and device and bay_info.get("present"):
            if serial not in serial_to_devices:
                serial_to_devices[serial] = []
            serial_to_devices[serial].append(bay_info)
    
    # Second pass: mark secondary paths for duplicates
    for serial, device_list in serial_to_devices.items():
        if len(device_list) > 1:
            # Multiple devices with same serial - mark all but first as secondary
            for i, bay_info in enumerate(device_list):
                if i > 0:  # First device is primary, rest are secondary
                    bay_info["sas_secondary_path"] = True
                    bay_info["recommendation"] = {"status": "LOCKED", "comment": "Secondary path of dual-port SAS drive. Use primary path for operations."}
                    bay_info["supported_methods"] = []
                    bay_info["diagnostics"]["commands"]["collection"] = {"ok": True, "reason": "secondary_path_deduplication"}
                else:
                    bay_info["sas_secondary_path"] = False

def _store_drive_payload(cache_key, payload):
    """Store a fresh payload in the per-device cache and prune expired entries."""
    now = time.time()
    with _DRIVE_DATA_CACHE_LOCK:
        for key in [k for k, v in _DRIVE_DATA_CACHE.items() if (now - v['timestamp']) >= DRIVE_DATA_CACHE_TTL]:
            del _DRIVE_DATA_CACHE[key]
        _DRIVE_DATA_CACHE[cache_key] = {'data': payload, 'timestamp': now}

def _get_cached_drive_payload(cache_key):
    with _DRIVE_DATA_CACHE_LOCK:
        entry = _DRIVE_DATA_CACHE.get(cache_key)
        if entry and (time.time() - entry['timestamp']) < DRIVE_DATA_CACHE_TTL:
            return entry['data']
    return None

def get_cached_smart_data(device_path):
    """Get cached drive payload for a device, encapsulating cache key construction.

    Cache keys are schema-specific (see _discover_drives_enclosure/_discover_drives_legacy):
    - Enclosure schema: (dev_node, dev_node)
    - Legacy schema: (resolved_by_path, dev_node) — by-path not available here, so
      legacy lookups will miss and fall through to a fresh get_smart_data() call.

    Args:
        device_path: Device path (e.g., "/dev/sda")

    Returns:
        Cached payload dict or None if not cached/expired.
    """
    cache_key = (device_path, device_path)
    return _get_cached_drive_payload(cache_key)

def _resolve_device_from_enclosure_slot(slot_config, pci_controller, master_map, expander_sas_address=None):
    """Resolve active device path from enclosure slot configuration using master map.

    Args:
        slot_config: Slot configuration dict with mappings
        pci_controller: PCI controller address for the enclosure
        master_map: Master slot map from generate_master_slot_map()
        expander_sas_address: SAS expander WWN (e.g. '0x500056b3...') or None for direct-attach

    Returns:
        Tuple of (resolved_device_path, interface_type) or (None, None) if not found
    """
    if not slot_config or not isinstance(slot_config, dict):
        return None, None

    mappings = slot_config.get('mappings', {})
    if not mappings:
        return None, None

    # Try each interface type mapping in priority order
    for interface_key, mapping in mappings.items():
        if not mapping or not isinstance(mapping, dict):
            continue

        slot_type = mapping.get('slot_type')
        hw_identifier = mapping.get('hardware_identifier')
        physical_slot = slot_config.get('physical_slot_number')

        if not slot_type or not hw_identifier:
            continue

        # Use persisted mappings directly - do not search master map by physical_slot_number
        # The master map only contains entries for occupied slots, so it fails for empty bays
        # Persisted identifiers in bay_map.json are authoritative
        dev_path = _resolve_device_from_hardware_identifier(
            pci_controller, slot_type, hw_identifier, physical_slot,
            expander_sas_address=expander_sas_address
        )

        if dev_path:
            # Apply MPIO resolution
            dev_name = os.path.basename(dev_path)
            resolved_path = resolve_multipath_parent(dev_name)
            return resolved_path, interface_key

    return None, None


def _resolve_device_from_hardware_identifier(pci_controller, slot_type, hw_identifier, physical_slot, expander_sas_address=None):
    """Resolve actual device path from hardware identifier.

    Args:
        pci_controller: PCI controller address
        slot_type: Slot type (sas_expander, sas_direct, motherboard_sata, pcie_nvme)
        hw_identifier: Hardware identifier (e.g., 'phy-0:0:0', '101', 'ata1')
        physical_slot: Physical slot number
        expander_sas_address: SAS expander WWN (e.g. '0x500056b3...') used to build an
            exact by-path match, preventing cross-expander slot collisions on the same HBA

    Returns:
        Device path string or None if not found
    """
    # Validate slot_type allowlist
    if slot_type not in ('sas_expander', 'sas_direct', 'motherboard_sata', 'pcie_nvme'):
        return None

    # Validate hw_identifier to prevent path traversal when used in os.path.join (Lesson #13)
    if not isinstance(hw_identifier, str) or not hw_identifier:
        return None
    if '..' in hw_identifier or '/' in hw_identifier or '\\' in hw_identifier or '\x00' in hw_identifier:
        return None
    if len(hw_identifier) > 100:
        return None

    # Validate pci_controller against PCI address format (A68)
    if not isinstance(pci_controller, str) or not re.match(r'^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F]\Z', pci_controller):
        return None

    # Validate physical_slot is a non-negative integer (A68)
    if physical_slot is not None:
        if isinstance(physical_slot, bool):
            return None
        if isinstance(physical_slot, int):
            if physical_slot < 0:
                return None
        elif isinstance(physical_slot, str):
            if not physical_slot.isdigit():
                return None
        else:
            return None

    # Validate expander_sas_address against WWN format if provided (A68)
    if expander_sas_address is not None:
        if not isinstance(expander_sas_address, str) or not re.match(r'^0x[0-9a-fA-F]{16}\Z', expander_sas_address):
            return None

    by_path_dir = '/dev/disk/by-path/'

    try:
        by_path_entries = os.listdir(by_path_dir)
    except (OSError, IOError):
        return None

    if slot_type == 'sas_expander':
        # Pattern: pci-{pci_addr}-sas-exp{expander_id}-phy{phy_num}-lun-0
        # When the expander SAS address is known, build an exact prefix that includes it
        # so that slots on different expanders behind the same HBA never cross-match.
        if expander_sas_address:
            exact_prefix = f"pci-{pci_controller}-sas-exp{expander_sas_address}-phy{physical_slot}-"
            for entry in by_path_entries:
                if entry.startswith(exact_prefix):
                    full_path = os.path.join(by_path_dir, entry)
                    if os.path.islink(full_path):
                        return os.path.realpath(full_path)
        else:
            # Fallback: no expander address known — match any expander on this controller
            pattern = f"pci-{pci_controller}-sas-exp"
            for entry in by_path_entries:
                if entry.startswith(pattern) and f"-phy{physical_slot}-" in entry:
                    full_path = os.path.join(by_path_dir, entry)
                    if os.path.islink(full_path):
                        return os.path.realpath(full_path)

    elif slot_type == 'sas_direct':
        # Pattern: pci-{pci_addr}-scsi-{host}:0:{slot}:0
        pattern = f"pci-{pci_controller}-scsi-"
        for entry in by_path_entries:
            if entry.startswith(pattern) and f":0:{physical_slot}:0" in entry:
                full_path = os.path.join(by_path_dir, entry)
                if os.path.islink(full_path):
                    return os.path.realpath(full_path)

    elif slot_type == 'motherboard_sata':
        # Pattern: pci-{pci_addr}-ata{ata_num}
        pattern = f"pci-{pci_controller}-ata"
        for entry in by_path_entries:
            if entry.startswith(pattern) and entry.endswith(f"-ata{physical_slot}"):
                full_path = os.path.join(by_path_dir, entry)
                if os.path.islink(full_path):
                    return os.path.realpath(full_path)

    elif slot_type == 'pcie_nvme':
        # For NVMe, match PCI address between device and slot
        # Hardware identifier can be:
        # 1. Slot folder name (e.g., '168') in /sys/bus/pci/slots/
        # 2. Full by-path (e.g., 'pci-0000:18:00.0-nvme-1') for fallback
        
        # Check if hw_identifier is a full by-path (fallback format)
        if hw_identifier.startswith('pci-') and 'nvme' in hw_identifier:
            # Direct by-path match - return the device if it exists
            by_path_dir = '/dev/disk/by-path'
            try:
                full_path = os.path.join(by_path_dir, hw_identifier)
                if os.path.islink(full_path):
                    return os.path.realpath(full_path)
            except (OSError, IOError):
                pass
            return None
        
        # Otherwise, treat as slot number and use PCI slot matching
        pci_slots_base = "/sys/bus/pci/slots"
        slot_address_file = os.path.join(pci_slots_base, hw_identifier, 'address')
        
        # Read the expected PCI address from the slot's address file
        expected_pci_addr = None
        try:
            with open(slot_address_file, 'r') as f:
                expected_pci_addr = f.read().strip()
        except (OSError, IOError):
            pass
        
        if not expected_pci_addr:
            return None
        
        # Scan NVMe devices and match PCI address
        block_dir = '/sys/class/block'
        try:
            for dev_name in os.listdir(block_dir):
                if dev_name.startswith('nvme'):
                    # Get the device's sysfs path
                    sys_path = f"/sys/class/block/{dev_name}"
                    real_path = os.path.realpath(sys_path)
                    
                    # Traverse up to find the PCI device directory
                    # Path structure: /sys/devices/pci0000:17/0000:17:02.0/0000:18:00.0/nvme/nvme0/nvme0n1
                    # We need to find the LAST PCI device directory (the actual NVMe controller, not the bridge)
                    path_parts = real_path.split('/')
                    device_pci_addr = None
                    
                    for part in reversed(path_parts):
                        # PCI device addresses match pattern: xxxx:xx:xx.x
                        if re.match(r'^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F]$', part):
                            device_pci_addr = part
                            break
                    
                    # Normalize PCI addresses for comparison (strip function number if present)
                    # Slot addresses may be "0000:18:00" while device addresses are "0000:18:00.0"
                    normalized_device = device_pci_addr.split('.')[0] if device_pci_addr else None
                    normalized_expected = expected_pci_addr.split('.')[0] if expected_pci_addr else None
                    
                    if normalized_device and normalized_device == normalized_expected:
                        return f"/dev/{dev_name}"
        except (OSError, IOError):
            pass

    return None


def _collect_pending_serial(pending, passphrase, use_identity_only=False):
    """Serial fallback collection used when the thread pool fails."""
    for item in pending:
        bay_info, is_os_drive, cache_key, dev_node, resolved_path, configured_path, configured_type = item
        try:
            payload = _collect_drive_data(dev_node, resolved_path, configured_path, configured_type, passphrase, use_identity_only)
            _store_drive_payload(cache_key, payload)
            _apply_drive_payload(bay_info, payload, is_os_drive)
        except Exception as e:
            logging.getLogger(__name__).warning(f"Drive data collection failed for {dev_node}: {e}")
            _apply_collection_failure(bay_info, dev_node, f"collection_failed: {e}")

def _collect_pending_parallel(pending, passphrase, use_identity_only=False):
    """Collect drive data for all pending bays in parallel with bounded workers."""
    max_workers = min(get_discovery_max_workers(), len(pending))
    executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="drive-discovery")
    futures = {}
    try:
        for item in pending:
            bay_info, is_os_drive, cache_key, dev_node, resolved_path, configured_path, configured_type = item
            futures[executor.submit(_collect_drive_data, dev_node, resolved_path, configured_path, configured_type, passphrase, use_identity_only)] = item
        remaining = set(futures)
        try:
            for future in as_completed(futures, timeout=_DISCOVERY_OVERALL_TIMEOUT):
                remaining.discard(future)
                bay_info, is_os_drive, cache_key, dev_node = futures[future][0], futures[future][1], futures[future][2], futures[future][3]
                try:
                    payload = future.result()
                except Exception as e:
                    logging.getLogger(__name__).warning(f"Drive data collection failed for {dev_node}: {e}")
                    _apply_collection_failure(bay_info, dev_node, f"collection_failed: {e}")
                    continue
                _store_drive_payload(cache_key, payload)
                _apply_drive_payload(bay_info, payload, is_os_drive)
        except FuturesTimeoutError:
            for future in remaining:
                future.cancel()
                item = futures[future]
                logging.getLogger(__name__).warning(f"Drive data collection timed out for {item[3]}")
                _apply_collection_failure(item[0], item[3], "collection_timeout")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

def _get_extended_smart_executor():
    """Lazy-initialize the persistent background extended SMART thread pool."""
    global _EXTENDED_SMART_EXECUTOR
    with _EXTENDED_SMART_LOCK:
        if _EXTENDED_SMART_EXECUTOR is not None:
            return _EXTENDED_SMART_EXECUTOR
        if _shutdown_event.is_set():
            return None
        workers = get_background_smart_max_workers()
        _EXTENDED_SMART_EXECUTOR = ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="ext-smart"
        )
        logging.getLogger(__name__).info(f"Started background extended SMART pool with {workers} workers")
        return _EXTENDED_SMART_EXECUTOR


def stop_extended_smart_pool(wait=True):
    """Shut down the persistent background extended SMART pool."""
    global _EXTENDED_SMART_EXECUTOR
    with _EXTENDED_SMART_LOCK:
        if _EXTENDED_SMART_EXECUTOR is not None:
            _EXTENDED_SMART_EXECUTOR.shutdown(wait=wait)
            _EXTENDED_SMART_EXECUTOR = None
            logging.getLogger(__name__).info("Stopped background extended SMART pool")
        # Clear pending set so cancelled futures do not leak entries
        _EXTENDED_SMART_PENDING.clear()


def _process_single_drive_extended_smart(item, passphrase):
    """Collect extended SMART data for a single drive and update the cache."""
    cache_key, dev_node, resolved_path, configured_path, configured_type, enclosure_id, slot_number = item
    logger = logging.getLogger(__name__)
    try:
        # Check if shutdown was requested
        if _shutdown_event.is_set():
            logger.info(f"Skipping extended SMART for {dev_node}, shutdown requested")
            return

        # Avoid redundant work if another task already cached this drive
        cached = _get_cached_drive_payload(cache_key)
        if cached is not None and not cached.get("smart", {}).get("smart_polling", True):
            logger.debug(f"Skipping extended SMART for {dev_node}, already cached")
            return

        # Collect full extended SMART data
        command_diagnostics = {}
        smart = get_smart_data(dev_node, command_diagnostics)
        interface_type = detect_interface_type(resolved_path or configured_path, dev_node, configured_type, smart.get("raw"))
        capabilities = detect_drive_capabilities(interface_type, dev_node, command_diagnostics)
        marker_status = read_marker_status(dev_node, interface_type, passphrase)
        _process_marker_status(marker_status, interface_type, smart)

        thresholds = get_triage_thresholds()
        health_score, penalty_breakdown = calculate_drive_health_score(interface_type, smart, smart.get("raw"), thresholds=thresholds)
        recommendation = get_drive_recommendation(interface_type, smart, health_score=health_score, thresholds=thresholds)
        drive_type = "ssd" if is_drive_ssd(interface_type, smart) else "hdd"

        # Record intake snapshot now that we have full data
        serial = smart.get("serial")
        if serial:
            try:
                record_intake_snapshot(serial, smart, recommendation, health_score=health_score)
            except Exception as e:
                logger.warning(f"Failed to record intake snapshot for {serial}: {e}")

        # Build full payload with smart_polling: false
        payload = _build_drive_payload(dev_node, smart, interface_type, capabilities, marker_status, recommendation,
                                       health_score, penalty_breakdown, drive_type, command_diagnostics, False)

        # Update cache with full data
        _store_drive_payload(cache_key, payload)
        logger.info(f"Background extended SMART collection completed for {dev_node}")

        # Broadcast WebSocket event with updated SMART data
        if _websocket_manager:
            try:
                _websocket_manager.emit('smart_data_updated', {
                    'event': 'smart_data_updated',
                    'device': dev_node,
                    'enclosure_id': enclosure_id,
                    'slot_number': slot_number,
                    'smart': payload.get('smart'),
                    'health_score': payload.get('health_score'),
                    'recommendation': payload.get('recommendation')
                })
                logger.info(f"Broadcasted SMART data update for {dev_node}")
            except Exception as e:
                logger.warning(f"Failed to broadcast SMART data update for {dev_node}: {e}")

    except Exception as e:
        logger.warning(f"Background extended SMART collection failed for {dev_node}: {e}")
    finally:
        with _EXTENDED_SMART_LOCK:
            _EXTENDED_SMART_PENDING.discard(cache_key)


def _submit_drive_for_extended_smart(item, passphrase):
    """Submit a drive for background extended SMART collection if not already pending."""
    cache_key = item[0]
    if _shutdown_event.is_set():
        return
    with _EXTENDED_SMART_LOCK:
        if cache_key in _EXTENDED_SMART_PENDING:
            return
        _EXTENDED_SMART_PENDING.add(cache_key)
    try:
        executor = _get_extended_smart_executor()
        if executor is None:
            with _EXTENDED_SMART_LOCK:
                _EXTENDED_SMART_PENDING.discard(cache_key)
            return
        executor.submit(_process_single_drive_extended_smart, item, passphrase)
    except RuntimeError:
        with _EXTENDED_SMART_LOCK:
            _EXTENDED_SMART_PENDING.discard(cache_key)

def discover_drives(bay_map_path='/opt/drive-eraser/config/bay_map.json', running_devices=None, skip_auto_enqueue=False):
    # Medium #34: Capture current generation in thread-local so we can detect
    # signals received during this discovery without clearing signals for other operations.
    _discovery_thread_state.generation = _discovery_interrupt_generation

    # Medium #35: Discovery operations are read-only (no device writes/modifications).
    # Device-level locking is intentionally skipped to avoid blocking verification operations.
    # Discovery only reads device information (SMART data, capabilities, etc.) and does not
    # perform any destructive operations, so concurrent discovery is safe without locks.
    try:
        with open(bay_map_path, 'r', encoding='utf-8') as f:
            bay_map_doc = json.load(f)
    except Exception:
        return []

    # Medium #34: Check for interruption after loading bay map
    if _check_discovery_interrupted():
        return []

    # Detect if using new enclosure-based schema or legacy by-path schema
    is_enclosure_schema = isinstance(bay_map_doc, dict) and "enclosures" in bay_map_doc

    if is_enclosure_schema:
        return _discover_drives_enclosure(bay_map_doc, running_devices, skip_auto_enqueue=skip_auto_enqueue)
    else:
        return _discover_drives_legacy(bay_map_doc, running_devices, skip_auto_enqueue=skip_auto_enqueue)


def _discover_drives_enclosure(bay_map_doc, running_devices, skip_auto_enqueue=False):
    """Discover drives using new enclosure-based physical slot mapping."""
    enclosures = bay_map_doc.get("enclosures", {})

    # Generate master slot map (cached, 60-second TTL)
    master_map = generate_master_slot_map(force_refresh=False)

    # Build by-path lookup for legacy compatibility
    path_to_dev = {}
    by_path_dir = '/dev/disk/by-path/'
    try:
        for entry in os.listdir(by_path_dir):
            full_path = os.path.join(by_path_dir, entry)
            if os.path.islink(full_path):
                path_to_dev[entry] = os.path.realpath(full_path)
    except (OSError, IOError):
        pass

    results, passphrase = [], None
    try:
        passphrase = load_policy(get_config_dir()).get("wipe_passphrase")
    except Exception as e:
        logging.getLogger(__name__).warning(f"Failed to load policy for wipe passphrase, marker HMAC verification disabled: {e}")

    os_dev_node, os_by_path = _get_os_by_path_cached()

    # Phase 5: single cached PCI scan shared by every bay in this discovery pass
    controllers = scan_pci_controllers(use_cache=True)

    pending = []

    # Iterate through enclosures in display_order
    enclosure_list = sorted(enclosures.values(), key=lambda e: e.get("display_order", 0))

    for enclosure in enclosure_list:
        # Medium #34: Check for interruption in each enclosure iteration
        if _check_discovery_interrupted():
            return []

        enclosure_id = enclosure.get("id")
        enclosure_name = enclosure.get("name")
        pci_controller = enclosure.get("pci_controller")
        expander_sas_address = enclosure.get("expander_sas_address")
        template_id = enclosure.get("template_id")
        slots = enclosure.get("slots", {})

        for slot_num, slot_config in slots.items():
            # Medium #34: Check for interruption in each slot iteration
            if _check_discovery_interrupted():
                return []

            physical_slot = slot_config.get("physical_slot_number", int(slot_num))
            physical_position = slot_config.get("physical_position")
            label = slot_config.get("label", f"Slot {slot_num}")
            role = slot_config.get("role", "wipe")
            locked = slot_config.get("locked", False)

            bay_id = f"{enclosure_id}_slot_{slot_num}"

            # Resolve device from enclosure slot using persisted HW identifiers.
            # Pass expander_sas_address so the by-path lookup is scoped to the correct
            # expander when multiple expanders share the same PCI controller.
            dev_node, interface_type = _resolve_device_from_enclosure_slot(
                slot_config, pci_controller, master_map,
                expander_sas_address=expander_sas_address
            )


            bay_info = {
                "bay": bay_id,
                "enclosure_id": enclosure_id,
                "enclosure_name": enclosure_name,
                "display_number": physical_slot,
                "physical_position": physical_position,
                "label": label,
                "role": role,
                "locked": locked,
                "configured_by_path": None,
                "resolved_by_path": None,
                "configured_by_path_nvme": None,
                "resolved_by_path_nvme": None,
                "type": interface_type or "sas_sata",
                "present": False,
                "device": None,
                "serial": None,
                "model": None,
                "status": "EMPTY",
                "interface_type": interface_type or "unknown",
                "drive_type": "unknown",
                "capacity_str": "-",
                "marker": {"ok": False, "status": "none", "error": None, "details": {}},
                "recommendation": {"status": "UNKNOWN", "comment": "-"},
                "health_score": 100,
                "capabilities": {"supports_crypto_erase": False, "supports_block_erase": False, "supports_secure_erase": False, "supports_enhanced_secure_erase": False, "supports_overwrite": True},
                "supported_methods": ["overwrite"],
                "smart": {},
                "diagnostics": {"mapping": {"ok": False, "reason": "not_mapped"}, "commands": {}},
                "controller": None
            }

            if dev_node:
                bay_info["diagnostics"]["mapping"] = {"ok": True, "reason": None}
                bay_info["device"] = dev_node
                bay_info["present"] = True

                # Get controller information for the device
                controller_info = get_controller_for_device(dev_node, controllers=controllers)
                bay_info["controller"] = controller_info

                is_os_drive = False
                if os_dev_node and os.path.realpath(dev_node) == os.path.realpath(os_dev_node):
                    is_os_drive = True

                if os_by_path and os.path.basename(dev_node) == os.path.basename(os_by_path):
                    is_os_drive = True

                # Respect config role if already set to "os", otherwise use detection
                if is_os_drive or bay_info.get("role") == "os":
                    bay_info["role"] = "os"
                    bay_info["locked"] = True

                if running_devices and dev_node in running_devices:
                    bay_info.update({
                        "status": "RUNNING",
                        "interface_type": detect_interface_type(dev_node, dev_node, interface_type, None),
                        "capacity_str": "Sanitizing..."
                    })
                    results.append(bay_info)
                    continue

                # Cache key: (dev_node, dev_node) — enclosure schema format.
                # Schema-specific: legacy mode uses (resolved_by_path, dev_node) instead.
                # Schemas are mutually exclusive; TTL cache handles expiration on schema migration.
                cache_key = (dev_node, dev_node)
                cached_payload = _get_cached_drive_payload(cache_key)
                if cached_payload is not None:
                    _apply_drive_payload(bay_info, cached_payload, is_os_drive)
                else:
                    pending.append((bay_info, is_os_drive, cache_key, dev_node, dev_node, dev_node, interface_type))
            else:
                bay_info["diagnostics"]["mapping"] = {"ok": False, "reason": "no_device_in_slot"}

            results.append(bay_info)

    if pending:
        # Medium #34: Check for interruption before launching expensive collection
        if _check_discovery_interrupted():
            return []
        # Phase 1: Use identity-only SMART collection for fast initial discovery
        try:
            _collect_pending_parallel(pending, passphrase, use_identity_only=True)
        except Exception as e:
            logging.getLogger(__name__).warning(f"Parallel drive collection failed, falling back to serial: {e}")
            _collect_pending_serial(pending, passphrase, use_identity_only=True)

    # Phase 1: Submit drives needing extended SMART to the persistent worker pool
    for bay_info in results:
        if bay_info.get("present") and bay_info.get("smart", {}).get("smart_polling"):
            dev_node = bay_info.get("device")
            resolved_by_path = bay_info.get("resolved_by_path") or bay_info.get("resolved_by_path_nvme")
            # Use same cache key as initial discovery to ensure cache hit
            cache_key = (dev_node, dev_node)
            resolved_path = resolved_by_path or dev_node
            configured_path = bay_info.get("configured_by_path") or dev_node
            configured_type = bay_info.get("interface_type")
            enclosure_id = bay_info.get("enclosure_id")
            slot_number = bay_info.get("display_number")
            _submit_drive_for_extended_smart((cache_key, dev_node, resolved_path, configured_path, configured_type, enclosure_id, slot_number), passphrase)

    # Phase 6: dual-port deduplication audit
    _audit_dual_port_deduplication(results)

    # Phase 7: queue background zero-checks for eligible internal drives
    if not skip_auto_enqueue:
        _auto_enqueue_zero_checks(results)

    return results


def _discover_drives_legacy(bay_map_doc, running_devices, skip_auto_enqueue=False):
    """Discover drives using legacy by-path mapping (backward compatibility)."""
    if isinstance(bay_map_doc, dict) and isinstance(bay_map_doc.get("bays"), dict):
        bay_map = bay_map_doc.get("bays", {})
    else:
        bay_map = {
            k: v for k, v in (bay_map_doc or {}).items()
            if isinstance(v, dict) and any(x in v for x in ["role", "by_path", "by_path_nvme", "type", "label", "locked"])
        }

    path_to_dev = {}
    by_path_dir = '/dev/disk/by-path/'
    try:
        for entry in os.listdir(by_path_dir):
            full_path = os.path.join(by_path_dir, entry)
            if os.path.islink(full_path): path_to_dev[entry] = os.path.realpath(full_path)
    except (OSError, IOError):
        pass

    results, passphrase = [], None
    try:
        passphrase = load_policy(get_config_dir()).get("wipe_passphrase")
    except Exception as e:
        logging.getLogger(__name__).warning(f"Failed to load policy for wipe passphrase, marker HMAC verification disabled: {e}")

    os_dev_node, os_by_path = _get_os_by_path_cached()

    # Phase 5: single cached PCI scan shared by every bay in this discovery pass
    controllers = scan_pci_controllers(use_cache=True)

    pending = []
    for bay_id, config in bay_map.items():
        # Medium #34: Check for interruption in each bay iteration
        if _check_discovery_interrupted():
            return []
        target_path = config.get('by_path')
        target_path_nvme = config.get('by_path_nvme')

        bay_info = {
            "bay": bay_id,
            "display_number": config.get("display_number"),
            "physical_position": config.get("physical_position"),
            "label": config.get('label', bay_id),
            "role": config.get('role', 'wipe'),
            "locked": config.get('locked', False),
            "configured_by_path": target_path,
            "resolved_by_path": None,
            "configured_by_path_nvme": target_path_nvme,
            "resolved_by_path_nvme": None,
            "type": config.get("type", "sas_sata"),  # Ensure type remains explicitly mapped
            "present": False,
            "device": None,
            "serial": None,
            "model": None,
            "status": "EMPTY",
            "interface_type": "unknown",
            "drive_type": "unknown",
            "capacity_str": "-",
            "marker": {"ok": False, "status": "none", "error": None, "details": {}},
            "recommendation": {"status": "UNKNOWN", "comment": "-"},
            "health_score": 100,
            "capabilities": {"supports_crypto_erase": False, "supports_block_erase": False, "supports_secure_erase": False, "supports_enhanced_secure_erase": False, "supports_overwrite": True},
            "supported_methods": ["overwrite"],
            "smart": {},
            "diagnostics": {"mapping": {"ok": False, "reason": "not_mapped"}, "commands": {}},
            "controller": None
        }

        # 1. Primary SATA/SAS path check
        matched_by_path, dev_node = resolve_bay_device(target_path, path_to_dev)
        matched_by_path_nvme = None

        # 2. Tri-Mode Fallback: If no SATA/SAS is found, resolve the NVMe motherboard port
        if not dev_node and target_path_nvme:
            matched_by_path_nvme, dev_node = resolve_bay_device(target_path_nvme, path_to_dev)
            if dev_node:
                bay_info["resolved_by_path_nvme"] = matched_by_path_nvme
        else:
            if dev_node:
                bay_info["resolved_by_path"] = matched_by_path

        if dev_node:
            bay_info["diagnostics"]["mapping"] = {"ok": True, "reason": None}

            # Get controller information for the device (shared PCI scan, no per-drive rescans)
            controller_info = get_controller_for_device(dev_node, controllers=controllers)
            bay_info["controller"] = controller_info

            is_os_drive = False
            if os_dev_node and os.path.realpath(dev_node) == os.path.realpath(os_dev_node):
                is_os_drive = True

            resolved_active_path = matched_by_path_nvme if matched_by_path_nvme else matched_by_path
            configured_active_path = target_path_nvme if matched_by_path_nvme else target_path

            if os_by_path and (resolved_active_path == os_by_path or configured_active_path == os_by_path or os.path.basename(resolved_active_path or "") == os.path.basename(os_by_path)):
                is_os_drive = True

            # Respect config role if already set to "os", otherwise use detection
            if is_os_drive or bay_info.get("role") == "os":
                bay_info["role"] = "os"
                bay_info["locked"] = True

            if running_devices and dev_node in running_devices:
                bay_info.update({"present": True, "device": dev_node, "status": "RUNNING", "interface_type": detect_interface_type(resolved_active_path or configured_active_path, dev_node, config.get('type'), None), "capacity_str": "Sanitizing..."})
                results.append(bay_info); continue

            # Phase 1: expensive data (SMART/capabilities/marker) is cached per device.
            # Presence above was resolved fresh, so insert/remove stays near real time.
            # Cache key: (resolved_by_path, dev_node) — legacy schema format.
            # Schema-specific: enclosure mode uses (dev_node, dev_node) instead.
            # Schemas are mutually exclusive; TTL cache handles expiration on schema migration.
            cache_key = (resolved_active_path or configured_active_path, dev_node)
            cached_payload = _get_cached_drive_payload(cache_key)
            if cached_payload is not None:
                _apply_drive_payload(bay_info, cached_payload, is_os_drive)
            else:
                pending.append((bay_info, is_os_drive, cache_key, dev_node, resolved_active_path, configured_active_path, config.get('type')))

        else:
            bay_info["diagnostics"]["mapping"] = {"ok": False, "reason": "by_path_not_found" if (target_path or target_path_nvme) else "missing_by_path"}
        results.append(bay_info)

    if pending:
        # Medium #34: Check for interruption before launching expensive collection
        if _check_discovery_interrupted():
            return []
        # Phase 1: Use identity-only SMART collection for fast initial discovery
        try:
            _collect_pending_parallel(pending, passphrase, use_identity_only=True)
        except Exception as e:
            logging.getLogger(__name__).warning(f"Parallel drive collection failed, falling back to serial: {e}")
            _collect_pending_serial(pending, passphrase, use_identity_only=True)

    # Phase 1: Submit drives needing extended SMART to the persistent worker pool
    for bay_info in results:
        if bay_info.get("present") and bay_info.get("smart", {}).get("smart_polling"):
            dev_node = bay_info.get("device")
            resolved_by_path = bay_info.get("resolved_by_path") or bay_info.get("resolved_by_path_nvme")
            configured_by_path = bay_info.get("configured_by_path") or bay_info.get("configured_by_path_nvme")
            # Use same cache key as initial discovery to ensure cache hit
            cache_key = (resolved_by_path or configured_by_path, dev_node)
            resolved_path = resolved_by_path or dev_node
            configured_path = configured_by_path or dev_node
            configured_type = bay_info.get("interface_type")
            enclosure_id = bay_info.get("enclosure_id")
            slot_number = bay_info.get("display_number")
            _submit_drive_for_extended_smart((cache_key, dev_node, resolved_path, configured_path, configured_type, enclosure_id, slot_number), passphrase)

    # Phase 6: dual-port deduplication audit
    _audit_dual_port_deduplication(results)

    # Phase 7: queue background zero-checks for eligible internal drives
    if not skip_auto_enqueue:
        _auto_enqueue_zero_checks(results)

    return results