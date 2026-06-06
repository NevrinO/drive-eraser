# --- START OF FILE backend/device_discovery.py ---
# Smart device detection with PCI scanning and controller detection

import os
import re
import subprocess
import time
import threading
from typing import Dict, List, Optional, Tuple
import logging

# Device path validation - strict regex whitelist following rule #9 and #15
# \Z (not $) anchors strictly at end-of-string to prevent "/dev/sda\n" bypass
_DEVICE_PATH_RE = re.compile(r'^/dev(/[a-zA-Z0-9_\-:.]+)+\Z')

# PCI address validation - strict format: domain:bus:device.function (e.g., 0000:00:1f.2)
_PCI_ADDRESS_RE = re.compile(r'^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F]\Z')

# Cache for PCI controller scan results to avoid redundant subprocess calls
_PCI_CACHE = {'data': None, 'timestamp': 0}
_PCI_CACHE_TTL = 60  # seconds
_PCI_CACHE_LOCK = threading.Lock()

def validate_device_path(device: str) -> bool:
    """Validate device path against strict whitelist to prevent path traversal and injection.
    
    Following lessons-learned rule #9: Never accept raw device paths without validation.
    Following lessons-learned rule #15: Use \Z for strict end-of-string anchor.
    
    Args:
        device: Device path string to validate
        
    Returns:
        True if path is valid, False otherwise
    """
    if not device or not isinstance(device, str):
        return False
    if ".." in device or "\n" in device or "\r" in device:
        return False
    return bool(_DEVICE_PATH_RE.match(device))


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
    
    try:
        # Use lspci to scan PCI devices
        result = subprocess.run(
            ["lspci", "-nn", "-D"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode != 0:
            logging.warning(f"lspci scan failed: {result.stderr}")
            # Update cache even on failure to avoid repeated failed scans
            if use_cache:
                with _PCI_CACHE_LOCK:
                    _PCI_CACHE['data'] = controllers
                    _PCI_CACHE['timestamp'] = time.time()
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
            controller_type = _map_pci_class_to_type(class_code, device_info)
            
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
    finally:
        # Update cache with results (even empty list on error)
        if use_cache:
            with _PCI_CACHE_LOCK:
                _PCI_CACHE['data'] = controllers
                _PCI_CACHE['timestamp'] = time.time()
        
    return controllers


def _map_pci_class_to_type(class_code: str, description: str) -> str:
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
    
    # Use class code mapping first
    if class_code in class_map:
        return class_map[class_code]
    
    # Fallback to description parsing
    desc_lower = description.lower()
    if 'nvme' in desc_lower:
        return 'nvme'
    elif 'sata' in desc_lower:
        return 'sata'
    elif 'sas' in desc_lower:
        return 'sas'
    elif 'raid' in desc_lower:
        return 'raid'
    elif 'scsi' in desc_lower:
        return 'scsi'
    elif 'ata' in desc_lower:
        return 'ata'
    
    return 'unknown'


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
    
    # Determine sysfs path based on device type
    if device_name.startswith('nvme'):
        # NVMe devices: /sys/class/block/nvme0n1 -> /sys/devices/pci0000:00/...
        sys_path = f"/sys/class/block/{device_name}"
    else:
        # SATA/SAS devices: /sys/class/block/sda -> /sys/devices/pci0000:00/...
        sys_path = f"/sys/class/block/{device_name}"
    
    if not os.path.exists(sys_path):
        return None
        
    try:
        # Resolve the sysfs path to find the PCI controller
        real_path = os.path.realpath(sys_path)
        
        # Extract PCI address from sysfs path
        # Path format: /sys/devices/pci0000:00/0000:00:1f.2/ata1/host0/target0:0:0/0:0:0:0/block/sda
        pci_match = re.search(r'([0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F])', real_path)
        if not pci_match:
            return None
            
        pci_addr = pci_match.group(1)
        
        # Find matching controller from the list
        for controller in controllers:
            if controller['pci_address'] == pci_addr:
                return controller
                
    except Exception as e:
        logging.warning(f"Error getting controller for {device_path}: {e}")
        
    return None


def discover_controllers_and_devices() -> Dict[str, List[Dict]]:
    """Discover all storage controllers and their associated devices.
    
    Note: This function is expensive due to sysfs scanning. Results should be cached
    at a higher level if called frequently.
    
    Returns:
        Dictionary with controller types as keys and lists of device info as values
    """
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
    if os.path.exists('/sys/class/block'):
        for device_name in os.listdir('/sys/class/block'):
            # Skip partitions and device mapper
            if '-' in device_name or device_name.startswith('dm-'):
                continue
                
            device_path = f"/dev/{device_name}"
            if not validate_device_path(device_path):
                continue
                
            controller = get_controller_for_device(device_path)
            if controller:
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
    
    return result


def get_device_by_pci_path(pci_address: str) -> Optional[str]:
    """Get device path for a specific PCI controller.
    
    Args:
        pci_address: PCI address (e.g., "0000:00:1f.2")
        
    Returns:
        Device path or None if not found
    """
    if not validate_pci_address(pci_address):
        logging.warning(f"Invalid PCI address: {pci_address}")
        return None
    
    controllers_and_devices = discover_controllers_and_devices()
    
    # Search all controller types for matching PCI address
    for controller_type, devices in controllers_and_devices.items():
        for device_info in devices:
            controller = device_info.get('controller')
            if controller and controller.get('pci_address') == pci_address:
                return device_info['device_path']
    
    return None


def get_nvme_controller_info(nvme_device: str) -> Optional[Dict]:
    """Get detailed NVMe controller information.
    
    Args:
        nvme_device: NVMe device path (e.g., /dev/nvme0n1)
        
    Returns:
        Dictionary with NVMe controller details or None
    """
    if not validate_device_path(nvme_device):
        return None
        
    device_name = os.path.basename(nvme_device)
    if not device_name.startswith('nvme'):
        return None
        
    # Extract controller number (nvme0n1 -> nvme0)
    controller_match = re.match(r'(nvme\d+)', device_name)
    if not controller_match:
        return None
        
    controller_name = controller_match.group(1)
    
    try:
        # Use nvme list command to get controller info
        result = subprocess.run(
            ['nvme', 'list', '-o', 'json'],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode != 0:
            # Fallback to non-JSON output if JSON not supported
            result = subprocess.run(
                ['nvme', 'list'],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode != 0:
                return None
            
            # Parse text output with more robust pattern matching
            for line in result.stdout.splitlines():
                # Look for device node pattern: "/dev/nvme0n1" or "nvme0n1"
                if device_name in line and '/dev/' in line:
                    return {
                        'controller_name': controller_name,
                        'device_name': device_name,
                        'type': 'nvme',
                        'raw_info': line.strip()
                    }
        else:
            # Parse JSON output for structured data
            import json
            try:
                data = json.loads(result.stdout)
                if isinstance(data, dict) and 'Devices' in data:
                    for device in data['Devices']:
                        if device.get('DevicePath') == nvme_device or device.get('Name') == device_name:
                            return {
                                'controller_name': controller_name,
                                'device_name': device_name,
                                'type': 'nvme',
                                'model': device.get('ModelNumber'),
                                'serial': device.get('SerialNumber'),
                                'firmware': device.get('Firmware'),
                                'raw_info': device
                            }
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logging.warning(f"Failed to parse nvme list JSON: {e}")
                
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
        logging.warning(f"Error getting NVMe info for {nvme_device}: {e}")
        
    return None


def get_sata_controller_ports(controller_pci: str) -> List[str]:
    """Get list of SATA ports for a given controller.
    
    Args:
        controller_pci: PCI address of SATA controller
        
    Returns:
        List of device paths connected to this controller
    """
    if not validate_pci_address(controller_pci):
        logging.warning(f"Invalid PCI address: {controller_pci}")
        return []
    
    devices = []
    controllers_and_devices = discover_controllers_and_devices()
    
    for device_info in controllers_and_devices.get('sata', []):
        controller = device_info.get('controller')
        if controller and controller.get('pci_address') == controller_pci:
            devices.append(device_info['device_path'])
            
    return devices


# --- END OF FILE backend/device_discovery.py ---
