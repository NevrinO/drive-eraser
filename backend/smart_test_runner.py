# SMART self-test execution and status polling
# Depends on: smart_utils, smart_data_parsing

import subprocess
import json
import logging

from disk_utils import get_command_path, run_command
from smart_constants import correct_self_test_log_hours
from smart_utils import validate_device_path
from smart_data_parsing import get_smart_data

logger = logging.getLogger(__name__)


def run_smart_test(device, test_type, diagnostics=None):
    """Run a SMART self-test on a device.

    Args:
        device: Device path (e.g., "/dev/sda")
        test_type: Test type - "short", "extended", "offline", "conveyance" (SATA only), "long" (SAS alias for extended)
        diagnostics: Optional diagnostics dict for logging

    Returns:
        Dict with test_type, status, estimated_minutes, poll_command, or error
    """
    # Validate device path (lesson #9, #13)
    if not validate_device_path(device):
        return {"error": "Invalid device path", "status": "failed"}

    # Normalize test type
    test_type = str(test_type).lower()
    if test_type == "extended":
        test_type = "long"  # smartctl uses "long" for extended tests

    # Validate test type
    valid_test_types = {"short", "long", "offline", "conveyance"}
    if test_type not in valid_test_types:
        return {"error": f"Invalid test type: {test_type}. Must be one of {valid_test_types}", "status": "failed"}

    # Build device path
    device_path = f"/dev/{device}" if not device.startswith("/dev/") else device

    # Get smartctl command
    smartctl_cmd = get_command_path("smartctl")
    if not smartctl_cmd:
        return {"error": "smartctl command not found", "status": "failed"}

    # Estimated time for tests (in minutes)
    estimated_minutes = {
        "short": 2,
        "long": 120,
        "offline": 5,
        "conveyance": 5
    }.get(test_type, 2)

    # Timeout for smartctl command (in seconds)
    # The -t flag just initiates the test and returns immediately (within seconds)
    # Use 30 seconds for all test types to prevent hanging if smartctl is unresponsive
    timeout_seconds = 30

    try:
        # Uses subprocess.run directly instead of run_command because:
        # 1. run_command uses check=True (raises on non-zero exit), but smartctl -t returns
        #    non-zero for recoverable errors (e.g. test already in progress) that need custom handling
        # 2. run_command only returns stdout; we need result.returncode and result.stderr for diagnostics
        result = subprocess.run(
            ["sudo", smartctl_cmd, "-t", test_type, device_path],
            capture_output=True,
            text=True,
            check=False,
            shell=False,
            timeout=timeout_seconds
        )
        if result.returncode != 0:
            return {"error": f"smartctl command failed with exit code {result.returncode}: {result.stderr}", "status": "failed"}

        # Check if test started successfully
        if "Self-test started" in result.stdout or "Test has begun" in result.stdout or "Testing has begun" in result.stdout:
            return {
                "test_type": test_type,
                "status": "started",
                "estimated_minutes": estimated_minutes,
                "poll_command": f"{smartctl_cmd} -l selftest {device_path}"
            }
        else:
            return {"error": f"Failed to start test - smartctl output: {result.stdout}", "status": "failed"}
    except subprocess.TimeoutExpired:
        return {"error": f"smartctl command timed out after {timeout_seconds} seconds", "status": "failed"}
    except (OSError, FileNotFoundError) as e:
        return {"error": f"System error running test: {str(e)}", "status": "failed"}
    except Exception as e:
        return {"error": f"Exception running test: {str(e)}", "status": "failed"}


def get_smart_test_status(device, diagnostics=None):
    """Get the status of a running SMART self-test.

    Args:
        device: Device path (e.g., "/dev/sda")
        diagnostics: Optional diagnostics dict for logging

    Returns:
        Dict with status, percentage, latest_result, or error
    """
    # Validate device path (lesson #9, #13)
    if not validate_device_path(device):
        return {"error": "Invalid device path", "status": "failed", "self_test_log_table": None}

    # Build device path
    device_path = f"/dev/{device}" if not device.startswith("/dev/") else device

    # Get smartctl command
    smartctl_cmd = get_command_path("smartctl")
    if not smartctl_cmd:
        return {"error": "smartctl command not found", "status": "failed", "self_test_log_table": None}

    try:
        # Use -a to get both the real-time self-test status register AND the log table.
        # ata_smart_data.self_test.status updates immediately while a test runs;
        # ata_smart_self_test_log only updates when a test completes, so checking
        # the log alone causes false "completed" detection during an active test.
        result = run_command([smartctl_cmd, "-j", "-a", device_path], diagnostics, "smartctl")
        if not result:
            return {"error": "Failed to read self-test log", "status": "failed", "self_test_log_table": None}

        data = json.loads(result)

        # ATA/SATA real-time in-progress check: ata_smart_data.self_test.status is the
        # drive's status register, updated immediately during a test.  The log table
        # (ata_smart_self_test_log) shows the PREVIOUS completed test while a new one runs.
        ata_current_test = data.get("ata_smart_data", {}).get("self_test", {}).get("status", {})
        if "in progress" in ata_current_test.get("string", "").lower():
            remaining = ata_current_test.get("remaining_percent", 50)
            percentage = max(0, min(100, (90 - remaining) / 90 * 100)) if remaining is not None else 0
            return {
                "status": "in_progress",
                "percentage": round(percentage, 1),
                "self_test_log_table": None,
                "latest_result": {
                    "type": "unknown",
                    "status": ata_current_test.get("string", ""),
                    "passed": None,
                    "remaining": remaining,
                    "lba": None,
                    "hours": None,
                    "corrected_hours": None,
                    "rollover_corrected": False,
                    "ambiguous": False
                }
            }

        # Check for ATA/SATA self-test log
        # smartctl JSON nests the table under "standard" (for -l selftest) or "extended" (for -x/-l xselftest)
        self_test_log = data.get("ata_smart_self_test_log", {})
        table = (self_test_log.get("standard", {}).get("table", [])
                 or self_test_log.get("extended", {}).get("table", [])
                 or self_test_log.get("table", []))

        # Check for NVMe self-test log
        nvme_log = data.get("nvme_self_test_log", {})
        nvme_results = nvme_log.get("results", [])

        # NVMe real-time in-progress check: current_operation.status.value is 0 when
        # no test is running; non-zero values indicate a test type is in progress.
        nvme_current_op = nvme_log.get("current_operation", {})
        if nvme_current_op.get("status", {}).get("value", 0) != 0:
            completion_pct = nvme_current_op.get("completion_percent", 0)
            return {
                "status": "in_progress",
                "percentage": float(completion_pct),
                "self_test_log_table": None,
                "latest_result": {
                    "type": "unknown",
                    "status": nvme_current_op.get("status", {}).get("string", ""),
                    "remaining": 100 - completion_pct,
                    "lba": None,
                    "hours": None
                }
            }

        # Check for SCSI/SAS self-test log (via SCSI Informational Exceptions)
        scsi_ie = data.get("scsi_ie", {})
        scsi_asc = scsi_ie.get("asc", "")
        scsi_ascq = scsi_ie.get("ascq", "")

        # Determine device type and process accordingly
        if table:
            # ATA/SATA device: log table reflects completed tests only.
            # Real-time in-progress detection is handled above via ata_smart_data.self_test.status.
            latest = table[0]
            test_type = latest.get("type", {}).get("string", "unknown")
            status_obj = latest.get("status", {})
            status = status_obj.get("string", "unknown")
            passed = status_obj.get("passed")
            remaining_raw = status_obj.get("remaining_percent", status_obj.get("remaining", 0))
            # Convert string "null" to actual None to avoid frontend workarounds
            remaining = None if remaining_raw == "null" or remaining_raw is None else remaining_raw
            log_hours = latest.get("hours") or latest.get("lifetime_hours")

            # SMART self-test log hours use 16-bit counters (max 65,535).
            # Apply multi-rollover correction if needed.
            corrected_hours = log_hours
            rollover_corrected = False
            ambiguous = False

            try:
                # Check drive cache first to avoid expensive smartctl call during polling
                current_poh = None
                serial = None
                from disk_ops import get_cached_smart_data
                cached_payload = get_cached_smart_data(device_path)
                if cached_payload:
                    smart_info = cached_payload.get('smart')
                    if smart_info:
                        current_poh = smart_info.get('power_on_hours')
                        serial = smart_info.get('serial')

                if current_poh is None:
                    current_smart = get_smart_data(device_path, diagnostics)
                    current_poh = current_smart.get("power_on_hours")
                    serial = current_smart.get("serial")

                historical_poh = None
                if serial:
                    try:
                        from database import get_historical_poh_for_serial
                        historical_poh = get_historical_poh_for_serial(serial)
                    except Exception as e:
                        logger.warning(f"Failed to get historical POH for {serial}: {e}")

                corrected_hours, rollover_corrected, ambiguous = correct_self_test_log_hours(log_hours, current_poh, historical_poh)
            except Exception:
                # If we can't get current POH, use raw log hours
                corrected_hours = log_hours

            # Calculate percentage complete
            # remaining is 0-90 for in-progress tests, 0 for completed
            percentage = 0
            if remaining is not None and remaining > 0:
                percentage = max(0, min(100, (90 - remaining) / 90 * 100))

            # Map status strings; prefer the reliable status.passed boolean when present
            if "in progress" in status.lower() or "running" in status.lower():
                test_status = "in_progress"
            elif passed is True:
                test_status = "completed"
            elif "failed" in status.lower() or passed is False:
                test_status = "failed"
            elif "aborted" in status.lower():
                test_status = "aborted"
            elif "completed" in status.lower() or "passed" in status.lower():
                test_status = "completed"
            else:
                test_status = "unknown"

            return {
                "status": test_status,
                "percentage": round(percentage, 1),
                "self_test_log_table": table,
                "latest_result": {
                    "type": test_type,
                    "status": status,
                    "passed": passed,
                    "remaining": remaining,
                    "lba": latest.get("lba"),
                    "hours": log_hours,
                    "corrected_hours": corrected_hours,
                    "rollover_corrected": rollover_corrected,
                    "ambiguous": ambiguous
                }
            }
        elif nvme_results:
            # NVMe device
            latest = nvme_results[0] if nvme_results else None
            if latest:
                test_type = latest.get("self_test_num", "unknown")
                status = latest.get("result", {}).get("string", "unknown")
                # NVMe doesn't provide percentage, use 0 or 100 based on status
                percentage = 100 if "complete" in status.lower() else 0
                
                if "in progress" in status.lower() or "running" in status.lower():
                    test_status = "in_progress"
                elif "complete" in status.lower() or "success" in status.lower():
                    test_status = "completed"
                elif "failed" in status.lower() or "error" in status.lower():
                    test_status = "failed"
                elif "aborted" in status.lower():
                    test_status = "aborted"
                else:
                    test_status = "unknown"

                return {
                    "status": test_status,
                    "percentage": percentage,
                    "self_test_log_table": None,
                    "latest_result": {
                        "type": test_type,
                        "status": status,
                        "remaining": 0,
                        "lba": None,
                        "hours": None
                    }
                }
        elif scsi_ie:
            # SCSI/SAS device - check for self-test in progress via ASC/ASCQ
            # ASC 0x3F, ASCQ 0x0E indicates self-test in progress
            test_status = "no_tests"
            percentage = 0
            
            if scsi_asc == 0x3F and scsi_ascq == 0x0E:
                test_status = "in_progress"
                percentage = 50  # SAS doesn't provide percentage, use midpoint
            
            return {
                "status": test_status,
                "percentage": percentage,
                "self_test_log_table": None,
                "latest_result": {
                    "type": "unknown",
                    "status": scsi_ie.get("string", "unknown"),
                    "remaining": 0,
                    "lba": None,
                    "hours": None
                }
            }
        else:
            return {"status": "no_tests", "self_test_log_table": None, "latest_result": None}
    except json.JSONDecodeError:
        return {"error": "Failed to parse smartctl output", "status": "failed", "self_test_log_table": None}
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError) as e:
        return {"error": f"System error getting test status: {str(e)}", "status": "failed", "self_test_log_table": None}
    except Exception as e:
        return {"error": f"Exception getting test status: {str(e)}", "status": "failed", "self_test_log_table": None}
