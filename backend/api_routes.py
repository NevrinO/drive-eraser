# --- START OF FILE backend/api_routes.py ---
import os
import json
import sqlite3
import re
import subprocess
import shutil
import urllib.request
import io
import csv
import tarfile
import socket
from datetime import datetime, timezone
from threading import Thread
from flask import request, jsonify, send_from_directory, send_file, g

from app_config import app, ERASE_JOBS, ERASE_JOBS_LOCK, FRONTEND_DIR, PROJECT_ROOT, logger, get_local_ip, calculate_session_token
from system_metrics import get_ram_usage, get_cpu_usage, get_system_uptime
from job_management import validate_single_bay, create_erase_job, run_erase_job
from common import (
    get_config_dir, load_policy, get_data_dir, get_db_path, get_logs_dir, get_failed_logs_dir,
    save_policy, save_bay_map
)
from database import init_wipe_db, persist_job
from disk_ops import discover_drives, get_os_by_path
from disk_utils import format_capacity_bytes
from smart_parsing import get_smart_data
from layout_templates import (
    load_layout_templates,
    normalize_bay_map_document,
    compose_bay_map_document,
    apply_template,
    validate_layout_metadata,
    SUPPORTED_TRAVERSALS
)

@app.route("/")
def home():
    index_path = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index_path):
        return send_from_directory(FRONTEND_DIR, "index.html")
    return "<h1>Drive Wipe Station API</h1><p>Status: Online</p>"

@app.route("/<path:path>")
def frontend_assets(path):
    if path.startswith("api/"):
        return jsonify({"error": "not found"}), 404
    asset_path = os.path.join(FRONTEND_DIR, path)
    if os.path.exists(asset_path) and os.path.isfile(asset_path):
        return send_from_directory(FRONTEND_DIR, path)
    return jsonify({"error": "not found"}), 404

@app.route("/docs/<path:path>")
def serve_docs(path):
    docs_dir = os.path.join(PROJECT_ROOT, "docs")
    doc_path = os.path.join(docs_dir, path)
    if os.path.exists(doc_path) and os.path.isfile(doc_path):
        return send_from_directory(docs_dir, path)
    return jsonify({"error": "documentation not found"}), 404

@app.route("/api/drives")
def get_drives():
    try:
        config_dir = get_config_dir()
        
        running_devices = set()
        with ERASE_JOBS_LOCK:
            for job in ERASE_JOBS.values():
                if job.get("status") in {"running", "queued"}:
                    dev = job.get("request", {}).get("device")
                    if dev:
                        running_devices.add(dev)

        drives = discover_drives(os.path.join(config_dir, "bay_map.json"), running_devices=running_devices)
        
        with ERASE_JOBS_LOCK:
            for d in drives:
                bay_name = d.get("bay")
                for job_id, job in ERASE_JOBS.items():
                    req = job.get("request") or {}
                    if str(req.get("bay")).lower() == str(bay_name).lower():
                        if job.get("status") in {"running", "queued"}:
                            d["status"] = job["status"].upper()
                            d["progress_percent"] = job.get("progress_percent", 0.0)
                            d["current_phase"] = job.get("current_phase", "Sanitizing")
                            
                            if req.get("serial"):
                                d["serial"] = req.get("serial")
                            if req.get("model"):
                                d["model"] = req.get("model")
                            if req.get("capacity_bytes"):
                                d["capacity_str"] = format_capacity_bytes(req.get("capacity_bytes"))
                            break
        return jsonify(drives)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/status")
def get_status():
    try:
        config_dir = get_config_dir()
        policy = load_policy(config_dir)
        passphrase = policy.get("wipe_passphrase")
        has_passphrase = bool(
            passphrase and 
            passphrase.strip() and 
            passphrase != "your_secure_shared_secret_passphrase_here"
        )
        return jsonify({
            "passphrase_enabled": has_passphrase
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/erase/start", methods=["POST"])
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

        accepted_jobs = []
        for validated in validated_bays:
            job = create_erase_job(validated)
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

@app.route("/api/erase/jobs/<job_id>", methods=["GET"])
def get_erase_job(job_id):
    with ERASE_JOBS_LOCK:
        job = ERASE_JOBS.get(job_id)
        if job:
            return jsonify(job), 200

    with sqlite3.connect(get_db_path(), timeout=30.0) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT job_number, id, friendly_id, status, created_at, started_at, finished_at, error,
                   request_json, result_json, verification_json, marker_json, certificate_json
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
    }), 200

@app.route("/api/erase/jobs/<job_id>/cancel", methods=["POST"])
def cancel_erase_job(job_id):
    from job_management import finalize_failed_job
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

@app.route("/api/erase/history", methods=["GET"])
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
        with sqlite3.connect(get_db_path(), timeout=30.0) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT id, friendly_id, status, created_at, started_at, finished_at, error,
                       request_json, result_json, verification_json, marker_json, certificate_json
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
            })

        return jsonify({"jobs": jobs, "count": len(jobs)}), 200
    except Exception as e:
        return jsonify({"error": f"Database query failed: {str(e)}"}), 500

@app.route("/api/certificates/<job_id>", methods=["GET"])
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
    if response_format == "json":
        return jsonify(certificate), 200

    formats = certificate.get("formats") or {}
    if response_format == "html":
        html_meta = formats.get("html") or {}
        html_path = html_meta.get("path")
        if not html_path or not os.path.isfile(html_path):
            return jsonify({"error": f"certificate html not found for job: {job_id}"}), 404
        return send_file(
            html_path,
            mimetype="text/html",
            as_attachment=True,
            download_name=html_meta.get("filename") or os.path.basename(html_path),
        )

    return jsonify({"error": "format must be one of: json, html"}), 400

@app.route("/api/auth/verify", methods=["POST"])
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

@app.route("/api/admin/metrics")
def get_admin_metrics():
    try:
        total, used, free = shutil.disk_usage(get_data_dir())
        disk_pct = round((used / total) * 100, 1)
        disk_str = f"{format_capacity_bytes(used)} / {format_capacity_bytes(total)}"
        
        return jsonify({
            "disk_pct": disk_pct,
            "disk_str": disk_str,
            "ram_pct": get_ram_usage(),
            "cpu_pct": get_cpu_usage(),
            "uptime": get_system_uptime(),
            "ip_address": get_local_ip()
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/test-webhook", methods=["POST"])
def test_webhook():
    try:
        config_dir = get_config_dir()
        policy = load_policy(config_dir)
        slack_url = policy.get("slack_webhook_url")
        
        if not slack_url:
            return jsonify({"error": "No Slack webhook URL configured in policy.json"}), 400
            
        test_payload = {
            "text": f"🔔 *Drive Wipe Station Test Notification*\nStation: `{policy.get('station_id', 'unknown')}`\nTime: `{datetime.now(timezone.utc).isoformat()}`\nStatus: Network communication verified."
        }
        
        req_data = json.dumps(test_payload).encode("utf-8")
        req = urllib.request.Request(
            slack_url,
            data=req_data,
            headers={"Content-Type": "application/json"}
        )
        
        with urllib.request.urlopen(req, timeout=10) as response:
            resp_code = response.getcode()
            
        if resp_code in (200, 201, 204):
            logger.info("Test slack webhook dispatch succeeded.")
            return jsonify({"status": "success", "message": "Test webhook dispatched successfully."}), 200
        return jsonify({"error": f"Slack returned status code {resp_code}"}), 400
    except Exception as e:
        return jsonify({"error": f"Failed to send webhook: {str(e)}"}), 500

@app.route("/api/admin/export-csv")
def export_csv_ledger():
    try:
        with sqlite3.connect(get_db_path(), timeout=30.0) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT id, friendly_id, status, created_at, started_at, finished_at, error, request_json, verification_json 
                FROM erase_jobs ORDER BY job_number DESC
                """
            ).fetchall()
            
        output = io.StringIO()
        writer = csv.writer(output)
        
        writer.writerow(["Job ID", "Friendly ID", "Status", "Created At", "Started At", "Finished At", "Technician", "Ticket Number", "Bay", "Serial", "Model", "Capacity", "Method", "Verification Status", "Error"])
        
        for row in rows:
            req = json.loads(row["request_json"] or "{}")
            ver = json.loads(row["verification_json"] or "{}")
            
            writer.writerow([
                row["id"],
                row["friendly_id"],
                row["status"],
                row["created_at"],
                row["started_at"],
                row["finished_at"],
                req.get("technician", ""),
                req.get("ticket_number", ""),
                req.get("bay", ""),
                req.get("serial", ""),
                req.get("model", ""),
                format_capacity_bytes(req.get("capacity_bytes")),
                req.get("method", ""),
                ver.get("status", "none"),
                row["error"] or ""
            ])
            
        output.seek(0)
        return send_file(
            io.BytesIO(output.getvalue().encode('utf-8')),
            mimetype="text/csv",
            as_attachment=True,
            download_name=f"wipe-ledger-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.after_request
def cleanup_support_bundle(response):
    if request.path == "/api/admin/support-bundle" and response.status_code == 200:
        tar_path = getattr(g, 'support_bundle_tar_path', None)
        if tar_path and os.path.exists(tar_path):
            try:
                os.remove(tar_path)
            except Exception:
                pass
    return response

@app.route("/api/admin/support-bundle")
def download_support_bundle():
    try:
        hostname = socket.gethostname()
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        bundle_name = f"support-bundle-{hostname}-{timestamp}"
        workspace_dir = os.path.join("/tmp", bundle_name)
        os.makedirs(workspace_dir, exist_ok=True)
        
        try:
            lsblk_proc = subprocess.run(["sudo", "lsblk", "-J"], capture_output=True, text=True, timeout=10)
            with open(os.path.join(workspace_dir, "hardware_environment.txt"), "w") as f:
                f.write("=== LSBLK -J OUTPUT ===\n")
                f.write(lsblk_proc.stdout or "")
                f.write("\n\n=== LSHW STORAGE DETAILS ===\n")
                lshw_proc = subprocess.run(["sudo", "lshw", "-class", "storage", "-class", "disk"], capture_output=True, text=True, timeout=15)
                f.write(lshw_proc.stdout or "")
        except Exception as e:
            with open(os.path.join(workspace_dir, "hardware_environment_error.txt"), "w") as f:
                f.write(f"Failed to gather hardware details: {str(e)}")
                
        try:
            total, used, free = shutil.disk_usage(get_data_dir())
            with open(os.path.join(workspace_dir, "system_metrics.txt"), "w") as f:
                f.write(f"Host Hostname: {hostname}\n")
                f.write(f"Current Date: {datetime.now(timezone.utc).isoformat()}\n")
                f.write(f"System Uptime: {get_system_uptime()}\n")
                f.write(f"CPU Utilization: {get_cpu_usage()}%\n")
                f.write(f"RAM Utilization: {get_ram_usage()}%\n")
                f.write(f"OS Disk Space total: {format_capacity_bytes(total)}\n")
                f.write(f"OS Disk Space used: {format_capacity_bytes(used)}\n")
                f.write(f"OS Disk Space free: {format_capacity_bytes(free)}\n")
        except Exception:
            pass
            
        try:
            policy_dir = get_config_dir()
            policy_path = os.path.join(policy_dir, "policy.json")
            if os.path.exists(policy_path):
                with open(policy_path, "r", encoding="utf-8") as f:
                    policy_data = json.load(f)
                for key in ["wipe_passphrase", "slack_webhook_url", "lan_passphrase"]:
                    if key in policy_data:
                        policy_data[key] = "[REDACTED]"
                with open(os.path.join(workspace_dir, "redacted_policy.json"), "w", encoding="utf-8") as f:
                    json.dump(policy_data, f, indent=2)
        except Exception:
            pass
            
        try:
            logs_dir = get_logs_dir()
            app_log_path = os.path.join(logs_dir, "app.log")
            if os.path.exists(app_log_path):
                shutil.copy(app_log_path, os.path.join(workspace_dir, "app.log"))
                
            failed_logs_dir = get_failed_logs_dir()
            if os.path.exists(failed_logs_dir):
                shutil.copytree(failed_logs_dir, os.path.join(workspace_dir, "failed_logs"), dirs_exist_ok=True)
        except Exception:
            pass
            
        tar_path = f"/tmp/{bundle_name}.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tar:
            tar.add(workspace_dir, arcname=bundle_name)

        shutil.rmtree(workspace_dir, ignore_errors=True)

        g.support_bundle_tar_path = tar_path
        logger.info(f"Support bundle built successfully: {tar_path}")
        return send_file(
            tar_path,
            mimetype="application/gzip",
            as_attachment=True,
            download_name=f"{bundle_name}.tar.gz"
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/bay-map")
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
        return jsonify(compose_bay_map_document(bays, metadata)), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/unmapped-drives")
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
                smart = get_smart_data(dev_node)
                
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
        return jsonify({"error": str(e)}), 500

# --- AUTOMATIC ENCLOSURE MAPPING ROUTE ---

@app.route("/api/admin/auto-detect-bays", methods=["POST"])
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
        if os.path.exists(enclosure_base) and os.listdir(enclosure_base):
            METADATA_DIRS = {"components", "device", "id", "power", "subsystem", "uevent"}

            for enc_id in os.listdir(enclosure_base):
                enc_path = os.path.join(enclosure_base, enc_id)
                if not os.path.isdir(enc_path):
                    continue
                
                for slot_id in os.listdir(enc_path):
                    if slot_id in METADATA_DIRS:
                        continue
                    slot_path = os.path.join(enc_path, slot_id)
                    if not os.path.isdir(slot_path):
                        continue
                    
                    block_devs = []

                    # Find associated block device nodes under slot path
                    dev_block_path = os.path.join(slot_path, "device", "block")
                    if os.path.exists(dev_block_path) and os.path.isdir(dev_block_path):
                        for b in os.listdir(dev_block_path):
                            block_devs.append(b)
                    
                    dev_path = os.path.join(slot_path, "device")
                    if os.path.exists(dev_path) and os.path.isdir(dev_path):
                        for name in os.listdir(dev_path):
                            if name.startswith("sd") or name.startswith("nvme"):
                                block_devs.append(name)

                    # Process found devices for this slot
                    for sd_node in sorted(list(set(block_devs))):
                        real_dev = f"/dev/{sd_node}"
                        digits = re.findall(r'\d+', slot_id)
                        if digits:
                            slot_num = int(digits[0])
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
            if os.path.exists(sys_block_dir):
                for name in os.listdir(sys_block_dir):
                    if not name.startswith("sd"):
                        continue
                        
                    real_path = os.path.realpath(os.path.join(sys_block_dir, name))
                    
                    # Walk up the parent directory tree to find the SCSI/SAS transport target node
                    npath = real_path
                    found_bay = None
                    
                    while npath and npath != "/":
                        sas_device_dir = os.path.join(npath, "sas_device")
                        if os.path.exists(sas_device_dir) and os.path.isdir(sas_device_dir):
                            # Inspect the specific SAS end device subdirectory inside sas_device
                            for end_dev_id in os.listdir(sas_device_dir):
                                bay_id_path = os.path.join(sas_device_dir, end_dev_id, "bay_identifier")
                                if os.path.exists(bay_id_path):
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
                    "label": f"Work Bay {bay_id[3:] if bay_id.startswith('bay') else bay_id}",
                    "by_path": by_path_val,
                    "by_path_nvme": None
                }
                updates_count += 1

        save_bay_map(compose_bay_map_document(bay_map, layout_metadata), config_dir)
        
        logger.info(f"Auto-detect bays updated {updates_count} map elements out of {len(discovered_slots)} total discovered enclosures.")
        return jsonify({
            "status": "success",
            "message": f"Successfully mapped {len(discovered_slots)} physical backplane slot(s). Updated {updates_count} bay(s).",
            "bay_map": bay_map
        }), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/layout-templates")
def get_layout_templates():
    try:
        config_dir = get_config_dir()
        templates = load_layout_templates(config_dir)
        return jsonify({"templates": list(templates.values()), "supported_traversals": sorted(list(SUPPORTED_TRAVERSALS))}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/apply-template", methods=["POST"])
def admin_apply_template():
    try:
        payload = request.get_json(silent=True) or {}
        template_id = str(payload.get("template_id") or "").strip()
        traversal_preset = str(payload.get("traversal_preset") or "").strip() or None
        custom_overrides = payload.get("custom_overrides") or {}

        if not template_id:
            return jsonify({"error": "template_id is required"}), 400

        config_dir = get_config_dir()
        templates = load_layout_templates(config_dir)
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
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/save-bay-map", methods=["POST"])
def update_bay_map():
    try:
        payload = request.get_json(silent=True) or {}
        if not payload:
            return jsonify({"error": "Invalid payload"}), 400

        config_dir = get_config_dir()

        if not isinstance(payload, dict):
            return jsonify({"error": "Payload must be a dictionary map."}), 400

        templates = load_layout_templates(config_dir)
        bays, layout_metadata = normalize_bay_map_document(payload)
        if not bays:
            return jsonify({"error": "At least one bay configuration is required."}), 400

        for bay_id, conf in bays.items():
            if not isinstance(conf, dict):
                return jsonify({"error": f"Configuration for {bay_id} must be a dictionary."}), 400

        validation_error = validate_layout_metadata(layout_metadata, bays, templates)
        if validation_error:
            return jsonify({"error": validation_error}), 400

        save_bay_map(compose_bay_map_document(bays, layout_metadata), config_dir)

        logger.info("Enclosure bay map edited manually by administrator.")
        return jsonify({"status": "success", "message": "Bay mapping configuration updated successfully."}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/policy", methods=["GET", "POST"])
def admin_policy():
    config_dir = get_config_dir()
    if request.method == "GET":
        try:
            policy = load_policy(config_dir)
            safe_policy = policy.copy()
            if "lan_passphrase" in safe_policy:
                safe_policy["lan_passphrase"] = ""
            return jsonify(safe_policy), 200
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    else:
        try:
            payload = request.get_json(silent=True) or {}
            current_policy = load_policy(config_dir)
            
            updatable_fields = ["station_id", "slack_webhook_url", "prewipe_spot_check", "post_erase_marker", "allow_method_override"]
            for field in updatable_fields:
                if field in payload:
                    current_policy[field] = payload[field]
                    
            new_pass = str(payload.get("lan_passphrase") or "").strip()
            if new_pass:
                current_policy["lan_passphrase"] = new_pass
                
            save_policy(current_policy, config_dir)
            
            logger.info("Operational policies modified successfully by administrator.")
            return jsonify({"status": "success", "message": "System policies updated successfully."}), 200
        except Exception as e:
            return jsonify({"error": str(e)}), 500
# --- END OF FILE backend/api_routes.py ---
