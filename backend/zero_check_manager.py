# Background zero-check manager for pre-wipe zero detection.
# Non-blocking, queue-based, with per-bay cancellation and SocketIO events.

import copy
from collections import deque
from datetime import datetime, timezone
import logging
import threading
import time

from crypto_verification import check_drive_already_zeroed
from common import load_policy
from disk_utils import validate_device_path


class ZeroCheckManager:
    """
    Manages background pre-wipe zero checks across all bays.

    Responsibilities:
    - FIFO queue for pending checks.
    - Concurrency limit via threading.Semaphore.
    - Per-bay cancellation tokens.
    - SocketIO event emission on state changes.
    - State lifecycle: cleared on drive removal; cancelled on wipe start.
    """

    def __init__(self, socketio=None, max_concurrency=8):
        self._socketio = socketio
        self._max_concurrency = max(1, max_concurrency)
        self._semaphore = threading.Semaphore(self._max_concurrency)
        self._semaphore_capacity = self._max_concurrency
        self._lock = threading.Lock()
        self._queue = deque()  # (bay, device)
        self._running = {}  # bay -> Thread
        self._cancel_events = {}  # bay -> threading.Event
        self._status = {}  # bay -> status dict
        self._generations = {}  # bay -> generation token
        self._generation_counter = 0
        self._auto_enqueue_delay_until = None  # monotonic timestamp; skip auto-enroll until this time

    def set_socketio(self, socketio):
        self._socketio = socketio

    def set_concurrency(self, max_concurrency):
        """Update the concurrency limit.

        If no checks are currently running, the active semaphore is recreated
        so the new limit takes effect immediately. If checks are running, the
        new value is stored and applies once all running checks complete.
        """
        with self._lock:
            self._max_concurrency = max(1, max_concurrency)
            if not self._running:
                self._semaphore = threading.Semaphore(self._max_concurrency)
                self._semaphore_capacity = self._max_concurrency

    # --- Internal state helpers ---

    def _base_status(self):
        return {
            "status": "not_started",
            "result": None,
            "is_zeroed": None,
            "started_at": None,
            "completed_at": None,
            "chunks_checked": 0,
            "bytes_checked": 0,
            "serial": None,
            "error": None,
            "details": None,
        }

    def _get_status(self, bay):
        with self._lock:
            return self._status.get(bay, self._base_status())

    def _set_status(self, bay, updates):
        with self._lock:
            status = self._status.get(bay, self._base_status())
            status.update(updates)
            self._status[bay] = status
        self._emit_update(bay, status)
        return status

    def _next_generation(self, bay):
        """Bump the generation token for a bay. Caller must hold self._lock."""
        self._generation_counter += 1
        self._generations[bay] = self._generation_counter
        return self._generation_counter

    def _clear_generation(self, bay):
        with self._lock:
            self._generations.pop(bay, None)

    def _is_current_generation(self, bay, token):
        with self._lock:
            return self._generations.get(bay) == token

    def _set_status_if_current(self, bay, token, updates):
        with self._lock:
            if self._generations.get(bay) != token:
                return None
            status = self._status.get(bay, self._base_status())
            status.update(updates)
            self._status[bay] = status
        self._emit_update(bay, status)
        return status

    def _emit_update(self, bay, status=None):
        try:
            if self._socketio:
                if status is None:
                    status = self._get_status(bay)
                self._socketio.emit("zero_check_updated", {"bay": bay, "zero_check": status})
        except Exception as e:
            logging.getLogger("app").warning(f"Failed to emit zero_check_updated for {bay}: {e}")

    # --- Public API ---

    def start_check(self, bay, device, serial=None):
        """Queue or start a zero-check for the given bay/device."""
        if not validate_device_path(device):
            return self._set_status(bay, {
                "status": "failed",
                "result": "failed",
                "is_zeroed": False,
                "error": "invalid_device_path",
                "details": "Device path validation failed",
            })

        with self._lock:
            # If already running, ignore duplicate
            if bay in self._running:
                return self._get_status(bay)
            # If already queued, ignore duplicate
            if any(q_bay == bay for q_bay, _, _ in self._queue):
                return self._get_status(bay)
            self._queue.append((bay, device, serial))
            token = self._next_generation(bay)

        # Use generation-checked setter so a concurrent cancel_check cannot
        # leave the status stuck at "queued" after the queue entry is removed.
        self._set_status_if_current(bay, token, {"status": "queued", "serial": serial})
        self._process_queue()
        return self._get_status(bay)

    def cancel_check(self, bay):
        """Cancel a running or queued check. Returns True if anything was cancelled."""
        removed_from_queue = False
        had_running = False
        token = None
        with self._lock:
            # Don't overwrite terminal statuses (Advisory 9)
            current_status = self._status.get(bay, {})
            if current_status.get("status") in ("completed", "failed"):
                return {"ok": True, "cancelled": False}

            original_len = len(self._queue)
            self._queue = deque((b, d, s) for b, d, s in self._queue if b != bay)
            removed_from_queue = len(self._queue) < original_len
            cancel_event = self._cancel_events.get(bay)
            thread = self._running.get(bay)
            had_running = thread is not None
            if cancel_event or thread or bay in self._status:
                token = self._next_generation(bay)

        if cancel_event:
            cancel_event.set()
        if thread:
            thread.join(timeout=5)

        if token is not None:
            # Use generation-checked setter so a concurrent start_check cannot
            # have its "queued" status overwritten by this "cancelled" write.
            self._set_status_if_current(bay, token, {
                "status": "cancelled",
                "result": "cancelled",
                "is_zeroed": None,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "error": None,
            })
            return {"ok": True, "cancelled": True}

        return {"ok": True, "cancelled": False}

    def clear_state(self, bay):
        """Remove all zero-check state for a bay (used when drive is removed)."""
        with self._lock:
            self._status.pop(bay, None)
            original_len = len(self._queue)
            self._queue = deque((b, d, s) for b, d, s in self._queue if b != bay)
            cancel_event = self._cancel_events.get(bay)
            thread = self._running.get(bay)
            self._next_generation(bay)
        if cancel_event:
            cancel_event.set()
        if thread:
            thread.join(timeout=2)
        self._emit_update(bay)

    def get_status(self, bay):
        return self._get_status(bay)

    def get_all_status(self):
        with self._lock:
            return {bay: copy.deepcopy(status) for bay, status in self._status.items()}

    def on_drive_removed(self, bay):
        self.clear_state(bay)

    def on_wipe_starting(self, bay):
        self.cancel_check(bay)

    def delay_auto_enqueue(self, seconds):
        """Delay auto-enrollment for the given number of seconds from now.

        Called on startup to give drives time to settle after restart (e.g.,
        flushing interrupted DD writes). All discovery cycles within the
        delay window will skip auto-enrolling zero checks.
        """
        with self._lock:
            self._auto_enqueue_delay_until = time.monotonic() + seconds

    def is_auto_enqueue_delayed(self):
        """Check if auto-enrollment is still in the startup delay window."""
        with self._lock:
            if self._auto_enqueue_delay_until is None:
                return False
            if time.monotonic() >= self._auto_enqueue_delay_until:
                self._auto_enqueue_delay_until = None
                return False
            return True

    # --- Queue/scheduling internals ---

    def _process_queue(self):
        while True:
            with self._lock:
                if not self._queue:
                    break
                if not self._semaphore.acquire(blocking=False):
                    break
                bay, device, serial = self._queue.popleft()
                if bay in self._running:
                    self._semaphore.release()
                    continue
                token = self._generations.get(bay)
                if token is None:
                    # Stale entry (bay was cleared while queued)
                    self._semaphore.release()
                    continue
                cancel_event = threading.Event()
                self._cancel_events[bay] = cancel_event
                # Register thread in _running before starting it to prevent
                # a fast-completing worker from leaving a stale entry (Root Problem 13).
                thread = threading.Thread(
                    target=self._worker,
                    args=(bay, device, serial, cancel_event, token),
                    daemon=True,
                    name=f"zero-check-{bay}",
                )
                self._running[bay] = thread
            # Use generation-checked setter so a concurrent cancel/clear cannot
            # have its status overwritten by this "running" write (Root Problem 12).
            self._set_status_if_current(bay, token, {
                "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
            })
            thread.start()

    def _worker(self, bay, device, serial, cancel_event, generation_token):
        try:
            if not self._is_current_generation(bay, generation_token):
                return

            if cancel_event.is_set():
                self._set_status_if_current(bay, generation_token, {
                    "status": "cancelled",
                    "result": "cancelled",
                    "is_zeroed": None,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                })
                return

            try:
                policy = load_policy()
                timeout_seconds = policy.get("zero_check_timeout_seconds", 60)
            except Exception:
                timeout_seconds = 60

            result = check_drive_already_zeroed(
                device, cancel_event=cancel_event, timeout_seconds=timeout_seconds
            )
            self._set_status_if_current(bay, generation_token, {
                "status": "completed" if result.get("ok") else "failed",
                "result": result.get("result"),
                "is_zeroed": result.get("is_zeroed"),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "chunks_checked": result.get("chunks_checked", 0),
                "bytes_checked": result.get("bytes_checked", 0),
                "serial": serial,
                "error": result.get("error"),
                "details": result.get("details"),
            })
        except Exception as e:
            logging.getLogger("app").warning(f"Zero-check worker unhandled exception for {bay}: {e}")
            self._set_status_if_current(bay, generation_token, {
                "status": "failed",
                "result": "failed",
                "is_zeroed": False,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "error": "worker_exception",
                "details": str(e),
            })
        finally:
            with self._lock:
                self._running.pop(bay, None)
                self._cancel_events.pop(bay, None)
                # Recreate semaphore if all workers have drained and the
                # capacity was changed by set_concurrency while checks were running.
                if not self._running and self._semaphore_capacity != self._max_concurrency:
                    self._semaphore = threading.Semaphore(self._max_concurrency)
                    self._semaphore_capacity = self._max_concurrency
            self._semaphore.release()
            self._process_queue()


# Module-level singleton
_manager = None
_manager_lock = threading.Lock()


def get_manager(socketio=None, max_concurrency=8):
    """Return the module-level ZeroCheckManager singleton."""
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = ZeroCheckManager(socketio=socketio, max_concurrency=max_concurrency)
        elif socketio is not None:
            _manager.set_socketio(socketio)
    return _manager


def reset_manager():
    """Reset the singleton (useful for tests)."""
    global _manager
    with _manager_lock:
        _manager = None
