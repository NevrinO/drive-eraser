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

    @patch('smart_parsing.run_command')
    @patch('smart_parsing.get_command_path')
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

    @patch('smart_parsing.run_command')
    @patch('smart_parsing.get_command_path')
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

    @patch('smart_parsing.run_command')
    @patch('smart_parsing.get_command_path')
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

    @patch('smart_parsing.run_command')
    @patch('smart_parsing.get_command_path')
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

    @patch('smart_parsing.run_command')
    @patch('smart_parsing.get_command_path')
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

    @patch('smart_parsing.run_command')
    @patch('smart_parsing.get_command_path')
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
        assert result["wear_level"] == 93
        assert result["rotation_rate"] == 0


class TestNVMeParserWithFixtures:
    """Test NVMe-specific parsing using real fixture data."""

    @patch('smart_parsing.run_command')
    @patch('smart_parsing.get_command_path')
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

    @patch('smart_parsing.run_command')
    @patch('smart_parsing.get_command_path')
    def test_sas_dead_drive_scoring(self, mock_get_command_path, mock_run_command):
        """Test that dead SAS drive (16,396 grown defects) scores ≤ 5."""
        from smart_parsing import get_smart_data, calculate_drive_health_score

        with open('tests/fixtures/smart/sas_dead_drive.json', 'r') as f:
            fixture_data = json.load(f)

        mock_get_command_path.return_value = "/usr/bin/smartctl"
        mock_run_command.return_value = json.dumps(fixture_data)

        smart = get_smart_data("/dev/sda")
        health, _ = calculate_drive_health_score("sas", smart, None)

        # With 16,396 grown defects, logarithmic penalty should be severe
        # log10(16396) ≈ 4.2, penalty ≈ 4.2 * 20 = 84, health ≈ 16
        assert health <= 5, f"Expected health <= 5 for dead SAS drive, got {health}"

    @patch('smart_parsing.run_command')
    @patch('smart_parsing.get_command_path')
    def test_sas_healthy_drive_scoring(self, mock_get_command_path, mock_run_command):
        """Test that healthy SAS drive (6 grown defects, 57M NME) scores 53-58."""
        from smart_parsing import get_smart_data, calculate_drive_health_score

        with open('tests/fixtures/smart/sas_healthy_drive.json', 'r') as f:
            fixture_data = json.load(f)

        mock_get_command_path.return_value = "/usr/bin/smartctl"
        mock_run_command.return_value = json.dumps(fixture_data)

        smart = get_smart_data("/dev/sda")
        health, _ = calculate_drive_health_score("sas", smart, None)

        # With 6 grown defects, log10(6) ≈ 0.78, penalty ≈ 0.78 * 20 = 15.6
        # 57M NME is below 100M penalty threshold, so no NME penalty
        # Health ≈ 100 - 15.6 = 84.4, but POH penalty also applies
        assert 53 <= health <= 58, f"Expected health 53-58 for healthy SAS drive, got {health}"


class TestSSDScoringWithFixtures:
    """Test SSD health scoring using real fixture data."""

    @patch('smart_parsing.run_command')
    @patch('smart_parsing.get_command_path')
    def test_samsung_ssd_high_poh_scoring(self, mock_get_command_path, mock_run_command):
        """Test Samsung SSD with high POH but zero reallocations scores ≥ 80."""
        from smart_parsing import get_smart_data, calculate_drive_health_score

        with open('tests/fixtures/smart/sata_ssd_healthy.json', 'r') as f:
            fixture_data = json.load(f)

        mock_get_command_path.return_value = "/usr/bin/smartctl"
        mock_run_command.return_value = json.dumps(fixture_data)

        smart = get_smart_data("/dev/sda")
        health, _ = calculate_drive_health_score("sata", smart, None)

        # 10% wear, 10K POH (below high threshold), no reallocations
        # Health = 100 - 10 = 90
        assert health >= 80, f"Expected health >= 80 for healthy Samsung SSD, got {health}"

    @patch('smart_parsing.run_command')
    @patch('smart_parsing.get_command_path')
    def test_intel_ssd_realloc_scoring(self, mock_get_command_path, mock_run_command):
        """Test Intel SSD with 8 reallocated sectors and 93% wear scores 60-75."""
        from smart_parsing import get_smart_data, calculate_drive_health_score

        with open('tests/fixtures/smart/sata_ssd_intel_realloc.json', 'r') as f:
            fixture_data = json.load(f)

        mock_get_command_path.return_value = "/usr/bin/smartctl"
        mock_run_command.return_value = json.dumps(fixture_data)

        smart = get_smart_data("/dev/sda")
        health, _ = calculate_drive_health_score("sata", smart, None)

        # 93% wear (7% remaining), 8 reallocated sectors
        # Wear penalty: 93 (since > 80), realloc penalty: 8 * 5 = 40
        # But SSD with spare reserve may mitigate realloc penalty
        assert 60 <= health <= 75, f"Expected health 60-75 for Intel SSD with reallocs, got {health}"


class TestSASRecommendationWithFixtures:
    """Test SAS drive recommendations using real fixture data."""

    @patch('smart_parsing.run_command')
    @patch('smart_parsing.get_command_path')
    def test_sas_dead_drive_recommendation(self, mock_get_command_path, mock_run_command):
        """Test dead SAS drive is recommended DESTROY."""
        from smart_parsing import get_smart_data, calculate_drive_health_score, get_drive_recommendation

        with open('tests/fixtures/smart/sas_dead_drive.json', 'r') as f:
            fixture_data = json.load(f)

        mock_get_command_path.return_value = "/usr/bin/smartctl"
        mock_run_command.return_value = json.dumps(fixture_data)

        smart = get_smart_data("/dev/sda")
        health, _ = calculate_drive_health_score("sas", smart, None)
        recommendation = get_drive_recommendation("sas", smart, health)

        assert recommendation["status"] == "DESTROY"

    @patch('smart_parsing.run_command')
    @patch('smart_parsing.get_command_path')
    def test_sas_healthy_drive_recommendation(self, mock_get_command_path, mock_run_command):
        """Test healthy SAS drive is recommended SCRATCH."""
        from smart_parsing import get_smart_data, calculate_drive_health_score, get_drive_recommendation

        with open('tests/fixtures/smart/sas_healthy_drive.json', 'r') as f:
            fixture_data = json.load(f)

        mock_get_command_path.return_value = "/usr/bin/smartctl"
        mock_run_command.return_value = json.dumps(fixture_data)

        smart = get_smart_data("/dev/sda")
        health, _ = calculate_drive_health_score("sas", smart, None)
        recommendation = get_drive_recommendation("sas", smart, health)

        assert recommendation["status"] == "SCRATCH"

    @patch('smart_parsing.run_command')
    @patch('smart_parsing.get_command_path')
    def test_sas_uncorrectable_verify_errors_recommendation(self, mock_get_command_path, mock_run_command):
        """Test SAS with verify errors is recommended DESTROY."""
        from smart_parsing import get_smart_data, calculate_drive_health_score, get_drive_recommendation

        with open('tests/fixtures/smart/sas_uncorrectable_errors.json', 'r') as f:
            fixture_data = json.load(f)

        mock_get_command_path.return_value = "/usr/bin/smartctl"
        mock_run_command.return_value = json.dumps(fixture_data)

        smart = get_smart_data("/dev/sda")
        health, _ = calculate_drive_health_score("sas", smart, None)
        recommendation = get_drive_recommendation("sas", smart, health)

        # Verify errors >= 1 should trigger DESTROY
        assert recommendation["status"] == "DESTROY"

    @patch('smart_parsing.run_command')
    @patch('smart_parsing.get_command_path')
    def test_sas_sticky_lba_recommendation(self, mock_get_command_path, mock_run_command):
        """Test SAS with sticky LBA is recommended at least SCRATCH."""
        from smart_parsing import get_smart_data, calculate_drive_health_score, get_drive_recommendation

        with open('tests/fixtures/smart/sas_sticky_lba.json', 'r') as f:
            fixture_data = json.load(f)

        mock_get_command_path.return_value = "/usr/bin/smartctl"
        mock_run_command.return_value = json.dumps(fixture_data)

        smart = get_smart_data("/dev/sda")
        health, _ = calculate_drive_health_score("sas", smart, None)
        recommendation = get_drive_recommendation("sas", smart, health)

        # Sticky LBA should trigger at least SCRATCH
        assert recommendation["status"] in ["SCRATCH", "DESTROY"]


class TestPreWipeHealthGate:
    """Test pre-wipe health gate functionality."""

    @patch('smart_parsing.get_smart_data')
    @patch('smart_parsing.get_triage_thresholds')
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

    @patch('smart_parsing.get_smart_data')
    @patch('smart_parsing.get_triage_thresholds')
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

    @patch('smart_parsing.get_smart_data')
    @patch('smart_parsing.calculate_drive_health_score')
    @patch('smart_parsing.get_drive_recommendation')
    @patch('smart_parsing.get_triage_thresholds')
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

    @patch('smart_parsing.get_smart_data')
    @patch('smart_parsing.calculate_drive_health_score')
    @patch('smart_parsing.get_drive_recommendation')
    @patch('smart_parsing.get_triage_thresholds')
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

    @patch('smart_parsing.get_smart_data')
    @patch('smart_parsing.calculate_drive_health_score')
    @patch('smart_parsing.get_drive_recommendation')
    @patch('smart_parsing.get_triage_thresholds')
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

    @patch('smart_parsing.get_smart_data')
    @patch('smart_parsing.calculate_drive_health_score')
    @patch('smart_parsing.get_drive_recommendation')
    @patch('smart_parsing.get_triage_thresholds')
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

    @patch('smart_parsing.get_smart_data')
    @patch('smart_parsing.calculate_drive_health_score')
    @patch('smart_parsing.get_drive_recommendation')
    @patch('smart_parsing.get_triage_thresholds')
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

    @patch('smart_parsing.get_smart_data')
    @patch('smart_parsing.calculate_drive_health_score')
    @patch('smart_parsing.get_drive_recommendation')
    @patch('smart_parsing.get_triage_thresholds')
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

    @patch('smart_parsing.get_smart_data')
    @patch('smart_parsing.calculate_drive_health_score')
    @patch('smart_parsing.get_drive_recommendation')
    @patch('smart_parsing.get_triage_thresholds')
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

    @patch('smart_parsing.get_smart_data')
    @patch('smart_parsing.calculate_drive_health_score')
    @patch('smart_parsing.get_drive_recommendation')
    @patch('smart_parsing.get_triage_thresholds')
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
