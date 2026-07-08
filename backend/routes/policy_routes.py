# Policy routes: operational policy and triage config management
# Extracted from admin_routes.py for modularity (fix-plan-G1)
from flask import Blueprint, jsonify, request
import ipaddress
from app_config import logger, limiter
from common import get_config_dir, load_policy, save_policy, validate_strict_audit_requirements
from disk_ops import invalidate_drive_cache, stop_extended_smart_pool
from routes._shared import require_admin_auth

policy_bp = Blueprint('policy_routes', __name__)


@policy_bp.route("/api/admin/policy", methods=["GET", "POST"])
@require_admin_auth
@limiter.limit("30 per minute")
def admin_policy():
    config_dir = get_config_dir()
    if request.method == "GET":
        try:
            policy = load_policy(config_dir)
            safe_policy = policy.copy()
            if "lan_passphrase" in safe_policy:
                safe_policy["lan_passphrase"] = ""
            if "wipe_passphrase" in safe_policy:
                safe_policy["wipe_passphrase"] = ""
            return jsonify(safe_policy), 200
        except Exception as e:
            logger.error(f"Error getting policy: {e}")
            return jsonify({"error": str(e)}), 500
    else:
        try:
            payload = request.get_json(silent=True) or {}
            current_policy = load_policy(config_dir)
            
            # Extract new values from payload before validation
            new_strict_audit_mode = payload.get("strict_audit_mode")
            new_wipe_pass = str(payload.get("wipe_passphrase") or "").strip()
            new_lan_pass = str(payload.get("lan_passphrase") or "").strip()
            
            # Type validation for boolean fields
            if new_strict_audit_mode is not None and not isinstance(new_strict_audit_mode, bool):
                return jsonify({"error": "strict_audit_mode must be a boolean value"}), 400
            
            # Reject deprecated policy key explicitly
            if "crypto_verification_mode" in payload:
                return jsonify({"error": "crypto_verification_mode is deprecated; use secondary_verification_mode"}), 400
            
            # Validation: strict_audit_mode requires wipe_passphrase of at least 8 characters
            # Check both the new value from payload and the existing value in current_policy
            strict_audit_enabled = new_strict_audit_mode if new_strict_audit_mode is not None else current_policy.get("strict_audit_mode", False)
            if strict_audit_enabled:
                # Use new passphrase if provided, otherwise check existing passphrase
                passphrase_to_check = new_wipe_pass if new_wipe_pass else current_policy.get("wipe_passphrase", "")
                is_valid, error_msg = validate_strict_audit_requirements(strict_audit_enabled, passphrase_to_check)
                if not is_valid:
                    return jsonify({"error": error_msg}), 400
            
            # Validate numeric and string fields before applying mutations
            old_background_smart_max_workers = current_policy.get("background_smart_max_workers")

            # Numeric policy fields with (type, min, max) constraints
            numeric_policy_fields = {
                "max_concurrent_wipes": (int, 1, 256),
                "background_smart_max_workers": (int, 1, 32),
                "discovery_max_workers": (int, 1, 32),
                "zero_detection_concurrency_limit": (int, 1, 32),
                "zero_check_timeout_seconds": (int, 5, 300),
                "zero_check_startup_delay_seconds": (int, 0, 300),
                "blockdev_post_wipe_retries": (int, 0, 10),
                "blockdev_post_wipe_retry_delay": (int, 0, 60),
                "prewipe_health_gate_max_pending_sectors": (int, 0, 1000),
                "prewipe_health_gate_max_reallocated_sectors": (int, 0, 1000),
                "prewipe_health_gate_max_interface_errors": (int, 0, 100000),
                "prewipe_health_gate_max_health_score_drop": (int, 0, 100),
                "max_logo_size_mb": (float, 0.1, 50),
                "max_bulk_cert_batch_size": (int, 1, 1000),
                "log_retention_days": (int, 1, 365),
            }

            # Boolean policy fields
            boolean_policy_fields = {
                "prewipe_zero_detection_enabled", "post_erase_marker", "allow_method_override",
                "strict_audit_mode", "prewipe_health_gate_enabled",
                "prewipe_health_gate_strict_mode", "prewipe_health_gate_block_destroy",
                "prewipe_health_gate_block_scratch", "prewipe_health_gate_block_failed_smart",
                "discovery_diag",
            }

            # String policy fields with max length
            string_policy_fields = {
                "station_id": 100,
                "slack_webhook_url": 500,
                "secondary_verification_mode": 50,
            }

            for field, (val_type, min_val, max_val) in numeric_policy_fields.items():
                if field in payload:
                    try:
                        value = val_type(payload[field])
                        if not (min_val <= value <= max_val):
                            return jsonify({"error": f"Invalid value for {field}: must be between {min_val} and {max_val}"}), 400
                        current_policy[field] = value
                    except (ValueError, TypeError):
                        return jsonify({"error": f"Invalid type for {field}: must be {val_type.__name__}"}), 400

            for field in boolean_policy_fields:
                if field in payload:
                    if not isinstance(payload[field], bool):
                        return jsonify({"error": f"{field} must be a boolean value"}), 400
                    current_policy[field] = payload[field]

            _valid_secondary_verification_modes = {"conservative_probe", "full_verify", "disabled"}
            for field, max_len in string_policy_fields.items():
                if field in payload:
                    val = str(payload[field]) if payload[field] is not None else ""
                    if len(val) > max_len:
                        return jsonify({"error": f"{field} exceeds maximum length of {max_len} characters"}), 400
                    if field == "secondary_verification_mode" and val not in _valid_secondary_verification_modes:
                        return jsonify({"error": f"secondary_verification_mode must be one of: {', '.join(sorted(_valid_secondary_verification_modes))}"}), 400
                    current_policy[field] = val
                    
            lan_passphrase_changed = False
            if new_lan_pass:
                current_policy["lan_passphrase"] = new_lan_pass
                lan_passphrase_changed = True

            # Allowed remote IPs (list of IP addresses or CIDR ranges)
            if "allowed_remote_ips" in payload:
                ip_list = payload["allowed_remote_ips"]
                if not isinstance(ip_list, list):
                    return jsonify({"error": "allowed_remote_ips must be a list of strings"}), 400
                if len(ip_list) > 50:
                    return jsonify({"error": "allowed_remote_ips cannot exceed 50 entries"}), 400
                validated_ips = []
                for entry in ip_list:
                    entry_str = str(entry).strip() if entry is not None else ""
                    if not entry_str:
                        continue
                    try:
                        if "/" in entry_str:
                            ipaddress.ip_network(entry_str, strict=False)
                        else:
                            ipaddress.ip_address(entry_str)
                        validated_ips.append(entry_str)
                    except (ValueError, TypeError):
                        return jsonify({"error": f"Invalid IP or CIDR in allowed_remote_ips: {entry_str}"}), 400
                current_policy["allowed_remote_ips"] = validated_ips

            wipe_passphrase_changed = False
            if new_wipe_pass:
                current_policy["wipe_passphrase"] = new_wipe_pass
                wipe_passphrase_changed = True
                
            save_policy(current_policy, config_dir)
            
            # Passphrase change invalidates marker HMAC verification results in drive cache
            if lan_passphrase_changed or wipe_passphrase_changed:
                invalidate_drive_cache()
            
            # Restart the background SMART pool so a changed worker count takes effect immediately
            if "background_smart_max_workers" in payload and current_policy.get("background_smart_max_workers") != old_background_smart_max_workers:
                stop_extended_smart_pool(wait=False)

            # Update zero-check manager concurrency when the policy limit changes
            if "zero_detection_concurrency_limit" in payload:
                from zero_check_manager import get_manager as get_zero_check_manager
                get_zero_check_manager().set_concurrency(current_policy.get("zero_detection_concurrency_limit", 8))
            
            logger.info("Operational policies modified successfully by administrator.")
            return jsonify({"status": "success", "message": "System policies updated successfully."}), 200
        except Exception as e:
            logger.error(f"Error updating policy: {e}")
            return jsonify({"error": str(e)}), 500

@policy_bp.route("/api/admin/triage-config", methods=["GET", "POST"])
@require_admin_auth
@limiter.limit("30 per minute")
def admin_triage_config():
    config_dir = get_config_dir()
    if request.method == "GET":
        try:
            policy = load_policy(config_dir)
            triage_thresholds = policy.get("triage_thresholds", {})
            return jsonify(triage_thresholds), 200
        except Exception as e:
            logger.error(f"Error getting triage config: {e}")
            return jsonify({"error": str(e)}), 500
    else:
        try:
            payload = request.get_json(silent=True) or {}
            current_policy = load_policy(config_dir)
            
            # Validate all threshold values are numeric and within reasonable ranges
            valid_thresholds = {
                "ssd_new_poh_threshold": (int, 0, 100000),
                "ssd_high_poh_threshold": (int, 0, 200000),
                "hdd_new_poh_threshold": (int, 0, 100000),
                "health_score_destroy_threshold": (int, 0, 100),
                "health_score_scratch_threshold": (int, 0, 100),
                "health_score_good_threshold": (int, 0, 100),
                "ssd_new_fdw_threshold": (float, 0.0, 100.0),
                "hdd_new_fdw_threshold": (float, 0.0, 100.0),
                "realloc_raw_new_threshold": (int, 0, 1000),
                "sas_grown_defect_fail_threshold": (int, 0, 100000),
                "sas_nme_advisory_threshold": (int, 0, 100000000),
                "sas_nme_penalty_threshold": (int, 0, 1000000000),
                "sas_sticky_lba_threshold": (int, 0, 10),
                "sas_high_poh_threshold": (int, 0, 100000)
            }
            
            # Load existing thresholds and merge new values into them
            existing_thresholds = current_policy.get("triage_thresholds", {})
            new_thresholds = existing_thresholds.copy()
            
            for key, (val_type, min_val, max_val) in valid_thresholds.items():
                if key in payload:
                    try:
                        value = val_type(payload[key])
                        if not (min_val <= value <= max_val):
                            return jsonify({"error": f"Invalid value for {key}: must be between {min_val} and {max_val}"}), 400
                        new_thresholds[key] = value
                    except (ValueError, TypeError):
                        return jsonify({"error": f"Invalid type for {key}: must be {val_type.__name__}"}), 400
            
            current_policy["triage_thresholds"] = new_thresholds
            save_policy(current_policy, config_dir)
            
            logger.info("Triage thresholds updated successfully by administrator.")
            return jsonify({"status": "success", "message": "Triage thresholds updated successfully."}), 200
        except Exception as e:
            logger.error(f"Error updating triage config: {e}")
            return jsonify({"error": str(e)}), 500
