# Backward-compatibility re-exports — do not add new code here
# Extracted from device_discovery.py for modularity (A64)
# All functions and variables now live in topic-specific modules.
# This shim preserves the public API so no import sites need changing.

from disk_utils import validate_device_path

from pci_controllers import (
    validate_pci_address,
    scan_pci_controllers,
    _map_pci_class_to_type,
    get_controller_for_device,
    discover_controllers_and_devices,
    _PCI_ADDRESS_RE,
    _PCI_CACHE,
    _PCI_CACHE_TTL,
    _PCI_CACHE_LOCK,
    _DISCOVERY_CACHE,
    _DISCOVERY_CACHE_TTL,
    _DISCOVERY_CACHE_LOCK,
)

from enclosure_discovery import (
    is_enclosure_device,
    get_enclosure_hardware_info,
    get_max_slot_from_enclosure,
    _ENCLOSURE_CACHE,
    _ENCLOSURE_CACHE_TTL,
    _ENCLOSURE_CACHE_LOCK,
)

from sas_expander import (
    detect_sas_expander,
    get_parent_pci,
    invalidate_sas_expander_cache,
    _SAS_EXPANDER_CACHE,
    _SAS_EXPANDER_CACHE_TTL,
    MAX_SAS_EXPANDER_CACHE_SIZE,
    _SAS_EXPANDER_CACHE_LOCK,
    _DEFAULT_SAS_PHY_COUNT,
)

from slot_mapping import (
    generate_master_slot_map,
    get_scsi_host_slot_projections,
    resolve_multipath_parent,
    invalidate_master_slot_cache,
    invalidate_scsi_projections_cache,
    _MASTER_SLOT_CACHE,
    _MASTER_SLOT_CACHE_TTL,
    _MASTER_SLOT_CACHE_LOCK,
    _SCSI_PROJECTIONS_CACHE,
    _SCSI_PROJECTIONS_CACHE_TTL,
    _SCSI_PROJECTIONS_CACHE_LOCK,
)
