# Pre-wipe health gate — prevents starting wipes on failing drives
# Depends on: smart_utils, smart_health, smart_data_parsing

import os
import logging

from disk_utils import safe_int
from smart_utils import validate_device_path
from smart_health import calculate_drive_health_score, get_drive_recommendation
from smart_data_parsing import get_smart_data, get_triage_thresholds

logger = logging.getLogger(__name__)


def pre_wipe_health_gate(device, interface_type, policy, diagnostics=None):
    """Pre-wipe health gate check to prevent starting wipes on failing drives.

    Args:
        device: Device path (e.g., "/dev/sda")
        interface_type: Interface type ("sata", "sas", "nvme")
        policy: Policy dict from load_policy()
        diagnostics: Optional diagnostics dict for logging

    Returns:
        Dict with structure:
        {
            "ok": true/false,
            "blocked": true/false,
            "block_reason": "string" or None,
            "health_score": int,
            "recommendation": "DESTROY"/"SCRATCH"/"USED_GOOD"/"NEW_STOCK"/"USED_HEAVY",
            "smart_status": "PASSED"/"FAILED"/"UNKNOWN",
            "penalty_breakdown": {...},
            "details": {
                "pending_sectors": int,
                "reallocated_sectors": int,
                "interface_errors": int,
                "sas_grown_defect_list": int or None,
                "sas_scan_status": str or None,
                "model_risk": "normal"/"high_risk",
                "health_score_delta": int or None
            }
        }
    """
    # Validate device path to prevent path traversal (Lesson #13)
    if not validate_device_path(device):
        return {
            "ok": False,
            "blocked": True,
            "block_reason": "invalid_device_path",
            "health_score": 0,
            "recommendation": "DESTROY",
            "smart_status": "UNKNOWN",
            "penalty_breakdown": {},
            "details": {}
        }

    # Load policy settings with fallback defaults
    gate_enabled = policy.get("prewipe_health_gate_enabled", True)
    if not gate_enabled:
        return {
            "ok": True,
            "blocked": False,
            "block_reason": None,
            "health_score": 100,
            "recommendation": "USED_GOOD",
            "smart_status": "UNKNOWN",
            "penalty_breakdown": {},
            "details": {}
        }

    block_destroy = policy.get("prewipe_health_gate_block_destroy", True)
    block_scratch = policy.get("prewipe_health_gate_block_scratch", False)
    block_failed_smart = policy.get("prewipe_health_gate_block_failed_smart", True)
    max_pending = policy.get("prewipe_health_gate_max_pending_sectors", 10)
    max_reallocated = policy.get("prewipe_health_gate_max_reallocated_sectors", 5)
    max_interface_errors = policy.get("prewipe_health_gate_max_interface_errors", 100)

    # Re-read SMART data fresh (not cached)
    smart = get_smart_data(device, diagnostics)
    if not smart or smart.get("status") == "UNKNOWN":
        return {
            "ok": False,
            "blocked": True,
            "block_reason": "drive_not_accessible",
            "health_score": 0,
            "recommendation": "DESTROY",
            "smart_status": "UNKNOWN",
            "penalty_breakdown": {},
            "details": {}
        }

    # Calculate health score and recommendation
    thresholds = get_triage_thresholds()
    health_score, penalty_breakdown = calculate_drive_health_score(interface_type, smart, thresholds=thresholds)
    recommendation = get_drive_recommendation(interface_type, smart, health_score=health_score, thresholds=thresholds)
    smart_status = smart.get("status", "UNKNOWN")

    # Extract critical attributes
    pending = safe_int(smart.get("pending_sectors"), 0)
    reallocated = safe_int(smart.get("reallocated_sectors"), 0)
    interface_errors = safe_int(smart.get("interface_errors"), 0)
    sas_grown_defect_list = smart.get("sas_grown_defect_list")
    sas_scan_status = smart.get("sas_scan_status")
    sas_uncorrectable_verify_errors = safe_int(smart.get("sas_uncorrectable_verify_errors"), 0)
    sas_uncorrectable_write_errors = safe_int(smart.get("sas_uncorrectable_write_errors"), 0)
    sas_uncorrectable_read_errors = safe_int(smart.get("sas_uncorrectable_read_errors"), 0)
    sas_sticky_lba = smart.get("sas_sticky_lba_detected", False)
    model_profile = smart.get("model_profile")

    # Check device state from sysfs
    device_name = os.path.basename(device) if device else ""
    device_state = "unknown"
    if device_name:
        try:
            state_path = f"/sys/block/{device_name}/device/state"
            with open(state_path, "r") as f:
                device_state = f.read().strip()
        except (FileNotFoundError, OSError):
            pass
        except Exception as e:
            logger.warning(f"Failed to read device state from {state_path}: {e}")

    # Model risk profile check
    model_risk = "normal"
    if model_profile and isinstance(model_profile, dict):
        if model_profile.get("high_risk"):
            model_risk = "high_risk"

    # Intake history comparison (placeholder - requires database integration)
    health_score_delta = None

    # Build details dict
    details = {
        "pending_sectors": pending,
        "reallocated_sectors": reallocated,
        "interface_errors": interface_errors,
        "sas_grown_defect_list": sas_grown_defect_list,
        "sas_scan_status": sas_scan_status,
        "model_risk": model_risk,
        "health_score_delta": health_score_delta,
        "device_state": device_state
    }

    # Check blocking conditions
    block_reason = None

    # 1. SMART status FAILED
    if block_failed_smart and smart_status == "FAILED":
        block_reason = "smart_status_failed"

    # 2. Health score below DESTROY threshold
    destroy_threshold = thresholds.get("health_score_destroy_threshold", 25)
    if block_destroy and health_score <= destroy_threshold:
        block_reason = "health_score_below_destroy_threshold"

    # 3. Recommendation is DESTROY
    if block_destroy and recommendation.get("status") == "DESTROY":
        block_reason = "recommendation_destroy"

    # 4. Recommendation is SCRATCH (if configured)
    if block_scratch and recommendation.get("status") == "SCRATCH":
        block_reason = "recommendation_scratch"

    # 5. Critical attribute thresholds
    if pending > max_pending:
        block_reason = "pending_sectors_exceeded"

    if reallocated > max_reallocated:
        block_reason = "reallocated_sectors_exceeded"

    if interface_errors > max_interface_errors:
        block_reason = "interface_errors_exceeded"

    # 6. NVMe-specific checks
    if interface_type == "nvme":
        nvme_available_spare = smart.get("reallocated_normalized")  # Available spare is stored here for NVMe
        if nvme_available_spare is not None and nvme_available_spare < 10:
            block_reason = "nvme_available_spare_low"

        nvme_critical_warning = safe_int(smart.get("_nvme_critical_warning"), 0)
        if nvme_critical_warning & 0x04 or nvme_critical_warning & 0x08:
            block_reason = "nvme_critical_warning"

    # 7. SAS-specific checks
    if interface_type == "sas":
        sas_grown_defect_fail_threshold = thresholds.get("sas_grown_defect_fail_threshold", 10000)
        if sas_grown_defect_list is not None and sas_grown_defect_list > sas_grown_defect_fail_threshold:
            block_reason = "sas_grown_defect_list_exceeded"

        if sas_scan_status and "halted" in str(sas_scan_status).lower():
            block_reason = "sas_scan_halted"

        if sas_uncorrectable_verify_errors >= 1:
            block_reason = "sas_uncorrectable_verify_error"

        if sas_uncorrectable_write_errors >= 1:
            block_reason = "sas_uncorrectable_write_error"

        if sas_uncorrectable_read_errors >= 10:
            block_reason = "sas_uncorrectable_read_errors_exceeded"

        if sas_sticky_lba:
            block_reason = "sas_sticky_lba_detected"

    # 8. Device state check
    if device_state in ("offline", "removed"):
        block_reason = "device_offline_or_removed"

    # Determine if blocked
    blocked = block_reason is not None

    return {
        "ok": True,
        "blocked": blocked,
        "block_reason": block_reason,
        "health_score": health_score,
        "recommendation": recommendation.get("status", "UNKNOWN"),
        "smart_status": smart_status,
        "penalty_breakdown": penalty_breakdown,
        "details": details
    }
