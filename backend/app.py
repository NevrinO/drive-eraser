# --- START OF FILE backend/app.py ---
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
import shutil
import socket
import logging
from logging.handlers import RotatingFileHandler

from common import (
    get_data_dir, get_config_dir, load_policy, get_db_path, get_cert_dir,
    get_logs_dir, get_active_logs_dir, get_failed_logs_dir,
    purge_old_logs, save_policy, save_bay_map
)
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

from disk_ops import (
    discover_drives, get_smart_data, format_capacity_bytes, 
    get_raw_smart_diagnostics, get_os_by_path
)

def setup_application_logging():
    try:
        logs_dir = get_logs_dir()
        log_file = os.path.join(logs_dir, "app.log")
        handler = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=3)
        formatter = logging.Formatter('%(asctime)s %(levelname)s [%(name)s] %(message)s')
        handler.setFormatter(formatter)
        
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(handler)
        
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)
    except Exception as e:
        print(f"Failed to setup file logging: {str(e)}", file=sys.stderr)

setup_application_logging()
logger = logging.getLogger("app")

app = Flask(__name__)
CORS(app)

ERASE_JOBS = {}
ERASE_JOBS_LOCK = Lock()

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(PROJECT_ROOT, "frontend")

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0)
        s.connect(('8.8.8.8', 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def calculate_session_token(passphrase):
    return hmac.new(passphrase.encode('utf-8'), b"dws_admin_session", hashlib.sha256).hexdigest()

def is_localhost(ip):
    return ip in ("127.0.0.1", "::1", "localhost")

@app.before_request
def security_gate():
    if not request.path.startswith("/api/"):
        return None
    if request.path in ("/api/auth/verify", "/api/status"):
        return None
    if is_localhost(request.remote_addr):
        return None
        
    policy = load_policy()
    lan_passphrase = policy.get("lan_passphrase", "eraser123")
    
    expected_token = calculate_session_token(lan_passphrase)
    cookie_token = request.cookies.get("admin_session")
    
    if cookie_token == expected_token:
        return None
        
    return jsonify({"authenticated": False, "message": "Authentication required for remote network access."}), 401

def get_ram_usage():
    try:
        with open("/proc/meminfo", "r") as f:
            lines = f.readlines()
        mem_total = 0
        mem_available = 0
        for line in lines:
            if line.startswith("MemTotal:"):
                mem_total = int(line.split()[1])
            elif line.startswith("MemAvailable:"):
                mem_available = int(line.split()[1])
        if mem_total > 0:
            used = mem_total - mem_available
            return round((used / mem_total) * 100, 1)
    except Exception:
        pass
    return 0.0

def get_cpu_usage():
    try:
        load = os.getloadavg()[0]
        cores = os.cpu_count() or 1
        return round(min(100.0, (load / cores) * 100.0), 1)
    except Exception:
        pass
    return 0.0

def get_system_uptime():
    try:
        with open("/proc/uptime", "r") as f:
            uptime_seconds = float(f.readline().split()[0])
        days = int(uptime_seconds // 86400)
        hours = int((uptime_seconds % 86400) // 3600)
        minutes = int((uptime_seconds % 3600) // 60)
        if days > 0:
            return f"{days}d {hours}h {minutes}m"
        return f"{hours}h {minutes}m"
    except Exception:
        pass
    return "Unknown"

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

    # Absolute dynamic hard-stop backend safety locks
    os_dev_node, os_by_path = get_os_by_path()
    configured_path = selected_drive.get("configured_by_path")
    resolved_path = selected_drive.get("resolved_by_path")
    configured_path_nvme = selected_drive.get("configured_by_path_nvme")
    resolved_path_nvme = selected_drive.get("resolved_by_path_nvme")

    if os_dev_node and device and os.path.realpath(device) == os.path.realpath(os_dev_node):
        return None, {"error": f"Device {device} is the active host OS drive and cannot be erased!"}, 403

    for path in [configured_path, resolved_path, configured_path_nvme, resolved_path_nvme]:
        if path and os_by_path and (path == os_by_path or os.path.basename(path) == os.path.basename(os_by_path)):
            return None, {"error": f"Device path {path} is the active host OS drive and cannot be erased!"}, 403

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

def get_device_sectors_written(device):
    try:
        dev_name = os.path.basename(device)
        stat_path = f"/sys/block/{dev_name}/stat"
        if os.path.exists(stat_path):
            with open(stat_path, "r") as f:
                content = f.read().strip()
            parts = content.split()
            if len(parts) >= 7:
                return int(parts[6])
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

def prepare_erase_command(device, interface_type, method):
    selected_method = str(method or "").strip().lower()
    iface = str(interface_type or "").strip().lower()

    if selected_method == "overwrite":
        dd_cmd = resolve_verify_command_path("dd", "DRIVE_ERASER_DD_PATH", "dd", ["/usr/bin/dd", "/bin/dd"])
        if not dd_cmd:
            return {"ok": False, "error": "dd_not_available"}
        return {"ok": True, "command": [dd_cmd, "if=/dev/zero", f"of={device}", "bs=16M", "status=none", "oflag=direct"]}

    if selected_method in {"secure_erase", "enhanced_secure_erase"}:
        hdparm_cmd = resolve_verify_command_path("hdparm", "DRIVE_ERASER_HDPARM_PATH", "hdparm", ["/usr/sbin/hdparm", "/usr/bin/hdparm", "/bin/hdparm"])
        if not hdparm_cmd:
            return {"ok": False, "error": "hdparm_not_available"}
        user_password = "wipestation"
        erase_flag = "--security-erase-enhanced" if selected_method == "enhanced_secure_erase" else "--security-erase"
        set_pass_cmd = [hdparm_cmd, "--user-master", "u", "--security-set-pass", user_password, device]
        erase_cmd = [hdparm_cmd, "--user-master", "u", erase_flag, user_password, device]
        return {"ok": True, "command": erase_cmd}

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
            
            active_log_path = os.path.join(get_active_logs_dir(), f"job-{job_id}.log")
            failed_log_path = os.path.join(get_failed_logs_dir(), f"failed-job-{job_id}-bay{job['request']['bay']}.log")
            if os.path.exists(active_log_path):
                try:
                    os.rename(active_log_path, failed_log_path)
                except Exception:
                    pass
            try:
                with open(failed_log_path, "a", encoding="utf-8") as lf:
                    lf.write(f"\n=== JOB CONFIGURATION FAILURE ===\nError Message: {error_message}\n")
                    dev = job["request"].get("device")
                    if dev:
                        lf.write(get_raw_smart_diagnostics(dev))
            except Exception:
                pass
                
            persist_job(job)
            send_slack_notification(job)
            
            try:
                purge_old_logs(30)
            except Exception:
                pass

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

    active_log_path = os.path.join(get_active_logs_dir(), f"job-{job_id}.log")
    try:
        log_file = open(active_log_path, "w", encoding="utf-8", buffering=1)
        log_file.write(f"=== Sanitization Job Started: {datetime.now(timezone.utc).isoformat()} ===\n")
        log_file.write(f"Target Device: {device}\n")
        log_file.write(f"Wipe Method: {method}\n")
        log_file.write(f"Command Invocation: {' '.join(command)}\n\n")
        log_file.flush()
    except Exception as e:
        finalize_failed_job(job_id, f"log_file_creation_failed: {str(e)}")
        return

    try:
        process = subprocess.Popen(
            ["sudo"] + command,
            stdout=log_file,
            stderr=log_file,
            text=True
        )
    except Exception as e:
        log_file.close()
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

    exit_code = process.returncode
    execution_ok = (exit_code == 0)

    log_file.flush()
    log_file.close()

    try:
        with open(active_log_path, "r", encoding="utf-8") as lf:
            stdout_content = lf.read()
    except Exception:
        stdout_content = "Failed to extract execution stream log content."

    # Intercept expected ENOSPC termination of dd raw overwrites
    if method == "overwrite" and exit_code == 1:
        if "no space left on device" in stdout_content.lower():
            execution_ok = True

    execution = {
        "ok": execution_ok,
        "command": " ".join(command),
        "stdout": stdout_content,
        "stderr": "",
        "exit_code": exit_code
    }

    if method in {"crypto", "block"}:
        firmware_complete = False
        poll_start_time = datetime.now(timezone.utc)
        max_poll_seconds = 1200 if method == "crypto" else 7200
        
        time.sleep(5)
        
        consecutive_errors = 0
        max_consecutive_errors = 15
        
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
                    consecutive_errors = 0
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
                    consecutive_errors += 1
                    if consecutive_errors >= max_consecutive_errors:
                        break
                    phase_text = f"Polling drive (reconnecting... {consecutive_errors}/{max_consecutive_errors})"
                    progress_pct = min(99.9, (elapsed_poll / (30.0 if method == "crypto" else 300.0)) * 100.0)
            else:
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

        if verification.get("ok"):
            if not execution.get("ok"):
                warnings_list = job.get("result", {}).get("warnings", [])
                if not isinstance(warnings_list, list):
                    warnings_list = []
                warnings_list.append(f"Initiation process returned non-zero code ({execution.get('exit_code')}), but hardware-level sanitization status verified successfully.")
                job["result"]["warnings"] = warnings_list

            marker_result = write_marker_and_verify(job)
            job["marker"] = marker_result
            
            if os.path.exists(active_log_path):
                try:
                    os.remove(active_log_path)
                except Exception:
                    pass
                    
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
            
            failed_log_path = os.path.join(get_failed_logs_dir(), f"failed-job-{job_id}-bay{job['request']['bay']}.log")
            if os.path.exists(active_log_path):
                try:
                    os.rename(active_log_path, failed_log_path)
                except Exception:
                    pass
            try:
                smart_diagnostics = get_raw_smart_diagnostics(device)
                with open(failed_log_path, "a", encoding="utf-8") as lf:
                    lf.write("\n=== WIPE ATTESTATION FAILURE ===\n")
                    lf.write(f"Failure Attestation Message: {job['error']}\n")
                    lf.write(smart_diagnostics)
            except Exception:
                pass

            try:
                job["certificate"] = build_certificate(job)
            except Exception as e:
                job["error"] = f"{job['error']} (and failure_certificate_generation_failed:{e})"
                job["certificate"] = None

        persist_job(job)

    send_slack_notification(job)
    
    try:
        purge_old_logs(30)
    except Exception:
        pass

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
            return jsonify({"status": "success", "message": "Test webhook dispatched successfully."}), 200
        return jsonify({"error": f"Slack returned status code {resp_code}"}), 400
    except Exception as e:
        return jsonify({"error": f"Failed to send webhook: {str(e)}"}), 500

@app.route("/api/admin/export-csv")
def export_csv_ledger():
    try:
        import io
        import csv
        
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

@app.route("/api/admin/support-bundle")
def download_support_bundle():
    try:
        import tarfile
        import socket
        
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
        
        return send_file(
            tar_path,
            mimetype="application/gzip",
            as_attachment=True,
            download_name=f"{bundle_name}.tar.gz"
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/unmapped-drives")
def get_unmapped_drives():
    try:
        config_dir = get_config_dir()
        bay_map_path = os.path.join(config_dir, "bay_map.json")
        with open(bay_map_path, "r", encoding="utf-8") as f:
            bay_map = json.load(f)
            
        mapped_paths = set()
        for config in bay_map.values():
            # Exclude both mapped SAS/SATA paths and mapped PCIe NVMe paths from unmapped selections
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

# --- backend/app.py ---

@app.route("/api/admin/auto-detect-bays", methods=["POST"])
def auto_detect_bays():
    try:
        config_dir = get_config_dir()
        bay_map_path = os.path.join(config_dir, "bay_map.json")
        
        try:
            with open(bay_map_path, "r", encoding="utf-8") as f:
                bay_map = json.load(f)
        except Exception:
            bay_map = {}

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

        save_bay_map(bay_map, config_dir)
        
        return jsonify({
            "status": "success",
            "message": f"Successfully mapped {len(discovered_slots)} physical backplane slot(s). Updated {updates_count} bay(s).",
            "bay_map": bay_map
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
            
        for bay_id, conf in payload.items():
            if not isinstance(conf, dict):
                return jsonify({"error": f"Configuration for {bay_id} must be a dictionary."}), 400
                
        save_bay_map(payload, config_dir)
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
            return jsonify({"status": "success", "message": "System policies updated successfully."}), 200
        except Exception as e:
            return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    config_dir = get_config_dir()
    policy = load_policy(config_dir)
    bind_address = policy.get("bind_address", "127.0.0.1")
    port = int(policy.get("port", 5000))
    init_wipe_db()
    logger.info(f"Drive Wipe Station starting on {bind_address}:{port} (config_dir={config_dir})")
    app.run(host=bind_address, port=port, debug=False)
# --- END OF FILE backend/app.py ---