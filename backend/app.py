# --- START OF FILE backend/app.py ---
# Main entry point for Drive Eraser Flask application
# This file imports and registers all modular components

import hmac
import signal
import threading
import time
from flask import jsonify
from app_config import app, logger, get_config_dir, load_policy, socketio
from routes import register_blueprints

# Register route blueprints (deferred to break circular imports)
# This is called here instead of app_config.py to avoid circular dependency
# when tests import from job_management → certificates → app_config
register_blueprints(app)

# Critical #4: Security gate middleware for remote access authentication
# Must be registered after blueprints to avoid Flask's "first request" state
@app.before_request
def security_gate():
    from flask import request
    from app_config import is_localhost, load_policy, calculate_session_token
    
    if not request.path.startswith("/api/"):
        return None
    if request.path in ("/api/auth/verify", "/api/status"):
        return None
    if is_localhost(request.remote_addr):
        return None

    policy = load_policy()
    lan_passphrase = policy.get("lan_passphrase", "eraser123")

    expected_token = calculate_session_token(lan_passphrase)
    cookie_token = request.cookies.get("admin_session")

    if hmac.compare_digest(cookie_token or "", expected_token):
        return None

    return jsonify({"authenticated": False, "message": "Authentication required for remote network access."}), 401

from database import init_wipe_db
from common import validate_policy
import api_routes  # Import all route handlers
import udev_listener  # Event-driven discovery with pyudev
from zero_check_manager import get_manager as get_zero_check_manager

# Critical #1: Centralized signal handler to prevent handler overwrites
# Import all modules with signal interruption flags
import job_management
import bulk_cert
import crypto_verification
import disk_ops

def _centralized_signal_handler(signum, frame):
    """Centralized signal handler that calls all module-specific handlers.
    
    This prevents signal handler overwrites by having a single handler
    registered in app.py that calls all module interruption flags.
    """
    job_management._handle_job_signal(signum, frame)
    bulk_cert._handle_bulk_cert_signal(signum, frame)
    crypto_verification._handle_verification_signal(signum, frame)
    disk_ops._handle_discovery_signal(signum, frame)
    udev_listener.stop_udev_listener()
    stop_smart_test_update_thread(wait=False)
    stop_orphaned_job_sweep_thread(wait=False)
    disk_ops.stop_extended_smart_pool(wait=False)
    # Exit gracefully after setting interruption flags
    import sys
    logger.info(f"Received signal {signum}, shutting down...")
    sys.exit(130)

# Critical #3: Add CSP HTTP header
@app.after_request
def add_security_headers(response):
    csp_header = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self';"
    response.headers['Content-Security-Policy'] = csp_header
    return response

# Global error handler to ensure all errors return JSON instead of HTML
@app.errorhandler(Exception)
def handle_exception(e):
    """Global exception handler to return JSON for all errors."""
    logger.error(f"Unhandled exception: {e}", exc_info=True)
    return jsonify({"error": "Internal server error"}), 500

# Background thread to update SMART test status in database
SMART_TEST_UPDATE_INTERVAL = 30  # Check every 30 seconds
smart_test_update_thread = None
smart_test_update_stop_event = threading.Event()
smart_test_update_thread_lock = threading.Lock()

def update_smart_test_status_background():
    """Background thread to update SMART test status in database.
    
    This ensures that tests complete even if the user closes the modal
    and stops polling. The database is updated based on drive status.
    Uses optimistic locking to prevent race conditions with frontend polling.
    """
    import os
    from database import get_smart_test_history, update_smart_test_run
    from smart_parsing import get_smart_test_status
    from routes.admin_routes import should_update_test_status, should_trust_completion_status
    
    logger.info("SMART test status background thread started")
    
    while not smart_test_update_stop_event.is_set():
        try:
            # Get all tests that are still in progress
            recent_tests = get_smart_test_history(limit=100)
            
            for test in recent_tests:
                if test.get("status") not in ("started", "in_progress"):
                    continue
                
                device = test.get("device")
                if not device:
                    continue
                
                try:
                    # Get live status from drive
                    status_result = get_smart_test_status(device)
                    
                    if "error" in status_result:
                        continue
                    
                    record_id = test.get("id")
                    started_at = test.get("started_at")
                    current_updated_at = test.get("updated_at")
                    db_status = test.get("status")
                    test_type = test.get("test_type")
                    drive_status = status_result.get("status")
                    
                    # Transition: DB "started" → "in_progress" when drive confirms test is running
                    if db_status == "started" and drive_status == "in_progress":
                        updated = update_smart_test_run(record_id, "in_progress",
                                                        current_updated_at=current_updated_at)
                        if updated:
                            logger.info(f"Background update: SMART test {device} confirmed in progress by drive status register")
                        continue
                    
                    # Update database if drive shows completed and trust conditions met
                    if drive_status == "completed" and should_trust_completion_status(started_at, db_status, test_type):
                        latest_result = status_result.get("latest_result", {})
                        passed = latest_result.get("passed")
                        
                        if passed is True:
                            result = "passed"
                        elif passed is False:
                            result = "failed"
                        else:
                            drive_status_str = latest_result.get("status", "").lower()
                            if ("passed" in drive_status_str or "completed without error" in drive_status_str) and "failed" not in drive_status_str:
                                result = "passed"
                            elif "failed" in drive_status_str or "error" in drive_status_str:
                                result = "failed"
                            else:
                                result = "unknown"
                        
                        logger.info(f"Background update: SMART test {device} completed with result={result}")
                        # Use optimistic locking with current_updated_at
                        updated = update_smart_test_run(record_id, "completed", result=result, 
                                                        output_json=status_result.get("self_test_log_table"),
                                                        current_updated_at=current_updated_at)
                        if not updated:
                            logger.debug(f"Background update: SMART test {device} record was modified by another process, skipping")
                    
                    # Update database if drive shows failed and trust conditions met
                    elif drive_status == "failed" and should_trust_completion_status(started_at, db_status, test_type):
                        logger.info(f"Background update: SMART test {device} failed")
                        # Use optimistic locking with current_updated_at
                        updated = update_smart_test_run(record_id, "failed", result="failed",
                                                        output_json=status_result.get("self_test_log_table"),
                                                        current_updated_at=current_updated_at)
                        if not updated:
                            logger.debug(f"Background update: SMART test {device} record was modified by another process, skipping")
                    
                    # Update database if drive shows aborted and trust conditions met
                    elif drive_status == "aborted" and should_trust_completion_status(started_at, db_status, test_type):
                        logger.info(f"Background update: SMART test {device} aborted")
                        updated = update_smart_test_run(record_id, "failed", result="aborted",
                                                        output_json=status_result.get("self_test_log_table"),
                                                        current_updated_at=current_updated_at)
                        if not updated:
                            logger.debug(f"Background update: SMART test {device} record was modified by another process, skipping")
                    
                    # Drive shows no_tests/unknown after trust conditions met: test is no longer running
                    # but we can't determine pass/fail from the drive's log. Mark as completed
                    # with result "unknown" so the card stops showing "running".
                    elif drive_status in ("no_tests", "unknown") and should_trust_completion_status(started_at, db_status, test_type):
                        logger.info(f"Background update: SMART test {device} no longer running (status={drive_status}), marking completed with unknown result")
                        updated = update_smart_test_run(record_id, "completed", result="unknown",
                                                        current_updated_at=current_updated_at)
                        if not updated:
                            logger.debug(f"Background update: SMART test {device} record was modified by another process, skipping")
                
                except Exception as e:
                    logger.warning(f"Failed to update SMART test status for {device}: {e}")
        
        except Exception as e:
            logger.error(f"SMART test status background thread error: {e}")
        
        # Wait for interval or stop event
        smart_test_update_stop_event.wait(SMART_TEST_UPDATE_INTERVAL)
    
    logger.info("SMART test status background thread stopped")

def start_smart_test_update_thread():
    """Start the background thread for SMART test status updates."""
    global smart_test_update_thread
    with smart_test_update_thread_lock:
        if smart_test_update_thread is None or not smart_test_update_thread.is_alive():
            smart_test_update_stop_event.clear()
            smart_test_update_thread = threading.Thread(target=update_smart_test_status_background, daemon=True)
            smart_test_update_thread.start()
            logger.info("Started SMART test status background thread")

def stop_smart_test_update_thread(wait=True):
    """Stop the background thread for SMART test status updates.

    Args:
        wait: If True, join the thread (up to 5s). If False, just signal
              and nullify without joining — used from signal handlers where
              blocking is unsafe. The daemon thread is killed on process exit.
    """
    global smart_test_update_thread
    with smart_test_update_thread_lock:
        thread = smart_test_update_thread
        if thread and thread.is_alive():
            smart_test_update_stop_event.set()
        else:
            return
    if wait:
        thread.join(timeout=5)
    with smart_test_update_thread_lock:
        if smart_test_update_thread is thread:
            smart_test_update_thread = None
    logger.info("Stopped SMART test status background thread")


# --- Orphaned erase job recovery ---

def recover_orphaned_jobs_on_startup():
    """Mark all DB jobs with status 'running' or 'queued' as failed on startup.
    
    When the server restarts, all in-memory ERASE_JOBS are lost and the wipe
    subprocesses are killed. Any job that was running is now dead and must be
    marked as failed so the UI doesn't show it as running indefinitely.
    """
    import sqlite3
    from contextlib import closing
    from common import get_db_path
    from database import persist_job, load_job
    from datetime import datetime, timezone
    
    now = datetime.now(timezone.utc).isoformat()
    try:
        with closing(sqlite3.connect(get_db_path(), timeout=30.0)) as conn, conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id FROM erase_jobs WHERE status IN ('running', 'queued')"
            ).fetchall()
        
        if not rows:
            logger.info("Startup recovery: no orphaned jobs found")
            return
        
        logger.warning(f"Startup recovery: found {len(rows)} orphaned job(s) to mark as failed")
        for row in rows:
            job_id = row["id"]
            job = load_job(job_id)
            if job:
                job["status"] = "failed"
                job["finished_at"] = now
                job["error"] = "Server restarted while job was running"
                persist_job(job)
                logger.info(f"Startup recovery: marked job {job_id} as failed")
    except Exception as e:
        logger.error(f"Startup recovery failed: {e}")


# Background thread for periodic sweep of orphaned/stuck erase jobs
ORPHANED_JOB_SWEEP_INTERVAL = 60  # Check every 60 seconds
orphaned_job_sweep_thread = None
orphaned_job_sweep_stop_event = threading.Event()
orphaned_job_sweep_thread_lock = threading.Lock()

def sweep_orphaned_jobs_background():
    """Background thread that periodically checks for orphaned or stuck erase jobs.
    
    Two checks:
    1. DB jobs with status 'running'/'queued' that are NOT in ERASE_JOBS (orphaned by restart)
    2. Jobs in ERASE_JOBS with status 'running' that have exceeded their erase timeout (stuck thread)
    
    Both are marked as failed with an appropriate error message.
    """
    import sqlite3
    from contextlib import closing
    from datetime import datetime, timezone
    from common import get_db_path
    from database import persist_job, load_job
    from app_config import ERASE_JOBS, ERASE_JOBS_LOCK
    from job_management import _get_erase_timeout
    
    logger.info("Orphaned job sweep background thread started")
    
    while not orphaned_job_sweep_stop_event.is_set():
        try:
            now = datetime.now(timezone.utc)
            now_iso = now.isoformat()
            
            # Check 1: DB running/queued jobs not in ERASE_JOBS (orphaned by restart)
            try:
                with closing(sqlite3.connect(get_db_path(), timeout=30.0)) as conn, conn:
                    conn.row_factory = sqlite3.Row
                    rows = conn.execute(
                        "SELECT id FROM erase_jobs WHERE status IN ('running', 'queued')"
                    ).fetchall()
                
                for row in rows:
                    job_id = row["id"]
                    with ERASE_JOBS_LOCK:
                        in_memory = job_id in ERASE_JOBS
                    
                    if not in_memory:
                        # Orphaned: in DB but not in memory — mark as failed
                        job = load_job(job_id)
                        if job and job.get("status") in ("running", "queued"):
                            job["status"] = "failed"
                            job["finished_at"] = now_iso
                            job["error"] = "Job orphaned: not found in memory (possible server restart)"
                            persist_job(job)
                            logger.warning(f"Job sweep: marked orphaned job {job_id} as failed")
            except Exception as e:
                logger.error(f"Job sweep DB check failed: {e}")
            
            # Check 2: Jobs in ERASE_JOBS with status 'running' that exceeded their erase timeout
            try:
                with ERASE_JOBS_LOCK:
                    stuck_jobs = []
                    for job_id, job in ERASE_JOBS.items():
                        if job.get("status") != "running":
                            continue
                        started_at = job.get("started_at")
                        if not started_at:
                            continue
                        try:
                            start_time = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                            elapsed = (now - start_time).total_seconds()
                            method = job.get("request", {}).get("method", "overwrite")
                            timeout = _get_erase_timeout(method)
                            if elapsed > timeout:
                                stuck_jobs.append((job_id, method, int(elapsed), timeout))
                        except Exception:
                            continue
                
                for job_id, method, elapsed, timeout in stuck_jobs:
                    with ERASE_JOBS_LOCK:
                        job = ERASE_JOBS.get(job_id)
                        if job and job.get("status") == "running":
                            job["status"] = "failed"
                            job["finished_at"] = now_iso
                            job["error"] = f"Job timed out: {method} erase exceeded {timeout}s (elapsed {elapsed}s)"
                            persist_job(job)
                            ERASE_JOBS.pop(job_id, None)
                            logger.error(f"Job sweep: marked stuck job {job_id} as failed ({method} exceeded {timeout}s)")
            except Exception as e:
                logger.error(f"Job sweep stuck check failed: {e}")
        
        except Exception as e:
            logger.error(f"Orphaned job sweep thread error: {e}")
        
        orphaned_job_sweep_stop_event.wait(ORPHANED_JOB_SWEEP_INTERVAL)
    
    logger.info("Orphaned job sweep background thread stopped")


def start_orphaned_job_sweep_thread():
    """Start the background thread for orphaned job sweep."""
    global orphaned_job_sweep_thread
    with orphaned_job_sweep_thread_lock:
        if orphaned_job_sweep_thread is None or not orphaned_job_sweep_thread.is_alive():
            orphaned_job_sweep_stop_event.clear()
            orphaned_job_sweep_thread = threading.Thread(target=sweep_orphaned_jobs_background, daemon=True)
            orphaned_job_sweep_thread.start()
            logger.info("Started orphaned job sweep background thread")


def stop_orphaned_job_sweep_thread(wait=True):
    """Stop the background thread for orphaned job sweep."""
    global orphaned_job_sweep_thread
    with orphaned_job_sweep_thread_lock:
        thread = orphaned_job_sweep_thread
        if thread and thread.is_alive():
            orphaned_job_sweep_stop_event.set()
        else:
            return
    if wait:
        thread.join(timeout=5)
    with orphaned_job_sweep_thread_lock:
        if orphaned_job_sweep_thread is thread:
            orphaned_job_sweep_thread = None
    logger.info("Stopped orphaned job sweep background thread")

def create_app():
    """Application factory: initializes database, background threads, and managers.
    
    Call this once at startup (from main() or wsgi.py) to trigger side effects.
    Importing app.py alone does NOT trigger side effects — safe for testing.
    
    Returns:
        (app, socketio) tuple
    """
    # Initialize database
    init_wipe_db()
    
    # Recover orphaned jobs from previous server instance
    recover_orphaned_jobs_on_startup()
    
    # Set WebSocket managers
    udev_listener.set_websocket_manager(socketio)
    disk_ops.set_websocket_manager(socketio)
    
    # Initialize zero-check manager with current policy concurrency limit
    config_dir = get_config_dir()
    _policy = load_policy(config_dir)
    zero_check_concurrency = int(_policy.get("zero_detection_concurrency_limit", 8))
    get_zero_check_manager(socketio=socketio, max_concurrency=zero_check_concurrency)
    
    # Start udev event listener for real-time device discovery
    udev_listener.start_udev_listener()
    
    # Start SMART test status background thread
    start_smart_test_update_thread()
    
    # Start orphaned job sweep background thread
    start_orphaned_job_sweep_thread()
    
    # Register signal handlers (only in main thread)
    try:
        signal.signal(signal.SIGTERM, _centralized_signal_handler)
        signal.signal(signal.SIGINT, _centralized_signal_handler)
    except ValueError:
        # Signal handlers can only be registered in main thread
        pass
    
    return app, socketio


def main():
    """Run the Drive Eraser Flask-SocketIO server."""
    app, socketio = create_app()
    
    config_dir = get_config_dir()
    policy = load_policy(config_dir)
    
    # Critical #6: Validate configuration before starting server
    try:
        validate_policy(policy)
    except ValueError as e:
        logger.error(f"Configuration validation failed: {e}")
        raise SystemExit(1) from e
    
    bind_address = policy.get("bind_address", "127.0.0.1")
    port = int(policy.get("port", 5000))
    logger.info(f"Drive Wipe Station starting on {bind_address}:{port} (config_dir={config_dir})")
    # allow_unsafe_werkzeug=True is required for the built-in Werkzeug server in production mode
    socketio.run(app, host=bind_address, port=port, debug=False, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    main()
# --- END OF FILE backend/app.py ---