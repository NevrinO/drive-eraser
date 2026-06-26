# --- START OF FILE backend/udev_listener.py ---
# Event-driven device discovery using pyudev for real-time drive detection

import os
import re
import json
import threading
import logging
import time
from typing import Dict, Optional, Tuple

try:
    import pyudev
except ImportError:
    pyudev = None
    logging.warning("pyudev not installed - event-driven discovery disabled")

from device_discovery import (
    generate_master_slot_map,
    resolve_multipath_parent
)
from disk_ops import invalidate_drive_cache
from routes.bay_mapping_routes import invalidate_unmapped_drive_cache
from common import get_config_dir, load_policy

# Runtime slot state: (enclosure_id, slot_number) -> device_info
_runtime_slot_state = {}
_runtime_slot_lock = threading.Lock()

# WebSocket manager reference (set at startup)
_websocket_manager = None

# Thread control
_udev_thread = None
_udev_thread_stop_event = threading.Event()

logger = logging.getLogger("app")


def set_websocket_manager(ws_manager):
    """Set the WebSocket manager for broadcasting slot updates."""
    global _websocket_manager
    _websocket_manager = ws_manager
    logger.info("WebSocket manager set for udev event listener")


def extract_coordinates_from_sysfs(sys_path: str) -> Optional[Dict]:
    """Extract hardware coordinates from a device's sysfs path.
    
    Args:
        sys_path: The sysfs path of the device (e.g., from device.sys_path)
        
    Returns:
        Dictionary with keys: pci_controller, expander_sas_address (optional), 
        physical_slot_number, slot_type, or None if extraction fails
    """
    if not sys_path:
        return None
    
    real_path = os.path.realpath(sys_path)
    path_parts = real_path.split('/')
    
    # Extract PCI controller from path
    pci_controller = None
    for part in path_parts:
        if re.match(r'^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F]$', part):
            pci_controller = part
            break
    
    if not pci_controller:
        return None
    
    # Determine slot type and extract additional coordinates
    # Check for SAS expander topology
    sas_device_dir = None
    for i, part in enumerate(path_parts):
        if part == 'sas_device':
            sas_device_dir = os.path.join('/'.join(path_parts[:i+1]), path_parts[i+1])
            break
    
    if sas_device_dir and os.path.exists(sas_device_dir):
        # SAS expander topology
        expander_id = os.path.basename(sas_device_dir)
        
        # Extract phy number from path
        phy_match = re.search(r'phy-?(\d+)', real_path)
        phy_num = int(phy_match.group(1)) if phy_match else 0
        
        return {
            'pci_controller': pci_controller,
            'slot_type': 'sas_expander',
            'expander_sas_address': expander_id,
            'physical_slot_number': phy_num,
            'hardware_identifier': f'phy-0:0:{phy_num}'
        }
    
    # Check for NVMe
    if 'nvme' in real_path:
        # NVMe devices: slot info comes from PCI slot matching
        # We'll match against the master slot map later
        return {
            'pci_controller': pci_controller,
            'slot_type': 'pcie_nvme',
            'expander_sas_address': None,
            'physical_slot_number': 0,  # Will be resolved via master slot map
            'hardware_identifier': None  # Will be resolved via master slot map
        }
    
    # Check for SAS direct-attached
    scsi_match = re.search(r'scsi-(\d+):0:(\d+)', real_path)
    if scsi_match:
        host_num = int(scsi_match.group(1))
        slot_num = int(scsi_match.group(2))
        return {
            'pci_controller': pci_controller,
            'slot_type': 'sas_direct',
            'expander_sas_address': None,
            'physical_slot_number': slot_num,
            'hardware_identifier': f'phy-{host_num}:0:{slot_num}'
        }
    
    # Check for SATA
    ata_match = re.search(r'ata(\d+)', real_path)
    if ata_match:
        ata_num = int(ata_match.group(1))
        return {
            'pci_controller': pci_controller,
            'slot_type': 'motherboard_sata',
            'expander_sas_address': None,
            'physical_slot_number': ata_num,
            'hardware_identifier': f'ata{ata_num}'
        }
    
    return None


def _resolve_device_from_enclosure_slot(enclosure_config: Dict, coords: Dict) -> Optional[Tuple[str, int]]:
    """Resolve a device to its enclosure and slot number using the enclosure configuration.
    
    Args:
        enclosure_config: The enclosures section from bay_map.json
        coords: Hardware coordinates from extract_coordinates_from_sysfs
        
    Returns:
        Tuple of (enclosure_id, slot_number) or None if no match found
    """
    enclosures = enclosure_config.get("enclosures", {})
    
    for enc_id, enc_config in enclosures.items():
        slots = enc_config.get("slots", {})
        
        for slot_num, slot_config in slots.items():
            # Traverse nested mappings structure: slots -> slot_num -> mappings -> interface_key -> mapping
            mappings = slot_config.get("mappings", {})
            if not mappings:
                continue
            
            for interface_key, mapping in mappings.items():
                if not mapping or not isinstance(mapping, dict):
                    continue
                
                # Match by hardware coordinates from mapping
                if mapping.get("pci_controller") == coords.get("pci_controller"):
                    if mapping.get("slot_type") == coords.get("slot_type"):
                        # For SAS expander, match expander SAS address
                        if coords.get("slot_type") == "sas_expander":
                            if mapping.get("expander_sas_address") == coords.get("expander_sas_address"):
                                if slot_config.get("physical_slot_number") == coords.get("physical_slot_number"):
                                    return (enc_id, int(slot_num))
                        # For other types, match physical slot number
                        elif slot_config.get("physical_slot_number") == coords.get("physical_slot_number"):
                            return (enc_id, int(slot_num))
    
    return None


def udev_event_listener_thread():
    """Background thread that listens for udev block device events."""
    if not pyudev:
        logger.error("pyudev not available - udev event listener cannot start")
        return
    
    logger.info("Starting udev event listener thread")
    
    try:
        context = pyudev.Context()
        monitor = pyudev.Monitor.from_netlink(context)
        monitor.filter_by('block')
        
        # Load enclosure configuration for slot resolution
        config_dir = get_config_dir()
        bay_map_path = os.path.join(config_dir, "bay_map.json")
        
        for device in iter(monitor.poll, None):
            if _udev_thread_stop_event.is_set():
                logger.info("udev event listener thread received stop signal")
                break
            
            if device.device_type != 'disk':
                continue
            
            action = device.action
            dev_node = device.device_node
            sys_path = device.sys_path
            
            if not dev_node or not sys_path:
                continue
            
            # Load current bay_map configuration
            try:
                with open(bay_map_path, 'r') as f:
                    bay_map_doc = json.load(f)
            except Exception as e:
                logger.debug(f"Could not load bay_map.json for udev event: {e}")
                continue
            
            if action == 'add':
                # Device added - extract coordinates and resolve slot
                coords = extract_coordinates_from_sysfs(sys_path)
                if coords:
                    match = _resolve_device_from_enclosure_slot(bay_map_doc, coords)
                    if match:
                        enc_id, slot_num = match
                        dev_name = os.path.basename(dev_node)

                        # MPIO settling time: If this is a raw device (not dm-), wait for multipathd to bind
                        # This prevents UI flicker when dual-ported SAS drives are inserted
                        if not dev_name.startswith('dm-') and not dev_name.startswith('mapper/'):
                            # Check if multipathd is likely running by checking for /dev/mapper directory
                            if os.path.isdir('/dev/mapper'):
                                # Settling delay to allow multipathd to bind paths (typically 500-1000ms)
                                time.sleep(0.5)
                                # Re-resolve after settling to get the final multipath parent
                                final_dev_node = resolve_multipath_parent(dev_name)
                            else:
                                final_dev_node = resolve_multipath_parent(dev_name)
                        else:
                            final_dev_node = resolve_multipath_parent(dev_name)

                        # Invalidate drive cache to ensure next /api/drives call returns fresh data
                        # Hardware topology caches (SAS expander, SCSI projections, master slot map) are NOT
                        # invalidated here because drive hot-plug does not change physical hardware topology
                        invalidate_drive_cache(final_dev_node)
                        # A previously unknown or swapped device now appears under the same by-path;
                        # clear its identity cache so the admin panel shows the new model/serial.
                        invalidate_unmapped_drive_cache(final_dev_node)

                        with _runtime_slot_lock:
                            _runtime_slot_state[(enc_id, slot_num)] = {
                                'logical_device': final_dev_node,
                                'status': 'Active'
                            }

                        # Broadcast via WebSocket if available
                        if _websocket_manager:
                            try:
                                _websocket_manager.emit('slot_update', {
                                    'event': 'slot_update',
                                    'enclosure_id': enc_id,
                                    'slot_number': slot_num,
                                    'logical_device': final_dev_node,
                                    'status': 'Active'
                                })
                            except Exception as e:
                                logger.debug(f"Failed to broadcast slot update via WebSocket: {e}")

                        logger.info(f"udev: Device {final_dev_node} added to enclosure {enc_id} slot {slot_num}")
            
            elif action == 'remove':
                # Device removed - clear from runtime state
                # Resolve multipath parent before comparison to match the stored logical_device
                final_dev_node = resolve_multipath_parent(os.path.basename(dev_node))

                # Invalidate drive cache to ensure next /api/drives call returns fresh data
                # Hardware topology caches (SAS expander, SCSI projections, master slot map) are NOT
                # invalidated here because drive hot-plug does not change physical hardware topology
                invalidate_drive_cache(final_dev_node)
                # A removed device may reappear under the same by-path with new identity;
                # clear its identity cache so the admin panel does not show stale data.
                invalidate_unmapped_drive_cache(final_dev_node)

                with _runtime_slot_lock:
                    for (enc_id, slot_num), state in list(_runtime_slot_state.items()):
                        if state and state.get('logical_device') == final_dev_node:
                            _runtime_slot_state[(enc_id, slot_num)] = None

                            # Broadcast via WebSocket if available
                            if _websocket_manager:
                                try:
                                    _websocket_manager.emit('slot_update', {
                                        'event': 'slot_update',
                                        'enclosure_id': enc_id,
                                        'slot_number': slot_num,
                                        'logical_device': None,
                                        'status': 'Empty'
                                    })
                                except Exception as e:
                                    logger.debug(f"Failed to broadcast slot update via WebSocket: {e}")

                            logger.info(f"udev: Device removed from enclosure {enc_id} slot {slot_num}")
                            break
    
    except Exception as e:
        logger.error(f"udev event listener thread error: {e}")
    finally:
        logger.info("udev event listener thread stopped")


def start_udev_listener():
    """Start the udev event listener background thread."""
    global _udev_thread
    
    if not pyudev:
        logger.warning("pyudev not installed - event-driven discovery not available")
        return False
    
    if _udev_thread and _udev_thread.is_alive():
        logger.warning("udev event listener thread already running")
        return False
    
    _udev_thread_stop_event.clear()
    _udev_thread = threading.Thread(target=udev_event_listener_thread, daemon=True)
    _udev_thread.start()
    logger.info("udev event listener thread started")
    return True


def stop_udev_listener():
    """Stop the udev event listener background thread."""
    global _udev_thread
    
    _udev_thread_stop_event.set()
    
    if _udev_thread and _udev_thread.is_alive():
        _udev_thread.join(timeout=5)
        logger.info("udev event listener thread stopped")


def get_runtime_slot_state() -> Dict:
    """Get the current runtime slot state.
    
    Returns:
        Dictionary mapping (enclosure_id, slot_number) to device info
    """
    with _runtime_slot_lock:
        return dict(_runtime_slot_state)
