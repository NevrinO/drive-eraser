# Drive data collection and caching — extracted from disk_ops.py (A70)
# Depends on: smart_data_parsing, smart_utils, smart_health, disk_capabilities, database

import copy
import time
import logging
import threading

from common import get_config_dir, load_policy, DRIVE_DATA_CACHE_TTL
from disk_utils import resolve_bay_device, check_write_tolerance, read_marker_status
from smart_data_parsing import get_smart_data, get_smart_identity, get_triage_thresholds
from smart_utils import detect_interface_type, is_drive_ssd
from smart_health import calculate_drive_health_score, get_drive_recommendation
from disk_capabilities import detect_drive_capabilities
from database import record_intake_snapshot

# Performance: per-device cache for expensive drive data (SMART, capabilities, marker).
# Presence detection (by-path resolution) is intentionally NOT cached so drive
# insertion/removal is still detected in near real time on every discovery call.
_DRIVE_DATA_CACHE = {}  # cache_key -> {'data': payload, 'timestamp': ts}
_DRIVE_DATA_CACHE_LOCK = threading.Lock()


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
        health_score, penalty_breakdown = calculate_drive_health_score(interface_type, smart, thresholds=thresholds)
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
