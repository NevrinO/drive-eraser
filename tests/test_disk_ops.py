# Unit tests for disk_ops.py OS drive detection
import pytest
import sys
import os
import json
import time
import threading
from unittest.mock import patch, MagicMock

# Add backend to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from disk_ops import get_os_parent_device, get_os_by_path, get_all_controllers, discover_drives, invalidate_drive_cache, _DRIVE_DATA_CACHE, _DRIVE_DATA_CACHE_TTL, _discovery_interrupted, _discovery_interrupt_lock


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


class TestDriveDataCache:
    """Test per-device drive data caching (Phase 1 of performance optimization)."""

    def setup_method(self):
        """Clear cache before each test."""
        invalidate_drive_cache()

    def teardown_method(self):
        """Clear cache after each test."""
        invalidate_drive_cache()

    def test_cache_hit_within_ttl(self):
        """Test that cache returns data within TTL window."""
        # Simulate a cache entry
        cache_key = ("pci-0000:00:1f.2-ata-1", "/dev/sda")
        mock_payload = {
            "present": True,
            "device": "/dev/sda",
            "serial": "TEST123",
            "model": "Test Drive",
            "status": "OK"
        }

        # Manually store in cache
        with patch('disk_ops._DRIVE_DATA_CACHE_LOCK'):
            _DRIVE_DATA_CACHE[cache_key] = {
                'data': mock_payload,
                'timestamp': time.time()
            }

        # Retrieve from cache
        from disk_ops import _get_cached_drive_payload
        cached = _get_cached_drive_payload(cache_key)

        assert cached is not None
        assert cached["serial"] == "TEST123"
        assert cached["model"] == "Test Drive"

    def test_cache_miss_after_ttl(self):
        """Test that cache expires after TTL."""
        cache_key = ("pci-0000:00:1f.2-ata-1", "/dev/sda")
        mock_payload = {"present": True, "device": "/dev/sda"}

        # Store with old timestamp (beyond TTL)
        with patch('disk_ops._DRIVE_DATA_CACHE_LOCK'):
            _DRIVE_DATA_CACHE[cache_key] = {
                'data': mock_payload,
                'timestamp': time.time() - _DRIVE_DATA_CACHE_TTL - 10
            }

        from disk_ops import _get_cached_drive_payload
        cached = _get_cached_drive_payload(cache_key)

        assert cached is None

    def test_invalidate_drive_cache_all(self):
        """Test that invalidate_drive_cache() clears all entries."""
        # Add multiple cache entries
        with patch('disk_ops._DRIVE_DATA_CACHE_LOCK'):
            _DRIVE_DATA_CACHE[("key1", "/dev/sda")] = {'data': {}, 'timestamp': time.time()}
            _DRIVE_DATA_CACHE[("key2", "/dev/sdb")] = {'data': {}, 'timestamp': time.time()}

        assert len(_DRIVE_DATA_CACHE) == 2

        # Invalidate all
        invalidate_drive_cache()

        assert len(_DRIVE_DATA_CACHE) == 0

    def test_invalidate_drive_cache_specific_device(self):
        """Test that invalidate_drive_cache(device) clears only that device."""
        # Add entries for different devices
        with patch('disk_ops._DRIVE_DATA_CACHE_LOCK'):
            _DRIVE_DATA_CACHE[("key1", "/dev/sda")] = {'data': {}, 'timestamp': time.time()}
            _DRIVE_DATA_CACHE[("key2", "/dev/sda")] = {'data': {}, 'timestamp': time.time()}
            _DRIVE_DATA_CACHE[("key3", "/dev/sdb")] = {'data': {}, 'timestamp': time.time()}

        assert len(_DRIVE_DATA_CACHE) == 3

        # Invalidate only /dev/sda
        invalidate_drive_cache(device="/dev/sda")

        assert len(_DRIVE_DATA_CACHE) == 1
        assert ("key3", "/dev/sdb") in _DRIVE_DATA_CACHE

    def test_cache_payload_deep_copy(self):
        """Test that cached payloads are deep-copied to prevent corruption."""
        cache_key = ("pci-0000:00:1f.2-ata-1", "/dev/sda")
        mock_payload = {
            "present": True,
            "device": "/dev/sda",
            "serial": "ORIGINAL",
            "nested": {"value": 42}
        }

        with patch('disk_ops._DRIVE_DATA_CACHE_LOCK'):
            _DRIVE_DATA_CACHE[cache_key] = {
                'data': mock_payload,
                'timestamp': time.time()
            }

        from disk_ops import _get_cached_drive_payload, _apply_drive_payload

        # Apply to bay record
        bay_info = {"bay": "bay0"}
        _apply_drive_payload(bay_info, mock_payload, is_os_drive=False)

        # Mutate the bay record
        bay_info["serial"] = "MODIFIED"
        bay_info["nested"]["value"] = 999

        # Cache should remain unchanged
        cached = _get_cached_drive_payload(cache_key)
        assert cached["serial"] == "ORIGINAL"
        assert cached["nested"]["value"] == 42


class TestPresenceDetectionUncached:
    """Test that presence detection (by-path resolution) is NOT cached for real-time detection."""

    def setup_method(self):
        """Clear cache and reset interruption flag before each test."""
        invalidate_drive_cache()
        global _discovery_interrupted
        with _discovery_interrupt_lock:
            _discovery_interrupted = False

    def teardown_method(self):
        """Clear cache after each test."""
        invalidate_drive_cache()

    @patch('disk_ops.resolve_bay_device')
    @patch('disk_ops.scan_pci_controllers')
    @patch('disk_ops.load_policy')
    @patch('builtins.open', create=True)
    @patch('os.path.exists')
    @patch('os.listdir')
    @patch('os.path.islink')
    @patch('os.path.realpath')
    def test_drive_removal_detected_realtime(self, mock_realpath, mock_islink, mock_listdir, mock_exists, mock_open, mock_load_policy, mock_pci, mock_resolve):
        """Test that drive removal is detected even with cached data."""
        # Mock bay map
        bay_map_content = {
            "bays": {
                "bay0": {
                    "by_path": "pci-0000:00:1f.2-ata-1",
                    "type": "sas_sata",
                    "role": "wipe"
                }
            }
        }

        mock_open.return_value.__enter__.return_value.read.return_value = json.dumps(bay_map_content)
        mock_exists.return_value = True
        mock_listdir.return_value = []
        mock_islink.return_value = True
        mock_realpath.return_value = "/dev/sda"
        mock_pci.return_value = []
        mock_load_policy.return_value = {"wipe_passphrase": "test"}

        # First call: drive present
        mock_resolve.return_value = ("pci-0000:00:1f.2-ata-1", "/dev/sda")
        with patch('disk_ops._get_os_by_path_cached', return_value=(None, None)):
            with patch('disk_ops._collect_drive_data') as mock_collect:
                mock_collect.return_value = {
                    "present": True,
                    "device": "/dev/sda",
                    "serial": "TEST123",
                    "status": "OK"
                }
                results1 = discover_drives(bay_map_path='/tmp/test_bay_map.json')

        assert isinstance(results1, list)
        assert len(results1) == 1
        assert results1[0]["present"] is True

        # Second call: drive removed (resolve_bay_device returns None)
        mock_resolve.return_value = (None, None)
        with patch('disk_ops._get_os_by_path_cached', return_value=(None, None)):
            results2 = discover_drives(bay_map_path='/tmp/test_bay_map.json')

        assert isinstance(results2, list)
        assert len(results2) == 1
        assert results2[0]["present"] is False
        assert results2[0]["status"] == "EMPTY"

    @patch('disk_ops.resolve_bay_device')
    @patch('disk_ops.scan_pci_controllers')
    @patch('disk_ops.load_policy')
    @patch('builtins.open', create=True)
    @patch('os.path.exists')
    @patch('os.listdir')
    @patch('os.path.islink')
    @patch('os.path.realpath')
    def test_drive_insertion_detected_realtime(self, mock_realpath, mock_islink, mock_listdir, mock_exists, mock_open, mock_load_policy, mock_pci, mock_resolve):
        """Test that drive insertion is detected even with cached data."""
        bay_map_content = {
            "bays": {
                "bay0": {
                    "by_path": "pci-0000:00:1f.2-ata-1",
                    "type": "sas_sata",
                    "role": "wipe"
                }
            }
        }

        mock_open.return_value.__enter__.return_value.read.return_value = json.dumps(bay_map_content)
        mock_exists.return_value = True
        mock_listdir.return_value = []
        mock_islink.return_value = True
        mock_realpath.return_value = "/dev/sda"
        mock_pci.return_value = []
        mock_load_policy.return_value = {"wipe_passphrase": "test"}

        # First call: drive absent
        mock_resolve.return_value = (None, None)
        with patch('disk_ops._get_os_by_path_cached', return_value=(None, None)):
            results1 = discover_drives(bay_map_path='/tmp/test_bay_map.json')

        assert isinstance(results1, list)
        assert len(results1) == 1
        assert results1[0]["present"] is False

        # Second call: drive inserted
        mock_resolve.return_value = ("pci-0000:00:1f.2-ata-1", "/dev/sda")
        with patch('disk_ops._get_os_by_path_cached', return_value=(None, None)):
            with patch('disk_ops._collect_drive_data') as mock_collect:
                mock_collect.return_value = {
                    "present": True,
                    "device": "/dev/sda",
                    "serial": "NEWDRIVE",
                    "status": "OK"
                }
                results2 = discover_drives(bay_map_path='/tmp/test_bay_map.json')

        assert isinstance(results2, list)
        assert len(results2) == 1
        assert results2[0]["present"] is True
        assert results2[0]["serial"] == "NEWDRIVE"


class TestCacheThreadSafety:
    """Test thread safety of cache operations."""

    def setup_method(self):
        """Clear cache before each test."""
        invalidate_drive_cache()

    def teardown_method(self):
        """Clear cache after each test."""
        invalidate_drive_cache()

    def test_concurrent_cache_reads(self):
        """Test that multiple threads can read from cache safely."""
        # Pre-populate cache
        cache_key = ("test-key", "/dev/sda")
        with patch('disk_ops._DRIVE_DATA_CACHE_LOCK'):
            _DRIVE_DATA_CACHE[cache_key] = {
                'data': {"serial": "TEST"},
                'timestamp': time.time()
            }

        from disk_ops import _get_cached_drive_payload

        results = []
        errors = []

        def read_cache():
            try:
                cached = _get_cached_drive_payload(cache_key)
                results.append(cached)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=read_cache) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 20
        assert all(r["serial"] == "TEST" for r in results)

    def test_concurrent_cache_invalidations(self):
        """Test that concurrent invalidations don't corrupt cache."""
        # Pre-populate cache with multiple entries
        with patch('disk_ops._DRIVE_DATA_CACHE_LOCK'):
            for i in range(10):
                _DRIVE_DATA_CACHE[(f"key{i}", f"/dev/sd{i}")] = {
                    'data': {"serial": f"DRIVE{i}"},
                    'timestamp': time.time()
                }

        errors = []

        def invalidate_all():
            try:
                invalidate_drive_cache()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=invalidate_all) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(_DRIVE_DATA_CACHE) == 0

    def test_concurrent_write_and_read(self):
        """Test that writes and reads can happen concurrently."""
        from disk_ops import _store_drive_payload, _get_cached_drive_payload

        errors = []
        results = []

        def writer(thread_id):
            try:
                for i in range(5):
                    cache_key = (f"key-{thread_id}-{i}", f"/dev/sd{thread_id}")
                    _store_drive_payload(cache_key, {"serial": f"DRIVE-{thread_id}-{i}"})
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                # Try to read any existing cache entries
                with patch('disk_ops._DRIVE_DATA_CACHE_LOCK'):
                    keys = list(_DRIVE_DATA_CACHE.keys())
                for key in keys:
                    cached = _get_cached_drive_payload(key)
                    if cached:
                        results.append(cached["serial"])
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(5):
            threads.append(threading.Thread(target=writer, args=(i,)))
        for i in range(5):
            threads.append(threading.Thread(target=reader))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        # Cache should have some entries
        assert len(_DRIVE_DATA_CACHE) > 0


class TestCacheInvalidationTriggers:
    """Test cache invalidation triggers in integration scenarios."""

    def setup_method(self):
        """Clear cache before each test."""
        invalidate_drive_cache()

    def teardown_method(self):
        """Clear cache after each test."""
        invalidate_drive_cache()

    def test_invalidation_after_job_completion(self):
        """Test that cache is invalidated after wipe job completion."""
        # Pre-populate cache
        with patch('disk_ops._DRIVE_DATA_CACHE_LOCK'):
            _DRIVE_DATA_CACHE[("key1", "/dev/sda")] = {'data': {}, 'timestamp': time.time()}
            _DRIVE_DATA_CACHE[("key2", "/dev/sdb")] = {'data': {}, 'timestamp': time.time()}

        assert len(_DRIVE_DATA_CACHE) == 2

        # Simulate job completion trigger (as in job_management.py)
        invalidate_drive_cache(device="/dev/sda")

        # Only /dev/sda should be invalidated
        assert len(_DRIVE_DATA_CACHE) == 1
        assert ("key2", "/dev/sdb") in _DRIVE_DATA_CACHE

    def test_invalidation_after_bay_map_save(self):
        """Test that cache is invalidated after bay map changes."""
        # Pre-populate cache
        with patch('disk_ops._DRIVE_DATA_CACHE_LOCK'):
            for i in range(5):
                _DRIVE_DATA_CACHE[(f"key{i}", f"/dev/sd{i}")] = {'data': {}, 'timestamp': time.time()}

        assert len(_DRIVE_DATA_CACHE) == 5

        # Simulate bay map save trigger (as in bay_mapping_routes.py)
        invalidate_drive_cache()

        # All entries should be cleared
        assert len(_DRIVE_DATA_CACHE) == 0

    def test_invalidation_after_policy_passphrase_change(self):
        """Test that cache is invalidated after policy passphrase change."""
        # Pre-populate cache with marker data (affected by passphrase HMAC)
        with patch('disk_ops._DRIVE_DATA_CACHE_LOCK'):
            _DRIVE_DATA_CACHE[("key1", "/dev/sda")] = {
                'data': {
                    "marker": {"status": "checksum_valid", "hmac_verified": True}
                },
                'timestamp': time.time()
            }
            _DRIVE_DATA_CACHE[("key2", "/dev/sdb")] = {
                'data': {
                    "marker": {"status": "checksum_valid", "hmac_verified": True}
                },
                'timestamp': time.time()
            }

        assert len(_DRIVE_DATA_CACHE) == 2

        # Simulate policy passphrase change trigger (as in admin_routes.py)
        invalidate_drive_cache()

        # All entries should be cleared since HMAC verification would be invalid
        assert len(_DRIVE_DATA_CACHE) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
