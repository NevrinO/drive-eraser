# Tests for bulk_cert.py
import pytest
import sys
import os
import tempfile
from unittest.mock import patch, MagicMock, Mock
from datetime import datetime, timezone

# Add backend to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))


class TestBulkCertSignalHandling:
    """Test bulk cert signal handling."""

    def test_handle_bulk_cert_signal_sets_flag(self):
        """Test that signal handler sets interrupted flag."""
        from bulk_cert import _handle_bulk_cert_signal, _check_bulk_cert_interrupted
        import bulk_cert
        bulk_cert._bulk_cert_interrupted = False
        
        try:
            _handle_bulk_cert_signal(15, None)
            assert _check_bulk_cert_interrupted() is True
        finally:
            bulk_cert._bulk_cert_interrupted = False

    def test_check_bulk_cert_interrupted_returns_flag(self):
        """Test that check_interrupted returns the flag state."""
        from bulk_cert import _check_bulk_cert_interrupted
        import bulk_cert
        bulk_cert._bulk_cert_interrupted = False
        try:
            assert _check_bulk_cert_interrupted() is False
            
            bulk_cert._bulk_cert_interrupted = True
            assert _check_bulk_cert_interrupted() is True
        finally:
            bulk_cert._bulk_cert_interrupted = False


class TestCreateBulkCertJob:
    """Test bulk certificate job creation."""

    def test_create_bulk_cert_job_not_list(self):
        """Test that non-list input is rejected."""
        from bulk_cert import create_bulk_cert_job
        result, error, status = create_bulk_cert_job("not_a_list")
        assert result is None
        assert "must be a list" in error["error"]
        assert status == 400

    def test_create_bulk_cert_job_empty_list(self):
        """Test that empty list is rejected."""
        from bulk_cert import create_bulk_cert_job
        result, error, status = create_bulk_cert_job([])
        assert result is None
        assert "cannot be empty" in error["error"]
        assert status == 400

    def test_create_bulk_cert_job_exceeds_default_limit(self):
        """Test that batch size exceeding default limit is rejected."""
        from bulk_cert import create_bulk_cert_job
        job_ids = [f"job-{i}" for i in range(101)]
        result, error, status = create_bulk_cert_job(job_ids)
        assert result is None
        assert "exceeds maximum limit" in error["error"]
        assert status == 400

    def test_create_bulk_cert_job_duplicate_ids(self):
        """Test that duplicate job IDs are rejected."""
        from bulk_cert import create_bulk_cert_job
        job_ids = ["job-1", "job-2", "job-1"]
        result, error, status = create_bulk_cert_job(job_ids)
        assert result is None
        assert "duplicate" in error["error"].lower()
        assert status == 400

    def test_create_bulk_cert_job_invalid_job_id(self):
        """Test that invalid job IDs are rejected."""
        from bulk_cert import create_bulk_cert_job
        job_ids = ["", "job-2"]
        result, error, status = create_bulk_cert_job(job_ids)
        assert result is None
        assert "invalid job_id" in error["error"]
        assert status == 400

    def test_create_bulk_cert_job_whitespace_job_id(self):
        """Test that whitespace-only job IDs are rejected."""
        from bulk_cert import create_bulk_cert_job
        job_ids = ["   ", "job-2"]
        result, error, status = create_bulk_cert_job(job_ids)
        assert result is None
        assert "invalid job_id" in error["error"]
        assert status == 400

    def test_create_bulk_cert_job_not_string(self):
        """Test that non-string job IDs are rejected."""
        from bulk_cert import create_bulk_cert_job
        job_ids = [123, "job-2"]
        result, error, status = create_bulk_cert_job(job_ids)
        assert result is None
        assert "invalid job_id" in error["error"]
        assert status == 400

    def test_create_bulk_cert_job_job_not_found(self):
        """Test that non-existent jobs are rejected."""
        from bulk_cert import create_bulk_cert_job
        with patch('bulk_cert.load_job', return_value=None):
            result, error, status = create_bulk_cert_job(["nonexistent-job"])
            assert result is None
            assert "not found" in error["error"]
            assert status == 404

    def test_create_bulk_cert_job_wrong_type(self):
        """Test that non-erase jobs are rejected."""
        from bulk_cert import create_bulk_cert_job
        job = {"job_type": "bulk_cert", "status": "completed"}
        with patch('bulk_cert.load_job', return_value=job):
            result, error, status = create_bulk_cert_job(["job-1"])
            assert result is None
            assert "not an erase job" in error["error"]
            assert status == 400

    def test_create_bulk_cert_job_not_completed(self):
        """Test that non-completed jobs are rejected."""
        from bulk_cert import create_bulk_cert_job
        job = {"job_type": "erase", "status": "running"}
        with patch('bulk_cert.load_job', return_value=job):
            result, error, status = create_bulk_cert_job(["job-1"])
            assert result is None
            assert "not completed" in error["error"]
            assert status == 400

    def test_create_bulk_cert_job_success(self):
        """Test successful bulk cert job creation."""
        from bulk_cert import create_bulk_cert_job
        from app_config import BULK_CERT_JOBS, BULK_CERT_JOBS_LOCK
        
        job = {"job_type": "erase", "status": "completed"}
        with patch('bulk_cert.load_job', return_value=job):
            with patch('bulk_cert.persist_job'):
                result, error, status = create_bulk_cert_job(["job-1"])
                assert result is not None
                assert error is None
                assert status is None
                assert result["job_type"] == "bulk_cert"
                assert result["status"] == "queued"
                assert result["request"]["total_jobs"] == 1

    def test_create_bulk_cert_job_friendly_id_format(self):
        """Test that friendly ID follows expected format."""
        from bulk_cert import create_bulk_cert_job
        job = {"job_type": "erase", "status": "completed"}
        with patch('bulk_cert.load_job', return_value=job):
            with patch('bulk_cert.persist_job'):
                result, error, status = create_bulk_cert_job(["job-1"])
                assert result is not None
                friendly_id = result["friendly_id"]
                assert friendly_id.startswith("BULK-")
                # Format: BULK-YYYYMMDD-XXXXXX
                parts = friendly_id.split("-")
                assert len(parts) == 3
                assert len(parts[1]) == 8  # YYYYMMDD
                assert len(parts[2]) == 6  # 6 hex chars

    def test_create_bulk_cert_job_custom_batch_size(self):
        """Test that custom batch size from policy is respected."""
        from bulk_cert import create_bulk_cert_job
        job = {"job_type": "erase", "status": "completed"}
        
        with patch('bulk_cert.load_policy', return_value={"max_bulk_cert_batch_size": 5}):
            with patch('bulk_cert.load_job', return_value=job):
                result, error, status = create_bulk_cert_job([f"job-{i}" for i in range(6)])
                assert result is None
                assert "exceeds maximum limit of 5" in error["error"]


class TestRunBulkCertJob:
    """Test bulk certificate job execution."""

    def test_run_bulk_cert_job_missing_job(self):
        """Test that missing job is handled gracefully."""
        from bulk_cert import run_bulk_cert_job
        from app_config import BULK_CERT_JOBS, BULK_CERT_JOBS_LOCK
        
        with BULK_CERT_JOBS_LOCK:
            if "nonexistent-job" in BULK_CERT_JOBS:
                del BULK_CERT_JOBS["nonexistent-job"]
        
        run_bulk_cert_job("nonexistent-job")
        # Should not raise exception

    def test_run_bulk_cert_job_sets_running_status(self):
        """Test that job status is set to running."""
        from bulk_cert import run_bulk_cert_job
        from app_config import BULK_CERT_JOBS, BULK_CERT_JOBS_LOCK
        
        job_id = "bulk-job-1"
        job = {
            "id": job_id,
            "status": "queued",
            "request": {"target_job_ids": []}
        }
        
        with BULK_CERT_JOBS_LOCK:
            BULK_CERT_JOBS[job_id] = job
        
        with patch('bulk_cert.persist_job'):
            with patch('bulk_cert.send_slack_notification'):
                run_bulk_cert_job(job_id)
        
        with BULK_CERT_JOBS_LOCK:
            assert BULK_CERT_JOBS[job_id]["status"] == "completed"

    def test_run_bulk_cert_job_interrupted(self):
        """Test that interruption is handled."""
        from bulk_cert import run_bulk_cert_job
        from app_config import BULK_CERT_JOBS, BULK_CERT_JOBS_LOCK
        import bulk_cert
        
        job_id = "bulk-job-2"
        job = {
            "id": job_id,
            "status": "queued",
            "request": {"target_job_ids": ["job-1", "job-2"]}
        }
        
        with BULK_CERT_JOBS_LOCK:
            BULK_CERT_JOBS[job_id] = job
        
        # Set interrupted flag
        bulk_cert._bulk_cert_interrupted = True
        
        try:
            with patch('bulk_cert.persist_job'):
                with patch('bulk_cert.send_slack_notification'):
                    with patch('bulk_cert._reset_bulk_cert_interrupted'):
                        run_bulk_cert_job(job_id)
            
            with BULK_CERT_JOBS_LOCK:
                assert BULK_CERT_JOBS[job_id]["status"] == "interrupted"
        finally:
            bulk_cert._bulk_cert_interrupted = False

    def test_run_bulk_cert_job_target_job_not_found(self):
        """Test handling when target job is not found."""
        from bulk_cert import run_bulk_cert_job
        from app_config import BULK_CERT_JOBS, BULK_CERT_JOBS_LOCK
        
        job_id = "bulk-job-3"
        job = {
            "id": job_id,
            "status": "queued",
            "request": {"target_job_ids": ["job-1"]}
        }
        
        with BULK_CERT_JOBS_LOCK:
            BULK_CERT_JOBS[job_id] = job
        
        with patch('bulk_cert.persist_job'):
            with patch('bulk_cert.send_slack_notification'):
                with patch('bulk_cert.load_job', return_value=None):
                    with patch('bulk_cert.build_bulk_certificate_html', return_value="<html></html>"):
                        with patch('builtins.open', MagicMock()):
                            with patch('bulk_cert.get_cert_dir', return_value=tempfile.gettempdir()):
                                run_bulk_cert_job(job_id)
        
        with BULK_CERT_JOBS_LOCK:
            assert BULK_CERT_JOBS[job_id]["status"] == "partial_success"

    def test_run_bulk_cert_job_certificate_generation_failure(self):
        """Test handling when certificate generation fails."""
        from bulk_cert import run_bulk_cert_job
        from app_config import BULK_CERT_JOBS, BULK_CERT_JOBS_LOCK
        
        job_id = "bulk-job-4"
        job = {
            "id": job_id,
            "status": "queued",
            "request": {"target_job_ids": ["job-1"]}
        }
        
        with BULK_CERT_JOBS_LOCK:
            BULK_CERT_JOBS[job_id] = job
        
        with patch('bulk_cert.persist_job'):
            with patch('bulk_cert.send_slack_notification'):
                with patch('bulk_cert.load_job', return_value={"job_type": "erase"}):
                    with patch('bulk_cert.build_certificate', side_effect=Exception("Cert error")):
                        with patch('bulk_cert.build_bulk_certificate_html', return_value="<html></html>"):
                            with patch('builtins.open', MagicMock()):
                                with patch('bulk_cert.get_cert_dir', return_value=tempfile.gettempdir()):
                                    run_bulk_cert_job(job_id)
        
        with BULK_CERT_JOBS_LOCK:
            assert BULK_CERT_JOBS[job_id]["status"] == "partial_success"

    def test_run_bulk_cert_job_progress_tracking(self):
        """Test that progress is tracked during execution."""
        from bulk_cert import run_bulk_cert_job
        from app_config import BULK_CERT_JOBS, BULK_CERT_JOBS_LOCK
        
        job_id = "bulk-job-5"
        job = {
            "id": job_id,
            "status": "queued",
            "request": {"target_job_ids": ["job-1", "job-2"]}
        }
        
        with BULK_CERT_JOBS_LOCK:
            BULK_CERT_JOBS[job_id] = job
        
        with patch('bulk_cert.persist_job'):
            with patch('bulk_cert.send_slack_notification'):
                with patch('bulk_cert.load_job', return_value={"job_type": "erase"}):
                    with patch('bulk_cert.build_certificate', return_value={"path": "/cert.pdf"}):
                        with patch('bulk_cert.build_bulk_certificate_html', return_value="<html></html>"):
                            with patch('builtins.open', MagicMock()):
                                with patch('bulk_cert.get_cert_dir', return_value=tempfile.gettempdir()):
                                    run_bulk_cert_job(job_id)
        
        with BULK_CERT_JOBS_LOCK:
            assert BULK_CERT_JOBS[job_id]["progress_percent"] == 100.0

    def test_run_bulk_cert_job_purges_old_logs(self):
        """Test that old logs are purged after completion."""
        from bulk_cert import run_bulk_cert_job
        from app_config import BULK_CERT_JOBS, BULK_CERT_JOBS_LOCK
        
        job_id = "bulk-job-6"
        job = {
            "id": job_id,
            "status": "queued",
            "request": {"target_job_ids": []}
        }
        
        with BULK_CERT_JOBS_LOCK:
            BULK_CERT_JOBS[job_id] = job
        
        with patch('bulk_cert.persist_job'):
            with patch('bulk_cert.send_slack_notification'):
                with patch('bulk_cert.purge_old_logs') as mock_purge:
                    with patch('bulk_cert.build_bulk_certificate_html', return_value="<html></html>"):
                        with patch('builtins.open', MagicMock()):
                            with patch('bulk_cert.get_cert_dir', return_value=tempfile.gettempdir()):
                                run_bulk_cert_job(job_id)
                                mock_purge.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
