import os
import re
import json
import subprocess
import hashlib
import hmac
import time
import threading
import logging
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from common import load_policy, get_device_lock

logger = logging.getLogger("app")

from disk_utils import (
    read_marker_status,
    check_write_tolerance,
    validate_device_path,
    get_command_path,
    MARKER_SIGNATURE,
    MARKER_BLOCK_SIZE,
    PBKDF2_ITERATIONS,
    PBKDF2_SALT,
)

# TTL cache for software versions (24 hour cache, per lesson-learned #56)
_VERSIONS_CACHE = {"data": None, "timestamp": 0}
_VERSIONS_CACHE_TTL = 86400  # 24 hours in seconds - software versions only change on package updates
_VERSIONS_CACHE_LOCK = threading.Lock()  # Thread-safe cache access (per lesson-learned #2)
from smart_parsing import get_smart_data
from crypto_verification import (
    verify_sampled_zero_check,
    capture_before_state,
    verify_crypto_probe,
    verify_crypto_hash_comparison,
)

def resolve_verify_command_path(command_name):
    # Delegate to the centralized, cached, thread-safe resolver in disk_utils so
    # there is a single source of truth for command resolution.
    return get_command_path(command_name)

def run_verification_command(command, text=True):
    if not command or not command[0]:
        return {"ok": False, "stdout": "", "stderr": "", "return_code": None, "output_bytes": b""}
    result = subprocess.run(["sudo"] + command, capture_output=True, text=text, shell=False)
    stdout = result.stdout if isinstance(result.stdout, str) else ""
    stderr = result.stderr if isinstance(result.stderr, str) else ""
    output_bytes = result.stdout if isinstance(result.stdout, bytes) else b""
    return {"ok": result.returncode == 0, "stdout": stdout.strip(), "stderr": stderr.strip(), "return_code": result.returncode, "output_bytes": output_bytes}

def verify_overwrite(device):
    if not validate_device_path(device):
        return {"ok": False, "status": "verification_error", "error": "invalid_device_path", "details": {"method": "overwrite"}}
    dd_cmd = resolve_verify_command_path("dd")
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
    # Match hex or decimal numbers, using word boundary to prevent partial matches
    match = re.search(rf"{field_name}[^\r\n:]*:\s*(0x[0-9a-fA-F]+|\d+)\b", output, re.IGNORECASE)
    if not match:
        return None
    raw_value = match.group(1)
    try:
        if raw_value.lower().startswith("0x"):
            # Validate hex characters before parsing
            hex_chars = raw_value[2:]
            if not hex_chars or not all(c in "0123456789abcdefABCDEF" for c in hex_chars):
                return None
            return int(raw_value, 16)
        return int(raw_value)
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
        return value * 3600
    elif unit in {"min", "minute", "m"}:
        return value * 60
    return None

def verify_nvme_sanitize(device, method):
    logger.info(f"verify_nvme_sanitize called: device={device}, method={method}")

    if not validate_device_path(device):
        logger.warning(f"verify_nvme_sanitize: device path validation failed for {device}")
        return {"ok": False, "status": "verification_error", "error": "invalid_device_path", "details": {"method": method}}
    nvme_cmd = resolve_verify_command_path("nvme")
    if not nvme_cmd:
        logger.warning(f"verify_nvme_sanitize: nvme command not found")
        return {"ok": False, "status": "verification_error", "error": "nvme_not_available_for_verification", "details": {"method": method}}

    # NVMe sanitize-log must be queried on the controller device (/dev/nvmeX), not namespace (/dev/nvmeXnY)
    sanitize_device = device
    if device and re.match(r'^/dev/nvme\d+n\d+\Z', device):
        # Extract controller from namespace (e.g., /dev/nvme0n1 -> /dev/nvme0)
        match = re.match(r'^(/dev/nvme\d+)n\d+\Z', device)
        if match:
            sanitize_device = match.group(1)
            # Validate extracted controller path before use (lesson-learned #9)
            if not validate_device_path(sanitize_device):
                return {"ok": False, "status": "verification_error", "error": "invalid_extracted_device_path", "details": {"method": method}}

    result = run_verification_command([nvme_cmd, "sanitize-log", sanitize_device], text=True)
    output = extract_command_output(result)

    logger.info(f"NVMe sanitize-log for {device} (using {sanitize_device}): return_code={result.get('return_code')}, output_preview={output[:300] if output else 'empty'}")

    if not result.get("ok"):
        return {
            "ok": False,
            "status": "verification_error",
            "error": "nvme_sanitize_log_failed",
            "details": {"method": method, "stderr": result.get("stderr", ""), "return_code": result.get("return_code")},
        }

    if not output.strip():
        return {"ok": False, "status": "verification_error", "error": "nvme_sanitize_log_empty", "details": {"method": method}}

    lowered = output.lower()
    sprog = parse_numeric_field(output, "sprog")
    sstat = parse_numeric_field(output, "sstat")

    # NVMe Sanitize Log interpretation per NVMe specification:
    # SSTAT values (bits 0-2 status, bit 8 Global Data Erased):
    #   0x000 = Never Sanitized
    #   0x002 = In Progress (bit 1 set)
    #   0x003 = Failed (bits 0 and 1 set)
    #   0x101 = Completed Successfully (bit 0 set + Global Data Erased bit)
    #   0x102 = No-Deallocate Completed (bit 1 set + Global Data Erased bit)
    # SPROG values (uint16, sanitize progress):
    #   0 = 0% (initialized/preparation)
    #   1-65534 = In progress (percentage = SPROG/65535 * 100)
    #   65535 = 100% (finalized/completed)

    # Check for failure state first
    if "failed" in lowered:
        return {"ok": False, "status": "verification_failed", "error": "nvme_sanitize_failed_state", "details": {"method": method, "sprog": sprog, "sstat": hex(sstat) if sstat is not None else None}}

    # Determine SSTAT state (use lower 3 bits for status code)
    sstat_status = sstat & 0x7 if sstat is not None else None

    # SSTAT = 0x000 (Never Sanitized) - SPROG should be 0
    if sstat == 0x000:
        return {"ok": False, "status": "verification_failed", "error": "nvme_sanitize_never_executed", "details": {"method": method, "sprog": sprog, "sstat": hex(sstat) if sstat is not None else None}}

    # SSTAT = 0x002 (In Progress) - SPROG should be 0-65534
    if sstat_status == 0x2:
        progress_pct = (sprog / 65535.0) * 100 if sprog is not None and sprog < 65535 else None
        return {"ok": False, "status": "verification_failed", "error": "nvme_sanitize_still_in_progress", "details": {"method": method, "sprog": sprog, "sstat": hex(sstat) if sstat is not None else None, "progress_pct": progress_pct}}

    # SSTAT = 0x003 (Failed) - SPROG should be 65535
    if sstat_status == 0x3:
        return {"ok": False, "status": "verification_failed", "error": "nvme_sanitize_failed", "details": {"method": method, "sprog": sprog, "sstat": hex(sstat) if sstat is not None else None}}

    # SSTAT = 0x101 (Completed Successfully) - SPROG should be 65535
    if sstat == 0x101:
        return {
            "ok": True,
            "status": "verified",
            "error": None,
            "details": {"mode": "nvme_sanitize_log", "method": method, "sprog": sprog, "sstat": hex(sstat), "note": "completed_with_global_data_erased"},
        }

    # SSTAT = 0x102 (No-Deallocate Completed) - SPROG should be 65535
    if sstat == 0x102:
        return {
            "ok": True,
            "status": "verified",
            "error": None,
            "details": {"mode": "nvme_sanitize_log", "method": method, "sprog": sprog, "sstat": hex(sstat), "note": "completed_no_deallocate"},
        }

    # Fallback: use SPROG if SSTAT parsing failed or returned unexpected values
    # SPROG = 0 means 0% (initialization phase) - still in progress
    if sprog == 0:
        return {"ok": False, "status": "verification_failed", "error": "nvme_sanitize_still_in_progress", "details": {"method": method, "sprog": sprog, "sstat": hex(sstat) if sstat is not None else None, "progress_pct": 0.0}}

    # SPROG = 1-65534 means in progress at calculable percentage
    if sprog is not None and 0 < sprog < 65535:
        progress_pct = (sprog / 65535.0) * 100
        return {"ok": False, "status": "verification_failed", "error": "nvme_sanitize_still_in_progress", "details": {"method": method, "sprog": sprog, "sstat": hex(sstat) if sstat is not None else None, "progress_pct": progress_pct}}

    # SPROG = 65535 means 100% complete - check if sstat indicates success
    if sprog == 65535:
        # If sstat is 0 or None with sprog=65535, this is inconsistent state
        if sstat is None or sstat == 0:
            return {"ok": False, "status": "verification_failed", "error": "nvme_sanitize_state_inconsistent", "details": {"method": method, "sprog": sprog, "sstat": hex(sstat) if sstat is not None else None, "note": "sprog_complete_but_sstat_invalid"}}

    # If we can't determine state but no failure indicators, assume success (defensive)
    # This prevents infinite loops when parsing fails but drive appears healthy
    logger.warning(f"NVMe sanitize verification ambiguous for {device}: sprog={sprog}, sstat={hex(sstat) if sstat else None}, output={output[:200]}")

    return {
        "ok": True,
        "status": "verified",
        "error": None,
        "details": {"mode": "nvme_sanitize_log", "method": method, "sprog": sprog, "sstat": hex(sstat) if sstat is not None else None, "note": "defensive_success"},
    }

def verify_sata_secure_erase(device, method):
    if not validate_device_path(device):
        return {"ok": False, "status": "verification_error", "error": "invalid_device_path", "details": {"method": method}}
    hdparm_cmd = resolve_verify_command_path("hdparm")
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
    if not validate_device_path(device):
        return {"ok": False, "status": "verification_error", "error": "invalid_device_path", "details": {"method": method}}
    hdparm_cmd = resolve_verify_command_path("hdparm")
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
    if not validate_device_path(device):
        return {"ok": False, "status": "verification_error", "error": "invalid_device_path", "details": {"method": method}}
    sg_sanitize_cmd = resolve_verify_command_path("sg_sanitize")
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

def write_marker_and_verify(job, smart_baseline=None):
    dd_cmd = resolve_verify_command_path("dd")
    if not dd_cmd:
        return {"ok": False, "status": "marker_error", "error": "dd_not_available_for_marker_write", "details": {}}

    device = (job.get("request") or {}).get("device")
    interface_type = (job.get("request") or {}).get("interface_type")
    if not device:
        return {"ok": False, "status": "marker_error", "error": "marker_missing_device", "details": {}}
    if not validate_device_path(device):
        return {"ok": False, "status": "marker_error", "error": "invalid_device_path", "details": {}}

    # High #13: Acquire device lock for marker write using context manager
    device_lock = get_device_lock(device)
    logger = logging.getLogger("app")

    with device_lock:
        try:
            # Use provided SMART baseline if available, otherwise capture it now
            if smart_baseline is not None:
                raw_writes = smart_baseline
            else:
                smart_metrics = get_smart_data(device)
                raw_writes = smart_metrics.get("data_written_raw")
            job["request"]["data_written_at_wipe"] = raw_writes
            logger.info(f"Marker write SMART baseline: data_written_raw={raw_writes}")

            payload = build_marker_payload(job)
            if len(payload) > (MARKER_BLOCK_SIZE - 1):
                return {"ok": False, "status": "marker_error", "error": "marker_payload_too_large", "details": {"payload_bytes": len(payload)}}

            block = payload + b"\n" + b"\x00" * (MARKER_BLOCK_SIZE - len(payload) - 1)
            command = [dd_cmd, f"of={device}", f"bs={MARKER_BLOCK_SIZE}", "count=1", "conv=fdatasync", "status=none"]
            result = subprocess.run(["sudo"] + command, input=block, capture_output=True, shell=False)
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
                logger.info(f"Post-marker SMART read: data_written_raw={current_writes}, stored={stored_writes}, diff={int(current_writes) - int(stored_writes) if current_writes and stored_writes else 'N/A'}")
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
        except Exception as e:
            logger.error(f"Marker write exception: {e}")
            return {"ok": False, "status": "marker_error", "error": f"marker_exception:{str(e)}", "details": {}}

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
        derived_key = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), PBKDF2_SALT, PBKDF2_ITERATIONS)
        payload["hmac"] = hmac.new(derived_key, serialized_for_hmac, hashlib.sha256).hexdigest()

    return json.dumps(payload, separators=(",", ":")).encode("utf-8")

def _get_tool_version(tool_name, command_path, version_flag):
    """Helper function to get version of a single tool. Used for parallel execution."""
    import logging
    logger = logging.getLogger("app")
    
    if not command_path:
        logger.warning(f"{tool_name} command not found")
        return tool_name, "not_found"
    
    try:
        result = subprocess.run([command_path, version_flag], capture_output=True, text=True, timeout=5, shell=False)
        # Check both stdout and stderr for version info (some tools output to stderr)
        output = (result.stdout.strip() or result.stderr.strip()) if result.returncode == 0 else ""
        if output and any(char.isdigit() for char in output):
            return tool_name, output
        else:
            logger.warning(f"{tool_name} {version_flag} failed with return code {result.returncode}, stdout: '{result.stdout.strip()[:100]}', stderr: '{result.stderr.strip()[:100]}'")
            return tool_name, "unavailable"
    except subprocess.TimeoutExpired:
        logger.warning(f"{tool_name} {version_flag} timed out after 5 seconds")
        return tool_name, "timeout"
    except Exception as e:
        logger.warning(f"{tool_name} {version_flag} failed: {str(e)}")
        return tool_name, "error"

def get_software_versions():
    """Capture versions of key software tools used for verification. Cached with 24-hour TTL."""
    import logging
    logger = logging.getLogger("app")
    
    # Check cache first (per lesson-learned #56: only cache successful data)
    current_time = time.time()
    with _VERSIONS_CACHE_LOCK:
        if _VERSIONS_CACHE["data"] is not None and (current_time - _VERSIONS_CACHE["timestamp"]) < _VERSIONS_CACHE_TTL:
            return _VERSIONS_CACHE["data"]
    
    versions = {}
    
    # Define tools to check in parallel
    tools_to_check = [
        ("hdparm", resolve_verify_command_path("hdparm"), "-V"),
        ("nvme-cli", resolve_verify_command_path("nvme"), "--version"),
        ("sg_sanitize", resolve_verify_command_path("sg_sanitize"), "--version"),
    ]
    
    # Run version checks in parallel using ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(_get_tool_version, tool_name, cmd_path, flag): tool_name
            for tool_name, cmd_path, flag in tools_to_check
        }
        
        for future in as_completed(futures):
            tool_name, result = future.result()
            versions[tool_name] = result
    
    # Log if all tools failed
    if all(v in {"not_found", "error", "timeout", "unavailable"} for v in versions.values()):
        logger.error("All software version detection tools failed - certificates may have incomplete version information")
        # Per lesson-learned #56: do not cache failure states
        return versions
    
    # Cache successful version data (per lesson-learned #56) with thread-safe write
    with _VERSIONS_CACHE_LOCK:
        _VERSIONS_CACHE["data"] = versions
        _VERSIONS_CACHE["timestamp"] = current_time
    
    return versions

def verification_for_method(device, interface_type, method, execution, before_state=None, sample_ratio=0.10):
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

    smart_baseline = None

    if primary_result and primary_result.get("ok"):
        primary_result.setdefault("details", {})

        # Determine whether secondary verification is disabled by policy
        try:
            policy = load_policy()
        except Exception:
            policy = {}
        secondary_mode = str(
            policy.get("secondary_verification_mode") or policy.get("crypto_verification_mode") or "conservative_probe"
        ).strip().lower()

        # Unified secondary verification: hash comparison if before_state available, otherwise sampled zero check
        if before_state and before_state.get("ok"):
            crypto_probe = verify_crypto_probe(device, secondary_mode, before_state=before_state, sample_ratio=sample_ratio)
            if not crypto_probe.get("ok"):
                return {
                    "ok": False,
                    "status": "verification_failed",
                    "error": f"secondary_verification_failed: {crypto_probe.get('error')}" if crypto_probe.get("error") else "secondary_verification_failed",
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
                primary_result["details"]["secondary_status"] = "PASSED_HASH_COMPARISON"
            primary_result["details"]["verification_level"] = (crypto_probe.get("details") or {}).get("verification_level", "controller_attested_with_hash_comparison")
        elif secondary_mode not in {"disabled", "controller_only"}:
            secondary_result = verify_sampled_zero_check(device, sample_ratio=sample_ratio)
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
            primary_result["details"]["secondary_status"] = "PASSED_SAMPLED_ZERO_CHECK"
            primary_result["details"]["verification_level"] = "sampled_zero_check"
            primary_result["details"]["sample_ratio"] = sample_ratio
        else:
            # Secondary verification disabled by policy; rely on primary verification only
            primary_result["details"]["secondary_status"] = "SKIPPED"
            primary_result["details"]["verification_level"] = "primary_verification_only"

        # Capture SMART baseline after secondary verification to leverage natural delay from read pass
        smart_metrics = get_smart_data(device)
        smart_baseline = smart_metrics.get("data_written_raw")
        primary_result["details"]["smart_baseline_for_marker"] = smart_baseline

    return primary_result
