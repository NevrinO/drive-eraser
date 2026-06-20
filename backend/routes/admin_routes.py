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
import re
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from flask import Blueprint, jsonify, request, send_file, g
from PIL import Image
from app_config import logger, calculate_session_token, limiter, ERASE_JOBS, ERASE_JOBS_LOCK
from common import get_config_dir, load_policy, save_policy, get_data_dir, get_logs_dir, get_failed_logs_dir, get_db_path, load_bay_map, save_bay_map, BAY_MAP_LOCK, BAY_MAP_SCHEMA, ENCLOSURE_SCHEMA, SLOT_SCHEMA, SLOT_MAPPING_SCHEMA, TEMPLATE_SCHEMA, validate_strict_audit_requirements
from layout_templates import load_layout_templates, save_layout_templates, TEMPLATES_LOCK, build_traversal_positions, SUPPORTED_TRAVERSALS
from device_discovery import generate_master_slot_map, validate_pci_address
from system_metrics import get_ram_usage, get_cpu_usage, get_system_uptime
from disk_utils import format_capacity_bytes
from app_config import get_local_ip
from disk_ops import invalidate_drive_cache
from database import persist_job
import ipaddress
from verification import verify_nvme_sanitize, verify_sata_sanitize, verify_sas_block
from job_management import poll_nvme_sanitize_progress, poll_sata_sanitize_progress, poll_sas_sanitize_progress, get_device_sectors_written

admin_bp = Blueprint('admin_routes', __name__)

# Device name validation patterns following lesson #9 and #15
# Use \Z (not $) for strict end-of-string anchor to prevent "/dev/sda\n" bypass
_SATA_DEVICE_RE = re.compile(r'^[a-z]+[0-9]*\Z')
_NVME_DEVICE_RE = re.compile(r'^nvme[0-9]+(n[0-9]+)?(p[0-9]+)?\Z')
MAX_DEVICES_FOR_BUNDLE = 50  # Rule #5: enforce size limits for DoS prevention

# Size limits for DoS prevention (Rule #5)
MAX_ENCODSURES = 100
MAX_SLOTS_PER_ENCLOSURE = 1000
MAX_TEMPLATES = 50

# ID validation pattern (alphanumeric, hyphens, underscores only)
_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+\Z')

def is_valid_id(id_str: str) -> bool:
    """Validate ID string against safe character whitelist.
    
    Following lessons-learned rule #9: Never accept raw strings without validation.
    
    Args:
        id_str: ID string to validate
        
    Returns:
        True if valid, False otherwise
    """
    if not id_str or not isinstance(id_str, str):
        return False
    if len(id_str) > 100:  # Reasonable length limit
        return False
    return bool(_ID_PATTERN.match(id_str))

def is_valid_device_name(name: str) -> bool:
    r"""Validate device name against strict whitelist to prevent path traversal and injection.
    
    Following lessons-learned rule #9: Never accept raw device paths without validation.
    Following lessons-learned rule #15: Use \Z for strict end-of-string anchor.
    
    Args:
        name: Device name string (e.g., "sda", "nvme0n1")
        
    Returns:
        True if name is valid, False otherwise
    """
    if not name or not isinstance(name, str):
        return False
    if ".." in name or "\n" in name or "\r" in name:
        return False
    return bool(_SATA_DEVICE_RE.match(name) or _NVME_DEVICE_RE.match(name))

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

            # Parse lsblk JSON to get disk devices and run smartctl -x on each
            lsblk_data = json.loads(lsblk_proc.stdout) if lsblk_proc.stdout else {}
            blockdevices = lsblk_data.get("blockdevices", [])
            
            # Create dedicated folder for smartctl output
            smartctl_dir = os.path.join(workspace_dir, "smartctl")
            os.makedirs(smartctl_dir, exist_ok=True)
            
            # Collect valid disk devices with validation and count limit
            valid_devices = []
            for device in blockdevices:
                device_name = device.get("name", "")
                # Skip loop devices (virtual block devices, not physical drives)
                if device_name.startswith("loop"):
                    continue
                # Validate device name against whitelist (lesson #9)
                if not device_name or not is_valid_device_name(device_name):
                    logger.warning(f"Skipping invalid device name: {device_name}")
                    continue
                # Rule #5: enforce size limits for DoS prevention
                if len(valid_devices) >= MAX_DEVICES_FOR_BUNDLE:
                    logger.warning(f"Reached device limit ({MAX_DEVICES_FOR_BUNDLE}), skipping remaining devices")
                    break
                device_path = f"/dev/{device_name}"
                valid_devices.append((device_name, device_path))
            
            # Collect smartctl data in parallel using ThreadPoolExecutor
            def _collect_smartctl_for_device(device_name, device_path):
                """Collect smartctl -x output for a single device."""
                try:
                    smartctl_proc = subprocess.run(
                        ["sudo", "smartctl", "-x", device_path],
                        capture_output=True,
                        text=True,
                        timeout=30,
                        shell=False
                    )
                    # Improved filename sanitization with regex
                    safe_name = re.sub(r'[^\w\-]', '_', device_name)
                    smartctl_file = os.path.join(smartctl_dir, f"{safe_name}.txt")
                    with open(smartctl_file, "w") as f:
                        f.write(f"=== SMARTCTL -X OUTPUT FOR {device_path} ===\n")
                        f.write(smartctl_proc.stdout or "")
                        if smartctl_proc.stderr:
                            f.write(f"\n=== STDERR ===\n")
                            f.write(smartctl_proc.stderr)
                    return (device_name, None)
                except subprocess.TimeoutExpired:
                    logger.warning(f"smartctl -x timed out for {device_path}")
                    return (device_name, "timeout")
                except Exception as e:
                    logger.warning(f"smartctl -x failed for {device_path}: {e}")
                    return (device_name, str(e))
            
            # Use ThreadPoolExecutor for parallel collection (similar to disk_ops.py pattern)
            max_workers = min(8, len(valid_devices))
            if valid_devices:
                executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="smartctl-bundle")
                futures = {}
                try:
                    for device_name, device_path in valid_devices:
                        futures[executor.submit(_collect_smartctl_for_device, device_name, device_path)] = device_name
                    # Overall timeout for the entire batch (120 seconds)
                    for future in as_completed(futures, timeout=120):
                        device_name = futures[future]
                        try:
                            future.result()
                        except FuturesTimeoutError:
                            logger.warning(f"smartctl collection timed out for {device_name}")
                        except Exception as e:
                            logger.warning(f"smartctl collection failed for {device_name}: {e}")
                finally:
                    executor.shutdown(wait=False)
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
            if "wipe_passphrase" in safe_policy:
                safe_policy["wipe_passphrase"] = ""
            return jsonify(safe_policy), 200
        except Exception as e:
            logger.error(f"Error getting policy: {e}")
            return jsonify({"error": str(e)}), 500
    else:
        try:
            payload = request.get_json(silent=True) or {}
            current_policy = load_policy(config_dir)
            
            # Extract new values from payload before validation
            new_strict_audit_mode = payload.get("strict_audit_mode")
            new_wipe_pass = str(payload.get("wipe_passphrase") or "").strip()
            new_lan_pass = str(payload.get("lan_passphrase") or "").strip()
            
            # Type validation for boolean fields
            if new_strict_audit_mode is not None and not isinstance(new_strict_audit_mode, bool):
                return jsonify({"error": "strict_audit_mode must be a boolean value"}), 400
            
            # Validation: strict_audit_mode requires wipe_passphrase of at least 8 characters
            # Check both the new value from payload and the existing value in current_policy
            strict_audit_enabled = new_strict_audit_mode if new_strict_audit_mode is not None else current_policy.get("strict_audit_mode", False)
            if strict_audit_enabled:
                # Use new passphrase if provided, otherwise check existing passphrase
                passphrase_to_check = new_wipe_pass if new_wipe_pass else current_policy.get("wipe_passphrase", "")
                is_valid, error_msg = validate_strict_audit_requirements(strict_audit_enabled, passphrase_to_check)
                if not is_valid:
                    return jsonify({"error": error_msg}), 400
            
            # Apply mutations after validation passes
            updatable_fields = ["station_id", "slack_webhook_url", "prewipe_spot_check", "post_erase_marker", "allow_method_override", "crypto_verification_mode", "discovery_max_workers", "max_concurrent_wipes", "blockdev_post_wipe_retries", "blockdev_post_wipe_retry_delay", "strict_audit_mode"]
            for field in updatable_fields:
                if field in payload:
                    current_policy[field] = payload[field]
                    
            lan_passphrase_changed = False
            if new_lan_pass:
                current_policy["lan_passphrase"] = new_lan_pass
                lan_passphrase_changed = True
            
            wipe_passphrase_changed = False
            if new_wipe_pass:
                current_policy["wipe_passphrase"] = new_wipe_pass
                wipe_passphrase_changed = True
                
            save_policy(current_policy, config_dir)
            
            # Passphrase change invalidates marker HMAC verification results in drive cache
            if lan_passphrase_changed or wipe_passphrase_changed:
                invalidate_drive_cache()
            
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
                except Exception as e:
                    logger.warning(f"Logo GET: failed to read/convert logo: {e}")
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
                
                # Calculate hash of the bytes that will be served (the committed PNG file)
                with open(temp_path, "rb") as f:
                    file_bytes = f.read()
                    file_hash = hashlib.sha256(file_bytes).hexdigest()
                
                # Atomic rename operation for logo file
                os.replace(temp_path, logo_path)
                
                # Write hash file atomically (temp file + rename)
                hash_path = logo_path + ".sha256"
                hash_temp_path = hash_path + ".tmp"
                with open(hash_temp_path, "w") as f:
                    f.write(file_hash)
                os.replace(hash_temp_path, hash_path)
                
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

@admin_bp.route("/api/admin/jobs/kill-all", methods=["POST"])
@require_admin_auth
@limiter.limit("10 per minute")
def kill_all_jobs():
    """Kill all running and queued jobs. Checks drive hardware status before killing.
    
    - If drive reports still wiping: job is skipped with detailed diagnostics
    - If drive reports idle/complete but subprocess stuck: job is killed
    """
    def check_drive_hardware_status(job):
        """Check actual hardware status of the drive. Returns dict with status info."""
        request_data = job.get("request", {})
        device = request_data.get("device")
        method = request_data.get("method")
        interface_type = request_data.get("interface_type", "unknown")
        
        if not device or not method:
            return {"can_query": False, "reason": "missing_device_or_method"}
        
        result = {
            "can_query": True,
            "device": device,
            "method": method,
            "interface_type": interface_type,
            "hardware_active": False,
            "hardware_status": None,
            "progress_percent": None,
            "raw_data": {}
        }
        
        try:
            if interface_type == "nvme" and method in {"crypto", "block"}:
                # Check NVMe sanitize status
                nvme_result = verify_nvme_sanitize(device, method)
                result["verification_result"] = nvme_result
                
                if nvme_result.get("ok"):
                    # Sanitize completed successfully
                    result["hardware_active"] = False
                    result["hardware_status"] = "completed"
                    details = nvme_result.get("details", {})
                    result["raw_data"] = {
                        "sstat": details.get("sstat"),
                        "sprog": details.get("sprog")
                    }
                else:
                    error = nvme_result.get("error", "")
                    details = nvme_result.get("details", {})
                    result["raw_data"] = {
                        "sstat": details.get("sstat"),
                        "sprog": details.get("sprog")
                    }
                    
                    if "still_in_progress" in error:
                        result["hardware_active"] = True
                        result["hardware_status"] = "in_progress"
                        sprog = details.get("sprog")
                        if sprog is not None and sprog < 65535:
                            result["progress_percent"] = round((sprog / 65535.0) * 100, 2)
                    elif "failed" in error:
                        result["hardware_active"] = False
                        result["hardware_status"] = "failed"
                    elif "never_executed" in error:
                        result["hardware_active"] = False
                        result["hardware_status"] = "never_started"
                    else:
                        # Unknown/error state - assume not active to be safe for kill
                        result["hardware_active"] = False
                        result["hardware_status"] = f"error: {error}"
                        
            elif interface_type == "sata" and method in {"crypto", "block"}:
                # Check SATA sanitize status
                sata_result = verify_sata_sanitize(device, method)
                result["verification_result"] = sata_result
                
                if sata_result.get("ok"):
                    result["hardware_active"] = False
                    result["hardware_status"] = "completed"
                else:
                    error = sata_result.get("error", "")
                    details = sata_result.get("details", {})
                    result["raw_data"]["output"] = details.get("output", "")[:500]
                    
                    if "still_in_progress" in error:
                        result["hardware_active"] = True
                        result["hardware_status"] = "in_progress"
                        # Try to get progress percentage
                        progress = poll_sata_sanitize_progress(device)
                        if progress is not None:
                            result["progress_percent"] = round(progress, 2)
                    elif "failed" in error:
                        result["hardware_active"] = False
                        result["hardware_status"] = "failed"
                    else:
                        result["hardware_active"] = False
                        result["hardware_status"] = f"error: {error}"
                        
            elif interface_type == "sas" and method == "block":
                # Check SAS sanitize status
                sas_result = verify_sas_block(device, method)
                result["verification_result"] = sas_result
                
                if sas_result.get("ok"):
                    result["hardware_active"] = False
                    result["hardware_status"] = "completed"
                else:
                    error = sas_result.get("error", "")
                    details = sas_result.get("details", {})
                    result["raw_data"]["output"] = details.get("output", "")[:500]
                    
                    if "still_in_progress" in error:
                        result["hardware_active"] = True
                        result["hardware_status"] = "in_progress"
                        progress = poll_sas_sanitize_progress(device)
                        if progress is not None:
                            result["progress_percent"] = round(progress, 2)
                    elif "failed" in error:
                        result["hardware_active"] = False
                        result["hardware_status"] = "failed"
                    else:
                        result["hardware_active"] = False
                        result["hardware_status"] = f"error: {error}"
                        
            elif method == "overwrite":
                # Overwrite method - no hardware status, just check if process is running
                # We can estimate progress from sectors written
                result["can_query"] = False
                result["reason"] = "overwrite_no_hardware_status"
                result["hardware_active"] = None  # Unknown, rely on subprocess status
                sectors = get_device_sectors_written(device)
                capacity_bytes = request_data.get("capacity_bytes", 100 * 1024 * 1024 * 1024)
                if sectors is not None:
                    wrote_bytes = sectors * 512
                    result["progress_percent"] = round(min(99.9, (wrote_bytes / capacity_bytes) * 100), 2)
                    result["raw_data"]["sectors_written"] = sectors
                    
            elif interface_type == "sata" and method in {"secure_erase", "enhanced_secure_erase"}:
                # SATA secure erase - use hdparm status
                sata_result = verify_sata_sanitize(device, method)
                result["verification_result"] = sata_result
                
                if sata_result.get("ok"):
                    result["hardware_active"] = False
                    result["hardware_status"] = "completed"
                else:
                    error = sata_result.get("error", "")
                    if "still_in_progress" in error:
                        result["hardware_active"] = True
                        result["hardware_status"] = "in_progress"
                    else:
                        result["hardware_active"] = False
                        result["hardware_status"] = f"error: {error}"
            else:
                result["can_query"] = False
                result["reason"] = f"unsupported_combination: {interface_type}/{method}"
                
        except Exception as e:
            result["can_query"] = False
            result["reason"] = f"verification_exception: {str(e)}"
            logger.warning(f"Failed to check hardware status for job {job.get('id')}: {e}")
            
        return result
    
    try:
        killed_jobs = []
        skipped_jobs = []
        
        with ERASE_JOBS_LOCK:
            for job_id, job in list(ERASE_JOBS.items()):
                if job.get("status") in {"running", "queued"}:
                    request_data = job.get("request", {})
                    device = request_data.get("device")
                    method = request_data.get("method")
                    interface_type = request_data.get("interface_type", "unknown")
                    bay = request_data.get("bay")
                    
                    # Check actual hardware status
                    hw_status = check_drive_hardware_status(job)
                    
                    # Determine if we should kill based on hardware status
                    should_kill = True
                    skip_reason = None
                    
                    if hw_status.get("can_query"):
                        if hw_status.get("hardware_active") is True:
                            # Hardware reports it's still active - don't kill
                            should_kill = False
                            skip_reason = "hardware_operation_active"
                        elif hw_status.get("hardware_active") is False:
                            # Hardware reports idle/complete - safe to kill stuck subprocess
                            should_kill = True
                    else:
                        # Can't query hardware status (overwrite method, etc.)
                        # Fall back to subprocess status
                        process = job.get("_process")
                        if process and process.poll() is None:
                            # Process is running but we can't verify hardware
                            # Check if job has been running unusually long (optional safety)
                            pass  # Will kill based on subprocess alone
                    
                    if not should_kill:
                        # Skip killing - hardware still active
                        skipped_info = {
                            "job_id": job_id,
                            "bay": bay,
                            "device": device,
                            "method": method,
                            "interface_type": interface_type,
                            "reason": skip_reason,
                            "hardware_status": {
                                "state": hw_status.get("hardware_status"),
                                "progress_percent": hw_status.get("progress_percent"),
                                "raw_data": hw_status.get("raw_data")
                            },
                            "subprocess_status": {
                                "pid": job.get("_process").pid if job.get("_process") else None,
                                "is_running": job.get("_process").poll() is None if job.get("_process") else False
                            },
                            "job_state": {
                                "status": job.get("status"),
                                "current_phase": job.get("current_phase"),
                                "started_at": job.get("started_at"),
                                "progress_percent": job.get("progress_percent")
                            }
                        }
                        skipped_jobs.append(skipped_info)
                        logger.info(f"Skipped killing job {job_id} - hardware still active: {hw_status.get('hardware_status')}")
                    else:
                        # Safe to kill - hardware idle or subprocess stuck
                        process = job.get("_process")
                        termination_result = {"terminated": False, "error": None}
                        
                        if process and process.poll() is None:
                            try:
                                process.terminate()
                                try:
                                    process.wait(timeout=10)
                                    termination_result["terminated"] = True
                                except subprocess.TimeoutExpired:
                                    process.kill()
                                    process.wait()
                                    termination_result["terminated"] = True
                                    termination_result["method"] = "kill"
                            except Exception as e:
                                logger.warning(f"Failed to terminate process for job {job_id}: {e}")
                                termination_result["error"] = str(e)
                        
                        job["status"] = "failed"
                        job["finished_at"] = datetime.now(timezone.utc).isoformat()
                        job["error"] = "Job killed by administrator"
                        job["current_phase"] = "Killed by Admin"
                        job["_kill_info"] = {
                            "hardware_status_before_kill": hw_status.get("hardware_status") if hw_status.get("can_query") else "unknown",
                            "termination_result": termination_result,
                            "killed_at": datetime.now(timezone.utc).isoformat()
                        }
                        persist_job(job)
                        
                        killed_info = {
                            "job_id": job_id,
                            "bay": bay,
                            "device": device,
                            "method": method,
                            "interface_type": interface_type,
                            "hardware_status": hw_status.get("hardware_status") if hw_status.get("can_query") else "unknown",
                            "termination_result": termination_result
                        }
                        killed_jobs.append(killed_info)
                        ERASE_JOBS.pop(job_id, None)

        # Build response
        response = {
            "status": "success",
            "killed_count": len(killed_jobs),
            "killed_jobs": killed_jobs,
            "skipped_count": len(skipped_jobs),
            "skipped_jobs": skipped_jobs
        }
        
        if killed_jobs:
            logger.warning(f"Admin killed {len(killed_jobs)} job(s): {[j['job_id'] for j in killed_jobs]}")
        if skipped_jobs:
            logger.info(f"Admin kill-all skipped {len(skipped_jobs)} active job(s): {[j['job_id'] for j in skipped_jobs]}")
            
        if not killed_jobs and not skipped_jobs:
            response["message"] = "No running or queued jobs found"
            
        return jsonify(response), 200
    except Exception as e:
        logger.error(f"Kill all jobs failed: {e}")
        return jsonify({"error": str(e)}), 500


# ==================== Enclosure Management APIs ====================

@admin_bp.route("/api/admin/enclosures", methods=["GET", "POST"])
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
                if not expander_sas_address.startswith("0x") or len(expander_sas_address) < 3 or not all(c in "0123456789abcdefABCDEF" for c in expander_sas_address[2:]):
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
                if len(enclosures) >= MAX_ENCODSURES:
                    return jsonify({"error": f"Maximum number of enclosures ({MAX_ENCODSURES}) reached"}), 400
                
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

                # Validate custom_labels and custom_roles (Rule #83)
                if not isinstance(custom_labels, dict):
                    return jsonify({"error": "custom_labels must be a dictionary"}), 400
                if not isinstance(custom_roles, dict):
                    return jsonify({"error": "custom_roles must be a dictionary"}), 400
                if len(custom_labels) > MAX_SLOTS_PER_ENCLOSURE:
                    return jsonify({"error": f"Custom labels count exceeds maximum ({MAX_SLOTS_PER_ENCLOSURE})"}), 400
                if len(custom_roles) > MAX_SLOTS_PER_ENCLOSURE:
                    return jsonify({"error": f"Custom roles count exceeds maximum ({MAX_SLOTS_PER_ENCLOSURE})"}), 400

                # Validate custom label and role keys are strings (type mismatch fix)
                for slot_num in custom_labels.keys():
                    if not isinstance(slot_num, str):
                        return jsonify({"error": f"Custom label key must be a string, got {type(slot_num).__name__}"}), 400
                for slot_num in custom_roles.keys():
                    if not isinstance(slot_num, str):
                        return jsonify({"error": f"Custom role key must be a string, got {type(slot_num).__name__}"}), 400

                # Validate custom label content (Rule #86)
                for slot_num, label in custom_labels.items():
                    if not isinstance(label, str):
                        return jsonify({"error": f"Custom label for slot {slot_num} must be a string"}), 400
                    if len(label) > 100:
                        return jsonify({"error": f"Custom label for slot {slot_num} exceeds maximum length (100)"}), 400
                    if any(ord(c) < 32 for c in label):
                        return jsonify({"error": f"Custom label for slot {slot_num} contains invalid characters"}), 400

                # Validate custom role values against allowlist (Rule #87)
                VALID_ROLES = {"wipe", "os", "reserved"}
                for slot_num, role in custom_roles.items():
                    if role not in VALID_ROLES:
                        return jsonify({"error": f"Invalid role '{role}' for slot {slot_num}. Must be one of: {', '.join(sorted(VALID_ROLES))}"}), 400

                if auto_map_slots:
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

                        # Auto-detect SAS/SATA mapping from master map
                        sas_mapping = _auto_detect_mapping(
                            master_map, pci_controller, expander_sas_address, physical_slot, "sas"
                        )
                        if sas_mapping:
                            slot_data["mappings"]["sas_sata"] = sas_mapping

                        # Auto-detect NVMe mapping for hybrid slots
                        if slot_index in hybrid_slots and nvme_start_slot is not None:
                            nvme_offset = hybrid_slots.index(slot_index)
                            nvme_slot_num = int(nvme_start_slot) + nvme_offset
                            nvme_mapping = _auto_detect_mapping(
                                master_map, pci_controller, None, nvme_slot_num, "nvme"
                            )
                            if nvme_mapping:
                                slot_data["mappings"]["nvme"] = nvme_mapping

                        enclosure["slots"][slot_key] = slot_data
                
                # Save to bay_map.json
                bay_map.setdefault("enclosures", {})[payload["id"]] = enclosure
                save_bay_map(bay_map, config_dir)
            
            logger.info(f"Created enclosure: {payload['id']}")
            return jsonify({"status": "success", "enclosure": enclosure}), 201
            
        except Exception as e:
            logger.error(f"Error creating enclosure: {e}")
            return jsonify({"error": str(e)}), 500


@admin_bp.route("/api/admin/enclosures/<enclosure_id>", methods=["GET", "PUT", "DELETE"])
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
                    if not expander_sas_address.startswith("0x") or len(expander_sas_address) < 3 or not all(c in "0123456789abcdefABCDEF" for c in expander_sas_address[2:]):
                        return jsonify({"error": f"Invalid expander SAS address format: {expander_sas_address}"}), 400
            
            # Validate custom_labels and custom_roles if provided (Rule #83)
            custom_labels = payload.get("custom_labels", {})
            custom_roles = payload.get("custom_roles", {})
            if not isinstance(custom_labels, dict):
                return jsonify({"error": "custom_labels must be a dictionary"}), 400
            if not isinstance(custom_roles, dict):
                return jsonify({"error": "custom_roles must be a dictionary"}), 400
            if len(custom_labels) > MAX_SLOTS_PER_ENCLOSURE:
                return jsonify({"error": f"Custom labels count exceeds maximum ({MAX_SLOTS_PER_ENCLOSURE})"}), 400
            if len(custom_roles) > MAX_SLOTS_PER_ENCLOSURE:
                return jsonify({"error": f"Custom roles count exceeds maximum ({MAX_SLOTS_PER_ENCLOSURE})"}), 400
            
            # Validate custom label and role keys are strings (type mismatch fix)
            for slot_num in custom_labels.keys():
                if not isinstance(slot_num, str):
                    return jsonify({"error": f"Custom label key must be a string, got {type(slot_num).__name__}"}), 400
            for slot_num in custom_roles.keys():
                if not isinstance(slot_num, str):
                    return jsonify({"error": f"Custom role key must be a string, got {type(slot_num).__name__}"}), 400
            
            # Validate custom label content (Rule #86)
            for slot_num, label in custom_labels.items():
                if not isinstance(label, str):
                    return jsonify({"error": f"Custom label for slot {slot_num} must be a string"}), 400
                if len(label) > 100:
                    return jsonify({"error": f"Custom label for slot {slot_num} exceeds maximum length (100)"}), 400
                if any(ord(c) < 32 for c in label):
                    return jsonify({"error": f"Custom label for slot {slot_num} contains invalid characters"}), 400
            
            # Validate custom role values against allowlist (Rule #87)
            VALID_ROLES = {"wipe", "os", "reserved"}
            for slot_num, role in custom_roles.items():
                if role not in VALID_ROLES:
                    return jsonify({"error": f"Invalid role '{role}' for slot {slot_num}. Must be one of: {', '.join(sorted(VALID_ROLES))}"}), 400
            
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
            
            logger.info(f"Deleted enclosure: {enclosure_id}")
            return jsonify({"status": "success", "message": f"Enclosure {enclosure_id} deleted"}), 200
            
        except Exception as e:
            logger.error(f"Error deleting enclosure: {e}")
            return jsonify({"error": str(e)}), 500


@admin_bp.route("/api/admin/enclosures/<enclosure_id>/slots", methods=["POST"])
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


@admin_bp.route("/api/admin/enclosures/<enclosure_id>/slots/<slot_num>", methods=["PUT", "DELETE"])
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


@admin_bp.route("/api/admin/enclosures/<enclosure_id>/slots/<slot_num>/mappings/<mapping_type>", methods=["PUT", "DELETE"])
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


@admin_bp.route("/api/admin/templates", methods=["GET", "POST"])
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


@admin_bp.route("/api/admin/templates/<template_id>", methods=["PUT", "DELETE"])
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
            # Check if template is in use by any enclosure (read from bay_map.json)
            with BAY_MAP_LOCK:
                bay_map = load_bay_map(config_dir)
                enclosures = bay_map.get("enclosures", {})
                for enc_id, enc_data in enclosures.items():
                    if enc_data.get("template_id") == template_id:
                        return jsonify({"error": f"Template is in use by enclosure: {enc_id}"}), 400
            
            # Delete from layout_templates.json
            with TEMPLATES_LOCK:
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


@admin_bp.route("/api/admin/master-slot-map", methods=["GET"])
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


def _auto_detect_mapping(master_map, pci_controller, expander_sas_address, slot_num, interface_type):
    """Auto-detect hardware identifier from master map for a given slot.
    
    Args:
        master_map: Master slot map from generate_master_slot_map()
        pci_controller: PCI address of the controller
        expander_sas_address: SAS expander address (null for direct/NVMe)
        slot_num: Physical slot number
        interface_type: "sas" or "nvme"
        
    Returns:
        Mapping dictionary or None if not found
    """
    for entry in master_map:
        if (entry["pci_controller"] == pci_controller and
            entry["physical_slot_number"] == slot_num):
            
            # Match SAS devices (both expander and direct)
            if interface_type == "sas":
                # Match entries with SAS slot types (sas_expander, sas_direct, motherboard_sata)
                if entry["slot_type"] in ("sas_expander", "sas_direct", "motherboard_sata"):
                    # For expander connections, verify expander address matches
                    if entry["slot_type"] == "sas_expander":
                        if entry["expander_sas_address"] == expander_sas_address:
                            return {
                                "slot_type": entry["slot_type"],
                                "hardware_identifier": entry["hardware_identifier"],
                                "auto_detected": True
                            }
                    # For direct/motherboard connections, expander_sas_address should be None
                    else:
                        if expander_sas_address is None:
                            return {
                                "slot_type": entry["slot_type"],
                                "hardware_identifier": entry["hardware_identifier"],
                                "auto_detected": True
                            }
            
            # Match NVMe slots (no expander)
            elif interface_type == "nvme":
                if entry["slot_type"] == "pcie_nvme" and entry["expander_sas_address"] is None:
                    return {
                        "slot_type": entry["slot_type"],
                        "hardware_identifier": entry["hardware_identifier"],
                        "auto_detected": True
                    }
    
    return None
