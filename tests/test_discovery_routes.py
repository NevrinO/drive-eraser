# Integration tests for discovery routes
import pytest
import sys
import os
import json
import tempfile
from unittest.mock import patch, MagicMock

# Add backend to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))


class TestDiscoveryRoutes:
    """Integration tests for discovery endpoints."""

    @pytest.fixture
    def test_config_dir(self):
        """Create a temporary directory for test configuration."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
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
            patch('routes.discovery_routes.get_config_dir', return_value=test_config_dir),
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
            from routes import discovery_routes
            discovery_bp = getattr(discovery_routes, 'discovery_bp', None)
            if discovery_bp:
                app.register_blueprint(discovery_bp)
            # Register api_routes module routes (e.g., /api/auth/verify)
            api_routes.register_routes(app)
            yield app
        finally:
            # Close log file handlers to prevent Windows file locking issues
            import logging
            root_logger = logging.getLogger()
            for handler in list(root_logger.handlers):
                if isinstance(handler, logging.FileHandler):
                    try:
                        handler.close()
                        root_logger.removeHandler(handler)
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

    def test_discover_slots_unauthenticated(self, client):
        """Test that unauthenticated requests return 401."""
        with patch('routes._shared.is_local_request', return_value=False):
            response = client.get('/api/admin/discover-slots')
            assert response.status_code == 401

    def test_discover_slots_invalid_controller_type(self, admin_session):
        """Test that invalid controller_type returns 400."""
        with patch('routes._shared.is_local_request', return_value=False):
            response = admin_session.get('/api/admin/discover-slots?controller_type=invalid')
            assert response.status_code == 400
            data = json.loads(response.data)
            assert "Invalid controller_type" in data["error"]

    def test_discover_slots_invalid_pci_address(self, admin_session):
        """Test that invalid pci_address returns 400."""
        with patch('routes._shared.is_local_request', return_value=False):
            with patch('routes.discovery_routes.validate_pci_address', return_value=False):
                response = admin_session.get('/api/admin/discover-slots?pci_address=invalid')
                assert response.status_code == 400
                data = json.loads(response.data)
                assert "Invalid pci_address" in data["error"]

    def test_discover_slots_valid_controller_type(self, admin_session):
        """Test that valid controller_type is accepted."""
        with patch('routes._shared.is_local_request', return_value=False):
            with patch('routes.discovery_routes.scan_pci_controllers', return_value=[]):
                with patch('routes.discovery_routes.discover_controllers_and_devices', return_value={}):
                    with patch('routes.discovery_routes.get_scsi_host_slot_projections', return_value=[]):
                        with patch('routes.discovery_routes.os.listdir', return_value=[]):
                            response = admin_session.get('/api/admin/discover-slots?controller_type=sata')
                            assert response.status_code == 200
                            data = json.loads(response.data)
                            assert "controllers" in data

    def test_discover_slots_controller_limit(self, admin_session):
        """Test that controller count limit is enforced."""
        with patch('routes._shared.is_local_request', return_value=False):
            with patch('routes.discovery_routes.scan_pci_controllers', return_value=[{"pci_address": f"0000:00:0{i}.0"} for i in range(101)]):
                with patch('routes.discovery_routes.discover_controllers_and_devices', return_value={}):
                    response = admin_session.get('/api/admin/discover-slots')
                    assert response.status_code == 400
                    data = json.loads(response.data)
                    assert "exceeds maximum limit" in data["error"]

    def test_discover_slots_device_limit(self, admin_session):
        """Test that device count limit is enforced."""
        with patch('routes._shared.is_local_request', return_value=False):
            with patch('routes.discovery_routes.scan_pci_controllers', return_value=[]):
                with patch('routes.discovery_routes.discover_controllers_and_devices', return_value={
                    "sata": [{"device_path": f"/dev/sd{i}", "controller": {}} for i in range(1001)]
                }):
                    with patch('routes.discovery_routes.validate_device_path', return_value=True):
                        with patch('routes.discovery_routes.os.listdir', return_value=[]):
                            with patch('routes.discovery_routes.get_scsi_host_slot_projections', return_value=[]):
                                response = admin_session.get('/api/admin/discover-slots')
                                assert response.status_code == 400
                                data = json.loads(response.data)
                                assert "exceeds maximum limit" in data["error"]

    def test_discover_slots_include_smart(self, admin_session):
        """Test that include_smart parameter works."""
        with patch('routes._shared.is_local_request', return_value=False):
            with patch('routes.discovery_routes.scan_pci_controllers', return_value=[]):
                with patch('routes.discovery_routes.discover_controllers_and_devices', return_value={
                    "sata": [{"device_path": "/dev/sda", "controller": {}}]
                }):
                    with patch('routes.discovery_routes.validate_device_path', return_value=True):
                        with patch('routes.discovery_routes.get_smart_data', return_value={"model": "test"}):
                            with patch('routes.discovery_routes.os.listdir', return_value=[]):
                                with patch('routes.discovery_routes.get_scsi_host_slot_projections', return_value=[]):
                                    response = admin_session.get('/api/admin/discover-slots?include_smart=true')
                                    assert response.status_code == 200

    def test_discover_slots_local_request_allowed(self, client):
        """Test that localhost requests bypass authentication."""
        with patch('routes._shared.is_local_request', return_value=True):
            with patch('routes.discovery_routes.scan_pci_controllers', return_value=[]):
                with patch('routes.discovery_routes.discover_controllers_and_devices', return_value={}):
                    with patch('routes.discovery_routes.get_scsi_host_slot_projections', return_value=[]):
                        with patch('routes.discovery_routes.os.listdir', return_value=[]):
                            response = client.get('/api/admin/discover-slots')
                            assert response.status_code == 200

    def test_apply_slot_mapping_unauthenticated(self, client):
        """Test that unauthenticated requests return 401."""
        with patch('routes._shared.is_local_request', return_value=False):
            response = client.post('/api/admin/apply-slot-mapping', json={})
            assert response.status_code == 401

    def test_apply_slot_mapping_invalid_payload(self, admin_session):
        """Test that invalid payload returns 400."""
        with patch('routes._shared.is_local_request', return_value=False):
            response = admin_session.post('/api/admin/apply-slot-mapping', data="not json")
            assert response.status_code == 400

    def test_apply_slot_mapping_exceeds_limit(self, admin_session):
        """Test that mapping count limit is enforced."""
        with patch('routes._shared.is_local_request', return_value=False):
            payload = {f"bay{i}": {"device_path": "/dev/sda"} for i in range(101)}
            response = admin_session.post('/api/admin/apply-slot-mapping', json=payload)
            assert response.status_code == 400
            data = json.loads(response.data)
            assert "exceeds maximum limit" in data["error"]

    def test_apply_slot_mapping_invalid_bay_id(self, admin_session):
        """Test that invalid bay_id is rejected."""
        with patch('routes._shared.is_local_request', return_value=False):
            with patch('routes._shared.load_policy', return_value={"lan_passphrase": "test-lan-pass"}):
                with patch('routes.discovery_routes.BAY_MAP_LOCK'):
                    # Patch only bay_map.json open to simulate file not found
                    original_open = open
                    def selective_open(path, *args, **kwargs):
                        if 'bay_map.json' in path:
                            raise FileNotFoundError
                        return original_open(path, *args, **kwargs)
                    with patch('builtins.open', side_effect=selective_open):
                        response = admin_session.post('/api/admin/apply-slot-mapping', json={"invalid_bay": {"device_path": "/dev/sda"}})
                        # Should return 500 due to file not found or 400 if bay doesn't exist
                        assert response.status_code in [400, 500]

    def test_apply_slot_mapping_invalid_device_path(self, admin_session):
        """Test that invalid device path is rejected."""
        with patch('routes._shared.is_local_request', return_value=False):
            with patch('routes._shared.load_policy', return_value={"lan_passphrase": "test-lan-pass"}):
                with patch('routes.discovery_routes.BAY_MAP_LOCK'):
                    with patch('builtins.open', MagicMock()):
                        with patch('json.load', return_value={"bay1": {"by_path": "/dev/sdb"}}):
                            with patch('routes.discovery_routes.normalize_bay_map_document', return_value=({"bay1": {}}, {})):
                                with patch('routes.discovery_routes.validate_device_path', return_value=False):
                                    response = admin_session.post('/api/admin/apply-slot-mapping', json={"bay1": {"device_path": "invalid-path"}})
                                    assert response.status_code == 400
                                    data = json.loads(response.data)
                                    assert "Validation failed" in data.get("error", "")

    def test_apply_slot_mapping_valid_nvme_path(self, admin_session):
        """Test that valid NVMe path is accepted."""
        with patch('routes._shared.is_local_request', return_value=False):
            with patch('routes._shared.load_policy', return_value={"lan_passphrase": "test-lan-pass"}):
                with patch('routes.discovery_routes.BAY_MAP_LOCK'):
                    with patch('builtins.open', MagicMock()):
                        with patch('json.load', return_value={"bay1": {"by_path": "/dev/sdb"}}):
                            with patch('routes.discovery_routes.normalize_bay_map_document', return_value=({"bay1": {}}, {})):
                                with patch('routes.discovery_routes.validate_device_path', return_value=True):
                                    with patch('routes.discovery_routes.save_bay_map'):
                                        response = admin_session.post('/api/admin/apply-slot-mapping', json={"bay1": {"device_path": "/dev/nvme0n1"}})
                                        assert response.status_code == 200

    def test_apply_slot_mapping_empty_slot_with_projected_path(self, admin_session):
        """Test that empty slot with projected by-path is accepted."""
        with patch('routes._shared.is_local_request', return_value=False):
            with patch('routes._shared.load_policy', return_value={"lan_passphrase": "test-lan-pass"}):
                with patch('routes.discovery_routes.BAY_MAP_LOCK'):
                    with patch('builtins.open', MagicMock()):
                        with patch('json.load', return_value={"bay1": {"by_path": "/dev/sdb"}}):
                            with patch('routes.discovery_routes.normalize_bay_map_document', return_value=({"bay1": {}}, {})):
                                with patch('routes.discovery_routes.save_bay_map'):
                                    response = admin_session.post('/api/admin/apply-slot-mapping', json={
                                        "bay1": {
                                            "is_empty": True,
                                            "projected_by_path": "pci-0000:01:00.0-scsi-0:0:0:0"
                                        }
                                    })
                                    assert response.status_code == 200

    def test_apply_slot_mapping_invalid_projected_path(self, admin_session):
        """Test that invalid projected by-path is rejected."""
        with patch('routes._shared.is_local_request', return_value=False):
            with patch('routes._shared.load_policy', return_value={"lan_passphrase": "test-lan-pass"}):
                with patch('routes.discovery_routes.BAY_MAP_LOCK'):
                    with patch('builtins.open', MagicMock()):
                        with patch('json.load', return_value={"bay1": {"by_path": "/dev/sdb"}}):
                            with patch('routes.discovery_routes.normalize_bay_map_document', return_value=({"bay1": {}}, {})):
                                response = admin_session.post('/api/admin/apply-slot-mapping', json={
                                    "bay1": {
                                        "is_empty": True,
                                        "projected_by_path": "invalid-path"
                                    }
                                })
                                assert response.status_code == 400
                                data = json.loads(response.data)
                                assert "Validation failed" in data.get("error", "")

    def test_apply_slot_mapping_local_request_allowed(self, client):
        """Test that localhost requests bypass authentication."""
        with patch('routes._shared.is_local_request', return_value=True):
            with patch('routes._shared.load_policy', return_value={"lan_passphrase": "test-lan-pass"}):
                with patch('routes.discovery_routes.BAY_MAP_LOCK'):
                    with patch('builtins.open', MagicMock()):
                        with patch('json.load', return_value={"bay1": {"by_path": "/dev/sdb"}}):
                            with patch('routes.discovery_routes.normalize_bay_map_document', return_value=({"bay1": {}}, {})):
                                with patch('routes.discovery_routes.validate_device_path', return_value=True):
                                    with patch('routes.discovery_routes.save_bay_map'):
                                        response = client.post('/api/admin/apply-slot-mapping', json={"bay1": {"device_path": "/dev/sda"}})
                                        assert response.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
