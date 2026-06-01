# --- START OF FILE backend/job_management.py ---
import os
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone

# Constants
ESTIMATED_ERASE_TIMEOUT_SECONDS = 600  # Default estimated timeout for erase operations (10 minutes)

from common import (
    get_config_dir, get_active_logs_dir, get_failed_logs_dir,
    purge_old_logs, DEFAULT_LOG_RETENTION_DAYS, load_policy
)
from database import persist_job
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
from disk_ops import get_os_by_path
from smart_parsing import get_raw_smart_diagnostics
from app_config import ERASE_JOBS, ERASE_JOBS_LOCK, logger

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

    # Validate secure mode requirements before proceeding
    strict_audit = policy.get("strict_audit_mode", False)
    if strict_audit:
        if not technician or technician.strip() == "" or technician == "System Operator":
            return None, {"error": "Strict audit mode requires a valid technician name (cannot be empty or 'System Operator')"}, 400
        if not ticket_number or ticket_number.strip() == "" or ticket_number == "INTERNAL":
            return None, {"error": "Strict audit mode requires a valid ticket number (cannot be empty or 'INTERNAL')"}, 400

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
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning(f"Failed to rename active log to failed log: {e}")
            try:
                with open(failed_log_path, "a", encoding="utf-8") as lf:
                    lf.write(f"\n=== JOB CONFIGURATION FAILURE ===\nError Message: {error_message}\n")
                    dev = job["request"].get("device")
                    if dev:
                        lf.write(get_raw_smart_diagnostics(dev))
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Failed to write failure diagnostics to log: {e}")
                
            persist_job(job)
            send_slack_notification(job)
            
            # Emit a high-signal application log representing an initialization failure
            logger.error(f"Job {job_id} (Bay {job['request']['bay']}) initialization failed: {error_message}")
            
            try:
                purge_old_logs(DEFAULT_LOG_RETENTION_DAYS)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Failed to purge old logs: {e}")

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
    capacity_bytes = job["request"].get("capacity_bytes")
    if capacity_bytes is None:
        capacity_bytes = 100 * 1024 * 1024 * 1024

    # High-signal event marking the active beginning of physical wipe commands
    logger.info(f"Job {job_id} (Bay {job['request']['bay']}) transitioning to RUNNING. Method: '{method}', Target: '{device}'")

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

    process = None
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

    try:
        start_time = datetime.now(timezone.utc)
        estimated_seconds = ESTIMATED_ERASE_TIMEOUT_SECONDS

        # Thread sleep telemetry updates loop (contained within individual job context)
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
    finally:
        # Ensure log_file is closed even if an exception occurs
        if 'log_file' in locals() and not log_file.closed:
            log_file.close()

    try:
        with open(active_log_path, "r", encoding="utf-8") as lf:
            stdout_content = lf.read()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to read execution log: {e}")
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

            # Check if post-erase marker is enabled in policy
            policy = load_policy(get_config_dir())
            post_erase_marker = policy.get("post_erase_marker", True)

            if post_erase_marker:
                logger.info(f"Job {job_id} (Bay {job['request']['bay']}) verified successfully. Writing supplemental station marker.")
                marker_result = write_marker_and_verify(job)
                job["marker"] = marker_result
                if not marker_result.get("ok"):
                    warnings_list = job.get("result", {}).get("warnings", [])
                    if not isinstance(warnings_list, list):
                        warnings_list = []
                    warnings_list.append(f"Supplemental station marker failed ({marker_result.get('error') or marker_result.get('status')}); sanitization certification is based on wipe verification evidence.")
                    job["result"]["warnings"] = warnings_list
                    logger.warning(f"Job {job_id} (Bay {job['request']['bay']}) supplemental marker failed: {marker_result.get('error') or marker_result.get('status')}")
            else:
                logger.info(f"Job {job_id} (Bay {job['request']['bay']}) verified successfully. Post-erase marker disabled by policy, skipping marker write.")
                job["marker"] = {"ok": True, "status": "disabled_by_policy", "error": None, "details": {}}
            
            if os.path.exists(active_log_path):
                try:
                    os.remove(active_log_path)
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning(f"Failed to remove active log: {e}")
                    
            try:
                job["certificate"] = build_certificate(job)
                job["status"] = "completed"
                job["error"] = None
                
                # High-signal application events of ultimate success
                logger.info(f"Job {job_id} (Bay {job['request']['bay']}) COMPLETED. Certificate generated, audit record finalized.")
            except Exception as e:
                warnings_list = job.get("result", {}).get("warnings", [])
                if not isinstance(warnings_list, list):
                    warnings_list = []
                warnings_list.append(f"Certificate generation failed: {str(e)}. Sanitization succeeded but audit record could not be finalized.")
                job["result"]["warnings"] = warnings_list
                job["status"] = "completed"
                job["error"] = None
                job["certificate"] = None
                logger.warning(f"Job {job_id} (Bay {job['request']['bay']}) certificate generation failed but sanitization completed: {str(e)}")
        else:
            job["status"] = "failed"
            if not execution.get("ok"):
                job["error"] = f"Initiation failed ({execution.get('exit_code')}). Verification report: {verification.get('error') or 'failed'}"
            else:
                job["error"] = verification.get("error") or "erase_verification_failed"
            
            # High-signal error event written to the global app.log
            logger.error(f"Job {job_id} (Bay {job['request']['bay']}) FAILED: {job['error']}")

            failed_log_path = os.path.join(get_failed_logs_dir(), f"failed-job-{job_id}-bay{job['request']['bay']}.log")
            if os.path.exists(active_log_path):
                try:
                    os.rename(active_log_path, failed_log_path)
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning(f"Failed to rename active log to failed log: {e}")
            try:
                smart_diagnostics = get_raw_smart_diagnostics(device)
                with open(failed_log_path, "a", encoding="utf-8") as lf:
                    lf.write("\n=== WIPE ATTESTATION FAILURE ===\n")
                    lf.write(f"Failure Attestation Message: {job['error']}\n")
                    lf.write(smart_diagnostics)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Failed to write attestation failure diagnostics: {e}")

            try:
                job["certificate"] = build_certificate(job)
            except Exception as e:
                job["error"] = f"{job['error']} (and failure_certificate_generation_failed:{e})"
                job["certificate"] = None

        persist_job(job)

    send_slack_notification(job)

    try:
        purge_old_logs(DEFAULT_LOG_RETENTION_DAYS)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to purge old logs: {e}")
# --- END OF FILE backend/job_management.py ---
