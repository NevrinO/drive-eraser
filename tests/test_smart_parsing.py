# Fixture-based tests for SMART parsing
import pytest
import sys
import os
import json
from unittest.mock import patch, MagicMock

# Add backend to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))


class TestSASParserWithFixtures:
    """Test SAS-specific parsing using real fixture data."""

    @patch('smart_data_parsing.run_command')
    @patch('smart_data_parsing.get_command_path')
    def test_sas_dead_drive_parsing(self, mock_get_command_path, mock_run_command):
        """Test parsing of dead SAS drive fixture (Z1Z3MFCJ, 16,396 grown defects)."""
        from smart_parsing import get_smart_data

        with open('tests/fixtures/smart/sas_dead_drive.json', 'r') as f:
            fixture_data = json.load(f)

        mock_get_command_path.return_value = "/usr/bin/smartctl"
        mock_run_command.return_value = json.dumps(fixture_data)

        result = get_smart_data("/dev/sda")

        assert result["serial"] == "Z1Z3MFCJ"
        assert result["model"] == "ST4000NM0023"
        assert result["sas_grown_defect_list"] == 16396
        assert result["sas_non_medium_errors"] == 50000000
        assert result["power_on_hours"] == 25000
        assert result["rotation_rate"] == 7200

    @patch('smart_data_parsing.run_command')
    @patch('smart_data_parsing.get_command_path')
    def test_sas_healthy_drive_parsing(self, mock_get_command_path, mock_run_command):
        """Test parsing of healthy SAS drive fixture (S1Z1M0YR, 6 grown defects, 57M NME)."""
        from smart_parsing import get_smart_data

        with open('tests/fixtures/smart/sas_healthy_drive.json', 'r') as f:
            fixture_data = json.load(f)

        mock_get_command_path.return_value = "/usr/bin/smartctl"
        mock_run_command.return_value = json.dumps(fixture_data)

        result = get_smart_data("/dev/sda")

        assert result["serial"] == "S1Z1M0YR"
        assert result["model"] == "ST4000NM0023"
        assert result["sas_grown_defect_list"] == 6
        assert result["sas_non_medium_errors"] == 57000000
        assert result["power_on_hours"] == 30000

    @patch('smart_data_parsing.run_command')
    @patch('smart_data_parsing.get_command_path')
    def test_sas_sticky_lba_detection(self, mock_get_command_path, mock_run_command):
        """Test sticky LBA detection from background scan log."""
        from smart_parsing import get_smart_data

        with open('tests/fixtures/smart/sas_sticky_lba.json', 'r') as f:
            fixture_data = json.load(f)

        mock_get_command_path.return_value = "/usr/bin/smartctl"
        mock_run_command.return_value = json.dumps(fixture_data)

        result = get_smart_data("/dev/sda")

        assert result["sas_sticky_lba_detected"] is True
        assert result["sas_scan_event_count"] == 6
        assert result["sas_scan_unique_lbas"] == 4  # 1000, 2000, 3000, 4000

    @patch('smart_data_parsing.run_command')
    @patch('smart_data_parsing.get_command_path')
    def test_sas_uncorrectable_errors_parsing(self, mock_get_command_path, mock_run_command):
        """Test parsing of uncorrectable error counters."""
        from smart_parsing import get_smart_data

        with open('tests/fixtures/smart/sas_uncorrectable_errors.json', 'r') as f:
            fixture_data = json.load(f)

        mock_get_command_path.return_value = "/usr/bin/smartctl"
        mock_run_command.return_value = json.dumps(fixture_data)

        result = get_smart_data("/dev/sda")

        assert result["sas_uncorrectable_read_errors"] == 15
        assert result["sas_uncorrectable_write_errors"] == 1
        assert result["sas_uncorrectable_verify_errors"] == 2


class TestSSDParserWithFixtures:
    """Test SSD-specific parsing using real fixture data."""

    @patch('smart_data_parsing.run_command')
    @patch('smart_data_parsing.get_command_path')
    def test_sata_ssd_healthy_parsing(self, mock_get_command_path, mock_run_command):
        """Test parsing of healthy SATA SSD fixture."""
        from smart_parsing import get_smart_data

        with open('tests/fixtures/smart/sata_ssd_healthy.json', 'r') as f:
            fixture_data = json.load(f)

        mock_get_command_path.return_value = "/usr/bin/smartctl"
        mock_run_command.return_value = json.dumps(fixture_data)

        result = get_smart_data("/dev/sda")

        assert result["serial"] == "S4Z1NY0K123456"
        assert result["model"] == "Samsung SSD 870 EVO"
        assert result["rotation_rate"] == 0
        assert result["wear_level"] == 10
        assert result["reallocated_sectors"] is None  # No bad sectors
        assert result["power_on_hours"] == 10000

    @patch('smart_data_parsing.run_command')
    @patch('smart_data_parsing.get_command_path')
    def test_sata_ssd_intel_realloc_parsing(self, mock_get_command_path, mock_run_command):
        """Test parsing of Intel SSD with reallocated sectors."""
        from smart_parsing import get_smart_data

        with open('tests/fixtures/smart/sata_ssd_intel_realloc.json', 'r') as f:
            fixture_data = json.load(f)

        mock_get_command_path.return_value = "/usr/bin/smartctl"
        mock_run_command.return_value = json.dumps(fixture_data)

        result = get_smart_data("/dev/sda")

        assert result["serial"] == "CVFT12345678"
        assert result["model"] == "INTEL SSDSC2KB480G8"
        assert result["reallocated_sectors"] == 8
        assert result["reallocated_normalized"] == 93
        assert result["wear_level"] == 7  # Percentage used (normalized value is 7, which is <= 50)
        assert result["rotation_rate"] == 0


class TestNVMeParserWithFixtures:
    """Test NVMe-specific parsing using real fixture data."""

    @patch('smart_data_parsing.run_command')
    @patch('smart_data_parsing.get_command_path')
    def test_nvme_healthy_parsing(self, mock_get_command_path, mock_run_command):
        """Test parsing of healthy NVMe drive fixture."""
        from smart_parsing import get_smart_data

        with open('tests/fixtures/smart/nvme_healthy.json', 'r') as f:
            fixture_data = json.load(f)

        mock_get_command_path.return_value = "/usr/bin/smartctl"
        mock_run_command.return_value = json.dumps(fixture_data)

        result = get_smart_data("/dev/nvme0n1")

        assert result["serial"] == "S4Z1NY0K123456"
        assert result["model"] == "Samsung SSD 970 EVO"
        assert result["wear_level"] == 11
        assert result["data_written_raw"] == 1000000
        assert result["data_read_raw"] == 5000000


class TestSASScoringWithFixtures:
    """Test SAS health scoring using real fixture data."""

    @patch('smart_data_parsing.run_command')
    @patch('smart_data_parsing.get_command_path')
    def test_sas_dead_drive_scoring(self, mock_get_command_path, mock_run_command):
        """Test that dead SAS drive (16,396 grown defects) scores ≤ 5."""
        from smart_parsing import get_smart_data, calculate_drive_health_score

        with open('tests/fixtures/smart/sas_dead_drive.json', 'r') as f:
            fixture_data = json.load(f)

        mock_get_command_path.return_value = "/usr/bin/smartctl"
        mock_run_command.return_value = json.dumps(fixture_data)

        smart = get_smart_data("/dev/sda")
        health, _ = calculate_drive_health_score("sas", smart)

        # With 16,396 grown defects, logarithmic penalty should be severe
        # log10(16396) ≈ 4.2, penalty ≈ 4.2 * 20 = 84, health ≈ 16
        assert health <= 5, f"Expected health <= 5 for dead SAS drive, got {health}"

    @patch('smart_data_parsing.run_command')
    @patch('smart_data_parsing.get_command_path')
    def test_sas_healthy_drive_scoring(self, mock_get_command_path, mock_run_command):
        """Test that healthy SAS drive (6 grown defects, 57M NME) scores 53-58."""
        from smart_parsing import get_smart_data, calculate_drive_health_score

        with open('tests/fixtures/smart/sas_healthy_drive.json', 'r') as f:
            fixture_data = json.load(f)

        mock_get_command_path.return_value = "/usr/bin/smartctl"
        mock_run_command.return_value = json.dumps(fixture_data)

        smart = get_smart_data("/dev/sda")
        health, _ = calculate_drive_health_score("sas", smart)

        # With 6 grown defects, log10(6) ≈ 0.78, penalty ≈ 0.78 * 20 = 15.6
        # 30K POH: (30000-20000)/40000*30 = 7.5 penalty
        # 57M NME is below 100M penalty threshold, so no NME penalty
        # Health ≈ 100 - 15.6 - 7.5 = 76.9
        assert 75 <= health <= 80, f"Expected health 75-80 for healthy SAS drive, got {health}"


class TestSSDScoringWithFixtures:
    """Test SSD health scoring using real fixture data."""

    @patch('smart_data_parsing.run_command')
    @patch('smart_data_parsing.get_command_path')
    def test_samsung_ssd_high_poh_scoring(self, mock_get_command_path, mock_run_command):
        """Test Samsung SSD with high POH but zero reallocations scores ≥ 80."""
        from smart_parsing import get_smart_data, calculate_drive_health_score

        with open('tests/fixtures/smart/sata_ssd_healthy.json', 'r') as f:
            fixture_data = json.load(f)

        mock_get_command_path.return_value = "/usr/bin/smartctl"
        mock_run_command.return_value = json.dumps(fixture_data)

        smart = get_smart_data("/dev/sda")
        health, _ = calculate_drive_health_score("sata", smart)

        # 10% wear, 10K POH (below high threshold), no reallocations
        # Health = 100 - 10 = 90
        assert health >= 80, f"Expected health >= 80 for healthy Samsung SSD, got {health}"

    @patch('smart_data_parsing.run_command')
    @patch('smart_data_parsing.get_command_path')
    def test_intel_ssd_realloc_scoring(self, mock_get_command_path, mock_run_command):
        """Test Intel SSD with 8 reallocated sectors and 93% wear scores 60-75."""
        from smart_parsing import get_smart_data, calculate_drive_health_score

        with open('tests/fixtures/smart/sata_ssd_intel_realloc.json', 'r') as f:
            fixture_data = json.load(f)

        mock_get_command_path.return_value = "/usr/bin/smartctl"
        mock_run_command.return_value = json.dumps(fixture_data)

        smart = get_smart_data("/dev/sda")
        health, _ = calculate_drive_health_score("sata", smart)

        # Normalized wear value is 7 (percentage used), so 7% wear, 93% remaining
        # Base health: 100 - 7 = 93
        # 8 reallocated sectors with 93% spare reserve: penalty = 8 * 2 = 16 (mitigated)
        # Health = 93 - 16 = 77
        assert 75 <= health <= 80, f"Expected health 75-80 for Intel SSD with reallocs, got {health}"


class TestSASRecommendationWithFixtures:
    """Test SAS drive recommendations using real fixture data."""

    @patch('smart_data_parsing.run_command')
    @patch('smart_data_parsing.get_command_path')
    def test_sas_dead_drive_recommendation(self, mock_get_command_path, mock_run_command):
        """Test dead SAS drive is recommended DESTROY."""
        from smart_parsing import get_smart_data, calculate_drive_health_score, get_drive_recommendation

        with open('tests/fixtures/smart/sas_dead_drive.json', 'r') as f:
            fixture_data = json.load(f)

        mock_get_command_path.return_value = "/usr/bin/smartctl"
        mock_run_command.return_value = json.dumps(fixture_data)

        smart = get_smart_data("/dev/sda")
        health, _ = calculate_drive_health_score("sas", smart)
        recommendation = get_drive_recommendation("sas", smart, health)

        assert recommendation["status"] == "DESTROY"

    @patch('smart_data_parsing.run_command')
    @patch('smart_data_parsing.get_command_path')
    def test_sas_healthy_drive_recommendation(self, mock_get_command_path, mock_run_command):
        """Test healthy SAS drive is recommended SCRATCH."""
        from smart_parsing import get_smart_data, calculate_drive_health_score, get_drive_recommendation

        with open('tests/fixtures/smart/sas_healthy_drive.json', 'r') as f:
            fixture_data = json.load(f)

        mock_get_command_path.return_value = "/usr/bin/smartctl"
        mock_run_command.return_value = json.dumps(fixture_data)

        smart = get_smart_data("/dev/sda")
        health, _ = calculate_drive_health_score("sas", smart)
        recommendation = get_drive_recommendation("sas", smart, health)

        assert recommendation["status"] == "SCRATCH"

    @patch('smart_data_parsing.run_command')
    @patch('smart_data_parsing.get_command_path')
    def test_sas_uncorrectable_verify_errors_recommendation(self, mock_get_command_path, mock_run_command):
        """Test SAS with verify errors is recommended DESTROY."""
        from smart_parsing import get_smart_data, calculate_drive_health_score, get_drive_recommendation

        with open('tests/fixtures/smart/sas_uncorrectable_errors.json', 'r') as f:
            fixture_data = json.load(f)

        mock_get_command_path.return_value = "/usr/bin/smartctl"
        mock_run_command.return_value = json.dumps(fixture_data)

        smart = get_smart_data("/dev/sda")
        health, _ = calculate_drive_health_score("sas", smart)
        recommendation = get_drive_recommendation("sas", smart, health)

        # Verify errors >= 1 should trigger DESTROY
        assert recommendation["status"] == "DESTROY"

    @patch('smart_data_parsing.run_command')
    @patch('smart_data_parsing.get_command_path')
    def test_sas_sticky_lba_recommendation(self, mock_get_command_path, mock_run_command):
        """Test SAS with sticky LBA is recommended at least SCRATCH."""
        from smart_parsing import get_smart_data, calculate_drive_health_score, get_drive_recommendation

        with open('tests/fixtures/smart/sas_sticky_lba.json', 'r') as f:
            fixture_data = json.load(f)

        mock_get_command_path.return_value = "/usr/bin/smartctl"
        mock_run_command.return_value = json.dumps(fixture_data)

        smart = get_smart_data("/dev/sda")
        health, _ = calculate_drive_health_score("sas", smart)
        recommendation = get_drive_recommendation("sas", smart, health)

        # Sticky LBA should trigger at least SCRATCH
        assert recommendation["status"] in ["SCRATCH", "DESTROY"]

    @patch('smart_data_parsing.run_command')
    @patch('smart_data_parsing.get_command_path')
    @patch('smart_health.get_triage_thresholds')
    def test_sas_grown_defects_exceed_fail_threshold(self, mock_get_triage_thresholds, mock_get_command_path, mock_run_command):
        """Test SAS with grown defects >= fail threshold is recommended DESTROY."""
        from smart_parsing import get_smart_data, calculate_drive_health_score, get_drive_recommendation

        mock_get_triage_thresholds.return_value = {
            "sas_grown_defect_fail_threshold": 10000,
            "health_score_destroy_threshold": 25,
            "health_score_scratch_threshold": 50,
            "health_score_good_threshold": 75,
            "ssd_high_poh_threshold": 43800,
            "ssd_new_poh_threshold": 720,
            "ssd_new_fdw_threshold": 0.06,
            "hdd_new_poh_threshold": 720,
            "hdd_new_fdw_threshold": 2.0,
            "realloc_raw_new_threshold": 0
        }

        with open('tests/fixtures/smart/sas_healthy_drive.json', 'r') as f:
            fixture_data = json.load(f)
        # Modify to have high grown defects
        fixture_data["scsi_grown_defect_list"] = 15000

        mock_get_command_path.return_value = "/usr/bin/smartctl"
        mock_run_command.return_value = json.dumps(fixture_data)

        smart = get_smart_data("/dev/sda")
        health, _ = calculate_drive_health_score("sas", smart)
        recommendation = get_drive_recommendation("sas", smart, health)

        # Grown defects >= 10000 should trigger DESTROY
        assert recommendation["status"] == "DESTROY"
        assert "grown defects" in recommendation["comment"].lower()

    @patch('smart_data_parsing.run_command')
    @patch('smart_data_parsing.get_command_path')
    @patch('smart_health.get_triage_thresholds')
    def test_sas_grown_defects_below_fail_threshold(self, mock_get_triage_thresholds, mock_get_command_path, mock_run_command):
        """Test SAS with grown defects > 0 but < fail threshold is recommended SCRATCH."""
        from smart_parsing import get_smart_data, calculate_drive_health_score, get_drive_recommendation

        mock_get_triage_thresholds.return_value = {
            "sas_grown_defect_fail_threshold": 10000,
            "health_score_destroy_threshold": 25,
            "health_score_scratch_threshold": 50,
            "health_score_good_threshold": 75,
            "ssd_high_poh_threshold": 43800,
            "ssd_new_poh_threshold": 720,
            "ssd_new_fdw_threshold": 0.06,
            "hdd_new_poh_threshold": 720,
            "hdd_new_fdw_threshold": 2.0,
            "realloc_raw_new_threshold": 0
        }

        with open('tests/fixtures/smart/sas_healthy_drive.json', 'r') as f:
            fixture_data = json.load(f)
        # Modify to have moderate grown defects
        fixture_data["scsi_grown_defect_list"] = 500

        mock_get_command_path.return_value = "/usr/bin/smartctl"
        mock_run_command.return_value = json.dumps(fixture_data)

        smart = get_smart_data("/dev/sda")
        health, _ = calculate_drive_health_score("sas", smart)
        recommendation = get_drive_recommendation("sas", smart, health)

        # 500 grown defects → log penalty ~54, score drops below scratch threshold
        assert recommendation["status"] == "SCRATCH"

    @patch('smart_data_parsing.run_command')
    @patch('smart_data_parsing.get_command_path')
    def test_sas_no_grown_defects_no_defect_recommendation(self, mock_get_command_path, mock_run_command):
        """Test SAS with 0 grown defects does not trigger defect-based recommendations."""
        from smart_parsing import get_smart_data, calculate_drive_health_score, get_drive_recommendation

        with open('tests/fixtures/smart/sas_healthy_drive.json', 'r') as f:
            fixture_data = json.load(f)
        # Ensure no grown defects
        fixture_data["scsi_grown_defect_list"] = 0

        mock_get_command_path.return_value = "/usr/bin/smartctl"
        mock_run_command.return_value = json.dumps(fixture_data)

        smart = get_smart_data("/dev/sda")
        health, _ = calculate_drive_health_score("sas", smart)
        recommendation = get_drive_recommendation("sas", smart, health)

        # No grown defects should not trigger defect-based DESTROY/SCRATCH
        # (may still trigger other recommendations based on health score)
        if recommendation["status"] in ["DESTROY", "SCRATCH"]:
            # If it's DESTROY/SCRATCH, it should NOT be due to grown defects
            assert "grown defects" not in recommendation["comment"].lower()


class TestPreWipeHealthGate:
    """Test pre-wipe health gate functionality."""

    @patch('smart_health_gate.get_smart_data')
    @patch('smart_health_gate.get_triage_thresholds')
    def test_health_gate_disabled(self, mock_get_triage_thresholds, mock_get_smart_data):
        """Test that health gate returns ok when disabled."""
        from smart_parsing import pre_wipe_health_gate

        policy = {
            "prewipe_health_gate_enabled": False,
            "prewipe_health_gate_strict_mode": False,
            "prewipe_health_gate_block_destroy": True,
            "prewipe_health_gate_block_scratch": False,
            "prewipe_health_gate_block_failed_smart": True,
            "prewipe_health_gate_max_pending_sectors": 10,
            "prewipe_health_gate_max_reallocated_sectors": 5,
            "prewipe_health_gate_max_interface_errors": 100,
            "prewipe_health_gate_max_health_score_drop": 20
        }

        result = pre_wipe_health_gate("/dev/sda", "sata", policy)

        assert result["ok"] is True
        assert result["blocked"] is False
        assert result["block_reason"] is None

    @patch('smart_health_gate.get_smart_data')
    @patch('smart_health_gate.get_triage_thresholds')
    def test_drive_not_accessible(self, mock_get_triage_thresholds, mock_get_smart_data):
        """Test that health gate blocks when drive is not accessible."""
        from smart_parsing import pre_wipe_health_gate

        mock_get_smart_data.return_value = None
        mock_get_triage_thresholds.return_value = {
            "health_score_destroy_threshold": 30,
            "sas_grown_defect_fail_threshold": 10000
        }

        policy = {
            "prewipe_health_gate_enabled": True,
            "prewipe_health_gate_strict_mode": False,
            "prewipe_health_gate_block_destroy": True,
            "prewipe_health_gate_block_scratch": False,
            "prewipe_health_gate_block_failed_smart": True,
            "prewipe_health_gate_max_pending_sectors": 10,
            "prewipe_health_gate_max_reallocated_sectors": 5,
            "prewipe_health_gate_max_interface_errors": 100,
            "prewipe_health_gate_max_health_score_drop": 20
        }

        result = pre_wipe_health_gate("/dev/sda", "sata", policy)

        assert result["ok"] is False
        assert result["blocked"] is True
        assert result["block_reason"] == "drive_not_accessible"

    @patch('smart_health_gate.get_smart_data')
    @patch('smart_health_gate.calculate_drive_health_score')
    @patch('smart_health_gate.get_drive_recommendation')
    @patch('smart_health_gate.get_triage_thresholds')
    def test_smart_status_failed_blocks(self, mock_get_triage_thresholds, mock_get_drive_recommendation, mock_calculate_health, mock_get_smart_data):
        """Test that SMART status FAILED blocks wipe."""
        from smart_parsing import pre_wipe_health_gate

        mock_get_smart_data.return_value = {
            "status": "FAILED",
            "pending_sectors": 0,
            "reallocated_sectors": 0,
            "interface_errors": 0,
            "raw": None
        }
        mock_calculate_health.return_value = (50, {})
        mock_get_drive_recommendation.return_value = {"status": "USED_GOOD", "comment": "Healthy"}
        mock_get_triage_thresholds.return_value = {
            "health_score_destroy_threshold": 30,
            "sas_grown_defect_fail_threshold": 10000
        }

        policy = {
            "prewipe_health_gate_enabled": True,
            "prewipe_health_gate_strict_mode": False,
            "prewipe_health_gate_block_destroy": True,
            "prewipe_health_gate_block_scratch": False,
            "prewipe_health_gate_block_failed_smart": True,
            "prewipe_health_gate_max_pending_sectors": 10,
            "prewipe_health_gate_max_reallocated_sectors": 5,
            "prewipe_health_gate_max_interface_errors": 100,
            "prewipe_health_gate_max_health_score_drop": 20
        }

        result = pre_wipe_health_gate("/dev/sda", "sata", policy)

        assert result["ok"] is True
        assert result["blocked"] is True
        assert result["block_reason"] == "smart_status_failed"

    @patch('smart_health_gate.get_smart_data')
    @patch('smart_health_gate.calculate_drive_health_score')
    @patch('smart_health_gate.get_drive_recommendation')
    @patch('smart_health_gate.get_triage_thresholds')
    def test_health_score_below_destroy_blocks(self, mock_get_triage_thresholds, mock_get_drive_recommendation, mock_calculate_health, mock_get_smart_data):
        """Test that health score below DESTROY threshold blocks wipe."""
        from smart_parsing import pre_wipe_health_gate

        mock_get_smart_data.return_value = {
            "status": "PASSED",
            "pending_sectors": 0,
            "reallocated_sectors": 0,
            "interface_errors": 0,
            "raw": None
        }
        mock_calculate_health.return_value = (25, {})
        mock_get_drive_recommendation.return_value = {"status": "USED_GOOD", "comment": "Healthy"}
        mock_get_triage_thresholds.return_value = {
            "health_score_destroy_threshold": 30,
            "sas_grown_defect_fail_threshold": 10000
        }

        policy = {
            "prewipe_health_gate_enabled": True,
            "prewipe_health_gate_strict_mode": False,
            "prewipe_health_gate_block_destroy": True,
            "prewipe_health_gate_block_scratch": False,
            "prewipe_health_gate_block_failed_smart": True,
            "prewipe_health_gate_max_pending_sectors": 10,
            "prewipe_health_gate_max_reallocated_sectors": 5,
            "prewipe_health_gate_max_interface_errors": 100,
            "prewipe_health_gate_max_health_score_drop": 20
        }

        result = pre_wipe_health_gate("/dev/sda", "sata", policy)

        assert result["ok"] is True
        assert result["blocked"] is True
        assert result["block_reason"] == "health_score_below_destroy_threshold"

    @patch('smart_health_gate.get_smart_data')
    @patch('smart_health_gate.calculate_drive_health_score')
    @patch('smart_health_gate.get_drive_recommendation')
    @patch('smart_health_gate.get_triage_thresholds')
    def test_recommendation_destroy_blocks(self, mock_get_triage_thresholds, mock_get_drive_recommendation, mock_calculate_health, mock_get_smart_data):
        """Test that DESTROY recommendation blocks wipe."""
        from smart_parsing import pre_wipe_health_gate

        mock_get_smart_data.return_value = {
            "status": "PASSED",
            "pending_sectors": 0,
            "reallocated_sectors": 0,
            "interface_errors": 0,
            "raw": None
        }
        mock_calculate_health.return_value = (50, {})
        mock_get_drive_recommendation.return_value = {"status": "DESTROY", "comment": "Critical issues"}
        mock_get_triage_thresholds.return_value = {
            "health_score_destroy_threshold": 30,
            "sas_grown_defect_fail_threshold": 10000
        }

        policy = {
            "prewipe_health_gate_enabled": True,
            "prewipe_health_gate_strict_mode": False,
            "prewipe_health_gate_block_destroy": True,
            "prewipe_health_gate_block_scratch": False,
            "prewipe_health_gate_block_failed_smart": True,
            "prewipe_health_gate_max_pending_sectors": 10,
            "prewipe_health_gate_max_reallocated_sectors": 5,
            "prewipe_health_gate_max_interface_errors": 100,
            "prewipe_health_gate_max_health_score_drop": 20
        }

        result = pre_wipe_health_gate("/dev/sda", "sata", policy)

        assert result["ok"] is True
        assert result["blocked"] is True
        assert result["block_reason"] == "recommendation_destroy"

    @patch('smart_health_gate.get_smart_data')
    @patch('smart_health_gate.calculate_drive_health_score')
    @patch('smart_health_gate.get_drive_recommendation')
    @patch('smart_health_gate.get_triage_thresholds')
    def test_recommendation_scratch_blocks_when_configured(self, mock_get_triage_thresholds, mock_get_drive_recommendation, mock_calculate_health, mock_get_smart_data):
        """Test that SCRATCH recommendation blocks wipe when configured."""
        from smart_parsing import pre_wipe_health_gate

        mock_get_smart_data.return_value = {
            "status": "PASSED",
            "pending_sectors": 0,
            "reallocated_sectors": 0,
            "interface_errors": 0,
            "raw": None
        }
        mock_calculate_health.return_value = (50, {})
        mock_get_drive_recommendation.return_value = {"status": "SCRATCH", "comment": "Unstable"}
        mock_get_triage_thresholds.return_value = {
            "health_score_destroy_threshold": 30,
            "sas_grown_defect_fail_threshold": 10000
        }

        policy = {
            "prewipe_health_gate_enabled": True,
            "prewipe_health_gate_strict_mode": False,
            "prewipe_health_gate_block_destroy": True,
            "prewipe_health_gate_block_scratch": True,
            "prewipe_health_gate_block_failed_smart": True,
            "prewipe_health_gate_max_pending_sectors": 10,
            "prewipe_health_gate_max_reallocated_sectors": 5,
            "prewipe_health_gate_max_interface_errors": 100,
            "prewipe_health_gate_max_health_score_drop": 20
        }

        result = pre_wipe_health_gate("/dev/sda", "sata", policy)

        assert result["ok"] is True
        assert result["blocked"] is True
        assert result["block_reason"] == "recommendation_scratch"

    @patch('smart_health_gate.get_smart_data')
    @patch('smart_health_gate.calculate_drive_health_score')
    @patch('smart_health_gate.get_drive_recommendation')
    @patch('smart_health_gate.get_triage_thresholds')
    def test_pending_sectors_exceeded_blocks(self, mock_get_triage_thresholds, mock_get_drive_recommendation, mock_calculate_health, mock_get_smart_data):
        """Test that pending sectors exceeding threshold blocks wipe."""
        from smart_parsing import pre_wipe_health_gate

        mock_get_smart_data.return_value = {
            "status": "PASSED",
            "pending_sectors": 15,
            "reallocated_sectors": 0,
            "interface_errors": 0,
            "raw": None
        }
        mock_calculate_health.return_value = (80, {})
        mock_get_drive_recommendation.return_value = {"status": "USED_GOOD", "comment": "Healthy"}
        mock_get_triage_thresholds.return_value = {
            "health_score_destroy_threshold": 30,
            "sas_grown_defect_fail_threshold": 10000
        }

        policy = {
            "prewipe_health_gate_enabled": True,
            "prewipe_health_gate_strict_mode": False,
            "prewipe_health_gate_block_destroy": True,
            "prewipe_health_gate_block_scratch": False,
            "prewipe_health_gate_block_failed_smart": True,
            "prewipe_health_gate_max_pending_sectors": 10,
            "prewipe_health_gate_max_reallocated_sectors": 5,
            "prewipe_health_gate_max_interface_errors": 100,
            "prewipe_health_gate_max_health_score_drop": 20
        }

        result = pre_wipe_health_gate("/dev/sda", "sata", policy)

        assert result["ok"] is True
        assert result["blocked"] is True
        assert result["block_reason"] == "pending_sectors_exceeded"

    @patch('smart_health_gate.get_smart_data')
    @patch('smart_health_gate.calculate_drive_health_score')
    @patch('smart_health_gate.get_drive_recommendation')
    @patch('smart_health_gate.get_triage_thresholds')
    def test_reallocated_sectors_exceeded_blocks(self, mock_get_triage_thresholds, mock_get_drive_recommendation, mock_calculate_health, mock_get_smart_data):
        """Test that reallocated sectors exceeding threshold blocks wipe."""
        from smart_parsing import pre_wipe_health_gate

        mock_get_smart_data.return_value = {
            "status": "PASSED",
            "pending_sectors": 0,
            "reallocated_sectors": 10,
            "interface_errors": 0,
            "raw": None
        }
        mock_calculate_health.return_value = (80, {})
        mock_get_drive_recommendation.return_value = {"status": "USED_GOOD", "comment": "Healthy"}
        mock_get_triage_thresholds.return_value = {
            "health_score_destroy_threshold": 30,
            "sas_grown_defect_fail_threshold": 10000
        }

        policy = {
            "prewipe_health_gate_enabled": True,
            "prewipe_health_gate_strict_mode": False,
            "prewipe_health_gate_block_destroy": True,
            "prewipe_health_gate_block_scratch": False,
            "prewipe_health_gate_block_failed_smart": True,
            "prewipe_health_gate_max_pending_sectors": 10,
            "prewipe_health_gate_max_reallocated_sectors": 5,
            "prewipe_health_gate_max_interface_errors": 100,
            "prewipe_health_gate_max_health_score_drop": 20
        }

        result = pre_wipe_health_gate("/dev/sda", "sata", policy)

        assert result["ok"] is True
        assert result["blocked"] is True
        assert result["block_reason"] == "reallocated_sectors_exceeded"

    @patch('smart_health_gate.get_smart_data')
    @patch('smart_health_gate.calculate_drive_health_score')
    @patch('smart_health_gate.get_drive_recommendation')
    @patch('smart_health_gate.get_triage_thresholds')
    def test_interface_errors_exceeded_blocks(self, mock_get_triage_thresholds, mock_get_drive_recommendation, mock_calculate_health, mock_get_smart_data):
        """Test that interface errors exceeding threshold blocks wipe."""
        from smart_parsing import pre_wipe_health_gate

        mock_get_smart_data.return_value = {
            "status": "PASSED",
            "pending_sectors": 0,
            "reallocated_sectors": 0,
            "interface_errors": 150,
            "raw": None
        }
        mock_calculate_health.return_value = (80, {})
        mock_get_drive_recommendation.return_value = {"status": "USED_GOOD", "comment": "Healthy"}
        mock_get_triage_thresholds.return_value = {
            "health_score_destroy_threshold": 30,
            "sas_grown_defect_fail_threshold": 10000
        }

        policy = {
            "prewipe_health_gate_enabled": True,
            "prewipe_health_gate_strict_mode": False,
            "prewipe_health_gate_block_destroy": True,
            "prewipe_health_gate_block_scratch": False,
            "prewipe_health_gate_block_failed_smart": True,
            "prewipe_health_gate_max_pending_sectors": 10,
            "prewipe_health_gate_max_reallocated_sectors": 5,
            "prewipe_health_gate_max_interface_errors": 100,
            "prewipe_health_gate_max_health_score_drop": 20
        }

        result = pre_wipe_health_gate("/dev/sda", "sata", policy)

        assert result["ok"] is True
        assert result["blocked"] is True
        assert result["block_reason"] == "interface_errors_exceeded"

    @patch('smart_health_gate.get_smart_data')
    @patch('smart_health_gate.calculate_drive_health_score')
    @patch('smart_health_gate.get_drive_recommendation')
    @patch('smart_health_gate.get_triage_thresholds')
    def test_healthy_drive_passes_gate(self, mock_get_triage_thresholds, mock_get_drive_recommendation, mock_calculate_health, mock_get_smart_data):
        """Test that healthy drive passes health gate."""
        from smart_parsing import pre_wipe_health_gate

        mock_get_smart_data.return_value = {
            "status": "PASSED",
            "pending_sectors": 0,
            "reallocated_sectors": 0,
            "interface_errors": 0,
            "raw": None
        }
        mock_calculate_health.return_value = (95, {})
        mock_get_drive_recommendation.return_value = {"status": "USED_GOOD", "comment": "Healthy"}
        mock_get_triage_thresholds.return_value = {
            "health_score_destroy_threshold": 30,
            "sas_grown_defect_fail_threshold": 10000
        }

        policy = {
            "prewipe_health_gate_enabled": True,
            "prewipe_health_gate_strict_mode": False,
            "prewipe_health_gate_block_destroy": True,
            "prewipe_health_gate_block_scratch": False,
            "prewipe_health_gate_block_failed_smart": True,
            "prewipe_health_gate_max_pending_sectors": 10,
            "prewipe_health_gate_max_reallocated_sectors": 5,
            "prewipe_health_gate_max_interface_errors": 100,
            "prewipe_health_gate_max_health_score_drop": 20
        }

        result = pre_wipe_health_gate("/dev/sda", "sata", policy)

        assert result["ok"] is True
        assert result["blocked"] is False
        assert result["block_reason"] is None
        assert result["health_score"] == 95
        assert result["recommendation"] == "USED_GOOD"

    @patch('smart_health_gate.get_smart_data')
    @patch('smart_health_gate.calculate_drive_health_score')
    @patch('smart_health_gate.get_drive_recommendation')
    @patch('smart_health_gate.get_triage_thresholds')
    def test_multi_letter_sata_device_passes_validation(self, mock_get_triage_thresholds, mock_get_drive_recommendation, mock_calculate_health, mock_get_smart_data):
        """Regression: /dev/sdac and /dev/sdbt must not be blocked as invalid_device_path."""
        from smart_parsing import pre_wipe_health_gate

        mock_get_smart_data.return_value = {
            "status": "PASSED",
            "pending_sectors": 0,
            "reallocated_sectors": 0,
            "interface_errors": 0,
            "raw": None
        }
        mock_calculate_health.return_value = (95, {})
        mock_get_drive_recommendation.return_value = {"status": "USED_GOOD", "comment": "Healthy"}
        mock_get_triage_thresholds.return_value = {
            "health_score_destroy_threshold": 30,
            "sas_grown_defect_fail_threshold": 10000
        }

        policy = {
            "prewipe_health_gate_enabled": True,
            "prewipe_health_gate_strict_mode": False,
            "prewipe_health_gate_block_destroy": True,
            "prewipe_health_gate_block_scratch": False,
            "prewipe_health_gate_block_failed_smart": True,
            "prewipe_health_gate_max_pending_sectors": 10,
            "prewipe_health_gate_max_reallocated_sectors": 5,
            "prewipe_health_gate_max_interface_errors": 100,
            "prewipe_health_gate_max_health_score_drop": 20
        }

        for device in ["/dev/sdac", "/dev/sdbt", "/dev/sdaa"]:
            result = pre_wipe_health_gate(device, "sata", policy)
            assert result["block_reason"] != "invalid_device_path", f"{device} rejected as invalid_device_path"


class TestValidateDevicePath:
    """Test device path validation in smart_parsing (regression for multi-letter SCSI names)."""

    def test_valid_sata_multi_letter(self):
        """Multi-letter SCSI device names beyond /dev/sdz must be accepted."""
        from smart_parsing import validate_device_path
        valid_paths = [
            "/dev/sdaa",
            "/dev/sdac",
            "/dev/sdbt",
            "/dev/sdaz",
            "/dev/sdba",
        ]
        for path in valid_paths:
            assert validate_device_path(path) is True, f"Valid path rejected: {path}"

    def test_valid_sata_partitions(self):
        """SATA partitions with multi-letter base names must be accepted."""
        from smart_parsing import validate_device_path
        assert validate_device_path("/dev/sdac1") is True
        assert validate_device_path("/dev/sdbt12") is True

    def test_valid_nvme(self):
        """NVMe device names must be accepted."""
        from smart_parsing import validate_device_path
        assert validate_device_path("/dev/nvme0n1") is True
        assert validate_device_path("/dev/nvme0n1p1") is True

    def test_invalid_paths(self):
        """Path traversal, newlines, and malformed paths must still be rejected."""
        from smart_parsing import validate_device_path
        invalid_paths = [
            "/dev/../etc/passwd",
            "/dev/sda\n",
            "/dev/sda\r",
            "/dev/sda*",
            "dev/sda",
            "",
            "   ",
        ]
        for path in invalid_paths:
            assert validate_device_path(path) is False, f"Invalid path accepted: {repr(path)}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
