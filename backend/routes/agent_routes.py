"""
agent_routes.py — Agent control + dining token + guest management

GET  /api/agent/state
GET  /api/agent/events
GET  /api/agent/video_feed
POST /api/dining_token/issue
POST /api/dining_token/redeem
GET  /api/guests
DELETE /api/guests/<guest_id>/face
"""

from flask import Blueprint, request, jsonify, Response
from bson import ObjectId
from datetime import datetime

from auth import login_required, admin_required
from db import (
    agent_events_col, dining_tokens_col, guest_profiles_col,
    users_col, lounge_registrations_col
)
from models import create_dining_token, serialize_doc, serialize_list
from agent_service import get_agent_service

agent_bp = Blueprint("agent", __name__)


# =====================================================
# GET /api/agent/state
# =====================================================
@agent_bp.route("/api/agent/state", methods=["GET"])
@login_required
def agent_state():
    svc = get_agent_service()
    state = svc.get_state()
    return jsonify({"success": True, "state": state})


# =====================================================
# GET /api/agent/events
# =====================================================
@agent_bp.route("/api/agent/events", methods=["GET"])
@login_required
def agent_events():
    since_str = request.args.get("since")  # ISO timestamp
    limit = int(request.args.get("limit", 50))

    query = {}
    if since_str:
        try:
            since = datetime.fromisoformat(since_str)
            query["timestamp"] = {"$gt": since}
        except ValueError:
            return jsonify({"success": False, "error": "Invalid 'since' format. Use ISO 8601"}), 400

    events = list(agent_events_col().find(
        query, sort=[("timestamp", -1)], limit=limit,
    ))

    events_out = []
    for e in events:
        events_out.append({
            "id": str(e["_id"]),
            "event_type": e.get("event_type"),
            "face_name": e.get("face_name") or e.get("guest_name") or "Unknown",
            "confidence": e.get("confidence"),
            "timestamp": e["timestamp"].isoformat() if e.get("timestamp") else None,
            "mode": e.get("mode"),
            "details": e.get("details"),
        })

    return jsonify({"success": True, "events": events_out, "count": len(events_out)})


# =====================================================
# GET /api/agent/video_feed  (MJPEG stream)
# =====================================================
@agent_bp.route("/api/agent/video_feed", methods=["GET"])
def video_feed():
    svc = get_agent_service()
    return Response(
        svc.generate_video_feed(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# =====================================================
# GET /api/agent/register_feed  (MJPEG stream for face registration)
# =====================================================
@agent_bp.route("/api/agent/register_feed", methods=["GET"])
def register_feed():
    """MJPEG stream with registration-specific overlays (bounding box, progress, instructions)."""
    svc = get_agent_service()
    return Response(
        svc.generate_registration_feed(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# =====================================================
# GET /api/agent/register_progress  (registration status)
# =====================================================
@agent_bp.route("/api/agent/register_progress", methods=["GET"])
def register_progress():
    """Returns current face registration progress."""
    svc = get_agent_service()
    return jsonify({"success": True, **svc.get_registration_progress()})


# =====================================================
# GET /api/agent/viewer  (HTML page with live feed)
# =====================================================
@agent_bp.route("/api/agent/viewer", methods=["GET"])
def video_viewer():
    """Simple live feed viewer. For showcase, use /api/agent/showcase."""
    return _redirect_to_showcase()


def _redirect_to_showcase():
    from flask import redirect
    return redirect("/api/agent/showcase")


# =====================================================
# GET /api/agent/showcase  (Full demo showcase page)
# =====================================================
@agent_bp.route("/api/agent/showcase", methods=["GET"])
def showcase_viewer():
    """Full-featured showcase page: live feed + mode controls + auto-cycle + descriptions."""
    html = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Airport Lounge Agent — Showcase</title>
<style>
  :root { --accent: #e94560; --bg: #0f0f23; --card: #1a1a2e; --text: #eaeaea; }
  * { margin:0; padding:0; box-sizing:border-box; }
  body { background:var(--bg); color:var(--text); font-family:'Segoe UI',system-ui,sans-serif;
         display:flex; flex-direction:column; align-items:center; min-height:100vh; padding:20px 10px; }
  h1 { font-size:1.6em; margin-bottom:6px; }
  h1 span { color:var(--accent); }
  .subtitle { color:#888; font-size:0.85em; margin-bottom:18px; }

  /* Layout */
  .main { display:flex; gap:20px; width:100%; max-width:1100px; flex-wrap:wrap; justify-content:center; }
  .feed-panel { flex:1; min-width:500px; }
  .control-panel { width:320px; display:flex; flex-direction:column; gap:14px; }

  /* Feed */
  .feed-container { position:relative; border:2px solid var(--accent); border-radius:10px;
                    overflow:hidden; box-shadow:0 0 40px rgba(233,69,96,0.25); background:#000; }
  .feed-container img { width:100%; display:block; }
  .mode-badge { position:absolute; top:12px; right:12px; background:var(--accent); color:#fff;
                padding:6px 16px; border-radius:20px; font-weight:700; font-size:0.95em;
                letter-spacing:1px; text-transform:uppercase; transition:all 0.4s; }

  /* Mode cards */
  .mode-grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
  .mode-card { background:var(--card); border:2px solid transparent; border-radius:8px;
               padding:12px; cursor:pointer; transition:all 0.25s; text-align:center; }
  .mode-card:hover { border-color:var(--accent); transform:translateY(-2px); }
  .mode-card.active { border-color:var(--accent); background:#2a1a3e;
                      box-shadow:0 0 15px rgba(233,69,96,0.3); }
  .mode-card .icon { font-size:1.8em; margin-bottom:4px; }
  .mode-card .name { font-weight:700; font-size:0.95em; text-transform:uppercase; }
  .mode-card .desc { font-size:0.72em; color:#aaa; margin-top:4px; line-height:1.3; }

  /* Auto-cycle */
  .cycle-section { background:var(--card); border-radius:8px; padding:14px; text-align:center; }
  .cycle-section label { font-size:0.9em; margin-right:8px; }
  .cycle-btn { background:var(--accent); color:#fff; border:none; padding:8px 20px;
               border-radius:6px; cursor:pointer; font-weight:600; font-size:0.9em; transition:0.2s; }
  .cycle-btn:hover { filter:brightness(1.2); }
  .cycle-btn.stop { background:#555; }
  .interval-input { width:50px; padding:4px 6px; border-radius:4px; border:1px solid #555;
                    background:#222; color:#eee; text-align:center; font-size:0.9em; }

  /* Status bar */
  .status-bar { background:var(--card); border-radius:8px; padding:12px 16px;
                display:flex; justify-content:space-between; align-items:center; font-size:0.82em; }
  .status-bar .dot { width:8px; height:8px; border-radius:50%; display:inline-block;
                     margin-right:6px; animation:pulse 1.5s infinite; }
  .dot.green { background:#4caf50; } .dot.red { background:#f44336; } .dot.yellow { background:#ff9800; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.3} }

  /* Mode description panel */
  .mode-info { background:var(--card); border-radius:8px; padding:14px; min-height:80px;
               border-left:3px solid var(--accent); }
  .mode-info h3 { color:var(--accent); margin-bottom:6px; font-size:1em; }
  .mode-info p { font-size:0.82em; color:#bbb; line-height:1.5; }

  /* Timer bar */
  .timer-bar { height:3px; background:#333; border-radius:2px; margin-top:8px; overflow:hidden; }
  .timer-fill { height:100%; background:var(--accent); width:0%; transition:width 0.3s linear; }
</style>
</head>
<body>

<h1>Airport Lounge <span>Agent</span></h1>
<p class="subtitle">Face Recognition Access Verification System — Live Showcase</p>

<div class="main">
  <!-- LEFT: Video Feed -->
  <div class="feed-panel">
    <div class="feed-container">
      <img id="feed" src="/api/agent/video_feed" alt="Live Feed" />
      <div class="mode-badge" id="modeBadge">GATE</div>
    </div>
    <div class="timer-bar" id="timerBarContainer" style="display:none;">
      <div class="timer-fill" id="timerFill"></div>
    </div>
  </div>

  <!-- RIGHT: Controls -->
  <div class="control-panel">
    <!-- Mode Selector -->
    <div class="mode-grid">
      <div class="mode-card active" data-mode="gate" onclick="switchMode('gate')">
        <div class="icon">🚪</div>
        <div class="name">Gate</div>
        <div class="desc">Boarding pass verification at lounge entrance</div>
      </div>
      <div class="mode-card" data-mode="reception" onclick="switchMode('reception')">
        <div class="icon">🛎️</div>
        <div class="name">Reception</div>
        <div class="desc">Check-in desk face registration &amp; identity match</div>
      </div>
      <div class="mode-card" data-mode="entry" onclick="switchMode('entry')">
        <div class="icon">✅</div>
        <div class="name">Entry</div>
        <div class="desc">Grants access to verified guests, logs attendance</div>
      </div>
      <div class="mode-card" data-mode="exit" onclick="switchMode('exit')">
        <div class="icon">🚶</div>
        <div class="name">Exit</div>
        <div class="desc">Tracks departures, calculates visit duration</div>
      </div>
    </div>

    <!-- Auto-Cycle -->
    <div class="cycle-section">
      <label>Auto-Cycle Interval:</label>
      <input type="number" class="interval-input" id="intervalInput" value="10" min="3" max="60" />
      <span style="font-size:0.8em;color:#888;">sec</span>
      <br/><br/>
      <button class="cycle-btn" id="cycleBtn" onclick="toggleCycle()">▶ Start Auto-Cycle</button>
    </div>

    <!-- Mode Description -->
    <div class="mode-info" id="modeInfo">
      <h3>🚪 Gate Mode</h3>
      <p>The agent monitors the lounge entrance. It detects faces and checks them against
         registered guest profiles. Unknown faces trigger an alert. Recognized guests see
         a welcome message with their name.</p>
    </div>

    <!-- Status Bar -->
    <div class="status-bar">
      <div><span class="dot green" id="statusDot"></span><span id="statusText">Agent Running</span></div>
      <div>Faces: <strong id="faceCount">0</strong> &nbsp;|&nbsp; Occupancy: <strong id="occCount">0</strong></div>
    </div>
  </div>
</div>

<script>
const BASE = '';
const MODES_INFO = {
  gate: {
    icon: '🚪', title: 'Gate Mode',
    desc: 'The agent monitors the lounge entrance. It detects faces and checks them against registered guest profiles. Unknown faces trigger an alert. Recognized guests see a welcome message with their name.'
  },
  reception: {
    icon: '🛎️', title: 'Reception Mode',
    desc: 'The agent assists at the check-in desk. It captures face embeddings for new guest registration and verifies identity for returning guests. Ideal for the registration workflow.'
  },
  entry: {
    icon: '✅', title: 'Entry Mode',
    desc: 'The agent grants access to verified guests entering the lounge. Entry timestamps are logged, dining tokens are issued, and real-time occupancy tracking begins.'
  },
  exit: {
    icon: '🚶', title: 'Exit Mode',
    desc: 'The agent tracks guests leaving the lounge. It calculates visit duration, updates occupancy count, and logs exit timestamps for analytics and reporting.'
  }
};

let currentMode = 'gate';
let cycling = false;
let timerInterval = null;

// Switch mode via API
async function switchMode(mode) {
  try {
    const res = await fetch(BASE + '/api/agent/showcase/mode', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: mode.toUpperCase() })
    });
    const data = await res.json();
    if (data.success) {
      currentMode = mode;
      updateUI(mode);
    }
  } catch(e) { console.error('Mode switch failed:', e); }
}

function updateUI(mode) {
  // Badge
  document.getElementById('modeBadge').textContent = mode.toUpperCase();

  // Cards
  document.querySelectorAll('.mode-card').forEach(c => {
    c.classList.toggle('active', c.dataset.mode === mode);
  });

  // Info panel
  const info = MODES_INFO[mode];
  document.getElementById('modeInfo').innerHTML =
    `<h3>${info.icon} ${info.title}</h3><p>${info.desc}</p>`;
}

// Auto-cycle toggle
async function toggleCycle() {
  const btn = document.getElementById('cycleBtn');
  if (!cycling) {
    const interval = parseInt(document.getElementById('intervalInput').value) || 10;
    try {
      await fetch(BASE + '/api/agent/showcase/autocycle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'start', interval: interval })
      });
      cycling = true;
      btn.textContent = '⏸ Stop Auto-Cycle';
      btn.classList.add('stop');
      startTimerBar(interval);
    } catch(e) { console.error(e); }
  } else {
    await fetch(BASE + '/api/agent/showcase/autocycle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'stop' })
    });
    cycling = false;
    btn.textContent = '▶ Start Auto-Cycle';
    btn.classList.remove('stop');
    stopTimerBar();
  }
}

// Timer bar animation
function startTimerBar(interval) {
  const container = document.getElementById('timerBarContainer');
  const fill = document.getElementById('timerFill');
  container.style.display = 'block';
  let elapsed = 0;
  const step = 300; // ms
  if (timerInterval) clearInterval(timerInterval);
  timerInterval = setInterval(() => {
    elapsed += step;
    let pct = (elapsed / (interval * 1000)) * 100;
    if (pct >= 100) { pct = 0; elapsed = 0; }
    fill.style.width = pct + '%';
  }, step);
}
function stopTimerBar() {
  if(timerInterval) clearInterval(timerInterval);
  document.getElementById('timerBarContainer').style.display = 'none';
  document.getElementById('timerFill').style.width = '0%';
}

// Poll agent state every 2s to sync mode badge + stats
setInterval(async () => {
  try {
    const res = await fetch(BASE + '/api/agent/showcase/status');
    const data = await res.json();
    if (data.success) {
      const mode = data.mode;
      if (mode !== currentMode) {
        currentMode = mode;
        updateUI(mode);
      }
      document.getElementById('faceCount').textContent = data.faces_detected || 0;
      document.getElementById('occCount').textContent = data.occupancy || 0;
      const dot = document.getElementById('statusDot');
      const stxt = document.getElementById('statusText');
      if (data.running) {
        dot.className = 'dot green'; stxt.textContent = 'Agent Running';
      } else {
        dot.className = 'dot red'; stxt.textContent = 'Agent Stopped';
      }
      if (data.cycling !== cycling) {
        cycling = data.cycling;
        const btn = document.getElementById('cycleBtn');
        if (cycling) { btn.textContent = '⏸ Stop Auto-Cycle'; btn.classList.add('stop'); }
        else { btn.textContent = '▶ Start Auto-Cycle'; btn.classList.remove('stop'); stopTimerBar(); }
      }
    }
  } catch(e) {}
}, 2000);

// Auto-reconnect feed
document.getElementById('feed').onerror = function() {
  setTimeout(() => { this.src = '/api/agent/video_feed?' + Date.now(); }, 2000);
};
</script>
</body>
</html>
"""
    return Response(html, mimetype="text/html")


# =====================================================
# POST /api/agent/showcase/mode  (no-auth for demo)
# =====================================================
@agent_bp.route("/api/agent/showcase/mode", methods=["POST"])
def showcase_switch_mode():
    """Switch agent mode (no auth required for demo convenience)."""
    data = request.get_json() or {}
    mode = data.get("mode", "").lower()
    valid = ["gate", "reception", "entry", "exit"]
    if mode not in valid:
        return jsonify({"success": False, "error": f"Invalid mode. Choose: {valid}"}), 400

    svc = get_agent_service()
    # Stop auto-cycle if manually switching
    if svc.is_cycling():
        svc.stop_auto_cycle()
    svc.set_mode(mode)
    return jsonify({"success": True, "mode": mode})


# =====================================================
# POST /api/agent/showcase/autocycle
# =====================================================
@agent_bp.route("/api/agent/showcase/autocycle", methods=["POST"])
def showcase_autocycle():
    """Start or stop auto-cycling through all modes."""
    data = request.get_json() or {}
    action = data.get("action", "start")
    interval = int(data.get("interval", 10))
    interval = max(3, min(60, interval))

    svc = get_agent_service()
    if action == "start":
        svc.start_auto_cycle(interval=interval)
        return jsonify({"success": True, "cycling": True, "interval": interval})
    else:
        svc.stop_auto_cycle()
        return jsonify({"success": True, "cycling": False})


# =====================================================
# GET /api/agent/showcase/status  (lightweight poll)
# =====================================================
@agent_bp.route("/api/agent/showcase/status", methods=["GET"])
def showcase_status():
    """Lightweight status endpoint polled by the showcase UI."""
    svc = get_agent_service()
    state = svc.get_state()
    return jsonify({
        "success": True,
        "running": svc.is_running(),
        "mode": svc.get_mode(),
        "cycling": svc.is_cycling(),
        "faces_detected": state.get("faces_detected", 0),
        "occupancy": state.get("occupancy", 0),
        "recognized_names": state.get("recognized_names", []),
    })


# =====================================================
# POST /api/dining_token/issue
# =====================================================
@agent_bp.route("/api/dining_token/issue", methods=["POST"])
@admin_required
def issue_dining_token():
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "No JSON body"}), 400

    guest_id = data.get("guest_id")
    registration_id = data.get("registration_id")

    if not guest_id:
        return jsonify({"success": False, "error": "guest_id required"}), 400

    # Verify guest
    profile = guest_profiles_col().find_one({"_id": ObjectId(guest_id)})
    if not profile:
        return jsonify({"success": False, "error": "Guest profile not found"}), 404

    token_doc = create_dining_token(guest_id, registration_id)
    dining_tokens_col().insert_one(token_doc)

    return jsonify({
        "success": True,
        "token_code": token_doc["token_code"],
        "guest_id": guest_id,
        "message": "Dining token issued",
    })


# =====================================================
# POST /api/dining_token/redeem
# =====================================================
@agent_bp.route("/api/dining_token/redeem", methods=["POST"])
@login_required
def redeem_dining_token():
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "No JSON body"}), 400

    token_code = data.get("token_code")
    if not token_code:
        return jsonify({"success": False, "error": "token_code required"}), 400

    token = dining_tokens_col().find_one({"token_code": token_code})
    if not token:
        return jsonify({"success": False, "error": "Token not found"}), 404

    if token.get("redeemed"):
        return jsonify({
            "success": False,
            "error": "Token already redeemed",
            "redeemed_at": token["redeemed_at"].isoformat() if token.get("redeemed_at") else None,
        }), 409

    dining_tokens_col().update_one(
        {"_id": token["_id"]},
        {"$set": {"redeemed": True, "redeemed_at": datetime.utcnow()}}
    )

    return jsonify({
        "success": True,
        "token_code": token_code,
        "redeemed": True,
        "message": "Token redeemed successfully",
    })


# =====================================================
# GET /api/guests  — list all registered guests
# =====================================================
@agent_bp.route("/api/guests", methods=["GET"])
@admin_required
def list_guests():
    search = request.args.get("search", "")
    page = max(1, int(request.args.get("page", 1)))
    per_page = min(100, int(request.args.get("per_page", 20)))
    skip = (page - 1) * per_page

    # Build query
    query = {}
    if search:
        query["$or"] = [
            {"full_name": {"$regex": search, "$options": "i"}},
        ]

    # Join profiles with users
    profiles = list(guest_profiles_col().find(
        query if not search else {},
        sort=[("created_at", -1)],
    ))

    # Enrich with user info
    guests_out = []
    for p in profiles:
        user = users_col().find_one({"_id": p.get("user_id")})
        if not user:
            continue
        if search and search.lower() not in user.get("full_name", "").lower():
            continue

        guests_out.append({
            "guest_id": str(p["_id"]),
            "user_id": str(p["user_id"]),
            "full_name": user.get("full_name"),
            "email": user.get("email"),
            "face_registered": p.get("face_registered", False),
            "membership_type": p.get("membership_type", "standard"),
            "boarding_pass_no": p.get("boarding_pass_no"),
            "airline": p.get("airline"),
            "created_at": p["created_at"].isoformat() if p.get("created_at") else None,
        })

    total = len(guests_out)
    guests_page = guests_out[skip:skip + per_page]

    return jsonify({
        "success": True,
        "guests": guests_page,
        "total": total,
        "page": page,
        "per_page": per_page,
    })


# =====================================================
# DELETE /api/guests/<guest_id>/face
# =====================================================
@agent_bp.route("/api/guests/<guest_id>/face", methods=["DELETE"])
@admin_required
def delete_guest_face(guest_id):
    import sys, os
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

    profile = guest_profiles_col().find_one({"_id": ObjectId(guest_id)})
    if not profile:
        return jsonify({"success": False, "error": "Guest profile not found"}), 404

    user = users_col().find_one({"_id": profile.get("user_id")})
    if not user:
        return jsonify({"success": False, "error": "User not found"}), 404

    guest_name = user["full_name"]

    # Remove from FaceEngine's pickle database
    try:
        import pickle
        import numpy as np
        from face_engine import FaceEngine

        names_path = "data/names.pkl"
        faces_path = "data/faces_data.pkl"

        engine = FaceEngine()
        engine.load_database(names_path, faces_path)

        if guest_name not in engine.database:
            removed = False
        else:
            # Rebuild names and embeddings without this person
            new_names = []
            new_embeddings = []
            for name, emb in zip(engine.all_names, engine.all_embeddings):
                if name != guest_name:
                    new_names.append(name)
                    new_embeddings.append(emb)

            # Overwrite pickle files
            import os as _os
            _os.makedirs("data", exist_ok=True)
            with open(names_path, "wb") as f:
                pickle.dump(new_names, f)
            with open(faces_path, "wb") as f:
                pickle.dump(np.vstack(new_embeddings) if new_embeddings else np.array([]).reshape(0, 128), f)

            # Reload into engine memory
            engine.load_database(names_path, faces_path)
            removed = True
    except Exception as ex:
        return jsonify({"success": False, "error": f"Engine error: {str(ex)}"}), 500

    # Update profile
    guest_profiles_col().update_one(
        {"_id": ObjectId(guest_id)},
        {"$set": {"face_registered": False}}
    )

    return jsonify({
        "success": True,
        "removed": removed,
        "guest_name": guest_name,
        "message": f"Face data {'removed' if removed else 'was not found in'} for {guest_name}",
    })
