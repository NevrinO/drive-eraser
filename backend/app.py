# --- START OF FILE backend/app.py ---
# Main entry point for Drive Eraser Flask application
# This file imports and registers all modular components

from app_config import app, logger, get_config_dir, load_policy
from database import init_wipe_db
import api_routes  # Import all route handlers

# Initialize database on module import (required for WSGI deployments)
init_wipe_db()

if __name__ == "__main__":
    config_dir = get_config_dir()
    policy = load_policy(config_dir)
    bind_address = policy.get("bind_address", "127.0.0.1")
    port = int(policy.get("port", 5000))
    logger.info(f"Drive Wipe Station starting on {bind_address}:{port} (config_dir={config_dir})")
    app.run(host=bind_address, port=port, debug=False)
# --- END OF FILE backend/app.py ---