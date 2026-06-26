# Tests for backend/routes/bay_mapping_routes.py
import pytest
import sys
import os
import json
import tempfile
from unittest.mock import patch, MagicMock, Mock, mock_open

# Add backend to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))


@pytest.fixture(autouse=True)
def clear_bay_mapping_routes_cache():
    """Clear the bay_mapping_routes module cache so each test imports it fresh.

    This ensures auth decorator patches and per-test function mocks are applied
    to a newly loaded module, avoiding stale MagicMock view functions.
    """
    import sys
    sys.modules.pop('routes.bay_mapping_routes', None)
    yield


class TestGetAdminBayMap:
    """Test GET /api/admin/bay-map endpoint."""

    @patch('routes.bay_mapping_routes.get_config_dir')
    @patch('routes.bay_mapping_routes.normalize_bay_map_document')
    @patch('routes.bay_mapping_routes.compose_bay_map_document')
    @patch('routes.admin_routes.require_admin_auth', new=lambda f: f)
    def test_get_bay_map_success(self, mock_compose, mock_normalize, mock_get_dir):
        """Test successful bay map retrieval."""
        from routes.bay_mapping_routes import bay_mapping_bp
        from flask import Flask
        
        app = Flask(__name__)
        app.register_blueprint(bay_mapping_bp)
        mock_get_dir.return_value = '/tmp/config'
        mock_normalize.return_value = ({'bay0': {'role': 'wipe'}}, {'version': '1.0'})
        mock_compose.return_value = {'bay0': {'role': 'wipe'}, 'metadata': {'version': '1.0'}}
        
        with patch('builtins.open', mock_open(read_data='{"bay0": {"role": "wipe"}}')):
            with app.test_client() as client:
                response = client.get('/api/admin/bay-map')
                assert response.status_code == 200

    @patch('routes.bay_mapping_routes.get_config_dir')
    @patch('routes.bay_mapping_routes.normalize_bay_map_document')
    @patch('routes.bay_mapping_routes.compose_bay_map_document')
    @patch('routes.admin_routes.require_admin_auth', new=lambda f: f)
    def test_get_bay_map_missing_file(self, mock_compose, mock_normalize, mock_get_dir):
        """Test bay map retrieval when file doesn't exist."""
        from routes.bay_mapping_routes import bay_mapping_bp
        from flask import Flask
        
        app = Flask(__name__)
        app.register_blueprint(bay_mapping_bp)
        mock_get_dir.return_value = '/tmp/config'
        mock_normalize.return_value = ({}, {})
        mock_compose.return_value = {'metadata': {}}
        
        with patch('builtins.open', side_effect=FileNotFoundError):
            with app.test_client() as client:
                response = client.get('/api/admin/bay-map')
                assert response.status_code == 200

    @patch('routes.bay_mapping_routes.get_config_dir')
    @patch('routes.admin_routes.require_admin_auth', new=lambda f: f)
    def test_get_bay_map_error(self, mock_get_dir):
        """Test bay map retrieval error handling."""
        from routes.bay_mapping_routes import bay_mapping_bp
        from flask import Flask
        
        app = Flask(__name__)
        app.register_blueprint(bay_mapping_bp)
        mock_get_dir.side_effect = Exception("Config error")
        
        with app.test_client() as client:
            response = client.get('/api/admin/bay-map')
            assert response.status_code == 500


class TestGetUnmappedDrives:
    """Test GET /api/admin/unmapped-drives endpoint."""

    @patch('routes.bay_mapping_routes.get_config_dir')
    @patch('routes.bay_mapping_routes.normalize_bay_map_document')
    @patch('routes.bay_mapping_routes.get_os_by_path')
    @patch('routes.bay_mapping_routes.get_smart_identity')
    @patch('routes.admin_routes.require_admin_auth', new=lambda f: f)
    def test_get_unmapped_drives_success(self, mock_smart, mock_os, mock_normalize, mock_get_dir):
        """Test successful unmapped drives retrieval using identity-only SMART."""
        from routes.bay_mapping_routes import bay_mapping_bp, _UNMAPPED_DRIVE_CACHE
        from flask import Flask
        
        app = Flask(__name__)
        app.register_blueprint(bay_mapping_bp)
        
        # Clear module cache so earlier tests do not interfere with this one.
        _UNMAPPED_DRIVE_CACHE.clear()
        mock_get_dir.return_value = '/tmp/config'
        mock_normalize.return_value = ({'bay0': {'by_path': '/dev/disk/by-path/pci-0000:00:1f.2-ata-1'}}, {})
        mock_os.return_value = ('/dev/sda', '/dev/disk/by-path/pci-0000:00:1f.2-ata-1')
        mock_smart.return_value = {
            'model': 'Test Drive',
            'serial': 'SN123',
            'capacity_str': '500 GB',
            'capacity_bytes': 500107862016
        }
        
        with patch('builtins.open', mock_open(read_data='{"bay0": {"by_path": "/dev/disk/by-path/pci-0000:00:1f.2-ata-1"}}')):
            with patch('os.path.exists', return_value=True):
                with patch('os.listdir', return_value=['pci-0000:00:1f.2-ata-2']):
                    with patch('os.path.islink', return_value=True):
                        with patch('os.path.realpath', side_effect=lambda x: x.replace('/dev/disk/by-path/', '/dev/')):
                            with app.test_client() as client:
                                response = client.get('/api/admin/unmapped-drives')
                                assert response.status_code == 200
                                data = json.loads(response.data)
                                assert len(data) == 1
                                assert data[0]['model'] == 'Test Drive'
                                assert data[0]['serial'] == 'SN123'

    @patch('routes.bay_mapping_routes.get_config_dir')
    @patch('routes.bay_mapping_routes.normalize_bay_map_document')
    @patch('routes.bay_mapping_routes.get_os_by_path')
    @patch('routes.bay_mapping_routes.get_smart_identity')
    @patch('routes.admin_routes.require_admin_auth', new=lambda f: f)
    def test_get_unmapped_drives_cache_hit(self, mock_smart, mock_os, mock_normalize, mock_get_dir):
        """Test that identity data is cached and smartctl is only called once per device."""
        from routes.bay_mapping_routes import bay_mapping_bp, _UNMAPPED_DRIVE_CACHE
        from flask import Flask
        
        app = Flask(__name__)
        app.register_blueprint(bay_mapping_bp)
        
        _UNMAPPED_DRIVE_CACHE.clear()
        mock_get_dir.return_value = '/tmp/config'
        mock_normalize.return_value = ({'bay0': {'by_path': '/dev/disk/by-path/pci-0000:00:1f.2-ata-1'}}, {})
        mock_os.return_value = ('/dev/sda', '/dev/disk/by-path/pci-0000:00:1f.2-ata-1')
        mock_smart.return_value = {
            'model': 'Test Drive',
            'serial': 'SN123',
            'capacity_str': '500 GB',
            'capacity_bytes': 500107862016
        }
        
        with patch('builtins.open', mock_open(read_data='{"bay0": {"by_path": "/dev/disk/by-path/pci-0000:00:1f.2-ata-1"}}')):
            with patch('os.path.exists', return_value=True):
                with patch('os.listdir', return_value=['pci-0000:00:1f.2-ata-2']):
                    with patch('os.path.islink', return_value=True):
                        with patch('os.path.realpath', side_effect=lambda x: x.replace('/dev/disk/by-path/', '/dev/')):
                            with app.test_client() as client:
                                response1 = client.get('/api/admin/unmapped-drives')
                                assert response1.status_code == 200
                                response2 = client.get('/api/admin/unmapped-drives')
                                assert response2.status_code == 200
                                assert mock_smart.call_count == 1

    @patch('routes.bay_mapping_routes.get_config_dir')
    @patch('routes.bay_mapping_routes.normalize_bay_map_document')
    @patch('routes.bay_mapping_routes.get_os_by_path')
    @patch('routes.bay_mapping_routes.get_smart_identity')
    @patch('routes.admin_routes.require_admin_auth', new=lambda f: f)
    def test_get_unmapped_drives_failure_not_cached(self, mock_smart, mock_os, mock_normalize, mock_get_dir):
        """Test that identity failures are not cached and retried on the next request."""
        from routes.bay_mapping_routes import bay_mapping_bp, _UNMAPPED_DRIVE_CACHE
        from flask import Flask

        app = Flask(__name__)
        app.register_blueprint(bay_mapping_bp)

        _UNMAPPED_DRIVE_CACHE.clear()
        mock_get_dir.return_value = '/tmp/config'
        mock_normalize.return_value = ({'bay0': {'by_path': '/dev/disk/by-path/pci-0000:00:1f.2-ata-1'}}, {})
        mock_os.return_value = ('/dev/sda', '/dev/disk/by-path/pci-0000:00:1f.2-ata-1')
        # Failure sentinel: no model, serial, or raw output.
        mock_smart.return_value = {
            'model': None,
            'serial': None,
            'capacity_str': '-',
            'capacity_bytes': None,
            'raw': None,
            'status': 'UNKNOWN'
        }

        with patch('builtins.open', mock_open(read_data='{"bay0": {"by_path": "/dev/disk/by-path/pci-0000:00:1f.2-ata-1"}}')):
            with patch('os.path.exists', return_value=True):
                with patch('os.listdir', return_value=['pci-0000:00:1f.2-ata-2']):
                    with patch('os.path.islink', return_value=True):
                        with patch('os.path.realpath', side_effect=lambda x: x.replace('/dev/disk/by-path/', '/dev/')):
                            with app.test_client() as client:
                                response1 = client.get('/api/admin/unmapped-drives')
                                assert response1.status_code == 200
                                response2 = client.get('/api/admin/unmapped-drives')
                                assert response2.status_code == 200
                                # Failure sentinel should not be cached, so smartctl is retried.
                                assert mock_smart.call_count == 2

    @patch('routes.bay_mapping_routes.get_config_dir')
    @patch('routes.bay_mapping_routes.normalize_bay_map_document')
    @patch('routes.admin_routes.require_admin_auth', new=lambda f: f)
    def test_get_unmapped_drives_error(self, mock_normalize, mock_get_dir):
        """Test unmapped drives error handling."""
        from routes.bay_mapping_routes import bay_mapping_bp
        from flask import Flask
        
        app = Flask(__name__)
        app.register_blueprint(bay_mapping_bp)
        mock_get_dir.side_effect = Exception("Config error")
        
        with app.test_client() as client:
            response = client.get('/api/admin/unmapped-drives')
            assert response.status_code == 500


class TestAutoDetectBays:
    """Test POST /api/admin/auto-detect-bays endpoint."""

    @patch('routes.bay_mapping_routes.get_config_dir')
    @patch('routes.bay_mapping_routes.normalize_bay_map_document')
    @patch('routes.bay_mapping_routes.save_bay_map')
    @patch('routes.bay_mapping_routes.compose_bay_map_document')
    @patch('routes.admin_routes.require_admin_auth', new=lambda f: f)
    def test_auto_detect_bays_no_slots(self, mock_compose, mock_save, mock_normalize, mock_get_dir):
        """Test auto-detect when no slots are found."""
        from routes.bay_mapping_routes import bay_mapping_bp
        from flask import Flask
        
        app = Flask(__name__)
        app.register_blueprint(bay_mapping_bp)
        mock_get_dir.return_value = '/tmp/config'
        mock_normalize.return_value = ({}, {})
        mock_compose.return_value = {'metadata': {}}
        
        with patch('os.path.exists', return_value=False):
            with app.test_client() as client:
                response = client.post('/api/admin/auto-detect-bays')
                assert response.status_code == 200
                data = json.loads(response.data)
                assert data['status'] == 'success'

    @patch('routes.bay_mapping_routes.get_config_dir')
    @patch('routes.bay_mapping_routes.normalize_bay_map_document')
    @patch('routes.bay_mapping_routes.save_bay_map')
    @patch('routes.bay_mapping_routes.compose_bay_map_document')
    @patch('routes.admin_routes.require_admin_auth', new=lambda f: f)
    def test_auto_detect_bays_with_slots(self, mock_compose, mock_save, mock_normalize, mock_get_dir):
        """Test auto-detect when slots are found."""
        from routes.bay_mapping_routes import bay_mapping_bp
        from flask import Flask
        
        app = Flask(__name__)
        app.register_blueprint(bay_mapping_bp)
        mock_get_dir.return_value = '/tmp/config'
        mock_normalize.return_value = ({}, {})
        mock_compose.return_value = {'bay0': {'by_path': '/dev/disk/by-path/test'}, 'metadata': {}}
        
        with patch('os.path.exists', side_effect=lambda x: x == '/dev/disk/by-path/'):
            with patch('os.listdir', return_value=['pci-0000:00:1f.2-ata-1']):
                with patch('os.path.islink', return_value=True):
                    with patch('os.path.realpath', return_value='/dev/sda'):
                        with app.test_client() as client:
                            response = client.post('/api/admin/auto-detect-bays')
                            assert response.status_code == 200

    @patch('routes.bay_mapping_routes.get_config_dir')
    @patch('routes.admin_routes.require_admin_auth', new=lambda f: f)
    def test_auto_detect_bays_error(self, mock_get_dir):
        """Test auto-detect error handling."""
        from routes.bay_mapping_routes import bay_mapping_bp
        from flask import Flask
        
        app = Flask(__name__)
        app.register_blueprint(bay_mapping_bp)
        mock_get_dir.side_effect = Exception("Config error")
        
        with app.test_client() as client:
            response = client.post('/api/admin/auto-detect-bays')
            assert response.status_code == 500


class TestUpdateBayMap:
    """Test POST /api/admin/save-bay-map endpoint."""

    @patch('routes.bay_mapping_routes.get_config_dir')
    @patch('routes.bay_mapping_routes.load_layout_templates')
    @patch('routes.bay_mapping_routes.normalize_bay_map_document')
    @patch('routes.bay_mapping_routes.validate_layout_metadata')
    @patch('routes.bay_mapping_routes.save_bay_map')
    @patch('routes.bay_mapping_routes.compose_bay_map_document')
    @patch('routes.admin_routes.require_admin_auth', new=lambda f: f)
    def test_update_bay_map_success(self, mock_compose, mock_save, mock_validate, mock_normalize, mock_templates, mock_get_dir):
        """Test successful bay map update."""
        from routes.bay_mapping_routes import bay_mapping_bp
        from flask import Flask
        
        app = Flask(__name__)
        app.register_blueprint(bay_mapping_bp)
        mock_get_dir.return_value = '/tmp/config'
        mock_templates.return_value = ({}, False)
        mock_normalize.return_value = ({'bay0': {'role': 'wipe'}}, {})
        mock_validate.return_value = None
        mock_compose.return_value = {'bay0': {'role': 'wipe'}, 'metadata': {}}
        
        payload = {'bay0': {'role': 'wipe', 'type': 'sas_sata'}}
        
        with app.test_client() as client:
            response = client.post('/api/admin/save-bay-map', 
                                   json=payload,
                                   content_type='application/json')
            assert response.status_code == 200

    @patch('routes.bay_mapping_routes.get_config_dir')
    @patch('routes.admin_routes.require_admin_auth', new=lambda f: f)
    def test_update_bay_map_invalid_payload(self, mock_get_dir):
        """Test bay map update with invalid payload."""
        from routes.bay_mapping_routes import bay_mapping_bp
        from flask import Flask
        
        app = Flask(__name__)
        app.register_blueprint(bay_mapping_bp)
        mock_get_dir.return_value = '/tmp/config'
        
        with app.test_client() as client:
            response = client.post('/api/admin/save-bay-map', 
                                   json="invalid",
                                   content_type='application/json')
            assert response.status_code == 400

    @patch('routes.bay_mapping_routes.get_config_dir')
    @patch('routes.bay_mapping_routes.load_layout_templates')
    @patch('routes.bay_mapping_routes.normalize_bay_map_document')
    @patch('routes.admin_routes.require_admin_auth', new=lambda f: f)
    def test_update_bay_map_no_bays(self, mock_normalize, mock_templates, mock_get_dir):
        """Test bay map update with no bays."""
        from routes.bay_mapping_routes import bay_mapping_bp
        from flask import Flask
        
        app = Flask(__name__)
        app.register_blueprint(bay_mapping_bp)
        mock_get_dir.return_value = '/tmp/config'
        mock_templates.return_value = ({}, False)
        mock_normalize.return_value = ({}, {})
        
        with app.test_client() as client:
            response = client.post('/api/admin/save-bay-map', 
                                   json={},
                                   content_type='application/json')
            assert response.status_code == 400

    @patch('routes.bay_mapping_routes.get_config_dir')
    @patch('routes.admin_routes.require_admin_auth', new=lambda f: f)
    def test_update_bay_map_error(self, mock_get_dir):
        """Test bay map update error handling."""
        from routes.bay_mapping_routes import bay_mapping_bp
        from flask import Flask
        
        app = Flask(__name__)
        app.register_blueprint(bay_mapping_bp)
        mock_get_dir.side_effect = Exception("Config error")
        
        with app.test_client() as client:
            response = client.post('/api/admin/save-bay-map', 
                                   json={'bay0': {'role': 'wipe'}},
                                   content_type='application/json')
            assert response.status_code == 500

    @patch('routes.bay_mapping_routes.get_config_dir')
    @patch('routes.bay_mapping_routes.load_layout_templates')
    @patch('routes.bay_mapping_routes.normalize_bay_map_document')
    @patch('routes.bay_mapping_routes.validate_layout_metadata')
    @patch('routes.bay_mapping_routes.save_bay_map')
    @patch('routes.bay_mapping_routes.compose_bay_map_document')
    @patch('routes.admin_routes.require_admin_auth', new=lambda f: f)
    def test_update_bay_map_invalidates_unmapped_cache(self, mock_compose, mock_save, mock_validate, mock_normalize, mock_templates, mock_get_dir):
        """Test that saving the bay map clears the unmapped-drive identity cache."""
        from routes.bay_mapping_routes import bay_mapping_bp, _UNMAPPED_DRIVE_CACHE
        from flask import Flask

        app = Flask(__name__)
        app.register_blueprint(bay_mapping_bp)

        _UNMAPPED_DRIVE_CACHE.clear()
        _UNMAPPED_DRIVE_CACHE['/dev/sdb'] = {
            'data': {'model': 'Cached', 'serial': 'OLD'},
            'timestamp': 0
        }
        mock_get_dir.return_value = '/tmp/config'
        mock_templates.return_value = ({}, False)
        mock_normalize.return_value = ({'bay0': {'role': 'wipe'}}, {})
        mock_validate.return_value = None
        mock_compose.return_value = {'bay0': {'role': 'wipe'}, 'metadata': {}}

        with app.test_client() as client:
            response = client.post('/api/admin/save-bay-map',
                                   json={'bay0': {'role': 'wipe'}},
                                   content_type='application/json')
            assert response.status_code == 200
            assert len(_UNMAPPED_DRIVE_CACHE) == 0
