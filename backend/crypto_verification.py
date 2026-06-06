# --- START OF FILE backend/crypto_verification.py ---
# Crypto erase verification: sampled zero check, before/after hash comparison,
# filesystem signature detection, crypto probe
import subprocess
import hashlib
import time
import random

from disk_utils import resolve_command_path

def resolve_verify_command_path(command_name, env_var_name=None, override_key=None, fallbacks=None):
    """
    Resolve the path to a verification command with support for environment variable
    overrides, config-based overrides, and fallback paths.

    Args:
        command_name: Base name of the command (e.g., "dd")
        env_var_name: Environment variable name to check for override (e.g., "DRIVE_ERASER_DD_PATH")
        override_key: Config key for override (currently unused, reserved for future config file support)
        fallbacks: List of fallback paths to try if command not found in PATH

    Returns:
        Resolved command path or None if not found
    """
    # Use provided fallbacks if available, otherwise use a sensible default
    candidates = fallbacks if fallbacks else []
    return resolve_command_path(command_name, candidates, env_var_name)

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
# --- END OF FILE backend/crypto_verification.py ---
