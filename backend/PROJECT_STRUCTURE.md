# Backend Project Structure

```
backend/
│
├── API_DESIGN.md              ← API endpoints, request/response schemas
├── FRONTEND_PAGES.md          ← All frontend pages, routes, components
├── PROJECT_STRUCTURE.md       ← This file
│
├── app.py                     ← Flask app factory, CORS, config
├── config.py                  ← App configuration (DB path, JWT secret, etc.)
├── models.py                  ← SQLAlchemy / SQLite models (all 7 tables)
├── auth.py                    ← JWT auth helpers, decorators (@admin_required, @guest_required)
│
├── routes/
│   ├── __init__.py
│   ├── auth_routes.py         ← /login_admin, /login_guest, /register_guest
│   ├── guest_routes.py        ← /guest_dashboard, /register_lounge, /check_credit, /payment
│   ├── admin_routes.py        ← /admin_dashboard, /change_modes, /analytics
│   ├── agent_routes.py        ← /agent/state, /agent/events, /agent/video_feed
│   └── dining_routes.py       ← /dining_token/issue, /dining_token/redeem
│
├── services/
│   ├── __init__.py
│   ├── agent_service.py       ← Wraps LoungeAgent for Flask (singleton, thread-safe)
│   ├── payment_service.py     ← Payment logic (simulate or integrate Stripe)
│   ├── credit_service.py      ← Credit card eligibility rules
│   ├── analytics_service.py   ← Attendance aggregation, stats computation
│   └── token_service.py       ← Dining token generation, redemption
│
├── database/
│   ├── lounge.db              ← SQLite database file (created on first run)
│   └── init_db.py             ← Database initialization script
│
└── requirements.txt           ← Flask, PyJWT, opencv-python, etc.
```

---

## How Backend Connects to Agent

```
                    ┌──────────────────────┐
                    │    Flask Backend      │
                    │                       │
                    │  agent_service.py     │
                    │    │                  │
                    │    ▼                  │
                    │  LoungeAgent          │◄── from ../lounge_agent.py
                    │    │                  │
                    │    ▼                  │
                    │  FaceEngine           │◄── from ../face_engine.py
                    │    │                  │
                    │    ▼                  │
                    │  data/*.pkl           │◄── face embeddings
                    │  data/*.onnx          │◄── YuNet + SFace models
                    └──────────────────────┘
```

The Flask backend imports `LoungeAgent` and `FaceEngine` from the parent directory.
The agent runs in a background thread, processing camera frames continuously.
Flask endpoints query the agent's state and emit commands (mode changes, token issuance).

---

## Key Design Decisions

1. **Agent as Singleton**: One `LoungeAgent` instance shared across all Flask requests via `agent_service.py`
2. **Background Thread**: Agent camera loop runs in a daemon thread; Flask serves API requests on main thread
3. **Event Streaming**: Admin dashboard polls `/api/agent/state` every 2-3 seconds (or use Server-Sent Events)
4. **Video Feed**: MJPEG stream served at `/api/agent/video_feed` — admin pages embed as `<img src="...">`
5. **SQLite**: Lightweight, no setup required. Upgrade to PostgreSQL later if needed.
6. **JWT Auth**: Stateless tokens, no server-side sessions. Short expiry (1 hour) with refresh.

---

## Dependencies

```
Flask==3.1.0
Flask-CORS==5.0.0
PyJWT==2.10.0
bcrypt==4.3.0
opencv-python==4.13.0.92
numpy==2.4.2
```

---
