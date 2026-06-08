# Automated tests for manual test plan scenarios (Low #74)
import pytest
import sys
import os
import json
from unittest.mock import patch, MagicMock

# Add backend to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))


class TestSSDWearBaseline:
    """Test Case 1: SSD Wear Baseline and POH Controller Fatigue."""

    def test_ssd_health_reflects_wear_percentage(self):
        """Test that SSD health score reflects remaining flash wear."""
        from smart_parsing import calculate_drive_health_score

        # Mock SSD with 11% wear (89% life remaining)
        smart_data = {
            "wear_level": 11,  # 11% worn, 89% remaining
            "power_on_hours": 1000,
            "status": "PASSED"
        }

        health = calculate_drive_health_score("nvme", smart_data, None)

        # For NVMe, health = 100 - wear_level = 89
        assert health == 89, f"Expected health 89, got {health}"

    def test_ssd_health_with_extreme_poh_penalty(self):
        """Test that extreme POH applies progressive penalty (max 20% at 80,000 hours)."""
        from smart_parsing import calculate_drive_health_score

        # Mock SSD with 89% life remaining but 80,000 POH
        smart_data = {
            "wear_level": 11,
            "power_on_hours": 80000,
            "status": "PASSED"
        }

        health = calculate_drive_health_score("nvme", smart_data, None)

        # Base health 89, penalty for POH > 40000: max 20% = 69 minimum
        assert health >= 69, f"Expected health >= 69, got {health}"
        assert health <= 89, f"Expected health <= 89, got {health}"

    def test_ssd_health_hard_floor(self):
        """Test that SSD health has a hard floor of 10%."""
        from smart_parsing import calculate_drive_health_score

        # Mock SSD with extreme wear and POH
        smart_data = {
            "wear_level": 95,
            "power_on_hours": 100000,
            "status": "PASSED"
        }

        health = calculate_drive_health_score("nvme", smart_data, None)

        assert health >= 10, f"Expected health >= 10 (hard floor), got {health}"


class TestHDDMechanicalAging:
    """Test Case 2: HDD Mechanical Aging (POH & Workload FDW)."""

    def test_hdd_health_low_poh_low_fdw(self):
        """Test HDD with 30,000 POH and low FDW (< 5) has ~91% health."""
        from smart_parsing import calculate_drive_health_score

        smart_data = {
            "power_on_hours": 30000,
            "data_written_raw": 5,  # Low FDW
            "capacity_bytes": 1000000000000,  # 1TB
            "status": "PASSED"
        }

        health = calculate_drive_health_score("sata", smart_data, None)

        # POH penalty: (30000-20000)/40000*30 = 7.5, FDW penalty: (5/150)*30 = 1
        # Base: 100 - 7.5 - 1 = 91.5, min 40
        assert health >= 90, f"Expected health >= 90, got {health}"

    def test_hdd_health_low_poh_high_fdw(self):
        """Test HDD with 30,000 POH and high FDW (>= 150) has ~62% health."""
        from smart_parsing import calculate_drive_health_score

        smart_data = {
            "power_on_hours": 30000,
            "data_written_raw": 150,  # High FDW
            "capacity_bytes": 1000000000000,  # 1TB
            "status": "PASSED"
        }

        health = calculate_drive_health_score("sata", smart_data, None)

        # POH penalty: 7.5, FDW penalty: (150/150)*30 = 30
        # Base: 100 - 7.5 - 30 = 62.5
        assert health >= 60, f"Expected health >= 60, got {health}"


class TestReallocatedSectors:
    """Test Case 3: Reallocated Sectors (HDD Strict vs. SSD Spare Reserve)."""

    def test_hdd_strict_bad_sector_penalty(self):
        """Test HDD with 1 bad sector drops health by 10%."""
        from smart_parsing import calculate_drive_health_score

        smart_data = {
            "reallocated_sectors": 1,
            "status": "PASSED"
        }

        health = calculate_drive_health_score("sata", smart_data, None)

        # HDD penalty: 10 for 1 sector
        assert health <= 90, f"Expected health <= 90 (10% penalty), got {health}"

    def test_hdd_multiple_bad_sectors_penalty(self):
        """Test HDD with 6 bad sectors drops health by 40%."""
        from smart_parsing import calculate_drive_health_score

        smart_data = {
            "reallocated_sectors": 6,
            "status": "PASSED"
        }

        health = calculate_drive_health_score("sata", smart_data, None)

        # HDD penalty: 10 + (6-1)*5 = 35 for 6 sectors
        assert health <= 65, f"Expected health <= 65 (35% penalty), got {health}"

    def test_ssd_reallocated_with_spare_reserve(self):
        """Test SSD with reallocated sectors but 100% spare has 0% penalty."""
        from smart_parsing import calculate_drive_health_score

        smart_data = {
            "reallocated_sectors": 4,
            "reallocated_normalized": 100,  # Full spare reserve
            "status": "PASSED"
        }

        health = calculate_drive_health_score("nvme", smart_data, None)

        # SSD with 100% spare has no penalty
        assert health >= 90, f"Expected health >= 90 (no penalty), got {health}"


class TestByteAccurateTrafficScaling:
    """Test Case 4: Byte-Accurate Traffic Scaling."""


    def test_frontend_tib_display(self):
        """Test that frontend displays TiB or PiB for large volumes."""
        from disk_utils import format_capacity_bytes

        # Test TiB display
        result = format_capacity_bytes(769 * 10**12)

        assert "TiB" in result or "TB" in result, f"Expected TiB/TB in display, got {result}"


class TestSASGListIntegrity:
    """Test Case 5: SAS G-List Integrity Check."""

    def test_sas_soft_ecc_ignored(self):
        """Test SAS with soft ECC errors but 0 G-list has no penalty."""
        from smart_parsing import calculate_drive_health_score

        smart_data = {
            "interface_errors": 100,  # Soft ECC errors
            "reallocated_sectors": 0,  # No G-list entries
            "status": "PASSED"
        }

        health = calculate_drive_health_score("sas", smart_data, None)

        # Interface errors > 50 only penalize by 10, not from reallocated sectors
        assert health >= 90, f"Expected health >= 90, got {health}"

    def test_sas_health_unimpaired_by_soft_ecc(self):
        """Test SAS health remains high with soft ECC but no G-list."""
        from smart_parsing import calculate_drive_health_score

        smart_data = {
            "interface_errors": 100,
            "reallocated_sectors": 0,
            "status": "PASSED"
        }

        health = calculate_drive_health_score("sas", smart_data, None)

        # No reallocated sectors, only interface error penalty
        assert health >= 90, f"Expected health >= 90 (no penalty), got {health}"


class TestProtocolMethodMatrix:
    """Test protocol-specific method detection (from test-plan.md)."""

    @patch('subprocess.run')
    @patch('shutil.which')
    def test_nvme_methods(self, mock_which, mock_run):
        """Test NVMe drives support crypto, block, overwrite."""
        from disk_capabilities import detect_drive_capabilities

        mock_which.return_value = '/usr/bin/nvme'
        # Mock nvme id-ctrl output with sanicap field (bits 0 and 1 set for crypto and block)
        mock_run.return_value = MagicMock(returncode=0, stdout='sanicap : 0x03\n')

        result = detect_drive_capabilities("nvme", "/dev/nvme0n1", {})

        # NVMe should support crypto, block, and overwrite
        assert result["supports_crypto_erase"] is True
        assert result["supports_block_erase"] is True
        assert result["supports_overwrite"] is True

    @patch('subprocess.run')
    @patch('shutil.which')
    def test_sata_methods(self, mock_which, mock_run):
        """Test SATA drives support crypto, block, overwrite (secure_erase intentionally disabled)."""
        from disk_capabilities import detect_drive_capabilities

        mock_which.return_value = '/usr/bin/hdparm'
        # Mock hdparm output with sanitize feature set
        mock_run.return_value = MagicMock(returncode=0, stdout='Security:\n\tsupported\nSanitize feature set\n\tcrypto_scramble_ext\n\tblock_erase_ext\n')

        result = detect_drive_capabilities("sata", "/dev/sda", {})

        # SATA should support crypto, block, and overwrite (secure_erase intentionally disabled per code comments)
        assert result["supports_crypto_erase"] is True
        assert result["supports_block_erase"] is True
        assert result["supports_overwrite"] is True
        # secure_erase and enhanced_secure_erase are intentionally disabled due to drive lockout issues

    @patch('subprocess.run')
    @patch('shutil.which')
    def test_sas_methods(self, mock_which, mock_run):
        """Test SAS drives support block, overwrite (conservative)."""
        from disk_capabilities import detect_drive_capabilities

        mock_which.return_value = '/usr/bin/sg_sanitize'
        # Mock sg_sanitize output showing sanitize support
        mock_run.return_value = MagicMock(returncode=0, stdout='Sanitize status: idle\n')

        result = detect_drive_capabilities("sas", "/dev/sdb", {})

        # SAS should support block and overwrite
        assert result["supports_block_erase"] is True
        assert result["supports_overwrite"] is True


class TestInterfaceClassification:
    """Test interface type classification from SMART data."""

    def test_classify_nvme_from_json(self):
        """Test NVMe classification from JSON protocol field."""
        from smart_parsing import classify_interface_from_smart

        smart_output = json.dumps({"device": {"protocol": "NVMe"}})
        result = classify_interface_from_smart(smart_output)
        assert result == "nvme"

    def test_classify_sata_from_json(self):
        """Test SATA classification from JSON protocol field."""
        from smart_parsing import classify_interface_from_smart

        smart_output = json.dumps({"device": {"protocol": "ATA"}})
        result = classify_interface_from_smart(smart_output)
        assert result == "sata"

    def test_classify_sas_from_json(self):
        """Test SAS classification from JSON protocol field."""
        from smart_parsing import classify_interface_from_smart

        smart_output = json.dumps({"device": {"protocol": "SCSI"}})
        result = classify_interface_from_smart(smart_output)
        assert result == "sas"

    def test_classify_nvme_from_text(self):
        """Test NVMe classification from text patterns."""
        from smart_parsing import classify_interface_from_smart

        smart_output = "NVMe Version 1.4"
        result = classify_interface_from_smart(smart_output)
        assert result == "nvme"

    def test_classify_sata_from_text(self):
        """Test SATA classification from text patterns."""
        from smart_parsing import classify_interface_from_smart

        smart_output = "SATA Version 3.0"
        result = classify_interface_from_smart(smart_output)
        assert result == "sata"

    def test_classify_sas_from_text(self):
        """Test SAS classification from text patterns."""
        from smart_parsing import classify_interface_from_smart

        smart_output = "Transport protocol: SAS"
        result = classify_interface_from_smart(smart_output)
        assert result == "sas"

    def test_classify_unknown(self):
        """Test unknown interface returns None."""
        from smart_parsing import classify_interface_from_smart

        smart_output = "Unknown drive type"
        result = classify_interface_from_smart(smart_output)
        assert result is None

    def test_classify_empty_input(self):
        """Test empty input returns None."""
        from smart_parsing import classify_interface_from_smart

        result = classify_interface_from_smart("")
        assert result is None


class TestDriveRecommendation:
    """Test drive recommendation logic."""

    def test_recommendation_new_stock_ssd(self):
        """Test NEW_STOCK recommendation for pristine SSD."""
        from smart_parsing import get_drive_recommendation

        smart = {
            "power_on_hours": 100,
            "data_written_bytes": 1000000,
            "capacity_bytes": 1000000000000,
            "reallocated_sectors": 0,
            "reallocated_normalized": 100,
            "wear_level": 0,
            "status": "PASSED"
        }
        result = get_drive_recommendation("nvme", smart, health_score=95)
        assert result["status"] == "NEW_STOCK"

    def test_recommendation_destroy_failed_smart(self):
        """Test DESTROY recommendation for failed SMART."""
        from smart_parsing import get_drive_recommendation

        smart = {
            "status": "FAILED",
            "power_on_hours": 1000,
            "reallocated_sectors": 0
        }
        result = get_drive_recommendation("sata", smart, health_score=50)
        assert result["status"] == "DESTROY"

    def test_recommendation_destroy_low_health(self):
        """Test DESTROY recommendation for low health score."""
        from smart_parsing import get_drive_recommendation

        smart = {
            "status": "PASSED",
            "power_on_hours": 1000,
            "reallocated_sectors": 0
        }
        result = get_drive_recommendation("sata", smart, health_score=15)
        assert result["status"] == "DESTROY"

    def test_recommendation_scratch_ssd_low_life(self):
        """Test SCRATCH recommendation for SSD with low remaining life."""
        from smart_parsing import get_drive_recommendation

        smart = {
            "wear_level": 50,  # 50% worn
            "status": "PASSED",
            "power_on_hours": 1000
        }
        result = get_drive_recommendation("nvme", smart, health_score=50)
        assert result["status"] == "SCRATCH"

    def test_recommendation_scratch_hdd_high_poh(self):
        """Test SCRATCH recommendation for HDD with high POH."""
        from smart_parsing import get_drive_recommendation

        smart = {
            "power_on_hours": 50000,
            "data_written_bytes": 1000000000000,
            "capacity_bytes": 1000000000000,
            "reallocated_sectors": 0,
            "status": "PASSED"
        }
        result = get_drive_recommendation("sata", smart, health_score=70)
        assert result["status"] == "SCRATCH"

    def test_recommendation_used_good_ssd(self):
        """Test USED_GOOD recommendation for healthy SSD."""
        from smart_parsing import get_drive_recommendation

        smart = {
            "wear_level": 10,
            "power_on_hours": 10000,
            "status": "PASSED"
        }
        result = get_drive_recommendation("nvme", smart, health_score=90)
        assert result["status"] == "USED_GOOD"

    def test_recommendation_used_heavy_ssd(self):
        """Test USED_HEAVY recommendation for high-POH SSD."""
        from smart_parsing import get_drive_recommendation

        smart = {
            "wear_level": 20,
            "power_on_hours": 50000,
            "status": "PASSED"
        }
        result = get_drive_recommendation("nvme", smart, health_score=80)
        assert result["status"] == "USED_HEAVY"



class TestIsDriveSSD:
    """Test SSD detection logic."""

    def test_nvme_is_ssd(self):
        """Test NVMe drives are classified as SSD."""
        from smart_parsing import is_drive_ssd

        result = is_drive_ssd("nvme", {})
        assert result is True

    def test_rotation_rate_zero_is_ssd(self):
        """Test drives with rotation_rate=0 are SSD."""
        from smart_parsing import is_drive_ssd

        smart_data = {"rotation_rate": 0}
        result = is_drive_ssd("sata", smart_data)
        assert result is True

    def test_rotation_rate_positive_is_hdd(self):
        """Test drives with positive rotation_rate are HDD."""
        from smart_parsing import is_drive_ssd

        smart_data = {"rotation_rate": 7200}
        result = is_drive_ssd("sata", smart_data)
        assert result is False

    def test_model_name_ssd(self):
        """Test model name containing 'SSD' is classified as SSD."""
        from smart_parsing import is_drive_ssd

        smart_data = {"model": "Samsung SSD 870"}
        result = is_drive_ssd("sata", smart_data)
        assert result is True

    def test_model_name_hdd_keywords(self):
        """Test model name with HDD keywords is classified as HDD."""
        from smart_parsing import is_drive_ssd

        for model in ["Seagate Barracuda", "WD IronWolf", "Toshiba HDD"]:
            smart_data = {"model": model}
            result = is_drive_ssd("sata", smart_data)
            assert result is False

    def test_wear_level_indicates_ssd(self):
        """Test presence of wear_level indicates SSD."""
        from smart_parsing import is_drive_ssd

        smart_data = {"wear_level": 10}
        result = is_drive_ssd("sata", smart_data)
        assert result is True


class TestTriageThresholds:
    """Test triage threshold loading."""

    def test_default_thresholds(self):
        """Test that default thresholds are returned when policy loading fails."""
        from smart_parsing import get_triage_thresholds

        with patch('smart_parsing.load_policy', side_effect=Exception("Test error")):
            thresholds = get_triage_thresholds()
            assert "ssd_new_poh_threshold" in thresholds
            assert thresholds["ssd_new_poh_threshold"] == 500

    def test_custom_thresholds_from_policy(self):
        """Test that custom thresholds from policy are used."""
        from smart_parsing import get_triage_thresholds

        with patch('smart_parsing.load_policy', return_value={"triage_thresholds": {"ssd_new_poh_threshold": 1000}}):
            thresholds = get_triage_thresholds()
            assert thresholds["ssd_new_poh_threshold"] == 1000


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
