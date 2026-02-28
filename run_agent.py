"""
run_agent.py — Standalone Agent Runner with OpenCV UI

Demonstrates the LoungeAgent running autonomously:
  - Feed it camera frames
  - It makes decisions on its own (no button press for access)
  - Renders events and state on screen
  - Switch modes with keyboard (G/R/I/X)

Usage:
  python run_agent.py              # default: GATE mode
  python run_agent.py --mode gate
  python run_agent.py --mode reception
  python run_agent.py --mode entry
  python run_agent.py --mode exit
"""

import cv2
import numpy as np
import argparse
import time
from datetime import datetime

from lounge_agent import LoungeAgent, AgentMode, EventType

# =====================================================
# COLORS
# =====================================================
COLOR_GREEN = (0, 200, 0)
COLOR_RED = (0, 0, 255)
COLOR_YELLOW = (0, 220, 255)
COLOR_WHITE = (255, 255, 255)
COLOR_DARK = (40, 40, 40)
COLOR_PANEL = (30, 30, 30)
COLOR_ACCENT = (255, 160, 50)
COLOR_ORANGE = (0, 140, 255)
COLOR_BLUE = (255, 120, 0)
COLOR_TEAL = (200, 180, 0)

MODE_COLORS = {
    AgentMode.GATE: COLOR_ACCENT,
    AgentMode.RECEPTION: COLOR_BLUE,
    AgentMode.ENTRY: COLOR_GREEN,
    AgentMode.EXIT: COLOR_RED,
}

EVENT_COLORS = {
    EventType.ACCESS_GRANTED: COLOR_GREEN,
    EventType.ACCESS_DENIED: COLOR_RED,
    EventType.FACE_RECOGNIZED: COLOR_GREEN,
    EventType.FACE_UNKNOWN: COLOR_RED,
    EventType.ALERT_UNKNOWN: COLOR_RED,
    EventType.ALERT_ALREADY_INSIDE: COLOR_YELLOW,
    EventType.PERSON_ENTERED: COLOR_TEAL,
    EventType.PERSON_EXITED: COLOR_ORANGE,
    EventType.DINING_TOKEN_ISSUED: COLOR_ACCENT,
}


# =====================================================
# UI DRAWING
# =====================================================
def draw_face_box(frame, face, mode):
    """Draw face bounding box with label and confidence."""
    name = face["name"]
    conf = face["confidence"]
    x, y, w, h = face["bbox"]
    stable = face.get("stable", False)

    if name == "Unknown":
        color = COLOR_RED
        border = 2
    elif stable:
        color = COLOR_GREEN
        border = 2
    else:
        color = COLOR_YELLOW
        border = 1

    # Draw box
    cv2.rectangle(frame, (x, y), (x + w, y + h), color, border)

    # Label background
    label_h = 28
    cv2.rectangle(frame, (x, y - label_h), (x + w, y), color, -1)

    # Label text
    if name != "Unknown":
        text = f"{name} {conf}%"
    else:
        text = "UNKNOWN"
    cv2.putText(frame, text, (x + 4, y - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, COLOR_WHITE, 1)

    # Status indicator
    if name == "Unknown":
        cv2.putText(frame, "!", (x + w - 18, y + 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, COLOR_RED, 2)
    elif stable:
        cv2.putText(frame, "OK", (x + w - 30, y + 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, COLOR_GREEN, 2)


def build_panel(height, panel_width, agent: LoungeAgent, events, event_log):
    """Build the right-side agent status panel."""
    panel = np.zeros((height, panel_width, 3), dtype=np.uint8)
    panel[:] = COLOR_PANEL

    y = 10
    mode_color = MODE_COLORS.get(agent.mode, COLOR_ACCENT)

    # ── Header ──
    cv2.putText(panel, "LOUNGE AGENT", (15, y + 25),
                cv2.FONT_HERSHEY_DUPLEX, 0.7, COLOR_ACCENT, 1)
    y += 35

    # Mode indicator
    cv2.rectangle(panel, (10, y), (panel_width - 10, y + 30), mode_color, -1)
    cv2.putText(panel, f"MODE: {agent.mode.value.upper()}", (18, y + 21),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, COLOR_WHITE, 2)
    y += 40

    # Date/time
    now = datetime.now()
    cv2.putText(panel, now.strftime("%d %b %Y  %H:%M:%S"), (15, y + 15),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (180, 180, 180), 1)
    y += 25

    # ── Divider ──
    cv2.line(panel, (10, y), (panel_width - 10, y), (80, 80, 80), 1)
    y += 12

    # ── Occupancy ──
    cv2.putText(panel, "OCCUPANCY", (15, y + 15),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, COLOR_YELLOW, 1)
    y += 20
    occ = agent.get_occupancy()
    cv2.putText(panel, f"  Inside: {len(occ)}", (15, y + 15),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, COLOR_WHITE, 1)
    y += 22
    for name in sorted(occ.keys()):
        cv2.circle(panel, (25, y + 8), 5, COLOR_GREEN, -1)
        cv2.putText(panel, name, (38, y + 13),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (150, 255, 150), 1)
        y += 20
        if y > height // 2 - 60:
            cv2.putText(panel, f"  +{len(occ) - 3} more", (25, y + 13),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, (120, 120, 120), 1)
            y += 18
            break

    y += 8
    cv2.line(panel, (10, y), (panel_width - 10, y), (80, 80, 80), 1)
    y += 12

    # ── Live Faces ──
    faces = agent.get_current_faces()
    recognized = [f for f in faces if f["name"] != "Unknown"]
    unknowns = [f for f in faces if f["name"] == "Unknown"]

    cv2.putText(panel, "LIVE DETECTION", (15, y + 15),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, COLOR_YELLOW, 1)
    y += 25
    cv2.circle(panel, (25, y + 7), 5, COLOR_GREEN, -1)
    cv2.putText(panel, f"Recognized: {len(recognized)}", (38, y + 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, COLOR_WHITE, 1)
    y += 18
    cv2.circle(panel, (25, y + 7), 5, COLOR_RED, -1)
    cv2.putText(panel, f"Unknown: {len(unknowns)}", (38, y + 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, COLOR_WHITE, 1)
    y += 25

    y += 5
    cv2.line(panel, (10, y), (panel_width - 10, y), (80, 80, 80), 1)
    y += 12

    # ── Recent Events (scrolling log) ──
    cv2.putText(panel, "AGENT EVENTS", (15, y + 15),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, COLOR_YELLOW, 1)
    y += 28

    visible_events = event_log[-8:]  # last 8 events
    for evt in reversed(visible_events):
        color = EVENT_COLORS.get(evt.event_type, COLOR_WHITE)
        tag = evt.event_type.value.replace("_", " ").upper()[:18]
        text = f"{evt.timestamp} {tag}"
        cv2.putText(panel, text, (15, y + 13),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.32, color, 1)
        y += 14
        if evt.name != "Unknown":
            cv2.putText(panel, f"  -> {evt.name}", (15, y + 13),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.32, (200, 200, 200), 1)
            y += 14
        if y > height - 100:
            break

    # ── Today's Attendance ──
    y = max(y + 10, height - 140)
    cv2.line(panel, (10, y), (panel_width - 10, y), (80, 80, 80), 1)
    y += 12
    att = agent.get_todays_attendance()
    cv2.putText(panel, f"TODAY'S RECORDS: {len(att)}", (15, y + 15),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, COLOR_YELLOW, 1)
    y += 22
    for name in sorted(att)[:4]:
        cv2.putText(panel, f"  {name}", (15, y + 13),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (150, 255, 150), 1)
        y += 16

    # ── Controls ──
    y_bottom = height - 42
    cv2.line(panel, (10, y_bottom - 8), (panel_width - 10, y_bottom - 8), (80, 80, 80), 1)
    cv2.putText(panel, "[G]ate [R]ecep [I]n [X]out [Q]uit",
                (10, y_bottom + 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.32, COLOR_ACCENT, 1)
    cv2.putText(panel, "[D] Issue dining token",
                (10, y_bottom + 26),
                cv2.FONT_HERSHEY_SIMPLEX, 0.32, (150, 150, 150), 1)

    return panel


# =====================================================
# NOTIFICATION OVERLAY
# =====================================================
class NotificationOverlay:
    """Shows brief on-screen notifications for agent events."""

    def __init__(self):
        self.messages = []  # list of {text, color, until}

    def add(self, text, color=COLOR_GREEN, duration=3.0):
        self.messages.append({
            "text": text,
            "color": color,
            "until": time.time() + duration,
        })

    def draw(self, frame):
        now = time.time()
        self.messages = [m for m in self.messages if m["until"] > now]
        h, w = frame.shape[:2]
        y = h - 30
        for msg in reversed(self.messages[-3:]):
            alpha = min(1.0, (msg["until"] - now) / 1.0)
            color = tuple(int(c * alpha) for c in msg["color"])
            cv2.rectangle(frame, (5, y - 22), (w - 5, y + 5), (0, 0, 0), -1)
            cv2.putText(frame, msg["text"], (12, y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
            y -= 30


# =====================================================
# MAIN
# =====================================================
def main():
    parser = argparse.ArgumentParser(description="Airport Lounge Agent")
    parser.add_argument("--mode", choices=["gate", "reception", "entry", "exit"],
                        default="gate", help="Agent operating mode")
    parser.add_argument("--camera", type=int, default=0, help="Camera index")
    args = parser.parse_args()

    mode_map = {
        "gate": AgentMode.GATE,
        "reception": AgentMode.RECEPTION,
        "entry": AgentMode.ENTRY,
        "exit": AgentMode.EXIT,
    }

    # Initialize agent
    agent = LoungeAgent(mode=mode_map[args.mode])
    notif = NotificationOverlay()
    event_log = []  # persistent log across frames

    # Open camera
    video = cv2.VideoCapture(args.camera)
    if not video.isOpened():
        print("Error: Could not open camera.")
        return

    PANEL_WIDTH = 300

    print("\n" + "=" * 60)
    print("  AIRPORT LOUNGE ACCESS VERIFICATION AGENT")
    print("=" * 60)
    print(f"  Mode      : {agent.mode.value.upper()}")
    print(f"  Camera    : {args.camera}")
    print(f"  Members   : {len(agent.engine.get_registered_names())}")
    print("  Controls  :")
    print("    [G] Gate mode    [R] Reception mode")
    print("    [I] Entry mode   [X] Exit mode")
    print("    [D] Issue dining token to recognized faces")
    print("    [Q] Quit")
    print("=" * 60 + "\n")

    while True:
        ret, frame = video.read()
        if not ret or frame is None:
            continue

        # === AGENT PROCESSES FRAME AUTONOMOUSLY ===
        events = agent.process_frame(frame)

        # Collect events into persistent log
        for evt in events:
            event_log.append(evt)
            # Show notifications for key events
            if evt.event_type == EventType.ACCESS_GRANTED:
                notif.add(f"ACCESS GRANTED: {evt.name}", COLOR_GREEN)
                print(f"  [AGENT] ACCESS GRANTED -> {evt.name}")
            elif evt.event_type == EventType.ACCESS_DENIED:
                notif.add(f"ACCESS DENIED", COLOR_RED)
                print(f"  [AGENT] ACCESS DENIED (unknown person)")
            elif evt.event_type == EventType.ALERT_UNKNOWN:
                notif.add(f"ALERT: Unknown at gate!", COLOR_RED, 4.0)
                print(f"  [AGENT] ALERT: Unknown person detected")
            elif evt.event_type == EventType.PERSON_ENTERED:
                notif.add(f"ENTERED: {evt.name}", COLOR_TEAL)
                print(f"  [AGENT] ENTRY -> {evt.name}")
            elif evt.event_type == EventType.PERSON_EXITED:
                dur = evt.details.get("duration_minutes", "?")
                notif.add(f"EXITED: {evt.name} ({dur}min)", COLOR_ORANGE)
                print(f"  [AGENT] EXIT -> {evt.name} ({dur} min)")
            elif evt.event_type == EventType.ALERT_ALREADY_INSIDE:
                notif.add(f"{evt.name} already inside", COLOR_YELLOW)
            elif evt.event_type == EventType.DINING_TOKEN_ISSUED:
                tok = evt.details.get("total_tokens", 0)
                notif.add(f"DINING TOKEN #{tok}: {evt.name}", COLOR_ACCENT)

        # Keep event log manageable
        if len(event_log) > 200:
            event_log = event_log[-100:]

        # === DRAW FACES ===
        faces = agent.get_current_faces()
        for face in faces:
            draw_face_box(frame, face, agent.mode)

        # === DRAW NOTIFICATIONS ===
        notif.draw(frame)

        # === DRAW MODE INDICATOR ON FRAME ===
        mode_color = MODE_COLORS.get(agent.mode, COLOR_ACCENT)
        cv2.rectangle(frame, (5, 5), (180, 32), mode_color, -1)
        cv2.putText(frame, f"AGENT: {agent.mode.value.upper()}", (10, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, COLOR_WHITE, 2)

        # === BUILD SIDE PANEL ===
        h_frame, w_frame = frame.shape[:2]
        panel = build_panel(h_frame, PANEL_WIDTH, agent, events, event_log)
        display = np.hstack([frame, panel])

        cv2.imshow("Airport Lounge Agent", display)
        k = cv2.waitKey(1) & 0xFF

        # --- Mode switching ---
        if k == ord('g'):
            agent.set_mode(AgentMode.GATE)
            notif.add("Mode: GATE (multi-face)", mode_color)
        elif k == ord('r'):
            agent.set_mode(AgentMode.RECEPTION)
            notif.add("Mode: RECEPTION (single)", COLOR_BLUE)
        elif k == ord('i'):
            agent.set_mode(AgentMode.ENTRY)
            notif.add("Mode: ENTRY (incoming)", COLOR_GREEN)
        elif k == ord('x'):
            agent.set_mode(AgentMode.EXIT)
            notif.add("Mode: EXIT (outgoing)", COLOR_RED)

        # --- Dining token ---
        elif k == ord('d'):
            for face in faces:
                if face["name"] != "Unknown" and face.get("stable"):
                    agent.issue_dining_token(face["name"])

        elif k == ord('q'):
            break

    video.release()
    cv2.destroyAllWindows()

    # Print session summary
    print("\n" + "=" * 60)
    print("  SESSION SUMMARY")
    print("=" * 60)
    state = agent.get_state()
    print(f"  Mode: {state['mode']}")
    print(f"  People still inside: {state['occupancy_count']}")
    for name in state['occupancy']:
        print(f"    - {name}")
    print(f"  Attendance records today: {len(state['todays_attendance'])}")
    print(f"  Total events logged: {state['session_log_count']}")
    if state['dining_tokens']:
        print(f"  Dining tokens issued:")
        for name, count in state['dining_tokens'].items():
            print(f"    - {name}: {count}")
    print("=" * 60)


if __name__ == "__main__":
    main()
