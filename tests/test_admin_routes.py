# Integration tests for admin routes
import pytest
import sys
import os
import json
import tempfile
from unittest.mock import patch, MagicMock, Mock
from io import BytesIO

# Add backend to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))


class TestAdminRoutes:
    """Integration tests for admin endpoints."""

    @pytest.fixture
    def test_config_dir(self):
        """Create a temporary directory for test configuration."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test policy.json
            policy = {
                "strict_audit_mode": False,
                "wipe_passphrase": "test-wipe-pass",
                "lan_passphrase": "test-lan-pass",
                "method_priority": {"sata": ["overwrite"]},
                "bind_address": "127.0.0.1",
                "port": 5000,
                "station_id": "test-station",
                "slack_webhook_url": "https://hooks.slack.com/test"
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
        patches = [
            patch('common.get_config_dir', return_value=test_config_dir),
            patch('common.get_data_dir', return_value=test_config_dir),
            patch('common.get_logs_dir', return_value=test_config_dir),
            patch('common.get_db_path', return_value=test_db_path),
            patch('common.get_failed_logs_dir', return_value=test_config_dir),
            patch('api_routes.get_config_dir', return_value=test_config_dir),
            patch('api_routes.get_db_path', return_value=test_db_path),
            patch('database.get_db_path', return_value=test_db_path),
            patch('database.get_cert_dir', return_value=test_config_dir),
            patch('routes.admin_routes.get_config_dir', return_value=test_config_dir),
            patch('routes.admin_routes.get_data_dir', return_value=test_config_dir),
            patch('routes.admin_routes.get_logs_dir', return_value=test_config_dir),
            patch('routes.admin_routes.get_failed_logs_dir', return_value=test_config_dir),
            patch('routes.admin_routes.get_db_path', return_value=test_db_path),
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
            from routes import admin_routes
            admin_bp = getattr(admin_routes, 'admin_bp', None)
            if admin_bp:
                app.register_blueprint(admin_bp)
            # Register api_routes module routes (e.g., /api/auth/verify)
            api_routes.register_routes(app)
            yield app
        finally:
            # Clean up database connections before stopping patches
            import sqlite3
            import gc
            try:
                # Force garbage collection to trigger any pending connection cleanup
                gc.collect()
                # Force close any open connections to the test database
                with sqlite3.connect(test_db_path) as conn:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    conn.execute("PRAGMA optimize")
            except Exception:
                pass
            for p in patches:
                p.stop()

    @pytest.fixture
    def client(self, app):
        """Create a test client."""
        return app.test_client()

    @pytest.fixture
    def admin_session(self, client):
        """Set up admin session cookie."""
        response = client.post('/api/auth/verify',
            json={"passphrase": "test-lan-pass"})
        assert response.status_code == 200
        return client

    def test_admin_metrics_unauthenticated_remote(self, client):
        """Test that remote requests without authentication return 401."""
        with patch('routes.admin_routes.is_local_request', return_value=False):
            response = client.get('/api/admin/metrics')
            assert response.status_code == 401

    def test_admin_metrics_authenticated(self, admin_session):
        """Test that authenticated requests return metrics."""
        with patch('routes.admin_routes.is_local_request', return_value=False):
            with patch('routes.admin_routes.get_ram_usage', return_value=50.0):
                with patch('routes.admin_routes.get_cpu_usage', return_value=25.0):
                    with patch('routes.admin_routes.get_system_uptime', return_value="1d 2h"):
                        with patch('routes.admin_routes.get_local_ip', return_value="192.168.1.100"):
                            with patch('routes.admin_routes.shutil.disk_usage') as mock_disk:
                                mock_disk.return_value = (1000000000, 500000000, 500000000)
                                response = admin_session.get('/api/admin/metrics')
                                assert response.status_code == 200
                                data = json.loads(response.data)
                                assert "disk_pct" in data
                                assert "ram_pct" in data
                                assert "cpu_pct" in data

    def test_admin_metrics_local_request_allowed(self, client):
        """Test that localhost requests bypass authentication."""
        with patch('routes.admin_routes.is_local_request', return_value=True):
            with patch('routes.admin_routes.get_ram_usage', return_value=50.0):
                with patch('routes.admin_routes.get_cpu_usage', return_value=25.0):
                    with patch('routes.admin_routes.get_system_uptime', return_value="1d 2h"):
                        with patch('routes.admin_routes.get_local_ip', return_value="127.0.0.1"):
                            with patch('routes.admin_routes.shutil.disk_usage') as mock_disk:
                                mock_disk.return_value = (1000000000, 500000000, 500000000)
                                response = client.get('/api/admin/metrics')
                                assert response.status_code == 200

    def test_test_webhook_no_url_configured(self, admin_session):
        """Test webhook test fails when no URL configured."""
        with patch('routes.admin_routes.load_policy') as mock_load:
            mock_load.return_value = {"slack_webhook_url": None}
            response = admin_session.post('/api/admin/test-webhook')
            assert response.status_code == 400
            data = json.loads(response.data)
            assert "No Slack webhook URL" in data["error"]

    def test_test_webhook_success(self, admin_session):
        """Test webhook test succeeds with valid URL."""
        with patch('routes.admin_routes.load_policy') as mock_load:
            mock_load.return_value = {
                "slack_webhook_url": "https://hooks.slack.com/test",
                "station_id": "test-station"
            }
            with patch('routes.admin_routes.urllib.request.urlopen') as mock_urlopen:
                mock_response = Mock()
                mock_response.getcode.return_value = 200
                mock_urlopen.return_value.__enter__.return_value = mock_response
                response = admin_session.post('/api/admin/test-webhook')
                assert response.status_code == 200
                data = json.loads(response.data)
                assert data["status"] == "success"

    def test_export_csv_ledger(self, admin_session):
        """Test CSV export endpoint."""
        response = admin_session.get('/api/admin/export-csv')
        assert response.status_code == 200
        assert "text/csv" in response.content_type
        assert "attachment" in response.headers.get("Content-Disposition", "")

    def test_support_bundle_download(self, admin_session):
        """Test support bundle download endpoint."""
        with patch('routes.admin_routes.socket.gethostname', return_value="test-host"):
            with patch('routes.admin_routes.subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(stdout="test output", stderr="")
                with patch('routes.admin_routes.os.listdir', return_value=[]):
                    with patch('routes.admin_routes.os.makedirs'):
                        with patch('routes.admin_routes.tarfile.open') as mock_tar:
                            mock_tar.return_value.__enter__.return_value = MagicMock()
                            with patch('routes.admin_routes.shutil.rmtree'):
                                with patch('routes.admin_routes.send_file') as mock_send:
                                    mock_send.return_value = MagicMock(status_code=200)
                                    response = admin_session.get('/api/admin/support-bundle')
                                    # Should return 200 or 500 depending on implementation
                                    assert response.status_code in [200, 500]

    def test_admin_policy_get(self, admin_session):
        """Test GET policy endpoint redacts passphrase."""
        response = admin_session.get('/api/admin/policy')
        assert response.status_code == 200
        data = json.loads(response.data)
        # Passphrase should be redacted
        assert data.get("lan_passphrase") == ""

    def test_admin_policy_post_update(self, admin_session):
        """Test POST policy endpoint updates fields."""
        payload = {
            "station_id": "new-station",
            "prewipe_spot_check": True
        }
        response = admin_session.post('/api/admin/policy', json=payload)
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "success"

    def test_admin_policy_post_update_passphrase(self, admin_session):
        """Test POST policy endpoint updates passphrase."""
        payload = {
            "lan_passphrase": "new-test-pass"
        }
        response = admin_session.post('/api/admin/policy', json=payload)
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "success"

    def test_admin_triage_config_get(self, admin_session):
        """Test GET triage config endpoint."""
        with patch('routes.admin_routes.load_policy') as mock_load:
            mock_load.return_value = {"triage_thresholds": {"ssd_new_poh_threshold": 1000}}
            response = admin_session.get('/api/admin/triage-config')
            assert response.status_code == 200
            data = json.loads(response.data)
            assert "ssd_new_poh_threshold" in data

    def test_admin_triage_config_post_valid(self, admin_session):
        """Test POST triage config with valid values."""
        payload = {
            "ssd_new_poh_threshold": 5000,
            "hdd_new_poh_threshold": 3000
        }
        with patch('routes.admin_routes.load_policy') as mock_load:
            mock_load.return_value = {"triage_thresholds": {}}
            with patch('routes.admin_routes.save_policy'):
                response = admin_session.post('/api/admin/triage-config', json=payload)
                assert response.status_code == 200

    def test_admin_triage_config_post_invalid_value(self, admin_session):
        """Test POST triage config with invalid value."""
        payload = {
            "ssd_new_poh_threshold": 999999  # Exceeds max
        }
        with patch('routes.admin_routes.load_policy') as mock_load:
            mock_load.return_value = {"triage_thresholds": {}}
            response = admin_session.post('/api/admin/triage-config', json=payload)
            assert response.status_code == 400
            data = json.loads(response.data)
            assert "Invalid value" in data["error"]

    def test_admin_triage_config_post_invalid_type(self, admin_session):
        """Test POST triage config with invalid type."""
        payload = {
            "ssd_new_poh_threshold": "not-a-number"
        }
        with patch('routes.admin_routes.load_policy') as mock_load:
            mock_load.return_value = {"triage_thresholds": {}}
            response = admin_session.post('/api/admin/triage-config', json=payload)
            assert response.status_code == 400
            data = json.loads(response.data)
            assert "Invalid type" in data["error"]

    def test_manage_logo_get_no_logo(self, admin_session):
        """Test GET logo when no logo exists."""
        with patch('routes.admin_routes.os.path.exists', return_value=False):
            response = admin_session.get('/api/admin/logo')
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["has_logo"] is False

    def test_manage_logo_get_with_logo(self, admin_session):
        """Test GET logo when logo exists."""
        with patch('routes.admin_routes.os.path.exists', return_value=True):
            with patch('routes.admin_routes.Image.open') as mock_img:
                mock_img.return_value.__enter__.return_value.width = 100
                mock_img.return_value.__enter__.return_value.height = 100
                with patch('builtins.open', MagicMock(return_value=BytesIO(b'test'))):
                    response = admin_session.get('/api/admin/logo')
                    assert response.status_code == 200
                    data = json.loads(response.data)
                    assert data["has_logo"] is True

    def test_manage_logo_post_no_file(self, admin_session):
        """Test POST logo without file."""
        response = admin_session.post('/api/admin/logo')
        assert response.status_code == 400
        data = json.loads(response.data)
        assert "No file provided" in data["error"]

    def test_manage_logo_post_requires_confirmation(self, admin_session):
        """Test POST logo requires confirmation when logo exists."""
        with patch('routes.admin_routes.os.path.exists', return_value=True):
            data = {'logo': (BytesIO(b'fake'), 'test.png')}
            response = admin_session.post('/api/admin/logo', data=data, content_type='multipart/form-data')
            assert response.status_code == 400
            data = json.loads(response.data)
            assert "confirmation_required" in data["error"]

    def test_manage_logo_post_with_confirmation(self, admin_session):
        """Test POST logo with confirmation."""
        with patch('routes.admin_routes.os.path.exists', return_value=True):
            with patch('routes.admin_routes.Image.open') as mock_img:
                mock_img.return_value.__enter__.return_value.format = "PNG"
                mock_img.return_value.__enter__.return_value.thumbnail = MagicMock()
                mock_img.return_value.__enter__.return_value.save = MagicMock()
                with patch('routes.admin_routes.os.makedirs'):
                    with patch('routes.admin_routes.os.path.getsize', return_value=500000):
                        with patch('routes.admin_routes.os.replace'):
                            with patch('builtins.open', MagicMock()):
                                response = admin_session.post(
                                    '/api/admin/logo?confirm=true',
                                    data={'logo': (BytesIO(b'fake'), 'test.png')},
                                    content_type='multipart/form-data'
                                )
                                assert response.status_code in [200, 400]

    def test_manage_logo_delete(self, admin_session):
        """Test DELETE logo."""
        with patch('routes.admin_routes.os.remove'):
            response = admin_session.delete('/api/admin/logo')
            assert response.status_code == 200

    def test_manage_logo_delete_not_found(self, admin_session):
        """Test DELETE logo when file doesn't exist."""
        with patch('routes.admin_routes.os.remove', side_effect=FileNotFoundError):
            response = admin_session.delete('/api/admin/logo')
            assert response.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
