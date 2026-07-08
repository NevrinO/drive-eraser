# Admin-related routes (slimmed — shared utilities in _shared.py, other routes in separate modules)
# See fix-plan-G1.md for the extraction plan
import subprocess
from datetime import datetime, timezone
from flask import Blueprint, jsonify
from app_config import logger, limiter, ERASE_JOBS, ERASE_JOBS_LOCK
from database import persist_job
from verification import verify_nvme_sanitize, verify_sata_sanitize, verify_sas_block
from job_management import poll_sata_sanitize_progress, poll_sas_sanitize_progress, get_device_sectors_written

# Re-exports for backward compat — external modules import these from routes.admin_routes
from routes._shared import (
    require_admin_auth, is_local_request, is_valid_device_name,
    is_valid_id, should_update_test_status, should_trust_completion_status, _validate_slot_metadata,
    _SATA_DEVICE_RE, _NVME_DEVICE_RE, _ID_PATTERN,
    _VALID_ROLES, MAX_ENCLOSURES, MAX_SLOTS_PER_ENCLOSURE,
    MAX_TEMPLATES, MAX_DEVICES_FOR_BUNDLE
)  # noqa: F401 — backward compat for external imports

admin_bp = Blueprint('admin_routes', __name__)


def _check_drive_hardware_status(job):
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
    

@admin_bp.route("/api/admin/jobs/kill-all", methods=["POST"])
@require_admin_auth
@limiter.limit("10 per minute")
def kill_all_jobs():
    """Kill all running and queued jobs. Checks drive hardware status before killing.

    - If drive reports still wiping: job is skipped with detailed diagnostics
    - If drive reports idle/complete but subprocess stuck: job is killed
    """
    try:
        killed_jobs = []
        skipped_jobs = []
        
        # Step 1: Snapshot running/queued jobs inside lock
        with ERASE_JOBS_LOCK:
            jobs_snapshot = [
                (job_id, job)
                for job_id, job in list(ERASE_JOBS.items())
                if job.get("status") in {"running", "queued"}
            ]

        # Step 2: Perform hardware checks and process termination outside lock
        jobs_to_kill = []
        for job_id, job in jobs_snapshot:
            request_data = job.get("request", {})
            device = request_data.get("device")
            method = request_data.get("method")
            interface_type = request_data.get("interface_type", "unknown")
            bay = request_data.get("bay")
            
            # Check actual hardware status
            hw_status = _check_drive_hardware_status(job)
            
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
                # Terminate process outside lock (process.wait can block)
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
                
                jobs_to_kill.append((job_id, bay, device, method, interface_type, hw_status, termination_result))

        # Step 3: Apply kill decisions inside lock
        with ERASE_JOBS_LOCK:
            for job_id, bay, device, method, interface_type, hw_status, termination_result in jobs_to_kill:
                job = ERASE_JOBS.get(job_id)
                if not job or job.get("status") not in {"running", "queued"}:
                    # Job state changed between snapshot and kill — skip
                    logger.info(f"Job {job_id} state changed before kill could be applied (termination_result={termination_result}), skipping")
                    continue

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
