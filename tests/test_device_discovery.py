# Unit tests for device_discovery.py
import pytest
import sys
import os
from unittest.mock import patch, MagicMock

# Add backend to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from device_discovery import (
    validate_device_path,
    validate_pci_address,
    scan_pci_controllers,
    _map_pci_class_to_type
)


class TestValidateDevicePath:
    """Test device path validation (Rule #9, #15)."""

    def test_valid_sata_device_path(self):
        """Test valid SATA device path."""
        assert validate_device_path("/dev/sda") is True
        assert validate_device_path("/dev/sdb") is True
        assert validate_device_path("/dev/sdc1") is True

    def test_valid_nvme_device_path(self):
        """Test valid NVMe device path."""
        assert validate_device_path("/dev/nvme0n1") is True
        assert validate_device_path("/dev/nvme1n1p1") is True

    def test_valid_mmc_device_path(self):
        """Test valid MMC device path."""
        assert validate_device_path("/dev/mmcblk0") is True
        assert validate_device_path("/dev/mmcblk0p1") is True

    def test_path_with_newline_rejected(self):
        """Test that paths with newlines are rejected (Rule #15)."""
        assert validate_device_path("/dev/sda\n") is False
        assert validate_device_path("/dev/sda\r") is False

    def test_path_with_carriage_return_rejected(self):
        """Test that paths with carriage returns are rejected."""
        assert validate_device_path("/dev/sda\r") is False

    def test_path_with_double_dot_rejected(self):
        """Test that paths with .. are rejected (Rule #9)."""
        assert validate_device_path("/dev/../sda") is False
        assert validate_device_path("/dev/sda/../sdb") is False

    def test_none_input_rejected(self):
        """Test that None input is rejected."""
        assert validate_device_path(None) is False

    def test_empty_string_rejected(self):
        """Test that empty string is rejected."""
        assert validate_device_path("") is False

    def test_non_string_input_rejected(self):
        """Test that non-string input is rejected."""
        assert validate_device_path(123) is False
        assert validate_device_path([]) is False

    def test_invalid_path_format_rejected(self):
        """Test that invalid path formats are rejected."""
        assert validate_device_path("sda") is False  # Missing /dev prefix
        assert validate_device_path("/etc/passwd") is False  # Not in /dev
        assert validate_device_path("/dev/sda; rm -rf /") is False  # Injection attempt

    def test_complex_valid_paths(self):
        """Test complex but valid device paths."""
        assert validate_device_path("/dev/disk/by-id/ata-ST1000DM003-1CH162_Z1D4K9RW") is True
        assert validate_device_path("/dev/disk/by-path/pci-0000:00:1f.2-ata-1") is True


class TestValidatePciAddress:
    """Test PCI address validation."""

    def test_valid_pci_addresses(self):
        """Test valid PCI address formats."""
        assert validate_pci_address("0000:00:1f.2") is True
        assert validate_pci_address("0000:01:00.0") is True
        assert validate_pci_address("0000:02:05.3") is True

    def test_case_insensitive_hex(self):
        """Test that hex digits are case-insensitive."""
        assert validate_pci_address("0000:00:1F.2") is True
        assert validate_pci_address("ABCD:EF:01.0") is True

    def test_invalid_format_missing_domain(self):
        """Test that missing domain is rejected."""
        assert validate_pci_address("00:1f.2") is False

    def test_invalid_format_missing_function(self):
        """Test that missing function is accepted (function is optional per implementation)."""
        assert validate_pci_address("0000:00:1f") is True

    def test_invalid_format_wrong_separator(self):
        """Test that wrong separators are rejected."""
        assert validate_pci_address("0000-00-1f.2") is False
        assert validate_pci_address("0000:00:1f:2") is False

    def test_invalid_hex_characters(self):
        """Test that invalid hex characters are rejected."""
        assert validate_pci_address("0000:00:1g.2") is False
        assert validate_pci_address("0000:00:1f.z") is False

    def test_none_input_rejected(self):
        """Test that None input is rejected."""
        assert validate_pci_address(None) is False

    def test_empty_string_rejected(self):
        """Test that empty string is rejected."""
        assert validate_pci_address("") is False

    def test_non_string_input_rejected(self):
        """Test that non-string input is rejected."""
        assert validate_pci_address(123) is False


class TestScanPciControllers:
    """Test PCI controller scanning."""

    @patch('pci_controllers.subprocess.run')
    def test_successful_scan(self, mock_run):
        """Test successful PCI controller scan."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="0000:00:1f.2 SATA controller: Intel Device 8c02 [0106] [8086:8c02]\n"
        )
        result = scan_pci_controllers(use_cache=False)
        assert len(result) > 0
        assert result[0]['pci_address'] == "0000:00:1f.2"

    @patch('pci_controllers.subprocess.run')
    def test_scan_with_cache(self, mock_run):
        """Test that cache is used when enabled."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="0000:00:1f.2 SATA controller: Intel Device 8c02 [0106] [8086:8c02]\n"
        )
        # First call should execute subprocess
        result1 = scan_pci_controllers(use_cache=True)
        # Second call should use cache
        result2 = scan_pci_controllers(use_cache=True)
        assert len(result1) == len(result2)

    @patch('pci_controllers.subprocess.run')
    def test_scan_failure_returns_empty(self, mock_run):
        """Test that scan failure returns empty list."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="lspci: command not found"
        )
        result = scan_pci_controllers(use_cache=False)
        assert result == []

    @patch('pci_controllers.subprocess.run')
    def test_scan_timeout(self, mock_run):
        """Test that timeout is handled."""
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired("lspci", 10)
        result = scan_pci_controllers(use_cache=False)
        assert result == []

    @patch('pci_controllers.subprocess.run')
    def test_filters_non_storage_controllers(self, mock_run):
        """Test that non-storage controllers are filtered out."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "0000:00:1f.2 SATA controller: Intel Device 8c02 [0106] [8086:8c02]\n"
                "0000:01:00.0 VGA compatible controller: NVIDIA Device [0300] [10DE:1234]\n"
            )
        )
        result = scan_pci_controllers(use_cache=False)
        # Should only include storage controller
        assert all('storage' in c.get('controller_type', '').lower() or c.get('class_code', '') in ['0101', '0104', '0105', '0106', '0107', '0108'] for c in result)


class TestMapPciClassToType:
    """Test PCI class to controller type mapping."""

    def test_sata_controller(self):
        """Test SATA controller mapping."""
        result = _map_pci_class_to_type("0106", "SATA controller")
        assert result == "sata"

    def test_sas_controller(self):
        """Test SAS controller mapping."""
        result = _map_pci_class_to_type("0107", "SAS controller")
        assert result == "sas"

    def test_nvme_controller(self):
        """Test NVMe controller mapping."""
        result = _map_pci_class_to_type("0108", "NVMe controller")
        assert result == "nvme"

    def test_raid_controller(self):
        """Test RAID controller mapping."""
        result = _map_pci_class_to_type("0104", "RAID controller")
        assert result == "raid"

    def test_scsi_controller(self):
        """Test SCSI controller mapping."""
        result = _map_pci_class_to_type("0100", "SCSI controller")
        assert result == "scsi"

    def test_unknown_controller(self):
        """Test unknown controller mapping."""
        result = _map_pci_class_to_type("0101", "Unknown controller")
        assert result == "ide"

    def test_case_insensitive_description(self):
        """Test that description matching is case-insensitive."""
        result = _map_pci_class_to_type("0106", "SATA CONTROLLER")
        assert result == "sata"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
