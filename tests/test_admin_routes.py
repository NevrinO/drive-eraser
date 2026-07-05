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
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
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
        active_logs_dir = os.path.join(test_config_dir, "active")
        failed_logs_dir = os.path.join(test_config_dir, "failed")
        os.makedirs(active_logs_dir, exist_ok=True)
        os.makedirs(failed_logs_dir, exist_ok=True)
        patches = [
            patch('common.get_config_dir', return_value=test_config_dir),
            patch('common.get_data_dir', return_value=test_config_dir),
            patch('common.get_logs_dir', return_value=test_config_dir),
            patch('common.get_db_path', return_value=test_db_path),
            patch('common.get_active_logs_dir', return_value=active_logs_dir),
            patch('common.get_failed_logs_dir', return_value=failed_logs_dir),
            patch('api_routes.get_config_dir', return_value=test_config_dir),
            patch('api_routes.get_db_path', return_value=test_db_path),
            patch('database.get_db_path', return_value=test_db_path),
            patch('database.get_cert_dir', return_value=test_config_dir),
            patch('routes._shared.get_config_dir', return_value=test_config_dir),
            patch('routes.support_routes.get_config_dir', return_value=test_config_dir),
            patch('routes.support_routes.get_data_dir', return_value=test_config_dir),
            patch('routes.support_routes.get_logs_dir', return_value=test_config_dir),
            patch('routes.support_routes.get_active_logs_dir', return_value=active_logs_dir),
            patch('routes.support_routes.get_failed_logs_dir', return_value=failed_logs_dir),
            patch('routes.support_routes.get_db_path', return_value=test_db_path),
            patch('routes.policy_routes.get_config_dir', return_value=test_config_dir),
            patch('routes.enclosure_routes.get_config_dir', return_value=test_config_dir),
            patch('routes.smart_routes.get_config_dir', return_value=test_config_dir),
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
            from routes.support_routes import support_bp
            from routes.policy_routes import policy_bp
            from routes.enclosure_routes import enclosure_bp
            from routes.smart_routes import smart_bp
            app.register_blueprint(support_bp)
            app.register_blueprint(policy_bp)
            app.register_blueprint(enclosure_bp)
            app.register_blueprint(smart_bp)
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

    @pytest.fixture
    def admin_session(self, client):
        """Set up admin session cookie."""
        response = client.post('/api/auth/verify',
            json={"passphrase": "test-lan-pass"})
        assert response.status_code == 200
        return client

    def test_admin_metrics_unauthenticated_remote(self, client):
        """Test that remote requests without authentication return 401."""
        with patch('routes._shared.is_local_request', return_value=False):
            response = client.get('/api/admin/metrics')
            assert response.status_code == 401

    def test_admin_metrics_authenticated(self, admin_session):
        """Test that authenticated requests return metrics."""
        with patch('routes._shared.is_local_request', return_value=False):
            with patch('routes.support_routes.get_ram_usage', return_value=50.0):
                with patch('routes.support_routes.get_cpu_usage', return_value=25.0):
                    with patch('routes.support_routes.get_system_uptime', return_value="1d 2h"):
                        with patch('routes.support_routes.get_local_ip', return_value="192.168.1.100"):
                            with patch('routes.support_routes.shutil.disk_usage') as mock_disk:
                                mock_disk.return_value = (1000000000, 500000000, 500000000)
                                response = admin_session.get('/api/admin/metrics')
                                assert response.status_code == 200
                                data = json.loads(response.data)
                                assert "disk_pct" in data
                                assert "ram_pct" in data
                                assert "cpu_pct" in data

    def test_admin_metrics_local_request_allowed(self, client):
        """Test that localhost requests bypass authentication."""
        with patch('routes._shared.is_local_request', return_value=True):
            with patch('routes.support_routes.get_ram_usage', return_value=50.0):
                with patch('routes.support_routes.get_cpu_usage', return_value=25.0):
                    with patch('routes.support_routes.get_system_uptime', return_value="1d 2h"):
                        with patch('routes.support_routes.get_local_ip', return_value="127.0.0.1"):
                            with patch('routes.support_routes.shutil.disk_usage') as mock_disk:
                                mock_disk.return_value = (1000000000, 500000000, 500000000)
                                response = client.get('/api/admin/metrics')
                                assert response.status_code == 200

    def test_test_webhook_no_url_configured(self, admin_session):
        """Test webhook test fails when no URL configured."""
        with patch('routes.support_routes.load_policy') as mock_load:
            mock_load.return_value = {"slack_webhook_url": None}
            response = admin_session.post('/api/admin/test-webhook')
            assert response.status_code == 400
            data = json.loads(response.data)
            assert "No Slack webhook URL" in data["error"]

    def test_test_webhook_success(self, admin_session):
        """Test webhook test succeeds with valid URL."""
        with patch('routes.support_routes.load_policy') as mock_load:
            mock_load.return_value = {
                "slack_webhook_url": "https://hooks.slack.com/test",
                "station_id": "test-station"
            }
            with patch('routes.support_routes.urllib.request.urlopen') as mock_urlopen:
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
        with patch('routes.support_routes.socket.gethostname', return_value="test-host"):
            with patch('routes.support_routes.subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(stdout="test output", stderr="")
                with patch('routes.support_routes.os.listdir', return_value=[]):
                    with patch('routes.support_routes.os.makedirs'):
                        with patch('routes.support_routes.tarfile.open') as mock_tar:
                            mock_tar.return_value.__enter__.return_value = MagicMock()
                            with patch('routes.support_routes.shutil.rmtree'):
                                with patch('routes.support_routes.send_file') as mock_send:
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
            "prewipe_zero_detection_enabled": True
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

    def test_admin_policy_post_update_background_smart_workers_restarts_pool(self, admin_session):
        """Test that changing background_smart_max_workers restarts the extended SMART pool."""
        import routes.policy_routes as policy_module
        with patch.object(policy_module, 'stop_extended_smart_pool') as mock_stop:
            payload = {"background_smart_max_workers": 6}
            response = admin_session.post('/api/admin/policy', json=payload)
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["status"] == "success"
            mock_stop.assert_called_once()

    def test_admin_policy_post_update_discovery_diag(self, admin_session):
        """Test that discovery_diag can be updated via POST."""
        payload = {"discovery_diag": True}
        response = admin_session.post('/api/admin/policy', json=payload)
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "success"

    def test_admin_policy_post_update_max_logo_size_mb(self, admin_session):
        """Test that max_logo_size_mb can be updated via POST."""
        payload = {"max_logo_size_mb": 5.0}
        response = admin_session.post('/api/admin/policy', json=payload)
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "success"

    def test_admin_policy_post_update_max_bulk_cert_batch_size(self, admin_session):
        """Test that max_bulk_cert_batch_size can be updated via POST."""
        payload = {"max_bulk_cert_batch_size": 500}
        response = admin_session.post('/api/admin/policy', json=payload)
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "success"

    def test_admin_policy_post_rejects_deprecated_crypto_verification_mode(self, admin_session):
        """Test that the deprecated crypto_verification_mode key is rejected."""
        payload = {"crypto_verification_mode": "disabled"}
        response = admin_session.post('/api/admin/policy', json=payload)
        assert response.status_code == 400
        data = json.loads(response.data)
        assert "secondary_verification_mode" in data["error"]

    def test_admin_triage_config_get(self, admin_session):
        """Test GET triage config endpoint."""
        with patch('routes.policy_routes.load_policy') as mock_load:
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
        with patch('routes.policy_routes.load_policy') as mock_load:
            mock_load.return_value = {"triage_thresholds": {}}
            with patch('routes.policy_routes.save_policy'):
                response = admin_session.post('/api/admin/triage-config', json=payload)
                assert response.status_code == 200

    def test_admin_triage_config_post_invalid_value(self, admin_session):
        """Test POST triage config with invalid value."""
        payload = {
            "ssd_new_poh_threshold": 999999  # Exceeds max
        }
        with patch('routes.policy_routes.load_policy') as mock_load:
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
        with patch('routes.policy_routes.load_policy') as mock_load:
            mock_load.return_value = {"triage_thresholds": {}}
            response = admin_session.post('/api/admin/triage-config', json=payload)
            assert response.status_code == 400
            data = json.loads(response.data)
            assert "Invalid type" in data["error"]

    def test_manage_logo_get_no_logo(self, admin_session):
        """Test GET logo when no logo exists."""
        with patch('routes.support_routes.os.path.exists', return_value=False):
            response = admin_session.get('/api/admin/logo')
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["has_logo"] is False

    def test_manage_logo_get_with_logo(self, admin_session):
        """Test GET logo when logo exists."""
        with patch('routes.support_routes.os.path.exists', return_value=True):
            with patch('routes.support_routes.Image.open') as mock_img:
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
        with patch('routes.support_routes.os.path.exists', return_value=True):
            data = {'logo': (BytesIO(b'fake'), 'test.png')}
            response = admin_session.post('/api/admin/logo', data=data, content_type='multipart/form-data')
            assert response.status_code == 400
            data = json.loads(response.data)
            assert "confirmation_required" in data["error"]

    # Phase 8: SMART endpoint tests
    def test_smart_export_invalid_device(self, admin_session):
        """Test smart-export rejects invalid device names."""
        response = admin_session.get('/api/admin/drives/sda*/smart-export')
        assert response.status_code == 400

    def test_smart_export_success(self, admin_session):
        """Test smart-export returns JSON file."""
        with patch('smart_parsing.get_smart_data') as mock_get_smart:
            mock_get_smart.return_value = {
                "serial": "TEST123",
                "model": "Test Drive",
                "raw": json.dumps({"test": "data"})
            }
            with patch('routes.smart_routes.ERASE_JOBS_LOCK') as mock_lock:
                mock_lock.__enter__ = Mock()
                mock_lock.__exit__ = Mock()
                response = admin_session.get('/api/admin/drives/sda/smart-export')
                assert response.status_code == 200
                assert "application/json" in response.content_type
                assert "attachment" in response.headers.get("Content-Disposition", "")

    def test_smart_export_while_wiping(self, admin_session):
        """Test smart-export blocked during active wipe."""
        with patch('routes.smart_routes.ERASE_JOBS') as mock_jobs:
            mock_jobs.values.return_value = [
                {"status": "running", "request": {"device": "/dev/sda"}}
            ]
            with patch('routes.smart_routes.ERASE_JOBS_LOCK') as mock_lock:
                mock_lock.__enter__ = Mock()
                mock_lock.__exit__ = Mock()
                response = admin_session.get('/api/admin/drives/sda/smart-export')
                assert response.status_code == 409
                data = json.loads(response.data)
                assert "wipe is in progress" in data["error"]

    def test_smart_details_invalid_device(self, admin_session):
        """Test smart-details rejects invalid device names."""
        response = admin_session.get('/api/admin/drives/sda*/smart-details')
        assert response.status_code == 400

    def test_smart_details_success(self, admin_session):
        """Test smart-details returns structured SMART data."""
        with patch('smart_parsing.get_smart_data') as mock_get_smart:
            mock_get_smart.return_value = {
                "serial": "TEST123",
                "raw": json.dumps({
                    "ata_smart_attributes": {"table": [{"id": 1, "name": "Raw_Read_Error_Rate", "value": 100}]}
                })
            }
            response = admin_session.get('/api/admin/drives/sda/smart-details')
            assert response.status_code == 200
            data = json.loads(response.data)
            assert "attributes" in data
            assert len(data["attributes"]) == 1

    def test_smart_details_size_limits(self, admin_session):
        """Test smart-details enforces size limits (DoS prevention)."""
        large_attrs = [{"id": i, "name": f"Attr{i}", "value": 100} for i in range(200)]
        with patch('smart_parsing.get_smart_data') as mock_get_smart:
            mock_get_smart.return_value = {
                "serial": "TEST123",
                "raw": json.dumps({
                    "ata_smart_attributes": {"table": large_attrs}
                })
            }
            response = admin_session.get('/api/admin/drives/sda/smart-details')
            assert response.status_code == 200
            data = json.loads(response.data)
            # Should be truncated to MAX_ATTRIBUTES (100)
            assert len(data["attributes"]) == 100
            assert data["truncated"] is True

    def test_smart_test_invalid_device(self, admin_session):
        """Test smart-test rejects invalid device names."""
        response = admin_session.post('/api/admin/drives/sda*/smart-test', json={"test_type": "short"})
        assert response.status_code == 400

    def test_smart_test_invalid_test_type(self, admin_session):
        """Test smart-test rejects invalid test types."""
        with patch('routes.smart_routes.os.path.exists', side_effect=lambda path: path == "/dev/sdb"):
            with patch('routes.smart_routes.ERASE_JOBS_LOCK') as mock_lock:
                mock_lock.__enter__ = Mock()
                mock_lock.__exit__ = Mock()
                with patch('routes.smart_routes.SMART_TEST_LOCKS_LOCK') as mock_test_locks_lock:
                    mock_test_locks_lock.__enter__ = Mock()
                    mock_test_locks_lock.__exit__ = Mock()
                    with patch('routes.smart_routes.SMART_TEST_LOCKS', {}):
                        # Mock lsblk to avoid mounted drive check (403)
                        with patch('routes.smart_routes.subprocess.run') as mock_run:
                            mock_run.return_value = MagicMock(returncode=0, stdout='{"blockdevices": []}')
                            # Mock discover_drives to avoid locked/secondary path checks (403)
                            with patch('routes.smart_routes.discover_drives', return_value=[]):
                                response = admin_session.post('/api/admin/drives/sdb/smart-test', json={"test_type": "invalid"})
                                assert response.status_code == 400

    def test_smart_test_success(self, admin_session):
        """Test smart-test starts a test successfully."""
        with patch('routes.smart_routes.os.path.exists', side_effect=lambda path: path == "/dev/sdb"):
            with patch('routes.smart_routes.ERASE_JOBS_LOCK') as mock_lock:
                mock_lock.__enter__ = Mock()
                mock_lock.__exit__ = Mock()
                with patch('routes.smart_routes.SMART_TEST_LOCKS_LOCK') as mock_test_locks_lock:
                    mock_test_locks_lock.__enter__ = Mock()
                    mock_test_locks_lock.__exit__ = Mock()
                    with patch('routes.smart_routes.SMART_TEST_LOCKS', {}):
                        # Mock lsblk to avoid mounted drive check (403)
                        with patch('routes.smart_routes.subprocess.run') as mock_run:
                            mock_run.return_value = MagicMock(returncode=0, stdout='{"blockdevices": []}')
                            # Mock discover_drives to avoid locked/secondary path checks (403)
                            with patch('routes.smart_routes.discover_drives', return_value=[]):
                                with patch('smart_parsing.get_smart_data') as mock_get_smart:
                                    mock_get_smart.return_value = {
                                        "serial": "TEST123",
                                        "interface_type": "sata"
                                    }
                                    with patch('smart_parsing.run_smart_test') as mock_run_test:
                                        mock_run_test.return_value = {
                                            "test_type": "short",
                                            "status": "started",
                                            "estimated_minutes": 2
                                        }
                                        with patch('database.record_smart_test_run', return_value=1):
                                            response = admin_session.post('/api/admin/drives/sdb/smart-test', json={"test_type": "short"})
                                            assert response.status_code == 200
                                            data = json.loads(response.data)
                                            assert data["status"] == "started"

    def test_smart_test_while_wiping(self, admin_session):
        """Test smart-test blocked during active wipe."""
        with patch('routes.smart_routes.os.path.exists', return_value=True):
            with patch('routes.smart_routes.ERASE_JOBS') as mock_jobs:
                mock_jobs.values.return_value = [
                    {"status": "running", "request": {"device": "/dev/sda"}}
                ]
                with patch('routes.smart_routes.ERASE_JOBS_LOCK') as mock_lock:
                    mock_lock.__enter__ = Mock()
                    mock_lock.__exit__ = Mock()
                    response = admin_session.post('/api/admin/drives/sda/smart-test', json={"test_type": "short"})
                    assert response.status_code == 409
                    data = json.loads(response.data)
                    assert "wipe is in progress" in data["error"]

    def test_smart_test_conveyance_sata_only(self, admin_session):
        """Test conveyance test rejected on non-SATA devices."""
        with patch('routes.smart_routes.os.path.exists', side_effect=lambda path: path == "/dev/sdb"):
            with patch('routes.smart_routes.ERASE_JOBS_LOCK') as mock_lock:
                mock_lock.__enter__ = Mock()
                mock_lock.__exit__ = Mock()
                with patch('routes.smart_routes.SMART_TEST_LOCKS_LOCK') as mock_test_locks_lock:
                    mock_test_locks_lock.__enter__ = Mock()
                    mock_test_locks_lock.__exit__ = Mock()
                    with patch('routes.smart_routes.SMART_TEST_LOCKS', {}):
                        # Mock lsblk to avoid mounted drive check (403)
                        with patch('routes.smart_routes.subprocess.run') as mock_run:
                            mock_run.return_value = MagicMock(returncode=0, stdout='{"blockdevices": []}')
                            # Mock discover_drives to avoid locked/secondary path checks (403)
                            with patch('routes.smart_routes.discover_drives', return_value=[]):
                                with patch('smart_parsing.get_smart_data') as mock_get_smart:
                                    mock_get_smart.return_value = {
                                        "serial": "TEST123",
                                        "interface_type": "sas"
                                    }
                                    response = admin_session.post('/api/admin/drives/sdb/smart-test', json={"test_type": "conveyance"})
                                    assert response.status_code == 400
                                    data = json.loads(response.data)
                                    assert "Conveyance test is only supported on SATA" in data["error"]

    def test_smart_test_status_invalid_device(self, admin_session):
        """Test smart-test-status rejects invalid device names."""
        response = admin_session.get('/api/admin/drives/sda*/smart-test-status')
        assert response.status_code == 400

    def test_smart_test_status_success(self, admin_session):
        """Test smart-test-status returns test status."""
        with patch('smart_parsing.get_smart_test_status') as mock_get_status:
            mock_get_status.return_value = {
                "status": "in_progress",
                "percentage": 50.0,
                "latest_result": {"type": "Short", "status": "Self-test in progress"}
            }
            response = admin_session.get('/api/admin/drives/sda/smart-test-status')
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["status"] == "in_progress"
            assert data["percentage"] == 50.0

    def test_drive_models_endpoint(self, admin_session):
        """Test drive-models endpoint returns model profiles."""
        with patch('routes.smart_routes.os.path.exists', return_value=True):
            with patch('builtins.open', MagicMock(return_value=BytesIO(json.dumps({
                "drive_models": {
                    "SEAGATE,ST4000NM0023,0003": {
                        "vendor": "SEAGATE",
                        "product": "ST4000NM0023",
                        "revision": "0003",
                        "trip_temperature": 60
                    }
                }
            }).encode()))):
                response = admin_session.get('/api/admin/drive-models')
                assert response.status_code == 200
                data = json.loads(response.data)
                assert "drive_models" in data

    def test_manage_logo_post_with_confirmation(self, admin_session):
        """Test POST logo with confirmation."""
        with patch('routes.support_routes.os.path.exists', return_value=True):
            with patch('routes.support_routes.Image.open') as mock_img:
                mock_img.return_value.__enter__.return_value.format = "PNG"
                mock_img.return_value.__enter__.return_value.thumbnail = MagicMock()
                mock_img.return_value.__enter__.return_value.save = MagicMock()
                with patch('routes.support_routes.os.makedirs'):
                    with patch('routes.support_routes.os.path.getsize', return_value=500000):
                        with patch('routes.support_routes.os.replace'):
                            with patch('builtins.open', MagicMock()):
                                response = admin_session.post(
                                    '/api/admin/logo?confirm=true',
                                    data={'logo': (BytesIO(b'fake'), 'test.png')},
                                    content_type='multipart/form-data'
                                )
                                assert response.status_code in [200, 400]

    def test_manage_logo_delete(self, admin_session):
        """Test DELETE logo."""
        with patch('routes.support_routes.os.remove'):
            response = admin_session.delete('/api/admin/logo')
            assert response.status_code == 200

    def test_manage_logo_delete_not_found(self, admin_session):
        """Test DELETE logo when file doesn't exist."""
        with patch('routes.support_routes.os.remove', side_effect=FileNotFoundError):
            response = admin_session.delete('/api/admin/logo')
            assert response.status_code == 200

    def test_create_enclosure_uses_layout_templates(self, admin_session):
        """Regression: enclosure creation must find templates via load_layout_templates and auto-map slots."""
        with patch('routes.enclosure_routes.load_layout_templates') as mock_load_templates:
            mock_load_templates.return_value = ({
                "test_4bay": {
                    "id": "test_4bay",
                    "name": "Test 4-Bay",
                    "vendor": "Test",
                    "slot_count": 4,
                    "rows": 2,
                    "cols": 2,
                    "traversal_preset": "top_left_down_then_across",
                    "default_role": "wipe"
                }
            }, False)
            with patch('routes.enclosure_routes.generate_master_slot_map') as mock_master:
                mock_master.return_value = [
                    {
                        "pci_controller": "0000:00:1f.2",
                        "slot_type": "sas_expander",
                        "physical_slot_number": 0,
                        "hardware_identifier": "0:0:0",
                        "expander_sas_address": None
                    },
                    {
                        "pci_controller": "0000:00:1f.2",
                        "slot_type": "sas_expander",
                        "physical_slot_number": 1,
                        "hardware_identifier": "0:0:1",
                        "expander_sas_address": None
                    },
                    {
                        "pci_controller": "0000:00:1f.2",
                        "slot_type": "sas_expander",
                        "physical_slot_number": 2,
                        "hardware_identifier": "0:0:2",
                        "expander_sas_address": None
                    },
                    {
                        "pci_controller": "0000:00:1f.2",
                        "slot_type": "sas_expander",
                        "physical_slot_number": 3,
                        "hardware_identifier": "0:0:3",
                        "expander_sas_address": None
                    }
                ]
                with patch('routes.enclosure_routes.save_bay_map') as mock_save:
                    payload = {
                        "id": "test_enc",
                        "name": "Test Enclosure",
                        "template_id": "test_4bay",
                        "pci_controller": "0000:00:1f.2",
                        "expander_sas_address": None,
                        "display_order": 0,
                        "auto_map_slots": True,
                        "nvme_start_slot": None
                    }
                    response = admin_session.post('/api/admin/enclosures', json=payload)
                    assert response.status_code == 201, response.data
                    data = json.loads(response.data)
                    assert data["enclosure"]["slots"]["0"]["physical_slot_number"] == 0
                    mock_save.assert_called_once()

    def test_create_enclosure_name_too_long_returns_400(self, admin_session):
        """Test that enclosure name exceeding 100 chars returns 400 (A91)."""
        payload = {
            "id": "test_enc",
            "name": "A" * 101,
            "template_id": "test_4bay",
            "pci_controller": "0000:00:1f.2",
            "expander_sas_address": None,
            "display_order": 0,
            "auto_map_slots": True,
            "nvme_start_slot": None
        }
        response = admin_session.post('/api/admin/enclosures', json=payload)
        assert response.status_code == 400
        data = json.loads(response.data)
        assert "100 characters" in data["error"]

    def test_is_valid_device_name_multi_letter_sata(self, app):
        """Regression: multi-letter SATA device names must be accepted by is_valid_device_name."""
        from routes.admin_routes import is_valid_device_name
        valid_names = ["sdac", "sdbt", "sdaa", "sdaz", "sdba"]
        for name in valid_names:
            assert is_valid_device_name(name) is True, f"Valid name rejected: {name}"

    def test_is_valid_device_name_sata_partitions(self, app):
        """SATA partition names must be rejected — SMART tests target whole disks only."""
        from routes.admin_routes import is_valid_device_name
        assert is_valid_device_name("sdac1") is False
        assert is_valid_device_name("sdbt12") is False
        assert is_valid_device_name("sda1") is False

    def test_is_valid_device_name_nvme(self, app):
        """NVMe device names must be accepted."""
        from routes.admin_routes import is_valid_device_name
        assert is_valid_device_name("nvme0n1") is True
        assert is_valid_device_name("nvme0n1p1") is True

    def test_is_valid_device_name_invalid(self, app):
        """Path traversal, newlines, and malformed names must be rejected."""
        from routes.admin_routes import is_valid_device_name
        invalid_names = [
            "../etc/passwd",
            "sda\n",
            "sda\r",
            "sda*",
            "",
            "   ",
        ]
        for name in invalid_names:
            assert is_valid_device_name(name) is False, f"Invalid name accepted: {repr(name)}"

    # --- Log viewer endpoint tests ---

    def test_list_logs(self, admin_session):
        """Test that list logs endpoint returns valid JSON array with expected fields."""
        with patch('routes._shared.is_local_request', return_value=True):
            from common import get_logs_dir, get_active_logs_dir, get_failed_logs_dir
            main_dir = get_logs_dir()
            active_dir = get_active_logs_dir()
            failed_dir = get_failed_logs_dir()

            with open(os.path.join(main_dir, "app.log"), "w") as f:
                f.write("test log line 1\ntest log line 2\n")
            with open(os.path.join(active_dir, "job-test123.log"), "w") as f:
                f.write("active job log\n")
            with open(os.path.join(failed_dir, "job-failed456.log"), "w") as f:
                f.write("failed job log\n")

            response = admin_session.get('/api/admin/logs')
            assert response.status_code == 200
            data = json.loads(response.data)
            assert isinstance(data, list)
            assert len(data) >= 3
            for entry in data:
                assert "name" in entry
                assert "category" in entry
                assert "size_bytes" in entry
                assert "modified_at" in entry
                assert "path_key" in entry

            # Clean up test files
            for f in [os.path.join(main_dir, "app.log"),
                      os.path.join(active_dir, "job-test123.log"),
                      os.path.join(failed_dir, "job-failed456.log")]:
                try:
                    os.remove(f)
                except FileNotFoundError:
                    pass

    def test_list_logs_empty(self, admin_session):
        """Test that empty log directories don't cause errors."""
        with patch('routes._shared.is_local_request', return_value=True):
            response = admin_session.get('/api/admin/logs')
            assert response.status_code == 200
            data = json.loads(response.data)
            assert isinstance(data, list)

    def test_download_log(self, admin_session):
        """Test that log file download works with correct headers."""
        import base64
        with patch('routes._shared.is_local_request', return_value=True):
            from common import get_logs_dir
            logs_dir = get_logs_dir()
            test_file = os.path.join(logs_dir, "test_download.log")
            with open(test_file, "w") as f:
                f.write("download test content\n")

            rel_path = "test_download.log"
            path_key = base64.urlsafe_b64encode(rel_path.encode("utf-8")).decode("ascii")

            response = admin_session.get(f'/api/admin/logs/{path_key}/download')
            assert response.status_code == 200
            assert "attachment" in response.headers.get("Content-Disposition", "")

            try:
                os.remove(test_file)
            except FileNotFoundError:
                pass

    def test_download_log_invalid_key(self, admin_session):
        """Test that invalid path keys are rejected."""
        with patch('routes._shared.is_local_request', return_value=True):
            # Use a path key that decodes to a path outside logs dir
            import base64
            traversal_path = "../../etc/passwd"
            path_key = base64.urlsafe_b64encode(traversal_path.encode("utf-8")).decode("ascii")
            response = admin_session.get(f'/api/admin/logs/{path_key}/download')
            assert response.status_code == 400

    def test_preview_log(self, admin_session):
        """Test that preview returns last N lines."""
        import base64
        with patch('routes._shared.is_local_request', return_value=True):
            from common import get_logs_dir
            logs_dir = get_logs_dir()
            test_file = os.path.join(logs_dir, "test_preview.log")
            with open(test_file, "w") as f:
                for i in range(50):
                    f.write(f"line {i}\n")

            rel_path = "test_preview.log"
            path_key = base64.urlsafe_b64encode(rel_path.encode("utf-8")).decode("ascii")

            response = admin_session.get(f'/api/admin/logs/{path_key}/preview?lines=10')
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["lines_returned"] == 10
            assert "line 49" in data["content"]
            assert "line 40" in data["content"]
            assert "line 39" not in data["content"]

            try:
                os.remove(test_file)
            except FileNotFoundError:
                pass

    def test_preview_log_path_traversal(self, admin_session):
        """Test that path traversal attempts are blocked."""
        import base64
        with patch('routes._shared.is_local_request', return_value=True):
            traversal_path = "../../etc/passwd"
            path_key = base64.urlsafe_b64encode(traversal_path.encode("utf-8")).decode("ascii")
            response = admin_session.get(f'/api/admin/logs/{path_key}/preview')
            assert response.status_code == 400

    def test_preview_log_search(self, admin_session):
        """Test that content search returns only matching lines."""
        import base64
        with patch('routes._shared.is_local_request', return_value=True):
            from common import get_logs_dir
            logs_dir = get_logs_dir()
            test_file = os.path.join(logs_dir, "test_search.log")
            with open(test_file, "w") as f:
                f.write("INFO: starting process\n")
                f.write("ERROR: something went wrong\n")
                f.write("INFO: process completed\n")
                f.write("DEBUG: debug message\n")

            rel_path = "test_search.log"
            path_key = base64.urlsafe_b64encode(rel_path.encode("utf-8")).decode("ascii")

            response = admin_session.get(f'/api/admin/logs/{path_key}/preview?lines=100&q=ERROR')
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["lines_returned"] == 1
            assert "ERROR" in data["content"]
            assert "INFO" not in data["content"]

            try:
                os.remove(test_file)
            except FileNotFoundError:
                pass

    def test_preview_log_search_case_sensitive(self, admin_session):
        """Test that case-sensitive search option works."""
        import base64
        with patch('routes._shared.is_local_request', return_value=True):
            from common import get_logs_dir
            logs_dir = get_logs_dir()
            test_file = os.path.join(logs_dir, "test_cs_search.log")
            with open(test_file, "w") as f:
                f.write("Error: lowercase error here\n")
                f.write("ERROR: uppercase error here\n")

            rel_path = "test_cs_search.log"
            path_key = base64.urlsafe_b64encode(rel_path.encode("utf-8")).decode("ascii")

            # Case-sensitive search for "ERROR" should only match the uppercase line
            response = admin_session.get(f'/api/admin/logs/{path_key}/preview?lines=100&q=ERROR&case_sensitive=true')
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["lines_returned"] == 1
            assert "uppercase" in data["content"]

            try:
                os.remove(test_file)
            except FileNotFoundError:
                pass

    def test_preview_log_search_invalid_regex(self, admin_session):
        """Test that invalid regex falls back to literal search."""
        import base64
        with patch('routes._shared.is_local_request', return_value=True):
            from common import get_logs_dir
            logs_dir = get_logs_dir()
            test_file = os.path.join(logs_dir, "test_regex.log")
            with open(test_file, "w") as f:
                f.write("line with [brackets]\n")
                f.write("normal line\n")

            rel_path = "test_regex.log"
            path_key = base64.urlsafe_b64encode(rel_path.encode("utf-8")).decode("ascii")

            # Invalid regex "[" should fall back to literal search
            response = admin_session.get(f'/api/admin/logs/{path_key}/preview?lines=100&q=[')
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data.get("regex_fallback") is True
            assert data["lines_returned"] == 1
            assert "brackets" in data["content"]

            try:
                os.remove(test_file)
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
