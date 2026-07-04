# Extended tests for job_management.py
import pytest
import sys
import os
import tempfile
from unittest.mock import patch, MagicMock, Mock, mock_open
from datetime import datetime, timezone

# Add backend to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))


class TestSignalHandling:
    """Test job interruption signal handling."""

    def test_handle_job_signal_sets_flag(self):
        """Test that signal handler increments generation counter."""
        from job_management import _handle_job_signal, _check_job_interrupted
        import job_management

        with job_management._job_interrupt_lock:
            gen_before = job_management._job_interrupt_generation

        _handle_job_signal(15, None)
        assert _check_job_interrupted(gen_before) is True

    def test_check_job_interrupted_returns_flag(self):
        """Test that check_interrupted returns correct state for a given generation."""
        from job_management import _check_job_interrupted
        import job_management

        with job_management._job_interrupt_lock:
            current_gen = job_management._job_interrupt_generation

        assert _check_job_interrupted(current_gen) is False
        assert _check_job_interrupted(current_gen - 1) is True


class TestBuildRecommendedMethod:
    """Test recommended method selection."""

    def test_method_priority_respected(self):
        """Test that method priority from policy is respected."""
        from job_validation import build_recommended_method
        drive = {"interface_type": "sata", "supported_methods": ["overwrite", "secure_erase"]}
        policy = {"method_priority": {"sata": ["secure_erase", "overwrite"]}}
        result = build_recommended_method(drive, policy)
        assert result == "secure_erase"

    def test_fallback_to_overwrite(self):
        """Test fallback to overwrite when priority method not supported."""
        from job_validation import build_recommended_method
        drive = {"interface_type": "sata", "supported_methods": ["overwrite"]}
        policy = {"method_priority": {"sata": ["secure_erase"]}}
        result = build_recommended_method(drive, policy)
        assert result == "overwrite"

    def test_fallback_to_first_supported(self):
        """Test fallback to first supported method when no priority."""
        from job_validation import build_recommended_method
        drive = {"interface_type": "nvme", "supported_methods": ["block", "crypto"]}
        policy = {"method_priority": {}}
        result = build_recommended_method(drive, policy)
        assert result == "block"

    def test_no_supported_methods(self):
        """Test handling when no methods are supported."""
        from job_validation import build_recommended_method
        drive = {"interface_type": "sata", "supported_methods": []}
        policy = {}
        result = build_recommended_method(drive, policy)
        assert result is None

    def test_case_insensitive_interface(self):
        """Test that interface type matching is case-insensitive."""
        from job_validation import build_recommended_method
        drive = {"interface_type": "SATA", "supported_methods": ["overwrite"]}
        policy = {"method_priority": {"sata": ["overwrite"]}}
        result = build_recommended_method(drive, policy)
        assert result == "overwrite"


class TestValidateSingleBay:
    """Test single bay validation."""

    def test_bay_not_found(self):
        """Test that missing bay returns error."""
        from job_validation import validate_single_bay
        drives = [{"bay": "bay1", "present": True}]
        result, error, status = validate_single_bay("tech", "TICKET-1", "bay2", None, drives, {})
        assert result is None
        assert "not found" in error.get("error", "")
        assert status == 404

    def test_locked_bay_rejected(self):
        """Test that locked bay is rejected."""
        from job_validation import validate_single_bay
        drives = [{"bay": "bay1", "locked": True, "present": True}]
        result, error, status = validate_single_bay("tech", "TICKET-1", "bay1", None, drives, {})
        assert result is None
        assert "protected" in error.get("error", "")
        assert status == 403

    def test_os_role_rejected(self):
        """Test that OS role bay is rejected."""
        from job_validation import validate_single_bay
        drives = [{"bay": "bay1", "role": "os", "present": True}]
        result, error, status = validate_single_bay("tech", "TICKET-1", "bay1", None, drives, {})
        assert result is None
        assert "not erasable" in error.get("error", "")
        assert status == 403

    def test_reserved_role_rejected(self):
        """Test that reserved role bay is rejected."""
        from job_validation import validate_single_bay
        drives = [{"bay": "bay1", "role": "reserved", "present": True}]
        result, error, status = validate_single_bay("tech", "TICKET-1", "bay1", None, drives, {})
        assert result is None
        assert "not erasable" in error.get("error", "")
        assert status == 403

    def test_no_drive_present(self):
        """Test that bay without drive is rejected."""
        from job_validation import validate_single_bay
        drives = [{"bay": "bay1", "present": False}]
        result, error, status = validate_single_bay("tech", "TICKET-1", "bay1", None, drives, {})
        assert result is None
        assert "no drive present" in error.get("error", "")
        assert status == 409

    def test_strict_audit_mode_requires_technician(self):
        """Test that strict audit mode requires valid technician."""
        from job_validation import validate_single_bay
        drives = [{"bay": "bay1", "present": True, "device": "/dev/sda", "supported_methods": ["overwrite"]}]
        policy = {"strict_audit_mode": True}
        result, error, status = validate_single_bay("", "TICKET-1", "bay1", None, drives, policy)
        assert result is None
        assert "technician name" in error.get("error", "")
        assert status == 400

    def test_strict_audit_mode_rejects_system_operator(self):
        """Test that strict audit mode rejects System Operator."""
        from job_validation import validate_single_bay
        drives = [{"bay": "bay1", "present": True, "device": "/dev/sda", "supported_methods": ["overwrite"]}]
        policy = {"strict_audit_mode": True}
        result, error, status = validate_single_bay("System Operator", "TICKET-1", "bay1", None, drives, policy)
        assert result is None
        assert "technician name" in error.get("error", "")
        assert status == 400

    def test_strict_audit_mode_requires_ticket(self):
        """Test that strict audit mode requires valid ticket."""
        from job_validation import validate_single_bay
        drives = [{"bay": "bay1", "present": True, "device": "/dev/sda", "supported_methods": ["overwrite"]}]
        policy = {"strict_audit_mode": True}
        result, error, status = validate_single_bay("tech", "", "bay1", None, drives, policy)
        assert result is None
        assert "ticket number" in error.get("error", "")
        assert status == 400

    def test_strict_audit_mode_rejects_internal(self):
        """Test that strict audit mode rejects INTERNAL ticket."""
        from job_validation import validate_single_bay
        drives = [{"bay": "bay1", "present": True, "device": "/dev/sda", "supported_methods": ["overwrite"]}]
        policy = {"strict_audit_mode": True}
        result, error, status = validate_single_bay("tech", "INTERNAL", "bay1", None, drives, policy)
        assert result is None
        assert "ticket number" in error.get("error", "")
        assert status == 400

    def test_os_drive_protection(self):
        """Test that OS drive protection works."""
        from job_validation import validate_single_bay
        drives = [{"bay": "bay1", "present": True, "device": "/dev/sda", "supported_methods": ["overwrite"]}]
        with patch('job_validation.get_os_by_path', return_value=("/dev/sda", "pci-0000:00:1f.2-scsi-0:0:0:0")):
            result, error, status = validate_single_bay("tech", "TICKET-1", "bay1", None, drives, {})
            assert result is None
            assert "OS drive" in error.get("error", "")
            assert status == 403

    def test_method_override_not_supported(self):
        """Test that unsupported method override is rejected."""
        from job_validation import validate_single_bay
        drives = [{"bay": "bay1", "present": True, "device": "/dev/sda", "supported_methods": ["overwrite"]}]
        with patch('job_validation.get_os_by_path', return_value=None):
            result, error, status = validate_single_bay("tech", "TICKET-1", "bay1", "secure_erase", drives, {})
            assert result is None
            assert "not supported" in error["error"]
            assert status == 400

    def test_method_override_disabled_by_policy(self):
        """Test that method override disabled by policy is rejected."""
        from job_validation import validate_single_bay
        drives = [{"bay": "bay1", "present": True, "device": "/dev/sda", "supported_methods": ["overwrite", "secure_erase"]}]
        policy = {"allow_method_override": False, "method_priority": {"sata": ["overwrite"]}}
        with patch('job_validation.get_os_by_path', return_value=None):
            result, error, status = validate_single_bay("tech", "TICKET-1", "bay1", "secure_erase", drives, policy)
            assert result is None
            assert "override is disabled" in error["error"]
            assert status == 403

    def test_successful_validation(self):
        """Test successful validation."""
        from job_validation import validate_single_bay
        drives = [{"bay": "bay1", "present": True, "device": "/dev/sda", "supported_methods": ["overwrite"], "interface_type": "sata"}]
        policy = {"method_priority": {"sata": ["overwrite"]}}
        with patch('job_validation.get_os_by_path', return_value=None):
            result, error, status = validate_single_bay("tech", "TICKET-1", "bay1", None, drives, policy)
            assert result is not None
            assert error is None
            assert status is None
            assert result["method"] == "overwrite"
            assert result["bay"] == "bay1"


class TestCreateEraseJob:
    """Test erase job creation."""

    def test_job_structure(self):
        """Test that job structure is correct."""
        from job_validation import create_erase_job
        validated = {
            "technician": "tech",
            "ticket_number": "TICKET-1",
            "bay": "bay1",
            "device": "/dev/sda",
            "method": "overwrite",
            "recommended_method": "overwrite",
            "supported_methods": ["overwrite"],
            "drive": {"interface_type": "sata", "serial": "ABC123", "model": "Test Drive"}
        }
        job = create_erase_job(validated)
        assert "id" in job
        assert job["status"] == "queued"
        assert job["job_type"] == "erase"
        assert job["request"]["technician"] == "tech"
        assert job["request"]["method"] == "overwrite"

    def test_uuid_generation(self):
        """Test that UUID is generated."""
        from job_validation import create_erase_job
        validated = {
            "technician": "tech",
            "ticket_number": "TICKET-1",
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


class TestGetDeviceSectorsWritten:
    """Test device sectors written retrieval."""

    def test_get_sectors_written_success(self):
        """Test successful sectors read."""
        from erase_commands import get_device_sectors_written
        with tempfile.TemporaryDirectory() as tmpdir:
            stat_path = os.path.join(tmpdir, "stat")
            with open(stat_path, "w") as f:
                f.write("   12345   67890   11111   22222   33333   44444   55555   66666")
            
            with patch('erase_commands.os.path.basename', return_value='sda'):
                with patch('erase_commands.os.path.join', return_value=stat_path):
                    with patch('erase_commands.os.path.exists', return_value=True):
                        with patch('builtins.open', mock_open(read_data="   12345   67890   11111   22222   33333   44444   55555   66666")):
                            result = get_device_sectors_written("/dev/sda")
                            assert result == 55555

    def test_get_sectors_written_file_not_found(self):
        """Test handling when stat file not found."""
        from erase_commands import get_device_sectors_written
        with patch('os.path.exists', return_value=False):
            result = get_device_sectors_written("/dev/sda")
            assert result is None

    def test_get_sectors_written_io_error(self):
        """Test handling IO error."""
        from erase_commands import get_device_sectors_written
        with patch('os.path.exists', return_value=True):
            with patch('builtins.open', side_effect=IOError):
                result = get_device_sectors_written("/dev/sda")
                assert result is None


class TestPollNvmeSanitizeProgress:
    """Test NVMe sanitize progress polling."""

    def test_successful_nvme_progress(self):
        """Test successful NVMe progress parsing."""
        from erase_commands import poll_nvme_sanitize_progress
        with patch('erase_commands.resolve_verify_command_path', return_value='/usr/bin/nvme'):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stdout="Sprog: 32768\n"
                )
                result = poll_nvme_sanitize_progress("/dev/nvme0n1")
                assert result == 32768

    def test_nvme_no_command(self):
        """Test handling when nvme command not found."""
        from erase_commands import poll_nvme_sanitize_progress
        with patch('erase_commands.resolve_verify_command_path', return_value=None):
            result = poll_nvme_sanitize_progress("/dev/nvme0n1")
            assert result is None

    def test_nvme_command_failure(self):
        """Test handling when nvme command fails."""
        from erase_commands import poll_nvme_sanitize_progress
        with patch('erase_commands.resolve_verify_command_path', return_value='/usr/bin/nvme'):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=1)
                result = poll_nvme_sanitize_progress("/dev/nvme0n1")
                assert result is None

    def test_nvme_no_sprog_in_output(self):
        """Test handling when sprog not in output."""
        from erase_commands import poll_nvme_sanitize_progress
        with patch('erase_commands.resolve_verify_command_path', return_value='/usr/bin/nvme'):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stdout="Some other output\n"
                )
                result = poll_nvme_sanitize_progress("/dev/nvme0n1")
                assert result is None


class TestPollSasSanitizeProgress:
    """Test SAS sanitize progress polling."""

    def test_successful_sas_progress(self):
        """Test successful SAS progress parsing."""
        from erase_commands import poll_sas_sanitize_progress
        with patch('erase_commands.resolve_verify_command_path', return_value='/usr/bin/sg_requests'):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stdout="Progress: 50.5%\n"
                )
                result = poll_sas_sanitize_progress("/dev/sda")
                assert result == 50.5

    def test_sas_no_command(self):
        """Test handling when sg_requests not found."""
        from erase_commands import poll_sas_sanitize_progress
        with patch('erase_commands.resolve_verify_command_path', return_value=None):
            result = poll_sas_sanitize_progress("/dev/sda")
            assert result is None

    def test_sas_command_failure(self):
        """Test handling when sg_requests fails."""
        from erase_commands import poll_sas_sanitize_progress
        with patch('erase_commands.resolve_verify_command_path', return_value='/usr/bin/sg_requests'):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=1)
                result = poll_sas_sanitize_progress("/dev/sda")
                assert result is None


class TestPollSataSanitizeProgress:
    """Test SATA sanitize progress polling."""

    def test_successful_sata_progress(self):
        """Test successful SATA progress parsing."""
        from erase_commands import poll_sata_sanitize_progress
        with patch('erase_commands.resolve_verify_command_path', return_value='/usr/bin/hdparm'):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stdout="Progress: 75.0%\n"
                )
                result = poll_sata_sanitize_progress("/dev/sda")
                assert result == 75.0

    def test_sata_no_command(self):
        """Test handling when hdparm not found."""
        from erase_commands import poll_sata_sanitize_progress
        with patch('erase_commands.resolve_verify_command_path', return_value=None):
            result = poll_sata_sanitize_progress("/dev/sda")
            assert result is None

    def test_sata_command_failure(self):
        """Test handling when hdparm fails."""
        from erase_commands import poll_sata_sanitize_progress
        with patch('erase_commands.resolve_verify_command_path', return_value='/usr/bin/hdparm'):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=1)
                result = poll_sata_sanitize_progress("/dev/sda")
                assert result is None


class TestPrepareEraseCommand:
    """Test erase command preparation."""

    def test_overwrite_method_sata(self):
        """Test overwrite method for SATA."""
        from erase_commands import prepare_erase_command
        with patch('erase_commands.resolve_verify_command_path', return_value='/usr/bin/dd'):
            result = prepare_erase_command("/dev/sda", "sata", "overwrite")
            assert result["ok"] is True
            assert any("dd" in cmd for cmd in result["command"])
            assert "if=/dev/zero" in result["command"]

    def test_overwrite_method_nvme(self):
        """Test overwrite method for NVMe."""
        from erase_commands import prepare_erase_command
        with patch('erase_commands.resolve_verify_command_path', return_value='/usr/bin/dd'):
            result = prepare_erase_command("/dev/nvme0n1", "nvme", "overwrite")
            assert result["ok"] is True
            assert any("dd" in cmd for cmd in result["command"])

    def test_overwrite_method_sas(self):
        """Test overwrite method for SAS."""
        from erase_commands import prepare_erase_command
        with patch('erase_commands.resolve_verify_command_path', return_value='/usr/bin/dd'):
            result = prepare_erase_command("/dev/sda", "sas", "overwrite")
            assert result["ok"] is True
            assert any("dd" in cmd for cmd in result["command"])

    def test_dd_not_available(self):
        """Test handling when dd not available."""
        from erase_commands import prepare_erase_command
        with patch('erase_commands.resolve_verify_command_path', return_value=None):
            result = prepare_erase_command("/dev/sda", "sata", "overwrite")
            assert result["ok"] is False
            assert result["error"] == "dd_not_available"

    def test_secure_erase_sata(self):
        """Test secure erase for SATA."""
        from erase_commands import prepare_erase_command
        with patch('erase_commands.resolve_verify_command_path', return_value='/usr/bin/hdparm'):
            result = prepare_erase_command("/dev/sda", "sata", "secure_erase")
            assert result["ok"] is True
            assert any("hdparm" in cmd for cmd in result["command"])
            assert "--security-erase" in result["command"]

    def test_enhanced_secure_erase_sata(self):
        """Test enhanced secure erase for SATA."""
        from erase_commands import prepare_erase_command
        with patch('erase_commands.resolve_verify_command_path', return_value='/usr/bin/hdparm'):
            result = prepare_erase_command("/dev/sda", "sata", "enhanced_secure_erase")
            assert result["ok"] is True
            assert any("hdparm" in cmd for cmd in result["command"])
            assert "--security-erase-enhanced" in result["command"]

    def test_hdparm_not_available_for_secure_erase(self):
        """Test handling when hdparm not available for secure erase."""
        from erase_commands import prepare_erase_command
        with patch('erase_commands.resolve_verify_command_path', return_value=None):
            result = prepare_erase_command("/dev/sda", "sata", "secure_erase")
            assert result["ok"] is False
            assert result["error"] == "hdparm_not_available"

    def test_crypto_erase_nvme(self):
        """Test crypto erase for NVMe."""
        from erase_commands import prepare_erase_command
        with patch('erase_commands.resolve_verify_command_path', return_value='/usr/bin/nvme'):
            result = prepare_erase_command("/dev/nvme0n1", "nvme", "crypto")
            assert result["ok"] is True
            assert any("nvme" in cmd for cmd in result["command"])
            assert "sanitize" in result["command"]
            assert "--sanact" in result["command"]
            assert "4" in result["command"]

    def test_block_erase_nvme(self):
        """Test block erase for NVMe."""
        from erase_commands import prepare_erase_command
        with patch('erase_commands.resolve_verify_command_path', return_value='/usr/bin/nvme'):
            result = prepare_erase_command("/dev/nvme0n1", "nvme", "block")
            assert result["ok"] is True
            assert any("nvme" in cmd for cmd in result["command"])
            assert "sanitize" in result["command"]
            assert "--sanact" in result["command"]
            assert "2" in result["command"]

    def test_nvme_not_available(self):
        """Test handling when nvme not available."""
        from erase_commands import prepare_erase_command
        with patch('erase_commands.resolve_verify_command_path', return_value=None):
            result = prepare_erase_command("/dev/nvme0n1", "nvme", "crypto")
            assert result["ok"] is False
            assert result["error"] == "nvme_not_available"

    def test_crypto_erase_sata(self):
        """Test crypto erase for SATA."""
        from erase_commands import prepare_erase_command
        with patch('erase_commands.resolve_verify_command_path', return_value='/usr/bin/hdparm'):
            result = prepare_erase_command("/dev/sda", "sata", "crypto")
            assert result["ok"] is True
            assert any("hdparm" in cmd for cmd in result["command"])
            assert "--sanitize-crypto-scramble" in result["command"]

    def test_block_erase_sata(self):
        """Test block erase for SATA."""
        from erase_commands import prepare_erase_command
        with patch('erase_commands.resolve_verify_command_path', return_value='/usr/bin/hdparm'):
            result = prepare_erase_command("/dev/sda", "sata", "block")
            assert result["ok"] is True
            assert any("hdparm" in cmd for cmd in result["command"])
            assert "--sanitize-block-erase" in result["command"]

    def test_block_erase_sas(self):
        """Test block erase for SAS."""
        from erase_commands import prepare_erase_command
        with patch('erase_commands.resolve_verify_command_path', return_value='/usr/bin/sg_sanitize'):
            result = prepare_erase_command("/dev/sda", "sas", "block")
            assert result["ok"] is True
            assert any("sg_sanitize" in cmd for cmd in result["command"])
            assert "--block" in result["command"]

    def test_sg_sanitize_not_available(self):
        """Test handling when sg_sanitize not available."""
        from erase_commands import prepare_erase_command
        with patch('erase_commands.resolve_verify_command_path', return_value=None):
            result = prepare_erase_command("/dev/sda", "sas", "block")
            assert result["ok"] is False
            assert result["error"] == "sg_sanitize_not_available"

    def test_unsupported_method(self):
        """Test unsupported method."""
        from erase_commands import prepare_erase_command
        result = prepare_erase_command("/dev/sda", "sata", "invalid_method")
        assert result["ok"] is False
        assert "unsupported" in result["error"]

    def test_unsupported_interface_for_method(self):
        """Test unsupported interface for method."""
        from erase_commands import prepare_erase_command
        result = prepare_erase_command("/dev/sda", "unsupported_interface", "overwrite")
        assert result["ok"] is False
        assert "unsupported_interface" in result["error"]

    def test_case_insensitive_method(self):
        """Test that method is case-insensitive."""
        from erase_commands import prepare_erase_command
        with patch('erase_commands.resolve_verify_command_path', return_value='/usr/bin/dd'):
            result = prepare_erase_command("/dev/sda", "sata", "OVERWRITE")
            assert result["ok"] is True

    def test_whitespace_in_method(self):
        """Test that whitespace in method is handled."""
        from erase_commands import prepare_erase_command
        with patch('erase_commands.resolve_verify_command_path', return_value='/usr/bin/dd'):
            result = prepare_erase_command("/dev/sda", "sata", "  overwrite  ")
            assert result["ok"] is True

    def test_none_method(self):
        """Test that None method is handled."""
        from erase_commands import prepare_erase_command
        result = prepare_erase_command("/dev/sda", "sata", None)
        assert result["ok"] is False

    def test_empty_method(self):
        """Test that empty method is handled."""
        from erase_commands import prepare_erase_command
        result = prepare_erase_command("/dev/sda", "sata", "")
        assert result["ok"] is False

    def test_none_interface(self):
        """Test that None interface is handled - overwrite works without interface."""
        from erase_commands import prepare_erase_command
        with patch('erase_commands.resolve_verify_command_path', return_value='/usr/bin/dd'):
            result = prepare_erase_command("/dev/sda", None, "overwrite")
            assert result["ok"] is True  # overwrite works regardless of interface

    def test_command_safety_no_shell(self):
        """Test that commands don't use shell=True."""
        from erase_commands import prepare_erase_command
        with patch('erase_commands.resolve_verify_command_path', return_value='/usr/bin/dd'):
            result = prepare_erase_command("/dev/sda", "sata", "overwrite")
            assert result["ok"] is True
            # Command should be a list, not a shell string
            assert isinstance(result["command"], list)


class TestFinalizeFailedJob:
    """Test job finalization on failure."""

    def test_finalize_failed_job_sets_status(self):
        """Test that finalize_failed_job sets job status to failed."""
        from job_management import finalize_failed_job
        from app_config import ERASE_JOBS, ERASE_JOBS_LOCK
        import job_management
        
        job_id = "test-job-1"
        job = {
            "id": job_id,
            "status": "running",
            "request": {"bay": "bay1", "device": "/dev/sda"},
            "error": None,
            "finished_at": None
        }
        
        with ERASE_JOBS_LOCK:
            ERASE_JOBS[job_id] = job
        
        with patch('job_management.persist_job'):
            with patch('job_management.send_slack_notification'):
                with patch('job_management.logger'):
                    finalize_failed_job(job_id, "Test error")
        
        with ERASE_JOBS_LOCK:
            assert ERASE_JOBS[job_id]["status"] == "failed"
            assert ERASE_JOBS[job_id]["error"] == "Test error"
            assert ERASE_JOBS[job_id]["finished_at"] is not None

    def test_finalize_failed_job_renames_log(self):
        """Test that finalize_failed_job renames active log to failed log."""
        from job_management import finalize_failed_job
        from app_config import ERASE_JOBS, ERASE_JOBS_LOCK
        import job_management
        
        job_id = "test-job-2"
        job = {
            "id": job_id,
            "status": "running",
            "request": {"bay": "bay1", "device": "/dev/sda"},
            "error": None
        }
        
        with ERASE_JOBS_LOCK:
            ERASE_JOBS[job_id] = job
        
        with tempfile.TemporaryDirectory() as tmpdir:
            active_log = os.path.join(tmpdir, "job-test-job-2.log")
            with open(active_log, "w") as f:
                f.write("test log content")
            
            with patch('job_management.get_active_logs_dir', return_value=tmpdir):
                with patch('job_management.get_failed_logs_dir', return_value=tmpdir):
                    with patch('job_management.persist_job'):
                        with patch('job_management.send_slack_notification'):
                            with patch('job_management.logger'):
                                finalize_failed_job(job_id, "Test error")
            
            # Check that log was renamed
            failed_log = os.path.join(tmpdir, "failed-job-test-job-2-baybay1.log")
            assert os.path.exists(failed_log)
            with open(failed_log, "r") as f:
                content = f.read()
                assert "test log content" in content
                assert "JOB CONFIGURATION FAILURE" in content

    def test_finalize_failed_job_writes_diagnostics(self):
        """Test that finalize_failed_job writes SMART diagnostics to log."""
        from job_management import finalize_failed_job
        from app_config import ERASE_JOBS, ERASE_JOBS_LOCK
        import job_management
        
        job_id = "test-job-3"
        job = {
            "id": job_id,
            "status": "running",
            "request": {"bay": "bay1", "device": "/dev/sda"},
            "error": None
        }
        
        with ERASE_JOBS_LOCK:
            ERASE_JOBS[job_id] = job
        
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('job_management.get_active_logs_dir', return_value=tmpdir):
                with patch('job_management.get_failed_logs_dir', return_value=tmpdir):
                    with patch('job_management.get_raw_smart_diagnostics', return_value="SMART DATA"):
                        with patch('job_management.persist_job'):
                            with patch('job_management.send_slack_notification'):
                                with patch('job_management.logger'):
                                    finalize_failed_job(job_id, "Test error")
            
            failed_log = os.path.join(tmpdir, "failed-job-test-job-3-baybay1.log")
            with open(failed_log, "r") as f:
                content = f.read()
                assert "SMART DATA" in content

    def test_finalize_failed_job_handles_missing_job(self):
        """Test that finalize_failed_job handles missing job gracefully."""
        from job_management import finalize_failed_job
        import job_management
        
        with patch('job_management.logger'):
            finalize_failed_job("nonexistent-job", "Test error")
        # Should not raise exception

    def test_finalize_failed_job_handles_log_rename_failure(self):
        """Test that finalize_failed_job handles log rename failure gracefully."""
        from job_management import finalize_failed_job
        from app_config import ERASE_JOBS, ERASE_JOBS_LOCK
        import job_management
        
        job_id = "test-job-4"
        job = {
            "id": job_id,
            "status": "running",
            "request": {"bay": "bay1", "device": "/dev/sda"},
            "error": None
        }
        
        with ERASE_JOBS_LOCK:
            ERASE_JOBS[job_id] = job
        
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create the active log file so rename is attempted
            active_log = os.path.join(tmpdir, f"job-{job_id}.log")
            with open(active_log, "w") as f:
                f.write("test log content")
            
            with patch('job_management.get_active_logs_dir', return_value=tmpdir):
                with patch('job_management.get_failed_logs_dir', return_value=tmpdir):
                    with patch('os.rename', side_effect=OSError("Permission denied")):
                        with patch('job_management.persist_job'):
                            with patch('job_management.send_slack_notification'):
                                with patch('job_management.logger') as mock_logger:
                                    finalize_failed_job(job_id, "Test error")
                                    # Should log warning but not crash
                                    mock_logger.warning.assert_called()
        
        # Verify job was still finalized despite log rename failure
        with ERASE_JOBS_LOCK:
            assert ERASE_JOBS[job_id]["status"] == "failed"
            assert ERASE_JOBS[job_id]["error"] == "Test error"

    def test_finalize_failed_job_purges_old_logs(self):
        """Test that finalize_failed_job purges old logs."""
        from job_management import finalize_failed_job
        from app_config import ERASE_JOBS, ERASE_JOBS_LOCK
        import job_management
        
        job_id = "test-job-5"
        job = {
            "id": job_id,
            "status": "running",
            "request": {"bay": "bay1", "device": "/dev/sda"},
            "error": None
        }
        
        with ERASE_JOBS_LOCK:
            ERASE_JOBS[job_id] = job
        
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('job_management.get_active_logs_dir', return_value=tmpdir):
                with patch('job_management.get_failed_logs_dir', return_value=tmpdir):
                    with patch('job_management.purge_old_logs') as mock_purge:
                        with patch('job_management.persist_job'):
                            with patch('job_management.send_slack_notification'):
                                with patch('job_management.logger'):
                                    finalize_failed_job(job_id, "Test error")
                                    mock_purge.assert_called_once()


class TestRunEraseJob:
    """Test erase job execution loop."""

    def test_run_erase_job_missing_job(self):
        """Test that run_erase_job handles missing job gracefully."""
        from job_management import run_erase_job
        from app_config import ERASE_JOBS, ERASE_JOBS_LOCK
        
        with ERASE_JOBS_LOCK:
            if "nonexistent-job" in ERASE_JOBS:
                del ERASE_JOBS["nonexistent-job"]
        
        run_erase_job("nonexistent-job")
        # Should not raise exception

    def test_run_erase_job_sets_running_status(self):
        """Test that run_erase_job sets job to running status."""
        from job_management import run_erase_job
        from app_config import ERASE_JOBS, ERASE_JOBS_LOCK
        
        job_id = "test-job-run-1"
        job = {
            "id": job_id,
            "status": "queued",
            "request": {
                "device": "/dev/sda",
                "interface_type": "sata",
                "method": "overwrite",
                "bay": "bay1",
                "capacity_bytes": 100 * 1024 * 1024
            }
        }
        
        with ERASE_JOBS_LOCK:
            ERASE_JOBS[job_id] = job
        
        with patch('job_management.send_slack_notification'):
            with patch('job_management.persist_job'):
                with patch('job_management.get_smart_data', return_value={"status": "PASSED"}):
                    with patch('job_management.pre_wipe_health_gate', return_value={"ok": True, "blocked": False}):
                        with patch('job_management.capture_before_state', return_value={"ok": True}):
                            with patch('job_management.prepare_erase_command', return_value={"ok": True, "command": ["dd", "if=/dev/zero"]}):
                                with patch('job_management.get_active_logs_dir', return_value=tempfile.gettempdir()):
                                    with patch('job_management.verification_for_method', return_value={"ok": True}):
                                        with patch('job_management.write_marker_and_verify', return_value={"ok": True}):
                                            with patch('job_management.load_policy', return_value={"post_erase_marker": False}):
                                                with patch('subprocess.Popen') as mock_popen:
                                                    mock_process = MagicMock()
                                                    mock_process.poll.return_value = 0
                                                    mock_process.returncode = 0
                                                    mock_popen.return_value = mock_process
                                                    
                                                    run_erase_job(job_id)
        
        with ERASE_JOBS_LOCK:
            assert ERASE_JOBS[job_id]["status"] in {"running", "completed", "failed"}

    def test_run_erase_job_overwrite_method(self):
        """Test overwrite method execution."""
        from job_management import run_erase_job
        from app_config import ERASE_JOBS, ERASE_JOBS_LOCK
        
        job_id = "test-job-run-2"
        job = {
            "id": job_id,
            "status": "queued",
            "request": {
                "device": "/dev/sda",
                "interface_type": "sata",
                "method": "overwrite",
                "bay": "bay1",
                "capacity_bytes": 100 * 1024 * 1024
            }
        }
        
        with ERASE_JOBS_LOCK:
            ERASE_JOBS[job_id] = job
        
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('job_management.send_slack_notification'):
                with patch('job_management.persist_job'):
                    with patch('job_management.get_smart_data', return_value={"status": "PASSED"}):
                        with patch('job_management.pre_wipe_health_gate', return_value={"ok": True, "blocked": False}):
                            with patch('job_management.capture_before_state', return_value={"ok": True}):
                                with patch('job_management.prepare_erase_command', return_value={"ok": True, "command": ["dd", "if=/dev/zero"]}):
                                    with patch('job_management.get_active_logs_dir', return_value=tmpdir):
                                        with patch('job_management.verification_for_method', return_value={"ok": True}):
                                            with patch('job_management.write_marker_and_verify', return_value={"ok": True}):
                                                with patch('job_management.load_policy', return_value={"post_erase_marker": False}):
                                                    with patch('subprocess.Popen') as mock_popen:
                                                        mock_process = MagicMock()
                                                        mock_process.poll.return_value = 0
                                                        mock_process.returncode = 0
                                                        mock_popen.return_value = mock_process
                                                        
                                                        run_erase_job(job_id)
        
        with ERASE_JOBS_LOCK:
            assert ERASE_JOBS[job_id]["status"] in {"completed", "failed"}

    def test_run_erase_job_hdparm_not_available(self):
        """Test that run_erase_job fails when hdparm not available for secure erase."""
        from job_management import run_erase_job
        from app_config import ERASE_JOBS, ERASE_JOBS_LOCK
        
        job_id = "test-job-run-3"
        job = {
            "id": job_id,
            "status": "queued",
            "request": {
                "device": "/dev/sda",
                "interface_type": "sata",
                "method": "secure_erase",
                "bay": "bay1",
                "capacity_bytes": 100 * 1024 * 1024
            }
        }
        
        with ERASE_JOBS_LOCK:
            ERASE_JOBS[job_id] = job
        
        with patch('job_management.send_slack_notification'):
            with patch('job_management.persist_job'):
                with patch('job_management.get_smart_data', return_value={"status": "PASSED"}):
                    with patch('job_management.pre_wipe_health_gate', return_value={"ok": True, "blocked": False}):
                        with patch('job_management.capture_before_state', return_value={"ok": True}):
                            with patch('job_management.resolve_verify_command_path', return_value=None):
                                with patch('job_management.finalize_failed_job') as mock_finalize:
                                    run_erase_job(job_id)
                                    mock_finalize.assert_called_once()
                                    assert "hdparm_not_available" in mock_finalize.call_args[0][1]

    def test_run_erase_job_interrupted_during_erase(self):
        """Test that run_erase_job handles interruption during erase."""
        from job_management import run_erase_job
        from app_config import ERASE_JOBS, ERASE_JOBS_LOCK
        import job_management
        
        job_id = "test-job-run-4"
        job = {
            "id": job_id,
            "status": "queued",
            "request": {
                "device": "/dev/sda",
                "interface_type": "sata",
                "method": "overwrite",
                "bay": "bay1",
                "capacity_bytes": 100 * 1024 * 1024
            }
        }
        
        with ERASE_JOBS_LOCK:
            ERASE_JOBS[job_id] = job
        
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                with patch('job_management.send_slack_notification'):
                    with patch('job_management.persist_job'):
                        with patch('job_management.get_smart_data', return_value={"status": "PASSED"}):
                            with patch('job_management.pre_wipe_health_gate', return_value={"ok": True, "blocked": False}):
                                with patch('job_management.capture_before_state', return_value={"ok": True}):
                                    with patch('job_management.prepare_erase_command', return_value={"ok": True, "command": ["dd", "if=/dev/zero"]}):
                                        with patch('job_management.get_active_logs_dir', return_value=tmpdir):
                                            with patch('subprocess.Popen') as mock_popen:
                                                mock_process = MagicMock()
                                                # Trigger signal on first poll to simulate interruption after job starts
                                                def poll_side_effect():
                                                    job_management._handle_job_signal(15, None)
                                                    return None
                                                mock_process.poll.side_effect = poll_side_effect
                                                mock_popen.return_value = mock_process
                                                
                                                run_erase_job(job_id)
            
            with ERASE_JOBS_LOCK:
                assert ERASE_JOBS[job_id]["status"] == "interrupted"
        finally:
            pass

    def test_run_erase_job_log_file_creation_failure(self):
        """Test that run_erase_job handles log file creation failure."""
        from job_management import run_erase_job
        from app_config import ERASE_JOBS, ERASE_JOBS_LOCK
        
        job_id = "test-job-run-5"
        job = {
            "id": job_id,
            "status": "queued",
            "request": {
                "device": "/dev/sda",
                "interface_type": "sata",
                "method": "overwrite",
                "bay": "bay1",
                "capacity_bytes": 100 * 1024 * 1024
            }
        }
        
        with ERASE_JOBS_LOCK:
            ERASE_JOBS[job_id] = job
        
        with patch('job_management.send_slack_notification'):
            with patch('job_management.persist_job'):
                with patch('job_management.get_smart_data', return_value={"status": "PASSED"}):
                    with patch('job_management.pre_wipe_health_gate', return_value={"ok": True, "blocked": False}):
                        with patch('job_management.capture_before_state', return_value={"ok": True}):
                            with patch('job_management.prepare_erase_command', return_value={"ok": True, "command": ["dd", "if=/dev/zero"]}):
                                with patch('job_management.get_active_logs_dir', return_value="/nonexistent/path"):
                                    with patch('job_management.finalize_failed_job') as mock_finalize:
                                        run_erase_job(job_id)
                                        mock_finalize.assert_called_once()
                                        assert "log_file_creation_failed" in mock_finalize.call_args[0][1]

    def test_run_erase_job_nvme_crypto_method(self):
        """Test NVMe crypto erase method."""
        from job_management import run_erase_job
        from app_config import ERASE_JOBS, ERASE_JOBS_LOCK
        
        job_id = "test-job-run-6"
        job = {
            "id": job_id,
            "status": "queued",
            "request": {
                "device": "/dev/nvme0n1",
                "interface_type": "nvme",
                "method": "crypto",
                "bay": "bay1",
                "capacity_bytes": 100 * 1024 * 1024
            }
        }
        
        with ERASE_JOBS_LOCK:
            ERASE_JOBS[job_id] = job
        
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('time.sleep'):
                with patch('job_management.send_slack_notification'):
                    with patch('job_management.persist_job'):
                        with patch('job_management.get_smart_data', return_value={"status": "PASSED"}):
                            with patch('job_management.pre_wipe_health_gate', return_value={"ok": True, "blocked": False}):
                                with patch('job_management.capture_before_state', return_value={"ok": True}):
                                    with patch('job_management.prepare_erase_command', return_value={"ok": True, "command": ["nvme", "sanitize", "/dev/nvme0", "--sanact", "4"]}):
                                        with patch('job_management.get_active_logs_dir', return_value=tmpdir):
                                            with patch('job_management.verification_for_method', return_value={"ok": True}):
                                                with patch('job_management.write_marker_and_verify', return_value={"ok": True}):
                                                    with patch('job_management.load_policy', return_value={"post_erase_marker": False}):
                                                        with patch('job_management.verify_nvme_sanitize', return_value={"ok": True}):
                                                            with patch('subprocess.Popen') as mock_popen:
                                                                mock_process = MagicMock()
                                                                mock_process.poll.return_value = 0
                                                                mock_process.returncode = 0
                                                                mock_popen.return_value = mock_process
                                                                
                                                                run_erase_job(job_id)
        
        with ERASE_JOBS_LOCK:
            assert ERASE_JOBS[job_id]["status"] in {"completed", "failed"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
