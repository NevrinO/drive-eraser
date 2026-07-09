# Backward-compatibility re-exports — do not add new code here
# Extracted from device_discovery.py for modularity (A64)
# All functions and variables now live in topic-specific modules.
# This shim preserves the public API so no import sites need changing.

from disk_utils import validate_device_path

from pci_controllers import (
    validate_pci_address,
    scan_pci_controllers,
    get_controller_for_device,
    discover_controllers_and_devices,
    invalidate_pci_cache,
    invalidate_discovery_cache,
)

from enclosure_discovery import (
    is_enclosure_device,
    get_enclosure_hardware_info,
    get_max_slot_from_enclosure,
    invalidate_enclosure_cache,
)

from sas_expander import (
    detect_sas_expander,
    get_parent_pci,
    invalidate_sas_expander_cache,
    MAX_SAS_EXPANDER_CACHE_SIZE,
)

from slot_mapping import (
    generate_master_slot_map,
    get_scsi_host_slot_projections,
    resolve_multipath_parent,
    invalidate_master_slot_cache,
    invalidate_scsi_projections_cache,
    rescan_scsi_hosts,
)
