# Template-related routes
import os
import json
import io
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request, send_file
from app_config import logger, limiter
from common import get_config_dir
from layout_templates import (
    load_layout_templates,
    normalize_bay_map_document,
    compose_bay_map_document,
    apply_template,
    validate_template,
    save_layout_templates,
    SUPPORTED_TRAVERSALS,
    TEMPLATES_LOCK,
    validate_layout_metadata
)
from routes._shared import require_admin_auth, is_valid_id

template_bp = Blueprint('template_routes', __name__)

@template_bp.route("/api/admin/layout-templates", methods=["GET", "POST", "PUT", "DELETE"])
@require_admin_auth
@limiter.limit("30 per minute")
def layout_templates_crud():
    try:
        config_dir = get_config_dir()
        
        if request.method == "GET":
            templates, is_fallback = load_layout_templates(config_dir)
            return jsonify({
                "templates": list(templates.values()),
                "supported_traversals": sorted(list(SUPPORTED_TRAVERSALS)),
                "source": "fallback" if is_fallback else "file"
            }), 200
        
        elif request.method == "POST":
            payload = request.get_json(silent=True) or {}
            if not isinstance(payload, dict):
                return jsonify({"error": "Request body must be a JSON object"}), 400
            
            template_id = str(payload.get("id") or "").strip()
            if not template_id:
                return jsonify({"error": "Template id is required"}), 400
            
            # Use lock to prevent race condition
            with TEMPLATES_LOCK:
                # Load existing templates
                templates, _ = load_layout_templates(config_dir)
                
                # Check if template already exists
                if template_id in templates:
                    return jsonify({"error": f"Template with id '{template_id}' already exists. Use PUT to update."}), 409
                
                # Validate the new template
                error = validate_template(payload)
                if error:
                    return jsonify({"error": error}), 400
                
                # Add the template
                templates[template_id] = payload
                
                # Save with atomic operations
                save_layout_templates(templates, config_dir)
            
            logger.info(f"Created new layout template: {template_id}")
            return jsonify({"status": "created", "template": payload}), 201
        
        elif request.method == "PUT":
            payload = request.get_json(silent=True) or {}
            if not isinstance(payload, dict):
                return jsonify({"error": "Request body must be a JSON object"}), 400
            
            template_id = str(payload.get("id") or "").strip()
            if not template_id:
                return jsonify({"error": "Template id is required"}), 400
            
            # Use lock to prevent race condition
            with TEMPLATES_LOCK:
                # Load existing templates
                templates, _ = load_layout_templates(config_dir)
                
                # Check if template exists
                if template_id not in templates:
                    return jsonify({"error": f"Template with id '{template_id}' not found. Use POST to create."}), 404
                
                # Validate the updated template
                error = validate_template(payload)
                if error:
                    return jsonify({"error": error}), 400
                
                # Update the template
                templates[template_id] = payload
                
                # Save with atomic operations
                save_layout_templates(templates, config_dir)
            
            logger.info(f"Updated layout template: {template_id}")
            return jsonify({"status": "updated", "template": payload}), 200
        
        elif request.method == "DELETE":
            payload = request.get_json(silent=True) or {}
            if not isinstance(payload, dict):
                return jsonify({"error": "Request body must be a JSON object"}), 400
            
            template_id = str(payload.get("id") or "").strip()
            if not template_id:
                return jsonify({"error": "Template id is required in request body"}), 400
            
            # Use lock to prevent race condition
            with TEMPLATES_LOCK:
                # Load existing templates
                templates, _ = load_layout_templates(config_dir)
                
                # Check if template exists
                if template_id not in templates:
                    return jsonify({"error": f"Template with id '{template_id}' not found"}), 404
                
                # Remove the template
                del templates[template_id]
                
                # Save with atomic operations
                save_layout_templates(templates, config_dir)
            
            logger.info(f"Deleted layout template: {template_id}")
            return jsonify({"status": "deleted", "template_id": template_id}), 200
        
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Template CRUD failed: {e}")
        return jsonify({"error": str(e)}), 500

@template_bp.route("/api/admin/layout-templates/export", methods=["GET"])
@require_admin_auth
@limiter.limit("10 per minute")
def layout_templates_export():
    """Export all layout templates as a JSON file download."""
    try:
        config_dir = get_config_dir()
        templates, _ = load_layout_templates(config_dir)
        
        # Prepare export data
        export_data = {
            "templates": templates,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "version": "1.0"
        }
        
        # Create JSON content
        json_content = json.dumps(export_data, indent=2)
        
        # Create file-like object for send_file
        buffer = io.BytesIO(json_content.encode('utf-8'))
        buffer.seek(0)
        
        logger.info("Exported layout templates")
        return send_file(
            buffer,
            mimetype='application/json',
            as_attachment=True,
            download_name=f'layout_templates_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
        ), 200
        
    except Exception as e:
        logger.error(f"Failed to export layout templates: {e}")
        return jsonify({"error": str(e)}), 500

@template_bp.route("/api/admin/layout-templates/import", methods=["POST"])
@require_admin_auth
@limiter.limit("10 per minute")
def layout_templates_import():
    """Import layout templates from a JSON file upload."""
    try:
        # Check for file upload
        if 'file' not in request.files:
            return jsonify({"error": "No file provided"}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({"error": "No file selected"}), 400
        
        # Validate file size
        # Limit to 64KB to prevent DoS attacks
        MAX_FILE_SIZE = 64 * 1024  # 64KB
        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)
        
        if file_size > MAX_FILE_SIZE:
            return jsonify({"error": f"File too large. Maximum size is {MAX_FILE_SIZE // 1024}KB"}), 400
        
        # Read and parse JSON
        try:
            content = file.read().decode('utf-8')
            import_data = json.loads(content)
        except json.JSONDecodeError as e:
            return jsonify({"error": f"Invalid JSON file: {str(e)}"}), 400
        except UnicodeDecodeError:
            return jsonify({"error": "File must be UTF-8 encoded"}), 400
        
        # Validate structure
        if not isinstance(import_data, dict):
            return jsonify({"error": "Invalid file structure: must be a JSON object"}), 400
        
        if "templates" not in import_data:
            return jsonify({"error": "Missing 'templates' key in import file"}), 400
        
        imported_templates = import_data["templates"]
        if not isinstance(imported_templates, dict):
            return jsonify({"error": "'templates' must be a JSON object"}), 400
        
        # Validate template count (DoS prevention)
        MAX_TEMPLATES = 100
        if len(imported_templates) > MAX_TEMPLATES:
            return jsonify({"error": f"Too many templates in import file. Maximum is {MAX_TEMPLATES}"}), 400

        # Validate each template
        validation_errors = []
        for template_id, template in imported_templates.items():
            if not is_valid_id(str(template_id)):
                validation_errors.append(f"Template ID '{template_id}' contains invalid characters")
                continue
            if not isinstance(template, dict):
                validation_errors.append(f"Template '{template_id}' is not a valid object")
                continue
            
            error = validate_template(template)
            if error:
                validation_errors.append(f"Template '{template_id}': {error}")
        
        if validation_errors:
            return jsonify({"error": "Template validation failed", "details": validation_errors}), 400
        
        # Use lock to prevent race condition
        merged_templates = None
        overwritten_templates = []
        with TEMPLATES_LOCK:
            config_dir = get_config_dir()
            existing_templates, _ = load_layout_templates(config_dir)
            
            # Track which templates will be overwritten
            for template_id in imported_templates.keys():
                if template_id in existing_templates:
                    overwritten_templates.append(template_id)
            
            # Merge templates (imported templates override existing ones)
            merged_templates = {**existing_templates, **imported_templates}
            
            # Save with atomic operations
            save_layout_templates(merged_templates, config_dir)
        
        imported_count = len(imported_templates)
        total_count = len(merged_templates) if merged_templates else 0
        
        # Enhanced audit logging
        imported_ids = list(imported_templates.keys())
        logger.info(
            f"Imported {imported_count} layout templates. "
            f"IDs: {imported_ids}. "
            f"Overwritten: {overwritten_templates if overwritten_templates else 'None'}. "
            f"Total templates after import: {total_count}"
        )
        
        return jsonify({
            "status": "imported",
            "imported_count": imported_count,
            "imported_ids": imported_ids,
            "overwritten_count": len(overwritten_templates),
            "overwritten_ids": overwritten_templates,
            "total_templates": total_count
        }), 200
        
    except Exception as e:
        logger.error(f"Failed to import layout templates: {e}")
        return jsonify({"error": str(e)}), 500

@template_bp.route("/api/admin/apply-template", methods=["POST"])
@require_admin_auth
@limiter.limit("30 per minute")
def admin_apply_template():
    try:
        payload = request.get_json(silent=True) or {}
        template_id = str(payload.get("template_id") or "").strip()
        traversal_preset = str(payload.get("traversal_preset") or "").strip() or None
        custom_overrides = payload.get("custom_overrides") or {}

        if not template_id:
            return jsonify({"error": "template_id is required"}), 400

        config_dir = get_config_dir()
        templates, _ = load_layout_templates(config_dir)
        template = templates.get(template_id)
        if not template:
            return jsonify({"error": f"Unknown template_id: {template_id}"}), 400

        bay_map_path = os.path.join(config_dir, "bay_map.json")
        try:
            with open(bay_map_path, "r", encoding="utf-8") as f:
                existing_doc = json.load(f)
        except Exception:
            existing_doc = {}

        existing_bays, _ = normalize_bay_map_document(existing_doc)
        generated_bays, resolved_traversal = apply_template(existing_bays, template, traversal_preset, custom_overrides)

        metadata = {
            "template_id": template_id,
            "traversal_preset": resolved_traversal,
            "custom_overrides": custom_overrides
        }

        validation_error = validate_layout_metadata(metadata, generated_bays, templates)
        if validation_error:
            return jsonify({"error": validation_error}), 400

        return jsonify({
            "status": "success",
            "template": template,
            "layout_metadata": metadata,
            "bay_map": compose_bay_map_document(generated_bays, metadata)
        }), 200
    except Exception as e:
        logger.error(f"Apply template failed: {e}")
        return jsonify({"error": str(e)}), 500
