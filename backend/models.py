"""
models.py — MongoDB document helpers

These are NOT ORM models. They are factory functions that produce
well-structured dicts for insertion into MongoDB collections.
Each function validates and returns a document matching the schema.
"""
from datetime import datetime
from bson import ObjectId
import uuid


def create_user(username, password_hash, role, full_name, email=None, phone=None):
    """Create a user document."""
    return {
        "username": username,
        "password_hash": password_hash,
        "role": role,  # "admin" or "guest"
        "full_name": full_name,
        "email": email,
        "phone": phone,
        "created_at": datetime.utcnow(),
    }


def create_guest_profile(user_id, membership_type="standard",
                          boarding_pass_no=None, airline=None,
                          flight_number=None, departure_time=None):
    """Create a guest profile document."""
    return {
        "user_id": ObjectId(user_id) if isinstance(user_id, str) else user_id,
        "face_registered": False,
        "face_registered_at": None,
        "membership_type": membership_type,
        "boarding_pass_no": boarding_pass_no,
        "airline": airline,
        "flight_number": flight_number,
        "departure_time": departure_time,
        "created_at": datetime.utcnow(),
    }


def create_lounge_registration(guest_id, lounge_name, payment_status="pending"):
    """Create a lounge registration document."""
    return {
        "guest_id": ObjectId(guest_id) if isinstance(guest_id, str) else guest_id,
        "lounge_name": lounge_name,
        "registered_at": datetime.utcnow(),
        "payment_status": payment_status,  # "pending", "paid", "complimentary"
        "payment_id": None,
        "access_granted": payment_status == "complimentary",
        "valid_until": None,
    }


def create_payment(guest_id, amount, currency="USD",
                    card_last_four=None, card_type=None,
                    transaction_ref=None):
    """Create a payment document."""
    return {
        "guest_id": ObjectId(guest_id) if isinstance(guest_id, str) else guest_id,
        "amount": amount,
        "currency": currency,
        "card_last_four": card_last_four,
        "card_type": card_type,
        "status": "pending",
        "transaction_ref": transaction_ref or f"TXN-{uuid.uuid4().hex[:12].upper()}",
        "created_at": datetime.utcnow(),
    }


def create_dining_token(guest_id, registration_id=None):
    """Create a dining token document."""
    return {
        "guest_id": ObjectId(guest_id) if isinstance(guest_id, str) else guest_id,
        "registration_id": ObjectId(registration_id) if registration_id else None,
        "token_code": f"DT-{uuid.uuid4().hex[:8].upper()}",
        "issued_at": datetime.utcnow(),
        "redeemed": False,
        "redeemed_at": None,
    }


def create_attendance_log(guest_name, event_type, mode, guest_id=None,
                           duration_minutes=None, detected_by="agent_auto"):
    """Create an attendance log document."""
    return {
        "guest_id": ObjectId(guest_id) if guest_id else None,
        "guest_name": guest_name,
        "event_type": event_type,  # "entry" or "exit"
        "mode": mode,
        "timestamp": datetime.utcnow(),
        "duration_minutes": duration_minutes,
        "detected_by": detected_by,
    }


def create_agent_event(event_type, face_name=None, guest_name=None, confidence=0,
                        mode=None, details=None):
    """Create an agent event document."""
    resolved_name = face_name or guest_name
    return {
        "event_type": event_type,
        # Keep both fields to support legacy readers and new frontend contracts.
        "face_name": resolved_name,
        "guest_name": resolved_name,
        "confidence": confidence,
        "mode": mode,
        "details": details or {},
        "timestamp": datetime.utcnow(),
    }


# =====================================================
# SERIALIZATION HELPERS
# =====================================================
def serialize_doc(doc):
    """Convert MongoDB document to JSON-serializable dict."""
    if doc is None:
        return None
    d = dict(doc)
    if "_id" in d:
        d["_id"] = str(d["_id"])
    for key, val in d.items():
        if isinstance(val, ObjectId):
            d[key] = str(val)
        elif isinstance(val, datetime):
            d[key] = val.isoformat()
    return d


def serialize_list(docs):
    """Convert a list of MongoDB documents."""
    return [serialize_doc(d) for d in docs]
