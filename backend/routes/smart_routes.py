# SMART routes: SMART data export, drive models, deep-dive details, test runner
# Extracted from admin_routes.py for modularity (fix-plan-G1)
import os
import json
import io
import subprocess
from datetime import datetime, timezone
from threading import Lock
from flask import Blueprint, jsonify, request, send_file
from app_config import logger, limiter, ERASE_JOBS, ERASE_JOBS_LOCK, SMART_TEST_LOCKS, SMART_TEST_LOCKS_LOCK
from common import get_config_dir
from disk_ops import get_os_by_path, discover_drives
from smart_constants import correct_self_test_log_hours
from routes._shared import require_admin_auth, is_valid_device_name, should_trust_completion_status

smart_bp = Blueprint('smart_routes', __name__)


@smart_bp.route("/api/admin/drives/<device>/smart-export")
@limiter.limit("30 per minute")
def export_smart_data(device):
    """Export raw SMART data for a specific device as a JSON file.
    
    Phase 6 Feature E: Per-Drive Raw SMART Export
    
    Note: This endpoint returns full, untruncated SMART data for export purposes.
    For size-limited data suitable for UI display, use the smart-details endpoint.
    """
    try:
        # Validate device name (lesson #9)
        if not is_valid_device_name(device):
            return jsonify({"error": "Invalid device name"}), 400
        
        # Build device path
        device_path = f"/dev/{device}"
        
        # Check if device is currently being wiped (safety guardrail)
        with ERASE_JOBS_LOCK:
            for job in ERASE_JOBS.values():
                if job.get("status") in {"running", "queued"}:
                    req = job.get("request", {})
                    if req.get("device") == device_path:
                        return jsonify({"error": "Cannot export SMART data while wipe is in progress"}), 409
        
        # Import smart_parsing to get SMART data
        from smart_parsing import get_smart_data
        
        # Get SMART data as dictionary
        smart_data = get_smart_data(device_path)
        
        if not smart_data or not smart_data.get("serial"):
            return jsonify({"error": "Failed to retrieve SMART data"}), 500
        
        # Enforce maximum export size (lesson #9) - 10MB limit to prevent abuse
        export_json = json.dumps(smart_data, indent=2)
        export_size = len(export_json.encode('utf-8'))
        MAX_EXPORT_SIZE = 10 * 1024 * 1024  # 10MB
        if export_size > MAX_EXPORT_SIZE:
            logger.warning(f"SMART export for {device} exceeded size limit: {export_size} bytes")
            return jsonify({"error": f"SMART data too large for export ({export_size} bytes > {MAX_EXPORT_SIZE} bytes limit)"}), 413
        
        # Extract serial for filename
        serial = smart_data.get("serial", "unknown")
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"smartctl-{serial}-{timestamp}.json"
        
        # Return as JSON file download
        return send_file(
            io.BytesIO(export_json.encode('utf-8')),
            mimetype="application/json",
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        logger.error(f"SMART export failed for {device}: {e}")
        return jsonify({"error": str(e)}), 500


@smart_bp.route("/api/admin/drive-models")
@require_admin_auth
@limiter.limit("30 per minute")
def get_drive_models():
    """Get drive model risk profiles from drive_models.json.
    
    Phase 6 Feature F: Model Risk Profile
    """
    try:
        config_dir = get_config_dir()
        drive_models_path = os.path.join(config_dir, "drive_models.json")
        
        try:
            with open(drive_models_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return jsonify({"drive_models": {}, "message": "No drive models configured"}), 200

        return jsonify(data), 200
    except Exception as e:
        logger.error(f"Error loading drive models: {e}")
        return jsonify({"error": str(e)}), 500


@smart_bp.route("/api/admin/drives/<device>/smart-details")
@limiter.limit("30 per minute")
def get_smart_details(device):
    """Get deep-dive SMART data for a specific device.
    
    Phase 7 Feature G: Deep-dive SMART viewer
    Returns structured attributes, error logs, self-test logs, and protocol-specific logs.
    """
    try:
        # Validate device name (lesson #9)
        if not is_valid_device_name(device):
            return jsonify({"error": "Invalid device name"}), 400
        
        # Build device path
        device_path = f"/dev/{device}"
        
        # Import smart_parsing to get SMART data
        from smart_parsing import get_smart_data
        from database import get_smart_test_history
        
        # Get SMART data as dictionary
        smart_data = get_smart_data(device_path)
        
        if not smart_data or not smart_data.get("raw"):
            return jsonify({"error": "Failed to retrieve SMART data"}), 500
        
        # Parse the raw JSON output
        try:
            raw_json = json.loads(smart_data["raw"])
        except json.JSONDecodeError:
            return jsonify({"error": "Failed to parse SMART data"}), 500
        
        # Get database audit history for this device
        try:
            audit_history = get_smart_test_history(device=device_path, limit=10)
        except Exception as e:
            logger.warning(f"Failed to get SMART test history for {device}: {e}")
            audit_history = []
        
        # Extract structured data with size limits (lesson #9: DoS prevention)
        result = {
            "attributes": [],
            "error_logs": None,
            "self_test_logs": [],
            "device_statistics": [],
            "sas_specific": None,
            "nvme_specific": None,
            "truncated": False
        }
        
        # Size limits for DoS prevention
        MAX_ATTRIBUTES = 100
        MAX_SELF_TEST_LOGS = 50
        MAX_DEVICE_STATISTICS_PAGES = 10
        MAX_JSON_SIZE_BYTES = 100000  # 100KB limit for nested JSON objects
        
        # ATA attributes table (limited to MAX_ATTRIBUTES)
        ata_attrs = raw_json.get("ata_smart_attributes", {}).get("table", [])
        for attr in ata_attrs[:MAX_ATTRIBUTES]:
            result["attributes"].append({
                "id": attr.get("id"),
                "name": attr.get("name"),
                "value": attr.get("value"),
                "worst": attr.get("worst"),
                "thresh": attr.get("thresh"),
                "raw": attr.get("raw", {}).get("value"),
                "flags": attr.get("flags")
            })
        if len(ata_attrs) > MAX_ATTRIBUTES:
            result["truncated"] = True
        
        # Error logs (size-limited)
        if "ata_smart_error_log" in raw_json:
            error_log_json = json.dumps(raw_json["ata_smart_error_log"])
            if len(error_log_json.encode('utf-8')) <= MAX_JSON_SIZE_BYTES:
                result["error_logs"] = raw_json["ata_smart_error_log"]
            else:
                result["error_logs"] = {"truncated": True, "reason": "exceeded_size_limit"}
                result["truncated"] = True
        
        # Self-test logs (limited to MAX_SELF_TEST_LOGS)
        # SMART self-test log hours use 16-bit counters (max 65,535).
        # For drives with >65,535 power-on hours, log hours will have rolled over.
        # We use database history and current POH to determine correct hours.
        # If ambiguous (no history, low hours on high-hour drive), flag for calibration test.
        current_poh = smart_data.get("power_on_hours")
        serial = smart_data.get("serial")
        
        # Get historical POH from database for this serial
        historical_poh = None
        if serial:
            try:
                from database import get_historical_poh_for_serial
                historical_poh = get_historical_poh_for_serial(serial)
            except Exception as e:
                logger.warning(f"Failed to get historical POH for {serial}: {e}")
        
        # Handle ATA/SATA self-test logs
        if "ata_smart_self_test_log" in raw_json:
            ata_self_test_log = raw_json["ata_smart_self_test_log"]
            # smartctl JSON nests the table under "standard" (for -l selftest) or "extended" (for -x/-l xselftest)
            self_test_table = (ata_self_test_log.get("standard", {}).get("table", [])
                               or ata_self_test_log.get("extended", {}).get("table", [])
                               or ata_self_test_log.get("table", []))
            logger.debug(f"Device {device}: Found {len(self_test_table)} ATA self-test log entries")
            for idx, test in enumerate(self_test_table[:MAX_SELF_TEST_LOGS]):
                log_hours = test.get("hours") or test.get("lifetime_hours")
                corrected_hours, rollover_corrected, ambiguous = correct_self_test_log_hours(log_hours, current_poh, historical_poh)
                
                # Handle remaining field: convert string "null" to actual None
                remaining_raw = test.get("status", {}).get("remaining_percent", test.get("status", {}).get("remaining"))
                remaining = None if remaining_raw == "null" or remaining_raw is None else remaining_raw

                result["self_test_logs"].append({
                    "type": test.get("type", {}).get("string"),
                    "status": test.get("status", {}).get("string"),
                    "passed": test.get("status", {}).get("passed"),
                    "remaining": remaining,
                    "lba": test.get("lba"),
                    "hours": log_hours,
                    "corrected_hours": corrected_hours,
                    "rollover_corrected": rollover_corrected,
                    "ambiguous": ambiguous,
                    "log_index": idx
                })
            if len(self_test_table) > MAX_SELF_TEST_LOGS:
                result["truncated"] = True
        
        # Handle NVMe self-test logs
        elif "nvme_self_test_log" in raw_json:
            nvme_results = raw_json["nvme_self_test_log"].get("results", [])
            logger.debug(f"Device {device}: Found {len(nvme_results)} NVMe self-test log entries")
            for idx, test in enumerate(nvme_results[:MAX_SELF_TEST_LOGS]):
                result["self_test_logs"].append({
                    "type": test.get("self_test_num", "unknown"),
                    "status": test.get("result", {}).get("string", "unknown"),
                    "remaining": 0,
                    "lba": None,
                    "hours": None,
                    "corrected_hours": None,
                    "rollover_corrected": False,
                    "ambiguous": False,
                    "log_index": idx
                })
            if len(nvme_results) > MAX_SELF_TEST_LOGS:
                result["truncated"] = True
        
        # Handle SCSI/SAS self-test logs (via SCSI Informational Exceptions or scsi_self_test_N)
        elif "scsi_ie" in raw_json:
            scsi_ie = raw_json["scsi_ie"]
            scsi_string = scsi_ie.get("string", "unknown")
            logger.debug(f"Device {device}: Found SCSI IE log: {scsi_string}")
            
            # SAS doesn't have a traditional self-test log like ATA, but we can show the IE status
            result["self_test_logs"].append({
                "type": "scsi_ie",
                "status": scsi_string,
                "remaining": 0,
                "lba": None,
                "hours": None,
                "corrected_hours": None,
                "rollover_corrected": False,
                "ambiguous": False,
                "log_index": 0
            })
        
        # Handle SAS self-test logs (scsi_self_test_0, scsi_self_test_1, etc.)
        # Some SAS drives report self-tests in this format instead of scsi_ie
        sas_self_test_count = 0
        for i in range(20):  # Check for scsi_self_test_0 through scsi_self_test_19
            test_key = f"scsi_self_test_{i}"
            if test_key in raw_json:
                # Stop processing if we've reached the limit
                if sas_self_test_count >= MAX_SELF_TEST_LOGS:
                    result["truncated"] = True
                    break
                
                sas_self_test_count += 1
                test_data = raw_json[test_key]
                test_code = test_data.get("code", {}).get("string", "unknown")
                test_result = test_data.get("result", {}).get("string", "unknown")
                test_hours = test_data.get("power_on_time", {}).get("hours")
                
                # Determine if test passed (result value 0 = completed successfully)
                result_value = test_data.get("result", {}).get("value")
                passed = result_value == 0
                
                result["self_test_logs"].append({
                    "type": test_code,
                    "status": test_result,
                    "passed": passed,
                    "remaining": 0,
                    "lba": None,
                    "hours": test_hours,
                    "corrected_hours": test_hours,  # SAS hours don't rollover like ATA
                    "rollover_corrected": False,
                    "ambiguous": False,
                    "log_index": i
                })
        
        if sas_self_test_count > 0:
            logger.debug(f"Device {device}: Found {sas_self_test_count} SAS self-test log entries")
        elif "scsi_ie" not in raw_json:
            logger.debug(f"Device {device}: No self-test log found in SMART data (checked ata_smart_self_test_log, nvme_self_test_log, scsi_ie, scsi_self_test_N)")
        
        # Include current POH for context
        result["current_power_on_hours"] = current_poh
        
        # Include serial and interface_type so frontend can refresh without losing context
        result["serial"] = serial
        result["interface_type"] = smart_data.get("interface_type")
        
        # Include database audit history
        result["audit_history"] = audit_history
        
        # Device statistics (GP Log 0x04, limited to MAX_DEVICE_STATISTICS_PAGES)
        if "ata_device_statistics" in raw_json:
            pages = raw_json["ata_device_statistics"].get("pages", [])
            if pages and isinstance(pages, list):
                for page in pages[:MAX_DEVICE_STATISTICS_PAGES]:
                    page_data = {
                        "number": page.get("number"),
                        "table": []
                    }
                    page_table = page.get("table", [])
                    if page_table and isinstance(page_table, list):
                        for item in page_table:
                            page_data["table"].append({
                                "name": item.get("name"),
                                "value": item.get("value"),
                                "offset": item.get("offset")
                            })
                    result["device_statistics"].append(page_data)
                if len(pages) > MAX_DEVICE_STATISTICS_PAGES:
                    result["truncated"] = True
        
        # SAS-specific logs (size-limited)
        if "scsi_grown_defect_list" in raw_json or "scsi_error_counter_log" in raw_json:
            sas_data = {
                "grown_defect_list": raw_json.get("scsi_grown_defect_list"),
                "background_scan_log": raw_json.get("scsi_background_scan_log") or raw_json.get("scsi_background_scan"),
                "error_counter_log": raw_json.get("scsi_error_counter_log"),
                "non_medium_errors": raw_json.get("scsi_non_medium_error_count"),
                "start_stop_cycle_counter": raw_json.get("scsi_start_stop_cycle_counter"),
                "sas_port_0": raw_json.get("scsi_sas_port_0"),
                "sas_port_1": raw_json.get("scsi_sas_port_1")
            }
            sas_json = json.dumps(sas_data)
            if len(sas_json.encode('utf-8')) <= MAX_JSON_SIZE_BYTES:
                result["sas_specific"] = sas_data
            else:
                result["sas_specific"] = {"truncated": True, "reason": "exceeded_size_limit"}
                result["truncated"] = True
        
        # NVMe-specific logs (size-limited)
        if "nvme_smart_health_information_log" in raw_json:
            nvme_data = {
                "health_log": raw_json.get("nvme_smart_health_information_log"),
                "error_log": raw_json.get("nvme_error_log")
            }
            nvme_json = json.dumps(nvme_data)
            if len(nvme_json.encode('utf-8')) <= MAX_JSON_SIZE_BYTES:
                result["nvme_specific"] = nvme_data
            else:
                result["nvme_specific"] = {"truncated": True, "reason": "exceeded_size_limit"}
                result["truncated"] = True
        
        return jsonify(result), 200
    except Exception as e:
        logger.error(f"SMART details failed for {device}: {e}")
        return jsonify({"error": str(e)}), 500


@smart_bp.route("/api/admin/drives/<device>/smart-test", methods=["POST"])
@require_admin_auth
@limiter.limit("10 per minute")
def run_smart_test_endpoint(device):
    """Run a SMART self-test on a device.
    
    Phase 7 Feature G: SMART test runner
    """
    lock_acquired = False
    try:
        # Validate device name (lesson #9)
        if not is_valid_device_name(device):
            return jsonify({"error": "Invalid device name"}), 400
        
        # Build device path
        device_path = f"/dev/{device}"

        # Check if device is currently being wiped (safety guardrail)
        with ERASE_JOBS_LOCK:
            for job in ERASE_JOBS.values():
                if job.get("status") in {"running", "queued"}:
                    req = job.get("request", {})
                    if req.get("device") == device_path:
                        return jsonify({"error": "Cannot run SMART test while wipe is in progress"}), 409
        
        # Lesson #92: Atomic check-then-act for SMART test allocation
        # Use device-specific lock to prevent concurrent test starts
        with SMART_TEST_LOCKS_LOCK:
            if device_path not in SMART_TEST_LOCKS:
                SMART_TEST_LOCKS[device_path] = Lock()
        
        # Acquire device-specific lock (non-blocking to avoid deadlocks)
        device_lock = SMART_TEST_LOCKS[device_path]
        if not device_lock.acquire(blocking=False):
            return jsonify({"error": "A SMART test is already in progress on this device"}), 409
        
        lock_acquired = True  # Set immediately after successful acquire
        # Check if a SMART test is already running on this device (server-side guardrail)
        # NOTE: This database check is safe for single-process deployment (current setup with Werkzeug).
        # For multi-worker deployment (e.g., Gunicorn with gevent), this should be replaced with:
        # - Distributed locks (Redis) for cross-process mutual exclusion, OR
        # - Atomic database operations (unique constraint on device+status with transaction), OR
        # - Stick to single-worker deployment (w=1 in Gunicorn)
        # The in-memory SMART_TEST_LOCKS provide primary protection within a single process.
        try:
            from database import get_smart_test_history
            recent_tests = get_smart_test_history(device=device_path, limit=5)
            for test in recent_tests:
                if test.get("status") in {"started", "in_progress"}:
                    return jsonify({"error": "A SMART test is already in progress on this device"}), 409
        except Exception as e:
            logger.warning(f"Failed to check for concurrent SMART tests on {device}: {e}")
            # Proceed anyway - this is a safety check, not a hard requirement
        
        # Phase 7.4: Safety checks - block tests on OS/locked drives and dual-port secondary paths
        # Check if device is the OS drive
        try:
            os_dev, _ = get_os_by_path()
            if os_dev and os.path.realpath(device_path) == os.path.realpath(os_dev):
                logger.warning(f"SMART test rejected for {device}: device is OS drive")
                return jsonify({"error": "Cannot run SMART test on OS drive"}), 403
        except Exception as e:
            logger.warning(f"Failed to check OS drive status for {device}: {e}")
        
        # Check if device is mounted (indicates it's in use)
        try:
            lsblk_proc = subprocess.run(["lsblk", "-J", "-o", "NAME,MOUNTPOINT"], capture_output=True, text=True, timeout=10, shell=False)
            if lsblk_proc.returncode == 0:
                lsblk_data = json.loads(lsblk_proc.stdout)
                for blockdevice in lsblk_data.get("blockdevices", []):
                    dev_name = blockdevice.get("name", "")
                    # Normalize device name to match lsblk output (no /dev/ prefix)
                    norm_device = device.replace("/dev/", "")
                    if dev_name == norm_device:
                        if blockdevice.get("mountpoint") or blockdevice.get("mountpoints"):
                            logger.warning(f"SMART test rejected for {device}: device is mounted")
                            return jsonify({"error": "Cannot run SMART test on mounted drive"}), 403
                        # Check children for mountpoints
                        for child in blockdevice.get("children", []):
                            if child.get("mountpoint") or child.get("mountpoints"):
                                logger.warning(f"SMART test rejected for {device}: device partition is mounted")
                                return jsonify({"error": "Cannot run SMART test on mounted drive"}), 403
        except Exception as e:
            logger.warning(f"Failed to check mount status for {device}: {e}")
        
        # Check if device is a dual-port secondary path
        # This requires checking the current drive discovery data
        try:
            config_dir = get_config_dir()
            drives = discover_drives(os.path.join(config_dir, "bay_map.json"))
            for drive in drives:
                if drive.get("device") == device_path or drive.get("device") == device:
                    if drive.get("sas_secondary_path"):
                        logger.warning(f"SMART test rejected for {device}: device is dual-port secondary path")
                        return jsonify({"error": "Cannot run SMART test on dual-port secondary path. Use primary path instead."}), 403
                    if drive.get("locked"):
                        logger.warning(f"SMART test rejected for {device}: device is locked")
                        return jsonify({"error": "Cannot run SMART test on locked drive"}), 403
        except Exception as e:
            logger.warning(f"Failed to check secondary path status for {device}: {e}")
        
        # Get test type from request
        payload = request.get_json(silent=True) or {}
        test_type = payload.get("test_type", "short")
        
        # Import smart_parsing functions
        from smart_parsing import run_smart_test, get_smart_data
        from database import record_smart_test_run
        
        # Get serial and interface type for audit log and validation
        smart_data = get_smart_data(device_path)
        serial = smart_data.get("serial") if smart_data else None
        interface_type = smart_data.get("interface_type") if smart_data else None

        # Validate test_type against interface type (conveyance is SATA-only)
        if test_type == "conveyance":
            if not interface_type or str(interface_type).lower() not in ("sata", "ata"):
                return jsonify({"error": "Conveyance test is only supported on SATA/ATA devices"}), 400
        
        # Run the test
        test_result = run_smart_test(device_path, test_type)

        # Normalize test_type for audit log: smartctl uses "long" but DB schema expects "extended"
        audit_test_type = "extended" if test_type == "long" else test_type

        if "error" in test_result:
            # Record failed test attempt
            record_smart_test_run(device_path, serial, audit_test_type, "failed", result=test_result.get("error"))
            return jsonify(test_result), 400
        
        # Record test start in audit log and capture record ID for future updates
        test_record_id = record_smart_test_run(device_path, serial, audit_test_type, "started")
        
        # Store record ID in response for frontend to use in status polling
        test_result["record_id"] = test_record_id
        
        return jsonify(test_result), 200
    except Exception as e:
        logger.error(f"SMART test failed for {device}: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        # Only release the lock if it was acquired
        if lock_acquired:
            device_lock.release()
            # Clean up lock entry to prevent unbounded dict growth
            with SMART_TEST_LOCKS_LOCK:
                SMART_TEST_LOCKS.pop(device_path, None)


def _try_update_test_record(record_id, latest_test, device, new_status,
                            result=None, output_json=None):
    """Attempt to update a SMART test record with optimistic locking.

    Returns True if the update succeeded, False if the record was modified
    by another process (optimistic lock mismatch).
    """
    from database import update_smart_test_run
    current_updated_at = latest_test.get("updated_at")
    updated = update_smart_test_run(record_id, new_status, result=result,
                                    output_json=output_json,
                                    current_updated_at=current_updated_at)
    if not updated:
        logger.debug(f"SMART test {device} record was modified by another process, skipping update")
    return updated


@smart_bp.route("/api/admin/drives/<device>/smart-test-status")
@require_admin_auth
@limiter.limit("30 per minute")
def get_smart_test_status_endpoint(device):
    """Get the status of a running SMART self-test.
    
    Phase 7 Feature G: SMART test runner polling
    """
    try:
        # Validate device name (lesson #9)
        if not is_valid_device_name(device):
            return jsonify({"error": "Invalid device name"}), 400
        
        # Build device path
        device_path = f"/dev/{device}"
        
        # Import smart_parsing function
        from smart_parsing import get_smart_test_status
        from database import get_smart_test_history, update_smart_test_run
        
        # Get test status from drive (live data)
        status_result = get_smart_test_status(device_path)
        
        if "error" in status_result:
            return jsonify(status_result), 400
        
        # Check database for active test record and update if completed
        try:
            recent_tests = get_smart_test_history(device=device_path, limit=1)
            if recent_tests:
                latest_test = recent_tests[0]
                test_status = latest_test.get("status")
                record_id = latest_test.get("id")
                started_at = latest_test.get("started_at")
                
                # Include started_at in response for frontend grace period check
                status_result["started_at"] = started_at
                
                test_type = latest_test.get("test_type")
                
                # Transition: DB "started" → "in_progress" when drive confirms test is running.
                # This is critical: the drive's real-time status register (ata_smart_data.self_test.status)
                # confirms the test is actually running. Once we see this, we can trust subsequent
                # "completed"/"failed" from the log table. Without this transition, the DB stays "started"
                # and we can't distinguish between the current test's completion and a previous test's
                # stale log entry.
                if test_status == "started" and status_result.get("status") == "in_progress":
                    if _try_update_test_record(record_id, latest_test, device, "in_progress"):
                        logger.debug(f"SMART test {device} confirmed in progress by drive status register")
                
                # If database shows test running but drive shows completed, update database.
                # Use should_trust_completion_status: when DB is "started" (never confirmed running),
                # the "completed" is likely from a previous test's log entry — don't trust it until
                # the estimated test duration has elapsed.
                elif test_status in ("started", "in_progress") and status_result.get("status") == "completed":
                    if should_trust_completion_status(started_at, test_status, test_type):
                        # Determine pass/fail: prefer the reliable status.passed boolean
                        latest_result = status_result.get("latest_result", {})
                        drive_status = latest_result.get("status", "").lower()
                        passed = latest_result.get("passed")
                        
                        if passed is True:
                            result = "passed"
                        elif passed is False:
                            result = "failed"
                        elif ("passed" in drive_status or "completed without error" in drive_status or "completed" in drive_status) and "failed" not in drive_status:
                            result = "passed"
                        elif "failed" in drive_status or "error" in drive_status:
                            result = "failed"
                        else:
                            result = "unknown"
                        
                        logger.debug(f"SMART test {device} completed with drive_status={drive_status}, passed={passed}, result={result}")
                        _try_update_test_record(record_id, latest_test, device, "completed",
                                                result=result,
                                                output_json=status_result.get("self_test_log_table"))
                # If database shows test running but drive shows failed, update database
                elif test_status in ("started", "in_progress") and status_result.get("status") == "failed":
                    if should_trust_completion_status(started_at, test_status, test_type):
                        logger.debug(f"SMART test {device} failed according to drive status")
                        _try_update_test_record(record_id, latest_test, device, "failed",
                                                result="failed",
                                                output_json=status_result.get("self_test_log_table"))
                # If database shows test running but drive shows aborted, update database
                elif test_status in ("started", "in_progress") and status_result.get("status") == "aborted":
                    if should_trust_completion_status(started_at, test_status, test_type):
                        logger.debug(f"SMART test {device} aborted according to drive status")
                        _try_update_test_record(record_id, latest_test, device, "failed",
                                                result="aborted",
                                                output_json=status_result.get("self_test_log_table"))
                # If database shows test running but drive shows no_tests/unknown after grace period,
                # the test is no longer running but we can't determine pass/fail. Mark as completed
                # with unknown result so the card stops showing "running".
                elif test_status in ("started", "in_progress") and status_result.get("status") in ("no_tests", "unknown"):
                    if should_trust_completion_status(started_at, test_status, test_type):
                        logger.debug(f"SMART test {device} no longer running (status={status_result.get('status')}), marking completed with unknown result")
                        _try_update_test_record(record_id, latest_test, device, "completed",
                                                result="unknown")
        except Exception as e:
            logger.warning(f"Failed to update SMART test database record for {device}: {e}")
        
        return jsonify(status_result), 200
    except Exception as e:
        logger.error(f"SMART test status failed for {device}: {e}")
        return jsonify({"error": str(e)}), 500
