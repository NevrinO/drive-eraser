# Enclosure (SES) hardware discovery and slot metadata
# Extracted from device_discovery.py for modularity (A64)

import os
import re
import time
import threading
from typing import Dict, List, Optional
import logging

# Cache for enclosure slot metadata to avoid redundant sysfs scans
_ENCLOSURE_CACHE = {'data': None, 'timestamp': 0}
_ENCLOSURE_CACHE_TTL = 86400  # seconds (24 hours - enclosure metadata changes rarely; manual refresh on enclosure add/edit)
_ENCLOSURE_CACHE_LOCK = threading.Lock()

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

            # Check if this is a slot directory (has a "device" symlink)
            if not os.path.isdir(slot_path):
                continue

            # Check if slot has a device (drive present)
            device_link = os.path.join(slot_path, "device")
            try:
                if os.path.islink(device_link):
                    # Resolve the symlink and check if it points to a block device
                    real_path = os.path.realpath(device_link)
                    # Block devices have a "block" subdirectory in their sysfs path
                    os.listdir(os.path.join(real_path, "block"))
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
        # Reuse slot_ids from the earlier os.listdir(enc_path) call —
        # the directory listing doesn't change between the two loops.
        total_slots = 0
        occupied_slots = 0

        for slot_id in slot_ids:
            if slot_id in METADATA_DIRS:
                continue
            slot_path = os.path.join(enc_path, slot_id)

            # Check if this is a slot directory (must be a directory)
            if not os.path.isdir(slot_path):
                continue

            total_slots += 1

            # Check if slot has a device (drive present)
            # Try multiple methods: status file, device symlink target existence
            status_file = os.path.join(slot_path, "status")
            try:
                with open(status_file, 'r') as f:
                    status = f.read().strip()
                    # Status file contains "unknown" when drive is present, "not installed" when empty
                    if status and status != "not installed":
                        occupied_slots += 1
                        continue
            except (OSError, IOError):
                pass

            # Fallback: check if device symlink target exists
            device_link = os.path.join(slot_path, "device")
            try:
                if os.path.islink(device_link):
                    real_path = os.path.realpath(device_link)
                    try:
                        os.stat(real_path)
                        occupied_slots += 1
                    except (OSError, IOError):
                        pass
            except (OSError, IOError):
                pass

        hardware_info.append({
            "pci_controller": pci_controller,
            "vendor": vendor,
            "model": model,
            "total_slots": total_slots,
            "occupied_slots": occupied_slots
        })

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
                with open(slot_num_path, 'r') as f:
                    slot_num = int(f.read().strip())
                    # Rule #31: validate numeric bounds
                    if 0 <= slot_num <= MAX_REASONABLE_SLOT and slot_num > max_slot:
                        max_slot = slot_num
            except (OSError, IOError, ValueError):
                # Fallback: try to parse from slot_id (e.g., "slot_1" -> 1)
                if slot_id.startswith('slot_'):
                    try:
                        slot_num = int(slot_id[5:])
                        # Rule #31: validate numeric bounds
                        if 0 <= slot_num <= MAX_REASONABLE_SLOT and slot_num > max_slot:
                            max_slot = slot_num
                    except ValueError:
                        pass

    # Update cache only on successful data acquisition
    if use_cache:
        with _ENCLOSURE_CACHE_LOCK:
            _ENCLOSURE_CACHE['data'] = max_slot
            _ENCLOSURE_CACHE['timestamp'] = time.time()

    return max_slot
