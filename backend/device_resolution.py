# Device resolution from enclosure slot — extracted from disk_ops.py (A70)
# Depends on: slot_mapping, sas_expander, pci_controllers (from device_discovery split)

import os
import re
import glob

from device_discovery import resolve_multipath_parent


def _resolve_device_from_enclosure_slot(slot_config, pci_controller, expander_sas_address=None):
    """Resolve active device path from enclosure slot configuration.

    Uses persisted hardware identifiers from bay_map.json (slot_type,
    hardware_identifier, physical_slot_number) to resolve the active device
    path. No master slot map scan is needed — the bay_map.json is authoritative
    once the enclosure is configured.

    Args:
        slot_config: Slot configuration dict with mappings
        pci_controller: PCI controller address for the enclosure
        expander_sas_address: SAS expander WWN (e.g. '0x500056b3...') or None for direct-attach

    Returns:
        Tuple of (resolved_device_path, interface_type) or (None, None) if not found
    """
    if not slot_config or not isinstance(slot_config, dict):
        return None, None

    mappings = slot_config.get('mappings', {})
    if not mappings:
        return None, None

    # Try each interface type mapping in priority order
    for interface_key, mapping in mappings.items():
        if not mapping or not isinstance(mapping, dict):
            continue

        slot_type = mapping.get('slot_type')
        hw_identifier = mapping.get('hardware_identifier')
        physical_slot = slot_config.get('physical_slot_number')

        if not slot_type or not hw_identifier:
            continue

        # Use persisted mappings from bay_map.json directly.
        # The hardware identifiers are authoritative once the enclosure is configured.
        dev_path = _resolve_device_from_hardware_identifier(
            pci_controller, slot_type, hw_identifier, physical_slot,
            expander_sas_address=expander_sas_address
        )

        if dev_path:
            # Apply MPIO resolution
            dev_name = os.path.basename(dev_path)
            resolved_path = resolve_multipath_parent(dev_name)
            return resolved_path, interface_key

    return None, None


def _resolve_device_from_hardware_identifier(pci_controller, slot_type, hw_identifier, physical_slot, expander_sas_address=None):
    """Resolve actual device path from hardware identifier.

    Args:
        pci_controller: PCI controller address
        slot_type: Slot type (sas_expander, sas_direct, motherboard_sata, pcie_nvme)
        hw_identifier: Hardware identifier (e.g., 'phy-0:0:0', '101', 'ata1')
        physical_slot: Physical slot number
        expander_sas_address: SAS expander WWN (e.g. '0x500056b3...') used to build an
            exact by-path match, preventing cross-expander slot collisions on the same HBA

    Returns:
        Device path string or None if not found
    """
    # Validate slot_type allowlist
    if slot_type not in ('sas_expander', 'sas_direct', 'motherboard_sata', 'pcie_nvme'):
        return None

    # Validate hw_identifier to prevent path traversal when used in os.path.join (Lesson #13)
    if not isinstance(hw_identifier, str) or not hw_identifier:
        return None
    if '..' in hw_identifier or '/' in hw_identifier or '\\' in hw_identifier or '\x00' in hw_identifier:
        return None
    if len(hw_identifier) > 100:
        return None

    # Validate pci_controller against PCI address format (A68)
    if not isinstance(pci_controller, str) or not re.match(r'^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F]\Z', pci_controller):
        return None

    # Validate physical_slot is a non-negative integer (A68)
    if physical_slot is not None:
        if isinstance(physical_slot, bool):
            return None
        if isinstance(physical_slot, int):
            if physical_slot < 0:
                return None
        elif isinstance(physical_slot, str):
            if not physical_slot.isdigit():
                return None
        else:
            return None

    # Validate expander_sas_address against WWN format if provided (A68)
    if expander_sas_address is not None:
        if not isinstance(expander_sas_address, str) or not re.match(r'^0x[0-9a-fA-F]{16}\Z', expander_sas_address):
            return None

    by_path_dir = '/dev/disk/by-path/'

    try:
        by_path_entries = os.listdir(by_path_dir)
    except (OSError, IOError):
        return None

    if slot_type == 'sas_expander':
        # Pattern: pci-{pci_addr}-sas-exp{expander_id}-phy{phy_num}-lun-0
        # When the expander SAS address is known, build an exact prefix that includes it
        # so that slots on different expanders behind the same HBA never cross-match.
        if expander_sas_address:
            exact_prefix = f"pci-{pci_controller}-sas-exp{expander_sas_address}-phy{physical_slot}-"
            for entry in by_path_entries:
                if entry.startswith(exact_prefix):
                    full_path = os.path.join(by_path_dir, entry)
                    if os.path.islink(full_path):
                        return os.path.realpath(full_path)
        else:
            # Fallback: no expander address known — match any expander on this controller
            pattern = f"pci-{pci_controller}-sas-exp"
            for entry in by_path_entries:
                if entry.startswith(pattern) and f"-phy{physical_slot}-" in entry:
                    full_path = os.path.join(by_path_dir, entry)
                    if os.path.islink(full_path):
                        return os.path.realpath(full_path)

    elif slot_type == 'sas_direct':
        # Pattern: pci-{pci_addr}-scsi-{host}:0:{slot}:0
        pattern = f"pci-{pci_controller}-scsi-"
        for entry in by_path_entries:
            if entry.startswith(pattern) and f":0:{physical_slot}:0" in entry:
                full_path = os.path.join(by_path_dir, entry)
                if os.path.islink(full_path):
                    return os.path.realpath(full_path)

    elif slot_type == 'motherboard_sata':
        # Pattern: pci-{pci_addr}-ata{ata_num}
        pattern = f"pci-{pci_controller}-ata"
        for entry in by_path_entries:
            if entry.startswith(pattern) and entry.endswith(f"-ata{physical_slot}"):
                full_path = os.path.join(by_path_dir, entry)
                if os.path.islink(full_path):
                    return os.path.realpath(full_path)

    elif slot_type == 'pcie_nvme':
        # For NVMe, match PCI address between device and slot
        # Hardware identifier can be:
        # 1. Slot folder name (e.g., '168') in /sys/bus/pci/slots/
        # 2. Full by-path (e.g., 'pci-0000:18:00.0-nvme-1') for fallback

        # Check if hw_identifier is a full by-path (fallback format)
        if hw_identifier.startswith('pci-') and 'nvme' in hw_identifier:
            # Direct by-path match - return the device if it exists
            by_path_dir = '/dev/disk/by-path'
            try:
                full_path = os.path.join(by_path_dir, hw_identifier)
                if os.path.islink(full_path):
                    return os.path.realpath(full_path)
            except (OSError, IOError):
                pass
            return None

        # Otherwise, treat as slot number and use PCI slot matching
        pci_slots_base = "/sys/bus/pci/slots"
        slot_address_file = os.path.join(pci_slots_base, hw_identifier, 'address')

        # Read the expected PCI address from the slot's address file
        expected_pci_addr = None
        try:
            with open(slot_address_file, 'r') as f:
                expected_pci_addr = f.read().strip()
        except (OSError, IOError):
            pass

        if not expected_pci_addr:
            return None

        # Scan NVMe devices and match PCI address
        block_dir = '/sys/class/block'
        try:
            for dev_name in os.listdir(block_dir):
                if dev_name.startswith('nvme'):
                    # Get the device's sysfs path
                    sys_path = f"/sys/class/block/{dev_name}"
                    real_path = os.path.realpath(sys_path)

                    # Traverse up to find the PCI device directory
                    # Path structure: /sys/devices/pci0000:17/0000:17:02.0/0000:18:00.0/nvme/nvme0/nvme0n1
                    # We need to find the LAST PCI device directory (the actual NVMe controller, not the bridge)
                    path_parts = real_path.split('/')
                    device_pci_addr = None

                    for part in reversed(path_parts):
                        # PCI device addresses match pattern: xxxx:xx:xx.x
                        if re.match(r'^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F]$', part):
                            device_pci_addr = part
                            break

                    # Normalize PCI addresses for comparison (strip function number if present)
                    # Slot addresses may be "0000:18:00" while device addresses are "0000:18:00.0"
                    normalized_device = device_pci_addr.split('.')[0] if device_pci_addr else None
                    normalized_expected = expected_pci_addr.split('.')[0] if expected_pci_addr else None

                    if normalized_device and normalized_device == normalized_expected:
                        return f"/dev/{dev_name}"
        except (OSError, IOError):
            pass

    # Fallback: if by-path resolution failed (symlinks removed by udev after SCSI
    # bus reset), try to find the device through sysfs using the persisted hardware
    # identifier. The block device may still exist in the kernel even though the
    # by-path symlink is gone. The hw_identifier (e.g., 'phy-0:0:0', 'ata1') is
    # stable and tied to the physical slot, so it works even after expander
    # re-enumeration changes SCSI target IDs.
    if slot_type in ('sas_expander', 'sas_direct'):
        return _resolve_via_sysfs_scsi(pci_controller, physical_slot, hw_identifier)
    elif slot_type == 'motherboard_sata':
        return _resolve_via_sysfs_ata(pci_controller, physical_slot, hw_identifier)

    return None


def _resolve_via_sysfs_scsi(pci_controller, physical_slot, hw_identifier=None):
    """Fallback: resolve SAS device via sysfs when by-path is missing.

    Primary path: use hw_identifier (e.g., 'phy-0:0:0') to follow the SAS PHY's
    device symlink directly to the SCSI device. This is stable across expander
    re-enumeration because the PHY name is tied to the physical slot.

    Secondary fallback: if hw_identifier is not available or the PHY symlink is
    missing, scan SCSI hosts matching the PCI controller and guess the target ID
    from the physical slot number. This may fail for expander setups where target
    IDs don't match PHY numbers.
    """
    scsi_device_base = "/sys/class/scsi_device"

    # Primary: follow SAS PHY device symlink
    if hw_identifier and hw_identifier.startswith('phy-'):
        phy_device_link = f"/sys/class/sas_phy/{hw_identifier}/device"
        try:
            scsi_dev_realpath = os.path.realpath(phy_device_link)
            # The symlink points to the SCSI device directory, e.g.:
            # /sys/devices/.../host0/port-0:0/target0:0:0/0:0:0:0
            # From there, check for block device
            block_dir = os.path.join(scsi_dev_realpath, 'block')
            block_entries = os.listdir(block_dir)
            for block_name in block_entries:
                dev_path = f"/dev/{block_name}"
                if os.path.exists(dev_path):
                    return dev_path
        except (OSError, IOError):
            pass

    # Secondary fallback: scan SCSI hosts, match PCI controller, guess target ID
    scsi_host_base = "/sys/class/scsi_host"

    try:
        host_dirs = os.listdir(scsi_host_base)
    except (OSError, IOError):
        return None

    for host_dir in host_dirs:
        if not host_dir.startswith('host'):
            continue

        # Get PCI address for this host
        device_link = os.path.join(scsi_host_base, host_dir, 'device')
        try:
            real_path = os.path.realpath(device_link)
        except (OSError, IOError):
            continue

        # Extract PCI address from sysfs path
        pci_matches = re.findall(r'([0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F])', real_path)
        if not pci_matches:
            continue

        host_pci = pci_matches[-1]
        if host_pci != pci_controller:
            continue

        # Extract host number
        try:
            host_num = int(host_dir[4:])
        except (ValueError, IndexError):
            continue

        # Check if SCSI device exists for this slot (assumes target ID = slot number)
        scsi_dev_path = os.path.join(scsi_device_base, f"{host_num}:0:{physical_slot}:0")
        block_dir = os.path.join(scsi_dev_path, 'device', 'block')
        try:
            block_entries = os.listdir(block_dir)
            for block_name in block_entries:
                dev_path = f"/dev/{block_name}"
                if os.path.exists(dev_path):
                    return dev_path
        except (OSError, IOError):
            continue

    return None


def _resolve_via_sysfs_ata(pci_controller, ata_num, hw_identifier=None):
    """Fallback: resolve SATA device via sysfs when by-path is missing.

    Primary path: use hw_identifier (e.g., 'ata1') to go directly to the ATA
    port and traverse to the block device.

    Secondary fallback: if hw_identifier is not available, scan all ATA ports
    matching the PCI controller and port number.
    """
    ata_port_base = "/sys/class/ata_port"
    scsi_device_base = "/sys/class/scsi_device"

    # Primary: use hw_identifier for direct ATA port lookup
    if hw_identifier and hw_identifier.startswith('ata'):
        port_path = os.path.join(ata_port_base, hw_identifier)
        if os.path.isdir(port_path):
            return _find_block_device_from_ata_port(port_path, scsi_device_base)

    # Secondary fallback: scan all ATA ports
    try:
        port_dirs = os.listdir(ata_port_base)
    except (OSError, IOError):
        return None

    for port_name in port_dirs:
        if not port_name.startswith('ata'):
            continue

        port_path = os.path.join(ata_port_base, port_name)
        try:
            real_path = os.path.realpath(port_path)
        except (OSError, IOError):
            continue

        # Extract PCI address from sysfs path
        pci_matches = re.findall(r'([0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F])', real_path)
        if not pci_matches:
            continue

        port_pci = pci_matches[-1]
        if port_pci != pci_controller:
            continue

        # Check if this is the right ATA port number
        port_match = re.search(r'ata(\d+)$', port_name)
        if not port_match:
            continue
        if int(port_match.group(1)) != ata_num:
            continue

        return _find_block_device_from_ata_port(port_path, scsi_device_base)

    return None


def _find_block_device_from_ata_port(port_path, scsi_device_base):
    """Find block device associated with an ATA port via its SCSI host."""
    host_dir_glob = os.path.join(port_path, 'host*')
    try:
        scsi_dev_dirs = os.listdir(scsi_device_base)
    except (OSError, IOError):
        return None

    try:
        host_dirs = glob.glob(host_dir_glob)
        for hdir in host_dirs:
            # Extract host number from path
            host_match = re.search(r'host(\d+)$', hdir)
            if not host_match:
                continue
            host_num = host_match.group(1)

            # Find SCSI device for this host
            for scsi_dev in scsi_dev_dirs:
                if scsi_dev.startswith(f"{host_num}:0:"):
                    block_dir = os.path.join(scsi_device_base, scsi_dev, 'device', 'block')
                    try:
                        block_entries = os.listdir(block_dir)
                        for block_name in block_entries:
                            dev_path = f"/dev/{block_name}"
                            if os.path.exists(dev_path):
                                return dev_path
                    except (OSError, IOError):
                        continue
    except (OSError, IOError):
        pass

    return None
