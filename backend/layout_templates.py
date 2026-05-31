import json
import os

SUPPORTED_TRAVERSALS = {
    "top_left_down_then_across",
    "bottom_left_up_then_across"
}

DEFAULT_TEMPLATES = {
    "dell_r320_4bay": {
        "id": "dell_r320_4bay",
        "name": "Dell R320 4-Bay (3.5\")",
        "vendor": "Dell",
        "rows": 1,
        "cols": 4,
        "bay_count": 4,
        "traversal_preset": "top_left_down_then_across"
    },
    "dell_r440_10bay": {
        "id": "dell_r440_10bay",
        "name": "Dell R440 10-Bay (2.5\")",
        "vendor": "Dell",
        "rows": 5,
        "cols": 2,
        "bay_count": 10,
        "traversal_preset": "top_left_down_then_across"
    },
    "dell_2u_8bay": {
        "id": "dell_2u_8bay",
        "name": "Dell 2U 8-Bay",
        "vendor": "Dell",
        "rows": 4,
        "cols": 2,
        "bay_count": 8,
        "traversal_preset": "top_left_down_then_across"
    },
    "supermicro_2u_8bay": {
        "id": "supermicro_2u_8bay",
        "name": "Supermicro 2U 8-Bay",
        "vendor": "Supermicro",
        "rows": 4,
        "cols": 2,
        "bay_count": 8,
        "traversal_preset": "bottom_left_up_then_across"
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


def compose_bay_map_document(bays, metadata):
    clean_bays = {k: v for k, v in (bays or {}).items() if is_bay_entry(v)}
    clean_meta = metadata if isinstance(metadata, dict) else {}
    if clean_meta:
        return {
            "layout_metadata": clean_meta,
            "bays": clean_bays
        }
    return clean_bays


def load_layout_templates(config_dir):
    path = os.path.join(config_dir, "layout_templates.json")
    if not os.path.exists(path):
        return DEFAULT_TEMPLATES
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if isinstance(payload, dict) and isinstance(payload.get("templates"), dict):
            templates = payload["templates"]
            result = {}
            for template_id, template in templates.items():
                if isinstance(template, dict):
                    entry = template.copy()
                    entry["id"] = template_id
                    result[template_id] = entry
            return result or DEFAULT_TEMPLATES
    except Exception:
        pass
    return DEFAULT_TEMPLATES


def build_traversal_positions(rows, cols, traversal, bay_count):
    positions = []
    rows = max(1, int(rows or 1))
    cols = max(1, int(cols or 1))
    bay_count = max(1, int(bay_count or (rows * cols)))

    if traversal == "bottom_left_up_then_across":
        for col in range(cols):
            for row in range(rows - 1, -1, -1):
                positions.append((row, col))
                if len(positions) >= bay_count:
                    return positions
    else:
        for col in range(cols):
            for row in range(rows):
                positions.append((row, col))
                if len(positions) >= bay_count:
                    return positions
    return positions


def apply_template(existing_bays, template, traversal_preset=None, custom_overrides=None):
    rows = int(template.get("rows") or 1)
    cols = int(template.get("cols") or 1)
    bay_count = int(template.get("bay_count") or (rows * cols))
    traversal = traversal_preset or template.get("traversal_preset") or "top_left_down_then_across"
    if traversal not in SUPPORTED_TRAVERSALS:
        traversal = "top_left_down_then_across"

    positions = build_traversal_positions(rows, cols, traversal, bay_count)
    overrides = custom_overrides if isinstance(custom_overrides, dict) else {}

    result = {}
    for index, (row, col) in enumerate(positions, start=1):
        bay_id = f"bay{index}"
        prior = existing_bays.get(bay_id, {}) if isinstance(existing_bays, dict) else {}
        display_number = str(index)
        override_value = overrides.get(bay_id)
        if isinstance(override_value, dict):
            override_value = override_value.get("display_number") or override_value.get("numbering_override")
        if override_value is not None and str(override_value).strip() != "":
            display_number = str(override_value).strip()

        label = prior.get("label") or f"Work Bay {index}"
        
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
