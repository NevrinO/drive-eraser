# Support routes: system metrics, CSV export, support bundle, logo management
# Extracted from admin_routes.py for modularity (fix-plan-G1)
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
import uuid
import sqlite3
import urllib.request
import re
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from flask import Blueprint, jsonify, request, send_file, g
from PIL import Image
from app_config import logger, limiter, get_local_ip
from common import get_config_dir, load_policy, get_data_dir, get_logs_dir, get_failed_logs_dir, get_db_path
from system_metrics import get_ram_usage, get_cpu_usage, get_system_uptime
from disk_utils import format_capacity_bytes
from routes._shared import require_admin_auth, is_valid_device_name, MAX_DEVICES_FOR_BUNDLE

support_bp = Blueprint('support_routes', __name__)


@support_bp.route("/api/admin/metrics")
@require_admin_auth
@limiter.limit("30 per minute")
def get_admin_metrics():
    try:
        total, used, free = shutil.disk_usage(get_data_dir())
        disk_pct = round((used / total) * 100, 1) if total > 0 else 0.0
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

@support_bp.route("/api/admin/test-webhook", methods=["POST"])
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

@support_bp.route("/api/admin/export-csv")
@require_admin_auth
@limiter.limit("10 per minute")
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

@support_bp.after_request
def cleanup_support_bundle(response):
    if request.path == "/api/admin/support-bundle":
        tar_path = getattr(g, 'support_bundle_tar_path', None)
        if tar_path:
            try:
                os.remove(tar_path)
            except FileNotFoundError:
                pass
            except Exception:
                pass
    return response

@support_bp.route("/api/admin/support-bundle")
@require_admin_auth
@limiter.limit("5 per minute")
def download_support_bundle():
    try:
        hostname = socket.gethostname()
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        bundle_name = f"support-bundle-{hostname}-{timestamp}-{uuid.uuid4().hex[:8]}"
        workspace_dir = os.path.join("/tmp", bundle_name)
        os.makedirs(workspace_dir, exist_ok=True)
        
        lsblk_proc = None
        try:
            lsblk_proc = subprocess.run(["sudo", "lsblk", "-J"], capture_output=True, text=True, timeout=10, shell=False)
            with open(os.path.join(workspace_dir, "hardware_environment.txt"), "w") as f:
                f.write("=== LSBLK -J OUTPUT ===\n")
                f.write(lsblk_proc.stdout or "")
                f.write("\n\n=== LSHW STORAGE DETAILS ===\n")
                try:
                    lshw_proc = subprocess.run(["sudo", "lshw", "-class", "storage", "-class", "disk"], capture_output=True, text=True, timeout=15, shell=False)
                    f.write(lshw_proc.stdout or "")
                except subprocess.TimeoutExpired:
                    f.write("lshw timed out after 15 seconds\n")
                except Exception as lshw_err:
                    f.write(f"lshw failed: {lshw_err}\n")
        except Exception as e:
            with open(os.path.join(workspace_dir, "hardware_environment_error.txt"), "w") as f:
                f.write(f"Failed to gather lsblk details: {str(e)}")

        # Parse lsblk JSON to get disk devices and run smartctl -x on each
        # This section is independent of lshw — lshw timeout must not prevent smartctl collection
        lsblk_data = json.loads(lsblk_proc.stdout) if (lsblk_proc and lsblk_proc.stdout) else {}
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
            try:
                with open(policy_path, "r", encoding="utf-8") as f:
                    policy_data = json.load(f)
                for key in ["wipe_passphrase", "slack_webhook_url", "lan_passphrase"]:
                    if key in policy_data:
                        policy_data[key] = "[REDACTED]"
                with open(os.path.join(workspace_dir, "redacted_policy.json"), "w", encoding="utf-8") as f:
                    json.dump(policy_data, f, indent=2)
            except FileNotFoundError:
                pass
        except Exception:
            pass
            
        try:
            logs_dir = get_logs_dir()
            app_log_path = os.path.join(logs_dir, "app.log")
            try:
                shutil.copy(app_log_path, os.path.join(workspace_dir, "app.log"))
            except FileNotFoundError:
                pass

            # Include discovery diagnostic log if it exists
            diag_log_path = os.path.join(logs_dir, "discovery_diag.log")
            try:
                shutil.copy(diag_log_path, os.path.join(workspace_dir, "discovery_diag.log"))
            except FileNotFoundError:
                pass

            # Also include rotated diagnostic log if it exists
            diag_log_rotated = os.path.join(logs_dir, "discovery_diag.log.1")
            try:
                shutil.copy(diag_log_rotated, os.path.join(workspace_dir, "discovery_diag.log.1"))
            except FileNotFoundError:
                pass

            failed_logs_dir = get_failed_logs_dir()
            try:
                shutil.copytree(failed_logs_dir, os.path.join(workspace_dir, "failed_logs"), dirs_exist_ok=True)
            except FileNotFoundError:
                pass
        except Exception:
            pass

        # Capture a point-in-time diagnostic snapshot into the bundle workspace.
        # This works even when discovery_diag is not enabled in policy, because
        # capture_snapshot_text() does not check the enabled flag.
        try:
            from discovery_diag import capture_snapshot_text
            snapshot_text = capture_snapshot_text("support_bundle")
            with open(os.path.join(workspace_dir, "diagnostic_snapshot.txt"), "w", encoding="utf-8") as f:
                f.write(snapshot_text)
        except Exception:
            pass

        tar_path = f"/tmp/{bundle_name}.tar.gz"
        try:
            with tarfile.open(tar_path, "w:gz") as tar:
                tar.add(workspace_dir, arcname=bundle_name)
        finally:
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

@support_bp.route("/api/admin/logo", methods=["GET", "POST", "DELETE"])
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
                with Image.open(file) as img:
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
                try:
                    os.remove(temp_path)
                except FileNotFoundError:
                    pass
                except Exception:
                    pass
                return jsonify({"error": f"Invalid image file: {str(e)}"}), 400
                
        except Exception as e:
            logger.error(f"Logo upload failed: {e}")
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
