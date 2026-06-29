# --- START OF FILE backend/wsgi.py ---
"""WSGI entry point for Gunicorn/uWSGI deployment.

Usage:
    gunicorn -k gevent -w 1 --bind 0.0.0.0:5000 wsgi:app
"""
from app import create_app

app, socketio = create_app()

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=False, allow_unsafe_werkzeug=True)
# --- END OF FILE backend/wsgi.py ---
