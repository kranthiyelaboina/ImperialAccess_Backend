"""
guest_routes.py — Guest endpoints

POST /api/register_lounge
POST /api/check_credit
POST /api/payment_confirmation
GET  /api/guest_dashboard
"""

from flask import Blueprint, request, jsonify
from bson import ObjectId
from datetime import datetime, timedelta

from auth import login_required, guest_required
from db import (
    guest_profiles_col, lounge_registrations_col, payments_col,
    dining_tokens_col, attendance_logs_col, users_col
)
from models import (
    create_lounge_registration, create_payment, create_dining_token,
    serialize_doc, serialize_list
)
from config import LOUNGE_PRICE, LOUNGE_CURRENCY, ELIGIBLE_CARD_NETWORKS, MAX_COMPLIMENTARY_VISITS

guest_bp = Blueprint("guest", __name__)


# =====================================================
# 4. POST /api/register_lounge
# =====================================================
@guest_bp.route("/api/register_lounge", methods=["POST"])
@login_required
def register_lounge():
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "No JSON body"}), 400

    guest_id = data.get("guest_id")
    lounge_name = data.get("lounge_name", "Premium International Lounge")

    if not guest_id:
        return jsonify({"success": False, "error": "guest_id required"}), 400

    # Check guest exists
    profile = guest_profiles_col().find_one({"_id": ObjectId(guest_id)})
    if not profile:
        return jsonify({"success": False, "error": "Guest profile not found"}), 404

    # Check if already registered today
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    existing = lounge_registrations_col().find_one({
        "guest_id": ObjectId(guest_id),
        "registered_at": {"$gte": today_start},
        "access_granted": True,
    })
    if existing:
        return jsonify({
            "success": True,
            "registration_id": str(existing["_id"]),
            "lounge_name": existing["lounge_name"],
            "payment_required": False,
            "access_granted": True,
            "message": "Already registered for today",
        })

    # Create registration (pending payment)
    reg_doc = create_lounge_registration(guest_id, lounge_name)
    result = lounge_registrations_col().insert_one(reg_doc)

    return jsonify({
        "success": True,
        "registration_id": str(result.inserted_id),
        "lounge_name": lounge_name,
        "amount_due": LOUNGE_PRICE,
        "currency": LOUNGE_CURRENCY,
        "payment_required": True,
        "redirect_to": f"/payment?registration_id={result.inserted_id}",
    })


# =====================================================
# 5. POST /api/check_credit
# =====================================================
@guest_bp.route("/api/check_credit", methods=["POST"])
@login_required
def check_credit():
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "No JSON body"}), 400

    guest_id = data.get("guest_id")
    card_network = data.get("card_network", "").lower()
    card_type = data.get("card_type", "")
    card_last_four = data.get("card_last_four", "")

    if not guest_id:
        return jsonify({"success": False, "error": "guest_id required"}), 400

    # Check eligibility (simulated)
    eligible = card_network in ELIGIBLE_CARD_NETWORKS

    if eligible:
        # Count complimentary visits used
        visits_used = lounge_registrations_col().count_documents({
            "guest_id": ObjectId(guest_id),
            "payment_status": "complimentary",
        })
        remaining = max(0, MAX_COMPLIMENTARY_VISITS - visits_used)

        if remaining > 0:
            return jsonify({
                "success": True,
                "eligible": True,
                "benefit_type": "complimentary_access",
                "max_visits": MAX_COMPLIMENTARY_VISITS,
                "visits_used": visits_used,
                "visits_remaining": remaining,
                "message": "Card qualifies for complimentary lounge access",
            })
        else:
            return jsonify({
                "success": True,
                "eligible": False,
                "message": f"Complimentary visits exhausted ({MAX_COMPLIMENTARY_VISITS} used). Standard rate: ${LOUNGE_PRICE:.2f}",
                "amount_due": LOUNGE_PRICE,
            })

    return jsonify({
        "success": True,
        "eligible": False,
        "message": f"Card does not qualify. Standard rate: ${LOUNGE_PRICE:.2f}",
        "amount_due": LOUNGE_PRICE,
    })


# =====================================================
# 6. POST /api/payment_confirmation
# =====================================================
@guest_bp.route("/api/payment_confirmation", methods=["POST"])
@login_required
def payment_confirmation():
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "No JSON body"}), 400

    registration_id = data.get("registration_id")
    guest_id = data.get("guest_id")
    amount = data.get("amount", LOUNGE_PRICE)
    card_last_four = data.get("card_last_four")
    card_type = data.get("card_type")
    transaction_ref = data.get("transaction_ref")

    if not registration_id or not guest_id:
        return jsonify({"success": False, "error": "registration_id and guest_id required"}), 400

    # Verify registration exists
    reg = lounge_registrations_col().find_one({"_id": ObjectId(registration_id)})
    if not reg:
        return jsonify({"success": False, "error": "Registration not found"}), 404

    if reg.get("access_granted"):
        return jsonify({
            "success": True,
            "message": "Access already granted",
            "access_granted": True,
        })

    # Create payment record
    pay_doc = create_payment(
        guest_id=guest_id,
        amount=amount,
        card_last_four=card_last_four,
        card_type=card_type,
        transaction_ref=transaction_ref,
    )
    pay_doc["status"] = "success"  # simulated success
    pay_result = payments_col().insert_one(pay_doc)

    # Update registration
    valid_until = datetime.utcnow().replace(hour=23, minute=59, second=59)
    lounge_registrations_col().update_one(
        {"_id": ObjectId(registration_id)},
        {"$set": {
            "payment_status": "paid",
            "payment_id": pay_result.inserted_id,
            "access_granted": True,
            "valid_until": valid_until,
        }}
    )

    # Issue 1 dining token
    token_doc = create_dining_token(guest_id, registration_id)
    dining_tokens_col().insert_one(token_doc)

    return jsonify({
        "success": True,
        "payment_id": str(pay_result.inserted_id),
        "status": "success",
        "access_granted": True,
        "valid_until": valid_until.isoformat(),
        "dining_tokens_issued": 1,
        "message": "Payment confirmed. Lounge access granted.",
    })


# =====================================================
# 7. GET /api/guest_dashboard
# =====================================================
@guest_bp.route("/api/guest_dashboard", methods=["GET"])
@login_required
def guest_dashboard():
    user_id = request.user_id

    # Get user
    user = users_col().find_one({"_id": ObjectId(user_id)})
    if not user:
        return jsonify({"success": False, "error": "User not found"}), 404

    # Get profile
    profile = guest_profiles_col().find_one({"user_id": ObjectId(user_id)})
    if not profile:
        return jsonify({"success": False, "error": "Guest profile not found"}), 404

    guest_id = profile["_id"]

    # Guest info
    guest_info = {
        "full_name": user["full_name"],
        "email": user.get("email"),
        "membership_type": profile.get("membership_type", "standard"),
        "face_registered": profile.get("face_registered", False),
        "boarding_pass_no": profile.get("boarding_pass_no"),
        "airline": profile.get("airline"),
        "flight_number": profile.get("flight_number"),
        "departure_time": profile.get("departure_time"),
    }

    # Lounge access (latest)
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    lounge_reg = lounge_registrations_col().find_one(
        {"guest_id": guest_id, "registered_at": {"$gte": today_start}},
        sort=[("registered_at", -1)]
    )

    lounge_access = None
    if lounge_reg:
        lounge_access = {
            "active": lounge_reg.get("access_granted", False),
            "lounge_name": lounge_reg.get("lounge_name"),
            "registered_at": lounge_reg["registered_at"].isoformat() if lounge_reg.get("registered_at") else None,
            "valid_until": lounge_reg["valid_until"].isoformat() if lounge_reg.get("valid_until") else None,
            "payment_status": lounge_reg.get("payment_status"),
        }

    # Dining tokens
    tokens = list(dining_tokens_col().find({"guest_id": guest_id}))
    total_tokens = len(tokens)
    redeemed = sum(1 for t in tokens if t.get("redeemed"))
    token_list = [{
        "token_code": t["token_code"],
        "redeemed": t.get("redeemed", False),
        "redeemed_at": t["redeemed_at"].isoformat() if t.get("redeemed_at") else None,
    } for t in tokens]

    # Attendance history
    guest_name = user["full_name"]
    att_logs = list(attendance_logs_col().find(
        {"guest_name": guest_name},
        sort=[("timestamp", -1)],
        limit=20,
    ))
    attendance_history = [{
        "event": log.get("event_type"),
        "time": log["timestamp"].isoformat() if log.get("timestamp") else None,
        "mode": log.get("mode"),
        "duration_minutes": log.get("duration_minutes"),
    } for log in att_logs]

    return jsonify({
        "success": True,
        "guest": guest_info,
        "lounge_access": lounge_access,
        "dining_tokens": {
            "total": total_tokens,
            "redeemed": redeemed,
            "remaining": total_tokens - redeemed,
            "tokens": token_list,
        },
        "attendance_history": attendance_history,
    })
