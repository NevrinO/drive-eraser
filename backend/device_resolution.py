# Device resolution from enclosure slot — extracted from disk_ops.py (A70)
# Depends on: slot_mapping, sas_expander, pci_controllers (from device_discovery split)

import os
import re
import glob
import logging

from device_discovery import resolve_multipath_parent

_logger = logging.getLogger(__name__)


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
            physical_slot = int(physical_slot)
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
        return _resolve_via_sysfs_scsi(pci_controller, physical_slot, hw_identifier,
                                       expander_sas_address=expander_sas_address)
    elif slot_type == 'motherboard_sata':
        return _resolve_via_sysfs_ata(pci_controller, physical_slot, hw_identifier)

    return None


def _resolve_via_sysfs_scsi(pci_controller, physical_slot, hw_identifier=None, expander_sas_address=None):
    r"""Fallback: resolve SAS device via sysfs when by-path is missing.

    Uses three strategies in order of robustness:

    1. **SCSI device scan** (most robust): Scan ``/sys/class/scsi_device/`` entries,
       which persist even when by-path symlinks AND PHY ``device`` symlinks are
       removed by the kernel SCSI error handler. For each SCSI device that has a
       block device, parse its sysfs realpath to extract PCI controller, expander
       SAS address, and PHY number. Match against the bay map's physical_slot.

    2. **PHY device symlink**: Search ``/sys/class/sas_phy/`` for a PHY whose
       number matches, then follow its ``device`` symlink to the block device.
       Fails when the PHY's ``device`` symlink is also gone (broken by SCSI error
       handler for orphaned devices).

    3. **Host scan with target guess**: Scan SCSI hosts matching the PCI
       controller and guess the target ID from the physical slot number. Only
       works for direct-attach (non-expander) setups where target ID = slot.
       **Skipped entirely when ``expander_sas_address`` is set** — on expander
       setups, kernel-assigned target IDs have no relation to PHY numbers, so
       the guess would match unrelated secondary SAS paths to wrong/empty slots.
    """
    scsi_device_base = "/sys/class/scsi_device"
    sas_phy_base = "/sys/class/sas_phy"

    # Extract PHY number from hw_identifier or use physical_slot directly
    phy_num = physical_slot
    if hw_identifier and hw_identifier.startswith('phy-'):
        parts = hw_identifier.split(':')
        if len(parts) >= 3:
            try:
                phy_num = int(parts[-1])
            except (ValueError, TypeError):
                pass

    # --- Strategy 1: SCSI device scan with sysfs attribute reads ---
    # /sys/class/scsi_device/ entries persist even when by-path symlinks and
    # PHY device symlinks are gone. Each entry's device symlink points to its
    # position in the sysfs tree. We extract the expander name and end_device
    # port from the path, then READ sysfs attribute files to get the expander
    # SAS address and expander PHY number — these are NOT in the path itself.
    #
    # The sysfs path uses kernel-internal names like expander-14:3 (not the
    # SAS address 0x500304800145493f) and port-14:3:132 (not the expander
    # PHY number 12). We must read attribute files to bridge the gap.
    try:
        scsi_dev_entries = os.listdir(scsi_device_base)
    except (OSError, IOError):
        scsi_dev_entries = []

    sas_expander_base = "/sys/class/sas_expander"

    # Build a port→phy_identifier lookup map by iterating all SAS PHYs once.
    # The end_device port number in a SCSI device's realpath (e.g. 14:0:133) is
    # a kernel-internal port number, NOT the expander PHY number. The actual
    # /sys/class/sas_phy/ entry is named phy-14:0:28 (using the expander PHY
    # number 28), and its realpath contains port-14:0:133. We map the port
    # component to the phy_identifier so we can look it up for each SCSI device.
    port_to_phy_id = {}
    try:
        for phy_name in os.listdir(sas_phy_base):
            if not re.match(r'^phy-\d+:\d+:\d+\Z', phy_name):
                continue
            phy_path = os.path.join(sas_phy_base, phy_name)
            try:
                phy_real = os.path.realpath(phy_path)
            except (OSError, IOError):
                continue
            # Extract the port component from the PHY's realpath
            port_match = re.search(r'/port-(\d+:\d+:\d+)/', phy_real)
            if not port_match:
                continue
            port_component = port_match.group(1)
            # Read phy_identifier
            phy_id_file = os.path.join(phy_path, "phy_identifier")
            try:
                with open(phy_id_file, 'r') as f:
                    phy_id = int(f.read().strip())
            except (OSError, IOError, ValueError):
                continue
            port_to_phy_id[port_component] = phy_id
    except (OSError, IOError):
        pass

    _logger.debug("sysfs_scsi Strategy1: looking for pci=%s phy_num=%s expander=%s",
                  pci_controller, phy_num, expander_sas_address)
    _logger.debug("sysfs_scsi Strategy1: port_to_phy_id map has %d entries", len(port_to_phy_id))

    for scsi_dev_name in scsi_dev_entries:
        # SCSI device names are host:channel:target:lun
        if not re.match(r'^\d+:\d+:\d+:\d+\Z', scsi_dev_name):
            continue

        scsi_dev_path = os.path.join(scsi_device_base, scsi_dev_name)

        # Check if this SCSI device has a block device
        block_dir = os.path.join(scsi_dev_path, 'device', 'block')
        try:
            block_entries = os.listdir(block_dir)
        except (OSError, IOError):
            continue

        if not block_entries:
            continue

        # Follow the SCSI device's device symlink to get the real sysfs path
        device_link = os.path.join(scsi_dev_path, 'device')
        try:
            real_path = os.path.realpath(device_link)
        except (OSError, IOError):
            continue

        _logger.debug("sysfs_scsi Strategy1: %s block=%s realpath=%s",
                      scsi_dev_name, block_entries, real_path)

        # Extract PCI controller from the sysfs path (this works — PCI
        # addresses are in the path components)
        pci_matches = re.findall(r'[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F]', real_path)
        if not pci_matches or pci_matches[-1] != pci_controller:
            _logger.debug("sysfs_scsi Strategy1: %s skip pci %s != %s",
                          scsi_dev_name, pci_matches[-1] if pci_matches else None, pci_controller)
            continue

        # Extract the LAST expander name from the path (cascaded expanders
        # may have multiple; we need the one closest to the end device).
        # Path format: .../expander-14:3/port-14:3:132/end_device-14:3:132/...
        expander_names = re.findall(r'/expander-(\d+:\d+)/', real_path)
        has_expander = len(expander_names) > 0

        if expander_sas_address:
            # Bay map says this slot is on an expander — device must be too
            if not has_expander:
                _logger.debug("sysfs_scsi Strategy1: %s skip: no expander in path, need %s",
                              scsi_dev_name, expander_sas_address)
                continue

            # Read the expander's SAS address from sysfs attribute file
            last_expander = expander_names[-1]
            expander_sas_file = os.path.join(sas_expander_base, f"expander-{last_expander}", "sas_address")
            try:
                with open(expander_sas_file, 'r') as f:
                    scsi_expander_addr = f.read().strip()
            except (OSError, IOError):
                _logger.debug("sysfs_scsi Strategy1: %s skip: cannot read %s",
                              scsi_dev_name, expander_sas_file)
                continue

            if scsi_expander_addr != expander_sas_address:
                _logger.debug("sysfs_scsi Strategy1: %s skip expander addr %s != %s",
                              scsi_dev_name, scsi_expander_addr, expander_sas_address)
                continue
        else:
            # Bay map says direct-attach (no expander) — skip expander devices
            if has_expander:
                continue

        # Extract the LAST end_device port number from the path.
        # Format: end_device-14:0:133 → port identifier is "14:0:133"
        # This port number is a kernel-internal identifier, NOT the expander
        # PHY number. We use it to look up the phy_identifier from the map.
        end_dev_matches = re.findall(r'/end_device-(\d+:\d+:\d+)/', real_path)
        if not end_dev_matches:
            _logger.debug("sysfs_scsi Strategy1: %s skip: no end_device in path",
                          scsi_dev_name)
            continue

        end_dev_port = end_dev_matches[-1]

        # Look up the expander PHY number from the port→phy_identifier map.
        # The port number (e.g. 14:0:133) maps to the expander PHY number
        # (e.g. 28) via the PHY's realpath containing the same port component.
        if end_dev_port not in port_to_phy_id:
            _logger.debug("sysfs_scsi Strategy1: %s skip: port %s not in phy_id map",
                          scsi_dev_name, end_dev_port)
            continue

        expander_phy_num = port_to_phy_id[end_dev_port]

        if expander_phy_num != phy_num:
            _logger.debug("sysfs_scsi Strategy1: %s skip phy_id=%s != phy_num=%s",
                          scsi_dev_name, expander_phy_num, phy_num)
            continue

        # Match found — return the block device
        for block_name in block_entries:
            dev_path = f"/dev/{block_name}"
            if os.path.exists(dev_path):
                _logger.debug("sysfs_scsi Strategy1: %s MATCHED -> %s", scsi_dev_name, dev_path)
                return dev_path

    # --- Strategy 2: PHY device symlink (fails when PHY device symlink is gone) ---
    try:
        phy_names = os.listdir(sas_phy_base)
    except (OSError, IOError):
        phy_names = []

    for phy_name in phy_names:
        # PHY names are like phy-14:0:0, phy-14:3:132, etc.
        # The last component is a sysfs port number, NOT the expander PHY number.
        # We must read phy_identifier attribute to get the expander PHY number.
        if not re.match(r'^phy-\d+:\d+:\d+\Z', phy_name):
            continue

        phy_path = os.path.join(sas_phy_base, phy_name)

        # Read the expander PHY identifier from sysfs attribute
        phy_id_file = os.path.join(phy_path, "phy_identifier")
        try:
            with open(phy_id_file, 'r') as f:
                expander_phy_num = int(f.read().strip())
        except (OSError, IOError, ValueError):
            continue

        if expander_phy_num != phy_num:
            continue

        try:
            real_path = os.path.realpath(phy_path)
        except (OSError, IOError):
            continue

        # Verify PCI controller
        phy_pci_matches = re.findall(r'[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F]', real_path)
        if not phy_pci_matches or phy_pci_matches[-1] != pci_controller:
            continue

        # Verify expander if specified — read SAS address from attribute file
        if expander_sas_address:
            expander_names = re.findall(r'/expander-(\d+:\d+)/', real_path)
            if not expander_names:
                continue
            last_expander = expander_names[-1]
            expander_sas_file = os.path.join(sas_expander_base, f"expander-{last_expander}", "sas_address")
            try:
                with open(expander_sas_file, 'r') as f:
                    scsi_expander_addr = f.read().strip()
            except (OSError, IOError):
                continue
            if scsi_expander_addr != expander_sas_address:
                continue

        # Follow the PHY's device symlink to find the block device
        phy_device_link = os.path.join(phy_path, "device")
        try:
            scsi_dev_realpath = os.path.realpath(phy_device_link)
            block_dir = os.path.join(scsi_dev_realpath, 'block')
            block_entries = os.listdir(block_dir)
            for block_name in block_entries:
                dev_path = f"/dev/{block_name}"
                if os.path.exists(dev_path):
                    return dev_path
        except (OSError, IOError):
            continue

    # --- Strategy 3: Host scan with target=slot guess (last resort) ---
    # Only applicable for direct-attach (non-expander) setups where target ID = slot.
    # When an expander is used, target IDs are assigned by the kernel and have no
    # relation to PHY numbers, so this guess would match unrelated secondary paths
    # to wrong slots (e.g., a dual-ported drive's secondary path at target 15
    # getting assigned to an empty slot mapped to PHY 15).
    if expander_sas_address:
        return None

    scsi_host_base = "/sys/class/scsi_host"

    try:
        host_dirs = os.listdir(scsi_host_base)
    except (OSError, IOError):
        return None

    for host_dir in host_dirs:
        if not host_dir.startswith('host'):
            continue

        device_link = os.path.join(scsi_host_base, host_dir, 'device')
        try:
            real_path = os.path.realpath(device_link)
        except (OSError, IOError):
            continue

        pci_matches = re.findall(r'([0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F])', real_path)
        if not pci_matches:
            continue

        host_pci = pci_matches[-1]
        if host_pci != pci_controller:
            continue

        try:
            host_num = int(host_dir[4:])
        except (ValueError, IndexError):
            continue

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
