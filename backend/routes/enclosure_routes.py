# Enclosure routes: enclosure/slot/template CRUD and hardware topology
# Extracted from admin_routes.py for modularity (fix-plan-G1)
from flask import Blueprint, jsonify, request
from app_config import logger, limiter
from common import get_config_dir, load_bay_map, save_bay_map, BAY_MAP_LOCK, ENCLOSURE_SCHEMA, SLOT_SCHEMA, SLOT_MAPPING_SCHEMA, TEMPLATE_SCHEMA
from layout_templates import load_layout_templates, save_layout_templates, TEMPLATES_LOCK, build_traversal_positions, SUPPORTED_TRAVERSALS
from device_discovery import (
    generate_master_slot_map,
    validate_pci_address,
    invalidate_sas_expander_cache,
    invalidate_scsi_projections_cache,
    invalidate_master_slot_cache,
    invalidate_enclosure_cache,
    invalidate_pci_cache,
    invalidate_discovery_cache,
    get_enclosure_hardware_info
)
from routes._shared import require_admin_auth, is_valid_id, _validate_slot_metadata, MAX_ENCLOSURES, MAX_SLOTS_PER_ENCLOSURE, MAX_TEMPLATES

enclosure_bp = Blueprint('enclosure_routes', __name__)


# ==================== Enclosure Management APIs ====================

@enclosure_bp.route("/api/admin/hardware-enclosure-info", methods=["GET"])
@require_admin_auth
@limiter.limit("30 per minute")
def get_hardware_enclosure_info():
    """Return SES hardware info for each PCI controller.

    Returns vendor, model, total_slots, and occupied_slots for each enclosure
    controller detected in /sys/class/enclosure. Used by the enclosure wizard
    to identify enclosures with human-readable names.
    """
    try:
        hardware_info = get_enclosure_hardware_info()
        return jsonify({"hardware_info": hardware_info}), 200
    except Exception as e:
        logger.error(f"Error getting hardware enclosure info: {e}")
        return jsonify({"error": str(e)}), 500


@enclosure_bp.route("/api/admin/enclosures", methods=["GET", "POST"])
@require_admin_auth
@limiter.limit("30 per minute")
def manage_enclosures():
    """Handle enclosure listing and creation."""
    config_dir = get_config_dir()
    
    if request.method == "GET":
        try:
            bay_map = load_bay_map(config_dir)
            enclosures = bay_map.get("enclosures", {})
            templates_dict, _ = load_layout_templates(config_dir)
            templates = list(templates_dict.values())
            
            # Return enclosures with their template details merged
            template_map = templates_dict
            enclosure_list = []
            
            for enc_id, enc_data in enclosures.items():
                template_id = enc_data.get("template_id")
                template = template_map.get(template_id, {})
                
                enclosure_list.append({
                    "id": enc_id,
                    **enc_data,
                    "template_name": template.get("name", "Unknown"),
                    "template": template
                })
            
            # Sort by display_order
            enclosure_list.sort(key=lambda x: x.get("display_order", 0))
            
            return jsonify({
                "enclosures": enclosure_list,
                "templates": templates
            }), 200
        except Exception as e:
            logger.error(f"Error listing enclosures: {e}")
            return jsonify({"error": str(e)}), 500
    
    else:  # POST - Create new enclosure
        try:
            payload = request.get_json(silent=True) or {}
            
            # Validate required fields
            required_fields = ["id", "name", "template_id", "pci_controller"]
            for field in required_fields:
                if field not in payload:
                    return jsonify({"error": f"Missing required field: {field}"}), 400

            # Validate enclosure name length (A91)
            if len(payload.get("name", "")) > 100:
                return jsonify({"error": "Enclosure name must be 100 characters or less"}), 400
            
            # Validate enclosure ID format
            if not is_valid_id(payload["id"]):
                return jsonify({"error": f"Invalid enclosure ID format: {payload['id']}. Only alphanumeric, hyphens, and underscores allowed"}), 400
            
            # Validate PCI address format
            pci_controller = payload["pci_controller"]
            if not validate_pci_address(pci_controller):
                return jsonify({"error": f"Invalid PCI address format: {pci_controller}"}), 400
            
            # Validate expander_sas_address if provided
            expander_sas_address = payload.get("expander_sas_address")
            if expander_sas_address is not None:
                if not expander_sas_address.startswith("0x") or len(expander_sas_address) != 18 or not all(c in "0123456789abcdefABCDEF" for c in expander_sas_address[2:]):
                    return jsonify({"error": f"Invalid expander SAS address format: {expander_sas_address}"}), 400
            
            # Validate PCI controller exists in master map (outside lock to avoid holding lock during expensive operation)
            master_map = generate_master_slot_map(force_refresh=True)
            pci_controllers = set(entry["pci_controller"] for entry in master_map)
            
            if pci_controller not in pci_controllers:
                return jsonify({"error": f"PCI controller not found in system: {pci_controller}"}), 400
            
            # Load templates from the same source the frontend uses
            templates_dict, _ = load_layout_templates(config_dir)
            template_map = templates_dict
            
            if payload["template_id"] not in template_map:
                return jsonify({"error": f"Template not found: {payload['template_id']}"}), 400
            
            with BAY_MAP_LOCK:
                bay_map = load_bay_map(config_dir)
                
                # Check for duplicate enclosure ID (inside lock to prevent TOCTOU race condition)
                enclosures = bay_map.get("enclosures", {})
                if payload["id"] in enclosures:
                    return jsonify({"error": f"Enclosure ID already exists: {payload['id']}"}), 400
                
                # Enforce size limit for DoS prevention (Rule #5)
                if len(enclosures) >= MAX_ENCLOSURES:
                    return jsonify({"error": f"Maximum number of enclosures ({MAX_ENCLOSURES}) reached"}), 400
                
                # Build enclosure object
                enclosure = {
                    "id": payload["id"],
                    "name": payload["name"],
                    "template_id": payload["template_id"],
                    "pci_controller": pci_controller,
                    "expander_sas_address": expander_sas_address,
                    "display_order": payload.get("display_order", len(enclosures)),
                    "slots": {}
                }
                
                # Validate against schema
                try:
                    from jsonschema import validate
                    validate(instance=enclosure, schema=ENCLOSURE_SCHEMA)
                except Exception as e:
                    return jsonify({"error": f"Enclosure validation failed: {str(e)}"}), 400
                
                # Auto-generate slots if requested
                auto_map_slots = payload.get("auto_map_slots", False)
                nvme_start_slot = payload.get("nvme_start_slot")
                starting_slot_number = payload.get("starting_slot_number")
                custom_labels = payload.get("custom_labels", {})
                custom_roles = payload.get("custom_roles", {})

                # Validate custom_labels and custom_roles
                err = _validate_slot_metadata(custom_labels, custom_roles, None)
                if err:
                    return jsonify({"error": err}), 400

                # Check if frontend provided explicit slot mappings
                slot_mappings = payload.get("slot_mappings")
                if slot_mappings:
                    # Use frontend-provided slot mappings with HW identifiers
                    template = template_map[payload["template_id"]]
                    slot_count = template.get("slot_count", 0)
                    rows = template.get("rows", 1)
                    cols = template.get("cols", 1)
                    traversal_preset = template.get("traversal_preset", "top_left_down_then_across")

                    if slot_count <= 0:
                        return jsonify({"error": "Template has no slots defined (slot_count is 0). Use a template with at least 1 slot."}), 400

                    # Enforce size limit for slots per enclosure (Rule #5)
                    if slot_count > MAX_SLOTS_PER_ENCLOSURE:
                        return jsonify({"error": f"Slot count ({slot_count}) exceeds maximum ({MAX_SLOTS_PER_ENCLOSURE})"}), 400

                    # Safe numeric conversion for starting_slot_number (Rule #84)
                    try:
                        starting_slot = int(starting_slot_number) if starting_slot_number is not None else 0
                        if starting_slot < 0 or starting_slot > 9999:
                            return jsonify({"error": "Starting slot number must be between 0 and 9999"}), 400
                    except (ValueError, TypeError):
                        return jsonify({"error": "Invalid starting_slot_number: must be a valid integer"}), 400

                    # Validate slot_mappings entries
                    err = _validate_slot_metadata({}, {}, slot_mappings, default_role=template.get("default_role", "wipe"))
                    if err:
                        return jsonify({"error": err}), 400

                    # Build traversal positions
                    if rows > 0 and cols > 0 and traversal_preset in SUPPORTED_TRAVERSALS:
                        try:
                            positions = build_traversal_positions(rows, cols, traversal_preset, slot_count)
                        except ValueError as e:
                            return jsonify({"error": f"Failed to build traversal positions: {str(e)}"}), 400
                    else:
                        positions = [(i, 0) for i in range(slot_count)]

                    # Build slots from frontend-provided mappings
                    for slot_index, (row, col) in enumerate(positions):
                        slot_key = str(slot_index)
                        slot_mapping = slot_mappings.get(slot_key, {})
                        
                        # Calculate physical slot number
                        physical_slot = starting_slot + slot_index

                        role = slot_mapping.get("role", template.get("default_role", "wipe"))
                        slot_data = {
                            "physical_slot_number": physical_slot,
                            "physical_position": {"row": row, "col": col},
                            "label": slot_mapping.get("label", f"Bay {slot_index}"),
                            "role": role,
                            "locked": slot_mapping.get("locked", role == "os"),
                            "mappings": slot_mapping.get("mappings", {})
                        }

                        enclosure["slots"][slot_key] = slot_data

                elif auto_map_slots:
                    template = template_map[payload["template_id"]]
                    slot_count = template.get("slot_count", 0)
                    hybrid_slots = template.get("hybrid_slots", [])
                    rows = template.get("rows", 1)
                    cols = template.get("cols", 1)
                    traversal_preset = template.get("traversal_preset", "top_left_down_then_across")

                    if slot_count <= 0:
                        return jsonify({"error": "Template has no slots defined (slot_count is 0). Use a template with at least 1 slot."}), 400

                    # Enforce size limit for slots per enclosure (Rule #5)
                    if slot_count > MAX_SLOTS_PER_ENCLOSURE:
                        return jsonify({"error": f"Slot count ({slot_count}) exceeds maximum ({MAX_SLOTS_PER_ENCLOSURE})"}), 400

                    # Generate slots based on template traversal order
                    # Safe numeric conversion for starting_slot_number (Rule #84)
                    try:
                        starting_slot = int(starting_slot_number) if starting_slot_number is not None else 0
                        if starting_slot < 0 or starting_slot > 9999:
                            return jsonify({"error": "Starting slot number must be between 0 and 9999"}), 400
                    except (ValueError, TypeError):
                        return jsonify({"error": "Invalid starting_slot_number: must be a valid integer"}), 400

                    # Safe numeric conversion for nvme_start_slot (A-B3-9)
                    if nvme_start_slot is not None:
                        try:
                            nvme_start_slot = int(nvme_start_slot)
                            if nvme_start_slot < 0 or nvme_start_slot > 9999:
                                return jsonify({"error": "nvme_start_slot must be between 0 and 9999"}), 400
                        except (ValueError, TypeError):
                            return jsonify({"error": "Invalid nvme_start_slot: must be a valid integer"}), 400

                    # Build traversal positions if template has grid layout (rows/cols)
                    # Otherwise use linear iteration for simple slot_count-only templates
                    if rows > 0 and cols > 0 and traversal_preset in SUPPORTED_TRAVERSALS:
                        try:
                            positions = build_traversal_positions(rows, cols, traversal_preset, slot_count)
                        except ValueError as e:
                            return jsonify({"error": f"Failed to build traversal positions: {str(e)}"}), 400
                    else:
                        # Fallback to linear iteration for templates without grid layout
                        positions = [(i, 0) for i in range(slot_count)]

                    for slot_index, (row, col) in enumerate(positions):
                        slot_key = str(slot_index)
                        # Calculate physical slot number: starting_slot + logical slot index
                        physical_slot = starting_slot + slot_index
                        slot_role = custom_roles.get(slot_key, template.get("default_role", "wipe"))
                        slot_data = {
                            "physical_slot_number": physical_slot,
                            "physical_position": {"row": row, "col": col},
                            "label": custom_labels.get(slot_key, f"Bay {slot_index}"),
                            "role": slot_role,
                            "locked": slot_role == "os",
                            "mappings": {}
                        }

                        # Compute HW identifiers arithmetically from the controller type.
                        # The master map only contains entries for currently occupied slots,
                        # so it cannot fill empty bays. Worse, searching it by physical slot
                        # number can match drives from other controllers that happen to share
                        # that slot number, producing non-sequential, random-looking results
                        # (e.g. ata1/ata2 between phy-0:0:0 and phy-0:0:3 on a SAS expander).
                        # Always derive the identifier from the controller pattern instead.
                        if expander_sas_address:
                            # SAS expander connection
                            sas_hw_id = f"phy-0:0:{physical_slot}"
                            sas_slot_type = "sas_expander"
                        else:
                            # Direct SAS (backplane without expander) - default to sas_direct
                            # motherboard_sata is only for actual motherboard SATA ports
                            sas_hw_id = f"phy-0:0:{physical_slot}"
                            sas_slot_type = "sas_direct"

                        slot_data["mappings"]["sas_sata"] = {
                            "slot_type": sas_slot_type,
                            "hardware_identifier": sas_hw_id,
                            "auto_detected": True
                        }

                        # Compute NVMe mapping for hybrid slots
                        if slot_index in hybrid_slots and nvme_start_slot is not None:
                            nvme_offset = hybrid_slots.index(slot_index)
                            nvme_slot_num = int(nvme_start_slot) + nvme_offset
                            # NVMe hardware identifier is the slot folder name in /sys/bus/pci/slots/
                            nvme_hw_id = str(nvme_slot_num)

                            slot_data["mappings"]["nvme"] = {
                                "slot_type": "pcie_nvme",
                                "hardware_identifier": nvme_hw_id,
                                "auto_detected": True
                            }

                        enclosure["slots"][slot_key] = slot_data
                
                # Save to bay_map.json
                bay_map.setdefault("enclosures", {})[payload["id"]] = enclosure
                save_bay_map(bay_map, config_dir)
                
                # Invalidate hardware topology caches since enclosure was added
                invalidate_sas_expander_cache()
                invalidate_scsi_projections_cache()
                invalidate_master_slot_cache()
                invalidate_enclosure_cache()
                invalidate_pci_cache()
                invalidate_discovery_cache()
            
            logger.info(f"Created enclosure: {payload['id']}")
            return jsonify({"status": "success", "enclosure": enclosure}), 201
            
        except Exception as e:
            logger.error(f"Error creating enclosure: {e}")
            return jsonify({"error": str(e)}), 500


@enclosure_bp.route("/api/admin/enclosures/<enclosure_id>", methods=["GET", "PUT", "DELETE"])
@require_admin_auth
@limiter.limit("30 per minute")
def manage_enclosure(enclosure_id):
    """Handle single enclosure operations."""
    config_dir = get_config_dir()
    
    if request.method == "GET":
        try:
            bay_map = load_bay_map(config_dir)
            enclosures = bay_map.get("enclosures", {})
            
            if enclosure_id not in enclosures:
                return jsonify({"error": f"Enclosure not found: {enclosure_id}"}), 404
            
            enclosure = enclosures[enclosure_id]
            
            # Merge template details from the same source the frontend uses
            templates_dict, _ = load_layout_templates(config_dir)
            template_id = enclosure.get("template_id")
            template = templates_dict.get(template_id, {})
            
            return jsonify({
                "id": enclosure_id,
                **enclosure,
                "template_name": template.get("name", "Unknown"),
                "template": template
            }), 200
        except Exception as e:
            logger.error(f"Error getting enclosure: {e}")
            return jsonify({"error": str(e)}), 500
    
    elif request.method == "PUT":
        try:
            payload = request.get_json(silent=True) or {}
            
            # Validate PCI address if updated (outside lock to avoid holding lock during expensive operation)
            if "pci_controller" in payload:
                if not validate_pci_address(payload["pci_controller"]):
                    return jsonify({"error": f"Invalid PCI address format: {payload['pci_controller']}"}), 400
                
                # Validate PCI controller exists (outside lock)
                master_map = generate_master_slot_map(force_refresh=True)
                pci_controllers = set(entry["pci_controller"] for entry in master_map)
                if payload["pci_controller"] not in pci_controllers:
                    return jsonify({"error": f"PCI controller not found in system: {payload['pci_controller']}"}), 400
            
            # Validate expander_sas_address if updated (outside lock)
            if "expander_sas_address" in payload:
                expander_sas_address = payload["expander_sas_address"]
                if expander_sas_address is not None:
                    if not expander_sas_address.startswith("0x") or len(expander_sas_address) != 18 or not all(c in "0123456789abcdefABCDEF" for c in expander_sas_address[2:]):
                        return jsonify({"error": f"Invalid expander SAS address format: {expander_sas_address}"}), 400
            
            # Validate enclosure name length if updated (A-B3-5)
            if "name" in payload and len(str(payload["name"])) > 100:
                return jsonify({"error": "Enclosure name must be 100 characters or less"}), 400

            # Validate custom_labels, custom_roles, and slot_mappings if provided
            custom_labels = payload.get("custom_labels", {})
            custom_roles = payload.get("custom_roles", {})
            slot_mappings = payload.get("slot_mappings")
            err = _validate_slot_metadata(custom_labels, custom_roles, slot_mappings)
            if err:
                return jsonify({"error": err}), 400
            
            with BAY_MAP_LOCK:
                bay_map = load_bay_map(config_dir)
                enclosures = bay_map.get("enclosures", {})
                
                if enclosure_id not in enclosures:
                    return jsonify({"error": f"Enclosure not found: {enclosure_id}"}), 404
                
                enclosure = enclosures[enclosure_id]
                slots = enclosure.get("slots", {})
                
                # Validate that all provided slot keys exist in the enclosure
                for slot_key in custom_labels.keys():
                    if slot_key not in slots:
                        return jsonify({"error": f"Slot {slot_key} not found in enclosure"}), 400
                for slot_key in custom_roles.keys():
                    if slot_key not in slots:
                        return jsonify({"error": f"Slot {slot_key} not found in enclosure"}), 400
                if slot_mappings is not None:
                    for slot_key in slot_mappings.keys():
                        if slot_key not in slots:
                            return jsonify({"error": f"Slot {slot_key} not found in enclosure"}), 400
                
                # Update allowed fields
                updatable_fields = ["name", "template_id", "pci_controller", "expander_sas_address", "display_order"]
                for field in updatable_fields:
                    if field in payload:
                        enclosure[field] = payload[field]
                
                # Update custom labels and roles on existing slots
                slots = enclosure.get("slots", {})
                for slot_key, label in custom_labels.items():
                    if slot_key in slots:
                        slots[slot_key]["label"] = label
                for slot_key, role in custom_roles.items():
                    if slot_key in slots:
                        slots[slot_key]["role"] = role
                        slots[slot_key]["locked"] = role == "os"
                
                # Update slot_mappings on existing slots
                if slot_mappings is not None:
                    for slot_key, slot_mapping in slot_mappings.items():
                        if slot_key not in slots:
                            continue
                        if "label" in slot_mapping:
                            slots[slot_key]["label"] = slot_mapping["label"]
                        if "role" in slot_mapping:
                            slots[slot_key]["role"] = slot_mapping["role"]
                            slots[slot_key]["locked"] = slot_mapping["role"] == "os"
                        if "mappings" in slot_mapping:
                            slots[slot_key]["mappings"] = slot_mapping["mappings"]
                
                # Validate template exists if updated (from the same source the frontend uses)
                if "template_id" in payload:
                    templates_dict, _ = load_layout_templates(config_dir)
                    if payload["template_id"] not in templates_dict:
                        return jsonify({"error": f"Template not found: {payload['template_id']}"}), 400
                
                # Validate against schema
                try:
                    from jsonschema import validate
                    validate(instance=enclosure, schema=ENCLOSURE_SCHEMA)
                except Exception as e:
                    return jsonify({"error": f"Enclosure validation failed: {str(e)}"}), 400
                
                bay_map["enclosures"][enclosure_id] = enclosure
                save_bay_map(bay_map, config_dir)
                
                # Invalidate hardware topology caches since enclosure was edited
                invalidate_sas_expander_cache()
                invalidate_scsi_projections_cache()
                invalidate_master_slot_cache()
                invalidate_enclosure_cache()
                invalidate_pci_cache()
                invalidate_discovery_cache()
            
            logger.info(f"Updated enclosure: {enclosure_id}")
            return jsonify({"status": "success", "enclosure": enclosure}), 200
            
        except Exception as e:
            logger.error(f"Error updating enclosure: {e}")
            return jsonify({"error": str(e)}), 500
    
    elif request.method == "DELETE":
        try:
            with BAY_MAP_LOCK:
                bay_map = load_bay_map(config_dir)
                enclosures = bay_map.get("enclosures", {})
                
                if enclosure_id not in enclosures:
                    return jsonify({"error": f"Enclosure not found: {enclosure_id}"}), 404
                
                del bay_map["enclosures"][enclosure_id]
                save_bay_map(bay_map, config_dir)
                
                # Invalidate hardware topology caches since enclosure was deleted
                invalidate_sas_expander_cache()
                invalidate_scsi_projections_cache()
                invalidate_master_slot_cache()
                invalidate_enclosure_cache()
                invalidate_pci_cache()
                invalidate_discovery_cache()
            
            logger.info(f"Deleted enclosure: {enclosure_id}")
            return jsonify({"status": "success", "message": f"Enclosure {enclosure_id} deleted"}), 200
            
        except Exception as e:
            logger.error(f"Error deleting enclosure: {e}")
            return jsonify({"error": str(e)}), 500


@enclosure_bp.route("/api/admin/enclosures/<enclosure_id>/slots", methods=["POST"])
@require_admin_auth
@limiter.limit("30 per minute")
def add_enclosure_slot(enclosure_id):
    """Add a slot to an enclosure."""
    config_dir = get_config_dir()
    
    try:
        payload = request.get_json(silent=True) or {}
        
        # Validate required fields
        if "physical_slot_number" not in payload:
            return jsonify({"error": "Missing required field: physical_slot_number"}), 400
        
        with BAY_MAP_LOCK:
            bay_map = load_bay_map(config_dir)
            enclosures = bay_map.get("enclosures", {})
            
            if enclosure_id not in enclosures:
                return jsonify({"error": f"Enclosure not found: {enclosure_id}"}), 404
            
            enclosure = enclosures[enclosure_id]
            slot_num = payload["physical_slot_number"]
            if not isinstance(slot_num, int) or isinstance(slot_num, bool):
                return jsonify({"error": "physical_slot_number must be an integer"}), 400
            if slot_num < 0 or slot_num > 9999:
                return jsonify({"error": "physical_slot_number must be between 0 and 9999"}), 400
            slot_key = str(slot_num)
            
            # Check if slot already exists
            if slot_key in enclosure.get("slots", {}):
                return jsonify({"error": f"Slot {slot_num} already exists in enclosure"}), 400
            
            # Enforce size limit for slots per enclosure (Rule #5)
            existing_slots = len(enclosure.get("slots", {}))
            if existing_slots >= MAX_SLOTS_PER_ENCLOSURE:
                return jsonify({"error": f"Maximum number of slots ({MAX_SLOTS_PER_ENCLOSURE}) reached for enclosure"}), 400
            
            # Build slot object
            slot_data = {
                "physical_slot_number": slot_num,
                "label": payload.get("label", f"Bay {slot_num + 1}"),
                "role": payload.get("role", "wipe"),
                "locked": payload.get("locked", False),
                "mappings": payload.get("mappings", {})
            }
            
            # Validate against schema
            try:
                from jsonschema import validate
                validate(instance=slot_data, schema=SLOT_SCHEMA)
            except Exception as e:
                return jsonify({"error": f"Slot validation failed: {str(e)}"}), 400
            
            enclosure.setdefault("slots", {})[slot_key] = slot_data
            bay_map["enclosures"][enclosure_id] = enclosure
            save_bay_map(bay_map, config_dir)
        
        logger.info(f"Added slot {slot_num} to enclosure {enclosure_id}")
        return jsonify({"status": "success", "slot": slot_data}), 201
        
    except Exception as e:
        logger.error(f"Error adding slot to enclosure: {e}")
        return jsonify({"error": str(e)}), 500


@enclosure_bp.route("/api/admin/enclosures/<enclosure_id>/slots/<slot_num>", methods=["PUT", "DELETE"])
@require_admin_auth
@limiter.limit("30 per minute")
def manage_enclosure_slot(enclosure_id, slot_num):
    """Handle slot update and deletion."""
    config_dir = get_config_dir()
    
    if request.method == "PUT":
        try:
            payload = request.get_json(silent=True) or {}
            
            with BAY_MAP_LOCK:
                bay_map = load_bay_map(config_dir)
                enclosures = bay_map.get("enclosures", {})
                
                if enclosure_id not in enclosures:
                    return jsonify({"error": f"Enclosure not found: {enclosure_id}"}), 404
                
                enclosure = enclosures[enclosure_id]
                slots = enclosure.get("slots", {})
                
                if slot_num not in slots:
                    return jsonify({"error": f"Slot {slot_num} not found in enclosure"}), 404
                
                slot = slots[slot_num]
                
                # Update allowed fields
                updatable_fields = ["label", "role", "locked", "mappings"]
                for field in updatable_fields:
                    if field in payload:
                        slot[field] = payload[field]
                
                # Validate against schema
                try:
                    from jsonschema import validate
                    validate(instance=slot, schema=SLOT_SCHEMA)
                except Exception as e:
                    return jsonify({"error": f"Slot validation failed: {str(e)}"}), 400
                
                bay_map["enclosures"][enclosure_id] = enclosure
                save_bay_map(bay_map, config_dir)
            
            logger.info(f"Updated slot {slot_num} in enclosure {enclosure_id}")
            return jsonify({"status": "success", "slot": slot}), 200
            
        except Exception as e:
            logger.error(f"Error updating slot: {e}")
            return jsonify({"error": str(e)}), 500
    
    elif request.method == "DELETE":
        try:
            with BAY_MAP_LOCK:
                bay_map = load_bay_map(config_dir)
                enclosures = bay_map.get("enclosures", {})
                
                if enclosure_id not in enclosures:
                    return jsonify({"error": f"Enclosure not found: {enclosure_id}"}), 404
                
                enclosure = enclosures[enclosure_id]
                slots = enclosure.get("slots", {})
                
                if slot_num not in slots:
                    return jsonify({"error": f"Slot {slot_num} not found in enclosure"}), 404
                
                del enclosure["slots"][slot_num]
                bay_map["enclosures"][enclosure_id] = enclosure
                save_bay_map(bay_map, config_dir)
            
            logger.info(f"Deleted slot {slot_num} from enclosure {enclosure_id}")
            return jsonify({"status": "success", "message": f"Slot {slot_num} deleted"}), 200
            
        except Exception as e:
            logger.error(f"Error deleting slot: {e}")
            return jsonify({"error": str(e)}), 500


@enclosure_bp.route("/api/admin/enclosures/<enclosure_id>/slots/<slot_num>/mappings/<mapping_type>", methods=["PUT", "DELETE"])
@require_admin_auth
@limiter.limit("30 per minute")
def manage_slot_mapping(enclosure_id, slot_num, mapping_type):
    """Handle slot mapping update and deletion."""
    config_dir = get_config_dir()
    
    if mapping_type not in ["sas_sata", "nvme"]:
        return jsonify({"error": f"Invalid mapping type: {mapping_type}"}), 400
    
    if request.method == "PUT":
        try:
            payload = request.get_json(silent=True) or {}
            
            with BAY_MAP_LOCK:
                bay_map = load_bay_map(config_dir)
                enclosures = bay_map.get("enclosures", {})
                
                if enclosure_id not in enclosures:
                    return jsonify({"error": f"Enclosure not found: {enclosure_id}"}), 404
                
                enclosure = enclosures[enclosure_id]
                slots = enclosure.get("slots", {})
                
                if slot_num not in slots:
                    return jsonify({"error": f"Slot {slot_num} not found in enclosure"}), 404
                
                slot = slots[slot_num]
                mappings = slot.setdefault("mappings", {})
                
                # Build mapping object
                mapping_data = {
                    "slot_type": payload.get("slot_type"),
                    "hardware_identifier": payload.get("hardware_identifier"),
                    "auto_detected": payload.get("auto_detected", False)
                }
                
                # Validate required fields
                if not mapping_data["slot_type"] or not mapping_data["hardware_identifier"]:
                    return jsonify({"error": "Missing required fields: slot_type, hardware_identifier"}), 400
                
                # Validate against schema
                try:
                    from jsonschema import validate
                    validate(instance=mapping_data, schema=SLOT_MAPPING_SCHEMA)
                except Exception as e:
                    return jsonify({"error": f"Mapping validation failed: {str(e)}"}), 400
                
                mappings[mapping_type] = mapping_data
                bay_map["enclosures"][enclosure_id] = enclosure
                save_bay_map(bay_map, config_dir)
            
            logger.info(f"Updated {mapping_type} mapping for slot {slot_num} in enclosure {enclosure_id}")
            return jsonify({"status": "success", "mapping": mapping_data}), 200
            
        except Exception as e:
            logger.error(f"Error updating slot mapping: {e}")
            return jsonify({"error": str(e)}), 500
    
    elif request.method == "DELETE":
        try:
            with BAY_MAP_LOCK:
                bay_map = load_bay_map(config_dir)
                enclosures = bay_map.get("enclosures", {})
                
                if enclosure_id not in enclosures:
                    return jsonify({"error": f"Enclosure not found: {enclosure_id}"}), 404
                
                enclosure = enclosures[enclosure_id]
                slots = enclosure.get("slots", {})
                
                if slot_num not in slots:
                    return jsonify({"error": f"Slot {slot_num} not found in enclosure"}), 404
                
                slot = slots[slot_num]
                mappings = slot.get("mappings", {})
                
                if mapping_type not in mappings:
                    return jsonify({"error": f"Mapping {mapping_type} not found for slot"}), 404
                
                del mappings[mapping_type]
                bay_map["enclosures"][enclosure_id] = enclosure
                save_bay_map(bay_map, config_dir)
            
            logger.info(f"Deleted {mapping_type} mapping for slot {slot_num} in enclosure {enclosure_id}")
            return jsonify({"status": "success", "message": f"Mapping {mapping_type} deleted"}), 200
            
        except Exception as e:
            logger.error(f"Error deleting slot mapping: {e}")
            return jsonify({"error": str(e)}), 500


@enclosure_bp.route("/api/admin/templates", methods=["GET", "POST"])
@require_admin_auth
@limiter.limit("30 per minute")
def manage_templates():
    """Handle template listing and creation."""
    config_dir = get_config_dir()
    
    if request.method == "GET":
        try:
            templates_dict, _ = load_layout_templates(config_dir)
            templates = list(templates_dict.values())
            return jsonify({"templates": templates}), 200
        except Exception as e:
            logger.error(f"Error listing templates: {e}")
            return jsonify({"error": str(e)}), 500
    
    else:  # POST - Create new template
        try:
            payload = request.get_json(silent=True) or {}
            
            # Validate required fields
            required_fields = ["id", "name", "slot_count"]
            for field in required_fields:
                if field not in payload:
                    return jsonify({"error": f"Missing required field: {field}"}), 400
            
            # Validate template ID format
            if not is_valid_id(payload["id"]):
                return jsonify({"error": f"Invalid template ID format: {payload['id']}. Only alphanumeric, hyphens, and underscores allowed"}), 400
            
            # Validate against schema
            try:
                from jsonschema import validate
                validate(instance=payload, schema=TEMPLATE_SCHEMA)
            except Exception as e:
                return jsonify({"error": f"Template validation failed: {str(e)}"}), 400
            
            with TEMPLATES_LOCK:
                templates_dict, _ = load_layout_templates(config_dir)
                
                # Enforce size limit for DoS prevention (Rule #5)
                if len(templates_dict) >= MAX_TEMPLATES:
                    return jsonify({"error": f"Maximum number of templates ({MAX_TEMPLATES}) reached"}), 400
                
                # Check for duplicate template ID
                if payload["id"] in templates_dict:
                    return jsonify({"error": f"Template ID already exists: {payload['id']}"}), 400
                
                templates_dict[payload["id"]] = payload
                save_layout_templates(templates_dict, config_dir)
            
            logger.info(f"Created template: {payload['id']}")
            return jsonify({"status": "success", "template": payload}), 201
            
        except Exception as e:
            logger.error(f"Error creating template: {e}")
            return jsonify({"error": str(e)}), 500


@enclosure_bp.route("/api/admin/templates/<template_id>", methods=["PUT", "DELETE"])
@require_admin_auth
@limiter.limit("30 per minute")
def manage_template(template_id):
    """Handle template update and deletion."""
    config_dir = get_config_dir()
    
    if request.method == "PUT":
        try:
            payload = request.get_json(silent=True) or {}
            
            with TEMPLATES_LOCK:
                templates_dict, _ = load_layout_templates(config_dir)
                
                if template_id not in templates_dict:
                    return jsonify({"error": f"Template not found: {template_id}"}), 404
                
                template = templates_dict[template_id]
                
                # Update allowed fields
                updatable_fields = ["name", "vendor", "slot_count", "hybrid_slots", "traversal_preset", "default_role"]
                for field in updatable_fields:
                    if field in payload:
                        template[field] = payload[field]
                
                # Validate against schema
                try:
                    from jsonschema import validate
                    validate(instance=template, schema=TEMPLATE_SCHEMA)
                except Exception as e:
                    return jsonify({"error": f"Template validation failed: {str(e)}"}), 400
                
                templates_dict[template_id] = template
                save_layout_templates(templates_dict, config_dir)
            
            logger.info(f"Updated template: {template_id}")
            return jsonify({"status": "success", "template": template}), 200
            
        except Exception as e:
            logger.error(f"Error updating template: {e}")
            return jsonify({"error": str(e)}), 500
    
    elif request.method == "DELETE":
        try:
            # Acquire both locks in consistent order (BAY_MAP_LOCK → TEMPLATES_LOCK)
            # to prevent two-phase lock race: enclosure can be created referencing
            # this template between the check and delete if locks are released separately.
            with BAY_MAP_LOCK:
                with TEMPLATES_LOCK:
                    # Check if template is in use by any enclosure
                    bay_map = load_bay_map(config_dir)
                    enclosures = bay_map.get("enclosures", {})
                    for enc_id, enc_data in enclosures.items():
                        if enc_data.get("template_id") == template_id:
                            return jsonify({"error": f"Template is in use by enclosure: {enc_id}"}), 400

                    # Delete from layout_templates.json
                    templates_dict, _ = load_layout_templates(config_dir)

                    if template_id not in templates_dict:
                        return jsonify({"error": f"Template not found: {template_id}"}), 404

                    del templates_dict[template_id]
                    save_layout_templates(templates_dict, config_dir)
            
            logger.info(f"Deleted template: {template_id}")
            return jsonify({"status": "success", "message": f"Template {template_id} deleted"}), 200
            
        except Exception as e:
            logger.error(f"Error deleting template: {e}")
            return jsonify({"error": str(e)}), 500


@enclosure_bp.route("/api/admin/master-slot-map", methods=["GET"])
@require_admin_auth
@limiter.limit("30 per minute")
def get_master_slot_map():
    """Return the master slot map (hardware topology)."""
    try:
        force_refresh = request.args.get("force_refresh", "false").lower() == "true"
        master_map = generate_master_slot_map(force_refresh=force_refresh)
        
        # Group by PCI controller for easier display
        grouped = {}
        for entry in master_map:
            pci = entry["pci_controller"]
            if pci not in grouped:
                grouped[pci] = []
            grouped[pci].append(entry)
        
        return jsonify({
            "master_map": master_map,
            "grouped_by_controller": grouped
        }), 200
    except Exception as e:
        logger.error(f"Error getting master slot map: {e}")
        return jsonify({"error": str(e)}), 500
