# --- START OF FILE backend/common.py ---
import os
import json
import time
import logging
from threading import Lock, RLock
from weakref import WeakValueDictionary
from jsonschema import validate, ValidationError

# Constants
DEFAULT_LOG_RETENTION_DAYS = 30  # Default number of days to retain log files
SIGNATURE_KDF_ITERATIONS = 200000  # Low #67: PBKDF2 iteration count for certificate signature (NIST recommendation: 100,000+)
DRIVE_DATA_CACHE_TTL = 600  # seconds (10 minutes) - TTL for drive discovery cache

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Lock for bay_map.json to prevent concurrent modifications (rule #2).
# Use RLock because callers (e.g., admin_routes) hold this lock across the
# load-modify-save sequence while load_bay_map() also acquires it internally.
BAY_MAP_LOCK = RLock()

# High #13: Device-level lock to prevent concurrent operations on same device
DEVICE_LOCKS = WeakValueDictionary()  # device_path -> Lock (auto-cleaned when no references remain)
DEVICE_LOCKS_LOCK = Lock()  # Lock for accessing DEVICE_LOCKS dict

logger = logging.getLogger("app")

def get_device_lock(device_path):
    """
    Get or create a device-specific lock for concurrent operation prevention.
    High #13: Shared lock mechanism for verification operations.
    Uses WeakValueDictionary so locks are auto-cleaned when no callers hold references.
    """
    with DEVICE_LOCKS_LOCK:
        lock = DEVICE_LOCKS.get(device_path)
        if lock is None:
            lock = Lock()
            DEVICE_LOCKS[device_path] = lock
        return lock

DEFAULT_POLICY = {
    "prewipe_zero_detection_enabled": True,
    "zero_detection_concurrency_limit": 8,
    "zero_check_total_bytes_gb": 2,
    "zero_check_zone_count": 5,
    "zero_check_block_size_mb": 16,
    "zero_check_timeout_seconds": 60,
    "zero_check_small_drive_threshold_gb": 2,
    "post_erase_marker": True,
    "allow_method_override": True,
    "crypto_fail_retry_block": True,
    "strict_audit_mode": True,
    "secondary_verification_mode": "conservative_probe",
    "health_soft_stop": True,
    "lan_passphrase": "eraser123",
    "slack_webhook_url": "",
    "discovery_max_workers": 8,
    "background_smart_max_workers": 4,
    "max_concurrent_wipes": 64,
    "blockdev_post_wipe_retries": 3,
    "blockdev_post_wipe_retry_delay": 5,
    "prewipe_health_gate_enabled": True,
    "prewipe_health_gate_strict_mode": False,
    "prewipe_health_gate_block_destroy": True,
    "prewipe_health_gate_block_scratch": False,
    "prewipe_health_gate_block_failed_smart": True,
    "prewipe_health_gate_max_pending_sectors": 10,
    "prewipe_health_gate_max_reallocated_sectors": 5,
    "prewipe_health_gate_max_interface_errors": 100,
    "prewipe_health_gate_max_health_score_drop": 20,
}

# High #9: JSON schema for policy.json configuration validation
POLICY_SCHEMA = {
    "type": "object",
    "properties": {
        "prewipe_zero_detection_enabled": {"type": "boolean"},
        "zero_detection_concurrency_limit": {"type": "integer", "minimum": 1, "maximum": 32},
        "zero_check_total_bytes_gb": {"type": "integer", "minimum": 1, "maximum": 16},
        "zero_check_zone_count": {"type": "integer", "minimum": 1, "maximum": 32},
        "zero_check_block_size_mb": {"type": "integer", "minimum": 1, "maximum": 128},
        "zero_check_timeout_seconds": {"type": "integer", "minimum": 5, "maximum": 600},
        "zero_check_small_drive_threshold_gb": {"type": "integer", "minimum": 1, "maximum": 16},
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
        "secondary_verification_mode": {"type": "string", "enum": ["conservative_probe", "full_verify", "disabled"]},
        "crypto_verification_mode": {"type": "string", "enum": ["conservative_probe", "full_verify", "disabled"]},  # Deprecated: use secondary_verification_mode
        "health_soft_stop": {"type": "boolean"},
        "port": {"type": "integer", "minimum": 1, "maximum": 65535},
        "bind_address": {"type": "string"},
        "station_id": {"type": "string"},
        "wipe_passphrase": {"type": "string"},
        "slack_webhook_url": {"type": "string"},
        "lan_passphrase": {"type": "string"},
        "allowed_cors_origins": {"type": "array", "items": {"type": "string"}},
        "discovery_max_workers": {"type": "integer", "minimum": 1, "maximum": 32},
        "background_smart_max_workers": {"type": "integer", "minimum": 1, "maximum": 8},
        "max_concurrent_wipes": {"type": "integer", "minimum": 1, "maximum": 256},
        "blockdev_post_wipe_retries": {"type": "integer", "minimum": 0, "maximum": 10},
        "blockdev_post_wipe_retry_delay": {"type": "integer", "minimum": 0, "maximum": 60},
        "prewipe_health_gate_enabled": {"type": "boolean"},
        "prewipe_health_gate_strict_mode": {"type": "boolean"},
        "prewipe_health_gate_block_destroy": {"type": "boolean"},
        "prewipe_health_gate_block_scratch": {"type": "boolean"},
        "prewipe_health_gate_block_failed_smart": {"type": "boolean"},
        "prewipe_health_gate_max_pending_sectors": {"type": "integer", "minimum": 0, "maximum": 1000},
        "prewipe_health_gate_max_reallocated_sectors": {"type": "integer", "minimum": 0, "maximum": 1000},
        "prewipe_health_gate_max_interface_errors": {"type": "integer", "minimum": 0, "maximum": 100000},
        "prewipe_health_gate_max_health_score_drop": {"type": "integer", "minimum": 0, "maximum": 100},
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
                "sas_grown_defect_fail_threshold": {"type": "integer", "minimum": 0},
                "sas_grown_defect_scratch_threshold": {"type": "integer", "minimum": 0},
                "sas_nme_advisory_threshold": {"type": "integer", "minimum": 0},
                "sas_nme_penalty_threshold": {"type": "integer", "minimum": 0},
                "sas_sticky_lba_threshold": {"type": "integer", "minimum": 0},
                "sas_high_poh_threshold": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
        },
    },
    "additionalProperties": True,  # Allow unknown keys but log warnings
}

# JSON schema for template configuration (physical layout definitions)
TEMPLATE_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "name": {"type": "string"},
        "vendor": {"type": "string"},
        "slot_count": {"type": "integer", "minimum": 1, "maximum": 1000},
        "hybrid_slots": {
            "type": "array",
            "items": {"type": "integer", "minimum": 0},
        },
        "traversal_preset": {
            "type": "string",
            "enum": ["top_left_down_then_across", "bottom_left_up_then_across", "top_left_across_then_down", "bottom_left_across_then_up"]
        },
        "rows": {"type": "integer", "minimum": 1, "maximum": 16},
        "cols": {"type": "integer", "minimum": 1, "maximum": 5},
        "bay_count": {"type": "integer", "minimum": 1, "maximum": 128},
        "skip_positions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "row": {"type": "integer", "minimum": 0},
                    "col": {"type": "integer", "minimum": 0}
                },
                "required": ["row", "col"],
                "additionalProperties": False,
            },
            "maxItems": 100,
        },
        "default_role": {
            "type": "string",
            "enum": ["wipe", "os", "reserved"]
        },
    },
    "required": ["id", "name", "slot_count"],
    "additionalProperties": False,
}

# JSON schema for slot mapping (per interface type)
SLOT_MAPPING_SCHEMA = {
    "type": "object",
    "properties": {
        "slot_type": {
            "type": "string",
            "enum": ["sas_expander", "sas_direct", "motherboard_sata", "pcie_nvme"]
        },
        "hardware_identifier": {"type": "string"},
        "auto_detected": {"type": "boolean"},
        "pci_controller": {"type": "string"},
        "expander_sas_address": {"type": ["string", "null"]},
    },
    "required": ["slot_type", "hardware_identifier"],
    "additionalProperties": False,
}

# JSON schema for slot configuration
SLOT_SCHEMA = {
    "type": "object",
    "properties": {
        "physical_slot_number": {"type": "integer", "minimum": 0},
        "physical_position": {
            "type": "object",
            "properties": {
                "row": {"type": "integer", "minimum": 0},
                "col": {"type": "integer", "minimum": 0}
            },
            "required": ["row", "col"],
            "additionalProperties": False
        },
        "label": {"type": "string"},
        "role": {
            "type": "string",
            "enum": ["wipe", "os", "reserved"]
        },
        "locked": {"type": "boolean"},
        "mappings": {
            "type": "object",
            "properties": {
                "sas_sata": SLOT_MAPPING_SCHEMA,
                "nvme": SLOT_MAPPING_SCHEMA,
            },
            "additionalProperties": False,
        },
    },
    "required": ["physical_slot_number"],
    "additionalProperties": False,
}

# JSON schema for enclosure configuration
ENCLOSURE_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "name": {"type": "string", "minLength": 2},
        "template_id": {"type": "string"},
        "pci_controller": {
            "type": "string",
            "pattern": r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F]\Z"
        },
        "expander_sas_address": {
            "type": ["string", "null"],
            "pattern": r"^0x[0-9a-fA-F]+\Z"
        },
        "display_order": {"type": "integer", "minimum": 0},
        "slots": {
            "type": "object",
            "patternProperties": {
                r"^\d+$": SLOT_SCHEMA
            },
            "additionalProperties": False,
        },
    },
    "required": ["id", "name", "template_id", "pci_controller"],
    "additionalProperties": False,
}

# JSON schema for new enclosure-based bay_map.json
BAY_MAP_SCHEMA = {
    "type": "object",
    "properties": {
        "templates": {
            "type": "array",
            "items": TEMPLATE_SCHEMA,
        },
        "enclosures": {
            "type": "object",
            "patternProperties": {
                r".+": ENCLOSURE_SCHEMA
            },
            "additionalProperties": False,
        },
    },
    "required": ["enclosures"],
    "additionalProperties": False,
}

def get_data_dir():
    candidates = [
        os.getenv("DRIVE_ERASER_DATA_DIR"),
        os.path.join(PROJECT_ROOT, "data"),
        "/opt/drive-eraser/data",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            os.listdir(candidate)
            return candidate
        except OSError:
            continue
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
        if not candidate:
            continue
        try:
            os.listdir(candidate)
            return candidate
        except OSError:
            continue
    return os.path.join(PROJECT_ROOT, "config")

def load_policy(config_dir=None):
    """
    Load and validate policy configuration from policy.json.
    High #9: Validates configuration against JSON schema and logs warnings for unknown keys.
    """
    if config_dir is None:
        config_dir = get_config_dir()
    policy_path = os.path.join(config_dir, "policy.json")
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

            # Migration: deprecated crypto_verification_mode -> secondary_verification_mode
            if "secondary_verification_mode" not in merged and "crypto_verification_mode" in merged:
                merged["secondary_verification_mode"] = merged["crypto_verification_mode"]

            # Migration: deprecated prewipe_spot_check -> prewipe_zero_detection_enabled
            if "prewipe_spot_check" in merged:
                if "prewipe_zero_detection_enabled" not in data:
                    merged["prewipe_zero_detection_enabled"] = merged["prewipe_spot_check"]
                merged.pop("prewipe_spot_check", None)

            return merged
    except FileNotFoundError:
        return DEFAULT_POLICY.copy()
    except Exception as e:
        logger.error(f"Failed to load policy configuration: {e}")
        raise ValueError(f"Configuration load failed: {e}")

def validate_strict_audit_requirements(strict_audit_mode, wipe_passphrase):
    """
    Validate that strict_audit_mode requirements are met.
    
    Args:
        strict_audit_mode: Boolean indicating if strict audit mode is enabled
        wipe_passphrase: Current wipe passphrase (may be empty string)
        
    Returns:
        tuple: (is_valid: bool, error_message: str or None)
    """
    if strict_audit_mode:
        if not wipe_passphrase or not wipe_passphrase.strip():
            return False, "strict_audit_mode requires a non-empty wipe_passphrase"
        if len(wipe_passphrase) < 8:
            return False, "strict_audit_mode requires a wipe_passphrase of at least 8 characters"
    return True, None

def validate_policy(policy):
    """
    Validate policy configuration for critical security requirements.
    Raises ValueError with descriptive message if validation fails.
    """
    # Critical #6: Validate wipe_passphrase when strict_audit_mode is enabled
    strict_audit_mode = policy.get("strict_audit_mode", False)
    wipe_passphrase = policy.get("wipe_passphrase", "")
    
    is_valid, error_msg = validate_strict_audit_requirements(strict_audit_mode, wipe_passphrase)
    if not is_valid:
        raise ValueError(
            f"Configuration validation failed: {error_msg}. "
            "Please set a strong passphrase in config/policy.json."
        )

def save_policy(policy_data, config_dir=None):
    if config_dir is None:
        config_dir = get_config_dir()
    os.makedirs(config_dir, exist_ok=True)
    policy_path = os.path.join(config_dir, "policy.json")

    # Atomic file save: write to temp file first, then rename (rule #20)
    temp_path = policy_path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(policy_data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())

    # Atomic save: os.replace is atomic on POSIX and overwrites on Windows
    os.replace(temp_path, policy_path)

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
    
    # Atomic save: os.replace is atomic on POSIX and overwrites on Windows
    os.replace(temp_path, bay_map_path)

def load_bay_map(config_dir=None):
    """
    Load bay map configuration with validation for placeholder values.
    Critical #7: Detects "REPLACE_ME" placeholder values and logs warning but allows load to proceed.
    Advisory #7: Fixed TOCTOU race condition by removing existence check and catching FileNotFoundError.
    """
    if config_dir is None:
        config_dir = get_config_dir()
    bay_map_path = os.path.join(config_dir, "bay_map.json")
    
    with BAY_MAP_LOCK:
        try:
            with open(bay_map_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # Critical #7: Detect placeholder values and log warning
            has_placeholders = False
            for bay_id, bay_config in data.items():
                if isinstance(bay_config, dict) and bay_config.get("by_path") == "REPLACE_ME":
                    has_placeholders = True
                    logger.warning(f"Bay {bay_id} has placeholder device path 'REPLACE_ME'. Please configure device mapping via System Administration panel before production use.")

            if has_placeholders:
                logger.warning("Bay map contains placeholder device paths (REPLACE_ME). System will load but drive operations will fail until bays are properly configured.")
            
            return data
        except FileNotFoundError:
            return {}
        except Exception as e:
            logger.error(f"Failed to load bay map: {e}")
            return {}
# --- END OF FILE backend/common.py ---