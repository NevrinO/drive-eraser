# Extended tests for device_discovery.py
import pytest
import sys
import os
import tempfile
from unittest.mock import patch, MagicMock, Mock
import time

# Add backend to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))


class TestValidateDevicePath:
    """Test device path validation."""

    def test_valid_sata_device_path(self):
        """Test valid SATA device path."""
        from device_discovery import validate_device_path
        assert validate_device_path("/dev/sda") is True
        assert validate_device_path("/dev/sdb") is True
        assert validate_device_path("/dev/sdz") is True

    def test_valid_nvme_device_path(self):
        """Test valid NVMe device path."""
        from device_discovery import validate_device_path
        assert validate_device_path("/dev/nvme0n1") is True
        assert validate_device_path("/dev/nvme0n1p1") is True
        assert validate_device_path("/dev/nvme1n2") is True

    def test_valid_mmc_device_path(self):
        """Test valid MMC device path."""
        from device_discovery import validate_device_path
        assert validate_device_path("/dev/mmcblk0") is True
        assert validate_device_path("/dev/mmcblk0p1") is True

    def test_path_with_newline_rejected(self):
        """Test that path with newline is rejected."""
        from device_discovery import validate_device_path
        assert validate_device_path("/dev/sda\n") is False

    def test_path_with_carriage_return_rejected(self):
        """Test that path with carriage return is rejected."""
        from device_discovery import validate_device_path
        assert validate_device_path("/dev/sda\r") is False

    def test_path_with_double_dot_rejected(self):
        """Test that path with double dot is rejected."""
        from device_discovery import validate_device_path
        assert validate_device_path("/dev/../etc/passwd") is False

    def test_none_input_rejected(self):
        """Test that None input is rejected."""
        from device_discovery import validate_device_path
        assert validate_device_path(None) is False

    def test_empty_string_rejected(self):
        """Test that empty string is rejected."""
        from device_discovery import validate_device_path
        assert validate_device_path("") is False

    def test_non_string_input_rejected(self):
        """Test that non-string input is rejected."""
        from device_discovery import validate_device_path
        assert validate_device_path(123) is False
        assert validate_device_path([]) is False

    def test_invalid_path_format_rejected(self):
        """Test that invalid path format is rejected."""
        from device_discovery import validate_device_path
        assert validate_device_path("sda") is False  # Missing /dev/
        assert validate_device_path("/etc/passwd") is False  # Not /dev/
        assert validate_device_path("/dev/") is False  # Trailing slash

    def test_complex_valid_paths(self):
        """Test complex but valid device paths."""
        from device_discovery import validate_device_path
        assert validate_device_path("/dev/sda1") is True
        assert validate_device_path("/dev/nvme0n1p2") is True
        assert validate_device_path("/dev/mmcblk0p3") is True


class TestScanPciControllers:
    """Test PCI controller scanning."""

    def test_successful_scan(self):
        """Test successful PCI scan."""
        from device_discovery import scan_pci_controllers
        with patch('device_discovery.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="0000:00:1f.2 SATA controller: Intel Device 8c02 [0106] [8086:8c02]\n"
                       "0000:01:00.0 NVMe device: Samsung Device 1234 [0108] [1234:5678]"
            )
            result = scan_pci_controllers(use_cache=False)
            assert len(result) == 2
            assert result[0]['pci_address'] == '0000:00:1f.2'
            assert result[0]['controller_type'] == 'sata'
            assert result[1]['controller_type'] == 'nvme'

    def test_scan_with_cache(self):
        """Test that cache is used when enabled."""
        from device_discovery import scan_pci_controllers, _PCI_CACHE
        # Clear cache before test
        _PCI_CACHE['data'] = None
        _PCI_CACHE['timestamp'] = 0
        with patch('device_discovery.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="0000:00:1f.2 SATA controller [0106]")
            # First call
            result1 = scan_pci_controllers(use_cache=True)
            # Second call should use cache
            result2 = scan_pci_controllers(use_cache=True)
            assert len(result1) == len(result2)
            # subprocess should only be called once
            assert mock_run.call_count == 1

    def test_scan_without_cache(self):
        """Test that cache is bypassed when disabled."""
        from device_discovery import scan_pci_controllers
        with patch('device_discovery.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="0000:00:1f.2 SATA controller [0106]")
            # Both calls should bypass cache
            result1 = scan_pci_controllers(use_cache=False)
            result2 = scan_pci_controllers(use_cache=False)
            assert mock_run.call_count == 2

    def test_scan_failure_returns_empty(self):
        """Test that scan failure returns empty list."""
        from device_discovery import scan_pci_controllers
        with patch('device_discovery.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="lspci error")
            result = scan_pci_controllers(use_cache=False)
            assert result == []

    def test_scan_timeout(self):
        """Test that scan timeout is handled."""
        from device_discovery import scan_pci_controllers
        from subprocess import TimeoutExpired
        with patch('device_discovery.subprocess.run', side_effect=TimeoutExpired("lspci", 10)):
            result = scan_pci_controllers(use_cache=False)
            assert result == []

    def test_scan_command_not_found(self):
        """Test that missing lspci command is handled."""
        from device_discovery import scan_pci_controllers
        with patch('device_discovery.subprocess.run', side_effect=FileNotFoundError):
            result = scan_pci_controllers(use_cache=False)
            assert result == []

    def test_filters_non_storage_controllers(self):
        """Test that non-storage controllers are filtered out."""
        from device_discovery import scan_pci_controllers
        with patch('device_discovery.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="0000:00:02.0 VGA controller: Intel Device [0300]\n"
                       "0000:00:1f.2 SATA controller: Intel Device [0106]"
            )
            result = scan_pci_controllers(use_cache=False)
            assert len(result) == 1
            assert result[0]['controller_type'] == 'sata'

    def test_cache_expiration(self):
        """Test that cache expires after TTL."""
        from device_discovery import scan_pci_controllers, _PCI_CACHE_TTL, _PCI_CACHE
        # Clear cache before test
        _PCI_CACHE['data'] = None
        _PCI_CACHE['timestamp'] = 0
        with patch('device_discovery.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="0000:00:1f.2 SATA controller [0106]")
            # First call
            result1 = scan_pci_controllers(use_cache=True)
            # Expire cache
            with patch('device_discovery.time.time', return_value=time.time() + _PCI_CACHE_TTL + 1):
                # Second call should bypass expired cache
                result2 = scan_pci_controllers(use_cache=True)
            assert mock_run.call_count == 2


class TestMapPciClassToType:
    """Test PCI class code to controller type mapping."""

    def test_sata_controller(self):
        """Test SATA controller mapping."""
        from device_discovery import _map_pci_class_to_type
        assert _map_pci_class_to_type('0106', 'SATA controller') == 'sata'

    def test_sas_controller(self):
        """Test SAS controller mapping."""
        from device_discovery import _map_pci_class_to_type
        assert _map_pci_class_to_type('0107', 'SAS controller') == 'sas'

    def test_nvme_controller(self):
        """Test NVMe controller mapping."""
        from device_discovery import _map_pci_class_to_type
        assert _map_pci_class_to_type('0108', 'NVMe device') == 'nvme'

    def test_raid_controller(self):
        """Test RAID controller mapping."""
        from device_discovery import _map_pci_class_to_type
        assert _map_pci_class_to_type('0104', 'RAID controller') == 'raid'

    def test_scsi_controller(self):
        """Test SCSI controller mapping."""
        from device_discovery import _map_pci_class_to_type
        assert _map_pci_class_to_type('0100', 'SCSI controller') == 'scsi'

    def test_unknown_controller(self):
        """Test unknown controller mapping."""
        from device_discovery import _map_pci_class_to_type
        assert _map_pci_class_to_type('9999', 'Unknown device') == 'unknown'

    def test_case_insensitive_description(self):
        """Test that description matching is case-insensitive."""
        from device_discovery import _map_pci_class_to_type
        assert _map_pci_class_to_type('9999', 'NVMe Controller') == 'nvme'
        assert _map_pci_class_to_type('9999', 'SATA CONTROLLER') == 'sata'


class TestDiscoverControllersAndDevices:
    """Test controller and device discovery."""

    def test_successful_discovery(self):
        """Test successful discovery."""
        from device_discovery import discover_controllers_and_devices
        with patch('device_discovery.scan_pci_controllers', return_value=[
            {'pci_address': '0000:00:1f.2', 'controller_type': 'sata', 'vendor_id': '8086', 'device_id': '8c02'}
        ]):
            with patch('os.path.exists', return_value=True):
                with patch('os.listdir', return_value=['sda', 'sdb']):
                    with patch('device_discovery.validate_device_path', return_value=True):
                        with patch('device_discovery.get_controller_for_device', return_value={
                            'pci_address': '0000:00:1f.2', 'controller_type': 'sata'
                        }):
                            result = discover_controllers_and_devices(use_cache=False)
                            assert 'sata' in result
                            assert len(result['sata']) == 2

    def test_discovery_with_cache(self):
        """Test that discovery cache is used."""
        from device_discovery import discover_controllers_and_devices
        with patch('device_discovery.scan_pci_controllers', return_value=[]):
            with patch('os.path.exists', return_value=True):
                with patch('os.listdir', return_value=[]):
                    # First call
                    result1 = discover_controllers_and_devices(use_cache=True)
                    # Second call should use cache
                    result2 = discover_controllers_and_devices(use_cache=True)
                    assert result1 == result2

    def test_skips_partitions(self):
        """Test that partitions are skipped."""
        from device_discovery import discover_controllers_and_devices
        with patch('device_discovery.scan_pci_controllers', return_value=[]):
            with patch('os.path.exists', return_value=True):
                with patch('os.listdir', return_value=['sda', 'sda1', 'sdb2']):
                    result = discover_controllers_and_devices(use_cache=False)
                    # Partitions should be filtered out
                    assert all('sda1' not in d.get('device_name', '') for d in result.get('sata', []))

    def test_skips_device_mapper(self):
        """Test that device mapper devices are skipped."""
        from device_discovery import discover_controllers_and_devices
        with patch('device_discovery.scan_pci_controllers', return_value=[]):
            with patch('os.path.exists', return_value=True):
                with patch('os.listdir', return_value=['dm-0', 'dm-1', 'sda']):
                    result = discover_controllers_and_devices(use_cache=False)
                    # Device mapper should be filtered out
                    assert all('dm-' not in d.get('device_name', '') for d in result.get('sata', []))

    def test_groups_by_controller_type(self):
        """Test that devices are grouped by controller type."""
        from device_discovery import discover_controllers_and_devices
        with patch('device_discovery.scan_pci_controllers', return_value=[
            {'pci_address': '0000:00:1f.2', 'controller_type': 'sata'},
            {'pci_address': '0000:01:00.0', 'controller_type': 'nvme'}
        ]):
            with patch('os.path.exists', return_value=True):
                with patch('os.listdir', return_value=['sda', 'nvme0n1']):
                    with patch('device_discovery.validate_device_path', return_value=True):
                        with patch('device_discovery.get_controller_for_device') as mock_get:
                            mock_get.side_effect = [
                                {'pci_address': '0000:00:1f.2', 'controller_type': 'sata'},
                                {'pci_address': '0000:01:00.0', 'controller_type': 'nvme'}
                            ]
                            result = discover_controllers_and_devices(use_cache=False)
                            assert len(result['sata']) == 1
                            assert len(result['nvme']) == 1

    def test_handles_missing_controller(self):
        """Test that devices without controller are added to unknown."""
        from device_discovery import discover_controllers_and_devices, _DISCOVERY_CACHE
        # Clear cache before test
        _DISCOVERY_CACHE['data'] = None
        _DISCOVERY_CACHE['timestamp'] = 0
        with patch('device_discovery.scan_pci_controllers', return_value=[]):
            with patch('os.path.exists', return_value=True):
                with patch('os.listdir', return_value=['sda']):
                    with patch('device_discovery.validate_device_path', return_value=True):
                        with patch('device_discovery.get_controller_for_device', return_value=None):
                            result = discover_controllers_and_devices(use_cache=False)
                            # Devices without controllers are added to unknown bucket
                            assert len(result['unknown']) == 1

    def test_sysfs_block_not_exists(self):
        """Test handling when /sys/class/block doesn't exist."""
        from device_discovery import discover_controllers_and_devices
        with patch('os.path.exists', return_value=False):
            result = discover_controllers_and_devices(use_cache=False)
            assert all(len(devices) == 0 for devices in result.values())


class TestGetNvmeControllerInfo:
    """Test NVMe controller information retrieval."""

    def test_invalid_device_path(self):
        """Test that invalid device path is rejected."""
        from device_discovery import get_nvme_controller_info
        assert get_nvme_controller_info("/dev/invalid") is None

    def test_non_nvme_device(self):
        """Test that non-NVMe device is rejected."""
        from device_discovery import get_nvme_controller_info
        with patch('device_discovery.validate_device_path', return_value=True):
            assert get_nvme_controller_info("/dev/sda") is None

    def test_successful_json_parsing(self):
        """Test successful JSON parsing."""
        from device_discovery import get_nvme_controller_info
        with patch('device_discovery.validate_device_path', return_value=True):
            with patch('device_discovery._get_nvme_list_data', return_value={
                'Devices': [
                    {
                        'DevicePath': '/dev/nvme0n1',
                        'Name': 'nvme0n1',
                        'ModelNumber': 'Samsung 970',
                        'SerialNumber': 'S123456',
                        'Firmware': '1.0.0'
                    }
                ]
            }):
                result = get_nvme_controller_info("/dev/nvme0n1")
                assert result is not None
                assert result['model'] == 'Samsung 970'
                assert result['serial'] == 'S123456'

    def test_fallback_to_text_parsing(self):
        """Test fallback to text output parsing."""
        from device_discovery import get_nvme_controller_info
        with patch('device_discovery.validate_device_path', return_value=True):
            with patch('device_discovery._get_nvme_list_data', return_value={
                'raw_text': '/dev/nvme0n1 Samsung 970\n'
            }):
                result = get_nvme_controller_info("/dev/nvme0n1")
                assert result is not None
                assert result['type'] == 'nvme'

    def test_no_nvme_data(self):
        """Test handling when no NVMe data is available."""
        from device_discovery import get_nvme_controller_info
        with patch('device_discovery.validate_device_path', return_value=True):
            with patch('device_discovery._get_nvme_list_data', return_value=None):
                assert get_nvme_controller_info("/dev/nvme0n1") is None


class TestGetSataControllerPorts:
    """Test SATA controller port enumeration."""

    def test_invalid_pci_address(self):
        """Test that invalid PCI address is rejected."""
        from device_discovery import get_sata_controller_ports
        assert get_sata_controller_ports("invalid") == []

    def test_successful_port_enumeration(self):
        """Test successful port enumeration."""
        from device_discovery import get_sata_controller_ports
        with patch('device_discovery.validate_pci_address', return_value=True):
            with patch('device_discovery.discover_controllers_and_devices', return_value={
                'sata': [
                    {'device_path': '/dev/sda', 'controller': {'pci_address': '0000:00:1f.2'}},
                    {'device_path': '/dev/sdb', 'controller': {'pci_address': '0000:00:1f.2'}}
                ]
            }):
                result = get_sata_controller_ports('0000:00:1f.2')
                assert len(result) == 2
                assert '/dev/sda' in result
                assert '/dev/sdb' in result

    def test_filters_by_pci_address(self):
        """Test that devices are filtered by PCI address."""
        from device_discovery import get_sata_controller_ports
        with patch('device_discovery.validate_pci_address', return_value=True):
            with patch('device_discovery.discover_controllers_and_devices', return_value={
                'sata': [
                    {'device_path': '/dev/sda', 'controller': {'pci_address': '0000:00:1f.2'}},
                    {'device_path': '/dev/sdb', 'controller': {'pci_address': '0000:01:00.0'}}
                ]
            }):
                result = get_sata_controller_ports('0000:00:1f.2')
                assert len(result) == 1
                assert '/dev/sda' in result
                assert '/dev/sdb' not in result


class TestIsEnclosureDevice:
    """Test enclosure device detection."""

    def test_enclosure_device_detected(self):
        """Test that enclosure device is detected."""
        from device_discovery import is_enclosure_device
        with tempfile.TemporaryDirectory() as tmpdir:
            device_path = os.path.join(tmpdir, 'device')
            os.makedirs(device_path)
            type_file = os.path.join(device_path, 'type')
            with open(type_file, 'w') as f:
                f.write('enclosure')
            
            result = is_enclosure_device(tmpdir)
            assert result is True

    def test_processor_device_detected(self):
        """Test that processor device is detected."""
        from device_discovery import is_enclosure_device
        with tempfile.TemporaryDirectory() as tmpdir:
            device_path = os.path.join(tmpdir, 'device')
            os.makedirs(device_path)
            type_file = os.path.join(device_path, 'type')
            with open(type_file, 'w') as f:
                f.write('processor')
            
            result = is_enclosure_device(tmpdir)
            assert result is True

    def test_regular_drive_not_enclosure(self):
        """Test that regular drive is not enclosure."""
        from device_discovery import is_enclosure_device
        with tempfile.TemporaryDirectory() as tmpdir:
            device_path = os.path.join(tmpdir, 'device')
            os.makedirs(device_path)
            type_file = os.path.join(device_path, 'type')
            with open(type_file, 'w') as f:
                f.write('disk')
            
            result = is_enclosure_device(tmpdir)
            assert result is False

    def test_type_read_error_returns_false(self):
        """Test that type read error returns False."""
        from device_discovery import is_enclosure_device
        with tempfile.TemporaryDirectory() as tmpdir:
            result = is_enclosure_device(tmpdir)
            assert result is False

    def test_cache_usage(self):
        """Test that cache is used when provided."""
        from device_discovery import is_enclosure_device
        cache = {}
        with tempfile.TemporaryDirectory() as tmpdir:
            device_path = os.path.join(tmpdir, 'device')
            os.makedirs(device_path)
            type_file = os.path.join(device_path, 'type')
            with open(type_file, 'w') as f:
                f.write('enclosure')
            
            # First call
            result1 = is_enclosure_device(tmpdir, device_type_cache=cache)
            # Second call should use cache
            result2 = is_enclosure_device(tmpdir, device_type_cache=cache)
            assert result1 == result2
            assert tmpdir in cache


class TestGetMaxSlotFromEnclosure:
    """Test enclosure slot enumeration."""

    def test_enclosure_directory_not_exists(self):
        """Test handling when enclosure directory doesn't exist."""
        from device_discovery import get_max_slot_from_enclosure
        with patch('os.path.exists', return_value=False):
            result = get_max_slot_from_enclosure(use_cache=False)
            assert result == 0

    def test_successful_slot_enumeration(self):
        """Test successful slot enumeration."""
        from device_discovery import get_max_slot_from_enclosure
        with patch('os.path.exists', return_value=True):
            with patch('os.listdir') as mock_listdir:
                # First call: list enclosures
                # Second call: list slots in enclosure 0
                mock_listdir.side_effect = [['0'], ['slot_1', 'slot_2', 'slot_5', 'slot_10']]
                with patch('builtins.open', create=True) as mock_open:
                    mock_file = MagicMock()
                    # Test with different slot_number values to ensure max-finding logic works
                    mock_file.read.side_effect = ['1', '2', '5', '10']
                    mock_open.return_value.__enter__.return_value = mock_file
                    result = get_max_slot_from_enclosure(use_cache=False)
                    assert result == 10

    def test_fallback_to_slot_id_parsing(self):
        """Test fallback to slot_id parsing when slot_number file missing."""
        from device_discovery import get_max_slot_from_enclosure
        with patch('os.path.exists') as mock_exists:
            # Return True for enclosure base, False for slot_number files
            # Use more specific path pattern to avoid false positives
            def exists_side_effect(path):
                # Check if path ends with 'slot_number' (the actual file name)
                if path.endswith('slot_number'):
                    return False
                return True
            mock_exists.side_effect = exists_side_effect
            with patch('os.listdir') as mock_listdir:
                # First call: list enclosures
                # Second call: list slots in enclosure 0
                mock_listdir.side_effect = [['0'], ['slot_1', 'slot_2', 'slot_5', 'slot_10']]
                # When slot_number file doesn't exist, should parse from slot_id
                result = get_max_slot_from_enclosure(use_cache=False)
                assert result == 10

    def test_invalid_slot_number_ignored(self):
        """Test that invalid slot numbers are ignored."""
        from device_discovery import get_max_slot_from_enclosure
        with patch('os.path.exists', return_value=True):
            with patch('os.listdir') as mock_listdir:
                # First call: list enclosures
                # Second call: list slots in enclosure 0
                mock_listdir.side_effect = [['0'], ['slot_1', 'slot_2', 'slot_invalid', 'slot_10']]
                with patch('builtins.open', create=True) as mock_open:
                    mock_file = MagicMock()
                    # Return valid slot number for valid slots, invalid for others
                    mock_file.read.side_effect = ['1', '2', 'invalid', '10']
                    mock_open.return_value.__enter__.return_value = mock_file
                    result = get_max_slot_from_enclosure(use_cache=False)
                    # Should ignore invalid slot_number and still find max valid
                    assert result == 10

    def test_cache_usage(self):
        """Test that cache is used when enabled."""
        from device_discovery import get_max_slot_from_enclosure, _ENCLOSURE_CACHE
        # Clear cache before test for isolation
        _ENCLOSURE_CACHE['data'] = None
        _ENCLOSURE_CACHE['timestamp'] = 0
        with patch('os.path.exists', return_value=False):
            # First call
            result1 = get_max_slot_from_enclosure(use_cache=True)
            # Second call should use cache
            result2 = get_max_slot_from_enclosure(use_cache=True)
            assert result1 == result2


class TestGetScsiHostSlotProjections:
    """Test SCSI host slot projections."""

    def test_scsi_host_directory_not_exists(self):
        """Test handling when SCSI host directory doesn't exist."""
        from device_discovery import get_scsi_host_slot_projections
        with patch('os.path.exists', return_value=False):
            result = get_scsi_host_slot_projections()
            assert result == []

    def test_successful_projection(self):
        """Test successful slot projection."""
        from device_discovery import get_scsi_host_slot_projections
        with patch('os.path.exists', return_value=True):
            with patch('os.path.isdir') as mock_isdir:
                # SCSI device directories exist, but block directories don't (empty slots)
                def isdir_side_effect(path):
                    if 'block' in path:
                        return False
                    return True
                mock_isdir.side_effect = isdir_side_effect
                with patch('os.listdir') as mock_listdir:
                    # Use a callable to handle dynamic number of listdir calls
                    def listdir_side_effect(path):
                        if 'scsi_host' in path:
                            return ['host0']
                        elif 'scsi_device' in path:
                            return ['0:0:0:0', '0:0:1:0']
                        else:
                            # Block directory calls (empty slots)
                            return []
                    mock_listdir.side_effect = listdir_side_effect
                    with patch('os.path.realpath', return_value='/sys/devices/pci0000:00/0000:00:1f.2/ata1/host0'):
                        with patch('device_discovery.is_enclosure_device', return_value=False):
                            with patch('device_discovery.get_max_slot_from_enclosure', return_value=2):
                                result = get_scsi_host_slot_projections()
                                assert len(result) == 3  # slots 0, 1, 2
                                assert result[0]['pci_address'] == '0000:00:1f.2'
                                assert result[0]['host_number'] == 0

    def test_filters_enclosure_devices(self):
        """Test that enclosure devices are filtered."""
        from device_discovery import get_scsi_host_slot_projections
        with patch('os.path.exists', return_value=True):
            with patch('os.listdir', side_effect=['host0', '0:0:0:0']):
                with patch('os.path.isdir', return_value=True):
                    with patch('os.path.realpath', return_value='/sys/devices/pci0000:00/0000:00:1f.2/ata1/host0'):
                        with patch('device_discovery.is_enclosure_device', return_value=True):
                            with patch('device_discovery.get_max_slot_from_enclosure', return_value=0):
                                result = get_scsi_host_slot_projections()
                                # Enclosure devices should be filtered out
                                assert len(result) == 0

    def test_enforces_projection_limit(self):
        """Test that projection limit is enforced."""
        from device_discovery import get_scsi_host_slot_projections
        with patch('os.path.exists', return_value=True):
            with patch('os.listdir', side_effect=['host0', '0:0:0:0']):
                with patch('os.path.isdir', return_value=True):
                    with patch('os.path.realpath', return_value='/sys/devices/pci0000:00/0000:00:1f.2/ata1/host0'):
                        with patch('device_discovery.is_enclosure_device', return_value=False):
                            with patch('device_discovery.get_max_slot_from_enclosure', return_value=2000):  # Exceeds limit
                                result = get_scsi_host_slot_projections()
                                # Should be limited to MAX_TOTAL_PROJECTIONS
                                assert len(result) <= 1000

    def test_detects_occupied_slots(self):
        """Test that occupied slots are detected."""
        from device_discovery import get_scsi_host_slot_projections
        with patch('os.path.exists', return_value=True):
            with patch('os.path.isdir') as mock_isdir:
                # SCSI device directories exist, block directory exists for slot 0 only
                def isdir_side_effect(path):
                    if '0:0:0:0' in path and 'block' in path:
                        return True  # Slot 0 has block device
                    elif '0:0:1:0' in path and 'block' in path:
                        return False  # Slot 1 is empty
                    return True
                mock_isdir.side_effect = isdir_side_effect
                with patch('os.listdir') as mock_listdir:
                    # First call: list SCSI hosts
                    # Second call: list SCSI devices
                    # Third call: list block entries for slot 0
                    # Fourth call: list block entries for slot 1 (should not be called due to isdir=False)
                    mock_listdir.side_effect = [['host0'], ['0:0:0:0'], ['sda'], []]
                    with patch('os.path.realpath', return_value='/sys/devices/pci0000:00/0000:00:1f.2/ata1/host0'):
                        with patch('device_discovery.is_enclosure_device', return_value=False):
                            with patch('device_discovery.get_max_slot_from_enclosure', return_value=1):
                                result = get_scsi_host_slot_projections()
                                assert len(result) == 2  # slots 0, 1
                                # Slot 0 should be occupied
                                assert result[0]['device_path'] == '/dev/sda'
                                assert result[0]['device_name'] == 'sda'
                                # Slot 1 should be empty
                                assert result[1]['device_path'] is None
                                assert result[1]['device_name'] is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
