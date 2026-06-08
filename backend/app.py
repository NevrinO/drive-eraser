# --- START OF FILE backend/app.py ---
# Main entry point for Drive Eraser Flask application
# This file imports and registers all modular components

import signal
from app_config import app, logger, get_config_dir, load_policy
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

# Initialize database on module import (required for WSGI deployments)
init_wipe_db()

if __name__ == "__main__":
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
    app.run(host=bind_address, port=port, debug=False)
# --- END OF FILE backend/app.py ---