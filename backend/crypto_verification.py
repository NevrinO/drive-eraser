# --- START OF FILE backend/crypto_verification.py ---
# Crypto erase verification: sampled zero check, before/after hash comparison,
# filesystem signature detection, crypto probe
import subprocess
import hashlib
import hmac
import time
import random
import logging
import threading

import disk_utils
from disk_utils import resolve_command_path, validate_device_path, get_command_path
from common import get_device_lock, load_policy


def resolve_verify_command_path(command_name):
    return disk_utils.get_command_path(command_name)

# High #12: Global flag for signal interruption
_verification_interrupted = False
_verification_interrupt_lock = threading.Lock()

_MAX_SAMPLE_OFFSETS = 1000

def _generate_sampled_offsets(capacity, sample_ratio, chunk_size_bytes, max_read_bytes):
    """Generate spaced random offsets spanning the entire LBA for sampled verification."""
    offsets = []
    target_read_bytes = int(capacity * sample_ratio)
    if max_read_bytes and target_read_bytes > max_read_bytes:
        target_read_bytes = max_read_bytes

    num_chunks = max(1, target_read_bytes // chunk_size_bytes)
    num_chunks = min(num_chunks, _MAX_SAMPLE_OFFSETS)
    if capacity < chunk_size_bytes:
        chunk_size_bytes = capacity
        num_chunks = 1
    if num_chunks == 0:
        num_chunks = 1

    interval_size = capacity // num_chunks
    for i in range(num_chunks):
        start = i * interval_size
        end = max(start, (i + 1) * interval_size - chunk_size_bytes)
        if end > start:
            offset = random.randint(start, end)
            if offset != 0:
                offsets.append(offset)
        else:
            if start != 0:
                offsets.append(start)

    return offsets

def _handle_verification_signal(signum, frame):
    """Signal handler for SIGTERM/SIGINT during verification operations.
    
    Note: Signal handler registration is centralized in app.py to ensure
    consistent handling across the application. This function is called
    when SIGTERM or SIGINT signals are received during verification operations.
    """
    global _verification_interrupted
    with _verification_interrupt_lock:
        _verification_interrupted = True
    logger = logging.getLogger("app")
    logger.warning(f"Verification operation interrupted by signal {signum}")

def _check_interrupted():
    """Check if verification was interrupted by signal."""
    global _verification_interrupted
    with _verification_interrupt_lock:
        return _verification_interrupted

def _run_blockdev_getsize64(device, retries=3, retry_delay=5):
    """
    Run blockdev --getsize64 with retry logic for post-wipe transient failures.
    
    Issue 14: After an overwrite, drives may temporarily drop off the bus, causing
    blockdev to return ENOTTY. This helper retries with delays and distinguishes
    between "drive detached" and "capacity check failed" errors.
    
    Args:
        device: Device path (e.g., /dev/sda)
        retries: Number of retry attempts (default 3, total attempts = retries + 1)
        retry_delay: Delay in seconds between retries (default 5)
    
    Returns:
        On success: {"capacity": int, "error": None}
        On failure: {"capacity": None, "error": str, "details": str}
            error is "drive_detached_post_wipe" if device appears detached,
            or "secondary_capacity_check_failed" for other failures
    """
    logger = logging.getLogger("app")
    attempts = retries + 1
    last_stderr = ""
    
    for attempt in range(attempts):
        blockdev_cmd = ["sudo", "blockdev", "--getsize64", device]
        result = subprocess.run(blockdev_cmd, capture_output=True, text=True, shell=False)
        
        if result.returncode == 0:
            try:
                capacity = int(result.stdout.strip())
                logger.debug(f"blockdev --getsize64 succeeded on attempt {attempt + 1}/{attempts} for {device}")
                return {"capacity": capacity, "error": None}
            except ValueError:
                last_stderr = f"Invalid capacity output: {result.stdout}"
                logger.warning(f"blockdev returned invalid output on attempt {attempt + 1}/{attempts}: {last_stderr}")
        else:
            last_stderr = result.stderr or ""
            logger.warning(f"blockdev failed on attempt {attempt + 1}/{attempts} for {device}: exit={result.returncode}, stderr={last_stderr}")
        
        # Sleep before retry, but not after the last attempt
        if attempt < attempts - 1:
            time.sleep(retry_delay)
    
    # All attempts failed - determine error type based on stderr
    detached_indicators = ["ioctl error", "Inappropriate ioctl", "No such device", "No such file or directory"]
    is_detached = any(indicator.lower() in last_stderr.lower() for indicator in detached_indicators)
    
    if is_detached:
        error_code = "drive_detached_post_wipe"
        logger.error(f"Device {device} appears detached after {attempts} blockdev attempts: {last_stderr}")
    else:
        error_code = "secondary_capacity_check_failed"
        logger.error(f"blockdev failed after {attempts} attempts for {device}: {last_stderr}")
    
    return {"capacity": None, "error": error_code, "details": last_stderr}

def _run_dd_read_with_retry(dd_cmd, device, bs, skip, count, retries=3, retry_delay=5):
    """
    Run dd read operation with retry logic for post-wipe transient failures.
    
    Feature C: After an overwrite, drives may temporarily drop off the bus, causing
    dd reads to fail. This helper retries with delays and distinguishes between
    "drive detached" and "read failed" errors.
    
    Args:
        dd_cmd: Path to dd command
        device: Device path (e.g., /dev/sda)
        bs: Block size for dd
        skip: Skip blocks for dd
        count: Count for dd
        retries: Number of retry attempts (default 3, total attempts = retries + 1)
        retry_delay: Delay in seconds between retries (default 5)
    
    Returns:
        On success: {"data": bytes, "error": None}
        On failure: {"data": None, "error": str, "details": str}
            error is "drive_detached_post_wipe" if device appears detached,
            or "secondary_sampled_read_failed" for other failures
    """
    logger = logging.getLogger("app")
    attempts = retries + 1
    last_stderr = ""
    
    for attempt in range(attempts):
        dd_cmd_str = ["sudo", dd_cmd, f"if={device}", f"bs={bs}", f"skip={skip}", f"count={count}", "status=none"]
        try:
            result = subprocess.run(dd_cmd_str, capture_output=True, shell=False)
        except Exception as e:
            last_stderr = str(e)
            logger.warning(f"dd read exception on attempt {attempt + 1}/{attempts} for {device}: {last_stderr}")
            # Sleep before retry, but not after the last attempt
            if attempt < attempts - 1:
                time.sleep(retry_delay)
            continue
        
        if result.returncode == 0:
            logger.debug(f"dd read succeeded on attempt {attempt + 1}/{attempts} for {device}")
            return {"data": result.stdout, "error": None}
        else:
            last_stderr = result.stderr.decode('utf-8', errors='replace') if result.stderr else ""
            logger.warning(f"dd read failed on attempt {attempt + 1}/{attempts} for {device}: exit={result.returncode}, stderr={last_stderr}")
        
        # Sleep before retry, but not after the last attempt
        if attempt < attempts - 1:
            time.sleep(retry_delay)
    
    # All attempts failed - determine error type based on stderr
    detached_indicators = ["No such device", "No such file or directory", "Input/output error", "Transport endpoint is not connected"]
    is_detached = any(indicator.lower() in last_stderr.lower() for indicator in detached_indicators)
    
    if is_detached:
        error_code = "drive_detached_post_wipe"
        logger.error(f"Device {device} appears detached after {attempts} dd read attempts: {last_stderr}")
    else:
        error_code = "secondary_sampled_read_failed"
        logger.error(f"dd read failed after {attempts} attempts for {device}: {last_stderr}")
    
    return {"data": None, "error": error_code, "details": last_stderr}

def _run_cancellable_zone_read(dd_cmd, device, offset, zone_size, block_size, cancel_event, deadline):
    """
    Run a single-zone dd read that can be killed by cancel_event or deadline.

    Returns:
        {"ok": True, "nonzero": bool, "bytes_read": int, "chunks_read": int, "error": None}
        {"ok": False, "error": str, "details": str}
    """
    logger = logging.getLogger("app")
    cmd = [
        "sudo",
        dd_cmd,
        f"if={device}",
        f"bs={block_size}",
        f"skip={offset}",
        f"count={zone_size}",
        "iflag=skip_bytes,count_bytes,direct",
        "status=none",
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
    except Exception as e:
        logger.warning(f"Failed to start zero-check dd for {device}: {e}")
        return {"ok": False, "error": "zero_check_read_failed", "details": str(e)}

    kill_reason = [None]
    kill_lock = threading.Lock()

    def _watcher():
        try:
            while proc.poll() is None:
                with kill_lock:
                    if cancel_event is not None and cancel_event.is_set():
                        kill_reason[0] = "cancelled"
                        break
                    if deadline is not None and time.time() >= deadline:
                        kill_reason[0] = "timeout"
                        break
                time.sleep(0.5)
            if kill_reason[0] is not None and proc.poll() is None:
                try:
                    proc.kill()
                except Exception as e:
                    logger.debug(f"Error killing zero-check subprocess for {device}: {e}")
        except Exception as e:
            logger.warning(f"Zero-check watcher error for {device}: {e}")

    watcher = threading.Thread(target=_watcher, daemon=True)
    watcher.start()

    bytes_read = 0
    chunks_read = 0
    try:
        while True:
            chunk = proc.stdout.read(block_size)
            if not chunk:
                break
            bytes_read += len(chunk)
            chunks_read += 1
            if any(memoryview(chunk)):
                with kill_lock:
                    if kill_reason[0] is None:
                        kill_reason[0] = "nonzero"
                try:
                    proc.kill()
                except Exception:
                    pass
                break
    except Exception as e:
        logger.warning(f"Zero-check read error for {device}: {e}")
        with kill_lock:
            if kill_reason[0] is None:
                kill_reason[0] = "read_error"
        try:
            proc.kill()
        except Exception:
            pass
    finally:
        if proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            proc.wait(timeout=2)
        except Exception:
            pass
        watcher.join(timeout=2)

    with kill_lock:
        reason = kill_reason[0]

    if reason == "cancelled":
        return {"ok": False, "error": "cancelled", "details": "Zero check cancelled by user"}
    if reason == "timeout":
        return {"ok": False, "error": "timeout", "details": "Zero check exceeded timeout"}
    if reason == "nonzero":
        return {"ok": True, "nonzero": True, "bytes_read": bytes_read, "chunks_read": chunks_read, "error": None}
    if reason == "read_error":
        return {"ok": False, "error": "zero_check_read_error", "details": "Error reading from device"}

    # No kill reason: process finished on its own.
    if bytes_read == 0:
        stderr = ""
        try:
            stderr = proc.stderr.read(1024).decode("utf-8", errors="replace")
        except Exception:
            pass
        return {"ok": False, "error": "drive_disappeared_or_empty_read", "details": f"Read returned empty data at offset {offset}: {stderr}"}

    if proc.returncode != 0:
        stderr = ""
        try:
            stderr = proc.stderr.read(1024).decode("utf-8", errors="replace")
        except Exception:
            pass
        logger.warning(f"Zero-check dd failed for {device}: exit={proc.returncode}, stderr={stderr}")
        return {"ok": False, "error": "zero_check_read_failed", "details": stderr}

    return {"ok": True, "nonzero": False, "bytes_read": bytes_read, "chunks_read": chunks_read, "error": None}


def check_drive_already_zeroed(device, cancel_event=None, timeout_seconds=60):
    """
    Pre-wipe zero detection: read a flat 2 GB sample from 5 zones and check
    whether the sampled bytes are all zero. Drives <= 2 GB are read in one pass.

    Args:
        device: Device path (e.g., /dev/sda)
        cancel_event: Optional threading.Event that aborts the check when set.
        timeout_seconds: Hard timeout; if exceeded, result is "inconclusive".

    Returns:
        dict with keys: ok, result, is_zeroed, chunks_checked, bytes_checked,
        failed_at_chunk, error, details.
    """
    if not validate_device_path(device):
        return {"ok": False, "result": "failed", "is_zeroed": False, "chunks_checked": 0, "bytes_checked": 0, "failed_at_chunk": None, "error": "invalid_device_path", "details": "Device path validation failed"}

    logger = logging.getLogger("app")
    dd_cmd = resolve_verify_command_path("dd")
    if not dd_cmd:
        return {"ok": False, "result": "failed", "is_zeroed": False, "chunks_checked": 0, "bytes_checked": 0, "failed_at_chunk": None, "error": "dd_not_available_for_zero_check", "details": "dd command not found"}

    # Load policy once for both zero-check and blockdev parameters
    try:
        policy = load_policy()
        total_bytes_gb = policy.get("zero_check_total_bytes_gb", 2)
        zone_count = policy.get("zero_check_zone_count", 5)
        block_size_mb = policy.get("zero_check_block_size_mb", 16)
        small_threshold_gb = policy.get("zero_check_small_drive_threshold_gb", 2)
        blockdev_retries = policy.get("blockdev_post_wipe_retries", 3)
        blockdev_retry_delay = policy.get("blockdev_post_wipe_retry_delay", 5)
    except Exception:
        logger.warning("Failed to load policy for zero check, using defaults")
        total_bytes_gb = 2
        zone_count = 5
        block_size_mb = 16
        small_threshold_gb = 2
        blockdev_retries = 3
        blockdev_retry_delay = 5

    total_bytes = total_bytes_gb * 1024 * 1024 * 1024
    block_size = block_size_mb * 1024 * 1024
    small_threshold_bytes = small_threshold_gb * 1024 * 1024 * 1024

    device_lock = get_device_lock(device)

    def _is_cancelled():
        return cancel_event is not None and cancel_event.is_set()

    # Acquire device lock only for the brief metadata read (blockdev).
    # The actual streaming read is performed by a separate subprocess that can
    # be killed on timeout or cancellation, so the lock is released during I/O.
    with device_lock:
        if _check_interrupted():
            return {"ok": False, "result": "failed", "is_zeroed": False, "error": "verification_interrupted", "details": "Operation interrupted by signal"}

        # Get capacity using blockdev with retry logic (policy already loaded above)
        result = _run_blockdev_getsize64(device, blockdev_retries, blockdev_retry_delay)
        if result["error"]:
            return {"ok": False, "result": "failed", "is_zeroed": False, "chunks_checked": 0, "bytes_checked": 0, "failed_at_chunk": None, "error": result["error"], "details": f"blockdev failed: {result['details']}"}
        capacity = result["capacity"]

    if capacity <= 0:
        return {"ok": False, "result": "failed", "is_zeroed": False, "chunks_checked": 0, "bytes_checked": 0, "failed_at_chunk": None, "error": "invalid_capacity", "details": f"Drive reported zero capacity: {capacity}"}

    # Set deadline after blockdev completes so retry delays don't eat into
    # the zone-read timeout budget. The timeout is meant to limit dd read
    # time, not metadata query time (blockdev has its own retry logic).
    deadline = time.time() + timeout_seconds

    def _is_timed_out():
        return time.time() >= deadline

    # Determine read strategy
    if capacity <= small_threshold_bytes:
        # Small drive: read the whole device in one pass
        zones = [(0, capacity)]
    else:
        zone_size = total_bytes // zone_count
        # Align zone_size and offsets to block_size for O_DIRECT compatibility
        zone_size = (zone_size // block_size) * block_size
        if zone_size <= 0:
            zone_size = block_size
        zones = [
            (0, zone_size),  # start
            ((capacity // 4 - zone_size // 2) // block_size * block_size, zone_size),  # 25% center
            ((capacity // 2 - zone_size // 2) // block_size * block_size, zone_size),  # middle
            (((3 * capacity) // 4 - zone_size // 2) // block_size * block_size, zone_size),  # 75% center
            (((capacity - zone_size) // block_size) * block_size, zone_size),  # end
        ]

    chunks_checked = 0
    bytes_checked = 0
    zone_names = ["start", "25%", "50%", "75%", "end"]

    try:
        for zone_idx, (offset, zone_size) in enumerate(zones):
            if _is_cancelled():
                return {"ok": False, "result": "cancelled", "is_zeroed": False, "chunks_checked": chunks_checked, "bytes_checked": bytes_checked, "failed_at_chunk": zone_idx, "error": "cancelled", "details": "Zero check cancelled by user"}
            if _is_timed_out():
                return {"ok": True, "result": "inconclusive", "is_zeroed": None, "chunks_checked": chunks_checked, "bytes_checked": bytes_checked, "failed_at_chunk": zone_idx, "error": "timeout", "details": f"Zero check exceeded {timeout_seconds} seconds"}

            # Clamp zone to device bounds
            offset = max(0, min(offset, capacity))
            zone_size = min(zone_size, capacity - offset)
            if zone_size <= 0:
                continue

            zone_result = _run_cancellable_zone_read(
                dd_cmd, device, offset, zone_size, block_size, cancel_event, deadline
            )
            if not zone_result["ok"]:
                if zone_result["error"] == "cancelled":
                    return {"ok": False, "result": "cancelled", "is_zeroed": False, "chunks_checked": chunks_checked, "bytes_checked": bytes_checked, "failed_at_chunk": zone_idx, "error": "cancelled", "details": "Zero check cancelled by user"}
                if zone_result["error"] == "timeout":
                    return {"ok": True, "result": "inconclusive", "is_zeroed": None, "chunks_checked": chunks_checked, "bytes_checked": bytes_checked, "failed_at_chunk": zone_idx, "error": "timeout", "details": f"Zero check exceeded {timeout_seconds} seconds"}
                return {"ok": False, "result": "failed", "is_zeroed": False, "chunks_checked": chunks_checked, "bytes_checked": bytes_checked, "failed_at_chunk": zone_idx, "error": zone_result["error"], "details": zone_result["details"]}

            bytes_checked += zone_result["bytes_read"]
            chunks_checked += zone_result["chunks_read"]
            if zone_result.get("nonzero"):
                return {
                    "ok": True,
                    "result": "data_present",
                    "is_zeroed": False,
                    "chunks_checked": chunks_checked,
                    "bytes_checked": bytes_checked,
                    "failed_at_chunk": zone_idx,
                    "error": None,
                    "details": {"zone": zone_names[zone_idx] if zone_idx < len(zone_names) else zone_idx, "offset": offset}
                }

    except Exception as e:
        logger.warning(f"Zero check unexpected error for {device}: {e}")
        return {"ok": False, "result": "failed", "is_zeroed": False, "chunks_checked": chunks_checked, "bytes_checked": bytes_checked, "failed_at_chunk": None, "error": "unexpected_error", "details": str(e)}

    return {
        "ok": True,
        "result": "zeroed",
        "is_zeroed": True,
        "chunks_checked": chunks_checked,
        "bytes_checked": bytes_checked,
        "failed_at_chunk": None,
        "error": None,
        "details": {"zones": zone_names[:len(zones)], "capacity": capacity}
    }


def verify_sampled_zero_check(device, sample_ratio=0.10, chunk_size_bytes=32*1024*1024, max_read_bytes=10*1024*1024*1024):
    """
    Performs a secondary zero-validation check by reading the first 32MB and
    spatially distributed samples across the drive LBA range. Combines random
    sampling with sequential chunk reads to avoid disk head seek bottlenecks on HDDs.
    High #12: Signal handling for interruption. High #13: Device-level locking.
    Issue 14: Uses policy-configured retry logic for blockdev calls.
    """
    if not validate_device_path(device):
        return {"ok": False, "error": "invalid_device_path", "details": "Device path validation failed"}
    dd_cmd = resolve_verify_command_path("dd")
    if not dd_cmd:
        return {"ok": False, "error": "dd_not_available_for_zero_check", "details": "dd command not found"}

    # High #13: Acquire device lock using context manager for automatic release
    device_lock = get_device_lock(device)
    logger = logging.getLogger("app")
    
    with device_lock:
        # High #12: Check for interruption
        if _check_interrupted():
            return {"ok": False, "error": "verification_interrupted", "details": "Operation interrupted by signal"}

        # Issue 14: Load policy for retry configuration with hardcoded fallback
        try:
            policy = load_policy()
            retries = policy.get("blockdev_post_wipe_retries", 3)
            retry_delay = policy.get("blockdev_post_wipe_retry_delay", 5)
        except Exception:
            logger.warning("Failed to load policy, using default retry values")
            retries = 3
            retry_delay = 5

        # Get capacity using blockdev with retry logic
        result = _run_blockdev_getsize64(device, retries, retry_delay)
        if result["error"]:
            return {"ok": False, "error": result["error"], "details": f"blockdev failed: {result['details']}"}
        capacity = result["capacity"]

        # Always check first 32MB (holds VBR/partition table)
        offsets = [0]
        offsets.extend(_generate_sampled_offsets(capacity, sample_ratio, chunk_size_bytes, max_read_bytes))

        total_verified_bytes = 0
        non_zero_found = False
        first_non_zero_offset = None

        for offset in offsets:
            # High #12: Check for interruption before each read
            if _check_interrupted():
                logger.warning(f"Verification interrupted at offset {offset}")
                return {"ok": False, "error": "verification_interrupted", "details": f"Operation interrupted at offset {offset}"}

            # Use 32MB chunks for all reads, with dynamic bs for partial chunks
            skip_blocks = offset // chunk_size_bytes
            read_size = min(chunk_size_bytes, capacity - offset)
            actual_bs = read_size if read_size < chunk_size_bytes else chunk_size_bytes
            
            # Feature C: Use retry logic for dd reads
            dd_result = _run_dd_read_with_retry(dd_cmd, device, actual_bs, skip_blocks, 1, retries, retry_delay)
            if dd_result["error"]:
                return {"ok": False, "error": dd_result["error"], "details": f"dd read failed at offset {offset}: {dd_result['details']}"}
            data = dd_result["data"]
            total_verified_bytes += len(data)

            if any(memoryview(data)):
                non_zero_found = True
                first_non_zero_offset = offset
                break

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
    High #12: Signal handling for interruption. High #13: Device-level locking.
    """
    if not validate_device_path(device):
        return {"ok": False, "error": "invalid_device_path", "details": "Device path validation failed"}
    dd_cmd = resolve_verify_command_path("dd")
    if not dd_cmd:
        return {"ok": False, "error": "dd_not_available_for_capture", "details": "dd command not found"}

    # High #13: Acquire device lock using context manager for automatic release
    device_lock = get_device_lock(device)
    logger = logging.getLogger("app")
    
    with device_lock:
        # High #12: Check for interruption
        if _check_interrupted():
            return {"ok": False, "error": "verification_interrupted", "details": "Operation interrupted by signal"}

        # Feature C: Load policy for retry configuration with hardcoded fallback
        try:
            policy = load_policy()
            retries = policy.get("blockdev_post_wipe_retries", 3)
            retry_delay = policy.get("blockdev_post_wipe_retry_delay", 5)
        except Exception:
            logger.warning("Failed to load policy, using default retry values")
            retries = 3
            retry_delay = 5

        # Get capacity using blockdev with retry logic
        result = _run_blockdev_getsize64(device, retries, retry_delay)
        if result["error"]:
            return {"ok": False, "error": "capture_capacity_check_failed", "details": f"blockdev failed: {result['details']}"}
        capacity = result["capacity"]

        # Always capture first 32MB (holds VBR/partition table)
        offsets = [0]
        offsets.extend(_generate_sampled_offsets(capacity, sample_ratio, chunk_size_bytes, max_read_bytes))

        hashes = []
        total_captured_bytes = 0

        for offset in offsets:
            # High #12: Check for interruption before each read
            if _check_interrupted():
                logger.warning(f"Capture interrupted at offset {offset}")
                return {"ok": False, "error": "verification_interrupted", "details": f"Operation interrupted at offset {offset}"}

            # Use 32MB chunks for all reads, with dynamic bs for partial chunks
            skip_blocks = offset // chunk_size_bytes
            read_size = min(chunk_size_bytes, capacity - offset)
            actual_bs = read_size if read_size < chunk_size_bytes else chunk_size_bytes
            
            # Feature C: Use retry logic for dd reads
            dd_result = _run_dd_read_with_retry(dd_cmd, device, actual_bs, skip_blocks, 1, retries, retry_delay)
            if dd_result["error"]:
                return {"ok": False, "error": "capture_read_failed", "details": f"dd read failed at offset {offset}: {dd_result['details']}", "is_detached": dd_result["error"] == "drive_detached_post_wipe"}
            data = dd_result["data"]
            total_captured_bytes += len(data)
            hashes.append(hashlib.sha256(data).hexdigest())

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

def verify_crypto_probe(device, mode="conservative_probe", sample_ratio=0.01, chunk_size_bytes=32*1024*1024, max_read_bytes=512*1024*1024, before_state=None):
    """
    Verifies crypto erase by comparing before/after hashes of sampled blocks.
    If before_state is provided, performs hash comparison. Otherwise falls back to
    sampled zero check for strong verification.
    """
    selected_mode = str(mode or "conservative_probe").strip().lower()
    if selected_mode in {"disabled", "controller_only"}:
        return {"ok": True, "status": "skipped", "details": {"mode": selected_mode, "verification_level": "controller_attested_only"}}

    # If before_state is available, perform hash comparison
    if before_state and before_state.get("ok"):
        return verify_crypto_hash_comparison(device, before_state, chunk_size_bytes)

    # Fallback to sampled zero check for strong verification
    return verify_sampled_zero_check(device, sample_ratio=sample_ratio, chunk_size_bytes=chunk_size_bytes, max_read_bytes=max_read_bytes)

def verify_crypto_hash_comparison(device, before_state, chunk_size_bytes):
    """
    Compares before/after hashes to verify crypto erase changed the data.
    Issue 14: Uses policy-configured retry logic for blockdev calls.
    """
    if not validate_device_path(device):
        return {"ok": False, "status": "verification_error", "error": "invalid_device_path", "details": {}}
    dd_cmd = resolve_verify_command_path("dd")
    if not dd_cmd:
        return {"ok": False, "status": "verification_error", "error": "dd_not_available_for_comparison", "details": {}}
    logger = logging.getLogger("app")

    # Issue 14: Load policy for retry configuration with hardcoded fallback
    try:
        policy = load_policy()
        retries = policy.get("blockdev_post_wipe_retries", 3)
        retry_delay = policy.get("blockdev_post_wipe_retry_delay", 5)
    except Exception:
        logger.warning("Failed to load policy, using default retry values")
        retries = 3
        retry_delay = 5

    # High #13: Acquire device lock for all read operations (consistent with verify_sampled_zero_check)
    device_lock = get_device_lock(device)
    with device_lock:
        # Get capacity for end-of-drive calculations with retry logic
        result = _run_blockdev_getsize64(device, retries, retry_delay)
        if result["error"]:
            return {"ok": False, "status": "verification_error", "error": result["error"], "details": f"blockdev failed: {result['details']}"}
        capacity = result["capacity"]

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

        for idx, offset in enumerate(offsets):
            # High #12: Check for interruption before each read
            if _check_interrupted():
                logger.warning(f"Hash comparison interrupted at offset {offset}")
                return {"ok": False, "status": "verification_interrupted", "error": "verification_interrupted", "details": f"Operation interrupted at offset {offset}"}

            # Use capacity-aware read size for end-of-drive chunks
            skip_blocks = offset // chunk_size_bytes
            read_size = min(chunk_size_bytes, capacity - offset)
            actual_bs = read_size if read_size < chunk_size_bytes else chunk_size_bytes
            
            # Feature C: Use retry logic for dd reads
            dd_result = _run_dd_read_with_retry(dd_cmd, device, actual_bs, skip_blocks, 1, retries, retry_delay)
            if dd_result["error"]:
                return {"ok": False, "status": "verification_error", "error": "crypto_comparison_read_failed", "details": {"offset": offset, "exception": dd_result['error'], "retries_attempted": retries + 1, "stderr": dd_result['details']}}
            data = dd_result["data"]
            total_verified_bytes += len(data)
            after_hash = hashlib.sha256(data).hexdigest()
            after_hashes.append(after_hash)

            if hmac.compare_digest(after_hash, before_hashes[idx]):
                unchanged_indices.append(idx)
            else:
                any_changed = True

        if any_changed:
            # Some chunks changed - verify unchanged chunks are all zero (partial wipe detection)
            # Optimization (A16): Compare before_hashes against pre-computed all-zeros hash.
            # If before-hash matches all-zeros hash, chunk was zero before wipe and is still
            # unchanged → still zero, no dd read needed. If before-hash differs, chunk was
            # non-zero before wipe and is still unchanged → partial wipe, no dd read needed.
            if unchanged_indices:
                _zeros_hash_cache = {}
                def _get_zeros_hash(size):
                    if size not in _zeros_hash_cache:
                        _zeros_hash_cache[size] = hashlib.sha256(b'\x00' * size).hexdigest()
                    return _zeros_hash_cache[size]

                unchanged_nonzero_found = False
                first_nonzero_offset = None
                for idx in unchanged_indices:
                    offset = offsets[idx]
                    if _check_interrupted():
                        logger.warning(f"Hash comparison interrupted during unchanged verification at offset {offset}")
                        return {"ok": False, "status": "verification_interrupted", "error": "verification_interrupted", "details": f"Operation interrupted at offset {offset}"}

                    read_size = min(chunk_size_bytes, capacity - offset)
                    zeros_hash = _get_zeros_hash(read_size)
                    if not hmac.compare_digest(before_hashes[idx], zeros_hash):
                        unchanged_nonzero_found = True
                        first_nonzero_offset = offset
                
                if unchanged_nonzero_found:
                    # Partial wipe - some chunks changed, some didn't and aren't zero
                    return {
                        "ok": False,
                        "status": "verification_failed",
                        "error": "crypto_comparison_partial_wipe",
                        "details": {
                            "first_nonzero_offset": first_nonzero_offset,
                            "total_verified_bytes": total_verified_bytes,
                            "chunks_checked": len(offsets),
                            "chunk_size_bytes": chunk_size_bytes,
                            "changed_indices": [i for i in range(len(offsets)) if i not in unchanged_indices],
                            "unchanged_indices": unchanged_indices,
                            "before_hashes": before_hashes,
                            "after_hashes": after_hashes
                        }
                    }
                # All unchanged chunks are zero - pass (some were already zero)
                return {
                    "ok": True,
                    "status": "verified",
                    "details": {
                        "verification_level": "controller_attested_with_hash_comparison",
                        "total_verified_bytes": total_verified_bytes,
                        "chunks_checked": len(offsets),
                        "chunk_size_bytes": chunk_size_bytes,
                        "changed_indices": [i for i in range(len(offsets)) if i not in unchanged_indices],
                        "unchanged_indices": unchanged_indices,
                        "before_hashes": before_hashes,
                        "after_hashes": after_hashes,
                        "note": "Some chunks unchanged but verified zero (pre-existing zero areas)"
                    }
                }
            # All chunks changed - pass
            return {
                "ok": True,
                "status": "verified",
                "details": {
                    "verification_level": "controller_attested_with_hash_comparison",
                    "total_verified_bytes": total_verified_bytes,
                    "chunks_checked": len(offsets),
                    "chunk_size_bytes": chunk_size_bytes,
                    "changed_indices": [i for i in range(len(offsets)) if i not in unchanged_indices],
                    "unchanged_indices": unchanged_indices,
                    "before_hashes": before_hashes,
                    "after_hashes": after_hashes
                }
            }

        # No hashes changed - check if drive was already zeroed
        all_before_same = len(set(before_hashes)) == 1
        all_after_same = len(set(after_hashes)) == 1

        if all_before_same and all_after_same and hmac.compare_digest(before_hashes[0], after_hashes[0]):
            # All hashes identical - check actual byte values to distinguish zeros from other patterns.
            try:
                first_offset = offsets[0]
                skip_blocks = first_offset // chunk_size_bytes
                read_size = min(chunk_size_bytes, capacity - first_offset)
                actual_bs = read_size if read_size < chunk_size_bytes else chunk_size_bytes
                
                # Feature C: Use retry logic for dd reads
                dd_result = _run_dd_read_with_retry(dd_cmd, device, actual_bs, skip_blocks, 1, retries, retry_delay)
                if dd_result["error"]:
                    pass  # If read fails, proceed to unchanged data check below
                else:
                    data = dd_result["data"]
                    if data:
                        is_all_zeros = not any(memoryview(data))
                        if is_all_zeros:
                            return {
                                "ok": True,
                                "status": "verified",
                                "details": {
                                    "verification_level": "controller_attested_with_hash_comparison",
                                    "total_verified_bytes": total_verified_bytes,
                                    "chunks_checked": len(offsets),
                                    "chunk_size_bytes": chunk_size_bytes,
                                    "note": "All hashes matched (drive was zero before wipe)",
                                    "before_hashes": before_hashes,
                                    "after_hashes": after_hashes,
                                    "drive_was_zeroed": True,
                                    "secondary_note": "Hashes zero before wipe - matching expected"
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
                "chunks_checked": len(offsets),
                "chunk_size_bytes": chunk_size_bytes,
                "unchanged_indices": unchanged_indices,
                "before_hashes": before_hashes,
                "after_hashes": after_hashes
            }
        }
# --- END OF FILE backend/crypto_verification.py ---
