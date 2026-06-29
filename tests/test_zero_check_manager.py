# Tests for backend/zero_check_manager.py
import sys
import os
import time
import threading
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from zero_check_manager import ZeroCheckManager, get_manager, reset_manager


class TestZeroCheckManager:
    def test_start_check_rejects_invalid_device(self):
        reset_manager()
        manager = ZeroCheckManager(socketio=None, max_concurrency=2)
        status = manager.start_check("bay1", "/tmp/not-a-valid-device")
        assert status["status"] == "failed"
        assert status["error"] == "invalid_device_path"

    def test_start_check_queues_and_runs(self):
        reset_manager()
        emitted = []

        def fake_emit(event, payload):
            emitted.append((event, payload))

        manager = ZeroCheckManager(socketio=None, max_concurrency=2)
        manager._emit_update = lambda bay, status=None: fake_emit("zero_check_updated", {"bay": bay, "zero_check": manager.get_status(bay)})

        with patch('zero_check_manager.check_drive_already_zeroed') as mock_check:
            mock_check.return_value = {
                "ok": True, "result": "zeroed", "is_zeroed": True,
                "chunks_checked": 10, "bytes_checked": 1024, "error": None, "details": {}
            }
            status = manager.start_check("bay1", "/dev/sda")
            # Mocked check runs fast; status may already be completed by the time we return
            assert status["status"] in ("queued", "running", "completed")
            # Wait for worker to complete
            for _ in range(50):
                if manager.get_status("bay1")["status"] == "completed":
                    break
                time.sleep(0.05)

        final = manager.get_status("bay1")
        assert final["status"] == "completed"
        assert final["result"] == "zeroed"
        assert final["is_zeroed"] is True
        assert any(event == "zero_check_updated" for event, _ in emitted)

    def test_cancel_check_removes_from_queue(self):
        reset_manager()
        manager = ZeroCheckManager(socketio=None, max_concurrency=1)
        running_event = threading.Event()

        def slow_check(device, cancel_event=None, timeout_seconds=60):
            running_event.set()
            for _ in range(200):
                if cancel_event and cancel_event.is_set():
                    return {"ok": False, "result": "cancelled", "is_zeroed": None, "error": "cancelled"}
                time.sleep(0.01)
            return {"ok": True, "result": "zeroed", "is_zeroed": True}

        with patch('zero_check_manager.check_drive_already_zeroed', side_effect=slow_check):
            manager.start_check("bay1", "/dev/sda")
            # Wait until bay1 is running
            for _ in range(50):
                if running_event.is_set():
                    break
                time.sleep(0.05)
            # Start a second check; it should be queued
            manager.start_check("bay2", "/dev/sdb")
            assert manager.get_status("bay2")["status"] == "queued"
            result = manager.cancel_check("bay2")
            assert result["cancelled"] is True
            assert manager.get_status("bay2")["status"] == "cancelled"
            manager.cancel_check("bay1")
            for _ in range(50):
                if manager.get_status("bay1")["status"] == "cancelled":
                    break
                time.sleep(0.05)

    def test_cancel_check_stops_running(self):
        reset_manager()
        manager = ZeroCheckManager(socketio=None, max_concurrency=1)
        captured_event = [None]

        def slow_check(device, cancel_event=None, timeout_seconds=60):
            captured_event[0] = cancel_event
            for _ in range(100):
                if cancel_event and cancel_event.is_set():
                    return {"ok": False, "result": "cancelled", "is_zeroed": None, "error": "cancelled"}
                time.sleep(0.01)
            return {"ok": True, "result": "zeroed", "is_zeroed": True}

        with patch('zero_check_manager.check_drive_already_zeroed', side_effect=slow_check):
            manager.start_check("bay1", "/dev/sda")
            # Wait until running
            for _ in range(50):
                if manager.get_status("bay1")["status"] == "running":
                    break
                time.sleep(0.05)
            assert manager.get_status("bay1")["status"] == "running"
            manager.cancel_check("bay1")
            assert captured_event[0] is not None
            assert captured_event[0].is_set()
            # Wait for worker to finish
            for _ in range(50):
                if manager.get_status("bay1")["status"] == "cancelled":
                    break
                time.sleep(0.05)
            assert manager.get_status("bay1")["status"] == "cancelled"

    def test_on_drive_removed_clears_state(self):
        reset_manager()
        manager = ZeroCheckManager(socketio=None, max_concurrency=2)
        with patch('zero_check_manager.check_drive_already_zeroed') as mock_check:
            mock_check.return_value = {"ok": True, "result": "zeroed", "is_zeroed": True, "chunks_checked": 1, "bytes_checked": 1, "error": None, "details": {}}
            manager.start_check("bay1", "/dev/sda")
            for _ in range(50):
                if manager.get_status("bay1")["status"] == "completed":
                    break
                time.sleep(0.05)
            assert manager.get_status("bay1")["status"] == "completed"
            manager.on_drive_removed("bay1")
            assert manager.get_status("bay1")["status"] == "not_started"
            assert manager.get_all_status() == {}

    def test_on_wipe_starting_cancels_check(self):
        reset_manager()
        manager = ZeroCheckManager(socketio=None, max_concurrency=2)
        running_event = threading.Event()

        def slow_check(device, cancel_event=None, timeout_seconds=60):
            running_event.set()
            for _ in range(100):
                if cancel_event and cancel_event.is_set():
                    return {"ok": False, "result": "cancelled", "is_zeroed": None, "error": "cancelled"}
                time.sleep(0.01)
            return {"ok": True, "result": "zeroed", "is_zeroed": True}

        with patch('zero_check_manager.check_drive_already_zeroed', side_effect=slow_check):
            manager.start_check("bay1", "/dev/sda")
            for _ in range(50):
                if running_event.is_set():
                    break
                time.sleep(0.05)
            manager.on_wipe_starting("bay1")
            for _ in range(50):
                if manager.get_status("bay1")["status"] == "cancelled":
                    break
                time.sleep(0.05)
            assert manager.get_status("bay1")["status"] == "cancelled"

    def test_clear_state_prevents_stale_worker_from_resurrecting(self):
        reset_manager()
        manager = ZeroCheckManager(socketio=None, max_concurrency=2)

        def slow_check(device, cancel_event=None, timeout_seconds=60):
            for _ in range(50):
                if cancel_event and cancel_event.is_set():
                    return {"ok": False, "result": "cancelled", "is_zeroed": None, "error": "cancelled"}
                time.sleep(0.01)
            return {"ok": True, "result": "zeroed", "is_zeroed": True, "chunks_checked": 1, "bytes_checked": 1, "error": None, "details": {}}

        with patch('zero_check_manager.check_drive_already_zeroed', side_effect=slow_check):
            manager.start_check("bay1", "/dev/sda")
            for _ in range(50):
                if manager.get_status("bay1")["status"] == "running":
                    break
                time.sleep(0.05)
            assert manager.get_status("bay1")["status"] == "running"
            manager.clear_state("bay1")
            for _ in range(100):
                if manager.get_status("bay1")["status"] == "not_started":
                    break
                time.sleep(0.05)
            assert manager.get_status("bay1")["status"] == "not_started"
            assert manager.get_all_status() == {}

    def test_set_concurrency_resizes_when_idle(self):
        reset_manager()
        manager = ZeroCheckManager(socketio=None, max_concurrency=2)
        manager.set_concurrency(4)
        # No running checks, so semaphore should be recreated with 4 slots
        assert manager._max_concurrency == 4
        # Try to start 4 checks without blocking
        with patch('zero_check_manager.check_drive_already_zeroed') as mock_check:
            mock_check.return_value = {"ok": True, "result": "zeroed", "is_zeroed": True, "chunks_checked": 1, "bytes_checked": 1, "error": None, "details": {}}
            for i in range(4):
                manager.start_check(f"bay{i}", f"/dev/sd{chr(ord('a') + i)}")
            for _ in range(50):
                if all(manager.get_status(f"bay{i}")["status"] == "completed" for i in range(4)):
                    break
                time.sleep(0.05)
            assert all(manager.get_status(f"bay{i}")["status"] == "completed" for i in range(4))

    def test_get_manager_singleton(self):
        reset_manager()
        m1 = get_manager(socketio=None, max_concurrency=2)
        m2 = get_manager()
        assert m1 is m2

    def test_worker_exception_sets_failed_status(self):
        """Test that an unhandled exception in the worker sets status to 'failed'."""
        reset_manager()
        manager = ZeroCheckManager(socketio=None, max_concurrency=2)

        with patch('zero_check_manager.check_drive_already_zeroed', side_effect=RuntimeError("unexpected crash")):
            manager.start_check("bay1", "/dev/sda")
            for _ in range(50):
                status = manager.get_status("bay1")["status"]
                if status in ("failed", "completed"):
                    break
                time.sleep(0.05)
            final = manager.get_status("bay1")
            assert final["status"] == "failed"
            assert final["error"] == "worker_exception"
            assert "unexpected crash" in str(final["details"])

    def test_recheck_after_completion_succeeds(self):
        """Test that start_check allows re-checking after a previous completion."""
        reset_manager()
        manager = ZeroCheckManager(socketio=None, max_concurrency=2)

        with patch('zero_check_manager.check_drive_already_zeroed') as mock_check:
            mock_check.return_value = {
                "ok": True, "result": "zeroed", "is_zeroed": True,
                "chunks_checked": 10, "bytes_checked": 1024, "error": None, "details": {}
            }
            manager.start_check("bay1", "/dev/sda")
            for _ in range(50):
                if manager.get_status("bay1")["status"] == "completed":
                    break
                time.sleep(0.05)
            assert manager.get_status("bay1")["status"] == "completed"

            # Re-check should succeed (not be blocked by completed status)
            manager.start_check("bay1", "/dev/sda")
            for _ in range(50):
                if manager.get_status("bay1")["status"] == "completed":
                    break
                time.sleep(0.05)
            assert manager.get_status("bay1")["status"] == "completed"
            assert mock_check.call_count == 2

    def test_cancel_check_returns_false_when_nothing_to_cancel(self):
        """Test that cancel_check returns cancelled:False when no check exists."""
        reset_manager()
        manager = ZeroCheckManager(socketio=None, max_concurrency=2)
        result = manager.cancel_check("bay_nonexistent")
        assert result["cancelled"] is False

    def test_cancel_check_does_not_overwrite_completed(self):
        """Test that cancel_check does not overwrite a completed status (Advisory 9)."""
        reset_manager()
        manager = ZeroCheckManager(socketio=None, max_concurrency=2)
        with patch('zero_check_manager.check_drive_already_zeroed') as mock_check:
            mock_check.return_value = {"ok": True, "result": "zeroed", "is_zeroed": True, "chunks_checked": 1, "bytes_checked": 1, "error": None, "details": {}}
            manager.start_check("bay1", "/dev/sda")
            for _ in range(50):
                if manager.get_status("bay1")["status"] == "completed":
                    break
                time.sleep(0.05)
            assert manager.get_status("bay1")["status"] == "completed"
            # Cancel should be a no-op for terminal statuses
            result = manager.cancel_check("bay1")
            assert result["cancelled"] is False
            assert manager.get_status("bay1")["status"] == "completed"

    def test_cancel_check_does_not_overwrite_failed(self):
        """Test that cancel_check does not overwrite a failed status (Advisory 9)."""
        reset_manager()
        manager = ZeroCheckManager(socketio=None, max_concurrency=2)
        with patch('zero_check_manager.check_drive_already_zeroed', side_effect=RuntimeError("boom")):
            manager.start_check("bay1", "/dev/sda")
            for _ in range(50):
                if manager.get_status("bay1")["status"] == "failed":
                    break
                time.sleep(0.05)
            assert manager.get_status("bay1")["status"] == "failed"
            result = manager.cancel_check("bay1")
            assert result["cancelled"] is False
            assert manager.get_status("bay1")["status"] == "failed"

    def test_cancel_check_does_not_overwrite_concurrent_start_check(self):
        """Test that cancel_check's generation-checked write doesn't overwrite a concurrent start_check (Root Problem 11)."""
        reset_manager()
        manager = ZeroCheckManager(socketio=None, max_concurrency=1)
        block_event = threading.Event()

        def slow_check(device, cancel_event=None, timeout_seconds=60):
            block_event.wait(timeout=5)
            if cancel_event and cancel_event.is_set():
                return {"ok": False, "result": "cancelled", "is_zeroed": None, "error": "cancelled"}
            return {"ok": True, "result": "zeroed", "is_zeroed": True}

        with patch('zero_check_manager.check_drive_already_zeroed', side_effect=slow_check):
            manager.start_check("bay1", "/dev/sda")
            for _ in range(50):
                if manager.get_status("bay1")["status"] == "running":
                    break
                time.sleep(0.05)
            assert manager.get_status("bay1")["status"] == "running"

            # Cancel the running check; the worker will return after block_event is set
            cancel_thread = threading.Thread(target=manager.cancel_check, args=("bay1",), daemon=True)
            cancel_thread.start()
            # Wait for cancel to set the cancel_event (generation bumped)
            for _ in range(50):
                ce = manager._cancel_events.get("bay1")
                if ce is not None and ce.is_set():
                    break
                time.sleep(0.01)
            # Let the worker finish
            block_event.set()
            cancel_thread.join(timeout=5)

            # Now start a new check — it should succeed, not be blocked by stale state
            with patch('zero_check_manager.check_drive_already_zeroed') as mock_check2:
                mock_check2.return_value = {"ok": True, "result": "zeroed", "is_zeroed": True, "chunks_checked": 1, "bytes_checked": 1, "error": None, "details": {}}
                manager.start_check("bay1", "/dev/sda")
                for _ in range(50):
                    if manager.get_status("bay1")["status"] == "completed":
                        break
                    time.sleep(0.05)
                assert manager.get_status("bay1")["status"] == "completed"

    def test_process_queue_does_not_overwrite_concurrent_cancel(self):
        """Test that _process_queue's generation-checked 'running' write doesn't overwrite a concurrent cancel (Root Problem 12).

        Uses a patched _set_status_if_current that blocks at the critical point (after lock release,
        before status write) so a concurrent cancel_check can bump the generation first.
        """
        reset_manager()
        manager = ZeroCheckManager(socketio=None, max_concurrency=1)
        block_before_running = threading.Event()
        running_write_attempted = threading.Event()
        cancel_done = threading.Event()

        original_set_status_if_current = manager._set_status_if_current

        def blocking_set_status_if_current(bay, token, updates):
            if updates.get("status") == "running":
                running_write_attempted.set()
                block_before_running.wait(timeout=5)
            return original_set_status_if_current(bay, token, updates)

        def slow_check(device, cancel_event=None, timeout_seconds=60):
            cancel_event.wait(timeout=5)
            if cancel_event and cancel_event.is_set():
                return {"ok": False, "result": "cancelled", "is_zeroed": None, "error": "cancelled"}
            return {"ok": True, "result": "zeroed", "is_zeroed": True}

        manager._set_status_if_current = blocking_set_status_if_current

        with patch('zero_check_manager.check_drive_already_zeroed', side_effect=slow_check):
            manager.start_check("bay1", "/dev/sda")
            # Wait for _process_queue to reach the blocking point before writing "running"
            for _ in range(50):
                if running_write_attempted.is_set():
                    break
                time.sleep(0.01)
            assert running_write_attempted.is_set(), "_process_queue did not reach the running status write"

            # While _process_queue is blocked, cancel the check — this bumps the generation
            cancel_thread = threading.Thread(target=manager.cancel_check, args=("bay1",), daemon=True)
            cancel_thread.start()
            # Wait for cancel to set the cancel_event
            for _ in range(50):
                ce = manager._cancel_events.get("bay1")
                if ce is not None and ce.is_set():
                    break
                time.sleep(0.01)
            cancel_done.set()

            # Now let _process_queue proceed with the generation-checked write
            block_before_running.set()
            cancel_thread.join(timeout=5)

            # Wait for the worker to finish
            for _ in range(50):
                if manager.get_status("bay1")["status"] in ("cancelled", "completed"):
                    break
                time.sleep(0.05)
            # The status should be "cancelled" (from cancel_check), NOT "running"
            # (from _process_queue), because the generation-checked write was a no-op.
            assert manager.get_status("bay1")["status"] != "running"
            assert manager.get_status("bay1")["status"] == "cancelled"

    def test_no_stale_running_entry_after_fast_worker(self):
        """Test that a fast-completing worker doesn't leave a stale _running entry (Root Problem 13)."""
        reset_manager()
        manager = ZeroCheckManager(socketio=None, max_concurrency=2)

        with patch('zero_check_manager.check_drive_already_zeroed') as mock_check:
            mock_check.return_value = {"ok": True, "result": "zeroed", "is_zeroed": True, "chunks_checked": 1, "bytes_checked": 1, "error": None, "details": {}}
            manager.start_check("bay1", "/dev/sda")
            for _ in range(50):
                if manager.get_status("bay1")["status"] == "completed":
                    break
                time.sleep(0.05)
            assert manager.get_status("bay1")["status"] == "completed"
            # _running should not have a stale entry
            with manager._lock:
                assert "bay1" not in manager._running
            # Should be able to start a new check without being blocked
            manager.start_check("bay1", "/dev/sda")
            for _ in range(50):
                if manager.get_status("bay1")["status"] == "completed":
                    break
                time.sleep(0.05)
            assert manager.get_status("bay1")["status"] == "completed"
            assert mock_check.call_count == 2

    def test_serial_is_stored_in_status(self):
        """Test that start_check accepts and stores the drive serial in the status."""
        reset_manager()
        manager = ZeroCheckManager(socketio=None, max_concurrency=2)

        with patch('zero_check_manager.check_drive_already_zeroed') as mock_check:
            mock_check.return_value = {"ok": True, "result": "zeroed", "is_zeroed": True, "chunks_checked": 1, "bytes_checked": 1, "error": None, "details": {}}
            manager.start_check("bay1", "/dev/sda", serial="ABC123")
            for _ in range(50):
                if manager.get_status("bay1")["status"] == "completed":
                    break
                time.sleep(0.05)
            final = manager.get_status("bay1")
            assert final["status"] == "completed"
            assert final["serial"] == "ABC123"

    def test_clear_state_with_queued_entry(self):
        """Test that clear_state does not crash when a bay has a queued entry (Critical 1).

        The queue stores 3-tuples (bay, device, serial). clear_state must unpack
        all three fields; unpacking only 2 raises ValueError.
        """
        reset_manager()
        manager = ZeroCheckManager(socketio=None, max_concurrency=1)
        running_event = threading.Event()

        def slow_check(device, cancel_event=None, timeout_seconds=60):
            running_event.set()
            for _ in range(200):
                if cancel_event and cancel_event.is_set():
                    return {"ok": False, "result": "cancelled", "is_zeroed": None, "error": "cancelled"}
                time.sleep(0.01)
            return {"ok": True, "result": "zeroed", "is_zeroed": True}

        with patch('zero_check_manager.check_drive_already_zeroed', side_effect=slow_check):
            # Start bay1 (fills the only concurrency slot)
            manager.start_check("bay1", "/dev/sda", serial="S1")
            for _ in range(50):
                if running_event.is_set():
                    break
                time.sleep(0.05)

            # Queue bay2 (will remain queued because max_concurrency=1)
            manager.start_check("bay2", "/dev/sdb", serial="S2")
            assert manager.get_status("bay2")["status"] == "queued"

            # clear_state on the queued bay must not raise ValueError
            manager.clear_state("bay2")
            assert manager.get_status("bay2")["status"] == "not_started"
            assert "bay2" not in manager.get_all_status()

            # Clean up bay1
            manager.cancel_check("bay1")
            for _ in range(50):
                if manager.get_status("bay1")["status"] == "cancelled":
                    break
                time.sleep(0.05)

