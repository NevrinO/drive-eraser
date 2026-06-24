# --- START OF FILE backend/device_discovery.py ---
# Smart device detection with PCI scanning and controller detection

import os
import re
import subprocess
import time
import threading
import json
from typing import Dict, List, Optional, Tuple
import logging

# Device path validation - strict regex whitelist following rule #9 and #15
# \Z (not $) anchors strictly at end-of-string to prevent "/dev/sda\n" bypass
_DEVICE_PATH_RE = re.compile(r'^/dev(/[a-zA-Z0-9_\-:.]+)+\Z')

# PCI address validation - strict format: domain:bus:device.function (e.g., 0000:00:1f.2)
_PCI_ADDRESS_RE = re.compile(r'^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F]\Z')

# Cache for PCI controller scan results to avoid redundant subprocess calls
_PCI_CACHE = {'data': None, 'timestamp': 0}
_PCI_CACHE_TTL = 3600  # seconds (1 hour - PCI topology changes rarely; manual refresh on enclosure add/edit)
_PCI_CACHE_LOCK = threading.Lock()

# Cache for enclosure slot metadata to avoid redundant sysfs scans
_ENCLOSURE_CACHE = {'data': None, 'timestamp': 0}
_ENCLOSURE_CACHE_TTL = 3600  # seconds (1 hour - enclosure metadata changes rarely; manual refresh on enclosure add/edit)
_ENCLOSURE_CACHE_LOCK = threading.Lock()

# Cache for NVMe list output to avoid redundant subprocess calls
_NVME_CACHE = {'data': None, 'timestamp': 0}
_NVME_CACHE_TTL = 3600  # seconds (1 hour - NVMe topology changes rarely; manual refresh on enclosure add/edit)
_NVME_CACHE_LOCK = threading.Lock()

# Cache for device discovery results to avoid redundant full scans
_DISCOVERY_CACHE = {'data': None, 'timestamp': 0}
_DISCOVERY_CACHE_TTL = 60  # seconds
_DISCOVERY_CACHE_LOCK = threading.Lock()

# Cache for master slot map (hardware topology) to avoid redundant sysfs scans
_MASTER_SLOT_CACHE = {'data': None, 'timestamp': 0}
_MASTER_SLOT_CACHE_TTL = 3600  # seconds (1 hour - master slot map changes rarely; manual refresh on enclosure add/edit)
_MASTER_SLOT_CACHE_LOCK = threading.Lock()

# Cache for SAS expander detection results to avoid redundant by-path scans
_SAS_EXPANDER_CACHE = {}  # Key: pci_address, Value: {'data': result, 'timestamp': time}
_SAS_EXPANDER_CACHE_TTL = 3600  # seconds (1 hour - SAS expander topology changes rarely; manual refresh on enclosure add/edit)
_SAS_EXPANDER_CACHE_LOCK = threading.Lock()

# Cache for SCSI host slot projections to avoid redundant full scans
_SCSI_PROJECTIONS_CACHE = {'data': None, 'timestamp': 0}
_SCSI_PROJECTIONS_CACHE_TTL = 3600  # seconds (1 hour - SCSI projections change rarely; manual refresh on enclosure add/edit)
_SCSI_PROJECTIONS_CACHE_LOCK = threading.Lock()

def validate_device_path(device: str) -> bool:
    r"""Validate device path against strict whitelist to prevent path traversal and injection.
    
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
            timeout=10,
            shell=False
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
        # SATA/SAS/SCSI devices: /sys/class/block/sda -> /sys/devices/pci0000:00/...
        sys_path = f"/sys/class/block/{device_name}"
    
    if not os.path.exists(sys_path):
        return None
        
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

    # Build PCI address → controller mapping for O(1) lookups
    controller_by_pci = {c['pci_address']: c for c in controllers}

    # Scan /sys/class/block for all block devices
    block_devices = []
    if os.path.exists('/sys/class/block'):
        for device_name in os.listdir('/sys/class/block'):
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

    controllers_and_devices = discover_controllers_and_devices(use_cache=True)
    
    # Search all controller types for matching PCI address
    for controller_type, devices in controllers_and_devices.items():
        for device_info in devices:
            controller = device_info.get('controller')
            if controller and controller.get('pci_address') == pci_address:
                return device_info['device_path']
    
    return None


def _get_nvme_list_data(use_cache: bool = True) -> Optional[Dict]:
    """Get cached NVMe list data to avoid redundant subprocess calls.

    Args:
        use_cache: If True, return cached results if available and not expired

    Returns:
        Dictionary with parsed NVMe list data or None
    """
    # Check cache first if enabled
    if use_cache:
        with _NVME_CACHE_LOCK:
            now = time.time()
            if _NVME_CACHE['data'] is not None and (now - _NVME_CACHE['timestamp']) < _NVME_CACHE_TTL:
                return _NVME_CACHE['data']

    nvme_data = None

    try:
        # Try JSON output first
        result = subprocess.run(
            ['nvme', 'list', '-o', 'json'],
            capture_output=True,
            text=True,
            timeout=10,
            shell=False
        )

        if result.returncode == 0:
            try:
                nvme_data = json.loads(result.stdout)
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logging.warning(f"Failed to parse nvme list JSON: {e}")
        else:
            # Fallback to text output
            result = subprocess.run(
                ['nvme', 'list'],
                capture_output=True,
                text=True,
                timeout=10,
                shell=False
            )
            if result.returncode == 0:
                nvme_data = {'raw_text': result.stdout}

        # Update cache only on successful data acquisition
        if use_cache and nvme_data is not None:
            with _NVME_CACHE_LOCK:
                _NVME_CACHE['data'] = nvme_data
                _NVME_CACHE['timestamp'] = time.time()

    except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
        logging.warning(f"Error running nvme list: {e}")
        # Do NOT update cache on failure

    return nvme_data


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

    # Get cached NVMe list data
    nvme_data = _get_nvme_list_data(use_cache=True)
    if nvme_data is None:
        return None

    # Try to parse from JSON data first
    if isinstance(nvme_data, dict) and 'Devices' in nvme_data:
        for device in nvme_data['Devices']:
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

    # Fallback to text output parsing
    if isinstance(nvme_data, dict) and 'raw_text' in nvme_data:
        for line in nvme_data['raw_text'].splitlines():
            # Look for device node pattern: "/dev/nvme0n1" or "nvme0n1"
            if device_name in line and '/dev/' in line:
                return {
                    'controller_name': controller_name,
                    'device_name': device_name,
                    'type': 'nvme',
                    'raw_info': line.strip()
                }

    return None


def get_sata_controller_ports(controller_pci: str, controllers_and_devices: Optional[Dict[str, List[Dict]]] = None) -> List[str]:
    """Get list of SATA ports for a given controller.

    Args:
        controller_pci: PCI address of SATA controller
        controllers_and_devices: Optional pre-discovered data to avoid redundant scans

    Returns:
        List of device paths connected to this controller
    """
    if not validate_pci_address(controller_pci):
        logging.warning(f"Invalid PCI address: {controller_pci}")
        return []

    devices = []
    if controllers_and_devices is None:
        controllers_and_devices = discover_controllers_and_devices(use_cache=True)

    for device_info in controllers_and_devices.get('sata', []):
        controller = device_info.get('controller')
        if controller and controller.get('pci_address') == controller_pci:
            devices.append(device_info['device_path'])

    return devices


def is_enclosure_device(scsi_device_path: str, device_type_cache: Optional[Dict[str, bool]] = None) -> bool:
    """Check if a SCSI device is an SES/enclosure management device (not a drive slot).

    Empty drive slots will NOT be filtered because they are not enclosure type.
    Only SES/enclosure management devices are excluded.

    Args:
        scsi_device_path: Path to the SCSI device directory (e.g., /sys/class/scsi_device/0:0:0:0)
        device_type_cache: Optional cache dictionary to avoid redundant file I/O during scans

    Returns:
        True if the device is an SES/enclosure management device, False otherwise
    """
    # Check cache first if provided
    if device_type_cache is not None and scsi_device_path in device_type_cache:
        return device_type_cache[scsi_device_path]

    type_path = os.path.join(scsi_device_path, 'device', 'type')
    try:
        with open(type_path, 'r') as f:
            device_type = f.read().strip()
        # SCSI type 0x0d (13 decimal) = enclosure services (SES)
        # Type 0x03 = processor (often used for enclosure management)
        enclosure_types = ['enclosure', 'processor', 'ses', '13', '3']
        is_enclosure = device_type.lower() in enclosure_types

        # Store in cache if provided
        if device_type_cache is not None:
            device_type_cache[scsi_device_path] = is_enclosure

        return is_enclosure
    except (OSError, IOError):
        # If we can't read the type, be conservative and include it
        if device_type_cache is not None:
            device_type_cache[scsi_device_path] = False
        return False


def get_enclosure_hardware_info() -> List[Dict]:
    """Collect SES hardware info for each PCI controller.

    Scans /sys/class/enclosure to collect vendor, model, total_slots, and occupied_slots
    for each enclosure controller. Used by the enclosure wizard to identify enclosures.

    Returns:
        List of dictionaries with hardware info per controller:
        - pci_controller: PCI address of the controller
        - vendor: Vendor name from sysfs
        - model: Model name from sysfs
        - total_slots: Total slot count from SES
        - occupied_slots: Count of slots with drives present
    """
    enclosure_base = "/sys/class/enclosure"
    METADATA_DIRS = {"components", "device", "id", "power", "subsystem", "uevent"}
    hardware_info = []

    # Attempt the operation directly; handle errors from the actual operation (Lesson #6)
    try:
        enc_ids = os.listdir(enclosure_base)
    except (OSError, IOError):
        logging.debug(f"Failed to list enclosure directory: {enclosure_base}")
        return hardware_info

    for enc_id in enc_ids:
        enc_path = os.path.join(enclosure_base, enc_id)
        device_path = os.path.join(enc_path, "device")

        # Extract PCI controller from actual drives in the enclosure slots
        # The enclosure device link points to the enclosure management interface (SES device),
        # which may be on a different PCI device than the actual HBA that drives are connected to.
        # Instead, look at the drives in the slots to find the actual HBA PCI address.
        pci_controller = None

        try:
            slot_ids = os.listdir(enc_path)
        except (OSError, IOError):
            continue

        for slot_id in slot_ids:
            if slot_id in METADATA_DIRS:
                continue
            slot_path = os.path.join(enc_path, slot_id)

            # Check if slot has a device (drive present)
            device_link = os.path.join(slot_path, "device")
            try:
                if os.path.islink(device_link):
                    # Resolve the symlink and check if it points to a block device
                    real_path = os.path.realpath(device_link)
                    # Block devices have a "block" subdirectory in their sysfs path
                    if os.path.exists(os.path.join(real_path, "block")):
                        # Extract PCI address from the drive's sysfs path
                        pci_matches = re.findall(r'[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F]', real_path)
                        if pci_matches:
                            # Use the last (deepest) PCI address, which is the HBA
                            pci_controller = pci_matches[-1]
                            break  # Found the HBA, no need to check other slots
            except (OSError, IOError):
                continue

        if not pci_controller:
            # Fallback: if no drives present, use the enclosure management interface PCI address
            # This won't match the master slot map's HBA address, but it's better than nothing
            try:
                if os.path.islink(device_path):
                    real_path = os.path.realpath(device_path)
                    pci_matches = re.findall(r'[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F]', real_path)
                    if pci_matches:
                        pci_controller = pci_matches[-1]
            except (OSError, IOError):
                pass

        if not pci_controller:
            continue

        # Read vendor and model from sysfs
        # Try multiple locations: enclosure directory, device directory, and resolved device path
        vendor = None
        model = None

        # Possible locations for vendor/model files
        vendor_paths = [
            os.path.join(enc_path, "vendor"),
            os.path.join(device_path, "vendor"),
        ]
        model_paths = [
            os.path.join(enc_path, "model"),
            os.path.join(device_path, "model"),
        ]

        # Try resolved device path if device is a symlink
        try:
            if os.path.islink(device_path):
                real_device_path = os.path.realpath(device_path)
                vendor_paths.append(os.path.join(real_device_path, "vendor"))
                model_paths.append(os.path.join(real_device_path, "model"))
        except (OSError, IOError):
            pass

        for vendor_file in vendor_paths:
            try:
                with open(vendor_file, 'r') as f:
                    vendor = f.read().strip()
                    break
            except (OSError, IOError):
                continue

        for model_file in model_paths:
            try:
                with open(model_file, 'r') as f:
                    model = f.read().strip()
                    break
            except (OSError, IOError):
                continue

        # Count total and occupied slots
        total_slots = 0
        occupied_slots = 0

        try:
            slot_ids = os.listdir(enc_path)
        except (OSError, IOError):
            continue

        for slot_id in slot_ids:
            if slot_id in METADATA_DIRS:
                continue
            slot_path = os.path.join(enc_path, slot_id)
            total_slots += 1

            # Check if slot has a device (drive present)
            # The "device" symlink points to the drive's sysfs entry if a drive is inserted
            device_link = os.path.join(slot_path, "device")
            try:
                if os.path.islink(device_link):
                    # Resolve the symlink and check if it points to a block device
                    real_path = os.path.realpath(device_link)
                    # Block devices have a "block" subdirectory in their sysfs path
                    if os.path.exists(os.path.join(real_path, "block")):
                        occupied_slots += 1
            except (OSError, IOError):
                pass

        hardware_info.append({
            "pci_controller": pci_controller,
            "vendor": vendor,
            "model": model,
            "total_slots": total_slots,
            "occupied_slots": occupied_slots
        })
        logging.debug(f"Enclosure hardware info: pci={pci_controller}, vendor={vendor}, model={model}, total={total_slots}, occupied={occupied_slots}")

    return hardware_info


def get_max_slot_from_enclosure(use_cache: bool = True) -> int:
    """Query enclosure metadata to get the maximum slot number.

    Scans /sys/class/enclosure to find the highest slot number across all enclosures.
    This provides the actual physical slot count for complete enumeration.

    Args:
        use_cache: If True, return cached results if available and not expired

    Returns:
        Maximum slot number found, or 0 if no enclosure data is available
    """
    # Check cache first if enabled
    if use_cache:
        with _ENCLOSURE_CACHE_LOCK:
            now = time.time()
            if _ENCLOSURE_CACHE['data'] is not None and (now - _ENCLOSURE_CACHE['timestamp']) < _ENCLOSURE_CACHE_TTL:
                return _ENCLOSURE_CACHE['data']

    enclosure_base = "/sys/class/enclosure"
    METADATA_DIRS = {"components", "device", "id", "power", "subsystem", "uevent"}
    max_slot = 0
    MAX_REASONABLE_SLOT = 9999  # Rule #31: validate numeric bounds

    if not os.path.exists(enclosure_base):
        logging.debug(f"Enclosure directory not found: {enclosure_base}")
        return max_slot

    try:
        enc_ids = os.listdir(enclosure_base)
    except (OSError, IOError):
        logging.debug(f"Failed to list enclosure directory: {enclosure_base}")
        return max_slot

    for enc_id in enc_ids:
        enc_path = os.path.join(enclosure_base, enc_id)
        try:
            slot_ids = os.listdir(enc_path)
        except (OSError, IOError):
            continue

        for slot_id in slot_ids:
            if slot_id in METADATA_DIRS:
                continue
            slot_path = os.path.join(enc_path, slot_id)

            # Extract slot number from slot_id (format: "slot_1", "slot_2", etc.)
            # or from the slot_number file if available
            try:
                slot_num_path = os.path.join(slot_path, 'slot_number')
                if os.path.exists(slot_num_path):
                    with open(slot_num_path, 'r') as f:
                        slot_num = int(f.read().strip())
                        # Rule #31: validate numeric bounds
                        if 0 <= slot_num <= MAX_REASONABLE_SLOT and slot_num > max_slot:
                            max_slot = slot_num
                else:
                    # Fallback: try to parse from slot_id (e.g., "slot_1" -> 1)
                    if slot_id.startswith('slot_'):
                        try:
                            slot_num = int(slot_id[5:])
                            # Rule #31: validate numeric bounds
                            if 0 <= slot_num <= MAX_REASONABLE_SLOT and slot_num > max_slot:
                                max_slot = slot_num
                        except ValueError:
                            pass
            except (OSError, IOError, ValueError):
                continue

    # Update cache only on successful data acquisition
    if use_cache:
        with _ENCLOSURE_CACHE_LOCK:
            _ENCLOSURE_CACHE['data'] = max_slot
            _ENCLOSURE_CACHE['timestamp'] = time.time()

    return max_slot


def detect_sas_expander(host_path: str, pci_address: str, use_cache: bool = True) -> Optional[Dict]:
    """Detect if a SCSI host is connected to a SAS expander and extract expander information.

    Args:
        host_path: Path to the SCSI host directory (e.g., /sys/class/scsi_host/host0)
        pci_address: PCI address of the controller (e.g., 0000:af:00.0)
        use_cache: If True, return cached results if available and not expired

    Returns:
        Dictionary with expander info if detected, None otherwise:
        - expander_id: SAS expander identifier (e.g., 0x500056b3059bdcff)
        - phy_count: Number of phy ports on the expander
    """
    # Check cache first if enabled
    if use_cache:
        with _SAS_EXPANDER_CACHE_LOCK:
            now = time.time()
            if pci_address in _SAS_EXPANDER_CACHE:
                cached = _SAS_EXPANDER_CACHE[pci_address]
                if cached['data'] is not None and (now - cached['timestamp']) < _SAS_EXPANDER_CACHE_TTL:
                    return cached['data']

    # Walk up the sysfs tree to find sas_device directories
    device_link = os.path.join(host_path, 'device')
    try:
        real_path = os.path.realpath(device_link)
    except (OSError, IOError):
        return None

    expander_id = None
    total_phy_count = 0
    
    # Skip SAS expander detection for ATA/SATA hosts
    # ATA hosts have paths like /sys/devices/.../ata1/hostX
    if 'ata' in real_path.lower():
        return None
    
    # Try to extract expander ID from existing device by-paths in /dev/disk/by-path
    # This is more reliable than sysfs traversal for some systems
    by_path_base = "/dev/disk/by-path"
    if os.path.exists(by_path_base):
        try:
            by_path_entries = os.listdir(by_path_base)
            # Look for SAS expander patterns: pci-{pci_addr}-sas-exp{expander_id}-phy*
            pattern = f"pci-{pci_address}-sas-exp"
            for entry in by_path_entries:
                if entry.startswith(pattern):
                    # Extract expander ID from the by-path entry
                    # Format: pci-0000:af:00.0-sas-exp0x500056b3059bdcff-phy0-lun-0
                    match = re.search(r'sas-exp(0x[0-9a-fA-F]+)-', entry)
                    if match:
                        expander_id = match.group(1)
                        break
        except (OSError, IOError):
            pass
    
    if expander_id:
        # Count phy ports by looking at by-path entries with this expander
        phy_count = 0
        if os.path.exists(by_path_base):
            try:
                by_path_entries = os.listdir(by_path_base)
                pattern = f"pci-{pci_address}-sas-exp{expander_id}-phy"
                phy_count = sum(1 for entry in by_path_entries if entry.startswith(pattern))
            except (OSError, IOError):
                pass
        
        if phy_count == 0:
            phy_count = get_max_slot_from_enclosure()
        
        if phy_count == 0:
            phy_count = 10  # Common SAS expander configuration
        
        return {
            'expander_id': expander_id,
            'phy_count': phy_count
        }
    
    # Fallback to sysfs traversal if by-path didn't work
    npath = real_path
    while npath and npath != "/":
        sas_device_dir = os.path.join(npath, "sas_device")
        if os.path.isdir(sas_device_dir):
            try:
                end_dev_ids = os.listdir(sas_device_dir)
                for end_dev_id in end_dev_ids:
                    # Extract expander ID from the end device ID format
                    # Format: typically contains the expander SAS address
                    if end_dev_id.startswith('0x') or ':' in end_dev_id:
                        if not expander_id:
                            expander_id = end_dev_id
                        
                        # Only count phy ports from end devices belonging to the same expander
                        # This prevents incorrectly summing phy counts from multiple expanders
                        if end_dev_id == expander_id:
                            phy_dir = os.path.join(sas_device_dir, end_dev_id)
                            if os.path.isdir(phy_dir):
                                try:
                                    phy_entries = os.listdir(phy_dir)
                                    phy_count = sum(1 for entry in phy_entries if entry.startswith('phy'))
                                    total_phy_count += phy_count
                                except (OSError, IOError):
                                    pass
            except (OSError, IOError):
                pass
        if expander_id:
            break
        npath = os.path.dirname(npath)

    if not expander_id:
        # Update cache with None result to avoid repeated failed scans
        if use_cache:
            with _SAS_EXPANDER_CACHE_LOCK:
                _SAS_EXPANDER_CACHE[pci_address] = {'data': None, 'timestamp': time.time()}
        return None

    # If no phy count found in sas_device directories, fall back to enclosure slot count
    if total_phy_count == 0:
        total_phy_count = get_max_slot_from_enclosure()

    # If still no count, use a reasonable default for SAS expanders
    if total_phy_count == 0:
        total_phy_count = 10  # Common SAS expander configuration

    result = {
        'expander_id': expander_id,
        'phy_count': total_phy_count
    }

    # Update cache with successful result
    if use_cache:
        with _SAS_EXPANDER_CACHE_LOCK:
            _SAS_EXPANDER_CACHE[pci_address] = {'data': result, 'timestamp': time.time()}

    return result


def get_parent_pci(real_path: str) -> Optional[str]:
    """Return the deepest (last) PCI address component from a sysfs real path.

    When a device sits behind an expander, the sysfs path contains multiple
    PCI addresses (root bridge, then HBA, etc.).  The last one is the actual
    HBA PCI address that appears in /dev/disk/by-path entries.

    Args:
        real_path: A resolved sysfs path string (e.g. from os.path.realpath)

    Returns:
        Last PCI address found in the path, or None if none present
    """
    matches = re.findall(
        r'[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F]',
        real_path
    )
    return matches[-1] if matches else None


def generate_master_slot_map(force_refresh: bool = False) -> List[Dict]:
    """Generate master slot map by scanning sysfs for all physical slot lanes.

    This function scans the hardware topology to build an inventory of all physical
    slot lanes on the system. It does NOT store active block device associations -
    only the static hardware mapping (PCI controller + slot type + slot number).

    Args:
        force_refresh: If True, bypass cache and force a fresh scan

    Returns:
        List of slot lane dictionaries with keys:
        - pci_controller: PCI address (e.g., '0000:af:00.0')
        - slot_type: One of 'sas_expander', 'sas_direct', 'motherboard_sata', 'pcie_nvme'
        - expander_sas_address: SAS expander address (null for direct AHCI/NVMe)
        - physical_slot_number: 0-indexed slot number from hardware
        - hardware_identifier: Hardware identifier (e.g., 'phy-0:0:0' or '101')
    """
    # Check cache first if not forcing refresh
    if not force_refresh:
        with _MASTER_SLOT_CACHE_LOCK:
            now = time.time()
            if _MASTER_SLOT_CACHE['data'] is not None and (now - _MASTER_SLOT_CACHE['timestamp']) < _MASTER_SLOT_CACHE_TTL:
                return _MASTER_SLOT_CACHE['data']

    master_map = []
    MAX_TOTAL_SLOTS = 1000  # Rule #5: enforce size limits for DoS prevention

    # Track seen (pci_controller, expander_sas_address, phy_num) tuples to avoid duplicates
    _seen_sas_phy = set()

    # Scan SAS expander topology from /sys/class/sas_phy
    # This enumerates ALL PHY lanes whether or not a drive is present, and records
    # the expander SAS address per PHY so resolution is expander-specific.
    sas_phy_base = "/sys/class/sas_phy"
    if os.path.isdir(sas_phy_base):
        try:
            for phy_name in os.listdir(sas_phy_base):
                if len(master_map) >= MAX_TOTAL_SLOTS:
                    logging.warning(f"Reached maximum slot limit of {MAX_TOTAL_SLOTS}")
                    break

                # phy_name format: phy-0:0:N  (where N = physical slot index)
                phy_match = re.search(r'phy-\d+(?::\d+)?:(\d+)$', phy_name)
                if not phy_match:
                    continue
                slot_number = int(phy_match.group(1))

                phy_path = os.path.join(sas_phy_base, phy_name)
                try:
                    real_path = os.path.realpath(phy_path)
                except (OSError, IOError):
                    continue

                pci_addr = get_parent_pci(real_path)
                if not pci_addr or not validate_pci_address(pci_addr):
                    continue

                # Read expander SAS address from the expander directory in the sysfs path
                # Expander directories may be named using the SAS address (e.g., expander-0x500056b3059bdcff)
                # or using a port:phy format (e.g., expander-0:0). Try both patterns.
                sas_addr = None
                exp_dir_match = re.search(r'/(expander-0x[0-9a-fA-F]+)/', real_path)
                if not exp_dir_match:
                    exp_dir_match = re.search(r'/(expander-\d+:\d+)/', real_path)
                if exp_dir_match:
                    exp_dir = exp_dir_match.group(1)
                    sas_addr_file = f"/sys/class/sas_device/{exp_dir}/sas_address"
                    try:
                        with open(sas_addr_file, 'r') as f:
                            sas_addr = f.read().strip()
                    except (OSError, IOError):
                        pass

                slot_type = "sas_expander" if sas_addr else "sas_direct"

                dedup_key = (pci_addr, sas_addr, slot_number)
                if dedup_key in _seen_sas_phy:
                    continue
                _seen_sas_phy.add(dedup_key)

                master_map.append({
                    'pci_controller': pci_addr,
                    'slot_type': slot_type,
                    'expander_sas_address': sas_addr,
                    'physical_slot_number': slot_number,
                    'hardware_identifier': phy_name
                })
        except (OSError, IOError) as e:
            logging.warning(f"Error scanning sas_phy for SAS expanders: {e}")

    # Fall back to /dev/disk/by-path for SAS expander detection to supplement sas_phy scan.
    # The sas_phy scan may not find expander entries due to sysfs path format differences,
    # so we always run the by-path scan to ensure expander entries are present.
    # Deduplication via _seen_sas_phy prevents duplicates.
    by_path_base = "/dev/disk/by-path"  # defined here for use by all subsequent scans
    if os.path.exists(by_path_base):
        try:
            by_path_entries = os.listdir(by_path_base)
            # Pattern: pci-{pci_addr}-sas-exp{expander_id}-phy{phy_num}-lun-0
            sas_expander_pattern = re.compile(r'^pci-([0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F])-sas-exp(0x[0-9a-fA-F]+)-phy(\d+)-')

            for entry in by_path_entries:
                if len(master_map) >= MAX_TOTAL_SLOTS:
                    logging.warning(f"Reached maximum slot limit of {MAX_TOTAL_SLOTS}")
                    break

                match = sas_expander_pattern.match(entry)
                if match:
                    pci_addr = match.group(1)
                    expander_id = match.group(2)
                    phy_num = int(match.group(3))

                    # Validate PCI address for defense-in-depth (lesson #9)
                    if not validate_pci_address(pci_addr):
                        logging.debug(f"Invalid PCI address in SAS expander entry: {pci_addr}")
                        continue

                    dedup_key = (pci_addr, expander_id, phy_num)
                    if dedup_key in _seen_sas_phy:
                        continue
                    _seen_sas_phy.add(dedup_key)

                    master_map.append({
                        'pci_controller': pci_addr,
                        'slot_type': 'sas_expander',
                        'expander_sas_address': expander_id,
                        'physical_slot_number': phy_num,
                        'hardware_identifier': f'phy-0:0:{phy_num}'
                    })
        except (OSError, IOError) as e:
            logging.warning(f"Error scanning by-path for SAS expanders: {e}")

    # Scan PCIe NVMe slots from /sys/bus/pci/slots/
    pci_slots_base = "/sys/bus/pci/slots"
    if os.path.exists(pci_slots_base):
        try:
            slot_entries = os.listdir(pci_slots_base)
            for slot_entry in slot_entries:
                if len(master_map) >= MAX_TOTAL_SLOTS:
                    logging.warning(f"Reached maximum slot limit of {MAX_TOTAL_SLOTS}")
                    break

                slot_path = os.path.join(pci_slots_base, slot_entry)
                if not os.path.isdir(slot_path):
                    continue

                # Extract PCI address from the slot's address file
                address_file = os.path.join(slot_path, 'address')
                if not os.path.exists(address_file):
                    continue

                try:
                    with open(address_file, 'r') as f:
                        pci_addr = f.read().strip()
                except (OSError, IOError):
                    continue

                # Validate PCI address format
                if not validate_pci_address(pci_addr):
                    logging.debug(f"Invalid PCI address in slot {slot_entry}: {pci_addr}")
                    continue

                # Use the slot_entry as the hardware_identifier (matches folder name in /sys/bus/pci/slots/)
                master_map.append({
                    'pci_controller': pci_addr,
                    'slot_type': 'pcie_nvme',
                    'expander_sas_address': None,
                    'physical_slot_number': int(slot_entry) if slot_entry.isdigit() else 0,
                    'hardware_identifier': slot_entry
                })
        except (OSError, IOError) as e:
            logging.warning(f"Error scanning PCI slots for NVMe: {e}")

    # Scan SAS direct-attached topology (no expander)
    # Pattern: pci-{pci_addr}-scsi-{host}:0:{slot}:0
    if os.path.exists(by_path_base):
        try:
            by_path_entries = os.listdir(by_path_base)
            # Pattern for direct-attached SAS: pci-{pci_addr}-scsi-{host}:0:{slot}:{lun}
            # Use \Z for strict end-of-string (lesson #12) and flexible LUN (\d+) for multi-LUN devices
            sas_direct_pattern = re.compile(r'^pci-([0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F])-scsi-(\d+):0:(\d+):\d+\Z')

            for entry in by_path_entries:
                if len(master_map) >= MAX_TOTAL_SLOTS:
                    logging.warning(f"Reached maximum slot limit of {MAX_TOTAL_SLOTS}")
                    break

                match = sas_direct_pattern.match(entry)
                if match:
                    pci_addr = match.group(1)
                    host_num = int(match.group(2))
                    slot_num = int(match.group(3))

                    # Check if this is already covered by SAS expander detection
                    # (avoid duplicates when expander is present)
                    is_duplicate = False
                    for existing in master_map:
                        if (existing['pci_controller'] == pci_addr and
                            existing['slot_type'] == 'sas_expander' and
                            existing['physical_slot_number'] == slot_num):
                            is_duplicate = True
                            break

                    if not is_duplicate:
                        # Validate PCI address for defense-in-depth (lesson #9)
                        if not validate_pci_address(pci_addr):
                            logging.debug(f"Invalid PCI address in SAS direct entry: {pci_addr}")
                            continue

                        master_map.append({
                            'pci_controller': pci_addr,
                            'slot_type': 'sas_direct',
                            'expander_sas_address': None,
                            'physical_slot_number': slot_num,
                            'hardware_identifier': f'phy-{host_num}:0:{slot_num}'
                        })
        except (OSError, IOError) as e:
            logging.warning(f"Error scanning by-path for SAS direct: {e}")

    # Scan motherboard SATA ports (ATA)
    # Pattern: pci-{pci_addr}-ata-{ata_num}
    if os.path.exists(by_path_base):
        try:
            by_path_entries = os.listdir(by_path_base)
            # Pattern for motherboard SATA: pci-{pci_addr}-ata-{ata_num}
            # Use \Z for strict end-of-string (lesson #12)
            sata_pattern = re.compile(r'^pci-([0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F])-ata-(\d+)\Z')

            for entry in by_path_entries:
                if len(master_map) >= MAX_TOTAL_SLOTS:
                    logging.warning(f"Reached maximum slot limit of {MAX_TOTAL_SLOTS}")
                    break

                match = sata_pattern.match(entry)
                if match:
                    pci_addr = match.group(1)
                    ata_num = int(match.group(2))

                    # Validate PCI address for defense-in-depth (lesson #9)
                    if not validate_pci_address(pci_addr):
                        logging.debug(f"Invalid PCI address in SATA entry: {pci_addr}")
                        continue

                    master_map.append({
                        'pci_controller': pci_addr,
                        'slot_type': 'motherboard_sata',
                        'expander_sas_address': None,
                        'physical_slot_number': ata_num,
                        'hardware_identifier': f'ata{ata_num}'
                    })
        except (OSError, IOError) as e:
            logging.warning(f"Error scanning by-path for SATA: {e}")

    # Supplement SATA scan with /sys/class/ata_port to capture empty SATA bays
    # that don't appear in by-path when no drive is installed.
    # Already-discovered ATA ports from by-path are skipped via seen_ata set.
    seen_ata = {
        (e['pci_controller'], e['physical_slot_number'])
        for e in master_map if e['slot_type'] == 'motherboard_sata'
    }
    sata_port_base = "/sys/class/ata_port"
    if os.path.isdir(sata_port_base):
        try:
            for port_name in os.listdir(sata_port_base):
                if len(master_map) >= MAX_TOTAL_SLOTS:
                    logging.warning(f"Reached maximum slot limit of {MAX_TOTAL_SLOTS}")
                    break
                port_match = re.search(r'ata(\d+)$', port_name)
                if not port_match:
                    continue
                ata_num = int(port_match.group(1))
                port_path = os.path.join(sata_port_base, port_name)
                try:
                    real_path = os.path.realpath(port_path)
                except (OSError, IOError):
                    continue
                pci_addr = get_parent_pci(real_path)
                if not pci_addr or not validate_pci_address(pci_addr):
                    continue
                if (pci_addr, ata_num) in seen_ata:
                    continue
                seen_ata.add((pci_addr, ata_num))
                master_map.append({
                    'pci_controller': pci_addr,
                    'slot_type': 'motherboard_sata',
                    'expander_sas_address': None,
                    'physical_slot_number': ata_num,
                    'hardware_identifier': f'ata{ata_num}'
                })
        except (OSError, IOError) as e:
            logging.warning(f"Error scanning ata_port for SATA: {e}")

    # Update cache with results
    with _MASTER_SLOT_CACHE_LOCK:
        _MASTER_SLOT_CACHE['data'] = master_map
        _MASTER_SLOT_CACHE['timestamp'] = time.time()

    logging.info(f"Generated master slot map with {len(master_map)} slot lanes")
    return master_map


def invalidate_master_slot_cache():
    """Invalidate the master slot map cache to force a fresh scan on next call.

    This should be called when hardware topology changes (e.g., bay_map.json modifications
    or physical hardware changes) to ensure the next discovery uses fresh hardware data.
    """
    with _MASTER_SLOT_CACHE_LOCK:
        _MASTER_SLOT_CACHE['data'] = None
        _MASTER_SLOT_CACHE['timestamp'] = 0
    logging.info("Master slot map cache invalidated")


def invalidate_sas_expander_cache():
    """Invalidate the SAS expander detection cache to force a fresh scan on next call.

    This should be called when hardware topology changes (e.g., SAS expander hot-plug
    or controller changes) to ensure the next discovery uses fresh hardware data.
    """
    with _SAS_EXPANDER_CACHE_LOCK:
        _SAS_EXPANDER_CACHE.clear()
    logging.info("SAS expander cache invalidated")


def invalidate_scsi_projections_cache():
    """Invalidate the SCSI host slot projections cache to force a fresh scan on next call.

    This should be called when hardware topology changes (e.g., drive hot-plug/removal
    or controller changes) to ensure the next discovery uses fresh hardware data.
    """
    with _SCSI_PROJECTIONS_CACHE_LOCK:
        _SCSI_PROJECTIONS_CACHE['data'] = None
        _SCSI_PROJECTIONS_CACHE['timestamp'] = 0
    logging.info("SCSI projections cache invalidated")


def resolve_multipath_parent(dev_name: str) -> str:
    """Check if a raw device is a slave of a multipath device and return the DM node.

    If the device is dual-ported under MPIO, returns the Device Mapper node
    (e.g., '/dev/mapper/mpatha' or '/dev/dm-X'). Otherwise returns the original path.

    Args:
        dev_name: Device name (e.g., 'sdb', 'sdc')

    Returns:
        Device path string (either /dev/mapper/mpathX, /dev/dm-X, or /dev/{dev_name})
    """
    if not dev_name or not isinstance(dev_name, str):
        return f"/dev/{dev_name}" if dev_name else "/dev/unknown"

    # Check if device is already a device mapper node
    if dev_name.startswith('dm-') or dev_name.startswith('mapper/'):
        return f"/dev/{dev_name}" if not dev_name.startswith('/') else dev_name

    holders_dir = f"/sys/block/{dev_name}/holders"
    if os.path.isdir(holders_dir):
        try:
            holders = os.listdir(holders_dir)
            dm_entries = [h for h in holders if h.startswith("dm-")]
            if dm_entries:
                dm_name = dm_entries[0]
                mapper_dir = "/dev/mapper"
                if os.path.isdir(mapper_dir):
                    for mapper_link in os.listdir(mapper_dir):
                        real_mapper_path = os.path.realpath(os.path.join(mapper_dir, mapper_link))
                        if real_mapper_path.endswith(dm_name):
                            return f"/dev/mapper/{mapper_link}"
                return f"/dev/{dm_name}"
        except (OSError, IOError):
            pass

    return f"/dev/{dev_name}"


def get_scsi_host_slot_projections(use_cache: bool = True) -> List[Dict]:
    """Scan SCSI hosts and project slot by-path information for physical bay mapping.

    This function implements the logic from the bash script that:
    1. Iterates /sys/class/scsi_host/host* to find HBA controllers
    2. Extracts PCI address from host device symlink
    3. Detects SAS expanders and projects phy-based paths if present
    4. Otherwise projects slot paths like pci-0000:01:00.0-scsi-0:0:0:0
    5. Checks /sys/class/scsi_device/{host}:0:{slot}:0 to see if slots are occupied
    6. Only projects slots that actually exist (detected by SCSI device directories)
    7. Filters out SES/enclosure management devices by checking device type
    8. Uses enclosure metadata to determine max slot for complete enumeration

    Args:
        use_cache: If True, return cached results if available and not expired

    Returns:
        List of slot projection dictionaries with keys:
        - pci_address: PCI address of the controller
        - host_number: SCSI host number
        - slot_number: Physical slot number (0-indexed)
        - projected_by_path: Predicted by-path string (e.g., pci-0000:01:00.0-scsi-0:0:0:0 or pci-0000:01:00.0-sas-exp0x500056b3059bdcff-phy0-lun-0)
        - device_path: Actual device path if occupied (e.g., /dev/sda), None if empty
        - device_name: Device name if occupied (e.g., sda), None if empty
        - is_sas_expander: True if this projection uses SAS expander phy paths
    """
    # Check cache first if enabled
    if use_cache:
        with _SCSI_PROJECTIONS_CACHE_LOCK:
            now = time.time()
            if _SCSI_PROJECTIONS_CACHE['data'] is not None and (now - _SCSI_PROJECTIONS_CACHE['timestamp']) < _SCSI_PROJECTIONS_CACHE_TTL:
                return _SCSI_PROJECTIONS_CACHE['data']

    projections = []
    scsi_host_base = "/sys/class/scsi_host"
    scsi_device_base = "/sys/class/scsi_device"
    MAX_TOTAL_PROJECTIONS = 1000  # Rule #5: enforce size limits for DoS prevention

    if not os.path.exists(scsi_host_base):
        logging.warning(f"SCSI host directory not found: {scsi_host_base}")
        return projections

    if not os.path.exists(scsi_device_base):
        logging.warning(f"SCSI device directory not found: {scsi_device_base}")
        return projections

    # Get max slot from enclosure metadata once (outside host loop to avoid redundant scans)
    enclosure_max_slot = get_max_slot_from_enclosure()

    try:
        host_dirs = os.listdir(scsi_host_base)
    except (OSError, IOError):
        logging.warning(f"Failed to list SCSI host directory: {scsi_host_base}")
        return projections

    # Move SCSI device directory listing outside host loop to avoid redundant I/O
    try:
        scsi_device_dirs = os.listdir(scsi_device_base)
    except (OSError, IOError):
        logging.warning(f"Failed to list SCSI device directory: {scsi_device_base}")
        return projections

    # Cache for device type checks to avoid redundant file I/O
    device_type_cache = {}

    for host_dir_name in host_dirs:
        if not host_dir_name.startswith('host'):
            continue

        host_path = os.path.join(scsi_host_base, host_dir_name)
        if not os.path.isdir(host_path):
            continue

        # Extract host number (e.g., host0 -> 0)
        try:
            host_num = int(host_dir_name[4:])
        except (ValueError, IndexError):
            logging.warning(f"Invalid host directory name: {host_dir_name}")
            continue

        # Trace back to the actual physical PCI device folder
        device_link = os.path.join(host_path, 'device')
        try:
            real_path = os.path.realpath(device_link)
        except (OSError, IOError):
            logging.debug(f"Failed to resolve device link for {host_dir_name}")
            continue

        # Extract PCI address from sysfs path
        # Path format: /sys/devices/pci0000:00/0000:00:1f.2/ata1/host0
        pci_matches = re.findall(r'([0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F])', real_path)
        if not pci_matches:
            logging.debug(f"No PCI address found in sysfs path for {host_dir_name}: {real_path}")
            continue

        pci_addr = pci_matches[-1]  # Use the last PCI address (actual controller, not bridge)

        # Detect SAS expander for this host (with caching)
        sas_expander_info = detect_sas_expander(host_path, pci_addr, use_cache=use_cache)
        is_sas_expander = sas_expander_info is not None

        if is_sas_expander:
            logging.info(f"Detected SAS expander for host {host_num}: expander_id={sas_expander_info['expander_id']}, phy_count={sas_expander_info['phy_count']}")
            # For SAS expanders, use phy-based projection
            max_slot = sas_expander_info['phy_count'] - 1
            expander_id = sas_expander_info['expander_id']

            for phy_num in range(sas_expander_info['phy_count']):
                # Rule #5: enforce total projection limit to prevent unbounded memory allocation
                if len(projections) >= MAX_TOTAL_PROJECTIONS:
                    logging.warning(f"Reached maximum projection limit of {MAX_TOTAL_PROJECTIONS}")
                    break

                # Construct SAS expander phy-based by-path string
                # Format: pci-{pci_addr}-sas-exp{expander_id}-phy{phy_num}-lun-0
                projected_by_path = f"pci-{pci_addr}-sas-exp{expander_id}-phy{phy_num}-lun-0"

                # For SAS expanders, we cannot easily detect which phy slots are occupied
                # without more complex sysfs traversal. Set as empty for now.
                # Enclosure slots data should provide the actual device mappings.
                projections.append({
                    'pci_address': pci_addr,
                    'host_number': host_num,
                    'slot_number': phy_num,
                    'projected_by_path': projected_by_path,
                    'device_path': None,
                    'device_name': None,
                    'is_sas_expander': True
                })
        else:
            # Standard SCSI slot projection
            # Find actual slots for this host by scanning SCSI device directories
            # Pattern: {host_num}:0:{slot}:0
            slot_pattern = re.compile(rf'^{host_num}:0:(\d+):0$')

            # Collect and sort slot numbers for deterministic ordering
            # Filter out SES/enclosure management devices by checking device type
            slot_numbers = []
            for device_dir in scsi_device_dirs:
                match = slot_pattern.match(device_dir)
                if match:
                    slot_num = int(match.group(1))
                    scsi_device_path = os.path.join(scsi_device_base, device_dir)

                    # Skip SES/enclosure management devices (not drive slots)
                    # Empty drive slots will NOT be filtered because they are not enclosure type
                    if is_enclosure_device(scsi_device_path, device_type_cache=device_type_cache):
                        logging.debug(f"Skipping SES/enclosure device at slot {slot_num}")
                        continue

                    slot_numbers.append(slot_num)

            slot_numbers.sort()

            # Use the highest SCSI device slot as the baseline for projection
            # This ensures drives inserted into previously empty bays are discovered
            # Enclosure metadata is only used as a fallback when no SCSI devices exist
            if slot_numbers:
                max_slot = max(slot_numbers)
            else:
                max_slot = enclosure_max_slot

            # Project all slots sequentially from 0 to max_slot for complete enumeration
            if max_slot > 0:
                for slot_num in range(max_slot + 1):
                    # Rule #5: enforce total projection limit to prevent unbounded memory allocation
                    if len(projections) >= MAX_TOTAL_PROJECTIONS:
                        logging.warning(f"Reached maximum projection limit of {MAX_TOTAL_PROJECTIONS}")
                        break

                    # Construct the standardized udev by-path string layout
                    # Format: pci-{pci_addr}-scsi-{host_num}:0:{slot}:0
                    projected_by_path = f"pci-{pci_addr}-scsi-{host_num}:0:{slot_num}:0"

                    # Check if this slot is currently occupied
                    scsi_device_path = os.path.join(scsi_device_base, f"{host_num}:0:{slot_num}:0")
                    device_path = None
                    device_name = None

                    if os.path.isdir(scsi_device_path):
                        # Find the current OS drive letter assigned to this slot
                        block_path = os.path.join(scsi_device_path, 'device', 'block')
                        try:
                            block_entries = os.listdir(block_path)
                            for entry in block_entries:
                                if entry.startswith('sd') or entry.startswith('nvme'):
                                    device_name = entry
                                    device_path = f"/dev/{entry}"
                                    break
                        except (OSError, IOError):
                            pass

                    projections.append({
                        'pci_address': pci_addr,
                        'host_number': host_num,
                        'slot_number': slot_num,
                        'projected_by_path': projected_by_path,
                        'device_path': device_path,
                        'device_name': device_name,
                        'is_sas_expander': False
                    })

        # Break outer loop if we've reached the limit
        if len(projections) >= MAX_TOTAL_PROJECTIONS:
            break

    # Update cache with results
    if use_cache:
        with _SCSI_PROJECTIONS_CACHE_LOCK:
            _SCSI_PROJECTIONS_CACHE['data'] = projections
            _SCSI_PROJECTIONS_CACHE['timestamp'] = time.time()

    return projections


# --- END OF FILE backend/device_discovery.py ---
