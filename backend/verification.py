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

from disk_utils import (
    read_marker_status,
    check_write_tolerance,
    MARKER_SIGNATURE,
    MARKER_BLOCK_SIZE,
    COMMAND_PATH_OVERRIDES
)
from smart_parsing import get_smart_data

def resolve_verify_command_path(command_name, env_var_name, override_key, fallbacks):
    env_value = os.getenv(env_var_name)
    if env_value and os.path.exists(env_value) and os.access(env_value, os.X_OK):
        return env_value
    
    # Sibling import resolution directly from disk_utils
    from disk_utils import COMMAND_PATH_OVERRIDES
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

def parse_sata_erase_time_estimate(output):
    """
    Parse the erase time estimate from hdparm -I output.
    Returns estimated time in seconds, or None if not found.
    Expected format: "6 min for SECURITY ERASE UNIT", "30 min", "2h", etc.
    """
    # Extract security section first to avoid matching unrelated time fields
    security_section = extract_sata_security_section(output)
    if not security_section:
        return None

    # Look for time patterns in the security section
    # Try multiple patterns to handle different hdparm output formats
    # Pattern 1: "X min" or "X minute" or "X m"
    time_match = re.search(r"(\d+)\s*(min|minute|m|h|hour)", security_section)
    if not time_match:
        # Pattern 2: "Xmin" without space
        time_match = re.search(r"(\d+)(min|minute|m|h|hour)", security_section)
    if not time_match:
        return None

    value = int(time_match.group(1))
    unit = time_match.group(2)

    if unit in {"h", "hour"}:
        return value * 60
    elif unit in {"min", "minute", "m"}:
        return value
    return None

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
    
    if not lowered.strip():
        if not result.get("ok"):
            return {
                "ok": False,
                "status": "verification_error",
                "error": "hdparm_identify_failed",
                "details": {"method": method, "stderr": result.get("stderr", ""), "return_code": result.get("return_code")},
            }
        return {"ok": False, "status": "verification_error", "error": "hdparm_output_empty", "details": {"method": method}}

    # If security section is missing, verify hdparm succeeded and check for other expected sections
    if not security_section:
        if not result.get("ok"):
            return {
                "ok": False,
                "status": "verification_error",
                "error": "hdparm_identify_failed",
                "details": {"method": method, "stderr": result.get("stderr", ""), "return_code": result.get("return_code")},
            }
        
        # Check if output contains other expected sections to distinguish parsing failure from security disabled
        has_config_section = bool(re.search(r"^[ \t]*Configuration:", output, re.IGNORECASE | re.MULTILINE))
        has_geometry_section = bool(re.search(r"^[ \t]*Geometry:", output, re.IGNORECASE | re.MULTILINE))
        
        if not has_config_section and not has_geometry_section:
            return {
                "ok": False,
                "status": "verification_error",
                "error": "hdparm_parsing_failed",
                "details": {"method": method, "note": "expected_sections_missing", "output": output[:500]},
            }
        
        # Security section absent with other sections present - treat as security disabled
        # Parse locked/frozen from full output if possible
        is_locked = bool(re.search(r"\blocked\b", lowered) and not re.search(r"\bnot\s+locked\b", lowered))
        is_frozen = bool(re.search(r"\bfrozen\b", lowered) and not re.search(r"\bnot\s+frozen\b", lowered))
        
        return {
            "ok": True,
            "status": "verified",
            "error": None,
            "details": {
                "mode": "post_hdparm_identify",
                "method": method,
                "locked": is_locked,
                "frozen": is_frozen,
                "note": "security_section_absent",
            },
        }

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

def verify_sampled_zero_check(device, sample_ratio=0.10, chunk_size_bytes=32*1024*1024, max_read_bytes=10*1024*1024*1024):
    """
    Performs a secondary zero-validation check by reading the first 32MB and
    spatially distributed samples across the drive LBA range. Combines random
    sampling with sequential chunk reads to avoid disk head seek bottlenecks on HDDs.
    """
    dd_cmd = resolve_verify_command_path("dd", "DRIVE_ERASER_DD_PATH", "dd", ["/usr/bin/dd", "/bin/dd"])
    if not dd_cmd:
        return {"ok": False, "error": "dd_not_available_for_zero_check", "details": "dd command not found"}

    try:
        # Get capacity using blockdev
        blockdev_cmd = ["sudo", "blockdev", "--getsize64", device]
        result = subprocess.run(blockdev_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return {"ok": False, "error": "secondary_capacity_check_failed", "details": f"blockdev failed (exit code {result.returncode}): stderr={result.stderr}, stdout={result.stdout}"}
        capacity = int(result.stdout.strip())
    except Exception as e:
        return {"ok": False, "error": "secondary_capacity_check_failed", "details": f"exception: {str(e)}"}

    # Always check first 32MB (holds VBR/partition table)
    offsets = [0]

    # Calculate total bytes to verify based on sample ratio
    target_read_bytes = int(capacity * sample_ratio)
    if max_read_bytes and target_read_bytes > max_read_bytes:
        target_read_bytes = max_read_bytes

    # Determine chunk count for spaced sampling
    num_chunks = max(1, target_read_bytes // chunk_size_bytes)
    if capacity < chunk_size_bytes:
        chunk_size_bytes = capacity
        num_chunks = 1
    # Guard against division by zero for very small drives
    if num_chunks == 0:
        num_chunks = 1

    # Generate spaced random offsets spanning the entire LBA
    interval_size = capacity // num_chunks
    for i in range(num_chunks):
        start = i * interval_size
        end = max(start, (i + 1) * interval_size - chunk_size_bytes)
        if end > start:
            offset = random.randint(start, end)
            if offset != 0:  # Don't duplicate the first 32MB check
                offsets.append(offset)
        else:
            if start != 0:
                offsets.append(start)

    total_verified_bytes = 0
    non_zero_found = False
    first_non_zero_offset = None

    try:
        for offset in offsets:
            # Use 32MB chunks for all reads, with dynamic bs for partial chunks
            skip_blocks = offset // chunk_size_bytes
            read_size = min(chunk_size_bytes, capacity - offset)
            actual_bs = read_size if read_size < chunk_size_bytes else chunk_size_bytes
            dd_cmd_str = ["sudo", dd_cmd, f"if={device}", f"bs={actual_bs}", f"skip={skip_blocks}", "count=1", "status=none"]
            result = subprocess.run(dd_cmd_str, capture_output=True)
            if result.returncode != 0:
                return {"ok": False, "error": "secondary_sampled_read_failed", "details": f"dd read failed at offset {offset}: {result.stderr.decode('utf-8', errors='replace')}"}
            data = result.stdout
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
                "sample_ratio": sample_ratio,
                "first_32mb_checked": True
            }
        }

    return {
        "ok": True,
        "status": "verified",
        "details": {
            "total_verified_bytes": total_verified_bytes,
            "chunks_read": len(offsets),
            "chunk_size_bytes": chunk_size_bytes,
            "sample_ratio": sample_ratio,
            "first_32mb_checked": True
        }
    }

def capture_before_state(device, sample_ratio=0.01, chunk_size_bytes=32*1024*1024, max_read_bytes=512*1024*1024):
    """
    Captures hashes of the first 32MB and spaced-out blocks before crypto erase.
    Returns a structure with offsets and hashes for post-wipe comparison.
    """
    dd_cmd = resolve_verify_command_path("dd", "DRIVE_ERASER_DD_PATH", "dd", ["/usr/bin/dd", "/bin/dd"])
    if not dd_cmd:
        return {"ok": False, "error": "dd_not_available_for_capture", "details": "dd command not found"}

    try:
        # Get capacity using blockdev
        blockdev_cmd = ["sudo", "blockdev", "--getsize64", device]
        result = subprocess.run(blockdev_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return {"ok": False, "error": "capture_capacity_check_failed", "details": f"blockdev failed (exit code {result.returncode}): stderr={result.stderr}, stdout={result.stdout}"}
        capacity = int(result.stdout.strip())
    except Exception as e:
        return {"ok": False, "error": "capture_capacity_check_failed", "details": f"exception: {str(e)}"}

    # Always capture first 32MB (holds VBR/partition table)
    offsets = [0]

    # Calculate total bytes to capture based on sample ratio
    target_read_bytes = int(capacity * sample_ratio)
    if max_read_bytes and target_read_bytes > max_read_bytes:
        target_read_bytes = max_read_bytes

    # Determine chunk count for spaced sampling
    num_chunks = max(1, target_read_bytes // chunk_size_bytes)
    if capacity < chunk_size_bytes:
        chunk_size_bytes = capacity
        num_chunks = 1
    # Guard against division by zero for very small drives
    if num_chunks == 0:
        num_chunks = 1

    # Generate spaced random offsets spanning the entire LBA
    interval_size = capacity // num_chunks
    for i in range(num_chunks):
        start = i * interval_size
        end = max(start, (i + 1) * interval_size - chunk_size_bytes)
        if end > start:
            offset = random.randint(start, end)
            if offset != 0:  # Don't duplicate the first 32MB check
                offsets.append(offset)
        else:
            if start != 0:
                offsets.append(start)

    hashes = []
    total_captured_bytes = 0

    try:
        for offset in offsets:
            # Use 32MB chunks for all reads, with dynamic bs for partial chunks
            skip_blocks = offset // chunk_size_bytes
            read_size = min(chunk_size_bytes, capacity - offset)
            actual_bs = read_size if read_size < chunk_size_bytes else chunk_size_bytes
            dd_cmd_str = ["sudo", dd_cmd, f"if={device}", f"bs={actual_bs}", f"skip={skip_blocks}", "count=1", "status=none"]
            result = subprocess.run(dd_cmd_str, capture_output=True)
            if result.returncode != 0:
                return {"ok": False, "error": "capture_read_failed", "details": f"dd read failed at offset {offset}: {result.stderr.decode('utf-8', errors='replace')}"}
            data = result.stdout
            total_captured_bytes += len(data)
            hashes.append(hashlib.sha256(data).hexdigest())
    except Exception as e:
        return {"ok": False, "error": "capture_read_failed", "details": str(e)}

    return {
        "ok": True,
        "details": {
            "offsets": offsets,
            "hashes": hashes,
            "total_captured_bytes": total_captured_bytes,
            "chunk_size_bytes": chunk_size_bytes,
            "sample_ratio": sample_ratio,
            "first_32mb_captured": True
        }
    }

def detect_filesystem_signatures(data):
    """Check the first 4KB of drive data for recognizable filesystem/boot sector signatures.
    Returns a list of detected signature names.
    """
    signatures = []
    if len(data) >= 11 and data[0:3] == b'\xEB\x52\x90' and data[3:7] == b'NTFS':
        signatures.append("NTFS")
    if len(data) >= 90 and data[0:3] in {b'\xEB\x3C\x90', b'\xEB\x58\x90', b'\xEB\x76\x90'}:
        if b'FAT' in data[54:90]:
            signatures.append("FAT")
    if len(data) >= 8 and data[3:8] == b'EXFAT':
        signatures.append("exFAT")
    if len(data) >= 520 and data[512:520] == b'EFI PART':
        signatures.append("GPT")
    if len(data) >= 1082 and data[1080:1082] == b'\x53\xEF':
        signatures.append("EXT")
    return signatures

def verify_crypto_probe(device, mode="conservative_probe", sample_ratio=0.01, chunk_size_bytes=32*1024*1024, max_read_bytes=512*1024*1024, before_state=None):
    """
    Verifies crypto erase by comparing before/after hashes of sampled blocks.
    If before_state is provided, performs hash comparison. Otherwise falls back to
    conservative filesystem signature check.
    """
    selected_mode = str(mode or "conservative_probe").strip().lower()
    if selected_mode in {"disabled", "controller_only"}:
        return {"ok": True, "status": "skipped", "details": {"mode": selected_mode, "verification_level": "controller_attested_only"}}

    # If before_state is available, perform hash comparison
    if before_state and before_state.get("ok"):
        return verify_crypto_hash_comparison(device, before_state, chunk_size_bytes)

    # Fallback to conservative probe (filesystem signature check)
    return verify_crypto_conservative_probe(device, selected_mode, sample_ratio, chunk_size_bytes, max_read_bytes)

def verify_crypto_hash_comparison(device, before_state, chunk_size_bytes):
    """
    Compares before/after hashes to verify crypto erase changed the data.
    """
    dd_cmd = resolve_verify_command_path("dd", "DRIVE_ERASER_DD_PATH", "dd", ["/usr/bin/dd", "/bin/dd"])
    if not dd_cmd:
        return {"ok": False, "status": "verification_error", "error": "dd_not_available_for_comparison", "details": {}}

    # Get capacity for end-of-drive calculations
    try:
        blockdev_cmd = ["sudo", "blockdev", "--getsize64", device]
        result = subprocess.run(blockdev_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return {"ok": False, "status": "verification_error", "error": "crypto_comparison_capacity_failed", "details": f"blockdev failed (exit code {result.returncode}): stderr={result.stderr}, stdout={result.stdout}"}
        capacity = int(result.stdout.strip())
    except Exception as e:
        return {"ok": False, "status": "verification_error", "error": "crypto_comparison_capacity_failed", "details": f"exception: {str(e)}"}

    before_details = before_state.get("details", {})
    offsets = before_details.get("offsets", [])
    before_hashes = before_details.get("hashes", [])

    if len(offsets) == 0:
        return {"ok": False, "status": "verification_error", "error": "before_state_invalid", "details": {"reason": "no_offsets_captured"}}
    if len(offsets) != len(before_hashes):
        return {"ok": False, "status": "verification_error", "error": "before_state_invalid", "details": {"offsets_count": len(offsets), "hashes_count": len(before_hashes)}}

    after_hashes = []
    total_verified_bytes = 0
    any_changed = False
    unchanged_indices = []

    # Retry with delays for drives needing time to become readable
    max_retries = 5
    retry_delays = [2, 4, 8, 15, 30]

    for idx, offset in enumerate(offsets):
        last_exception = None
        for attempt in range(max_retries):
            try:
                # Use capacity-aware read size for end-of-drive chunks
                skip_blocks = offset // chunk_size_bytes
                read_size = min(chunk_size_bytes, capacity - offset)
                actual_bs = read_size if read_size < chunk_size_bytes else chunk_size_bytes
                dd_cmd_str = ["sudo", dd_cmd, f"if={device}", f"bs={actual_bs}", f"skip={skip_blocks}", "count=1", "status=none"]
                result = subprocess.run(dd_cmd_str, capture_output=True)
                if result.returncode != 0:
                    raise Exception(f"dd read failed (exit code {result.returncode}): {result.stderr.decode('utf-8', errors='replace')}")
                data = result.stdout
                total_verified_bytes += len(data)
                after_hash = hashlib.sha256(data).hexdigest()
                after_hashes.append(after_hash)

                if after_hash == before_hashes[idx]:
                    unchanged_indices.append(idx)
                else:
                    any_changed = True
                break
            except Exception as e:
                last_exception = e
                if attempt < max_retries - 1:
                    time.sleep(retry_delays[attempt])
                else:
                    return {"ok": False, "status": "verification_error", "error": "crypto_comparison_read_failed", "details": {"offset": offset, "exception": str(last_exception), "retries_attempted": max_retries}}

    if any_changed:
        return {
            "ok": True,
            "status": "verified",
            "details": {
                "verification_level": "controller_attested_with_hash_comparison",
                "total_verified_bytes": total_verified_bytes,
                "blocks_checked": len(offsets),
                "changed_indices": [i for i in range(len(offsets)) if i not in unchanged_indices],
                "unchanged_indices": unchanged_indices,
                "before_hashes": before_hashes,
                "after_hashes": after_hashes
            }
        }

    # No hashes changed - check if drive was already zeroed
    all_before_same = len(set(before_hashes)) == 1
    all_after_same = len(set(after_hashes)) == 1

    if all_before_same and all_after_same and before_hashes[0] == after_hashes[0]:
        # All hashes identical - check actual byte values to distinguish zeros from other patterns.
        try:
            first_offset = offsets[0]
            skip_blocks = first_offset // chunk_size_bytes
            read_size = min(chunk_size_bytes, capacity - first_offset)
            actual_bs = read_size if read_size < chunk_size_bytes else chunk_size_bytes
            dd_check_cmd = ["sudo", dd_cmd, f"if={device}", f"bs={actual_bs}", f"skip={skip_blocks}", "count=1", "status=none"]
            result = subprocess.run(dd_check_cmd, capture_output=True)
            if result.returncode == 0:
                data = result.stdout
                if data:
                    is_all_zeros = data == b'\x00' * len(data)
                    if is_all_zeros:
                        return {
                            "ok": True,
                            "status": "verified",
                            "details": {
                                "verification_level": "controller_attested_with_hash_comparison",
                                "total_verified_bytes": total_verified_bytes,
                                "blocks_checked": len(offsets),
                                "warning": "Drive was already blank (all zeros) before wipe. Verification based on controller attestation.",
                                "before_hashes": before_hashes,
                                "after_hashes": after_hashes,
                                "drive_was_zeroed": True
                            }
                        }
        except Exception as e:
            pass

        # Not zeros - actual data didn't change, potential failure
        return {
            "ok": False,
            "status": "verification_failed",
            "error": "crypto_comparison_unchanged_data",
            "details": {
                "total_verified_bytes": total_verified_bytes,
                "blocks_checked": len(offsets),
                "unchanged_indices": unchanged_indices,
                "before_hashes": before_hashes,
                "after_hashes": after_hashes
            }
        }

def verify_crypto_conservative_probe(device, selected_mode, sample_ratio, chunk_size_bytes, max_read_bytes):
    """
    Fallback conservative probe: checks for filesystem signatures in first 4KB.
    """
    dd_cmd = resolve_verify_command_path("dd", "DRIVE_ERASER_DD_PATH", "dd", ["/usr/bin/dd", "/bin/dd"])
    if not dd_cmd:
        return {"ok": False, "status": "verification_error", "error": "dd_not_available_for_crypto_probe", "details": {"mode": selected_mode}}

    # Retry initial read with delays - drives may need time to become readable after crypto sanitize
    first_read = None
    capacity = None
    last_exception = None
    max_retries = 5
    retry_delays = [2, 4, 8, 15, 30]  # Progressive delays in seconds

    for attempt in range(max_retries):
        try:
            # Get capacity using blockdev
            blockdev_cmd = ["sudo", "blockdev", "--getsize64", device]
            result = subprocess.run(blockdev_cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise Exception(f"blockdev failed (exit code {result.returncode}): stderr={result.stderr}, stdout={result.stdout}")
            capacity = int(result.stdout.strip())
            if capacity <= 0:
                return {"ok": False, "status": "verification_error", "error": "crypto_probe_capacity_invalid", "details": {"mode": selected_mode}}

            # Read first 4KB using dd (or full capacity if smaller)
            read_bs = min(4096, capacity)
            dd_read_cmd = ["sudo", dd_cmd, f"if={device}", f"bs={read_bs}", "count=1", "status=none"]
            result = subprocess.run(dd_read_cmd, capture_output=True)
            if result.returncode != 0:
                raise Exception(f"dd read failed (exit code {result.returncode}): stderr={result.stderr.decode('utf-8', errors='replace')}")
            first_read = result.stdout
            break  # Success, exit retry loop
        except Exception as e:
            last_exception = e
            if attempt < max_retries - 1:
                delay = retry_delays[attempt]
                time.sleep(delay)
            else:
                return {"ok": False, "status": "verification_error", "error": "crypto_probe_read_failed", "details": {"mode": selected_mode, "exception": str(last_exception), "retries_attempted": max_retries}}

    details = {
        "mode": selected_mode,
        "verification_level": "controller_attested_with_probe",
        "capacity_bytes": capacity,
        "first_read_bytes": len(first_read),
        "zero_fill_claimed": False
    }

    # conservative_probe (default): check for filesystem signatures in first 4KB
    fs_sigs = detect_filesystem_signatures(first_read)
    details["filesystem_signatures_detected"] = fs_sigs
    if fs_sigs:
        return {
            "ok": False,
            "status": "verification_failed",
            "error": "crypto_probe_filesystem_signatures_found",
            "details": {"mode": selected_mode, "signatures": fs_sigs, "first_read_bytes": len(first_read)}
        }
    return {"ok": True, "status": "probed", "error": None, "details": details}

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
        derived_key = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), b"DWS_SALT_v1", 200000)
        payload["hmac"] = hmac.new(derived_key, serialized_for_hmac, hashlib.sha256).hexdigest()

    return json.dumps(payload, separators=(",", ":")).encode("utf-8")

def verification_for_method(device, interface_type, method, execution, before_state=None):
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

    if primary_result and primary_result.get("ok"):
        primary_result.setdefault("details", {})
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
            primary_result["details"]["verification_level"] = "full_overwrite_sampled"
        else:
            try:
                policy = load_policy()
            except Exception:
                policy = {}
            crypto_probe = verify_crypto_probe(device, policy.get("crypto_verification_mode", "conservative_probe"), before_state=before_state)
            if not crypto_probe.get("ok"):
                return {
                    "ok": False,
                    "status": "verification_failed",
                    "error": crypto_probe.get("error") or "crypto_probe_failed",
                    "details": {
                        "primary_details": primary_result.get("details"),
                        "secondary_details": crypto_probe.get("details")
                    }
                }
            primary_result["details"]["secondary_validation"] = crypto_probe.get("details")
            probe_status = crypto_probe.get("status")
            if probe_status == "skipped":
                primary_result["details"]["secondary_status"] = "SKIPPED"
            else:
                primary_result["details"]["secondary_status"] = "PASSED_CRYPTO_PROBE"
            primary_result["details"]["verification_level"] = (crypto_probe.get("details") or {}).get("verification_level", "controller_attested_with_probe")

    return primary_result
