# Extended tests for verification.py
import pytest
import sys
import os
import time
from unittest.mock import patch, MagicMock, Mock
from threading import Lock

# Add backend to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))


class TestResolveVerifyCommandPath:
    """Test command path resolution."""

    def test_delegates_to_disk_utils(self):
        """Test that resolve_verify_command_path delegates to disk_utils."""
        with patch('verification.get_command_path', return_value='/usr/bin/dd'):
            from verification import resolve_verify_command_path
            result = resolve_verify_command_path("dd")
            assert result == '/usr/bin/dd'

    def test_none_when_command_not_found(self):
        """Test that None is returned when command not found."""
        with patch('verification.get_command_path', return_value=None):
            from verification import resolve_verify_command_path
            result = resolve_verify_command_path("dd")
            assert result is None


class TestRunVerificationCommand:
    """Test verification command execution."""

    def test_successful_command(self):
        """Test successful command execution."""
        from verification import run_verification_command
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="output", stderr="")
            result = run_verification_command(["dd", "if=/dev/zero"])
            assert result["ok"] is True
            assert result["stdout"] == "output"

    def test_failed_command(self):
        """Test failed command execution."""
        from verification import run_verification_command
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
            result = run_verification_command(["dd", "if=/dev/zero"])
            assert result["ok"] is False
            assert result["stderr"] == "error"

    def test_empty_command(self):
        """Test that empty command is handled."""
        from verification import run_verification_command
        result = run_verification_command([])
        assert result["ok"] is False
        assert result["return_code"] is None

    def test_command_with_none_first_element(self):
        """Test that command with None first element is handled."""
        from verification import run_verification_command
        result = run_verification_command([None, "arg"])
        assert result["ok"] is False

    def test_binary_output(self):
        """Test that binary output is handled."""
        from verification import run_verification_command
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=b"\x00\x01\x02", stderr=b"")
            result = run_verification_command(["dd"], text=False)
            assert result["ok"] is True
            assert result["output_bytes"] == b"\x00\x01\x02"


class TestVerifyOverwrite:
    """Test overwrite verification."""

    def test_invalid_device_path(self):
        """Test that invalid device path is rejected."""
        from verification import verify_overwrite
        with patch('verification.validate_device_path', return_value=False):
            result = verify_overwrite("/dev/invalid")
            assert result["ok"] is False
            assert result["error"] == "invalid_device_path"

    def test_dd_not_available(self):
        """Test that missing dd command is handled."""
        from verification import verify_overwrite
        with patch('verification.validate_device_path', return_value=True):
            with patch('verification.resolve_verify_command_path', return_value=None):
                result = verify_overwrite("/dev/sda")
                assert result["ok"] is False
                assert result["error"] == "dd_not_available_for_verification"

    def test_successful_zero_check(self):
        """Test successful zero verification."""
        from verification import verify_overwrite
        with patch('verification.validate_device_path', return_value=True):
            with patch('verification.resolve_verify_command_path', return_value='/usr/bin/dd'):
                with patch('verification.run_verification_command') as mock_run:
                    mock_run.return_value = {
                        "ok": True,
                        "output_bytes": b'\x00' * 4096
                    }
                    result = verify_overwrite("/dev/sda")
                    assert result["ok"] is True
                    assert result["status"] == "verified"

    def test_non_zero_data_detected(self):
        """Test that non-zero data is detected."""
        from verification import verify_overwrite
        with patch('verification.validate_device_path', return_value=True):
            with patch('verification.resolve_verify_command_path', return_value='/usr/bin/dd'):
                with patch('verification.run_verification_command') as mock_run:
                    mock_run.return_value = {
                        "ok": True,
                        "output_bytes": b'\x00\x01' + b'\x00' * 4094
                    }
                    result = verify_overwrite("/dev/sda")
                    assert result["ok"] is False
                    assert result["error"] == "overwrite_nonzero_sample"

    def test_sample_read_failure(self):
        """Test that sample read failure is handled."""
        from verification import verify_overwrite
        with patch('verification.validate_device_path', return_value=True):
            with patch('verification.resolve_verify_command_path', return_value='/usr/bin/dd'):
                with patch('verification.run_verification_command') as mock_run:
                    mock_run.return_value = {
                        "ok": False,
                        "stderr": "read error"
                    }
                    result = verify_overwrite("/dev/sda")
                    assert result["ok"] is False
                    assert result["error"] == "overwrite_sample_read_failed"

    def test_empty_sample(self):
        """Test that empty sample is handled."""
        from verification import verify_overwrite
        with patch('verification.validate_device_path', return_value=True):
            with patch('verification.resolve_verify_command_path', return_value='/usr/bin/dd'):
                with patch('verification.run_verification_command') as mock_run:
                    mock_run.return_value = {
                        "ok": True,
                        "output_bytes": b""
                    }
                    result = verify_overwrite("/dev/sda")
                    assert result["ok"] is False
                    assert result["error"] == "overwrite_sample_empty"


class TestParseNumericField:
    """Test numeric field parsing."""

    def test_parse_hex_value(self):
        """Test parsing hex value."""
        from verification import parse_numeric_field
        result = parse_numeric_field("sprog: 0x1234", "sprog")
        assert result == 0x1234

    def test_parse_decimal_value(self):
        """Test parsing decimal value."""
        from verification import parse_numeric_field
        result = parse_numeric_field("sprog: 1234", "sprog")
        assert result == 1234

    def test_case_insensitive_field_name(self):
        """Test that field name is case-insensitive."""
        from verification import parse_numeric_field
        result = parse_numeric_field("SPROG: 1234", "sprog")
        assert result == 1234

    def test_field_not_found(self):
        """Test that missing field returns None."""
        from verification import parse_numeric_field
        result = parse_numeric_field("other: 1234", "sprog")
        assert result is None

    def test_invalid_hex_value(self):
        """Test that invalid hex value returns None."""
        from verification import parse_numeric_field
        # The regex pattern (0x[0-9a-fA-F]+|\d+) won't match "0xZZZZ" at all
        # because 'Z' is not a valid hex character, so match is None
        result = parse_numeric_field("sprog: 0xZZZZ", "sprog")
        assert result is None


class TestExtractCommandOutput:
    """Test command output extraction."""

    def test_extract_stdout(self):
        """Test extracting stdout."""
        from verification import extract_command_output
        result = extract_command_output({"stdout": "output", "stderr": "error"})
        assert result == "output"

    def test_extract_stderr_fallback(self):
        """Test falling back to stderr when stdout empty."""
        from verification import extract_command_output
        result = extract_command_output({"stdout": "", "stderr": "error"})
        assert result == "error"

    def test_both_empty(self):
        """Test handling when both are empty."""
        from verification import extract_command_output
        result = extract_command_output({"stdout": "", "stderr": ""})
        assert result == ""

    def test_none_values(self):
        """Test handling None values."""
        from verification import extract_command_output
        result = extract_command_output({"stdout": None, "stderr": None})
        assert result == ""


class TestExtractSataSecuritySection:
    """Test SATA security section extraction."""

    def test_extract_security_section(self):
        """Test extracting security section."""
        from verification import extract_sata_security_section
        output = """
Configuration:
Geometry:
Security:
    enabled
    not locked
"""
        result = extract_sata_security_section(output)
        assert "enabled" in result
        assert "not locked" in result

    def test_fallback_extraction(self):
        """Test fallback extraction when indented parsing fails."""
        from verification import extract_sata_security_section
        output = "Security: enabled not locked\n\n"
        result = extract_sata_security_section(output)
        assert "enabled" in result

    def test_no_security_section(self):
        """Test handling when no security section exists."""
        from verification import extract_sata_security_section
        output = "Configuration:\nGeometry:\n"
        result = extract_sata_security_section(output)
        assert result == ""


class TestParseSataEraseTimeEstimate:
    """Test SATA erase time estimate parsing."""

    def test_parse_minutes(self):
        """Test parsing minutes."""
        from verification import parse_sata_erase_time_estimate
        output = "Security:\n6 min for SECURITY ERASE UNIT"
        result = parse_sata_erase_time_estimate(output)
        assert result == 360  # 6 minutes * 60

    def test_parse_hours(self):
        """Test parsing hours."""
        from verification import parse_sata_erase_time_estimate
        output = "Security:\n2h for SECURITY ERASE UNIT"
        result = parse_sata_erase_time_estimate(output)
        assert result == 7200  # 2 hours * 3600

    def test_parse_minutes_without_space(self):
        """Test parsing minutes without space."""
        from verification import parse_sata_erase_time_estimate
        output = "Security:\n6min for SECURITY ERASE UNIT"
        result = parse_sata_erase_time_estimate(output)
        assert result == 360

    def test_no_time_pattern(self):
        """Test handling when no time pattern found."""
        from verification import parse_sata_erase_time_estimate
        output = "Security:\nno time info"
        result = parse_sata_erase_time_estimate(output)
        assert result is None

    def test_no_security_section(self):
        """Test handling when no security section."""
        from verification import parse_sata_erase_time_estimate
        output = "Configuration:\nGeometry:\n"
        result = parse_sata_erase_time_estimate(output)
        assert result is None


class TestVerifyNvmeSanitize:
    """Test NVMe sanitize verification."""

    def test_invalid_device_path(self):
        """Test that invalid device path is rejected."""
        from verification import verify_nvme_sanitize
        with patch('verification.validate_device_path', return_value=False):
            result = verify_nvme_sanitize("/dev/invalid", "crypto")
            assert result["ok"] is False
            assert result["error"] == "invalid_device_path"

    def test_nvme_not_available(self):
        """Test that missing nvme command is handled."""
        from verification import verify_nvme_sanitize
        with patch('verification.validate_device_path', return_value=True):
            with patch('verification.resolve_verify_command_path', return_value=None):
                result = verify_nvme_sanitize("/dev/nvme0n1", "crypto")
                assert result["ok"] is False
                assert result["error"] == "nvme_not_available_for_verification"

    def test_successful_verification(self):
        """Test successful verification."""
        from verification import verify_nvme_sanitize
        with patch('verification.validate_device_path', return_value=True):
            with patch('verification.resolve_verify_command_path', return_value='/usr/bin/nvme'):
                with patch('verification.run_verification_command') as mock_run:
                    mock_run.return_value = {
                        "ok": True,
                        "stdout": "sprog: 0\nsstat: 0x5"
                    }
                    result = verify_nvme_sanitize("/dev/nvme0n1", "crypto")
                    assert result["ok"] is True
                    assert result["status"] == "verified"

    def test_failed_state(self):
        """Test that failed state is detected."""
        from verification import verify_nvme_sanitize
        with patch('verification.validate_device_path', return_value=True):
            with patch('verification.resolve_verify_command_path', return_value='/usr/bin/nvme'):
                with patch('verification.run_verification_command') as mock_run:
                    mock_run.return_value = {
                        "ok": True,
                        "stdout": "failed\nsstat: 0x2"
                    }
                    result = verify_nvme_sanitize("/dev/nvme0n1", "crypto")
                    assert result["ok"] is False
                    assert result["error"] == "nvme_sanitize_failed_state"

    def test_in_progress_state(self):
        """Test that in-progress state is detected."""
        from verification import verify_nvme_sanitize
        with patch('verification.validate_device_path', return_value=True):
            with patch('verification.resolve_verify_command_path', return_value='/usr/bin/nvme'):
                with patch('verification.run_verification_command') as mock_run:
                    mock_run.return_value = {
                        "ok": True,
                        "stdout": "in progress\nsprog: 32768"
                    }
                    result = verify_nvme_sanitize("/dev/nvme0n1", "crypto")
                    assert result["ok"] is False
                    assert result["error"] == "nvme_sanitize_still_in_progress"

    def test_empty_output(self):
        """Test that empty output is handled."""
        from verification import verify_nvme_sanitize
        with patch('verification.validate_device_path', return_value=True):
            with patch('verification.resolve_verify_command_path', return_value='/usr/bin/nvme'):
                with patch('verification.run_verification_command') as mock_run:
                    mock_run.return_value = {
                        "ok": True,
                        "stdout": ""
                    }
                    result = verify_nvme_sanitize("/dev/nvme0n1", "crypto")
                    assert result["ok"] is False
                    assert result["error"] == "nvme_sanitize_log_empty"


class TestVerifySataSecureErase:
    """Test SATA secure erase verification."""

    def test_invalid_device_path(self):
        """Test that invalid device path is rejected."""
        from verification import verify_sata_secure_erase
        with patch('verification.validate_device_path', return_value=False):
            result = verify_sata_secure_erase("/dev/invalid", "secure_erase")
            assert result["ok"] is False
            assert result["error"] == "invalid_device_path"

    def test_hdparm_not_available(self):
        """Test that missing hdparm command is handled."""
        from verification import verify_sata_secure_erase
        with patch('verification.validate_device_path', return_value=True):
            with patch('verification.resolve_verify_command_path', return_value=None):
                result = verify_sata_secure_erase("/dev/sda", "secure_erase")
                assert result["ok"] is False
                assert result["error"] == "hdparm_not_available_for_verification"

    def test_security_still_enabled(self):
        """Test that enabled security is detected."""
        from verification import verify_sata_secure_erase
        with patch('verification.validate_device_path', return_value=True):
            with patch('verification.resolve_verify_command_path', return_value='/usr/bin/hdparm'):
                with patch('verification.run_verification_command') as mock_run:
                    mock_run.return_value = {
                        "ok": True,
                        "stdout": "Security:\n    enabled\n    not locked"
                    }
                    result = verify_sata_secure_erase("/dev/sda", "secure_erase")
                    assert result["ok"] is False
                    assert result["error"] == "sata_security_still_enabled"

    def test_successful_verification(self):
        """Test successful verification."""
        from verification import verify_sata_secure_erase
        with patch('verification.validate_device_path', return_value=True):
            with patch('verification.resolve_verify_command_path', return_value='/usr/bin/hdparm'):
                with patch('verification.run_verification_command') as mock_run:
                    mock_run.return_value = {
                        "ok": True,
                        "stdout": "Security:\n    not enabled\n    not locked"
                    }
                    result = verify_sata_secure_erase("/dev/sda", "secure_erase")
                    assert result["ok"] is True
                    assert result["status"] == "verified"

    def test_security_section_absent_with_other_sections(self):
        """Test handling when security section absent but other sections present."""
        from verification import verify_sata_secure_erase
        with patch('verification.validate_device_path', return_value=True):
            with patch('verification.resolve_verify_command_path', return_value='/usr/bin/hdparm'):
                with patch('verification.run_verification_command') as mock_run:
                    mock_run.return_value = {
                        "ok": True,
                        "stdout": "Configuration:\nGeometry:\n"
                    }
                    result = verify_sata_secure_erase("/dev/sda", "secure_erase")
                    assert result["ok"] is True
                    assert result["details"]["note"] == "security_section_absent"

    def test_parsing_failed_no_expected_sections(self):
        """Test that parsing failure is detected when expected sections missing."""
        from verification import verify_sata_secure_erase
        with patch('verification.validate_device_path', return_value=True):
            with patch('verification.resolve_verify_command_path', return_value='/usr/bin/hdparm'):
                with patch('verification.run_verification_command') as mock_run:
                    mock_run.return_value = {
                        "ok": True,
                        "stdout": "some other output"
                    }
                    result = verify_sata_secure_erase("/dev/sda", "secure_erase")
                    assert result["ok"] is False
                    assert result["error"] == "hdparm_parsing_failed"


class TestVerifySataSanitize:
    """Test SATA sanitize verification."""

    def test_invalid_device_path(self):
        """Test that invalid device path is rejected."""
        from verification import verify_sata_sanitize
        with patch('verification.validate_device_path', return_value=False):
            result = verify_sata_sanitize("/dev/invalid", "crypto")
            assert result["ok"] is False
            assert result["error"] == "invalid_device_path"

    def test_hdparm_not_available(self):
        """Test that missing hdparm command is handled."""
        from verification import verify_sata_sanitize
        with patch('verification.validate_device_path', return_value=True):
            with patch('verification.resolve_verify_command_path', return_value=None):
                result = verify_sata_sanitize("/dev/sda", "crypto")
                assert result["ok"] is False
                assert result["error"] == "hdparm_not_available_for_verification"

    def test_successful_verification(self):
        """Test successful verification."""
        from verification import verify_sata_sanitize
        with patch('verification.validate_device_path', return_value=True):
            with patch('verification.resolve_verify_command_path', return_value='/usr/bin/hdparm'):
                with patch('verification.run_verification_command') as mock_run:
                    mock_run.return_value = {
                        "ok": True,
                        "stdout": "State: idle\n"
                    }
                    result = verify_sata_sanitize("/dev/sda", "crypto")
                    assert result["ok"] is True
                    assert result["status"] == "verified"

    def test_in_progress_state(self):
        """Test that in-progress state is detected."""
        from verification import verify_sata_sanitize
        with patch('verification.validate_device_path', return_value=True):
            with patch('verification.resolve_verify_command_path', return_value='/usr/bin/hdparm'):
                with patch('verification.run_verification_command') as mock_run:
                    mock_run.return_value = {
                        "ok": True,
                        "stdout": "State: in process\n"
                    }
                    result = verify_sata_sanitize("/dev/sda", "crypto")
                    assert result["ok"] is False
                    assert result["error"] == "sata_sanitize_still_in_progress"

    def test_failed_state(self):
        """Test that failed state is detected."""
        from verification import verify_sata_sanitize
        with patch('verification.validate_device_path', return_value=True):
            with patch('verification.resolve_verify_command_path', return_value='/usr/bin/hdparm'):
                with patch('verification.run_verification_command') as mock_run:
                    mock_run.return_value = {
                        "ok": True,
                        "stdout": "State: failed\n"
                    }
                    result = verify_sata_sanitize("/dev/sda", "crypto")
                    assert result["ok"] is False
                    assert result["error"] == "sata_sanitize_failed_state"

    def test_retry_on_eio_error(self):
        """Test that EIO errors trigger retries."""
        from verification import verify_sata_sanitize
        with patch('verification.validate_device_path', return_value=True):
            with patch('verification.resolve_verify_command_path', return_value='/usr/bin/hdparm'):
                with patch('verification.run_verification_command') as mock_run:
                    with patch('time.sleep') as mock_sleep:
                        # First 4 attempts fail, 5th succeeds
                        mock_run.side_effect = [
                            {"ok": True, "stdout": "input/output error"},
                            {"ok": True, "stdout": "input/output error"},
                            {"ok": True, "stdout": "input/output error"},
                            {"ok": True, "stdout": "input/output error"},
                            {"ok": True, "stdout": "State: idle"}
                        ]
                        result = verify_sata_sanitize("/dev/sda", "crypto")
                        assert result["ok"] is True
                        assert mock_sleep.call_count == 4


class TestVerifySasBlock:
    """Test SAS block erase verification."""

    def test_invalid_device_path(self):
        """Test that invalid device path is rejected."""
        from verification import verify_sas_block
        with patch('verification.validate_device_path', return_value=False):
            result = verify_sas_block("/dev/invalid", "block")
            assert result["ok"] is False
            assert result["error"] == "invalid_device_path"

    def test_sg_sanitize_not_available(self):
        """Test that missing sg_sanitize command is handled."""
        from verification import verify_sas_block
        with patch('verification.validate_device_path', return_value=True):
            with patch('verification.resolve_verify_command_path', return_value=None):
                result = verify_sas_block("/dev/sda", "block")
                assert result["ok"] is False
                assert result["error"] == "sg_sanitize_not_available_for_verification"

    def test_successful_verification(self):
        """Test successful verification."""
        from verification import verify_sas_block
        with patch('verification.validate_device_path', return_value=True):
            with patch('verification.resolve_verify_command_path', return_value='/usr/bin/sg_sanitize'):
                with patch('verification.run_verification_command') as mock_run:
                    mock_run.return_value = {
                        "ok": True,
                        "stdout": "completed\n"
                    }
                    result = verify_sas_block("/dev/sda", "block")
                    assert result["ok"] is True
                    assert result["status"] == "verified"

    def test_failed_state(self):
        """Test that failed state is detected."""
        from verification import verify_sas_block
        with patch('verification.validate_device_path', return_value=True):
            with patch('verification.resolve_verify_command_path', return_value='/usr/bin/sg_sanitize'):
                with patch('verification.run_verification_command') as mock_run:
                    mock_run.return_value = {
                        "ok": True,
                        "stdout": "failed\n"
                    }
                    result = verify_sas_block("/dev/sda", "block")
                    assert result["ok"] is False
                    assert result["error"] == "sas_sanitize_failed_state"

    def test_in_progress_state(self):
        """Test that in-progress state is detected."""
        from verification import verify_sas_block
        with patch('verification.validate_device_path', return_value=True):
            with patch('verification.resolve_verify_command_path', return_value='/usr/bin/sg_sanitize'):
                with patch('verification.run_verification_command') as mock_run:
                    mock_run.return_value = {
                        "ok": True,
                        "stdout": "in progress\n"
                    }
                    result = verify_sas_block("/dev/sda", "block")
                    assert result["ok"] is False
                    assert result["error"] == "sas_sanitize_still_in_progress"


class TestBuildMarkerPayload:
    """Test marker payload building."""

    def test_build_payload_structure(self):
        """Test that payload structure is correct."""
        from verification import build_marker_payload
        job = {
            "id": "job-123",
            "friendly_id": "FRIENDLY-123",
            "finished_at": "2026-01-01T00:00:00Z",
            "request": {
                "ticket_number": "TICKET-1",
                "serial": "ABC123",
                "method": "overwrite",
                "data_written_at_wipe": 1000
            }
        }
        with patch('verification.load_policy', return_value={}):
            payload = build_marker_payload(job)
            assert b"signature" in payload
            assert b"job_id" in payload
            assert b"FRIENDLY-123" in payload

    def test_payload_with_passphrase(self):
        """Test that passphrase adds HMAC."""
        from verification import build_marker_payload
        job = {
            "id": "job-123",
            "finished_at": "2026-01-01T00:00:00Z",
            "request": {
                "ticket_number": "TICKET-1",
                "serial": "ABC123",
                "method": "overwrite"
            }
        }
        with patch('verification.load_policy', return_value={"wipe_passphrase": "testpass"}):
            payload = build_marker_payload(job)
            assert b"hmac" in payload

    def test_payload_checksum(self):
        """Test that checksum is calculated."""
        from verification import build_marker_payload
        job = {
            "id": "job-123",
            "finished_at": "2026-01-01T00:00:00Z",
            "request": {
                "ticket_number": "TICKET-1",
                "serial": "ABC123",
                "method": "overwrite"
            }
        }
        with patch('verification.load_policy', return_value={}):
            payload = build_marker_payload(job)
            assert b"checksum" in payload


class TestGetSoftwareVersions:
    """Test software version detection."""

    def test_cache_hit(self):
        """Test that cache is used when available."""
        from verification import get_software_versions, _VERSIONS_CACHE
        _VERSIONS_CACHE["data"] = {"hdparm": "v9.60"}
        _VERSIONS_CACHE["timestamp"] = time.time()
        
        result = get_software_versions()
        assert result == {"hdparm": "v9.60"}

    def test_cache_miss_and_populate(self):
        """Test that cache is populated on miss."""
        from verification import get_software_versions, _VERSIONS_CACHE
        _VERSIONS_CACHE["data"] = None
        
        with patch('verification.resolve_verify_command_path', return_value='/usr/bin/hdparm'):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="hdparm v9.60")
                result = get_software_versions()
                assert "hdparm" in result
                assert _VERSIONS_CACHE["data"] is not None

    def test_parallel_execution(self):
        """Test that version checks run in parallel."""
        from verification import get_software_versions, _VERSIONS_CACHE
        _VERSIONS_CACHE["data"] = None
        
        with patch('verification.resolve_verify_command_path', return_value='/usr/bin/tool'):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="v1.0")
                result = get_software_versions()
                # Should have checked multiple tools
                assert len(result) > 0

    def test_command_not_found(self):
        """Test handling when command not found."""
        from verification import get_software_versions, _VERSIONS_CACHE
        _VERSIONS_CACHE["data"] = None
        
        with patch('verification.resolve_verify_command_path', return_value=None):
            result = get_software_versions()
            assert "hdparm" in result
            assert result["hdparm"] == "not_found"

    def test_cache_expiration(self):
        """Test that cache expires after TTL."""
        from verification import get_software_versions, _VERSIONS_CACHE, _VERSIONS_CACHE_TTL
        _VERSIONS_CACHE["data"] = {"hdparm": "v9.60"}
        _VERSIONS_CACHE["timestamp"] = time.time() - _VERSIONS_CACHE_TTL - 1
        
        with patch('verification.resolve_verify_command_path', return_value='/usr/bin/hdparm'):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="v9.61")
                result = get_software_versions()
                # Should have refreshed cache
                assert result["hdparm"] == "v9.61"


class TestVerificationForMethod:
    """Test verification method dispatch."""

    def test_overwrite_method(self):
        """Test overwrite verification dispatch."""
        from verification import verification_for_method
        with patch('verification.verify_overwrite', return_value={"ok": True, "status": "verified"}):
            with patch('verification.verify_sampled_zero_check', return_value={"ok": True, "status": "verified"}):
                result = verification_for_method("/dev/sda", "sata", "overwrite", {"ok": True})
                assert result["ok"] is True

    def test_overwrite_failed_eraser(self):
        """Test that failed overwrite erase skips verification."""
        from verification import verification_for_method
        result = verification_for_method("/dev/sda", "sata", "overwrite", {"ok": False})
        assert result["ok"] is False
        assert result["error"] == "erase_failed"

    def test_nvme_crypto_method(self):
        """Test NVMe crypto verification dispatch."""
        from verification import verification_for_method
        with patch('verification.verify_nvme_sanitize', return_value={"ok": True, "status": "verified"}):
            with patch('verification.verify_sampled_zero_check', return_value={"ok": True, "status": "verified"}):
                result = verification_for_method("/dev/nvme0n1", "nvme", "crypto", {"ok": True})
                assert result["ok"] is True

    def test_sata_crypto_method(self):
        """Test SATA crypto verification dispatch."""
        from verification import verification_for_method
        with patch('verification.verify_sata_sanitize', return_value={"ok": True, "status": "verified"}):
            with patch('verification.verify_sampled_zero_check', return_value={"ok": True, "status": "verified"}):
                result = verification_for_method("/dev/sda", "sata", "crypto", {"ok": True})
                assert result["ok"] is True

    def test_sata_secure_erase_method(self):
        """Test SATA secure erase verification dispatch."""
        from verification import verification_for_method
        with patch('verification.verify_sata_secure_erase', return_value={"ok": True, "status": "verified"}):
            with patch('verification.verify_sampled_zero_check', return_value={"ok": True, "status": "verified"}):
                result = verification_for_method("/dev/sda", "sata", "secure_erase", {"ok": True})
                assert result["ok"] is True

    def test_sas_block_method(self):
        """Test SAS block verification dispatch."""
        from verification import verification_for_method
        with patch('verification.verify_sas_block', return_value={"ok": True, "status": "verified"}):
            with patch('verification.verify_sampled_zero_check', return_value={"ok": True, "status": "verified"}):
                result = verification_for_method("/dev/sda", "sas", "block", {"ok": True})
                assert result["ok"] is True

    def test_unsupported_method(self):
        """Test that unsupported method returns error."""
        from verification import verification_for_method
        result = verification_for_method("/dev/sda", "sata", "invalid_method", {"ok": True})
        assert result["ok"] is False
        assert "verification_not_defined" in result["error"]

    def test_with_before_state_hash_comparison(self):
        """Test that before_state triggers hash comparison."""
        from verification import verification_for_method
        with patch('verification.verify_overwrite', return_value={"ok": True, "status": "verified"}):
            with patch('verification.verify_crypto_probe', return_value={"ok": True, "status": "verified"}):
                before_state = {"ok": True, "details": {"offsets": [0], "hashes": ["abc"]}}
                result = verification_for_method("/dev/sda", "sata", "overwrite", {"ok": True}, before_state=before_state)
                assert result["ok"] is True
                assert "secondary_validation" in result["details"]

    def test_secondary_verification_failure(self):
        """Test that secondary verification failure is propagated."""
        from verification import verification_for_method
        with patch('verification.verify_overwrite', return_value={"ok": True, "status": "verified"}):
            with patch('verification.verify_crypto_probe', return_value={"ok": False, "error": "secondary_failed"}):
                before_state = {"ok": True, "details": {"offsets": [0], "hashes": ["abc"]}}
                result = verification_for_method("/dev/sda", "sata", "overwrite", {"ok": True}, before_state=before_state)
                assert result["ok"] is False
                assert "secondary_verification_failed" in result["error"]

    def test_without_before_state_zero_check(self):
        """Test that missing before_state triggers zero check."""
        from verification import verification_for_method
        with patch('verification.verify_overwrite', return_value={"ok": True, "status": "verified"}):
            with patch('verification.verify_sampled_zero_check', return_value={"ok": True, "status": "verified"}):
                result = verification_for_method("/dev/sda", "sata", "overwrite", {"ok": True}, before_state=None)
                assert result["ok"] is True
                assert "secondary_validation" in result["details"]

    def test_case_insensitive_method(self):
        """Test that method is case-insensitive."""
        from verification import verification_for_method
        with patch('verification.verify_overwrite', return_value={"ok": True, "status": "verified"}):
            with patch('verification.verify_sampled_zero_check', return_value={"ok": True, "status": "verified"}):
                result = verification_for_method("/dev/sda", "sata", "OVERWRITE", {"ok": True})
                assert result["ok"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
