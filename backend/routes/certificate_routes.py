# Certificate-related routes
import os
import json
import sqlite3
import io
import hmac
from datetime import datetime
from threading import Thread
from flask import Blueprint, jsonify, request, send_file
from app_config import ERASE_JOBS, ERASE_JOBS_LOCK, logger, calculate_session_token, limiter
from common import get_config_dir, load_policy, get_db_path, get_cert_dir
from bulk_cert import create_bulk_cert_job, run_bulk_cert_job
from certificates import build_bulk_certificate_html
from routes.admin_routes import require_admin_auth

certificate_bp = Blueprint('certificate_routes', __name__)

def _validate_file_path(file_path, allowed_dir):
    """Validate that file_path is within allowed_dir to prevent path traversal."""
    if not file_path:
        return None, "File path is empty"
    
    # Resolve to absolute paths
    abs_file_path = os.path.abspath(file_path)
    abs_allowed_dir = os.path.abspath(allowed_dir)
    
    # Check if the resolved file path is within the allowed directory
    if not os.path.commonprefix([abs_file_path, abs_allowed_dir]) == abs_allowed_dir:
        return None, "File path is outside allowed directory"
    
    return abs_file_path, None

def _serve_certificate_file(file_path, filename, error_context):
    """Helper function to serve certificate files with proper error handling."""
    try:
        return send_file(
            file_path,
            mimetype="text/html",
            as_attachment=True,
            download_name=filename or os.path.basename(file_path),
        )
    except FileNotFoundError:
        return jsonify({"error": f"{error_context} file not found"}), 404
    except Exception as e:
        logger.error(f"Failed to serve certificate file: {str(e)}")
        return jsonify({"error": f"Failed to serve file: {str(e)}"}), 500

@certificate_bp.route("/api/certificates/<job_id>", methods=["GET"])
@require_admin_auth
@limiter.limit("30 per minute")
def get_certificate(job_id):
    certificate = None
    with ERASE_JOBS_LOCK:
        job = ERASE_JOBS.get(job_id)
        if job and job.get("certificate"):
            certificate = job.get("certificate")

    if not certificate:
        with sqlite3.connect(get_db_path(), timeout=30.0) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT certificate_json FROM erase_jobs WHERE id = ? OR friendly_id = ?",
                (job_id, job_id),
            ).fetchone()

        if not row:
            return jsonify({"error": f"job not found: {job_id}"}), 404

        certificate = json.loads(row["certificate_json"] or "{}")
        if not certificate:
            return jsonify({"error": f"certificate not found for job: {job_id}"}), 404

    response_format = str(request.args.get("format", "json")).strip().lower()
    is_bulk = str(request.args.get("bulk", "false")).strip().lower() == "true"
    
    # Authentication check for bulk certificate downloads
    if is_bulk:
        policy = load_policy()
        lan_passphrase = policy.get("lan_passphrase", "eraser123")
        session_token = request.cookies.get("admin_session")
        if not session_token or not hmac.compare_digest(session_token, calculate_session_token(lan_passphrase)):
            return jsonify({"error": "Authentication required for bulk certificate downloads"}), 401
    
    if response_format == "json":
        return jsonify(certificate), 200

    if response_format == "html":
        cert_dir = get_cert_dir()
        
        # Handle bulk certificate files
        if is_bulk:
            bulk_path = certificate.get("bulk_html_path")
            bulk_filename = certificate.get("bulk_html_filename")
            
            # Validate path is within cert directory
            validated_path, error = _validate_file_path(bulk_path, cert_dir)
            if error:
                return jsonify({"error": f"bulk certificate path validation failed: {error}"}), 403
            
            return _serve_certificate_file(validated_path, bulk_filename, "bulk certificate")
        
        # Handle regular certificate files
        formats = certificate.get("formats") or {}
        html_meta = formats.get("html") or {}
        html_path = html_meta.get("path")
        
        # Validate path is within cert directory
        validated_path, error = _validate_file_path(html_path, cert_dir)
        if error:
            return jsonify({"error": f"certificate path validation failed: {error}"}), 403
        
        return _serve_certificate_file(validated_path, html_meta.get("filename"), "certificate")

    return jsonify({"error": "format must be one of: json, html"}), 400

@certificate_bp.route("/api/certificates/bulk-html", methods=["POST"])
@require_admin_auth
@limiter.limit("30 per minute")
def get_bulk_certificates_html():
    try:
        payload = request.get_json(silent=True) or {}
        
        # Validate input
        if "job_ids" in payload and not isinstance(payload["job_ids"], list):
            return jsonify({"error": "job_ids must be a list"}), 400
        
        if "job_ids" in payload and payload["job_ids"]:
            if not all(isinstance(job_id, str) for job_id in payload["job_ids"]):
                return jsonify({"error": "all job_ids must be strings"}), 400
            # DoS prevention: limit number of job_ids to prevent memory exhaustion
            if len(payload["job_ids"]) > 100:
                return jsonify({"error": "job_ids list cannot exceed 100 items"}), 400
        
        if "ticket_number" in payload and payload["ticket_number"]:
            if not isinstance(payload["ticket_number"], str):
                return jsonify({"error": "ticket_number must be a string"}), 400
            ticket_number = payload["ticket_number"].strip()
            if not ticket_number:
                return jsonify({"error": "ticket_number cannot be empty or whitespace"}), 400
            payload["ticket_number"] = ticket_number
        
        # Validate date formats if provided
        for date_field in ["start_date", "end_date"]:
            if date_field in payload and payload[date_field]:
                try:
                    datetime.fromisoformat(payload[date_field])
                except ValueError:
                    return jsonify({"error": f"{date_field} must be a valid ISO 8601 datetime string"}), 400
        
        # Validate date range logical consistency
        if "start_date" in payload and payload["start_date"] and "end_date" in payload and payload["end_date"]:
            try:
                start_dt = datetime.fromisoformat(payload["start_date"])
                end_dt = datetime.fromisoformat(payload["end_date"])
                if start_dt > end_dt:
                    return jsonify({"error": "start_date must be before or equal to end_date"}), 400
            except ValueError:
                # Already validated above, but handle edge case
                pass
        
        # Build query based on filters
        with sqlite3.connect(get_db_path(), timeout=30.0) as conn:
            conn.row_factory = sqlite3.Row
            
            query = "SELECT certificate_json FROM erase_jobs WHERE certificate_json IS NOT NULL"
            params = []
            
            # Filter by job IDs (manual selection)
            # Search both id and friendly_id columns since users may provide either identifier type
            if "job_ids" in payload and payload["job_ids"]:
                placeholders = ",".join(["?" for _ in payload["job_ids"]])
                query += f" AND (id IN ({placeholders}) OR friendly_id IN ({placeholders}))"
                params.extend(payload["job_ids"])
                params.extend(payload["job_ids"])
            
            # Filter by ticket number
            elif "ticket_number" in payload and payload["ticket_number"]:
                query += " AND json_extract(request_json, '$.ticket_number') = ?"
                params.append(payload["ticket_number"])
            
            # Filter by date range and status
            else:
                if "start_date" in payload and payload["start_date"]:
                    query += " AND finished_at >= ?"
                    params.append(payload["start_date"])
                if "end_date" in payload and payload["end_date"]:
                    query += " AND finished_at <= ?"
                    params.append(payload["end_date"])
                if "status" in payload and payload["status"]:
                    # Use json_extract for safe JSON field filtering
                    query += " AND json_extract(verification_json, '$.status') = ?"
                    params.append(payload["status"])
            
            query += " ORDER BY finished_at DESC LIMIT 500"
            
            rows = conn.execute(query, params).fetchall()
        
        certificates = []
        for row in rows:
            try:
                cert = json.loads(row["certificate_json"] or "{}")
                if cert:
                    certificates.append(cert)
            except json.JSONDecodeError as e:
                logger.warning(f"Skipping malformed certificate JSON for row: {e}")
                continue
        
        if not certificates:
            return jsonify({"certificates": [], "count": 0, "message": "No certificates found matching the specified criteria"}), 200
        
        # Warn if results were truncated by the limit
        message = None
        if len(certificates) == 500:
            message = "Results limited to 500 certificates. Use date range or ticket number filters to narrow results."
        
        # Generate bulk HTML
        bulk_html = build_bulk_certificate_html(certificates)
        
        # Return as downloadable HTML file
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        response = send_file(
            io.BytesIO(bulk_html.encode('utf-8')),
            mimetype="text/html",
            as_attachment=True,
            download_name=f"bulk-certificates-{timestamp}.html"
        )
        if message:
            response.headers['X-Warning'] = message
        return response
    except Exception as e:
        logger.error(f"Bulk certificate generation failed: {str(e)}", exc_info=True)
        return jsonify({"error": f"Failed to generate bulk certificates: {str(e)}"}), 500

@certificate_bp.route("/api/admin/bulk-cert/create", methods=["POST"])
@require_admin_auth
@limiter.limit("10 per minute")
def create_bulk_cert():
    try:
        payload = request.get_json(silent=True) or {}
        
        # Validate job_ids is present and is a list
        if "job_ids" not in payload:
            return jsonify({"error": "job_ids is required"}), 400
        
        job_ids = payload.get("job_ids")
        if not isinstance(job_ids, list):
            return jsonify({"error": "job_ids must be a list"}), 400
        
        # create_bulk_cert_job handles validation including:
        # - Max 100 items (DoS prevention)
        # - Non-empty list
        # - Duplicate detection
        # - Job existence and completion validation
        job, error_body, status_code = create_bulk_cert_job(job_ids)
        
        if error_body:
            return jsonify(error_body), status_code
        
        # Start the bulk cert job in a background thread
        worker = Thread(target=run_bulk_cert_job, args=(job["id"],), daemon=True)
        worker.start()
        
        logger.info(f"Bulk certificate job created: {job['id']} for {len(job_ids)} jobs")
        
        return jsonify({
            "status": "accepted",
            "message": "bulk certificate job started",
            "job_id": job["id"],
            "target_job_count": len(job_ids)
        }), 202
    except Exception as e:
        logger.error(f"Bulk certificate job creation failed: {str(e)}", exc_info=True)
        return jsonify({"error": f"Failed to create bulk certificate job: {str(e)}"}), 500
