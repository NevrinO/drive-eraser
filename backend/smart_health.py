# SMART health scoring and drive recommendations
# Depends on: smart_utils, smart_data_parsing

import math
import logging

from disk_utils import safe_int
from smart_utils import is_drive_ssd
from smart_data_parsing import get_triage_thresholds

logger = logging.getLogger(__name__)


def calculate_drive_health_score(interface_type, smart_data, thresholds=None):
    iface = str(interface_type or "unknown").lower()
    is_ssd = is_drive_ssd(interface_type, smart_data)
    wear = smart_data.get("wear_level")
    poh = safe_int(smart_data.get("power_on_hours"), 0)
    if thresholds is None:
        thresholds = get_triage_thresholds()
    
    # Initialize penalty breakdown
    penalty_breakdown = {
        "base_score": 100,
        "poh_penalty": 0,
        "fdw_penalty": 0,
        "realloc_penalty": 0,
        "pending_penalty": 0,
        "nme_penalty": 0,
        "nvme_media_penalty": 0,
        "failed_override": False,
        "final_score": 100
    }
    
    if is_ssd and wear is not None:
        wear_val = safe_int(wear, 0)
        base_score = max(0, 100 - wear_val)
        ssd_high_poh_thresh = thresholds["ssd_high_poh_threshold"]
        if poh > ssd_high_poh_thresh:
            poh_penalty = min(20, 20 * ((poh - ssd_high_poh_thresh) / ssd_high_poh_thresh) ** 2)
            base_score = max(10, base_score - poh_penalty)
            penalty_breakdown["poh_penalty"] = poh_penalty
    else:
        poh_penalty = min(30, max(0, (poh - 20000) / 40000 * 30)) if poh > 20000 else 0
        written_bytes = smart_data.get("data_written_bytes")
        if written_bytes is None:
            raw_written = smart_data.get("data_written_raw")
            written_bytes = safe_int(raw_written, 0) * 512 if raw_written is not None else 0
        else:
            written_bytes = safe_int(written_bytes, 0)
        capacity = safe_int(smart_data.get("capacity_bytes"), 0)
        fdw = (written_bytes / capacity) if capacity > 0 else 0.0
        fdw_penalty = min(30, max(0, (fdw / 150.0) * 30))
        base_score = max(40, 100 - poh_penalty - fdw_penalty)
        penalty_breakdown["poh_penalty"] = poh_penalty
        penalty_breakdown["fdw_penalty"] = fdw_penalty

    penalty_breakdown["base_score"] = base_score

    reallocated = safe_int(smart_data.get("reallocated_sectors"), 0)
    pending = safe_int(smart_data.get("pending_sectors"), 0)

    realloc_penalty = 0
    if is_ssd:
        # For SSDs, use raw reallocated sector count with penalty mitigated by spare reserve
        realloc_raw = safe_int(smart_data.get("reallocated_sectors"), 0)
        if realloc_raw > 0:
            realloc_normalized = smart_data.get("reallocated_normalized")
            if realloc_normalized is not None:
                norm_val = safe_int(realloc_normalized, 100)
                # If spare reserve is high (>80), mitigate the penalty significantly
                if norm_val >= 80:
                    realloc_penalty = min(40, realloc_raw * 2)  # Reduced penalty with good spare
                else:
                    realloc_penalty = min(40, realloc_raw * 5)  # Full penalty with low spare
            else:
                realloc_penalty = min(40, realloc_raw * 5)  # Full penalty if no spare info
    elif iface == "sas":
        # SAS logarithmic grown-defect penalty: ~40 at 100, ~70 at 1000, ~100 at 10000+
        sas_grown_defects = safe_int(smart_data.get("sas_grown_defect_list"), 0)
        if sas_grown_defects > 0:
            # Logarithmic scaling: penalty = 20 * log10(defects), capped at 100
            # This applies to any defects > 0, not just above threshold
            realloc_penalty = min(100, 20 * math.log10(max(1, sas_grown_defects)))
    else:
        if reallocated > 0:
            realloc_penalty = min(40, 10 if reallocated == 1 else (10 + (reallocated - 1) * 5 if reallocated <= 5 else 30 + (reallocated - 5) * 10))

    penalty_breakdown["realloc_penalty"] = realloc_penalty

    pending_penalty = min(60, pending * 15)
    penalty_breakdown["pending_penalty"] = pending_penalty
    
    # SAS NME (Non-Medium Errors) penalty
    nme_penalty = 0
    if iface == "sas":
        sas_nme = safe_int(smart_data.get("sas_non_medium_errors"), 0)
        if sas_nme > 0:
            nme_advisory_thresh = thresholds.get("sas_nme_advisory_threshold", 1000000)
            nme_penalty_thresh = thresholds.get("sas_nme_penalty_threshold", 100000000)
            if sas_nme >= nme_penalty_thresh:
                # Penalty only above 100M: scale from 0 to 30 based on excess
                nme_penalty = min(30, 30 * ((sas_nme - nme_penalty_thresh) / nme_penalty_thresh))
            # Below 1M: no penalty
            # 1M-100M: advisory only (no score penalty, but could flag in UI)
    
    penalty_breakdown["nme_penalty"] = nme_penalty
    
    nvme_media_penalty = 0
    if iface == "nvme":
        nvme_media_penalty = min(80, safe_int(smart_data.get("_nvme_media_errors"), 0) * 20)

    penalty_breakdown["nvme_media_penalty"] = nvme_media_penalty

    score = max(0, base_score - realloc_penalty - pending_penalty - nme_penalty - nvme_media_penalty)
    failed_override = str(smart_data.get("status") or "UNKNOWN").upper() == "FAILED"
    exit_status_val = safe_int(smart_data.get("_smartctl_exit_status"), 0)
    if (exit_status_val & 8 != 0) or (exit_status_val & 16 != 0): failed_override = True
    if iface == "nvme":
        crit_warn_val = safe_int(smart_data.get("_nvme_critical_warning"), 0)
        if (crit_warn_val & 0x04 != 0) or (crit_warn_val & 0x08 != 0): failed_override = True

    penalty_breakdown["failed_override"] = failed_override
    final_score = min(int(round(score)), 5) if failed_override else int(round(score))
    penalty_breakdown["final_score"] = final_score

    return final_score, penalty_breakdown


def get_drive_recommendation(interface_type, smart, health_score=None, thresholds=None):
    if thresholds is None:
        thresholds = get_triage_thresholds()
    
    iface = str(interface_type or "unknown").lower()
    is_ssd = is_drive_ssd(interface_type, smart)
    poh = safe_int(smart.get("power_on_hours"), 0)
    status = str(smart.get("status") or "UNKNOWN").upper()
    pending = safe_int(smart.get("pending_sectors"), 0)
    realloc_raw = safe_int(smart.get("reallocated_sectors"), 0)
    realloc_norm = safe_int(smart.get("reallocated_normalized"), 100)
    realloc_thresh = safe_int(smart.get("reallocated_threshold"), 10)

    written_bytes = smart.get("data_written_bytes")
    if written_bytes is None:
        raw_written = smart.get("data_written_raw")
        written_bytes = safe_int(raw_written, 0) * (512000 if "nvme" in iface else 512) if raw_written is not None else 0
    else:
        written_bytes = safe_int(written_bytes, 0)

    capacity = safe_int(smart.get("capacity_bytes"), 0)
    fdw = (written_bytes / capacity) if capacity > 0 else 0.0

    remaining_life = 100
    wear = smart.get("wear_level")
    if wear is not None:
        wear_val = safe_int(wear, 0)
        remaining_life = max(0, 100 - wear_val)

    health_destroy_thresh = thresholds["health_score_destroy_threshold"]
    health_scratch_thresh = thresholds["health_score_scratch_threshold"]
    pending_destroy_thresh = thresholds["pending_sectors_destroy_threshold"]
    pending_scratch_thresh = thresholds["pending_sectors_scratch_threshold"]
    
    # SAS uncorrectable error recommendations (critical indicators)
    if iface == "sas":
        sas_verify_errors = safe_int(smart.get("sas_uncorrectable_verify_errors"), 0)
        sas_write_errors = safe_int(smart.get("sas_uncorrectable_write_errors"), 0)
        sas_read_errors = safe_int(smart.get("sas_uncorrectable_read_errors"), 0)
        sas_sticky_lba = smart.get("sas_sticky_lba_detected")
        sas_grown_defects = safe_int(smart.get("sas_grown_defect_list"), 0)
        sas_grown_defect_fail_thresh = thresholds.get("sas_grown_defect_fail_threshold", 10000)
        
        if sas_verify_errors >= 1 or sas_write_errors >= 1:
            return {"status": "DESTROY", "comment": "SAS drive has uncorrectable verify or write errors. Critical data integrity risk."}
        if sas_read_errors >= 10:
            return {"status": "DESTROY", "comment": "SAS drive has excessive uncorrectable read errors. Critical data integrity risk."}
        if sas_grown_defects >= sas_grown_defect_fail_thresh:
            return {"status": "DESTROY", "comment": f"SAS drive has {sas_grown_defects:,} grown defects (exceeds fail threshold). Critical mechanical degradation."}
        if sas_read_errors >= 1:
            return {"status": "SCRATCH", "comment": "SAS drive has uncorrectable read errors. Use only for non-critical data."}
        if sas_sticky_lba:
            return {"status": "SCRATCH", "comment": "SAS drive has sticky LBA detected (recurring errors at same location). Use only for non-critical data."}
        if sas_grown_defects > 0:
            return {"status": "SCRATCH", "comment": f"SAS drive has {sas_grown_defects:,} grown defects. Mechanical degradation detected. Use only for non-critical data."}

    if health_score is not None:
        if status == "FAILED" or health_score <= health_destroy_thresh: return {"status": "DESTROY", "comment": "Drive shows critical physical degradation or SMART health failure."}
        if health_score <= health_scratch_thresh: return {"status": "SCRATCH", "comment": "Unstable or significantly aged drive. Safe only for non-critical use."}
    else:
        if status == "FAILED" or realloc_norm < 50 or pending > pending_destroy_thresh: return {"status": "DESTROY", "comment": "Drive shows critical physical degradation or SMART health failure."}
        if realloc_norm <= realloc_thresh or (0 < pending <= pending_scratch_thresh): return {"status": "SCRATCH", "comment": "Unstable or threshold-breached sectors detected. Safe only for non-critical use."}

    if is_ssd:
        ssd_life_destroy_thresh = thresholds["ssd_remaining_life_destroy_threshold"]
        ssd_life_scratch_thresh = thresholds["ssd_remaining_life_scratch_threshold"]
        ssd_life_good_thresh = thresholds["ssd_remaining_life_good_threshold"]
        ssd_new_poh_thresh = thresholds["ssd_new_poh_threshold"]
        ssd_high_poh_thresh = thresholds["ssd_high_poh_threshold"]
        ssd_new_fdw_thresh = thresholds["ssd_new_fdw_threshold"]
        realloc_new_thresh = thresholds["realloc_raw_new_threshold"]
        
        if remaining_life < ssd_life_destroy_thresh: return {"status": "DESTROY", "comment": "SSD wear is fully depleted (remaining life below threshold)."}
        if remaining_life < ssd_life_scratch_thresh: return {"status": "SCRATCH", "comment": "SSD remaining life is heavily worn (under 60%). Relegate to scratch."}
        if poh < ssd_new_poh_thresh and fdw < ssd_new_fdw_thresh and remaining_life == 100 and realloc_raw == realloc_new_thresh: return {"status": "NEW_STOCK", "comment": "This drive is practically new (low runtime, pristine life and sectors)."}
        if remaining_life >= ssd_life_good_thresh:
            if poh >= ssd_high_poh_thresh:
                return {"status": "USED_HEAVY", "comment": f"Excellent health, but high runtime (exceeds {ssd_high_poh_thresh:,} hours)."}
            return {"status": "USED_GOOD", "comment": "This drive is used but still has excellent remaining life."}
        return {"status": "USED_HEAVY", "comment": "This drive is heavily used but still has life."}
    else:
        hdd_high_poh_thresh = thresholds["hdd_high_poh_threshold"]
        hdd_new_poh_thresh = thresholds["hdd_new_poh_threshold"]
        hdd_new_fdw_thresh = thresholds["hdd_new_fdw_threshold"]
        hdd_heavy_fdw_thresh = thresholds["hdd_heavy_fdw_threshold"]
        realloc_new_thresh = thresholds["realloc_raw_new_threshold"]
        
        if poh >= hdd_high_poh_thresh: return {"status": "USED_HEAVY", "comment": f"High Power-On Hours (exceeds {hdd_high_poh_thresh:,} server hours)."}
        if poh < hdd_new_poh_thresh and fdw < hdd_new_fdw_thresh and realloc_raw == realloc_new_thresh: return {"status": "NEW_STOCK", "comment": "Practically new (extremely low runtime and zero sector reallocations)."}
        if fdw >= hdd_heavy_fdw_thresh:
            return {"status": "USED_HEAVY", "comment": "High workload or raw sector writes history. Monitor closely."}
        return {"status": "USED_GOOD", "comment": "Used but has clean write history and moderate runtime."}
