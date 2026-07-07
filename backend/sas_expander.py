# SAS expander detection and PCI parent resolution
# Extracted from device_discovery.py for modularity (A64)

import os
import re
import time
import threading
from typing import Dict, Optional
import logging

from enclosure_discovery import get_max_slot_from_enclosure

# Cache for SAS expander detection results to avoid redundant by-path scans
_SAS_EXPANDER_CACHE = {}  # Key: pci_address, Value: {'data': result, 'timestamp': time}
_SAS_EXPANDER_CACHE_TTL = 86400  # seconds (24 hours - SAS expander topology changes rarely; manual refresh on enclosure add/edit)
MAX_SAS_EXPANDER_CACHE_SIZE = 100  # Prevent unbounded growth
_SAS_EXPANDER_CACHE_LOCK = threading.Lock()

# Conservative fallback for SAS expander phy count when sysfs doesn't expose it
_DEFAULT_SAS_PHY_COUNT = 10

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
    if re.search(r'/ata\d+/host\d+', real_path):
        return None
    
    # Try to extract expander ID from existing device by-paths in /dev/disk/by-path
    # This is more reliable than sysfs traversal for some systems
    by_path_base = "/dev/disk/by-path"
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
        try:
            by_path_entries = os.listdir(by_path_base)
            pattern = f"pci-{pci_address}-sas-exp{expander_id}-phy"
            phy_count = sum(1 for entry in by_path_entries if entry.startswith(pattern))
        except (OSError, IOError):
            pass
        
        if phy_count == 0:
            phy_count = get_max_slot_from_enclosure()
        
        if phy_count == 0:
            phy_count = _DEFAULT_SAS_PHY_COUNT
        
        return {
            'expander_id': expander_id,
            'phy_count': phy_count
        }
    
    # Fallback to sysfs traversal if by-path didn't work
    npath = real_path
    while npath and npath != "/":
        sas_device_dir = os.path.join(npath, "sas_device")
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
        return None

    # If no phy count found in sas_device directories, fall back to enclosure slot count
    if total_phy_count == 0:
        total_phy_count = get_max_slot_from_enclosure()

    # If still no count, use a reasonable default for SAS expanders
    if total_phy_count == 0:
        total_phy_count = _DEFAULT_SAS_PHY_COUNT

    result = {
        'expander_id': expander_id,
        'phy_count': total_phy_count
    }

    # Update cache with successful result
    if use_cache:
        with _SAS_EXPANDER_CACHE_LOCK:
            if len(_SAS_EXPANDER_CACHE) >= MAX_SAS_EXPANDER_CACHE_SIZE:
                oldest_keys = sorted(_SAS_EXPANDER_CACHE, key=lambda k: _SAS_EXPANDER_CACHE[k]['timestamp'])
                for k in oldest_keys[:len(oldest_keys) - MAX_SAS_EXPANDER_CACHE_SIZE + 1]:
                    del _SAS_EXPANDER_CACHE[k]
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


def invalidate_sas_expander_cache():
    """Invalidate the SAS expander detection cache to force a fresh scan on next call.

    This should be called when hardware topology changes (e.g., SAS expander hot-plug
    or controller changes) to ensure the next discovery uses fresh hardware data.
    """
    with _SAS_EXPANDER_CACHE_LOCK:
        _SAS_EXPANDER_CACHE.clear()
    logging.info("SAS expander cache invalidated")
