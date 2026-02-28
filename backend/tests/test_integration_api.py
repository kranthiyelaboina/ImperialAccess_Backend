from __future__ import annotations

from datetime import datetime
import uuid

from db import agent_events_col


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_guest_admin_flow_and_contracts(client):
    tag = f"itx_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    username = f"{tag}_guest"
    password = "Pass123!"
    email = f"{tag}@example.com"
    full_name = f"{tag}_name"

    # 1) Guest register -> login
    register_res = client.post(
        "/api/register_guest",
        json={
            "username": username,
            "password": password,
            "full_name": full_name,
            "email": email,
            "phone": "1234567890",
        },
    )
    assert register_res.status_code == 201
    register_body = register_res.get_json()
    assert register_body["success"] is True
    guest_id = register_body["guest_id"]

    login_guest_res = client.post(
        "/api/login_guest",
        json={"username": username, "password": password},
    )
    assert login_guest_res.status_code == 200
    login_guest_body = login_guest_res.get_json()
    guest_token = login_guest_body["token"]
    guest_headers = _auth_header(guest_token)

    # 2) Guest payment journey
    lounge_res = client.post(
        "/api/register_lounge",
        headers=guest_headers,
        json={"guest_id": guest_id, "lounge_name": "Skyview Premium Lounge"},
    )
    assert lounge_res.status_code == 200
    lounge_body = lounge_res.get_json()
    registration_id = lounge_body["registration_id"]

    credit_res = client.post(
        "/api/check_credit",
        headers=guest_headers,
        json={
            "guest_id": guest_id,
            "card_last_four": "4242",
            "card_type": "visa",
            "card_network": "visa_infinite",
        },
    )
    assert credit_res.status_code == 200
    assert credit_res.get_json()["success"] is True

    payment_res = client.post(
        "/api/payment_confirmation",
        headers=guest_headers,
        json={
            "registration_id": registration_id,
            "guest_id": guest_id,
            "amount": 45,
            "card_last_four": "4242",
            "card_type": "credit",
        },
    )
    assert payment_res.status_code == 200
    assert payment_res.get_json()["success"] is True

    guest_dash_res = client.get("/api/guest_dashboard", headers=guest_headers)
    assert guest_dash_res.status_code == 200
    guest_dash = guest_dash_res.get_json()
    assert guest_dash["success"] is True
    assert isinstance(guest_dash["lounge_access"], (dict, type(None)))

    # 3) Admin login + key admin endpoints
    admin_login_res = client.post(
        "/api/login_admin",
        json={"username": "admin", "password": "admin123"},
    )
    assert admin_login_res.status_code == 200
    admin_token = admin_login_res.get_json()["token"]
    admin_headers = _auth_header(admin_token)

    admin_dash_res = client.get("/api/admin_dashboard", headers=admin_headers)
    assert admin_dash_res.status_code == 200
    assert admin_dash_res.get_json()["success"] is True

    analytics_res = client.get("/api/analytics", headers=admin_headers)
    assert analytics_res.status_code == 200
    assert analytics_res.get_json()["success"] is True

    guests_res = client.get("/api/guests", headers=admin_headers)
    assert guests_res.status_code == 200
    assert guests_res.get_json()["success"] is True

    # 4) Issue and redeem dining token
    issue_res = client.post(
        "/api/dining_token/issue",
        headers=admin_headers,
        json={"guest_id": guest_id},
    )
    assert issue_res.status_code == 200
    assert issue_res.get_json()["success"] is True

    guest_dash_after_issue = client.get("/api/guest_dashboard", headers=guest_headers).get_json()
    active_token = next(
        (tok["token_code"] for tok in guest_dash_after_issue["dining_tokens"]["tokens"] if not tok["redeemed"]),
        None,
    )
    assert active_token is not None

    redeem_res = client.post(
        "/api/dining_token/redeem",
        headers=guest_headers,
        json={"token_code": active_token},
    )
    assert redeem_res.status_code == 200
    assert redeem_res.get_json()["success"] is True

    # 5) Mode API accepts case-insensitive values (regression fix)
    for mode in ("gate", "GATE"):
        mode_res = client.post("/api/change_modes", headers=admin_headers, json={"mode": mode})
        assert mode_res.status_code == 200
        mode_body = mode_res.get_json()
        assert mode_body["success"] is True
        assert mode_body["current_mode"] == "gate"

    # 6) Event payload fallback: legacy guest_name -> face_name
    legacy_name = f"{tag}_legacy_face"
    legacy_insert = agent_events_col().insert_one(
        {
            "event_type": "access_granted",
            "guest_name": legacy_name,
            "confidence": 99,
            "mode": "gate",
            "details": {"source": "integration_test"},
            "timestamp": datetime.utcnow(),
        }
    )
    legacy_id = str(legacy_insert.inserted_id)

    events_res = client.get("/api/agent/events?limit=50", headers=admin_headers)
    assert events_res.status_code == 200
    events = events_res.get_json()["events"]
    matching_events = [e for e in events if e["id"] == legacy_id]
    assert matching_events
    assert matching_events[0]["face_name"] == legacy_name

    admin_dash_after_legacy = client.get("/api/admin_dashboard", headers=admin_headers).get_json()
    matching_recent = [e for e in admin_dash_after_legacy["recent_events"] if e["id"] == legacy_id]
    assert matching_recent
    assert matching_recent[0]["face_name"] == legacy_name
