# Bay mapping-related routes
import os
import json
import re
import time
from threading import Lock
from flask import Blueprint, jsonify, request
from app_config import logger, limiter
from common import get_config_dir, load_policy, BAY_MAP_LOCK, save_bay_map, DRIVE_DATA_CACHE_TTL
from layout_templates import normalize_bay_map_document, compose_bay_map_document, load_layout_templates, validate_layout_metadata
from routes.admin_routes import require_admin_auth
from disk_ops import get_os_by_path, invalidate_drive_cache
from device_discovery import invalidate_master_slot_cache
from smart_parsing import get_smart_identity

bay_mapping_bp = Blueprint('bay_mapping_routes', __name__)

# Performance: cache identity-only SMART data for unmapped drive listings.
# The list of unmapped devices is rebuilt from /dev/disk/by-path on every request,
# but per-device identity data is expensive enough to cache. Device nodes are the
# natural cache key because identity attributes (model/serial/capacity) do not
# change while a drive remains physically present.
_UNMAPPED_DRIVE_CACHE = {}  # dev_node -> {'data': dict, 'timestamp': float}
_UNMAPPED_DRIVE_CACHE_LOCK = Lock()
_UNMAPPED_DRIVE_CACHE_TTL = DRIVE_DATA_CACHE_TTL  # 15 minutes (inherits DRIVE_DATA_CACHE_TTL)


def _get_cached_unmapped_drive(dev_node):
    """Return cached unmapped drive identity data if still fresh, else None."""
    with _UNMAPPED_DRIVE_CACHE_LOCK:
        entry = _UNMAPPED_DRIVE_CACHE.get(dev_node)
        if entry and (time.time() - entry['timestamp']) < _UNMAPPED_DRIVE_CACHE_TTL:
            return entry['data']
    return None


def _is_valid_identity_result(data):
    """Return True if `data` contains actual identity data (not a failure sentinel).

    `get_smart_identity` returns an empty template when smartctl is missing, times
    out, or returns invalid JSON. Caching that sentinel would hide transient failures
    for the full TTL, so we only cache results that carry a real model/serial or raw
    smartctl output.
    """
    if not data or not isinstance(data, dict):
        return False
    return bool(
        data.get("model") or data.get("serial") or data.get("raw")
    )


def _set_cached_unmapped_drive(dev_node, data):
    """Store or refresh identity data for an unmapped drive, pruning stale entries.

    Failure sentinels are not stored; callers must retry on the next request.
    """
    if not _is_valid_identity_result(data):
        return
    now = time.time()
    with _UNMAPPED_DRIVE_CACHE_LOCK:
        for key in [k for k, v in _UNMAPPED_DRIVE_CACHE.items()
                    if (now - v['timestamp']) >= _UNMAPPED_DRIVE_CACHE_TTL]:
            del _UNMAPPED_DRIVE_CACHE[key]
        _UNMAPPED_DRIVE_CACHE[dev_node] = {'data': data, 'timestamp': now}


def invalidate_unmapped_drive_cache(device=None):
    """Clear the unmapped-drive identity cache.

    Args:
        device: Optional device node. If provided, only that entry is removed;
                otherwise the entire cache is cleared.
    """
    with _UNMAPPED_DRIVE_CACHE_LOCK:
        if device is None:
            _UNMAPPED_DRIVE_CACHE.clear()
        else:
            _UNMAPPED_DRIVE_CACHE.pop(device, None)

@bay_mapping_bp.route("/api/admin/bay-map")
@require_admin_auth
@limiter.limit("30 per minute")
def get_admin_bay_map():
    try:
        config_dir = get_config_dir()
        bay_map_path = os.path.join(config_dir, "bay_map.json")
        try:
            with open(bay_map_path, "r", encoding="utf-8") as f:
                bay_map_doc = json.load(f)
        except Exception:
            bay_map_doc = {}
        bays, metadata = normalize_bay_map_document(bay_map_doc)
        enclosures = bay_map_doc.get("enclosures") if isinstance(bay_map_doc, dict) else None
        return jsonify(compose_bay_map_document(bays, metadata, enclosures)), 200
    except Exception as e:
        logger.error(f"Error getting bay map: {e}")
        return jsonify({"error": str(e)}), 500

@bay_mapping_bp.route("/api/admin/unmapped-drives")
@require_admin_auth
@limiter.limit("30 per minute")
def get_unmapped_drives():
    try:
        config_dir = get_config_dir()
        bay_map_path = os.path.join(config_dir, "bay_map.json")
        try:
            with open(bay_map_path, "r", encoding="utf-8") as f:
                bay_map_doc = json.load(f)
        except Exception:
            bay_map_doc = {}

        bay_map, _ = normalize_bay_map_document(bay_map_doc)

        mapped_paths = set()
        for config in bay_map.values():
            p = config.get("by_path")
            if p:
                mapped_paths.add(os.path.basename(p))
                mapped_paths.add(p)
            p_nvme = config.get("by_path_nvme")
            if p_nvme:
                mapped_paths.add(os.path.basename(p_nvme))
                mapped_paths.add(p_nvme)
                
        path_to_dev = {}
        by_path_dir = '/dev/disk/by-path/'
        unmapped_devices = []

        os_dev_node, os_by_path = get_os_by_path()
        
        if os.path.exists(by_path_dir):
            for entry in os.listdir(by_path_dir):
                if entry in mapped_paths:
                    continue
                full_path = os.path.join(by_path_dir, entry)
                if os.path.islink(full_path):
                    dev_node = os.path.realpath(full_path)
                    if "-part" in entry:
                        continue
                    path_to_dev[entry] = dev_node
                    
        for by_path, dev_node in path_to_dev.items():
            try:
                # Use cached identity-only data when available; full extended SMART is
                # unnecessary here and was the cause of the 60s admin page load.
                smart = _get_cached_unmapped_drive(dev_node)
                if smart is None:
                    smart = get_smart_identity(dev_node)
                    _set_cached_unmapped_drive(dev_node, smart)

                is_os = False
                if os_dev_node and os.path.realpath(dev_node) == os.path.realpath(os_dev_node):
                    is_os = True
                if os_by_path and (by_path == os_by_path or os.path.basename(by_path) == os.path.basename(os_by_path)):
                    is_os = True

                model_str = smart.get("model") or "Unknown"
                if is_os:
                    model_str = f"{model_str} [OS Drive]"

                unmapped_devices.append({
                    "by_path": by_path,
                    "device": dev_node,
                    "model": model_str,
                    "serial": smart.get("serial") or "Unknown",
                    "capacity_str": smart.get("capacity_str", "-"),
                    "capacity_bytes": smart.get("capacity_bytes"),
                    "is_os": is_os
                })
            except Exception:
                pass
        return jsonify(unmapped_devices), 200
    except Exception as e:
        logger.error(f"Error getting unmapped drives: {e}")
        return jsonify({"error": str(e)}), 500

@bay_mapping_bp.route("/api/admin/auto-detect-bays", methods=["POST"])
@require_admin_auth
@limiter.limit("10 per minute")
def auto_detect_bays():
    try:
        config_dir = get_config_dir()
        bay_map_path = os.path.join(config_dir, "bay_map.json")
        
        try:
            with open(bay_map_path, "r", encoding="utf-8") as f:
                bay_map_doc = json.load(f)
            bay_map, layout_metadata = normalize_bay_map_document(bay_map_doc)
        except Exception:
            bay_map = {}
            layout_metadata = {}

        path_to_dev = {}
        by_path_dir = '/dev/disk/by-path/'
        if os.path.exists(by_path_dir):
            for entry in os.listdir(by_path_dir):
                full_path = os.path.join(by_path_dir, entry)
                if os.path.islink(full_path):
                    if "-part" in entry:
                        continue
                    path_to_dev[entry] = os.path.realpath(full_path)

        discovered_slots = {}

        # --- METHOD A: SCSI Enclosure Services (SES) /sys/class/enclosure scanning ---
        enclosure_base = "/sys/class/enclosure"
        METADATA_DIRS = {"components", "device", "id", "power", "subsystem", "uevent"}

        try:
            enc_ids = os.listdir(enclosure_base)
        except (OSError, IOError):
            enc_ids = []

        for enc_id in enc_ids:
            enc_path = os.path.join(enclosure_base, enc_id)
            try:
                slot_ids = os.listdir(enc_path)
            except (OSError, IOError):
                continue

            for slot_id in slot_ids:
                    if slot_id in METADATA_DIRS:
                        continue
                    slot_path = os.path.join(enc_path, slot_id)

                    block_devs = []

                    # Find associated block device nodes under slot path
                    dev_block_path = os.path.join(slot_path, "device", "block")
                    try:
                        for b in os.listdir(dev_block_path):
                            block_devs.append(b)
                    except (OSError, IOError):
                        pass

                    dev_path = os.path.join(slot_path, "device")
                    try:
                        for name in os.listdir(dev_path):
                            if name.startswith("sd") or name.startswith("nvme"):
                                block_devs.append(name)
                    except (OSError, IOError):
                        pass

                    # Process found devices for this slot
                    for sd_node in sorted(list(set(block_devs))):
                        real_dev = f"/dev/{sd_node}"
                        digits = re.findall(r'\d+', slot_id)
                        if digits:
                            try:
                                slot_num = int(digits[0])
                                # Validate slot number is within reasonable bounds
                                if slot_num < 0 or slot_num > 9999:
                                    continue
                            except (ValueError, IndexError):
                                continue
                            # Map Slot 0-7 directly to bay0-bay7
                            bay_id = f"bay{slot_num}"
                            
                            if bay_id not in bay_map and f"bay{slot_num:02d}" in bay_map:
                                bay_id = f"bay{slot_num:02d}"
                            
                            by_path_link = None
                            for link_entry, node_path in path_to_dev.items():
                                if os.path.realpath(node_path) == os.path.realpath(real_dev):
                                    by_path_link = link_entry
                                    break
                            
                            if by_path_link:
                                discovered_slots[bay_id] = by_path_link

        # --- METHOD B: SAS Transport Subsystem bay_identifier Fallback (For Passive Direct-Attach Backplanes) ---
        if not discovered_slots:
            sys_block_dir = "/sys/block"
            try:
                block_names = os.listdir(sys_block_dir)
            except (OSError, IOError):
                block_names = []

            for name in block_names:
                    if not name.startswith("sd"):
                        continue
                        
                    real_path = os.path.realpath(os.path.join(sys_block_dir, name))
                    
                    # Walk up the parent directory tree to find the SCSI/SAS transport target node
                    npath = real_path
                    found_bay = None
                    
                    while npath and npath != "/":
                        sas_device_dir = os.path.join(npath, "sas_device")
                        try:
                            end_dev_ids = os.listdir(sas_device_dir)
                        except (OSError, IOError):
                            end_dev_ids = []

                        for end_dev_id in end_dev_ids:
                            bay_id_path = os.path.join(sas_device_dir, end_dev_id, "bay_identifier")
                            try:
                                with open(bay_id_path, "r") as f:
                                    slot_str = f.read().strip()
                                if slot_str.isdigit():
                                    found_bay = int(slot_str)
                                    break
                            except Exception:
                                pass
                        if found_bay is not None:
                            break
                        npath = os.path.dirname(npath)
                        
                    if found_bay is not None:
                        slot_num = found_bay
                        bay_id = f"bay{slot_num}"
                        
                        if bay_id not in bay_map and f"bay{slot_num:02d}" in bay_map:
                            bay_id = f"bay{slot_num:02d}"
                        
                        real_dev = f"/dev/{name}"
                        by_path_link = None
                        for link_entry, node_path in path_to_dev.items():
                            if os.path.realpath(node_path) == os.path.realpath(real_dev):
                                by_path_link = link_entry
                                break
                                
                        if by_path_link:
                            discovered_slots[bay_id] = by_path_link

        # If both scans yielded 0 populated slots, report back to the user
        if not discovered_slots:
            logger.info("Auto-detect bays completed: no physical backplane slots or block devices detected.")
            return jsonify({
                "status": "success",
                "message": "Auto-detection run completed, but no physical backplane slots or block devices were detected on this server.",
                "bay_map": bay_map
            }), 200

        updates_count = 0
        for bay_id, by_path_val in discovered_slots.items():
            if bay_id in bay_map:
                if bay_map[bay_id].get("by_path") != by_path_val:
                    bay_map[bay_id]["by_path"] = by_path_val
                    updates_count += 1
            else:
                bay_map[bay_id] = {
                    "role": "wipe",
                    "locked": False,
                    "type": "sas_sata",
                    "label": "Work Bay",
                    "by_path": by_path_val,
                    "by_path_nvme": None
                }
                updates_count += 1

        # Preserve existing enclosures section to prevent data loss when saving bay map
        # Use BAY_MAP_LOCK to ensure atomic read-modify-write (Lesson #2)
        with BAY_MAP_LOCK:
            existing_bay_map = {}
            try:
                bay_map_path = os.path.join(config_dir, "bay_map.json")
                with open(bay_map_path, "r", encoding="utf-8") as f:
                    existing_bay_map = json.load(f)
            except Exception:
                pass
            
            enclosures = existing_bay_map.get("enclosures") if isinstance(existing_bay_map, dict) else None
            save_bay_map(compose_bay_map_document(bay_map, layout_metadata, enclosures), config_dir)
        # Bay mapping changed: drop cached drive data so the next discovery re-resolves everything
        invalidate_drive_cache()
        # Also invalidate master slot map cache to refresh hardware topology
        invalidate_master_slot_cache()
        # The set of unmapped devices may have changed; clear identity cache too
        invalidate_unmapped_drive_cache()

        logger.info(f"Auto-detect bays updated {updates_count} map elements out of {len(discovered_slots)} total discovered enclosures.")
        return jsonify({
            "status": "success",
            "message": f"Successfully mapped {len(discovered_slots)} physical backplane slot(s). Updated {updates_count} bay(s).",
            "bay_map": bay_map
        }), 200
        
    except Exception as e:
        logger.error(f"Auto-detect bays failed: {e}")
        return jsonify({"error": str(e)}), 500

@bay_mapping_bp.route("/api/admin/save-bay-map", methods=["POST"])
@require_admin_auth
@limiter.limit("30 per minute")
def update_bay_map():
    try:
        payload = request.get_json(silent=True) or {}
        if not payload:
            return jsonify({"error": "Invalid payload"}), 400

        config_dir = get_config_dir()

        if not isinstance(payload, dict):
            return jsonify({"error": "Payload must be a dictionary map."}), 400

        templates, _ = load_layout_templates(config_dir)
        bays, layout_metadata = normalize_bay_map_document(payload)

        for bay_id, conf in bays.items():
            if not isinstance(conf, dict):
                return jsonify({"error": f"Configuration for {bay_id} must be a dictionary."}), 400

        validation_error = validate_layout_metadata(layout_metadata, bays, templates)
        if validation_error:
            return jsonify({"error": validation_error}), 400

        # Preserve existing enclosures section to prevent data loss when saving bay map
        # Use BAY_MAP_LOCK to ensure atomic read-modify-write (Lesson #2)
        with BAY_MAP_LOCK:
            existing_bay_map = {}
            try:
                bay_map_path = os.path.join(config_dir, "bay_map.json")
                with open(bay_map_path, "r", encoding="utf-8") as f:
                    existing_bay_map = json.load(f)
            except Exception:
                pass
            
            enclosures = existing_bay_map.get("enclosures") if isinstance(existing_bay_map, dict) else None

            save_bay_map(compose_bay_map_document(bays, layout_metadata, enclosures), config_dir)
        # Bay mapping changed: drop cached drive data so the next discovery re-resolves everything
        invalidate_drive_cache()
        # Also invalidate master slot map cache to refresh hardware topology
        invalidate_master_slot_cache()
        # The set of unmapped devices may have changed; clear identity cache too
        invalidate_unmapped_drive_cache()

        logger.info("Enclosure bay map edited manually by administrator.")
        return jsonify({"status": "success", "message": "Bay mapping configuration updated successfully."}), 200
    except Exception as e:
        logger.error(f"Save bay map failed: {e}")
        return jsonify({"error": str(e)}), 500
