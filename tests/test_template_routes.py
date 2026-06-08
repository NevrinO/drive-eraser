# Tests for backend/routes/template_routes.py
import pytest
import sys
import os
import json
import io
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock, Mock, mock_open

# Add backend to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))


class TestLayoutTemplatesCRUD:
    """Test GET/POST/PUT/DELETE /api/admin/layout-templates endpoint."""

    @patch('routes.template_routes.get_config_dir')
    @patch('routes.template_routes.load_layout_templates')
    @patch('routes.admin_routes.require_admin_auth')
    def test_get_templates_success(self, mock_auth, mock_load, mock_get_dir):
        """Test successful template retrieval."""
        from routes.template_routes import template_bp
        from flask import Flask
        
        app = Flask(__name__)
        app.register_blueprint(template_bp)
        
        mock_auth.return_value = lambda f: f
        mock_get_dir.return_value = '/tmp/config'
        mock_load.return_value = ({
            'template1': {'id': 'template1', 'name': 'Test'}
        }, False)
        
        with app.test_client() as client:
            response = client.get('/api/admin/layout-templates')
            assert response.status_code == 200
            data = json.loads(response.data)
            assert 'templates' in data
            assert 'supported_traversals' in data

    @patch('routes.template_routes.get_config_dir')
    @patch('routes.template_routes.load_layout_templates')
    @patch('routes.template_routes.validate_template')
    @patch('routes.template_routes.save_layout_templates')
    @patch('routes.admin_routes.require_admin_auth')
    def test_post_template_success(self, mock_auth, mock_save, mock_validate, mock_load, mock_get_dir):
        """Test successful template creation."""
        from routes.template_routes import template_bp
        from flask import Flask
        
        app = Flask(__name__)
        app.register_blueprint(template_bp)
        
        mock_auth.return_value = lambda f: f
        mock_get_dir.return_value = '/tmp/config'
        mock_load.return_value = ({}, False)
        mock_validate.return_value = None
        
        payload = {'id': 'template1', 'name': 'Test Template'}
        
        with app.test_client() as client:
            response = client.post('/api/admin/layout-templates',
                                   json=payload,
                                   content_type='application/json')
            assert response.status_code == 201
            data = json.loads(response.data)
            assert data['status'] == 'created'

    @patch('routes.template_routes.get_config_dir')
    @patch('routes.template_routes.load_layout_templates')
    @patch('routes.admin_routes.require_admin_auth')
    def test_post_template_missing_id(self, mock_auth, mock_load, mock_get_dir):
        """Test template creation with missing ID."""
        from routes.template_routes import template_bp
        from flask import Flask
        
        app = Flask(__name__)
        app.register_blueprint(template_bp)
        
        mock_auth.return_value = lambda f: f
        mock_get_dir.return_value = '/tmp/config'
        mock_load.return_value = ({}, False)
        
        payload = {'name': 'Test Template'}
        
        with app.test_client() as client:
            response = client.post('/api/admin/layout-templates',
                                   json=payload,
                                   content_type='application/json')
            assert response.status_code == 400

    @patch('routes.template_routes.get_config_dir')
    @patch('routes.template_routes.load_layout_templates')
    @patch('routes.template_routes.validate_template')
    @patch('routes.admin_routes.require_admin_auth')
    def test_post_template_already_exists(self, mock_auth, mock_validate, mock_load, mock_get_dir):
        """Test template creation when ID already exists."""
        from routes.template_routes import template_bp
        from flask import Flask
        
        app = Flask(__name__)
        app.register_blueprint(template_bp)
        
        mock_auth.return_value = lambda f: f
        mock_get_dir.return_value = '/tmp/config'
        mock_load.return_value = ({'template1': {'id': 'template1'}}, False)
        mock_validate.return_value = None
        
        payload = {'id': 'template1', 'name': 'Test Template'}
        
        with app.test_client() as client:
            response = client.post('/api/admin/layout-templates',
                                   json=payload,
                                   content_type='application/json')
            assert response.status_code == 409

    @patch('routes.template_routes.get_config_dir')
    @patch('routes.template_routes.load_layout_templates')
    @patch('routes.template_routes.validate_template')
    @patch('routes.template_routes.save_layout_templates')
    @patch('routes.admin_routes.require_admin_auth')
    def test_put_template_success(self, mock_auth, mock_save, mock_validate, mock_load, mock_get_dir):
        """Test successful template update."""
        from routes.template_routes import template_bp
        from flask import Flask
        
        app = Flask(__name__)
        app.register_blueprint(template_bp)
        
        mock_auth.return_value = lambda f: f
        mock_get_dir.return_value = '/tmp/config'
        mock_load.return_value = ({'template1': {'id': 'template1'}}, False)
        mock_validate.return_value = None
        
        payload = {'id': 'template1', 'name': 'Updated Template'}
        
        with app.test_client() as client:
            response = client.put('/api/admin/layout-templates',
                                  json=payload,
                                  content_type='application/json')
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data['status'] == 'updated'

    @patch('routes.template_routes.get_config_dir')
    @patch('routes.template_routes.load_layout_templates')
    @patch('routes.admin_routes.require_admin_auth')
    def test_put_template_not_found(self, mock_auth, mock_load, mock_get_dir):
        """Test template update when template doesn't exist."""
        from routes.template_routes import template_bp
        from flask import Flask
        
        app = Flask(__name__)
        app.register_blueprint(template_bp)
        
        mock_auth.return_value = lambda f: f
        mock_get_dir.return_value = '/tmp/config'
        mock_load.return_value = ({}, False)
        
        payload = {'id': 'template1', 'name': 'Updated Template'}
        
        with app.test_client() as client:
            response = client.put('/api/admin/layout-templates',
                                  json=payload,
                                  content_type='application/json')
            assert response.status_code == 404

    @patch('routes.template_routes.get_config_dir')
    @patch('routes.template_routes.load_layout_templates')
    @patch('routes.template_routes.save_layout_templates')
    @patch('routes.admin_routes.require_admin_auth')
    def test_delete_template_success(self, mock_auth, mock_save, mock_load, mock_get_dir):
        """Test successful template deletion."""
        from routes.template_routes import template_bp
        from flask import Flask
        
        app = Flask(__name__)
        app.register_blueprint(template_bp)
        
        mock_auth.return_value = lambda f: f
        mock_get_dir.return_value = '/tmp/config'
        mock_load.return_value = ({'template1': {'id': 'template1'}}, False)
        
        payload = {'id': 'template1'}
        
        with app.test_client() as client:
            response = client.delete('/api/admin/layout-templates',
                                     json=payload,
                                     content_type='application/json')
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data['status'] == 'deleted'

    @patch('routes.template_routes.get_config_dir')
    @patch('routes.template_routes.load_layout_templates')
    @patch('routes.admin_routes.require_admin_auth')
    def test_delete_template_not_found(self, mock_auth, mock_load, mock_get_dir):
        """Test template deletion when template doesn't exist."""
        from routes.template_routes import template_bp
        from flask import Flask
        
        app = Flask(__name__)
        app.register_blueprint(template_bp)
        
        mock_auth.return_value = lambda f: f
        mock_get_dir.return_value = '/tmp/config'
        mock_load.return_value = ({}, False)
        
        payload = {'id': 'template1'}
        
        with app.test_client() as client:
            response = client.delete('/api/admin/layout-templates',
                                     json=payload,
                                     content_type='application/json')
            assert response.status_code == 404

    @patch('routes.admin_routes.require_admin_auth')
    def test_crud_invalid_method(self, mock_auth):
        """Test CRUD with invalid request body type."""
        from routes.template_routes import template_bp
        from flask import Flask
        
        app = Flask(__name__)
        app.register_blueprint(template_bp)
        
        mock_auth.return_value = lambda f: f
        
        with app.test_client() as client:
            response = client.post('/api/admin/layout-templates',
                                   data="invalid",
                                   content_type='application/json')
            assert response.status_code == 400


class TestLayoutTemplatesExport:
    """Test GET /api/admin/layout-templates/export endpoint."""

    @patch('routes.template_routes.get_config_dir')
    @patch('routes.template_routes.load_layout_templates')
    @patch('routes.admin_routes.require_admin_auth')
    def test_export_templates_success(self, mock_auth, mock_load, mock_get_dir):
        """Test successful template export."""
        from routes.template_routes import template_bp
        from flask import Flask
        
        app = Flask(__name__)
        app.register_blueprint(template_bp)
        
        mock_auth.return_value = lambda f: f
        mock_get_dir.return_value = '/tmp/config'
        mock_load.return_value = ({
            'template1': {'id': 'template1', 'name': 'Test'}
        }, False)
        
        with app.test_client() as client:
            response = client.get('/api/admin/layout-templates/export')
            assert response.status_code == 200
            assert response.content_type == 'application/json'

    @patch('routes.template_routes.get_config_dir')
    @patch('routes.admin_routes.require_admin_auth')
    def test_export_templates_error(self, mock_auth, mock_get_dir):
        """Test template export error handling."""
        from routes.template_routes import template_bp
        from flask import Flask
        
        app = Flask(__name__)
        app.register_blueprint(template_bp)
        
        mock_auth.return_value = lambda f: f
        mock_get_dir.side_effect = Exception("Config error")
        
        with app.test_client() as client:
            response = client.get('/api/admin/layout-templates/export')
            assert response.status_code == 500


class TestLayoutTemplatesImport:
    """Test POST /api/admin/layout-templates/import endpoint."""

    @patch('routes.template_routes.get_config_dir')
    @patch('routes.template_routes.load_layout_templates')
    @patch('routes.template_routes.validate_template')
    @patch('routes.template_routes.save_layout_templates')
    @patch('routes.admin_routes.require_admin_auth')
    def test_import_templates_success(self, mock_auth, mock_save, mock_validate, mock_load, mock_get_dir):
        """Test successful template import."""
        from routes.template_routes import template_bp
        from flask import Flask
        
        app = Flask(__name__)
        app.register_blueprint(template_bp)
        
        mock_auth.return_value = lambda f: f
        mock_get_dir.return_value = '/tmp/config'
        mock_load.return_value = ({}, False)
        mock_validate.return_value = None
        
        import_data = {
            'templates': {
                'template1': {'id': 'template1', 'name': 'Imported'}
            },
            'exported_at': datetime.now(timezone.utc).isoformat(),
            'version': '1.0'
        }
        
        file_content = json.dumps(import_data).encode('utf-8')
        
        with app.test_client() as client:
            response = client.post('/api/admin/layout-templates/import',
                                   data={'file': (io.BytesIO(file_content), 'templates.json')})
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data['status'] == 'imported'

    @patch('routes.admin_routes.require_admin_auth')
    def test_import_no_file(self, mock_auth):
        """Test import with no file provided."""
        from routes.template_routes import template_bp
        from flask import Flask
        
        app = Flask(__name__)
        app.register_blueprint(template_bp)
        
        mock_auth.return_value = lambda f: f
        
        with app.test_client() as client:
            response = client.post('/api/admin/layout-templates/import')
            assert response.status_code == 400

    @patch('routes.admin_routes.require_admin_auth')
    def test_import_file_too_large(self, mock_auth):
        """Test import with file exceeding size limit."""
        from routes.template_routes import template_bp
        from flask import Flask
        
        app = Flask(__name__)
        app.register_blueprint(template_bp)
        
        mock_auth.return_value = lambda f: f
        
        # Create a file larger than 64KB
        large_content = b'x' * (65 * 1024)
        
        with app.test_client() as client:
            response = client.post('/api/admin/layout-templates/import',
                                   data={'file': (io.BytesIO(large_content), 'templates.json')})
            assert response.status_code == 400

    @patch('routes.admin_routes.require_admin_auth')
    def test_import_invalid_json(self, mock_auth):
        """Test import with invalid JSON."""
        from routes.template_routes import template_bp
        from flask import Flask
        
        app = Flask(__name__)
        app.register_blueprint(template_bp)
        
        mock_auth.return_value = lambda f: f
        
        with app.test_client() as client:
            response = client.post('/api/admin/layout-templates/import',
                                   data={'file': (io.BytesIO(b'not valid json'), 'templates.json')})
            assert response.status_code == 400

    @patch('routes.admin_routes.require_admin_auth')
    def test_import_missing_templates_key(self, mock_auth):
        """Test import with missing templates key."""
        from routes.template_routes import template_bp
        from flask import Flask
        
        app = Flask(__name__)
        app.register_blueprint(template_bp)
        
        mock_auth.return_value = lambda f: f
        
        import_data = {'version': '1.0'}
        
        with app.test_client() as client:
            response = client.post('/api/admin/layout-templates/import',
                                   data={'file': (io.BytesIO(json.dumps(import_data).encode('utf-8')), 'templates.json')})
            assert response.status_code == 400

    @patch('routes.admin_routes.require_admin_auth')
    def test_import_too_many_templates(self, mock_auth):
        """Test import with too many templates (DoS prevention)."""
        from routes.template_routes import template_bp
        from flask import Flask
        
        app = Flask(__name__)
        app.register_blueprint(template_bp)
        
        mock_auth.return_value = lambda f: f
        
        # Create 101 templates
        import_data = {
            'templates': {f'template{i}': {'id': f'template{i}'} for i in range(101)},
            'version': '1.0'
        }
        
        with app.test_client() as client:
            response = client.post('/api/admin/layout-templates/import',
                                   data={'file': (io.BytesIO(json.dumps(import_data).encode('utf-8')), 'templates.json')})
            assert response.status_code == 400

    @patch('routes.template_routes.get_config_dir')
    @patch('routes.template_routes.load_layout_templates')
    @patch('routes.template_routes.validate_template')
    @patch('routes.admin_routes.require_admin_auth')
    def test_import_validation_error(self, mock_auth, mock_validate, mock_load, mock_get_dir):
        """Test import with template validation errors."""
        from routes.template_routes import template_bp
        from flask import Flask
        
        app = Flask(__name__)
        app.register_blueprint(template_bp)
        
        mock_auth.return_value = lambda f: f
        mock_get_dir.return_value = '/tmp/config'
        mock_load.return_value = ({}, False)
        mock_validate.return_value = "Invalid template structure"
        
        import_data = {
            'templates': {
                'template1': {'id': 'template1', 'name': 'Invalid'}
            },
            'version': '1.0'
        }
        
        with app.test_client() as client:
            response = client.post('/api/admin/layout-templates/import',
                                   data={'file': (io.BytesIO(json.dumps(import_data).encode('utf-8')), 'templates.json')})
            assert response.status_code == 400


class TestAdminApplyTemplate:
    """Test POST /api/admin/apply-template endpoint."""

    @patch('routes.template_routes.get_config_dir')
    @patch('routes.template_routes.load_layout_templates')
    @patch('routes.template_routes.normalize_bay_map_document')
    @patch('routes.template_routes.apply_template')
    @patch('routes.template_routes.validate_layout_metadata')
    @patch('routes.template_routes.compose_bay_map_document')
    @patch('routes.admin_routes.require_admin_auth')
    def test_apply_template_success(self, mock_auth, mock_compose, mock_validate, mock_apply, mock_normalize, mock_load, mock_get_dir):
        """Test successful template application."""
        from routes.template_routes import template_bp
        from flask import Flask
        
        app = Flask(__name__)
        app.register_blueprint(template_bp)
        
        mock_auth.return_value = lambda f: f
        mock_get_dir.return_value = '/tmp/config'
        mock_load.return_value = ({
            'template1': {'id': 'template1', 'name': 'Test'}
        }, False)
        mock_normalize.return_value = ({'bay0': {'role': 'wipe'}}, {})
        mock_apply.return_value = ({'bay0': {'role': 'wipe'}}, 'sequential')
        mock_validate.return_value = None
        mock_compose.return_value = {'bay0': {'role': 'wipe'}, 'metadata': {}}
        
        with patch('builtins.open', mock_open(read_data='{"bay0": {"role": "wipe"}}')):
            payload = {'template_id': 'template1'}
            
            with app.test_client() as client:
                response = client.post('/api/admin/apply-template',
                                       json=payload,
                                       content_type='application/json')
                assert response.status_code == 200

    @patch('routes.admin_routes.require_admin_auth')
    def test_apply_template_missing_id(self, mock_auth):
        """Test template application with missing template_id."""
        from routes.template_routes import template_bp
        from flask import Flask
        
        app = Flask(__name__)
        app.register_blueprint(template_bp)
        
        mock_auth.return_value = lambda f: f
        
        payload = {}
        
        with app.test_client() as client:
            response = client.post('/api/admin/apply-template',
                                   json=payload,
                                   content_type='application/json')
            assert response.status_code == 400

    @patch('routes.template_routes.get_config_dir')
    @patch('routes.template_routes.load_layout_templates')
    @patch('routes.admin_routes.require_admin_auth')
    def test_apply_template_not_found(self, mock_auth, mock_load, mock_get_dir):
        """Test template application with unknown template_id."""
        from routes.template_routes import template_bp
        from flask import Flask
        
        app = Flask(__name__)
        app.register_blueprint(template_bp)
        
        mock_auth.return_value = lambda f: f
        mock_get_dir.return_value = '/tmp/config'
        mock_load.return_value = ({}, False)
        
        payload = {'template_id': 'unknown'}
        
        with app.test_client() as client:
            response = client.post('/api/admin/apply-template',
                                   json=payload,
                                   content_type='application/json')
            assert response.status_code == 400

    @patch('routes.template_routes.get_config_dir')
    @patch('routes.admin_routes.require_admin_auth')
    def test_apply_template_error(self, mock_auth, mock_get_dir):
        """Test template application error handling."""
        from routes.template_routes import template_bp
        from flask import Flask
        
        app = Flask(__name__)
        app.register_blueprint(template_bp)
        
        mock_auth.return_value = lambda f: f
        mock_get_dir.side_effect = Exception("Config error")
        
        payload = {'template_id': 'template1'}
        
        with app.test_client() as client:
            response = client.post('/api/admin/apply-template',
                                   json=payload,
                                   content_type='application/json')
            assert response.status_code == 500
