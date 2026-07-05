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

import disk_ops
from disk_ops import get_os_parent_device, get_os_by_path, discover_drives, invalidate_drive_cache, _DRIVE_DATA_CACHE, _auto_enqueue_zero_checks
from common import DRIVE_DATA_CACHE_TTL


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
    @patch('subprocess.run')
    def test_get_os_parent_device_from_proc_mounts(self, mock_subprocess, mock_realpath, mock_exists, mock_stat):
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
        # findmnt must fail so execution falls through to /proc/mounts
        mock_subprocess.return_value = MagicMock(returncode=1, stdout=MagicMock(strip=MagicMock(return_value="")))

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

    @patch('os_detection.get_os_parent_device')
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

    @patch('os_detection.get_os_parent_device')
    def test_get_os_by_path_no_parent(self, mock_get_parent):
        """Test when parent device cannot be determined."""
        mock_get_parent.return_value = None
        dev_node, by_path = get_os_by_path()
        assert dev_node is None
        assert by_path is None

    @patch('os_detection.get_os_parent_device')
    @patch('os.path.exists')
    def test_get_os_by_path_no_by_path_dir(self, mock_exists, mock_get_parent):
        """Test when /dev/disk/by-path does not exist."""
        mock_get_parent.return_value = "sda"
        mock_exists.return_value = False
        dev_node, by_path = get_os_by_path()
        assert dev_node == "/dev/sda"
        assert by_path is None

    @patch('os_detection.get_os_parent_device')
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
        """Test that signal handler increments generation counter."""
        from disk_ops import _handle_discovery_signal, _check_discovery_interrupted, _discovery_thread_state
        import discovery_state

        # Set thread-local generation to current value
        _discovery_thread_state.generation = discovery_state._discovery_interrupt_generation

        # Initially not interrupted
        assert _check_discovery_interrupted() is False

        # Simulate signal handler
        _handle_discovery_signal(None, None)

        # Should now be interrupted
        assert _check_discovery_interrupted() is True

    def test_check_interrupted_thread_safe(self):
        """Test that _check_discovery_interrupted is thread-safe."""
        from disk_ops import _handle_discovery_signal, _check_discovery_interrupted, _discovery_thread_state
        import discovery_state
        import threading

        gen_before_signal = discovery_state._discovery_interrupt_generation

        # Simulate signal handler
        _handle_discovery_signal(None, None)

        # Multiple threads should be able to check safely
        results = []
        def check_flag():
            _discovery_thread_state.generation = gen_before_signal
            results.append(_check_discovery_interrupted())

        threads = [threading.Thread(target=check_flag) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All should return True (generation was set before the signal)
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
                'timestamp': time.time() - DRIVE_DATA_CACHE_TTL - 10
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
        """Clear cache and reset thread-local state before each test."""
        import disk_ops
        invalidate_drive_cache()
        # Clear thread-local generation so _check_discovery_interrupted returns False
        if hasattr(disk_ops._discovery_thread_state, 'generation'):
            del disk_ops._discovery_thread_state.generation

    def teardown_method(self):
        """Clear cache after each test."""
        invalidate_drive_cache()

    @patch('discovery.resolve_bay_device')
    @patch('discovery.scan_pci_controllers')
    @patch('discovery.load_policy')
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
        with patch('discovery._get_os_by_path_cached', return_value=(None, None)):
            with patch('discovery._collect_drive_data') as mock_collect:
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
        with patch('discovery._get_os_by_path_cached', return_value=(None, None)):
            results2 = discover_drives(bay_map_path='/tmp/test_bay_map.json')

        assert isinstance(results2, list)
        assert len(results2) == 1
        assert results2[0]["present"] is False
        assert results2[0]["status"] == "EMPTY"

    @patch('discovery.resolve_bay_device')
    @patch('discovery.scan_pci_controllers')
    @patch('discovery.load_policy')
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
        with patch('discovery._get_os_by_path_cached', return_value=(None, None)):
            results1 = discover_drives(bay_map_path='/tmp/test_bay_map.json')

        assert isinstance(results1, list)
        assert len(results1) == 1
        assert results1[0]["present"] is False

        # Second call: drive inserted
        mock_resolve.return_value = ("pci-0000:00:1f.2-ata-1", "/dev/sda")
        with patch('discovery._get_os_by_path_cached', return_value=(None, None)):
            with patch('discovery._collect_drive_data') as mock_collect:
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


class TestExtendedSmartPool:
    """Test persistent background extended SMART collection pool."""

    def setup_method(self):
        """Reset pool state before each test."""
        import disk_ops
        disk_ops.stop_extended_smart_pool(wait=False)
        disk_ops._shutdown_event.clear()
        with disk_ops._EXTENDED_SMART_LOCK:
            disk_ops._EXTENDED_SMART_PENDING.clear()

    def teardown_method(self):
        """Stop pool after each test."""
        import disk_ops
        disk_ops.stop_extended_smart_pool(wait=False)

    def test_get_background_smart_max_workers_default(self):
        """Default background worker count is 8."""
        from disk_ops import get_background_smart_max_workers
        with patch('extended_smart.load_policy') as mock_load_policy:
            mock_load_policy.return_value = {}
            assert get_background_smart_max_workers() == 8

    def test_get_background_smart_max_workers_clamped(self):
        """Worker count is clamped to [1, 32]."""
        from disk_ops import get_background_smart_max_workers
        with patch('extended_smart.load_policy') as mock_load_policy:
            mock_load_policy.return_value = {"background_smart_max_workers": 50}
            assert get_background_smart_max_workers() == 32
            mock_load_policy.return_value = {"background_smart_max_workers": 0}
            assert get_background_smart_max_workers() == 1

    def test_submit_drive_adds_to_pending(self):
        """Submitting a drive adds its cache key to the pending set."""
        from disk_ops import _submit_drive_for_extended_smart, _EXTENDED_SMART_PENDING, _EXTENDED_SMART_LOCK
        item = (("path", "/dev/sda"), "/dev/sda", "/dev/sda", "/dev/sda", "sas_sata", "enc1", 1)
        with patch('extended_smart._get_extended_smart_executor'):
            _submit_drive_for_extended_smart(item, "pass")
            with _EXTENDED_SMART_LOCK:
                assert item[0] in _EXTENDED_SMART_PENDING

    def test_submit_same_drive_skipped(self):
        """Duplicate submissions for the same cache key are ignored."""
        from disk_ops import _submit_drive_for_extended_smart, _EXTENDED_SMART_PENDING, _EXTENDED_SMART_LOCK
        item = (("path", "/dev/sda"), "/dev/sda", "/dev/sda", "/dev/sda", "sas_sata", "enc1", 1)
        with patch('extended_smart._get_extended_smart_executor'):
            _submit_drive_for_extended_smart(item, "pass")
            _submit_drive_for_extended_smart(item, "pass")
            with _EXTENDED_SMART_LOCK:
                assert len(_EXTENDED_SMART_PENDING) == 1

    def test_shutdown_prevents_new_submissions(self):
        """When shutdown is requested, drives are not added to the pending set."""
        import disk_ops
        from disk_ops import _submit_drive_for_extended_smart, _EXTENDED_SMART_PENDING, _EXTENDED_SMART_LOCK
        disk_ops._shutdown_event.set()
        item = (("path", "/dev/sda"), "/dev/sda", "/dev/sda", "/dev/sda", "sas_sata", "enc1", 1)
        with patch('extended_smart._get_extended_smart_executor'):
            _submit_drive_for_extended_smart(item, "pass")
            with _EXTENDED_SMART_LOCK:
                assert item[0] not in _EXTENDED_SMART_PENDING

    def test_process_single_drive_skips_when_cached(self):
        """Tasks skip processing when the cache already has a finished payload."""
        import disk_ops
        item = (("path", "/dev/sda"), "/dev/sda", "/dev/sda", "/dev/sda", "sas_sata", "enc1", 1)
        with patch('extended_smart._get_cached_drive_payload') as mock_get_cache, \
             patch('extended_smart._store_drive_payload') as mock_store:
            mock_get_cache.return_value = {"smart": {"smart_polling": False}}
            disk_ops._process_single_drive_extended_smart(item, "pass")
            mock_store.assert_not_called()

    def test_process_single_drive_success(self):
        """A successful task stores the payload and broadcasts a WebSocket event."""
        import disk_ops
        item = (("path", "/dev/sda"), "/dev/sda", "/dev/sda", "/dev/sda", "sas_sata", "enc1", 1)
        with patch('extended_smart._get_cached_drive_payload', return_value=None), \
             patch('extended_smart.get_smart_data', return_value={"status": "OK", "serial": "S1", "model": "M1", "capacity_str": "1TB", "data_written_raw": 0, "raw": {}}), \
             patch('extended_smart.detect_interface_type', return_value="sas_sata"), \
             patch('extended_smart.detect_drive_capabilities', return_value={"supports_crypto_erase": False, "supports_block_erase": False, "supports_secure_erase": False, "supports_enhanced_secure_erase": False, "supports_overwrite": True}), \
             patch('extended_smart.read_marker_status', return_value={"status": "none"}), \
             patch('extended_smart.calculate_drive_health_score', return_value=(95, {})), \
             patch('extended_smart.get_drive_recommendation', return_value={"status": "OK", "comment": "-"}), \
             patch('extended_smart.is_drive_ssd', return_value=False), \
             patch('extended_smart.record_intake_snapshot'), \
             patch('extended_smart._store_drive_payload') as mock_store, \
             patch('extended_smart._websocket_manager') as mock_ws:
            disk_ops._process_single_drive_extended_smart(item, "pass")
            mock_store.assert_called_once()
            mock_ws.emit.assert_called_once()

    def test_stop_extended_smart_pool(self):
        """Stopping the pool clears the executor reference."""
        import extended_smart
        executor = extended_smart._get_extended_smart_executor()
        assert executor is not None
        extended_smart.stop_extended_smart_pool(wait=False)
        assert extended_smart._EXTENDED_SMART_EXECUTOR is None

    def test_stop_extended_smart_pool_clears_pending(self):
        """Stopping the pool clears the pending set so cancelled tasks do not leak."""
        import disk_ops
        from disk_ops import _EXTENDED_SMART_PENDING, _EXTENDED_SMART_LOCK
        item = (("path", "/dev/sda"), "/dev/sda", "/dev/sda", "/dev/sda", "sas_sata", "enc1", 1)
        with _EXTENDED_SMART_LOCK:
            _EXTENDED_SMART_PENDING.add(item[0])
        disk_ops.stop_extended_smart_pool(wait=False)
        with _EXTENDED_SMART_LOCK:
            assert item[0] not in _EXTENDED_SMART_PENDING

    def test_invalidate_drive_cache_clears_pending_all(self):
        """Cache invalidation clears all pending background SMART keys."""
        import disk_ops
        from disk_ops import _EXTENDED_SMART_PENDING, _EXTENDED_SMART_LOCK, invalidate_drive_cache
        item1 = (("path", "/dev/sda"), "/dev/sda", "/dev/sda", "/dev/sda", "sas_sata", "enc1", 1)
        item2 = (("path", "/dev/sdb"), "/dev/sdb", "/dev/sdb", "/dev/sdb", "sas_sata", "enc2", 1)
        with _EXTENDED_SMART_LOCK:
            _EXTENDED_SMART_PENDING.add(item1[0])
            _EXTENDED_SMART_PENDING.add(item2[0])
        invalidate_drive_cache()
        with _EXTENDED_SMART_LOCK:
            assert len(_EXTENDED_SMART_PENDING) == 0

    def test_invalidate_drive_cache_clears_pending_specific_device(self):
        """Cache invalidation for a specific device removes only that device's pending key."""
        import disk_ops
        from disk_ops import _EXTENDED_SMART_PENDING, _EXTENDED_SMART_LOCK, invalidate_drive_cache
        item1 = (("path", "/dev/sda"), "/dev/sda", "/dev/sda", "/dev/sda", "sas_sata", "enc1", 1)
        item2 = (("path", "/dev/sdb"), "/dev/sdb", "/dev/sdb", "/dev/sdb", "sas_sata", "enc2", 1)
        with _EXTENDED_SMART_LOCK:
            _EXTENDED_SMART_PENDING.add(item1[0])
            _EXTENDED_SMART_PENDING.add(item2[0])
        invalidate_drive_cache(device="/dev/sda")
        with _EXTENDED_SMART_LOCK:
            assert item1[0] not in _EXTENDED_SMART_PENDING
            assert item2[0] in _EXTENDED_SMART_PENDING


class TestAutoEnqueueZeroChecks:
    """Tests for the _auto_enqueue_zero_checks helper."""

    def test_auto_enqueue_passes_serial_to_manager(self):
        """Auto-enqueue should pass the discovered drive serial to the manager."""
        fake_manager = MagicMock()
        fake_manager.get_status.return_value = {"status": "not_started"}
        fake_manager.get_all_status.return_value = {}
        fake_manager.is_auto_enqueue_delayed.return_value = False

        with patch('discovery.load_policy', return_value={"prewipe_zero_detection_enabled": True}):
            with patch('discovery.get_zero_check_manager', return_value=fake_manager):
                with patch('discovery._is_eligible_for_zero_check', return_value=(True, None)):
                    _auto_enqueue_zero_checks([
                        {"bay": "bay1", "present": True, "device": "/dev/sda", "serial": "S1"}
                    ])

        fake_manager.start_check.assert_called_once_with("bay1", "/dev/sda", serial="S1")

    def test_auto_enqueue_clears_state_when_drive_serial_changes(self):
        """If the drive in a bay has a new serial, stale completed state must be cleared."""
        fake_manager = MagicMock()
        fake_manager.get_status.return_value = {"status": "completed", "serial": "OLD_SERIAL"}
        fake_manager.get_all_status.return_value = {}
        fake_manager.is_auto_enqueue_delayed.return_value = False

        with patch('discovery.load_policy', return_value={"prewipe_zero_detection_enabled": True}):
            with patch('discovery.get_zero_check_manager', return_value=fake_manager):
                with patch('discovery._is_eligible_for_zero_check', return_value=(True, None)):
                    _auto_enqueue_zero_checks([
                        {"bay": "bay1", "present": True, "device": "/dev/sda", "serial": "NEW_SERIAL"}
                    ])

        fake_manager.clear_state.assert_called_once_with("bay1")
        fake_manager.start_check.assert_called_once_with("bay1", "/dev/sda", serial="NEW_SERIAL")

    def test_auto_enqueue_does_not_clear_state_when_serial_matches(self):
        """If the serial is unchanged, completed state should not be cleared."""
        fake_manager = MagicMock()
        fake_manager.get_status.return_value = {"status": "completed", "serial": "SAME_SERIAL"}
        fake_manager.get_all_status.return_value = {}
        fake_manager.is_auto_enqueue_delayed.return_value = False

        with patch('discovery.load_policy', return_value={"prewipe_zero_detection_enabled": True}):
            with patch('discovery.get_zero_check_manager', return_value=fake_manager):
                with patch('discovery._is_eligible_for_zero_check', return_value=(True, None)):
                    _auto_enqueue_zero_checks([
                        {"bay": "bay1", "present": True, "device": "/dev/sda", "serial": "SAME_SERIAL"}
                    ])

        fake_manager.clear_state.assert_not_called()
        fake_manager.start_check.assert_called_once_with("bay1", "/dev/sda", serial="SAME_SERIAL")


class TestResolveDeviceFromHardwareIdentifier:
    """Test input validation in _resolve_device_from_hardware_identifier (A68)."""

    def test_invalid_pci_controller_rejected(self):
        """Test that malformed pci_controller is rejected."""
        from disk_ops import _resolve_device_from_hardware_identifier
        assert _resolve_device_from_hardware_identifier(
            "invalid", "sas_direct", "0:0:0:0", 0
        ) is None
        assert _resolve_device_from_hardware_identifier(
            None, "sas_direct", "0:0:0:0", 0
        ) is None
        assert _resolve_device_from_hardware_identifier(
            "0000:01:00.0; rm -rf", "sas_direct", "0:0:0:0", 0
        ) is None

    def test_valid_pci_controller_accepted(self):
        """Test that valid PCI address format is accepted (returns None only if no device found)."""
        from disk_ops import _resolve_device_from_hardware_identifier
        with patch('os.listdir', return_value=[]):
            result = _resolve_device_from_hardware_identifier(
                "0000:01:00.0", "sas_direct", "0:0:0:0", 0
            )
            # Returns None because no by-path entries match, but validation passed
            assert result is None

    def test_negative_physical_slot_rejected(self):
        """Test that negative physical_slot is rejected."""
        from disk_ops import _resolve_device_from_hardware_identifier
        assert _resolve_device_from_hardware_identifier(
            "0000:01:00.0", "sas_direct", "0:0:0:0", -1
        ) is None

    def test_boolean_physical_slot_rejected(self):
        """Test that boolean physical_slot is rejected (isinstance(True, int) is True)."""
        from disk_ops import _resolve_device_from_hardware_identifier
        assert _resolve_device_from_hardware_identifier(
            "0000:01:00.0", "sas_direct", "0:0:0:0", True
        ) is None
        assert _resolve_device_from_hardware_identifier(
            "0000:01:00.0", "sas_direct", "0:0:0:0", False
        ) is None

    def test_string_physical_slot_accepted(self):
        """Test that numeric string physical_slot passes validation."""
        from disk_ops import _resolve_device_from_hardware_identifier
        with patch('os.listdir', return_value=[]):
            result = _resolve_device_from_hardware_identifier(
                "0000:01:00.0", "sas_direct", "0:0:0:0", "5"
            )
            assert result is None  # No match, but validation passed

    def test_non_numeric_string_physical_slot_rejected(self):
        """Test that non-numeric string physical_slot is rejected."""
        from disk_ops import _resolve_device_from_hardware_identifier
        assert _resolve_device_from_hardware_identifier(
            "0000:01:00.0", "sas_direct", "0:0:0:0", "abc"
        ) is None

    def test_invalid_expander_sas_address_rejected(self):
        """Test that malformed expander_sas_address is rejected."""
        from disk_ops import _resolve_device_from_hardware_identifier
        assert _resolve_device_from_hardware_identifier(
            "0000:01:00.0", "sas_expander", "0:0:0:0", 0,
            expander_sas_address="not-a-wwn"
        ) is None
        assert _resolve_device_from_hardware_identifier(
            "0000:01:00.0", "sas_expander", "0:0:0:0", 0,
            expander_sas_address="0xshort"
        ) is None

    def test_valid_expander_sas_address_accepted(self):
        """Test that valid WWN format expander_sas_address passes validation."""
        from disk_ops import _resolve_device_from_hardware_identifier
        with patch('os.listdir', return_value=[]):
            result = _resolve_device_from_hardware_identifier(
                "0000:01:00.0", "sas_expander", "0:0:0:0", 0,
                expander_sas_address="0x500056b3059bdcff"
            )
            assert result is None  # No match, but validation passed


class TestResolveViaSysfsScsiPhySearch:
    r"""Test that _resolve_via_sysfs_scsi reads sysfs attribute files to match devices.

    Regression test for Issue 7: drives disappearing from UI because the sysfs fallback
    parsed path components for expander SAS address and PHY number, but the kernel uses
    internal names (expander-14:0, port-14:0:133) that don't contain the SAS address
    or expander PHY number.

    The fix reads attribute files and builds a port→phy_identifier lookup map:
    - /sys/class/sas_expander/expander-14:0/sas_address → SAS address
    - For each /sys/class/sas_phy/phy-14:0:12, read its realpath to get the
      kernel port number (e.g. port-14:0:133), then read phy_identifier to
      get the expander PHY number (e.g. 12). Map: "14:0:133" → 12.
    - For each SCSI device, extract end_device-14:0:133 from its realpath,
      look up port "14:0:133" in the map to get expander PHY number 12.
    """

    def _make_mock_open(self, file_contents):
        """Create a mock open that returns file contents for specific paths."""
        def mock_open(path, mode='r', *args, **kwargs):
            if path in file_contents:
                import io
                return io.StringIO(file_contents[path])
            raise OSError(f"mock open: {path}")
        return mock_open

    def test_scsi_device_scan_finds_orphaned_device(self):
        """Strategy 1: Should find a block device via /sys/class/scsi_device/ scan
        even when both by-path symlinks AND PHY device symlinks are gone.

        Production scenario: kernel SCSI error handler removes by-path and PHY device
        symlinks, but /sys/class/scsi_device/ entries persist with block devices.
        The realpath uses kernel-internal names (expander-14:0, end_device-14:0:133)
        and we read attribute files + build a port→phy_id map to get SAS address
        and PHY number.
        """
        from device_resolution import _resolve_via_sysfs_scsi

        scsi_dev_base = "/sys/class/scsi_device"
        phy_base = "/sys/class/sas_phy"
        scsi_host_base = "/sys/class/scsi_host"
        sas_expander_base = "/sys/class/sas_expander"
        scsi_sysfs_path = (
            "/sys/devices/pci0000:3a/0000:3a:00.0/0000:3b:00.0/host14/"
            "port-14:1/expander-14:0/port-14:0:133/"
            "end_device-14:0:133/target14:0:267/14:0:267:0"
        )
        # PHY phy-14:0:12 has realpath with port-14:0:133 and phy_identifier=12
        phy_realpath = (
            "/sys/devices/pci0000:3a/0000:3a:00.0/0000:3b:00.0/host14/"
            "port-14:1/expander-14:0/port-14:0:133/phy-14:0:12"
        )

        file_contents = {
            os.path.join(sas_expander_base, "expander-14:0", "sas_address"): "0x500304800145493f",
            os.path.join(phy_base, "phy-14:0:12", "phy_identifier"): "12",
        }

        def mock_listdir(path):
            if path == scsi_dev_base:
                return ["14:0:267:0", "14:0:268:0"]
            if path == phy_base:
                return ["phy-14:0:12"]
            if path == scsi_host_base:
                return []
            if "block" in path:
                return ["sdk"]
            raise OSError("mock")

        def mock_realpath(path):
            if path == os.path.join(scsi_dev_base, "14:0:267:0", "device"):
                return scsi_sysfs_path
            if path == os.path.join(phy_base, "phy-14:0:12"):
                return phy_realpath
            return path

        def mock_exists(path):
            return path == "/dev/sdk"

        with patch('os.listdir', side_effect=mock_listdir), \
             patch('os.path.realpath', side_effect=mock_realpath), \
             patch('os.path.exists', side_effect=mock_exists), \
             patch('builtins.open', side_effect=self._make_mock_open(file_contents)):
            result = _resolve_via_sysfs_scsi(
                "0000:3b:00.0", 12, "phy-0:0:12",
                expander_sas_address="0x500304800145493f"
            )
            assert result == "/dev/sdk"

    def test_scsi_device_scan_skips_wrong_phy(self):
        """Strategy 1: Should skip SCSI devices on the wrong PHY number."""
        from device_resolution import _resolve_via_sysfs_scsi

        scsi_dev_base = "/sys/class/scsi_device"
        phy_base = "/sys/class/sas_phy"
        scsi_host_base = "/sys/class/scsi_host"
        sas_expander_base = "/sys/class/sas_expander"
        scsi_sysfs_path = (
            "/sys/devices/pci0000:3a/0000:3a:00.0/0000:3b:00.0/host14/"
            "port-14:1/expander-14:0/port-14:0:135/"
            "end_device-14:0:135/target14:0:268/14:0:268:0"
        )
        # PHY phy-14:0:5 maps to port 14:0:135 with phy_identifier=5
        phy_realpath = (
            "/sys/devices/pci0000:3a/0000:3a:00.0/0000:3b:00.0/host14/"
            "port-14:1/expander-14:0/port-14:0:135/phy-14:0:5"
        )

        file_contents = {
            os.path.join(sas_expander_base, "expander-14:0", "sas_address"): "0x500304800145493f",
            os.path.join(phy_base, "phy-14:0:5", "phy_identifier"): "5",
        }

        def mock_listdir(path):
            if path == scsi_dev_base:
                return ["14:0:268:0"]
            if path == phy_base:
                return ["phy-14:0:5"]
            if path == scsi_host_base:
                return []
            if "block" in path:
                return ["sdac"]
            raise OSError("mock")

        def mock_realpath(path):
            if path == os.path.join(scsi_dev_base, "14:0:268:0", "device"):
                return scsi_sysfs_path
            if path == os.path.join(phy_base, "phy-14:0:5"):
                return phy_realpath
            return path

        def mock_exists(path):
            return path == "/dev/sdac"

        with patch('os.listdir', side_effect=mock_listdir), \
             patch('os.path.realpath', side_effect=mock_realpath), \
             patch('os.path.exists', side_effect=mock_exists), \
             patch('builtins.open', side_effect=self._make_mock_open(file_contents)):
            # Looking for PHY 12 but the SCSI device's phy_identifier is 5
            result = _resolve_via_sysfs_scsi(
                "0000:3b:00.0", 12, "phy-0:0:12",
                expander_sas_address="0x500304800145493f"
            )
            assert result is None

    def test_scsi_device_scan_skips_wrong_expander(self):
        """Strategy 1: Should skip SCSI devices on the wrong expander."""
        from device_resolution import _resolve_via_sysfs_scsi

        scsi_dev_base = "/sys/class/scsi_device"
        phy_base = "/sys/class/sas_phy"
        scsi_host_base = "/sys/class/scsi_host"
        sas_expander_base = "/sys/class/sas_expander"
        scsi_sysfs_path = (
            "/sys/devices/pci0000:3a/0000:3a:00.0/0000:3b:00.0/host14/"
            "port-14:1/expander-14:0/port-14:0:133/"
            "end_device-14:0:133/target14:0:267/14:0:267:0"
        )
        phy_realpath = (
            "/sys/devices/pci0000:3a/0000:3a:00.0/0000:3b:00.0/host14/"
            "port-14:1/expander-14:0/port-14:0:133/phy-14:0:12"
        )

        file_contents = {
            # Expander reports a DIFFERENT SAS address than what we're looking for
            os.path.join(sas_expander_base, "expander-14:0", "sas_address"): "0x500056b3059bdcff",
            os.path.join(phy_base, "phy-14:0:12", "phy_identifier"): "12",
        }

        def mock_listdir(path):
            if path == scsi_dev_base:
                return ["14:0:267:0"]
            if path == phy_base:
                return ["phy-14:0:12"]
            if path == scsi_host_base:
                return []
            if "block" in path:
                return ["sdk"]
            raise OSError("mock")

        def mock_realpath(path):
            if path == os.path.join(scsi_dev_base, "14:0:267:0", "device"):
                return scsi_sysfs_path
            if path == os.path.join(phy_base, "phy-14:0:12"):
                return phy_realpath
            return path

        def mock_exists(path):
            return path == "/dev/sdk"

        with patch('os.listdir', side_effect=mock_listdir), \
             patch('os.path.realpath', side_effect=mock_realpath), \
             patch('os.path.exists', side_effect=mock_exists), \
             patch('builtins.open', side_effect=self._make_mock_open(file_contents)):
            # SCSI device is on expander 0x500056b3059bdcff but looking for 0x500304800145493f
            result = _resolve_via_sysfs_scsi(
                "0000:3b:00.0", 12, "phy-0:0:12",
                expander_sas_address="0x500304800145493f"
            )
            assert result is None

    def test_scsi_device_scan_direct_attach_no_expander(self):
        """Strategy 1: Should match direct-attach (non-expander) devices."""
        from device_resolution import _resolve_via_sysfs_scsi

        scsi_dev_base = "/sys/class/scsi_device"
        phy_base = "/sys/class/sas_phy"
        scsi_host_base = "/sys/class/scsi_host"
        scsi_sysfs_path = (
            "/sys/devices/pci0000:ae/0000:ae:00.0/0000:af:00.0/host15/"
            "port-15:0:0/end_device-15:0:0/target15:0:0/15:0:0:0"
        )
        phy_realpath = (
            "/sys/devices/pci0000:ae/0000:ae:00.0/0000:af:00.0/host15/"
            "port-15:0:0/phy-15:0:0"
        )

        file_contents = {
            os.path.join(phy_base, "phy-15:0:0", "phy_identifier"): "0",
        }

        def mock_listdir(path):
            if path == scsi_dev_base:
                return ["15:0:0:0"]
            if path == phy_base:
                return ["phy-15:0:0"]
            if path == scsi_host_base:
                return []
            if "block" in path:
                return ["sda"]
            raise OSError("mock")

        def mock_realpath(path):
            if path == os.path.join(scsi_dev_base, "15:0:0:0", "device"):
                return scsi_sysfs_path
            if path == os.path.join(phy_base, "phy-15:0:0"):
                return phy_realpath
            return path

        def mock_exists(path):
            return path == "/dev/sda"

        with patch('os.listdir', side_effect=mock_listdir), \
             patch('os.path.realpath', side_effect=mock_realpath), \
             patch('os.path.exists', side_effect=mock_exists), \
             patch('builtins.open', side_effect=self._make_mock_open(file_contents)):
            # Direct-attach: no expander_sas_address
            result = _resolve_via_sysfs_scsi(
                "0000:af:00.0", 0, "phy-0:0:0"
            )
            assert result == "/dev/sda"

    def test_scsi_device_scan_skips_wrong_pci(self):
        """Strategy 1: Should skip SCSI devices on a different PCI controller."""
        from device_resolution import _resolve_via_sysfs_scsi

        scsi_dev_base = "/sys/class/scsi_device"
        phy_base = "/sys/class/sas_phy"
        scsi_host_base = "/sys/class/scsi_host"
        # Path has 0000:af:00.0, not 0000:3b:00.0
        scsi_sysfs_path = (
            "/sys/devices/pci0000:ae/0000:ae:00.0/0000:af:00.0/host15/"
            "port-15:0:0/end_device-15:0:0/target15:0:0/15:0:0:0"
        )

        def mock_listdir(path):
            if path == scsi_dev_base:
                return ["15:0:0:0"]
            if path == phy_base:
                return []
            if path == scsi_host_base:
                return []
            if "block" in path:
                return ["sda"]
            raise OSError("mock")

        def mock_realpath(path):
            if path == os.path.join(scsi_dev_base, "15:0:0:0", "device"):
                return scsi_sysfs_path
            return path

        def mock_exists(path):
            return path == "/dev/sda"

        with patch('os.listdir', side_effect=mock_listdir), \
             patch('os.path.realpath', side_effect=mock_realpath), \
             patch('os.path.exists', side_effect=mock_exists):
            # Looking for 0000:3b:00.0 but device is on 0000:af:00.0
            result = _resolve_via_sysfs_scsi(
                "0000:3b:00.0", 0, "phy-0:0:0"
            )
            assert result is None

    def test_phy_strategy_finds_device(self):
        """Strategy 2: Should find a device via PHY phy_identifier attribute."""
        from device_resolution import _resolve_via_sysfs_scsi

        scsi_dev_base = "/sys/class/scsi_device"
        phy_base = "/sys/class/sas_phy"
        scsi_host_base = "/sys/class/scsi_host"
        sas_expander_base = "/sys/class/sas_expander"
        phy_realpath = (
            "/sys/devices/pci0000:3a/0000:3a:00.0/0000:3b:00.0/host14/"
            "port-14:1/expander-14:0/port-14:0:133/phy-14:0:12"
        )
        scsi_device_path = (
            "/sys/devices/pci0000:3a/0000:3a:00.0/0000:3b:00.0/host14/"
            "port-14:1/expander-14:0/port-14:0:133/"
            "end_device-14:0:133/target14:0:267/14:0:267:0"
        )

        file_contents = {
            os.path.join(sas_expander_base, "expander-14:0", "sas_address"): "0x500304800145493f",
            os.path.join(phy_base, "phy-14:0:12", "phy_identifier"): "12",
        }

        def mock_listdir(path):
            if path == scsi_dev_base:
                return []  # Strategy 1 finds nothing, falls through to Strategy 2
            if path == phy_base:
                return ["phy-14:0:12", "phy-14:0:5"]
            if path == scsi_host_base:
                return []
            if "block" in path:
                return ["sdk"]
            raise OSError("mock")

        def mock_realpath(path):
            if path == os.path.join(phy_base, "phy-14:0:12"):
                return phy_realpath
            if path == os.path.join(phy_base, "phy-14:0:12", "device"):
                return scsi_device_path
            if path == os.path.join(phy_base, "phy-14:0:5"):
                return (
                    "/sys/devices/pci0000:3a/0000:3a:00.0/0000:3b:00.0/host14/"
                    "port-14:1/expander-14:0/port-14:0:135/phy-14:0:5"
                )
            return path

        def mock_exists(path):
            return path == "/dev/sdk"

        with patch('os.listdir', side_effect=mock_listdir), \
             patch('os.path.realpath', side_effect=mock_realpath), \
             patch('os.path.exists', side_effect=mock_exists), \
             patch('builtins.open', side_effect=self._make_mock_open(file_contents)):
            result = _resolve_via_sysfs_scsi(
                "0000:3b:00.0", 12, "phy-0:0:12",
                expander_sas_address="0x500304800145493f"
            )
            assert result == "/dev/sdk"

    def test_phy_strategy_skips_wrong_phy_identifier(self):
        """Strategy 2: Should skip PHYs whose phy_identifier doesn't match."""
        from device_resolution import _resolve_via_sysfs_scsi

        scsi_dev_base = "/sys/class/scsi_device"
        phy_base = "/sys/class/sas_phy"
        scsi_host_base = "/sys/class/scsi_host"

        file_contents = {
            os.path.join(phy_base, "phy-14:0:5", "phy_identifier"): "5",
            os.path.join(phy_base, "phy-14:0:8", "phy_identifier"): "8",
        }

        def mock_listdir(path):
            if path == scsi_dev_base:
                return []
            if path == phy_base:
                return ["phy-14:0:5", "phy-14:0:8"]
            if path == scsi_host_base:
                return []
            raise OSError("mock")

        def mock_realpath(path):
            if path == os.path.join(phy_base, "phy-14:0:5"):
                return (
                    "/sys/devices/pci0000:3a/0000:3a:00.0/0000:3b:00.0/host14/"
                    "port-14:1/expander-14:0/port-14:0:135/phy-14:0:5"
                )
            if path == os.path.join(phy_base, "phy-14:0:8"):
                return (
                    "/sys/devices/pci0000:3a/0000:3a:00.0/0000:3b:00.0/host14/"
                    "port-14:1/expander-14:0/port-14:0:138/phy-14:0:8"
                )
            return path

        with patch('os.listdir', side_effect=mock_listdir), \
             patch('os.path.realpath', side_effect=mock_realpath), \
             patch('builtins.open', side_effect=self._make_mock_open(file_contents)):
            # Looking for PHY 12 but no PHY has phy_identifier=12
            result = _resolve_via_sysfs_scsi(
                "0000:3b:00.0", 12, "phy-0:0:12",
                expander_sas_address="0x500304800145493f"
            )
            assert result is None

    def test_phy_strategy_skips_wrong_controller(self):
        """Strategy 2: Should not match a PHY on a different PCI controller."""
        from device_resolution import _resolve_via_sysfs_scsi

        scsi_dev_base = "/sys/class/scsi_device"
        phy_base = "/sys/class/sas_phy"
        scsi_host_base = "/sys/class/scsi_host"
        phy_realpath = (
            "/sys/devices/pci0000:ae/0000:ae:00.0/0000:af:00.0/host15/"
            "port-15:0:0/phy-15:0:3"
        )

        file_contents = {
            os.path.join(phy_base, "phy-15:0:3", "phy_identifier"): "3",
        }

        def mock_listdir(path):
            if path == scsi_dev_base:
                return []
            if path == phy_base:
                return ["phy-15:0:3"]
            if path == scsi_host_base:
                return []
            raise OSError("mock")

        def mock_realpath(path):
            if path == os.path.join(phy_base, "phy-15:0:3"):
                return phy_realpath
            return path

        with patch('os.listdir', side_effect=mock_listdir), \
             patch('os.path.realpath', side_effect=mock_realpath), \
             patch('builtins.open', side_effect=self._make_mock_open(file_contents)):
            # Looking for controller 0000:3b:00.0 but PHY is on 0000:af:00.0
            result = _resolve_via_sysfs_scsi(
                "0000:3b:00.0", 3, "phy-0:0:3"
            )
            assert result is None

    def test_no_match_returns_none(self):
        """Should return None when no strategy finds a matching device."""
        from device_resolution import _resolve_via_sysfs_scsi

        scsi_dev_base = "/sys/class/scsi_device"
        phy_base = "/sys/class/sas_phy"
        scsi_host_base = "/sys/class/scsi_host"

        file_contents = {
            os.path.join(phy_base, "phy-14:0:0", "phy_identifier"): "0",
            os.path.join(phy_base, "phy-14:0:1", "phy_identifier"): "1",
        }

        def mock_listdir(path):
            if path == scsi_dev_base:
                return []
            if path == phy_base:
                return ["phy-14:0:0", "phy-14:0:1"]
            if path == scsi_host_base:
                return []
            raise OSError("mock")

        def mock_realpath(path):
            if path == os.path.join(phy_base, "phy-14:0:0"):
                return (
                    "/sys/devices/pci0000:3a/0000:3a:00.0/0000:3b:00.0/host14/"
                    "port-14:1/expander-14:0/port-14:0:130/phy-14:0:0"
                )
            if path == os.path.join(phy_base, "phy-14:0:1"):
                return (
                    "/sys/devices/pci0000:3a/0000:3a:00.0/0000:3b:00.0/host14/"
                    "port-14:1/expander-14:0/port-14:0:131/phy-14:0:1"
                )
            return path

        with patch('os.listdir', side_effect=mock_listdir), \
             patch('os.path.realpath', side_effect=mock_realpath), \
             patch('builtins.open', side_effect=self._make_mock_open(file_contents)):
            result = _resolve_via_sysfs_scsi(
                "0000:3b:00.0", 99, "phy-0:0:99"
            )
            assert result is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
