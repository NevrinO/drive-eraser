# Discovery engine — extracted from disk_ops.py (A70)
# Depends on: os_detection, device_resolution, drive_collection, extended_smart,
#   discovery_state, device_discovery, smart_utils, disk_utils, common, zero_check_manager

import os
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError

from common import get_config_dir, load_policy
from disk_utils import resolve_bay_device
from smart_utils import detect_interface_type
from device_discovery import get_controller_for_device, scan_pci_controllers, generate_master_slot_map
from zero_check_manager import get_manager as get_zero_check_manager
from os_detection import _get_os_by_path_cached
from device_resolution import _resolve_device_from_enclosure_slot, _resolve_device_from_hardware_identifier
from drive_collection import (
    _collect_drive_data, _apply_drive_payload, _apply_collection_failure,
    _store_drive_payload, _get_cached_drive_payload,
    _DRIVE_DATA_CACHE, _DRIVE_DATA_CACHE_LOCK,
)
from extended_smart import (
    _submit_drive_for_extended_smart, _EXTENDED_SMART_LOCK, _EXTENDED_SMART_PENDING,
)
import discovery_state
from discovery_state import _discovery_thread_state, _check_discovery_interrupted


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

def discover_drives(bay_map_path='/opt/drive-eraser/config/bay_map.json', running_devices=None, skip_auto_enqueue=False):
    # Medium #34: Capture current generation in thread-local so we can detect
    # signals received during this discovery without clearing signals for other operations.
    _discovery_thread_state.generation = discovery_state._discovery_interrupt_generation

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


def _build_path_to_dev():
    """Build a mapping from by-path entries to resolved device nodes."""
    path_to_dev = {}
    by_path_dir = '/dev/disk/by-path/'
    try:
        for entry in os.listdir(by_path_dir):
            full_path = os.path.join(by_path_dir, entry)
            if os.path.islink(full_path):
                path_to_dev[entry] = os.path.realpath(full_path)
    except (OSError, IOError):
        pass
    return path_to_dev


def _load_wipe_passphrase():
    """Load wipe passphrase from policy for marker HMAC verification."""
    try:
        return load_policy(get_config_dir()).get("wipe_passphrase")
    except Exception as e:
        logging.getLogger(__name__).warning(f"Failed to load policy for wipe passphrase, marker HMAC verification disabled: {e}")
        return None


def _finalize_discovery(results, pending, passphrase, skip_auto_enqueue):
    """Post-loop discovery finalization: pending collection, extended SMART, dedup, auto-enqueue."""
    if pending:
        if _check_discovery_interrupted():
            return []
        try:
            _collect_pending_parallel(pending, passphrase, use_identity_only=True)
        except Exception as e:
            logging.getLogger(__name__).warning(f"Parallel drive collection failed, falling back to serial: {e}")
            _collect_pending_serial(pending, passphrase, use_identity_only=True)

    for bay_info in results:
        if bay_info.get("present") and bay_info.get("smart", {}).get("smart_polling"):
            dev_node = bay_info.get("device")
            resolved_by_path = bay_info.get("resolved_by_path") or bay_info.get("resolved_by_path_nvme")
            configured_by_path = bay_info.get("configured_by_path") or bay_info.get("configured_by_path_nvme")
            cache_key = bay_info.get("_discovery_cache_key")
            resolved_path = resolved_by_path or dev_node
            configured_path = configured_by_path or dev_node
            configured_type = bay_info.get("interface_type")
            enclosure_id = bay_info.get("enclosure_id")
            slot_number = bay_info.get("display_number")
            _submit_drive_for_extended_smart((cache_key, dev_node, resolved_path, configured_path, configured_type, enclosure_id, slot_number), passphrase)

    _audit_dual_port_deduplication(results)

    if not skip_auto_enqueue:
        _auto_enqueue_zero_checks(results)

    for bay_info in results:
        bay_info.pop("_discovery_cache_key", None)

    return results


def _discover_drives_enclosure(bay_map_doc, running_devices, skip_auto_enqueue=False):
    """Discover drives using new enclosure-based physical slot mapping."""
    enclosures = bay_map_doc.get("enclosures", {})

    # Generate master slot map (cached, 60-second TTL)
    master_map = generate_master_slot_map(force_refresh=False)

    # Build by-path lookup for legacy compatibility
    path_to_dev = _build_path_to_dev()

    results, passphrase = [], _load_wipe_passphrase()

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
                bay_info["_discovery_cache_key"] = cache_key
                cached_payload = _get_cached_drive_payload(cache_key)
                if cached_payload is not None:
                    _apply_drive_payload(bay_info, cached_payload, is_os_drive)
                else:
                    pending.append((bay_info, is_os_drive, cache_key, dev_node, dev_node, dev_node, interface_type))
            else:
                bay_info["diagnostics"]["mapping"] = {"ok": False, "reason": "no_device_in_slot"}

            results.append(bay_info)

    return _finalize_discovery(results, pending, passphrase, skip_auto_enqueue)


def _discover_drives_legacy(bay_map_doc, running_devices, skip_auto_enqueue=False):
    """Discover drives using legacy by-path mapping (backward compatibility)."""
    if isinstance(bay_map_doc, dict) and isinstance(bay_map_doc.get("bays"), dict):
        bay_map = bay_map_doc.get("bays", {})
    else:
        bay_map = {
            k: v for k, v in (bay_map_doc or {}).items()
            if isinstance(v, dict) and any(x in v for x in ["role", "by_path", "by_path_nvme", "type", "label", "locked"])
        }

    path_to_dev = _build_path_to_dev()

    results, passphrase = [], _load_wipe_passphrase()

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
            bay_info["_discovery_cache_key"] = cache_key
            cached_payload = _get_cached_drive_payload(cache_key)
            if cached_payload is not None:
                _apply_drive_payload(bay_info, cached_payload, is_os_drive)
            else:
                pending.append((bay_info, is_os_drive, cache_key, dev_node, resolved_active_path, configured_active_path, config.get('type')))

        else:
            bay_info["diagnostics"]["mapping"] = {"ok": False, "reason": "by_path_not_found" if (target_path or target_path_nvme) else "missing_by_path"}
        results.append(bay_info)

    return _finalize_discovery(results, pending, passphrase, skip_auto_enqueue)
