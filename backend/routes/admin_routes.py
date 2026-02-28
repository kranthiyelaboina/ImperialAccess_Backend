"""
admin_routes.py — Admin endpoints

GET  /api/admin_dashboard
POST /api/change_modes
GET  /api/analytics
"""

from flask import Blueprint, request, jsonify
from bson import ObjectId
from datetime import datetime, timedelta

from auth import login_required, admin_required
from db import (
    agent_events_col, attendance_logs_col,
    lounge_registrations_col, guest_profiles_col, users_col
)
from models import serialize_doc, serialize_list
from agent_service import get_agent_service

admin_bp = Blueprint("admin", __name__)


# =====================================================
# 8. GET /api/admin_dashboard
# =====================================================
@admin_bp.route("/api/admin_dashboard", methods=["GET"])
@admin_required
def admin_dashboard():
    svc = get_agent_service()
    state = svc.get_state()

    # Recent events (last 20)
    recent_events = list(agent_events_col().find(
        sort=[("timestamp", -1)],
        limit=20,
    ))
    events_out = []
    for e in recent_events:
        events_out.append({
            "id": str(e["_id"]),
            "event_type": e.get("event_type"),
            "face_name": e.get("face_name") or e.get("guest_name") or "Unknown",
            "confidence": e.get("confidence"),
            "timestamp": e["timestamp"].isoformat() if e.get("timestamp") else None,
            "mode": e.get("mode"),
        })

    # Today's stats
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_entries = attendance_logs_col().count_documents({
        "event_type": "entry",
        "timestamp": {"$gte": today_start},
    })
    today_exits = attendance_logs_col().count_documents({
        "event_type": "exit",
        "timestamp": {"$gte": today_start},
    })
    today_registrations = lounge_registrations_col().count_documents({
        "registered_at": {"$gte": today_start},
    })
    total_guests = guest_profiles_col().count_documents({})

    return jsonify({
        "success": True,
        "agent_state": {
            "running": state.get("running", False),
            "mode": state.get("mode"),
            "occupancy": state.get("occupancy", 0),
            "faces_detected": state.get("faces_detected", 0),
            "recognized_faces": state.get("recognized_names", []),
        },
        "recent_events": events_out,
        "stats": {
            "today_entries": today_entries,
            "today_exits": today_exits,
            "current_occupancy": state.get("occupancy", 0),
            "today_registrations": today_registrations,
            "total_guests": total_guests,
        },
    })


# =====================================================
# 9. POST /api/change_modes
# =====================================================
@admin_bp.route("/api/change_modes", methods=["POST"])
@admin_required
def change_modes():
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "No JSON body"}), 400

    mode = str(data.get("mode", "")).strip().lower()
    valid_modes = ["gate", "reception", "entry", "exit"]
    if mode not in valid_modes:
        return jsonify({
            "success": False,
            "error": f"Invalid mode. Choose from: {valid_modes}",
        }), 400

    svc = get_agent_service()
    svc.set_mode(mode)
    state = svc.get_state()
    current_mode = state.get("mode") or mode

    return jsonify({
        "success": True,
        "current_mode": current_mode,
        "occupancy": state.get("occupancy", 0),
        "message": f"Agent mode changed to {str(current_mode).upper()}",
    })


# =====================================================
# 10. GET /api/analytics
# =====================================================
@admin_bp.route("/api/analytics", methods=["GET"])
@admin_required
def analytics():
    date_str = request.args.get("date")        # YYYY-MM-DD
    range_str = request.args.get("range")       # today / week / month
    mode = request.args.get("mode")

    # Build date range
    now = datetime.utcnow()
    if date_str:
        try:
            day = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            return jsonify({"success": False, "error": "Invalid date format. Use YYYY-MM-DD"}), 400
        start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
    elif range_str == "week":
        start = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = now
    elif range_str == "month":
        start = (now - timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = now
    else:
        # Default: today
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now

    # Base filter
    match_filter = {"timestamp": {"$gte": start, "$lte": end}}
    if mode:
        match_filter["mode"] = mode.upper()

    # Attendance logs in range
    logs = list(attendance_logs_col().find(match_filter, sort=[("timestamp", 1)]))

    # Aggregate hourly breakdown
    hourly = {}
    for log in logs:
        ts = log.get("timestamp")
        if ts:
            hour = ts.hour
            hourly.setdefault(hour, {"entries": 0, "exits": 0})
            if log.get("event_type") == "entry":
                hourly[hour]["entries"] += 1
            elif log.get("event_type") == "exit":
                hourly[hour]["exits"] += 1

    hourly_breakdown = [
        {"hour": h, "entries": v["entries"], "exits": v["exits"]}
        for h, v in sorted(hourly.items())
    ]

    # Unique guests
    unique_guests = set()
    for log in logs:
        name = log.get("guest_name")
        if name:
            unique_guests.add(name)

    # Peak occupancy (simple estimation: max running total)
    running = 0
    peak = 0
    for log in logs:
        if log.get("event_type") == "entry":
            running += 1
        elif log.get("event_type") == "exit":
            running = max(0, running - 1)
        peak = max(peak, running)

    total_entries = sum(h["entries"] for h in hourly_breakdown)
    total_exits = sum(h["exits"] for h in hourly_breakdown)

    return jsonify({
        "success": True,
        "period": {
            "start": start.isoformat(),
            "end": end.isoformat(),
        },
        "summary": {
            "total_entries": total_entries,
            "total_exits": total_exits,
            "unique_guests": len(unique_guests),
            "peak_occupancy": peak,
        },
        "hourly_breakdown": hourly_breakdown,
    })
