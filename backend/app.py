from flask import Flask, jsonify, request, send_from_directory, send_file
from flask_cors import CORS
import sys
import os
import json
import sqlite3
import re
import html
import subprocess
import hmac
import hashlib
import time
from datetime import datetime, timezone
from threading import Lock, Thread
import urllib.request
import uuid

# Flat sibling imports directly within the backend directory
from common import get_config_dir, load_policy, get_db_path, get_cert_dir
from database import init_wipe_db, persist_job
from verification import (
    verification_for_method,
    write_marker_and_verify,
    verify_sata_sanitize,
    verify_nvme_sanitize,
    verify_sas_block,
    resolve_verify_command_path
)
from certificates import build_certificate
from notifier import send_slack_notification

from disk_ops import discover_drives, get_smart_data, format_capacity_bytes

app = Flask(__name__)
CORS(app)

ERASE_JOBS = {}
ERASE_JOBS_LOCK = Lock()

# Restored correct path depth evaluation for frontend assets
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(PROJECT_ROOT, "frontend")

# --- SANITIZATION ORDER & VALIDATION HELPERS ---

def build_recommended_method(drive, policy):
    interface_type = (drive.get("interface_type") or "unknown").lower()
    supported_methods = drive.get("supported_methods") or []
    method_priority = policy.get("method_priority") or {}
    prioritized = method_priority.get(interface_type, [])
    for method in prioritized:
        if method in supported_methods:
            return method
    if "overwrite" in supported_methods:
        return "overwrite"
    return supported_methods[0] if supported_methods else None

def validate_single_bay(technician, ticket_number, bay, method_override, drives, policy):
    selected_drive = None
    for drive in drives:
        if str(drive.get("bay") or "").strip().lower() == bay:
            selected_drive = drive
            break

    if not selected_drive:
        return None, {"error": f"bay not found: {bay}"}, 404
    if selected_drive.get("locked"):
        return None, {"error": f"bay is protected and cannot be erased: {bay}"}, 403
    if selected_drive.get("role") in {"os", "reserved"}:
        return None, {"error": f"bay role is not erasable: {bay}"}, 403
    if not selected_drive.get("present"):
        return None, {"error": f"no drive present in bay: {bay}"}, 409

    device = selected_drive.get("device")
    if not device:
        return None, {"error": f"drive device could not be resolved for bay: {bay}"}, 409

    supported_methods = selected_drive.get("supported_methods") or []
    recommended_method = build_recommended_method(selected_drive, policy)
    chosen_method = str(method_override).strip().lower() if method_override else None

    if chosen_method:
        if chosen_method not in supported_methods:
            return None, {"error": f"method not supported by drive in {bay}: {chosen_method}"}, 400
        if not policy.get("allow_method_override", True) and recommended_method and chosen_method != recommended_method:
            return None, {"error": "method override is disabled by policy"}, 403
    else:
        chosen_method = recommended_method

    if not chosen_method:
        return None, {"error": f"no supported erase method available for bay: {bay}"}, 409

    return {
        "technician": technician,
        "ticket_number": ticket_number,
        "bay": bay,
        "device": device,
        "method": chosen_method,
        "recommended_method": recommended_method,
        "supported_methods": supported_methods,
        "drive": selected_drive,
    }, None, None

def create_erase_job(validated):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": str(uuid.uuid4()),
        "friendly_id": None,
        "status": "queued",
        "created_at": now,
        "started_at": None,
        "finished_at": None,
        "error": None,
        "result": None,
        "verification": None,
        "marker": None,
        "certificate": None,
        "progress_percent": 0.0,
        "current_phase": "Queued in Line",
        "request": {
            "technician": validated["technician"],
            "ticket_number": validated["ticket_number"],
            "bay": validated["bay"],
            "device": validated["device"],
            "method": validated["method"],
            "recommended_method": validated["recommended_method"],
            "supported_methods": validated["supported_methods"],
            "interface_type": validated["drive"].get("interface_type"),
            "serial": validated["drive"].get("serial"),
            "model": validated["drive"].get("model"),
            "capacity_bytes": validated["drive"].get("smart", {}).get("capacity_bytes") or (100 * 1024 * 1024 * 1024),
            "data_written_at_wipe": None,
        },
    }

# --- PROGRESS HARVESTING UTILITIES ---

def get_device_sectors_written(device):
    try:
        dev_name = os.path.basename(device)
        stat_path = f"/sys/block/{dev_name}/stat"
        if os.path.exists(stat_path):
            with open(stat_path, "r") as f:
                content = f.read().strip()
            parts = content.split()
            if len(parts) >= 7:
                return int(parts[6])  # 7th column representing write sectors
    except Exception:
        pass
    return None

def poll_nvme_sanitize_progress(device):
    try:
        nvme_path = resolve_verify_command_path("nvme", "DRIVE_ERASER_NVME_PATH", "nvme", ["/usr/sbin/nvme", "/usr/bin/nvme", "/bin/nvme"])
        if nvme_path:
            result = subprocess.run(["sudo", nvme_path, "sanitize-log", device], capture_output=True, text=True)
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if "sprog" in line.lower():
                        match = re.search(r"sprog\s*[:=]\s*(\d+)", line, re.IGNORECASE)
                        if match:
                            return int(match.group(1))
    except Exception:
        pass
    return None

def poll_sas_sanitize_progress(device):
    try:
        sg_req_path = resolve_verify_command_path("sg_requests", "DRIVE_ERASER_SG_REQUESTS_PATH", "sg_requests", ["/usr/bin/sg_requests", "/usr/sbin/sg_requests"])
        if sg_req_path:
            result = subprocess.run(["sudo", sg_req_path, "--progress", device], capture_output=True, text=True)
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if "progress" in line.lower():
                        match = re.search(r"(\d+\.?\d*)\s*%", line)
                        if match:
                            return float(match.group(1))
    except Exception:
        pass
    return None

def poll_sata_sanitize_progress(device):
    try:
        hdparm_path = resolve_verify_command_path("hdparm", "DRIVE_ERASER_HDPARM_PATH", "hdparm", ["/usr/sbin/hdparm", "/usr/bin/hdparm"])
        if hdparm_path:
            result = subprocess.run(["sudo", hdparm_path, "--sanitize-status", device], capture_output=True, text=True)
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if "progress" in line.lower() or "percent" in line.lower():
                        match = re.search(r"(\d+\.?\d*)\s*%", line)
                        if match:
                            return float(match.group(1))
    except Exception:
        pass
    return None

# --- COMMAND INITIATION & CONCURRENCY CONTROLLERS ---

def prepare_erase_command(device, interface_type, method):
    selected_method = str(method or "").strip().lower()
    iface = str(interface_type or "").strip().lower()

    if selected_method == "overwrite":
        dd_cmd = resolve_verify_command_path("dd", "DRIVE_ERASER_DD_PATH", "dd", ["/usr/bin/dd", "/bin/dd"])
        if not dd_cmd:
            return {"ok": False, "error": "dd_not_available"}
        return {"ok": True, "command": [dd_cmd, "if=/dev/zero", f"of={device}", "bs=16M", "status=none", "oflag=direct"]}

    # Legacy security erase commands (Requires temporary drive passwords)
    if selected_method in {"secure_erase", "enhanced_secure_erase"}:
        hdparm_cmd = resolve_verify_command_path("hdparm", "DRIVE_ERASER_HDPARM_PATH", "hdparm", ["/usr/sbin/hdparm", "/usr/bin/hdparm", "/bin/hdparm"])
        if not hdparm_cmd:
            return {"ok": False, "error": "hdparm_not_available"}
        user_password = "wipestation"
        erase_flag = "--security-erase-enhanced" if selected_method == "enhanced_secure_erase" else "--security-erase"
        set_pass_cmd = [hdparm_cmd, "--user-master", "u", "--security-set-pass", user_password, device]
        erase_cmd = [hdparm_cmd, "--user-master", "u", erase_flag, user_password, device]
        return {"ok": True, "command": erase_cmd}

    # Modern native firmware sanitize commands (Passwordless)
    if selected_method in {"block", "crypto"}:
        if iface == "nvme":
            nvme_cmd = resolve_verify_command_path("nvme", "DRIVE_ERASER_NVME_PATH", "nvme", ["/usr/sbin/nvme", "/usr/bin/nvme", "/bin/nvme"])
            if not nvme_cmd:
                return {"ok": False, "error": "nvme_not_available"}
            action = "crypto" if selected_method == "crypto" else "block"
            return {"ok": True, "command": [nvme_cmd, "sanitize", device, "-a", action]}
            
        if iface == "sata":
            hdparm_cmd = resolve_verify_command_path("hdparm", "DRIVE_ERASER_HDPARM_PATH", "hdparm", ["/usr/sbin/hdparm", "/usr/bin/hdparm", "/bin/hdparm"])
            if not hdparm_cmd:
                return {"ok": False, "error": "hdparm_not_available"}
            action = "--sanitize-crypto-scramble" if selected_method == "crypto" else "--sanitize-block-erase"
            return {"ok": True, "command": [hdparm_cmd, "--yes-i-know-what-i-am-doing", action, device]}

        if iface == "sas":
            sg_sanitize_cmd = resolve_verify_command_path("sg_sanitize", "DRIVE_ERASER_SG_SANITIZE_PATH", "sg_sanitize", ["/usr/bin/sg_sanitize", "/usr/sbin/sg_sanitize", "/bin/sg_sanitize"])
            if not sg_sanitize_cmd:
                return {"ok": False, "error": "sg_sanitize_not_available"}
            return {"ok": True, "command": [sg_sanitize_cmd, "--block", device]}

    return {"ok": False, "error": f"unsupported_method_or_interface:{selected_method}:{iface}"}

def finalize_failed_job(job_id, error_message):
    with ERASE_JOBS_LOCK:
        job = ERASE_JOBS.get(job_id)
        if job:
            job["status"] = "failed"
            job["finished_at"] = datetime.now(timezone.utc).isoformat()
            job["error"] = error_message
            persist_job(job)
            send_slack_notification(job)

def run_erase_job(job_id):
    with ERASE_JOBS_LOCK:
        job = ERASE_JOBS.get(job_id)
        if not job:
            return
        job["status"] = "running"
        job["started_at"] = datetime.now(timezone.utc).isoformat()
        job["progress_percent"] = 0.0
        job["current_phase"] = "Initializing Sanitization"
        persist_job(job)

    send_slack_notification(job, "running")

    device = job["request"]["device"]
    interface_type = job["request"]["interface_type"]
    method = job["request"]["method"]
    capacity_bytes = job["request"].get("capacity_bytes") or (100 * 1024 * 1024 * 1024)

    # 1. First-Stage: Apply and verify password synchronously on SATA configurations
    if method in {"secure_erase", "enhanced_secure_erase"} and interface_type == "sata":
        hdparm_cmd = resolve_verify_command_path("hdparm", "DRIVE_ERASER_HDPARM_PATH", "hdparm", ["/usr/sbin/hdparm", "/usr/bin/hdparm", "/bin/hdparm"])
        if not hdparm_cmd:
            finalize_failed_job(job_id, "hdparm_not_available")
            return
        
        user_password = "wipestation"
        set_pass_cmd = ["sudo", hdparm_cmd, "--user-master", "u", "--security-set-pass", user_password, device]
        try:
            set_pass_proc = subprocess.run(set_pass_cmd, capture_output=True, text=True)
            if set_pass_proc.returncode != 0:
                err_msg = set_pass_proc.stderr.strip() or "set_password_failed"
                if "frozen" in set_pass_proc.stdout.lower() or "frozen" in set_pass_proc.stderr.lower():
                    err_msg = "SATA drive is FROZEN by BIOS. Suspend-to-RAM or hot-plug SATA power to unfreeze."
                finalize_failed_job(job_id, f"security_set_password_failed: {err_msg}")
                return
        except Exception as e:
            finalize_failed_job(job_id, f"security_set_password_exception: {str(e)}")
            return

        erase_flag = "--security-erase-enhanced" if method == "enhanced_secure_erase" else "--security-erase"
        command = [hdparm_cmd, "--user-master", "u", erase_flag, user_password, device]
    else:
        cmd_result = prepare_erase_command(device, interface_type, method)
        if not cmd_result.get("ok"):
            finalize_failed_job(job_id, cmd_result.get("error") or "prepare_command_failed")
            return
        command = cmd_result["command"]

    initial_sectors = None
    if method == "overwrite":
        initial_sectors = get_device_sectors_written(device)

    # 2. Second-Stage: Spawn sanitize routine in background
    try:
        process = subprocess.Popen(
            ["sudo"] + command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
    except Exception as e:
        finalize_failed_job(job_id, f"process_spawn_failed:{str(e)}")
        return

    start_time = datetime.now(timezone.utc)
    estimated_seconds = 600

    while process.poll() is None:
        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
        progress = 0.0
        phase = "Sanitizing Drive..."

        if method == "overwrite":
            current_sectors = get_device_sectors_written(device)
            if current_sectors is not None and initial_sectors is not None:
                delta_sectors = max(0, current_sectors - initial_sectors)
                wrote_bytes = delta_sectors * 512
                progress = min(99.9, (wrote_bytes / capacity_bytes) * 100)
                phase = f"Writing zeroes ({progress:.1f}%)"
            else:
                progress = min(99.9, (elapsed / (capacity_bytes / (50 * 1024 * 1024))) * 100)
                phase = f"Overwriting blocks ({progress:.1f}%)"

        elif method in {"crypto", "block"} and interface_type == "nvme":
            sprog_val = poll_nvme_sanitize_progress(device)
            if sprog_val is not None:
                progress = min(99.9, (sprog_val / 65535.0) * 100)
                phase = f"NVMe controller sanitize ({progress:.1f}%)"
            else:
                progress = min(99.9, (elapsed / 60.0) * 100)
                phase = "NVMe Sanitize in progress..."

        elif method in {"secure_erase", "enhanced_secure_erase"} and interface_type == "sata":
            prog_val = poll_sata_sanitize_progress(device)
            if prog_val is not None:
                progress = min(99.9, prog_val)
                phase = f"SATA sanitize active ({progress:.1f}%)"
            else:
                progress = min(99.9, (elapsed / estimated_seconds) * 100)
                phase = f"SATA Secure Erase running ({progress:.1f}%)"

        elif method == "block" and interface_type == "sas":
            prog_val = poll_sas_sanitize_progress(device)
            if prog_val is not None:
                progress = min(99.9, prog_val)
                phase = f"SAS firmware sanitizing ({progress:.1f}%)"
            else:
                progress = min(99.9, (elapsed / 120.0) * 100)
                phase = "SAS Sanitize running..."

        with ERASE_JOBS_LOCK:
            job = ERASE_JOBS.get(job_id)
            if job:
                job["progress_percent"] = round(progress, 1)
                job["current_phase"] = phase

        time.sleep(3)

    stdout, stderr = process.communicate()
    exit_code = process.returncode
    execution_ok = (exit_code == 0)

    execution = {
        "ok": execution_ok,
        "command": " ".join(command),
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code
    }

    # 3. Third-Stage: For asynchronous sanitize methods, poll drive firmware state until complete or timeout
    if method in {"crypto", "block"}:
        firmware_complete = False
        poll_start_time = datetime.now(timezone.utc)
        max_poll_seconds = 1200 if method == "crypto" else 7200  # 20 mins for crypto, 2 hours for block erase
        
        # Wait briefly to let the link negotiate and settle after initial command execution
        time.sleep(5)
        
        consecutive_errors = 0
        max_consecutive_errors = 15  # Up to 60 seconds of tolerance for bus reconnects/temporary drive resets
        
        while not firmware_complete:
            elapsed_poll = (datetime.now(timezone.utc) - poll_start_time).total_seconds()
            if elapsed_poll > max_poll_seconds:
                break
                
            status_report = None
            progress_pct = 0.0
            phase_text = "Sanitizing in background..."
            
            if interface_type == "sata":
                status_report = verify_sata_sanitize(device, method)
            elif interface_type == "nvme":
                status_report = verify_nvme_sanitize(device, method)
            elif interface_type == "sas":
                status_report = verify_sas_block(device, method)
                
            if status_report:
                if status_report.get("ok"):
                    firmware_complete = True
                    progress_pct = 100.0
                    phase_text = "Sanitization completed"
                    consecutive_errors = 0
                elif status_report.get("error") in {"sata_sanitize_still_in_progress", "nvme_sanitize_still_in_progress", "sas_sanitize_still_in_progress"}:
                    firmware_complete = False
                    consecutive_errors = 0  # Successfully communicated with drive, reset error tracker
                    parsed_pct = None
                    details = status_report.get("details") or {}
                    output_str = str(details.get("output") or "").lower()
                    
                    if "progress:" in output_str:
                        match = re.search(r"progress:\s*(0x[0-9a-fA-F]+|\d+)\s*\(([0-9.]+)%\)", output_str)
                        if match:
                            parsed_pct = float(match.group(2))
                            
                    if parsed_pct is None and interface_type == "sas":
                        prog_val = poll_sas_sanitize_progress(device)
                        if prog_val is not None:
                            parsed_pct = prog_val
                            
                    if parsed_pct is None and interface_type == "nvme":
                        sprog_val = details.get("sprog")
                        if sprog_val is not None:
                            parsed_pct = (sprog_val / 65535.0) * 100.0
                            
                    if parsed_pct is not None:
                        progress_pct = min(99.9, parsed_pct)
                    else:
                        progress_pct = min(99.9, (elapsed_poll / (30.0 if method == "crypto" else 300.0)) * 100.0)
                        
                    phase_text = f"Firmware sanitizing in progress ({progress_pct:.1f}%)"
                else:
                    # Report returned an unrecognized error or query command failed
                    consecutive_errors += 1
                    if consecutive_errors >= max_consecutive_errors:
                        break
                    phase_text = f"Polling drive (reconnecting... {consecutive_errors}/{max_consecutive_errors})"
                    progress_pct = min(99.9, (elapsed_poll / (30.0 if method == "crypto" else 300.0)) * 100.0)
            else:
                # No status report parsed/returned from validation query
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    break
                phase_text = f"Polling drive (no response... {consecutive_errors}/{max_consecutive_errors})"
                progress_pct = min(99.9, (elapsed_poll / (30.0 if method == "crypto" else 300.0)) * 100.0)
                
            with ERASE_JOBS_LOCK:
                job = ERASE_JOBS.get(job_id)
                if job:
                    job["progress_percent"] = round(progress_pct, 1)
                    job["current_phase"] = phase_text
                    
            time.sleep(4)

    # 4. Fourth-Stage: Pause briefly to allow the SATA/SAS link to negotiate and settle down after the drive resets itself
    if method in {"crypto", "block", "secure_erase", "enhanced_secure_erase"}:
        time.sleep(5)

    verification = verification_for_method(
        job["request"]["device"],
        job["request"]["interface_type"],
        job["request"]["method"],
        execution,
    )

    with ERASE_JOBS_LOCK:
        job = ERASE_JOBS.get(job_id)
        if not job:
            return
        job["finished_at"] = datetime.now(timezone.utc).isoformat()
        job["result"] = {
            "command": execution.get("command"),
            "stdout": execution.get("stdout", ""),
            "stderr": execution.get("stderr", ""),
            "exit_code": execution.get("exit_code"),
        }
        job["verification"] = verification

        # Evaluate job success strictly based on the physical verification status
        if verification.get("ok"):
            if not execution.get("ok"):
                warnings_list = job.get("result", {}).get("warnings", [])
                if not isinstance(warnings_list, list):
                    warnings_list = []
                warnings_list.append(f"Initiation process returned non-zero code ({execution.get('exit_code')}), but hardware-level sanitization status verified successfully.")
                job["result"]["warnings"] = warnings_list

            marker_result = write_marker_and_verify(job)
            job["marker"] = marker_result
            try:
                job["certificate"] = build_certificate(job)
                job["status"] = "completed"
                job["error"] = None
            except Exception as e:
                job["status"] = "failed"
                job["error"] = f"certificate_generation_failed:{e}"
                job["certificate"] = None
        else:
            job["status"] = "failed"
            if not execution.get("ok"):
                job["error"] = f"Initiation failed ({execution.get('exit_code')}). Verification report: {verification.get('error') or 'failed'}"
            else:
                job["error"] = verification.get("error") or "erase_verification_failed"
            
            # Record failed sanitization history details inside a clean Failure Certificate
            try:
                job["certificate"] = build_certificate(job)
            except Exception as e:
                job["error"] = f"{job['error']} (and failure_certificate_generation_failed:{e})"
                job["certificate"] = None

        persist_job(job)

    send_slack_notification(job)

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

@app.route("/api/drives")
def get_drives():
    try:
        config_dir = get_config_dir()
        
        # Track currently active or queued device paths to bypass physical query locks
        running_devices = set()
        with ERASE_JOBS_LOCK:
            for job in ERASE_JOBS.values():
                if job.get("status") in {"running", "queued"}:
                    dev = job.get("request", {}).get("device")
                    if dev:
                        running_devices.add(dev)

        # Pass running_devices to prevent blocking queries on busy drives
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
                            
                            # Restore cached physical drive attributes to prevent metadata blackout while busy
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

@app.route("/api/erase/start", methods=["POST"])
def start_erase():
    try:
        payload = request.get_json(silent=True) or {}
        config_dir = get_config_dir()
        policy = load_policy(config_dir)
        
        # Guard Clause: Terminate instantly if strict auditing is requested but keys are missing
        strict_audit = policy.get("strict_audit_mode", False)
        passphrase = policy.get("wipe_passphrase")
        if strict_audit and not passphrase:
            return jsonify({"error": "Configuration Error: strict_audit_mode is enabled, but no wipe_passphrase is configured in policy.json."}), 400

        # Track currently active or queued device paths to bypass physical query locks during startup validation
        running_devices = set()
        with ERASE_JOBS_LOCK:
            for job in ERASE_JOBS.values():
                if job.get("status") in {"running", "queued"}:
                    dev = job.get("request", {}).get("device")
                    if dev:
                        running_devices.add(dev)

        # Pass running_devices to prevent blocking queries on busy drives
        drives = discover_drives(os.path.join(config_dir, "bay_map.json"), running_devices=running_devices)

        # Resolve blank fields with default fallback strings
        technician = str(payload.get("technician") or "").strip()
        ticket_number = str(payload.get("ticket_number") or "").strip()
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

if __name__ == "__main__":
    config_dir = get_config_dir()
    policy = load_policy(config_dir)
    bind_address = policy.get("bind_address", "127.0.0.1")
    port = int(policy.get("port", 5000))
    init_wipe_db()
    print(f"Drive Wipe Station starting on {bind_address}:{port} (config_dir={config_dir})", flush=True)
    app.run(host=bind_address, port=port, debug=False)