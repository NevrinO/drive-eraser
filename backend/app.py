# --- START OF FILE backend/app.py ---
# Main entry point for Drive Eraser Flask application
# This file imports and registers all modular components

from app_config import app, logger, get_config_dir, load_policy
from database import init_wipe_db
import api_routes  # Import all route handlers

# Import route blueprints
from routes.drive_routes import drive_bp
from routes.certificate_routes import certificate_bp
from routes.admin_routes import admin_bp
from routes.bay_mapping_routes import bay_mapping_bp
from routes.discovery_routes import discovery_bp
from routes.template_routes import template_bp

# Register blueprints
app.register_blueprint(drive_bp)
app.register_blueprint(certificate_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(bay_mapping_bp)
app.register_blueprint(discovery_bp)
app.register_blueprint(template_bp)

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