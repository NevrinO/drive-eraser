# --- START OF FILE backend/api_routes.py ---
# This file contains only routes NOT extracted into blueprints:
# - Static file serving (/, /<path:path>, /docs/<path:path>)
# - Erase job routes (/api/erase/*)
# - Auth verification (/api/auth/verify)
import os
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from threading import Thread
from flask import request, jsonify, send_from_directory

from app_config import app, ERASE_JOBS, ERASE_JOBS_LOCK, FRONTEND_DIR, PROJECT_ROOT, logger, calculate_session_token, limiter
from routes.admin_routes import require_admin_auth
from job_management import validate_single_bay, create_erase_job, run_erase_job
from common import get_config_dir, load_policy, get_db_path
from database import persist_job
from disk_ops import discover_drives

def register_routes(flask_app):
    """Register all api_routes on the given Flask app instance.
    
    This function is used in tests to register routes on a test app instance
    instead of the global app from app_config.py.
    """
    # Home route
    @flask_app.route("/")
    @limiter.exempt
    def home():
        index_path = os.path.join(FRONTEND_DIR, "index.html")
        if os.path.exists(index_path):
            return send_from_directory(FRONTEND_DIR, "index.html")
        return "<h1>Drive Wipe Station API</h1><p>Status: Online</p>"

    @flask_app.route("/<path:path>")
    @limiter.exempt
    def frontend_assets(path):
        if path.startswith("api/"):
            return jsonify({"error": "not found"}), 404
        asset_path = os.path.join(FRONTEND_DIR, path)
        if os.path.exists(asset_path) and os.path.isfile(asset_path):
            return send_from_directory(FRONTEND_DIR, path)
        return jsonify({"error": "not found"}), 404

    @flask_app.route("/docs/<path:path>")
    @limiter.exempt
    def serve_docs(path):
        docs_dir = os.path.join(PROJECT_ROOT, "docs")
        doc_path = os.path.join(docs_dir, path)
        if os.path.exists(doc_path) and os.path.isfile(doc_path):
            return send_from_directory(docs_dir, path)
        return jsonify({"error": "documentation not found"}), 404

    @flask_app.route("/api/erase/start", methods=["POST"])
    @require_admin_auth
    @limiter.limit("20 per minute")
    def start_erase():
        try:
            payload = request.get_json(silent=True) or {}
            config_dir = get_config_dir()
            policy = load_policy(config_dir)
            
            strict_audit = policy.get("strict_audit_mode", False)
            passphrase = policy.get("wipe_passphrase")
            if strict_audit and not passphrase:
                return jsonify({"error": "Configuration Error: strict_audit_mode is enabled, but no wipe_passphrase is configured in policy.json."}), 400

            running_devices = set()
            with ERASE_JOBS_LOCK:
                for job in ERASE_JOBS.values():
                    if job.get("status") in {"running", "queued"}:
                        dev = job.get("request", {}).get("device")
                        if dev:
                            running_devices.add(dev)

            drives = discover_drives(os.path.join(config_dir, "bay_map.json"), running_devices=running_devices)

            technician = str(payload.get("technician") or "").strip()
            ticket_number = str(payload.get("ticket_number") or "").strip()

            # Only set defaults in unsecured mode (strict_audit_mode disabled)
            if not strict_audit:
                if not technician:
                    technician = "System Operator"
                if not ticket_number:
                    ticket_number = "INTERNAL"

            confirmation_text = str(payload.get("confirmation_text") or "").strip().lower()
            
            bays = payload.get("bays")
            if not bays and payload.get("bay"):
                bays = [payload.get("bay")]
            
            methods_map = payload.get("methods") or {}
            if not methods_map and payload.get("method"):
                methods_map = {bays[0]: payload.get("method")} if bays else {}

            if not bays or not isinstance(bays, list):
                return jsonify({"error": "bays list is required"}), 400

            expected_confirmation = f"erase {bays[0]}" if len(bays) == 1 else f"erase {len(bays)} drives"
            if confirmation_text != expected_confirmation:
                return jsonify({"error": f"confirmation_text must exactly be '{expected_confirmation}'"}), 400

            validated_bays = []
            for bay in bays:
                bay_val = str(bay).strip().lower()
                method_override = methods_map.get(bay_val)
                validated, error_body, status_code = validate_single_bay(
                    technician, ticket_number, bay_val, method_override, drives, policy
                )
                if error_body:
                    return jsonify(error_body), status_code
                validated_bays.append(validated)

            # Extract wipe options from payload
            disable_marker = payload.get("disable_marker", False)
            full_verification = payload.get("full_verification", False)

            accepted_jobs = []
            for validated in validated_bays:
                job = create_erase_job(validated)
                # Store wipe options in job request for use during execution
                job["request"]["disable_marker"] = disable_marker
                job["request"]["full_verification"] = full_verification
                with ERASE_JOBS_LOCK:
                    ERASE_JOBS[job["id"]] = job
                persist_job(job)

                worker = Thread(target=run_erase_job, args=(job["id"],), daemon=True)
                worker.start()

                accepted_jobs.append({
                    "id": job["id"],
                    "friendly_id": job["friendly_id"],
                    "status": job["status"],
                    "created_at": job["created_at"],
                    **job["request"],
                })

            # High-signal audit trail log entry
            logger.info(f"Erase request accepted for bays: {bays}. Technician: '{technician}', Ticket: '{ticket_number}'. Created {len(accepted_jobs)} job(s).")

            return jsonify({
                "status": "accepted",
                "message": f"started {len(accepted_jobs)} concurrent wipe process(es)",
                "jobs": accepted_jobs
            }), 202
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @flask_app.route("/api/erase/jobs/<job_id>", methods=["GET"])
    def get_erase_job(job_id):
        with ERASE_JOBS_LOCK:
            job = ERASE_JOBS.get(job_id)
            if job:
                return jsonify(job), 200

        with closing(sqlite3.connect(get_db_path(), timeout=30.0)) as conn, conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT job_number, id, friendly_id, status, created_at, started_at, finished_at, error,
                       request_json, result_json, verification_json, marker_json, certificate_json, job_type
                FROM erase_jobs WHERE id = ? OR friendly_id = ?
                """,
                (job_id, job_id),
            ).fetchone()

        if not row:
            return jsonify({"error": f"job not found: {job_id}"}), 404

        return jsonify({
            "id": row["id"],
            "friendly_id": row["friendly_id"],
            "status": row["status"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "error": row["error"],
            "request": json.loads(row["request_json"] or "{}"),
            "result": json.loads(row["result_json"] or "{}"),
            "verification": json.loads(row["verification_json"] or "{}"),
            "marker": json.loads(row["marker_json"] or "{}"),
            "certificate": json.loads(row["certificate_json"] or "{}"),
            "job_type": row["job_type"],
        }), 200

    @flask_app.route("/api/erase/jobs/<job_id>/cancel", methods=["POST"])
    def cancel_erase_job(job_id):
        try:
            with ERASE_JOBS_LOCK:
                job = ERASE_JOBS.get(job_id)
                if job and job.get("status") in {"running", "queued"}:
                    job["status"] = "failed"
                    job["finished_at"] = datetime.now(timezone.utc).isoformat()
                    job["error"] = "Job cancelled by user"
                    persist_job(job)
                    ERASE_JOBS.pop(job_id, None)
                    return jsonify({"status": "cancelled", "job_id": job_id}), 200
            return jsonify({"error": f"job not found or not cancellable: {job_id}"}), 404
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @flask_app.route("/api/erase/history", methods=["GET"])
    def get_erase_history():
        limit_raw = request.args.get("limit", "100")
        query_str = request.args.get("query", "").strip().lower()
        
        try:
            limit = int(limit_raw)
        except ValueError:
            return jsonify({"error": "limit must be an integer"}), 400
        if limit < 1 or limit > 500:
            return jsonify({"error": "limit must be between 1 and 500"}), 400

        try:
            with closing(sqlite3.connect(get_db_path(), timeout=30.0)) as conn, conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT id, friendly_id, status, created_at, started_at, finished_at, error,
                           request_json, result_json, verification_json, marker_json, certificate_json, job_type
                    FROM erase_jobs
                    ORDER BY job_number DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()

            jobs = []
            for row in rows:
                req = json.loads(row["request_json"] or "{}")
                res = json.loads(row["result_json"] or "{}")
                ver = json.loads(row["verification_json"] or "{}")
                
                if query_str:
                    match_pool = [
                        str(row["id"]),
                        str(row["friendly_id"]),
                        str(row["status"]),
                        str(row["error"]),
                        str(req.get("technician")),
                        str(req.get("ticket_number")),
                        str(req.get("bay")),
                        str(req.get("serial")),
                        str(req.get("model")),
                        str(ver.get("status")),
                    ]
                    if not any(query_str in item.lower() for item in match_pool if item):
                        continue

                jobs.append({
                    "id": row["id"],
                    "friendly_id": row["friendly_id"],
                    "status": row["status"],
                    "created_at": row["created_at"],
                    "started_at": row["started_at"],
                    "finished_at": row["finished_at"],
                    "error": row["error"],
                    "request": req,
                    "result": res,
                    "verification": ver,
                    "marker": json.loads(row["marker_json"] or "{}"),
                    "certificate": json.loads(row["certificate_json"] or "{}"),
                    "job_type": row["job_type"],
                })

            return jsonify({"jobs": jobs, "count": len(jobs)}), 200
        except Exception as e:
            return jsonify({"error": f"Database query failed: {str(e)}"}), 500

    @flask_app.route("/api/auth/verify", methods=["POST"])
    @require_admin_auth
    @limiter.limit("5 per minute")
    def verify_auth():
        try:
            payload = request.get_json(silent=True) or {}
            passphrase = payload.get("passphrase", "")
            policy = load_policy()
            lan_passphrase = policy.get("lan_passphrase", "eraser123")
            
            if passphrase == lan_passphrase:
                token = calculate_session_token(lan_passphrase)
                response = jsonify({"status": "authenticated"})
                response.set_cookie("admin_session", token, httponly=True, samesite="Lax", max_age=86400 * 30)
                return response, 200
            return jsonify({"error": "Invalid passphrase"}), 401
        except Exception as e:
            return jsonify({"error": str(e)}), 500

# Register routes on the global app (for production)
register_routes(app)

# --- END OF FILE backend/api_routes.py ---
