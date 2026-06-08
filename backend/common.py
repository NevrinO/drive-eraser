# --- START OF FILE backend/common.py ---
import os
import json
import time
import logging
from threading import Lock
from jsonschema import validate, ValidationError

# Constants
DEFAULT_LOG_RETENTION_DAYS = 30  # Default number of days to retain log files
DEFAULT_CERTIFICATE_RETENTION_DAYS = 365  # Default number of days to retain certificates
SIGNATURE_KDF_ITERATIONS = 200000  # Low #67: PBKDF2 iteration count for certificate signature (NIST recommendation: 100,000+)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Lock for bay_map.json to prevent concurrent modifications (rule #2)
BAY_MAP_LOCK = Lock()

# High #13: Device-level lock to prevent concurrent operations on same device
DEVICE_LOCKS = {}  # device_path -> Lock
DEVICE_LOCKS_LOCK = Lock()  # Lock for accessing DEVICE_LOCKS dict

logger = logging.getLogger("app")

def get_device_lock(device_path):
    """
    Get or create a device-specific lock for concurrent operation prevention.
    High #13: Shared lock mechanism for verification operations.
    """
    with DEVICE_LOCKS_LOCK:
        if device_path not in DEVICE_LOCKS:
            DEVICE_LOCKS[device_path] = Lock()
        return DEVICE_LOCKS[device_path]

DEFAULT_POLICY = {
    "prewipe_spot_check": True,
    "post_erase_marker": True,
    "allow_method_override": True,
    "crypto_fail_retry_block": True,
    "strict_audit_mode": True,
    "crypto_verification_mode": "conservative_probe",
    "health_soft_stop": True,
    "lan_passphrase": "eraser123",
}

# High #9: JSON schema for policy.json configuration validation
POLICY_SCHEMA = {
    "type": "object",
    "properties": {
        "prewipe_spot_check": {"type": "boolean"},
        "post_erase_marker": {"type": "boolean"},
        "allow_method_override": {"type": "boolean"},
        "method_priority": {
            "type": "object",
            "properties": {
                "nvme": {"type": "array", "items": {"type": "string"}},
                "sas": {"type": "array", "items": {"type": "string"}},
                "sata": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
        "crypto_fail_retry_block": {"type": "boolean"},
        "strict_audit_mode": {"type": "boolean"},
        "crypto_verification_mode": {"type": "string", "enum": ["conservative_probe", "full_verify", "disabled"]},
        "health_soft_stop": {"type": "boolean"},
        "port": {"type": "integer", "minimum": 1, "maximum": 65535},
        "bind_address": {"type": "string"},
        "station_id": {"type": "string"},
        "wipe_passphrase": {"type": "string"},
        "slack_webhook_url": {"type": "string"},
        "lan_passphrase": {"type": "string"},
        "allowed_cors_origins": {"type": "array", "items": {"type": "string"}},
        "certificate_retention_days": {"type": "integer", "minimum": 1},
        "max_logo_size_mb": {"type": "number", "minimum": 0.1},
        "max_bulk_cert_batch_size": {"type": "integer", "minimum": 1, "maximum": 1000},
        "triage_thresholds": {
            "type": "object",
            "properties": {
                "ssd_new_poh_threshold": {"type": "integer", "minimum": 0},
                "ssd_high_poh_threshold": {"type": "integer", "minimum": 0},
                "hdd_new_poh_threshold": {"type": "integer", "minimum": 0},
                "hdd_high_poh_threshold": {"type": "integer", "minimum": 0},
                "health_score_destroy_threshold": {"type": "integer", "minimum": 0},
                "health_score_scratch_threshold": {"type": "integer", "minimum": 0},
                "ssd_remaining_life_destroy_threshold": {"type": "integer", "minimum": 0},
                "ssd_remaining_life_scratch_threshold": {"type": "integer", "minimum": 0},
                "ssd_remaining_life_good_threshold": {"type": "integer", "minimum": 0},
                "ssd_new_fdw_threshold": {"type": "number", "minimum": 0},
                "hdd_new_fdw_threshold": {"type": "number", "minimum": 0},
                "hdd_heavy_fdw_threshold": {"type": "number", "minimum": 0},
                "realloc_raw_new_threshold": {"type": "integer", "minimum": 0},
                "pending_sectors_destroy_threshold": {"type": "integer", "minimum": 0},
                "pending_sectors_scratch_threshold": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
        },
    },
    "additionalProperties": True,  # Allow unknown keys but log warnings
}

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

def purge_old_certificates(max_age_days=DEFAULT_CERTIFICATE_RETENTION_DAYS):
    """
    Medium #58: Scans certificate directory and purges any files
    whose last modified time exceeds max_age_days.
    """
    now = time.time()
    max_age_seconds = max_age_days * 86400
    cert_dir = get_cert_dir()

    purged_count = 0
    if not os.path.isdir(cert_dir):
        return purged_count

    for entry in os.listdir(cert_dir):
        full_path = os.path.join(cert_dir, entry)
        # Ensure we only delete certificate files (json and html), avoiding folders
        if os.path.isfile(full_path) and (entry.endswith(".json") or entry.endswith(".html")):
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
    """
    Load and validate policy configuration from policy.json.
    High #9: Validates configuration against JSON schema and logs warnings for unknown keys.
    """
    if config_dir is None:
        config_dir = get_config_dir()
    policy_path = os.path.join(config_dir, "policy.json")
    if not os.path.exists(policy_path):
        return DEFAULT_POLICY.copy()
    try:
        with open(policy_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("policy.json must contain a JSON object")

            # High #9: Validate against schema
            try:
                validate(instance=data, schema=POLICY_SCHEMA)
            except ValidationError as e:
                raise ValueError(
                    f"Configuration validation failed: {e.message} "
                    f"(path: {'.'.join(str(p) for p in e.path)})"
                )

            # High #9: Log warnings for unknown configuration keys
            known_keys = set(POLICY_SCHEMA["properties"].keys())
            unknown_keys = set(data.keys()) - known_keys
            if unknown_keys:
                logger.warning(
                    f"Unknown configuration keys in policy.json: {', '.join(sorted(unknown_keys))}. "
                    f"These keys will be ignored."
                )

            # Merge with defaults
            merged = DEFAULT_POLICY.copy()
            merged.update(data)
            return merged
    except Exception as e:
        logger.error(f"Failed to load policy configuration: {e}")
        raise ValueError(f"Configuration load failed: {e}")

def validate_policy(policy):
    """
    Validate policy configuration for critical security requirements.
    Raises ValueError with descriptive message if validation fails.
    """
    # Critical #6: Validate wipe_passphrase when strict_audit_mode is enabled
    if policy.get("strict_audit_mode", False):
        wipe_passphrase = policy.get("wipe_passphrase", "")
        if not wipe_passphrase or not wipe_passphrase.strip():
            raise ValueError(
                "Configuration validation failed: strict_audit_mode is enabled but wipe_passphrase is empty. "
                "A non-empty wipe_passphrase is required when strict_audit_mode=true. "
                "Please set a strong passphrase in config/policy.json."
            )
        # Minimum strength check: at least 8 characters
        if len(wipe_passphrase) < 8:
            raise ValueError(
                "Configuration validation failed: wipe_passphrase is too weak (minimum 8 characters required). "
                "Please set a stronger passphrase in config/policy.json."
            )

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
    
    # Atomic file save: write to temp file first, then rename (rule #20)
    temp_path = bay_map_path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(bay_map_data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    
    # Atomic rename (POSIX guarantees this is atomic)
    os.rename(temp_path, bay_map_path)

def load_bay_map(config_dir=None):
    """
    Load bay map configuration with validation for placeholder values.
    Critical #7: Detects "REPLACE_ME" placeholder values and logs warning but allows load to proceed.
    Advisory #7: Fixed TOCTOU race condition by moving existence check inside lock.
    """
    if config_dir is None:
        config_dir = get_config_dir()
    bay_map_path = os.path.join(config_dir, "bay_map.json")
    
    with BAY_MAP_LOCK:
        # Advisory #7: Move existence check inside lock to prevent TOCTOU race condition
        if not os.path.exists(bay_map_path):
            return {}
        
        try:
            with open(bay_map_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # Critical #7: Detect placeholder values and log warning
            logger = __import__("logging").getLogger("app")
            has_placeholders = False
            for bay_id, bay_config in data.items():
                if isinstance(bay_config, dict) and bay_config.get("by_path") == "REPLACE_ME":
                    has_placeholders = True
                    logger.warning(f"Bay {bay_id} has placeholder device path 'REPLACE_ME'. Please configure device mapping via System Administration panel before production use.")

            if has_placeholders:
                logger.warning("Bay map contains placeholder device paths (REPLACE_ME). System will load but drive operations will fail until bays are properly configured.")
            
            return data
        except Exception as e:
            logger = __import__("logging").getLogger("app")
            logger.error(f"Failed to load bay map: {e}")
            return {}
# --- END OF FILE backend/common.py ---