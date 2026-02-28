# Airport Lounge Access — Backend API Design

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        FRONTEND                              │
│  Landing │ Login │ Registration │ Dashboard │ Admin Panel    │
└─────────────────────┬───────────────────────────────────────┘
                      │ REST API (JSON)
┌─────────────────────▼───────────────────────────────────────┐
│                    FLASK BACKEND                             │
│  Auth │ Guest │ Payment │ Agent │ Analytics                  │
└─────────────────────┬───────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────┐
│              LOUNGE AGENT (face_engine + lounge_agent)        │
│  FaceEngine │ LoungeAgent │ Tracker │ Events                 │
└─────────────────────┬───────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────┐
│                    DATA LAYER                                 │
│  SQLite (users, payments, tokens) │ Pickle (face embeddings) │
│  CSV (attendance logs)                                       │
└──────────────────────────────────────────────────────────────┘
```

---

## Database Schemas

### 1. `users` table
| Column         | Type     | Constraints                    |
|----------------|----------|--------------------------------|
| id             | INTEGER  | PRIMARY KEY AUTOINCREMENT      |
| username       | TEXT     | UNIQUE, NOT NULL               |
| password_hash  | TEXT     | NOT NULL                       |
| role           | TEXT     | NOT NULL — `admin` or `guest`  |
| full_name      | TEXT     | NOT NULL                       |
| email          | TEXT     | UNIQUE                         |
| phone          | TEXT     |                                |
| created_at     | DATETIME | DEFAULT CURRENT_TIMESTAMP      |

### 2. `guest_profiles` table
| Column              | Type     | Constraints                    |
|---------------------|----------|--------------------------------|
| id                  | INTEGER  | PRIMARY KEY AUTOINCREMENT      |
| user_id             | INTEGER  | FK → users.id, UNIQUE          |
| face_registered     | BOOLEAN  | DEFAULT FALSE                  |
| face_registered_at  | DATETIME |                                |
| membership_type     | TEXT     | `standard` / `premium` / `vip` |
| boarding_pass_no    | TEXT     |                                |
| airline             | TEXT     |                                |
| flight_number       | TEXT     |                                |
| departure_time      | DATETIME |                                |

### 3. `lounge_registrations` table
| Column           | Type     | Constraints                    |
|------------------|----------|--------------------------------|
| id               | INTEGER  | PRIMARY KEY AUTOINCREMENT      |
| guest_id         | INTEGER  | FK → guest_profiles.id         |
| lounge_name      | TEXT     | NOT NULL                       |
| registered_at    | DATETIME | DEFAULT CURRENT_TIMESTAMP      |
| payment_status   | TEXT     | `pending` / `paid` / `complimentary` |
| payment_id       | INTEGER  | FK → payments.id (nullable)    |
| access_granted   | BOOLEAN  | DEFAULT FALSE                  |
| valid_until       | DATETIME |                                |

### 4. `payments` table
| Column           | Type     | Constraints                    |
|------------------|----------|--------------------------------|
| id               | INTEGER  | PRIMARY KEY AUTOINCREMENT      |
| guest_id         | INTEGER  | FK → guest_profiles.id         |
| amount           | REAL     | NOT NULL                       |
| currency         | TEXT     | DEFAULT 'USD'                  |
| card_last_four   | TEXT     |                                |
| card_type        | TEXT     | `visa` / `mastercard` / `amex` |
| status           | TEXT     | `pending` / `success` / `failed` |
| transaction_ref  | TEXT     | UNIQUE                         |
| created_at       | DATETIME | DEFAULT CURRENT_TIMESTAMP      |

### 5. `dining_tokens` table
| Column           | Type     | Constraints                    |
|------------------|----------|--------------------------------|
| id               | INTEGER  | PRIMARY KEY AUTOINCREMENT      |
| guest_id         | INTEGER  | FK → guest_profiles.id         |
| registration_id  | INTEGER  | FK → lounge_registrations.id   |
| token_code       | TEXT     | UNIQUE, NOT NULL               |
| issued_at        | DATETIME | DEFAULT CURRENT_TIMESTAMP      |
| redeemed         | BOOLEAN  | DEFAULT FALSE                  |
| redeemed_at      | DATETIME |                                |

### 6. `attendance_logs` table
| Column           | Type     | Constraints                    |
|------------------|----------|--------------------------------|
| id               | INTEGER  | PRIMARY KEY AUTOINCREMENT      |
| guest_id         | INTEGER  | FK → guest_profiles.id         |
| guest_name       | TEXT     | NOT NULL                       |
| event_type       | TEXT     | `entry` / `exit`               |
| mode             | TEXT     | `gate` / `reception` / `entry` / `exit` |
| timestamp        | DATETIME | DEFAULT CURRENT_TIMESTAMP      |
| duration_minutes | REAL     | nullable (filled on exit)      |
| detected_by      | TEXT     | `agent_auto` / `manual`        |

### 7. `agent_events` table
| Column           | Type     | Constraints                    |
|------------------|----------|--------------------------------|
| id               | INTEGER  | PRIMARY KEY AUTOINCREMENT      |
| event_type       | TEXT     | NOT NULL (from EventType enum) |
| guest_name       | TEXT     |                                |
| confidence       | INTEGER  |                                |
| mode             | TEXT     |                                |
| details_json     | TEXT     | JSON string                    |
| timestamp        | DATETIME | DEFAULT CURRENT_TIMESTAMP      |

---

## API Endpoints

---

### 1. `POST /api/login_admin`

**Purpose:** Authenticate admin users

**Request Body:**
```json
{
  "username": "admin01",
  "password": "hashed_or_plain"
}
```

**Response — 200:**
```json
{
  "success": true,
  "token": "jwt_token_here",
  "user": {
    "id": 1,
    "username": "admin01",
    "full_name": "Admin User",
    "role": "admin"
  }
}
```

**Response — 401:**
```json
{
  "success": false,
  "error": "Invalid credentials"
}
```

---

### 2. `POST /api/login_guest`

**Purpose:** Authenticate guest/passenger users

**Request Body:**
```json
{
  "username": "john_doe",
  "password": "password123"
}
```

**Response — 200:**
```json
{
  "success": true,
  "token": "jwt_token_here",
  "user": {
    "id": 5,
    "username": "john_doe",
    "full_name": "John Doe",
    "role": "guest",
    "face_registered": true,
    "membership_type": "premium"
  }
}
```

---

### 3. `POST /api/register_guest`

**Purpose:** Register a new guest account + trigger face data collection

**Request Body:**
```json
{
  "username": "jane_smith",
  "password": "securePass",
  "full_name": "Jane Smith",
  "email": "jane@email.com",
  "phone": "+1234567890",
  "boarding_pass_no": "BP-2026-1234",
  "airline": "Emirates",
  "flight_number": "EK501",
  "departure_time": "2026-02-27T18:30:00"
}
```

**Response — 201:**
```json
{
  "success": true,
  "user_id": 6,
  "guest_id": 6,
  "message": "Account created. Proceed to face registration.",
  "next_step": "/api/register_guest/face",
  "face_registered": false
}
```

#### 3b. `POST /api/register_guest/face`

**Purpose:** Start face registration camera session (calls face_engine internally)

**Request Body:**
```json
{
  "guest_id": 6,
  "name": "Jane Smith"
}
```

**Response — 200:**
```json
{
  "success": true,
  "embeddings_collected": 150,
  "message": "Face registered successfully"
}
```

---

### 4. `POST /api/register_lounge`

**Purpose:** Register guest for lounge access, redirect to payment

**Request Body:**
```json
{
  "guest_id": 6,
  "lounge_name": "Premium International Lounge",
  "membership_type": "premium"
}
```

**Response — 200:**
```json
{
  "success": true,
  "registration_id": 12,
  "lounge_name": "Premium International Lounge",
  "amount_due": 45.00,
  "currency": "USD",
  "payment_required": true,
  "redirect_to": "/payment?registration_id=12"
}
```

**Response — 200 (complimentary):**
```json
{
  "success": true,
  "registration_id": 13,
  "lounge_name": "Premium International Lounge",
  "payment_required": false,
  "access_granted": true,
  "message": "Complimentary access via credit card benefit"
}
```

---

### 5. `POST /api/check_credit`

**Purpose:** Verify if guest's credit card qualifies for complimentary lounge access

**Request Body:**
```json
{
  "guest_id": 6,
  "card_last_four": "4242",
  "card_type": "visa",
  "card_network": "visa_infinite"
}
```

**Response — 200:**
```json
{
  "success": true,
  "eligible": true,
  "benefit_type": "complimentary_access",
  "max_visits": 4,
  "visits_used": 1,
  "visits_remaining": 3,
  "message": "Card qualifies for complimentary lounge access"
}
```

**Response — 200 (not eligible):**
```json
{
  "success": true,
  "eligible": false,
  "message": "Card does not qualify. Standard rate: $45.00",
  "amount_due": 45.00
}
```

---

### 6. `POST /api/payment_confirmation`

**Purpose:** Confirm payment and finalize lounge registration

**Request Body:**
```json
{
  "registration_id": 12,
  "guest_id": 6,
  "amount": 45.00,
  "card_last_four": "4242",
  "card_type": "visa",
  "transaction_ref": "TXN-2026-0227-001"
}
```

**Response — 200:**
```json
{
  "success": true,
  "payment_id": 8,
  "status": "success",
  "access_granted": true,
  "valid_until": "2026-02-27T23:59:59",
  "dining_tokens_issued": 1,
  "message": "Payment confirmed. Lounge access granted."
}
```

---

### 7. `GET /api/guest_dashboard`

**Purpose:** Guest's personal dashboard

**Headers:** `Authorization: Bearer <jwt_token>`

**Response — 200:**
```json
{
  "success": true,
  "guest": {
    "full_name": "Jane Smith",
    "email": "jane@email.com",
    "membership_type": "premium",
    "face_registered": true,
    "boarding_pass_no": "BP-2026-1234",
    "airline": "Emirates",
    "flight_number": "EK501",
    "departure_time": "2026-02-27T18:30:00"
  },
  "lounge_access": {
    "active": true,
    "lounge_name": "Premium International Lounge",
    "registered_at": "2026-02-27T10:15:00",
    "valid_until": "2026-02-27T23:59:59",
    "payment_status": "paid"
  },
  "dining_tokens": {
    "total": 2,
    "redeemed": 1,
    "remaining": 1,
    "tokens": [
      {"token_code": "DT-001", "redeemed": true, "redeemed_at": "2026-02-27T12:30:00"},
      {"token_code": "DT-002", "redeemed": false, "redeemed_at": null}
    ]
  },
  "attendance_history": [
    {"event": "entry", "time": "2026-02-27T10:20:00", "mode": "gate"},
    {"event": "exit", "time": "2026-02-27T12:45:00", "duration_minutes": 145.0}
  ]
}
```

---

### 8. `GET /api/admin_dashboard`

**Purpose:** Admin dashboard — agent state, occupancy, live data

**Headers:** `Authorization: Bearer <jwt_token>` (admin only)

**Response — 200:**
```json
{
  "success": true,
  "agent": {
    "mode": "gate",
    "occupancy_count": 5,
    "occupancy": [
      {"name": "Jane Smith", "entered_at": "2026-02-27T10:20:00"},
      {"name": "John Doe", "entered_at": "2026-02-27T11:05:00"}
    ],
    "registered_members": ["Jane Smith", "John Doe", "Alice Brown"],
    "todays_attendance_count": 8
  },
  "recent_events": [
    {
      "event_type": "access_granted",
      "name": "Jane Smith",
      "confidence": 92,
      "timestamp": "2026-02-27T10:20:00"
    }
  ],
  "stats": {
    "total_guests_today": 8,
    "currently_inside": 5,
    "unknown_alerts": 2,
    "dining_tokens_issued": 12,
    "avg_stay_minutes": 87.5
  }
}
```

---

### 9. `POST /api/change_modes`

**Purpose:** Switch agent operating mode + get current guest tracking info

**Headers:** `Authorization: Bearer <jwt_token>` (admin only)

**Request Body:**
```json
{
  "mode": "entry"
}
```

**Allowed modes:** `gate`, `reception`, `entry`, `exit`

**Response — 200:**
```json
{
  "success": true,
  "previous_mode": "gate",
  "current_mode": "entry",
  "occupancy": {
    "inside": [
      {"name": "Jane Smith", "entered_at": "10:20:00"},
      {"name": "John Doe", "entered_at": "11:05:00"}
    ],
    "count": 2
  },
  "session_log": [
    {"name": "Jane Smith", "event": "entry", "time": "10:20:00"},
    {"name": "Alice Brown", "event": "entry", "time": "10:45:00"},
    {"name": "Alice Brown", "event": "exit", "time": "12:00:00", "duration_minutes": 75.0}
  ]
}
```

---

### 10. `GET /api/analytics`

**Purpose:** Attendance analytics and reporting

**Headers:** `Authorization: Bearer <jwt_token>` (admin only)

**Query Params:**
- `date` (optional) — `DD-MM-YYYY`, defaults to today
- `range` (optional) — `week`, `month`, `all`

**Response — 200:**
```json
{
  "success": true,
  "date": "27-02-2026",
  "attendance": {
    "total_today": 15,
    "entries": [
      {"name": "Jane Smith", "time": "10:20:00", "mode": "gate", "event": "entry"},
      {"name": "Jane Smith", "time": "12:45:00", "mode": "exit", "event": "exit", "duration_minutes": 145.0}
    ]
  },
  "analytics": {
    "peak_hour": "10:00-11:00",
    "avg_stay_minutes": 95.2,
    "busiest_day_this_week": "Monday",
    "total_this_week": 87,
    "unique_guests_this_week": 34,
    "unknown_alerts_today": 3,
    "repeat_visitors": 12,
    "dining_tokens_redeemed_today": 8
  },
  "hourly_breakdown": [
    {"hour": "08:00", "entries": 2, "exits": 0},
    {"hour": "09:00", "entries": 5, "exits": 1},
    {"hour": "10:00", "entries": 8, "exits": 3}
  ]
}
```

---

## Additional Utility Endpoints

### `GET /api/agent/state`
Returns raw agent state (for real-time polling from admin frontend).

### `GET /api/agent/events?since=<timestamp>`
Returns agent events since a timestamp (for live event feed via polling or SSE).

### `POST /api/dining_token/issue`
```json
{"guest_id": 6}
```
Issues a dining token to a guest currently inside the lounge.

### `POST /api/dining_token/redeem`
```json
{"token_code": "DT-001"}
```
Marks a dining token as redeemed.

### `GET /api/guests`
List all registered guests (admin only).

### `DELETE /api/guests/<guest_id>/face`
Remove face data for a guest (re-registration).

---

## Authentication Flow

```
Guest:                              Admin:
  /api/login_guest ─► JWT             /api/login_admin ─► JWT
       │                                    │
       ▼                                    ▼
  /api/guest_dashboard              /api/admin_dashboard
  /api/register_lounge              /api/change_modes
  /api/check_credit                 /api/analytics
  /api/payment_confirmation         /api/agent/state
```

JWT payload:
```json
{
  "user_id": 5,
  "role": "guest",
  "exp": 1740700000
}
```

---
