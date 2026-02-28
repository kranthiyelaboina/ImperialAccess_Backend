"""
auth.py — JWT authentication helpers and route decorators
"""
import jwt
import bcrypt
from datetime import datetime, timedelta
from functools import wraps
from flask import request, jsonify
from config import JWT_SECRET, JWT_EXPIRY_HOURS


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def check_password(password: str, hashed: str) -> bool:
    """Verify a password against its hash."""
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


def create_token(user_id: str, role: str) -> str:
    """Create a JWT token."""
    payload = {
        "user_id": user_id,
        "role": role,
        "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRY_HOURS),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def decode_token(token: str) -> dict:
    """Decode and validate a JWT token."""
    return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])


def get_token_from_request():
    """Extract JWT token from Authorization header."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    return None


# =====================================================
# DECORATORS
# =====================================================
def login_required(f):
    """Require valid JWT token for a route."""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = get_token_from_request()
        if not token:
            return jsonify({"success": False, "error": "Missing authorization token"}), 401
        try:
            payload = decode_token(token)
            request.user_id = payload["user_id"]
            request.user_role = payload["role"]
        except jwt.ExpiredSignatureError:
            return jsonify({"success": False, "error": "Token expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"success": False, "error": "Invalid token"}), 401
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Require valid JWT token with admin role."""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = get_token_from_request()
        if not token:
            return jsonify({"success": False, "error": "Missing authorization token"}), 401
        try:
            payload = decode_token(token)
            if payload["role"] != "admin":
                return jsonify({"success": False, "error": "Admin access required"}), 403
            request.user_id = payload["user_id"]
            request.user_role = payload["role"]
        except jwt.ExpiredSignatureError:
            return jsonify({"success": False, "error": "Token expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"success": False, "error": "Invalid token"}), 401
        return f(*args, **kwargs)
    return decorated


def guest_required(f):
    """Require valid JWT token with guest role."""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = get_token_from_request()
        if not token:
            return jsonify({"success": False, "error": "Missing authorization token"}), 401
        try:
            payload = decode_token(token)
            if payload["role"] != "guest":
                return jsonify({"success": False, "error": "Guest access required"}), 403
            request.user_id = payload["user_id"]
            request.user_role = payload["role"]
        except jwt.ExpiredSignatureError:
            return jsonify({"success": False, "error": "Token expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"success": False, "error": "Invalid token"}), 401
        return f(*args, **kwargs)
    return decorated
