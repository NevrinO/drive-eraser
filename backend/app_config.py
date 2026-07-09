# --- START OF FILE backend/app_config.py ---
from flask import Flask
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_socketio import SocketIO
import sys
import os
import re
import logging
from logging.handlers import RotatingFileHandler
from threading import Lock, Semaphore
import hmac
import hashlib
import socket

from common import get_logs_dir, get_config_dir, load_policy, PROJECT_ROOT

class PollingFilter(logging.Filter):
    """
    Filters out routine high-frequency polling telemetry requests from the Werkzeug logs
    unless they return a non-success HTTP status code (such as a 4xx or 5xx error).
    """
    def filter(self, record):
        try:
            msg = record.getMessage()
            # Suppress routine ANSI escape codes that colorize logs in development
            clean_msg = re.sub(r'\x1b\[[0-9;]*m', '', msg)
            
            # Identify repetitive poll endpoints
            is_poll_endpoint = any(x in clean_msg for x in [
                "GET /api/drives",
                "GET /api/admin/metrics",
                "GET /api/erase/history",
                "GET /api/admin/enclosures",
                "/socket.io/"
            ])
            
            if is_poll_endpoint:
                # Suppress if HTTP status represents success (200 OK or 304 Not Modified)
                if " 200 " in clean_msg or " 304 " in clean_msg:
                    return False
        except Exception:
            pass
        return True

def setup_application_logging():
    try:
        logs_dir = get_logs_dir()
        log_file = os.path.join(logs_dir, "app.log")
        
        # Configure file rotating handler (capped strictly at 10MB as requested)
        handler = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=3)
        formatter = logging.Formatter('%(asctime)s %(levelname)s [%(name)s] %(message)s')
        handler.setFormatter(formatter)
        
        # Inject the polling filter to intercept Werkzeug telemetry lines
        polling_filter = PollingFilter()
        handler.addFilter(polling_filter)
        
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        # Suppress routine per-request Werkzeug logging; only warnings/errors surface
        logging.getLogger("werkzeug").setLevel(logging.WARNING)
        root_logger.addHandler(handler)
        
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        console_handler.addFilter(polling_filter)
        root_logger.addHandler(console_handler)
    except Exception as e:
        print(f"Failed to setup file logging: {str(e)}", file=sys.stderr)

logger = logging.getLogger("app")

# --- Lazy initialization: app and socketio are None until init_app() is called ---
# This prevents import-time side effects (duplicate logging handlers, policy.json dependency,
# Flask app creation) when modules are imported in tests or for type checking.
# limiter is created eagerly (without an app) so @limiter.limit decorators work at
# import time regardless of init_app() call order. The app binding happens in init_app().
app = None
socketio = None
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://",
    strategy="fixed-window"
)

_initialized = False

def init_app():
    """Initialize Flask app, SocketIO, Limiter, CORS, and logging.
    
    Idempotent — safe to call multiple times (e.g., from app.py and conftest.py).
    Must be called before any module imports `app`, `socketio`, or `limiter`
    from app_config at usage time.
    """
    global _initialized, app, socketio
    if _initialized:
        return
    _initialized = True
    
    setup_application_logging()
    
    app = Flask(__name__)
    
    # Initialize SocketIO for real-time WebSocket communication
    # SocketIO CORS is set to '*' because the station is accessed from LAN IPs
    # that aren't in the policy's allowed_cors_origins list (which only covers
    # localhost). HTTP CORS (below) still enforces the policy-based origins.
    # Access control is provided by the IP allowlist and authentication in
    # security_gate, not by SocketIO CORS.
    policy = load_policy()
    allowed_origins = policy.get("allowed_cors_origins", ["http://localhost:5000", "http://127.0.0.1:5000"])
    socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')
    
    # High #11: Bind Flask-Limiter to the app.
    # limiter was created eagerly at module level (without an app) so @limiter.limit
    # decorators work at import time. Here we bind it to the Flask app.
    # NOTE: Using in-memory storage (storage_uri="memory://") which is suitable for single-worker deployments.
    # For multi-worker deployments (e.g., gunicorn with multiple workers), configure Redis or Memcached:
    #   storage_uri="redis://localhost:6379" or storage_uri="memcached://localhost:11211"
    # This is a known limitation documented for the current single-worker architecture.
    limiter.init_app(app)
    
    # Critical #2: CORS origins loaded from policy configuration (above, shared with SocketIO)
    CORS(app, origins=allowed_origins)
    
    # Critical #4: Configure SameSite cookie attribute for CSRF protection
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024

ERASE_JOBS = {}
ERASE_JOBS_LOCK = Lock()

# Semaphore for limiting concurrent wipe operations
WIPE_SEMAPHORE = None
WIPE_SEMAPHORE_LOCK = Lock()
WIPE_SEMAPHORE_CURRENT_LIMIT = None

def get_wipe_semaphore():
    """Get or create the wipe semaphore based on policy configuration."""
    global WIPE_SEMAPHORE, WIPE_SEMAPHORE_CURRENT_LIMIT
    with WIPE_SEMAPHORE_LOCK:
        try:
            policy = load_policy()
            max_concurrent = policy.get("max_concurrent_wipes", 34)
            # Clamp to reasonable bounds
            max_concurrent = max(1, min(max_concurrent, 256))
        except Exception:
            max_concurrent = 64
        
        # Recreate semaphore if limit changed
        if WIPE_SEMAPHORE is None or WIPE_SEMAPHORE_CURRENT_LIMIT != max_concurrent:
            WIPE_SEMAPHORE = Semaphore(max_concurrent)
            WIPE_SEMAPHORE_CURRENT_LIMIT = max_concurrent
        
        return WIPE_SEMAPHORE

BULK_CERT_JOBS = {}
BULK_CERT_JOBS_LOCK = Lock()

# Lesson #92: Atomic check-then-act for SMART test allocation
SMART_TEST_LOCKS = {}
SMART_TEST_LOCKS_LOCK = Lock()

FRONTEND_DIR = os.path.join(PROJECT_ROOT, "frontend")

def get_local_ip():
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0)
        s.connect(('8.8.8.8', 1))
        ip = s.getsockname()[0]
        return ip
    except Exception:
        return "127.0.0.1"
    finally:
        if s:
            s.close()

def calculate_session_token(passphrase):
    return hmac.new(passphrase.encode('utf-8'), b"dws_admin_session", hashlib.sha256).hexdigest()

def is_localhost(ip):
    if ip in ("127.0.0.1", "::1", "localhost"):
        return True
    # Handle IPv4-mapped IPv6 (e.g. ::ffff:127.0.0.1)
    return ip.startswith("::ffff:127.0.0.1")

# Blueprint registration and security middleware are deferred to app.py to break circular imports.
# Route modules import from app_config.py (logger, limiter, etc.).
# App/socketio are initialized lazily via init_app() — called from app.py
# or tests/conftest.py before any module uses them. limiter is created eagerly
# (without an app) and bound to the app in init_app().
# --- END OF FILE backend/app_config.py ---
