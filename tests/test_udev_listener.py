# Tests for backend/udev_listener.py
import sys
import os
import threading
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

import udev_listener


class TestUdevListenerRemoval:
    """Tests for drive removal handling in the udev listener."""

    def test_remove_event_clears_zero_check_state_for_known_slot(self):
        """A udev 'remove' event for a tracked slot must clear the bay's zero-check state."""
        fake_manager = MagicMock()

        # Simulate a previously added drive tracked in the runtime slot state
        udev_listener._runtime_slot_state.clear()
        udev_listener._runtime_slot_state[("enc1", "0")] = {
            "logical_device": "/dev/sda",
            "status": "Active"
        }

        fake_device = MagicMock()
        fake_device.device_type = 'disk'
        fake_device.action = 'remove'
        fake_device.device_node = '/dev/sda'
        fake_device.sys_path = '/sys/devices/.../sda'

        with patch('udev_listener.pyudev') as mock_pyudev:
            mock_context = MagicMock()
            mock_monitor = MagicMock()
            mock_pyudev.Context.return_value = mock_context
            mock_pyudev.Monitor.from_netlink.return_value = mock_monitor
            # Yield one remove event, then stop the thread
            mock_monitor.poll.side_effect = [fake_device, lambda: None]

            with patch('udev_listener.resolve_multipath_parent', return_value='/dev/sda'):
                with patch('udev_listener.invalidate_drive_cache'):
                    with patch('udev_listener.invalidate_unmapped_drive_cache'):
                        with patch('udev_listener.get_zero_check_manager', return_value=fake_manager):
                            with patch('udev_listener._udev_thread_stop_event') as mock_stop_event:
                                mock_stop_event.is_set.side_effect = [False, True]
                                udev_listener.udev_event_listener_thread()

        fake_manager.on_drive_removed.assert_called_once_with("enc1_slot_0")
        assert udev_listener._runtime_slot_state[("enc1", "0")] is None
