# Drive-related routes
import os
from flask import Blueprint, jsonify
from app_config import ERASE_JOBS, ERASE_JOBS_LOCK, logger
from common import get_config_dir, load_policy
from disk_ops import discover_drives
from disk_utils import format_capacity_bytes

drive_bp = Blueprint('drive_routes', __name__)

@drive_bp.route("/api/drives")
def get_drives():
    try:
        config_dir = get_config_dir()
        
        running_devices = set()
        with ERASE_JOBS_LOCK:
            for job in ERASE_JOBS.values():
                if job.get("status") in {"running", "queued"}:
                    dev = job.get("request", {}).get("device")
                    if dev:
                        running_devices.add(dev)

        drives = discover_drives(os.path.join(config_dir, "bay_map.json"), running_devices=running_devices)
        
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
        return jsonify({
            "passphrase_enabled": has_passphrase
        })
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        return jsonify({"error": str(e)}), 500
