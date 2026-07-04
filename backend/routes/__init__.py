# Backend routes module
# This module contains route blueprints organized by functionality

def register_blueprints(app):
    """Register all route blueprints with the Flask app.

    This function is called from app_config.py to break circular imports.
    Route modules import from app_config.py (logger, limiter, etc.),
    while app_config.py calls this function to register blueprints.
    """
    from routes.drive_routes import drive_bp
    from routes.certificate_routes import certificate_bp
    from routes.admin_routes import admin_bp
    from routes.bay_mapping_routes import bay_mapping_bp
    from routes.discovery_routes import discovery_bp
    from routes.template_routes import template_bp
    from routes.support_routes import support_bp
    from routes.policy_routes import policy_bp
    from routes.enclosure_routes import enclosure_bp
    from routes.smart_routes import smart_bp

    app.register_blueprint(drive_bp)
    app.register_blueprint(certificate_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(bay_mapping_bp)
    app.register_blueprint(discovery_bp)
    app.register_blueprint(template_bp)
    app.register_blueprint(support_bp)
    app.register_blueprint(policy_bp)
    app.register_blueprint(enclosure_bp)
    app.register_blueprint(smart_bp)
