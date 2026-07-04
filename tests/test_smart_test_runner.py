# Tests for SMART test runner and deep-dive endpoint
import pytest
import sys
import os
import json
import tempfile
import sqlite3
from unittest.mock import patch, MagicMock, Mock
import io

# Add backend to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))


class TestRunSmartTest:
    """Test run_smart_test function."""

    @patch('smart_test_runner.get_command_path')
    @patch('smart_test_runner.validate_device_path')
    @patch('subprocess.run')
    def test_run_smart_test_short(self, mock_subprocess_run, mock_validate, mock_get_command_path):
        """Test running a short SMART test."""
        from smart_parsing import run_smart_test

        mock_validate.return_value = True
        mock_get_command_path.return_value = "/usr/bin/smartctl"
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Self-test started"
        mock_result.stderr = ""
        mock_subprocess_run.return_value = mock_result

        result = run_smart_test("/dev/sda", "short")

        assert result["test_type"] == "short"
        assert result["status"] == "started"
        assert result["estimated_minutes"] == 2
        assert "poll_command" in result

    @patch('smart_test_runner.get_command_path')
    @patch('smart_test_runner.validate_device_path')
    @patch('subprocess.run')
    def test_run_smart_test_extended(self, mock_subprocess_run, mock_validate, mock_get_command_path):
        """Test running an extended SMART test (normalized to 'long' for smartctl)."""
        from smart_parsing import run_smart_test

        mock_validate.return_value = True
        mock_get_command_path.return_value = "/usr/bin/smartctl"
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Self-test started"
        mock_result.stderr = ""
        mock_subprocess_run.return_value = mock_result

        result = run_smart_test("/dev/sda", "extended")

        assert result["test_type"] == "long"  # Normalized to smartctl's expected value
        assert result["status"] == "started"
        assert result["estimated_minutes"] == 120

    @patch('smart_test_runner.get_command_path')
    @patch('smart_test_runner.validate_device_path')
    @patch('subprocess.run')
    def test_run_smart_test_extended_alias(self, mock_subprocess_run, mock_validate, mock_get_command_path):
        """Test that 'extended' is aliased to 'long' for smartctl compatibility."""
        from smart_parsing import run_smart_test

        mock_validate.return_value = True
        mock_get_command_path.return_value = "/usr/bin/smartctl"
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Self-test started"
        mock_result.stderr = ""
        mock_subprocess_run.return_value = mock_result

        result = run_smart_test("/dev/sda", "extended")

        assert result["test_type"] == "long"  # Should be normalized to smartctl's expected value
        assert result["status"] == "started"

    @patch('smart_test_runner.validate_device_path')
    def test_run_smart_test_invalid_device(self, mock_validate):
        """Test that invalid device path is rejected."""
        from smart_parsing import run_smart_test

        mock_validate.return_value = False

        result = run_smart_test("/etc/passwd", "short")

        assert result["status"] == "failed"
        assert "error" in result

    @patch('smart_test_runner.get_command_path')
    @patch('smart_test_runner.validate_device_path')
    def test_run_smart_test_invalid_test_type(self, mock_validate, mock_get_command_path):
        """Test that invalid test type is rejected."""
        from smart_parsing import run_smart_test

        mock_validate.return_value = True
        mock_get_command_path.return_value = "/usr/bin/smartctl"

        result = run_smart_test("/dev/sda", "invalid_type")

        assert result["status"] == "failed"
        assert "Invalid test type" in result["error"]

    @patch('smart_test_runner.get_command_path')
    @patch('smart_test_runner.validate_device_path')
    def test_run_smart_test_smartctl_not_found(self, mock_validate, mock_get_command_path):
        """Test that missing smartctl is handled."""
        from smart_parsing import run_smart_test

        mock_validate.return_value = True
        mock_get_command_path.return_value = None

        result = run_smart_test("/dev/sda", "short")

        assert result["status"] == "failed"
        assert "smartctl command not found" in result["error"]


class TestGetSmartTestStatus:
    """Test get_smart_test_status function."""

    @patch('smart_test_runner.get_command_path')
    @patch('smart_test_runner.run_command')
    @patch('smart_test_runner.validate_device_path')
    def test_get_smart_test_status_in_progress(self, mock_validate, mock_run_command, mock_get_command_path):
        """Test getting status of in-progress test."""
        from smart_parsing import get_smart_test_status

        mock_validate.return_value = True
        mock_get_command_path.return_value = "/usr/bin/smartctl"
        mock_run_command.return_value = json.dumps({
            "ata_smart_self_test_log": {
                "standard": {
                    "revision": 1,
                    "table": [
                        {
                            "type": {"string": "Short"},
                            "status": {"string": "Self-test in progress", "remaining_percent": 45}
                        }
                    ]
                }
            }
        })

        result = get_smart_test_status("/dev/sda")

        assert result["status"] == "in_progress"
        assert result["percentage"] == 50.0  # (90 - 45) / 90 * 100

    @patch('smart_test_runner.get_command_path')
    @patch('smart_test_runner.run_command')
    @patch('smart_test_runner.validate_device_path')
    def test_get_smart_test_status_completed(self, mock_validate, mock_run_command, mock_get_command_path):
        """Test getting status of completed test."""
        from smart_parsing import get_smart_test_status

        mock_validate.return_value = True
        mock_get_command_path.return_value = "/usr/bin/smartctl"
        mock_run_command.return_value = json.dumps({
            "ata_smart_self_test_log": {
                "standard": {
                    "revision": 1,
                    "table": [
                        {
                            "type": {"string": "Short"},
                            "status": {"string": "Completed without error", "passed": True, "remaining_percent": 0},
                            "lba": 0,
                            "lifetime_hours": 100
                        }
                    ]
                }
            }
        })

        result = get_smart_test_status("/dev/sda")

        assert result["status"] == "completed"
        assert result["percentage"] == 0
        assert result["latest_result"]["status"] == "Completed without error"

    @patch('smart_test_runner.get_command_path')
    @patch('smart_test_runner.run_command')
    @patch('smart_test_runner.validate_device_path')
    def test_get_smart_test_status_failed(self, mock_validate, mock_run_command, mock_get_command_path):
        """Test getting status of failed test."""
        from smart_parsing import get_smart_test_status

        mock_validate.return_value = True
        mock_get_command_path.return_value = "/usr/bin/smartctl"
        mock_run_command.return_value = json.dumps({
            "ata_smart_self_test_log": {
                "standard": {
                    "revision": 1,
                    "table": [
                        {
                            "type": {"string": "Short"},
                            "status": {"string": "Completed: read failure", "passed": False, "remaining_percent": 0},
                            "lba": 123456,
                            "lifetime_hours": 100
                        }
                    ]
                }
            }
        })

        result = get_smart_test_status("/dev/sda")

        assert result["status"] == "failed"
        assert result["latest_result"]["lba"] == 123456

    @patch('smart_test_runner.get_command_path')
    @patch('smart_test_runner.run_command')
    @patch('smart_test_runner.validate_device_path')
    def test_get_smart_test_status_extended_table(self, mock_validate, mock_run_command, mock_get_command_path):
        """Test getting status when self-test log is under extended.table (smartctl 7.x)."""
        from smart_parsing import get_smart_test_status

        mock_validate.return_value = True
        mock_get_command_path.return_value = "/usr/bin/smartctl"
        mock_run_command.return_value = json.dumps({
            "ata_smart_self_test_log": {
                "extended": {
                    "revision": 1,
                    "sectors": 1,
                    "table": [
                        {
                            "type": {"value": 1, "string": "Short offline"},
                            "status": {"value": 0, "string": "Completed without error", "passed": True},
                            "lifetime_hours": 244
                        }
                    ],
                    "count": 1,
                    "error_count_total": 0,
                    "error_count_outdated": 0
                }
            }
        })

        result = get_smart_test_status("/dev/sda")

        assert result["status"] == "completed"
        assert result["latest_result"]["status"] == "Completed without error"
        assert result["latest_result"]["passed"] is True
        assert result["latest_result"]["hours"] == 244
        assert len(result["self_test_log_table"]) == 1

    @patch('smart_test_runner.get_command_path')
    @patch('smart_test_runner.run_command')
    @patch('smart_test_runner.validate_device_path')
    def test_get_smart_test_status_standard_table_from_selftest_flag(self, mock_validate, mock_run_command, mock_get_command_path):
        """Test parsing standard.table format from 'smartctl -j -l selftest' (the polling command).
        
        This is the exact JSON structure returned by smartctl 7.5 when using -l selftest.
        This was the root cause of the test-never-completes bug: the code only checked
        direct 'table' and 'extended.table' but never 'standard.table'.
        """
        from smart_parsing import get_smart_test_status

        mock_validate.return_value = True
        mock_get_command_path.return_value = "/usr/bin/smartctl"
        mock_run_command.return_value = json.dumps({
            "ata_smart_self_test_log": {
                "standard": {
                    "revision": 1,
                    "count": 3,
                    "table": [
                        {
                            "type": {"value": 1, "string": "Short offline"},
                            "status": {"value": 0, "string": "Completed without error", "passed": True, "remaining_percent": 0},
                            "lifetime_hours": 245,
                            "lba": 0
                        },
                        {
                            "type": {"value": 1, "string": "Short offline"},
                            "status": {"value": 0, "string": "Completed without error", "passed": True, "remaining_percent": 0},
                            "lifetime_hours": 244,
                            "lba": 0
                        }
                    ],
                    "error_count_total": 0,
                    "error_count_outdated": 0
                }
            }
        })

        result = get_smart_test_status("/dev/sda")

        assert result["status"] == "completed"
        assert result["latest_result"]["status"] == "Completed without error"
        assert result["latest_result"]["passed"] is True
        assert result["latest_result"]["hours"] == 245
        assert len(result["self_test_log_table"]) == 2

    @patch('smart_test_runner.validate_device_path')
    def test_get_smart_test_status_invalid_device(self, mock_validate):
        """Test that invalid device path is rejected."""
        from smart_parsing import get_smart_test_status

        mock_validate.return_value = False

        result = get_smart_test_status("/etc/passwd")

        assert result["status"] == "failed"
        assert "error" in result


class TestSmartTestDatabaseFunctions:
    """Test SMART test database functions."""

    @patch('database.get_db_path')
    def test_record_smart_test_run(self, mock_get_db_path):
        """Test recording a SMART test run."""
        from database import record_smart_test_run

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            mock_get_db_path.return_value = db_path

            # Initialize database
            with sqlite3.connect(db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS smart_test_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        device TEXT NOT NULL,
                        serial TEXT,
                        test_type TEXT NOT NULL,
                        started_at TEXT NOT NULL,
                        finished_at TEXT,
                        status TEXT NOT NULL,
                        result TEXT,
                        output_json TEXT,
                        updated_at TEXT NOT NULL
                    )
                """)

            record_id = record_smart_test_run("/dev/sda", "TEST123", "short", "started")

            assert record_id is not None
            assert isinstance(record_id, int)

            # Verify record was inserted
            with sqlite3.connect(db_path) as conn:
                row = conn.execute("SELECT * FROM smart_test_log WHERE id = ?", (record_id,)).fetchone()
                assert row is not None
                assert row[1] == "/dev/sda"
                assert row[2] == "TEST123"
                assert row[3] == "short"
                assert row[6] == "started"

    @patch('database.get_db_path')
    def test_record_smart_test_run_invalid_device(self, mock_get_db_path):
        """Test that invalid device path is rejected."""
        from database import record_smart_test_run

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            mock_get_db_path.return_value = db_path

            record_id = record_smart_test_run("/etc/passwd", "TEST123", "short", "started")

            assert record_id is None

    @patch('database.get_db_path')
    def test_update_smart_test_run(self, mock_get_db_path):
        """Test updating a SMART test run."""
        from database import record_smart_test_run, update_smart_test_run

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            mock_get_db_path.return_value = db_path

            # Initialize database
            with sqlite3.connect(db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS smart_test_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        device TEXT NOT NULL,
                        serial TEXT,
                        test_type TEXT NOT NULL,
                        started_at TEXT NOT NULL,
                        finished_at TEXT,
                        status TEXT NOT NULL,
                        result TEXT,
                        output_json TEXT,
                        updated_at TEXT NOT NULL
                    )
                """)

            record_id = record_smart_test_run("/dev/sda", "TEST123", "short", "started")
            assert record_id is not None

            updated = update_smart_test_run(record_id, "completed", "passed", {"test": "data"})

            assert updated is True

            # Verify record was updated
            with sqlite3.connect(db_path) as conn:
                row = conn.execute("SELECT * FROM smart_test_log WHERE id = ?", (record_id,)).fetchone()
                assert row is not None
                assert row[6] == "completed"
                assert row[7] == "passed"
                assert row[5] is not None  # finished_at should be set

    @patch('database.get_db_path')
    def test_get_smart_test_history(self, mock_get_db_path):
        """Test getting SMART test history."""
        from database import record_smart_test_run, get_smart_test_history

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            mock_get_db_path.return_value = db_path

            # Initialize database
            with sqlite3.connect(db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS smart_test_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        device TEXT NOT NULL,
                        serial TEXT,
                        test_type TEXT NOT NULL,
                        started_at TEXT NOT NULL,
                        finished_at TEXT,
                        status TEXT NOT NULL,
                        result TEXT,
                        output_json TEXT,
                        updated_at TEXT NOT NULL
                    )
                """)

            # Insert test records
            record_smart_test_run("/dev/sda", "TEST123", "short", "completed", "passed")
            record_smart_test_run("/dev/sda", "TEST123", "extended", "started")

            history = get_smart_test_history(device="/dev/sda", limit=10)

            assert len(history) == 2
            assert history[0]["device"] == "/dev/sda"
            assert history[0]["serial"] == "TEST123"

    @patch('database.get_db_path')
    def test_get_smart_test_history_invalid_device(self, mock_get_db_path):
        """Test that invalid device path is rejected."""
        from database import get_smart_test_history

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            mock_get_db_path.return_value = db_path

            history = get_smart_test_history(device="/etc/passwd")

            assert history == []

    @patch('database.get_db_path')
    def test_get_smart_test_history_limit_enforcement(self, mock_get_db_path):
        """Test that limit parameter is enforced (DoS prevention)."""
        from database import get_smart_test_history

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            mock_get_db_path.return_value = db_path

            # Initialize database
            with sqlite3.connect(db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS smart_test_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        device TEXT NOT NULL,
                        serial TEXT,
                        test_type TEXT NOT NULL,
                        started_at TEXT NOT NULL,
                        finished_at TEXT,
                        status TEXT NOT NULL,
                        result TEXT,
                        output_json TEXT,
                        updated_at TEXT NOT NULL
                    )
                """)

            # Insert many records
            for i in range(100):
                conn.execute(
                    "INSERT INTO smart_test_log (device, serial, test_type, started_at, status, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    ("/dev/sda", "TEST123", "short", "2024-01-01T00:00:00Z", "completed", "2024-01-01T00:00:00Z")
                )
            conn.commit()

            # Request with limit
            history = get_smart_test_history(device="/dev/sda", limit=10)

            assert len(history) == 10

            # Request with excessive limit (should be capped)
            history = get_smart_test_history(device="/dev/sda", limit=5000)

            assert len(history) <= 1000  # Should be capped at 1000


class TestSmartDetailsEndpoint:
    """Test smart-details endpoint."""

    @patch('smart_parsing.get_smart_data')
    @patch('routes.admin_routes.is_valid_device_name')
    def test_smart_details_endpoint_basic(self, mock_is_valid, mock_get_smart_data):
        """Test basic smart-details endpoint."""
        from routes.admin_routes import get_smart_details
        from flask import Flask

        app = Flask(__name__)
        app.config['TESTING'] = True

        mock_is_valid.return_value = True
        mock_get_smart_data.return_value = {
            "serial": "TEST123",
            "raw": json.dumps({
                "ata_smart_attributes": {"table": [{"id": 1, "name": "Raw_Read_Error_Rate", "value": 100}]}
            })
        }

        with app.test_request_context():
            response, status_code = get_smart_details("sda")

            assert status_code == 200
            data = response.get_json()
            assert "attributes" in data
            assert len(data["attributes"]) == 1

    @patch('routes.admin_routes.is_valid_device_name')
    def test_smart_details_invalid_device(self, mock_is_valid):
        """Test that invalid device name is rejected."""
        from routes.admin_routes import get_smart_details
        from flask import Flask

        app = Flask(__name__)
        app.config['TESTING'] = True

        mock_is_valid.return_value = False

        with app.test_request_context():
            response, status_code = get_smart_details("../../../etc/passwd")

            assert status_code == 400

    @patch('smart_parsing.get_smart_data')
    @patch('routes.admin_routes.is_valid_device_name')
    def test_smart_details_size_limits(self, mock_is_valid, mock_get_smart_data):
        """Test that size limits are enforced (DoS prevention)."""
        from routes.admin_routes import get_smart_details
        from flask import Flask

        app = Flask(__name__)
        app.config['TESTING'] = True

        mock_is_valid.return_value = True

        # Create a large attributes table
        large_attrs = [{"id": i, "name": f"Attr{i}", "value": 100} for i in range(200)]
        mock_get_smart_data.return_value = {
            "serial": "TEST123",
            "raw": json.dumps({
                "ata_smart_attributes": {"table": large_attrs}
            })
        }

        with app.test_request_context():
            response, status_code = get_smart_details("sda")

            assert status_code == 200
            data = response.get_json()
            # Should be truncated to MAX_ATTRIBUTES (100)
            assert len(data["attributes"]) == 100
            assert data["truncated"] is True

    @patch('smart_parsing.get_smart_data')
    @patch('routes.admin_routes.is_valid_device_name')
    def test_smart_details_extended_self_test_log(self, mock_is_valid, mock_get_smart_data):
        """Test that smart-details endpoint reads self-test log from extended.table."""
        from routes.admin_routes import get_smart_details
        from flask import Flask

        app = Flask(__name__)
        app.config['TESTING'] = True

        mock_is_valid.return_value = True
        mock_get_smart_data.return_value = {
            "serial": "TEST123",
            "power_on_hours": 65781,
            "raw": json.dumps({
                "ata_smart_self_test_log": {
                    "extended": {
                        "revision": 1,
                        "sectors": 1,
                        "table": [
                            {
                                "type": {"value": 1, "string": "Short offline"},
                                "status": {"value": 0, "string": "Completed without error", "passed": True},
                                "lifetime_hours": 244
                            }
                        ],
                        "count": 1,
                        "error_count_total": 0,
                        "error_count_outdated": 0
                    }
                }
            })
        }

        with app.test_request_context():
            response, status_code = get_smart_details("sda")

            assert status_code == 200
            data = response.get_json()
            assert len(data["self_test_logs"]) == 1
            assert data["self_test_logs"][0]["status"] == "Completed without error"
            assert data["self_test_logs"][0]["passed"] is True
            assert data["self_test_logs"][0]["hours"] == 244


class TestSmartExportEndpoint:
    """Test smart-export endpoint."""

    @patch('routes.admin_routes.ERASE_JOBS_LOCK')
    @patch('routes.admin_routes.is_valid_device_name')
    @patch('smart_parsing.get_smart_data')
    def test_smart_export_endpoint(self, mock_get_smart_data, mock_is_valid, mock_jobs_lock):
        """Test basic smart-export endpoint."""
        from routes.admin_routes import export_smart_data, ERASE_JOBS
        from flask import Flask

        app = Flask(__name__)
        app.config['TESTING'] = True

        mock_is_valid.return_value = True
        mock_jobs_lock.__enter__ = Mock()
        mock_jobs_lock.__exit__ = Mock()
        mock_get_smart_data.return_value = {
            "serial": "TEST123",
            "model": "Test Drive",
            "raw": "{}"
        }

        # Register the route with the test app
        @app.route('/api/admin/drives/<device>/smart-export')
        def test_route(device):
            return export_smart_data(device)

        # Clear ERASE_JOBS to avoid 409 conflict
        original_jobs = ERASE_JOBS.copy()
        ERASE_JOBS.clear()
        try:
            with app.test_client() as client:
                response = client.get('/api/admin/drives/sda/smart-export')

                assert response.status_code == 200
                # Response should be a file-like object
                assert hasattr(response, "get_data")
        finally:
            ERASE_JOBS.update(original_jobs)

    @patch('routes.admin_routes.is_valid_device_name')
    def test_smart_export_invalid_device(self, mock_is_valid):
        """Test that invalid device name is rejected."""
        from routes.admin_routes import export_smart_data
        from flask import Flask

        app = Flask(__name__)
        app.config['TESTING'] = True

        mock_is_valid.return_value = False

        with app.test_request_context():
            response, status_code = export_smart_data("../../../etc/passwd")

            assert status_code == 400


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
