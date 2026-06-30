# SMART utility functions — interface detection, device path validation, SSD classification
# Leaf module: no dependencies on other smart_* modules

import subprocess
import json
import os
import re
import logging

from disk_utils import get_command_path, validate_device_path as _validate_device_path_base

logger = logging.getLogger(__name__)


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
        try:
            with open(sys_vendor_path, "r") as f:
                vendor = f.read().strip()
            if "ATA" in vendor:
                return "sata"
            else:
                sys_device_path = f"/sys/block/{dev_name}/device"
                real_sys_path = os.path.realpath(sys_device_path)
                if "sas" in real_sys_path.lower():
                    return "sas"
        except (FileNotFoundError, OSError):
            pass

    if configured_type and configured_type in ("sas", "sata", "nvme"):
        return configured_type
    return "sata" if dev.startswith("/dev/sd") else "unknown"


def validate_device_path(device):
    r"""Validate device path against strict whitelist (lesson #9, #13, #16).

    Accepts both /dev/sda and bare sda names. Delegates common security
    checks (path traversal, newlines) to disk_utils.validate_device_path,
    then applies additional device-type restrictions (only sd* and nvme*).

    Args:
        device: Device path string (e.g., "/dev/sda", "sda")

    Returns:
        True if valid, False otherwise
    """
    if not device or not isinstance(device, str):
        return False

    # Normalize bare device names to full /dev/ paths for base validation
    full_path = device if device.startswith("/dev/") else f"/dev/{device}"
    if not _validate_device_path_base(full_path):
        return False

    # Extract device name for restrictive pattern matching
    device_name = device.lstrip("/").replace("dev/", "", 1) if device.startswith("/") else device

    # Lesson #91: Use specific patterns matching actual system naming conventions
    sata_pattern = re.compile(r'^sd[a-z]+[0-9]*\Z')
    nvme_pattern = re.compile(r'^nvme[0-9]+(n[0-9]+)?(p[0-9]+)?\Z')

    return bool(sata_pattern.match(device_name) or nvme_pattern.match(device_name))


def get_raw_smart_diagnostics(device):
    if not validate_device_path(device):
        return "Invalid device path\n"
    smartctl_cmd = get_command_path("smartctl")
    if not smartctl_cmd or not device:
        return "SMARTCTL command not resolved or invalid device target.\n"
    try:
        # Uses subprocess.run directly instead of run_command because:
        # run_command only returns stdout; this function needs stdout, stderr, and exit code
        # in a combined diagnostic string for troubleshooting
        result = subprocess.run(["sudo", smartctl_cmd, "-a", device], capture_output=True, text=True, timeout=15, shell=False)
        output = result.stdout or ""
        stderr = result.stderr or ""
        return f"\n=== RAW SMARTCTL DIAGNOSTICS FOR {device} ===\nExit Code: {result.returncode}\nSTDOUT:\n{output}\nSTDERR:\n{stderr}\n"
    except subprocess.TimeoutExpired:
        return f"\n=== RAW SMARTCTL DIAGNOSTICS FOR {device} ===\nError: Command timed out after 15 seconds.\n"
    except Exception as e:
        return f"\n=== RAW SMARTCTL DIAGNOSTICS FOR {device} ===\nException raised: {str(e)}\n"
