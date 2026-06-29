# --- START OF FILE backend/disk_ops.py ---
# Backward-compatibility re-export shim - do not add new code here
# All functions have been split into topic-specific modules:
#   os_detection.py, discovery_state.py, device_resolution.py,
#   drive_collection.py, extended_smart.py, discovery.py

from os_detection import get_os_parent_device, get_os_by_path, _get_os_by_path_cached
from device_resolution import _resolve_device_from_enclosure_slot, _resolve_device_from_hardware_identifier
from drive_collection import (
    _process_marker_status, _build_drive_payload, _collect_drive_data,
    _apply_drive_payload, _apply_collection_failure,
    _store_drive_payload, _get_cached_drive_payload, get_cached_smart_data,
    _DRIVE_DATA_CACHE, _DRIVE_DATA_CACHE_LOCK,
)
from extended_smart import (
    _get_extended_smart_executor, stop_extended_smart_pool,
    _process_single_drive_extended_smart, _submit_drive_for_extended_smart,
    set_websocket_manager, get_background_smart_max_workers,
    _websocket_manager, _EXTENDED_SMART_EXECUTOR, _EXTENDED_SMART_LOCK,
    _EXTENDED_SMART_PENDING,
)
from discovery_state import (
    _handle_discovery_signal, _check_discovery_interrupted,
    _discovery_interrupt_generation, _discovery_thread_state, _shutdown_event,
)
from discovery import (
    _is_eligible_for_zero_check, _auto_enqueue_zero_checks,
    get_discovery_max_workers, invalidate_drive_cache,
    _audit_dual_port_deduplication, _collect_pending_serial,
    _collect_pending_parallel, discover_drives, _build_path_to_dev,
    _load_wipe_passphrase, _finalize_discovery,
    _discover_drives_enclosure, _discover_drives_legacy,
    _DISCOVERY_MAX_WORKERS, _DISCOVERY_OVERALL_TIMEOUT,
)
# --- END OF FILE backend/disk_ops.py ---
