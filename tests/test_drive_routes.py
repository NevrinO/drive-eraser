# Integration tests for drive routes
import pytest
import sys
import os
import json
import tempfile
from unittest.mock import patch, MagicMock

# Add backend to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))


class TestDriveRoutes:
    """Integration tests for drive endpoints."""

    @pytest.fixture
    def test_config_dir(self):
        """Create a temporary directory for test configuration."""
        with tempfile.TemporaryDirectory() as tmpdir:
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
        patches = [
            patch('common.get_config_dir', return_value=test_config_dir),
            patch('common.get_data_dir', return_value=test_config_dir),
            patch('common.get_logs_dir', return_value=test_config_dir),
            patch('common.get_db_path', return_value=test_db_path),
            patch('api_routes.get_config_dir', return_value=test_config_dir),
            patch('api_routes.get_db_path', return_value=test_db_path),
            patch('database.get_db_path', return_value=test_db_path),
            patch('database.get_cert_dir', return_value=test_config_dir),
            patch('routes._shared.get_config_dir', return_value=test_config_dir),
            patch('routes.drive_routes.get_config_dir', return_value=test_config_dir),
        ]
        for p in patches:
            p.start()
        try:
            from database import init_wipe_db
            init_wipe_db()
            from flask import Flask
            from app_config import logger, calculate_session_token, limiter
            app = Flask(__name__)
            app.config['TESTING'] = True
            limiter.init_app(app)
            import api_routes
            from routes import drive_routes
            drive_bp = getattr(drive_routes, 'drive_bp', None)
            if drive_bp:
                app.register_blueprint(drive_bp)
            # Register api_routes module routes (e.g., /api/auth/verify)
            api_routes.register_routes(app)
            yield app
        finally:
            for p in patches:
                p.stop()

    @pytest.fixture
    def client(self, app):
        """Create a test client."""
        return app.test_client()

    def test_get_drives_unauthenticated(self, client):
        """Test that unauthenticated requests return 401."""
        with patch('routes._shared.is_local_request', return_value=False):
            response = client.get('/api/drives')
            assert response.status_code == 401

    def test_get_drives_local_request_allowed(self, client):
        """Test that localhost requests bypass authentication."""
        with patch('routes._shared.is_local_request', return_value=True):
            with patch('routes.drive_routes.discover_drives', return_value=[]):
                with patch('routes.drive_routes.ERASE_JOBS', {}):
                    with patch('routes.drive_routes.ERASE_JOBS_LOCK'):
                        response = client.get('/api/drives')
                        assert response.status_code == 200

    def test_get_drives_authenticated(self, client):
        """Test that authenticated requests return drives."""
        # First authenticate
        response = client.post('/api/auth/verify', json={"passphrase": "test-lan-pass"})
        assert response.status_code == 200
        
        with patch('routes._shared.is_local_request', return_value=False):
            with patch('routes.drive_routes.discover_drives', return_value=[
                {"bay": "bay1", "device": "/dev/sdb"}
            ]):
                with patch('routes.drive_routes.ERASE_JOBS', {}):
                    with patch('routes.drive_routes.ERASE_JOBS_LOCK'):
                        response = client.get('/api/drives')
                        assert response.status_code == 200
                        data = json.loads(response.data)
                        assert isinstance(data, list)

    def test_get_drives_with_running_job(self, client):
        """Test that drives show running job status."""
        # First authenticate
        response = client.post('/api/auth/verify', json={"passphrase": "test-lan-pass"})
        assert response.status_code == 200
        
        with patch('routes._shared.is_local_request', return_value=False):
            with patch('routes.drive_routes.discover_drives', return_value=[
                {"bay": "bay1", "device": "/dev/sdb"}
            ]):
                with patch('routes.drive_routes.ERASE_JOBS', {
                    "job1": {
                        "status": "running",
                        "progress_percent": 50.0,
                        "current_phase": "Sanitizing",
                        "request": {
                            "bay": "bay1",
                            "serial": "ABC123",
                            "model": "TestDrive",
                            "capacity_bytes": 1000000000
                        }
                    }
                }):
                    with patch('routes.drive_routes.ERASE_JOBS_LOCK'):
                        response = client.get('/api/drives')
                        assert response.status_code == 200
                        data = json.loads(response.data)
                        assert len(data) > 0
                        assert data[0].get("status") == "RUNNING"
                        assert data[0].get("progress_percent") == 50.0

    def test_get_drives_with_queued_job(self, client):
        """Test that drives show queued job status."""
        # First authenticate
        response = client.post('/api/auth/verify', json={"passphrase": "test-lan-pass"})
        assert response.status_code == 200
        
        with patch('routes._shared.is_local_request', return_value=False):
            with patch('routes.drive_routes.discover_drives', return_value=[
                {"bay": "bay1", "device": "/dev/sdb"}
            ]):
                with patch('routes.drive_routes.ERASE_JOBS', {
                    "job1": {
                        "status": "queued",
                        "request": {"bay": "bay1"}
                    }
                }):
                    with patch('routes.drive_routes.ERASE_JOBS_LOCK'):
                        response = client.get('/api/drives')
                        assert response.status_code == 200
                        data = json.loads(response.data)
                        assert data[0].get("status") == "QUEUED"

    def test_get_status_no_passphrase(self, client):
        """Test status endpoint when passphrase is not configured."""
        with patch('routes.drive_routes.load_policy') as mock_load:
            mock_load.return_value = {"wipe_passphrase": ""}
            response = client.get('/api/status')
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["passphrase_enabled"] is False

    def test_get_status_with_default_passphrase(self, client):
        """Test status endpoint with default placeholder passphrase."""
        with patch('routes.drive_routes.load_policy') as mock_load:
            mock_load.return_value = {
                "wipe_passphrase": "your_secure_shared_secret_passphrase_here"
            }
            response = client.get('/api/status')
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["passphrase_enabled"] is False

    def test_get_status_with_custom_passphrase(self, client):
        """Test status endpoint with custom passphrase."""
        with patch('routes.drive_routes.load_policy') as mock_load:
            mock_load.return_value = {
                "wipe_passphrase": "my-secure-passphrase"
            }
            response = client.get('/api/status')
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["passphrase_enabled"] is True

    def test_get_status_with_whitespace_passphrase(self, client):
        """Test status endpoint with whitespace-only passphrase."""
        with patch('routes.drive_routes.load_policy') as mock_load:
            mock_load.return_value = {
                "wipe_passphrase": "   "
            }
            response = client.get('/api/status')
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["passphrase_enabled"] is False

    def test_get_status_error_handling(self, client):
        """Test status endpoint error handling."""
        with patch('routes.drive_routes.load_policy', side_effect=Exception("Config error")):
            response = client.get('/api/status')
            assert response.status_code == 500
            data = json.loads(response.data)
            assert "error" in data

    def test_start_zero_check_success(self, client):
        """Test manual zero-check succeeds for an eligible drive."""
        response = client.post('/api/auth/verify', json={"passphrase": "test-lan-pass"})
        assert response.status_code == 200

        fake_drive = {"bay": "bay1", "present": True, "device": "/dev/sdb"}
        fake_manager = MagicMock()
        fake_manager.start_check.return_value = {"status": "queued"}

        with patch('routes.drive_routes._resolve_drive_for_bay', return_value=fake_drive):
            with patch('routes.drive_routes.load_policy', return_value={"prewipe_zero_detection_enabled": True}):
                with patch('routes.drive_routes._is_eligible_for_zero_check', return_value=(True, "")):
                    with patch('routes.drive_routes.get_zero_check_manager', return_value=fake_manager):
                        response = client.post('/api/drives/bay1/zero-check')
                        assert response.status_code == 200
                        data = json.loads(response.data)
                        assert data["status"] == "success"
                        fake_manager.start_check.assert_called_once_with("bay1", "/dev/sdb", serial=None)

    def test_start_zero_check_rejects_ineligible(self, client):
        """Test manual zero-check rejects drives that fail the shared eligibility filter."""
        response = client.post('/api/auth/verify', json={"passphrase": "test-lan-pass"})
        assert response.status_code == 200

        fake_drive = {"bay": "bay1", "present": True, "device": "/dev/sdb"}

        with patch('routes.drive_routes._resolve_drive_for_bay', return_value=fake_drive):
            with patch('routes.drive_routes.load_policy', return_value={"prewipe_zero_detection_enabled": True}):
                with patch('routes.drive_routes._is_eligible_for_zero_check', return_value=(False, "drive is locked or busy")):
                    with patch('routes.drive_routes.get_zero_check_manager'):
                        response = client.post('/api/drives/bay1/zero-check')
                        assert response.status_code == 409
                        data = json.loads(response.data)
                        assert "drive is locked or busy" in data["error"]

    def test_start_zero_check_disabled_by_policy(self, client):
        """Test manual zero-check returns 403 when disabled by policy."""
        response = client.post('/api/auth/verify', json={"passphrase": "test-lan-pass"})
        assert response.status_code == 200

        fake_drive = {"bay": "bay1", "present": True, "device": "/dev/sdb"}

        with patch('routes.drive_routes._resolve_drive_for_bay', return_value=fake_drive):
            with patch('routes.drive_routes.load_policy', return_value={"prewipe_zero_detection_enabled": False}):
                response = client.post('/api/drives/bay1/zero-check')
                assert response.status_code == 403

    def test_start_zero_check_allows_recheck_after_completion(self, client):
        """Test manual zero-check succeeds even when a previous check is completed."""
        response = client.post('/api/auth/verify', json={"passphrase": "test-lan-pass"})
        assert response.status_code == 200

        fake_drive = {"bay": "bay1", "present": True, "device": "/dev/sdb"}
        fake_manager = MagicMock()
        fake_manager.get_status.return_value = {"status": "completed", "result": "zeroed"}
        fake_manager.start_check.return_value = {"status": "queued"}

        with patch('routes.drive_routes._resolve_drive_for_bay', return_value=fake_drive):
            with patch('routes.drive_routes.load_policy', return_value={"prewipe_zero_detection_enabled": True}):
                with patch('routes.drive_routes._is_eligible_for_zero_check', return_value=(True, None)) as mock_elig:
                    with patch('routes.drive_routes.get_zero_check_manager', return_value=fake_manager):
                        response = client.post('/api/drives/bay1/zero-check')
                        assert response.status_code == 200
                        data = json.loads(response.data)
                        assert data["status"] == "success"
                        # Verify allow_completed=True was passed
                        assert mock_elig.call_args.kwargs.get("allow_completed") is True

    def test_start_zero_check_blocked_for_running_wipe(self, client):
        """Test manual zero-check is blocked when a wipe is running on the drive."""
        response = client.post('/api/auth/verify', json={"passphrase": "test-lan-pass"})
        assert response.status_code == 200

        # _resolve_drive_for_bay should return a drive with status=RUNNING when
        # the device is in ERASE_JOBS as running. The eligibility filter should
        # then block the zero-check.
        fake_drive = {"bay": "bay1", "present": True, "device": "/dev/sdb", "status": "RUNNING"}

        with patch('routes.drive_routes._resolve_drive_for_bay', return_value=fake_drive):
            with patch('routes.drive_routes.load_policy', return_value={"prewipe_zero_detection_enabled": True}):
                with patch('routes.drive_routes.get_zero_check_manager'):
                    response = client.post('/api/drives/bay1/zero-check')
                    assert response.status_code == 409
                    data = json.loads(response.data)
                    assert "wipe is running" in data["error"]

    def test_resolve_drive_for_bay_passes_running_devices(self):
        """Test that _resolve_drive_for_bay passes running_devices to discover_drives."""
        from routes import drive_routes

        fake_drives = [{"bay": "bay1", "present": True, "device": "/dev/sdb"}]

        with patch('routes.drive_routes.discover_drives', return_value=fake_drives) as mock_discover:
            with patch('routes.drive_routes.ERASE_JOBS', {
                "job1": {"status": "running", "request": {"device": "/dev/sdc"}}
            }):
                with patch('routes.drive_routes.ERASE_JOBS_LOCK'):
                    with patch('routes.drive_routes.get_config_dir', return_value="/tmp"):
                        result = drive_routes._resolve_drive_for_bay("bay1")
                        assert result is not None
                        assert result["bay"] == "bay1"
                        # Verify running_devices was passed
                        call_kwargs = mock_discover.call_args
                        assert call_kwargs.kwargs.get("running_devices") is not None
                        assert "/dev/sdc" in call_kwargs.kwargs["running_devices"]

    def test_resolve_drive_for_bay_skips_auto_enqueue(self):
        """Test that _resolve_drive_for_bay passes skip_auto_enqueue=True (Advisory 10)."""
        from routes import drive_routes

        fake_drives = [{"bay": "bay1", "present": True, "device": "/dev/sdb"}]

        with patch('routes.drive_routes.discover_drives', return_value=fake_drives) as mock_discover:
            with patch('routes.drive_routes.ERASE_JOBS', {}):
                with patch('routes.drive_routes.ERASE_JOBS_LOCK'):
                    with patch('routes.drive_routes.get_config_dir', return_value="/tmp"):
                        result = drive_routes._resolve_drive_for_bay("bay1")
                        assert result is not None
                        call_kwargs = mock_discover.call_args
                        assert call_kwargs.kwargs.get("skip_auto_enqueue") is True


    def test_validate_bay_rejects_invalid_identifiers(self):
        """Test that _validate_bay rejects invalid bay identifiers (A39)."""
        from routes.drive_routes import _validate_bay
        assert _validate_bay("bay1") is True
        assert _validate_bay("BAY-2") is True
        assert _validate_bay("a") is True
        assert _validate_bay("") is False
        assert _validate_bay(None) is False
        assert _validate_bay(123) is False
        assert _validate_bay("../etc") is False
        assert _validate_bay("bay;rm") is False
        assert _validate_bay("a" * 51) is False  # Exceeds 50 char limit

    def test_start_zero_check_invalid_bay_returns_400(self, client):
        """Test that invalid bay identifier returns 400 on zero-check start (A39)."""
        with patch('routes._shared.is_local_request', return_value=True):
            response = client.post('/api/drives/bay%3Brm/zero-check')
            assert response.status_code == 400

    def test_cancel_zero_check_invalid_bay_returns_400(self, client):
        """Test that invalid bay identifier returns 400 on zero-check cancel (A39)."""
        with patch('routes._shared.is_local_request', return_value=True):
            response = client.delete('/api/drives/bay%3Brm/zero-check')
            assert response.status_code == 400


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
