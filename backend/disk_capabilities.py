# --- START OF FILE backend/disk_capabilities.py ---
# Drive capability detection

import re

from disk_utils import HDPARM_CMD, NVME_CMD, SG_SANITIZE_CMD, run_command

def detect_sata_capabilities(device, diagnostics=None):
    capabilities = {"supports_secure_erase": False, "supports_enhanced_secure_erase": False, "supports_crypto_erase": False, "supports_block_erase": False}
    if not HDPARM_CMD:
        if diagnostics is not None: diagnostics["hdparm"] = {"ok": False, "reason": "command_not_resolved"}
        return capabilities
    output = run_command([HDPARM_CMD, "-I", device], diagnostics, "hdparm")
    if not output: return capabilities
    
    if re.search(r"Security:", output, re.IGNORECASE):
        if re.search(r"\bsupported\b", output, re.IGNORECASE): capabilities["supports_secure_erase"] = True
        if re.search(r"\benhanced erase\b", output, re.IGNORECASE): capabilities["supports_enhanced_secure_erase"] = True
            
    output_lowered = output.lower()
    if "sanitize feature set" in output_lowered:
        if "crypto_scramble_ext" in output_lowered or "cryptographic scramble" in output_lowered: capabilities["supports_crypto_erase"] = True
        if "block_erase_ext" in output_lowered or "block erase" in output_lowered: capabilities["supports_block_erase"] = True
    return capabilities

def detect_nvme_capabilities(device, diagnostics=None):
    capabilities = {"supports_crypto_erase": False, "supports_block_erase": False}
    if not NVME_CMD: return capabilities
    output = run_command([NVME_CMD, "id-ctrl", device], diagnostics, "nvme")
    if not output: return capabilities
    sanicap_match = re.search(r"sanicap\s*:\s*0x([0-9a-fA-F]+)", output)
    if not sanicap_match: return capabilities
    sanicap_value = int(sanicap_match.group(1), 16)
    capabilities["supports_crypto_erase"] = bool(sanicap_value & (1 << 0))
    capabilities["supports_block_erase"] = bool(sanicap_value & (1 << 1))
    return capabilities

def detect_sas_capabilities(device, diagnostics=None):
    capabilities = {"supports_crypto_erase": False, "supports_block_erase": False}
    if not SG_SANITIZE_CMD: return capabilities
    output = run_command([SG_SANITIZE_CMD, "--status", device], diagnostics, "sg_sanitize")
    if not output: return capabilities
    if any(marker in output.lower() for marker in ["sanitize", "in progress", "completed", "idle", "status"]):
        capabilities["supports_block_erase"] = True
    return capabilities

def detect_drive_capabilities(interface_type, device, diagnostics=None):
    capabilities = {"supports_crypto_erase": False, "supports_block_erase": False, "supports_secure_erase": False, "supports_enhanced_secure_erase": False, "supports_overwrite": True}
    if not device: return capabilities
    if interface_type == "nvme": capabilities.update(detect_nvme_capabilities(device, diagnostics))
    elif interface_type == "sata": capabilities.update(detect_sata_capabilities(device, diagnostics))
    elif interface_type == "sas": capabilities.update(detect_sas_capabilities(device, diagnostics))
    return capabilities
# --- END OF FILE backend/disk_capabilities.py ---
