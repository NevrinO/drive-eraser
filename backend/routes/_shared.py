# Shared utilities used by multiple route modules
# Extracted from admin_routes.py for modularity (fix-plan-G1)
import os
import re
import hmac
import ipaddress
from datetime import datetime, timezone
from flask import jsonify, request
from app_config import logger, calculate_session_token
from common import get_config_dir, load_policy
from smart_constants import SMART_TEST_GRACE_PERIOD_SECONDS, ESTIMATED_TEST_DURATION_SECONDS


def should_update_test_status(started_at, grace_period_seconds=SMART_TEST_GRACE_PERIOD_SECONDS):
    """Check if enough time has passed to trust drive status.
    
    The drive's self-test log may not update immediately after starting a test,
    so we need a grace period to avoid false completion/failure detection.
    
    Args:
        started_at: ISO format timestamp string of when the test started
        grace_period_seconds: Minimum seconds to wait before trusting drive status
        
    Returns:
        True if enough time has passed or if started_at is missing/invalid, False otherwise
    """
    if not started_at:
        return True
    try:
        start_time = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
        if elapsed < grace_period_seconds:
            return False
    except Exception as e:
        logger.warning(f"Failed to parse started_at timestamp: {e}")
    return True


def should_trust_completion_status(started_at, db_status, test_type):
    """Check if we can trust 'completed'/'failed'/'aborted' from the drive's log table.
    
    When the DB status is 'in_progress' (we've confirmed the test is actually running
    on the drive via the real-time status register), we can trust the log table's
    completion status after the normal grace period (10 seconds).
    
    When the DB status is 'started' (we've never confirmed the test is running),
    the drive's log table still shows the PREVIOUS test's result. We must NOT trust
    'completed'/'failed' until enough time has passed for the current test to have
    actually completed (based on estimated test duration).
    
    Args:
        started_at: ISO format timestamp string of when the test started
        db_status: Current DB status ('started' or 'in_progress')
        test_type: Type of test ('short', 'extended', 'offline', 'conveyance')
        
    Returns:
        True if the completion status can be trusted, False otherwise
    """
    if db_status == "in_progress":
        # Test was confirmed running — trust completion after normal grace period
        return should_update_test_status(started_at)
    elif db_status == "started":
        # Test was never confirmed running — use estimated duration as grace period
        # to avoid trusting stale log entries from previous tests
        estimated_duration = ESTIMATED_TEST_DURATION_SECONDS.get(test_type, 120)
        return should_update_test_status(started_at, grace_period_seconds=estimated_duration)
    return True


# Device name validation patterns following lesson #9 and #15
# Use \Z (not $) for strict end-of-string anchor to prevent "/dev/sda\n" bypass
# Lesson #91: Use specific patterns matching actual system naming conventions
_SATA_DEVICE_RE = re.compile(r'^sd[a-z]+\Z')
_NVME_DEVICE_RE = re.compile(r'^nvme[0-9]+(n[0-9]+)?(p[0-9]+)?\Z')
MAX_DEVICES_FOR_BUNDLE = 100  # Rule #5: enforce size limits for DoS prevention

_VALID_ROLES = frozenset({"wipe", "os", "reserved"})

# Size limits for DoS prevention (Rule #5)
MAX_ENCLOSURES = 100
MAX_SLOTS_PER_ENCLOSURE = 1000
MAX_TEMPLATES = 50

# ID validation pattern (alphanumeric, hyphens, underscores only)
_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+\Z')

def is_valid_id(id_str: str) -> bool:
    """Validate ID string against safe character whitelist.
    
    Following lessons-learned rule #9: Never accept raw strings without validation.
    
    Args:
        id_str: ID string to validate
        
    Returns:
        True if valid, False otherwise
    """
    if not id_str or not isinstance(id_str, str):
        return False
    if len(id_str) > 100:  # Reasonable length limit
        return False
    return bool(_ID_PATTERN.match(id_str))

def is_valid_device_name(name: str) -> bool:
    r"""Validate device name against strict whitelist to prevent path traversal and injection.
    
    Following lessons-learned rule #9: Never accept raw device paths without validation.
    Following lessons-learned rule #15: Use \Z for strict end-of-string anchor.
    
    Args:
        name: Device name string (e.g., "sda", "nvme0n1")
        
    Returns:
        True if name is valid, False otherwise
    """
    if not name or not isinstance(name, str):
        return False
    if ".." in name or "\n" in name or "\r" in name:
        return False
    return bool(_SATA_DEVICE_RE.match(name) or _NVME_DEVICE_RE.match(name))

def _validate_slot_metadata(custom_labels, custom_roles, slot_mappings, default_role=None):
    """Validate custom_labels, custom_roles, and slot_mappings for enclosure POST/PUT handlers.

    Args:
        custom_labels: Dict of slot number -> label string.
        custom_roles: Dict of slot number -> role string.
        slot_mappings: Dict of slot number -> slot mapping dict, or None.
        default_role: Default role for slot_mappings entries (POST uses template default, PUT uses None).

    Returns:
        Error message string if validation fails, None if valid.
    """
    # Validate custom_labels and custom_roles types and size limits
    if not isinstance(custom_labels, dict):
        return "custom_labels must be a dictionary"
    if not isinstance(custom_roles, dict):
        return "custom_roles must be a dictionary"
    if slot_mappings is not None and not isinstance(slot_mappings, dict):
        return "slot_mappings must be a dictionary"
    if len(custom_labels) > MAX_SLOTS_PER_ENCLOSURE:
        return f"Custom labels count exceeds maximum ({MAX_SLOTS_PER_ENCLOSURE})"
    if len(custom_roles) > MAX_SLOTS_PER_ENCLOSURE:
        return f"Custom roles count exceeds maximum ({MAX_SLOTS_PER_ENCLOSURE})"
    if slot_mappings is not None and len(slot_mappings) > MAX_SLOTS_PER_ENCLOSURE:
        return f"Slot mappings count exceeds maximum ({MAX_SLOTS_PER_ENCLOSURE})"

    # Validate custom label and role keys are strings
    for slot_num in custom_labels.keys():
        if not isinstance(slot_num, str):
            return f"Custom label key must be a string, got {type(slot_num).__name__}"
    for slot_num in custom_roles.keys():
        if not isinstance(slot_num, str):
            return f"Custom role key must be a string, got {type(slot_num).__name__}"

    # Validate custom label content
    for slot_num, label in custom_labels.items():
        if not isinstance(label, str):
            return f"Custom label for slot {slot_num} must be a string"
        if len(label) > 100:
            return f"Custom label for slot {slot_num} exceeds maximum length (100)"
        if any(ord(c) < 32 for c in label):
            return f"Custom label for slot {slot_num} contains invalid characters"

    # Validate custom role values against allowlist
    for slot_num, role in custom_roles.items():
        if role not in _VALID_ROLES:
            return f"Invalid role '{role}' for slot {slot_num}. Must be one of: {', '.join(sorted(_VALID_ROLES))}"

    # Validate slot_mappings if provided
    if slot_mappings is not None:
        for slot_key, slot_mapping in slot_mappings.items():
            if not isinstance(slot_mapping, dict):
                return f"Slot mapping for {slot_key} must be a dictionary"
            label = slot_mapping.get("label", "")
            if not isinstance(label, str):
                return f"Custom label for slot {slot_key} must be a string"
            if len(label) > 100:
                return f"Custom label for slot {slot_key} exceeds maximum length (100)"
            if any(ord(c) < 32 for c in label):
                return f"Custom label for slot {slot_key} contains invalid characters"
            role = slot_mapping.get("role", default_role)
            if role is not None and role not in _VALID_ROLES:
                return f"Invalid role '{role}' for slot {slot_key}. Must be one of: {', '.join(sorted(_VALID_ROLES))}"
            mappings = slot_mapping.get("mappings", {})
            if not isinstance(mappings, dict):
                return f"Mappings for slot {slot_key} must be a dictionary"
            for interface_type, mapping in mappings.items():
                if interface_type not in ("sas_sata", "nvme"):
                    return f"Invalid interface type '{interface_type}' for slot {slot_key}"
                if not isinstance(mapping, dict):
                    return f"Mapping for {interface_type} in slot {slot_key} must be a dictionary"
                if "slot_type" not in mapping or "hardware_identifier" not in mapping:
                    return f"Mapping for {interface_type} in slot {slot_key} must include slot_type and hardware_identifier"
                if mapping.get("slot_type") not in ("sas_expander", "sas_direct", "motherboard_sata", "pcie_nvme"):
                    return f"Invalid slot_type '{mapping.get('slot_type')}' for {interface_type} in slot {slot_key}"
                hw_id = mapping.get("hardware_identifier")
                if not isinstance(hw_id, str) or len(hw_id) == 0 or len(hw_id) > 100:
                    return f"Invalid hardware_identifier for {interface_type} in slot {slot_key}"

    return None

def is_local_request(req):
    """Check if the request is from localhost or local network."""
    remote_addr = req.remote_addr
    if not remote_addr:
        return False
    
    # Check for localhost IPv4 and IPv6
    if remote_addr in ('127.0.0.1', '::1'):
        return True
    
    # Check for private/local network ranges
    try:
        ip = ipaddress.ip_address(remote_addr)
        return ip.is_private
    except ValueError:
        return False

def require_admin_auth(f):
    """Decorator for conditional authentication on admin routes.
    
    Allows access from localhost without authentication.
    Requires authentication from remote addresses.
    """
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if is_local_request(request):
            return f(*args, **kwargs)
        
        # Remote request: require authentication
        config_dir = get_config_dir()
        policy = load_policy(config_dir)
        lan_passphrase = policy.get("lan_passphrase", "eraser123")
        session_token = request.cookies.get("admin_session")
        
        if not session_token or not hmac.compare_digest(session_token, calculate_session_token(lan_passphrase)):
            return jsonify({"error": "Authentication required"}), 401
        
        return f(*args, **kwargs)
    return decorated_function


_ENCLOSURE_METADATA_DIRS = {"components", "device", "id", "power", "subsystem", "uevent"}


def scan_enclosure_slots():
    """Scan /sys/class/enclosure for SCSI Enclosure Services slot information.

    Returns:
        List of dicts with keys: enclosure_id, slot_id, slot_number, slot_path, block_devs.
        block_devs is a sorted list of unique device node names (e.g. ["sda", "sdb"]).
        slot_number is None if the slot_id has no digits or is out of range 0-9999.
    """
    enclosure_base = "/sys/class/enclosure"
    entries = []

    try:
        enc_ids = os.listdir(enclosure_base)
    except (OSError, IOError):
        return entries

    for enc_id in enc_ids:
        enc_path = os.path.join(enclosure_base, enc_id)
        try:
            slot_ids = os.listdir(enc_path)
        except (OSError, IOError):
            continue

        for slot_id in slot_ids:
            if slot_id in _ENCLOSURE_METADATA_DIRS:
                continue
            slot_path = os.path.join(enc_path, slot_id)

            # Extract slot number with validation
            slot_num = None
            digits = re.findall(r'\d+', slot_id)
            if digits:
                try:
                    slot_num = int(digits[0])
                    if slot_num < 0 or slot_num > 9999:
                        slot_num = None
                except (ValueError, IndexError):
                    slot_num = None

            # Find associated block device nodes
            block_devs = []
            dev_block_path = os.path.join(slot_path, "device", "block")
            try:
                for b in os.listdir(dev_block_path):
                    block_devs.append(b)
            except (OSError, IOError):
                pass

            dev_path = os.path.join(slot_path, "device")
            try:
                for name in os.listdir(dev_path):
                    if name.startswith("sd") or name.startswith("nvme"):
                        block_devs.append(name)
            except (OSError, IOError):
                pass

            entries.append({
                "enclosure_id": enc_id,
                "slot_id": slot_id,
                "slot_number": slot_num,
                "slot_path": slot_path,
                "block_devs": sorted(list(set(block_devs))),
            })

    return entries
