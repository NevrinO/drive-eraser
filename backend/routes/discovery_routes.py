# Discovery-related routes
import os
import json
import re
import copy
from flask import Blueprint, jsonify, request
from app_config import logger, calculate_session_token, limiter
from common import get_config_dir, load_policy, BAY_MAP_LOCK, save_bay_map
from routes.admin_routes import require_admin_auth
from layout_templates import normalize_bay_map_document, compose_bay_map_document
from device_discovery import (
    discover_controllers_and_devices,
    scan_pci_controllers,
    validate_device_path,
    validate_pci_address,
    get_scsi_host_slot_projections
)
from smart_parsing import get_smart_data

discovery_bp = Blueprint('discovery_routes', __name__)

# Strict regex validation for projected by-path format
# Format: pci-{pci_addr}-scsi-{host}:0:{slot}:0
# Example: pci-0000:01:00.0-scsi-0:0:0:0
_PROJECTED_BY_PATH_RE = re.compile(r'^pci-[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F]-scsi-\d+:0:\d+:0\Z')

# Regex validation for udev by-path format (from /dev/disk/by-path/)
# Strict whitelist of known udev by-path prefix types to prevent malicious input
# Format: {type}-{bus-specific-info}
# Examples: pci-0000:01:00.0-scsi-0:0:0:0, pci-0000:01:00.0-ata-1, usb-1:1.2, ieee1394-0, virtio-0, platform-...
_UDEV_BY_PATH_RE = re.compile(r'^(pci|usb|ieee1394|virtio|platform)-[0-9a-fA-F:\-.a-zA-Z]+\Z')

@discovery_bp.route("/api/admin/discover-slots")
@require_admin_auth
@limiter.limit("10 per minute")
def discover_slots():
    """Enhanced discovery API for comprehensive slot and device detection.
    
    Provides detailed information about storage controllers, devices, and enclosure slots.
    Supports optional filtering by controller_type and pci_address query parameters.
    
    Query parameters (optional):
    - controller_type: Filter by controller type (sata, sas, nvme, scsi, raid, unknown)
    - pci_address: Filter by specific PCI address (e.g., "0000:00:1f.2")
    - include_smart: Include SMART data for discovered devices (default: false)
    """
    try:
        # Validate query parameters for DoS prevention
        controller_type_filter = request.args.get("controller_type", "").strip().lower()
        pci_address_filter = request.args.get("pci_address", "").strip().lower()
        include_smart_raw = request.args.get("include_smart", "false").strip().lower()
        
        # Validate controller_type filter
        valid_controller_types = {"sata", "sas", "nvme", "scsi", "raid", "unknown", ""}
        if controller_type_filter not in valid_controller_types:
            return jsonify({"error": f"Invalid controller_type. Must be one of: {', '.join(sorted(valid_controller_types - {''}))}"}), 400
        
        # Validate pci_address format if provided
        if pci_address_filter and not validate_pci_address(pci_address_filter):
            return jsonify({"error": "Invalid pci_address format. Expected format: 0000:00:1f.2"}), 400
        
        include_smart = include_smart_raw == "true"
        
        # Scan all PCI storage controllers (including those without devices)
        all_controllers = scan_pci_controllers(use_cache=True)
        
        # Discover controllers and devices to ensure cache coherence
        controllers_and_devices = discover_controllers_and_devices()
        
        # Extract unique controllers from device data to avoid cache coherence issues
        # Use composite key to handle controllers without pci_address
        controllers_set = {}
        for controller_type, devices in controllers_and_devices.items():
            for device_info in devices:
                controller = device_info.get("controller", {})
                if controller and isinstance(controller, dict):
                    # Create composite key from controller attributes for deduplication
                    pci_addr = controller.get("pci_address", "")
                    vendor_id = controller.get("vendor_id", "")
                    device_id = controller.get("device_id", "")
                    controller_type_key = controller.get("controller_type", "")
                    composite_key = f"{pci_addr}:{vendor_id}:{device_id}:{controller_type_key}"
                    if composite_key not in controllers_set:
                        controllers_set[composite_key] = controller
        
        # Add controllers that have no devices attached
        for controller in all_controllers:
            pci_addr = controller.get("pci_address", "")
            vendor_id = controller.get("vendor_id", "")
            device_id = controller.get("device_id", "")
            controller_type_key = controller.get("controller_type", "")
            composite_key = f"{pci_addr}:{vendor_id}:{device_id}:{controller_type_key}"
            if composite_key not in controllers_set:
                controllers_set[composite_key] = controller
        
        controllers = list(controllers_set.values())
        
        # DoS protection: enforce size limits
        MAX_CONTROLLERS = 100
        MAX_DEVICES = 1000
        MAX_SLOTS = 1000
        
        if len(controllers) > MAX_CONTROLLERS:
            return jsonify({"error": f"Controller count exceeds maximum limit of {MAX_CONTROLLERS}"}), 400
        
        # Build comprehensive response
        result = {
            "controllers": controllers,
            "devices_by_type": {},
            "total_devices": 0,
            "enclosure_slots": [],
            "scsi_slot_projections": []
        }

        # Build mapping from device nodes to by-path entries (for udev persistent paths)
        # This is similar to how unmapped-drives endpoint works
        by_path_dir = '/dev/disk/by-path/'
        dev_node_to_by_path = {}
        try:
            for entry in os.listdir(by_path_dir):
                if "-part" in entry:
                    continue  # Skip partitions
                full_path = os.path.join(by_path_dir, entry)
                if os.path.islink(full_path):
                    dev_node = os.path.realpath(full_path)
                    dev_node_to_by_path[dev_node] = entry
        except (OSError, IOError) as e:
            logger.warning(f"Failed to scan {by_path_dir}: {e}")
        
        # Add SCSI host slot projections for physical bay mapping
        # This provides projected by-path information for empty slots
        try:
            scsi_projections = get_scsi_host_slot_projections()
            result["scsi_slot_projections"] = scsi_projections
        except Exception as e:
            logger.warning(f"Failed to get SCSI slot projections: {e}")
            result["scsi_slot_projections"] = []
        
        # Filter devices by controller_type if specified
        for controller_type, devices in controllers_and_devices.items():
            if controller_type_filter and controller_type != controller_type_filter:
                continue
            
            enhanced_devices = []
            for device_info in devices:
                device_path = device_info.get("device_path")
                controller = device_info.get("controller", {})
                
                # Ensure controller is a dict before using it
                if not controller or not isinstance(controller, dict):
                    controller = {}
                
                # Filter by pci_address if specified
                if pci_address_filter:
                    if controller.get("pci_address") != pci_address_filter:
                        continue
                
                # Validate device path
                if not validate_device_path(device_path):
                    continue
                
                # Resolve udev by-path for this device
                by_path = dev_node_to_by_path.get(device_path, "")

                enhanced_device = {
                    "device_path": device_path,
                    "by_path": by_path,
                    "device_name": device_info.get("device_name"),
                    "controller_pci": controller.get("pci_address"),
                    "controller_type": controller.get("controller_type"),
                    "controller_description": controller.get("description"),
                    "vendor_id": controller.get("vendor_id"),
                    "device_id": controller.get("device_id")
                }
                
                # Add SMART data if requested
                if include_smart:
                    try:
                        smart = get_smart_data(device_path)
                        enhanced_device["smart"] = {
                            "model": smart.get("model"),
                            "serial": smart.get("serial"),
                            "capacity_str": smart.get("capacity_str"),
                            "capacity_bytes": smart.get("capacity_bytes")
                        }
                    except Exception as e:
                        enhanced_device["smart"] = None
                        enhanced_device["smart_error"] = str(e)
                
                enhanced_devices.append(enhanced_device)
            
            if enhanced_devices:
                result["devices_by_type"][controller_type] = enhanced_devices
                result["total_devices"] += len(enhanced_devices)
                
                # DoS protection: check device limit
                if result["total_devices"] > MAX_DEVICES:
                    return jsonify({"error": f"Device count exceeds maximum limit of {MAX_DEVICES}"}), 400
        
        # Scan enclosure slots if available (SCSI Enclosure Services)
        # Use try-except for atomic operations to avoid TOCTOU
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
            
                # Extract slot number with validation
                slot_num = None
                digits = re.findall(r'\d+', slot_id)
                if digits:
                    try:
                        slot_num = int(digits[0])
                        # Validate slot number is within reasonable bounds
                        if slot_num < 0 or slot_num > 9999:
                            slot_num = None
                    except (ValueError, IndexError):
                        slot_num = None
                
                # Find associated device using try-except for atomic operations
                slot_device = None
                block_devs = []
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
                
                if block_devs:
                    for sd_node in sorted(list(set(block_devs))):
                        real_dev = f"/dev/{sd_node}"
                        if validate_device_path(real_dev):
                            slot_device = real_dev
                            break
                
                slot_info = {
                    "enclosure_id": enc_id,
                    "slot_id": slot_id,
                    "slot_number": slot_num,
                    "device": slot_device
                }
                
                # Add SMART data if device present and requested
                if slot_device and include_smart:
                    try:
                        smart = get_smart_data(slot_device)
                        slot_info["smart"] = {
                            "model": smart.get("model"),
                            "serial": smart.get("serial"),
                            "capacity_str": smart.get("capacity_str")
                        }
                    except Exception as e:
                        slot_info["smart"] = None
                        slot_info["smart_error"] = str(e)
                
                result["enclosure_slots"].append(slot_info)
                
                # DoS protection: check slot limit
                if len(result["enclosure_slots"]) > MAX_SLOTS:
                    return jsonify({"error": f"Enclosure slot count exceeds maximum limit of {MAX_SLOTS}"}), 400
        
        return jsonify(result), 200
    except Exception as e:
        logger.error(f"Slot discovery failed: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@discovery_bp.route("/api/admin/apply-slot-mapping", methods=["POST"])
@require_admin_auth
@limiter.limit("10 per minute")
def apply_slot_mapping():
    """Apply discovered device mappings to bay configuration.
    
    Accepts a mapping of bay IDs to device paths and updates the bay_map.json configuration.
    Handles both regular device paths (SATA/SAS) and NVMe device paths.
    
    Expected payload format:
    {
        "bay0": {"device_path": "/dev/sda"},
        "bay1": {"device_path": "/dev/nvme0n1"},
        ...
    }
    """
    try:
        # Input validation
        payload = request.get_json(silent=True)
        if not payload or not isinstance(payload, dict):
            return jsonify({"error": "Invalid payload: expected JSON object"}), 400
        
        # DoS prevention: limit number of mappings
        MAX_MAPPINGS = 100
        if len(payload) > MAX_MAPPINGS:
            return jsonify({"error": f"Mapping count exceeds maximum limit of {MAX_MAPPINGS}"}), 400
        
        # Load current bay map with lock to prevent race conditions
        config_dir = get_config_dir()
        with BAY_MAP_LOCK:
            bay_map_path = os.path.join(config_dir, "bay_map.json")
            try:
                with open(bay_map_path, "r", encoding="utf-8") as f:
                    bay_map_doc = json.load(f)
                bay_map, layout_metadata = normalize_bay_map_document(bay_map_doc)
            except Exception as e:
                return jsonify({"error": f"Failed to load bay map: {str(e)}"}), 500
            
            bay_map_copy = copy.deepcopy(bay_map)
            
            # Apply mappings with validation on copy
            updated_bays = 0
            validation_errors = []
            
            for bay_id, device_info in payload.items():
                # Validate bay_id exists
                if bay_id not in bay_map_copy:
                    validation_errors.append(f"Bay {bay_id} does not exist in configuration")
                    continue
                
                if not isinstance(device_info, dict):
                    validation_errors.append(f"Invalid device info for {bay_id}: expected object")
                    continue
                
                device_path = device_info.get("device_path")
                projected_by_path = device_info.get("projected_by_path")
                is_empty = device_info.get("is_empty", False)
                
                # Handle empty slots with projected by-path
                if is_empty and not device_path and projected_by_path:
                    # Empty slot - use projected by-path for future mapping
                    # Validate projected by-path format strictly (Rule #9, #15)
                    # Format: pci-{pci_addr}-scsi-{host}:0:{slot}:0
                    if not isinstance(projected_by_path, str):
                        validation_errors.append(f"Invalid projected_by_path for {bay_id}: not a string")
                        continue
                    if not _PROJECTED_BY_PATH_RE.match(projected_by_path):
                        validation_errors.append(f"Invalid projected_by_path format for {bay_id}: {projected_by_path}")
                        continue
                    
                    bay_map_copy[bay_id]["by_path"] = projected_by_path
                    bay_map_copy[bay_id]["by_path_nvme"] = ""
                    updated_bays += 1
                    continue
                
                # For occupied slots, device_path is required
                if not device_path:
                    validation_errors.append(f"Missing device_path for {bay_id}")
                    continue

                # Validate device path - accept both device nodes and udev by-path
                # Device nodes: /dev/sda, /dev/nvme0n1
                # Udev by-path: pci-0000:01:00.0-scsi-0:0:0:0, etc.
                is_valid_path = validate_device_path(device_path) or _UDEV_BY_PATH_RE.match(device_path)
                if not is_valid_path:
                    validation_errors.append(f"Invalid device path for {bay_id}: {device_path}")
                    continue

                # Determine if device is NVMe using regex pattern
                # Check both device node format and by-path format
                nvme_device_pattern = re.compile(r'^/dev/nvme[0-9]+(n[0-9]+)?(p[0-9]+)?$')
                nvme_by_path_pattern = re.compile(r'nvme[0-9]+n[0-9]+\Z')  # Use \Z for strict end anchor
                is_nvme = bool(nvme_device_pattern.match(device_path)) or bool(nvme_by_path_pattern.search(device_path))
                
                # Apply mapping based on device type to copy
                if is_nvme:
                    bay_map_copy[bay_id]["by_path_nvme"] = device_path
                    bay_map_copy[bay_id]["by_path"] = ""
                    # Update type to u2 for NVMe devices
                    if bay_map_copy[bay_id].get("type") != "u2":
                        bay_map_copy[bay_id]["type"] = "u2"
                else:
                    bay_map_copy[bay_id]["by_path"] = device_path
                    bay_map_copy[bay_id]["by_path_nvme"] = ""
                
                updated_bays += 1
            
            # If there were validation errors, return them
            if validation_errors:
                return jsonify({
                    "error": "Validation failed",
                    "details": validation_errors,
                    "updated_bays": updated_bays
                }), 400
            
            # Save updated bay map copy (atomic operation to avoid TOCTOU)
            try:
                save_bay_map(compose_bay_map_document(bay_map_copy, layout_metadata), config_dir)
                # Only update in-memory state after successful save
                bay_map = bay_map_copy
            except Exception as e:
                logger.error(f"Failed to save bay map: {str(e)}", exc_info=True)
                return jsonify({"error": f"Failed to save bay map: {str(e)}"}), 500
        
        logger.info(f"Slot mapping applied: {updated_bays} bays updated by administrator")
        return jsonify({
            "status": "success",
            "message": f"Mapping applied successfully to {updated_bays} bay(s)",
            "updated_bays": updated_bays
        }), 200
    except Exception as e:
        logger.error(f"Slot mapping application failed: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500
