# --- START OF FILE backend/disk_utils.py ---
# Command resolution and disk utility functions

import subprocess
import json
import os
import re
import shutil
import hashlib
import hmac
import time
import logging
import threading
from common import get_config_dir, load_policy

MARKER_SIGNATURE = "DWS_MARKER_V1"
MARKER_BLOCK_SIZE = 4096

# Timeout for read-only discovery commands (smartctl/hdparm/nvme/sg_sanitize --status/dd reads).
# Prevents a hung device from stalling discovery worker threads indefinitely.
_READONLY_COMMAND_TIMEOUT = 30  # seconds

# Single source of truth for marker HMAC key derivation. Both the write path
# (verification.build_marker_payload) and read path (read_marker_status) must
# use these identical parameters or HMAC verification will always fail.
PBKDF2_ITERATIONS = 200000
PBKDF2_SALT = b"DWS_SALT_v1"

def safe_int(val, default=0):
    try: return int(val) if val is not None else default
    except (ValueError, TypeError): return default

_MAX_JSON_SIZE = 65536
# \Z (not $) anchors strictly at end-of-string; $ would also match just before a
# trailing newline, allowing "/dev/sda\n" to pass the whitelist.
_DEVICE_PATH_RE = re.compile(r'^/dev(/[a-zA-Z0-9_\-:.]+)+\Z')

def validate_device_path(device):
    if not device or not isinstance(device, str):
        return False
    if ".." in device or "\n" in device or "\r" in device:
        return False
    return bool(_DEVICE_PATH_RE.match(device))

_QUOTE = ord(b'"')
_BACKSLASH = ord(b'\\')
_OPEN_BRACE = ord(b'{')
_CLOSE_BRACE = ord(b'}')

def _find_json_bounds(data, marker_index):
    """Find the JSON object enclosing marker_index using string-aware brace matching.

    Braces inside JSON string literals (e.g. a ticket_number or serial value
    containing '{' or '}') must not be counted as structural delimiters. The
    forward scan locates the enclosing object's opening brace by tracking string
    state, then matches the corresponding closing brace.
    """
    n = len(data)
    if marker_index < 0 or marker_index >= n:
        return -1, -1

    # Forward scan from the start of the buffer to the marker, maintaining a
    # stack of structural '{' positions while ignoring braces inside strings.
    in_string = False
    escaped = False
    brace_stack = []
    for i in range(0, marker_index + 1):
        c = data[i]
        if in_string:
            if escaped:
                escaped = False
            elif c == _BACKSLASH:
                escaped = True
            elif c == _QUOTE:
                in_string = False
            continue
        if c == _QUOTE:
            in_string = True
        elif c == _OPEN_BRACE:
            brace_stack.append(i)
        elif c == _CLOSE_BRACE:
            if brace_stack:
                brace_stack.pop()
    if not brace_stack:
        return -1, -1
    start = brace_stack[0]

    # Forward scan from start to find the matching closing brace, again ignoring
    # braces that appear inside string literals.
    in_string = False
    escaped = False
    depth = 0
    for i in range(start, n):
        c = data[i]
        if in_string:
            if escaped:
                escaped = False
            elif c == _BACKSLASH:
                escaped = True
            elif c == _QUOTE:
                in_string = False
            continue
        if c == _QUOTE:
            in_string = True
        elif c == _OPEN_BRACE:
            depth += 1
        elif c == _CLOSE_BRACE:
            depth -= 1
            if depth == 0:
                return start, i
    return -1, -1

_COMMAND_RESOLUTION_CACHE = {}
_COMMAND_TTL_SECONDS = 60
_COMMAND_CACHE_LOCK = threading.Lock()

def get_command_path(command_name):
    # Unconfigured commands return None (the documented "not available" contract
    # every call site checks for) rather than raising KeyError.
    config = _COMMAND_CONFIG.get(command_name)
    if config is None:
        return None
    now = time.time()
    with _COMMAND_CACHE_LOCK:
        cached = _COMMAND_RESOLUTION_CACHE.get(command_name)
        if cached is None or (now - cached["ts"]) > _COMMAND_TTL_SECONDS:
            candidates, env_var = config
            _COMMAND_RESOLUTION_CACHE[command_name] = {"path": resolve_command_path(command_name, candidates, env_var), "ts": now}
        return _COMMAND_RESOLUTION_CACHE[command_name]["path"]

def __getattr__(name):
    if name in ("SMARTCTL_CMD", "NVME_CMD", "HDPARM_CMD", "SG_SANITIZE_CMD", "DD_CMD"):
        return get_command_path(name.replace("_CMD", "").lower())
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

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
            logging.getLogger(__name__).warning(f"Failed to load command path overrides from {config_path}: {e}")
    return {}

COMMAND_PATH_OVERRIDES = load_command_path_overrides()

_COMMAND_CONFIG = {
    "smartctl": ([COMMAND_PATH_OVERRIDES.get("smartctl"), "/usr/sbin/smartctl", "/usr/bin/smartctl", "/bin/smartctl"], "DRIVE_ERASER_SMARTCTL_PATH"),
    "nvme": ([COMMAND_PATH_OVERRIDES.get("nvme"), "/usr/sbin/nvme", "/usr/bin/nvme", "/bin/nvme"], "DRIVE_ERASER_NVME_PATH"),
    "hdparm": ([COMMAND_PATH_OVERRIDES.get("hdparm"), "/usr/sbin/hdparm", "/usr/bin/hdparm", "/bin/hdparm"], "DRIVE_ERASER_HDPARM_PATH"),
    "sg_sanitize": ([COMMAND_PATH_OVERRIDES.get("sg_sanitize"), "/usr/bin/sg_sanitize", "/usr/sbin/sg_sanitize", "/bin/sg_sanitize"], "DRIVE_ERASER_SG_SANITIZE_PATH"),
    "dd": ([COMMAND_PATH_OVERRIDES.get("dd"), "/usr/bin/dd", "/bin/dd"], "DRIVE_ERASER_DD_PATH"),
}


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
        # NVMe write accounting granularity is 1 unit = 1 block (512B or 4K),
        # so tolerance of 4 accounts for metadata writes during sanitize.
        # SATA SMART attr 241 reports in 512B sectors, so 4096 sectors = 2MB
        # tolerance for firmware accounting lag during sanitize operations.
        # SAS drives report via scsi_error_counter_log.write.gigabytes_processed
        # with only 3 decimal places of GB (1 MB granularity). Each 0.001 GB
        # increment = ~1953 sectors, and the counter naturally drifts over time
        # due to firmware accounting lag. A 100,000-sector tolerance (~49 MB)
        # accounts for ~50 counter increments of drift while still detecting
        # any significant post-wipe write activity.
        NVME_WRITE_TOLERANCE = 4
        SATA_WRITE_TOLERANCE = 4096
        SAS_WRITE_TOLERANCE = 100000
        if "nvme" in iface:
            return diff <= NVME_WRITE_TOLERANCE
        elif "sas" in iface:
            return diff <= SAS_WRITE_TOLERANCE
        else:
            return diff <= SATA_WRITE_TOLERANCE
    except Exception as e:
        logging.getLogger(__name__).warning(f"Failed to check write tolerance: {e}")
        return False

def read_marker_status(device, interface_type="unknown", passphrase=None):
    if not validate_device_path(device):
        return {"ok": False, "status": "marker_error", "error": "invalid_device_path", "details": {}}
    dd_cmd = get_command_path("dd")
    if not dd_cmd: return {"ok": False, "status": "marker_error", "error": "dd_not_available_for_marker_read", "details": {}}
    command = [dd_cmd, f"if={device}", f"bs={MARKER_BLOCK_SIZE}", "count=1", "iflag=direct", "status=none"]
    try:
        result = subprocess.run(["sudo"] + command, capture_output=True, shell=False, timeout=_READONLY_COMMAND_TIMEOUT)
        if result.returncode != 0:
            result = subprocess.run(["sudo", dd_cmd, f"if={device}", f"bs={MARKER_BLOCK_SIZE}", "count=1", "status=none"], capture_output=True, shell=False, timeout=_READONLY_COMMAND_TIMEOUT)
        if result.returncode != 0:
            return {"ok": False, "status": "marker_error", "error": "marker_read_failed", "details": {"return_code": result.returncode, "stderr": (result.stderr or b"").decode("utf-8", errors="replace").strip()}}
        output_bytes = result.stdout or b""
    except Exception as e:
        logging.getLogger(__name__).warning(f"marker_read_exception: {e}")
        return {"ok": False, "status": "marker_error", "error": "marker_read_exception", "details": {}}

    marker_index = output_bytes.find(MARKER_SIGNATURE.encode("utf-8"))
    if marker_index < 0: return {"ok": True, "status": "none", "error": None, "details": {}}

    start, end = _find_json_bounds(output_bytes, marker_index)
    if start < 0 or end < 0 or end < start: return {"ok": True, "status": "corrupted", "error": "json_parse_failed", "details": {}}

    json_bytes = output_bytes[start:end + 1]
    if len(json_bytes) > _MAX_JSON_SIZE:
        return {"ok": True, "status": "corrupted", "error": "json_too_large", "details": {}}

    try:
        parsed = json.loads(json_bytes.decode("utf-8", errors="strict"))
    except Exception as e:
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
        derived_key = hashlib.pbkdf2_hmac('sha256', passphrase.encode('utf-8'), PBKDF2_SALT, PBKDF2_ITERATIONS)
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
        result = subprocess.run(["sudo"] + command, capture_output=True, text=True, check=True, shell=False, timeout=_READONLY_COMMAND_TIMEOUT)
        if diagnostics is not None and key: diagnostics[key] = {"ok": True, "reason": None, "exit_code": result.returncode}
        return (result.stdout or "").strip()
    except subprocess.TimeoutExpired:
        if diagnostics is not None and key: diagnostics[key] = {"ok": False, "reason": f"command_timeout_{_READONLY_COMMAND_TIMEOUT}s", "exit_code": None}
        return None
    except subprocess.CalledProcessError as e:
        if diagnostics is not None and key: diagnostics[key] = {"ok": False, "reason": (e.stderr or "").strip() or f"exit_code_{e.returncode}", "exit_code": e.returncode}
        return (e.stdout or "").strip() if command and os.path.basename(command[0]) == "smartctl" else None

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
