# Regression tests for explicit slot_mappings in enclosure create/update
import pytest
import sys
import os
import json
import tempfile
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))


class TestEnclosureSlotMappings:
    """Tests for the explicit slot_mappings payload path in enclosure management."""

    @pytest.fixture
    def test_config_dir(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
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
            with open(os.path.join(tmpdir, "bay_map.json"), "w") as f:
                json.dump({"enclosures": {}}, f)
            yield tmpdir

    @pytest.fixture
    def app(self, test_config_dir):
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
            from app_config import limiter
            app = Flask(__name__)
            app.config['TESTING'] = True
            limiter.init_app(app)
            import api_routes
            from routes import admin_routes
            admin_bp = getattr(admin_routes, 'admin_bp', None)
            if admin_bp:
                app.register_blueprint(admin_bp)
            api_routes.register_routes(app)
            yield app
        finally:
            from database import close_all_connections
            try:
                close_all_connections()
            except Exception:
                pass
            for p in patches:
                p.stop()

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    @pytest.fixture
    def admin_session(self, client):
        response = client.post('/api/auth/verify', json={"passphrase": "test-lan-pass"})
        assert response.status_code == 200
        return client

    def _mock_templates(self):
        return {
            "test_4bay": {
                "id": "test_4bay",
                "name": "Test 4-Bay",
                "vendor": "Test",
                "slot_count": 4,
                "rows": 4,
                "cols": 1,
                "traversal_preset": "top_left_down_then_across",
                "default_role": "wipe"
            }
        }

    def test_create_enclosure_with_slot_mappings(self, admin_session):
        """Explicit slot_mappings must be accepted and stored with slot_type."""
        with patch('routes.admin_routes.load_layout_templates') as mock_load_templates:
            mock_load_templates.return_value = (self._mock_templates(), False)
            with patch('routes.admin_routes.generate_master_slot_map') as mock_master:
                mock_master.return_value = [
                    {
                        "pci_controller": "0000:00:1f.2",
                        "slot_type": "sas_expander",
                        "physical_slot_number": 0,
                        "hardware_identifier": "0:0:0",
                        "expander_sas_address": None
                    }
                ]
                with patch('routes.admin_routes.save_bay_map') as mock_save:
                    payload = {
                        "id": "test_enc",
                        "name": "Test Enclosure",
                        "template_id": "test_4bay",
                        "pci_controller": "0000:00:1f.2",
                        "expander_sas_address": None,
                        "display_order": 0,
                        "starting_slot_number": 0,
                        "slot_mappings": {
                            "0": {
                                "label": "Bay 0",
                                "role": "wipe",
                                "locked": False,
                                "mappings": {
                                    "sas_sata": {
                                        "slot_type": "sas_expander",
                                        "hardware_identifier": "phy-0:0:0"
                                    }
                                }
                            },
                            "1": {
                                "label": "Bay 1",
                                "role": "os",
                                "locked": True,
                                "mappings": {
                                    "sas_sata": {
                                        "slot_type": "sas_expander",
                                        "hardware_identifier": "phy-0:0:1"
                                    }
                                }
                            }
                        }
                    }
                    response = admin_session.post('/api/admin/enclosures', json=payload)
                    assert response.status_code == 201, response.data
                    data = json.loads(response.data)
                    assert data["enclosure"]["slots"]["0"]["mappings"]["sas_sata"]["slot_type"] == "sas_expander"
                    assert data["enclosure"]["slots"]["0"]["mappings"]["sas_sata"]["hardware_identifier"] == "phy-0:0:0"
                    assert data["enclosure"]["slots"]["1"]["role"] == "os"
                    assert data["enclosure"]["slots"]["1"]["locked"] is True
                    mock_save.assert_called_once()

    def test_create_enclosure_rejects_missing_slot_type(self, admin_session):
        """slot_mappings without slot_type must be rejected before schema validation."""
        with patch('routes.admin_routes.load_layout_templates') as mock_load_templates:
            mock_load_templates.return_value = (self._mock_templates(), False)
            with patch('routes.admin_routes.generate_master_slot_map') as mock_master:
                mock_master.return_value = [
                    {
                        "pci_controller": "0000:00:1f.2",
                        "slot_type": "sas_expander",
                        "physical_slot_number": 0,
                        "hardware_identifier": "0:0:0",
                        "expander_sas_address": None
                    }
                ]
                payload = {
                    "id": "test_enc",
                    "name": "Test Enclosure",
                    "template_id": "test_4bay",
                    "pci_controller": "0000:00:1f.2",
                    "expander_sas_address": None,
                    "display_order": 0,
                    "starting_slot_number": 0,
                    "slot_mappings": {
                        "0": {
                            "mappings": {
                                "sas_sata": {
                                    "hardware_identifier": "phy-0:0:0"
                                }
                            }
                        }
                    }
                }
                response = admin_session.post('/api/admin/enclosures', json=payload)
                assert response.status_code == 400
                data = json.loads(response.data)
                assert "slot_type" in data["error"]

    def test_update_enclosure_with_slot_mappings(self, admin_session):
        """PUT must accept slot_mappings to update existing slot hardware identifiers."""
        with patch('routes.admin_routes.load_layout_templates') as mock_load_templates:
            mock_load_templates.return_value = (self._mock_templates(), False)
            with patch('routes.admin_routes.generate_master_slot_map') as mock_master:
                mock_master.return_value = [
                    {
                        "pci_controller": "0000:00:1f.2",
                        "slot_type": "sas_expander",
                        "physical_slot_number": 0,
                        "hardware_identifier": "0:0:0",
                        "expander_sas_address": None
                    }
                ]
                # Create the enclosure first
                create_payload = {
                    "id": "test_enc",
                    "name": "Test Enclosure",
                    "template_id": "test_4bay",
                    "pci_controller": "0000:00:1f.2",
                    "expander_sas_address": None,
                    "display_order": 0,
                    "auto_map_slots": True,
                    "nvme_start_slot": None
                }
                response = admin_session.post('/api/admin/enclosures', json=create_payload)
                assert response.status_code == 201, response.data

                # Update slot mappings
                update_payload = {
                    "slot_mappings": {
                        "0": {
                            "label": "Updated Bay 0",
                            "role": "reserved",
                            "mappings": {
                                "sas_sata": {
                                    "slot_type": "sas_expander",
                                    "hardware_identifier": "phy-0:0:99"
                                }
                            }
                        }
                    }
                }
                response = admin_session.put('/api/admin/enclosures/test_enc', json=update_payload)
                assert response.status_code == 200, response.data
                data = json.loads(response.data)
                assert data["enclosure"]["slots"]["0"]["label"] == "Updated Bay 0"
                assert data["enclosure"]["slots"]["0"]["role"] == "reserved"
                assert data["enclosure"]["slots"]["0"]["mappings"]["sas_sata"]["hardware_identifier"] == "phy-0:0:99"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
