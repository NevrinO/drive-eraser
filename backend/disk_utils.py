# --- START OF FILE backend/disk_utils.py ---
# Command resolution and disk utility functions

import subprocess
import json
import os
import re
import shutil
import hashlib
import hmac
from common import get_config_dir, load_policy

MARKER_SIGNATURE = "DWS_MARKER_V1"
MARKER_BLOCK_SIZE = 4096

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

def load_command_path_overrides():
    config_path = os.path.join(get_config_dir(), "command_paths.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception as e:
            # Log error but continue with empty overrides
            import logging
            logging.getLogger(__name__).warning(f"Failed to load command path overrides from {config_path}: {e}")
    return {}

COMMAND_PATH_OVERRIDES = load_command_path_overrides()

SMARTCTL_CMD = resolve_command_path("smartctl", [COMMAND_PATH_OVERRIDES.get("smartctl"), "/usr/sbin/smartctl", "/usr/bin/smartctl", "/bin/smartctl"], "DRIVE_ERASER_SMARTCTL_PATH")
NVME_CMD = resolve_command_path("nvme", [COMMAND_PATH_OVERRIDES.get("nvme"), "/usr/sbin/nvme", "/usr/bin/nvme", "/bin/nvme"], "DRIVE_ERASER_NVME_PATH")
HDPARM_CMD = resolve_command_path("hdparm", [COMMAND_PATH_OVERRIDES.get("hdparm"), "/usr/sbin/hdparm", "/usr/bin/hdparm", "/bin/hdparm"], "DRIVE_ERASER_HDPARM_PATH")
SG_SANITIZE_CMD = resolve_command_path("sg_sanitize", [COMMAND_PATH_OVERRIDES.get("sg_sanitize"), "/usr/bin/sg_sanitize", "/usr/sbin/sg_sanitize", "/bin/sg_sanitize"], "DRIVE_ERASER_SG_SANITIZE_PATH")
DD_CMD = resolve_command_path("dd", [COMMAND_PATH_OVERRIDES.get("dd"), "/usr/bin/dd", "/bin/dd"], "DRIVE_ERASER_DD_PATH")

def format_capacity_bytes(num_bytes):
    if not num_bytes: return "-"
    tb = num_bytes / (10**12)
    if tb >= 1.0: return f"{round(tb)} TB" if abs(tb - round(tb)) < 0.05 else f"{tb:.2f} TB"
    gb = num_bytes / (10**9)
    if gb >= 1.0: return f"{round(gb)} GB" if abs(gb - round(gb)) < 0.5 else f"{gb:.1f} GB"
    return f"{round(num_bytes / (10**6))} MB"

def check_write_tolerance(interface_type, current, stored):
    if current is None or stored is None: return False
    try:
        diff = int(current) - int(stored)
        if diff < 0: return False
        iface = str(interface_type or "unknown").lower()
        return (diff <= 4) if "nvme" in iface else (diff <= 4096)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to check write tolerance: {e}")
        return False

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
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to parse marker JSON: {e}")
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
# --- END OF FILE backend/disk_utils.py ---
