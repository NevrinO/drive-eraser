# Drive-related routes
import os
import json
import sqlite3
from contextlib import closing
from flask import Blueprint, jsonify
from app_config import ERASE_JOBS, ERASE_JOBS_LOCK, logger, limiter
from common import get_config_dir, load_policy, get_db_path
from disk_ops import discover_drives, invalidate_drive_cache
from disk_utils import format_capacity_bytes
from routes.admin_routes import require_admin_auth
from database import load_prior_visit, get_smart_test_history, get_smart_test_status_batch
from device_discovery import (
    invalidate_sas_expander_cache,
    invalidate_scsi_projections_cache,
    invalidate_master_slot_cache
)

drive_bp = Blueprint('drive_routes', __name__)

@drive_bp.route("/api/drives")
@require_admin_auth
@limiter.limit("60 per minute")
def get_drives():
    try:
        config_dir = get_config_dir()

        # Invalidate all discovery caches to ensure fresh data on manual refresh
        # This is intentional: users clicking "Refresh" expect to see current hardware state
        # Performance impact is acceptable for manual refresh operations (rate-limited to 60/min)
        invalidate_sas_expander_cache()
        invalidate_scsi_projections_cache()
        invalidate_master_slot_cache()
        invalidate_drive_cache()

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

        with ERASE_JOBS_LOCK:
            for d in drives:
                bay_name = d.get("bay")
                for job_id, job in ERASE_JOBS.items():
                    req = job.get("request") or {}
                    if str(req.get("bay")).lower() == str(bay_name).lower():
                        if job.get("status") in {"running", "queued"}:
                            d["status"] = job["status"].upper()
                            d["progress_percent"] = job.get("progress_percent", 0.0)
                            d["current_phase"] = job.get("current_phase", "Sanitizing")

                            if req.get("serial"):
                                d["serial"] = req.get("serial")
                            if req.get("model"):
                                d["model"] = req.get("model")
                            if req.get("capacity_bytes"):
                                d["capacity_str"] = format_capacity_bytes(req.get("capacity_bytes"))
                            break

                        # Phase 5: Include prior-visit data and snapshot IDs when drive is linked to a job
                        if req.get("serial"):
                            serial = req.get("serial")
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
                                        (job_id,)
                                    ).fetchone()
                                    if row:
                                        d["has_pre_wipe_snapshot"] = bool(row["pre_wipe_smart_json"])
                                        d["has_post_wipe_snapshot"] = bool(row["post_wipe_smart_json"])
                            except Exception as e:
                                logger.warning(f"Failed to load snapshot IDs for job {job_id}: {e}")

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
