# --- START OF FILE backend/disk_ops.py ---
import subprocess
import json
import os
import re
import shutil
import hashlib
import hmac

MARKER_SIGNATURE = "DWS_MARKER_V1"
MARKER_BLOCK_SIZE = 4096

SSD_HIGH_POH_THRESHOLD = 40000
HDD_HIGH_POH_THRESHOLD = 40000
SSD_NEW_POH_THRESHOLD = 500
HDD_NEW_POH_THRESHOLD = 500

def safe_int(val, default=0):
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default

def safe_float(val, default=0.0):
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default

def resolve_command_path(command_name, candidates, env_var_name=None):
    env_value = os.getenv(env_var_name) if env_var_name else None
    if env_value and os.path.exists(env_value) and os.access(env_value, os.X_OK):
        return env_value
    for candidate in candidates:
        if candidate and os.path.exists(candidate) and os.access(candidate, os.X_OK):
            return candidate
    resolved = shutil.which(command_name)
    if resolved and os.path.exists(resolved) and os.access(resolved, os.X_OK):
        return resolved
    return None

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def get_config_dir():
    candidates = [
        os.getenv("DRIVE_ERASER_CONFIG_DIR"),
        os.path.join(PROJECT_ROOT, "config"),
        "/opt/drive-eraser/config",
    ]
    for candidate in candidates:
        if candidate and os.path.isdir(candidate):
            return candidate
    return os.path.join(PROJECT_ROOT, "config")

def load_command_path_overrides():
    config_path = os.path.join(get_config_dir(), "command_paths.json")
    if not os.path.exists(config_path):
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

COMMAND_PATH_OVERRIDES = load_command_path_overrides()

SMARTCTL_CMD = resolve_command_path(
    "smartctl",
    [COMMAND_PATH_OVERRIDES.get("smartctl"), "/usr/sbin/smartctl", "/usr/bin/smartctl", "/bin/smartctl"],
    "DRIVE_ERASER_SMARTCTL_PATH"
)
NVME_CMD = resolve_command_path(
    "nvme",
    [COMMAND_PATH_OVERRIDES.get("nvme"), "/usr/sbin/nvme", "/usr/bin/nvme", "/bin/nvme"],
    "DRIVE_ERASER_NVME_PATH"
)
HDPARM_CMD = resolve_command_path(
    "hdparm",
    [COMMAND_PATH_OVERRIDES.get("hdparm"), "/usr/sbin/hdparm", "/usr/bin/hdparm", "/bin/hdparm"],
    "DRIVE_ERASER_HDPARM_PATH"
)
SG_SANITIZE_CMD = resolve_command_path(
    "sg_sanitize",
    [COMMAND_PATH_OVERRIDES.get("sg_sanitize"), "/usr/bin/sg_sanitize", "/usr/sbin/sg_sanitize", "/bin/sg_sanitize"],
    "DRIVE_ERASER_SG_SANITIZE_PATH"
)
DD_CMD = resolve_command_path(
    "dd",
    [COMMAND_PATH_OVERRIDES.get("dd"), "/usr/bin/dd", "/bin/dd"],
    "DRIVE_ERASER_DD_PATH"
)

def load_policy(config_dir):
    policy_path = os.path.join(config_dir, "policy.json")
    if not os.path.exists(policy_path):
        return {}
    try:
        with open(policy_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def check_write_tolerance(interface_type, current, stored):
    if current is None or stored is None:
        return False
    try:
        diff = int(current) - int(stored)
        if diff < 0:
            return False
        iface = str(interface_type or "unknown").lower()
        if "nvme" in iface:
            return diff <= 4
        else:
            return diff <= 4096
    except Exception:
        return False

def format_capacity_bytes(num_bytes):
    if not num_bytes:
        return "-"
    tb = num_bytes / (10**12)
    if tb >= 1.0:
        if abs(tb - round(tb)) < 0.05:
            return f"{round(tb)} TB"
        return f"{tb:.2f} TB"
    gb = num_bytes / (10**9)
    if gb >= 1.0:
        if abs(gb - round(gb)) < 0.5:
            return f"{round(gb)} GB"
        return f"{gb:.1f} GB"
    mb = num_bytes / (10**6)
    return f"{round(mb)} MB"

def read_marker_status(device, interface_type="unknown", passphrase=None):
    if not DD_CMD:
        return {"ok": False, "status": "marker_error", "error": "dd_not_available_for_marker_read", "details": {}}

    command = [DD_CMD, f"if={device}", f"bs={MARKER_BLOCK_SIZE}", "count=1", "iflag=direct", "status=none"]
    try:
        result = subprocess.run(["sudo"] + command, capture_output=True)
        if result.returncode != 0:
            fallback_command = [DD_CMD, f"if={device}", f"bs={MARKER_BLOCK_SIZE}", "count=1", "status=none"]
            result = subprocess.run(["sudo"] + fallback_command, capture_output=True)

        if result.returncode != 0:
            return {
                "ok": False,
                "status": "marker_error",
                "error": "marker_read_failed",
                "details": {
                    "return_code": result.returncode,
                    "stderr": (result.stderr or b"").decode("utf-8", errors="replace").strip()
                }
            }
        output_bytes = result.stdout or b""
    except Exception as e:
        return {"ok": False, "status": "marker_error", "error": f"marker_read_exception:{e}", "details": {}}

    marker_index = output_bytes.find(MARKER_SIGNATURE.encode("utf-8"))
    if marker_index < 0:
        return {"ok": True, "status": "none", "error": None, "details": {}}

    start = output_bytes.rfind(b"{", 0, marker_index)
    end = output_bytes.find(b"}", marker_index)

    if start < 0 or end < start:
        return {"ok": True, "status": "corrupted", "error": "json_parse_failed", "details": {}}

    candidate = output_bytes[start:end + 1]
    try:
        parsed = json.loads(candidate.decode("utf-8", errors="strict"))
    except Exception:
        return {"ok": True, "status": "corrupted", "error": "json_parse_failed", "details": {}}

    if parsed.get("signature") != MARKER_SIGNATURE:
        return {"ok": True, "status": "corrupted", "error": "invalid_signature", "details": {}}

    stored_checksum = parsed.pop("checksum", None)
    stored_hmac = parsed.pop("hmac", None)

    serialized_for_checksum = json.dumps(parsed, sort_keys=True, separators=(',', ':')).encode('utf-8')
    calculated_checksum = hashlib.sha256(serialized_for_checksum).hexdigest()

    if calculated_checksum != stored_checksum:
        return {"ok": True, "status": "corrupted", "error": "checksum_mismatch", "details": {}}

    parsed["checksum"] = stored_checksum

    details = {
        "job_id": parsed.get("job_id"),
        "finished_at": parsed.get("finished_at"),
        "method": parsed.get("method"),
        "serial": parsed.get("serial"),
        "ticket_number": parsed.get("ticket_number"),
        "data_written_at_wipe": parsed.get("data_written_at_wipe"),
    }

    hmac_verified = False
    if passphrase and stored_hmac:
        serialized_for_hmac = json.dumps(parsed, sort_keys=True, separators=(',', ':')).encode('utf-8')
        derived_key = hashlib.pbkdf2_hmac('sha256', passphrase.encode('utf-8'), b'DWS_SALT_v1', 10000)
        calculated_hmac = hmac.new(derived_key, serialized_for_hmac, hashlib.sha256).hexdigest()

        if hmac.compare_digest(calculated_hmac, stored_hmac):
            hmac_verified = True

    return {
        "ok": True,
        "status": "checksum_valid",
        "hmac_verified": hmac_verified,
        "error": None,
        "details": details,
    }

def run_command(command, diagnostics=None, key=None):
    if not command or not command[0]:
        if diagnostics is not None and key:
            diagnostics[key] = {"ok": False, "reason": "command_not_resolved"}
        return None
    try:
        result = subprocess.run(["sudo"] + command, capture_output=True, text=True, check=True)
        if diagnostics is not None and key:
            diagnostics[key] = {"ok": True, "reason": None, "exit_code": result.returncode}
        return (result.stdout or "").strip()
    except subprocess.CalledProcessError as e:
        if diagnostics is not None and key:
            reason = (e.stderr or "").strip() or f"exit_code_{e.returncode}"
            diagnostics[key] = {"ok": False, "reason": reason, "exit_code": e.returncode}
        if command and os.path.basename(command[0]) == "smartctl":
            return (e.stdout or "").strip()
        return None

def run_destructive_command(command):
    if not command or not command[0]:
        return {"ok": False, "error": "command_not_resolved", "stdout": "", "stderr": "", "exit_code": None}
    try:
        result = subprocess.run(["sudo"] + command, capture_output=True, text=True, check=True)
        return {"ok": True, "error": None, "stdout": (result.stdout or "").strip(), "stderr": (result.stderr or "").strip(), "exit_code": result.returncode}
    except subprocess.CalledProcessError as e:
        return {"ok": False, "error": "command_failed", "stdout": (e.stdout or "").strip(), "stderr": (e.stderr or "").strip(), "exit_code": e.returncode}
    except FileNotFoundError:
        return {"ok": False, "error": "sudo_or_command_not_found", "stdout": "", "stderr": "", "exit_code": None}

def classify_interface_from_smart(smart_output):
    output = str(smart_output or "").strip()
    if not output:
        return None
    try:
        data = json.loads(output)
        proto = data.get("device", {}).get("protocol", "").lower()
        if "nvme" in proto:
            return "nvme"
        if "ata" in proto or "sata" in proto:
            return "sata"
        if "scsi" in proto or "sas" in proto:
            return "sas"
    except Exception:
        pass
    if re.search(r"\bNVMe Version\b", output, re.IGNORECASE) or '"protocol": "NVMe"' in output:
        return "nvme"
    if re.search(r"\bSATA Version\b", output, re.IGNORECASE) or re.search(r"\bATA Version\b", output, re.IGNORECASE) or '"protocol": "ATA"' in output:
        return "sata"
    if re.search(r"Transport protocol:\s*SAS\b", output, re.IGNORECASE) or '"protocol": "SCSI"' in output:
        return "sas"
    return None

def get_smart_data(device, diagnostics=None):
    empty_template = {
        "status": "UNKNOWN", "model": None, "serial": None, "capacity_str": "-", "capacity_bytes": None,
        "wear_level": None, "reallocated_sectors": None, "pending_sectors": None, "power_on_hours": None,
        "power_on_days": None, "temperature": None, "interface_errors": None, "data_written_raw": None,
        "data_written_bytes": None, "data_read_raw": None, "data_read_bytes": None, "reallocated_normalized": None, "reallocated_threshold": None, "raw": None
    }
    if not SMARTCTL_CMD:
        return empty_template
    raw_output = run_command([SMARTCTL_CMD, "-j", "-x", device], diagnostics, "smartctl")
    if not raw_output:
        return empty_template
    try:
        data = json.loads(raw_output)
    except Exception:
        return empty_template

    def get_sata_attr(attr_id, get_normalized=False):
        table = data.get("ata_smart_attributes", {}).get("table", [])
        for attr in table:
            if attr.get("id") == attr_id:
                if get_normalized:
                    return attr.get("value")
                return attr.get("raw", {}).get("value")
        return None

    def get_sata_attr_details(attr_id):
        table = data.get("ata_smart_attributes", {}).get("table", [])
        for attr in table:
            if attr.get("id") == attr_id:
                return {
                    "raw": attr.get("raw", {}).get("value"), 
                    "normalized": attr.get("value"), 
                    "thresh": attr.get("thresh"),
                    "name": attr.get("name")
                }
        return None

    model = data.get("model_name") or data.get("model_number") or data.get("device", {}).get("product")
    serial = data.get("serial_number")
    capacity_bytes = data.get("user_capacity", {}).get("bytes") or data.get("capacity", {}).get("bytes")
    capacity_str = format_capacity_bytes(capacity_bytes)

    nvme_log = data.get("nvme_smart_health_information_log", {})
    scsi_log = data.get("scsi_error_counter_log", {})

    written_bytes = None
    written_raw = None
    sata_write_details = get_sata_attr_details(241)
    
    if sata_write_details and sata_write_details.get("raw") is not None:
        raw_val = sata_write_details["raw"]
        written_raw = raw_val
        attr_name = str(sata_write_details.get("name") or "").lower()
        if "32mib" in attr_name:
            written_bytes = raw_val * 32 * 1024 * 1024
        elif "gib" in attr_name:
            written_bytes = raw_val * 1024 * 1024 * 1024
        elif "gb" in attr_name:
            written_bytes = raw_val * 1000 * 1000 * 1000
        else:
            written_bytes = raw_val * 512
    elif nvme_log.get("data_units_written") is not None:
        raw_val = nvme_log["data_units_written"]
        written_raw = raw_val
        written_bytes = raw_val * 1000 * 512
    elif "write" in scsi_log:
        gb_processed = scsi_log["write"].get("gigabytes_processed")
        if gb_processed is not None:
            written_bytes = int(float(gb_processed) * 10**9)
            written_raw = int(written_bytes / 512)

    read_bytes = None
    read_raw = None
    sata_read_details = get_sata_attr_details(242)
    
    if sata_read_details and sata_read_details.get("raw") is not None:
        raw_val = sata_read_details["raw"]
        read_raw = raw_val
        attr_name = str(sata_read_details.get("name") or "").lower()
        if "32mib" in attr_name:
            read_bytes = raw_val * 32 * 1024 * 1024
        elif "gib" in attr_name:
            read_bytes = raw_val * 1024 * 1024 * 1024
        elif "gb" in attr_name:
            read_bytes = raw_val * 1000 * 1000 * 1000
        else:
            read_bytes = raw_val * 512
    elif nvme_log.get("data_units_read") is not None:
        raw_val = nvme_log["data_units_read"]
        read_raw = raw_val
        read_bytes = raw_val * 1000 * 512
    elif "read" in scsi_log:
        gb_processed = scsi_log["read"].get("gigabytes_processed")
        if gb_processed is not None:
            read_bytes = int(float(gb_processed) * 10**9)
            read_raw = int(read_bytes / 512)

    sata_wear = None
    for attr_id in [177, 233, 202]:
        val = get_sata_attr(attr_id, get_normalized=True)
        if val is not None:
            sata_wear = val
            break

    nvme_wear = nvme_log.get("percentage_used")
    sas_wear = data.get("scsi_percentage_used_endurance_indicator")

    if sata_wear is not None:
        wear = sata_wear
    elif nvme_wear is not None:
        wear = nvme_wear
    elif sas_wear is not None:
        wear = sas_wear
    else:
        wear = None

    poh = get_sata_attr(9) or data.get("power_on_time", {}).get("hours")
    poh_val = safe_int(poh, None)
    poh_days = round(poh_val / 24, 1) if poh_val is not None else None
    temp = get_sata_attr(194) or get_sata_attr(190) or data.get("temperature", {}).get("current")
    sata_realloc = get_sata_attr(5)
    sas_realloc = data.get("scsi_grown_defect_list")
    if sata_realloc is not None:
        realloc = sata_realloc
    elif sas_realloc is not None:
        realloc = sas_realloc
    else:
        realloc = scsi_log.get("read", {}).get("total_uncorrectable_errors")
    pend = get_sata_attr(197)
    errs = get_sata_attr(199) or data.get("scsi_non_medium_error_count") or nvme_log.get("error_log_entries")

    sata_realloc_details = get_sata_attr_details(5)
    realloc_normalized = sata_realloc_details.get("normalized") if sata_realloc_details else None
    realloc_threshold = sata_realloc_details.get("thresh") if sata_realloc_details else None

    if nvme_log:
        realloc_normalized = nvme_log.get("available_spare")
        realloc_threshold = nvme_log.get("available_spare_threshold")

    status_str = "UNKNOWN"
    smart_status = data.get("smart_status", {})
    if smart_status.get("passed") is True:
        status_str = "PASSED"
    elif smart_status.get("passed") is False:
        status_str = "FAILED"

    return {
        "status": status_str, "model": model, "serial": serial, "capacity_str": capacity_str,
        "capacity_bytes": capacity_bytes, "wear_level": wear, "reallocated_sectors": realloc,
        "reallocated_normalized": realloc_normalized, "reallocated_threshold": realloc_threshold,
        "pending_sectors": pend, "power_on_hours": poh_val, "power_on_days": poh_days, "temperature": temp,
        "interface_errors": errs, "data_written_raw": written_raw, "data_written_bytes": written_bytes,
        "data_read_raw": read_raw, "data_read_bytes": read_bytes, "raw": raw_output
    }

def detect_interface_type(by_path_value, device, configured_type=None, smart_output=None):
    value = (by_path_value or "").lower()
    dev = (device or "").lower()
    smart_hint = classify_interface_from_smart(smart_output)
    if smart_hint:
        return smart_hint
    if "nvme" in value or dev.startswith("/dev/nvme"):
        return "nvme"
    if "sas" in value:
        return "sas"
    if "ata" in value:
        return "sata"
    return "sata" if dev.startswith("/dev/sd") else "unknown"

def detect_sata_capabilities(device, diagnostics=None):
    capabilities = {
        "supports_secure_erase": False,
        "supports_enhanced_secure_erase": False,
        "supports_crypto_erase": False,
        "supports_block_erase": False,
    }
    if not HDPARM_CMD:
        if diagnostics is not None:
            diagnostics["hdparm"] = {"ok": False, "reason": "command_not_resolved"}
        return capabilities
    output = run_command([HDPARM_CMD, "-I", device], diagnostics, "hdparm")
    if not output:
        return capabilities
    
    if re.search(r"Security:", output, re.IGNORECASE):
        if re.search(r"\bsupported\b", output, re.IGNORECASE):
            capabilities["supports_secure_erase"] = True
        if re.search(r"\benhanced erase\b", output, re.IGNORECASE):
            capabilities["supports_enhanced_secure_erase"] = True
            
    output_lowered = output.lower()
    if "sanitize feature set" in output_lowered:
        if "crypto_scramble_ext" in output_lowered or "cryptographic scramble" in output_lowered:
            capabilities["supports_crypto_erase"] = True
        if "block_erase_ext" in output_lowered or "block erase" in output_lowered:
            capabilities["supports_block_erase"] = True
            
    return capabilities

def detect_nvme_capabilities(device, diagnostics=None):
    capabilities = {"supports_crypto_erase": False, "supports_block_erase": False}
    if not NVME_CMD:
        return capabilities
    output = run_command([NVME_CMD, "id-ctrl", device], diagnostics, "nvme")
    if not output:
        return capabilities
    sanicap_match = re.search(r"sanicap\s*:\s*0x([0-9a-fA-F]+)", output)
    if not sanicap_match:
        return capabilities
    sanicap_value = int(sanicap_match.group(1), 16)
    capabilities["supports_crypto_erase"] = bool(sanicap_value & (1 << 0))
    capabilities["supports_block_erase"] = bool(sanicap_value & (1 << 1))
    return capabilities

def detect_sas_capabilities(device, diagnostics=None):
    capabilities = {"supports_crypto_erase": False, "supports_block_erase": False}
    if not SG_SANITIZE_CMD:
        return capabilities
    output = run_command([SG_SANITIZE_CMD, "--status", device], diagnostics, "sg_sanitize")
    if not output:
        return capabilities
    lowered = output.lower()
    if any(marker in lowered for marker in ["sanitize", "in progress", "completed", "idle", "status"]):
        capabilities["supports_block_erase"] = True
    return capabilities

def detect_drive_capabilities(interface_type, device, diagnostics=None):
    capabilities = {
        "supports_crypto_erase": False, "supports_block_erase": False, "supports_secure_erase": False,
        "supports_enhanced_secure_erase": False, "supports_overwrite": True
    }
    if not device:
        return capabilities
    if interface_type == "nvme":
        capabilities.update(detect_nvme_capabilities(device, diagnostics))
    elif interface_type == "sata":
        capabilities.update(detect_sata_capabilities(device, diagnostics))
    elif interface_type == "sas":
        capabilities.update(detect_sas_capabilities(device, diagnostics))
    return capabilities

def execute_erase_method(device, interface_type, method):
    selected_method = str(method or "").strip().lower()
    iface = str(interface_type or "").strip().lower()
    if not device:
        return {"ok": False, "error": "missing_device", "command": None, "stdout": "", "stderr": "", "exit_code": None}

    if selected_method == "overwrite":
        if not DD_CMD:
            return {"ok": False, "error": "dd_not_available", "command": None, "stdout": "", "stderr": "", "exit_code": None}
        command = [DD_CMD, "if=/dev/zero", f"of={device}", "bs=16M", "status=progress", "oflag=direct"]
        result = run_destructive_command(command)
        result["command"] = " ".join(command)
        return result

    if selected_method in {"secure_erase", "enhanced_secure_erase"}:
        if not HDPARM_CMD:
            return {"ok": False, "error": "hdparm_not_available", "command": None, "stdout": "", "stderr": "", "exit_code": None}
        if iface != "sata":
            return {"ok": False, "error": "secure_erase_requires_sata", "command": None, "stdout": "", "stderr": "", "exit_code": None}
        user_password = "wipestation"
        set_pass_cmd = [HDPARM_CMD, "--user-master", "u", "--security-set-pass", user_password, device]
        erase_flag = "--security-erase-enhanced" if selected_method == "enhanced_secure_erase" else "--security-erase"
        erase_cmd = [HDPARM_CMD, "--user-master", "u", erase_flag, user_password, device]
        first = run_destructive_command(set_pass_cmd)
        first["command"] = " ".join(set_pass_cmd)
        if not first.get("ok"):
            return first
        second = run_destructive_command(erase_cmd)
        second["command"] = " ".join(erase_cmd)
        return second

    if selected_method in {"block", "crypto"}:
        if iface == "nvme":
            if not NVME_CMD:
                return {"ok": False, "error": "nvme_not_available", "command": None, "stdout": "", "stderr": "", "exit_code": None}
            action = "crypto" if selected_method == "crypto" else "block"
            command = [NVME_CMD, "sanitize", device, "-a", action]
            result = run_destructive_command(command)
            result["command"] = " ".join(command)
            return result
        if iface == "sas":
            if not SG_SANITIZE_CMD:
                return {"ok": False, "error": "sg_sanitize_not_available", "command": None, "stdout": "", "stderr": "", "exit_code": None}
            if selected_method == "crypto":
                return {"ok": False, "error": "crypto_not_supported_for_sas_currently", "command": None, "stdout": "", "stderr": "", "exit_code": None}
            command = [SG_SANITIZE_CMD, "--block", device]
            result = run_destructive_command(command)
            result["command"] = " ".join(command)
            return result

    return {"ok": False, "error": f"unsupported_method_or_interface:{selected_method}:{iface}", "command": None, "stdout": "", "stderr": "", "exit_code": None}

def resolve_bay_device(target_path, path_to_dev):
    if target_path is None:
        return None, None
    configured = str(target_path).strip()
    if not configured:
        return None, None
    normalized = configured.replace("\\", "/")
    if normalized.startswith("/dev/disk/by-path/"):
        normalized = normalized[len("/dev/disk/by-path/"):]
    else:
        normalized = os.path.basename(normalized)
    if normalized in path_to_dev:
        return normalized, path_to_dev[normalized]
    if configured in path_to_dev:
        return configured, path_to_dev[configured]
    if configured.startswith("/dev/"):
        configured_real = os.path.realpath(configured)
        for by_path_name, dev_node in path_to_dev.items():
            if os.path.realpath(dev_node) == configured_real:
                return by_path_name, dev_node
    return None, None

def calculate_drive_health_score(interface_type, smart_data, raw_json):
    iface = str(interface_type or "unknown").lower()
    model_lower = str(smart_data.get("model") or "").lower()
    
    is_ssd = "ssd" in model_lower or "nvme" in iface or smart_data.get("wear_level") is not None

    wear = smart_data.get("wear_level")
    poh = safe_int(smart_data.get("power_on_hours"), 0)
    
    if is_ssd and wear is not None:
        wear_val = safe_int(wear, 0)
        if iface in {"nvme", "sas"}:
            base_score = max(0, 100 - wear_val)
        else:
            base_score = wear_val
            
        if poh > 40000:
            ssd_poh_penalty = min(20, max(0, (poh - 40000) / 40000 * 20))
            base_score = max(10, base_score - ssd_poh_penalty)
    else:
        poh_penalty = 0
        if poh > 20000:
            poh_penalty = min(30, max(0, (poh - 20000) / 40000 * 30))
            
        written_bytes = smart_data.get("data_written_bytes")
        if written_bytes is None:
            raw_written = smart_data.get("data_written_raw")
            if raw_written is not None:
                written_bytes = safe_int(raw_written, 0) * 512
            else:
                written_bytes = 0
        else:
            written_bytes = safe_int(written_bytes, 0)

        capacity = safe_int(smart_data.get("capacity_bytes"), 0)
        fdw = (written_bytes / capacity) if capacity > 0 else 0.0
        
        fdw_penalty = min(30, max(0, (fdw / 150.0) * 30))
        base_score = max(40, 100 - poh_penalty - fdw_penalty)

    reallocated = safe_int(smart_data.get("reallocated_sectors"), 0)
    pending = safe_int(smart_data.get("pending_sectors"), 0)
    errs = safe_int(smart_data.get("interface_errors"), 0)

    realloc_penalty = 0
    if is_ssd:
        realloc_normalized = smart_data.get("reallocated_normalized")
        if realloc_normalized is not None:
            norm_val = safe_int(realloc_normalized, 100)
            if norm_val < 100:
                realloc_penalty = min(40, (100 - norm_val) * 1)
    else:
        if reallocated > 0:
            if reallocated == 1:
                realloc_penalty = 10
            elif reallocated <= 5:
                realloc_penalty = 10 + (reallocated - 1) * 5
            else:
                realloc_penalty = 10 + 20 + (reallocated - 5) * 10
            realloc_penalty = min(40, realloc_penalty)

    pending_penalty = min(60, pending * 15)

    nvme_media_penalty = 0
    if iface == "nvme" and raw_json:
        try:
            data = json.loads(raw_json)
            nvme_log = data.get("nvme_smart_health_information_log", {})
            media_errors = safe_int(nvme_log.get("media_errors"), 0)
            nvme_media_penalty = min(80, media_errors * 20)
        except Exception:
            pass

    interface_penalty = 10 if errs > 50 else 0

    score = max(0, base_score - realloc_penalty - pending_penalty - nvme_media_penalty - interface_penalty)

    status = str(smart_data.get("status") or "UNKNOWN").upper()
    failed_override = False
    
    if status == "FAILED":
        failed_override = True
        
    if raw_json:
        try:
            data = json.loads(raw_json)
            exit_status = data.get("smartctl", {}).get("exit_status")
            if exit_status is not None:
                exit_status_val = safe_int(exit_status, 0)
                if (exit_status_val & 8 != 0) or (exit_status_val & 16 != 0):
                    failed_override = True
                    
            if iface == "nvme":
                nvme_log = data.get("nvme_smart_health_information_log", {})
                crit_warn = nvme_log.get("critical_warning")
                if crit_warn is not None:
                    crit_warn_val = safe_int(crit_warn, 0)
                    if (crit_warn_val & 0x04 != 0) or (crit_warn_val & 0x08 != 0):
                        failed_override = True
        except Exception:
            pass

    if failed_override:
        score = min(score, 5)

    return int(round(score))

def get_drive_recommendation(interface_type, smart, health_score=None):
    iface = str(interface_type or "unknown").lower()
    is_ssd = "ssd" in str(smart.get("model") or "").lower() or "nvme" in iface or smart.get("wear_level") is not None
    poh = safe_int(smart.get("power_on_hours"), 0)
    status = str(smart.get("status") or "UNKNOWN").upper()
    pending = safe_int(smart.get("pending_sectors"), 0)
    realloc_raw = safe_int(smart.get("reallocated_sectors"), 0)
    
    realloc_norm = smart.get("reallocated_normalized")
    realloc_norm = safe_int(realloc_norm, 100) if realloc_norm is not None else 100
    
    realloc_thresh = smart.get("reallocated_threshold")
    realloc_thresh = safe_int(realloc_thresh, 10) if realloc_thresh is not None else 10

    written_bytes = smart.get("data_written_bytes")
    if written_bytes is None:
        raw_written = smart.get("data_written_raw")
        if raw_written is not None:
            written_bytes = safe_int(raw_written, 0) * (512000 if "nvme" in iface else 512)
        else:
            written_bytes = 0
    else:
        written_bytes = safe_int(written_bytes, 0)

    capacity = safe_int(smart.get("capacity_bytes"), 0)
    fdw = (written_bytes / capacity) if capacity > 0 else 0.0

    remaining_life = 100
    wear = smart.get("wear_level")
    if wear is not None:
        wear_val = safe_int(wear, 0)
        if "nvme" in iface or "sas" in iface:
            remaining_life = max(0, 100 - wear_val)
        else:
            remaining_life = wear_val

    if health_score is not None:
        if status == "FAILED" or health_score <= 20:
            return {"status": "DESTROY", "comment": "Drive shows critical physical degradation or SMART health failure."}
        if health_score <= 60:
            return {"status": "SCRATCH", "comment": "Unstable or significantly aged drive. Safe only for non-critical use."}
    else:
        if status == "FAILED" or realloc_norm < 50 or pending > 10:
            return {"status": "DESTROY", "comment": "Drive shows critical physical degradation or SMART health failure."}
        if realloc_norm <= realloc_thresh or (0 < pending <= 10):
            return {"status": "SCRATCH", "comment": "Unstable or threshold-breached sectors detected. Safe only for non-critical use."}

    if is_ssd:
        if remaining_life < 10:
            return {"status": "DESTROY", "comment": "SSD wear is fully depleted (less than 10% life remaining)."}
        if remaining_life < 60:
            return {"status": "SCRATCH", "comment": "SSD remaining life is heavily worn (under 60%). Relegate to scratch."}
        if poh < SSD_NEW_POH_THRESHOLD and fdw < 0.06 and remaining_life == 100 and realloc_raw == 0:
            return {"status": "NEW_STOCK", "comment": "This drive is practically new (low runtime, pristine life and sectors)."}
        if remaining_life >= 80:
            if poh >= SSD_HIGH_POH_THRESHOLD:
                return {"status": "USED_HEAVY", "comment": f"Excellent health, but high runtime (exceeds {SSD_HIGH_POH_THRESHOLD:,} hours)."}
            return {"status": "USED_GOOD", "comment": "This drive is used but still has excellent remaining life."}
        return {"status": "USED_HEAVY", "comment": "This drive is heavily used but still has life."}
    else:
        if poh >= HDD_HIGH_POH_THRESHOLD:
            return {"status": "SCRATCH", "comment": f"High Power-On Hours (exceeds {HDD_HIGH_POH_THRESHOLD:,} server hours)."}
        if poh < HDD_NEW_POH_THRESHOLD and fdw < 1.0 and realloc_raw == 0:
            return {"status": "NEW_STOCK", "comment": "Practically new (extremely low runtime and zero sector reallocations)."}
        if fdw >= 150:
            return {"status": "USED_HEAVY", "comment": "High workload or raw sector writes history. Monitor closely."}
        return {"status": "USED_GOOD", "comment": "Used but has clean write history and moderate runtime."}

def discover_drives(bay_map_path='/opt/drive-eraser/config/bay_map.json', running_devices=None):
    with open(bay_map_path, 'r', encoding='utf-8') as f:
        bay_map = json.load(f)
    path_to_dev = {}
    by_path_dir = '/dev/disk/by-path/'
    if os.path.exists(by_path_dir):
        for entry in os.listdir(by_path_dir):
            full_path = os.path.join(by_path_dir, entry)
            if os.path.islink(full_path):
                path_to_dev[entry] = os.path.realpath(full_path)

    results = []
    passphrase = None
    try:
        policy = load_policy(get_config_dir())
        passphrase = policy.get("wipe_passphrase")
    except Exception:
        passphrase = None

    for bay_id, config in bay_map.items():
        target_path = config.get('by_path')
        bay_info = {
            "bay": bay_id, "label": config.get('label', bay_id), "role": config.get('role', 'wipe'),
            "locked": config.get('locked', False), "configured_by_path": target_path, "resolved_by_path": None,
            "present": False, "device": None, "serial": None, "model": None, "status": "EMPTY",
            "interface_type": "unknown", "capacity_str": "-", "marker": {"ok": False, "status": "none", "error": None, "details": {}},
            "recommendation": {"status": "UNKNOWN", "comment": "-"},
            "health_score": 100,
            "capabilities": {"supports_crypto_erase": False, "supports_block_erase": False, "supports_secure_erase": False, "supports_enhanced_secure_erase": False, "supports_overwrite": True},
            "supported_methods": ["overwrite"],
            "smart": {}, "diagnostics": {"mapping": {"ok": False, "reason": "not_mapped"}, "commands": {}}
        }
        matched_by_path, dev_node = resolve_bay_device(target_path, path_to_dev)
        if dev_node:
            bay_info["diagnostics"]["mapping"] = {"ok": True, "reason": None}
            
            if running_devices and dev_node in running_devices:
                bay_info.update({
                    "resolved_by_path": matched_by_path,
                    "present": True,
                    "device": dev_node,
                    "status": "RUNNING",
                    "interface_type": detect_interface_type(matched_by_path or target_path, dev_node, config.get('type'), None),
                    "capacity_str": "Sanitizing..."
                })
                results.append(bay_info)
                continue

            command_diagnostics = {}
            smart = get_smart_data(dev_node, command_diagnostics)
            interface_type = detect_interface_type(matched_by_path or target_path, dev_node, config.get('type'), smart.get("raw"))
            capabilities = detect_drive_capabilities(interface_type, dev_node, command_diagnostics)
            marker_status = read_marker_status(dev_node, interface_type, passphrase)

            if marker_status.get("status") == "checksum_valid":
                stored_writes = marker_status.get("details", {}).get("data_written_at_wipe")
                current_writes = smart.get("data_written_raw")
                is_pristine = check_write_tolerance(interface_type, current_writes, stored_writes)
                marker_status["is_pristine"] = is_pristine
                if not is_pristine:
                    marker_status["status"] = "written_since_wipe"
                else:
                    marker_status["status"] = "pristine_secure" if marker_status.get("hmac_verified") else "pristine_insecure"

            health_score = calculate_drive_health_score(interface_type, smart, smart.get("raw"))
            recommendation = get_drive_recommendation(interface_type, smart, health_score=health_score)

            bay_info.update({
                "resolved_by_path": matched_by_path, "present": True, "device": dev_node, "serial": smart.get("serial"),
                "model": smart.get("model"), "status": smart.get("status", "UNKNOWN"), "interface_type": interface_type,
                "capacity_str": smart.get("capacity_str", "-"), "capabilities": capabilities, "marker": marker_status,
                "recommendation": recommendation,
                "health_score": health_score,
                "supported_methods": [m for m, s in {
                    "crypto": capabilities.get("supports_crypto_erase", False),
                    "block": capabilities.get("supports_block_erase", False),
                    "secure_erase": capabilities.get("supports_secure_erase", False),
                    "enhanced_secure_erase": capabilities.get("supports_enhanced_secure_erase", False),
                    "overwrite": capabilities.get("supports_overwrite", False),
                }.items() if s],
                "diagnostics": {"mapping": {"ok": True, "reason": None}, "commands": command_diagnostics},
                "smart": {
                    "temperature": smart.get("temperature"), "reallocated_sectors": smart.get("reallocated_sectors"),
                    "pending_sectors": smart.get("pending_sectors"), "wear_level": smart.get("wear_level"),
                    "power_on_hours": smart.get("power_on_hours"), "power_on_days": smart.get("power_on_days"),
                    "interface_errors": smart.get("interface_errors"), "data_read_raw": smart.get("data_read_raw"),
                    "data_read_bytes": smart.get("data_read_bytes"), "data_written_raw": smart.get("data_written_raw"),
                    "data_written_bytes": smart.get("data_written_bytes"), "reallocated_normalized": smart.get("reallocated_normalized"),
                    "reallocated_threshold": smart.get("reallocated_threshold"), "capacity_bytes": smart.get("capacity_bytes"),
                    "raw": smart.get("raw")
                }
            })
        else:
            bay_info["diagnostics"]["mapping"] = {"ok": False, "reason": "by_path_not_found" if target_path else "missing_by_path"}
        results.append(bay_info)
    return results
# --- END OF FILE backend/disk_ops.py ---