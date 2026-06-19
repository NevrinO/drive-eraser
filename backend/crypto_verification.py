# --- START OF FILE backend/crypto_verification.py ---
# Crypto erase verification: sampled zero check, before/after hash comparison,
# filesystem signature detection, crypto probe
import subprocess
import hashlib
import time
import random
import logging
import threading

from disk_utils import resolve_command_path, validate_device_path
from common import get_device_lock, load_policy

# High #12: Global flag for signal interruption
_verification_interrupted = False
_verification_interrupt_lock = threading.Lock()

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

def resolve_verify_command_path(command_name):
    """
    Resolve the path to a verification command using the centralized resolver.

    Args:
        command_name: Base name of the command (e.g., "dd")

    Returns:
        Resolved command path or None if not found
    """
    from disk_utils import get_command_path
    return get_command_path(command_name)

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

        for offset in offsets:
            # High #12: Check for interruption before each read
            if _check_interrupted():
                logger.warning(f"Verification interrupted at offset {offset}")
                return {"ok": False, "error": "verification_interrupted", "details": f"Operation interrupted at offset {offset}"}

            # Use 32MB chunks for all reads, with dynamic bs for partial chunks
            skip_blocks = offset // chunk_size_bytes
            read_size = min(chunk_size_bytes, capacity - offset)
            actual_bs = read_size if read_size < chunk_size_bytes else chunk_size_bytes
            dd_cmd_str = ["sudo", dd_cmd, f"if={device}", f"bs={actual_bs}", f"skip={skip_blocks}", "count=1", "status=none"]
            result = subprocess.run(dd_cmd_str, capture_output=True, shell=False)
            if result.returncode != 0:
                return {"ok": False, "error": "secondary_sampled_read_failed", "details": f"dd read failed at offset {offset}: {result.stderr.decode('utf-8', errors='replace')}"}
            data = result.stdout
            total_verified_bytes += len(data)

            # Highly optimized C-level block evaluation in Python
            if data != b'\x00' * len(data):
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

        # Get capacity using blockdev
        blockdev_cmd = ["sudo", "blockdev", "--getsize64", device]
        result = subprocess.run(blockdev_cmd, capture_output=True, text=True, shell=False)
        if result.returncode != 0:
            return {"ok": False, "error": "capture_capacity_check_failed", "details": f"blockdev failed (exit code {result.returncode}): stderr={result.stderr}, stdout={result.stdout}"}
        capacity = int(result.stdout.strip())

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

        for offset in offsets:
            # High #12: Check for interruption before each read
            if _check_interrupted():
                logger.warning(f"Capture interrupted at offset {offset}")
                return {"ok": False, "error": "verification_interrupted", "details": f"Operation interrupted at offset {offset}"}

            # Use 32MB chunks for all reads, with dynamic bs for partial chunks
            skip_blocks = offset // chunk_size_bytes
            read_size = min(chunk_size_bytes, capacity - offset)
            actual_bs = read_size if read_size < chunk_size_bytes else chunk_size_bytes
            dd_cmd_str = ["sudo", dd_cmd, f"if={device}", f"bs={actual_bs}", f"skip={skip_blocks}", "count=1", "status=none"]
            result = subprocess.run(dd_cmd_str, capture_output=True, shell=False)
            if result.returncode != 0:
                return {"ok": False, "error": "capture_read_failed", "details": f"dd read failed at offset {offset}: {result.stderr.decode('utf-8', errors='replace')}"}
            data = result.stdout
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

    # Issue 14: Load policy for retry configuration with hardcoded fallback
    try:
        policy = load_policy()
        retries = policy.get("blockdev_post_wipe_retries", 3)
        retry_delay = policy.get("blockdev_post_wipe_retry_delay", 5)
    except Exception:
        logger = logging.getLogger("app")
        logger.warning("Failed to load policy, using default retry values")
        retries = 3
        retry_delay = 5

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
                result = subprocess.run(dd_cmd_str, capture_output=True, shell=False)
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
        # Some chunks changed - verify unchanged chunks are all zero (partial wipe detection)
        if unchanged_indices:
            unchanged_nonzero_found = False
            first_nonzero_offset = None
            for idx in unchanged_indices:
                offset = offsets[idx]
                last_exception = None
                for attempt in range(max_retries):
                    try:
                        skip_blocks = offset // chunk_size_bytes
                        read_size = min(chunk_size_bytes, capacity - offset)
                        actual_bs = read_size if read_size < chunk_size_bytes else chunk_size_bytes
                        dd_check_cmd = ["sudo", dd_cmd, f"if={device}", f"bs={actual_bs}", f"skip={skip_blocks}", "count=1", "status=none"]
                        result = subprocess.run(dd_check_cmd, capture_output=True, shell=False)
                        if result.returncode != 0:
                            raise Exception(f"dd read failed (exit code {result.returncode}): {result.stderr.decode('utf-8', errors='replace')}")
                        data = result.stdout
                        if data and data != b'\x00' * len(data):
                            unchanged_nonzero_found = True
                            first_nonzero_offset = offset
                            break
                        break  # Success - chunk is zero, move to next
                    except Exception as e:
                        last_exception = e
                        if attempt < max_retries - 1:
                            time.sleep(retry_delays[attempt])
                        else:
                            return {
                                "ok": False,
                                "status": "verification_error",
                                "error": "crypto_comparison_unchanged_verification_failed",
                                "details": {
                                    "offset": offset,
                                    "exception": str(last_exception),
                                    "retries_attempted": max_retries,
                                    "total_verified_bytes": total_verified_bytes,
                                    "chunks_checked": len(offsets),
                                    "chunk_size_bytes": chunk_size_bytes,
                                    "changed_indices": [i for i in range(len(offsets)) if i not in unchanged_indices],
                                    "unchanged_indices": unchanged_indices,
                                    "before_hashes": before_hashes,
                                    "after_hashes": after_hashes
                                }
                            }
            
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

    if all_before_same and all_after_same and before_hashes[0] == after_hashes[0]:
        # All hashes identical - check actual byte values to distinguish zeros from other patterns.
        try:
            first_offset = offsets[0]
            skip_blocks = first_offset // chunk_size_bytes
            read_size = min(chunk_size_bytes, capacity - first_offset)
            actual_bs = read_size if read_size < chunk_size_bytes else chunk_size_bytes
            dd_check_cmd = ["sudo", dd_cmd, f"if={device}", f"bs={actual_bs}", f"skip={skip_blocks}", "count=1", "status=none"]
            result = subprocess.run(dd_check_cmd, capture_output=True, shell=False)
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
