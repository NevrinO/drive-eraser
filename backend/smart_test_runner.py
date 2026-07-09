# SMART self-test execution and status polling
# Depends on: smart_utils, smart_data_parsing

import subprocess
import json
import logging

from disk_utils import get_command_path, run_command
from smart_constants import correct_self_test_log_hours
from smart_utils import validate_device_path
from smart_data_parsing import get_smart_data
from smart_db import get_historical_poh_for_serial

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


def _parse_scsi_self_test_entries(data):
    """Parse scsi_self_test_N entries from smartctl JSON output.

    Returns:
        List of dicts with keys: code, result_str, result_value, hours, passed.
        Sorted by key name (same order as smartctl output).
    """
    entries = []
    for k in sorted(data.keys()):
        if k.startswith("scsi_self_test_"):
            test_data = data[k]
            result_value = test_data.get("result", {}).get("value")
            entries.append({
                "code": test_data.get("code", {}).get("string", "unknown"),
                "result_str": test_data.get("result", {}).get("string", "unknown"),
                "result_value": result_value,
                "hours": test_data.get("power_on_time", {}).get("hours"),
                "passed": result_value == 0,
            })
    return entries


def _scsi_in_progress_result(entry):
    """Build an in-progress status dict from a parsed SCSI self-test entry."""
    return {
        "status": "in_progress",
        "percentage": 50,
        "self_test_log_table": None,
        "latest_result": {
            "type": entry["code"],
            "status": entry["result_str"],
            "passed": None,
            "remaining": 0,
            "lba": None,
            "hours": entry["hours"]
        }
    }


def _check_ata_in_progress(data):
    """Check if an ATA/SATA self-test is currently in progress.

    Uses ata_smart_data.self_test.status (the drive's status register),
    which updates immediately during a test — unlike the log table which
    only updates when a test completes.

    Returns:
        In-progress result dict, or None if no test is running.
    """
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
    return None


def _check_nvme_in_progress(data):
    """Check if an NVMe self-test is currently in progress.

    Uses nvme_self_test_log.current_operation.status.value, which is 0
    when no test is running and non-zero when a test is in progress.

    Returns:
        In-progress result dict, or None if no test is running.
    """
    nvme_log = data.get("nvme_self_test_log", {})
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
    return None


def _check_scsi_in_progress(data):
    """Check if a SCSI/SAS self-test is currently in progress.

    Uses two mechanisms:
    1. scsi_ie ASC 0x3F/ASCQ 0x0E (Informational Exceptions)
    2. scsi_self_test_N entries with result value 15 or "in progress" string

    Returns:
        In-progress result dict, or None if no test is running.
    """
    scsi_ie = data.get("scsi_ie", {})
    scsi_asc = scsi_ie.get("asc", "")
    scsi_ascq = scsi_ie.get("ascq", "")

    if scsi_asc == 0x3F and scsi_ascq == 0x0E:
        return {
            "status": "in_progress",
            "percentage": 50,
            "self_test_log_table": None,
            "latest_result": {
                "type": "unknown",
                "status": scsi_ie.get("string", "Self test in progress"),
                "remaining": 0,
                "lba": None,
                "hours": None
            }
        }
    for entry in _parse_scsi_self_test_entries(data):
        if entry["result_value"] == 15 or "in progress" in entry["result_str"].lower():
            return _scsi_in_progress_result(entry)
    return None


def _parse_ata_test_status(data, device_path, diagnostics):
    """Parse completed ATA/SATA self-test status from smartctl JSON data.

    Extracts the self-test log table (standard/extended/table formats),
    parses the latest entry, applies POH rollover correction, and maps
    status strings to test_status enum values.

    Returns:
        Result dict with status/percentage/latest_result, or None if no
        ATA self-test log table is present (not an ATA device).
    """
    self_test_log = data.get("ata_smart_self_test_log", {})
    table = (self_test_log.get("standard", {}).get("table", [])
             or self_test_log.get("extended", {}).get("table", [])
             or self_test_log.get("table", []))

    if not table:
        return None

    latest = table[0]
    test_type = latest.get("type", {}).get("string", "unknown")
    status_obj = latest.get("status", {})
    status = status_obj.get("string", "unknown")
    passed = status_obj.get("passed")
    remaining_raw = status_obj.get("remaining_percent", status_obj.get("remaining", 0))
    remaining = None if remaining_raw == "null" or remaining_raw is None else remaining_raw
    log_hours = latest.get("hours") or latest.get("lifetime_hours")

    corrected_hours = log_hours
    rollover_corrected = False
    ambiguous = False

    try:
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
                historical_poh = get_historical_poh_for_serial(serial)
            except Exception as e:
                logger.warning(f"Failed to get historical POH for {serial}: {e}")

        corrected_hours, rollover_corrected, ambiguous = correct_self_test_log_hours(log_hours, current_poh, historical_poh)
    except Exception as e:
        logger.warning(f"POH correction failed for {device_path}: {e}")
        corrected_hours = log_hours

    percentage = 0
    if remaining is not None and remaining > 0:
        percentage = max(0, min(100, (90 - remaining) / 90 * 100))

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


def _parse_nvme_test_status(data, device_path=None, diagnostics=None):
    """Parse completed NVMe self-test status from smartctl JSON data.

    Extracts NVMe self-test log results and parses the latest entry.

    Returns:
        Result dict with status/percentage/latest_result, or None if no
        NVMe self-test results are present (not an NVMe device).
    """
    nvme_log = data.get("nvme_self_test_log", {})
    nvme_results = nvme_log.get("results", [])

    if not nvme_results:
        return None

    latest = nvme_results[0] if nvme_results else None
    if latest:
        test_type = latest.get("self_test_num", "unknown")
        status = latest.get("result", {}).get("string", "unknown")
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
    return None


def _parse_scsi_test_status(data, device_path=None, diagnostics=None):
    """Parse completed SCSI/SAS self-test status from smartctl JSON data.

    Handles two cases:
    1. SCSI with IE log (scsi_ie present) — returns "no_tests" status
    2. SCSI without IE log — fallback scanning via scsi_self_test_N entries

    Returns:
        Result dict with status/percentage/latest_result, or None if no
        SCSI data is present (not a SCSI device).
    """
    scsi_ie = data.get("scsi_ie", {})

    if scsi_ie:
        return {
            "status": "no_tests",
            "percentage": 0,
            "self_test_log_table": None,
            "latest_result": {
                "type": "unknown",
                "status": scsi_ie.get("string", "unknown"),
                "remaining": 0,
                "lba": None,
                "hours": None
            }
        }

    for entry in _parse_scsi_self_test_entries(data):
        if entry["result_value"] == 15 or "in progress" in entry["result_str"].lower():
            return _scsi_in_progress_result(entry)
        else:
            result_str = entry["result_str"]
            if entry["result_value"] == 0:
                test_status = "completed"
            elif "failed" in result_str.lower() or "error" in result_str.lower():
                test_status = "failed"
            elif "aborted" in result_str.lower():
                test_status = "aborted"
            else:
                test_status = "unknown"
            return {
                "status": test_status,
                "percentage": 100 if test_status == "completed" else 0,
                "self_test_log_table": None,
                "latest_result": {
                    "type": entry["code"],
                    "status": result_str,
                    "passed": entry["passed"],
                    "remaining": 0,
                    "lba": None,
                    "hours": entry["hours"]
                }
            }

    return None


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

        # Real-time in-progress checks (early return if any match)
        for checker in (_check_ata_in_progress, _check_nvme_in_progress, _check_scsi_in_progress):
            in_progress = checker(data)
            if in_progress:
                return in_progress

        # Completed test parsing — dispatch by device type
        for parser in (_parse_ata_test_status, _parse_nvme_test_status, _parse_scsi_test_status):
            result = parser(data, device_path, diagnostics)
            if result is not None:
                return result

        return {"status": "no_tests", "self_test_log_table": None, "latest_result": None}
    except json.JSONDecodeError:
        return {"error": "Failed to parse smartctl output", "status": "failed", "self_test_log_table": None}
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError) as e:
        return {"error": f"System error getting test status: {str(e)}", "status": "failed", "self_test_log_table": None}
    except Exception as e:
        return {"error": f"Exception getting test status: {str(e)}", "status": "failed", "self_test_log_table": None}
