# --- START OF FILE backend/common.py ---
import os
import json

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
        return {}
    with open(policy_path, "r", encoding="utf-8") as f:
        return json.load(f)
# --- END OF FILE backend/common.py ---