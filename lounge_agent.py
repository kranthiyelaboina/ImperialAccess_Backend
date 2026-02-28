"""
lounge_agent.py — Airport Lounge Access Verification Agent

This is an AGENT, not a script. It:
  1. OBSERVES   — processes camera frames autonomously
  2. REASONS    — matches faces, tracks identity over time, decides access
  3. DECIDES    — grants/denies entry, flags unknowns, tracks occupancy
  4. ACTS       — emits structured events for the backend to consume
  5. REMEMBERS  — maintains state: who's inside, entry/exit times, sessions

The agent is mode-aware:
  - GATE mode:       Multi-face detection at the lounge entrance gate
  - RECEPTION mode:  Single-person verification at reception desk
  - ENTRY mode:      Logs incoming passengers
  - EXIT mode:       Logs departing passengers

The backend (Flask) feeds frames to the agent and receives structured events.
No human button presses needed — the agent decides on its own.
"""

import time
import csv
import os
import numpy as np
from datetime import datetime
from collections import Counter, defaultdict
from enum import Enum
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Tuple

from face_engine import FaceEngine


# =====================================================
# AGENT MODES
# =====================================================
class AgentMode(Enum):
    GATE = "gate"               # Multi-face entry gate — detect everyone
    RECEPTION = "reception"     # Single-person desk — focus on one face
    ENTRY = "entry"             # Log people coming IN
    EXIT = "exit"               # Log people going OUT


# =====================================================
# EVENTS — Structured outputs the agent emits
# =====================================================
class EventType(Enum):
    FACE_RECOGNIZED = "face_recognized"
    FACE_UNKNOWN = "face_unknown"
    ACCESS_GRANTED = "access_granted"
    ACCESS_DENIED = "access_denied"
    PERSON_ENTERED = "person_entered"
    PERSON_EXITED = "person_exited"
    ALERT_UNKNOWN = "alert_unknown"           # security alert
    ALERT_ALREADY_INSIDE = "alert_already_inside"
    DINING_TOKEN_ISSUED = "dining_token_issued"


@dataclass
class AgentEvent:
    """Structured event emitted by the agent."""
    event_type: EventType
    timestamp: str
    name: str = "Unknown"
    confidence: int = 0
    bbox: Tuple[int, int, int, int] = (0, 0, 0, 0)
    details: Dict = field(default_factory=dict)

    def to_dict(self):
        d = asdict(self)
        d["event_type"] = self.event_type.value
        return d


# =====================================================
# FACE TRACKER — Temporal smoothing across frames
# =====================================================
TRACK_HISTORY_FRAMES = 15
MIN_CONSISTENT_FRAMES = 4
FACE_MATCH_IOU_THRESHOLD = 0.25
EMBEDDING_SMOOTH_ALPHA = 0.5


def compute_iou(box1, box2):
    x1, y1, w1, h1 = box1
    x2, y2, w2, h2 = box2
    xa, ya = max(x1, x2), max(y1, y2)
    xb, yb = min(x1 + w1, x2 + w2), min(y1 + h1, y2 + h2)
    inter = max(0, xb - xa) * max(0, yb - ya)
    union = w1 * h1 + w2 * h2 - inter
    return inter / union if union > 0 else 0


class FaceTracker:
    """
    Tracks faces across frames with temporal smoothing.
    Uses embedding averaging + label voting for stable recognition.
    """

    def __init__(self):
        self.tracks = {}
        self.next_id = 0

    def update(self, detections, engine: FaceEngine):
        """
        detections: list of dicts from FaceEngine.detect_and_recognize()
        Returns: list of smoothed results:
          {track_id, name, confidence, bbox, stable: bool}
        """
        now = time.time()
        used = set()
        results = []

        for det in detections:
            box = det["bbox"]
            label = det["name"]
            conf = det["confidence"]
            embedding = det["embedding"]

            # Match to existing track by IoU
            best_tid, best_iou = None, FACE_MATCH_IOU_THRESHOLD
            for tid, trk in self.tracks.items():
                if tid in used:
                    continue
                iou = compute_iou(box, trk["box"])
                if iou > best_iou:
                    best_iou = iou
                    best_tid = tid

            if best_tid is not None:
                trk = self.tracks[best_tid]
                trk["box"] = box
                trk["history"].append(label)
                trk["last_seen"] = now
                if embedding is not None:
                    if trk["smooth_emb"] is not None:
                        trk["smooth_emb"] = (
                            EMBEDDING_SMOOTH_ALPHA * embedding +
                            (1 - EMBEDDING_SMOOTH_ALPHA) * trk["smooth_emb"]
                        )
                    else:
                        trk["smooth_emb"] = embedding.copy()
                if len(trk["history"]) > TRACK_HISTORY_FRAMES:
                    trk["history"] = trk["history"][-TRACK_HISTORY_FRAMES:]
                used.add(best_tid)
            else:
                best_tid = self.next_id
                self.next_id += 1
                self.tracks[best_tid] = {
                    "box": box,
                    "history": [label],
                    "last_seen": now,
                    "smooth_emb": embedding.copy() if embedding is not None else None,
                }
                used.add(best_tid)

            trk = self.tracks[best_tid]
            # Re-classify with smoothed embedding
            if trk["smooth_emb"] is not None and len(trk["history"]) >= 2:
                sm_label, sm_conf = engine.classify(trk["smooth_emb"])
                if sm_conf > conf:
                    label = sm_label
                    trk["history"][-1] = label

            history = trk["history"]
            vote = Counter(history)

            if len(history) >= 2:
                top_label, top_count = vote.most_common(1)[0]
                if top_label != "Unknown" and top_count >= MIN_CONSISTENT_FRAMES:
                    s_label = top_label
                    s_conf = int((top_count / len(history)) * 100)
                    stable = True
                elif top_label != "Unknown" and top_count >= 2:
                    s_label = top_label
                    s_conf = int((top_count / len(history)) * 50)
                    stable = False
                else:
                    s_label, s_conf, stable = "Unknown", 0, False
            else:
                s_label, s_conf, stable = "Unknown", 0, False

            results.append({
                "track_id": best_tid,
                "name": s_label,
                "confidence": s_conf,
                "bbox": box,
                "stable": stable,
            })

        # Purge stale tracks
        stale = [t for t, v in self.tracks.items() if now - v["last_seen"] > 1.5]
        for t in stale:
            del self.tracks[t]

        return results


# =====================================================
# THE AGENT
# =====================================================
class LoungeAgent:
    """
    Airport Lounge Access Verification Agent.

    Autonomous decision-making loop:
      frame → detect → recognize → track → decide → emit events

    State maintained:
      - occupancy: who is currently inside the lounge
      - session_log: all entry/exit events with timestamps
      - dining_tokens: token count per person
      - pending_events: events emitted this cycle for backend consumption
    """

    def __init__(self, mode: AgentMode = AgentMode.GATE):
        self.mode = mode
        self.engine = FaceEngine()
        self.engine.load_database()
        self.tracker = FaceTracker()

        # === Agent State ===
        self.occupancy: Dict[str, dict] = {}  # {name: {entered_at, ...}}
        self.session_log: List[dict] = []
        self.dining_tokens: Dict[str, int] = defaultdict(int)
        self.pending_events: List[AgentEvent] = []
        self.attendance_recorded: set = set()  # names recorded today
        self._auto_grant_cooldown: Dict[str, float] = {}  # prevent spam

        # Load today's existing records
        self._load_today_records()

        print(f"[LoungeAgent] Initialized in {mode.value.upper()} mode")
        print(f"[LoungeAgent] Registered members: {', '.join(self.engine.get_registered_names()) or 'none'}")

    # --------------------------------------------------
    # MODE SWITCHING
    # --------------------------------------------------
    def set_mode(self, mode: AgentMode):
        """Switch agent mode (GATE / RECEPTION / ENTRY / EXIT)."""
        self.mode = mode
        self.tracker = FaceTracker()  # reset tracker on mode change
        print(f"[LoungeAgent] Mode changed to {mode.value.upper()}")

    # --------------------------------------------------
    # MAIN AGENT LOOP — call this per frame
    # --------------------------------------------------
    def process_frame(self, frame) -> List[AgentEvent]:
        """
        Core agent method. Feed it a camera frame, get back events.

        This is the autonomous decision loop:
          1. Detect & recognize faces
          2. Apply temporal smoothing
          3. Make decisions based on mode
          4. Emit events
          5. Return events for the backend

        The agent does NOT wait for human input — it decides on its own.
        """
        self.pending_events = []

        # Step 1: Detect and recognize all faces
        raw_detections = self.engine.detect_and_recognize(frame)

        # Step 2: Apply temporal smoothing
        smoothed = self.tracker.update(raw_detections, self.engine)

        # Step 3: Mode-specific decision-making
        if self.mode == AgentMode.GATE:
            self._decide_gate(smoothed)
        elif self.mode == AgentMode.RECEPTION:
            self._decide_reception(smoothed)
        elif self.mode == AgentMode.ENTRY:
            self._decide_entry(smoothed)
        elif self.mode == AgentMode.EXIT:
            self._decide_exit(smoothed)

        # Return the raw smoothed face list AND events
        return self.pending_events

    def get_current_faces(self) -> list:
        """Return the last set of smoothed face detections for UI rendering."""
        # Rebuild from tracker state
        results = []
        for tid, trk in self.tracker.tracks.items():
            if time.time() - trk["last_seen"] > 0.5:
                continue
            vote = Counter(trk["history"])
            if vote:
                top_label, top_count = vote.most_common(1)[0]
                if top_label != "Unknown" and top_count >= MIN_CONSISTENT_FRAMES:
                    results.append({
                        "track_id": tid,
                        "name": top_label,
                        "confidence": int((top_count / len(trk["history"])) * 100),
                        "bbox": trk["box"],
                        "stable": True,
                    })
                elif top_label != "Unknown" and top_count >= 2:
                    results.append({
                        "track_id": tid,
                        "name": top_label,
                        "confidence": int((top_count / len(trk["history"])) * 50),
                        "bbox": trk["box"],
                        "stable": False,
                    })
                else:
                    results.append({
                        "track_id": tid,
                        "name": "Unknown",
                        "confidence": 0,
                        "bbox": trk["box"],
                        "stable": False,
                    })
        return results

    # --------------------------------------------------
    # DECISION ENGINES (one per mode)
    # --------------------------------------------------
    def _decide_gate(self, faces):
        """
        GATE mode: Multi-face detection at the entry gate.
        - Recognized premium member → AUTO-GRANT access
        - Unknown face → ALERT security
        All decisions are autonomous — no button press needed.
        """
        now_ts = datetime.now().strftime("%H:%M:%S")

        for face in faces:
            name = face["name"]
            bbox = face["bbox"]
            stable = face["stable"]
            conf = face["confidence"]

            if not stable:
                continue  # Wait for stable identification

            if name == "Unknown":
                self._emit(EventType.FACE_UNKNOWN, name="Unknown", bbox=bbox,
                           details={"action": "security_alert"})
                self._emit(EventType.ALERT_UNKNOWN, name="Unknown", bbox=bbox,
                           details={"message": "Unregistered person at gate"})
            else:
                # Check cooldown (don't spam events for the same person)
                if self._in_cooldown(name):
                    continue

                self._emit(EventType.FACE_RECOGNIZED, name=name,
                           confidence=conf, bbox=bbox)

                # Auto-grant access
                if name not in self.occupancy:
                    self._grant_access(name, bbox)
                else:
                    self._emit(EventType.ALERT_ALREADY_INSIDE, name=name,
                               bbox=bbox,
                               details={"message": f"{name} is already inside"})

    def _decide_reception(self, faces):
        """
        RECEPTION mode: Single-person verification at the desk.
        Focus on the LARGEST face (closest person to camera).
        """
        if not faces:
            return

        # Pick the largest face (closest to camera)
        faces_sorted = sorted(faces, key=lambda f: f["bbox"][2] * f["bbox"][3],
                               reverse=True)
        primary = faces_sorted[0]

        name = primary["name"]
        bbox = primary["bbox"]
        conf = primary["confidence"]
        stable = primary["stable"]

        if not stable:
            return

        now_ts = datetime.now().strftime("%H:%M:%S")

        if name == "Unknown":
            self._emit(EventType.ACCESS_DENIED, name="Unknown", bbox=bbox,
                       details={"reason": "Not registered as premium member"})
        else:
            if self._in_cooldown(name):
                return
            self._emit(EventType.FACE_RECOGNIZED, name=name,
                       confidence=conf, bbox=bbox,
                       details={"member_type": "premium"})
            self._grant_access(name, bbox)

    def _decide_entry(self, faces):
        """
        ENTRY mode: Log incoming passengers.
        Tracks who enters with timestamps.
        """
        for face in faces:
            name = face["name"]
            bbox = face["bbox"]
            stable = face["stable"]

            if not stable or name == "Unknown":
                if name == "Unknown" and face.get("stable", False):
                    self._emit(EventType.FACE_UNKNOWN, name="Unknown", bbox=bbox)
                continue

            if self._in_cooldown(name):
                continue

            if name not in self.occupancy:
                self._log_entry(name)
                self._emit(EventType.PERSON_ENTERED, name=name, bbox=bbox,
                           details={"entered_at": datetime.now().strftime("%H:%M:%S")})
                self._set_cooldown(name)
            else:
                self._emit(EventType.ALERT_ALREADY_INSIDE, name=name, bbox=bbox)

    def _decide_exit(self, faces):
        """
        EXIT mode: Log departing passengers.
        Records exit time and calculates session duration.
        """
        for face in faces:
            name = face["name"]
            bbox = face["bbox"]
            stable = face["stable"]

            if not stable or name == "Unknown":
                continue

            if self._in_cooldown(name):
                continue

            if name in self.occupancy:
                duration = self._log_exit(name)
                self._emit(EventType.PERSON_EXITED, name=name, bbox=bbox,
                           details={
                               "exited_at": datetime.now().strftime("%H:%M:%S"),
                               "duration_minutes": round(duration, 1),
                           })
                self._set_cooldown(name)
            else:
                # Person exiting who wasn't logged as inside — still log it
                self._emit(EventType.PERSON_EXITED, name=name, bbox=bbox,
                           details={"note": "no matching entry record"})
                self._set_cooldown(name)

    # --------------------------------------------------
    # ACCESS CONTROL
    # --------------------------------------------------
    def _grant_access(self, name, bbox):
        """Grant lounge access: log entry + record attendance + emit event."""
        self._log_entry(name)
        self._record_attendance(name)
        self._emit(EventType.ACCESS_GRANTED, name=name, bbox=bbox,
                   details={
                       "entered_at": datetime.now().strftime("%H:%M:%S"),
                       "lounge": "Premium Lounge",
                   })
        self._set_cooldown(name)

    # --------------------------------------------------
    # OCCUPANCY & SESSION MANAGEMENT
    # --------------------------------------------------
    def _log_entry(self, name):
        """Record a person entering the lounge."""
        self.occupancy[name] = {
            "entered_at": datetime.now().isoformat(),
            "timestamp": time.time(),
        }
        self.session_log.append({
            "name": name,
            "event": "entry",
            "time": datetime.now().isoformat(),
        })

    def _log_exit(self, name) -> float:
        """Record a person exiting. Returns duration in minutes."""
        entry = self.occupancy.pop(name, None)
        duration = 0.0
        if entry:
            duration = (time.time() - entry["timestamp"]) / 60.0

        self.session_log.append({
            "name": name,
            "event": "exit",
            "time": datetime.now().isoformat(),
            "duration_minutes": round(duration, 1),
        })
        return duration

    def get_occupancy(self) -> Dict[str, dict]:
        """Return current lounge occupancy."""
        return dict(self.occupancy)

    def get_occupancy_count(self) -> int:
        return len(self.occupancy)

    def get_session_log(self) -> List[dict]:
        return list(self.session_log)

    # --------------------------------------------------
    # DINING TOKEN MANAGEMENT
    # --------------------------------------------------
    def issue_dining_token(self, name) -> Optional[AgentEvent]:
        """Issue a dining token to a known guest."""
        if name in self.occupancy:
            self.dining_tokens[name] += 1
            evt = AgentEvent(
                event_type=EventType.DINING_TOKEN_ISSUED,
                timestamp=datetime.now().strftime("%H:%M:%S"),
                name=name,
                details={"total_tokens": self.dining_tokens[name]},
            )
            self.pending_events.append(evt)
            return evt
        return None

    def get_dining_tokens(self, name) -> int:
        return self.dining_tokens.get(name, 0)

    # --------------------------------------------------
    # ATTENDANCE / CSV
    # --------------------------------------------------
    def _get_attendance_path(self):
        os.makedirs("Attendance", exist_ok=True)
        date = datetime.now().strftime("%d-%m-%Y")
        return f"Attendance/Attendance_{date}.csv"

    def _load_today_records(self):
        path = self._get_attendance_path()
        if os.path.isfile(path):
            with open(path, "r") as f:
                reader = csv.reader(f)
                next(reader, None)
                for row in reader:
                    if row:
                        self.attendance_recorded.add(row[0])

    def _record_attendance(self, name):
        """Auto-record attendance (no button press needed)."""
        if name in self.attendance_recorded or name == "Unknown":
            return
        path = self._get_attendance_path()
        file_exists = os.path.isfile(path)
        with open(path, "a", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["NAME", "TIME", "MODE", "EVENT"])
            writer.writerow([
                name,
                datetime.now().strftime("%H:%M:%S"),
                self.mode.value,
                "access_granted",
            ])
        self.attendance_recorded.add(name)

    def get_todays_attendance(self) -> set:
        return set(self.attendance_recorded)

    # --------------------------------------------------
    # EVENT SYSTEM
    # --------------------------------------------------
    def _emit(self, event_type: EventType, name="Unknown",
              confidence=0, bbox=(0, 0, 0, 0), details=None):
        """Emit a structured event."""
        evt = AgentEvent(
            event_type=event_type,
            timestamp=datetime.now().strftime("%H:%M:%S"),
            name=name,
            confidence=confidence,
            bbox=bbox,
            details=details or {},
        )
        self.pending_events.append(evt)

    # --------------------------------------------------
    # COOLDOWN (prevents spamming events for same person)
    # --------------------------------------------------
    def _in_cooldown(self, name, seconds=5.0) -> bool:
        last = self._auto_grant_cooldown.get(name, 0)
        return (time.time() - last) < seconds

    def _set_cooldown(self, name):
        self._auto_grant_cooldown[name] = time.time()

    # --------------------------------------------------
    # SERIALIZABLE STATE (for backend/API)
    # --------------------------------------------------
    def get_state(self) -> dict:
        """
        Return the full agent state as a JSON-serializable dict.
        This is what the Flask backend would serve at /api/agent/state.
        """
        return {
            "mode": self.mode.value,
            "occupancy": {
                name: info for name, info in self.occupancy.items()
            },
            "occupancy_count": len(self.occupancy),
            "registered_members": self.engine.get_registered_names(),
            "todays_attendance": sorted(self.attendance_recorded),
            "dining_tokens": dict(self.dining_tokens),
            "session_log_count": len(self.session_log),
            "recent_events": [e.to_dict() for e in self.pending_events[-10:]],
        }
