# Code Concerns — Consolidated Review Tracker

Running document for findings across the codebase that need a deeper look before fixing. Items are grouped by file and tagged with severity: **[Critical]** or **[Advisory]**. Some critical issues may be listed here when they require investigation or design decisions before implementing a fix.

---

## backend/app.py

### A1: [Advisory] `load_policy()` Called on Every API Request (Performance)
- **Line**: 32 (inside `security_gate` before_request hook)
- **Issue**: `load_policy()` reads `policy.json` from disk, parses JSON, validates against JSON schema, and merges with defaults on every non-localhost API request. No caching layer.
- **Impact**: Unnecessary disk I/O on the hot path. Tolerable for a LAN tool with limited users, but scales poorly.
- **Suggestion**: Add a TTL cache (e.g., `functools.lru_cache` with a TTL wrapper) or a file-mtime-based cache to avoid re-reading unchanged policy.

### A2: [Advisory] `smart_test_update_thread` Global Without Lock (Concurrency)
- **Lines**: 210-225 (`start_smart_test_update_thread` / `stop_smart_test_update_thread`)
- **Issue**: `start_smart_test_update_thread` checks `smart_test_update_thread is None or not smart_test_update_thread.is_alive()` then assigns a new thread. Two concurrent callers could both see the thread as dead and start two threads.
- **Impact**: Unlikely in practice (called once at startup and from signal handling), but violates Lesson #6 concurrency guardrails.
- **Suggestion**: Add a `Lock` around the check-and-start sequence.

### A3: [Advisory] `os.path.exists()` TOCTOU in Background Thread (Concurrency)
- **Line**: 147 (inside `update_smart_test_status_background`)
- **Issue**: `os.path.exists(device)` is a TOCTOU pre-check before `get_smart_test_status(device)`. The device could disappear between check and use.
- **Impact**: Low — the subsequent call is wrapped in try/except (line 199-200), so it won't crash. But the pre-check is unnecessary.
- **Suggestion**: Remove the `os.path.exists` check and let `get_smart_test_status` handle the missing device, catching the exception.

### A4: [Advisory] Redundant Import in `security_gate` (Code Quality)
- **Line**: 21
- **Issue**: `from flask import request, jsonify` — `jsonify` is already imported at module level (line 9). Only `request` needs the local import.
- **Impact**: No functional impact, just redundant.
- **Suggestion**: Change to `from flask import request`.

### A5: [Advisory] `sys.exit(0)` in Signal Handler Exits with Success Code
- **Line**: 71
- **Issue**: `sys.exit(0)` indicates a clean exit, but wipe jobs may still be running. The handler sets interruption flags but doesn't wait for jobs to finish.
- **Impact**: Data integrity risk if jobs are mid-write. Also, exit code 0 is unconventional for signal-triggered shutdown.
- **Suggestion**: Consider `sys.exit(130)` (conventional for SIGINT) or at minimum log a warning if jobs are still active before exiting.

### A6: [Advisory] Module-Level Side Effects Make Testing Fragile (Architecture)
- **Lines**: 97, 100, 103, 109, 112, 228
- **Issue**: `init_wipe_db()`, `udev_listener.start_udev_listener()`, `start_smart_test_update_thread()`, and `get_zero_check_manager()` all execute at import time. Any test importing `app.py` triggers database initialization, starts a udev listener (fails on Windows), and spawns a background thread.
- **Impact**: Test suite fragility, especially cross-platform. Documented as necessary for WSGI deployment (line 96).
- **Suggestion**: Consider a `create_app()` factory pattern or guard behind `if __name__ == "__main__"` with a separate WSGI entry point.

---

## backend/common.py

### C1: [Critical] `save_policy()` Non-Atomic Write (Data Corruption Risk)
- **Line**: 475-481
- **Issue**: `save_policy()` writes directly to `policy.json` without tempfile + rename. If the process crashes mid-write (signal, power loss, disk full), the file is left truncated or empty. `save_bay_map()` (line 483-497) correctly uses atomic write — `save_policy()` does not match.
- **Impact**: Corrupted `policy.json` causes `load_policy()` to raise `ValueError`, which is called in the `security_gate` before_request hook. **All remote API access locked out** until manual file restoration. Called from admin routes during passphrase/threshold updates — exactly when corruption is most dangerous.
- **Suggestion**: Apply same atomic write pattern as `save_bay_map()`: write to temp file, `f.flush()` + `os.fsync()`, then `os.replace()`.

### C2: [Critical] `DEVICE_LOCKS` Dict Grows Unbounded (Memory Leak)
- **Lines**: 23-36
- **Issue**: `get_device_lock()` creates per-device `Lock` objects that are never removed. In a hot-swap drive erasure station processing many drives over months, this dict grows indefinitely.
- **Impact**: Slow memory leak. Each `Lock` is ~80 bytes — won't crash, but accumulates over long-running deployments.
- **Suggestion**: Use `WeakValueDictionary` or add cleanup when locks are released. Alternatively, document as a known limitation given expected scale.

### C3: [Critical] `load_policy()` TOCTOU on File Existence
- **Line**: 396-397
- **Issue**: `os.path.exists(policy_path)` check before `open()` is a TOCTOU race per Lesson #6. File could appear/disappear between check and open.
- **Impact**: Low probability, but violates explicit guardrail. Could produce confusing error if file is deleted mid-check.
- **Suggestion**: Remove `os.path.exists` check, attempt `open()` directly, catch `FileNotFoundError`.

### A7: [Advisory] `load_bay_map()` TOCTOU on File Existence (Same Pattern)
- **Line**: 510-511
- **Issue**: `os.path.exists(bay_map_path)` inside `BAY_MAP_LOCK`. The lock prevents concurrent Python threads but not external processes (admin editing file in another terminal). Comment claims "Fixed TOCTOU" but only fixed thread-level race, not filesystem-level.
- **Impact**: Low — same as C3 but with a misleading comment.
- **Suggestion**: Attempt `open()` directly, catch `FileNotFoundError`.

### A8: [Advisory] `purge_old_certificates()` Never Called (Dead Code)
- **Lines**: 350-374
- **Issue**: Function is defined but has zero callers anywhere in the backend. `purge_old_logs()` is called from `job_management.py` and `bulk_cert.py`, but `purge_old_certificates()` was never wired in.
- **Impact**: Dead code. Certificate retention policy exists but is never enforced.
- **Suggestion**: Wire into same cleanup paths as `purge_old_logs()`, or remove if retention is handled elsewhere.

### A9: [Advisory] `__import__("logging")` Anti-Pattern in `load_bay_map`
- **Lines**: 519, 531
- **Issue**: `logger = __import__("logging").getLogger("app")` — module already has `logger = logging.getLogger("app")` at line 26. Redundant and non-idiomatic, likely a refactor leftover.
- **Impact**: No functional impact, just code smell.
- **Suggestion**: Use the module-level `logger` directly.

### A10: [Advisory] `get_data_dir()` / `get_config_dir()` TOCTOU Pattern
- **Lines**: 291-300, 377-386
- **Issue**: `os.path.isdir(candidate)` check before returning path. Directory could be removed between check and use.
- **Impact**: Low — callers typically use `os.makedirs(path, exist_ok=True)` downstream, but pattern is inconsistent with Lesson #6.
- **Suggestion**: Return the path without the isdir check, or handle errors at the point of use.

### A11: [Advisory] `POLICY_SCHEMA` Allows `additionalProperties: True` (Config Drift)
- **Line**: 149
- **Issue**: Schema explicitly allows unknown keys. Typos in `policy.json` are silently ignored (warning logged) rather than rejected at validation time. Migration logic (lines 426-434) handles known deprecated keys, so unknown keys are likely user error.
- **Impact**: Misspelled config keys silently have no effect, which can be confusing for operators.
- **Suggestion**: Consider `additionalProperties: False` for stricter validation, with explicit handling of all deprecated keys in migration logic.

---

## backend/crypto_verification.py

### C4: [Critical] `verify_crypto_hash_comparison()` Missing Device Lock (Concurrency)
- **Line**: 672-872
- **Issue**: Every other verification function acquires `get_device_lock(device)` before reading. This function does not. Concurrent operations on the same device could produce inconsistent verification results.
- **Impact**: A concurrent wipe or discovery operation could start on the same device while hash comparison reads are in progress, causing false pass/fail.
- **Suggestion**: Acquire `get_device_lock(device)` at function start, same pattern as `verify_sampled_zero_check` (line 446-449).

### C5: [Critical] `verify_crypto_hash_comparison()` Missing Interruption Check (Signal Handling)
- **Line**: 672-872
- **Issue**: Every other verification function checks `_check_interrupted()` before and during reads. This function never checks it. SIGTERM during hash comparison will not abort gracefully.
- **Impact**: Delays shutdown in multi-drive wipe station. The function will continue reading all offsets instead of aborting.
- **Suggestion**: Add `_check_interrupted()` checks before each offset read loop, same as `verify_sampled_zero_check` (line 506) and `capture_before_state` (line 626).

### C6: [Critical] `capture_before_state()` Uses Raw `blockdev` Without Retry (Inconsistency)
- **Lines**: 585-589
- **Issue**: Uses raw `subprocess.run` for blockdev capacity check while all other functions use `_run_blockdev_getsize64()` with policy-configured retry logic. Also has uncaught `ValueError` on `int(result.stdout.strip())` (line 589).
- **Impact**: Transient blockdev failure causes before-state capture to fail, which silently downgrades verification from hash comparison to weaker sampled zero check. Uncaught `ValueError` crashes the function.
- **Suggestion**: Replace with `_run_blockdev_getsize64(device, retries, retry_delay)` using policy-loaded retry values, matching the pattern in `verify_sampled_zero_check` (line 465).

### A12: [Advisory] Duplicated Offset Generation Logic (Code Duplication)
- **Lines**: 470-498 (`verify_sampled_zero_check`) and 591-619 (`capture_before_state`)
- **Issue**: ~28 lines of nearly identical offset generation logic (calculate target bytes, determine chunk count, generate spaced random offsets).
- **Impact**: Maintenance burden — sampling strategy changes must be applied in two places.
- **Suggestion**: Extract `_generate_sampled_offsets(capacity, sample_ratio, chunk_size_bytes, max_read_bytes)` helper.

### A13: [Advisory] `b'\x00' * len(data)` Zero-Check Allocates Full-Size Temporary (Performance)
- **Lines**: 523, 767, 839
- **Issue**: Creates a temporary bytes object equal to data size (32MB) for each comparison. `_run_cancellable_zone_read` uses the more efficient `any(memoryview(chunk))` pattern (line 228).
- **Impact**: 32MB temporary allocation per chunk check. Tolerable for post-wipe verification with few chunks, but inconsistent.
- **Suggestion**: Use `not any(memoryview(data))` for memory efficiency.

### A14: [Advisory] No Size Limit on `offsets` List (DoS Prevention)
- **Lines**: 488-498, 608-619
- **Issue**: `num_chunks` derived from `capacity * sample_ratio / chunk_size_bytes`. A 20TB drive with `sample_ratio=0.10` and 32MB chunks produces ~6,400 offsets, each triggering a separate `dd` subprocess. Per Lesson #9.
- **Impact**: Excessive subprocess spawning on large drives. Slow verification, high resource usage.
- **Suggestion**: Cap `num_chunks` at a reasonable maximum (e.g., 1000).

### A15: [Advisory] `resolve_verify_command_path()` Defined Twice (Code Duplication)
- **Line**: 156-167 (also in `verification.py:39`)
- **Issue**: Same thin wrapper defined in both files, both delegating to `disk_utils.get_command_path`.
- **Impact**: Maintenance risk — if one is updated and the other isn't, behavior diverges.
- **Suggestion**: Import from one location, or move to `disk_utils` and import from there.

### A16: [Advisory] `verify_crypto_hash_comparison` Re-reads Unchanged Chunks Unnecessarily (Performance)
- **Lines**: 736-769
- **Issue**: When some chunks changed and some didn't, the function re-reads all unchanged chunks to check if they're zero. But if the before-hash differs from the all-zeros hash, the chunk is definitely non-zero without re-reading.
- **Impact**: Unnecessary disk I/O on large drives with many unchanged chunks.
- **Suggestion**: Pre-compute all-zeros hash once, skip re-reading chunks whose before-hash differs from it.

### A17: [Advisory] Hash Comparison Uses `==` Instead of `hmac.compare_digest` (Consistency)
- **Line**: 729
- **Issue**: `after_hash == before_hashes[idx]` uses `==` for hash comparison. Not a practical timing attack risk since hashes are of drive data, not secrets, but inconsistent with security best practices.
- **Impact**: No practical security impact. Consistency concern only.
- **Suggestion**: Use `hmac.compare_digest` for defensive consistency, or document as intentional.

---

## backend/disk_ops.py

### C7: [Critical] `discover_drives` Returns Inconsistent Types (Correctness/Architecture)
- **Line**: 806, 810, 856, 868, 964, 1026, 1117
- **Issue**: Function returns `[]` (empty list), `{"error": "..."}` (dict), or `results` (list of dicts) depending on code path. All callers treat return value as a list and call `.get()` on elements, which raises `AttributeError` when iterating over a dict's keys (strings).
- **Impact**: During SIGTERM/SIGINT, `/api/drives` and `/api/erase/start` endpoints return misleading HTTP 500 errors instead of graceful shutdown responses. The meaningful "Discovery interrupted by signal" message is lost.
- **Suggestion**: Replace all `return {"error": "..."}` with `return []` (callers already handle empty lists), or raise a custom exception that callers catch explicitly.

### C8: [Critical] Signal Handler Uses `threading.Lock` — Potential Deadlock (Concurrency)
- **Line**: 161-167
- **Issue**: `_handle_discovery_signal` acquires `_discovery_interrupt_lock` and `_shutdown_lock`. If signal is delivered while `_check_discovery_interrupted()` (line 172) holds `_discovery_interrupt_lock`, the handler deadlocks trying to re-acquire the non-reentrant Lock.
- **Impact**: Very low probability deadlock during shutdown. If it occurs, process hangs on SIGTERM/SIGINT and requires SIGKILL.
- **Suggestion**: Use `threading.Event` instead of `Lock`+boolean. `Event.set()` and `Event.is_set()` are lock-free and async-signal-safe in CPython.

### C9: [Critical] Massive Code Duplication Between `_collect_drive_data` and `_process_single_drive_extended_smart` (Architecture/DRY — Lesson #65)
- **Line**: 299-358 vs 689-751
- **Issue**: ~40 lines of nearly identical logic: marker status check, health score calculation, intake snapshot recording, and full payload dict construction. Only difference is `smart_polling` value.
- **Impact**: Bug fixes and schema changes must be applied in two locations. Missing one path causes inconsistent API responses between initial discovery and background SMART collection.
- **Suggestion**: Extract `_build_drive_payload(smart, interface_type, capabilities, marker_status, recommendation, health_score, penalty_breakdown, drive_type, command_diagnostics, smart_polling)` helper.

### A18: [Advisory] `_discover_drives_enclosure` and `_discover_drives_legacy` ~80% Duplicated (Architecture/DRY — Lesson #65)
- **Line**: 821-993 vs 996-1147
- **Issue**: ~130 lines of duplicated code: path_to_dev building, passphrase loading, OS path detection, PCI scan, bay_info initialization (~30 fields), pending collection, extended SMART submission, dual-port dedup, auto-enqueue.
- **Impact**: Schema changes to `bay_info` must be applied in both functions. Maintenance burden.
- **Suggestion**: Extract shared logic into helpers: `_init_bay_info()`, `_build_path_to_dev()`, `_submit_extended_smart_for_results()`, `_finalize_discovery()`.

### A19: [Advisory] TOCTOU `os.path.exists` Patterns Throughout (Concurrency — Lesson #5)
- **Line**: 205, 223, 238, 251, 267, 499, 555, 567, 579, 585
- **Issue**: Multiple `os.path.exists()` checks before `open()`/`os.listdir()`/`os.path.realpath()` operations. Per Lesson #5, should use `try: operation() except OSError: handle_error()` without pre-checks.
- **Impact**: Low — read-only operations on system paths that rarely disappear.
- **Suggestion**: Replace pre-check patterns with direct `try/except (OSError, IOError)`.

### A20: [Advisory] `_get_extended_smart_executor` Lock Acquisition Not Atomic (Concurrency — Lesson #5)
- **Line**: 658-674
- **Issue**: `_shutdown_lock` is released before `_EXTENDED_SMART_LOCK` is acquired. Between these, `stop_extended_smart_pool()` could shut down the executor. Stale `shutdown_requested = False` causes a new executor to be created after shutdown.
- **Impact**: Low — leaked ThreadPoolExecutor (never shut down). Subsequent shutdown checks in `_submit_drive_for_extended_smart` and `_process_single_drive_extended_smart` prevent work from being processed.
- **Suggestion**: Check `_shutdown_requested` inside `_EXTENDED_SMART_LOCK` scope, or acquire both locks atomically.

### A21: [Advisory] `get_discovery_max_workers` / `get_background_smart_max_workers` Load Policy on Every Call (Performance — Lesson #32)
- **Line**: 128-147
- **Issue**: Both functions call `load_policy(get_config_dir())` on every invocation, reading and parsing `policy.json` from disk.
- **Impact**: Low — 1-2 extra disk reads per discovery batch. Acceptable for a LAN tool.
- **Suggestion**: Consider caching with short TTL or file-mtime check if discovery frequency increases.

### A22: [Advisory] `_apply_collection_failure` Assumes `diagnostics.commands` Key Exists (Correctness)
- **Line**: 378
- **Issue**: Accesses `bay_info["diagnostics"]["commands"]["collection"]` without guard. Safe in practice (both discovery functions initialize this structure), but fragile if called from other contexts.
- **Impact**: Low — only called from `_collect_pending_parallel` and `_collect_pending_serial` which operate on initialized bay_info dicts.
- **Suggestion**: Use `bay_info.setdefault("diagnostics", {}).setdefault("commands", {})["collection"] = ...` for defensive robustness.

### A23: [Advisory] Cache Key Construction Differs Between Enclosure and Legacy Modes (Architecture)
- **Line**: 950 vs 1103
- **Issue**: Enclosure mode uses `cache_key = (dev_node, dev_node)`, legacy uses `cache_key = (resolved_active_path or configured_active_path, dev_node)`. Schema transition would cause cache misses.
- **Impact**: Low — schemas are mutually exclusive. TTL cache expires stale entries within `DRIVE_DATA_CACHE_TTL` seconds.
- **Suggestion**: Document that cache keys are schema-specific, or unify on `(dev_node, dev_node)` for both modes.

### A24: [Advisory] `_auto_enqueue_zero_checks` Swallows Policy Load Errors Silently (Error Handling — Lesson #33)
- **Line**: 89-92
- **Issue**: `except Exception: return` with no logging. If `load_policy` fails, zero-checks are silently disabled with no diagnostic trail.
- **Impact**: Low — zero-checks are a pre-wipe convenience. Silent failure means drives won't be auto-checked but wipe workflow is unaffected.
- **Suggestion**: Add `logging.getLogger(__name__).warning(f"Failed to load policy for zero-check enqueue: {e}")` in the except block.

---

<!-- Add new file sections below as more files are reviewed -->

## backend/job_management.py

### [Critical] C10 — `_job_interrupted` flag never reset after signal
- **Lines**: 420-429
- **Issue**: The module-level `_job_interrupted` flag is set to `True` by the signal handler but never reset to `False`. If the process catches SIGINT/SIGTERM and doesn't exit immediately (graceful shutdown), every subsequent `run_erase_job` call will immediately mark the job as interrupted at line 427-429.
- **Impact**: High — permanently disables ability to start new jobs without full process restart after any caught signal.
- **Suggestion**: Reset `_job_interrupted = False` at the start of each `run_erase_job` call, or use a per-job interruption flag instead of a module-level flag.

### [Critical] C11 — Log file handle leaked if write/flush fails after successful open
- **Lines**: 482-490
- **Issue**: If `open()` succeeds but `write()` or `flush()` raises (disk full, permissions change), the `except` block calls `finalize_failed_job` and returns without closing `log_file`. The later `try/finally` at line 510 never executes.
- **Impact**: Medium — file descriptor leak on log write failure. Repeated failures could exhaust the fd limit.
- **Suggestion**: Use a context manager (`with open(...) as log_file:`) or close `log_file` in the except block before returning.

### [Advisory] A25 — `os.path.exists` TOCTOU patterns
- **Lines**: 188, 311, 866, 905
- **Issue**: Multiple `os.path.exists()` checks followed by file operations (stat read, log rename, log remove). Per Lesson #5 (TOCTOU Prevention).
- **Impact**: Low — race window is small, but pattern is fragile.
- **Suggestion**: Use direct operation + exception handling (`try: os.rename(...) except FileNotFoundError: pass`).

### [Advisory] A26 — Bare `except Exception: pass` in poll functions swallow all errors
- **Lines**: 194, 209, 224, 239
- **Issue**: `poll_*_sanitize_progress` functions catch all exceptions and return `None` without logging. Silent swallowing of `PermissionError`, `FileNotFoundError`, `OSError`, etc.
- **Impact**: Low — progress reporting failures become impossible to debug.
- **Suggestion**: Add `logger.debug(f"poll failed for {device}: {e}")` in except blocks.

### [Advisory] A27 — Hardcoded 512-byte sector size assumption
- **Lines**: 544
- **Issue**: `wrote_bytes = delta_sectors * 512` assumes 512-byte logical sectors. Modern 4Kn drives use 4096-byte logical sectors.
- **Impact**: Low — progress calculations off by 8x on 4Kn drives. Does not affect actual wipe.
- **Suggestion**: Read logical block size from `/sys/block/{dev}/queue/logical_block_size`.

### [Advisory] A28 — Stray line after END OF FILE marker
- **Lines**: 938
- **Issue**: Line 938 (`# Validate input is a list and enforce size limit for DoS prevention`) appears after the `# --- END OF FILE ---` marker. Copy-paste artifact from another file.
- **Impact**: None (comment only), but indicates file corruption or bad merge.
- **Suggestion**: Remove the stray line.

---

## backend/udev_listener.py

### [Critical] C12 — `get_runtime_slot_state()` is dead code (zero external callers)
- **Lines**: 348-355
- **Issue**: `get_runtime_slot_state()` is defined but never called from any file other than its own definition. Grep across `backend/` shows only 1 match (the definition).
- **Impact**: Low — dead code adds maintenance burden and confusion.
- **Suggestion**: Remove the function, or connect it to a consumer if the feature was intended.

### [Advisory] A29 — `_runtime_slot_state` stores `None` instead of deleting keys
- **Lines**: 286
- **Issue**: On drive removal, `_runtime_slot_state[(enc_id, slot_num)] = None` sets value to `None` rather than deleting the key. `get_runtime_slot_state()` returns entries with `None` values.
- **Impact**: Low — no current external callers, but future callers must filter `None` values.
- **Suggestion**: Use `_runtime_slot_state.pop((enc_id, slot_num), None)` instead.

### [Advisory] A30 — `os.path.exists` TOCTOU patterns
- **Lines**: 82, 231
- **Issue**: `os.path.exists(sas_device_dir)` before `os.listdir()`, `os.path.isdir('/dev/mapper')` before listing. Per Lesson #5.
- **Impact**: Low — race window is small.
- **Suggestion**: Use direct `os.listdir()` in try/except `OSError`.

### [Advisory] A31 — `bay_map.json` reloaded on every udev event
- **Lines**: 212-216
- **Issue**: `json.load(f)` called for every udev hot-plug event. Unnecessary disk I/O for frequent device changes.
- **Impact**: Low — performance concern only on systems with rapid hot-plug cycles.
- **Suggestion**: Cache with file mtime check or use inotify-based file watcher.

---

## backend/zero_check_manager.py

### [Advisory] A32 — `_emit_update` double lock acquisition
- **Lines**: 80, 104, 196
- **Issue**: `_emit_update` calls `_get_status(bay)` which re-acquires `_lock`. Two lock/unlock cycles per status update.
- **Impact**: Low — minor performance overhead.
- **Suggestion**: Pass the status dict directly to `_emit_update` instead of re-reading it.

### [Advisory] A33 — `get_all_status` shallow copy of status dicts
- **Lines**: 203
- **Issue**: `dict(status)` creates a shallow copy. Nested dicts (if added in future) would be shared references.
- **Impact**: Low — currently safe since status dicts contain only primitives.
- **Suggestion**: Use `copy.deepcopy()` if nested structures are added in the future.

---

## backend/routes/admin_routes.py

### [Critical] C13 — `ERASE_JOBS_LOCK` held during subprocess calls in `kill_all_jobs`
- **Lines**: 865-966
- **Issue**: `kill_all_jobs` holds `ERASE_JOBS_LOCK` while calling `check_drive_hardware_status(job)` (line 875), which spawns subprocesses (`verify_nvme_sanitize`, `verify_sata_sanitize`, `verify_sas_block`, etc.) that can take several seconds per job. All other operations requiring the lock are blocked.
- **Impact**: High — status polling, job starts, and other kills are blocked for potentially tens of seconds during kill-all.
- **Suggestion**: Snapshot job list inside lock, release lock, perform hardware checks, then re-acquire lock to apply kill decisions.

### [Critical] C14 — Hardcoded OS device paths in SMART test endpoint
- **Lines**: 2411-2418
- **Issue**: Only checks `/dev/sda` and `/dev/nvme0n1` for OS drive detection. OS could be on `/dev/sdb`, `/dev/nvme1n1`, etc. Safety bypass allows SMART test on OS drive.
- **Impact**: Medium — SMART test on OS drive could cause system instability.
- **Suggestion**: Use the same `get_os_by_path()` function used by `job_management.py` for robust OS drive detection.

### [Advisory] A34 — `str(e)` in API responses throughout (information disclosure)
- **Lines**: 178, 212, 263, 428, 446, 509, 577, 1008, 1049, 1353, 1558, 1582, 1644, 1694, 1720, 1780, 1812, 1871, 1914, 1941, 1967, 2027, 2051, 2345, 2494, 2587
- **Issue**: Exception messages returned directly to clients via `jsonify({"error": str(e)})`. Can expose internal file paths, database schema, stack trace fragments.
- **Impact**: Low — LAN-only tool, but violates defense-in-depth.
- **Suggestion**: Return generic error messages to clients; log detailed errors server-side.

### [Advisory] A35 — `os.path.exists` TOCTOU patterns
- **Lines**: 269, 389, 403, 407, 587, 611, 668, 2042, 2366, 2412
- **Issue**: Multiple `os.path.exists()` checks before file operations. Per Lesson #5.
- **Impact**: Low — race window is small.
- **Suggestion**: Use direct operation + exception handling.

### [Advisory] A36 — Session token comparison uses `!=` instead of `hmac.compare_digest`
- **Lines**: 153
- **Issue**: `session_token != calculate_session_token(lan_passphrase)` uses string comparison vulnerable to timing attacks. Also appears in `certificate_routes.py:80`.
- **Impact**: Low — comparing computed hashes, not raw secrets, and LAN-only. But violates best practice.
- **Suggestion**: Use `hmac.compare_digest(session_token, calculate_session_token(lan_passphrase))`.

### [Advisory] A37 — `MAX_ENCODSURES` typo
- **Lines**: 76
- **Issue**: Constant `MAX_ENCODSURES` is misspelled (missing 'L'). Used at lines 1099, 1132-1135.
- **Impact**: None — cosmetic, but creates confusion.
- **Suggestion**: Rename to `MAX_ENCLOSURES`.

### [Advisory] A38 — `_SATA_DEVICE_RE` allows partition names for SMART test endpoint
- **Lines**: 71
- **Issue**: `^sd[a-z]+[0-9]*\Z` allows partition names like `sda1`. SMART tests target whole disks, not partitions.
- **Impact**: Low — `smartctl` may handle gracefully, but the endpoint should reject partition names for test operations.
- **Suggestion**: Use a stricter regex for the SMART test endpoint, or validate that the device is a whole disk before proceeding.

---

## backend/routes/drive_routes.py

### [Critical] C15 — `ERASE_JOBS_LOCK` held during database queries in `get_drives`
- **Lines**: 75-117
- **Issue**: `get_drives` holds `ERASE_JOBS_LOCK` while performing database queries (`load_prior_visit(serial)` at line 97, `sqlite3.connect` at line 107). These I/O operations block all job operations during drive listing.
- **Impact**: Medium — drive listing is called frequently by frontend polling; lock contention can delay job starts and status updates.
- **Suggestion**: Snapshot `ERASE_JOBS` state inside lock, release lock, then perform database queries outside the lock.

### [Advisory] A39 — `bay` URL parameter not validated in zero-check endpoints
- **Lines**: 177, 208
- **Issue**: `bay` parameter from URL path passed directly to `manager.start_check(bay, ...)` and `manager.cancel_check(bay)` without validation.
- **Impact**: Low — used as dict key (no injection risk), but arbitrary strings could be passed.
- **Suggestion**: Validate `bay` against expected format (alphanumeric, reasonable length).

### [Advisory] A40 — `str(e)` in error responses
- **Lines**: 131, 205, 218
- **Issue**: Same pattern as admin_routes.py — exception messages returned to clients.
- **Impact**: Low — LAN-only tool.
- **Suggestion**: Return generic error messages; log details server-side.

