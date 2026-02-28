"""
auth_routes.py — Authentication endpoints

POST /api/login_admin
POST /api/login_guest
POST /api/register_guest
POST /api/register_guest/face
POST /api/register_guest/face_start   (agent-based, non-blocking)
GET  /api/register_guest/face_progress
POST /api/register_guest/face_finish
"""

import sys
import os
import numpy as np
from flask import Blueprint, request, jsonify
from bson import ObjectId

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from auth import hash_password, check_password, create_token, login_required
from db import users_col, guest_profiles_col
from models import create_user, create_guest_profile, serialize_doc

auth_bp = Blueprint("auth", __name__)

# In-memory sessions for browser-frame-based face registration
_face_reg_sessions = {}  # guest_id -> {"name", "engine", "embeddings", "max_samples", "frame_count"}


# =====================================================
# 1. POST /api/login_admin
# =====================================================
@auth_bp.route("/api/login_admin", methods=["POST"])
def login_admin():
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "No JSON body provided"}), 400

    username = data.get("username", "").strip()
    password = data.get("password", "")

    if not username or not password:
        return jsonify({"success": False, "error": "Username and password required"}), 400

    user = users_col().find_one({"username": username, "role": "admin"})
    if not user:
        return jsonify({"success": False, "error": "Invalid credentials"}), 401

    if not check_password(password, user["password_hash"]):
        return jsonify({"success": False, "error": "Invalid credentials"}), 401

    token = create_token(str(user["_id"]), "admin")

    return jsonify({
        "success": True,
        "token": token,
        "user": {
            "id": str(user["_id"]),
            "username": user["username"],
            "full_name": user["full_name"],
            "role": "admin",
        }
    })


# =====================================================
# 2. POST /api/login_guest
# =====================================================
@auth_bp.route("/api/login_guest", methods=["POST"])
def login_guest():
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "No JSON body provided"}), 400

    username = data.get("username", "").strip()
    password = data.get("password", "")

    if not username or not password:
        return jsonify({"success": False, "error": "Username and password required"}), 400

    user = users_col().find_one({"username": username, "role": "guest"})
    if not user:
        return jsonify({"success": False, "error": "Invalid credentials"}), 401

    if not check_password(password, user["password_hash"]):
        return jsonify({"success": False, "error": "Invalid credentials"}), 401

    # Get guest profile
    profile = guest_profiles_col().find_one({"user_id": user["_id"]})

    token = create_token(str(user["_id"]), "guest")

    resp = {
        "success": True,
        "token": token,
        "user": {
            "id": str(user["_id"]),
            "username": user["username"],
            "full_name": user["full_name"],
            "role": "guest",
        }
    }

    if profile:
        resp["user"]["face_registered"] = profile.get("face_registered", False)
        resp["user"]["membership_type"] = profile.get("membership_type", "standard")
        resp["user"]["guest_id"] = str(profile["_id"])

    return jsonify(resp)


# =====================================================
# 3. POST /api/register_guest
# =====================================================
@auth_bp.route("/api/register_guest", methods=["POST"])
def register_guest():
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "No JSON body provided"}), 400

    username = data.get("username", "").strip()
    password = data.get("password", "")
    full_name = data.get("full_name", "").strip()
    email = data.get("email", "").strip() or None
    phone = data.get("phone", "").strip() or None

    if not username or not password or not full_name:
        return jsonify({"success": False, "error": "username, password, and full_name required"}), 400

    # Check duplicates
    if users_col().find_one({"username": username}):
        return jsonify({"success": False, "error": "Username already taken"}), 409

    if email and users_col().find_one({"email": email}):
        return jsonify({"success": False, "error": "Email already registered"}), 409

    # Create user
    user_doc = create_user(
        username=username,
        password_hash=hash_password(password),
        role="guest",
        full_name=full_name,
        email=email,
        phone=phone,
    )
    result = users_col().insert_one(user_doc)
    user_id = result.inserted_id

    # Create guest profile
    profile_doc = create_guest_profile(
        user_id=user_id,
        boarding_pass_no=data.get("boarding_pass_no"),
        airline=data.get("airline"),
        flight_number=data.get("flight_number"),
        departure_time=data.get("departure_time"),
    )
    profile_result = guest_profiles_col().insert_one(profile_doc)

    return jsonify({
        "success": True,
        "user_id": str(user_id),
        "guest_id": str(profile_result.inserted_id),
        "message": "Account created. Proceed to face registration.",
        "next_step": "/api/register_guest/face",
        "face_registered": False,
    }), 201


# =====================================================
# 3b. POST /api/register_guest/face
# =====================================================
@auth_bp.route("/api/register_guest/face", methods=["POST"])
@login_required
def register_face():
    """
    Trigger face registration using the AgentService's inline registration.
    The camera loop captures embeddings while producing an annotated MJPEG
    stream (with bounding boxes, progress bar, instructions) that the
    frontend can display via /api/agent/register_feed.
    """
    from datetime import datetime as dt
    from agent_service import get_agent_service
    from face_engine import FaceEngine

    data = request.get_json() or {}
    guest_id = data.get("guest_id")
    name = data.get("name", "").strip()

    if not guest_id or not name:
        return jsonify({"success": False, "error": "guest_id and name required"}), 400

    svc = get_agent_service()
    max_samples = 50
    timeout = 30  # seconds

    if svc.is_running():
        # ── Use agent's inline registration (camera loop handles capture) ──
        started = svc.start_face_registration(name, max_samples=max_samples)
        if not started:
            return jsonify({"success": False, "error": "Another registration is already in progress."}), 409

        result = svc.wait_face_registration(timeout=timeout)

        if not result["success"]:
            return jsonify({"success": False, "error": result["error"]}), 400

        embeddings = result["embeddings"]
    else:
        # ── Fallback: open camera directly (same as register_face.py) ──
        import cv2
        import time

        engine = FaceEngine()
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            return jsonify({"success": False, "error": "Cannot open camera"}), 500

        embeddings = []
        frame_count = 0
        capture_every = 2
        start_time = time.time()

        while len(embeddings) < max_samples:
            if time.time() - start_time > timeout:
                break
            ret, frame = cap.read()
            if not ret:
                continue
            frame_count += 1
            if frame_count % capture_every != 0:
                continue
            faces = engine.detect(frame)
            for i in range(len(faces)):
                try:
                    aug = engine.get_augmented_embeddings(frame, faces[i])
                    for emb in aug:
                        if len(embeddings) < max_samples:
                            embeddings.append(np.asarray(emb, dtype=np.float32).flatten())
                except Exception:
                    pass
            if frame_count > 500:
                break
        cap.release()

    if not embeddings:
        return jsonify({"success": False, "error": "No face detected. Try again."}), 400

    # Save embeddings using the engine
    engine = FaceEngine()
    saved = engine.save_embeddings(name, embeddings)

    # Reload the running agent's face database so the new face is
    # immediately recognized without needing a restart.
    svc.reload_face_database()

    # Update guest profile
    from bson import ObjectId as OID
    guest_profiles_col().update_one(
        {"_id": OID(guest_id)},
        {"$set": {
            "face_registered": True,
            "face_registered_at": dt.utcnow(),
        }}
    )

    return jsonify({
        "success": True,
        "embeddings_collected": saved,
        "message": "Face registered successfully",
    })


# =====================================================
# 3c–e. Agent-based face registration (zero camera conflict)
#   Uses the backend's already-open camera via AgentService.
#   Frontend shows MJPEG feed from /api/agent/register_feed
#   and polls /api/register_guest/face_progress for updates.
# =====================================================

@auth_bp.route("/api/register_guest/face_start", methods=["POST"])
@login_required
def face_start():
    """Start agent-based face registration (non-blocking).
    The agent's camera loop captures embeddings in background."""
    from agent_service import get_agent_service

    data = request.get_json() or {}
    guest_id = data.get("guest_id")
    name = data.get("name", "").strip()

    if not guest_id or not name:
        return jsonify({"success": False, "error": "guest_id and name required"}), 400

    svc = get_agent_service()
    if not svc.is_running():
        return jsonify({"success": False, "error": "Agent is not running. Start the backend agent first."}), 503

    started = svc.start_face_registration(name, max_samples=50)
    if not started:
        return jsonify({"success": False, "error": "Another registration is already in progress."}), 409

    # Store guest_id so face_finish knows which profile to update
    _face_reg_sessions[guest_id] = {"name": name, "agent_based": True}

    return jsonify({"success": True, "target": 50})


@auth_bp.route("/api/register_guest/face_progress", methods=["GET"])
@login_required
def face_progress():
    """Poll registration progress from AgentService."""
    from agent_service import get_agent_service

    svc = get_agent_service()
    prog = svc.get_registration_progress()

    return jsonify({
        "success": True,
        "active": prog["active"],
        "collected": prog["collected"],
        "target": prog["target"],
        "percent": prog["percent"],
        "done": prog["collected"] >= prog["target"],
    })


@auth_bp.route("/api/register_guest/face_finish", methods=["POST"])
@login_required
def face_finish():
    """Finalize face registration: collect agent embeddings, save, update profile."""
    from datetime import datetime as dt
    from face_engine import FaceEngine
    from agent_service import get_agent_service
    from bson import ObjectId as OID

    data = request.get_json() or {}
    guest_id = data.get("guest_id")
    name = data.get("name", "").strip()

    session = _face_reg_sessions.pop(guest_id, None)
    if not session:
        return jsonify({"success": False, "error": "No registration session found"}), 400

    svc = get_agent_service()

    # Wait briefly for agent to finish (in case it's almost done)
    result = svc.wait_face_registration(timeout=5.0)

    embeddings = result.get("embeddings", [])
    if not embeddings:
        return jsonify({"success": False, "error": "No face detected. Try again."}), 400

    # Save embeddings
    engine = FaceEngine()
    saved = engine.save_embeddings(name or session["name"], embeddings)

    # Reload the running agent's face database
    svc.reload_face_database()

    # Update guest profile
    try:
        guest_profiles_col().update_one(
            {"_id": OID(guest_id)},
            {"$set": {"face_registered": True, "face_registered_at": dt.utcnow()}}
        )
    except Exception:
        pass  # non-critical

    return jsonify({
        "success": True,
        "embeddings_collected": saved,
        "message": "Face registered successfully",
    })
