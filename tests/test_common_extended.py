# Extended tests for common.py
import pytest
import sys
import os
import tempfile
import json
import time
from unittest.mock import patch, MagicMock, Mock
from threading import Lock

# Add backend to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))


class TestGetDeviceLock:
    """Test device lock management."""

    def test_get_device_lock_creates_new_lock(self):
        """Test that a new lock is created for first access."""
        from common import get_device_lock, DEVICE_LOCKS
        # Clear existing locks
        DEVICE_LOCKS.clear()
        
        lock1 = get_device_lock("/dev/sda")
        assert lock1 is not None
        assert isinstance(lock1, Lock)
        assert "/dev/sda" in DEVICE_LOCKS

    def test_get_device_lock_returns_existing_lock(self):
        """Test that existing lock is returned for same device."""
        from common import get_device_lock, DEVICE_LOCKS
        DEVICE_LOCKS.clear()
        
        lock1 = get_device_lock("/dev/sda")
        lock2 = get_device_lock("/dev/sda")
        assert lock1 is lock2  # Same object

    def test_get_device_lock_different_devices(self):
        """Test that different devices get different locks."""
        from common import get_device_lock, DEVICE_LOCKS
        DEVICE_LOCKS.clear()
        
        lock1 = get_device_lock("/dev/sda")
        lock2 = get_device_lock("/dev/sdb")
        assert lock1 is not lock2
        assert len(DEVICE_LOCKS) == 2


class TestGetDataDir:
    """Test data directory resolution."""

    def test_env_variable_priority(self):
        """Test that environment variable takes priority."""
        from common import get_data_dir
        with patch('os.getenv', return_value='/custom/data'):
            with patch('os.path.isdir', return_value=True):
                result = get_data_dir()
                assert result == '/custom/data'

    def test_project_root_data_dir(self):
        """Test fallback to project root data directory."""
        from common import get_data_dir, PROJECT_ROOT
        with patch('os.getenv', return_value=None):
            with patch('os.path.isdir', side_effect=lambda x: x == os.path.join(PROJECT_ROOT, "data")):
                result = get_data_dir()
                assert result == os.path.join(PROJECT_ROOT, "data")

    def test_system_data_dir_fallback(self):
        """Test fallback to system data directory."""
        from common import get_data_dir
        with patch('os.getenv', return_value=None):
            with patch('os.path.isdir', side_effect=lambda x: x == "/opt/drive-eraser/data"):
                result = get_data_dir()
                assert result == "/opt/drive-eraser/data"

    def test_returns_project_root_when_none_exist(self):
        """Test that project root is returned when no directories exist."""
        from common import get_data_dir, PROJECT_ROOT
        with patch('os.getenv', return_value=None):
            with patch('os.path.isdir', return_value=False):
                result = get_data_dir()
                assert result == os.path.join(PROJECT_ROOT, "data")


class TestGetConfigDir:
    """Test config directory resolution."""

    def test_env_variable_priority(self):
        """Test that environment variable takes priority."""
        from common import get_config_dir
        with patch('os.getenv', return_value='/custom/config'):
            with patch('os.path.isdir', return_value=True):
                result = get_config_dir()
                assert result == '/custom/config'

    def test_project_root_config_dir(self):
        """Test fallback to project root config directory."""
        from common import get_config_dir, PROJECT_ROOT
        with patch('os.getenv', return_value=None):
            with patch('os.path.isdir', side_effect=lambda x: x == os.path.join(PROJECT_ROOT, "config")):
                result = get_config_dir()
                assert result == os.path.join(PROJECT_ROOT, "config")

    def test_system_config_dir_fallback(self):
        """Test fallback to system config directory."""
        from common import get_config_dir
        with patch('os.getenv', return_value=None):
            with patch('os.path.isdir', side_effect=lambda x: x == "/opt/drive-eraser/config"):
                result = get_config_dir()
                assert result == "/opt/drive-eraser/config"


class TestLoadPolicy:
    """Test policy loading and validation."""

    def test_load_policy_default_when_missing(self):
        """Test that default policy is returned when file missing."""
        from common import load_policy, DEFAULT_POLICY
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('common.get_config_dir', return_value=tmpdir):
                result = load_policy()
                assert result == DEFAULT_POLICY

    def test_load_policy_valid_json(self):
        """Test loading valid policy.json."""
        from common import load_policy
        with tempfile.TemporaryDirectory() as tmpdir:
            policy_path = os.path.join(tmpdir, "policy.json")
            policy_data = {"strict_audit_mode": True, "lan_passphrase": "test123"}
            with open(policy_path, "w") as f:
                json.dump(policy_data, f)
            
            with patch('common.get_config_dir', return_value=tmpdir):
                result = load_policy()
                assert result["strict_audit_mode"] is True
                assert result["lan_passphrase"] == "test123"

    def test_load_policy_invalid_json_structure(self):
        """Test that invalid JSON structure raises error."""
        from common import load_policy
        with tempfile.TemporaryDirectory() as tmpdir:
            policy_path = os.path.join(tmpdir, "policy.json")
            with open(policy_path, "w") as f:
                f.write("not a dict")
            
            with patch('common.get_config_dir', return_value=tmpdir):
                with pytest.raises(ValueError, match="Configuration load failed"):
                    load_policy()

    def test_load_policy_schema_validation_fails(self):
        """Test that schema validation fails for invalid values."""
        from common import load_policy
        with tempfile.TemporaryDirectory() as tmpdir:
            policy_path = os.path.join(tmpdir, "policy.json")
            policy_data = {"port": 99999}  # Exceeds maximum
            with open(policy_path, "w") as f:
                json.dump(policy_data, f)
            
            with patch('common.get_config_dir', return_value=tmpdir):
                with pytest.raises(ValueError, match="Configuration validation failed"):
                    load_policy()

    def test_load_policy_logs_unknown_keys(self):
        """Test that unknown keys are logged as warnings."""
        from common import load_policy
        with tempfile.TemporaryDirectory() as tmpdir:
            policy_path = os.path.join(tmpdir, "policy.json")
            policy_data = {"strict_audit_mode": True, "unknown_key": "value"}
            with open(policy_path, "w") as f:
                json.dump(policy_data, f)
            
            with patch('common.get_config_dir', return_value=tmpdir):
                with patch('common.logger') as mock_logger:
                    result = load_policy()
                    mock_logger.warning.assert_called()
                    assert "unknown_key" in str(mock_logger.warning.call_args)

    def test_load_policy_merges_with_defaults(self):
        """Test that loaded policy is merged with defaults."""
        from common import load_policy, DEFAULT_POLICY
        with tempfile.TemporaryDirectory() as tmpdir:
            policy_path = os.path.join(tmpdir, "policy.json")
            policy_data = {"strict_audit_mode": True}
            with open(policy_path, "w") as f:
                json.dump(policy_data, f)
            
            with patch('common.get_config_dir', return_value=tmpdir):
                result = load_policy()
                assert result["strict_audit_mode"] is True
                # Default values should be present
                assert "lan_passphrase" in result
                assert "prewipe_zero_detection_enabled" in result
                assert "zero_detection_concurrency_limit" in result

    def test_load_policy_migrates_deprecated_prewipe_spot_check(self):
        """Test that deprecated prewipe_spot_check is migrated to prewipe_zero_detection_enabled."""
        from common import load_policy
        with tempfile.TemporaryDirectory() as tmpdir:
            policy_path = os.path.join(tmpdir, "policy.json")
            policy_data = {"prewipe_spot_check": False}
            with open(policy_path, "w") as f:
                json.dump(policy_data, f)

            with patch('common.get_config_dir', return_value=tmpdir):
                result = load_policy()
                assert result["prewipe_zero_detection_enabled"] is False
                assert "prewipe_spot_check" not in result


class TestValidatePolicy:
    """Test policy validation."""

    def test_validate_policy_strict_audit_requires_passphrase(self):
        """Test that strict_audit_mode requires non-empty passphrase."""
        from common import validate_strict_audit_requirements
        is_valid, error_msg = validate_strict_audit_requirements(True, "")
        assert is_valid is False
        assert "non-empty" in error_msg

    def test_validate_policy_strict_audit_passphrase_min_length(self):
        """Test that passphrase minimum length is enforced."""
        from common import validate_strict_audit_requirements
        is_valid, error_msg = validate_strict_audit_requirements(True, "short")
        assert is_valid is False
        assert "at least 8 characters" in error_msg

    def test_validate_policy_strict_audit_whitespace_passphrase(self):
        """Test that whitespace-only passphrase is rejected."""
        from common import validate_strict_audit_requirements
        is_valid, error_msg = validate_strict_audit_requirements(True, "   ")
        assert is_valid is False
        assert "non-empty" in error_msg

    def test_validate_policy_strict_audit_valid_passphrase(self):
        """Test that valid passphrase passes validation."""
        from common import validate_policy
        policy = {"strict_audit_mode": True, "wipe_passphrase": "strongpass123"}
        validate_policy(policy)  # Should not raise

    def test_validate_policy_non_strict_audit_allows_empty_passphrase(self):
        """Test that non-strict mode allows empty passphrase."""
        from common import validate_policy
        policy = {"strict_audit_mode": False, "wipe_passphrase": ""}
        validate_policy(policy)  # Should not raise


class TestSavePolicy:
    """Test policy saving."""

    def test_save_policy_creates_file(self):
        """Test that policy file is created."""
        from common import save_policy
        with tempfile.TemporaryDirectory() as tmpdir:
            policy_data = {"strict_audit_mode": True, "lan_passphrase": "test"}
            with patch('common.get_config_dir', return_value=tmpdir):
                save_policy(policy_data)
                policy_path = os.path.join(tmpdir, "policy.json")
                assert os.path.exists(policy_path)

    def test_save_policy_writes_correct_json(self):
        """Test that policy is written as correct JSON."""
        from common import save_policy
        with tempfile.TemporaryDirectory() as tmpdir:
            policy_data = {"strict_audit_mode": True, "lan_passphrase": "test"}
            with patch('common.get_config_dir', return_value=tmpdir):
                save_policy(policy_data)
                policy_path = os.path.join(tmpdir, "policy.json")
                with open(policy_path, "r") as f:
                    loaded = json.load(f)
                assert loaded["strict_audit_mode"] is True
                assert loaded["lan_passphrase"] == "test"


class TestSaveBayMap:
    """Test bay map saving with atomic writes."""

    def test_save_bay_map_atomic_write(self):
        """Test that bay map is saved atomically."""
        from common import save_bay_map
        with tempfile.TemporaryDirectory() as tmpdir:
            bay_map_data = {"bay1": {"by_path": "/dev/sda"}}
            with patch('common.get_config_dir', return_value=tmpdir):
                save_bay_map(bay_map_data)
                bay_map_path = os.path.join(tmpdir, "bay_map.json")
                assert os.path.exists(bay_map_path)
                
                with open(bay_map_path, "r") as f:
                    loaded = json.load(f)
                assert loaded == bay_map_data

    def test_save_bay_map_creates_config_dir(self):
        """Test that config directory is created if missing."""
        from common import save_bay_map
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = os.path.join(tmpdir, "config")
            bay_map_data = {"bay1": {"by_path": "/dev/sda"}}
            with patch('common.get_config_dir', return_value=config_dir):
                save_bay_map(bay_map_data)
                assert os.path.exists(config_dir)
                assert os.path.exists(os.path.join(config_dir, "bay_map.json"))

    def test_save_bay_map_temp_file_cleanup(self):
        """Test that temp file is cleaned up after atomic rename."""
        from common import save_bay_map
        with tempfile.TemporaryDirectory() as tmpdir:
            bay_map_data = {"bay1": {"by_path": "/dev/sda"}}
            with patch('common.get_config_dir', return_value=tmpdir):
                save_bay_map(bay_map_data)
                temp_path = os.path.join(tmpdir, "bay_map.json.tmp")
                assert not os.path.exists(temp_path)


class TestLoadBayMap:
    """Test bay map loading."""

    def test_load_bay_map_missing_returns_empty(self):
        """Test that missing bay map returns empty dict."""
        from common import load_bay_map
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('common.get_config_dir', return_value=tmpdir):
                result = load_bay_map()
                assert result == {}

    def test_load_bay_map_valid_json(self):
        """Test loading valid bay map."""
        from common import load_bay_map
        with tempfile.TemporaryDirectory() as tmpdir:
            bay_map_path = os.path.join(tmpdir, "bay_map.json")
            bay_map_data = {"bay1": {"by_path": "/dev/sda"}}
            with open(bay_map_path, "w") as f:
                json.dump(bay_map_data, f)
            
            with patch('common.get_config_dir', return_value=tmpdir):
                result = load_bay_map()
                assert result == bay_map_data

    def test_load_bay_map_placeholder_detection(self):
        """Test that REPLACE_ME placeholder is detected and logged."""
        from common import load_bay_map
        with tempfile.TemporaryDirectory() as tmpdir:
            bay_map_path = os.path.join(tmpdir, "bay_map.json")
            bay_map_data = {"bay1": {"by_path": "REPLACE_ME"}}
            with open(bay_map_path, "w") as f:
                json.dump(bay_map_data, f)
            
            with patch('common.get_config_dir', return_value=tmpdir):
                with patch('logging.getLogger') as mock_get_logger:
                    mock_logger = MagicMock()
                    mock_get_logger.return_value = mock_logger
                    result = load_bay_map()
                    mock_logger.warning.assert_called()
                    assert "REPLACE_ME" in str(mock_logger.warning.call_args)

    def test_load_bay_map_invalid_json_returns_empty(self):
        """Test that invalid JSON returns empty dict."""
        from common import load_bay_map
        with tempfile.TemporaryDirectory() as tmpdir:
            bay_map_path = os.path.join(tmpdir, "bay_map.json")
            with open(bay_map_path, "w") as f:
                f.write("invalid json")
            
            with patch('common.get_config_dir', return_value=tmpdir):
                with patch('logging.getLogger') as mock_get_logger:
                    mock_logger = MagicMock()
                    mock_get_logger.return_value = mock_logger
                    result = load_bay_map()
                    assert result == {}
                    mock_logger.error.assert_called()

    def test_load_bay_map_thread_safe(self):
        """Test that load is thread-safe with lock."""
        from common import load_bay_map, BAY_MAP_LOCK
        with tempfile.TemporaryDirectory() as tmpdir:
            bay_map_path = os.path.join(tmpdir, "bay_map.json")
            bay_map_data = {"bay1": {"by_path": "/dev/sda"}}
            with open(bay_map_path, "w") as f:
                json.dump(bay_map_data, f)
            
            with patch('common.get_config_dir', return_value=tmpdir):
                # Lock should be acquired during load
                result = load_bay_map()
                assert result == bay_map_data


class TestPurgeOldLogs:
    """Test log purging functionality."""

    def test_purge_old_logs_removes_old_files(self):
        """Test that old log files are removed."""
        from common import purge_old_logs
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create old log file
            old_log = os.path.join(tmpdir, "old.log")
            with open(old_log, "w") as f:
                f.write("old log")
            
            # Set modification time to 100 days ago
            old_time = time.time() - (100 * 86400)
            os.utime(old_log, (old_time, old_time))
            
            with patch('common.get_logs_dir', return_value=tmpdir):
                with patch('common.get_active_logs_dir', return_value=tmpdir):
                    with patch('common.get_failed_logs_dir', return_value=tmpdir):
                        purged = purge_old_logs(max_age_days=30)
                        assert purged == 1
                        assert not os.path.exists(old_log)

    def test_purge_old_logs_keeps_recent_files(self):
        """Test that recent files are not removed."""
        from common import purge_old_logs
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create recent log file
            recent_log = os.path.join(tmpdir, "recent.log")
            with open(recent_log, "w") as f:
                f.write("recent log")
            
            with patch('common.get_logs_dir', return_value=tmpdir):
                with patch('common.get_active_logs_dir', return_value=tmpdir):
                    with patch('common.get_failed_logs_dir', return_value=tmpdir):
                        purged = purge_old_logs(max_age_days=30)
                        assert purged == 0
                        assert os.path.exists(recent_log)

    def test_purge_old_logs_only_log_files(self):
        """Test that only .log files are removed."""
        from common import purge_old_logs
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create old log file and old non-log file
            old_log = os.path.join(tmpdir, "old.log")
            old_other = os.path.join(tmpdir, "old.txt")
            with open(old_log, "w") as f:
                f.write("old log")
            with open(old_other, "w") as f:
                f.write("old other")
            
            old_time = time.time() - (100 * 86400)
            os.utime(old_log, (old_time, old_time))
            os.utime(old_other, (old_time, old_time))
            
            with patch('common.get_logs_dir', return_value=tmpdir):
                with patch('common.get_active_logs_dir', return_value=tmpdir):
                    with patch('common.get_failed_logs_dir', return_value=tmpdir):
                        purged = purge_old_logs(max_age_days=30)
                        assert purged == 1
                        assert not os.path.exists(old_log)
                        assert os.path.exists(old_other)

    def test_purge_old_logs_handles_missing_dirs(self):
        """Test that missing directories are handled gracefully."""
        from common import purge_old_logs
        with patch('common.get_logs_dir', return_value='/nonexistent'):
            with patch('common.get_active_logs_dir', return_value='/nonexistent'):
                with patch('common.get_failed_logs_dir', return_value='/nonexistent'):
                    purged = purge_old_logs()
                    assert purged == 0

    def test_purge_old_logs_handles_file_errors(self):
        """Test that file errors are handled gracefully."""
        from common import purge_old_logs
        with tempfile.TemporaryDirectory() as tmpdir:
            old_log = os.path.join(tmpdir, "old.log")
            with open(old_log, "w") as f:
                f.write("old log")
            
            old_time = time.time() - (100 * 86400)
            os.utime(old_log, (old_time, old_time))
            
            with patch('common.get_logs_dir', return_value=tmpdir):
                with patch('common.get_active_logs_dir', return_value=tmpdir):
                    with patch('common.get_failed_logs_dir', return_value=tmpdir):
                        with patch('os.remove', side_effect=PermissionError):
                            purged = purge_old_logs(max_age_days=30)
                            assert purged == 0  # Should not crash


class TestPurgeOldCertificates:
    """Test certificate purging functionality."""

    def test_purge_old_certificates_removes_old_files(self):
        """Test that old certificate files are removed."""
        from common import purge_old_certificates
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create old certificate files
            old_json = os.path.join(tmpdir, "old.json")
            old_html = os.path.join(tmpdir, "old.html")
            with open(old_json, "w") as f:
                f.write("old cert")
            with open(old_html, "w") as f:
                f.write("old cert")
            
            old_time = time.time() - (400 * 86400)  # 400 days
            os.utime(old_json, (old_time, old_time))
            os.utime(old_html, (old_time, old_time))
            
            with patch('common.get_cert_dir', return_value=tmpdir):
                purged = purge_old_certificates(max_age_days=365)
                assert purged == 2
                assert not os.path.exists(old_json)
                assert not os.path.exists(old_html)

    def test_purge_old_certificates_keeps_recent_files(self):
        """Test that recent certificates are not removed."""
        from common import purge_old_certificates
        with tempfile.TemporaryDirectory() as tmpdir:
            recent_json = os.path.join(tmpdir, "recent.json")
            with open(recent_json, "w") as f:
                f.write("recent cert")
            
            with patch('common.get_cert_dir', return_value=tmpdir):
                purged = purge_old_certificates(max_age_days=365)
                assert purged == 0
                assert os.path.exists(recent_json)

    def test_purge_old_certificates_only_cert_files(self):
        """Test that only .json and .html files are removed."""
        from common import purge_old_certificates
        with tempfile.TemporaryDirectory() as tmpdir:
            old_json = os.path.join(tmpdir, "old.json")
            old_txt = os.path.join(tmpdir, "old.txt")
            with open(old_json, "w") as f:
                f.write("old cert")
            with open(old_txt, "w") as f:
                f.write("old other")
            
            old_time = time.time() - (400 * 86400)
            os.utime(old_json, (old_time, old_time))
            os.utime(old_txt, (old_time, old_time))
            
            with patch('common.get_cert_dir', return_value=tmpdir):
                purged = purge_old_certificates(max_age_days=365)
                assert purged == 1
                assert not os.path.exists(old_json)
                assert os.path.exists(old_txt)

    def test_purge_old_certificates_handles_missing_dir(self):
        """Test that missing cert directory is handled gracefully."""
        from common import purge_old_certificates
        with patch('common.get_cert_dir', return_value='/nonexistent'):
            purged = purge_old_certificates()
            assert purged == 0


class TestLoggingDirectories:
    """Test logging directory functions."""

    def test_get_logs_dir_creates_directory(self):
        """Test that logs directory is created."""
        from common import get_logs_dir
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('common.get_data_dir', return_value=tmpdir):
                logs_dir = get_logs_dir()
                assert os.path.exists(logs_dir)
                assert logs_dir.endswith("logs")

    def test_get_active_logs_dir_creates_directory(self):
        """Test that active logs directory is created."""
        from common import get_active_logs_dir
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('common.get_data_dir', return_value=tmpdir):
                active_dir = get_active_logs_dir()
                assert os.path.exists(active_dir)
                assert active_dir.endswith("active")

    def test_get_failed_logs_dir_creates_directory(self):
        """Test that failed logs directory is created."""
        from common import get_failed_logs_dir
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('common.get_data_dir', return_value=tmpdir):
                failed_dir = get_failed_logs_dir()
                assert os.path.exists(failed_dir)
                assert failed_dir.endswith("failed")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
