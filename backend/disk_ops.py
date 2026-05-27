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
    try: return int(val) if val is not None else default
    except (ValueError, TypeError): return default

def safe_float(val, default=0.0):
    try: return float(val) if val is not None else default
    except (ValueError, TypeError): return default

def resolve_command_path(command_name, candidates, env_var_name=None):
    env_value = os.getenv(env_var_name) if env_var_name else None
    if env_value and os.path.exists(env_value) and os.access(env_value, os.X_OK):
        return env_value
    for candidate in candidates:
        if candidate and os.path.exists(candidate) and os.access(candidate, os.X_OK):
            return candidate
    resolved = shutil.which(command_name)
    return resolved if (resolved and os.path.exists(resolved) and os.access(resolved, os.X_OK)) else None

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def get_config_dir():
    for candidate in [os.getenv("DRIVE_ERASER_CONFIG_DIR"), os.path.join(PROJECT_ROOT, "config"), "/opt/drive-eraser/config"]:
        if candidate and os.path.isdir(candidate): return candidate
    return os.path.join(PROJECT_ROOT, "config")

def load_command_path_overrides():
    config_path = os.path.join(get_config_dir(), "command_paths.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception: pass
    return {}

COMMAND_PATH_OVERRIDES = load_command_path_overrides()

SMARTCTL_CMD = resolve_command_path("smartctl", [COMMAND_PATH_OVERRIDES.get("smartctl"), "/usr/sbin/smartctl", "/usr/bin/smartctl", "/bin/smartctl"], "DRIVE_ERASER_SMARTCTL_PATH")
NVME_CMD = resolve_command_path("nvme", [COMMAND_PATH_OVERRIDES.get("nvme"), "/usr/sbin/nvme", "/usr/bin/nvme", "/bin/nvme"], "DRIVE_ERASER_NVME_PATH")
HDPARM_CMD = resolve_command_path("hdparm", [COMMAND_PATH_OVERRIDES.get("hdparm"), "/usr/sbin/hdparm", "/usr/bin/hdparm", "/bin/hdparm"], "DRIVE_ERASER_HDPARM_PATH")
SG_SANITIZE_CMD = resolve_command_path("sg_sanitize", [COMMAND_PATH_OVERRIDES.get("sg_sanitize"), "/usr/bin/sg_sanitize", "/usr/sbin/sg_sanitize", "/bin/sg_sanitize"], "DRIVE_ERASER_SG_SANITIZE_PATH")
DD_CMD = resolve_command_path("dd", [COMMAND_PATH_OVERRIDES.get("dd"), "/usr/bin/dd", "/bin/dd"], "DRIVE_ERASER_DD_PATH")

def load_policy(config_dir):
    policy_path = os.path.join(config_dir, "policy.json")
    if os.path.exists(policy_path):
        try:
            with open(policy_path, "r", encoding="utf-8") as f: return json.load(f)
        except Exception: pass
    return {}

def check_write_tolerance(interface_type, current, stored):
    if current is None or stored is None: return False
    try:
        diff = int(current) - int(stored)
        if diff < 0: return False
        iface = str(interface_type or "unknown").lower()
        return (diff <= 4) if "nvme" in iface else (diff <= 4096)
    except Exception: return False

def format_capacity_bytes(num_bytes):
    if not num_bytes: return "-"
    tb = num_bytes / (10**12)
    if tb >= 1.0: return f"{round(tb)} TB" if abs(tb - round(tb)) < 0.05 else f"{tb:.2f} TB"
    gb = num_bytes / (10**9)
    if gb >= 1.0: return f"{round(gb)} GB" if abs(gb - round(gb)) < 0.5 else f"{gb:.1f} GB"
    return f"{round(num_bytes / (10**6))} MB"

def read_marker_status(device, interface_type="unknown", passphrase=None):
    if not DD_CMD: return {"ok": False, "status": "marker_error", "error": "dd_not_available_for_marker_read", "details": {}}
    command = [DD_CMD, f"if={device}", f"bs={MARKER_BLOCK_SIZE}", "count=1", "iflag=direct", "status=none"]
    try:
        result = subprocess.run(["sudo"] + command, capture_output=True)
        if result.returncode != 0:
            result = subprocess.run(["sudo", DD_CMD, f"if={device}", f"bs={MARKER_BLOCK_SIZE}", "count=1", "status=none"], capture_output=True)
        if result.returncode != 0:
            return {"ok": False, "status": "marker_error", "error": "marker_read_failed", "details": {"return_code": result.returncode, "stderr": (result.stderr or b"").decode("utf-8", errors="replace").strip()}}
        output_bytes = result.stdout or b""
    except Exception as e:
        return {"ok": False, "status": "marker_error", "error": f"marker_read_exception:{e}", "details": {}}

    marker_index = output_bytes.find(MARKER_SIGNATURE.encode("utf-8"))
    if marker_index < 0: return {"ok": True, "status": "none", "error": None, "details": {}}

    start = output_bytes.rfind(b"{", 0, marker_index)
    end = output_bytes.find(b"}", marker_index)
    if start < 0 or end < start: return {"ok": True, "status": "corrupted", "error": "json_parse_failed", "details": {}}

    try:
        parsed = json.loads(output_bytes[start:end + 1].decode("utf-8", errors="strict"))
    except Exception:
        return {"ok": True, "status": "corrupted", "error": "json_parse_failed", "details": {}}

    if parsed.get("signature") != MARKER_SIGNATURE: return {"ok": True, "status": "corrupted", "error": "invalid_signature", "details": {}}

    stored_checksum, stored_hmac = parsed.pop("checksum", None), parsed.pop("hmac", None)
    serialized_for_checksum = json.dumps(parsed, sort_keys=True, separators=(',', ':')).encode('utf-8')
    calculated_checksum = hashlib.sha256(serialized_for_checksum).hexdigest()

    if calculated_checksum != stored_checksum: return {"ok": True, "status": "corrupted", "error": "checksum_mismatch", "details": {}}
    parsed["checksum"] = stored_checksum

    hmac_verified = False
    if passphrase and stored_hmac:
        serialized_for_hmac = json.dumps(parsed, sort_keys=True, separators=(',', ':')).encode('utf-8')
        derived_key = hashlib.pbkdf2_hmac('sha256', passphrase.encode('utf-8'), b'DWS_SALT_v1', 10000)
        calculated_hmac = hmac.new(derived_key, serialized_for_hmac, hashlib.sha256).hexdigest()
        hmac_verified = hmac.compare_digest(calculated_hmac, stored_hmac)

    return {
        "ok": True, "status": "checksum_valid", "hmac_verified": hmac_verified, "error": None,
        "details": {
            "job_id": parsed.get("job_id"), "finished_at": parsed.get("finished_at"),
            "method": parsed.get("method"), "serial": parsed.get("serial"),
            "ticket_number": parsed.get("ticket_number"), "data_written_at_wipe": parsed.get("data_written_at_wipe"),
        }
    }

def run_command(command, diagnostics=None, key=None):
    if not command or not command[0]:
        if diagnostics is not None and key: diagnostics[key] = {"ok": False, "reason": "command_not_resolved"}
        return None
    try:
        result = subprocess.run(["sudo"] + command, capture_output=True, text=True, check=True)
        if diagnostics is not None and key: diagnostics[key] = {"ok": True, "reason": None, "exit_code": result.returncode}
        return (result.stdout or "").strip()
    except subprocess.CalledProcessError as e:
        if diagnostics is not None and key: diagnostics[key] = {"ok": False, "reason": (e.stderr or "").strip() or f"exit_code_{e.returncode}", "exit_code": e.returncode}
        return (e.stdout or "").strip() if command and os.path.basename(command[0]) == "smartctl" else None

def run_destructive_command(command):
    if not command or not command[0]: return {"ok": False, "error": "command_not_resolved", "stdout": "", "stderr": "", "exit_code": None}
    try:
        result = subprocess.run(["sudo"] + command, capture_output=True, text=True, check=True)
        return {"ok": True, "error": None, "stdout": (result.stdout or "").strip(), "stderr": (result.stderr or "").strip(), "exit_code": result.returncode}
    except subprocess.CalledProcessError as e:
        return {"ok": False, "error": "command_failed", "stdout": (e.stdout or "").strip(), "stderr": (e.stderr or "").strip(), "exit_code": e.returncode}
    except FileNotFoundError:
        return {"ok": False, "error": "sudo_or_command_not_found", "stdout": "", "stderr": "", "exit_code": None}

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
        "rotation_rate": None
    }
    if not SMARTCTL_CMD: return empty_template
    raw_output = run_command([SMARTCTL_CMD, "-j", "-x", device], diagnostics, "smartctl")
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

    status_str = "UNKNOWN"
    smart_status = data.get("smart_status", {})
    if smart_status.get("passed") is True: status_str = "PASSED"
    elif smart_status.get("passed") is False: status_str = "FAILED"

    return {
        "status": status_str, "model": model, "serial": serial, "capacity_str": capacity_str,
        "capacity_bytes": capacity_bytes, "wear_level": wear, "reallocated_sectors": realloc,
        "reallocated_normalized": realloc_normalized, "reallocated_threshold": realloc_threshold,
        "pending_sectors": pend, "power_on_hours": poh_val, "power_on_days": poh_days, "temperature": temp,
        "interface_errors": errs, "data_written_raw": written_raw, "data_written_bytes": written_bytes,
        "data_read_raw": read_raw, "data_read_bytes": read_bytes, "raw": raw_output, "rotation_rate": rotation_rate
    }

def get_raw_smart_diagnostics(device):
    if not SMARTCTL_CMD or not device:
        return "SMARTCTL command not resolved or invalid device target.\n"
    try:
        result = subprocess.run(["sudo", SMARTCTL_CMD, "-a", device], capture_output=True, text=True, timeout=15)
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
    return "sata" if dev.startswith("/dev/sd") else "unknown"

def detect_sata_capabilities(device, diagnostics=None):
    capabilities = {"supports_secure_erase": False, "supports_enhanced_secure_erase": False, "supports_crypto_erase": False, "supports_block_erase": False}
    if not HDPARM_CMD:
        if diagnostics is not None: diagnostics["hdparm"] = {"ok": False, "reason": "command_not_resolved"}
        return capabilities
    output = run_command([HDPARM_CMD, "-I", device], diagnostics, "hdparm")
    if not output: return capabilities
    
    if re.search(r"Security:", output, re.IGNORECASE):
        if re.search(r"\bsupported\b", output, re.IGNORECASE): capabilities["supports_secure_erase"] = True
        if re.search(r"\benhanced erase\b", output, re.IGNORECASE): capabilities["supports_enhanced_secure_erase"] = True
            
    output_lowered = output.lower()
    if "sanitize feature set" in output_lowered:
        if "crypto_scramble_ext" in output_lowered or "cryptographic scramble" in output_lowered: capabilities["supports_crypto_erase"] = True
        if "block_erase_ext" in output_lowered or "block erase" in output_lowered: capabilities["supports_block_erase"] = True
    return capabilities

def detect_nvme_capabilities(device, diagnostics=None):
    capabilities = {"supports_crypto_erase": False, "supports_block_erase": False}
    if not NVME_CMD: return capabilities
    output = run_command([NVME_CMD, "id-ctrl", device], diagnostics, "nvme")
    if not output: return capabilities
    sanicap_match = re.search(r"sanicap\s*:\s*0x([0-9a-fA-F]+)", output)
    if not sanicap_match: return capabilities
    sanicap_value = int(sanicap_match.group(1), 16)
    capabilities["supports_crypto_erase"] = bool(sanicap_value & (1 << 0))
    capabilities["supports_block_erase"] = bool(sanicap_value & (1 << 1))
    return capabilities

def detect_sas_capabilities(device, diagnostics=None):
    capabilities = {"supports_crypto_erase": False, "supports_block_erase": False}
    if not SG_SANITIZE_CMD: return capabilities
    output = run_command([SG_SANITIZE_CMD, "--status", device], diagnostics, "sg_sanitize")
    if not output: return capabilities
    if any(marker in output.lower() for marker in ["sanitize", "in progress", "completed", "idle", "status"]):
        capabilities["supports_block_erase"] = True
    return capabilities

def detect_drive_capabilities(interface_type, device, diagnostics=None):
    capabilities = {"supports_crypto_erase": False, "supports_block_erase": False, "supports_secure_erase": False, "supports_enhanced_secure_erase": False, "supports_overwrite": True}
    if not device: return capabilities
    if interface_type == "nvme": capabilities.update(detect_nvme_capabilities(device, diagnostics))
    elif interface_type == "sata": capabilities.update(detect_sata_capabilities(device, diagnostics))
    elif interface_type == "sas": capabilities.update(detect_sas_capabilities(device, diagnostics))
    return capabilities

def execute_erase_method(device, interface_type, method):
    selected_method, iface = str(method or "").strip().lower(), str(interface_type or "").strip().lower()
    if not device: return {"ok": False, "error": "missing_device", "command": None, "stdout": "", "stderr": "", "exit_code": None}

    if selected_method == "overwrite":
        if not DD_CMD: return {"ok": False, "error": "dd_not_available", "command": None, "stdout": "", "stderr": "", "exit_code": None}
        command = [DD_CMD, "if=/dev/zero", f"of={device}", "bs=16M", "status=progress", "oflag=direct"]
        result = run_destructive_command(command)
        result["command"] = " ".join(command)
        return result

    if selected_method in {"secure_erase", "enhanced_secure_erase"}:
        if not HDPARM_CMD: return {"ok": False, "error": "hdparm_not_available", "command": None, "stdout": "", "stderr": "", "exit_code": None}
        if iface != "sata": return {"ok": False, "error": "secure_erase_requires_sata", "command": None, "stdout": "", "stderr": "", "exit_code": None}
        user_password = "wipestation"

        security_enabled = False
        try:
            init_output = run_command([HDPARM_CMD, "-I", device])
            if init_output:
                sec_match = re.search(r"^[ \t]*Security:[ \t]*\n((?:[ \t]+.*\n?)+)", init_output, re.IGNORECASE | re.MULTILINE)
                if not sec_match:
                    sec_match = re.search(r"security:\s*(.*?)(?:\n\s*\n|$)", init_output, re.IGNORECASE | re.DOTALL)
                if sec_match:
                    sec_sec = sec_match.group(1).lower()
                    sec_lines = [line.strip() for line in sec_sec.splitlines()]
                    security_enabled = any(re.search(r"\benabled\b", line) and not re.search(r"\bnot\b", line) for line in sec_lines)
        except Exception:
            pass

        if security_enabled:
            disable_res = run_destructive_command([HDPARM_CMD, "--user-master", "u", "--security-disable", user_password, device])
            if disable_res.get("ok"):
                security_enabled = False
            else:
                erase_flag = "--security-erase-enhanced" if selected_method == "enhanced_secure_erase" else "--security-erase"
                second = run_destructive_command([HDPARM_CMD, "--user-master", "u", erase_flag, user_password, device])
                second["command"] = f"hdparm {erase_flag} (direct)"
                return second

        first = run_destructive_command([HDPARM_CMD, "--user-master", "u", "--security-set-pass", user_password, device])
        if not first.get("ok"): return first
        erase_flag = "--security-erase-enhanced" if selected_method == "enhanced_secure_erase" else "--security-erase"
        second = run_destructive_command([HDPARM_CMD, "--user-master", "u", erase_flag, user_password, device])
        second["command"] = f"hdparm security-set-pass && hdparm {erase_flag}"
        return second

    if selected_method in {"block", "crypto"}:
        if iface == "nvme":
            if not NVME_CMD: return {"ok": False, "error": "nvme_not_available", "command": None, "stdout": "", "stderr": "", "exit_code": None}
            command = [NVME_CMD, "sanitize", device, "-a", "crypto" if selected_method == "crypto" else "block"]
            result = run_destructive_command(command)
            result["command"] = " ".join(command)
            return result
        if iface == "sas":
            if not SG_SANITIZE_CMD: return {"ok": False, "error": "sg_sanitize_not_available", "command": None, "stdout": "", "stderr": "", "exit_code": None}
            if selected_method == "crypto": return {"ok": False, "error": "crypto_not_supported_for_sas_currently", "command": None, "stdout": "", "stderr": "", "exit_code": None}
            command = [SG_SANITIZE_CMD, "--block", device]
            result = run_destructive_command(command)
            result["command"] = " ".join(command)
            return result

    return {"ok": False, "error": f"unsupported_method_or_interface:{selected_method}:{iface}", "command": None, "stdout": "", "stderr": "", "exit_code": None}

def resolve_bay_device(target_path, path_to_dev):
    if target_path is None: return None, None
    configured = str(target_path).strip()
    if not configured: return None, None
    normalized = configured.replace("\\", "/")
    if normalized.startswith("/dev/disk/by-path/"): normalized = normalized[len("/dev/disk/by-path/"):]
    else: normalized = os.path.basename(normalized)
    if normalized in path_to_dev: return normalized, path_to_dev[normalized]
    if configured in path_to_dev: return configured, path_to_dev[configured]
    if configured.startswith("/dev/"):
        configured_real = os.path.realpath(configured)
        for by_path_name, dev_node in path_to_dev.items():
            if os.path.realpath(dev_node) == configured_real: return by_path_name, dev_node
    return None, None

def calculate_drive_health_score(interface_type, smart_data, raw_json):
    iface = str(interface_type or "unknown").lower()
    is_ssd = is_drive_ssd(interface_type, smart_data)
    wear = smart_data.get("wear_level")
    poh = safe_int(smart_data.get("power_on_hours"), 0)
    
    if is_ssd and wear is not None:
        wear_val = safe_int(wear, 0)
        base_score = max(0, 100 - wear_val) if iface in {"nvme", "sas"} else wear_val
        if poh > 40000:
            base_score = max(10, base_score - min(20, max(0, (poh - 40000) / 40000 * 20)))
    else:
        poh_penalty = min(30, max(0, (poh - 20000) / 40000 * 30)) if poh > 20000 else 0
        written_bytes = smart_data.get("data_written_bytes")
        if written_bytes is None:
            raw_written = smart_data.get("data_written_raw")
            written_bytes = safe_int(raw_written, 0) * 512 if raw_written is not None else 0
        else:
            written_bytes = safe_int(written_bytes, 0)
        capacity = safe_int(smart_data.get("capacity_bytes"), 0)
        fdw = (written_bytes / capacity) if capacity > 0 else 0.0
        base_score = max(40, 100 - poh_penalty - min(30, max(0, (fdw / 150.0) * 30)))

    reallocated = safe_int(smart_data.get("reallocated_sectors"), 0)
    pending = safe_int(smart_data.get("pending_sectors"), 0)
    errs = safe_int(smart_data.get("interface_errors"), 0)

    realloc_penalty = 0
    if is_ssd:
        realloc_normalized = smart_data.get("reallocated_normalized")
        if realloc_normalized is not None:
            norm_val = safe_int(realloc_normalized, 100)
            if norm_val < 100: realloc_penalty = min(40, (100 - norm_val) * 1)
    else:
        if reallocated > 0:
            realloc_penalty = min(40, 10 if reallocated == 1 else (10 + (reallocated - 1) * 5 if reallocated <= 5 else 30 + (reallocated - 5) * 10))

    pending_penalty = min(60, pending * 15)
    nvme_media_penalty = 0
    if iface == "nvme" and raw_json:
        try:
            nvme_log = json.loads(raw_json).get("nvme_smart_health_information_log", {})
            nvme_media_penalty = min(80, safe_int(nvme_log.get("media_errors"), 0) * 20)
        except Exception: pass

    score = max(0, base_score - realloc_penalty - pending_penalty - nvme_media_penalty - (10 if errs > 50 else 0))
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

    return min(int(round(score)), 5) if failed_override else int(round(score))

def get_drive_recommendation(interface_type, smart, health_score=None):
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

    if health_score is not None:
        if status == "FAILED" or health_score <= 20: return {"status": "DESTROY", "comment": "Drive shows critical physical degradation or SMART health failure."}
        if health_score <= 60: return {"status": "SCRATCH", "comment": "Unstable or significantly aged drive. Safe only for non-critical use."}
    else:
        if status == "FAILED" or realloc_norm < 50 or pending > 10: return {"status": "DESTROY", "comment": "Drive shows critical physical degradation or SMART health failure."}
        if realloc_norm <= realloc_thresh or (0 < pending <= 10): return {"status": "SCRATCH", "comment": "Unstable or threshold-breached sectors detected. Safe only for non-critical use."}

    if is_ssd:
        if remaining_life < 10: return {"status": "DESTROY", "comment": "SSD wear is fully depleted (less than 10% life remaining)."}
        if remaining_life < 60: return {"status": "SCRATCH", "comment": "SSD remaining life is heavily worn (under 60%). Relegate to scratch."}
        if poh < SSD_NEW_POH_THRESHOLD and fdw < 0.06 and remaining_life == 100 and realloc_raw == 0: return {"status": "NEW_STOCK", "comment": "This drive is practically new (low runtime, pristine life and sectors)."}
        return {"status": "USED_HEAVY" if poh >= SSD_HIGH_POH_THRESHOLD else "USED_GOOD", "comment": f"Excellent health, but high runtime (exceeds {SSD_HIGH_POH_THRESHOLD:,} hours)." if poh >= SSD_HIGH_POH_THRESHOLD else "This drive is used but still has excellent remaining life."} if remaining_life >= 80 else {"status": "USED_HEAVY", "comment": "This drive is heavily used but still has life."}
    else:
        if poh >= HDD_HIGH_POH_THRESHOLD: return {"status": "SCRATCH", "comment": f"High Power-On Hours (exceeds {HDD_HIGH_POH_THRESHOLD:,} server hours)."}
        if poh < HDD_NEW_POH_THRESHOLD and fdw < 1.0 and realloc_raw == 0: return {"status": "NEW_STOCK", "comment": "Practically new (extremely low runtime and zero sector reallocations)."}
        return {"status": "USED_HEAVY" if fdw >= 150 else "USED_GOOD", "comment": "High workload or raw sector writes history. Monitor closely." if fdw >= 150 else "Used but has clean write history and moderate runtime."}

# --- PROGRAMMATIC OS DRIVE DETECTION AND OVERRIDES ---

def get_os_parent_device():
    try:
        st = os.stat("/")
        major = os.major(st.st_dev)
        minor = os.minor(st.st_dev)
        
        uevent_path = f"/sys/dev/block/{major}:{minor}/uevent"
        devname = None
        if os.path.exists(uevent_path):
            with open(uevent_path, "r") as f:
                for line in f:
                    if line.startswith("DEVNAME="):
                        devname = line.strip().split("=")[1]
                        break
                        
        if not devname:
            try:
                res = subprocess.run(["findmnt", "-n", "-o", "SOURCE", "/"], capture_output=True, text=True, timeout=5)
                if res.returncode == 0 and res.stdout.strip():
                    src = res.stdout.strip()
                    if src.startswith("/dev/"):
                        devname = src[5:]
            except Exception:
                pass
                
        if not devname:
            if os.path.exists("/proc/mounts"):
                with open("/proc/mounts", "r") as f:
                    for line in f:
                        parts = line.split()
                        if len(parts) >= 2 and parts[1] == "/":
                            src = parts[0]
                            if src.startswith("/dev/"):
                                devname = src[5:]
                                break

        if not devname:
            return None
            
        def resolve_leaf_parent(name):
            sys_path = f"/sys/class/block/{name}"
            if not os.path.exists(sys_path):
                return name
            real_path = os.path.realpath(sys_path)
            if "/block/" in real_path:
                parts = real_path.split("/block/")
                if len(parts) > 1:
                    subparts = parts[1].split("/")
                    if len(subparts) > 0:
                        return subparts[0]
            return name

        if devname.startswith("dm-"):
            slaves_dir = f"/sys/class/block/{devname}/slaves"
            if os.path.isdir(slaves_dir):
                slaves = os.listdir(slaves_dir)
                if slaves:
                    return resolve_leaf_parent(slaves[0])
                    
        return resolve_leaf_parent(devname)
    except Exception:
        return None

def get_os_by_path():
    parent_name = get_os_parent_device()
    if not parent_name:
        return None, None
        
    dev_node = f"/dev/{parent_name}"
    by_path_dir = "/dev/disk/by-path/"
    if os.path.exists(by_path_dir):
        for entry in os.listdir(by_path_dir):
            full_path = os.path.join(by_path_dir, entry)
            if os.path.islink(full_path):
                if "-part" in entry:
                    continue
                if os.path.realpath(full_path) == os.path.realpath(dev_node):
                    return dev_node, entry
                    
    return dev_node, None

# --- DISCOVERY ENGINE ---

def discover_drives(bay_map_path='/opt/drive-eraser/config/bay_map.json', running_devices=None):
    try:
        with open(bay_map_path, 'r', encoding='utf-8') as f: bay_map = json.load(f)
    except Exception: return []
    path_to_dev = {}
    by_path_dir = '/dev/disk/by-path/'
    if os.path.exists(by_path_dir):
        for entry in os.listdir(by_path_dir):
            full_path = os.path.join(by_path_dir, entry)
            if os.path.islink(full_path): path_to_dev[entry] = os.path.realpath(full_path)

    results, passphrase = [], None
    try: passphrase = load_policy(get_config_dir()).get("wipe_passphrase")
    except Exception: pass

    os_dev_node, os_by_path = get_os_by_path()

    for bay_id, config in bay_map.items():
        target_path = config.get('by_path')
        target_path_nvme = config.get('by_path_nvme')
        
        bay_info = {
            "bay": bay_id, 
            "label": config.get('label', bay_id), 
            "role": config.get('role', 'wipe'), 
            "locked": config.get('locked', False),
            "configured_by_path": target_path, 
            "resolved_by_path": None,
            "configured_by_path_nvme": target_path_nvme, 
            "resolved_by_path_nvme": None,
            "type": config.get("type", "sas_sata"),  # <-- Ensure this is explicitly set here
            "present": False, 
            "device": None, 
            "serial": None, 
            "model": None, 
            "status": "EMPTY",
            "interface_type": "unknown", 
            "capacity_str": "-", 
            "marker": {"ok": False, "status": "none", "error": None, "details": {}}, 
            "recommendation": {"status": "UNKNOWN", "comment": "-"}, 
            "health_score": 100,
            "capabilities": {"supports_crypto_erase": False, "supports_block_erase": False, "supports_secure_erase": False, "supports_enhanced_secure_erase": False, "supports_overwrite": True}, 
            "supported_methods": ["overwrite"],
            "smart": {}, 
            "diagnostics": {"mapping": {"ok": False, "reason": "not_mapped"}, "commands": {}}
        }
        
        # 1. Primary SATA/SAS path check
        matched_by_path, dev_node = resolve_bay_device(target_path, path_to_dev)
        matched_by_path_nvme = None
        
        # 2. Tri-Mode Fallback: If no SATA/SAS is found, resolve the NVMe motherboard port
        if not dev_node and target_path_nvme:
            matched_by_path_nvme, dev_node = resolve_bay_device(target_path_nvme, path_to_dev)
            if dev_node:
                bay_info["resolved_by_path_nvme"] = matched_by_path_nvme
        else:
            if dev_node:
                bay_info["resolved_by_path"] = matched_by_path

        if dev_node:
            bay_info["diagnostics"]["mapping"] = {"ok": True, "reason": None}

            is_os_drive = False
            if os_dev_node and os.path.realpath(dev_node) == os.path.realpath(os_dev_node):
                is_os_drive = True
            
            resolved_active_path = matched_by_path_nvme if matched_by_path_nvme else matched_by_path
            configured_active_path = target_path_nvme if matched_by_path_nvme else target_path
            
            if os_by_path and (resolved_active_path == os_by_path or configured_active_path == os_by_path or os.path.basename(resolved_active_path or "") == os.path.basename(os_by_path)):
                is_os_drive = True

            if is_os_drive:
                bay_info["role"] = "os"
                bay_info["locked"] = True

            if running_devices and dev_node in running_devices:
                bay_info.update({"present": True, "device": dev_node, "status": "RUNNING", "interface_type": detect_interface_type(resolved_active_path or configured_active_path, dev_node, config.get('type'), None), "capacity_str": "Sanitizing..."})
                results.append(bay_info); continue

            command_diagnostics = {}
            smart = get_smart_data(dev_node, command_diagnostics)
            interface_type = detect_interface_type(resolved_active_path or configured_active_path, dev_node, config.get('type'), smart.get("raw"))
            capabilities = detect_drive_capabilities(interface_type, dev_node, command_diagnostics)
            marker_status = read_marker_status(dev_node, interface_type, passphrase)

            if marker_status.get("status") == "checksum_valid":
                is_pristine = check_write_tolerance(interface_type, smart.get("data_written_raw"), marker_status.get("details", {}).get("data_written_at_wipe"))
                marker_status["is_pristine"] = is_pristine
                marker_status["status"] = "written_since_wipe" if not is_pristine else ("pristine_secure" if marker_status.get("hmac_verified") else "pristine_insecure")

            health_score = calculate_drive_health_score(interface_type, smart, smart.get("raw"))
            recommendation = get_drive_recommendation(interface_type, smart, health_score=health_score)

            bay_info.update({
                "present": True, "device": dev_node, "serial": smart.get("serial"), "model": smart.get("model"), "status": smart.get("status", "UNKNOWN"), "interface_type": interface_type, "capacity_str": smart.get("capacity_str", "-"),
                "capabilities": capabilities, "marker": marker_status, "recommendation": recommendation, "health_score": health_score,
                "supported_methods": [m for m, s in {"crypto": capabilities.get("supports_crypto_erase", False), "block": capabilities.get("supports_block_erase", False), "secure_erase": capabilities.get("supports_secure_erase", False), "enhanced_secure_erase": capabilities.get("supports_enhanced_secure_erase", False), "overwrite": capabilities.get("supports_overwrite", False)}.items() if s],
                "diagnostics": {"mapping": {"ok": True, "reason": None}, "commands": command_diagnostics},
                "smart": {
                    "temperature": smart.get("temperature"), "reallocated_sectors": smart.get("reallocated_sectors"), "pending_sectors": smart.get("pending_sectors"), "wear_level": smart.get("wear_level"), "power_on_hours": smart.get("power_on_hours"), "power_on_days": smart.get("power_on_days"),
                    "interface_errors": smart.get("interface_errors"), "data_read_raw": smart.get("data_read_raw"), "data_read_bytes": smart.get("data_read_bytes"), "data_written_raw": smart.get("data_written_raw"), "data_written_bytes": smart.get("data_written_bytes"),
                    "reallocated_normalized": smart.get("reallocated_normalized"), "reallocated_threshold": smart.get("reallocated_threshold"), "capacity_bytes": smart.get("capacity_bytes"), "raw": smart.get("raw")
                }
            })

            if is_os_drive:
                bay_info["role"] = "os"
                bay_info["locked"] = True
                bay_info["supported_methods"] = []
                bay_info["recommendation"] = {"status": "LOCKED", "comment": "Active Operating System Disk. Sanitization strictly blocked."}
                if not bay_info["capacity_str"].endswith(" [OS]"):
                    bay_info["capacity_str"] = f"{bay_info['capacity_str']} [OS]"

        else:
            bay_info["diagnostics"]["mapping"] = {"ok": False, "reason": "by_path_not_found" if (target_path or target_path_nvme) else "missing_by_path"}
        results.append(bay_info)
    return results
# --- END OF FILE backend/disk_ops.py ---