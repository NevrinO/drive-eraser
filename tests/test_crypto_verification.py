# Tests for crypto_verification.py
import pytest
import sys
import os
from unittest.mock import patch, MagicMock, Mock
import hashlib
import threading
import time
import io

# Add backend to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))


@pytest.fixture(autouse=True)
def reset_interrupted_flag():
    """Reset the global interruption flag before each test to prevent state pollution."""
    import crypto_verification
    crypto_verification._verification_interrupted = False
    yield


class TestResolveVerifyCommandPath:
    """Test command path resolution."""

    def test_delegates_to_disk_utils(self):
        """Test that resolve_verify_command_path delegates to disk_utils."""
        with patch('disk_utils.get_command_path', return_value='/usr/bin/dd'):
            from crypto_verification import resolve_verify_command_path
            result = resolve_verify_command_path("dd")
            assert result == '/usr/bin/dd'

    def test_none_when_command_not_found(self):
        """Test that None is returned when command not found."""
        with patch('disk_utils.get_command_path', return_value=None):
            from crypto_verification import resolve_verify_command_path
            result = resolve_verify_command_path("dd")
            assert result is None


class TestSignalHandling:
    """Test signal interruption handling."""

    def test_handle_verification_signal_sets_flag(self):
        """Test that signal handler sets interrupted flag."""
        from crypto_verification import _handle_verification_signal, _check_interrupted, _verification_interrupted
        # Reset flag
        import crypto_verification
        crypto_verification._verification_interrupted = False
        
        _handle_verification_signal(15, None)
        assert _check_interrupted() is True

    def test_check_interrupted_returns_flag(self):
        """Test that check_interrupted returns the flag state."""
        from crypto_verification import _check_interrupted
        import crypto_verification
        crypto_verification._verification_interrupted = False
        assert _check_interrupted() is False
        
        crypto_verification._verification_interrupted = True
        assert _check_interrupted() is True


class TestRunDdReadWithRetry:
    """Test dd read retry helper function (Feature C)."""

    def test_dd_read_success_on_first_attempt(self):
        """Test that successful dd read on first attempt works."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=b'\x00' * 1024)
            from crypto_verification import _run_dd_read_with_retry
            result = _run_dd_read_with_retry('/usr/bin/dd', '/dev/sda', 1024, 0, 1, retries=2, retry_delay=1)
            assert result["data"] == b'\x00' * 1024
            assert result["error"] is None
            assert mock_run.call_count == 1

    def test_dd_read_retry_success_after_transient_failure(self):
        """Test that dd read retry succeeds after transient failure."""
        with patch('subprocess.run') as mock_run:
            with patch('time.sleep') as mock_sleep:
                mock_run.side_effect = [
                    MagicMock(returncode=1, stderr=b"dd read error"),
                    MagicMock(returncode=1, stderr=b"dd read error"),
                    MagicMock(returncode=0, stdout=b'\x00' * 1024),
                ]
                from crypto_verification import _run_dd_read_with_retry
                result = _run_dd_read_with_retry('/usr/bin/dd', '/dev/sda', 1024, 0, 1, retries=2, retry_delay=1)
                assert result["data"] == b'\x00' * 1024
                assert result["error"] is None
                assert mock_run.call_count == 3
                assert mock_sleep.call_count == 2

    def test_dd_read_retry_exhaustion_detached_drive(self):
        """Test that dd read retry exhaustion with detached drive returns correct error."""
        with patch('subprocess.run') as mock_run:
            with patch('time.sleep') as mock_sleep:
                mock_run.side_effect = [
                    MagicMock(returncode=1, stderr=b"No such device"),
                    MagicMock(returncode=1, stderr=b"No such device"),
                    MagicMock(returncode=1, stderr=b"No such device"),
                ]
                from crypto_verification import _run_dd_read_with_retry
                result = _run_dd_read_with_retry('/usr/bin/dd', '/dev/sda', 1024, 0, 1, retries=2, retry_delay=1)
                assert result["data"] is None
                assert result["error"] == "drive_detached_post_wipe"
                assert mock_run.call_count == 3
                assert mock_sleep.call_count == 2

    def test_dd_read_retry_exhaustion_generic_failure(self):
        """Test that dd read retry exhaustion with generic failure returns correct error."""
        with patch('subprocess.run') as mock_run:
            with patch('time.sleep') as mock_sleep:
                mock_run.side_effect = [
                    MagicMock(returncode=1, stderr=b"generic read error"),
                    MagicMock(returncode=1, stderr=b"generic read error"),
                    MagicMock(returncode=1, stderr=b"generic read error"),
                ]
                from crypto_verification import _run_dd_read_with_retry
                result = _run_dd_read_with_retry('/usr/bin/dd', '/dev/sda', 1024, 0, 1, retries=2, retry_delay=1)
                assert result["data"] is None
                assert result["error"] == "secondary_sampled_read_failed"
                assert mock_run.call_count == 3
                assert mock_sleep.call_count == 2

    def test_dd_read_retry_transport_endpoint_error(self):
        """Test that transport endpoint error is detected as detached drive."""
        with patch('subprocess.run') as mock_run:
            with patch('time.sleep') as mock_sleep:
                mock_run.side_effect = [
                    MagicMock(returncode=1, stderr=b"Transport endpoint is not connected"),
                ]
                from crypto_verification import _run_dd_read_with_retry
                result = _run_dd_read_with_retry('/usr/bin/dd', '/dev/sda', 1024, 0, 1, retries=0, retry_delay=1)
                assert result["data"] is None
                assert result["error"] == "drive_detached_post_wipe"


class TestVerifySampledZeroCheck:
    """Test sampled zero check verification."""

    def test_invalid_device_path(self):
        """Test that invalid device path is rejected."""
        with patch('crypto_verification.validate_device_path', return_value=False):
            from crypto_verification import verify_sampled_zero_check
            result = verify_sampled_zero_check("/dev/invalid")
            assert result["ok"] is False
            assert result["error"] == "invalid_device_path"

    def test_dd_not_available(self):
        """Test that missing dd command is handled."""
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value=None):
                from crypto_verification import verify_sampled_zero_check
                result = verify_sampled_zero_check("/dev/sda")
                assert result["ok"] is False
                assert result["error"] == "dd_not_available_for_zero_check"

    def test_blockdev_capacity_check_fails(self):
        """Test that blockdev failure is handled."""
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value='/usr/bin/dd'):
                with patch('crypto_verification.get_device_lock') as mock_lock:
                    mock_lock.return_value.__enter__ = Mock(return_value=None)
                    mock_lock.return_value.__exit__ = Mock(return_value=None)
                    with patch('subprocess.run') as mock_run:
                        mock_run.return_value = MagicMock(returncode=1, stderr="blockdev error", stdout="")
                        from crypto_verification import verify_sampled_zero_check
                        result = verify_sampled_zero_check("/dev/sda")
                        assert result["ok"] is False
                        assert result["error"] == "secondary_capacity_check_failed"

    def test_interrupted_before_capacity_check(self):
        """Test that interruption before capacity check is handled."""
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value='/usr/bin/dd'):
                with patch('crypto_verification.get_device_lock') as mock_lock:
                    mock_lock.return_value.__enter__ = Mock(return_value=None)
                    mock_lock.return_value.__exit__ = Mock(return_value=None)
                    with patch('crypto_verification._check_interrupted', return_value=True):
                        from crypto_verification import verify_sampled_zero_check
                        result = verify_sampled_zero_check("/dev/sda")
                        assert result["ok"] is False
                        assert result["error"] == "verification_interrupted"

    def test_successful_zero_check_all_zeros(self):
        """Test successful verification when all data is zero."""
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value='/usr/bin/dd'):
                with patch('crypto_verification.get_device_lock') as mock_lock:
                    mock_lock.return_value.__enter__ = Mock(return_value=None)
                    mock_lock.return_value.__exit__ = Mock(return_value=None)
                    with patch('subprocess.run') as mock_run:
                        # blockdev returns capacity, then multiple dd reads for offsets
                        mock_run.side_effect = [
                            MagicMock(returncode=0, stdout="1073741824"),  # 1GB
                            MagicMock(returncode=0, stdout=b'\x00' * (32*1024*1024)),  # First 32MB
                            MagicMock(returncode=0, stdout=b'\x00' * (32*1024*1024)),  # Additional offset
                            MagicMock(returncode=0, stdout=b'\x00' * (32*1024*1024)),  # Additional offset
                            MagicMock(returncode=0, stdout=b'\x00' * (32*1024*1024)),  # Additional offset
                        ]
                        from crypto_verification import verify_sampled_zero_check
                        result = verify_sampled_zero_check("/dev/sda", sample_ratio=0.01, max_read_bytes=100*1024*1024)
                        assert result["ok"] is True
                        assert result["status"] == "verified"
                        assert "total_verified_bytes" in result["details"]

    def test_non_zero_data_detected(self):
        """Test that non-zero data is detected."""
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value='/usr/bin/dd'):
                with patch('crypto_verification.get_device_lock') as mock_lock:
                    mock_lock.return_value.__enter__ = Mock(return_value=None)
                    mock_lock.return_value.__exit__ = Mock(return_value=None)
                    with patch('subprocess.run') as mock_run:
                        # blockdev returns capacity, dd returns non-zero data
                        mock_run.side_effect = [
                            MagicMock(returncode=0, stdout="1073741824"),
                            MagicMock(returncode=0, stdout=b'\x00\x01' + b'\x00' * (32*1024*1024 - 2)),
                        ]
                        from crypto_verification import verify_sampled_zero_check
                        result = verify_sampled_zero_check("/dev/sda", sample_ratio=0.01, max_read_bytes=100*1024*1024)
                        assert result["ok"] is False
                        assert result["error"] == "secondary_zero_check_failed_nonzero_data_detected"
                        assert "offset" in result["details"]

    def test_dd_read_failure(self):
        """Test that dd read failure is handled with retry logic (retries=0 configuration)."""
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value='/usr/bin/dd'):
                with patch('crypto_verification.get_device_lock') as mock_lock:
                    mock_lock.return_value.__enter__ = Mock(return_value=None)
                    mock_lock.return_value.__exit__ = Mock(return_value=None)
                    with patch('crypto_verification.load_policy', return_value={"blockdev_post_wipe_retries": 0, "blockdev_post_wipe_retry_delay": 0}):
                        with patch('subprocess.run') as mock_run:
                            mock_run.side_effect = [
                                MagicMock(returncode=0, stdout="1073741824"),
                                MagicMock(returncode=1, stderr=b"dd read error"),
                            ]
                            from crypto_verification import verify_sampled_zero_check
                            result = verify_sampled_zero_check("/dev/sda")
                            assert result["ok"] is False
                            assert result["error"] == "secondary_sampled_read_failed"

    def test_interrupted_during_reads(self):
        """Test that interruption during reads is handled."""
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value='/usr/bin/dd'):
                with patch('crypto_verification.get_device_lock') as mock_lock:
                    mock_lock.return_value.__enter__ = Mock(return_value=None)
                    mock_lock.return_value.__exit__ = Mock(return_value=None)
                    with patch('subprocess.run') as mock_run:
                        with patch('crypto_verification._check_interrupted') as mock_check:
                            mock_check.side_effect = [False, True]  # First check passes, second fails
                            mock_run.side_effect = [
                                MagicMock(returncode=0, stdout="1073741824"),
                                MagicMock(returncode=0, stdout=b'\x00' * (32*1024*1024)),
                            ]
                            from crypto_verification import verify_sampled_zero_check
                            result = verify_sampled_zero_check("/dev/sda")
                            assert result["ok"] is False
                            assert result["error"] == "verification_interrupted"

    def test_small_drive_capacity(self):
        """Test handling of very small drives."""
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value='/usr/bin/dd'):
                with patch('crypto_verification.get_device_lock') as mock_lock:
                    mock_lock.return_value.__enter__ = Mock(return_value=None)
                    mock_lock.return_value.__exit__ = Mock(return_value=None)
                    with patch('subprocess.run') as mock_run:
                        # Very small drive (10MB)
                        mock_run.side_effect = [
                            MagicMock(returncode=0, stdout="10485760"),
                            MagicMock(returncode=0, stdout=b'\x00' * 10485760),
                        ]
                        from crypto_verification import verify_sampled_zero_check
                        result = verify_sampled_zero_check("/dev/sda")
                        assert result["ok"] is True

    def test_max_read_bytes_limit(self):
        """Test that max_read_bytes limit is enforced."""
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value='/usr/bin/dd'):
                with patch('crypto_verification.get_device_lock') as mock_lock:
                    mock_lock.return_value.__enter__ = Mock(return_value=None)
                    mock_lock.return_value.__exit__ = Mock(return_value=None)
                    with patch('subprocess.run') as mock_run:
                        # Large drive but limited by max_read_bytes
                        mock_run.side_effect = [
                            MagicMock(returncode=0, stdout="1099511627776"),  # 1TB
                            MagicMock(returncode=0, stdout=b'\x00' * (32*1024*1024)),  # First 32MB
                            MagicMock(returncode=0, stdout=b'\x00' * (32*1024*1024)),  # Additional offset
                        ]
                        from crypto_verification import verify_sampled_zero_check
                        result = verify_sampled_zero_check("/dev/sda", max_read_bytes=50*1024*1024)
                        assert result["ok"] is True


class TestCaptureBeforeState:
    """Test before-state capture for hash comparison."""

    def test_invalid_device_path(self):
        """Test that invalid device path is rejected."""
        with patch('crypto_verification.validate_device_path', return_value=False):
            from crypto_verification import capture_before_state
            result = capture_before_state("/dev/invalid")
            assert result["ok"] is False
            assert result["error"] == "invalid_device_path"

    def test_dd_not_available(self):
        """Test that missing dd command is handled."""
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value=None):
                from crypto_verification import capture_before_state
                result = capture_before_state("/dev/sda")
                assert result["ok"] is False
                assert result["error"] == "dd_not_available_for_capture"

    def test_blockdev_capacity_check_fails(self):
        """Test that blockdev failure is handled."""
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value='/usr/bin/dd'):
                with patch('crypto_verification.get_device_lock') as mock_lock:
                    mock_lock.return_value.__enter__ = Mock(return_value=None)
                    mock_lock.return_value.__exit__ = Mock(return_value=None)
                    with patch('subprocess.run') as mock_run:
                        mock_run.return_value = MagicMock(returncode=1, stderr="blockdev error", stdout="")
                        from crypto_verification import capture_before_state
                        result = capture_before_state("/dev/sda")
                        assert result["ok"] is False
                        assert result["error"] == "capture_capacity_check_failed"

    def test_successful_capture(self):
        """Test successful hash capture."""
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value='/usr/bin/dd'):
                with patch('crypto_verification.get_device_lock') as mock_lock:
                    mock_lock.return_value.__enter__ = Mock(return_value=None)
                    mock_lock.return_value.__exit__ = Mock(return_value=None)
                    with patch('subprocess.run') as mock_run:
                        mock_run.side_effect = [
                            MagicMock(returncode=0, stdout="1073741824"),  # capacity
                            MagicMock(returncode=0, stdout=b'\x00' * (32*1024*1024)),  # First 32MB
                            MagicMock(returncode=0, stdout=b'\x00' * (32*1024*1024)),  # Additional offset
                            MagicMock(returncode=0, stdout=b'\x00' * (32*1024*1024)),  # Additional offset
                            MagicMock(returncode=0, stdout=b'\x00' * (32*1024*1024)),  # Additional offset
                        ]
                        from crypto_verification import capture_before_state
                        result = capture_before_state("/dev/sda", sample_ratio=0.01, max_read_bytes=100*1024*1024)
                        assert result["ok"] is True
                        assert "offsets" in result["details"]
                        assert "hashes" in result["details"]
                        assert len(result["details"]["offsets"]) == len(result["details"]["hashes"])

    def test_interrupted_during_capture(self):
        """Test that interruption during capture is handled."""
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value='/usr/bin/dd'):
                with patch('crypto_verification.get_device_lock') as mock_lock:
                    mock_lock.return_value.__enter__ = Mock(return_value=None)
                    mock_lock.return_value.__exit__ = Mock(return_value=None)
                    with patch('subprocess.run') as mock_run:
                        with patch('crypto_verification._check_interrupted', return_value=True):
                            mock_run.return_value = MagicMock(returncode=0, stdout="1073741824")
                            from crypto_verification import capture_before_state
                            result = capture_before_state("/dev/sda")
                            assert result["ok"] is False
                            assert result["error"] == "verification_interrupted"

    def test_dd_read_failure_during_capture(self):
        """Test that dd read failure during capture is handled with retry logic."""
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value='/usr/bin/dd'):
                with patch('crypto_verification.get_device_lock') as mock_lock:
                    mock_lock.return_value.__enter__ = Mock(return_value=None)
                    mock_lock.return_value.__exit__ = Mock(return_value=None)
                    with patch('crypto_verification.load_policy', return_value={"blockdev_post_wipe_retries": 0, "blockdev_post_wipe_retry_delay": 0}):
                        with patch('subprocess.run') as mock_run:
                            mock_run.side_effect = [
                                MagicMock(returncode=0, stdout="1073741824"),
                                MagicMock(returncode=1, stderr=b"dd read error"),
                            ]
                            from crypto_verification import capture_before_state
                            result = capture_before_state("/dev/sda")
                            assert result["ok"] is False
                            assert result["error"] == "capture_read_failed"
                            assert result["is_detached"] is False

    def test_dd_read_failure_during_capture_detached(self):
        """Test that drive detachment is detected during capture with is_detached=True."""
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value='/usr/bin/dd'):
                with patch('crypto_verification.get_device_lock') as mock_lock:
                    mock_lock.return_value.__enter__ = Mock(return_value=None)
                    mock_lock.return_value.__exit__ = Mock(return_value=None)
                    with patch('crypto_verification.load_policy', return_value={"blockdev_post_wipe_retries": 0, "blockdev_post_wipe_retry_delay": 0}):
                        with patch('subprocess.run') as mock_run:
                            mock_run.side_effect = [
                                MagicMock(returncode=0, stdout="1073741824"),
                                MagicMock(returncode=1, stderr=b"No such device"),
                            ]
                            from crypto_verification import capture_before_state
                            result = capture_before_state("/dev/sda")
                            assert result["ok"] is False
                            assert result["error"] == "capture_read_failed"
                            assert result["is_detached"] is True


class TestVerifyCryptoProbe:
    """Test crypto probe verification entry point."""

    def test_disabled_mode(self):
        """Test that disabled mode skips verification."""
        from crypto_verification import verify_crypto_probe
        result = verify_crypto_probe("/dev/sda", mode="disabled")
        assert result["ok"] is True
        assert result["status"] == "skipped"
        assert result["details"]["mode"] == "disabled"

    def test_controller_only_mode(self):
        """Test that controller_only mode skips verification."""
        from crypto_verification import verify_crypto_probe
        result = verify_crypto_probe("/dev/sda", mode="controller_only")
        assert result["ok"] is True
        assert result["status"] == "skipped"
        assert result["details"]["mode"] == "controller_only"

    def test_conservative_probe_without_before_state(self):
        """Test conservative probe falls back to zero check without before_state."""
        with patch('crypto_verification.verify_sampled_zero_check', return_value={"ok": True, "status": "verified"}):
            from crypto_verification import verify_crypto_probe
            result = verify_crypto_probe("/dev/sda", mode="conservative_probe", before_state=None)
            assert result["ok"] is True
            assert result["status"] == "verified"

    def test_with_valid_before_state(self):
        """Test that valid before_state triggers hash comparison."""
        with patch('crypto_verification.verify_crypto_hash_comparison', return_value={"ok": True, "status": "verified"}):
            from crypto_verification import verify_crypto_probe
            before_state = {"ok": True, "details": {"offsets": [0], "hashes": ["abc"]}}
            result = verify_crypto_probe("/dev/sda", before_state=before_state)
            assert result["ok"] is True
            assert result["status"] == "verified"

    def test_with_invalid_before_state(self):
        """Test that invalid before_state falls back to zero check."""
        with patch('crypto_verification.verify_sampled_zero_check', return_value={"ok": True, "status": "verified"}):
            from crypto_verification import verify_crypto_probe
            before_state = {"ok": False, "error": "capture_failed"}
            result = verify_crypto_probe("/dev/sda", before_state=before_state)
            assert result["ok"] is True
            assert result["status"] == "verified"

    def test_mode_case_insensitive(self):
        """Test that mode is case-insensitive."""
        from crypto_verification import verify_crypto_probe
        result = verify_crypto_probe("/dev/sda", mode="DISABLED")
        assert result["ok"] is True
        assert result["status"] == "skipped"


class TestVerifyCryptoHashComparison:
    """Test hash comparison verification."""

    def test_invalid_device_path(self):
        """Test that invalid device path is rejected."""
        with patch('crypto_verification.validate_device_path', return_value=False):
            from crypto_verification import verify_crypto_hash_comparison
            result = verify_crypto_hash_comparison("/dev/invalid", {"ok": True}, 32*1024*1024)
            assert result["ok"] is False
            assert result["error"] == "invalid_device_path"

    def test_dd_not_available(self):
        """Test that missing dd command is handled."""
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value=None):
                from crypto_verification import verify_crypto_hash_comparison
                result = verify_crypto_hash_comparison("/dev/sda", {"ok": True}, 32*1024*1024)
                assert result["ok"] is False
                assert result["error"] == "dd_not_available_for_comparison"

    def test_blockdev_capacity_check_fails(self):
        """Test that blockdev failure is handled."""
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value='/usr/bin/dd'):
                with patch('subprocess.run') as mock_run:
                    mock_run.return_value = MagicMock(returncode=1, stderr="blockdev error", stdout="")
                    from crypto_verification import verify_crypto_hash_comparison
                    result = verify_crypto_hash_comparison("/dev/sda", {"ok": True}, 32*1024*1024)
                    assert result["ok"] is False
                    assert result["error"] == "secondary_capacity_check_failed"

    def test_before_state_no_offsets(self):
        """Test that missing offsets in before_state is rejected."""
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value='/usr/bin/dd'):
                with patch('subprocess.run') as mock_run:
                    mock_run.return_value = MagicMock(returncode=0, stdout="1073741824")
                    from crypto_verification import verify_crypto_hash_comparison
                    result = verify_crypto_hash_comparison("/dev/sda", {"ok": True, "details": {}}, 32*1024*1024)
                    assert result["ok"] is False
                    assert result["error"] == "before_state_invalid"

    def test_before_state_offset_hash_mismatch(self):
        """Test that offset/hash count mismatch is rejected."""
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value='/usr/bin/dd'):
                with patch('subprocess.run') as mock_run:
                    mock_run.return_value = MagicMock(returncode=0, stdout="1073741824")
                    from crypto_verification import verify_crypto_hash_comparison
                    before_state = {"ok": True, "details": {"offsets": [0, 1], "hashes": ["abc"]}}
                    result = verify_crypto_hash_comparison("/dev/sda", before_state, 32*1024*1024)
                    assert result["ok"] is False
                    assert result["error"] == "before_state_invalid"

    def test_all_hashes_changed(self):
        """Test successful verification when all hashes changed."""
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value='/usr/bin/dd'):
                with patch('subprocess.run') as mock_run:
                    mock_run.side_effect = [
                        MagicMock(returncode=0, stdout="1073741824"),  # capacity
                        MagicMock(returncode=0, stdout=b'\x01' * (32*1024*1024)),  # after data
                    ]
                    from crypto_verification import verify_crypto_hash_comparison
                    before_state = {"ok": True, "details": {"offsets": [0], "hashes": [hashlib.sha256(b'\x00' * (32*1024*1024)).hexdigest()]}}
                    result = verify_crypto_hash_comparison("/dev/sda", before_state, 32*1024*1024)
                    assert result["ok"] is True
                    assert result["status"] == "verified"

    def test_no_hashes_changed_all_zeros(self):
        """Test that unchanged all-zero drive passes."""
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value='/usr/bin/dd'):
                with patch('subprocess.run') as mock_run:
                    mock_run.side_effect = [
                        MagicMock(returncode=0, stdout="1073741824"),  # capacity
                        MagicMock(returncode=0, stdout=b'\x00' * (32*1024*1024)),  # after data
                        MagicMock(returncode=0, stdout=b'\x00' * (32*1024*1024)),  # zero check
                    ]
                    from crypto_verification import verify_crypto_hash_comparison
                    before_hash = hashlib.sha256(b'\x00' * (32*1024*1024)).hexdigest()
                    before_state = {"ok": True, "details": {"offsets": [0], "hashes": [before_hash]}}
                    result = verify_crypto_hash_comparison("/dev/sda", before_state, 32*1024*1024)
                    assert result["ok"] is True
                    assert result["status"] == "verified"
                    assert result["details"]["drive_was_zeroed"] is True

    def test_no_hashes_changed_non_zero(self):
        """Test that unchanged non-zero drive fails."""
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value='/usr/bin/dd'):
                with patch('subprocess.run') as mock_run:
                    mock_run.side_effect = [
                        MagicMock(returncode=0, stdout="1073741824"),  # capacity
                        MagicMock(returncode=0, stdout=b'\x01' * (32*1024*1024)),  # after data
                        MagicMock(returncode=0, stdout=b'\x01' * (32*1024*1024)),  # zero check
                    ]
                    from crypto_verification import verify_crypto_hash_comparison
                    before_hash = hashlib.sha256(b'\x01' * (32*1024*1024)).hexdigest()
                    before_state = {"ok": True, "details": {"offsets": [0], "hashes": [before_hash]}}
                    result = verify_crypto_hash_comparison("/dev/sda", before_state, 32*1024*1024)
                    assert result["ok"] is False
                    assert result["error"] == "crypto_comparison_unchanged_data"

    def test_partial_wipe_detection(self):
        """Test that partial wipe (some changed, some non-zero unchanged) fails."""
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value='/usr/bin/dd'):
                with patch('subprocess.run') as mock_run:
                    mock_run.side_effect = [
                        MagicMock(returncode=0, stdout="1073741824"),  # capacity
                        MagicMock(returncode=0, stdout=b'\x00' * (32*1024*1024)),  # offset 0 after (changed)
                        MagicMock(returncode=0, stdout=b'\x01' * (32*1024*1024)),  # offset 1 after (unchanged)
                        MagicMock(returncode=0, stdout=b'\x01' * (32*1024*1024)),  # offset 1 zero check
                    ]
                    from crypto_verification import verify_crypto_hash_comparison
                    before_state = {
                        "ok": True,
                        "details": {
                            "offsets": [0, 1],
                            "hashes": [
                                hashlib.sha256(b'\x01' * (32*1024*1024)).hexdigest(),  # offset 0 before
                                hashlib.sha256(b'\x01' * (32*1024*1024)).hexdigest(),  # offset 1 before
                            ]
                        }
                    }
                    result = verify_crypto_hash_comparison("/dev/sda", before_state, 32*1024*1024)
                    assert result["ok"] is False
                    assert result["error"] == "crypto_comparison_partial_wipe"

    def test_partial_wipe_unchanged_are_zero(self):
        """Test that partial wipe with unchanged zero chunks passes."""
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value='/usr/bin/dd'):
                with patch('subprocess.run') as mock_run:
                    mock_run.side_effect = [
                        MagicMock(returncode=0, stdout="1073741824"),  # capacity
                        MagicMock(returncode=0, stdout=b'\x00' * (32*1024*1024)),  # offset 0 after (changed)
                        MagicMock(returncode=0, stdout=b'\x00' * (32*1024*1024)),  # offset 1 after (unchanged)
                        MagicMock(returncode=0, stdout=b'\x00' * (32*1024*1024)),  # offset 1 zero check
                    ]
                    from crypto_verification import verify_crypto_hash_comparison
                    before_state = {
                        "ok": True,
                        "details": {
                            "offsets": [0, 1],
                            "hashes": [
                                hashlib.sha256(b'\x01' * (32*1024*1024)).hexdigest(),  # offset 0 before
                                hashlib.sha256(b'\x00' * (32*1024*1024)).hexdigest(),  # offset 1 before
                            ]
                        }
                    }
                    result = verify_crypto_hash_comparison("/dev/sda", before_state, 32*1024*1024)
                    assert result["ok"] is True
                    assert result["status"] == "verified"

    def test_read_failure_with_retries(self):
        """Test that read failure with retries is handled."""
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value='/usr/bin/dd'):
                with patch('subprocess.run') as mock_run:
                    with patch('time.sleep') as mock_sleep:
                        mock_run.side_effect = [
                            MagicMock(returncode=0, stdout="1073741824"),  # capacity
                            Exception("dd read failed"),  # All retries fail
                        ]
                        from crypto_verification import verify_crypto_hash_comparison
                        before_state = {"ok": True, "details": {"offsets": [0], "hashes": ["abc"]}}
                        result = verify_crypto_hash_comparison("/dev/sda", before_state, 32*1024*1024)
                        assert result["ok"] is False
                        assert result["error"] == "crypto_comparison_read_failed"
                        assert mock_sleep.call_count == 3  # 3 sleep calls between 4 attempts

    def test_read_success_after_retry(self):
        """Test that read success after retry works."""
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value='/usr/bin/dd'):
                with patch('subprocess.run') as mock_run:
                    with patch('time.sleep') as mock_sleep:
                        mock_run.side_effect = [
                            MagicMock(returncode=0, stdout="1073741824"),  # capacity
                            Exception("dd read failed"),  # First attempt fails
                            MagicMock(returncode=0, stdout=b'\x00' * (32*1024*1024)),  # Second succeeds
                        ]
                        from crypto_verification import verify_crypto_hash_comparison
                        before_state = {"ok": True, "details": {"offsets": [0], "hashes": [hashlib.sha256(b'\x01' * (32*1024*1024)).hexdigest()]}}
                        result = verify_crypto_hash_comparison("/dev/sda", before_state, 32*1024*1024)
                        assert result["ok"] is True
                        assert mock_sleep.call_count == 1  # 1 retry

    def test_unchanged_verification_failure_with_retries(self):
        """Test that unchanged chunk verification failure with retries is handled."""
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value='/usr/bin/dd'):
                with patch('subprocess.run') as mock_run:
                    mock_run.side_effect = [
                        MagicMock(returncode=0, stdout="1073741824"),  # capacity
                        MagicMock(returncode=0, stdout=b'\x00' * (32*1024*1024)),  # offset 0 after
                        MagicMock(returncode=0, stdout=b'\x01' * (32*1024*1024)),  # offset 1 after
                        Exception("dd read failed"),  # All retries fail for zero check
                    ]
                    from crypto_verification import verify_crypto_hash_comparison
                    before_state = {
                        "ok": True,
                        "details": {
                            "offsets": [0, 1],
                            "hashes": [
                                hashlib.sha256(b'\x01' * (32*1024*1024)).hexdigest(),
                                hashlib.sha256(b'\x01' * (32*1024*1024)).hexdigest(),
                            ]
                        }
                    }
                    result = verify_crypto_hash_comparison("/dev/sda", before_state, 32*1024*1024)
                    assert result["ok"] is False
                    assert result["error"] == "crypto_comparison_unchanged_verification_failed"


class TestBlockdevRetryLogic:
    """Test Issue 14: blockdev retry logic with drive-detach detection."""

    def test_retry_success_after_transient_failure(self):
        """Test that retry succeeds after initial transient failure."""
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value='/usr/bin/dd'):
                with patch('crypto_verification.get_device_lock') as mock_lock:
                    mock_lock.return_value.__enter__ = Mock(return_value=None)
                    mock_lock.return_value.__exit__ = Mock(return_value=None)
                    with patch('crypto_verification.load_policy', return_value={"blockdev_post_wipe_retries": 2, "blockdev_post_wipe_retry_delay": 1}):
                        with patch('subprocess.run') as mock_run:
                            with patch('time.sleep') as mock_sleep:
                                with patch('random.randint', return_value=0):
                                    # First two attempts fail, third succeeds
                                    mock_run.side_effect = [
                                        MagicMock(returncode=1, stderr="Inappropriate ioctl for device"),
                                        MagicMock(returncode=1, stderr="Inappropriate ioctl for device"),
                                        MagicMock(returncode=0, stdout="1073741824"),
                                        MagicMock(returncode=0, stdout=b'\x00' * (32*1024*1024)),
                                    ]
                                    from crypto_verification import verify_sampled_zero_check
                                    result = verify_sampled_zero_check("/dev/sda", sample_ratio=0.01, max_read_bytes=64*1024*1024)
                                assert result["ok"] is True
                                assert mock_sleep.call_count == 2  # Slept between retries

    def test_retry_exhaustion_drive_detached(self):
        """Test that retry exhaustion with detached drive returns correct error code."""
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value='/usr/bin/dd'):
                with patch('crypto_verification.get_device_lock') as mock_lock:
                    mock_lock.return_value.__enter__ = Mock(return_value=None)
                    mock_lock.return_value.__exit__ = Mock(return_value=None)
                    with patch('crypto_verification.load_policy', return_value={"blockdev_post_wipe_retries": 2, "blockdev_post_wipe_retry_delay": 1}):
                        with patch('subprocess.run') as mock_run:
                            with patch('time.sleep') as mock_sleep:
                                # All attempts fail with detached indicators
                                mock_run.side_effect = [
                                    MagicMock(returncode=1, stderr="ioctl error: No such device"),
                                    MagicMock(returncode=1, stderr="Inappropriate ioctl for device"),
                                    MagicMock(returncode=1, stderr="No such file or directory"),
                                ]
                                from crypto_verification import verify_sampled_zero_check
                                result = verify_sampled_zero_check("/dev/sda")
                                assert result["ok"] is False
                                assert result["error"] == "drive_detached_post_wipe"
                                assert mock_sleep.call_count == 2

    def test_retry_exhaustion_other_failure(self):
        """Test that retry exhaustion with non-detached error returns secondary_capacity_check_failed."""
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value='/usr/bin/dd'):
                with patch('crypto_verification.get_device_lock') as mock_lock:
                    mock_lock.return_value.__enter__ = Mock(return_value=None)
                    mock_lock.return_value.__exit__ = Mock(return_value=None)
                    with patch('crypto_verification.load_policy', return_value={"blockdev_post_wipe_retries": 2, "blockdev_post_wipe_retry_delay": 1}):
                        with patch('subprocess.run') as mock_run:
                            with patch('time.sleep') as mock_sleep:
                                # All attempts fail with non-detached error
                                mock_run.side_effect = [
                                    MagicMock(returncode=1, stderr="Permission denied"),
                                    MagicMock(returncode=1, stderr="Permission denied"),
                                    MagicMock(returncode=1, stderr="Permission denied"),
                                ]
                                from crypto_verification import verify_sampled_zero_check
                                result = verify_sampled_zero_check("/dev/sda")
                                assert result["ok"] is False
                                assert result["error"] == "secondary_capacity_check_failed"
                                assert mock_sleep.call_count == 2

    def test_retry_with_policy_load_failure_uses_defaults(self):
        """Test that policy load failure uses hardcoded defaults (3 retries, 5s delay)."""
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value='/usr/bin/dd'):
                with patch('crypto_verification.get_device_lock') as mock_lock:
                    mock_lock.return_value.__enter__ = Mock(return_value=None)
                    mock_lock.return_value.__exit__ = Mock(return_value=None)
                    with patch('crypto_verification.load_policy', side_effect=Exception("Policy load failed")):
                        with patch('subprocess.run') as mock_run:
                            with patch('time.sleep') as mock_sleep:
                                with patch('random.randint', return_value=0):
                                    # First 3 attempts fail, 4th succeeds (default retries=3 means 4 total attempts)
                                    mock_run.side_effect = [
                                        MagicMock(returncode=1, stderr="Transient error"),
                                        MagicMock(returncode=1, stderr="Transient error"),
                                        MagicMock(returncode=1, stderr="Transient error"),
                                        MagicMock(returncode=0, stdout="1073741824"),
                                        MagicMock(returncode=0, stdout=b'\x00' * (32*1024*1024)),
                                    ]
                                    from crypto_verification import verify_sampled_zero_check
                                    result = verify_sampled_zero_check("/dev/sda", sample_ratio=0.01, max_read_bytes=64*1024*1024)
                                assert result["ok"] is True
                                assert mock_sleep.call_count == 3  # Default 3 retries

    def test_verify_crypto_hash_comparison_uses_retry_logic(self):
        """Test that verify_crypto_hash_comparison also uses retry logic."""
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value='/usr/bin/dd'):
                with patch('crypto_verification.load_policy', return_value={"blockdev_post_wipe_retries": 1, "blockdev_post_wipe_retry_delay": 1}):
                    with patch('subprocess.run') as mock_run:
                        with patch('time.sleep') as mock_sleep:
                            # First attempt fails, second succeeds
                            mock_run.side_effect = [
                                MagicMock(returncode=1, stderr="ioctl error"),
                                MagicMock(returncode=0, stdout="1073741824"),
                                MagicMock(returncode=0, stdout=b'\x01' * (32*1024*1024)),
                            ]
                            from crypto_verification import verify_crypto_hash_comparison
                            before_state = {"ok": True, "details": {"offsets": [0], "hashes": [hashlib.sha256(b'\x00' * (32*1024*1024)).hexdigest()]}}
                            result = verify_crypto_hash_comparison("/dev/sda", before_state, 32*1024*1024)
                            assert result["ok"] is True
                            assert mock_sleep.call_count == 1


class FakeBlockingProcess:
    """Simulates a dd subprocess that blocks in stdout.read until killed."""

    def __init__(self):
        self._killed = threading.Event()
        self.stdout = FakeBlockingStdout(self._killed)
        self.stderr = io.BytesIO(b"")
        self._returncode = None

    def poll(self):
        return 0 if self._killed.is_set() else None

    def kill(self):
        self._killed.set()

    def wait(self, timeout=None):
        self._killed.wait(timeout=timeout)
        self._returncode = 0
        return 0

    @property
    def returncode(self):
        return self._returncode


class FakeBlockingStdout:
    def __init__(self, killed_event):
        self._killed = killed_event

    def read(self, n):
        self._killed.wait()
        return b""


class TestCheckDriveAlreadyZeroed:
    """Tests for the pre-wipe zero-check helper."""

    def _make_zero_result(self, nonzero=False, bytes_read=0, chunks_read=1, error=None):
        return {
            "ok": True,
            "nonzero": nonzero,
            "bytes_read": bytes_read,
            "chunks_read": chunks_read,
            "error": error,
        }

    def test_invalid_device_path(self):
        with patch('crypto_verification.validate_device_path', return_value=False):
            from crypto_verification import check_drive_already_zeroed
            result = check_drive_already_zeroed("/dev/invalid")
            assert result["ok"] is False
            assert result["error"] == "invalid_device_path"

    def test_dd_not_available(self):
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value=None):
                from crypto_verification import check_drive_already_zeroed
                result = check_drive_already_zeroed("/dev/sda")
                assert result["ok"] is False
                assert result["error"] == "dd_not_available_for_zero_check"

    def test_zeroed_large_drive(self):
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value='/bin/dd'):
                with patch('crypto_verification.get_device_lock') as mock_lock:
                    mock_lock.return_value.__enter__ = Mock(return_value=None)
                    mock_lock.return_value.__exit__ = Mock(return_value=None)
                    with patch('crypto_verification._check_interrupted', return_value=False):
                        with patch('crypto_verification.load_policy', return_value={
                            "zero_check_total_bytes_gb": 2,
                            "zero_check_zone_count": 5,
                            "zero_check_block_size_mb": 16,
                            "zero_check_small_drive_threshold_gb": 2,
                            "blockdev_post_wipe_retries": 0,
                            "blockdev_post_wipe_retry_delay": 0,
                        }):
                            with patch('crypto_verification._run_blockdev_getsize64', return_value={"error": None, "capacity": 100 * 1024 * 1024 * 1024}):
                                with patch('crypto_verification._run_cancellable_zone_read') as mock_zone:
                                    mock_zone.return_value = self._make_zero_result(
                                        nonzero=False, bytes_read=429496729, chunks_read=27
                                    )
                                    from crypto_verification import check_drive_already_zeroed
                                    result = check_drive_already_zeroed("/dev/sda")
                                    assert result["ok"] is True
                                    assert result["result"] == "zeroed"
                                    assert result["is_zeroed"] is True
                                    assert mock_zone.call_count == 5

    def test_data_present_detected(self):
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value='/bin/dd'):
                with patch('crypto_verification.get_device_lock') as mock_lock:
                    mock_lock.return_value.__enter__ = Mock(return_value=None)
                    mock_lock.return_value.__exit__ = Mock(return_value=None)
                    with patch('crypto_verification._check_interrupted', return_value=False):
                        with patch('crypto_verification.load_policy', return_value={
                            "zero_check_total_bytes_gb": 2,
                            "zero_check_zone_count": 5,
                            "zero_check_block_size_mb": 16,
                            "zero_check_small_drive_threshold_gb": 2,
                            "blockdev_post_wipe_retries": 0,
                            "blockdev_post_wipe_retry_delay": 0,
                        }):
                            with patch('crypto_verification._run_blockdev_getsize64', return_value={"error": None, "capacity": 100 * 1024 * 1024 * 1024}):
                                with patch('crypto_verification._run_cancellable_zone_read') as mock_zone:
                                    def side_effect(*args, **kwargs):
                                        side_effect.calls += 1
                                        if side_effect.calls == 1:
                                            return self._make_zero_result(
                                                nonzero=True, bytes_read=16 * 1024 * 1024, chunks_read=1
                                            )
                                        return self._make_zero_result(
                                            nonzero=False, bytes_read=429496729, chunks_read=27
                                        )
                                    side_effect.calls = 0
                                    mock_zone.side_effect = side_effect
                                    from crypto_verification import check_drive_already_zeroed
                                    result = check_drive_already_zeroed("/dev/sda")
                                    assert result["ok"] is True
                                    assert result["result"] == "data_present"
                                    assert result["is_zeroed"] is False

    def test_small_drive_read_whole(self):
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value='/bin/dd'):
                with patch('crypto_verification.get_device_lock') as mock_lock:
                    mock_lock.return_value.__enter__ = Mock(return_value=None)
                    mock_lock.return_value.__exit__ = Mock(return_value=None)
                    with patch('crypto_verification._check_interrupted', return_value=False):
                        with patch('crypto_verification.load_policy', return_value={
                            "zero_check_total_bytes_gb": 2,
                            "zero_check_zone_count": 5,
                            "zero_check_block_size_mb": 16,
                            "zero_check_small_drive_threshold_gb": 2,
                            "blockdev_post_wipe_retries": 0,
                            "blockdev_post_wipe_retry_delay": 0,
                        }):
                            with patch('crypto_verification._run_blockdev_getsize64', return_value={"error": None, "capacity": 1 * 1024 * 1024 * 1024}):
                                with patch('crypto_verification._run_cancellable_zone_read') as mock_zone:
                                    mock_zone.return_value = self._make_zero_result(
                                        nonzero=False, bytes_read=1 * 1024 * 1024 * 1024, chunks_read=68
                                    )
                                    from crypto_verification import check_drive_already_zeroed
                                    result = check_drive_already_zeroed("/dev/sda")
                                    assert result["ok"] is True
                                    assert result["result"] == "zeroed"

    def test_timeout_returns_inconclusive(self):
        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value='/bin/dd'):
                with patch('crypto_verification.get_device_lock') as mock_lock:
                    mock_lock.return_value.__enter__ = Mock(return_value=None)
                    mock_lock.return_value.__exit__ = Mock(return_value=None)
                    with patch('crypto_verification._check_interrupted', return_value=False):
                        with patch('crypto_verification.load_policy', return_value={
                            "zero_check_total_bytes_gb": 2,
                            "zero_check_zone_count": 5,
                            "zero_check_block_size_mb": 16,
                            "zero_check_small_drive_threshold_gb": 2,
                            "blockdev_post_wipe_retries": 0,
                            "blockdev_post_wipe_retry_delay": 0,
                        }):
                            with patch('crypto_verification._run_blockdev_getsize64', return_value={"error": None, "capacity": 100 * 1024 * 1024 * 1024}):
                                with patch('crypto_verification.subprocess.Popen') as mock_popen:
                                    fake_proc = FakeBlockingProcess()
                                    mock_popen.return_value = fake_proc
                                    from crypto_verification import check_drive_already_zeroed
                                    result = check_drive_already_zeroed("/dev/sda", timeout_seconds=0.5)
                                    assert result["ok"] is True
                                    assert result["result"] == "inconclusive"
                                    assert result["error"] == "timeout"

    def test_cancellation_kills_blocking_read(self):
        cancel_event = threading.Event()

        def trigger_cancel():
            time.sleep(0.1)
            cancel_event.set()

        threading.Thread(target=trigger_cancel, daemon=True).start()

        with patch('crypto_verification.validate_device_path', return_value=True):
            with patch('crypto_verification.resolve_verify_command_path', return_value='/bin/dd'):
                with patch('crypto_verification.get_device_lock') as mock_lock:
                    mock_lock.return_value.__enter__ = Mock(return_value=None)
                    mock_lock.return_value.__exit__ = Mock(return_value=None)
                    with patch('crypto_verification._check_interrupted', return_value=False):
                        with patch('crypto_verification.load_policy', return_value={
                            "zero_check_total_bytes_gb": 2,
                            "zero_check_zone_count": 5,
                            "zero_check_block_size_mb": 16,
                            "zero_check_small_drive_threshold_gb": 2,
                            "blockdev_post_wipe_retries": 0,
                            "blockdev_post_wipe_retry_delay": 0,
                        }):
                            with patch('crypto_verification._run_blockdev_getsize64', return_value={"error": None, "capacity": 100 * 1024 * 1024 * 1024}):
                                with patch('crypto_verification.subprocess.Popen') as mock_popen:
                                    fake_proc = FakeBlockingProcess()
                                    mock_popen.return_value = fake_proc
                                    from crypto_verification import check_drive_already_zeroed
                                    result = check_drive_already_zeroed(
                                        "/dev/sda", cancel_event=cancel_event, timeout_seconds=60
                                    )
                                    assert result["ok"] is False
                                    assert result["result"] == "cancelled"
                                    assert result["error"] == "cancelled"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
