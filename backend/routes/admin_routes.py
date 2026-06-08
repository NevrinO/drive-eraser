# Admin-related routes
import os
import json
import csv
import io
import shutil
import socket
import subprocess
import tarfile
import base64
import hashlib
import sqlite3
import urllib.request
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request, send_file, g
from PIL import Image
from app_config import logger, calculate_session_token, limiter
from common import get_config_dir, load_policy, save_policy, get_data_dir, get_logs_dir, get_failed_logs_dir, get_db_path
from system_metrics import get_ram_usage, get_cpu_usage, get_system_uptime
from disk_utils import format_capacity_bytes
from app_config import get_local_ip
import ipaddress

admin_bp = Blueprint('admin_routes', __name__)

def is_local_request(request):
    """Check if the request is from localhost or local network."""
    remote_addr = request.remote_addr
    if not remote_addr:
        return False
    
    # Check for localhost IPv4 and IPv6
    if remote_addr in ('127.0.0.1', '::1'):
        return True
    
    # Check for private/local network ranges
    try:
        ip = ipaddress.ip_address(remote_addr)
        return ip.is_private
    except ValueError:
        return False

def require_admin_auth(f):
    """Decorator for conditional authentication on admin routes.
    
    Allows access from localhost without authentication.
    Requires authentication from remote addresses.
    """
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if is_local_request(request):
            return f(*args, **kwargs)
        
        # Remote request: require authentication
        config_dir = get_config_dir()
        policy = load_policy(config_dir)
        lan_passphrase = policy.get("lan_passphrase", "eraser123")
        session_token = request.cookies.get("admin_session")
        
        if not session_token or session_token != calculate_session_token(lan_passphrase):
            return jsonify({"error": "Authentication required"}), 401
        
        return f(*args, **kwargs)
    return decorated_function

@admin_bp.route("/api/admin/metrics")
@require_admin_auth
@limiter.limit("30 per minute")
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
        logger.error(f"Error getting admin metrics: {e}")
        return jsonify({"error": str(e)}), 500

@admin_bp.route("/api/admin/test-webhook", methods=["POST"])
@require_admin_auth
@limiter.limit("10 per minute")
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
        logger.error(f"Test webhook failed: {e}")
        return jsonify({"error": f"Failed to send webhook: {str(e)}"}), 500

@admin_bp.route("/api/admin/export-csv")
@require_admin_auth
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
        logger.error(f"CSV export failed: {e}")
        return jsonify({"error": str(e)}), 500

@admin_bp.after_request
def cleanup_support_bundle(response):
    if request.path == "/api/admin/support-bundle" and response.status_code == 200:
        tar_path = getattr(g, 'support_bundle_tar_path', None)
        if tar_path and os.path.exists(tar_path):
            try:
                os.remove(tar_path)
            except Exception:
                pass
    return response

@admin_bp.route("/api/admin/support-bundle")
@require_admin_auth
def download_support_bundle():
    try:
        hostname = socket.gethostname()
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        bundle_name = f"support-bundle-{hostname}-{timestamp}"
        workspace_dir = os.path.join("/tmp", bundle_name)
        os.makedirs(workspace_dir, exist_ok=True)
        
        try:
            lsblk_proc = subprocess.run(["sudo", "lsblk", "-J"], capture_output=True, text=True, timeout=10, shell=False)
            with open(os.path.join(workspace_dir, "hardware_environment.txt"), "w") as f:
                f.write("=== LSBLK -J OUTPUT ===\n")
                f.write(lsblk_proc.stdout or "")
                f.write("\n\n=== LSHW STORAGE DETAILS ===\n")
                lshw_proc = subprocess.run(["sudo", "lshw", "-class", "storage", "-class", "disk"], capture_output=True, text=True, timeout=15, shell=False)
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
        logger.error(f"Support bundle download failed: {e}")
        return jsonify({"error": str(e)}), 500

@admin_bp.route("/api/admin/policy", methods=["GET", "POST"])
@require_admin_auth
@limiter.limit("30 per minute")
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
            logger.error(f"Error getting policy: {e}")
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
            logger.error(f"Error updating policy: {e}")
            return jsonify({"error": str(e)}), 500

@admin_bp.route("/api/admin/triage-config", methods=["GET", "POST"])
@require_admin_auth
@limiter.limit("30 per minute")
def admin_triage_config():
    config_dir = get_config_dir()
    if request.method == "GET":
        try:
            policy = load_policy(config_dir)
            triage_thresholds = policy.get("triage_thresholds", {})
            return jsonify(triage_thresholds), 200
        except Exception as e:
            logger.error(f"Error getting triage config: {e}")
            return jsonify({"error": str(e)}), 500
    else:
        try:
            payload = request.get_json(silent=True) or {}
            current_policy = load_policy(config_dir)
            
            # Validate all threshold values are numeric and within reasonable ranges
            valid_thresholds = {
                "ssd_new_poh_threshold": (int, 0, 100000),
                "ssd_high_poh_threshold": (int, 0, 200000),
                "hdd_new_poh_threshold": (int, 0, 100000),
                "hdd_high_poh_threshold": (int, 0, 200000),
                "health_score_destroy_threshold": (int, 0, 100),
                "health_score_scratch_threshold": (int, 0, 100),
                "ssd_remaining_life_destroy_threshold": (int, 0, 100),
                "ssd_remaining_life_scratch_threshold": (int, 0, 100),
                "ssd_remaining_life_good_threshold": (int, 0, 100),
                "ssd_new_fdw_threshold": (float, 0.0, 100.0),
                "hdd_new_fdw_threshold": (float, 0.0, 100.0),
                "hdd_heavy_fdw_threshold": (float, 0.0, 1000.0),
                "realloc_raw_new_threshold": (int, 0, 1000),
                "pending_sectors_destroy_threshold": (int, 0, 1000),
                "pending_sectors_scratch_threshold": (int, 0, 1000)
            }
            
            # Load existing thresholds and merge new values into them
            existing_thresholds = current_policy.get("triage_thresholds", {})
            new_thresholds = existing_thresholds.copy()
            
            for key, (val_type, min_val, max_val) in valid_thresholds.items():
                if key in payload:
                    try:
                        value = val_type(payload[key])
                        if not (min_val <= value <= max_val):
                            return jsonify({"error": f"Invalid value for {key}: must be between {min_val} and {max_val}"}), 400
                        new_thresholds[key] = value
                    except (ValueError, TypeError):
                        return jsonify({"error": f"Invalid type for {key}: must be {val_type.__name__}"}), 400
            
            current_policy["triage_thresholds"] = new_thresholds
            save_policy(current_policy, config_dir)
            
            logger.info("Triage thresholds updated successfully by administrator.")
            return jsonify({"status": "success", "message": "Triage thresholds updated successfully."}), 200
        except Exception as e:
            logger.error(f"Error updating triage config: {e}")
            return jsonify({"error": str(e)}), 500

@admin_bp.route("/api/admin/logo", methods=["GET", "POST", "DELETE"])
@require_admin_auth
@limiter.limit("10 per minute")
def manage_logo():
    logo_path = os.path.join(get_data_dir(), "logo.png")
    
    if request.method == "GET":
        try:
            has_logo = os.path.exists(logo_path)
            dimensions = None
            base64_data = None
            if has_logo:
                try:
                    with Image.open(logo_path) as img:
                        dimensions = {"width": img.width, "height": img.height}
                    # Read file and convert to base64 for preview
                    with open(logo_path, "rb") as f:
                        img_bytes = f.read()
                    base64_data = base64.b64encode(img_bytes).decode("utf-8")
                except Exception:
                    dimensions = None
                    base64_data = None
            return jsonify({"has_logo": has_logo, "dimensions": dimensions, "base64": base64_data}), 200
        except Exception as e:
            logger.error(f"Error getting logo: {e}")
            return jsonify({"error": str(e)}), 500
    
    elif request.method == "POST":
        try:
            # Check for confirmation if logo already exists
            confirm = request.args.get("confirm", "false").lower() == "true"
            if os.path.exists(logo_path) and not confirm:
                return jsonify({"error": "confirmation_required", "message": "A logo already exists. Confirm replacement by adding ?confirm=true to the request."}), 400
            
            # Check if file is present
            if "logo" not in request.files:
                return jsonify({"error": "No file provided"}), 400
            
            file = request.files["logo"]
            if file.filename == "":
                return jsonify({"error": "No file selected"}), 400
            
            # Validate format by reading with PIL
            try:
                img = Image.open(file)
                file_format = img.format
                if file_format not in ("PNG", "JPEG", "JPG"):
                    return jsonify({"error": f"Unsupported format: {file_format}. Only PNG, JPG, JPEG allowed."}), 400
                
                # Ensure data directory exists
                os.makedirs(get_data_dir(), exist_ok=True)
                
                # Resize to target dimensions (max 500x500) for better quality
                img.thumbnail((500, 500), Image.Resampling.LANCZOS)
                
                # Use atomic write: save to temporary file first, then rename
                temp_path = logo_path + ".tmp"
                file.seek(0)
                img.save(temp_path, format="PNG", optimize=True, compress_level=3)
                
                # Validate converted PNG file size (max 1MB)
                png_size = os.path.getsize(temp_path)
                logger.info(f"Converted PNG size: {png_size} bytes ({png_size / 1024:.2f} KB)")
                if png_size > 1024 * 1024:
                    os.remove(temp_path)
                    return jsonify({"error": f"Converted PNG exceeds 1MB limit (was {png_size / 1024:.2f} KB)"}), 400
                
                # Atomic rename operation
                os.replace(temp_path, logo_path)
                
                # Calculate hash of the committed file for integrity validation
                with open(logo_path, "rb") as f:
                    file_hash = hashlib.sha256(f.read()).hexdigest()
                
                # Store hash alongside the logo file
                hash_path = logo_path + ".sha256"
                with open(hash_path, "w") as f:
                    f.write(file_hash)
                
                logger.info(f"Custom logo uploaded successfully: {logo_path}")
                return jsonify({"status": "success", "message": "Logo uploaded successfully"}), 200
                
            except Exception as e:
                # Clean up temporary file if it exists
                temp_path = logo_path + ".tmp"
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass
                return jsonify({"error": f"Invalid image file: {str(e)}"}), 400
                
        except Exception as e:
            logger.error(f"Logo upload failed: {str(e)}")
            return jsonify({"error": str(e)}), 500
    
    elif request.method == "DELETE":
        try:
            # Direct removal without existence check to avoid TOCTOU race condition
            os.remove(logo_path)
            # Also remove the hash file if it exists
            hash_path = logo_path + ".sha256"
            try:
                os.remove(hash_path)
            except FileNotFoundError:
                pass
            logger.info("Custom logo deleted by administrator.")
            return jsonify({"status": "success", "message": "Logo deleted successfully"}), 200
        except FileNotFoundError:
            # File doesn't exist, which is fine
            return jsonify({"status": "success", "message": "No logo to delete"}), 200
        except Exception as e:
            logger.error(f"Logo deletion failed: {e}")
            return jsonify({"error": str(e)}), 500
