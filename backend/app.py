# --- START OF FILE backend/app.py ---
# Main entry point for Drive Eraser Flask application
# This file imports and registers all modular components

import signal
import threading
import time
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
    from flask import request, jsonify
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

    if cookie_token == expected_token:
        return None

    return jsonify({"authenticated": False, "message": "Authentication required for remote network access."}), 401

from database import init_wipe_db
from common import validate_policy
import api_routes  # Import all route handlers
import udev_listener  # Event-driven discovery with pyudev

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
    stop_smart_test_update_thread()
    # Exit gracefully after setting interruption flags
    import sys
    logger.info(f"Received signal {signum}, shutting down...")
    sys.exit(0)

# Register centralized signal handlers (only in main thread)
try:
    signal.signal(signal.SIGTERM, _centralized_signal_handler)
    signal.signal(signal.SIGINT, _centralized_signal_handler)
except ValueError:
    # Signal handlers can only be registered in main thread
    pass

# Critical #3: Add CSP HTTP header
@app.after_request
def add_security_headers(response):
    csp_header = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self';"
    response.headers['Content-Security-Policy'] = csp_header
    return response

# Global error handler to ensure all errors return JSON instead of HTML
@app.errorhandler(Exception)
def handle_exception(e):
    """Global exception handler to return JSON for all errors."""
    logger.error(f"Unhandled exception: {e}", exc_info=True)
    # Return JSON error response for all exceptions
    return jsonify({"error": str(e)}), 500

# Initialize database on module import (required for WSGI deployments)
init_wipe_db()

# Set WebSocket manager for udev event listener
udev_listener.set_websocket_manager(socketio)

# Start udev event listener for real-time device discovery
udev_listener.start_udev_listener()

# Background thread to update SMART test status in database
SMART_TEST_UPDATE_INTERVAL = 30  # Check every 30 seconds
smart_test_update_thread = None
smart_test_update_stop_event = threading.Event()

def update_smart_test_status_background():
    """Background thread to update SMART test status in database.
    
    This ensures that tests complete even if the user closes the modal
    and stops polling. The database is updated based on drive status.
    Uses optimistic locking to prevent race conditions with frontend polling.
    """
    import os
    from database import get_smart_test_history, update_smart_test_run
    from smart_parsing import get_smart_test_status
    from routes.admin_routes import should_update_test_status
    
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
                
                # Check if device still exists before querying SMART status
                if not os.path.exists(device):
                    logger.debug(f"Device {device} no longer exists, skipping SMART status check")
                    continue
                
                try:
                    # Get live status from drive
                    status_result = get_smart_test_status(device)
                    
                    if "error" in status_result:
                        continue
                    
                    record_id = test.get("id")
                    started_at = test.get("started_at")
                    current_updated_at = test.get("updated_at")
                    drive_status = status_result.get("status")
                    
                    # Update database if drive shows completed and grace period elapsed
                    if drive_status == "completed" and should_update_test_status(started_at):
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
                    
                    # Update database if drive shows failed and grace period elapsed
                    elif drive_status == "failed" and should_update_test_status(started_at):
                        logger.info(f"Background update: SMART test {device} failed")
                        # Use optimistic locking with current_updated_at
                        updated = update_smart_test_run(record_id, "failed", result="failed",
                                                        output_json=status_result.get("self_test_log_table"),
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
    if smart_test_update_thread is None or not smart_test_update_thread.is_alive():
        smart_test_update_stop_event.clear()
        smart_test_update_thread = threading.Thread(target=update_smart_test_status_background, daemon=True)
        smart_test_update_thread.start()
        logger.info("Started SMART test status background thread")

def stop_smart_test_update_thread():
    """Stop the background thread for SMART test status updates."""
    global smart_test_update_thread
    if smart_test_update_thread and smart_test_update_thread.is_alive():
        smart_test_update_stop_event.set()
        smart_test_update_thread.join(timeout=5)
        logger.info("Stopped SMART test status background thread")

# Start the background thread
start_smart_test_update_thread()

def main():
    """Run the Drive Eraser Flask-SocketIO server."""
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