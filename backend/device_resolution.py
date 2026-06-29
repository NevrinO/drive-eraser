# Device resolution from enclosure slot — extracted from disk_ops.py (A70)
# Depends on: slot_mapping, sas_expander, pci_controllers (from device_discovery split)

import os
import re

from device_discovery import resolve_multipath_parent


def _resolve_device_from_enclosure_slot(slot_config, pci_controller, master_map, expander_sas_address=None):
    """Resolve active device path from enclosure slot configuration using master map.

    Args:
        slot_config: Slot configuration dict with mappings
        pci_controller: PCI controller address for the enclosure
        master_map: Master slot map from generate_master_slot_map()
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

        # Use persisted mappings directly - do not search master map by physical_slot_number
        # The master map only contains entries for occupied slots, so it fails for empty bays
        # Persisted identifiers in bay_map.json are authoritative
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

    return None
