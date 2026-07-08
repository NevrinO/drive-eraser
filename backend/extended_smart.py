# Extended SMART background collection pool — extracted from disk_ops.py (A70)
# Depends on: discovery_state, drive_collection, smart_data_parsing, smart_utils,
#   smart_health, disk_capabilities, database

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from common import get_config_dir, load_policy
from disk_utils import read_marker_status
from smart_data_parsing import get_smart_data, get_triage_thresholds
from smart_utils import detect_interface_type, is_drive_ssd
from smart_health import calculate_drive_health_score, get_drive_recommendation
from disk_capabilities import detect_drive_capabilities
from database import record_intake_snapshot
# These functions are shared across disk_ops.py, discovery.py, and extended_smart.py.
# They are prefixed with _ but are intentionally cross-module — they are internal to
# the drive data collection subsystem and not part of any public API.
from drive_collection import _process_marker_status, _build_drive_payload, _store_drive_payload, _get_cached_drive_payload
from discovery_state import _shutdown_event

# WebSocket manager reference (set at startup)
_websocket_manager = None

# Phase 1: Persistent background extended SMART collection worker pool
_EXTENDED_SMART_EXECUTOR = None
_EXTENDED_SMART_LOCK = threading.Lock()
_EXTENDED_SMART_PENDING = set()


def set_websocket_manager(ws_manager):
    """Set the WebSocket manager for broadcasting SMART data updates."""
    global _websocket_manager
    _websocket_manager = ws_manager
    logging.getLogger(__name__).info("WebSocket manager set for disk_ops")


def get_background_smart_max_workers():
    """Get the maximum number of workers for background extended SMART collection."""
    try:
        policy = load_policy(get_config_dir())
        workers = policy.get("background_smart_max_workers", 8)
        # Clamp to reasonable bounds for background load
        return max(1, min(workers, 32))
    except Exception:
        return 8


def _get_extended_smart_executor():
    """Lazy-initialize the persistent background extended SMART thread pool."""
    global _EXTENDED_SMART_EXECUTOR
    with _EXTENDED_SMART_LOCK:
        if _EXTENDED_SMART_EXECUTOR is not None:
            return _EXTENDED_SMART_EXECUTOR
        if _shutdown_event.is_set():
            return None
        workers = get_background_smart_max_workers()
        _EXTENDED_SMART_EXECUTOR = ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="ext-smart"
        )
        logging.getLogger(__name__).info(f"Started background extended SMART pool with {workers} workers")
        return _EXTENDED_SMART_EXECUTOR


def stop_extended_smart_pool(wait=True):
    """Shut down the persistent background extended SMART pool."""
    global _EXTENDED_SMART_EXECUTOR
    with _EXTENDED_SMART_LOCK:
        if _EXTENDED_SMART_EXECUTOR is not None:
            _EXTENDED_SMART_EXECUTOR.shutdown(wait=wait)
            _EXTENDED_SMART_EXECUTOR = None
            logging.getLogger(__name__).info("Stopped background extended SMART pool")
        # Clear pending set so cancelled futures do not leak entries
        _EXTENDED_SMART_PENDING.clear()


def _process_single_drive_extended_smart(item, passphrase):
    """Collect extended SMART data for a single drive and update the cache."""
    cache_key, dev_node, resolved_path, configured_path, configured_type, enclosure_id, slot_number = item
    logger = logging.getLogger(__name__)
    try:
        # Check if shutdown was requested
        if _shutdown_event.is_set():
            logger.info(f"Skipping extended SMART for {dev_node}, shutdown requested")
            return

        # Avoid redundant work if another task already cached this drive
        cached = _get_cached_drive_payload(cache_key)
        if cached is not None and not cached.get("smart", {}).get("smart_polling", True):
            logger.debug(f"Skipping extended SMART for {dev_node}, already cached")
            return

        # Collect full extended SMART data
        command_diagnostics = {}
        smart = get_smart_data(dev_node, command_diagnostics)
        interface_type = detect_interface_type(resolved_path or configured_path, dev_node, configured_type, smart.get("raw"))
        capabilities = detect_drive_capabilities(interface_type, dev_node, command_diagnostics)
        marker_status = read_marker_status(dev_node, interface_type, passphrase)
        _process_marker_status(marker_status, interface_type, smart)

        thresholds = get_triage_thresholds()
        health_score, penalty_breakdown = calculate_drive_health_score(interface_type, smart, thresholds=thresholds)
        recommendation = get_drive_recommendation(interface_type, smart, health_score=health_score, thresholds=thresholds)
        drive_type = "ssd" if is_drive_ssd(interface_type, smart) else "hdd"

        # Record intake snapshot now that we have full data
        serial = smart.get("serial")
        if serial:
            try:
                record_intake_snapshot(serial, smart, recommendation, health_score=health_score)
            except Exception as e:
                logger.warning(f"Failed to record intake snapshot for {serial}: {e}")

        # Build full payload with smart_polling: false
        payload = _build_drive_payload(dev_node, smart, interface_type, capabilities, marker_status, recommendation,
                                       health_score, penalty_breakdown, drive_type, command_diagnostics, False)

        # Update cache with full data
        _store_drive_payload(cache_key, payload)
        logger.info(f"Background extended SMART collection completed for {dev_node}")

        # Broadcast WebSocket event with updated SMART data
        if _websocket_manager:
            try:
                _websocket_manager.emit('smart_data_updated', {
                    'event': 'smart_data_updated',
                    'device': dev_node,
                    'enclosure_id': enclosure_id,
                    'slot_number': slot_number,
                    'status': payload.get('status'),
                    'smart': payload.get('smart'),
                    'health_score': payload.get('health_score'),
                    'recommendation': payload.get('recommendation'),
                    'marker': payload.get('marker')
                })
                logger.info(f"Broadcasted SMART data update for {dev_node}")
            except Exception as e:
                logger.warning(f"Failed to broadcast SMART data update for {dev_node}: {e}")

    except Exception as e:
        logger.warning(f"Background extended SMART collection failed for {dev_node}: {e}")
    finally:
        with _EXTENDED_SMART_LOCK:
            _EXTENDED_SMART_PENDING.discard(cache_key)


def _submit_drive_for_extended_smart(item, passphrase):
    """Submit a drive for background extended SMART collection if not already pending."""
    cache_key = item[0]
    if _shutdown_event.is_set():
        return
    with _EXTENDED_SMART_LOCK:
        if cache_key in _EXTENDED_SMART_PENDING:
            return
        _EXTENDED_SMART_PENDING.add(cache_key)
    try:
        executor = _get_extended_smart_executor()
        if executor is None:
            with _EXTENDED_SMART_LOCK:
                _EXTENDED_SMART_PENDING.discard(cache_key)
            return
        executor.submit(_process_single_drive_extended_smart, item, passphrase)
    except RuntimeError:
        with _EXTENDED_SMART_LOCK:
            _EXTENDED_SMART_PENDING.discard(cache_key)
