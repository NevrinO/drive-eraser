# Tests for policy routes — log_retention_days validation and default
import pytest
import sys
import os
import json
import tempfile
from unittest.mock import patch

# Add backend to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))


class TestPolicyRoutes:
    """Tests for policy configuration endpoints."""

    @pytest.fixture
    def test_config_dir(self):
        """Create a temporary directory for test configuration."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            policy = {
                "strict_audit_mode": False,
                "wipe_passphrase": "test-wipe-pass",
                "lan_passphrase": "test-lan-pass",
                "station_id": "test-station",
            }
            with open(os.path.join(tmpdir, "policy.json"), "w") as f:
                json.dump(policy, f)
            yield tmpdir

    @pytest.fixture
    def app(self, test_config_dir):
        """Create a test Flask app with test configuration."""
        test_db_path = os.path.join(test_config_dir, "test.db")
        patches = [
            patch('common.get_config_dir', return_value=test_config_dir),
            patch('common.get_data_dir', return_value=test_config_dir),
            patch('common.get_db_path', return_value=test_db_path),
            patch('api_routes.get_config_dir', return_value=test_config_dir),
            patch('routes._shared.get_config_dir', return_value=test_config_dir),
            patch('routes.policy_routes.get_config_dir', return_value=test_config_dir),
        ]
        for p in patches:
            p.start()
        try:
            from database import init_wipe_db
            init_wipe_db()
            from flask import Flask
            from app_config import limiter
            app = Flask(__name__)
            app.config['TESTING'] = True
            limiter.init_app(app)
            from routes.policy_routes import policy_bp
            app.register_blueprint(policy_bp)
            import api_routes
            api_routes.register_routes(app)
            yield app
        finally:
            for p in patches:
                p.stop()

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    @pytest.fixture
    def admin_session(self, client):
        """Set up admin session cookie."""
        response = client.post('/api/auth/verify',
            json={"passphrase": "test-lan-pass"})
        assert response.status_code == 200
        return client

    def test_log_retention_days_validation(self, admin_session):
        """Test that log_retention_days enforces min/max bounds (1-365)."""
        with patch('routes._shared.is_local_request', return_value=True):
            # Test value below minimum
            response = admin_session.post('/api/admin/policy',
                json={"log_retention_days": 0})
            assert response.status_code == 400
            data = json.loads(response.data)
            assert "log_retention_days" in data["error"]

            # Test value above maximum
            response = admin_session.post('/api/admin/policy',
                json={"log_retention_days": 366})
            assert response.status_code == 400
            data = json.loads(response.data)
            assert "log_retention_days" in data["error"]

            # Test valid value
            response = admin_session.post('/api/admin/policy',
                json={"log_retention_days": 60})
            assert response.status_code == 200

            # Verify it was saved
            response = admin_session.get('/api/admin/policy')
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data.get("log_retention_days") == 60

    def test_log_retention_days_default(self, admin_session):
        """Test that default 30 is used when log_retention_days not in policy."""
        with patch('routes._shared.is_local_request', return_value=True):
            # GET policy should return default 30 from DEFAULT_POLICY merge
            response = admin_session.get('/api/admin/policy')
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data.get("log_retention_days") == 30


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
