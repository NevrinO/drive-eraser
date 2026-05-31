# --- START OF FILE backend/common.py ---
import os
import json
import time

# Constants
DEFAULT_LOG_RETENTION_DAYS = 30  # Default number of days to retain log files

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def get_data_dir():
    candidates = [
        os.getenv("DRIVE_ERASER_DATA_DIR"),
        os.path.join(PROJECT_ROOT, "data"),
        "/opt/drive-eraser/data",
    ]
    for candidate in candidates:
        if candidate and os.path.isdir(candidate):
            return candidate
    return os.path.join(PROJECT_ROOT, "data")

def get_db_path():
    return os.path.join(get_data_dir(), "wipes.db")

def get_cert_dir():
    return os.path.join(get_data_dir(), "certs")

# --- START OF LOGGING DIRECTORY EXTENSIONS ---
def get_logs_dir():
    path = os.path.join(get_data_dir(), "logs")
    os.makedirs(path, exist_ok=True)
    return path

def get_active_logs_dir():
    path = os.path.join(get_logs_dir(), "active")
    os.makedirs(path, exist_ok=True)
    return path

def get_failed_logs_dir():
    path = os.path.join(get_logs_dir(), "failed")
    os.makedirs(path, exist_ok=True)
    return path

def purge_old_logs(max_age_days=DEFAULT_LOG_RETENTION_DAYS):
    """
    Scans active and failed log directories and purges any files
    whose last modified time exceeds max_age_days.
    """
    now = time.time()
    max_age_seconds = max_age_days * 86400
    targets = [get_logs_dir(), get_active_logs_dir(), get_failed_logs_dir()]
    
    purged_count = 0
    for target_dir in targets:
        if not os.path.isdir(target_dir):
            continue
        for entry in os.listdir(target_dir):
            full_path = os.path.join(target_dir, entry)
            # Ensure we only delete log files, avoiding folders
            if os.path.isfile(full_path) and entry.endswith(".log"):
                try:
                    mtime = os.path.getmtime(full_path)
                    if (now - mtime) > max_age_seconds:
                        os.remove(full_path)
                        purged_count += 1
                except Exception:
                    pass # Remain stable if a file is currently locked or deleted by another thread
    return purged_count
# --- END OF LOGGING DIRECTORY EXTENSIONS ---

def get_config_dir():
    candidates = [
        os.getenv("DRIVE_ERASER_CONFIG_DIR"),
        os.path.join(PROJECT_ROOT, "config"),
        "/opt/drive-eraser/config",
    ]
    for candidate in candidates:
        if candidate and os.path.isdir(candidate):
            return candidate
    return os.path.join(PROJECT_ROOT, "config")

def load_policy(config_dir=None):
    if config_dir is None:
        config_dir = get_config_dir()
    policy_path = os.path.join(config_dir, "policy.json")
    if not os.path.exists(policy_path):
        return {"lan_passphrase": "eraser123"}
    try:
        with open(policy_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Ensure fallback key exists
            if "lan_passphrase" not in data:
                data["lan_passphrase"] = "eraser123"
            return data
    except Exception:
        return {"lan_passphrase": "eraser123"}

def save_policy(policy_data, config_dir=None):
    if config_dir is None:
        config_dir = get_config_dir()
    os.makedirs(config_dir, exist_ok=True)
    policy_path = os.path.join(config_dir, "policy.json")
    with open(policy_path, "w", encoding="utf-8") as f:
        json.dump(policy_data, f, indent=2)

def save_bay_map(bay_map_data, config_dir=None):
    if config_dir is None:
        config_dir = get_config_dir()
    os.makedirs(config_dir, exist_ok=True)
    bay_map_path = os.path.join(config_dir, "bay_map.json")
    with open(bay_map_path, "w", encoding="utf-8") as f:
        json.dump(bay_map_data, f, indent=2)
# --- END OF FILE backend/common.py ---