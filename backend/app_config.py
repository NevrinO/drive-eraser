# --- START OF FILE backend/app_config.py ---
from flask import Flask
from flask_cors import CORS
import sys
import os
import re
import logging
from logging.handlers import RotatingFileHandler
from threading import Lock
import hmac
import hashlib
import socket

from common import get_logs_dir, load_policy, get_config_dir

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
                "GET /api/erase/history"
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
        root_logger.addHandler(handler)
        
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        console_handler.addFilter(polling_filter)
        root_logger.addHandler(console_handler)
    except Exception as e:
        print(f"Failed to setup file logging: {str(e)}", file=sys.stderr)

setup_application_logging()
logger = logging.getLogger("app")

app = Flask(__name__)
CORS(app)

ERASE_JOBS = {}
ERASE_JOBS_LOCK = Lock()

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(PROJECT_ROOT, "frontend")

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0)
        s.connect(('8.8.8.8', 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def calculate_session_token(passphrase):
    return hmac.new(passphrase.encode('utf-8'), b"dws_admin_session", hashlib.sha256).hexdigest()

def is_localhost(ip):
    return ip in ("127.0.0.1", "::1", "localhost")

@app.before_request
def security_gate():
    from flask import request, jsonify
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
# --- END OF FILE backend/app_config.py ---
