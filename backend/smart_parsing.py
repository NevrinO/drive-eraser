# --- START OF FILE backend/smart_parsing.py ---
# SMART data parsing utilities

import subprocess
import json
import os
import re
import math

from disk_utils import get_command_path, safe_int, safe_float, format_capacity_bytes, run_command
from common import load_policy, get_config_dir, DRIVE_DATA_CACHE_TTL
from smart_constants import SMART_SELF_TEST_LOG_MAX_HOURS, SMART_SELF_TEST_LOG_ROLLOVER_BOUNDARY, SMART_SELF_TEST_AMBIGUOUS_THRESHOLD_HOURS

def get_triage_thresholds():
    """Load triage thresholds from policy.json with fallback defaults."""
    try:
        config_dir = get_config_dir()
        policy = load_policy(config_dir)
        thresholds = policy.get("triage_thresholds", {})
        return {
            "ssd_new_poh_threshold": thresholds.get("ssd_new_poh_threshold", 720),
            "ssd_high_poh_threshold": thresholds.get("ssd_high_poh_threshold", 43800),
            "hdd_new_poh_threshold": thresholds.get("hdd_new_poh_threshold", 720),
            "hdd_high_poh_threshold": thresholds.get("hdd_high_poh_threshold", 40000),
            "health_score_destroy_threshold": thresholds.get("health_score_destroy_threshold", 30),
            "health_score_scratch_threshold": thresholds.get("health_score_scratch_threshold", 50),
            "ssd_remaining_life_destroy_threshold": thresholds.get("ssd_remaining_life_destroy_threshold", 10),
            "ssd_remaining_life_scratch_threshold": thresholds.get("ssd_remaining_life_scratch_threshold", 50),
            "ssd_remaining_life_good_threshold": thresholds.get("ssd_remaining_life_good_threshold", 80),
            "ssd_new_fdw_threshold": thresholds.get("ssd_new_fdw_threshold", 0.06),
            "hdd_new_fdw_threshold": thresholds.get("hdd_new_fdw_threshold", 2.0),
            "hdd_heavy_fdw_threshold": thresholds.get("hdd_heavy_fdw_threshold", 200),
            "realloc_raw_new_threshold": thresholds.get("realloc_raw_new_threshold", 0),
            "pending_sectors_destroy_threshold": thresholds.get("pending_sectors_destroy_threshold", 10),
            "pending_sectors_scratch_threshold": thresholds.get("pending_sectors_scratch_threshold", 10),
            "sas_grown_defect_fail_threshold": thresholds.get("sas_grown_defect_fail_threshold", 10000),
            "sas_grown_defect_scratch_threshold": thresholds.get("sas_grown_defect_scratch_threshold", 100),
            "sas_nme_advisory_threshold": thresholds.get("sas_nme_advisory_threshold", 1000000),
            "sas_nme_penalty_threshold": thresholds.get("sas_nme_penalty_threshold", 100000000),
            "sas_sticky_lba_threshold": thresholds.get("sas_sticky_lba_threshold", 3),
            "sas_high_poh_threshold": thresholds.get("sas_high_poh_threshold", 50000)
        }
    except Exception:
        # Fallback to defaults if policy loading fails
        return {
            "ssd_new_poh_threshold": 720,
            "ssd_high_poh_threshold": 43800,
            "hdd_new_poh_threshold": 720,
            "hdd_high_poh_threshold": 40000,
            "health_score_destroy_threshold": 30,
            "health_score_scratch_threshold": 50,
            "ssd_remaining_life_destroy_threshold": 10,
            "ssd_remaining_life_scratch_threshold": 50,
            "ssd_remaining_life_good_threshold": 80,
            "ssd_new_fdw_threshold": 0.06,
            "hdd_new_fdw_threshold": 2.0,
            "hdd_heavy_fdw_threshold": 200,
            "realloc_raw_new_threshold": 0,
            "pending_sectors_destroy_threshold": 10,
            "pending_sectors_scratch_threshold": 10,
            "sas_grown_defect_fail_threshold": 10000,
            "sas_grown_defect_scratch_threshold": 100,
            "sas_nme_advisory_threshold": 1000000,
            "sas_nme_penalty_threshold": 100000000,
            "sas_sticky_lba_threshold": 3,
            "sas_high_poh_threshold": 50000
        }

def classify_interface_from_smart(smart_output):
    output = str(smart_output or "").strip()
    if not output: return None
    try:
        data = json.loads(output)
        proto = data.get("device", {}).get("protocol", "").lower()
        if "nvme" in proto: return "nvme"
        if "ata" in proto or "sata" in proto: return "sata"
        if "scsi" in proto or "sas" in proto: return "sas"
    except Exception: pass
    if re.search(r"\bNVMe Version\b", output, re.IGNORECASE) or '"protocol": "NVMe"' in output: return "nvme"
    if re.search(r"\bSATA Version\b|\bATA Version\b", output, re.IGNORECASE) or '"protocol": "ATA"' in output: return "sata"
    if re.search(r"Transport protocol:\s*SAS\b", output, re.IGNORECASE) or '"protocol": "SCSI"' in output: return "sas"
    return None

def is_drive_ssd(interface_type, smart_data):
    iface = str(interface_type or "unknown").lower()
    if "nvme" in iface: return True
    rot_rate = smart_data.get("rotation_rate")
    if rot_rate is not None:
        try:
            rot_val = int(rot_rate)
            if rot_val > 0: return False
            if rot_val == 0: return True
        except (ValueError, TypeError): pass
    model_lower = str(smart_data.get("model") or "").lower()
    if "ssd" in model_lower: return True
    if any(m in model_lower for m in ["hdd", "barracuda", "ironwolf", "toshiba"]): return False
    return smart_data.get("wear_level") is not None

def get_smart_data(device, diagnostics=None):
    empty_template = {
        "status": "UNKNOWN", "model": None, "serial": None, "capacity_str": "-", "capacity_bytes": None,
        "wear_level": None, "reallocated_sectors": None, "pending_sectors": None, "power_on_hours": None,
        "power_on_days": None, "temperature": None, "interface_errors": None, "data_written_raw": None,
        "data_written_bytes": None, "data_read_raw": None, "data_read_bytes": None, "reallocated_normalized": None, "reallocated_threshold": None, "raw": None,
        "rotation_rate": None,
        "sas_grown_defect_list": None, "sas_scan_status": None, "sas_non_medium_errors": None,
        "sas_uncorrectable_read_errors": None, "sas_uncorrectable_write_errors": None, "sas_uncorrectable_verify_errors": None,
        "sas_scan_event_count": None, "sas_scan_unique_lbas": None, "sas_sticky_lba_detected": None,
        "model_profile": None
    }
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
    sata_write_details = get_sata_attr_details(241)
    if sata_write_details and sata_write_details.get("raw") is not None:
        raw_val = sata_write_details["raw"]
        written_raw = raw_val
        attr_name = str(sata_write_details.get("name") or "").lower()
        if "32mib" in attr_name: written_bytes = raw_val * 32 * 1024 * 1024
        elif "gib" in attr_name: written_bytes = raw_val * 1024 * 1024 * 1024
        elif "gb" in attr_name: written_bytes = raw_val * 1000 * 1000 * 1000
        else: written_bytes = raw_val * 512
    elif nvme_log.get("data_units_written") is not None:
        raw_val = nvme_log["data_units_written"]
        written_raw, written_bytes = raw_val, raw_val * 1000 * 512
    elif "write" in scsi_log:
        gb_processed = scsi_log["write"].get("gigabytes_processed")
        if gb_processed is not None:
            written_bytes = int(float(gb_processed) * 10**9)
            written_raw = int(written_bytes / 512)
    elif devstat_written is not None:
        written_raw, written_bytes = devstat_written, devstat_written * 512

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
        if val is not None: sata_wear = val; break

    nvme_wear = nvme_log.get("percentage_used")
    sas_wear = data.get("scsi_percentage_used_endurance_indicator")
    if sata_wear is not None: wear = sata_wear
    elif nvme_wear is not None: wear = nvme_wear
    elif sas_wear is not None: wear = sas_wear
    elif devstat_wear is not None: wear = max(0, 100 - devstat_wear)
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
                    if "failed" in status_desc or "error" in status_desc:
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

    # Load drive model profile from drive_models.json
    model_profile = None
    try:
        config_dir = get_config_dir()
        drive_models_path = os.path.join(config_dir, "drive_models.json")
        if os.path.exists(drive_models_path):
            with open(drive_models_path, "r") as f:
                drive_models = json.load(f)
                vendor = str(data.get("vendor", "") or "").upper()
                product = str(model or "").upper()
                revision = str(data.get("firmware_version", "") or "").upper()
                lookup_key = f"{vendor},{product},{revision}"
                model_profile = drive_models.get("drive_models", {}).get(lookup_key)
    except Exception:
        pass

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
        "model_profile": model_profile, "interface_type": interface_type
    }

def get_raw_smart_diagnostics(device):
    smartctl_cmd = get_command_path("smartctl")
    if not smartctl_cmd or not device:
        return "SMARTCTL command not resolved or invalid device target.\n"
    try:
        result = subprocess.run(["sudo", smartctl_cmd, "-a", device], capture_output=True, text=True, timeout=15, shell=False)
        output = result.stdout or ""
        stderr = result.stderr or ""
        return f"\n=== RAW SMARTCTL DIAGNOSTICS FOR {device} ===\nExit Code: {result.returncode}\nSTDOUT:\n{output}\nSTDERR:\n{stderr}\n"
    except subprocess.TimeoutExpired:
        return f"\n=== RAW SMARTCTL DIAGNOSTICS FOR {device} ===\nError: Command timed out after 15 seconds.\n"
    except Exception as e:
        return f"\n=== RAW SMARTCTL DIAGNOSTICS FOR {device} ===\nException raised: {str(e)}\n"

def detect_interface_type(by_path_value, device, configured_type=None, smart_output=None):
    value, dev = (by_path_value or "").lower(), (device or "").lower()
    smart_hint = classify_interface_from_smart(smart_output)
    if smart_hint: return smart_hint
    if "nvme" in value or dev.startswith("/dev/nvme"): return "nvme"
    if "sas" in value: return "sas"
    if "ata" in value: return "sata"
    
    dev_name = os.path.basename(dev)
    if dev_name and dev.startswith("/dev/sd"):
        sys_vendor_path = f"/sys/block/{dev_name}/device/vendor"
        if os.path.exists(sys_vendor_path):
            try:
                with open(sys_vendor_path, "r") as f:
                    vendor = f.read().strip()
                if "ATA" in vendor:
                    return "sata"
                else:
                    sys_device_path = f"/sys/block/{dev_name}/device"
                    if os.path.exists(sys_device_path):
                        real_sys_path = os.path.realpath(sys_device_path)
                        if "sas" in real_sys_path.lower():
                            return "sas"
            except Exception:
                pass

    return "sata" if dev.startswith("/dev/sd") else "unknown"

def calculate_drive_health_score(interface_type, smart_data, raw_json):
    iface = str(interface_type or "unknown").lower()
    is_ssd = is_drive_ssd(interface_type, smart_data)
    wear = smart_data.get("wear_level")
    poh = safe_int(smart_data.get("power_on_hours"), 0)
    
    # Initialize penalty breakdown
    penalty_breakdown = {
        "base_score": 100,
        "poh_penalty": 0,
        "fdw_penalty": 0,
        "realloc_penalty": 0,
        "pending_penalty": 0,
        "nme_penalty": 0,
        "nvme_media_penalty": 0,
        "failed_override": False,
        "final_score": 100
    }
    
    if is_ssd and wear is not None:
        wear_val = safe_int(wear, 0)
        base_score = max(0, 100 - wear_val) if iface in {"nvme", "sas"} else wear_val
        thresholds = get_triage_thresholds()
        ssd_high_poh_thresh = thresholds["ssd_high_poh_threshold"]
        if poh > ssd_high_poh_thresh:
            poh_penalty = min(20, 20 * ((poh - ssd_high_poh_thresh) / (ssd_high_poh_thresh * 2 - ssd_high_poh_thresh)) ** 2)
            base_score = max(10, base_score - poh_penalty)
            penalty_breakdown["poh_penalty"] = poh_penalty
    else:
        thresholds = get_triage_thresholds()
        if iface == "sas":
            # SAS-specific POH threshold: 50,000
            sas_high_poh_thresh = thresholds.get("sas_high_poh_threshold", 50000)
            poh_penalty = min(30, max(0, (poh - sas_high_poh_thresh) / (sas_high_poh_thresh * 2) * 30)) if poh > sas_high_poh_thresh else 0
        else:
            # HDD POH threshold: 20,000
            poh_penalty = min(30, max(0, (poh - 20000) / 40000 * 30)) if poh > 20000 else 0
        written_bytes = smart_data.get("data_written_bytes")
        if written_bytes is None:
            raw_written = smart_data.get("data_written_raw")
            written_bytes = safe_int(raw_written, 0) * 512 if raw_written is not None else 0
        else:
            written_bytes = safe_int(written_bytes, 0)
        capacity = safe_int(smart_data.get("capacity_bytes"), 0)
        fdw = (written_bytes / capacity) if capacity > 0 else 0.0
        fdw_penalty = min(30, max(0, (fdw / 150.0) * 30))
        base_score = max(40, 100 - poh_penalty - fdw_penalty)
        penalty_breakdown["poh_penalty"] = poh_penalty
        penalty_breakdown["fdw_penalty"] = fdw_penalty

    penalty_breakdown["base_score"] = base_score

    reallocated = safe_int(smart_data.get("reallocated_sectors"), 0)
    pending = safe_int(smart_data.get("pending_sectors"), 0)
    errs = safe_int(smart_data.get("interface_errors"), 0)

    realloc_penalty = 0
    if is_ssd:
        realloc_normalized = smart_data.get("reallocated_normalized")
        if realloc_normalized is not None:
            norm_val = safe_int(realloc_normalized, 100)
            if norm_val < 100: realloc_penalty = min(40, (100 - norm_val) * 1)
    elif iface == "sas":
        # SAS logarithmic grown-defect penalty: ~40 at 100, ~70 at 1000, ~100 at 10000+
        sas_grown_defects = safe_int(smart_data.get("sas_grown_defect_list"), 0)
        if sas_grown_defects > 0:
            thresholds = get_triage_thresholds()
            sas_grown_defect_scratch_threshold = thresholds.get("sas_grown_defect_scratch_threshold", 100)
            if sas_grown_defects >= sas_grown_defect_scratch_threshold:
                # Logarithmic scaling: penalty = 35 * log10(defects / 10), capped at 100
                realloc_penalty = min(100, 35 * math.log10(max(1, sas_grown_defects / 10)))
    else:
        if reallocated > 0:
            realloc_penalty = min(40, 10 if reallocated == 1 else (10 + (reallocated - 1) * 5 if reallocated <= 5 else 30 + (reallocated - 5) * 10))

    penalty_breakdown["realloc_penalty"] = realloc_penalty

    pending_penalty = min(60, pending * 15)
    penalty_breakdown["pending_penalty"] = pending_penalty
    
    # SAS NME (Non-Medium Errors) penalty
    nme_penalty = 0
    if iface == "sas":
        sas_nme = safe_int(smart_data.get("sas_non_medium_errors"), 0)
        if sas_nme > 0:
            thresholds = get_triage_thresholds()
            nme_advisory_thresh = thresholds.get("sas_nme_advisory_threshold", 1000000)
            nme_penalty_thresh = thresholds.get("sas_nme_penalty_threshold", 100000000)
            if sas_nme >= nme_penalty_thresh:
                # Penalty only above 100M: scale from 0 to 30 based on excess
                nme_penalty = min(30, 30 * ((sas_nme - nme_penalty_thresh) / nme_penalty_thresh))
            # Below 1M: no penalty
            # 1M-100M: advisory only (no score penalty, but could flag in UI)
    
    penalty_breakdown["nme_penalty"] = nme_penalty
    
    nvme_media_penalty = 0
    if iface == "nvme" and raw_json:
        try:
            nvme_log = json.loads(raw_json).get("nvme_smart_health_information_log", {})
            nvme_media_penalty = min(80, safe_int(nvme_log.get("media_errors"), 0) * 20)
        except Exception: pass

    penalty_breakdown["nvme_media_penalty"] = nvme_media_penalty

    score = max(0, base_score - realloc_penalty - pending_penalty - nme_penalty - nvme_media_penalty)
    failed_override = str(smart_data.get("status") or "UNKNOWN").upper() == "FAILED"
    if raw_json:
        try:
            data = json.loads(raw_json)
            exit_status_val = safe_int(data.get("smartctl", {}).get("exit_status"), 0)
            if (exit_status_val & 8 != 0) or (exit_status_val & 16 != 0): failed_override = True
            if iface == "nvme":
                crit_warn_val = safe_int(data.get("nvme_smart_health_information_log", {}).get("critical_warning"), 0)
                if (crit_warn_val & 0x04 != 0) or (crit_warn_val & 0x08 != 0): failed_override = True
        except Exception: pass

    penalty_breakdown["failed_override"] = failed_override
    final_score = min(int(round(score)), 5) if failed_override else int(round(score))
    penalty_breakdown["final_score"] = final_score

    return final_score, penalty_breakdown

def validate_device_path(device):
    r"""Validate device path against strict whitelist (lesson #9, #13, #16).

    Args:
        device: Device path string (e.g., "/dev/sda", "sda")

    Returns:
        True if valid, False otherwise
    """
    if not device or not isinstance(device, str):
        return False

    # Remove /dev/ prefix if present for validation (handle edge cases like multiple slashes)
    device_name = device.lstrip("/").replace("dev/", "", 1) if device.startswith("/") else device

    # Reject path traversal and newlines
    if ".." in device_name or "\n" in device_name or "\r" in device_name:
        return False

    # Validate against strict regex patterns (lesson #16: use \Z for strict end anchor)
    # Lesson #91: Use specific patterns matching actual system naming conventions
    sata_pattern = re.compile(r'^sd[a-z][0-9]*\Z')
    nvme_pattern = re.compile(r'^nvme[0-9]+(n[0-9]+)?(p[0-9]+)?\Z')

    return bool(sata_pattern.match(device_name) or nvme_pattern.match(device_name))


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
        # Run smartctl -t to start the test with appropriate timeout
        # Note: check=False is used to handle non-zero exit codes manually with custom error messages
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
        return {"error": "Invalid device path", "status": "failed"}

    # Build device path
    device_path = f"/dev/{device}" if not device.startswith("/dev/") else device

    # Get smartctl command
    smartctl_cmd = get_command_path("smartctl")
    if not smartctl_cmd:
        return {"error": "smartctl command not found", "status": "failed"}

    try:
        # Use -a to get both the real-time self-test status register AND the log table.
        # ata_smart_data.self_test.status updates immediately while a test runs;
        # ata_smart_self_test_log only updates when a test completes, so checking
        # the log alone causes false "completed" detection during an active test.
        result = run_command([smartctl_cmd, "-j", "-a", device_path], diagnostics, "smartctl")
        if not result:
            return {"error": "Failed to read self-test log", "status": "failed"}

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
                from disk_ops import _get_cached_drive_payload
                import time
                cache_key = (device_path, device_path.replace("/dev/", ""))
                cached_payload = _get_cached_drive_payload(cache_key)
                if cached_payload and (time.time() - cached_payload['timestamp']) < DRIVE_DATA_CACHE_TTL:
                    # Use cached data if available and fresh
                    smart_info = cached_payload['data'].get('smart')
                    if smart_info:
                        current_poh = smart_info.get('power_on_hours')
                        serial = smart_info.get('serial')
                else:
                    # Cache miss or expired, fetch fresh data
                    current_smart = get_smart_data(device_path, diagnostics)
                    current_poh = current_smart.get("power_on_hours")
                    serial = current_smart.get("serial")

                if current_poh and log_hours is not None:
                    if current_poh < SMART_SELF_TEST_LOG_MAX_HOURS:
                        # No rollover possible
                        corrected_hours = log_hours
                    else:
                        # POH > 65,535 - rollover has occurred
                        # Get historical POH from database
                        historical_poh = None
                        if serial:
                            try:
                                from database import get_historical_poh_for_serial
                                historical_poh = get_historical_poh_for_serial(serial)
                            except Exception as e:
                                import logging
                                logging.getLogger(__name__).warning(f"Failed to get historical POH for {serial}: {e}")

                        # Only correct if we have historical evidence that drive was already over 65,535
                        # when we started tracking it (proves this is our system's data)
                        if historical_poh and historical_poh > SMART_SELF_TEST_LOG_MAX_HOURS:
                            # We know from database that drive was already over 65,535 when we first saw it
                            # Calculate rollovers based on current POH (use 65536 for accurate boundary)
                            rollover_count = int(current_poh // SMART_SELF_TEST_LOG_ROLLOVER_BOUNDARY)
                            corrected_hours = log_hours + (rollover_count * SMART_SELF_TEST_LOG_MAX_HOURS)
                            rollover_corrected = True
                            # Flag ambiguous if near rollover boundary (within 1000 hours)
                            # or if log hours differ significantly from expected corrected hours
                            if current_poh > SMART_SELF_TEST_LOG_MAX_HOURS and (abs(current_poh % SMART_SELF_TEST_LOG_MAX_HOURS) < SMART_SELF_TEST_AMBIGUOUS_THRESHOLD_HOURS or abs(current_poh - corrected_hours) > SMART_SELF_TEST_AMBIGUOUS_THRESHOLD_HOURS):
                                ambiguous = True
                        else:
                            # No database history or drive was under 65,535 when we first saw it
                            # Don't correct - these may be from another system or before rollover
                            corrected_hours = log_hours
            except Exception:
                # If we can't get current POH, use raw log hours
                corrected_hours = log_hours

            # Calculate percentage complete
            # remaining is 0-90 for in-progress tests, 0 for completed
            percentage = 0
            if remaining > 0:
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
                "latest_result": {
                    "type": "unknown",
                    "status": scsi_ie.get("string", "unknown"),
                    "remaining": 0,
                    "lba": None,
                    "hours": None
                }
            }
        else:
            return {"status": "no_tests", "latest_result": None}
    except json.JSONDecodeError:
        return {"error": "Failed to parse smartctl output", "status": "failed"}
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError) as e:
        return {"error": f"System error getting test status: {str(e)}", "status": "failed"}
    except Exception as e:
        return {"error": f"Exception getting test status: {str(e)}", "status": "failed"}


def get_drive_recommendation(interface_type, smart, health_score=None):
    thresholds = get_triage_thresholds()
    
    iface = str(interface_type or "unknown").lower()
    is_ssd = is_drive_ssd(interface_type, smart)
    poh = safe_int(smart.get("power_on_hours"), 0)
    status = str(smart.get("status") or "UNKNOWN").upper()
    pending = safe_int(smart.get("pending_sectors"), 0)
    realloc_raw = safe_int(smart.get("reallocated_sectors"), 0)
    realloc_norm = safe_int(smart.get("reallocated_normalized"), 100)
    realloc_thresh = safe_int(smart.get("reallocated_threshold"), 10)

    written_bytes = smart.get("data_written_bytes")
    if written_bytes is None:
        raw_written = smart.get("data_written_raw")
        written_bytes = safe_int(raw_written, 0) * (512000 if "nvme" in iface else 512) if raw_written is not None else 0
    else:
        written_bytes = safe_int(written_bytes, 0)

    capacity = safe_int(smart.get("capacity_bytes"), 0)
    fdw = (written_bytes / capacity) if capacity > 0 else 0.0

    remaining_life = 100
    wear = smart.get("wear_level")
    if wear is not None:
        wear_val = safe_int(wear, 0)
        remaining_life = max(0, 100 - wear_val) if ("nvme" in iface or "sas" in iface) else wear_val

    health_destroy_thresh = thresholds["health_score_destroy_threshold"]
    health_scratch_thresh = thresholds["health_score_scratch_threshold"]
    pending_destroy_thresh = thresholds["pending_sectors_destroy_threshold"]
    pending_scratch_thresh = thresholds["pending_sectors_scratch_threshold"]
    
    # SAS uncorrectable error recommendations (critical indicators)
    if iface == "sas":
        sas_verify_errors = safe_int(smart.get("sas_uncorrectable_verify_errors"), 0)
        sas_write_errors = safe_int(smart.get("sas_uncorrectable_write_errors"), 0)
        sas_read_errors = safe_int(smart.get("sas_uncorrectable_read_errors"), 0)
        sas_sticky_lba = smart.get("sas_sticky_lba_detected")
        
        if sas_verify_errors >= 1 or sas_write_errors >= 1:
            return {"status": "DESTROY", "comment": "SAS drive has uncorrectable verify or write errors. Critical data integrity risk."}
        if sas_read_errors >= 10:
            return {"status": "DESTROY", "comment": "SAS drive has excessive uncorrectable read errors. Critical data integrity risk."}
        if sas_read_errors >= 1:
            return {"status": "SCRATCH", "comment": "SAS drive has uncorrectable read errors. Use only for non-critical data."}
        if sas_sticky_lba:
            return {"status": "SCRATCH", "comment": "SAS drive has sticky LBA detected (recurring errors at same location). Use only for non-critical data."}

    if health_score is not None:
        if status == "FAILED" or health_score <= health_destroy_thresh: return {"status": "DESTROY", "comment": "Drive shows critical physical degradation or SMART health failure."}
        if health_score <= health_scratch_thresh: return {"status": "SCRATCH", "comment": "Unstable or significantly aged drive. Safe only for non-critical use."}
    else:
        if status == "FAILED" or realloc_norm < 50 or pending > pending_destroy_thresh: return {"status": "DESTROY", "comment": "Drive shows critical physical degradation or SMART health failure."}
        if realloc_norm <= realloc_thresh or (0 < pending <= pending_scratch_thresh): return {"status": "SCRATCH", "comment": "Unstable or threshold-breached sectors detected. Safe only for non-critical use."}

    if is_ssd:
        ssd_life_destroy_thresh = thresholds["ssd_remaining_life_destroy_threshold"]
        ssd_life_scratch_thresh = thresholds["ssd_remaining_life_scratch_threshold"]
        ssd_life_good_thresh = thresholds["ssd_remaining_life_good_threshold"]
        ssd_new_poh_thresh = thresholds["ssd_new_poh_threshold"]
        ssd_high_poh_thresh = thresholds["ssd_high_poh_threshold"]
        ssd_new_fdw_thresh = thresholds["ssd_new_fdw_threshold"]
        realloc_new_thresh = thresholds["realloc_raw_new_threshold"]
        
        if remaining_life < ssd_life_destroy_thresh: return {"status": "DESTROY", "comment": "SSD wear is fully depleted (remaining life below threshold)."}
        if remaining_life < ssd_life_scratch_thresh: return {"status": "SCRATCH", "comment": "SSD remaining life is heavily worn (under 60%). Relegate to scratch."}
        if poh < ssd_new_poh_thresh and fdw < ssd_new_fdw_thresh and remaining_life == 100 and realloc_raw == realloc_new_thresh: return {"status": "NEW_STOCK", "comment": "This drive is practically new (low runtime, pristine life and sectors)."}
        return {"status": "USED_HEAVY" if poh >= ssd_high_poh_thresh else "USED_GOOD", "comment": f"Excellent health, but high runtime (exceeds {ssd_high_poh_thresh:,} hours)." if poh >= ssd_high_poh_thresh else "This drive is used but still has excellent remaining life."} if remaining_life >= ssd_life_good_thresh else {"status": "USED_HEAVY", "comment": "This drive is heavily used but still has life."}
    else:
        hdd_high_poh_thresh = thresholds["hdd_high_poh_threshold"]
        hdd_new_poh_thresh = thresholds["hdd_new_poh_threshold"]
        hdd_new_fdw_thresh = thresholds["hdd_new_fdw_threshold"]
        hdd_heavy_fdw_thresh = thresholds["hdd_heavy_fdw_threshold"]
        realloc_new_thresh = thresholds["realloc_raw_new_threshold"]
        
        if poh >= hdd_high_poh_thresh: return {"status": "USED_HEAVY", "comment": f"High Power-On Hours (exceeds {hdd_high_poh_thresh:,} server hours)."}
        if poh < hdd_new_poh_thresh and fdw < hdd_new_fdw_thresh and realloc_raw == realloc_new_thresh: return {"status": "NEW_STOCK", "comment": "Practically new (extremely low runtime and zero sector reallocations)."}
        return {"status": "USED_HEAVY" if fdw >= hdd_heavy_fdw_thresh else "USED_GOOD", "comment": "High workload or raw sector writes history. Monitor closely." if fdw >= hdd_heavy_fdw_thresh else "Used but has clean write history and moderate runtime."}


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

    strict_mode = policy.get("prewipe_health_gate_strict_mode", False)
    block_destroy = policy.get("prewipe_health_gate_block_destroy", True)
    block_scratch = policy.get("prewipe_health_gate_block_scratch", False)
    block_failed_smart = policy.get("prewipe_health_gate_block_failed_smart", True)
    max_pending = policy.get("prewipe_health_gate_max_pending_sectors", 10)
    max_reallocated = policy.get("prewipe_health_gate_max_reallocated_sectors", 5)
    max_interface_errors = policy.get("prewipe_health_gate_max_interface_errors", 100)
    max_health_score_drop = policy.get("prewipe_health_gate_max_health_score_drop", 20)

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
    health_score, penalty_breakdown = calculate_drive_health_score(interface_type, smart, smart.get("raw"))
    recommendation = get_drive_recommendation(interface_type, smart, health_score=health_score)
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
            if os.path.exists(state_path):
                with open(state_path, "r") as f:
                    device_state = f.read().strip()
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
    thresholds = get_triage_thresholds()
    destroy_threshold = thresholds.get("health_score_destroy_threshold", 30)
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

        nvme_critical_warning = None
        try:
            if smart.get("raw"):
                raw_data = json.loads(smart.get("raw"))
                nvme_log = raw_data.get("nvme_smart_health_information_log", {})
                nvme_critical_warning = safe_int(nvme_log.get("critical_warning"), 0)
                if nvme_critical_warning & 0x04 or nvme_critical_warning & 0x08:
                    block_reason = "nvme_critical_warning"
        except Exception:
            pass

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
# --- END OF FILE backend/smart_parsing.py ---
