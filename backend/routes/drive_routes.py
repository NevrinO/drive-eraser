# Drive-related routes
import os
import json
import re
import sqlite3
from contextlib import closing
from flask import Blueprint, jsonify
from app_config import ERASE_JOBS, ERASE_JOBS_LOCK, logger, limiter
from common import get_config_dir, load_policy, get_db_path
from disk_ops import discover_drives, invalidate_drive_cache, _is_eligible_for_zero_check
from disk_utils import format_capacity_bytes
from routes.admin_routes import require_admin_auth
from zero_check_manager import get_manager as get_zero_check_manager
from database import load_prior_visit, get_smart_test_history, get_smart_test_status_batch
from device_discovery import (
    invalidate_sas_expander_cache,
    invalidate_scsi_projections_cache,
    invalidate_master_slot_cache,
    rescan_scsi_hosts
)
from flask import request

_BAY_PATTERN = re.compile(r'^[a-zA-Z0-9_-]{1,50}\Z')


def _validate_bay(bay):
    """Validate bay parameter from URL path (A39)."""
    if not bay or not isinstance(bay, str) or not _BAY_PATTERN.match(bay):
        return False
    return True

drive_bp = Blueprint('drive_routes', __name__)

@drive_bp.route("/api/drives")
@require_admin_auth
@limiter.limit("60 per minute")
def get_drives():
    try:
        config_dir = get_config_dir()

        # Check if this is a manual refresh (explicit request for fresh hardware topology)
        force_refresh = request.args.get("force_refresh", "false").lower() == "true"
        
        if force_refresh:
            # Manual refresh: rescan SCSI bus first to restore devices that
            # dropped off due to bad drive causing SCSI resets/timeouts.
            # This re-enumerates devices and recreates /dev/disk/by-path symlinks.
            try:
                rescan_scsi_hosts()
            except Exception as e:
                logger.warning(f"SCSI rescan failed (non-fatal): {e}")

            # Invalidate all caches including hardware topology
            # Users clicking "Refresh" expect to see current hardware state
            invalidate_sas_expander_cache()
            invalidate_scsi_projections_cache()
            invalidate_master_slot_cache()
            invalidate_drive_cache()
        # Normal polling: do NOT invalidate drive cache
        # The 600s TTL in _get_cached_drive_payload() handles cache expiration
        # Drive hot-plug events invalidate cache via udev_listener
        # Other cache invalidations happen on bay mapping changes, wipe completion, policy changes

        running_devices = set()
        with ERASE_JOBS_LOCK:
            for job in ERASE_JOBS.values():
                if job.get("status") in {"running", "queued"}:
                    dev = job.get("request", {}).get("device")
                    if dev:
                        running_devices.add(dev)

        drives = discover_drives(os.path.join(config_dir, "bay_map.json"), running_devices=running_devices)

        # Add SMART test status to each drive using batch query for performance
        device_paths = [d.get("device") for d in drives if d.get("device")]
        smart_test_statuses = get_smart_test_status_batch(device_paths)

        for d in drives:
            device_path = d.get("device")
            if device_path and device_path in smart_test_statuses:
                try:
                    latest_test = smart_test_statuses[device_path]
                    test_status = latest_test.get("status")
                    if test_status in ("started", "in_progress"):
                        d["smart_test_status"] = "running"
                        d["smart_test_type"] = latest_test.get("test_type")
                    elif test_status == "completed":
                        d["smart_test_status"] = "completed"
                    elif test_status == "failed":
                        d["smart_test_status"] = "failed"
                except Exception as e:
                    logger.warning(f"Failed to set SMART test status for {device_path}: {e}")

        # Snapshot ERASE_JOBS state inside lock to avoid holding lock during DB queries
        jobs_snapshot = []
        with ERASE_JOBS_LOCK:
            for job_id, job in ERASE_JOBS.items():
                req = job.get("request") or {}
                jobs_snapshot.append({
                    "job_id": job_id,
                    "status": job.get("status"),
                    "progress_percent": job.get("progress_percent", 0.0),
                    "current_phase": job.get("current_phase", "Sanitizing"),
                    "bay": req.get("bay"),
                    "serial": req.get("serial"),
                    "model": req.get("model"),
                    "capacity_bytes": req.get("capacity_bytes"),
                    "eta_seconds": job.get("eta_seconds"),
                    "speed_mb_s": job.get("speed_mb_s"),
                    "elapsed_seconds": job.get("elapsed_seconds"),
                })

        # Match drives to jobs and perform DB queries outside lock
        for d in drives:
            bay_name = d.get("bay")
            for job_snap in jobs_snapshot:
                if str(job_snap.get("bay")).lower() == str(bay_name).lower():
                    if job_snap["status"] in {"running", "queued"}:
                        d["status"] = job_snap["status"].upper()
                        d["progress_percent"] = job_snap["progress_percent"]
                        d["current_phase"] = job_snap["current_phase"]
                        d["eta_seconds"] = job_snap.get("eta_seconds")
                        d["speed_mb_s"] = job_snap.get("speed_mb_s")
                        d["elapsed_seconds"] = job_snap.get("elapsed_seconds")
                        d["job_id"] = job_snap.get("job_id")

                        if job_snap.get("serial"):
                            d["serial"] = job_snap["serial"]
                        if job_snap.get("model"):
                            d["model"] = job_snap["model"]
                        if job_snap.get("capacity_bytes"):
                            d["capacity_str"] = format_capacity_bytes(job_snap["capacity_bytes"])
                        break

                    # Phase 5: Include prior-visit data and snapshot IDs when drive is linked to a job
                    if job_snap.get("serial"):
                        serial = job_snap["serial"]
                        prior_visit = load_prior_visit(serial)
                        if prior_visit:
                            d["prior_visit"] = {
                                "seen_at": prior_visit.get("seen_at"),
                                "health_score": prior_visit.get("health_score"),
                                "recommendation": prior_visit.get("recommendation"),
                            }

                        # Load snapshot IDs from database
                        try:
                            with closing(sqlite3.connect(get_db_path(), timeout=30.0)) as conn:
                                conn.row_factory = sqlite3.Row
                                row = conn.execute(
                                    "SELECT pre_wipe_smart_json, post_wipe_smart_json FROM erase_jobs WHERE id = ?",
                                    (job_snap["job_id"],)
                                ).fetchone()
                                if row:
                                    d["has_pre_wipe_snapshot"] = bool(row["pre_wipe_smart_json"])
                                    d["has_post_wipe_snapshot"] = bool(row["post_wipe_smart_json"])
                        except Exception as e:
                            logger.warning(f"Failed to load snapshot IDs for job {job_snap['job_id']}: {e}")

        # Merge ephemeral zero-check status for each drive
        try:
            zero_check_manager = get_zero_check_manager()
            for d in drives:
                bay = d.get("bay")
                if bay:
                    d["zero_check"] = zero_check_manager.get_status(bay)
        except Exception as e:
            logger.warning(f"Failed to merge zero-check status: {e}")

        return jsonify(drives)
    except Exception as e:
        logger.error(f"Error getting drives: {e}")
        return jsonify({"error": str(e)}), 500

@drive_bp.route("/api/status")
def get_status():
    try:
        config_dir = get_config_dir()
        policy = load_policy(config_dir)
        passphrase = policy.get("wipe_passphrase")
        has_passphrase = bool(
            passphrase and 
            passphrase.strip() and 
            passphrase != "your_secure_shared_secret_passphrase_here"
        )
        strict_audit = policy.get("strict_audit_mode", False)
        return jsonify({
            "passphrase_enabled": has_passphrase,
            "strict_audit_mode": strict_audit
        })
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        return jsonify({"error": str(e)}), 500


def _resolve_drive_for_bay(bay):
    """Resolve a bay to its current drive dict from discovery, or None."""
    if not bay:
        return None
    try:
        running_devices = set()
        with ERASE_JOBS_LOCK:
            for job in ERASE_JOBS.values():
                if job.get("status") in {"running", "queued"}:
                    dev = job.get("request", {}).get("device")
                    if dev:
                        running_devices.add(dev)
        config_dir = get_config_dir()
        drives = discover_drives(os.path.join(config_dir, "bay_map.json"), running_devices=running_devices, skip_auto_enqueue=True)
        for d in drives:
            if d.get("bay") == bay:
                return d
    except Exception as e:
        logger.warning(f"Failed to resolve drive for bay {bay}: {e}")
    return None


@drive_bp.route("/api/drives/<bay>/zero-check", methods=["POST"])
@require_admin_auth
@limiter.limit("30 per minute")
def start_zero_check(bay):
    """Manually trigger a background zero-check for a bay."""
    if not _validate_bay(bay):
        return jsonify({"error": "Invalid bay identifier"}), 400
    try:
        drive = _resolve_drive_for_bay(bay)
        if not drive:
            return jsonify({"error": f"bay not found or no drive present: {bay}"}), 404
        if not drive.get("present"):
            return jsonify({"error": f"no drive present in bay: {bay}"}), 409
        device = drive.get("device")
        if not device:
            return jsonify({"error": f"device not resolved for bay: {bay}"}), 409

        policy = load_policy(get_config_dir())
        if not policy.get("prewipe_zero_detection_enabled", True):
            return jsonify({"error": "pre-wipe zero detection is disabled by policy"}), 403

        manager = get_zero_check_manager()
        eligible, reason = _is_eligible_for_zero_check(drive, manager, allow_completed=True)
        if not eligible:
            return jsonify({"error": reason}), 409

        status = manager.start_check(bay, device, serial=drive.get("serial"))
        return jsonify({"status": "success", "zero_check": status}), 200
    except Exception as e:
        logger.error(f"Error starting zero check for bay {bay}: {e}")
        return jsonify({"error": str(e)}), 500


@drive_bp.route("/api/drives/<bay>/zero-check", methods=["DELETE"])
@require_admin_auth
@limiter.limit("30 per minute")
def cancel_zero_check(bay):
    """Cancel a running or queued zero-check for a bay."""
    if not _validate_bay(bay):
        return jsonify({"error": "Invalid bay identifier"}), 400
    try:
        manager = get_zero_check_manager()
        result = manager.cancel_check(bay)
        return jsonify({"status": "success", "cancelled": result.get("cancelled", True)}), 200
    except Exception as e:
        logger.error(f"Error cancelling zero check for bay {bay}: {e}")
        return jsonify({"error": str(e)}), 500
