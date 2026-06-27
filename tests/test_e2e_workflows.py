# End-to-end workflow tests for Drive Eraser
import pytest
import sys
import os
import tempfile
import json
from unittest.mock import patch, MagicMock

# Add backend to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))


class TestE2EEraseWorkflow:
    """End-to-end tests for the complete erase workflow."""

    @pytest.fixture
    def test_config_dir(self):
        """Create a temporary directory for test configuration."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test policy.json
            policy = {
                "strict_audit_mode": False,
                "wipe_passphrase": "test-wipe-pass",
                "method_priority": {"sata": ["overwrite"]}
            }
            with open(os.path.join(tmpdir, "policy.json"), "w") as f:
                json.dump(policy, f)
            yield tmpdir

    @patch('job_management.get_os_by_path')
    def test_validate_single_bay_success(self, mock_os_path, test_config_dir):
        """Test validate_single_bay with valid input."""
        from job_management import validate_single_bay
        from common import load_policy

        # Mock OS drive detection to return a different device
        mock_os_path.return_value = ("/dev/sda", "pci-0000:00:1f.2-ata-1")

        policy = load_policy(test_config_dir)
        drives = [{
            "bay": "bay1",
            "device": "/dev/sdb",
            "configured_by_path": "pci-0000:00:1f.2-ata-2",
            "resolved_by_path": "/dev/sdb",
            "present": True,
            "locked": False,
            "role": "wipe",
            "supported_methods": ["overwrite"],
            "interface_type": "sata"
        }]

        validated, error, status = validate_single_bay(
            "Test Tech", "TICKET-001", "bay1", None, drives, policy
        )

        assert error is None
        assert status is None
        assert validated["device"] == "/dev/sdb"
        assert validated["method"] == "overwrite"
        assert validated["technician"] == "Test Tech"
        assert validated["ticket_number"] == "TICKET-001"

    @patch('job_management.get_os_by_path')
    def test_validate_single_bay_os_drive_protection(self, mock_os_path, test_config_dir):
        """Test that OS drive protection blocks erase workflow."""
        from job_management import validate_single_bay
        from common import load_policy

        # Mock OS drive detection to return the same device being tested
        mock_os_path.return_value = ("/dev/sdb", "pci-0000:00:1f.2-ata-2")

        policy = load_policy(test_config_dir)
        drives = [{
            "bay": "bay1",
            "device": "/dev/sdb",
            "configured_by_path": "pci-0000:00:1f.2-ata-2",
            "resolved_by_path": "/dev/sdb",
            "present": True,
            "locked": False,
            "role": "wipe",
            "supported_methods": ["overwrite"],
            "interface_type": "sata"
        }]

        validated, error, status = validate_single_bay(
            "Test Tech", "TICKET-001", "bay1", None, drives, policy
        )

        assert validated is None
        assert error is not None
        assert "OS drive" in error["error"]
        assert status == 403

    @patch('job_management.get_os_by_path')
    def test_validate_single_bay_strict_audit_validation(self, mock_os_path, test_config_dir):
        """Test that strict audit mode enforces technician and ticket requirements."""
        from job_management import validate_single_bay
        from common import load_policy

        mock_os_path.return_value = ("/dev/sda", "pci-0000:00:1f.2-ata-1")

        # Enable strict audit mode
        policy = load_policy(test_config_dir)
        policy["strict_audit_mode"] = True

        drives = [{
            "bay": "bay1",
            "device": "/dev/sdb",
            "configured_by_path": "pci-0000:00:1f.2-ata-2",
            "resolved_by_path": "/dev/sdb",
            "present": True,
            "locked": False,
            "role": "wipe",
            "supported_methods": ["overwrite"],
            "interface_type": "sata"
        }]

        # Test empty technician
        validated, error, status = validate_single_bay(
            "", "TICKET-001", "bay1", None, drives, policy
        )
        assert validated is None
        assert error is not None
        assert "technician" in error["error"].lower()
        assert status == 400

        # Test empty ticket number
        validated, error, status = validate_single_bay(
            "Test Tech", "", "bay1", None, drives, policy
        )
        assert validated is None
        assert error is not None
        assert "ticket" in error["error"].lower()
        assert status == 400

    def test_create_erase_job(self):
        """Test that erase job is created correctly."""
        from job_management import create_erase_job

        validated = {
            "technician": "Test Tech",
            "ticket_number": "TICKET-001",
            "bay": "bay1",
            "device": "/dev/sdb",
            "method": "overwrite",
            "recommended_method": "overwrite",
            "supported_methods": ["overwrite"],
            "drive": {
                "interface_type": "sata",
                "serial": "SN12345",
                "model": "Test Drive",
                "smart": {"capacity_bytes": 500000000000}
            }
        }

        job = create_erase_job(validated)
        assert job["status"] == "queued"
        assert job["request"]["device"] == "/dev/sdb"
        assert job["request"]["method"] == "overwrite"
        assert "id" in job
        assert "friendly_id" in job
        assert "created_at" in job


    @patch('subprocess.run')
    @patch('shutil.which')
    def test_prepare_erase_command_overwrite(self, mock_which, mock_run):
        """Test that overwrite command is prepared correctly."""
        from job_management import prepare_erase_command

        mock_which.return_value = "/bin/dd"
        mock_run.return_value = MagicMock(returncode=0)

        result = prepare_erase_command("/dev/sdb", "sata", "overwrite")
        assert result["ok"] is True
        assert "dd" in result["command"][0]
        assert "of=/dev/sdb" in " ".join(result["command"])

    @patch('subprocess.run')
    @patch('shutil.which')
    def test_prepare_erase_command_secure_erase(self, mock_which, mock_run):
        """Test that secure erase command is prepared correctly."""
        from job_management import prepare_erase_command

        mock_which.return_value = "/sbin/hdparm"
        mock_run.return_value = MagicMock(returncode=0)

        result = prepare_erase_command("/dev/sdb", "sata", "secure_erase")
        assert result["ok"] is True
        assert "hdparm" in result["command"][0]
        assert "--security-erase" in " ".join(result["command"])

    @patch('os.path.exists')
    @patch('os.listdir')
    @patch('os.path.islink')
    @patch('os.path.realpath')
    @patch('disk_ops.get_os_parent_device')
    def test_discovery_workflow_os_detection(self, mock_get_os_parent, mock_realpath, mock_islink, mock_listdir, mock_exists):
        """Test that OS drive detection works in discovery workflow."""
        from disk_ops import get_os_by_path

        mock_get_os_parent.return_value = "sda"
        mock_exists.return_value = True
        mock_listdir.return_value = ["pci-0000:00:1f.2-ata-1"]
        mock_islink.return_value = True
        mock_realpath.side_effect = lambda path: "/dev/sda" if "pci-0000" in path else path

        dev_node, by_path = get_os_by_path()

        assert dev_node == "/dev/sda"
        assert by_path == "pci-0000:00:1f.2-ata-1"

    def test_device_path_validation_workflow(self):
        """Test that device path validation works correctly."""
        from disk_utils import validate_device_path

        # Test valid path
        result = validate_device_path("/dev/sdb")
        assert result is True

        # Test invalid path (path traversal)
        result = validate_device_path("/dev/sda/../../etc/passwd")
        assert result is False

        # Test invalid path (newline injection)
        result = validate_device_path("/dev/sdb\n/etc/passwd")
        assert result is False


class TestE2ECertificateWorkflow:
    """End-to-end tests for certificate generation workflow."""

    @patch('certificates.load_policy')
    def test_certificate_generation_after_successful_erase(self, mock_load_policy):
        """Test that certificate is generated after successful erase."""
        from certificates import build_certificate
        import tempfile
        import os

        # Mock policy to provide wipe_passphrase for strict audit mode
        mock_load_policy.return_value = {
            "strict_audit_mode": False,
            "wipe_passphrase": "test-passphrase"
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('certificates.get_cert_dir', return_value=tmpdir):
                job = {
                    "id": "test-job-id",
                    "friendly_id": "CERT-20250101-ABC123",
                    "request": {
                        "technician": "Test Tech",
                        "ticket_number": "TICKET-001",
                        "bay": "bay1",
                        "serial": "SN12345",
                        "model": "Test Drive",
                        "capacity_bytes": 500000000000,
                        "method": "overwrite",
                        "interface_type": "sata"
                    },
                    "verification": {
                        "ok": True,
                        "status": "verified"
                    },
                    "result": {
                        "ok": True
                    }
                }

                result = build_certificate(job)
                # build_certificate returns the certificate dict directly
                assert result["id"] == "cert-CERT-20250101-ABC123"
                assert result["job_id"] == "test-job-id"
                assert "path" in result


class TestE2EErrorHandling:
    """End-to-end tests for error handling workflows."""

    def test_interrupted_job_workflow(self):
        """Test that interrupted jobs are handled gracefully."""
        from job_management import _check_job_interrupted, _handle_job_signal
        import job_management
        import signal

        # Capture current generation
        with job_management._job_interrupt_lock:
            gen_before = job_management._job_interrupt_generation

        # Not interrupted yet
        assert _check_job_interrupted(gen_before) is False

        # Simulate interruption signal
        _handle_job_signal(signal.SIGTERM, None)
        assert _check_job_interrupted(gen_before) is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
