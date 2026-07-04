import os
import uuid
from datetime import datetime, timezone

from smart_parsing import pre_wipe_health_gate
from disk_ops import get_os_by_path
from erase_commands import get_device_capacity_bytes


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


def check_health_gate_sync(device, interface_type, policy, health_gate_override=False):
    """Synchronous health gate check for use by API routes before starting a job.

    Returns a dict:
        {"blocked": False} if not blocked or override accepted.
        {"blocked": True, "error_code": "pre_wipe_health_check_failed",
         "block_reason": "...", "override_available": bool} if blocked.
    """
    health_gate_result = pre_wipe_health_gate(device, interface_type, policy)
    if not health_gate_result.get("blocked"):
        return {"blocked": False}

    block_reason = health_gate_result.get("block_reason")
    strict_mode = policy.get("prewipe_health_gate_strict_mode", False)
    strict_audit_mode = policy.get("strict_audit_mode", False)
    override_allowed = not strict_mode and not strict_audit_mode

    if health_gate_override and override_allowed:
        return {"blocked": False, "override_accepted": True, "block_reason": block_reason}

    return {
        "blocked": True,
        "error_code": "pre_wipe_health_check_failed",
        "block_reason": block_reason,
        "override_available": override_allowed,
    }


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
            "capacity_bytes": validated["drive"].get("smart", {}).get("capacity_bytes") or get_device_capacity_bytes(validated["device"]) or (100 * 1024 * 1024 * 1024),
            "data_written_at_wipe": None,
        },
    }
