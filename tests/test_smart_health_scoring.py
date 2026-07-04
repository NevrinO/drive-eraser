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

        health, _ = calculate_drive_health_score("nvme", smart_data)

        # For NVMe, health = 100 - wear_level = 89
        assert health == 89, f"Expected health 89, got {health}"

    def test_ssd_health_with_extreme_poh_penalty(self):
        """Test that extreme POH applies quadratic penalty (max 25% at 2x threshold)."""
        from smart_parsing import calculate_drive_health_score, get_triage_thresholds

        thresholds = get_triage_thresholds()
        ssd_high_poh_thresh = thresholds["ssd_high_poh_threshold"]
        max_poh = ssd_high_poh_thresh * 2  # 87600 with default threshold

        # Mock SSD with 89% life remaining but at max POH
        smart_data = {
            "wear_level": 11,
            "power_on_hours": max_poh,
            "status": "PASSED"
        }

        health, _ = calculate_drive_health_score("nvme", smart_data)

        # Base health 89, penalty for POH > threshold: max 25% = 64 minimum
        assert health >= 64, f"Expected health >= 64, got {health}"
        assert health <= 89, f"Expected health <= 89, got {health}"

    def test_ssd_health_quadratic_poh_at_midpoint(self):
        """Test that quadratic POH penalty is lower at midpoint (1.5x threshold)."""
        from smart_parsing import calculate_drive_health_score, get_triage_thresholds

        thresholds = get_triage_thresholds()
        ssd_high_poh_thresh = thresholds["ssd_high_poh_threshold"]
        midpoint_poh = ssd_high_poh_thresh * 1.5  # 65700 with default threshold

        # Mock SSD with 89% life remaining at midpoint POH
        smart_data = {
            "wear_level": 11,
            "power_on_hours": midpoint_poh,
            "status": "PASSED"
        }

        health, _ = calculate_drive_health_score("nvme", smart_data)

        # Base health 89, quadratic penalty at 50% position = 6.25 points = ~83
        assert health >= 82, f"Expected health >= 82, got {health}"
        assert health <= 89, f"Expected health <= 89, got {health}"


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

        health, _ = calculate_drive_health_score("sata", smart_data)

        # POH penalty: (30000-20000)/40000*30 = 7.5, FDW penalty: (5/200)*30 = 0.75
        # Base: 100 - 7.5 - 0.75 = 91.75, min 30
        assert health >= 90, f"Expected health >= 90, got {health}"

    def test_hdd_health_low_poh_high_fdw(self):
        """Test HDD with 30,000 POH and high FDW (>= 200) has ~62% health."""
        from smart_parsing import calculate_drive_health_score

        smart_data = {
            "power_on_hours": 30000,
            "data_written_raw": 200,  # High FDW
            "capacity_bytes": 1000000000000,  # 1TB
            "status": "PASSED"
        }

        health, _ = calculate_drive_health_score("sata", smart_data)

        # POH penalty: 7.5, FDW penalty: (200/200)*30 = 30
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

        health, _ = calculate_drive_health_score("sata", smart_data)

        # HDD penalty: 10 for 1 sector
        assert health <= 90, f"Expected health <= 90 (10% penalty), got {health}"

    def test_hdd_multiple_bad_sectors_penalty(self):
        """Test HDD with 6 bad sectors drops health by 40%."""
        from smart_parsing import calculate_drive_health_score

        smart_data = {
            "reallocated_sectors": 6,
            "status": "PASSED"
        }

        health, _ = calculate_drive_health_score("sata", smart_data)

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

        health, _ = calculate_drive_health_score("nvme", smart_data)

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

    def test_sas_interface_errors_ignored(self):
        """Test SAS with interface errors but 0 G-list has no penalty (interface errors removed)."""
        from smart_parsing import calculate_drive_health_score

        smart_data = {
            "interface_errors": 100,  # Interface errors (no longer penalized)
            "reallocated_sectors": 0,  # No G-list entries
            "status": "PASSED"
        }

        health, _ = calculate_drive_health_score("sas", smart_data)

        # Interface errors no longer penalized
        assert health >= 95, f"Expected health >= 95, got {health}"

    def test_sas_health_unimpaired_by_interface_errors(self):
        """Test SAS health remains high with interface errors but no G-list."""
        from smart_parsing import calculate_drive_health_score

        smart_data = {
            "interface_errors": 100,
            "reallocated_sectors": 0,
            "status": "PASSED"
        }

        health, _ = calculate_drive_health_score("sas", smart_data)

        # No reallocated sectors, interface errors no longer penalized
        assert health >= 95, f"Expected health >= 95, got {health}"


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
        from smart_data_parsing import _DEFAULT_TRIAGE_THRESHOLDS

        smart = {
            "power_on_hours": 500,
            "data_written_bytes": 1000000,
            "capacity_bytes": 1000000000000,
            "reallocated_sectors": 0,
            "reallocated_normalized": 100,
            "wear_level": 0,
            "status": "PASSED"
        }
        with patch('smart_health.get_triage_thresholds', return_value=_DEFAULT_TRIAGE_THRESHOLDS.copy()):
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

    def test_recommendation_destroy_sas_low_health_with_grown_defects(self):
        """Test DESTROY for SAS drive with grown defects below fail threshold but critically low health score.

        Regression: SAS grown-defect SCRATCH check was short-circuiting
        the health-score DESTROY check. Drive with 1891 grown defects and health
        score of 5 should be DESTROY, not SCRATCH.
        """
        from smart_parsing import get_drive_recommendation

        smart = {
            "status": "PASSED",
            "power_on_hours": 8920,
            "sas_grown_defect_list": 1891,
            "sas_uncorrectable_read_errors": 0,
            "sas_uncorrectable_write_errors": 0,
            "sas_uncorrectable_verify_errors": 0,
            "capacity_bytes": 4000787030016,
            "data_written_bytes": 589915841000000,
            "reallocated_sectors": 1891,
        }
        result = get_drive_recommendation("sas", smart, health_score=5)
        assert result["status"] == "DESTROY", f"Expected DESTROY, got {result['status']}"

    def test_recommendation_scratch_sas_grown_defects_moderate_health(self):
        """Test SCRATCH for SAS drive with many grown defects pushing score below scratch threshold."""
        from smart_parsing import get_drive_recommendation

        smart = {
            "status": "PASSED",
            "power_on_hours": 8920,
            "sas_grown_defect_list": 150,
            "sas_uncorrectable_read_errors": 0,
            "sas_uncorrectable_write_errors": 0,
            "sas_uncorrectable_verify_errors": 0,
            "capacity_bytes": 4000787030016,
            "data_written_bytes": 1000000000000,
            "reallocated_sectors": 150,
        }
        # With 150 grown defects, score penalty = 20 * log10(150) ~= 43.5
        # A base score of 65 - 43.5 = 21.5 would be DESTROY, so use a score of 45
        # to test that the unified scratch threshold catches it
        result = get_drive_recommendation("sas", smart, health_score=45)
        assert result["status"] == "SCRATCH", f"Expected SCRATCH, got {result['status']}"

    def test_recommendation_used_good_sas_few_grown_defects_high_health(self):
        """Test USED_GOOD for SAS drive with grown defects below scratch threshold and high health score.

        Regression: SAS grown-defect SCRATCH check used `> 0` instead of the
        sas_grown_defect_scratch_threshold (default 100). A drive with 1 grown
        defect and 89% health was incorrectly marked SCRATCH instead of USED_GOOD.
        """
        from smart_parsing import get_drive_recommendation

        smart = {
            "status": "PASSED",
            "power_on_hours": 25598,
            "sas_grown_defect_list": 1,
            "sas_uncorrectable_read_errors": 0,
            "sas_uncorrectable_write_errors": 0,
            "sas_uncorrectable_verify_errors": 0,
            "capacity_bytes": 4000787030016,
            "data_written_bytes": 126501145000000,
            "reallocated_sectors": 1,
        }
        result = get_drive_recommendation("sas", smart, health_score=89)
        assert result["status"] == "USED_GOOD", f"Expected USED_GOOD, got {result['status']}"

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

    def test_recommendation_used_heavy_hdd_high_poh(self):
        """Test USED_HEAVY recommendation for HDD with high POH (changed from SCRATCH)."""
        from smart_parsing import get_drive_recommendation

        smart = {
            "power_on_hours": 50000,
            "data_written_bytes": 1000000000000,
            "capacity_bytes": 1000000000000,
            "reallocated_sectors": 0,
            "status": "PASSED"
        }
        result = get_drive_recommendation("sata", smart, health_score=70)
        assert result["status"] == "USED_HEAVY"

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
        """Test USED_HEAVY recommendation for SSD with moderate wear."""
        from smart_parsing import get_drive_recommendation

        smart = {
            "wear_level": 30,
            "power_on_hours": 50000,
            "status": "PASSED"
        }
        # Score 70 is below good threshold (75) but above scratch (50)
        result = get_drive_recommendation("nvme", smart, health_score=70)
        assert result["status"] == "USED_HEAVY"

    def test_recommendation_unknown_status_returns_unknown(self):
        """Test that UNKNOWN SMART status returns UNKNOWN recommendation, not NEW_STOCK.

        Regression: failed SMART reads produce empty_template with status UNKNOWN and
        all fields None/0. Without the early return, these would match the NEW_STOCK
        condition (low POH, zero reallocations, 100% remaining life).
        """
        from smart_parsing import get_drive_recommendation

        smart = {
            "status": "UNKNOWN",
            "power_on_hours": 0,
            "reallocated_sectors": 0,
            "wear_level": None,
            "capacity_bytes": None,
        }
        result = get_drive_recommendation("sata", smart, health_score=100)
        assert result["status"] == "UNKNOWN"

    def test_health_score_unknown_status_returns_none(self):
        """Test that UNKNOWN SMART status returns None health score, not a misleading 100.

        Regression: calculate_drive_health_score with all None/0 fields would compute
        a score of 100 (no penalties), making a failed SMART read look pristine.
        """
        from smart_parsing import calculate_drive_health_score

        smart = {
            "status": "UNKNOWN",
            "power_on_hours": 0,
            "reallocated_sectors": 0,
            "wear_level": None,
        }
        score, breakdown = calculate_drive_health_score("sata", smart)
        assert score is None
        assert breakdown is None



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

        with patch('smart_data_parsing.load_policy', side_effect=Exception("Test error")):
            thresholds = get_triage_thresholds()
            assert "ssd_new_poh_threshold" in thresholds
            assert thresholds["ssd_new_poh_threshold"] == 720

    def test_custom_thresholds_from_policy(self):
        """Test that custom thresholds from policy are used."""
        from smart_parsing import get_triage_thresholds

        with patch('smart_data_parsing.load_policy', return_value={"triage_thresholds": {"ssd_new_poh_threshold": 1000}}):
            thresholds = get_triage_thresholds()
            assert thresholds["ssd_new_poh_threshold"] == 1000

    def test_sas_thresholds_in_defaults(self):
        """Test that new SAS-specific thresholds are present in defaults."""
        from smart_parsing import get_triage_thresholds

        thresholds = get_triage_thresholds()
        assert "sas_grown_defect_fail_threshold" in thresholds
        assert "sas_nme_advisory_threshold" in thresholds
        assert "sas_nme_penalty_threshold" in thresholds
        assert "sas_sticky_lba_threshold" in thresholds
        assert "sas_high_poh_threshold" in thresholds
        assert thresholds["sas_grown_defect_fail_threshold"] == 10000
        assert thresholds["sas_nme_advisory_threshold"] == 1000000
        assert thresholds["sas_nme_penalty_threshold"] == 100000000
        assert thresholds["sas_sticky_lba_threshold"] == 3
        assert thresholds["sas_high_poh_threshold"] == 50000

    def test_unified_health_score_thresholds_in_defaults(self):
        """Test that unified health score thresholds are present in defaults."""
        from smart_parsing import get_triage_thresholds

        thresholds = get_triage_thresholds()
        assert "health_score_destroy_threshold" in thresholds
        assert "health_score_scratch_threshold" in thresholds
        assert "health_score_good_threshold" in thresholds
        assert thresholds["health_score_destroy_threshold"] == 25
        assert thresholds["health_score_scratch_threshold"] == 50
        assert thresholds["health_score_good_threshold"] == 75
        assert "ssd_remaining_life_destroy_threshold" not in thresholds
        assert "ssd_remaining_life_scratch_threshold" not in thresholds
        assert "ssd_remaining_life_good_threshold" not in thresholds
        assert "pending_sectors_destroy_threshold" not in thresholds
        assert "pending_sectors_scratch_threshold" not in thresholds
        assert "sas_grown_defect_scratch_threshold" not in thresholds


class TestSASFields:
    """Test new SAS-specific fields in smart data."""

    @patch('smart_data_parsing.run_command')
    @patch('smart_data_parsing.get_command_path')
    def test_sas_fields_present_in_empty_template(self, mock_get_command_path, mock_run_command):
        """Test that SAS-specific fields are present in the empty template."""
        from smart_parsing import get_smart_data

        mock_get_command_path.return_value = None
        result = get_smart_data("/dev/sda")

        assert "sas_grown_defect_list" in result
        assert "sas_scan_status" in result
        assert "sas_non_medium_errors" in result
        assert "sas_uncorrectable_read_errors" in result
        assert "sas_uncorrectable_write_errors" in result
        assert "sas_uncorrectable_verify_errors" in result
        assert "sas_scan_event_count" in result
        assert "sas_scan_unique_lbas" in result
        assert "sas_sticky_lba_detected" in result
        assert "model_profile" in result

    @patch('smart_data_parsing.run_command')
    @patch('smart_data_parsing.get_command_path')
    @patch('smart_data_parsing.get_config_dir')
    @patch('os.path.exists')
    def test_model_profile_loading(self, mock_exists, mock_get_config_dir, mock_get_command_path, mock_run_command):
        """Test that model_profile is loaded from drive_models.json."""
        from smart_parsing import get_smart_data
        import tempfile
        import json as json_lib

        mock_get_command_path.return_value = "/usr/bin/smartctl"
        mock_run_command.return_value = json_lib.dumps({
            "model_name": "ST4000NM0023",
            "serial_number": "Z1Z3MFCJ",
            "vendor": "SEAGATE",
            "firmware_version": "0003",
            "user_capacity": {"bytes": 4000000000000},
            "smart_status": {"passed": True}
        })

        # Create a temporary config directory with drive_models.json
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_get_config_dir.return_value = tmpdir
            drive_models_path = f"{tmpdir}/drive_models.json"
            mock_exists.side_effect = lambda path: path == drive_models_path

            with open(drive_models_path, "w") as f:
                json_lib.dump({
                    "drive_models": {
                        "SEAGATE,ST4000NM0023,0003": {
                            "vendor": "SEAGATE",
                            "product": "ST4000NM0023",
                            "revision": "0003",
                            "trip_temperature": 60,
                            "nme_normal_range_max": 100000000,
                            "notes": "Test entry"
                        }
                    }
                }, f)

            result = get_smart_data("/dev/sda")
            assert result["model_profile"] is not None
            assert result["model_profile"]["vendor"] == "SEAGATE"
            assert result["model_profile"]["product"] == "ST4000NM0023"
            assert result["model_profile"]["revision"] == "0003"

    @patch('smart_data_parsing.run_command')
    @patch('smart_data_parsing.get_command_path')
    def test_sas_fields_populated_from_smartctl(self, mock_get_command_path, mock_run_command):
        """Test that SAS-specific fields are populated from smartctl JSON output."""
        from smart_parsing import get_smart_data
        import json as json_lib

        mock_get_command_path.return_value = "/usr/bin/smartctl"
        mock_run_command.return_value = json_lib.dumps({
            "model_name": "ST4000NM0023",
            "serial_number": "Z1Z3MFCJ",
            "vendor": "SEAGATE",
            "firmware_version": "0003",
            "user_capacity": {"bytes": 4000000000000},
            "smart_status": {"passed": True},
            "scsi_grown_defect_list": 150,
            "scsi_non_medium_error_count": 5000,
            "scsi_error_counter_log": {
                "read": {"total_uncorrectable_errors": 10},
                "write": {"total_uncorrectable_errors": 5},
                "verify": {"total_uncorrectable_errors": 2}
            },
            "scsi_background_scan_log": {
                "status": {
                    "string": "completed"
                },
                "table": [
                    {"lba": 1000, "status": "Completed without error"},
                    {"lba": 2000, "status": "Completed without error"},
                    {"lba": 3000, "status": "Completed: read failure"},
                    {"lba": 3000, "status": "Completed: read failure"},
                    {"lba": 3000, "status": "Completed: read failure"}
                ]
            }
        })

        result = get_smart_data("/dev/sda")
        
        # Verify SAS fields are populated
        assert result["sas_grown_defect_list"] == 150
        assert result["sas_non_medium_errors"] == 5000
        assert result["sas_uncorrectable_read_errors"] == 10
        assert result["sas_uncorrectable_write_errors"] == 5
        assert result["sas_uncorrectable_verify_errors"] == 2
        assert result["sas_scan_status"] == "COMPLETED"
        assert result["sas_scan_event_count"] == 5
        assert result["sas_scan_unique_lbas"] == 3  # 1000, 2000, 3000
        assert result["sas_sticky_lba_detected"] == True  # LBA 3000 has 3 errors


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
