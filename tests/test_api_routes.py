# Integration tests for API routes
import pytest
import sys
import os
import json
import logging
import tempfile
import threading
from unittest.mock import patch, MagicMock

# Add backend to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))


class TestAPIRoutes:
    """Integration tests for critical API endpoints."""

    @pytest.fixture
    def test_config_dir(self):
        """Create a temporary directory for test configuration."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            # Create test policy.json
            policy = {
                "strict_audit_mode": False,
                "wipe_passphrase": "test-wipe-pass",
                "lan_passphrase": "test-lan-pass",
                "method_priority": {"sata": ["overwrite"]},
                "bind_address": "127.0.0.1",
                "port": 5000
            }
            with open(os.path.join(tmpdir, "policy.json"), "w") as f:
                json.dump(policy, f)
            
            # Create test bay_map.json
            bay_map = {
                "bay1": "/dev/sdb",
                "bay2": "/dev/sdc"
            }
            with open(os.path.join(tmpdir, "bay_map.json"), "w") as f:
                json.dump(bay_map, f)
            
            yield tmpdir

    @pytest.fixture
    def app(self, test_config_dir):
        """Create a test Flask app with test configuration."""
        test_db_path = os.path.join(test_config_dir, "test.db")
        # Patch at all locations where these are bound via from-import
        patches = [
            patch('common.get_config_dir', return_value=test_config_dir),
            patch('common.get_data_dir', return_value=test_config_dir),
            patch('common.get_logs_dir', return_value=test_config_dir),
            patch('common.get_db_path', return_value=test_db_path),
            patch('api_routes.get_config_dir', return_value=test_config_dir),
            patch('api_routes.get_db_path', return_value=test_db_path),
            patch('database.get_db_path', return_value=test_db_path),
            patch('database.get_cert_dir', return_value=test_config_dir),
        ]
        for p in patches:
            p.start()
        try:
            # Initialize the test database
            from database import init_wipe_db
            init_wipe_db()
            from flask import Flask
            from app_config import limiter
            app = Flask(__name__)
            app.config['TESTING'] = True
            limiter.init_app(app)
            import api_routes
            api_routes.register_routes(app)
            yield app
        finally:
            root_logger = logging.getLogger()
            for handler in list(root_logger.handlers):
                if isinstance(handler, logging.FileHandler):
                    try:
                        if os.path.commonpath([handler.baseFilename, test_config_dir]) == test_config_dir:
                            root_logger.removeHandler(handler)
                            handler.close()
                    except (ValueError, OSError):
                        pass
            for p in patches:
                p.stop()

    @pytest.fixture
    def client(self, app):
        """Create a test client."""
        return app.test_client()

    def test_erase_start_confirmation_validation_single_bay(self, client):
        """Test that confirmation text is validated correctly for single bay."""
        # Test incorrect confirmation
        response = client.post('/api/erase/start', 
            json={
                "bays": ["bay1"],
                "confirmation_text": "wrong confirmation",
                "technician": "Test Tech",
                "ticket_number": "TICKET-001"
            })
        assert response.status_code == 400
        data = json.loads(response.data)
        assert "confirmation_text" in data["error"].lower()

    def test_erase_start_confirmation_validation_multiple_bays(self, client):
        """Test that confirmation text is validated correctly for multiple bays."""
        # Test incorrect confirmation for multiple bays
        response = client.post('/api/erase/start',
            json={
                "bays": ["bay1", "bay2"],
                "confirmation_text": "erase bay1",  # Should be "erase 2 drives"
                "technician": "Test Tech",
                "ticket_number": "TICKET-001"
            })
        assert response.status_code == 400

    def test_erase_start_confirmation_uses_display_label(self, client):
        """Test that single-bay confirmation expects the display label (BAY N), not the raw bay id."""
        mocked_drive = {
            "bay": "dell_front_slot_4",
            "display_number": "4",
            "present": True,
            "device": "/dev/sdb",
            "supported_methods": ["overwrite"],
        }
        with patch('api_routes.discover_drives', return_value=[mocked_drive]):
            response = client.post('/api/erase/start',
                json={
                    "bays": ["dell_front_slot_4"],
                    "confirmation_text": "erase dell_front_slot_4",  # old raw-id text
                    "technician": "Test Tech",
                    "ticket_number": "TICKET-001"
                })
            assert response.status_code == 400
            data = json.loads(response.data)
            assert "erase BAY 4" in data["error"]

    def test_erase_start_confirmation_accepts_lowercase_display_label(self, client):
        """Test that the correct lowercase display-label confirmation succeeds."""
        mocked_drive = {
            "bay": "dell_front_slot_4",
            "display_number": "4",
            "present": True,
            "device": "/dev/sdb",
            "supported_methods": ["overwrite"],
        }
        validated = {
            "technician": "Test Tech",
            "ticket_number": "TICKET-001",
            "bay": "dell_front_slot_4",
            "device": "/dev/sdb",
            "method": "overwrite",
            "recommended_method": "overwrite",
            "drive": mocked_drive,
        }
        job = {
            "id": "job-123",
            "friendly_id": "JOB-123",
            "request": validated.copy(),
            "status": "queued",
            "created_at": "2026-06-26T14:00:00Z",
        }
        mock_semaphore = MagicMock()
        mock_semaphore.acquire = MagicMock()
        mock_semaphore.release = MagicMock()

        with patch('api_routes.discover_drives', return_value=[mocked_drive]), \
             patch('api_routes.validate_single_bay', return_value=(validated, None, None)), \
             patch('api_routes.check_health_gate_sync', return_value={"blocked": False, "health_gate_result": {"blocked": False}}), \
             patch('api_routes.create_erase_job', return_value=job), \
             patch('api_routes.get_wipe_semaphore', return_value=mock_semaphore), \
             patch('api_routes.run_erase_job'), \
             patch('api_routes.persist_job'):
            response = client.post('/api/erase/start',
                json={
                    "bays": ["dell_front_slot_4"],
                    "confirmation_text": "erase bay 4",
                    "technician": "Test Tech",
                    "ticket_number": "TICKET-001"
                })
            assert response.status_code == 202
            data = json.loads(response.data)
            assert "jobs" in data
            assert len(data["jobs"]) == 1

    def test_erase_start_confirmation_validation_missing(self, client):
        """Test that missing confirmation returns 400."""
        response = client.post('/api/erase/start',
            json={
                "bays": ["bay1"],
                "technician": "Test Tech",
                "ticket_number": "TICKET-001"
            })
        assert response.status_code == 400

    def test_erase_start_health_gate_blocked_returns_all_failures(self, client):
        """Test that health gate failure returns ALL blocked drives with bay/serial info."""
        mocked_drive_1 = {
            "bay": "bay1",
            "display_number": "1",
            "present": True,
            "device": "/dev/sdb",
            "serial": "SN111",
            "model": "ModelA",
            "interface_type": "sata",
            "supported_methods": ["overwrite"],
        }
        mocked_drive_2 = {
            "bay": "bay2",
            "display_number": "2",
            "present": True,
            "device": "/dev/sdc",
            "serial": "SN222",
            "model": "ModelB",
            "interface_type": "sata",
            "supported_methods": ["overwrite"],
        }
        validated_1 = {
            "technician": "Test Tech",
            "ticket_number": "TICKET-001",
            "bay": "bay1",
            "device": "/dev/sdb",
            "method": "overwrite",
            "recommended_method": "overwrite",
            "drive": mocked_drive_1,
        }
        validated_2 = {
            "technician": "Test Tech",
            "ticket_number": "TICKET-001",
            "bay": "bay2",
            "device": "/dev/sdc",
            "method": "overwrite",
            "recommended_method": "overwrite",
            "drive": mocked_drive_2,
        }

        def mock_validate(tech, ticket, bay, method_override, drives, policy):
            if bay == "bay1":
                return (validated_1, None, None)
            return (validated_2, None, None)

        call_count = []
        call_lock = threading.Lock()
        def mock_health_gate(device, interface_type, policy, health_gate_override=False):
            with call_lock:
                call_count.append(device)
            if device == "/dev/sdb":
                return {"blocked": True, "error_code": "pre_wipe_health_check_failed",
                        "block_reason": "smart_status_failed", "override_available": True,
                        "health_gate_result": {"blocked": True, "block_reason": "smart_status_failed"}}
            return {"blocked": False, "health_gate_result": {"blocked": False}}

        with patch('api_routes.discover_drives', return_value=[mocked_drive_1, mocked_drive_2]), \
             patch('api_routes.validate_single_bay', side_effect=mock_validate), \
             patch('api_routes.check_health_gate_sync', side_effect=mock_health_gate):
            response = client.post('/api/erase/start',
                json={
                    "bays": ["bay1", "bay2"],
                    "confirmation_text": "erase 2 drives",
                    "technician": "Test Tech",
                    "ticket_number": "TICKET-001"
                })
            assert response.status_code == 400
            data = json.loads(response.data)
            assert data["error_code"] == "pre_wipe_health_check_failed"
            assert "blocked_drives" in data
            assert len(data["blocked_drives"]) == 1
            assert data["blocked_drives"][0]["bay"] == "bay1"
            assert data["blocked_drives"][0]["serial"] == "SN111"
            assert data["blocked_drives"][0]["block_reason"] == "smart_status_failed"
            assert "passing_bays" in data
            assert "bay2" in data["passing_bays"]
            assert data["override_available"] is True
            assert len(call_count) == 2

    def test_erase_start_health_gate_all_drives_blocked(self, client):
        """Test that health gate returns empty passing_bays when all drives are blocked."""
        mocked_drive_1 = {
            "bay": "bay1",
            "display_number": "1",
            "present": True,
            "device": "/dev/sdb",
            "serial": "SN111",
            "model": "ModelA",
            "interface_type": "sata",
            "supported_methods": ["overwrite"],
        }
        mocked_drive_2 = {
            "bay": "bay2",
            "display_number": "2",
            "present": True,
            "device": "/dev/sdc",
            "serial": "SN222",
            "model": "ModelB",
            "interface_type": "sata",
            "supported_methods": ["overwrite"],
        }
        validated_1 = {
            "technician": "Test Tech",
            "ticket_number": "TICKET-001",
            "bay": "bay1",
            "device": "/dev/sdb",
            "method": "overwrite",
            "recommended_method": "overwrite",
            "drive": mocked_drive_1,
        }
        validated_2 = {
            "technician": "Test Tech",
            "ticket_number": "TICKET-001",
            "bay": "bay2",
            "device": "/dev/sdc",
            "method": "overwrite",
            "recommended_method": "overwrite",
            "drive": mocked_drive_2,
        }

        def mock_validate(tech, ticket, bay, method_override, drives, policy):
            if bay == "bay1":
                return (validated_1, None, None)
            return (validated_2, None, None)

        def mock_health_gate(device, interface_type, policy, health_gate_override=False):
            return {"blocked": True, "error_code": "pre_wipe_health_check_failed",
                    "block_reason": "smart_status_failed", "override_available": True,
                    "health_gate_result": {"blocked": True, "block_reason": "smart_status_failed"}}

        with patch('api_routes.discover_drives', return_value=[mocked_drive_1, mocked_drive_2]), \
             patch('api_routes.validate_single_bay', side_effect=mock_validate), \
             patch('api_routes.check_health_gate_sync', side_effect=mock_health_gate):
            response = client.post('/api/erase/start',
                json={
                    "bays": ["bay1", "bay2"],
                    "confirmation_text": "erase 2 drives",
                    "technician": "Test Tech",
                    "ticket_number": "TICKET-001"
                })
            assert response.status_code == 400
            data = json.loads(response.data)
            assert data["error_code"] == "pre_wipe_health_check_failed"
            assert len(data["blocked_drives"]) == 2
            assert data["passing_bays"] == []
            assert data["override_available"] is True

    def test_erase_start_requires_bays_list(self, client):
        """Test that bays list is required."""
        response = client.post('/api/erase/start',
            json={
                "technician": "Test Tech",
                "ticket_number": "TICKET-001"
            })
        assert response.status_code == 400
        data = json.loads(response.data)
        assert "bays" in data["error"].lower()

    def test_history_endpoint_limit_validation_too_high(self, client):
        """Test that limit > 500 returns 400 (Lesson #5)."""
        response = client.get('/api/erase/history?limit=501')
        assert response.status_code == 400
        data = json.loads(response.data)
        assert "limit" in data["error"].lower()

    def test_history_endpoint_limit_validation_too_low(self, client):
        """Test that limit < 1 returns 400 (Lesson #5)."""
        response = client.get('/api/erase/history?limit=0')
        assert response.status_code == 400

    def test_history_endpoint_limit_validation_invalid(self, client):
        """Test that non-integer limit returns 400 (Lesson #5)."""
        response = client.get('/api/erase/history?limit=abc')
        assert response.status_code == 400

    def test_history_endpoint_limit_valid(self, client):
        """Test that valid limit returns 200."""
        response = client.get('/api/erase/history?limit=10')
        # Should return 200 even if no jobs exist
        assert response.status_code == 200
        data = json.loads(response.data)
        assert "jobs" in data

    def test_job_status_endpoint_not_found(self, client):
        """Test that GET /api/erase/jobs/<invalid_job_id> returns 404."""
        response = client.get('/api/erase/jobs/nonexistent-job-id')
        assert response.status_code == 404
        data = json.loads(response.data)
        assert "not found" in data["error"].lower()

    def test_job_cancel_endpoint_not_found(self, client):
        """Test that POST to cancel non-existent job returns 404."""
        response = client.post('/api/erase/jobs/nonexistent-job-id/cancel')
        assert response.status_code == 404

    def test_auth_verify_endpoint_invalid_passphrase(self, client):
        """Test that invalid passphrase returns 401."""
        response = client.post('/api/auth/verify',
            json={"passphrase": "wrong-passphrase"})
        assert response.status_code == 401
        data = json.loads(response.data)
        assert "invalid" in data["error"].lower()

    def test_auth_verify_endpoint_valid_passphrase(self, client):
        """Test that valid passphrase returns 200 and sets cookie."""
        response = client.post('/api/auth/verify',
            json={"passphrase": "test-lan-pass"})
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "authenticated"
        # Check that session cookie is set
        # Werkzeug 3.x: use response.headers to check Set-Cookie
        set_cookie_headers = [v for k, v in response.headers if k.lower() == 'set-cookie']
        assert any('admin_session' in c for c in set_cookie_headers)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
