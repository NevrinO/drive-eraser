# Unit tests for disk_ops.py OS drive detection
import pytest
import sys
import os
import json
from unittest.mock import patch, MagicMock

# Add backend to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from disk_ops import get_os_parent_device, get_os_by_path, get_all_controllers, discover_drives


class TestGetOSParentDevice:
    """Test OS parent device detection."""

    @patch('os.stat')
    @patch('os.path.exists')
    @patch('os.path.realpath')
    def test_get_os_parent_device_from_uevent(self, mock_realpath, mock_exists, mock_stat):
        """Test detection via /sys/dev/block uevent file."""
        # Mock os.stat("/") to return device numbers
        mock_stat_result = MagicMock()
        mock_stat_result.st_dev = 2048  # major:minor
        mock_stat.return_value = mock_stat_result

        # Mock uevent file exists and contains DEVNAME
        mock_exists.return_value = True
        mock_realpath.return_value = "/sys/class/block/sda"

        # Mock open to return uevent content
        uevent_content = "DEVNAME=sda\nDEVTYPE=disk\n"
        with patch('builtins.open', create=True) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = uevent_content
            result = get_os_parent_device()

        assert result == "sda"

    @patch('os.stat')
    @patch('os.path.exists')
    @patch('subprocess.run')
    def test_get_os_parent_device_from_findmnt(self, mock_subprocess, mock_exists, mock_stat):
        """Test detection via findmnt as fallback."""
        # Mock os.stat("/") to return device numbers
        mock_stat_result = MagicMock()
        mock_stat_result.st_dev = 2048
        mock_stat.return_value = mock_stat_result

        # Mock uevent file does not exist
        mock_exists.side_effect = lambda path: "/sys/dev/block" not in path

        # Mock findmnt to return device
        mock_subprocess_result = MagicMock()
        mock_subprocess_result.returncode = 0
        mock_subprocess_result.stdout.strip.return_value = "/dev/sda"
        mock_subprocess.return_value = mock_subprocess_result

        result = get_os_parent_device()
        assert result == "sda"

    @patch('os.stat')
    @patch('os.path.exists')
    @patch('os.path.realpath')
    def test_get_os_parent_device_from_proc_mounts(self, mock_realpath, mock_exists, mock_stat):
        """Test detection via /proc/mounts as fallback."""
        # Mock os.stat("/") to return device numbers
        mock_stat_result = MagicMock()
        mock_stat_result.st_dev = 2048
        mock_stat.return_value = mock_stat_result

        # Mock uevent and findmnt paths don't exist, but sysfs path exists
        mock_exists.side_effect = lambda path: "/proc/mounts" in path or "/sys/class/block" in path
        # Mock realpath to return a path that resolves to the base device (not partition)
        # For /sys/class/block/sda, return path that ends with sda (not sda2)
        mock_realpath.side_effect = lambda path: "/sys/devices/pci0000:00/0000:00:1f.2/ata1/host0/target0:0:0/0:0:0:0/block/sda" if "sda" in path and "/sys/class/block/" in path else path

        # Mock /proc/mounts content
        mounts_content = "/dev/sda / ext4 rw 0 0\n"
        with patch('builtins.open', create=True) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = mounts_content
            result = get_os_parent_device()

        assert result == "sda"

    @patch('os.stat')
    def test_get_os_parent_device_exception_handling(self, mock_stat):
        """Test that exceptions return None gracefully."""
        mock_stat.side_effect = Exception("Test exception")
        result = get_os_parent_device()
        assert result is None

    @patch('os.stat')
    @patch('os.path.exists')
    @patch('os.path.realpath')
    def test_get_os_parent_device_dm_device(self, mock_realpath, mock_exists, mock_stat):
        """Test detection for device-mapper (dm-) devices."""
        # Mock os.stat("/") to return device numbers
        mock_stat_result = MagicMock()
        mock_stat_result.st_dev = 2048
        mock_stat.return_value = mock_stat_result

        # Mock uevent file exists and contains DEVNAME for dm device
        mock_exists.return_value = True
        # Mock realpath to resolve dm-0 to its slave, and sda to base device path
        def realpath_side_effect(path):
            if "dm-0" in path:
                return "/sys/class/block/sda"
            elif "/sys/class/block/sda" in path:
                return "/sys/devices/pci0000:00/0000:00:1f.2/ata1/host0/target0:0:0/0:0:0:0/block/sda"
            return path
        mock_realpath.side_effect = realpath_side_effect

        # Mock open to return uevent content for dm device
        uevent_content = "DEVNAME=dm-0\n"
        with patch('builtins.open', create=True) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = uevent_content
            # Mock os.listdir for slaves directory
            with patch('os.listdir') as mock_listdir:
                mock_listdir.return_value = ["sda"]
                result = get_os_parent_device()

        assert result == "sda"


class TestGetOSByPath:
    """Test OS drive by-path resolution."""

    @patch('disk_ops.get_os_parent_device')
    @patch('os.path.exists')
    @patch('os.listdir')
    @patch('os.path.islink')
    @patch('os.path.realpath')
    def test_get_os_by_path_found(self, mock_realpath, mock_islink, mock_listdir, mock_exists, mock_get_parent):
        """Test successful by-path resolution."""
        mock_get_parent.return_value = "sda"
        mock_exists.return_value = True
        mock_listdir.return_value = ["pci-0000:00:1f.2-ata-1"]
        mock_islink.return_value = True
        mock_realpath.side_effect = lambda path: "/dev/sda" if "pci-0000" in path else path

        dev_node, by_path = get_os_by_path()

        assert dev_node == "/dev/sda"
        assert by_path == "pci-0000:00:1f.2-ata-1"

    @patch('disk_ops.get_os_parent_device')
    def test_get_os_by_path_no_parent(self, mock_get_parent):
        """Test when parent device cannot be determined."""
        mock_get_parent.return_value = None
        dev_node, by_path = get_os_by_path()
        assert dev_node is None
        assert by_path is None

    @patch('disk_ops.get_os_parent_device')
    @patch('os.path.exists')
    def test_get_os_by_path_no_by_path_dir(self, mock_exists, mock_get_parent):
        """Test when /dev/disk/by-path does not exist."""
        mock_get_parent.return_value = "sda"
        mock_exists.return_value = False
        dev_node, by_path = get_os_by_path()
        assert dev_node == "/dev/sda"
        assert by_path is None

    @patch('disk_ops.get_os_parent_device')
    @patch('os.path.exists')
    @patch('os.listdir')
    @patch('os.path.islink')
    @patch('os.path.realpath')
    def test_get_os_by_path_partition_filtered(self, mock_realpath, mock_islink, mock_listdir, mock_exists, mock_get_parent):
        """Test that partition entries are filtered out."""
        mock_get_parent.return_value = "sda"
        mock_exists.return_value = True
        mock_listdir.return_value = ["pci-0000:00:1f.2-ata-1", "pci-0000:00:1f.2-ata-1-part1"]
        mock_islink.return_value = True
        mock_realpath.side_effect = lambda path: "/dev/sda" if "part1" not in path else "/dev/sda1"

        dev_node, by_path = get_os_by_path()

        # Should return the non-partition entry
        assert dev_node == "/dev/sda"
        assert by_path == "pci-0000:00:1f.2-ata-1"


class TestDiscoveryInterruption:
    """Test discovery interruption handling (Medium #34)."""

    def test_signal_handler_sets_interrupted_flag(self):
        """Test that signal handler sets the interrupted flag."""
        from disk_ops import _handle_discovery_signal, _check_discovery_interrupted

        # Initially not interrupted
        assert _check_discovery_interrupted() is False

        # Simulate signal handler
        _handle_discovery_signal(None, None)

        # Should now be interrupted
        assert _check_discovery_interrupted() is True

    def test_check_interrupted_thread_safe(self):
        """Test that _check_discovery_interrupted is thread-safe."""
        from disk_ops import _handle_discovery_signal, _check_discovery_interrupted
        import threading

        # Set interrupted flag
        _handle_discovery_signal(None, None)

        # Multiple threads should be able to check safely
        results = []
        def check_flag():
            results.append(_check_discovery_interrupted())

        threads = [threading.Thread(target=check_flag) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All should return True
        assert all(results)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
