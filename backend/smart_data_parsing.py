# SMART data parsing — get_smart_data, get_smart_identity, triage thresholds, drive model loading
# Depends on: smart_utils

import json
import os
import logging
import threading

from disk_utils import get_command_path, safe_int, safe_float, format_capacity_bytes, run_command, read_seagate_cache_writes, flush_drive_cache
from common import load_policy, get_config_dir
from smart_utils import detect_interface_type, validate_device_path

logger = logging.getLogger(__name__)

_DRIVE_MODELS_CACHE = {'data': None, 'mtime': None}
_DRIVE_MODELS_LOCK = threading.Lock()

_DEFAULT_TRIAGE_THRESHOLDS = {
    "ssd_new_poh_threshold": 720,
    "ssd_high_poh_threshold": 43800,
    "hdd_new_poh_threshold": 720,
    "health_score_destroy_threshold": 25,
    "health_score_scratch_threshold": 50,
    "health_score_good_threshold": 75,
    "ssd_new_fdw_threshold": 0.06,
    "hdd_new_fdw_threshold": 2.0,
    "realloc_raw_new_threshold": 0,
    "sas_grown_defect_fail_threshold": 10000,
    "sas_nme_advisory_threshold": 1000000,
    "sas_nme_penalty_threshold": 100000000,
    "sas_sticky_lba_threshold": 3,
    "sas_high_poh_threshold": 50000
}

def _load_drive_models():
    """Load drive_models.json with file-mtime-based caching."""
    try:
        config_dir = get_config_dir()
        path = os.path.join(config_dir, "drive_models.json")
        with _DRIVE_MODELS_LOCK:
            mtime = os.path.getmtime(path)
            if _DRIVE_MODELS_CACHE['data'] is not None and _DRIVE_MODELS_CACHE['mtime'] == mtime:
                return _DRIVE_MODELS_CACHE['data']
            with open(path, "r") as f:
                data = json.load(f)
            _DRIVE_MODELS_CACHE['data'] = data
            _DRIVE_MODELS_CACHE['mtime'] = mtime
            return data
    except (OSError, json.JSONDecodeError):
        return None

def get_triage_thresholds():
    """Load triage thresholds from policy.json with fallback defaults."""
    try:
        config_dir = get_config_dir()
        policy = load_policy(config_dir)
        thresholds = policy.get("triage_thresholds", {})
        return {key: thresholds.get(key, default) for key, default in _DEFAULT_TRIAGE_THRESHOLDS.items()}
    except Exception:
        return _DEFAULT_TRIAGE_THRESHOLDS.copy()

def _make_empty_template(smart_polling=True):
    """Create a SMART data template with all fields set to None/UNKNOWN.

    Used as the early-return value when device validation or smartctl fails.
    """
    return {
        "status": "UNKNOWN", "model": None, "serial": None, "capacity_str": "-", "capacity_bytes": None,
        "wear_level": None, "reallocated_sectors": None, "pending_sectors": None, "power_on_hours": None,
        "power_on_days": None, "temperature": None, "interface_errors": None, "data_written_raw": None,
        "data_written_bytes": None, "data_read_raw": None, "data_read_bytes": None, "reallocated_normalized": None, "reallocated_threshold": None, "raw": None,
        "rotation_rate": None,
        "sas_grown_defect_list": None, "sas_scan_status": None, "sas_non_medium_errors": None,
        "sas_uncorrectable_read_errors": None, "sas_uncorrectable_write_errors": None, "sas_uncorrectable_verify_errors": None,
        "sas_scan_event_count": None, "sas_scan_unique_lbas": None, "sas_sticky_lba_detected": None,
        "model_profile": None, "interface_type": None, "smart_polling": smart_polling,
        "write_counter_source": None
    }

def get_smart_identity(device, diagnostics=None):
    """Get basic device identity information using smartctl -j -i (fast, ~0.5s per drive).

    Returns minimal device info (model, serial, capacity, device type) with
    smart_polling: true flag to indicate extended SMART data should be collected
    in the background.

    Args:
        device: Device path (e.g., "/dev/sda")
        diagnostics: Optional diagnostics dict for logging

    Returns:
        Dict with basic device info and smart_polling: true flag
    """
    empty_template = _make_empty_template(smart_polling=True)
    if not validate_device_path(device):
        return empty_template
    smartctl_cmd = get_command_path("smartctl")
    if not smartctl_cmd: return empty_template
    raw_output = run_command([smartctl_cmd, "-j", "-i", device], diagnostics, "smartctl")
    if not raw_output: return empty_template
    try: data = json.loads(raw_output)
    except Exception: return empty_template

    model = data.get("model_name") or data.get("model_number") or data.get("device", {}).get("product")
    serial = data.get("serial_number")
    capacity_bytes = data.get("user_capacity", {}).get("bytes") or data.get("capacity", {}).get("bytes")
    capacity_str = format_capacity_bytes(capacity_bytes)
    rotation_rate = data.get("rotation_rate")

    # Detect interface type from identity info
    interface_type = detect_interface_type(None, device, None, raw_output)

    return {
        "status": "UNKNOWN", "model": model, "serial": serial, "capacity_str": capacity_str,
        "capacity_bytes": capacity_bytes, "wear_level": None, "reallocated_sectors": None,
        "reallocated_normalized": None, "reallocated_threshold": None,
        "pending_sectors": None, "power_on_hours": None, "power_on_days": None, "temperature": None,
        "interface_errors": None, "data_written_raw": None, "data_written_bytes": None,
        "data_read_raw": None, "data_read_bytes": None, "raw": raw_output, "rotation_rate": rotation_rate,
        "sas_grown_defect_list": None, "sas_scan_status": None, "sas_non_medium_errors": None,
        "sas_uncorrectable_read_errors": None, "sas_uncorrectable_write_errors": None, "sas_uncorrectable_verify_errors": None,
        "sas_scan_event_count": None, "sas_scan_unique_lbas": None, "sas_sticky_lba_detected": None,
        "model_profile": None, "interface_type": interface_type, "smart_polling": True,
        "write_counter_source": None
    }

def get_smart_data(device, diagnostics=None):
    empty_template = _make_empty_template(smart_polling=False)
    if not validate_device_path(device):
        return empty_template
    smartctl_cmd = get_command_path("smartctl")
    if not smartctl_cmd: return empty_template
    raw_output = run_command([smartctl_cmd, "-j", "-x", device], diagnostics, "smartctl")
    if not raw_output: return empty_template
    try: data = json.loads(raw_output)
    except Exception: return empty_template

    def get_sata_attr(attr_id, get_normalized=False):
        for attr in data.get("ata_smart_attributes", {}).get("table", []):
            if attr.get("id") == attr_id: return attr.get("value") if get_normalized else attr.get("raw", {}).get("value")
        return None

    def get_sata_attr_details(attr_id):
        for attr in data.get("ata_smart_attributes", {}).get("table", []):
            if attr.get("id") == attr_id: return {"raw": attr.get("raw", {}).get("value"), "normalized": attr.get("value"), "thresh": attr.get("thresh"), "name": attr.get("name")}
        return None

    model = data.get("model_name") or data.get("model_number") or data.get("device", {}).get("product")
    serial = data.get("serial_number")
    capacity_bytes = data.get("user_capacity", {}).get("bytes") or data.get("capacity", {}).get("bytes")
    capacity_str = format_capacity_bytes(capacity_bytes)
    rotation_rate = data.get("rotation_rate")
    nvme_log = data.get("nvme_smart_health_information_log", {})
    scsi_log = data.get("scsi_error_counter_log", {})

    devstat_written, devstat_read, devstat_wear = None, None, None
    for page in data.get("ata_device_statistics", {}).get("pages", []):
        p_num = page.get("number")
        for item in page.get("table", []):
            name_str = str(item.get("name") or "").strip().lower()
            offset_val = item.get("offset")
            item_val = item.get("value")
            if p_num == 1:
                if "sectors written" in name_str or offset_val == 24: devstat_written = safe_int(item_val, None)
                elif "sectors read" in name_str or offset_val == 40: devstat_read = safe_int(item_val, None)
            elif p_num == 7:
                if "percentage used" in name_str or offset_val == 8: devstat_wear = safe_int(item_val, None)

    written_bytes, written_raw = None, None
    write_counter_source = None
    sata_write_details = get_sata_attr_details(241)
    if sata_write_details and sata_write_details.get("raw") is not None:
        raw_val = sata_write_details["raw"]
        written_raw = raw_val
        attr_name = str(sata_write_details.get("name") or "").lower()
        if "32mib" in attr_name: written_bytes = raw_val * 32 * 1024 * 1024
        elif "gib" in attr_name: written_bytes = raw_val * 1024 * 1024 * 1024
        elif "gb" in attr_name: written_bytes = raw_val * 1000 * 1000 * 1000
        else: written_bytes = raw_val * 512
        write_counter_source = "sata_attr_241"
    elif nvme_log.get("data_units_written") is not None:
        raw_val = nvme_log["data_units_written"]
        written_raw, written_bytes = raw_val, raw_val * 1000 * 512
        write_counter_source = "nvme_data_units"
    elif scsi_log or (data.get("device", {}).get("protocol") == "SCSI"):
        scsi_vendor = str(data.get("vendor", "") or "").upper()
        if scsi_vendor == "SEAGATE":
            seagate_writes = read_seagate_cache_writes(device)
            if seagate_writes is not None:
                written_raw = seagate_writes
                written_bytes = seagate_writes * 512
                write_counter_source = "seagate_cache_0x37"
            else:
                # 0x37 unavailable — fall back to gigabytes_processed for
                # health/SMART data, but mark as approximate (write check disabled)
                gb_processed = scsi_log.get("write", {}).get("gigabytes_processed") if scsi_log else None
                gb_val = safe_float(gb_processed)
                if gb_val is not None:
                    written_bytes = int(gb_val * 10**9)
                    written_raw = int(written_bytes / 512)
                    write_counter_source = "gigabytes_processed"
                else:
                    write_counter_source = "disabled"
        else:
            # Non-Seagate SAS: gigabytes_processed drifts from firmware
            # background activity, so write detection is disabled. But we
            # still populate data_written_raw/bytes for health scoring.
            gb_processed = scsi_log.get("write", {}).get("gigabytes_processed") if scsi_log else None
            gb_val = safe_float(gb_processed)
            if gb_val is not None:
                written_bytes = int(gb_val * 10**9)
                written_raw = int(written_bytes / 512)
                write_counter_source = "gigabytes_processed"
            else:
                write_counter_source = "disabled"
    elif devstat_written is not None:
        written_raw, written_bytes = devstat_written, devstat_written * 512
        write_counter_source = "sata_devstat"
    else:
        write_counter_source = "disabled"

    read_bytes, read_raw = None, None
    sata_read_details = get_sata_attr_details(242)
    if sata_read_details and sata_read_details.get("raw") is not None:
        raw_val = sata_read_details["raw"]
        read_raw = raw_val
        attr_name = str(sata_read_details.get("name") or "").lower()
        if "32mib" in attr_name: read_bytes = raw_val * 32 * 1024 * 1024
        elif "gib" in attr_name: read_bytes = raw_val * 1024 * 1024 * 1024
        elif "gb" in attr_name: read_bytes = raw_val * 1000 * 1000 * 1000
        else: read_bytes = raw_val * 512
    elif nvme_log.get("data_units_read") is not None:
        raw_val = nvme_log["data_units_read"]
        read_raw, read_bytes = raw_val, raw_val * 1000 * 512
    elif "read" in scsi_log:
        gb_processed = scsi_log["read"].get("gigabytes_processed")
        if gb_processed is not None:
            read_bytes = int(float(gb_processed) * 10**9)
            read_raw = int(read_bytes / 512)
    elif devstat_read is not None:
        read_raw, read_bytes = devstat_read, devstat_read * 512

    sata_wear = None
    for attr_id in [177, 233, 202]:
        val = get_sata_attr(attr_id, get_normalized=True)
        if val is not None:
            # Heuristic: if normalized > 50, it's remaining life (wear = 100 - val)
            # If normalized <= 50, it's percentage used (wear = val)
            # This handles manufacturer differences (Samsung vs Intel)
            # LIMITATION: This assumes a 50-threshold split between manufacturers.
            # Some drives may report percentage used as 60 (meaning 60% used, 40% remaining),
            # which would be incorrectly interpreted as 60% remaining life (40% used).
            # This is a best-effort heuristic without manufacturer-specific logic.
            sata_wear = (100 - val) if val > 50 else val
            break

    nvme_wear = nvme_log.get("percentage_used")
    sas_wear = data.get("scsi_percentage_used_endurance_indicator")
    if sata_wear is not None: wear = sata_wear
    elif nvme_wear is not None: wear = nvme_wear
    elif sas_wear is not None: wear = sas_wear
    elif devstat_wear is not None: wear = devstat_wear
    else: wear = None

    poh = get_sata_attr(9) or data.get("power_on_time", {}).get("hours")
    poh_val = safe_int(poh, None)
    poh_days = round(poh_val / 24, 1) if poh_val is not None else None
    temp = get_sata_attr(194) or get_sata_attr(190) or data.get("temperature", {}).get("current")
    sata_realloc = get_sata_attr(5)
    sas_realloc = data.get("scsi_grown_defect_list")
    if sata_realloc is not None: realloc = sata_realloc
    elif sas_realloc is not None: realloc = sas_realloc
    else: realloc = scsi_log.get("read", {}).get("total_uncorrectable_errors")
    pend = get_sata_attr(197)
    errs = get_sata_attr(199) or data.get("scsi_non_medium_error_count") or nvme_log.get("error_log_entries")

    sata_realloc_details = get_sata_attr_details(5)
    realloc_normalized = sata_realloc_details.get("normalized") if sata_realloc_details else None
    realloc_threshold = sata_realloc_details.get("thresh") if sata_realloc_details else None
    if nvme_log:
        realloc_normalized = nvme_log.get("available_spare")
        realloc_threshold = nvme_log.get("available_spare_threshold")

    # Parse SAS-specific fields from smartctl JSON output
    sas_grown_defect_list = data.get("scsi_grown_defect_list")
    sas_non_medium_errors = data.get("scsi_non_medium_error_count")
    
    # Parse uncorrectable errors from SCSI error counter log
    sas_uncorrectable_read_errors = None
    sas_uncorrectable_write_errors = None
    sas_uncorrectable_verify_errors = None
    if "read" in scsi_log:
        sas_uncorrectable_read_errors = scsi_log["read"].get("total_uncorrectable_errors")
    if "write" in scsi_log:
        sas_uncorrectable_write_errors = scsi_log["write"].get("total_uncorrectable_errors")
    if "verify" in scsi_log:
        sas_uncorrectable_verify_errors = scsi_log["verify"].get("total_uncorrectable_errors")
    
    # Parse SAS background scan status from scsi_background_scan_log
    sas_scan_status = None
    scsi_background_scan = data.get("scsi_background_scan_log", {})
    if scsi_background_scan:
        scan_status_obj = scsi_background_scan.get("status", {})
        if isinstance(scan_status_obj, dict):
            sas_scan_status = scan_status_obj.get("string")
        elif isinstance(scan_status_obj, str):
            sas_scan_status = scan_status_obj
        if sas_scan_status:
            sas_scan_status = sas_scan_status.upper()
    
    # Parse scan event data from background_scan_log.table
    sas_scan_event_count = None
    sas_scan_unique_lbas = None
    sas_sticky_lba_detected = None
    
    if scsi_background_scan:
        scan_table = scsi_background_scan.get("table", [])
        if scan_table:
            sas_scan_event_count = len(scan_table)
            unique_lbas = set()
            lba_error_count = {}
            for entry in scan_table:
                lba = entry.get("lba")
                if lba is not None:
                    unique_lbas.add(lba)
                    # Count errors per LBA for sticky LBA detection
                    status_desc = str(entry.get("status", "") or "").lower()
                    if "failed" in status_desc or "error" in status_desc or "failure" in status_desc:
                        lba_error_count[lba] = lba_error_count.get(lba, 0) + 1
            sas_scan_unique_lbas = len(unique_lbas) if unique_lbas else None
            # If any LBA has 3+ errors, mark as sticky LBA detected
            sas_sticky_lba_detected = any(count >= 3 for count in lba_error_count.values()) if lba_error_count else False

    status_str = "UNKNOWN"
    smart_status = data.get("smart_status", {})
    if smart_status.get("passed") is True: status_str = "PASSED"
    elif smart_status.get("passed") is False: status_str = "FAILED"
    
    # SAS status override: force FAILED for critical SAS conditions
    thresholds = get_triage_thresholds()
    sas_grown_defect_fail_threshold = thresholds.get("sas_grown_defect_fail_threshold", 10000)
    if sas_grown_defect_list is not None and sas_grown_defect_list > sas_grown_defect_fail_threshold:
        status_str = "FAILED"
    if sas_scan_status and "halted" in str(sas_scan_status).lower():
        status_str = "FAILED"
    if sas_uncorrectable_verify_errors is not None and sas_uncorrectable_verify_errors > 0:
        status_str = "FAILED"

    # Load drive model profile from drive_models.json (cached via mtime)
    model_profile = None
    drive_models = _load_drive_models()
    if drive_models:
        vendor = str(data.get("vendor", "") or "").upper()
        product = str(model or "").upper()
        revision = str(data.get("firmware_version", "") or "").upper()
        lookup_key = f"{vendor},{product},{revision}"
        model_profile = drive_models.get("drive_models", {}).get(lookup_key)

    # Detect interface type from SMART data
    interface_type = detect_interface_type(None, device, None, raw_output)

    return {
        "status": status_str, "model": model, "serial": serial, "capacity_str": capacity_str,
        "capacity_bytes": capacity_bytes, "wear_level": wear, "reallocated_sectors": realloc,
        "reallocated_normalized": realloc_normalized, "reallocated_threshold": realloc_threshold,
        "pending_sectors": pend, "power_on_hours": poh_val, "power_on_days": poh_days, "temperature": temp,
        "interface_errors": errs, "data_written_raw": written_raw, "data_written_bytes": written_bytes,
        "data_read_raw": read_raw, "data_read_bytes": read_bytes, "raw": raw_output, "rotation_rate": rotation_rate,
        "sas_grown_defect_list": sas_grown_defect_list, "sas_scan_status": sas_scan_status, "sas_non_medium_errors": sas_non_medium_errors,
        "sas_uncorrectable_read_errors": sas_uncorrectable_read_errors, "sas_uncorrectable_write_errors": sas_uncorrectable_write_errors, "sas_uncorrectable_verify_errors": sas_uncorrectable_verify_errors,
        "sas_scan_event_count": sas_scan_event_count, "sas_scan_unique_lbas": sas_scan_unique_lbas, "sas_sticky_lba_detected": sas_sticky_lba_detected,
        "model_profile": model_profile, "interface_type": interface_type,
        "write_counter_source": write_counter_source,
        "_nvme_media_errors": safe_int(nvme_log.get("media_errors"), 0),
        "_smartctl_exit_status": safe_int(data.get("smartctl", {}).get("exit_status"), 0),
        "_nvme_critical_warning": safe_int(nvme_log.get("critical_warning"), 0)
    }


def capture_write_baseline(device, interface_type):
    """Flush drive cache and capture write counter baseline in a single pass.

    Replaces the old stabilize_smart_writes polling loop. Instead of polling
    for 2 minutes hoping the counter converges, this:
    1. Flushes the drive's write cache to physical media (sg_sync for SAS,
       hdparm -F for SATA, no-op for NVMe)
    2. Reads the write counter once via get_smart_data

    For Seagate SAS, get_smart_data already reads log page 0x37
    (Blocks received from initiator) which is a host-only counter that
    doesn't drift from firmware background activity.

    For non-Seagate SAS, the write counter source is "disabled" and
    data_written_raw will be None — the marker check will skip write
    comparison and rely on checksum/HMAC only.

    Returns:
        (data_written_raw, write_counter_source) tuple
    """
    flush_ok = flush_drive_cache(device, interface_type)
    if not flush_ok:
        logger.warning(f"Cache flush failed for {device} ({interface_type}); proceeding with counter read anyway")
    smart = get_smart_data(device)
    return smart.get("data_written_raw"), smart.get("write_counter_source")
