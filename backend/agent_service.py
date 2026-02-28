"""
agent_service.py — Wraps LoungeAgent for Flask backend

Runs the agent's camera loop in a background thread.
Flask endpoints query this service for agent state, events, and video frames.
The existing agent code (lounge_agent.py, face_engine.py) is NOT modified.
"""

import sys
import os
import threading
import time
import cv2
import numpy as np
from datetime import datetime

# Add parent directory to path so we can import agent modules
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from lounge_agent import LoungeAgent, AgentMode, EventType
from face_engine import FaceEngine
from concierge_service import get_concierge_service

from db import attendance_logs_col, agent_events_col, guest_profiles_col
from models import create_attendance_log, create_agent_event


class AgentService:
    """
    Singleton service that wraps the LoungeAgent.
    Runs camera processing in a background thread.
    Thread-safe access to agent state from Flask routes.
    """

    def __init__(self):
        self._agent = None
        self._thread = None
        self._running = False
        self._lock = threading.Lock()
        self._raw_frame = None           # latest raw camera frame (for face reg)
        self._latest_frame = None        # last processed frame with overlays
        self._latest_frame_jpeg = None   # JPEG-encoded frame
        self._new_frame_event = threading.Event()  # signals new frame ready
        self._event_log = []             # persistent event log
        self._camera_index = 0
        self._show_local_window = False  # native OpenCV window
        self._jpeg_quality = 70          # balanced quality vs size
        self._target_fps = 25            # smooth 25fps MJPEG stream
        self._agent_process_every = 4    # run full agent detection every Nth frame
        self._stream_width = 640         # 640x480 for fast encode + decent clarity
        self._stream_height = 480
        self._frame_counter = 0          # monotonic counter for frame-skipping

        # ── Face registration state ──
        self._reg_active = False          # True while registration capture is running
        self._reg_name = ""               # name being registered
        self._reg_embeddings = []         # collected embeddings
        self._reg_max_samples = 50
        self._reg_frame_jpeg = None       # registration-specific MJPEG frame
        self._reg_frame_event = threading.Event()
        self._reg_done_event = threading.Event()
        self._reg_error = ""

        # ── Voice Concierge state ──
        self._concierge = get_concierge_service()  # Get singleton concierge instance
        self._guest_greeting_cache = {}  # Cache recent greetings to avoid duplicates {guest_id: timestamp}
        self._greeting_cooldown = 30  # Don't greet same guest more than once per N seconds

    def start(self, camera_index=0, mode="gate", show_window=True):
        """Start the agent in a background thread.
        
        Args:
            camera_index: Camera device index.
            mode: Agent mode (gate/reception/entry/exit).
            show_window: If True, open a native OpenCV window for
                         zero-latency local display.
        """
        if self._running:
            return

        self._camera_index = camera_index
        self._show_local_window = show_window

        # Change working directory to project root for model/data file paths
        project_root = os.path.dirname(os.path.dirname(__file__))
        os.chdir(project_root)

        mode_map = {
            "gate": AgentMode.GATE,
            "reception": AgentMode.RECEPTION,
            "entry": AgentMode.ENTRY,
            "exit": AgentMode.EXIT,
        }

        with self._lock:
            self._agent = LoungeAgent(mode=mode_map.get(mode, AgentMode.GATE))

        self._running = True
        self._thread = threading.Thread(target=self._camera_loop, daemon=True)
        self._thread.start()
        print(f"[AgentService] Started camera loop (camera={camera_index}, mode={mode})")

    def stop(self):
        """Stop the background camera loop."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        print("[AgentService] Stopped.")

    def is_running(self) -> bool:
        return self._running

    # --------------------------------------------------
    # CAMERA LOOP (runs in background thread)
    # --------------------------------------------------
    def _camera_loop(self):
        """Background thread: read frames, feed to agent, store events."""
        cap = cv2.VideoCapture(self._camera_index, cv2.CAP_DSHOW)  # DirectShow = fastest on Windows
        if not cap.isOpened():
            # Fallback to default backend
            cap = cv2.VideoCapture(self._camera_index)
        if not cap.isOpened():
            print(f"[AgentService] ERROR: Cannot open camera {self._camera_index}")
            self._running = False
            return

        # Camera settings for low-latency capture
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_FPS, 30)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        window_name = "Lounge Agent - Live Feed"
        if self._show_local_window:
            cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(window_name, 800, 600)
            print(f"[AgentService] Local display window opened. Press 'q' in the window to close it.")

        frame_interval = 1.0 / self._target_fps
        last_frame_time = 0.0

        while self._running:
            ret, frame = cap.read()
            if not ret or frame is None:
                time.sleep(0.005)
                continue

            # ── Frame-rate cap: don't process more than _target_fps ──
            now = time.time()
            if now - last_frame_time < frame_interval:
                continue
            last_frame_time = now

            self._frame_counter += 1

            # Store raw frame for face registration to borrow
            self._raw_frame = frame

            # ── Registration capture mode ──
            if self._reg_active:
                # ONLY do registration processing — skip heavy agent detection
                # to maximize capture speed and avoid camera resource contention
                self._process_registration_frame(frame)

                # Still encode a simple frame for the normal video feed
                stream_frame = cv2.resize(frame, (self._stream_width, self._stream_height))
                _, jpeg = cv2.imencode(".jpg", stream_frame,
                                       [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_quality])
                self._latest_frame_jpeg = jpeg.tobytes()
                self._latest_frame = frame
                self._new_frame_event.set()
                continue  # skip agent processing entirely during registration

            # ── Normal agent mode ──
            # Run full face detection/recognition only every Nth frame
            # to reduce CPU load and improve stream responsiveness
            events = []
            if self._frame_counter % self._agent_process_every == 0:
                with self._lock:
                    events = self._agent.process_frame(frame)

            # --- Build display frame with overlays ---
            display_frame = frame.copy()
            with self._lock:
                faces = self._agent.get_current_faces()

            for face in faces:
                name = face["name"]
                conf = face["confidence"]
                x, y, w, h = face["bbox"]

                color = (0, 200, 0) if name != "Unknown" else (0, 0, 255)
                cv2.rectangle(display_frame, (x, y), (x + w, y + h), color, 2)

                label_h = 28
                cv2.rectangle(display_frame, (x, y - label_h), (x + w, y), color, -1)
                text = f"{name} {conf}%" if name != "Unknown" else "UNKNOWN"
                cv2.putText(display_frame, text, (x + 4, y - 8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

            # Mode + occupancy indicator
            with self._lock:
                mode_text = self._agent.mode.value.upper()
                occ = self._agent.get_occupancy() if hasattr(self._agent, 'get_occupancy') else {}
            occ_count = len(occ) if isinstance(occ, dict) else 0

            cv2.rectangle(display_frame, (5, 5), (250, 35), (255, 160, 50), -1)
            cv2.putText(display_frame, f"AGENT: {mode_text}  |  IN: {occ_count}", (10, 27),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)

            # --- NATIVE LOCAL WINDOW (zero latency, full resolution) ---
            if self._show_local_window:
                cv2.imshow(window_name, display_frame)
                key = cv2.waitKey(1) & 0xFF
                if key == ord('q'):
                    self._show_local_window = False
                    cv2.destroyWindow(window_name)
                    print("[AgentService] Local window closed. MJPEG stream still active.")

            # --- JPEG encode for MJPEG web stream ---
            # Resize to smaller resolution for faster encoding & lower bandwidth
            stream_frame = cv2.resize(display_frame, (self._stream_width, self._stream_height))
            _, jpeg = cv2.imencode(".jpg", stream_frame,
                                   [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_quality])
            self._latest_frame_jpeg = jpeg.tobytes()
            self._latest_frame = display_frame
            self._new_frame_event.set()

            # --- Process and persist events ---
            for evt in events:
                evt_dict = evt.to_dict()
                self._event_log.append(evt_dict)

                try:
                    with self._lock:
                        cur_mode = self._agent.mode.value.lower()

                    agent_events_col().insert_one(create_agent_event(
                        event_type=evt_dict["event_type"],
                        face_name=evt_dict.get("name"),
                        confidence=evt_dict.get("confidence", 0),
                        mode=cur_mode,
                        details=evt_dict.get("details"),
                    ))

                    if evt.event_type in (EventType.PERSON_ENTERED, EventType.ACCESS_GRANTED):
                        attendance_logs_col().insert_one(create_attendance_log(
                            guest_name=evt.name,
                            event_type="entry",
                            mode=cur_mode,
                            detected_by="agent_auto",
                        ))
                        
                        # ── Trigger voice concierge greeting ──
                        if evt.event_type == EventType.ACCESS_GRANTED and evt.name != "Unknown":
                            self._trigger_greeting(evt.name)
                    elif evt.event_type == EventType.PERSON_EXITED:
                        dur = evt.details.get("duration_minutes")
                        attendance_logs_col().insert_one(create_attendance_log(
                            guest_name=evt.name,
                            event_type="exit",
                            mode=cur_mode,
                            duration_minutes=dur,
                            detected_by="agent_auto",
                        ))
                except Exception as e:
                    print(f"[AgentService] DB write error: {e}")

            if len(self._event_log) > 500:
                self._event_log = self._event_log[-250:]

        cap.release()
        if self._show_local_window:
            cv2.destroyAllWindows()

    # --------------------------------------------------
    # FACE REGISTRATION (inline in camera loop)
    # --------------------------------------------------
    def _process_registration_frame(self, frame):
        """
        Called every camera frame while _reg_active is True.
        Detects faces, collects augmented embeddings, and renders a
        registration-specific MJPEG frame with bounding box + progress.
        """
        if not self._reg_active:
            return

        reg_engine = getattr(self, '_reg_engine', None)
        if reg_engine is None:
            return

        reg_frame = frame.copy()
        faces = reg_engine.detect(frame)

        # Increment grab counter
        self._reg_grab_count = getattr(self, '_reg_grab_count', 0) + 1

        collected = len(self._reg_embeddings)
        pct = int(collected / self._reg_max_samples * 100)

        if len(faces) > 0:
            # Use the largest face
            areas = [f[2] * f[3] for f in faces]
            best_idx = int(np.argmax(areas))
            face = faces[best_idx]
            x, y, w, h = int(face[0]), int(face[1]), int(face[2]), int(face[3])

            # Collect embeddings every frame for fast registration
            if collected < self._reg_max_samples:
                try:
                    aug = reg_engine.get_augmented_embeddings(frame, face)
                    for emb in aug:
                        if len(self._reg_embeddings) < self._reg_max_samples:
                            self._reg_embeddings.append(np.asarray(emb, dtype=np.float32).flatten())
                except Exception:
                    pass

            # Draw registration-specific overlay
            cv2.rectangle(reg_frame, (x, y), (x + w, y + h), (50, 50, 255), 2)

            # Face scan corner brackets
            bracket_len = min(w, h) // 4
            color = (126, 164, 197)  # gold-ish in BGR
            for cx, cy, dx, dy in [
                (x, y, 1, 1), (x + w, y, -1, 1),
                (x, y + h, 1, -1), (x + w, y + h, -1, -1)
            ]:
                cv2.line(reg_frame, (cx, cy), (cx + dx * bracket_len, cy), color, 3)
                cv2.line(reg_frame, (cx, cy), (cx, cy + dy * bracket_len), color, 3)

        # Top banner
        cv2.rectangle(reg_frame, (0, 0), (reg_frame.shape[1], 50), (50, 50, 200), -1)
        progress_text = f"REGISTERING: {self._reg_name}  |  {collected}/{self._reg_max_samples} ({pct}%)"
        cv2.putText(reg_frame, progress_text, (12, 34),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)

        # Progress bar at bottom
        bar_y = reg_frame.shape[0] - 8
        bar_w = int(reg_frame.shape[1] * pct / 100)
        cv2.rectangle(reg_frame, (0, bar_y), (reg_frame.shape[1], reg_frame.shape[0]), (40, 40, 40), -1)
        cv2.rectangle(reg_frame, (0, bar_y), (bar_w, reg_frame.shape[0]), (126, 164, 197), -1)

        # Instruction text
        if len(faces) == 0:
            cv2.putText(reg_frame, "No face detected — look at the camera",
                        (12, reg_frame.shape[0] - 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 1)
        else:
            cv2.putText(reg_frame, "Move head slowly left/right/up/down",
                        (12, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)

        # Encode registration frame as JPEG
        _, jpeg = cv2.imencode(".jpg", reg_frame,
                               [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_quality])
        self._reg_frame_jpeg = jpeg.tobytes()
        self._reg_frame_event.set()

        # Check if done
        if len(self._reg_embeddings) >= self._reg_max_samples:
            self._reg_active = False
            self._reg_done_event.set()

    def start_face_registration(self, name: str, max_samples: int = 150) -> bool:
        """
        Begin inline face registration. The camera loop will start
        collecting embeddings and streaming a registration-specific feed.
        Returns True if started successfully.
        """
        if self._reg_active:
            return False  # already registering

        self._reg_name = name
        self._reg_max_samples = max_samples
        self._reg_embeddings = []
        self._reg_grab_count = 0
        self._reg_error = ""
        self._reg_done_event.clear()
        self._reg_frame_event.clear()
        self._reg_engine = FaceEngine()  # fresh engine instance for registration
        self._reg_active = True
        print(f"[AgentService] Face registration started for '{name}' (max {max_samples} embeddings)")
        return True

    def wait_face_registration(self, timeout: float = 30.0) -> dict:
        """
        Block until registration finishes or times out.
        Returns dict with results.
        """
        self._reg_done_event.wait(timeout=timeout)

        # Force stop if timed out
        self._reg_active = False
        embeddings = self._reg_embeddings
        self._reg_embeddings = []

        if not embeddings:
            return {"success": False, "embeddings": [], "error": "No face detected. Try again."}

        return {"success": True, "embeddings": embeddings, "error": ""}

    def cancel_face_registration(self):
        """Cancel an in-progress registration."""
        self._reg_active = False
        self._reg_embeddings = []
        self._reg_done_event.set()

    def reload_face_database(self):
        """
        Reload the face embedding database in the agent's engine so
        newly registered faces are immediately recognized.
        """
        with self._lock:
            if self._agent and hasattr(self._agent, 'engine'):
                self._agent.engine.load_database()
                print("[AgentService] Agent face database reloaded.")

    def is_registering(self) -> bool:
        return self._reg_active

    def get_registration_progress(self) -> dict:
        """Get current registration progress."""
        collected = len(self._reg_embeddings)
        return {
            "active": self._reg_active,
            "name": self._reg_name,
            "collected": collected,
            "target": self._reg_max_samples,
            "percent": int(collected / self._reg_max_samples * 100) if self._reg_max_samples > 0 else 0,
        }

    def generate_registration_feed(self):
        """
        MJPEG generator for the face registration stream.
        Shows camera with registration-specific overlays
        (bounding box, progress, instructions).
        """
        while self._reg_active or self._running:
            self._reg_frame_event.wait(timeout=0.04)  # ~25fps max wait
            self._reg_frame_event.clear()

            frame = self._reg_frame_jpeg
            if frame:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                )

            # Stop streaming once registration is complete
            if not self._reg_active:
                # Send one last frame, then stop
                break

    # --------------------------------------------------
    # PUBLIC API (called by Flask routes, thread-safe)
    # --------------------------------------------------
    def get_state(self) -> dict:
        """Get normalized agent state for API responses."""
        with self._lock:
            if self._agent:
                raw_state = self._agent.get_state() or {}
                faces = self._agent.get_current_faces() or []

                occupancy_map = raw_state.get("occupancy")
                if not isinstance(occupancy_map, dict):
                    occupancy_map = self._agent.get_occupancy() if hasattr(self._agent, "get_occupancy") else {}
                if not isinstance(occupancy_map, dict):
                    occupancy_map = {}

                occupancy_count = raw_state.get("occupancy_count")
                if not isinstance(occupancy_count, int):
                    occupancy_count = len(occupancy_map)

                recognized_names = sorted({
                    face.get("name")
                    for face in faces
                    if face.get("name") and face.get("name") != "Unknown"
                })

                mode = raw_state.get("mode")
                if mode is None and hasattr(self._agent, "mode"):
                    mode = self._agent.mode.value

                # Keep legacy keys from LoungeAgent while normalizing integration keys.
                state = dict(raw_state)
                state.update({
                    "running": self._running,
                    "mode": mode,
                    "occupancy": occupancy_count,
                    "occupancy_count": occupancy_count,
                    "occupancy_map": occupancy_map,
                    "faces_detected": len(faces),
                    "recognized_names": recognized_names,
                })
                return state

        return {
            "running": False,
            "mode": None,
            "occupancy": 0,
            "occupancy_count": 0,
            "occupancy_map": {},
            "faces_detected": 0,
            "recognized_names": [],
        }

    def get_current_faces(self) -> list:
        with self._lock:
            if self._agent:
                return self._agent.get_current_faces()
        return []

    def get_occupancy(self) -> dict:
        with self._lock:
            if self._agent:
                return self._agent.get_occupancy()
        return {}

    def get_session_log(self) -> list:
        with self._lock:
            if self._agent:
                return self._agent.get_session_log()
        return []

    def get_todays_attendance(self) -> set:
        with self._lock:
            if self._agent:
                return self._agent.get_todays_attendance()
        return set()

    def set_mode(self, mode_str: str) -> str:
        """Switch agent mode. Returns previous mode."""
        mode_map = {
            "gate": AgentMode.GATE,
            "reception": AgentMode.RECEPTION,
            "entry": AgentMode.ENTRY,
            "exit": AgentMode.EXIT,
        }
        if mode_str not in mode_map:
            raise ValueError(f"Invalid mode: {mode_str}")

        with self._lock:
            if self._agent:
                prev = self._agent.mode.value
                self._agent.set_mode(mode_map[mode_str])
                return prev
        return "unknown"

    def get_mode(self) -> str:
        with self._lock:
            if self._agent:
                return self._agent.mode.value
        return "unknown"

    # --------------------------------------------------
    # AUTO-CYCLE (for demo/showcase)
    # --------------------------------------------------
    _MODES_ORDER = ["gate", "reception", "entry", "exit"]

    def start_auto_cycle(self, interval=10):
        """Start auto-cycling through all 4 modes every `interval` seconds."""
        if getattr(self, "_cycling", False):
            return
        self._cycling = True
        self._cycle_interval = interval
        self._cycle_thread = threading.Thread(target=self._cycle_loop, daemon=True)
        self._cycle_thread.start()
        print(f"[AgentService] Auto-cycle started ({interval}s per mode)")

    def stop_auto_cycle(self):
        """Stop auto-cycling."""
        self._cycling = False
        if hasattr(self, "_cycle_thread") and self._cycle_thread:
            self._cycle_thread.join(timeout=2)
        print("[AgentService] Auto-cycle stopped")

    def is_cycling(self) -> bool:
        return getattr(self, "_cycling", False)

    def _cycle_loop(self):
        idx = 0
        while getattr(self, "_cycling", False) and self._running:
            mode = self._MODES_ORDER[idx % 4]
            self.set_mode(mode)
            # Sleep in small increments so we can stop quickly
            for _ in range(int(self._cycle_interval * 10)):
                if not getattr(self, "_cycling", False):
                    return
                time.sleep(0.1)
            idx += 1

    def issue_dining_token(self, name: str):
        """Issue dining token via the agent."""
        with self._lock:
            if self._agent:
                return self._agent.issue_dining_token(name)
        return None

    def _trigger_greeting(self, guest_name: str):
        """
        Trigger voice concierge greeting for recognized guest.
        Fetches guest context (flight, tokens) and initiates async greeting.
        Non-blocking: runs in background thread.
        """
        try:
            # Look up guest in MongoDB
            guest = guest_profiles_col().find_one({"full_name": guest_name})
            if not guest:
                print(f"[Concierge] Guest not found: {guest_name}")
                return

            guest_id = str(guest.get("_id", "unknown"))

            # Check cooldown: don't greet same guest multiple times per minute
            last_greeting = self._guest_greeting_cache.get(guest_id, 0)
            if time.time() - last_greeting < self._greeting_cooldown:
                print(f"[Concierge] Cooldown: skipping greeting for {guest_name}")
                return

            # Update cooldown timestamp
            self._guest_greeting_cache[guest_id] = time.time()

            # Extract guest context
            flight_gate = guest.get("gate", None)
            flight_time = guest.get("flight_time", None)  # e.g., "2 hours 15 minutes"
            
            # Fetch dining tokens (from a dining_tokens collection, if available)
            dining_tokens = {}
            try:
                from db import dining_tokens_col
                tokens = list(dining_tokens_col().find(
                    {"guest_id": guest_id, "redeemed_at": None}
                ))
                # Count by type
                for token in tokens:
                    token_type = token.get("type", "unknown")
                    dining_tokens[token_type] = dining_tokens.get(token_type, 0) + 1
            except Exception:
                # Table may not exist yet
                pass

            print(f"[Concierge] Triggering greeting: {guest_name} (gate={flight_gate}, time={flight_time}, tokens={dining_tokens})")

            # Asynchronously generate and queue greeting (non-blocking)
            self._concierge.generate_greeting_async(
                guest_id=guest_id,
                guest_name=guest_name,
                flight_gate=flight_gate,
                flight_time=flight_time,
                dining_tokens=dining_tokens,
            )

        except Exception as e:
            print(f"[Concierge] Error triggering greeting: {e}")

    def get_registered_members(self) -> list:
        with self._lock:
            if self._agent:
                return self._agent.engine.get_registered_names()
        return []

    def get_event_log(self, limit=50) -> list:
        return self._event_log[-limit:]

    def get_raw_frame(self):
        """
        Get the latest RAW camera frame (numpy array).
        Used by face registration to borrow the agent's camera.
        """
        return self._raw_frame

    def get_frame_jpeg(self) -> bytes:
        """Get the latest JPEG-encoded frame for MJPEG streaming."""
        return self._latest_frame_jpeg

    def generate_video_feed(self):
        """
        Generator for MJPEG video streaming.
        Uses threading.Event for instant frame delivery.
        """
        while self._running:
            self._new_frame_event.wait(timeout=0.04)  # ~25fps max wait
            self._new_frame_event.clear()

            frame = self._latest_frame_jpeg
            if frame:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                )


# Singleton instance
_agent_service = AgentService()


def get_agent_service() -> AgentService:
    """Get the singleton AgentService instance."""
    return _agent_service
