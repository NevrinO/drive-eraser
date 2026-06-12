# Tests for certificate_routes.py
import pytest
import sys
import os
import json
import tempfile
import sqlite3
from unittest.mock import patch, MagicMock, Mock
from io import BytesIO

# Add backend to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))


class TestValidateFilePath:
    """Test path validation for security."""

    def test_empty_path(self):
        """Test that empty path is rejected."""
        from routes.certificate_routes import _validate_file_path
        path, error = _validate_file_path("", "/tmp/certs")
        assert path is None
        assert error == "File path is empty"

    def test_valid_path_within_allowed_dir(self):
        """Test that valid path within allowed dir is accepted."""
        from routes.certificate_routes import _validate_file_path
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            test_file = os.path.join(tmpdir, "cert.html")
            path, error = _validate_file_path(test_file, tmpdir)
            assert path is not None
            assert error is None
            assert os.path.isabs(path)

    def test_path_traversal_attack(self):
        """Test that path traversal is prevented."""
        from routes.certificate_routes import _validate_file_path
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            malicious_path = os.path.join(tmpdir, "..", "etc", "passwd")
            path, error = _validate_file_path(malicious_path, tmpdir)
            assert path is None
            assert error == "File path is outside allowed directory"

    def test_absolute_path_outside_allowed_dir(self):
        """Test that absolute path outside allowed dir is rejected."""
        from routes.certificate_routes import _validate_file_path
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            outside_path = "/etc/passwd"
            path, error = _validate_file_path(outside_path, tmpdir)
            assert path is None
            assert error == "File path is outside allowed directory"

    def test_relative_path_within_allowed_dir(self):
        """Test that relative path within allowed dir is accepted."""
        from routes.certificate_routes import _validate_file_path
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            test_file = os.path.join(tmpdir, "cert.html")
            path, error = _validate_file_path(test_file, tmpdir)
            assert path is not None
            assert error is None


class TestServeCertificateFile:
    """Test certificate file serving."""

    @pytest.fixture
    def app(self):
        """Create test Flask app."""
        from flask import Flask
        from api_routes import register_routes
        app = Flask(__name__)
        app.config['TESTING'] = True
        register_routes(app)
        from routes import certificate_routes
        app.register_blueprint(certificate_routes.certificate_bp)
        return app

    def test_successful_file_serve(self, app):
        """Test successful file serving."""
        from routes.certificate_routes import _serve_certificate_file
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            test_file = os.path.join(tmpdir, "cert.html")
            with open(test_file, "w") as f:
                f.write("<html>test</html>")
            
            with app.test_request_context():
                with patch('routes.certificate_routes.send_file') as mock_send:
                    mock_send.return_value = MagicMock(status_code=200)
                    result = _serve_certificate_file(test_file, "cert.html", "test")
                    assert result is not None
                    mock_send.assert_called_once()

    def test_file_not_found(self, app):
        """Test that missing file returns 404."""
        from routes.certificate_routes import _serve_certificate_file
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            missing_file = os.path.join(tmpdir, "missing.html")
            with app.test_request_context():
                result = _serve_certificate_file(missing_file, "missing.html", "test")
                assert result[1] == 404
                data = json.loads(result[0].get_data(as_text=True))
                assert "not found" in data["error"]

    def test_generic_exception(self, app):
        """Test that generic exceptions return 500."""
        from routes.certificate_routes import _serve_certificate_file
        with app.test_request_context():
            with patch('routes.certificate_routes.send_file', side_effect=Exception("test error")):
                result = _serve_certificate_file("/tmp/test.html", "test.html", "test")
                assert result[1] == 500


class TestGetCertificate:
    """Test single certificate retrieval endpoint."""

    @pytest.fixture
    def app(self):
        """Create test Flask app."""
        from flask import Flask
        from api_routes import register_routes
        app = Flask(__name__)
        app.config['TESTING'] = True
        register_routes(app)
        from routes import certificate_routes
        app.register_blueprint(certificate_routes.certificate_bp)
        return app

    @pytest.fixture
    def client(self, app):
        """Create test client."""
        return app.test_client()

    @pytest.fixture
    def admin_session(self, client):
        """Set up admin session by manually setting the auth cookie."""
        from app_config import calculate_session_token
        # Manually set the admin session cookie to bypass rate limiting
        client.set_cookie('admin_session', calculate_session_token('test-pass'))
        # Patch load_policy to return test passphrase for validation
        # Also patch limiter to bypass rate limiting on certificate routes
        from routes import certificate_routes
        mock_limiter = MagicMock()
        mock_limiter.limit = lambda x: lambda f: f
        original_limiter = certificate_routes.limiter
        certificate_routes.limiter = mock_limiter
        
        with patch('routes.admin_routes.load_policy', return_value={"lan_passphrase": "test-pass"}):
            with patch('common.load_policy', return_value={"lan_passphrase": "test-pass"}):
                yield client
        
        # Restore original limiter
        certificate_routes.limiter = original_limiter

    def test_certificate_from_memory(self, admin_session):
        """Test retrieving certificate from in-memory job cache."""
        from routes.certificate_routes import ERASE_JOBS, ERASE_JOBS_LOCK
        test_cert = {"serial": "ABC123", "method": "overwrite"}
        with ERASE_JOBS_LOCK:
            ERASE_JOBS["test-job"] = {"certificate": test_cert}
        
        with patch('routes.admin_routes.is_local_request', return_value=False):
            response = admin_session.get('/api/certificates/test-job?format=json')
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["serial"] == "ABC123"

    def test_certificate_from_database(self, admin_session):
        """Test retrieving certificate from database."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch('routes.certificate_routes.get_db_path', return_value=db_path):
                # Create test database
                with sqlite3.connect(db_path) as conn:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS erase_jobs (
                            id TEXT PRIMARY KEY,
                            friendly_id TEXT,
                            certificate_json TEXT,
                            request_json TEXT
                        )
                    """)
                    test_cert = {"serial": "XYZ789", "method": "crypto"}
                    conn.execute(
                        "INSERT INTO erase_jobs (id, friendly_id, certificate_json) VALUES (?, ?, ?)",
                        ("db-job", "friendly-1", json.dumps(test_cert))
                    )
                    conn.commit()
                
                with patch('routes.admin_routes.is_local_request', return_value=False):
                    response = admin_session.get('/api/certificates/db-job?format=json')
                    assert response.status_code == 200
                    data = json.loads(response.data)
                    assert data["serial"] == "XYZ789"

    def test_certificate_by_friendly_id(self, admin_session):
        """Test retrieving certificate by friendly_id."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch('routes.certificate_routes.get_db_path', return_value=db_path):
                with sqlite3.connect(db_path) as conn:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS erase_jobs (
                            id TEXT PRIMARY KEY,
                            friendly_id TEXT,
                            certificate_json TEXT,
                            request_json TEXT
                        )
                    """)
                    test_cert = {"serial": "FRIENDLY"}
                    conn.execute(
                        "INSERT INTO erase_jobs (id, friendly_id, certificate_json) VALUES (?, ?, ?)",
                        ("real-id", "friendly-2", json.dumps(test_cert))
                    )
                    conn.commit()
                
                with patch('routes.admin_routes.is_local_request', return_value=False):
                    response = admin_session.get('/api/certificates/friendly-2?format=json')
                    assert response.status_code == 200
                    data = json.loads(response.data)
                    assert data["serial"] == "FRIENDLY"

    def test_job_not_found(self, admin_session):
        """Test that missing job returns 404."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch('routes.certificate_routes.get_db_path', return_value=db_path):
                with sqlite3.connect(db_path) as conn:
                    conn.execute("CREATE TABLE IF NOT EXISTS erase_jobs (id TEXT PRIMARY KEY, friendly_id TEXT, certificate_json TEXT, request_json TEXT)")
                    conn.commit()
                
                with patch('routes.admin_routes.is_local_request', return_value=False):
                    response = admin_session.get('/api/certificates/missing-job?format=json')
                    assert response.status_code == 404
                    data = json.loads(response.data)
                    assert "not found" in data["error"]

    def test_certificate_not_found_in_job(self, admin_session):
        """Test that job without certificate returns 404."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch('routes.certificate_routes.get_db_path', return_value=db_path):
                with sqlite3.connect(db_path) as conn:
                    conn.execute("CREATE TABLE IF NOT EXISTS erase_jobs (id TEXT PRIMARY KEY, friendly_id TEXT, certificate_json TEXT, request_json TEXT)")
                    conn.execute("INSERT INTO erase_jobs (id, friendly_id, certificate_json) VALUES (?, ?, ?)",
                               ("no-cert-job", "friendly-3", None))
                    conn.commit()
                
                with patch('routes.admin_routes.is_local_request', return_value=False):
                    response = admin_session.get('/api/certificates/no-cert-job?format=json')
                    assert response.status_code == 404
                    data = json.loads(response.data)
                    assert "certificate not found" in data["error"]

    def test_json_format(self, admin_session):
        """Test JSON format response."""
        from routes.certificate_routes import ERASE_JOBS, ERASE_JOBS_LOCK
        test_cert = {"serial": "JSON123"}
        with ERASE_JOBS_LOCK:
            ERASE_JOBS["json-test"] = {"certificate": test_cert}
        
        with patch('routes.admin_routes.is_local_request', return_value=False):
            response = admin_session.get('/api/certificates/json-test?format=json')
            assert response.status_code == 200
            assert response.content_type == "application/json"

    def test_invalid_format(self, admin_session):
        """Test that invalid format returns 400."""
        from routes.certificate_routes import ERASE_JOBS, ERASE_JOBS_LOCK
        test_cert = {"serial": "TEST"}
        with ERASE_JOBS_LOCK:
            ERASE_JOBS["format-test"] = {"certificate": test_cert}
        
        with patch('routes.admin_routes.is_local_request', return_value=False):
            response = admin_session.get('/api/certificates/format-test?format=xml')
            assert response.status_code == 400
            data = json.loads(response.data)
            assert "format must be one of" in data["error"]

    def test_html_format_regular_certificate(self, admin_session):
        """Test HTML format for regular certificate."""
        from routes.certificate_routes import ERASE_JOBS, ERASE_JOBS_LOCK
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            cert_dir = os.path.join(tmpdir, "certs")
            os.makedirs(cert_dir)
            cert_file = os.path.join(cert_dir, "cert.html")
            with open(cert_file, "w") as f:
                f.write("<html>certificate</html>")
            
            test_cert = {
                "formats": {
                    "html": {
                        "path": cert_file,
                        "filename": "cert.html"
                    }
                }
            }
            with ERASE_JOBS_LOCK:
                ERASE_JOBS["html-test"] = {"certificate": test_cert}
            
            with patch('routes.certificate_routes.get_cert_dir', return_value=cert_dir):
                with patch('routes.admin_routes.is_local_request', return_value=False):
                    with patch('routes.certificate_routes.send_file') as mock_send:
                        mock_send.return_value = MagicMock(status_code=200)
                        response = admin_session.get('/api/certificates/html-test?format=html')
                        assert response is not None

    def test_html_format_bulk_certificate(self, admin_session):
        """Test HTML format for bulk certificate."""
        from routes.certificate_routes import ERASE_JOBS, ERASE_JOBS_LOCK
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            cert_dir = os.path.join(tmpdir, "certs")
            os.makedirs(cert_dir)
            bulk_file = os.path.join(cert_dir, "bulk.html")
            with open(bulk_file, "w") as f:
                f.write("<html>bulk</html>")
            
            test_cert = {
                "bulk_html_path": bulk_file,
                "bulk_html_filename": "bulk.html"
            }
            with ERASE_JOBS_LOCK:
                ERASE_JOBS["bulk-test"] = {"certificate": test_cert}
            
            with patch('routes.certificate_routes.get_cert_dir', return_value=cert_dir):
                with patch('routes.admin_routes.is_local_request', return_value=False):
                    with patch('routes.certificate_routes.send_file') as mock_send:
                        mock_send.return_value = MagicMock(status_code=200)
                        response = admin_session.get('/api/certificates/bulk-test?format=html&bulk=true')
                        assert response is not None

    def test_bulk_requires_authentication(self, client):
        """Test that bulk download requires authentication."""
        from routes.certificate_routes import ERASE_JOBS, ERASE_JOBS_LOCK
        test_cert = {"bulk_html_path": "/tmp/bulk.html"}
        with ERASE_JOBS_LOCK:
            ERASE_JOBS["bulk-auth-test"] = {"certificate": test_cert}
        
        with patch('routes.admin_routes.is_local_request', return_value=False):
            with patch('routes.certificate_routes.load_policy', return_value={"lan_passphrase": "test-pass"}):
                response = client.get('/api/certificates/bulk-auth-test?format=html&bulk=true')
                assert response.status_code == 401

    def test_html_path_traversal_prevented(self, admin_session):
        """Test that path traversal is prevented in HTML format."""
        from routes.certificate_routes import ERASE_JOBS, ERASE_JOBS_LOCK
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            cert_dir = os.path.join(tmpdir, "certs")
            os.makedirs(cert_dir)
            
            test_cert = {
                "formats": {
                    "html": {
                        "path": "/etc/passwd",
                        "filename": "passwd"
                    }
                }
            }
            with ERASE_JOBS_LOCK:
                ERASE_JOBS["traversal-test"] = {"certificate": test_cert}
            
            with patch('routes.certificate_routes.get_cert_dir', return_value=cert_dir):
                with patch('routes.admin_routes.is_local_request', return_value=False):
                    response = admin_session.get('/api/certificates/traversal-test?format=html')
                    assert response.status_code == 403
                    data = json.loads(response.data)
                    assert "path validation failed" in data["error"]


class TestGetBulkCertificatesHtml:
    """Test bulk HTML certificate generation endpoint."""

    @pytest.fixture
    def app(self):
        """Create test Flask app."""
        from flask import Flask
        from api_routes import register_routes
        from database import init_wipe_db
        # Patch limiter at module level before routes are imported
        with patch('app_config.limiter') as mock_limiter:
            mock_limiter.limit = lambda x: lambda f: f
            app = Flask(__name__)
            app.config['TESTING'] = True
            register_routes(app)
            from routes import certificate_routes
            app.register_blueprint(certificate_routes.certificate_bp)
            init_wipe_db()
            yield app

    @pytest.fixture
    def client(self, app):
        """Create test client."""
        return app.test_client()

    @pytest.fixture
    def admin_session(self, client):
        """Set up admin session by manually setting the auth cookie."""
        from app_config import calculate_session_token
        # Manually set the admin session cookie to bypass rate limiting
        client.set_cookie('admin_session', calculate_session_token('test-pass'))
        # Patch load_policy to return test passphrase for validation
        with patch('routes.admin_routes.load_policy', return_value={"lan_passphrase": "test-pass"}):
            with patch('common.load_policy', return_value={"lan_passphrase": "test-pass"}):
                yield client

    def test_invalid_json_payload(self, admin_session):
        """Test that invalid JSON is handled."""
        with patch('routes.admin_routes.is_local_request', return_value=False):
            response = admin_session.post('/api/certificates/bulk-html', data="not json")
            assert response.status_code in [200, 400]  # request.get_json(silent=True) returns None

    def test_job_ids_not_list(self, admin_session):
        """Test that job_ids must be a list."""
        with patch('routes.admin_routes.is_local_request', return_value=False):
            response = admin_session.post('/api/certificates/bulk-html', json={"job_ids": "not-a-list"})
            assert response.status_code == 400
            data = json.loads(response.data)
            assert "must be a list" in data["error"]

    def test_job_ids_not_strings(self, admin_session):
        """Test that all job_ids must be strings."""
        with patch('routes.admin_routes.is_local_request', return_value=False):
            response = admin_session.post('/api/certificates/bulk-html', json={"job_ids": [123, 456]})
            assert response.status_code == 400
            data = json.loads(response.data)
            assert "must be strings" in data["error"]

    def test_job_ids_exceeds_limit(self, admin_session):
        """Test that job_ids list is limited to 100 items."""
        with patch('routes.admin_routes.is_local_request', return_value=False):
            response = admin_session.post('/api/certificates/bulk-html', json={"job_ids": [str(i) for i in range(101)]})
            assert response.status_code == 400
            data = json.loads(response.data)
            assert "cannot exceed 100" in data["error"]

    def test_ticket_number_not_string(self, admin_session):
        """Test that ticket_number must be a string."""
        with patch('routes.admin_routes.is_local_request', return_value=False):
            response = admin_session.post('/api/certificates/bulk-html', json={"ticket_number": 123})
            assert response.status_code == 400
            data = json.loads(response.data)
            assert "must be a string" in data["error"]

    def test_ticket_number_empty(self, admin_session):
        """Test that ticket_number cannot be empty/whitespace."""
        with patch('routes.admin_routes.is_local_request', return_value=False):
            response = admin_session.post('/api/certificates/bulk-html', json={"ticket_number": "   "})
            assert response.status_code == 400
            data = json.loads(response.data)
            assert "cannot be empty" in data["error"]

    def test_invalid_start_date_format(self, admin_session):
        """Test that invalid start_date format is rejected."""
        with patch('routes.admin_routes.is_local_request', return_value=False):
            response = admin_session.post('/api/certificates/bulk-html', json={"start_date": "invalid-date"})
            assert response.status_code == 400
            data = json.loads(response.data)
            assert "ISO 8601" in data["error"]

    def test_invalid_end_date_format(self, admin_session):
        """Test that invalid end_date format is rejected."""
        with patch('routes.admin_routes.is_local_request', return_value=False):
            response = admin_session.post('/api/certificates/bulk-html', json={"end_date": "invalid-date"})
            assert response.status_code == 400
            data = json.loads(response.data)
            assert "ISO 8601" in data["error"]

    def test_start_date_after_end_date(self, admin_session):
        """Test that start_date after end_date is rejected."""
        with patch('routes.admin_routes.is_local_request', return_value=False):
            response = admin_session.post('/api/certificates/bulk-html', json={
                "start_date": "2026-12-31",
                "end_date": "2026-01-01"
            })
            assert response.status_code == 400
            data = json.loads(response.data)
            assert "must be before" in data["error"]

    def test_successful_bulk_generation_by_job_ids(self, admin_session):
        """Test successful bulk generation by job_ids."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch('routes.certificate_routes.get_db_path', return_value=db_path):
                with sqlite3.connect(db_path) as conn:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS erase_jobs (
                            id TEXT PRIMARY KEY,
                            friendly_id TEXT,
                            certificate_json TEXT,
                            finished_at TEXT,
                            request_json TEXT
                        )
                    """)
                    test_cert = {"serial": "BULK1"}
                    conn.execute(
                        "INSERT INTO erase_jobs (id, friendly_id, certificate_json, finished_at) VALUES (?, ?, ?, ?)",
                        ("job-1", "friendly-1", json.dumps(test_cert), "2026-01-01T00:00:00")
                    )
                    conn.commit()
                
                with patch('routes.admin_routes.is_local_request', return_value=False):
                    with patch('routes.certificate_routes.build_bulk_certificate_html', return_value="<html>bulk</html>"):
                        with patch('routes.certificate_routes.send_file') as mock_send:
                            mock_send.return_value = MagicMock(status_code=200)
                            response = admin_session.post('/api/certificates/bulk-html', json={"job_ids": ["job-1"]})
                            assert response is not None

    def test_successful_bulk_generation_by_ticket_number(self, admin_session):
        """Test successful bulk generation by ticket_number."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch('routes.certificate_routes.get_db_path', return_value=db_path):
                with sqlite3.connect(db_path) as conn:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS erase_jobs (
                            id TEXT PRIMARY KEY,
                            friendly_id TEXT,
                            certificate_json TEXT,
                            finished_at TEXT,
                            request_json TEXT
                        )
                    """)
                    test_cert = {"serial": "TICKET1"}
                    conn.execute(
                        "INSERT INTO erase_jobs (id, friendly_id, certificate_json, finished_at, request_json) VALUES (?, ?, ?, ?, ?)",
                        ("job-2", "friendly-2", json.dumps(test_cert), "2026-01-01T00:00:00", json.dumps({"ticket_number": "TICKET-123"}))
                    )
                    conn.commit()
                
                with patch('routes.admin_routes.is_local_request', return_value=False):
                    with patch('routes.certificate_routes.build_bulk_certificate_html', return_value="<html>bulk</html>"):
                        with patch('routes.certificate_routes.send_file') as mock_send:
                            mock_send.return_value = MagicMock(status_code=200)
                            response = admin_session.post('/api/certificates/bulk-html', json={"ticket_number": "TICKET-123"})
                            assert response is not None

    def test_successful_bulk_generation_by_date_range(self, admin_session):
        """Test successful bulk generation by date range."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch('routes.certificate_routes.get_db_path', return_value=db_path):
                with sqlite3.connect(db_path) as conn:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS erase_jobs (
                            id TEXT PRIMARY KEY,
                            friendly_id TEXT,
                            certificate_json TEXT,
                            finished_at TEXT,
                            request_json TEXT
                        )
                    """)
                    test_cert = {"serial": "DATE1"}
                    conn.execute(
                        "INSERT INTO erase_jobs (id, friendly_id, certificate_json, finished_at) VALUES (?, ?, ?, ?)",
                        ("job-3", "friendly-3", json.dumps(test_cert), "2026-06-01T00:00:00")
                    )
                    conn.commit()
                
                with patch('routes.admin_routes.is_local_request', return_value=False):
                    with patch('routes.certificate_routes.build_bulk_certificate_html', return_value="<html>bulk</html>"):
                        with patch('routes.certificate_routes.send_file') as mock_send:
                            mock_send.return_value = MagicMock(status_code=200)
                            response = admin_session.post('/api/certificates/bulk-html', json={
                                "start_date": "2026-01-01T00:00:00",
                                "end_date": "2026-12-31T23:59:59"
                            })
                            assert response is not None

    def test_no_certificates_found(self, admin_session):
        """Test that no certificates returns empty list."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch('routes.certificate_routes.get_db_path', return_value=db_path):
                with sqlite3.connect(db_path) as conn:
                    conn.execute("CREATE TABLE IF NOT EXISTS erase_jobs (id TEXT PRIMARY KEY, friendly_id TEXT, certificate_json TEXT, request_json TEXT, finished_at TEXT)")
                    conn.commit()
                
                with patch('routes.admin_routes.is_local_request', return_value=False):
                    response = admin_session.post('/api/certificates/bulk-html', json={"ticket_number": "NONEXISTENT"})
                    assert response.status_code == 200
                    data = json.loads(response.data)
                    assert data["count"] == 0
                    assert "No certificates found" in data["message"]

    def test_malformed_certificate_json_skipped(self, admin_session):
        """Test that malformed certificate JSON is skipped with warning."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch('routes.certificate_routes.get_db_path', return_value=db_path):
                with sqlite3.connect(db_path) as conn:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS erase_jobs (
                            id TEXT PRIMARY KEY,
                            friendly_id TEXT,
                            certificate_json TEXT,
                            finished_at TEXT,
                            request_json TEXT
                        )
                    """)
                    conn.execute(
                        "INSERT INTO erase_jobs (id, friendly_id, certificate_json, finished_at) VALUES (?, ?, ?, ?)",
                        ("job-bad", "friendly-bad", "invalid-json{", "2026-01-01T00:00:00")
                    )
                    conn.commit()
                
                with patch('routes.admin_routes.is_local_request', return_value=False):
                    with patch('routes.certificate_routes.build_bulk_certificate_html', return_value="<html>bulk</html>"):
                        with patch('routes.certificate_routes.send_file') as mock_send:
                            mock_send.return_value = MagicMock(status_code=200)
                            response = admin_session.post('/api/certificates/bulk-html', json={"job_ids": ["job-bad"]})
                            assert response is not None

    def test_results_truncated_warning(self, admin_session):
        """Test that truncated results include warning header."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch('routes.certificate_routes.get_db_path', return_value=db_path):
                with sqlite3.connect(db_path) as conn:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS erase_jobs (
                            id TEXT PRIMARY KEY,
                            friendly_id TEXT,
                            certificate_json TEXT,
                            finished_at TEXT,
                            request_json TEXT
                        )
                    """)
                    # Insert 500 certificates to hit limit
                    for i in range(500):
                        test_cert = {"serial": f"CERT{i}"}
                        conn.execute(
                            "INSERT INTO erase_jobs (id, friendly_id, certificate_json, finished_at) VALUES (?, ?, ?, ?)",
                            (f"job-{i}", f"friendly-{i}", json.dumps(test_cert), "2026-01-01T00:00:00")
                        )
                    conn.commit()
                
                with patch('routes.admin_routes.is_local_request', return_value=False):
                    with patch('routes.certificate_routes.build_bulk_certificate_html', return_value="<html>bulk</html>"):
                        with patch('routes.certificate_routes.send_file') as mock_send:
                            mock_response = MagicMock(status_code=200)
                            mock_response.headers = {}
                            mock_send.return_value = mock_response
                            response = admin_session.post('/api/certificates/bulk-html', json={
                                "start_date": "2026-01-01T00:00:00",
                                "end_date": "2026-12-31T23:59:59"
                            })
                            assert mock_response.headers.get('X-Warning') is not None


class TestCreateBulkCert:
    """Test bulk certificate job creation endpoint."""

    @pytest.fixture
    def app(self):
        """Create test Flask app."""
        from flask import Flask
        from api_routes import register_routes
        app = Flask(__name__)
        app.config['TESTING'] = True
        register_routes(app)
        from routes import certificate_routes
        app.register_blueprint(certificate_routes.certificate_bp)
        return app

    @pytest.fixture
    def client(self, app):
        """Create test client."""
        return app.test_client()

    @pytest.fixture
    def admin_session(self, client):
        """Set up admin session by manually setting the auth cookie."""
        from app_config import calculate_session_token
        # Manually set the admin session cookie to bypass rate limiting
        client.set_cookie('admin_session', calculate_session_token('test-pass'))
        # Patch load_policy to return test passphrase for validation
        # Also patch limiter to bypass rate limiting on certificate routes
        from routes import certificate_routes
        mock_limiter = MagicMock()
        mock_limiter.limit = lambda x: lambda f: f
        original_limiter = certificate_routes.limiter
        certificate_routes.limiter = mock_limiter
        
        with patch('routes.admin_routes.load_policy', return_value={"lan_passphrase": "test-pass"}):
            with patch('common.load_policy', return_value={"lan_passphrase": "test-pass"}):
                yield client
        
        # Restore original limiter
        certificate_routes.limiter = original_limiter

    def test_job_ids_required(self, admin_session):
        """Test that job_ids is required."""
        with patch('routes.admin_routes.is_local_request', return_value=False):
            response = admin_session.post('/api/admin/bulk-cert/create', json={})
            assert response.status_code == 400
            data = json.loads(response.data)
            assert "job_ids is required" in data["error"]

    def test_job_ids_must_be_list(self, admin_session):
        """Test that job_ids must be a list."""
        with patch('routes.admin_routes.is_local_request', return_value=False):
            response = admin_session.post('/api/admin/bulk-cert/create', json={"job_ids": "not-a-list"})
            assert response.status_code == 400
            data = json.loads(response.data)
            assert "must be a list" in data["error"]

    def test_successful_job_creation(self, admin_session):
        """Test successful bulk cert job creation."""
        with patch('routes.certificate_routes.create_bulk_cert_job') as mock_create:
            mock_create.return_value = ({"id": "bulk-job-123"}, None, None)
            with patch('routes.certificate_routes.run_bulk_cert_job'):
                with patch('routes.admin_routes.is_local_request', return_value=False):
                    response = admin_session.post('/api/admin/bulk-cert/create', json={"job_ids": ["job-1", "job-2"]})
                    assert response.status_code == 202
                    data = json.loads(response.data)
                    assert data["status"] == "accepted"
                    assert data["job_id"] == "bulk-job-123"

    def test_validation_error_from_create_bulk_cert_job(self, admin_session):
        """Test that validation errors from create_bulk_cert_job are returned."""
        with patch('routes.certificate_routes.create_bulk_cert_job') as mock_create:
            mock_create.return_value = (None, {"error": "Job not found"}, 404)
            with patch('routes.admin_routes.is_local_request', return_value=False):
                response = admin_session.post('/api/admin/bulk-cert/create', json={"job_ids": ["invalid-job"]})
                assert response.status_code == 404
                data = json.loads(response.data)
                assert "Job not found" in data["error"]

    def test_background_thread_started(self, admin_session):
        """Test that background thread is started for job execution."""
        with patch('routes.certificate_routes.create_bulk_cert_job') as mock_create:
            mock_create.return_value = ({"id": "bulk-job-456"}, None, None)
            with patch('routes.certificate_routes.run_bulk_cert_job') as mock_run:
                with patch('routes.certificate_routes.Thread') as mock_thread:
                    mock_thread_instance = MagicMock()
                    mock_thread.return_value = mock_thread_instance
                    with patch('routes.admin_routes.is_local_request', return_value=False):
                        response = admin_session.post('/api/admin/bulk-cert/create', json={"job_ids": ["job-1"]})
                        assert response.status_code == 202
                        mock_thread.assert_called_once()
                        mock_thread_instance.start.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
