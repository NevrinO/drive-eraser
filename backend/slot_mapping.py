# Master slot map generation, SCSI host projections, and multipath resolution
# Extracted from device_discovery.py for modularity (A64)

import os
import re
import time
import threading
from typing import Dict, List, Optional
import logging

from pci_controllers import validate_pci_address
from sas_expander import get_parent_pci, detect_sas_expander
from enclosure_discovery import is_enclosure_device, get_max_slot_from_enclosure

# Cache for master slot map (hardware topology) to avoid redundant sysfs scans
_MASTER_SLOT_CACHE = {'data': None, 'timestamp': 0}
_MASTER_SLOT_CACHE_TTL = 86400  # seconds (24 hours - master slot map changes rarely; manual refresh on enclosure add/edit)
_MASTER_SLOT_CACHE_LOCK = threading.Lock()

# Cache for SCSI host slot projections to avoid redundant full scans
_SCSI_PROJECTIONS_CACHE = {'data': None, 'timestamp': 0}
_SCSI_PROJECTIONS_CACHE_TTL = 86400  # seconds (24 hours - SCSI projections change rarely; manual refresh on enclosure add/edit)
_SCSI_PROJECTIONS_CACHE_LOCK = threading.Lock()

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
    try:
        for phy_name in os.listdir(sas_phy_base):
            if len(master_map) >= MAX_TOTAL_SLOTS:
                logging.warning(f"Reached maximum slot limit of {MAX_TOTAL_SLOTS}")
                break

            # phy_name format: phy-0:0:N  (where N = physical slot index)
            phy_match = re.search(r'phy-\d+(?::\d+)?:(\d+)\Z', phy_name)
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
    by_path_entries = []  # default in case os.listdir fails
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
    # Pre-build set of sas_expander slots for O(1) duplicate check (A-B7-7)
    sas_expander_slots = {
        (entry['pci_controller'], entry['physical_slot_number'])
        for entry in master_map
        if entry.get('slot_type') == 'sas_expander'
    }
    try:
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
                is_duplicate = (pci_addr, slot_num) in sas_expander_slots

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
    try:
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
    try:
        for port_name in os.listdir(sata_port_base):
            if len(master_map) >= MAX_TOTAL_SLOTS:
                logging.warning(f"Reached maximum slot limit of {MAX_TOTAL_SLOTS}")
                break
            port_match = re.search(r'ata(\d+)\Z', port_name)
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


def invalidate_scsi_projections_cache():
    """Invalidate the SCSI host slot projections cache to force a fresh scan on next call.

    This should be called when hardware topology changes (e.g., drive hot-plug/removal
    or controller changes) to ensure the next discovery uses fresh hardware data.
    """
    with _SCSI_PROJECTIONS_CACHE_LOCK:
        _SCSI_PROJECTIONS_CACHE['data'] = None
        _SCSI_PROJECTIONS_CACHE['timestamp'] = 0
    logging.info("SCSI projections cache invalidated")


def rescan_scsi_hosts():
    """Trigger SCSI host rescan to re-enumerate devices that dropped off the bus.

    Writes "- - -" to /sys/class/scsi_host/host*/scan, causing the kernel to
    rescan all SCSI buses. This is necessary when a bad drive causes SCSI bus
    resets that cause the kernel to remove other (good) devices from the bus.
    The good drives' block devices may still exist in /sys/class/block/ but
    their /dev/disk/by-path/ symlinks are gone, so discovery can't map them
    to physical bays.

    Also triggers udev to reprocess block device events, which recreates
    by-path symlinks for devices that are still in the kernel but lost their
    udev-managed symlinks.

    Returns:
        Number of SCSI hosts rescanned.
    """
    import subprocess

    scsi_host_base = "/sys/class/scsi_host"
    count = 0
    try:
        host_dirs = os.listdir(scsi_host_base)
    except (OSError, IOError):
        logging.warning(f"Cannot read {scsi_host_base}")
        return 0

    for host_dir in host_dirs:
        if not host_dir.startswith('host'):
            continue
        scan_path = os.path.join(scsi_host_base, host_dir, 'scan')
        try:
            with open(scan_path, 'w') as f:
                f.write("- - -\n")
            count += 1
            logging.info(f"Triggered SCSI rescan on {host_dir}")
        except (OSError, IOError) as e:
            logging.warning(f"Failed to rescan {host_dir}: {e}")

    if count > 0:
        # Give the kernel time to re-enumerate devices
        time.sleep(1.0)

        # Trigger udev to recreate by-path symlinks for re-discovered devices.
        # This handles the case where the block device still exists but the
        # by-path symlink was removed by udev when the SCSI device was dropped.
        try:
            subprocess.run(
                ['udevadm', 'trigger', '--subsystem-match=block', '--action=add'],
                timeout=10,
                capture_output=True
            )
            logging.info("Triggered udev reprocessing for block devices")
            # Give udev time to process events and create symlinks
            time.sleep(1.0)
        except Exception as e:
            logging.warning(f"udevadm trigger failed: {e}")

    logging.info(f"SCSI rescan complete: {count} hosts rescanned")
    return count


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

    if not re.match(r'^[a-zA-Z0-9_-]+\Z', dev_name):
        return "/dev/unknown"

    # Check if device is already a device mapper node
    # Note: 'mapper/' prefix is rejected by the regex above (contains '/'),
    # so only 'dm-' prefixed names reach this branch.
    if dev_name.startswith('dm-'):
        return f"/dev/{dev_name}"

    holders_dir = f"/sys/block/{dev_name}/holders"
    try:
        holders = os.listdir(holders_dir)
        dm_entries = [h for h in holders if h.startswith("dm-")]
        if dm_entries:
            dm_name = dm_entries[0]
            mapper_dir = "/dev/mapper"
            try:
                for mapper_link in os.listdir(mapper_dir):
                    real_mapper_path = os.path.realpath(os.path.join(mapper_dir, mapper_link))
                    if real_mapper_path.endswith(dm_name):
                        return f"/dev/mapper/{mapper_link}"
            except (OSError, IOError):
                pass
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
        return projections
    try:
        host_dirs = os.listdir(scsi_host_base)
    except (OSError, IOError):
        logging.warning(f"Failed to list SCSI host directory: {scsi_host_base}")
        return projections

    if not os.path.exists(scsi_device_base):
        return projections
    try:
        scsi_device_dirs = os.listdir(scsi_device_base)
    except (OSError, IOError):
        logging.warning(f"Failed to list SCSI device directory: {scsi_device_base}")
        return projections

    # Get max slot from enclosure metadata once (outside host loop to avoid redundant scans)
    enclosure_max_slot = get_max_slot_from_enclosure()

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
            slot_pattern = re.compile(rf'^{host_num}:0:(\d+):0\Z')

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
