import os
import re
import subprocess
import logging

from verification import resolve_verify_command_path
from disk_utils import validate_device_path

SATA_SECURITY_PASSWORD = "wipestation"  # Used for hdparm security-erase commands


def get_device_logical_block_size(device):
    """Read logical block size from sysfs. Falls back to 512 if unavailable."""
    try:
        dev_name = os.path.basename(device)
        bs_path = f"/sys/block/{dev_name}/queue/logical_block_size"
        with open(bs_path, "r") as f:
            return int(f.read().strip())
    except Exception:
        return 512


def get_device_capacity_bytes(device):
    """Get real device capacity in bytes via blockdev or sysfs. Returns None if unavailable."""
    try:
        result = subprocess.run(
            ["blockdev", "--getsize64", device],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return int(result.stdout.strip())
    except Exception:
        pass
    try:
        dev_name = os.path.basename(device)
        size_path = f"/sys/block/{dev_name}/size"
        with open(size_path, "r") as f:
            sectors = int(f.read().strip())
            return sectors * 512
    except Exception:
        return None


def calculate_firmware_progress(interface_type, method, device, sprog_val=None, parsed_pct=None):
    """Calculate firmware sanitize progress for NVMe/SATA/SAS interfaces.

    Returns (progress_pct, phase_text) or (None, None) if no progress data available.
    """
    if interface_type == "nvme" and sprog_val is not None:
        pct = min(99.9, (sprog_val / 65535.0) * 100)
        return pct, f"NVMe controller sanitize ({pct:.1f}%)"

    if interface_type == "sas":
        if parsed_pct is not None:
            pct = min(99.9, parsed_pct)
            return pct, f"SAS firmware sanitizing ({pct:.1f}%)"
        prog_val = poll_sas_sanitize_progress(device)
        if prog_val is not None:
            pct = min(99.9, prog_val)
            return pct, f"SAS firmware sanitizing ({pct:.1f}%)"

    if interface_type == "sata" and parsed_pct is not None:
        pct = min(99.9, parsed_pct)
        return pct, f"SATA sanitize active ({pct:.1f}%)"
    prog_val = poll_sata_sanitize_progress(device)
    if prog_val is not None:
        pct = min(99.9, prog_val)
        return pct, f"SATA sanitize active ({pct:.1f}%)"

    return None, None


def get_device_sectors_written(device):
    try:
        dev_name = os.path.basename(device)
        stat_path = f"/sys/block/{dev_name}/stat"
        if not os.path.exists(stat_path):
            return None
        with open(stat_path, "r") as f:
            content = f.read().strip()
        parts = content.split()
        if len(parts) >= 7:
            return int(parts[6])
    except Exception as e:
        logging.getLogger(__name__).debug(f"poll failed for sectors written on {device}: {e}")
    return None


def poll_nvme_sanitize_progress(device):
    try:
        nvme_path = resolve_verify_command_path("nvme")
        if nvme_path:
            result = subprocess.run(["sudo", nvme_path, "sanitize-log", device], capture_output=True, text=True, shell=False)
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if "sprog" in line.lower():
                        match = re.search(r"sprog\)?\s*[:=]\s*(\d+)", line, re.IGNORECASE)
                        if match:
                            return int(match.group(1))
    except Exception as e:
        logging.getLogger(__name__).debug(f"poll failed for NVMe sanitize on {device}: {e}")
    return None


def poll_sas_sanitize_progress(device):
    try:
        sg_req_path = resolve_verify_command_path("sg_requests")
        if sg_req_path:
            result = subprocess.run(["sudo", sg_req_path, "--progress", device], capture_output=True, text=True, shell=False)
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if "progress" in line.lower():
                        match = re.search(r"(\d+\.?\d*)\s*%", line)
                        if match:
                            return float(match.group(1))
    except Exception as e:
        logging.getLogger(__name__).debug(f"poll failed for SAS sanitize on {device}: {e}")
    return None


def poll_sata_sanitize_progress(device):
    try:
        hdparm_path = resolve_verify_command_path("hdparm")
        if hdparm_path:
            result = subprocess.run(["sudo", hdparm_path, "--sanitize-status", device], capture_output=True, text=True, shell=False)
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if "progress" in line.lower() or "percent" in line.lower():
                        match = re.search(r"(\d+\.?\d*)\s*%", line)
                        if match:
                            return float(match.group(1))
    except Exception as e:
        logging.getLogger(__name__).debug(f"poll failed for SATA sanitize on {device}: {e}")
    return None


def prepare_erase_command(device, interface_type, method):
    selected_method = str(method or "").strip().lower()
    iface = str(interface_type or "").strip().lower()

    # Validate interface type is one of the supported types
    supported_interfaces = {"sata", "sas", "nvme"}
    if iface and iface not in supported_interfaces:
        return {"ok": False, "error": f"unsupported_interface:{iface}"}

    if selected_method == "overwrite":
        dd_cmd = resolve_verify_command_path("dd")
        if not dd_cmd:
            return {"ok": False, "error": "dd_not_available"}
        return {"ok": True, "command": [dd_cmd, "if=/dev/zero", f"of={device}", "bs=16M", "status=none", "conv=fdatasync"]}

    if selected_method in {"secure_erase", "enhanced_secure_erase"}:
        hdparm_cmd = resolve_verify_command_path("hdparm")
        if not hdparm_cmd:
            return {"ok": False, "error": "hdparm_not_available"}
        user_password = SATA_SECURITY_PASSWORD
        erase_flag = "--security-erase-enhanced" if selected_method == "enhanced_secure_erase" else "--security-erase"
        erase_cmd = [hdparm_cmd, "--user-master", "u", erase_flag, user_password, device]
        return {"ok": True, "command": erase_cmd}

    if selected_method in {"block", "crypto"}:
        if iface == "nvme":
            nvme_cmd = resolve_verify_command_path("nvme")
            if not nvme_cmd:
                return {"ok": False, "error": "nvme_not_available"}
            # NVMe sanitize must be run on the controller device (/dev/nvmeX), not namespace (/dev/nvmeXnY)
            sanitize_device = device
            if device and re.match(r'^/dev/nvme\d+n\d+\Z', device):
                # Extract controller from namespace (e.g., /dev/nvme0n1 -> /dev/nvme0)
                match = re.match(r'^(/dev/nvme\d+)n\d+\Z', device)
                if match:
                    sanitize_device = match.group(1)
                    # Validate extracted controller path before use (lesson-learned #9)
                    if not validate_device_path(sanitize_device):
                        return {"ok": False, "error": "invalid_extracted_device_path"}
            # --sanact expects decimal value: 4=crypto erase, 2=block erase
            sanact_value = "4" if selected_method == "crypto" else "2"
            return {"ok": True, "command": [nvme_cmd, "sanitize", sanitize_device, "--sanact", sanact_value]}

        if iface == "sata":
            hdparm_cmd = resolve_verify_command_path("hdparm")
            if not hdparm_cmd:
                return {"ok": False, "error": "hdparm_not_available"}
            action = "--sanitize-crypto-scramble" if selected_method == "crypto" else "--sanitize-block-erase"
            return {"ok": True, "command": [hdparm_cmd, "--yes-i-know-what-i-am-doing", action, device]}

        if iface == "sas":
            sg_sanitize_cmd = resolve_verify_command_path("sg_sanitize")
            if not sg_sanitize_cmd:
                return {"ok": False, "error": "sg_sanitize_not_available"}
            return {"ok": True, "command": [sg_sanitize_cmd, "--block", device]}

    return {"ok": False, "error": f"unsupported_method_or_interface:{selected_method}:{iface}"}
