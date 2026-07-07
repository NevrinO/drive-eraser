import json
import os
import re
import tempfile
import hashlib
import logging
from threading import Lock

logger = logging.getLogger("app")

SUPPORTED_TRAVERSALS = {
    "top_left_down_then_across",
    "bottom_left_up_then_across",
    "top_left_across_then_down",
    "bottom_left_across_then_up"
}

# Lock for template file operations to prevent race conditions (Lesson #2)
TEMPLATES_LOCK = Lock()

DEFAULT_TEMPLATES = {
    "dell_r320_4bay": {
        "id": "dell_r320_4bay",
        "name": "Dell R320 4-Bay (3.5\")",
        "vendor": "Dell",
        "rows": 1,
        "cols": 4,
        "bay_count": 4,
        "slot_count": 4,
        "traversal_preset": "top_left_down_then_across"
    },
    "dell_r440_10bay": {
        "id": "dell_r440_10bay",
        "name": "Dell R440 10-Bay (2.5\")",
        "vendor": "Dell",
        "rows": 2,
        "cols": 5,
        "bay_count": 10,
        "slot_count": 10,
        "traversal_preset": "top_left_down_then_across"
    }
}


def is_bay_entry(value):
    if not isinstance(value, dict):
        return False
    marker_keys = {"role", "by_path", "by_path_nvme", "type", "label", "locked"}
    return any(k in value for k in marker_keys)


def normalize_bay_map_document(document):
    if not isinstance(document, dict):
        return {}, {}

    if isinstance(document.get("bays"), dict):
        bays = {k: v for k, v in document.get("bays", {}).items() if is_bay_entry(v)}
        metadata = document.get("layout_metadata") if isinstance(document.get("layout_metadata"), dict) else {}
        return bays, metadata

    bays = {}
    metadata = {}
    for key, value in document.items():
        if key == "layout_metadata" and isinstance(value, dict):
            metadata = value
            continue
        if is_bay_entry(value):
            bays[key] = value
    return bays, metadata


def compose_bay_map_document(bays, metadata, enclosures=None):
    clean_bays = {k: v for k, v in (bays or {}).items() if is_bay_entry(v)}
    clean_meta = metadata if isinstance(metadata, dict) else {}
    
    # If no metadata, return bays directly (flat structure)
    if not clean_meta and not enclosures:
        return clean_bays
    
    result = {}
    if clean_meta:
        result["layout_metadata"] = clean_meta
    result["bays"] = clean_bays
    
    # Preserve enclosures section if provided (prevents data loss when saving bay map)
    if enclosures and isinstance(enclosures, dict):
        result["enclosures"] = enclosures
    
    return result


def load_layout_templates(config_dir):
    """
    Load templates from config directory.
    Returns (templates_dict, is_fallback) tuple where is_fallback is True if
    templates were loaded from DEFAULT_TEMPLATES due to missing/corrupted file.
    """
    path = os.path.join(config_dir, "layout_templates.json")
    hash_path = os.path.join(config_dir, "layout_templates.json.sha256")

    if not os.path.exists(path):
        logger.warning(f"Template file not found: {path}. Using DEFAULT_TEMPLATES as fallback.")
        return DEFAULT_TEMPLATES, True

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
            payload = json.loads(content)

        # Validate SHA256 hash for file integrity (Lesson #22)
        if os.path.exists(hash_path):
            try:
                with open(hash_path, "r", encoding="utf-8") as hf:
                    stored_hash = hf.read().strip()
                calculated_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()
                if stored_hash != calculated_hash:
                    logger.warning(f"Template file hash mismatch detected. File may have been tampered with: {path}. Using DEFAULT_TEMPLATES as fallback.")
                    return DEFAULT_TEMPLATES, True
            except Exception as e:
                logger.warning(f"Failed to validate template file hash: {str(e)}")
                # Continue loading despite hash validation failure

        if isinstance(payload, dict) and isinstance(payload.get("templates"), dict):
            templates = payload["templates"]
            result = {}
            for template_id, template in templates.items():
                if isinstance(template, dict):
                    entry = template.copy()
                    entry["id"] = template_id
                    # Enclosure wizard expects slot_count; derive from legacy fields if missing
                    if "slot_count" not in entry:
                        bay_count = entry.get("bay_count")
                        rows = entry.get("rows")
                        cols = entry.get("cols")
                        try:
                            if bay_count is not None:
                                entry["slot_count"] = int(bay_count)
                            elif rows is not None and cols is not None:
                                entry["slot_count"] = int(rows) * int(cols)
                        except (ValueError, TypeError):
                            entry["slot_count"] = 0
                    result[template_id] = entry
            if result:
                return result, False
            else:
                logger.warning(f"Template file contains no valid templates: {path}. Using DEFAULT_TEMPLATES as fallback.")
                return DEFAULT_TEMPLATES, True
    except Exception as e:
        logger.warning(f"Failed to load template file: {path}. Error: {str(e)}. Using DEFAULT_TEMPLATES as fallback.")
        return DEFAULT_TEMPLATES, True

    return DEFAULT_TEMPLATES, True


def build_traversal_positions(rows, cols, traversal, bay_count, skip_positions=None):
    positions = []
    rows = max(1, int(rows or 1))
    cols = max(1, int(cols or 1))
    # Respect provided bay_count; only fall back to rows * cols if not provided
    if bay_count is None or bay_count == 0:
        bay_count = rows * cols
    else:
        bay_count = int(bay_count)
    
    # Parse skip_positions into a set of (row, col) tuples for fast lookup
    skip_set = set()
    if skip_positions and isinstance(skip_positions, list):
        for skip in skip_positions:
            if isinstance(skip, dict) and "row" in skip and "col" in skip:
                try:
                    skip_set.add((int(skip["row"]), int(skip["col"])))
                except (ValueError, TypeError):
                    # Skip invalid entries silently - validation should catch these
                    continue

    if traversal == "bottom_left_up_then_across":
        for col in range(cols):
            for row in range(rows - 1, -1, -1):
                if (row, col) not in skip_set:
                    positions.append((row, col))
                if len(positions) >= bay_count:
                    return positions
    elif traversal == "top_left_across_then_down":
        for row in range(rows):
            for col in range(cols):
                if (row, col) not in skip_set:
                    positions.append((row, col))
                if len(positions) >= bay_count:
                    return positions
    elif traversal == "bottom_left_across_then_up":
        for row in range(rows - 1, -1, -1):
            for col in range(cols):
                if (row, col) not in skip_set:
                    positions.append((row, col))
                if len(positions) >= bay_count:
                    return positions
    else:
        for col in range(cols):
            for row in range(rows):
                if (row, col) not in skip_set:
                    positions.append((row, col))
                if len(positions) >= bay_count:
                    return positions
    
    # Validate that we have enough positions after skipping
    if bay_count > 0 and len(positions) == 0:
        if skip_set:
            raise ValueError(f"skip_positions eliminates all available positions (rows={rows}, cols={cols})")
        else:
            raise ValueError(f"bay_count exceeds grid capacity: requested {bay_count} bays but grid only has {rows * cols} positions (rows={rows}, cols={cols})")
    if len(positions) < bay_count:
        if skip_set:
            raise ValueError(f"skip_positions eliminates too many positions: requested {bay_count} bays but only {len(positions)} available after skipping")
        else:
            raise ValueError(f"bay_count exceeds grid capacity: requested {bay_count} bays but grid only has {rows * cols} positions (rows={rows}, cols={cols})")
    
    return positions


def apply_template(existing_bays, template, traversal_preset=None, custom_overrides=None):
    rows = int(template.get("rows") or 1)
    cols = int(template.get("cols") or 1)
    bay_count_val = template.get("bay_count")
    bay_count = int(bay_count_val) if bay_count_val is not None else (rows * cols)
    traversal = traversal_preset or template.get("traversal_preset") or "top_left_down_then_across"
    if traversal not in SUPPORTED_TRAVERSALS:
        traversal = "top_left_down_then_across"

    skip_positions = template.get("skip_positions")
    positions = build_traversal_positions(rows, cols, traversal, bay_count, skip_positions)
    overrides = custom_overrides if isinstance(custom_overrides, dict) else {}

    result = {}
    for index, (row, col) in enumerate(positions, start=0):
        bay_id = f"bay{index}"
        prior = existing_bays.get(bay_id, {}) if isinstance(existing_bays, dict) else {}
        display_number = str(index)
        override_value = overrides.get(bay_id)
        if isinstance(override_value, dict):
            override_value = override_value.get("display_number") or override_value.get("numbering_override")
        if override_value is not None and str(override_value).strip() != "":
            display_number = str(override_value).strip()

        label = prior.get("label") or "Work Bay"
        
        # Handle type override from custom_overrides
        bay_type = prior.get("type", "sas_sata")
        override_dict = overrides.get(bay_id)
        if isinstance(override_dict, dict) and override_dict.get("type"):
            bay_type = override_dict.get("type")
        
        result[bay_id] = {
            "role": prior.get("role", "wipe"),
            "locked": bool(prior.get("locked", False)),
            "type": bay_type,
            "label": label,
            "by_path": prior.get("by_path"),
            "by_path_nvme": prior.get("by_path_nvme"),
            "physical_position": {"row": row, "col": col},
            "display_number": display_number
        }
    return result, traversal


def validate_layout_metadata(layout_metadata, bays, templates):
    if layout_metadata is None:
        return None
    if not isinstance(layout_metadata, dict):
        return "layout_metadata must be an object"

    template_id = layout_metadata.get("template_id")
    if template_id and template_id not in templates:
        return f"Unknown template_id: {template_id}"

    traversal = layout_metadata.get("traversal_preset")
    if traversal and traversal not in SUPPORTED_TRAVERSALS:
        return f"Unsupported traversal_preset: {traversal}"

    overrides = layout_metadata.get("custom_overrides")
    if overrides is not None and not isinstance(overrides, dict):
        return "custom_overrides must be an object"

    # Validate skip_positions if present in template (by ID or inline)
    template_to_validate = None
    if template_id and template_id in templates:
        template_to_validate = templates[template_id]
    elif isinstance(layout_metadata.get("template"), dict):
        template_to_validate = layout_metadata.get("template")
    
    if template_to_validate:
        skip_positions = template_to_validate.get("skip_positions")
        if skip_positions is not None:
            if not isinstance(skip_positions, list):
                return "skip_positions must be an array"
            # Enforce size limit to prevent DoS (Lesson #5)
            if len(skip_positions) > 100:
                return f"skip_positions array too large (max 100 entries, got {len(skip_positions)})"
            
            rows = int(template_to_validate.get("rows") or 1)
            cols = int(template_to_validate.get("cols") or 1)
            seen_positions = set()
            
            for skip in skip_positions:
                if not isinstance(skip, dict):
                    return "Each skip_positions entry must be an object"
                if "row" not in skip or "col" not in skip:
                    return "Each skip_positions entry must have 'row' and 'col' fields"
                try:
                    row = int(skip["row"])
                    col = int(skip["col"])
                    if row < 0 or row >= rows:
                        return f"skip_positions row {row} out of bounds (0-{rows-1})"
                    if col < 0 or col >= cols:
                        return f"skip_positions col {col} out of bounds (0-{cols-1})"
                    # Check for duplicates
                    pos_key = (row, col)
                    if pos_key in seen_positions:
                        return f"Duplicate skip_positions entry: row {row}, col {col}"
                    seen_positions.add(pos_key)
                except (ValueError, TypeError):
                    return "skip_positions row and col must be integers"

    seen = set()
    for bay_id, conf in (bays or {}).items():
        if not isinstance(conf, dict):
            continue
        number = conf.get("display_number")
        if number is None:
            continue
        key = str(number).strip().lower()
        if not key:
            continue
        if key in seen:
            return f"Duplicate display_number detected: {number}"
        seen.add(key)

    return None


def validate_template(template):
    """Validate a single template object for structure and constraints.
    
    Accepts both old-style templates (with rows/cols/bay_count/traversal_preset)
    and new-style templates (with slot_count only for enclosure use).
    """
    if not isinstance(template, dict):
        return "Template must be an object"
    
    required_fields = ["id", "name"]
    for field in required_fields:
        if field not in template:
            return f"Template missing required field: {field}"
    
    # Must have at least one of slot_count or bay_count
    if "slot_count" not in template and "bay_count" not in template:
        return "Template must have either slot_count or bay_count"
    
    # Validate types
    if not isinstance(template["id"], str) or not template["id"].strip():
        return "Template id must be a non-empty string"
    
    # Validate template ID format (lowercase, numbers, hyphens, underscores only)
    id_pattern = r"^[a-z0-9_-]+$"
    if not re.match(id_pattern, template["id"]):
        return "Template id must contain only lowercase letters, numbers, hyphens, and underscores"
    
    if not isinstance(template["name"], str) or not template["name"].strip():
        return "Template name must be a non-empty string"
    
    # Validate vendor if present
    if "vendor" in template:
        if not isinstance(template["vendor"], str) or not template["vendor"].strip():
            return "Template vendor must be a non-empty string"
    
    # Validate slot_count if present
    if "slot_count" in template:
        try:
            slot_count = int(template["slot_count"])
            if slot_count < 1:
                return "Template slot_count must be a positive integer"
        except (ValueError, TypeError):
            return "Template slot_count must be an integer"
    
    # Grid validation only when rows/cols/bay_count are all present
    has_grid = all(k in template for k in ("rows", "cols", "bay_count"))
    if has_grid:
        try:
            rows = int(template["rows"])
            cols = int(template["cols"])
            bay_count = int(template["bay_count"])
        except (ValueError, TypeError):
            return "Template rows, cols, and bay_count must be integers"
        
        if rows < 1 or cols < 1 or bay_count < 1:
            return "Template rows, cols, and bay_count must be positive integers"
        
        if cols > 5:
            return "Template cols cannot exceed 5 (UI layout constraint)"
        
        if bay_count > rows * cols:
            return f"Template bay_count ({bay_count}) cannot exceed rows * cols ({rows * cols})"
    
    # Validate traversal_preset if present
    if "traversal_preset" in template:
        if template["traversal_preset"] not in SUPPORTED_TRAVERSALS:
            return f"Template traversal_preset must be one of: {', '.join(SUPPORTED_TRAVERSALS)}"
    
    # Validate skip_positions if present (requires grid fields)
    skip_positions = template.get("skip_positions")
    if skip_positions is not None:
        if not isinstance(skip_positions, list):
            return "skip_positions must be an array"
        # Enforce size limit to prevent DoS (Lesson #5)
        if len(skip_positions) > 100:
            return f"skip_positions array too large (max 100 entries, got {len(skip_positions)})"
        
        if not has_grid:
            return "skip_positions requires rows, cols, and bay_count to be defined"
        
        seen_positions = set()
        for skip in skip_positions:
            if not isinstance(skip, dict):
                return "Each skip_positions entry must be an object"
            if "row" not in skip or "col" not in skip:
                return "Each skip_positions entry must have 'row' and 'col' fields"
            try:
                row = int(skip["row"])
                col = int(skip["col"])
                if row < 0 or row >= rows:
                    return f"skip_positions row {row} out of bounds (0-{rows-1})"
                if col < 0 or col >= cols:
                    return f"skip_positions col {col} out of bounds (0-{cols-1})"
                # Check for duplicates
                pos_key = (row, col)
                if pos_key in seen_positions:
                    return f"Duplicate skip_positions entry: row {row}, col {col}"
                seen_positions.add(pos_key)
            except (ValueError, TypeError):
                return "skip_positions row and col must be integers"
    
    # Validate hybrid_slots if present
    hybrid_slots = template.get("hybrid_slots")
    if hybrid_slots is not None:
        if not isinstance(hybrid_slots, list):
            return "hybrid_slots must be an array"
        # Enforce size limit to prevent DoS (Lesson #5)
        if len(hybrid_slots) > 128:
            return f"hybrid_slots array too large (max 128 entries, got {len(hybrid_slots)})"
        
        # If grid fields are present, validate range and duplicates
        if has_grid:
            seen_slots = set()
            for slot in hybrid_slots:
                try:
                    slot_num = int(slot)
                    if slot_num < 1 or slot_num > (rows * cols):
                        return f"hybrid_slots entry {slot_num} out of bounds (1-{rows * cols})"
                    # Check for duplicates
                    if slot_num in seen_slots:
                        return f"Duplicate hybrid_slots entry: {slot_num}"
                    seen_slots.add(slot_num)
                except (ValueError, TypeError):
                    return "hybrid_slots entries must be integers"
        else:
            # If no grid, just validate type and duplicates
            seen_slots = set()
            for slot in hybrid_slots:
                try:
                    slot_num = int(slot)
                    if slot_num < 0:
                        return f"hybrid_slots entry {slot_num} must be non-negative"
                    # Check for duplicates
                    if slot_num in seen_slots:
                        return f"Duplicate hybrid_slots entry: {slot_num}"
                    seen_slots.add(slot_num)
                except (ValueError, TypeError):
                    return "hybrid_slots entries must be integers"
    
    return None


def save_layout_templates(templates, config_dir):
    """
    Save templates to config directory with atomic file operations.
    Uses atomic write pattern to prevent TOCTOU race conditions (Lesson #20).
    Stores SHA256 hash for file integrity validation (Lesson #22).
    """
    os.makedirs(config_dir, exist_ok=True)
    templates_path = os.path.join(config_dir, "layout_templates.json")
    hash_path = os.path.join(config_dir, "layout_templates.json.sha256")
    
    # Validate all templates before saving
    for template_id, template in templates.items():
        error = validate_template(template)
        if error:
            raise ValueError(f"Invalid template '{template_id}': {error}")
    
    # Prepare the data structure
    data = {"templates": templates}
    json_content = json.dumps(data, indent=2)
    
    # Calculate SHA256 hash for integrity validation
    content_hash = hashlib.sha256(json_content.encode('utf-8')).hexdigest()
    
    # Atomic write using temporary file (Lesson #20)
    fd, temp_path = tempfile.mkstemp(dir=config_dir, prefix=".layout_templates_tmp_", suffix=".json")
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(json_content)
            f.flush()
            os.fsync(f.fileno())
        
        # Atomic rename on POSIX systems
        os.replace(temp_path, templates_path)
        
        # Also save the hash atomically for integrity validation
        hash_fd, hash_temp_path = tempfile.mkstemp(dir=config_dir, prefix=".layout_templates_hash_tmp_", suffix=".sha256")
        try:
            with os.fdopen(hash_fd, 'w', encoding='utf-8') as f:
                f.write(content_hash)
                f.flush()
                os.fsync(f.fileno())
            os.replace(hash_temp_path, hash_path)
        except Exception:
            try:
                os.unlink(hash_temp_path)
            except Exception:
                pass
            raise
        
    except Exception:
        # Clean up temp file if write failed
        try:
            os.unlink(temp_path)
        except Exception:
            pass
        raise
