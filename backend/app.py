"""
app.py — Flask application entry point

Registers all blueprints, sets up MongoDB indexes,
seeds a default admin, and starts the agent service.
"""

import os
import sys
import traceback

# Ensure backend/ is on the Python path
sys.path.insert(0, os.path.dirname(__file__))

from flask import Flask, jsonify
from flask_cors import CORS

from config import CAMERA_INDEX, AGENT_DEFAULT_MODE
from db import setup_indexes, users_col
from auth import hash_password
from agent_service import get_agent_service

# Blueprint imports
from routes.auth_routes import auth_bp
from routes.guest_routes import guest_bp
from routes.admin_routes import admin_bp
from routes.agent_routes import agent_bp
from routes.concierge_routes import concierge_bp


def create_app(start_agent=True):
    """Flask application factory."""
    app = Flask(__name__)

    # CORS — allow all origins for Postman / frontend dev
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    # ── Global JSON error handler ──
    # Ensures unhandled exceptions ALWAYS return JSON, never HTML.
    @app.errorhandler(Exception)
    def handle_exception(e):
        """Catch-all: convert any unhandled error into a JSON 500 response."""
        tb = traceback.format_exc()
        print(f"[APP] Unhandled exception:\n{tb}")
        return jsonify({
            "success": False,
            "error": str(e) or "Internal server error",
        }), 500

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"success": False, "error": "Endpoint not found"}), 404

    @app.errorhandler(405)
    def method_not_allowed(e):
        return jsonify({"success": False, "error": "Method not allowed"}), 405

    # Register blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(guest_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(agent_bp)
    app.register_blueprint(concierge_bp)

    # Setup MongoDB indexes & seed admin (deferred — fails gracefully if DB not configured yet)
    with app.app_context():
        try:
            setup_indexes()
            seed_admin()
        except Exception as e:
            print(f"[APP] DB setup deferred (configure MONGO_URI in .env): {e}")

    # Health check
    @app.route("/api/health", methods=["GET"])
    def health():
        svc = get_agent_service()
        return jsonify({
            "status": "ok",
            "agent_running": svc.is_running(),
        })

    # Start agent camera loop
    if start_agent:
        show_window = os.environ.get("SHOW_CAMERA_WINDOW", "false").lower() in ("1", "true", "yes")
        svc = get_agent_service()
        svc.start(camera_index=CAMERA_INDEX, mode=AGENT_DEFAULT_MODE, show_window=show_window)

    return app


def seed_admin():
    """Create default admin user if none exists."""
    existing = users_col().find_one({"role": "admin"})
    if not existing:
        admin_doc = {
            "username": "admin",
            "password_hash": hash_password("admin123"),
            "role": "admin",
            "full_name": "System Admin",
            "email": "admin@airport-lounge.local",
            "phone": None,
        }
        users_col().insert_one(admin_doc)
        print("[APP] Default admin created (username: admin, password: admin123)")
    else:
        print("[APP] Admin account already exists.")


# ==========================================================
# RUN
# ==========================================================
if __name__ == "__main__":
    app = create_app(start_agent=True)
    print("\n[APP] Flask server starting on http://127.0.0.1:5000")
    print("[APP] Default admin login — username: admin / password: admin123")
    print("[APP] Agent video feed: http://127.0.0.1:5000/api/agent/video_feed\n")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
