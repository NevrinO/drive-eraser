# Unit tests for job_management.py critical safety functions
import pytest
import sys
import os
from io import StringIO
from unittest.mock import patch, MagicMock

# Add backend to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from job_management import (
    _handle_job_signal,
    _check_job_interrupted,
    finalize_failed_job,
    run_erase_job,
)
from erase_commands import (
    prepare_erase_command,
    get_device_sectors_written,
    poll_nvme_sanitize_progress,
    poll_sas_sanitize_progress,
    poll_sata_sanitize_progress,
)
from job_validation import (
    build_recommended_method,
    validate_single_bay,
    create_erase_job,
)


class TestPrepareEraseCommand:
    """Test prepare_erase_command for safety and correctness."""

    @patch('erase_commands.resolve_verify_command_path')
    def test_overwrite_method_sata(self, mock_resolve):
        """Test overwrite command generation for SATA."""
        mock_resolve.return_value = "/bin/dd"
        result = prepare_erase_command("/dev/sda", "sata", "overwrite")

        assert result["ok"] is True
        assert result["command"] == ["/bin/dd", "if=/dev/zero", "of=/dev/sda", "bs=16M", "status=none", "conv=fdatasync"]
        mock_resolve.assert_called_once_with("dd")

    @patch('erase_commands.resolve_verify_command_path')
    def test_overwrite_method_nvme(self, mock_resolve):
        """Test overwrite command generation for NVMe."""
        mock_resolve.return_value = "/bin/dd"
        result = prepare_erase_command("/dev/nvme0n1", "nvme", "overwrite")

        assert result["ok"] is True
        assert result["command"] == ["/bin/dd", "if=/dev/zero", "of=/dev/nvme0n1", "bs=16M", "status=none", "conv=fdatasync"]

    @patch('erase_commands.resolve_verify_command_path')
    def test_overwrite_method_sas(self, mock_resolve):
        """Test overwrite command generation for SAS."""
        mock_resolve.return_value = "/bin/dd"
        result = prepare_erase_command("/dev/sdb", "sas", "overwrite")

        assert result["ok"] is True
        assert result["command"] == ["/bin/dd", "if=/dev/zero", "of=/dev/sdb", "bs=16M", "status=none", "conv=fdatasync"]

    @patch('erase_commands.resolve_verify_command_path')
    def test_dd_not_available(self, mock_resolve):
        """Test that missing dd command is handled safely."""
        mock_resolve.return_value = None
        result = prepare_erase_command("/dev/sda", "sata", "overwrite")

        assert result["ok"] is False
        assert result["error"] == "dd_not_available"

    @patch('erase_commands.resolve_verify_command_path')
    def test_secure_erase_sata(self, mock_resolve):
        """Test secure erase command for SATA."""
        mock_resolve.return_value = "/sbin/hdparm"
        result = prepare_erase_command("/dev/sda", "sata", "secure_erase")

        assert result["ok"] is True
        assert result["command"] == ["/sbin/hdparm", "--user-master", "u", "--security-erase", "wipestation", "/dev/sda"]

    @patch('erase_commands.resolve_verify_command_path')
    def test_enhanced_secure_erase_sata(self, mock_resolve):
        """Test enhanced secure erase command for SATA."""
        mock_resolve.return_value = "/sbin/hdparm"
        result = prepare_erase_command("/dev/sda", "sata", "enhanced_secure_erase")

        assert result["ok"] is True
        assert result["command"] == ["/sbin/hdparm", "--user-master", "u", "--security-erase-enhanced", "wipestation", "/dev/sda"]

    @patch('erase_commands.resolve_verify_command_path')
    def test_hdparm_not_available_for_secure_erase(self, mock_resolve):
        """Test that missing hdparm command is handled safely for secure erase."""
        mock_resolve.return_value = None
        result = prepare_erase_command("/dev/sda", "sata", "secure_erase")

        assert result["ok"] is False
        assert result["error"] == "hdparm_not_available"

    @patch('erase_commands.resolve_verify_command_path')
    def test_crypto_erase_nvme(self, mock_resolve):
        """Test crypto erase command for NVMe."""
        mock_resolve.return_value = "/usr/bin/nvme"
        result = prepare_erase_command("/dev/nvme0n1", "nvme", "crypto")

        assert result["ok"] is True
        assert result["command"] == ["/usr/bin/nvme", "sanitize", "/dev/nvme0", "--sanact", "4"]

    @patch('erase_commands.resolve_verify_command_path')
    def test_block_erase_nvme(self, mock_resolve):
        """Test block erase command for NVMe."""
        mock_resolve.return_value = "/usr/bin/nvme"
        result = prepare_erase_command("/dev/nvme0n1", "nvme", "block")

        assert result["ok"] is True
        assert result["command"] == ["/usr/bin/nvme", "sanitize", "/dev/nvme0", "--sanact", "2"]

    @patch('erase_commands.resolve_verify_command_path')
    def test_nvme_not_available(self, mock_resolve):
        """Test that missing nvme command is handled safely."""
        mock_resolve.return_value = None
        result = prepare_erase_command("/dev/nvme0n1", "nvme", "crypto")

        assert result["ok"] is False
        assert result["error"] == "nvme_not_available"

    @patch('erase_commands.resolve_verify_command_path')
    @patch('erase_commands.validate_device_path')
    def test_invalid_extracted_device_path_nvme(self, mock_validate, mock_resolve):
        """Test that invalid extracted controller path is rejected for NVMe."""
        # The code only validates the extracted controller path (/dev/nvme0), not the original
        mock_validate.return_value = False
        mock_resolve.return_value = "/usr/bin/nvme"
        result = prepare_erase_command("/dev/nvme0n1", "nvme", "crypto")

        assert result["ok"] is False
        assert result["error"] == "invalid_extracted_device_path"

    @patch('erase_commands.resolve_verify_command_path')
    def test_crypto_erase_sata(self, mock_resolve):
        """Test crypto erase command for SATA."""
        mock_resolve.return_value = "/sbin/hdparm"
        result = prepare_erase_command("/dev/sda", "sata", "crypto")

        assert result["ok"] is True
        assert result["command"] == ["/sbin/hdparm", "--yes-i-know-what-i-am-doing", "--sanitize-crypto-scramble", "/dev/sda"]

    @patch('erase_commands.resolve_verify_command_path')
    def test_block_erase_sata(self, mock_resolve):
        """Test block erase command for SATA."""
        mock_resolve.return_value = "/sbin/hdparm"
        result = prepare_erase_command("/dev/sda", "sata", "block")

        assert result["ok"] is True
        assert result["command"] == ["/sbin/hdparm", "--yes-i-know-what-i-am-doing", "--sanitize-block-erase", "/dev/sda"]

    @patch('erase_commands.resolve_verify_command_path')
    def test_block_erase_sas(self, mock_resolve):
        """Test block erase command for SAS."""
        mock_resolve.return_value = "/usr/bin/sg_sanitize"
        result = prepare_erase_command("/dev/sdb", "sas", "block")

        assert result["ok"] is True
        assert result["command"] == ["/usr/bin/sg_sanitize", "--block", "/dev/sdb"]

    @patch('erase_commands.resolve_verify_command_path')
    def test_sg_sanitize_not_available(self, mock_resolve):
        """Test that missing sg_sanitize command is handled safely."""
        mock_resolve.return_value = None
        result = prepare_erase_command("/dev/sdb", "sas", "block")

        assert result["ok"] is False
        assert result["error"] == "sg_sanitize_not_available"

    @patch('erase_commands.resolve_verify_command_path')
    def test_unsupported_method(self, mock_resolve):
        """Test that unsupported method combinations are rejected."""
        mock_resolve.return_value = "/bin/dd"
        result = prepare_erase_command("/dev/sda", "sata", "invalid_method")

        assert result["ok"] is False
        assert "unsupported_method_or_interface" in result["error"]

    @patch('erase_commands.resolve_verify_command_path')
    def test_unsupported_interface_for_method(self, mock_resolve):
        """Test that unsupported interface/method combinations are rejected."""
        mock_resolve.return_value = "/bin/dd"
        # Try crypto erase on an unsupported interface
        result = prepare_erase_command("/dev/sda", "unsupported_interface", "crypto")

        assert result["ok"] is False
        assert "unsupported_interface" in result["error"]

    @patch('erase_commands.resolve_verify_command_path')
    def test_case_insensitive_method(self, mock_resolve):
        """Test that method names are case-insensitive."""
        mock_resolve.return_value = "/bin/dd"
        result = prepare_erase_command("/dev/sda", "sata", "OVERWRITE")

        assert result["ok"] is True
        assert result["command"] == ["/bin/dd", "if=/dev/zero", "of=/dev/sda", "bs=16M", "status=none", "conv=fdatasync"]

    @patch('erase_commands.resolve_verify_command_path')
    def test_whitespace_in_method(self, mock_resolve):
        """Test that method names with whitespace are trimmed."""
        mock_resolve.return_value = "/bin/dd"
        result = prepare_erase_command("/dev/sda", "sata", "  overwrite  ")

        assert result["ok"] is True
        assert result["command"] == ["/bin/dd", "if=/dev/zero", "of=/dev/sda", "bs=16M", "status=none", "conv=fdatasync"]

    @patch('erase_commands.resolve_verify_command_path')
    def test_none_method(self, mock_resolve):
        """Test that None method is handled gracefully."""
        mock_resolve.return_value = "/bin/dd"
        result = prepare_erase_command("/dev/sda", "sata", None)

        assert result["ok"] is False
        assert "unsupported_method_or_interface" in result["error"]

    @patch('erase_commands.resolve_verify_command_path')
    def test_empty_method(self, mock_resolve):
        """Test that empty method is handled gracefully."""
        mock_resolve.return_value = "/bin/dd"
        result = prepare_erase_command("/dev/sda", "sata", "")

        assert result["ok"] is False
        assert "unsupported_method_or_interface" in result["error"]

    @patch('erase_commands.resolve_verify_command_path')
    def test_none_interface(self, mock_resolve):
        """Test that None interface is handled gracefully."""
        mock_resolve.return_value = "/bin/dd"
        result = prepare_erase_command("/dev/sda", None, "overwrite")

        assert result["ok"] is True  # overwrite works regardless of interface

    @patch('erase_commands.resolve_verify_command_path')
    def test_command_safety_no_shell(self, mock_resolve):
        """Test that commands never use shell=True (Lesson #21)."""
        mock_resolve.return_value = "/bin/dd"
        result = prepare_erase_command("/dev/sda", "sata", "overwrite")

        assert result["ok"] is True
        # Verify command is a list (not a string that would require shell=True)
        assert isinstance(result["command"], list)
        # Verify no shell injection patterns in command
        for arg in result["command"]:
            assert ";" not in arg
            assert "|" not in arg
            assert "&" not in arg
            assert "$(" not in arg
            assert "`" not in arg


class TestBuildRecommendedMethod:
    """Test build_recommended_method logic."""

    def test_method_priority_respected(self):
        """Test that method priority from policy is respected."""
        drive = {
            "interface_type": "sata",
            "supported_methods": ["overwrite", "secure_erase", "crypto"]
        }
        policy = {
            "method_priority": {
                "sata": ["secure_erase", "overwrite", "crypto"]
            }
        }
        result = build_recommended_method(drive, policy)
        assert result == "secure_erase"

    def test_fallback_to_overwrite(self):
        """Test fallback to overwrite when priority method not supported."""
        drive = {
            "interface_type": "sata",
            "supported_methods": ["overwrite", "crypto"]
        }
        policy = {
            "method_priority": {
                "sata": ["secure_erase"]
            }
        }
        result = build_recommended_method(drive, policy)
        assert result == "overwrite"

    def test_fallback_to_first_supported(self):
        """Test fallback to first supported method when overwrite not available."""
        drive = {
            "interface_type": "sata",
            "supported_methods": ["crypto", "block"]
        }
        policy = {
            "method_priority": {}
        }
        result = build_recommended_method(drive, policy)
        assert result == "crypto"

    def test_no_supported_methods(self):
        """Test handling when no methods are supported."""
        drive = {
            "interface_type": "sata",
            "supported_methods": []
        }
        policy = {}
        result = build_recommended_method(drive, policy)
        assert result is None

    def test_case_insensitive_interface(self):
        """Test that interface type is case-insensitive."""
        drive = {
            "interface_type": "SATA",
            "supported_methods": ["overwrite"]
        }
        policy = {}
        result = build_recommended_method(drive, policy)
        assert result == "overwrite"


class TestValidateSingleBay:
    """Test validate_single_bay safety checks."""

    def test_bay_not_found(self):
        """Test that non-existent bay returns error."""
        drives = [{"bay": "bay1", "device": "/dev/sda"}]
        validated, error, status = validate_single_bay("Tech", "TICKET-001", "bay2", None, drives, {})
        assert validated is None
        assert error["error"] == "bay not found: bay2"
        assert status == 404

    def test_locked_bay_rejected(self):
        """Test that locked bays are rejected."""
        drives = [{"bay": "bay1", "device": "/dev/sda", "locked": True}]
        validated, error, status = validate_single_bay("Tech", "TICKET-001", "bay1", None, drives, {})
        assert validated is None
        assert "protected and cannot be erased" in error["error"]
        assert status == 403

    def test_os_role_rejected(self):
        """Test that OS role bays are rejected."""
        drives = [{"bay": "bay1", "device": "/dev/sda", "role": "os"}]
        validated, error, status = validate_single_bay("Tech", "TICKET-001", "bay1", None, drives, {})
        assert validated is None
        assert "role is not erasable" in error["error"]
        assert status == 403

    def test_reserved_role_rejected(self):
        """Test that reserved role bays are rejected."""
        drives = [{"bay": "bay1", "device": "/dev/sda", "role": "reserved"}]
        validated, error, status = validate_single_bay("Tech", "TICKET-001", "bay1", None, drives, {})
        assert validated is None
        assert "role is not erasable" in error["error"]
        assert status == 403

    def test_no_drive_present(self):
        """Test that bays without drives are rejected."""
        drives = [{"bay": "bay1", "device": "/dev/sda", "present": False}]
        validated, error, status = validate_single_bay("Tech", "TICKET-001", "bay1", None, drives, {})
        assert validated is None
        assert "no drive present" in error["error"]
        assert status == 409

    def test_strict_audit_mode_requires_technician(self):
        """Test that strict audit mode requires valid technician."""
        drives = [{"bay": "bay1", "device": "/dev/sda", "present": True}]
        policy = {"strict_audit_mode": True}
        validated, error, status = validate_single_bay("", "TICKET-001", "bay1", None, drives, policy)
        assert validated is None
        assert "requires a valid technician name" in error["error"]
        assert status == 400

    def test_strict_audit_mode_rejects_system_operator(self):
        """Test that strict audit mode rejects 'System Operator'."""
        drives = [{"bay": "bay1", "device": "/dev/sda", "present": True}]
        policy = {"strict_audit_mode": True}
        validated, error, status = validate_single_bay("System Operator", "TICKET-001", "bay1", None, drives, policy)
        assert validated is None
        assert "requires a valid technician name" in error["error"]
        assert status == 400

    def test_strict_audit_mode_requires_ticket(self):
        """Test that strict audit mode requires valid ticket number."""
        drives = [{"bay": "bay1", "device": "/dev/sda", "present": True}]
        policy = {"strict_audit_mode": True}
        validated, error, status = validate_single_bay("Tech", "", "bay1", None, drives, policy)
        assert validated is None
        assert "requires a valid ticket number" in error["error"]
        assert status == 400

    def test_strict_audit_mode_rejects_internal(self):
        """Test that strict audit mode rejects 'INTERNAL' ticket."""
        drives = [{"bay": "bay1", "device": "/dev/sda", "present": True}]
        policy = {"strict_audit_mode": True}
        validated, error, status = validate_single_bay("Tech", "INTERNAL", "bay1", None, drives, policy)
        assert validated is None
        assert "requires a valid ticket number" in error["error"]
        assert status == 400

    @patch('job_validation.get_os_by_path')
    def test_os_drive_protection(self, mock_get_os):
        """Test that OS drive is protected from erasure."""
        drives = [{"bay": "bay1", "device": "/dev/sda", "present": True}]
        mock_get_os.return_value = ("/dev/sda", None)
        policy = {}
        validated, error, status = validate_single_bay("Tech", "TICKET-001", "bay1", None, drives, policy)
        assert validated is None
        assert "active host OS drive" in error["error"]
        assert status == 403

    @patch('job_validation.get_os_by_path')
    def test_method_override_not_supported(self, mock_get_os):
        """Test that unsupported method override is rejected."""
        mock_get_os.return_value = (None, None)
        drives = [{
            "bay": "bay1",
            "device": "/dev/sda",
            "present": True,
            "supported_methods": ["overwrite"]
        }]
        policy = {}
        validated, error, status = validate_single_bay("Tech", "TICKET-001", "bay1", "secure_erase", drives, policy)
        assert validated is None
        assert "method not supported" in error["error"]
        assert status == 400

    @patch('job_validation.get_os_by_path')
    def test_method_override_disabled_by_policy(self, mock_get_os):
        """Test that method override is rejected when disabled by policy."""
        mock_get_os.return_value = (None, None)
        drives = [{
            "bay": "bay1",
            "device": "/dev/sda",
            "present": True,
            "supported_methods": ["overwrite", "secure_erase"]
        }]
        policy = {"allow_method_override": False}
        validated, error, status = validate_single_bay("Tech", "TICKET-001", "bay1", "secure_erase", drives, policy)
        assert validated is None
        assert "method override is disabled" in error["error"]
        assert status == 403

    @patch('job_validation.get_os_by_path')
    def test_successful_validation(self, mock_get_os):
        """Test successful validation returns validated data."""
        mock_get_os.return_value = (None, None)
        drives = [{
            "bay": "bay1",
            "device": "/dev/sda",
            "present": True,
            "supported_methods": ["overwrite"],
            "interface_type": "sata",
            "serial": "ABC123",
            "model": "TestDrive",
            "smart": {"capacity_bytes": 1000000000}
        }]
        policy = {}
        validated, error, status = validate_single_bay("Tech", "TICKET-001", "bay1", None, drives, policy)
        assert validated is not None
        assert error is None
        assert status is None
        assert validated["technician"] == "Tech"
        assert validated["bay"] == "bay1"
        assert validated["device"] == "/dev/sda"


class TestCreateEraseJob:
    """Test create_erase_job function."""

    def test_job_structure(self):
        """Test that created job has correct structure."""
        validated = {
            "technician": "Tech",
            "ticket_number": "TICKET-001",
            "bay": "bay1",
            "device": "/dev/sda",
            "method": "overwrite",
            "recommended_method": "overwrite",
            "supported_methods": ["overwrite"],
            "drive": {
                "interface_type": "sata",
                "serial": "ABC123",
                "model": "TestDrive",
                "smart": {"capacity_bytes": 1000000000}
            }
        }
        job = create_erase_job(validated)
        
        assert job["id"] is not None
        assert job["status"] == "queued"
        assert job["created_at"] is not None
        assert job["started_at"] is None
        assert job["finished_at"] is None
        assert job["error"] is None
        assert job["progress_percent"] == 0.0
        assert job["current_phase"] == "Queued in Line"
        assert job["job_type"] == "erase"
        assert job["request"]["technician"] == "Tech"
        assert job["request"]["bay"] == "bay1"
        assert job["request"]["device"] == "/dev/sda"
        assert job["request"]["method"] == "overwrite"

    def test_uuid_generation(self):
        """Test that unique UUIDs are generated."""
        validated = {
            "technician": "Tech",
            "ticket_number": "TICKET-001",
            "bay": "bay1",
            "device": "/dev/sda",
            "method": "overwrite",
            "recommended_method": "overwrite",
            "supported_methods": ["overwrite"],
            "drive": {}
        }
        job1 = create_erase_job(validated)
        job2 = create_erase_job(validated)
        assert job1["id"] != job2["id"]


class TestSignalHandling:
    """Test signal handling for job interruption."""

    def test_handle_job_signal_sets_flag(self):
        """Test that signal handler increments generation counter."""
        import job_management
        with job_management._job_interrupt_lock:
            gen_before = job_management._job_interrupt_generation
        _handle_job_signal(15, None)
        assert _check_job_interrupted(gen_before) is True

    def test_check_interrupted_returns_flag(self):
        """Test that check function returns correct interruption state for a given generation."""
        import job_management
        with job_management._job_interrupt_lock:
            current_gen = job_management._job_interrupt_generation
        result1 = _check_job_interrupted(current_gen)
        result2 = _check_job_interrupted(current_gen)
        assert result1 == result2 == False


class TestProgressPolling:
    """Test progress polling functions."""

    @patch('erase_commands.resolve_verify_command_path')
    @patch('erase_commands.subprocess.run')
    def test_poll_nvme_sanitize_progress_success(self, mock_run, mock_resolve):
        """Test successful NVMe sanitize progress polling."""
        mock_resolve.return_value = "/usr/bin/nvme"
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="sprog: 50\n"
        )
        result = poll_nvme_sanitize_progress("/dev/nvme0n1")
        assert result == 50

    @patch('erase_commands.resolve_verify_command_path')
    def test_poll_nvme_sanitize_progress_no_command(self, mock_resolve):
        """Test NVMe progress polling when command not available."""
        mock_resolve.return_value = None
        result = poll_nvme_sanitize_progress("/dev/nvme0n1")
        assert result is None

    @patch('erase_commands.resolve_verify_command_path')
    @patch('erase_commands.subprocess.run')
    def test_poll_sas_sanitize_progress_success(self, mock_run, mock_resolve):
        """Test successful SAS sanitize progress polling."""
        mock_resolve.return_value = "/usr/bin/sg_requests"
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Progress: 75.5%\n"
        )
        result = poll_sas_sanitize_progress("/dev/sdb")
        assert result == 75.5

    @patch('erase_commands.resolve_verify_command_path')
    def test_poll_sas_sanitize_progress_no_command(self, mock_resolve):
        """Test SAS progress polling when command not available."""
        mock_resolve.return_value = None
        result = poll_sas_sanitize_progress("/dev/sdb")
        assert result is None

    @patch('erase_commands.resolve_verify_command_path')
    @patch('erase_commands.subprocess.run')
    def test_poll_sata_sanitize_progress_success(self, mock_run, mock_resolve):
        """Test successful SATA sanitize progress polling."""
        mock_resolve.return_value = "/sbin/hdparm"
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Sanitize progress: 25.0%\n"
        )
        result = poll_sata_sanitize_progress("/dev/sda")
        assert result == 25.0

    @patch('erase_commands.resolve_verify_command_path')
    def test_poll_sata_sanitize_progress_no_command(self, mock_resolve):
        """Test SATA progress polling when command not available."""
        mock_resolve.return_value = None
        result = poll_sata_sanitize_progress("/dev/sda")
        assert result is None

    # --- A-JM12: Edge case tests for poll functions ---

    @patch('erase_commands.resolve_verify_command_path')
    @patch('erase_commands.subprocess.run')
    def test_poll_sata_no_progress_in_output(self, mock_run, mock_resolve):
        """Test SATA polling returns None when output has no percentage."""
        mock_resolve.return_value = "/sbin/hdparm"
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Sanitize feature supported\nNo progress information available\n"
        )
        result = poll_sata_sanitize_progress("/dev/sda")
        assert result is None

    @patch('erase_commands.resolve_verify_command_path')
    @patch('erase_commands.subprocess.run')
    def test_poll_sata_subprocess_failure(self, mock_run, mock_resolve):
        """Test SATA polling returns None on non-zero return code."""
        mock_resolve.return_value = "/sbin/hdparm"
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Device not found\n"
        )
        result = poll_sata_sanitize_progress("/dev/sda")
        assert result is None

    @patch('erase_commands.resolve_verify_command_path')
    @patch('erase_commands.subprocess.run')
    def test_poll_sata_subprocess_exception(self, mock_run, mock_resolve):
        """Test SATA polling returns None on subprocess exception."""
        mock_resolve.return_value = "/sbin/hdparm"
        mock_run.side_effect = Exception("Command timed out")
        result = poll_sata_sanitize_progress("/dev/sda")
        assert result is None

    @patch('erase_commands.resolve_verify_command_path')
    @patch('erase_commands.subprocess.run')
    def test_poll_sata_realistic_multiline_output(self, mock_run, mock_resolve):
        """Test SATA polling with realistic hdparm --sanitize-status output."""
        mock_resolve.return_value = "/sbin/hdparm"
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "\n/dev/sda:\n\n"
                "ATA Sanitize feature set\n"
                "Sanitize command: supported\n"
                "Sanitize freeze locked: not supported\n"
                "Sanitize progress: 42.5%\n"
                "Estimated time remaining: 300 seconds\n"
            )
        )
        result = poll_sata_sanitize_progress("/dev/sda")
        assert result == 42.5

    @patch('erase_commands.resolve_verify_command_path')
    @patch('erase_commands.subprocess.run')
    def test_poll_sata_percent_keyword(self, mock_run, mock_resolve):
        """Test SATA polling matches 'percent' keyword in addition to 'progress'."""
        mock_resolve.return_value = "/sbin/hdparm"
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Sanitize percent complete: 90%\n"
        )
        result = poll_sata_sanitize_progress("/dev/sda")
        assert result == 90.0

    @patch('erase_commands.resolve_verify_command_path')
    @patch('erase_commands.subprocess.run')
    def test_poll_sas_no_progress_in_output(self, mock_run, mock_resolve):
        """Test SAS polling returns None when output has no percentage."""
        mock_resolve.return_value = "/usr/bin/sg_requests"
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="No sanitize operation in progress\n"
        )
        result = poll_sas_sanitize_progress("/dev/sdb")
        assert result is None

    @patch('erase_commands.resolve_verify_command_path')
    @patch('erase_commands.subprocess.run')
    def test_poll_sas_subprocess_failure(self, mock_run, mock_resolve):
        """Test SAS polling returns None on non-zero return code."""
        mock_resolve.return_value = "/usr/bin/sg_requests"
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="SG_IO: bad host\n"
        )
        result = poll_sas_sanitize_progress("/dev/sdb")
        assert result is None

    @patch('erase_commands.resolve_verify_command_path')
    @patch('erase_commands.subprocess.run')
    def test_poll_sas_subprocess_exception(self, mock_run, mock_resolve):
        """Test SAS polling returns None on subprocess exception."""
        mock_resolve.return_value = "/usr/bin/sg_requests"
        mock_run.side_effect = Exception("Permission denied")
        result = poll_sas_sanitize_progress("/dev/sdb")
        assert result is None

    @patch('erase_commands.resolve_verify_command_path')
    @patch('erase_commands.subprocess.run')
    def test_poll_sas_realistic_multiline_output(self, mock_run, mock_resolve):
        """Test SAS polling with realistic sg_requests --progress output."""
        mock_resolve.return_value = "/usr/bin/sg_requests"
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "Sanitize progress:\n"
                "  progress: 15.0%\n"
                "  estimated time: 600 seconds\n"
            )
        )
        result = poll_sas_sanitize_progress("/dev/sdb")
        assert result == 15.0

    @patch('erase_commands.resolve_verify_command_path')
    @patch('erase_commands.subprocess.run')
    def test_poll_nvme_no_sprog_in_output(self, mock_run, mock_resolve):
        """Test NVMe polling returns None when output has no sprog value."""
        mock_resolve.return_value = "/usr/bin/nvme"
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Sanitize Log Entry\nNo progress data\n"
        )
        result = poll_nvme_sanitize_progress("/dev/nvme0n1")
        assert result is None

    @patch('erase_commands.resolve_verify_command_path')
    @patch('erase_commands.subprocess.run')
    def test_poll_nvme_subprocess_failure(self, mock_run, mock_resolve):
        """Test NVMe polling returns None on non-zero return code."""
        mock_resolve.return_value = "/usr/bin/nvme"
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="NVMe controller error\n"
        )
        result = poll_nvme_sanitize_progress("/dev/nvme0n1")
        assert result is None

    @patch('erase_commands.resolve_verify_command_path')
    @patch('erase_commands.subprocess.run')
    def test_poll_nvme_subprocess_exception(self, mock_run, mock_resolve):
        """Test NVMe polling returns None on subprocess exception."""
        mock_resolve.return_value = "/usr/bin/nvme"
        mock_run.side_effect = FileNotFoundError("nvme not found")
        result = poll_nvme_sanitize_progress("/dev/nvme0n1")
        assert result is None

    @patch('erase_commands.resolve_verify_command_path')
    @patch('erase_commands.subprocess.run')
    def test_poll_nvme_realistic_multiline_output(self, mock_run, mock_resolve):
        """Test NVMe polling with realistic nvme sanitize-log output."""
        mock_resolve.return_value = "/usr/bin/nvme"
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "Sanitize Log (SLOG)\n"
                "  sprog = 100\n"
                "  sstat = 0x101\n"
            )
        )
        result = poll_nvme_sanitize_progress("/dev/nvme0n1")
        assert result == 100

    @patch('erase_commands.resolve_verify_command_path')
    @patch('erase_commands.subprocess.run')
    def test_poll_nvme_sprog_with_colon(self, mock_run, mock_resolve):
        """Test NVMe polling parses sprog with colon separator."""
        mock_resolve.return_value = "/usr/bin/nvme"
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="SPROG: 37\n"
        )
        result = poll_nvme_sanitize_progress("/dev/nvme0n1")
        assert result == 37

    @patch('erase_commands.resolve_verify_command_path')
    @patch('erase_commands.subprocess.run')
    def test_poll_nvme_sprog_real_format(self, mock_run, mock_resolve):
        """Test NVMe polling parses real nvme-cli (SPROG): output format."""
        mock_resolve.return_value = "/usr/bin/nvme"
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Sanitize Progress (SPROG): 100\n"
        )
        result = poll_nvme_sanitize_progress("/dev/nvme0n1")
        assert result == 100


class TestGetDeviceSectorsWritten:
    """Test get_device_sectors_written function."""

    @patch('builtins.open', MagicMock(return_value=StringIO("  1 2 3 4 5 6 1000\n")))
    @patch('erase_commands.os.path.exists', return_value=True)
    def test_get_sectors_written_success(self, mock_exists):
        """Test successful sector count reading."""
        result = get_device_sectors_written("/dev/sda")
        assert result == 1000

    @patch('erase_commands.os.path.exists', return_value=False)
    def test_get_sectors_written_file_not_found(self, mock_exists):
        """Test handling when stat file doesn't exist."""
        result = get_device_sectors_written("/dev/sda")
        assert result is None

    @patch('builtins.open', side_effect=IOError("Permission denied"))
    @patch('erase_commands.os.path.exists', return_value=True)
    def test_get_sectors_written_io_error(self, mock_exists, mock_open):
        """Test handling of IO errors."""
        result = get_device_sectors_written("/dev/sda")
        assert result is None


class TestGetEraseTimeout:
    """Test _get_erase_timeout for default values, policy overrides, and invalid values."""

    def test_default_overwrite(self):
        """Test default timeout for overwrite method."""
        from job_management import _get_erase_timeout
        with patch('job_management.load_policy', return_value={}):
            assert _get_erase_timeout("overwrite") == 172800

    def test_default_secure_erase(self):
        """Test default timeout for secure_erase method."""
        from job_management import _get_erase_timeout
        with patch('job_management.load_policy', return_value={}):
            assert _get_erase_timeout("secure_erase") == 7200

    def test_default_crypto(self):
        """Test default timeout for crypto method."""
        from job_management import _get_erase_timeout
        with patch('job_management.load_policy', return_value={}):
            assert _get_erase_timeout("crypto") == 7200

    def test_default_block(self):
        """Test default timeout for block method."""
        from job_management import _get_erase_timeout
        with patch('job_management.load_policy', return_value={}):
            assert _get_erase_timeout("block") == 7200

    def test_policy_override(self):
        """Test that policy.json overrides default timeouts."""
        from job_management import _get_erase_timeout
        policy = {"erase_timeouts": {"overwrite_seconds": 3600}}
        with patch('job_management.load_policy', return_value=policy):
            assert _get_erase_timeout("overwrite") == 3600

    def test_policy_override_all_methods(self):
        """Test policy override for each method."""
        from job_management import _get_erase_timeout
        policy = {
            "erase_timeouts": {
                "overwrite_seconds": 100,
                "secure_erase_seconds": 200,
                "enhanced_secure_erase_seconds": 300,
                "crypto_seconds": 400,
                "block_seconds": 500,
            }
        }
        with patch('job_management.load_policy', return_value=policy):
            assert _get_erase_timeout("overwrite") == 100
            assert _get_erase_timeout("secure_erase") == 200
            assert _get_erase_timeout("enhanced_secure_erase") == 300
            assert _get_erase_timeout("crypto") == 400
            assert _get_erase_timeout("block") == 500

    def test_invalid_non_numeric_value(self):
        """Test that non-numeric policy value falls back to default with warning."""
        from job_management import _get_erase_timeout
        policy = {"erase_timeouts": {"overwrite_seconds": "172800s"}}
        with patch('job_management.load_policy', return_value=policy):
            assert _get_erase_timeout("overwrite") == 172800

    def test_invalid_negative_value(self):
        """Test that negative policy value is rejected and falls back to default."""
        from job_management import _get_erase_timeout
        policy = {"erase_timeouts": {"overwrite_seconds": -1}}
        with patch('job_management.load_policy', return_value=policy):
            assert _get_erase_timeout("overwrite") == 172800

    def test_zero_value(self):
        """Test that zero is rejected as invalid timeout and falls back to default."""
        from job_management import _get_erase_timeout
        policy = {"erase_timeouts": {"overwrite_seconds": 0}}
        with patch('job_management.load_policy', return_value=policy):
            assert _get_erase_timeout("overwrite") == 172800

    def test_unknown_method_default(self):
        """Test that unknown method returns the global default of 7200."""
        from job_management import _get_erase_timeout
        with patch('job_management.load_policy', return_value={}):
            assert _get_erase_timeout("nonexistent") == 7200

    def test_unknown_method_with_policy(self):
        """Test that unknown method not in policy returns global default."""
        from job_management import _get_erase_timeout
        policy = {"erase_timeouts": {"overwrite_seconds": 100}}
        with patch('job_management.load_policy', return_value=policy):
            assert _get_erase_timeout("nonexistent") == 7200

    def test_policy_load_failure(self):
        """Test that policy load failure falls back to defaults."""
        from job_management import _get_erase_timeout
        with patch('job_management.load_policy', side_effect=FileNotFoundError("no policy.json")):
            assert _get_erase_timeout("overwrite") == 172800

    def test_empty_erase_timeouts_key(self):
        """Test that empty erase_timeouts dict falls back to defaults."""
        from job_management import _get_erase_timeout
        policy = {"erase_timeouts": {}}
        with patch('job_management.load_policy', return_value=policy):
            assert _get_erase_timeout("overwrite") == 172800

    def test_missing_erase_timeouts_key(self):
        """Test that missing erase_timeouts key in policy falls back to defaults."""
        from job_management import _get_erase_timeout
        policy = {"other_setting": True}
        with patch('job_management.load_policy', return_value=policy):
            assert _get_erase_timeout("overwrite") == 172800


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
