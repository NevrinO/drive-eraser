# PCI controller scanning and device-to-controller mapping
# Extracted from device_discovery.py for modularity (A64)

import os
import re
import subprocess
import time
import threading
from typing import Dict, List, Optional
import logging

from disk_utils import validate_device_path

# PCI address validation - strict format: domain:bus:device.function (e.g., 0000:00:1f.2)
# Function number is optional for some PCIe slot implementations (e.g., 0000:18:00)
_PCI_ADDRESS_RE = re.compile(r'^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}(\.[0-9a-fA-F])?\Z')

# Cache for PCI controller scan results to avoid redundant subprocess calls
_PCI_CACHE = {'data': None, 'timestamp': 0}
_PCI_CACHE_TTL = 86400  # seconds (24 hours - PCI topology changes rarely; manual refresh on enclosure add/edit)
_PCI_CACHE_LOCK = threading.Lock()

# Cache for device discovery results to avoid redundant full scans
_DISCOVERY_CACHE = {'data': None, 'timestamp': 0}
_DISCOVERY_CACHE_TTL = 86400  # seconds (24 hours - only hit from admin discovery panel; data changes on hot-plug but panel is not real-time)
_DISCOVERY_CACHE_LOCK = threading.Lock()

def validate_pci_address(pci_address: str) -> bool:
    """Validate PCI address format.
    
    Args:
        pci_address: PCI address string (e.g., "0000:00:1f.2")
        
    Returns:
        True if format is valid, False otherwise
    """
    if not pci_address or not isinstance(pci_address, str):
        return False
    return bool(_PCI_ADDRESS_RE.match(pci_address))


def scan_pci_controllers(use_cache: bool = True) -> List[Dict]:
    """Scan PCI bus for storage controllers and their details.
    
    Args:
        use_cache: If True, return cached results if available and not expired
        
    Returns:
        List of controller dictionaries with PCI address, vendor, device, class info
    """
    # Check cache first if enabled
    if use_cache:
        with _PCI_CACHE_LOCK:
            now = time.time()
            if _PCI_CACHE['data'] is not None and (now - _PCI_CACHE['timestamp']) < _PCI_CACHE_TTL:
                return _PCI_CACHE['data']
    
    controllers = []
    result = None

    try:
        # Use lspci to scan PCI devices
        result = subprocess.run(
            ["lspci", "-nn", "-D"],
            capture_output=True,
            text=True,
            timeout=10,
            shell=False
        )
        
        if result.returncode != 0:
            logging.warning(f"lspci scan failed: {result.stderr}")
            return controllers
            
        # Parse lspci output for storage controllers
        # Storage class codes: 0100 (SCSI), 0101 (IDE), 0102 (Floppy), 0103 (IPI), 
        # 0104 (RAID), 0105 (ATA), 0106 (SATA), 0107 (SAS), 0108 (NVM)
        storage_classes = ['0100', '0101', '0104', '0105', '0106', '0107', '0108']
        
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
                
            # Extract PCI address and device info
            # Format: "0000:00:1f.2 SATA controller: Intel Device 8c02 [8086:8c02]"
            parts = line.split(None, 1)
            if len(parts) < 2:
                continue
                
            pci_addr = parts[0]
            device_info = parts[1]
            
            # Check if this is a storage controller by looking for class code
            # lspci -nn shows class codes in brackets like [0106]
            class_match = re.search(r'\[([0-9a-fA-F]{4})\]', device_info)
            if not class_match:
                continue
                
            class_code = class_match.group(1).lower()
            if class_code not in storage_classes:
                continue
                
            # Extract vendor and device IDs
            vendor_device_match = re.search(r'\[([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\]', device_info)
            vendor_id = vendor_device_match.group(1) if vendor_device_match else None
            device_id = vendor_device_match.group(2) if vendor_device_match else None
            
            # Determine controller type based on class code
            controller_type = _map_pci_class_to_type(class_code)
            
            controllers.append({
                'pci_address': pci_addr,
                'vendor_id': vendor_id,
                'device_id': device_id,
                'class_code': class_code,
                'controller_type': controller_type,
                'description': device_info
            })
            
    except subprocess.TimeoutExpired:
        logging.warning("PCI scan timed out")
    except FileNotFoundError:
        logging.warning("lspci command not found")
    except Exception as e:
        logging.warning(f"PCI scan error: {e}")

    # Only cache on successful parsing (not on failure/error paths)
    if use_cache and result and result.returncode == 0:
        with _PCI_CACHE_LOCK:
            _PCI_CACHE['data'] = controllers
            _PCI_CACHE['timestamp'] = time.time()

    return controllers


def _map_pci_class_to_type(class_code: str, description: str = "") -> str:
    """Map PCI class code to controller type string."""
    class_map = {
        '0100': 'scsi',
        '0101': 'ide',
        '0104': 'raid',
        '0105': 'ata',
        '0106': 'sata',
        '0107': 'sas',
        '0108': 'nvme'
    }
    return class_map.get(class_code, 'unknown')


def get_controller_for_device(device_path: str, controllers: Optional[List[Dict]] = None) -> Optional[Dict]:
    """Get PCI controller information for a given device path.
    
    Args:
        device_path: Device path (e.g., /dev/sda, /dev/nvme0n1)
        controllers: Optional pre-scanned controller list to avoid redundant scans
        
    Returns:
        Controller dictionary or None if not found
    """
    if not validate_device_path(device_path):
        logging.warning(f"Invalid device path: {device_path}")
        return None
    
    # Use provided controllers or scan with caching
    if controllers is None:
        controllers = scan_pci_controllers(use_cache=True)
        
    device_name = os.path.basename(device_path)
    
    # sysfs path is the same for all device types (NVMe, SATA, SAS, SCSI)
    sys_path = f"/sys/class/block/{device_name}"
    
    try:
        # Resolve the sysfs path to find the PCI controller
        real_path = os.path.realpath(sys_path)
        
        # Extract PCI address from sysfs path
        # Path format: /sys/devices/pci0000:00/0000:00:1f.2/ata1/host0/target0:0:0/0:0:0:0/block/sda
        # Or for SCSI/RAID: /sys/devices/pci0000:00/0000:00:01.0/0000:01:00.0/host0/target0:0:0/0:0:0:0/block/sdX
        # Need to match the LAST PCI address (the actual controller), not the bridge
        pci_matches = re.findall(r'([0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F])', real_path)
        if not pci_matches:
            logging.debug(f"No PCI address found in sysfs path for {device_path}: {real_path}")
            return None
            
        pci_addr = pci_matches[-1]  # Use the last PCI address (actual controller, not bridge)
        
        # Find matching controller from the list
        for controller in controllers:
            if controller['pci_address'] == pci_addr:
                return controller
        
        logging.debug(f"No controller found matching PCI address {pci_addr} for {device_path}")
                
    except Exception as e:
        logging.warning(f"Error getting controller for {device_path}: {e}")
        
    return None


def discover_controllers_and_devices(use_cache: bool = True) -> Dict[str, List[Dict]]:
    """Discover all storage controllers and their associated devices.

    Note: This function is expensive due to sysfs scanning. Results should be cached
    at a higher level if called frequently.

    Args:
        use_cache: If True, return cached results if available and not expired

    Returns:
        Dictionary with controller types as keys and lists of device info as values
    """
    # Check cache first if enabled
    if use_cache:
        with _DISCOVERY_CACHE_LOCK:
            now = time.time()
            if _DISCOVERY_CACHE['data'] is not None and (now - _DISCOVERY_CACHE['timestamp']) < _DISCOVERY_CACHE_TTL:
                return _DISCOVERY_CACHE['data']
    result = {
        'sata': [],
        'sas': [],
        'nvme': [],
        'scsi': [],
        'raid': [],
        'unknown': []
    }
    
    # Scan all PCI controllers (uses cache)
    controllers = scan_pci_controllers(use_cache=True)

    # Scan /sys/class/block for all block devices
    block_devices = []
    if not os.path.exists('/sys/class/block'):
        block_device_names = []
    else:
        try:
            block_device_names = os.listdir('/sys/class/block')
        except (OSError, IOError):
            block_device_names = []

    for device_name in block_device_names:
        # Skip partitions, device mapper, and loop devices
        if '-' in device_name or device_name.startswith('dm-') or device_name.startswith('loop'):
            continue

        device_path = f"/dev/{device_name}"
        if not validate_device_path(device_path):
            continue

        # Use PCI address mapping for efficient controller lookup
        controller = get_controller_for_device(device_path, controllers=controllers)
        # Add device even if controller is None (will be grouped into 'unknown' later)
        block_devices.append({
            'device_path': device_path,
            'device_name': device_name,
            'controller': controller
        })
    
    # Group devices by controller type with defensive checks
    for device_info in block_devices:
        controller = device_info.get('controller')
        if not controller or not isinstance(controller, dict):
            result['unknown'].append(device_info)
            continue

        controller_type = controller.get('controller_type', 'unknown')
        if controller_type in result:
            result[controller_type].append(device_info)
        else:
            result['unknown'].append(device_info)

    # Update cache only on successful data acquisition
    if use_cache:
        with _DISCOVERY_CACHE_LOCK:
            _DISCOVERY_CACHE['data'] = result
            _DISCOVERY_CACHE['timestamp'] = time.time()

    return result
