# Unit tests for verification.py
import pytest
import sys
import os
from unittest.mock import patch, MagicMock

# Add backend to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from verification import (
    resolve_verify_command_path,
    run_verification_command,
    verify_overwrite,
    parse_numeric_field,
    extract_command_output,
    extract_sata_security_section,
    parse_sata_erase_time_estimate,
    verify_nvme_sanitize
)


class TestResolveVerifyCommandPath:
    """Test command path resolution."""

    @patch('verification.get_command_path')
    def test_delegates_to_disk_utils(self, mock_get):
        """Test that it delegates to disk_utils.get_command_path."""
        mock_get.return_value = "/bin/dd"
        result = resolve_verify_command_path("dd")
        assert result == "/bin/dd"
        mock_get.assert_called_once_with("dd")

    @patch('verification.get_command_path')
    def test_none_when_command_not_found(self, mock_get):
        """Test that None is returned when command not found."""
        mock_get.return_value = None
        result = resolve_verify_command_path("nonexistent")
        assert result is None


class TestRunVerificationCommand:
    """Test verification command execution."""

    @patch('verification.subprocess.run')
    def test_successful_command(self, mock_run):
        """Test successful command execution."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="output",
            stderr=""
        )
        result = run_verification_command(["echo", "test"])
        assert result["ok"] is True
        assert result["stdout"] == "output"
        assert result["return_code"] == 0

    @patch('verification.subprocess.run')
    def test_failed_command(self, mock_run):
        """Test failed command execution."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="error"
        )
        result = run_verification_command(["false"])
        assert result["ok"] is False
        assert result["stderr"] == "error"
        assert result["return_code"] == 1

    def test_empty_command(self):
        """Test handling of empty command."""
        result = run_verification_command([])
        assert result["ok"] is False
        assert result["return_code"] is None

    def test_none_command(self):
        """Test handling of None command."""
        result = run_verification_command(None)
        assert result["ok"] is False

    @patch('verification.subprocess.run')
    def test_sudo_prefix_added(self, mock_run):
        """Test that sudo is added to command."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        run_verification_command(["echo", "test"])
        mock_run.assert_called_once()
        called_cmd = mock_run.call_args[0][0]
        assert called_cmd[0] == "sudo"
        assert called_cmd[1:] == ["echo", "test"]

    @patch('verification.subprocess.run')
    def test_shell_false(self, mock_run):
        """Test that shell=False is used (Lesson #21)."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        run_verification_command(["echo", "test"])
        assert mock_run.call_args[1]["shell"] is False


class TestVerifyOverwrite:
    """Test overwrite verification."""

    @patch('verification.validate_device_path')
    @patch('verification.resolve_verify_command_path')
    def test_invalid_device_path(self, mock_resolve, mock_validate):
        """Test that invalid device path is rejected."""
        mock_validate.return_value = False
        result = verify_overwrite("/dev/invalid")
        assert result["ok"] is False
        assert result["error"] == "invalid_device_path"

    @patch('verification.validate_device_path')
    @patch('verification.resolve_verify_command_path')
    def test_dd_not_available(self, mock_resolve, mock_validate):
        """Test that missing dd command is handled."""
        mock_validate.return_value = True
        mock_resolve.return_value = None
        result = verify_overwrite("/dev/sda")
        assert result["ok"] is False
        assert result["error"] == "dd_not_available_for_verification"

    @patch('verification.validate_device_path')
    @patch('verification.resolve_verify_command_path')
    @patch('verification.run_verification_command')
    def test_successful_verification(self, mock_run_cmd, mock_resolve, mock_validate):
        """Test successful overwrite verification."""
        mock_validate.return_value = True
        mock_resolve.return_value = "/bin/dd"
        mock_run_cmd.return_value = {
            "ok": True,
            "output_bytes": b"\x00\x00\x00\x00"  # All zeros
        }
        result = verify_overwrite("/dev/sda")
        assert result["ok"] is True
        assert result["status"] == "verified"

    @patch('verification.validate_device_path')
    @patch('verification.resolve_verify_command_path')
    @patch('verification.run_verification_command')
    def test_nonzero_sample_fails(self, mock_run_cmd, mock_resolve, mock_validate):
        """Test that nonzero samples cause verification failure."""
        mock_validate.return_value = True
        mock_resolve.return_value = "/bin/dd"
        mock_run_cmd.return_value = {
            "ok": True,
            "output_bytes": b"\x00\x01\x00\x00"  # Contains nonzero
        }
        result = verify_overwrite("/dev/sda")
        assert result["ok"] is False
        assert result["status"] == "verification_failed"
        assert result["error"] == "overwrite_nonzero_sample"

    @patch('verification.validate_device_path')
    @patch('verification.resolve_verify_command_path')
    @patch('verification.run_verification_command')
    def test_sample_read_failure(self, mock_run_cmd, mock_resolve, mock_validate):
        """Test that sample read failure is handled."""
        mock_validate.return_value = True
        mock_resolve.return_value = "/bin/dd"
        mock_run_cmd.return_value = {
            "ok": False,
            "stderr": "Read error"
        }
        result = verify_overwrite("/dev/sda")
        assert result["ok"] is False
        assert result["error"] == "overwrite_sample_read_failed"


class TestParseNumericField:
    """Test numeric field parsing."""

    def test_parse_decimal_value(self):
        """Test parsing decimal value."""
        output = "Capacity: 1000 sectors"
        result = parse_numeric_field(output, "Capacity")
        assert result == 1000

    def test_parse_hex_value(self):
        """Test parsing hex value."""
        output = "LBA: 0x1000"
        result = parse_numeric_field(output, "LBA")
        assert result == 4096

    def test_case_insensitive_hex(self):
        """Test case-insensitive hex parsing."""
        output = "LBA: 0XABC"
        result = parse_numeric_field(output, "LBA")
        assert result == 2748

    def test_field_not_found(self):
        """Test when field is not found."""
        output = "Capacity: 1000 sectors"
        result = parse_numeric_field(output, "Nonexistent")
        assert result is None

    def test_invalid_value(self):
        """Test handling of invalid value."""
        output = "Capacity: invalid"
        result = parse_numeric_field(output, "Capacity")
        assert result is None


class TestExtractCommandOutput:
    """Test command output extraction."""

    def test_extract_stdout(self):
        """Test extracting stdout."""
        result = {"stdout": "test output", "stderr": ""}
        output = extract_command_output(result)
        assert output == "test output"

    def test_extract_stderr_fallback(self):
        """Test falling back to stderr."""
        result = {"stdout": "", "stderr": "error message"}
        output = extract_command_output(result)
        assert output == "error message"

    def test_extract_both_empty(self):
        """Test when both are empty."""
        result = {"stdout": "", "stderr": ""}
        output = extract_command_output(result)
        assert output == ""

    def test_none_values(self):
        """Test handling of None values."""
        result = {"stdout": None, "stderr": None}
        output = extract_command_output(result)
        assert output == ""


class TestExtractSataSecuritySection:
    """Test SATA security section extraction."""

    def test_extract_indented_section(self):
        """Test extracting indented security section."""
        output = """
Security:
    not frozen
    supported
    enabled
"""
        result = extract_sata_security_section(output)
        assert "not frozen" in result
        assert "supported" in result

    def test_fallback_pattern(self):
        """Test fallback pattern when indented parsing fails."""
        output = "Security: not frozen, supported"
        result = extract_sata_security_section(output)
        assert "not frozen" in result

    def test_no_security_section(self):
        """Test when no security section exists."""
        output = "Drive information\nModel: Test"
        result = extract_sata_security_section(output)
        assert result == ""

    def test_case_insensitive(self):
        """Test case-insensitive matching."""
        output = "SECURITY: not frozen"
        result = extract_sata_security_section(output)
        assert "not frozen" in result


class TestParseSataEraseTimeEstimate:
    """Test SATA erase time estimate parsing."""

    def test_parse_minutes(self):
        """Test parsing minutes."""
        output = "Security:\n    6 min for SECURITY ERASE UNIT"
        result = parse_sata_erase_time_estimate(output)
        assert result == 360  # 6 minutes * 60 seconds

    def test_parse_hours(self):
        """Test parsing hours."""
        output = "Security:\n    2h for SECURITY ERASE UNIT"
        result = parse_sata_erase_time_estimate(output)
        assert result == 7200  # 2 hours * 3600 seconds

    def test_parse_without_space(self):
        """Test parsing without space."""
        output = "Security:\n    30min for SECURITY ERASE UNIT"
        result = parse_sata_erase_time_estimate(output)
        assert result == 1800  # 30 minutes * 60 seconds

    def test_no_security_section(self):
        """Test when no security section exists."""
        output = "Drive information"
        result = parse_sata_erase_time_estimate(output)
        assert result is None

    def test_no_time_in_security_section(self):
        """Test when security section has no time."""
        output = "Security:\n    not frozen"
        result = parse_sata_erase_time_estimate(output)
        assert result is None


class TestVerifyNvmeSanitize:
    """Test NVMe sanitize verification."""

    @patch('verification.validate_device_path')
    @patch('verification.resolve_verify_command_path')
    def test_invalid_device_path(self, mock_resolve, mock_validate):
        """Test that invalid device path is rejected."""
        mock_validate.return_value = False
        result = verify_nvme_sanitize("/dev/invalid", "block")
        assert result["ok"] is False
        assert result["error"] == "invalid_device_path"

    @patch('verification.validate_device_path')
    @patch('verification.resolve_verify_command_path')
    def test_nvme_not_available(self, mock_resolve, mock_validate):
        """Test that missing nvme command is handled."""
        mock_validate.return_value = True
        mock_resolve.return_value = None
        result = verify_nvme_sanitize("/dev/nvme0n1", "block")
        assert result["ok"] is False
        assert result["error"] == "nvme_not_available_for_verification"

    @patch('verification.validate_device_path')
    @patch('verification.resolve_verify_command_path')
    @patch('verification.run_verification_command')
    def test_successful_verification(self, mock_run_cmd, mock_resolve, mock_validate):
        """Test successful NVMe sanitize verification."""
        mock_validate.return_value = True
        mock_resolve.return_value = "/usr/bin/nvme"
        mock_run_cmd.return_value = {
            "ok": True,
            "stdout": "Sanitize Status: Completed",
            "stderr": ""
        }
        result = verify_nvme_sanitize("/dev/nvme0n1", "block")
        # Should return a result with status
        assert "status" in result

    @patch('verification.validate_device_path')
    @patch('verification.resolve_verify_command_path')
    @patch('verification.run_verification_command')
    def test_command_failure(self, mock_run_cmd, mock_resolve, mock_validate):
        """Test command failure handling."""
        mock_validate.return_value = True
        mock_resolve.return_value = "/usr/bin/nvme"
        mock_run_cmd.return_value = {
            "ok": False,
            "stdout": "",
            "stderr": "Device not ready"
        }
        result = verify_nvme_sanitize("/dev/nvme0n1", "block")
        assert result["ok"] is False
        assert result["error"] == "nvme_sanitize_log_failed"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
