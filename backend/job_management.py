# --- START OF FILE backend/job_management.py ---
import os
import re
import subprocess
import time
import uuid
import threading
import logging
import json
import sqlite3
from collections import deque
from contextlib import closing
from datetime import datetime, timezone

# Constants
DEFAULT_SATA_ERASE_ESTIMATE_SECONDS = 600  # Default estimated seconds for SATA erase progress calculation
SATA_SECURITY_PASSWORD = "wipestation"  # Used for hdparm security-erase commands

# High #14: Global generation counter for job interruption.
# Uses a monotonically increasing counter instead of a boolean flag to avoid
# the cross-operation reset race (Lesson #101): each operation captures the
# generation at start and compares it to detect signals received since then.
_job_interrupt_generation = 0
_job_interrupt_lock = threading.Lock()

def _handle_job_signal(signum, frame):
    """Signal handler for SIGTERM/SIGINT during job operations.
    
    Note: Signal handler registration is centralized in app.py to ensure
    consistent handling across the application. This function is called
    when SIGTERM or SIGINT signals are received during job operations.
    """
    global _job_interrupt_generation
    with _job_interrupt_lock:
        _job_interrupt_generation += 1
    signal_logger = logging.getLogger("app")
    signal_logger.warning(f"Job operation interrupted by signal {signum}")

def _check_job_interrupted(generation):
    """Check if job was interrupted by signal since the given generation was captured."""
    with _job_interrupt_lock:
        return _job_interrupt_generation != generation

from common import (
    get_config_dir, get_active_logs_dir, get_failed_logs_dir,
    purge_old_logs, DEFAULT_LOG_RETENTION_DAYS, load_policy, get_db_path
)
from smart_parsing import pre_wipe_health_gate
from database import persist_job, load_job, save_wipe_smart_snapshot, calculate_smart_diff
from verification import (
    verification_for_method,
    write_marker_and_verify,
    verify_sata_sanitize,
    verify_sata_secure_erase,
    verify_nvme_sanitize,
    verify_sas_block,
    resolve_verify_command_path,
    capture_before_state,
    parse_sata_erase_time_estimate
)
from certificates import build_certificate
from notifier import send_slack_notification
from disk_ops import get_os_by_path, invalidate_drive_cache
from disk_utils import validate_device_path
from zero_check_manager import get_manager as get_zero_check_manager
from smart_parsing import get_raw_smart_diagnostics, get_smart_data
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
    if selected_drive.get("sas_secondary_path"):
        return None, {"error": f"Cannot wipe secondary path of dual-port SAS drive: {bay}"}, 403

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
    os_path_result = get_os_by_path()
    if os_path_result is None:
        os_dev_node, os_by_path = None, None
    else:
        os_dev_node, os_by_path = os_path_result
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
        "job_type": "erase",
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

def get_device_logical_block_size(device):
    """Read logical block size from sysfs. Falls back to 512 if unavailable."""
    try:
        dev_name = os.path.basename(device)
        bs_path = f"/sys/block/{dev_name}/queue/logical_block_size"
        with open(bs_path, "r") as f:
            return int(f.read().strip())
    except Exception:
        return 512

def get_device_sectors_written(device):
    try:
        dev_name = os.path.basename(device)
        stat_path = f"/sys/block/{dev_name}/stat"
        with open(stat_path, "r") as f:
            content = f.read().strip()
        parts = content.split()
        if len(parts) >= 7:
            return int(parts[6])
    except Exception as e:
        logging.getLogger(__name__).debug(f"poll failed for sectors written on {device}: {e}")
    return None

def poll_nvme_sanitize_progress(device):
    try:
        nvme_path = resolve_verify_command_path("nvme")
        if nvme_path:
            result = subprocess.run(["sudo", nvme_path, "sanitize-log", device], capture_output=True, text=True, shell=False)
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if "sprog" in line.lower():
                        match = re.search(r"sprog\s*[:=]\s*(\d+)", line, re.IGNORECASE)
                        if match:
                            return int(match.group(1))
    except Exception as e:
        logging.getLogger(__name__).debug(f"poll failed for NVMe sanitize on {device}: {e}")
    return None

def poll_sas_sanitize_progress(device):
    try:
        sg_req_path = resolve_verify_command_path("sg_requests")
        if sg_req_path:
            result = subprocess.run(["sudo", sg_req_path, "--progress", device], capture_output=True, text=True, shell=False)
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if "progress" in line.lower():
                        match = re.search(r"(\d+\.?\d*)\s*%", line)
                        if match:
                            return float(match.group(1))
    except Exception as e:
        logging.getLogger(__name__).debug(f"poll failed for SAS sanitize on {device}: {e}")
    return None

def poll_sata_sanitize_progress(device):
    try:
        hdparm_path = resolve_verify_command_path("hdparm")
        if hdparm_path:
            result = subprocess.run(["sudo", hdparm_path, "--sanitize-status", device], capture_output=True, text=True, shell=False)
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if "progress" in line.lower() or "percent" in line.lower():
                        match = re.search(r"(\d+\.?\d*)\s*%", line)
                        if match:
                            return float(match.group(1))
    except Exception as e:
        logging.getLogger(__name__).debug(f"poll failed for SATA sanitize on {device}: {e}")
    return None

def prepare_erase_command(device, interface_type, method):
    selected_method = str(method or "").strip().lower()
    iface = str(interface_type or "").strip().lower()

    # Validate interface type is one of the supported types
    supported_interfaces = {"sata", "sas", "nvme"}
    if iface and iface not in supported_interfaces:
        return {"ok": False, "error": f"unsupported_interface:{iface}"}

    if selected_method == "overwrite":
        dd_cmd = resolve_verify_command_path("dd")
        if not dd_cmd:
            return {"ok": False, "error": "dd_not_available"}
        return {"ok": True, "command": [dd_cmd, "if=/dev/zero", f"of={device}", "bs=16M", "status=none", "conv=fdatasync"]}

    if selected_method in {"secure_erase", "enhanced_secure_erase"}:
        hdparm_cmd = resolve_verify_command_path("hdparm")
        if not hdparm_cmd:
            return {"ok": False, "error": "hdparm_not_available"}
        user_password = SATA_SECURITY_PASSWORD
        erase_flag = "--security-erase-enhanced" if selected_method == "enhanced_secure_erase" else "--security-erase"
        erase_cmd = [hdparm_cmd, "--user-master", "u", erase_flag, user_password, device]
        return {"ok": True, "command": erase_cmd}

    if selected_method in {"block", "crypto"}:
        if iface == "nvme":
            nvme_cmd = resolve_verify_command_path("nvme")
            if not nvme_cmd:
                return {"ok": False, "error": "nvme_not_available"}
            # NVMe sanitize must be run on the controller device (/dev/nvmeX), not namespace (/dev/nvmeXnY)
            sanitize_device = device
            if device and re.match(r'^/dev/nvme\d+n\d+\Z', device):
                # Extract controller from namespace (e.g., /dev/nvme0n1 -> /dev/nvme0)
                match = re.match(r'^(/dev/nvme\d+)n\d+\Z', device)
                if match:
                    sanitize_device = match.group(1)
                    # Validate extracted controller path before use (lesson-learned #9)
                    if not validate_device_path(sanitize_device):
                        return {"ok": False, "error": "invalid_extracted_device_path"}
            # --sanact expects decimal value: 4=crypto erase, 2=block erase
            sanact_value = "4" if selected_method == "crypto" else "2"
            return {"ok": True, "command": [nvme_cmd, "sanitize", sanitize_device, "--sanact", sanact_value]}
            
        if iface == "sata":
            hdparm_cmd = resolve_verify_command_path("hdparm")
            if not hdparm_cmd:
                return {"ok": False, "error": "hdparm_not_available"}
            action = "--sanitize-crypto-scramble" if selected_method == "crypto" else "--sanitize-block-erase"
            return {"ok": True, "command": [hdparm_cmd, "--yes-i-know-what-i-am-doing", action, device]}

        if iface == "sas":
            sg_sanitize_cmd = resolve_verify_command_path("sg_sanitize")
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
            try:
                os.rename(active_log_path, failed_log_path)
            except FileNotFoundError:
                pass
            except Exception as e:
                logger.warning(f"Failed to rename active log to failed log: {e}")
            try:
                with open(failed_log_path, "a", encoding="utf-8") as lf:
                    lf.write(f"\n=== JOB CONFIGURATION FAILURE ===\nError Message: {error_message}\n")
                    dev = job["request"].get("device")
                    if dev:
                        lf.write(get_raw_smart_diagnostics(dev))
            except Exception as e:
                logger.warning(f"Failed to write failure diagnostics to log: {e}")
                
            persist_job(job)
            # Drop cached discovery data for this device so the next discovery reflects post-job state
            invalidate_drive_cache(job["request"].get("device"))
            send_slack_notification(job)
            
            # Emit a high-signal application log representing an initialization failure
            logger.error(f"Job {job_id} (Bay {job['request']['bay']}) initialization failed: {error_message}")
            
            try:
                purge_old_logs(DEFAULT_LOG_RETENTION_DAYS)
            except Exception as e:
                logger.warning(f"Failed to purge old logs: {e}")

def run_erase_job(job_id):
    # High #14: Capture current generation so we can detect signals received during this job.
    with _job_interrupt_lock:
        _job_generation = _job_interrupt_generation

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
    logger.info(f"Job {job_id} (Bay {job['request']['bay']}) transitioning to RUNNING. Method: '{method}', Target: '{device}', Interface: '{interface_type}'")

    # Cancel any background zero-check for this bay before starting destructive operations
    try:
        bay = job["request"].get("bay")
        if bay:
            get_zero_check_manager().on_wipe_starting(bay)
    except Exception as e:
        logger.warning(f"Failed to cancel zero-check for bay {job['request'].get('bay')} before wipe: {e}")

    # Pre-wipe health gate check to prevent starting wipes on failing drives
    config_dir = get_config_dir()
    policy = load_policy(config_dir)
    health_gate_result = pre_wipe_health_gate(device, interface_type, policy)
    
    # Check if override was requested
    health_gate_override = job["request"].get("health_gate_override", False)
    health_gate_override_justification = job["request"].get("health_gate_override_justification", "")
    
    if health_gate_result.get("blocked"):
        block_reason = health_gate_result.get("block_reason")
        strict_mode = policy.get("prewipe_health_gate_strict_mode", False)
        strict_audit_mode = policy.get("strict_audit_mode", False)
        
        # Determine if override is allowed
        override_allowed = not strict_mode and not strict_audit_mode
        
        # If override was requested and allowed, log and proceed
        if health_gate_override and override_allowed:
            logger.warning(f"Job {job_id} (Bay {job['request']['bay']}) health gate override requested: {block_reason}. Justification: {health_gate_override_justification}")
            # Update job with override details for audit trail
            with ERASE_JOBS_LOCK:
                job = ERASE_JOBS.get(job_id)
                if job:
                    job["health_gate_result"] = health_gate_result
                    job["health_gate_override"] = True
                    job["health_gate_override_justification"] = health_gate_override_justification
                    persist_job(job)
            # Proceed with wipe despite health gate block
        else:
            logger.warning(f"Job {job_id} (Bay {job['request']['bay']}) blocked by pre-wipe health gate: {block_reason}")
            
            # Update job with health gate failure details
            with ERASE_JOBS_LOCK:
                job = ERASE_JOBS.get(job_id)
                if job:
                    job["health_gate_result"] = health_gate_result
                    job["override_allowed"] = override_allowed
                    persist_job(job)
            
            if override_allowed:
                finalize_failed_job(job_id, f"pre_wipe_health_check_failed_override_available: {block_reason}")
            else:
                finalize_failed_job(job_id, f"pre_wipe_health_check_failed: {block_reason}")
            return

    # Capture before-state for all methods for hash comparison verification
    before_state = None
    logger.info(f"Job {job_id} (Bay {job['request']['bay']}) capturing before-state for hash comparison verification")
    before_state = capture_before_state(device)
    if before_state and before_state.get("ok"):
        with ERASE_JOBS_LOCK:
            job = ERASE_JOBS.get(job_id)
            if job:
                job["verification_state"] = {"before": before_state}
                persist_job(job)
            else:
                # Medium #36: Job was deleted between lock sections, log and continue
                logger.warning(f"Job {job_id} was deleted before verification state could be saved")

    # Initialize erase time estimate for SATA secure erase
    erase_time_estimate_seconds = None

    if method in {"secure_erase", "enhanced_secure_erase"} and interface_type == "sata":
        hdparm_cmd = resolve_verify_command_path("hdparm")
        if not hdparm_cmd:
            finalize_failed_job(job_id, "hdparm_not_available")
            return

        user_password = SATA_SECURITY_PASSWORD
        set_pass_cmd = ["sudo", hdparm_cmd, "--user-master", "u", "--security-set-pass", user_password, device]
        try:
            set_pass_proc = subprocess.run(set_pass_cmd, capture_output=True, text=True, shell=False)
            if set_pass_proc.returncode != 0:
                err_msg = set_pass_proc.stderr.strip() or "set_password_failed"
                if "frozen" in set_pass_proc.stdout.lower() or "frozen" in set_pass_proc.stderr.lower():
                    err_msg = "SATA drive is FROZEN by BIOS. Suspend-to-RAM or hot-plug SATA power to unfreeze."
                finalize_failed_job(job_id, f"security_set_password_failed: {err_msg}")
                return
        except Exception as e:
            finalize_failed_job(job_id, f"security_set_password_exception: {str(e)}")
            return

        # Capture erase time estimate from hdparm -I before starting erase
        try:
            identify_result = subprocess.run(["sudo", hdparm_cmd, "-I", device], capture_output=True, text=True, shell=False)
            if identify_result.returncode == 0:
                erase_time_estimate_seconds = parse_sata_erase_time_estimate(identify_result.stdout)
                logger.info(f"Job {job_id} (Bay {job['request']['bay']}) captured erase time estimate: {erase_time_estimate_seconds} seconds")
        except Exception as e:
            logger.warning(f"Job {job_id} (Bay {job['request']['bay']}) failed to capture erase time estimate: {e}")

        erase_flag = "--security-erase-enhanced" if method == "enhanced_secure_erase" else "--security-erase"
        command = [hdparm_cmd, "--user-master", "u", erase_flag, user_password, device]
    else:
        cmd_result = prepare_erase_command(device, interface_type, method)
        logger.info(f"prepare_erase_command result: ok={cmd_result.get('ok')}, error={cmd_result.get('error')}, interface_type={interface_type}, method={method}")
        if not cmd_result.get("ok"):
            finalize_failed_job(job_id, cmd_result.get("error") or "prepare_command_failed")
            return
        command = cmd_result["command"]

    start_time = datetime.now(timezone.utc)
    initial_sectors = None
    last_sectors = None
    last_progress_time = None
    speed_samples = deque(maxlen=10)  # Rolling speed samples for ETA smoothing
    logical_block_size = 512
    if method == "overwrite":
        initial_sectors = get_device_sectors_written(device)
        last_sectors = initial_sectors
        last_progress_time = start_time
        logical_block_size = get_device_logical_block_size(device)

    active_log_path = os.path.join(get_active_logs_dir(), f"job-{job_id}.log")
    try:
        log_file = open(active_log_path, "w", encoding="utf-8", buffering=1)
        log_file.write(f"=== Sanitization Job Started: {datetime.now(timezone.utc).isoformat()} ===\n")
        log_file.write(f"Target Device: {device}\n")
        log_file.write(f"Wipe Method: {method}\n")
        log_file.write(f"Command Invocation: {' '.join(command)}\n\n")
        log_file.flush()
    except Exception as e:
        if 'log_file' in locals() and not log_file.closed:
            log_file.close()
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
        # Store process reference for admin kill-all functionality
        with ERASE_JOBS_LOCK:
            job = ERASE_JOBS.get(job_id)
            if job:
                job["_process"] = process
    except Exception as e:
        try:
            log_file.close()
        except Exception:
            pass
        finalize_failed_job(job_id, f"process_spawn_failed:{str(e)}")
        return

    try:
        estimated_seconds = DEFAULT_SATA_ERASE_ESTIMATE_SECONDS

        # Thread sleep telemetry updates loop (contained within individual job context)
        while process.poll() is None:
            # High #14: Check for job interruption
            if _check_job_interrupted(_job_generation):
                logger.warning(f"Job {job_id} (Bay {job['request']['bay']}) interrupted during erase subprocess execution")
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                with ERASE_JOBS_LOCK:
                    job = ERASE_JOBS.get(job_id)
                    if job:
                        job["status"] = "interrupted"
                        job["finished_at"] = datetime.now(timezone.utc).isoformat()
                        job["error"] = "Job interrupted by signal during erase execution"
                        job["current_phase"] = "Interrupted"
                        persist_job(job)
                if 'log_file' in locals() and not log_file.closed:
                    log_file.close()
                return

            elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
            progress = 0.0
            phase = "Sanitizing Drive..."

            if method == "overwrite":
                current_sectors = get_device_sectors_written(device)
                if current_sectors is not None and initial_sectors is not None:
                    delta_sectors = max(0, current_sectors - initial_sectors)
                    wrote_bytes = delta_sectors * logical_block_size
                    progress = min(99.9, (wrote_bytes / capacity_bytes) * 100)
                    
                    # Calculate ETA based on write speed (rolling average for stability)
                    eta_text = ""
                    if last_sectors is not None and last_progress_time is not None and elapsed > 5:
                        time_since_last = elapsed - (last_progress_time - start_time).total_seconds()
                        if time_since_last > 0:
                            sectors_since_last = max(0, current_sectors - last_sectors)
                            bytes_since_last = sectors_since_last * logical_block_size
                            interval_speed = bytes_since_last / time_since_last  # bytes per second
                            speed_samples.append(interval_speed)
                            # Blend rolling average with overall average for stability
                            rolling_avg = sum(speed_samples) / len(speed_samples)
                            overall_avg = wrote_bytes / elapsed if elapsed > 0 else 0
                            # Weight: 70% rolling average (adapts to trend), 30% overall average (anchors to reality)
                            write_speed = (rolling_avg * 0.7) + (overall_avg * 0.3)
                            # Minimum write speed threshold to prevent extremely large ETA estimates
                            min_write_speed = 1024 * 1024  # 1 MB/s minimum
                            if write_speed > min_write_speed:
                                remaining_bytes = capacity_bytes - wrote_bytes
                                eta_seconds = remaining_bytes / write_speed
                                eta_minutes = eta_seconds / 60
                                if eta_minutes < 60:
                                    eta_text = f" - ~{eta_minutes:.0f} min remaining"
                                else:
                                    eta_hours = eta_minutes / 60
                                    eta_text = f" - ~{eta_hours:.1f} hr remaining"
                    
                    phase = f"Writing zeroes ({progress:.1f}%){eta_text}"
                    last_sectors = current_sectors
                    last_progress_time = datetime.now(timezone.utc)
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
                    # Use drive-specific estimate if available, otherwise fall back to default
                    timeout_for_progress = erase_time_estimate_seconds if erase_time_estimate_seconds and erase_time_estimate_seconds > 0 else estimated_seconds
                    progress = min(99.9, (elapsed / timeout_for_progress) * 100)
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

        with ERASE_JOBS_LOCK:
            job = ERASE_JOBS.get(job_id)
            if job:
                job["_process"] = None

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
        logger.warning(f"Failed to read execution log: {e}")
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

    if method in {"crypto", "block", "secure_erase", "enhanced_secure_erase"}:
        logger.info(f"Starting firmware polling: method={method}, interface_type={interface_type}, device={device}")
        firmware_complete = False
        poll_start_time = datetime.now(timezone.utc)
        # Use erase command start time for progress calculation, not polling start time
        erase_start_time = start_time
        max_poll_seconds = 1200 if method == "crypto" else 7200
        
        time.sleep(5)
        
        consecutive_errors = 0
        max_consecutive_errors = 15

        while not firmware_complete:
            # High #14: Check for job interruption during firmware polling
            if _check_job_interrupted(_job_generation):
                logger.warning(f"Job {job_id} (Bay {job['request']['bay']}) interrupted during firmware polling")
                with ERASE_JOBS_LOCK:
                    job = ERASE_JOBS.get(job_id)
                    if job:
                        job["status"] = "interrupted"
                        job["finished_at"] = datetime.now(timezone.utc).isoformat()
                        job["error"] = "Job interrupted by signal during firmware polling"
                        job["current_phase"] = "Interrupted"
                        persist_job(job)
                return

            elapsed_poll = (datetime.now(timezone.utc) - poll_start_time).total_seconds()
            if elapsed_poll > max_poll_seconds:
                break
                
            status_report = None
            progress_pct = 0.0
            phase_text = "Sanitizing in background..."
            
            if interface_type == "sata":
                if method in {"secure_erase", "enhanced_secure_erase"}:
                    # ATA secure erase doesn't have sanitize-status, check if security is disabled
                    status_report = verify_sata_secure_erase(device, method)
                else:
                    status_report = verify_sata_sanitize(device, method)
            elif interface_type == "nvme":
                logger.info(f"Polling NVMe firmware: device={device}, method={method}")
                status_report = verify_nvme_sanitize(device, method)
                logger.info(f"NVMe firmware poll result: ok={status_report.get('ok')}, error={status_report.get('error')}")
            elif interface_type == "sas":
                status_report = verify_sas_block(device, method)
                
            if status_report:
                if status_report.get("ok"):
                    firmware_complete = True
                    progress_pct = 100.0
                    phase_text = "Sanitization completed"
                    consecutive_errors = 0
                elif status_report.get("error") in {"sata_sanitize_still_in_progress", "sata_security_still_enabled", "nvme_sanitize_still_in_progress", "sas_sanitize_still_in_progress"}:
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

                    if parsed_pct is None and interface_type == "sata" and method in {"secure_erase", "enhanced_secure_erase"}:
                        # ATA secure erase doesn't provide progress, use time-based estimate
                        # Use total elapsed time from erase command start, not just polling start
                        total_elapsed = (datetime.now(timezone.utc) - erase_start_time).total_seconds()
                        if erase_time_estimate_seconds and erase_time_estimate_seconds > 0:
                            progress_pct = min(99.9, (total_elapsed / erase_time_estimate_seconds) * 100)
                            if total_elapsed > erase_time_estimate_seconds:
                                phase_text = f"Verifying completion (taking longer than estimated {erase_time_estimate_seconds/60:.0f} min)"
                            else:
                                phase_text = f"Secure erase in progress ({progress_pct:.1f}%)"
                        else:
                            # No estimate available, use generic fallback (15 minutes)
                            fallback_timeout = 900.0
                            progress_pct = min(99.9, (total_elapsed / fallback_timeout) * 100.0)
                            phase_text = f"Secure erase in progress ({progress_pct:.1f}%)"

                    if parsed_pct is None and interface_type == "nvme":
                        sprog_val = details.get("sprog")
                        if sprog_val is not None:
                            parsed_pct = (sprog_val / 65535.0) * 100.0

                    if parsed_pct is not None:
                        progress_pct = min(99.9, parsed_pct)
                        phase_text = f"Firmware sanitizing in progress ({progress_pct:.1f}%)"
                else:
                    consecutive_errors += 1
                    if consecutive_errors >= max_consecutive_errors:
                        break
                    phase_text = f"Polling drive (reconnecting... {consecutive_errors}/{max_consecutive_errors})"
                    fallback_timeout = 30.0 if method == "crypto" else (900.0 if method in {"secure_erase", "enhanced_secure_erase"} else 300.0)
                    progress_pct = min(99.9, (elapsed_poll / fallback_timeout) * 100.0)
            elif status_report is None:
                # No status report for this interface type (e.g., scsi)
                logger.warning(f"No status report for interface_type={interface_type} during firmware polling for {device}")
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    break
                phase_text = f"Polling drive (no status report... {consecutive_errors}/{max_consecutive_errors})"
            else:
                # Unknown error - log details and increment error counter
                logger.warning(f"Unexpected verification error during firmware polling for {device}: {status_report.get('error')}, details: {status_report.get('details')}")
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    break
                phase_text = f"Polling drive (verification error... {consecutive_errors}/{max_consecutive_errors})"
                fallback_timeout = 30.0 if method == "crypto" else (900.0 if method in {"secure_erase", "enhanced_secure_erase"} else 300.0)
                progress_pct = min(99.9, (elapsed_poll / fallback_timeout) * 100.0)
                
            with ERASE_JOBS_LOCK:
                job = ERASE_JOBS.get(job_id)
                if job:
                    job["progress_percent"] = round(progress_pct, 1)
                    job["current_phase"] = phase_text
                    
            time.sleep(4)

    if method in {"crypto", "block", "secure_erase", "enhanced_secure_erase"}:
        time.sleep(5)

    # Get before_state for crypto verification (inside lock to avoid race condition)
    with ERASE_JOBS_LOCK:
        job = ERASE_JOBS.get(job_id)
        if not job:
            return
        before_state = job.get("verification_state", {}).get("before")
        # Copy job fields needed for verification to avoid race condition after lock release
        device = job["request"]["device"]
        interface_type = job["request"]["interface_type"]
        method = job["request"]["method"]
        full_verification = job["request"].get("full_verification", False)
        sample_ratio = 1.0 if full_verification else 0.10

    verification = verification_for_method(
        device,
        interface_type,
        method,
        execution,
        before_state,
        sample_ratio=sample_ratio
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

            # Phase 5: Capture post-wipe SMART snapshot after successful verification
            post_wipe_smart = None
            try:
                command_diagnostics = {}
                post_wipe_smart = get_smart_data(device, command_diagnostics)
                if post_wipe_smart:
                    save_wipe_smart_snapshot(job_id, "post", post_wipe_smart)
                    logger.info(f"Job {job_id} (Bay {job['request']['bay']}) post-wipe SMART snapshot captured")
            except Exception as e:
                logger.warning(f"Failed to capture post-wipe SMART snapshot for job {job_id}: {e}")

            # Phase 5: Calculate SMART diff if both pre and post snapshots exist
            if post_wipe_smart:
                try:
                    # Load pre-wipe snapshot from database
                    pre_wipe_smart = None
                    with closing(sqlite3.connect(get_db_path(), timeout=30.0)) as conn:
                        conn.row_factory = sqlite3.Row
                        row = conn.execute(
                            "SELECT pre_wipe_smart_json FROM erase_jobs WHERE id = ?",
                            (job_id,)
                        ).fetchone()
                        if row and row["pre_wipe_smart_json"]:
                            pre_wipe_smart = json.loads(row["pre_wipe_smart_json"])

                    if pre_wipe_smart:
                        smart_diff = calculate_smart_diff(pre_wipe_smart, post_wipe_smart)
                        if smart_diff:
                            job["smart_diff"] = smart_diff
                            if smart_diff.get("worsened"):
                                logger.warning(f"Job {job_id} (Bay {job['request']['bay']}) SMART metrics worsened during wipe: {smart_diff['worsened']}")
                except Exception as e:
                    logger.warning(f"Failed to calculate SMART diff for job {job_id}: {e}")

            # Check if post-erase marker is enabled in policy or disabled per request
            policy = load_policy(get_config_dir())
            post_erase_marker = policy.get("post_erase_marker", True)
            disable_marker_request = job["request"].get("disable_marker", False)

            if post_erase_marker and not disable_marker_request:
                logger.info(f"Job {job_id} (Bay {job['request']['bay']}) verified successfully. Writing supplemental station marker.")
                smart_baseline = verification.get("details", {}).get("smart_baseline_for_marker")
                marker_result = write_marker_and_verify(job, smart_baseline=smart_baseline)
                job["marker"] = marker_result
                if not marker_result.get("ok"):
                    warnings_list = job.get("result", {}).get("warnings", [])
                    if not isinstance(warnings_list, list):
                        warnings_list = []
                    warnings_list.append(f"Supplemental station marker failed ({marker_result.get('error') or marker_result.get('status')}); sanitization certification is based on wipe verification evidence.")
                    job["result"]["warnings"] = warnings_list
                    logger.warning(f"Job {job_id} (Bay {job['request']['bay']}) supplemental marker failed: {marker_result.get('error') or marker_result.get('status')}")
            elif disable_marker_request:
                logger.info(f"Job {job_id} (Bay {job['request']['bay']}) verified successfully. Post-erase marker disabled per request, skipping marker write.")
                job["marker"] = {"ok": True, "status": "disabled_per_request", "error": None, "details": {}}
            else:
                logger.info(f"Job {job_id} (Bay {job['request']['bay']}) verified successfully. Post-erase marker disabled by policy, skipping marker write.")
                job["marker"] = {"ok": True, "status": "disabled_by_policy", "error": None, "details": {}}
            
            try:
                os.remove(active_log_path)
            except FileNotFoundError:
                pass
            except Exception as e:
                logger.warning(f"Failed to remove active log: {e}")
                    
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

            # Include verification details in error for debugging
            verification_details = verification.get("details", {})
            if verification_details:
                job["error"] += f" | Details: {verification_details}"

            # High-signal error event written to the global app.log
            logger.error(f"Job {job_id} (Bay {job['request']['bay']}) FAILED: {job['error']}")

            failed_log_path = os.path.join(get_failed_logs_dir(), f"failed-job-{job_id}-bay{job['request']['bay']}.log")
            try:
                os.rename(active_log_path, failed_log_path)
            except FileNotFoundError:
                pass
            except Exception as e:
                logger.warning(f"Failed to rename active log to failed log: {e}")
            try:
                smart_diagnostics = get_raw_smart_diagnostics(device)
                with open(failed_log_path, "a", encoding="utf-8") as lf:
                    lf.write("\n=== WIPE ATTESTATION FAILURE ===\n")
                    lf.write(f"Failure Attestation Message: {job['error']}\n")
                    lf.write(smart_diagnostics)
            except Exception as e:
                logger.warning(f"Failed to write attestation failure diagnostics: {e}")

            try:
                job["certificate"] = build_certificate(job)
            except Exception as e:
                job["error"] = f"{job['error']} (and failure_certificate_generation_failed:{e})"
                job["certificate"] = None

        persist_job(job)

    # Drop cached discovery data for this device so the next discovery reflects post-wipe state
    invalidate_drive_cache(device)

    send_slack_notification(job)

    try:
        purge_old_logs(DEFAULT_LOG_RETENTION_DAYS)
    except Exception as e:
        logger.warning(f"Failed to purge old logs: {e}")

# --- END OF FILE backend/job_management.py ---
