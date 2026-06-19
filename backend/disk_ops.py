# --- START OF FILE backend/disk_ops.py ---
# OS drive detection and discovery engine

import os
import copy
import json
import time
import logging
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError

from common import get_config_dir, load_policy
from disk_utils import resolve_bay_device, check_write_tolerance, read_marker_status
from smart_parsing import get_smart_data, detect_interface_type, calculate_drive_health_score, get_drive_recommendation, is_drive_ssd
from disk_capabilities import detect_drive_capabilities
from device_discovery import get_controller_for_device, scan_pci_controllers

# Performance: per-device cache for expensive drive data (SMART, capabilities, marker).
# Presence detection (by-path resolution) is intentionally NOT cached so drive
# insertion/removal is still detected in near real time on every discovery call.
_DRIVE_DATA_CACHE = {}  # cache_key -> {'data': payload, 'timestamp': ts}
_DRIVE_DATA_CACHE_TTL = 30  # seconds
_DRIVE_DATA_CACHE_LOCK = threading.Lock()

# Performance: cached OS drive lookup (OS drive cannot change while the service runs)
# Cached indefinitely until service restart since the OS drive is a static property
_OS_BY_PATH_CACHE = {'data': None}
_OS_BY_PATH_LOCK = threading.Lock()

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

# Medium #34: Global flag for discovery interruption
_discovery_interrupted = False
_discovery_interrupt_lock = threading.Lock()

def _handle_discovery_signal(signum, frame):
    """Signal handler for SIGTERM/SIGINT during discovery operations."""
    global _discovery_interrupted
    with _discovery_interrupt_lock:
        _discovery_interrupted = True

def _check_discovery_interrupted():
    """Check if discovery was interrupted by signal."""
    global _discovery_interrupted
    with _discovery_interrupt_lock:
        return _discovery_interrupted

def invalidate_drive_cache(device=None):
    """Invalidate cached per-device drive data.

    Args:
        device: Specific device node (e.g. /dev/sda) to invalidate, or None to clear all.
    """
    with _DRIVE_DATA_CACHE_LOCK:
        if device is None:
            _DRIVE_DATA_CACHE.clear()
        else:
            for key in [k for k in _DRIVE_DATA_CACHE if k[1] == device]:
                del _DRIVE_DATA_CACHE[key]

# --- PROGRAMMATIC OS DRIVE DETECTION AND OVERRIDES ---

def get_os_parent_device():
    try:
        st = os.stat("/")
        major = os.major(st.st_dev)
        minor = os.minor(st.st_dev)
        
        uevent_path = f"/sys/dev/block/{major}:{minor}/uevent"
        devname = None
        if os.path.exists(uevent_path):
            with open(uevent_path, "r") as f:
                for line in f.read().splitlines():
                    if line.startswith("DEVNAME="):
                        devname = line.strip().split("=")[1]
                        break
                        
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
            if os.path.exists("/proc/mounts"):
                with open("/proc/mounts", "r") as f:
                    for line in f.read().splitlines():
                        parts = line.split()
                        if len(parts) >= 2 and parts[1] == "/":
                            src = parts[0]
                            if src.startswith("/dev/"):
                                devname = src[5:]
                                break

        if not devname:
            return None
            
        def resolve_leaf_parent(name):
            sys_path = f"/sys/class/block/{name}"
            if not os.path.exists(sys_path):
                return name
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
    if os.path.exists(by_path_dir):
        for entry in os.listdir(by_path_dir):
            full_path = os.path.join(by_path_dir, entry)
            if os.path.islink(full_path):
                if "-part" in entry:
                    continue
                if os.path.realpath(full_path) == os.path.realpath(dev_node):
                    return dev_node, entry
                    
    return dev_node, None

def _get_os_by_path_cached():
    """Cached wrapper around get_os_by_path(). Cached indefinitely until service restart since the OS drive is a static property."""
    with _OS_BY_PATH_LOCK:
        if _OS_BY_PATH_CACHE['data'] is not None:
            return _OS_BY_PATH_CACHE['data']
    data = get_os_by_path()
    if data and data[0]:
        with _OS_BY_PATH_LOCK:
            _OS_BY_PATH_CACHE['data'] = data
    return data

# --- DISCOVERY ENGINE ---

def get_all_controllers():
    """Get all PCI storage controllers for discovery API.
    
    Returns:
        List of controller dictionaries with PCI address, vendor, device, and type info
    """
    return scan_pci_controllers()

def _collect_drive_data(dev_node, resolved_active_path, configured_active_path, configured_type, passphrase):
    """Collect all expensive per-drive data (SMART, capabilities, marker) for one device.

    Runs in a worker thread during parallel discovery. Returns a payload dict that is
    merged into the bay record and cached for _DRIVE_DATA_CACHE_TTL seconds.
    """
    command_diagnostics = {}
    smart = get_smart_data(dev_node, command_diagnostics)
    interface_type = detect_interface_type(resolved_active_path or configured_active_path, dev_node, configured_type, smart.get("raw"))
    capabilities = detect_drive_capabilities(interface_type, dev_node, command_diagnostics)
    marker_status = read_marker_status(dev_node, interface_type, passphrase)

    if marker_status.get("status") == "checksum_valid":
        is_pristine = check_write_tolerance(interface_type, smart.get("data_written_raw"), marker_status.get("details", {}).get("data_written_at_wipe"))
        marker_status["is_pristine"] = is_pristine
        marker_status["status"] = "written_since_wipe" if not is_pristine else ("pristine_secure" if marker_status.get("hmac_verified") else "pristine_insecure")

    health_score = calculate_drive_health_score(interface_type, smart, smart.get("raw"))
    recommendation = get_drive_recommendation(interface_type, smart, health_score=health_score)
    drive_type = "ssd" if is_drive_ssd(interface_type, smart) else "hdd"

    return {
        "present": True, "device": dev_node, "serial": smart.get("serial"), "model": smart.get("model"), "status": smart.get("status", "UNKNOWN"), "interface_type": interface_type, "drive_type": drive_type, "capacity_str": smart.get("capacity_str", "-"),
        "capabilities": capabilities, "marker": marker_status, "recommendation": recommendation, "health_score": health_score,
        "supported_methods": [m for m, s in {"crypto": capabilities.get("supports_crypto_erase", False), "block": capabilities.get("supports_block_erase", False), "secure_erase": capabilities.get("supports_secure_erase", False), "enhanced_secure_erase": capabilities.get("supports_enhanced_secure_erase", False), "overwrite": capabilities.get("supports_overwrite", False)}.items() if s],
        "diagnostics": {"mapping": {"ok": True, "reason": None}, "commands": command_diagnostics},
        "smart": {
            "temperature": smart.get("temperature"), "reallocated_sectors": smart.get("reallocated_sectors"), "pending_sectors": smart.get("pending_sectors"), "wear_level": smart.get("wear_level"), "power_on_hours": smart.get("power_on_hours"), "power_on_days": smart.get("power_on_days"),
            "interface_errors": smart.get("interface_errors"), "data_read_raw": smart.get("data_read_raw"), "data_read_bytes": smart.get("data_read_bytes"), "data_written_raw": smart.get("data_written_raw"), "data_written_bytes": smart.get("data_written_bytes"),
            "reallocated_normalized": smart.get("reallocated_normalized"), "reallocated_threshold": smart.get("reallocated_threshold"), "capacity_bytes": smart.get("capacity_bytes"), "raw": smart.get("raw")
        }
    }

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
    bay_info["diagnostics"]["commands"]["collection"] = {"ok": False, "reason": reason}

def _store_drive_payload(cache_key, payload):
    """Store a fresh payload in the per-device cache and prune expired entries."""
    now = time.time()
    with _DRIVE_DATA_CACHE_LOCK:
        for key in [k for k, v in _DRIVE_DATA_CACHE.items() if (now - v['timestamp']) >= _DRIVE_DATA_CACHE_TTL]:
            del _DRIVE_DATA_CACHE[key]
        _DRIVE_DATA_CACHE[cache_key] = {'data': payload, 'timestamp': now}

def _get_cached_drive_payload(cache_key):
    with _DRIVE_DATA_CACHE_LOCK:
        entry = _DRIVE_DATA_CACHE.get(cache_key)
        if entry and (time.time() - entry['timestamp']) < _DRIVE_DATA_CACHE_TTL:
            return entry['data']
    return None

def _collect_pending_serial(pending, passphrase):
    """Serial fallback collection used when the thread pool fails."""
    for item in pending:
        bay_info, is_os_drive, cache_key, dev_node, resolved_path, configured_path, configured_type = item
        try:
            payload = _collect_drive_data(dev_node, resolved_path, configured_path, configured_type, passphrase)
            _store_drive_payload(cache_key, payload)
            _apply_drive_payload(bay_info, payload, is_os_drive)
        except Exception as e:
            logging.getLogger(__name__).warning(f"Drive data collection failed for {dev_node}: {e}")
            _apply_collection_failure(bay_info, dev_node, f"collection_failed: {e}")

def _collect_pending_parallel(pending, passphrase):
    """Collect drive data for all pending bays in parallel with bounded workers."""
    max_workers = min(get_discovery_max_workers(), len(pending))
    executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="drive-discovery")
    futures = {}
    try:
        for item in pending:
            bay_info, is_os_drive, cache_key, dev_node, resolved_path, configured_path, configured_type = item
            futures[executor.submit(_collect_drive_data, dev_node, resolved_path, configured_path, configured_type, passphrase)] = item
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
        executor.shutdown(wait=False)

def discover_drives(bay_map_path='/opt/drive-eraser/config/bay_map.json', running_devices=None):
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
        return {"error": "Discovery interrupted by signal"}

    if isinstance(bay_map_doc, dict) and isinstance(bay_map_doc.get("bays"), dict):
        bay_map = bay_map_doc.get("bays", {})
    else:
        bay_map = {
            k: v for k, v in (bay_map_doc or {}).items()
            if isinstance(v, dict) and any(x in v for x in ["role", "by_path", "by_path_nvme", "type", "label", "locked"])
        }

    path_to_dev = {}
    by_path_dir = '/dev/disk/by-path/'
    if os.path.exists(by_path_dir):
        for entry in os.listdir(by_path_dir):
            full_path = os.path.join(by_path_dir, entry)
            if os.path.islink(full_path): path_to_dev[entry] = os.path.realpath(full_path)

    results, passphrase = [], None
    try: passphrase = load_policy(get_config_dir()).get("wipe_passphrase")
    except Exception: pass

    os_dev_node, os_by_path = _get_os_by_path_cached()

    # Phase 5: single cached PCI scan shared by every bay in this discovery pass
    controllers = scan_pci_controllers(use_cache=True)

    pending = []
    for bay_id, config in bay_map.items():
        # Medium #34: Check for interruption in each bay iteration
        if _check_discovery_interrupted():
            return {"error": "Discovery interrupted by signal"}
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

            if is_os_drive:
                bay_info["role"] = "os"
                bay_info["locked"] = True

            if running_devices and dev_node in running_devices:
                bay_info.update({"present": True, "device": dev_node, "status": "RUNNING", "interface_type": detect_interface_type(resolved_active_path or configured_active_path, dev_node, config.get('type'), None), "capacity_str": "Sanitizing..."})
                results.append(bay_info); continue

            # Phase 1: expensive data (SMART/capabilities/marker) is cached per device.
            # Presence above was resolved fresh, so insert/remove stays near real time.
            # Cache key uses only by-path and dev_node to uniquely identify physical drive hardware.
            # Configured type is excluded since it can change independently of the drive.
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
            return {"error": "Discovery interrupted by signal"}
        # Phases 2-4: parallel SMART + capability + marker collection with serial fallback
        try:
            _collect_pending_parallel(pending, passphrase)
        except Exception as e:
            logging.getLogger(__name__).warning(f"Parallel drive collection failed, falling back to serial: {e}")
            _collect_pending_serial(pending, passphrase)

    return results