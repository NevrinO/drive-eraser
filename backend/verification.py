import os
import re
import json
import subprocess
import hashlib
import hmac
import time
import random
from datetime import datetime, timezone
from common import load_policy

from disk_ops import (
    get_smart_data,
    read_marker_status,
    check_write_tolerance,
    MARKER_SIGNATURE,
    MARKER_BLOCK_SIZE
)

def resolve_verify_command_path(command_name, env_var_name, override_key, fallbacks):
    env_value = os.getenv(env_var_name)
    if env_value and os.path.exists(env_value) and os.access(env_value, os.X_OK):
        return env_value
    
    # Sibling import resolution directly from disk_ops
    from disk_ops import COMMAND_PATH_OVERRIDES
    configured = COMMAND_PATH_OVERRIDES.get(override_key)
    if configured and os.path.exists(configured) and os.access(configured, os.X_OK):
        return configured
    for candidate in fallbacks:
        if candidate and os.path.exists(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None

def run_verification_command(command, text=True):
    if not command or not command[0]:
        return {"ok": False, "stdout": "", "stderr": "", "return_code": None, "output_bytes": b""}
    result = subprocess.run(["sudo"] + command, capture_output=True, text=text)
    stdout = result.stdout if isinstance(result.stdout, str) else ""
    stderr = result.stderr if isinstance(result.stderr, str) else ""
    output_bytes = result.stdout if isinstance(result.stdout, bytes) else b""
    return {"ok": result.returncode == 0, "stdout": stdout.strip(), "stderr": stderr.strip(), "return_code": result.returncode, "output_bytes": output_bytes}

def verify_overwrite(device):
    dd_cmd = resolve_verify_command_path("dd", "DRIVE_ERASER_DD_PATH", "dd", ["/usr/bin/dd", "/bin/dd"])
    if not dd_cmd:
        return {"ok": False, "status": "verification_error", "error": "dd_not_available_for_verification", "details": {"method": "overwrite"}}

    sample_blocks = [0, 1024, 4096]
    checked_samples = []
    for block_offset in sample_blocks:
        command = [dd_cmd, f"if={device}", "bs=4096", f"skip={block_offset}", "count=1", "iflag=direct", "status=none"]
        result = run_verification_command(command, text=False)
        if not result.get("ok"):
            return {
                "ok": False,
                "status": "verification_error",
                "error": "overwrite_sample_read_failed",
                "details": {
                    "method": "overwrite",
                    "block_offset": block_offset,
                    "stderr": result.get("stderr", ""),
                    "return_code": result.get("return_code"),
                },
            }
        sample_data = result.get("output_bytes") or b""
        if not sample_data:
            return {"ok": False, "status": "verification_error", "error": "overwrite_sample_empty", "details": {"method": "overwrite", "block_offset": block_offset}}
        if any(byte != 0 for byte in sample_data):
            return {
                "ok": False,
                "status": "verification_failed",
                "error": "overwrite_nonzero_sample",
                "details": {"method": "overwrite", "block_offset": block_offset, "sample_size": len(sample_data)},
            }
        checked_samples.append({"block_offset": block_offset, "sample_size": len(sample_data)})

    return {"ok": True, "status": "verified", "error": None, "details": {"mode": "sampled_zero_check", "method": "overwrite", "samples": checked_samples}}

def parse_numeric_field(output, field_name):
    match = re.search(rf"{field_name}[^\r\n:]*:\s*(0x[0-9a-fA-F]+|\d+)", output, re.IGNORECASE)
    if not match:
        return None
    raw_value = match.group(1)
    try:
        return int(raw_value, 16) if raw_value.lower().startswith("0x") else int(raw_value)
    except ValueError:
        return None

def extract_command_output(result):
    stdout = str(result.get("stdout") or "").strip()
    stderr = str(result.get("stderr") or "").strip()
    return stdout if stdout else stderr

def extract_sata_security_section(output):
    # Match the "Security:" header and extract all subsequent indented lines
    match = re.search(r"^[ \t]*Security:[ \t]*\n((?:[ \t]+.*\n?)+)", output, re.IGNORECASE | re.MULTILINE)
    if match:
        return match.group(1).lower()
    # Fallback to the blank-line or end-of-string bounded check if indentation parsing fails
    fallback_match = re.search(r"security:\s*(.*?)(?:\n\s*\n|$)", output, re.IGNORECASE | re.DOTALL)
    return (fallback_match.group(1) if fallback_match else "").lower()

def verify_nvme_sanitize(device, method):
    nvme_cmd = resolve_verify_command_path("nvme", "DRIVE_ERASER_NVME_PATH", "nvme", ["/usr/sbin/nvme", "/usr/bin/nvme", "/bin/nvme"])
    if not nvme_cmd:
        return {"ok": False, "status": "verification_error", "error": "nvme_not_available_for_verification", "details": {"method": method}}

    result = run_verification_command([nvme_cmd, "sanitize-log", device], text=True)
    output = extract_command_output(result)
    
    if not output.strip():
        if not result.get("ok"):
            return {
                "ok": False,
                "status": "verification_error",
                "error": "nvme_sanitize_log_failed",
                "details": {"method": method, "stderr": result.get("stderr", ""), "return_code": result.get("return_code")},
            }
        return {"ok": False, "status": "verification_error", "error": "nvme_sanitize_log_empty", "details": {"method": method}}

    lowered = output.lower()
    sprog = parse_numeric_field(output, "sprog")
    sstat = parse_numeric_field(output, "sstat")
    sstat_failed = bool(sstat is not None and ((sstat & 0x7) == 0x2 or (sstat & 0x7) == 0x3))

    if "failed" in lowered or sstat_failed:
        return {"ok": False, "status": "verification_failed", "error": "nvme_sanitize_failed_state", "details": {"method": method, "sprog": sprog, "sstat": hex(sstat) if sstat is not None else None}}

    if "in progress" in lowered or (sprog is not None and sprog < 65535):
        return {"ok": False, "status": "verification_failed", "error": "nvme_sanitize_still_in_progress", "details": {"method": method, "sprog": sprog, "sstat": hex(sstat) if sstat is not None else None}}

    return {
        "ok": True,
        "status": "verified",
        "error": None,
        "details": {"mode": "nvme_sanitize_log", "method": method, "sprog": sprog, "sstat": hex(sstat) if sstat is not None else None},
    }

def verify_sata_secure_erase(device, method):
    hdparm_cmd = resolve_verify_command_path("hdparm", "DRIVE_ERASER_HDPARM_PATH", "hdparm", ["/usr/sbin/hdparm", "/usr/bin/hdparm", "/bin/hdparm"])
    if not hdparm_cmd:
        return {"ok": False, "status": "verification_error", "error": "hdparm_not_available_for_verification", "details": {"method": method}}

    result = run_verification_command([hdparm_cmd, "-I", device], text=True)
    output = extract_command_output(result)
    lowered = output.lower()
    security_section = extract_sata_security_section(output)
    
    if not lowered.strip() or not security_section:
        if not result.get("ok"):
            return {
                "ok": False,
                "status": "verification_error",
                "error": "hdparm_identify_failed",
                "details": {"method": method, "stderr": result.get("stderr", ""), "return_code": result.get("return_code")},
            }
        return {"ok": False, "status": "verification_error", "error": "sata_security_section_missing", "details": {"method": method}}

    # Parse individual flags precisely using word boundaries to avoid false substring matches
    sec_lines = [line.strip() for line in security_section.splitlines()]
    is_enabled = any(re.search(r"\benabled\b", line) and not re.search(r"\bnot\b", line) for line in sec_lines)
    is_locked = any(re.search(r"\blocked\b", line) and not re.search(r"\bnot\b", line) for line in sec_lines)
    is_frozen = any(re.search(r"\bfrozen\b", line) and not re.search(r"\bnot\b", line) for line in sec_lines)

    if is_enabled:
        return {
            "ok": False,
            "status": "verification_failed",
            "error": "sata_security_still_enabled",
            "details": {"method": method, "locked": is_locked, "frozen": is_frozen},
        }

    return {
        "ok": True,
        "status": "verified",
        "error": None,
        "details": {
            "mode": "post_hdparm_identify",
            "method": method,
            "locked": is_locked,
            "frozen": is_frozen,
        },
    }

def verify_sata_sanitize(device, method):
    hdparm_cmd = resolve_verify_command_path("hdparm", "DRIVE_ERASER_HDPARM_PATH", "hdparm", ["/usr/sbin/hdparm", "/usr/bin/hdparm", "/bin/hdparm"])
    if not hdparm_cmd:
        return {"ok": False, "status": "verification_error", "error": "hdparm_not_available_for_verification", "details": {"method": method}}

    # Wait out hardware resets and retries on SATA links if EIO errors are returned
    output = ""
    result = None
    for attempt in range(5):
        result = run_verification_command([hdparm_cmd, "--sanitize-status", device], text=True)
        output = extract_command_output(result)
        lowered = output.lower()
        if "bad/missing sense data" in lowered or "input/output error" in lowered or not lowered.strip():
            time.sleep(2)
            continue
        break

    lowered = output.lower()

    if not lowered.strip():
        if result and not result.get("ok"):
            return {
                "ok": False,
                "status": "verification_error",
                "error": "hdparm_sanitize_status_failed",
                "details": {"method": method, "stderr": result.get("stderr", ""), "return_code": result.get("return_code")},
            }
        return {"ok": False, "status": "verification_error", "error": "sata_sanitize_status_empty", "details": {"method": method}}

    # Parse lines strictly based on the "State:" line to prevent false-positives from command failures
    has_success = False
    has_active = False
    has_failed = False

    for line in lowered.splitlines():
        if "state:" in line:
            if any(ind in line for ind in ["idle", "completed", "succeeded", "sd0", "sd4"]):
                has_success = True
            if any(ind in line for ind in ["in process", "in progress", "sd2"]):
                has_active = True
            if any(ind in line for ind in ["failed", "unsuccessful", "sd3"]):
                has_failed = True

    if has_failed:
        return {"ok": False, "status": "verification_failed", "error": "sata_sanitize_failed_state", "details": {"method": method, "output": output}}

    if has_active:
        return {"ok": False, "status": "verification_failed", "error": "sata_sanitize_still_in_progress", "details": {"method": method, "output": output}}

    if not has_success:
        # Fallback for unrecognized status that isn't actively running or failed
        return {"ok": False, "status": "verification_failed", "error": "sata_sanitize_status_unrecognized", "details": {"method": method, "output": output}}

    return {"ok": True, "status": "verified", "error": None, "details": {"mode": "sata_sanitize_status", "method": method, "output": output}}

def verify_sas_block(device, method):
    sg_sanitize_cmd = resolve_verify_command_path("sg_sanitize", "DRIVE_ERASER_SG_SANITIZE_PATH", "sg_sanitize", ["/usr/bin/sg_sanitize", "/usr/sbin/sg_sanitize", "/bin/sg_sanitize"])
    if not sg_sanitize_cmd:
        return {"ok": False, "status": "verification_error", "error": "sg_sanitize_not_available_for_verification", "details": {"method": method}}

    result = run_verification_command([sg_sanitize_cmd, "--status", device], text=True)
    output = extract_command_output(result)
    
    if not output.strip():
        if not result.get("ok"):
            return {
                "ok": False,
                "status": "verification_error",
                "error": "sas_sanitize_status_failed",
                "details": {"method": method, "stderr": result.get("stderr", ""), "return_code": result.get("return_code")},
            }
        return {"ok": False, "status": "verification_error", "error": "sas_sanitize_status_empty", "details": {"method": method}}

    lowered = output.lower()
    in_progress_markers = ["in progress", "background operation in progress", "sanitize in progress", "progress indication"]
    failed_markers = ["failed", "failure", "check condition", "medium error", "aborted"]
    complete_markers = ["completed", "success", "no sanitize operation in progress", "idle", "not in progress"]

    if any(marker in lowered for marker in failed_markers):
        return {"ok": False, "status": "verification_failed", "error": "sas_sanitize_failed_state", "details": {"method": method, "output": output}}

    has_complete = any(marker in lowered for marker in complete_markers)
    if not has_complete and any(marker in lowered for marker in in_progress_markers):
        return {"ok": False, "status": "verification_failed", "error": "sas_sanitize_still_in_progress", "details": {"method": method, "output": output}}

    if not has_complete:
        return {"ok": False, "status": "verification_error", "error": "sas_sanitize_status_unrecognized", "details": {"method": method, "output": output}}

    return {"ok": True, "status": "verified", "error": None, "details": {"mode": "sas_sanitize_status", "method": method, "output": output}}

def verify_sampled_zero_check(device, sample_ratio=0.10, chunk_size_bytes=10*1024*1024, max_read_bytes=10*1024*1024*1024):
    """
    Performs a secondary zero-validation check by reading spatially distributed
    samples across the drive LBA range. Combines random sampling with sequential chunk
    reads to avoid disk head seek bottlenecks on HDDs.
    """
    try:
        with open(device, "rb") as f:
            f.seek(0, 2)
            capacity = f.tell()
    except Exception as e:
        return {"ok": False, "error": "secondary_capacity_check_failed", "details": str(e)}

    # Calculate total bytes to verify based on 10% sample ratio
    target_read_bytes = int(capacity * sample_ratio)
    if max_read_bytes and target_read_bytes > max_read_bytes:
        target_read_bytes = max_read_bytes

    # Determine chunk count
    num_chunks = max(1, target_read_bytes // chunk_size_bytes)
    if capacity < chunk_size_bytes:
        chunk_size_bytes = capacity
        num_chunks = 1

    # Generate spaced random offsets spanning the entire LBA
    interval_size = capacity // num_chunks
    offsets = []
    for i in range(num_chunks):
        start = i * interval_size
        end = max(start, (i + 1) * interval_size - chunk_size_bytes)
        if end > start:
            offsets.append(random.randint(start, end))
        else:
            offsets.append(start)

    total_verified_bytes = 0
    non_zero_found = False
    first_non_zero_offset = None

    try:
        with open(device, "rb") as f:
            for offset in offsets:
                f.seek(offset)
                data = f.read(chunk_size_bytes)
                total_verified_bytes += len(data)
                
                # Highly optimized C-level block evaluation in Python
                if data != b'\x00' * len(data):
                    non_zero_found = True
                    first_non_zero_offset = offset
                    break
    except Exception as e:
        return {"ok": False, "error": "secondary_sampled_read_failed", "details": str(e)}

    if non_zero_found:
        return {
            "ok": False,
            "status": "verification_failed",
            "error": "secondary_zero_check_failed_nonzero_data_detected",
            "details": {
                "offset": first_non_zero_offset,
                "total_verified_bytes": total_verified_bytes,
                "sample_ratio": sample_ratio
            }
        }

    return {
        "ok": True,
        "status": "verified",
        "details": {
            "total_verified_bytes": total_verified_bytes,
            "chunks_read": num_chunks,
            "chunk_size_bytes": chunk_size_bytes,
            "sample_ratio": sample_ratio
        }
    }

def write_marker_and_verify(job):
    dd_cmd = resolve_verify_command_path("dd", "DRIVE_ERASER_DD_PATH", "dd", ["/usr/bin/dd", "/bin/dd"])
    if not dd_cmd:
        return {"ok": False, "status": "marker_error", "error": "dd_not_available_for_marker_write", "details": {}}

    device = (job.get("request") or {}).get("device")
    interface_type = (job.get("request") or {}).get("interface_type")
    if not device:
        return {"ok": False, "status": "marker_error", "error": "marker_missing_device", "details": {}}

    smart_metrics = get_smart_data(device)
    raw_writes = smart_metrics.get("data_written_raw")
    job["request"]["data_written_at_wipe"] = raw_writes

    payload = build_marker_payload(job)
    if len(payload) > (MARKER_BLOCK_SIZE - 1):
        return {"ok": False, "status": "marker_error", "error": "marker_payload_too_large", "details": {"payload_bytes": len(payload)}}

    block = payload + b"\n" + b"\x00" * (MARKER_BLOCK_SIZE - len(payload) - 1)
    command = [dd_cmd, f"of={device}", f"bs={MARKER_BLOCK_SIZE}", "count=1", "conv=fsync", "oflag=direct", "status=none"]
    result = subprocess.run(["sudo"] + command, input=block, capture_output=True)
    if result.returncode != 0:
        return {
            "ok": False,
            "status": "marker_error",
            "error": "marker_write_failed",
            "details": {
                "return_code": result.returncode,
                "stderr": (result.stderr or b"").decode("utf-8", errors="replace").strip(),
            },
        }

    passphrase = None
    try:
        passphrase = load_policy().get("wipe_passphrase")
    except Exception:
        passphrase = None

    readback = read_marker_status(device, interface_type, passphrase)
    if not readback.get("ok"):
        return readback

    if readback.get("status") == "checksum_valid":
        stored_writes = readback.get("details", {}).get("data_written_at_wipe")
        current_writes = get_smart_data(device).get("data_written_raw")
        is_pristine = check_write_tolerance(interface_type, current_writes, stored_writes)
        readback["is_pristine"] = is_pristine

        if not is_pristine:
            readback["status"] = "written_since_wipe"
        else:
            readback["status"] = "pristine_secure" if readback.get("hmac_verified") else "pristine_insecure"

    if readback.get("status") not in {"pristine_secure", "pristine_insecure"}:
        return {"ok": False, "status": "marker_error", "error": f"marker_verification_failed:{readback.get('status')}", "details": readback.get("details") or {}}

    return {
        "ok": True,
        "status": "marked",
        "error": None,
        "details": {
            "signature": MARKER_SIGNATURE,
            "block_size": MARKER_BLOCK_SIZE,
            "readback": readback.get("details") or {},
        },
    }

def build_marker_payload(job):
    request_data = job.get("request") or {}
    payload = {
        "signature": MARKER_SIGNATURE,
        "version": 1,
        "job_id": job.get("friendly_id") or job.get("id"),
        "finished_at": job.get("finished_at") or datetime.now(timezone.utc).isoformat(),
        "ticket_number": request_data.get("ticket_number") or None,
        "serial": request_data.get("serial"),
        "method": request_data.get("method"),
        "data_written_at_wipe": request_data.get("data_written_at_wipe"),
    }

    serialized_fields = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload["checksum"] = hashlib.sha256(serialized_fields).hexdigest()

    passphrase = None
    try:
        passphrase = load_policy().get("wipe_passphrase")
    except Exception:
        passphrase = None

    if passphrase:
        serialized_for_hmac = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        derived_key = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), b"DWS_SALT_v1", 10000)
        payload["hmac"] = hmac.new(derived_key, serialized_for_hmac, hashlib.sha256).hexdigest()

    return json.dumps(payload, separators=(",", ":")).encode("utf-8")

def verification_for_method(device, interface_type, method, execution):
    selected_method = str(method or "").strip().lower()
    iface = str(interface_type or "").strip().lower()

    primary_result = None

    if selected_method == "overwrite":
        if not execution.get("ok"):
            return {"ok": False, "status": "skipped", "error": "erase_failed", "details": {"method": selected_method, "interface_type": iface, "exit_code": execution.get("exit_code")}}
        primary_result = verify_overwrite(device)
    elif selected_method in {"crypto", "block"} and iface == "nvme":
        primary_result = verify_nvme_sanitize(device, selected_method)
    elif selected_method in {"crypto", "block"} and iface == "sata":
        primary_result = verify_sata_sanitize(device, selected_method)
    elif selected_method in {"secure_erase", "enhanced_secure_erase"} and iface == "sata":
        primary_result = verify_sata_secure_erase(device, selected_method)
    elif selected_method == "block" and iface == "sas":
        primary_result = verify_sas_block(device, selected_method)
    else:
        return {"ok": False, "status": "unsupported_method", "error": f"verification_not_defined:{selected_method}:{iface}", "details": {"method": selected_method, "interface_type": iface}}

    # Apply secondary spatial 10% zero-verification on success of any zero-writing sanitization routine
    if primary_result and primary_result.get("ok"):
        if selected_method in {"overwrite", "block", "secure_erase", "enhanced_secure_erase"}:
            secondary_result = verify_sampled_zero_check(device, sample_ratio=0.10)
            if not secondary_result.get("ok"):
                return {
                    "ok": False,
                    "status": "verification_failed",
                    "error": secondary_result.get("error") or "secondary_verification_failed",
                    "details": {
                        "primary_details": primary_result.get("details"),
                        "secondary_details": secondary_result.get("details")
                    }
                }
            primary_result["details"]["secondary_validation"] = secondary_result.get("details")
            primary_result["details"]["secondary_status"] = "PASSED"
        else:
            primary_result["details"]["secondary_status"] = "SKIPPED_FOR_CRYPTO"

    return primary_result