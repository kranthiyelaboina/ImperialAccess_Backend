# ImperialAccess – Airport Lounge Verification System

Hands-free premium lounge access verification using face recognition, an autonomous decision-making agent, and a Flask REST API backend with MongoDB Atlas.

---

## Quick Start (New Laptop Setup)

### Prerequisites

| Requirement | Version |
|-------------|---------|
| Python | 3.10 or higher |
| pip | Latest |
| Git | Any recent version |
| MongoDB Atlas | Free tier (M0) or above |
| Webcam | Built-in or USB |

### Step-by-step

```bash
# 1. Clone the repo
git clone https://github.com/kranthiyelaboina/ImperialAccess_Backend.git
cd ImperialAccess_Backend

# 2. Create and activate a virtual environment
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS / Linux:
# source .venv/bin/activate

# 3. Install ALL dependencies (agent + backend)
pip install -r requirements.txt
pip install -r backend/requirements.txt

# 4. Create the backend .env file
# (see "Environment Variables" section below)

# 5. Register at least one face
python register_face.py

# 6. Start the backend (includes the camera agent)
cd backend
python app.py
```

The server starts on **http://localhost:5000**. The camera agent launches automatically and opens an OpenCV window.

---

## Environment Variables

Create `backend/.env` with the following:

```env
MONGO_URI="mongodb+srv://<user>:<password>@<cluster>.mongodb.net/airport_lounge?retryWrites=true&w=majority"
JWT_SECRET="your_secret_key_here"
CAMERA_INDEX=0
```

| Variable | Description |
|----------|-------------|
| `MONGO_URI` | MongoDB Atlas connection string (replace `<user>`, `<password>`, `<cluster>`) |
| `JWT_SECRET` | Secret key for signing JWT tokens (any random string) |
| `CAMERA_INDEX` | Camera device index (0 = default webcam, 1 = external) |

> **Note:** The `.env` file is gitignored and must be created manually on each machine.

---

## Architecture

```
Camera Frame
     │
     ▼
┌─────────────┐     ┌──────────────┐     ┌──────────────────┐
│  FaceEngine │────►│ LoungeAgent  │────►│  Flask Backend   │
│  (YuNet +   │     │  (observe →  │     │  (REST API +     │
│   SFace)    │     │   reason →   │     │   MongoDB Atlas) │
│             │     │   decide →   │     │                  │
│  Detection  │     │   act)       │     │  JWT Auth +      │
│  Recognition│     │              │     │  Role-based ACL  │
└─────────────┘     └──────────────┘     └──────────────────┘
```

---

## Project Structure

```
├── face_engine.py          # Core face detection & recognition (YuNet + SFace)
├── lounge_agent.py         # Autonomous agent: 4 modes, events, occupancy
├── run_agent.py            # Standalone agent runner with OpenCV UI
├── register_face.py        # Register new faces into the database
├── download_models.py      # Download model files if missing
├── app.py                  # Streamlit attendance dashboard
├── test.py                 # Legacy attendance script
├── add_faces.py            # Legacy face registration
├── requirements.txt        # Agent Python dependencies
│
├── data/
│   ├── face_detection_yunet_2023mar.onnx    # YuNet face detector
│   ├── face_recognition_sface_2021dec.onnx  # SFace face recognizer
│   ├── haarcascade_frontalface_default.xml  # Haar cascade (legacy)
│   ├── deploy.prototxt                      # SSD deploy config
│   ├── res10_300x300_ssd_iter_140000.caffemodel  # SSD model
│   ├── openface_nn4.small2.v1.t7           # OpenFace model (legacy)
│   ├── names.pkl            # Registered names (gitignored)
│   └── faces_data.pkl      # Face embeddings (gitignored)
│
├── Attendance/
│   └── Attendance_DD-MM-YYYY.csv   # Daily attendance logs
│
└── backend/
    ├── app.py               # Flask application factory
    ├── config.py            # App configuration (loads .env)
    ├── db.py                # MongoDB connection & collections
    ├── models.py            # Document factory functions
    ├── auth.py              # JWT + bcrypt authentication
    ├── agent_service.py     # Camera agent singleton service
    ├── requirements.txt     # Backend-specific dependencies
    ├── .env                 # Environment variables (gitignored)
    ├── API_DESIGN.md        # Full API endpoint documentation
    ├── FRONTEND_PAGES.md    # Frontend page map (24 pages)
    ├── PROJECT_STRUCTURE.md # Backend architecture plan
    └── routes/
        ├── __init__.py
        ├── auth_routes.py   # Login, register, face registration
        ├── guest_routes.py  # Guest dashboard, payments
        ├── admin_routes.py  # Admin dashboard, analytics
        └── agent_routes.py  # Agent control, video feed, showcase
```

---

## Features

### Face Recognition Agent
- **Autonomous Agent** — observes, reasons, decides, and acts without human input
- **4 Operating Modes:**
  - `GATE` — multi-face detection at entry gate, auto-grants access
  - `RECEPTION` — single-person verification at the desk
  - `ENTRY` — logs incoming passengers with timestamps
  - `EXIT` — logs departing passengers with session duration
- **Occupancy Tracking** — knows who is inside the lounge at all times
- **Dining Token Management** — issue and track dining tokens per guest
- **Attendance Logging** — auto-saves to CSV
- **Temporal Smoothing** — embedding averaging + label voting for stable recognition
- **Security Alerts** — flags unknown/unregistered faces

### Backend API
- **JWT Authentication** — role-based access (admin / guest)
- **24 REST Endpoints** — see `backend/API_DESIGN.md` for full docs
- **MongoDB Atlas** — cloud-hosted database
- **Live MJPEG Stream** — camera feed accessible via `<img>` tag
- **Showcase Viewer** — built-in demo page at `/api/agent/showcase`
- **Auto-Cycle Modes** — cycle through all 4 modes automatically for demos

---

## API Endpoints (Summary)

### Authentication
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/login_admin` | Admin login |
| POST | `/api/login_guest` | Guest login |
| POST | `/api/register_guest` | Register new guest |
| POST | `/api/register_guest/face` | Register face via camera |

### Guest
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/guest_dashboard` | Guest profile & status |
| POST | `/api/register_lounge` | Register for lounge access |
| POST | `/api/check_credit` | Validate payment card |
| POST | `/api/payment_confirmation` | Confirm payment |

### Admin
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/admin_dashboard` | Stats overview |
| POST | `/api/change_modes` | Switch agent mode |
| GET | `/api/analytics` | Usage analytics |

### Agent
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/agent/state` | Current agent state |
| GET | `/api/agent/events` | Recent events |
| GET | `/api/agent/video_feed` | Live MJPEG stream |
| GET | `/api/agent/showcase` | Interactive demo page |
| POST | `/api/agent/showcase/mode` | Change showcase mode |
| POST | `/api/agent/showcase/autocycle` | Toggle auto-cycle |
| GET | `/api/agent/showcase/status` | Current mode & stats |
| POST | `/api/dining_token/issue` | Issue dining token |
| POST | `/api/dining_token/redeem` | Redeem dining token |
| GET | `/api/guests` | List all guests |
| DELETE | `/api/guests/<id>/face` | Delete a guest's face |

> Full request/response schemas are in `backend/API_DESIGN.md`.

---

## Default Credentials

| Role | Username | Password |
|------|----------|----------|
| Admin | `admin` | `admin123` |

> The default admin is auto-seeded on first run.

---

## Running the Standalone Agent (no backend)

```bash
python run_agent.py
```

### Command-line Options

```bash
python run_agent.py --mode gate        # Multi-face entry gate
python run_agent.py --mode reception   # Single-person desk
python run_agent.py --mode entry       # Log incoming passengers
python run_agent.py --mode exit        # Log departing passengers
python run_agent.py --camera 1         # Use a different camera
```

### Keyboard Controls

| Key | Action |
|-----|--------|
| `G` | Switch to Gate mode |
| `R` | Switch to Reception mode |
| `I` | Switch to Entry mode |
| `X` | Switch to Exit mode |
| `D` | Issue dining token |
| `Q` | Quit |

---

## How the Agent Works

1. **Observes** — reads camera frames, detects faces with YuNet
2. **Recognizes** — extracts 128-D SFace embeddings, cosine similarity match
3. **Tracks** — temporal smoothing across frames for stable identity
4. **Decides** — mode-specific logic (grant/deny/log)
5. **Acts** — emits structured JSON events for backend consumption

### Agent Events

| Event | Trigger |
|-------|---------|
| `FACE_RECOGNIZED` | Known member detected |
| `FACE_UNKNOWN` | Unregistered person detected |
| `ACCESS_GRANTED` | Lounge entry approved |
| `ACCESS_DENIED` | Entry denied |
| `PERSON_ENTERED` | Entry timestamp logged |
| `PERSON_EXITED` | Exit logged with duration |
| `ALERT_UNKNOWN` | Security alert |
| `ALERT_ALREADY_INSIDE` | Duplicate entry attempt |
| `DINING_TOKEN_ISSUED` | Token given to guest |

---

## Clear Face Database

```powershell
Remove-Item data\names.pkl, data\faces_data.pkl -Force
python register_face.py   # re-register faces
```

---

## Integration Validation

### Automated API Flow Test

From `backend/`:

```bash
pip install pytest
pytest -q tests/test_integration_api.py
```

What it validates:
- Guest flow: register -> login -> lounge registration -> credit check -> payment -> dashboard
- Admin flow: login -> dashboard -> analytics -> guest list -> issue/redeem dining token
- Contract checks:
  - `/api/change_modes` accepts lowercase and uppercase mode values
  - `/api/agent/events` and `/api/admin_dashboard` expose usable `face_name` values (legacy fallback)

Test records are intentionally created with an `itx_...` tag prefix for traceability.

### Manual Webcam Checklist

1. Start backend server from `backend/`:
   - `python app.py`
2. Verify live feed loads:
   - `http://127.0.0.1:5000/api/agent/video_feed`
3. Verify mode switching from frontend admin panel.
4. Confirm recent event log updates as faces are detected.

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Face Detection | YuNet (ONNX) |
| Face Recognition | SFace (ONNX, 128-D embeddings) |
| Similarity Metric | Cosine similarity (threshold: 0.363) |
| Computer Vision | OpenCV 4.13 |
| Backend | Flask + Flask-CORS |
| Database | MongoDB Atlas |
| Auth | JWT (PyJWT) + bcrypt |
| Language | Python 3.10+ |
| Dashboard | Streamlit (attendance viewer) |
