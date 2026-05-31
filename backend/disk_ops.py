# --- START OF FILE backend/disk_ops.py ---
# OS drive detection and discovery engine

import os
import json
import subprocess

from common import get_config_dir, load_policy
from disk_utils import resolve_bay_device, check_write_tolerance, read_marker_status
from smart_parsing import get_smart_data, detect_interface_type, calculate_drive_health_score, get_drive_recommendation
from disk_capabilities import detect_drive_capabilities

# --- PROGRAMMATIC OS DRIVE DETECTION AND OVERRIDES ---

def get_os_parent_device():
    try:
        st = os.stat("/")
        major = os.major(st.st_dev)
        minor = os.minor(st.st_dev)
        
        uevent_path = f"/sys/dev/block/{major}:{minor}/uevent"
        devname = None
        if os.path.exists(uevent_path):
            with open(uevent_path, "r") as f:
                for line in f:
                    if line.startswith("DEVNAME="):
                        devname = line.strip().split("=")[1]
                        break
                        
        if not devname:
            try:
                res = subprocess.run(["findmnt", "-n", "-o", "SOURCE", "/"], capture_output=True, text=True, timeout=5)
                if res.returncode == 0 and res.stdout.strip():
                    src = res.stdout.strip()
                    if src.startswith("/dev/"):
                        devname = src[5:]
            except Exception:
                pass
                
        if not devname:
            if os.path.exists("/proc/mounts"):
                with open("/proc/mounts", "r") as f:
                    for line in f:
                        parts = line.split()
                        if len(parts) >= 2 and parts[1] == "/":
                            src = parts[0]
                            if src.startswith("/dev/"):
                                devname = src[5:]
                                break

        if not devname:
            return None
            
        def resolve_leaf_parent(name):
            sys_path = f"/sys/class/block/{name}"
            if not os.path.exists(sys_path):
                return name
            real_path = os.path.realpath(sys_path)
            if "/block/" in real_path:
                parts = real_path.split("/block/")
                if len(parts) > 1:
                    subparts = parts[1].split("/")
                    if len(subparts) > 0:
                        return subparts[0]
            return name

        if devname.startswith("dm-"):
            slaves_dir = f"/sys/class/block/{devname}/slaves"
            if os.path.isdir(slaves_dir):
                slaves = os.listdir(slaves_dir)
                if slaves:
                    return resolve_leaf_parent(slaves[0])
                    
        return resolve_leaf_parent(devname)
    except Exception:
        return None

def get_os_by_path():
    parent_name = get_os_parent_device()
    if not parent_name:
        return None, None
        
    dev_node = f"/dev/{parent_name}"
    by_path_dir = "/dev/disk/by-path/"
    if os.path.exists(by_path_dir):
        for entry in os.listdir(by_path_dir):
            full_path = os.path.join(by_path_dir, entry)
            if os.path.islink(full_path):
                if "-part" in entry:
                    continue
                if os.path.realpath(full_path) == os.path.realpath(dev_node):
                    return dev_node, entry
                    
    return dev_node, None

# --- DISCOVERY ENGINE ---

def discover_drives(bay_map_path='/opt/drive-eraser/config/bay_map.json', running_devices=None):
    try:
        with open(bay_map_path, 'r', encoding='utf-8') as f:
            bay_map_doc = json.load(f)
    except Exception:
        return []

    if isinstance(bay_map_doc, dict) and isinstance(bay_map_doc.get("bays"), dict):
        bay_map = bay_map_doc.get("bays", {})
    else:
        bay_map = {
            k: v for k, v in (bay_map_doc or {}).items()
            if isinstance(v, dict) and any(x in v for x in ["role", "by_path", "by_path_nvme", "type", "label", "locked"])
        }

    path_to_dev = {}
    by_path_dir = '/dev/disk/by-path/'
    if os.path.exists(by_path_dir):
        for entry in os.listdir(by_path_dir):
            full_path = os.path.join(by_path_dir, entry)
            if os.path.islink(full_path): path_to_dev[entry] = os.path.realpath(full_path)

    results, passphrase = [], None
    try: passphrase = load_policy(get_config_dir()).get("wipe_passphrase")
    except Exception: pass

    os_dev_node, os_by_path = get_os_by_path()

    for bay_id, config in bay_map.items():
        target_path = config.get('by_path')
        target_path_nvme = config.get('by_path_nvme')
        
        bay_info = {
            "bay": bay_id,
            "display_number": config.get("display_number"),
            "physical_position": config.get("physical_position"),
            "label": config.get('label', bay_id),
            "role": config.get('role', 'wipe'), 
            "locked": config.get('locked', False),
            "configured_by_path": target_path, 
            "resolved_by_path": None,
            "configured_by_path_nvme": target_path_nvme, 
            "resolved_by_path_nvme": None,
            "type": config.get("type", "sas_sata"),  # Ensure type remains explicitly mapped
            "present": False, 
            "device": None, 
            "serial": None, 
            "model": None, 
            "status": "EMPTY",
            "interface_type": "unknown", 
            "capacity_str": "-", 
            "marker": {"ok": False, "status": "none", "error": None, "details": {}}, 
            "recommendation": {"status": "UNKNOWN", "comment": "-"}, 
            "health_score": 100,
            "capabilities": {"supports_crypto_erase": False, "supports_block_erase": False, "supports_secure_erase": False, "supports_enhanced_secure_erase": False, "supports_overwrite": True}, 
            "supported_methods": ["overwrite"],
            "smart": {}, 
            "diagnostics": {"mapping": {"ok": False, "reason": "not_mapped"}, "commands": {}}
        }
        
        # 1. Primary SATA/SAS path check
        matched_by_path, dev_node = resolve_bay_device(target_path, path_to_dev)
        matched_by_path_nvme = None
        
        # 2. Tri-Mode Fallback: If no SATA/SAS is found, resolve the NVMe motherboard port
        if not dev_node and target_path_nvme:
            matched_by_path_nvme, dev_node = resolve_bay_device(target_path_nvme, path_to_dev)
            if dev_node:
                bay_info["resolved_by_path_nvme"] = matched_by_path_nvme
        else:
            if dev_node:
                bay_info["resolved_by_path"] = matched_by_path

        if dev_node:
            bay_info["diagnostics"]["mapping"] = {"ok": True, "reason": None}

            is_os_drive = False
            if os_dev_node and os.path.realpath(dev_node) == os.path.realpath(os_dev_node):
                is_os_drive = True
            
            resolved_active_path = matched_by_path_nvme if matched_by_path_nvme else matched_by_path
            configured_active_path = target_path_nvme if matched_by_path_nvme else target_path
            
            if os_by_path and (resolved_active_path == os_by_path or configured_active_path == os_by_path or os.path.basename(resolved_active_path or "") == os.path.basename(os_by_path)):
                is_os_drive = True

            if is_os_drive:
                bay_info["role"] = "os"
                bay_info["locked"] = True

            if running_devices and dev_node in running_devices:
                bay_info.update({"present": True, "device": dev_node, "status": "RUNNING", "interface_type": detect_interface_type(resolved_active_path or configured_active_path, dev_node, config.get('type'), None), "capacity_str": "Sanitizing..."})
                results.append(bay_info); continue

            command_diagnostics = {}
            smart = get_smart_data(dev_node, command_diagnostics)
            interface_type = detect_interface_type(resolved_active_path or configured_active_path, dev_node, config.get('type'), smart.get("raw"))
            capabilities = detect_drive_capabilities(interface_type, dev_node, command_diagnostics)
            marker_status = read_marker_status(dev_node, interface_type, passphrase)

            if marker_status.get("status") == "checksum_valid":
                is_pristine = check_write_tolerance(interface_type, smart.get("data_written_raw"), marker_status.get("details", {}).get("data_written_at_wipe"))
                marker_status["is_pristine"] = is_pristine
                marker_status["status"] = "written_since_wipe" if not is_pristine else ("pristine_secure" if marker_status.get("hmac_verified") else "pristine_insecure")

            health_score = calculate_drive_health_score(interface_type, smart, smart.get("raw"))
            recommendation = get_drive_recommendation(interface_type, smart, health_score=health_score)

            bay_info.update({
                "present": True, "device": dev_node, "serial": smart.get("serial"), "model": smart.get("model"), "status": smart.get("status", "UNKNOWN"), "interface_type": interface_type, "capacity_str": smart.get("capacity_str", "-"),
                "capabilities": capabilities, "marker": marker_status, "recommendation": recommendation, "health_score": health_score,
                "supported_methods": [m for m, s in {"crypto": capabilities.get("supports_crypto_erase", False), "block": capabilities.get("supports_block_erase", False), "secure_erase": capabilities.get("supports_secure_erase", False), "enhanced_secure_erase": capabilities.get("supports_enhanced_secure_erase", False), "overwrite": capabilities.get("supports_overwrite", False)}.items() if s],
                "diagnostics": {"mapping": {"ok": True, "reason": None}, "commands": command_diagnostics},
                "smart": {
                    "temperature": smart.get("temperature"), "reallocated_sectors": smart.get("reallocated_sectors"), "pending_sectors": smart.get("pending_sectors"), "wear_level": smart.get("wear_level"), "power_on_hours": smart.get("power_on_hours"), "power_on_days": smart.get("power_on_days"),
                    "interface_errors": smart.get("interface_errors"), "data_read_raw": smart.get("data_read_raw"), "data_read_bytes": smart.get("data_read_bytes"), "data_written_raw": smart.get("data_written_raw"), "data_written_bytes": smart.get("data_written_bytes"),
                    "reallocated_normalized": smart.get("reallocated_normalized"), "reallocated_threshold": smart.get("reallocated_threshold"), "capacity_bytes": smart.get("capacity_bytes"), "raw": smart.get("raw")
                }
            })

            if is_os_drive:
                bay_info["role"] = "os"
                bay_info["locked"] = True
                bay_info["supported_methods"] = []
                bay_info["recommendation"] = {"status": "LOCKED", "comment": "Active Operating System Disk. Sanitization strictly blocked."}
                if not bay_info["capacity_str"].endswith(" [OS]"):
                    bay_info["capacity_str"] = f"{bay_info['capacity_str']} [OS]"

        else:
            bay_info["diagnostics"]["mapping"] = {"ok": False, "reason": "by_path_not_found" if (target_path or target_path_nvme) else "missing_by_path"}
        results.append(bay_info)
    return results