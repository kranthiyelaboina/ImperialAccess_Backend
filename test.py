import cv2
import pickle
import numpy as np
import os
import csv
from datetime import datetime
from collections import Counter, defaultdict
import time

from win32com.client import Dispatch

# =====================================================
# CONFIGURATION
# =====================================================
# Face detection (YuNet)
DETECTION_SCORE_THRESHOLD = 0.75  # minimum face detection confidence

# Face recognition thresholds (SFace cosine similarity)
# SFace cosine scores: same person ~0.3-0.5+, different person <0.2
COSINE_MATCH_THRESHOLD = 0.363    # OpenCV's recommended threshold for cosine
SECOND_BEST_MARGIN = 0.04         # margin over second-best to avoid confusion

# Temporal smoothing (multi-frame voting)
TRACK_HISTORY_FRAMES = 15     # number of frames to track each face
MIN_CONSISTENT_FRAMES = 4     # need at least this many agreeing frames to confirm identity
FACE_MATCH_IOU_THRESHOLD = 0.25  # IoU threshold to match faces across frames
EMBEDDING_SMOOTH_ALPHA = 0.5  # exponential moving average weight for embedding smoothing

# Display
NOTIFICATION_DISPLAY_SEC = 3

# Model paths
YUNET_MODEL = "data/face_detection_yunet_2023mar.onnx"
SFACE_MODEL = "data/face_recognition_sface_2021dec.onnx"

# --- Colors (BGR) ---
COLOR_GREEN = (0, 200, 0)
COLOR_RED = (0, 0, 255)
COLOR_YELLOW = (0, 220, 255)
COLOR_WHITE = (255, 255, 255)
COLOR_DARK = (40, 40, 40)
COLOR_PANEL = (30, 30, 30)
COLOR_ACCENT = (255, 160, 50)
COLOR_ORANGE = (0, 140, 255)


# =====================================================
# LOAD MODELS — YuNet + SFace
# =====================================================
print("Loading YuNet face detector...")
detector = cv2.FaceDetectorYN.create(YUNET_MODEL, "", (320, 320), DETECTION_SCORE_THRESHOLD, 0.3, 5000)

print("Loading SFace face recognizer...")
recognizer = cv2.FaceRecognizerSF.create(SFACE_MODEL, "")


def speak_text(text):
    """Text-to-speech using Windows SAPI."""
    try:
        voice = Dispatch("SAPI.SpVoice")
        voice.Speak(text)
    except Exception:
        pass


def detect_faces(frame):
    """
    Detect faces using YuNet. Returns raw detection array.
    Each row: [x, y, w, h, ..landmarks.., score]
    """
    h, w = frame.shape[:2]
    detector.setInputSize((w, h))
    _, faces = detector.detect(frame)
    return faces if faces is not None else np.array([])


def get_face_embedding(frame, face):
    """
    Extract 128-D face embedding using SFace with landmark-based alignment.
    This alignment is what makes SFace so much more accurate than OpenFace.
    """
    try:
        aligned = recognizer.alignCrop(frame, face)
        embedding = recognizer.feature(aligned)
        return embedding.flatten()
    except Exception:
        return None


def cosine_similarity(a, b):
    """Compute cosine similarity between two vectors."""
    dot = np.dot(a, b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def load_face_database():
    """
    Load registered face data and compute per-person mean embeddings.
    Returns dict: {name: [list of embedding vectors]}
    """
    with open('data/names.pkl', 'rb') as f:
        labels = pickle.load(f)
    with open('data/faces_data.pkl', 'rb') as f:
        embeddings = pickle.load(f)

    # Group embeddings by person
    db = defaultdict(list)
    for label, emb in zip(labels, embeddings):
        db[label].append(emb)

    people = sorted(db.keys())
    total = sum(len(v) for v in db.values())
    print(f"Loaded {total} embeddings for {len(people)} people: {', '.join(people)}")

    return db, people


def classify_face(embedding, face_db):
    """
    Classify a face using cosine similarity against the SFace database.
    Returns (label, confidence_pct).

    SFace cosine similarity ranges:
    - Same person: typically 0.3 - 0.6+
    - Different person: typically < 0.2
    - OpenCV recommended threshold: 0.363

    The best match must:
    1. Exceed COSINE_MATCH_THRESHOLD
    2. Be SECOND_BEST_MARGIN better than the second-best match
    """
    if embedding is None:
        return "Unknown", 0

    scores = {}
    for name, embeddings_list in face_db.items():
        # Compute similarity to each stored embedding, take the top-K average
        sims = [cosine_similarity(embedding, stored) for stored in embeddings_list]
        sims.sort(reverse=True)
        top_k = min(15, len(sims))
        scores[name] = np.mean(sims[:top_k])

    if not scores:
        return "Unknown", 0

    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    best_name, best_score = sorted_scores[0]

    # Check absolute threshold
    if best_score < COSINE_MATCH_THRESHOLD:
        return "Unknown", 0

    # Check margin over second-best (if there are multiple people)
    if len(sorted_scores) > 1:
        second_score = sorted_scores[1][1]
        margin = best_score - second_score
        if margin < SECOND_BEST_MARGIN:
            return "Unknown", 0

    # Map SFace similarity (0.363-0.7) to display confidence (60-99%)
    confidence = int(np.clip((best_score - 0.2) / 0.5 * 100, 30, 99))
    return best_name, confidence


# =====================================================
# FACE TRACKER — Temporal Smoothing
# =====================================================
def compute_iou(box1, box2):
    """Compute Intersection over Union between two (x,y,w,h) boxes."""
    x1, y1, w1, h1 = box1
    x2, y2, w2, h2 = box2

    xa = max(x1, x2)
    ya = max(y1, y2)
    xb = min(x1 + w1, x2 + w2)
    yb = min(y1 + h1, y2 + h2)

    inter = max(0, xb - xa) * max(0, yb - ya)
    union = w1 * h1 + w2 * h2 - inter
    return inter / union if union > 0 else 0


class FaceTracker:
    """
    Tracks faces across frames and applies temporal smoothing.
    Uses both label voting AND embedding averaging for stable results.
    """

    def __init__(self):
        self.tracks = {}   # track_id -> {box, history, last_seen, smooth_embedding}
        self.next_id = 0

    def update(self, detections, face_db):
        """
        Update tracker with current frame detections.
        detections: list of (label, raw_confidence, x, y, w, h, embedding)
        Returns: list of (smoothed_label, smoothed_confidence, x, y, w, h)
        """
        current_time = time.time()
        used_tracks = set()
        results = []

        # Match detections to existing tracks by IoU
        for det in detections:
            label, conf, x, y, w, h, embedding = det
            box = (x, y, w, h)
            best_track_id = None
            best_iou = FACE_MATCH_IOU_THRESHOLD

            for tid, track in self.tracks.items():
                if tid in used_tracks:
                    continue
                iou = compute_iou(box, track['box'])
                if iou > best_iou:
                    best_iou = iou
                    best_track_id = tid

            if best_track_id is not None:
                # Update existing track
                track = self.tracks[best_track_id]
                track['box'] = box
                track['history'].append(label)
                track['last_seen'] = current_time
                # Exponential moving average of embeddings for smoother classification
                if embedding is not None:
                    if track['smooth_embedding'] is not None:
                        track['smooth_embedding'] = (
                            EMBEDDING_SMOOTH_ALPHA * embedding +
                            (1 - EMBEDDING_SMOOTH_ALPHA) * track['smooth_embedding']
                        )
                    else:
                        track['smooth_embedding'] = embedding.copy()
                # Keep only recent history
                if len(track['history']) > TRACK_HISTORY_FRAMES:
                    track['history'] = track['history'][-TRACK_HISTORY_FRAMES:]
                used_tracks.add(best_track_id)
            else:
                # Create new track
                best_track_id = self.next_id
                self.next_id += 1
                self.tracks[best_track_id] = {
                    'box': box,
                    'history': [label],
                    'last_seen': current_time,
                    'smooth_embedding': embedding.copy() if embedding is not None else None
                }
                used_tracks.add(best_track_id)

            # Re-classify using smoothed embedding for better accuracy
            track = self.tracks[best_track_id]
            if track['smooth_embedding'] is not None and len(track['history']) >= 2:
                smooth_label, smooth_conf = classify_face(track['smooth_embedding'], face_db)
                # Use smooth result if it's more confident
                if smooth_conf > conf:
                    label = smooth_label
                    track['history'][-1] = label  # update last history entry

            history = track['history']
            vote_counts = Counter(history)

            # Determine smoothed label from history
            if len(history) >= 2:
                top_label, top_count = vote_counts.most_common(1)[0]
                if top_label != "Unknown" and top_count >= MIN_CONSISTENT_FRAMES:
                    smoothed_label = top_label
                    smoothed_conf = int((top_count / len(history)) * 100)
                elif top_label != "Unknown" and top_count >= 2:
                    # Tentative — show as pending with lower confidence
                    smoothed_label = top_label
                    smoothed_conf = int((top_count / len(history)) * 50)
                else:
                    smoothed_label = "Unknown"
                    smoothed_conf = 0
            else:
                # First frame — show raw result but at low confidence
                smoothed_label = "Unknown"
                smoothed_conf = 0

            results.append((smoothed_label, smoothed_conf, x, y, w, h))

        # Remove stale tracks (not seen for 1.5 seconds)
        stale = [tid for tid, t in self.tracks.items()
                 if current_time - t['last_seen'] > 1.5]
        for tid in stale:
            del self.tracks[tid]

        return results


def get_attendance_file_path():
    """Get today's attendance CSV path."""
    date = datetime.now().strftime("%d-%m-%Y")
    os.makedirs("Attendance", exist_ok=True)
    return f"Attendance/Attendance_{date}.csv"


def load_existing_attendance(filepath):
    """Load names already recorded today to avoid duplicates."""
    recorded = set()
    if os.path.isfile(filepath):
        with open(filepath, 'r') as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if row:
                    recorded.add(row[0])
    return recorded


def save_attendance(filepath, attendance_list):
    """Save attendance records to CSV, avoiding duplicates."""
    recorded = load_existing_attendance(filepath)
    file_exists = os.path.isfile(filepath)
    new_entries = []

    for name, timestamp in attendance_list:
        if name not in recorded and name != "Unknown":
            new_entries.append([name, timestamp])
            recorded.add(name)

    if not new_entries:
        return 0

    with open(filepath, "a", newline='') as csvfile:
        writer = csv.writer(csvfile)
        if not file_exists:
            writer.writerow(['NAME', 'TIME'])
        writer.writerows(new_entries)

    return len(new_entries)


def draw_rounded_rect(img, pt1, pt2, color, thickness, radius=10):
    """Draw a rounded rectangle (approximate with filled rect + circles)."""
    x1, y1 = pt1
    x2, y2 = pt2
    cv2.rectangle(img, (x1 + radius, y1), (x2 - radius, y2), color, thickness)
    cv2.rectangle(img, (x1, y1 + radius), (x2, y2 - radius), color, thickness)
    if thickness == -1:
        cv2.rectangle(img, (x1 + radius, y1), (x2 - radius, y2), color, -1)
        cv2.rectangle(img, (x1, y1 + radius), (x2, y2 - radius), color, -1)
        cv2.circle(img, (x1 + radius, y1 + radius), radius, color, -1)
        cv2.circle(img, (x2 - radius, y1 + radius), radius, color, -1)
        cv2.circle(img, (x1 + radius, y2 - radius), radius, color, -1)
        cv2.circle(img, (x2 - radius, y2 - radius), radius, color, -1)


def build_side_panel(height, panel_width, current_faces, today_attendance, notification, known_people):
    """Build the right-side info panel."""
    panel = np.zeros((height, panel_width, 3), dtype=np.uint8)
    panel[:] = COLOR_PANEL

    y = 10
    # Title
    cv2.putText(panel, "ATTENDANCE SYSTEM", (15, y + 25),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, COLOR_ACCENT, 2)
    y += 45

    # Date & Time
    now = datetime.now()
    cv2.putText(panel, now.strftime("%d %b %Y"), (15, y + 18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, COLOR_WHITE, 1)
    y += 25
    cv2.putText(panel, now.strftime("%H:%M:%S"), (15, y + 18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 180, 180), 1)
    y += 35

    # Divider
    cv2.line(panel, (10, y), (panel_width - 10, y), (80, 80, 80), 1)
    y += 15

    # Live Detection
    recognized = [f for f in current_faces if f[0] != "Unknown"]
    unknowns = [f for f in current_faces if f[0] == "Unknown"]

    cv2.putText(panel, "LIVE DETECTION", (15, y + 18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, COLOR_YELLOW, 1)
    y += 30

    # Detected faces count with colored indicators
    cv2.circle(panel, (25, y + 8), 6, COLOR_GREEN, -1)
    cv2.putText(panel, f"Recognized: {len(recognized)}", (40, y + 14),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, COLOR_WHITE, 1)
    y += 25
    cv2.circle(panel, (25, y + 8), 6, COLOR_RED, -1)
    cv2.putText(panel, f"Unknown: {len(unknowns)}", (40, y + 14),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, COLOR_WHITE, 1)
    y += 30

    # Currently visible names
    for name, conf, _, _, _, _ in current_faces:
        if name != "Unknown":
            color = COLOR_GREEN
            txt = f"{name} ({conf}%)"
        else:
            color = COLOR_RED
            txt = "Unknown Person"
        cv2.putText(panel, txt, (20, y + 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, 1)
        y += 22
        if y > height - 200:
            break

    y += 10
    cv2.line(panel, (10, y), (panel_width - 10, y), (80, 80, 80), 1)
    y += 15

    # Today's attendance
    cv2.putText(panel, "TODAY'S ATTENDANCE", (15, y + 18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, COLOR_YELLOW, 1)
    y += 30

    if today_attendance:
        for name in sorted(today_attendance):
            cv2.putText(panel, f"  {name}", (15, y + 14),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (150, 255, 150), 1)
            y += 20
            if y > height - 100:
                cv2.putText(panel, f"  +{len(today_attendance) - 5} more...", (15, y + 14),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.42, (150, 150, 150), 1)
                break
    else:
        cv2.putText(panel, "  No records yet", (15, y + 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (120, 120, 120), 1)
    y += 30

    # Notification area
    if notification and notification["until"] > datetime.now().timestamp():
        notif_color = notification.get("color", COLOR_GREEN)
        draw_rounded_rect(panel, (10, y), (panel_width - 10, y + 40), notif_color, -1, 5)
        cv2.putText(panel, notification["text"], (18, y + 26),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, COLOR_WHITE, 1)
        y += 50

    # Controls at bottom
    y_bottom = height - 50
    cv2.line(panel, (10, y_bottom - 10), (panel_width - 10, y_bottom - 10), (80, 80, 80), 1)
    cv2.putText(panel, "[O] Take Attendance", (15, y_bottom + 5),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, COLOR_ACCENT, 1)
    cv2.putText(panel, "[Q] Quit", (15, y_bottom + 25),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, (150, 150, 150), 1)

    return panel


# ============================================================
# MAIN
# ============================================================
video = cv2.VideoCapture(0)
if not video.isOpened():
    print("Error: Could not open camera.")
    exit(1)

face_db, known_people = load_face_database()
tracker = FaceTracker()

PANEL_WIDTH = 280
notification = None

print("\nFace Recognition Attendance System (DNN + Temporal Smoothing)")
print("=============================================================")
print("  [O] — Record attendance for recognized faces")
print("  [Q] — Quit\n")

while True:
    ret, frame = video.read()
    if not ret or frame is None:
        continue

    # --- Detect faces using YuNet (with landmarks for alignment) ---
    detected_faces = detect_faces(frame)

    # --- Classify each detected face ---
    raw_detections = []
    for i in range(len(detected_faces)):
        face = detected_faces[i]
        x, y, w, h = int(face[0]), int(face[1]), int(face[2]), int(face[3])
        det_score = face[-1]

        embedding = get_face_embedding(frame, face)
        label, confidence = classify_face(embedding, face_db)
        raw_detections.append((label, confidence, x, y, w, h, embedding))

    # --- Apply temporal smoothing (multi-frame voting + embedding averaging) ---
    current_faces = tracker.update(raw_detections, face_db)

    # --- Draw all faces ---
    for (label, confidence, x, y, w, h) in current_faces:
        if label == "Unknown":
            color = COLOR_RED
        else:
            color = COLOR_GREEN

        # Draw face box
        cv2.rectangle(frame, (x, y), (x+w, y+h), color, 2)

        # Label background
        label_h = 30
        cv2.rectangle(frame, (x, y - label_h), (x + w, y), color, -1)

        # Label text
        if label != "Unknown":
            display_text = f"{label} {confidence}%"
        else:
            display_text = "Unknown"
        cv2.putText(frame, display_text, (x + 5, y - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, COLOR_WHITE, 2)

        # For unknown: draw a warning icon indicator
        if label == "Unknown":
            cv2.putText(frame, "?", (x + w - 20, y + 25),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, COLOR_RED, 2)

    # Load today's attendance for the panel
    filepath = get_attendance_file_path()
    today_attendance = load_existing_attendance(filepath)

    # Build UI: camera feed + side panel
    h_frame, w_frame = frame.shape[:2]
    panel = build_side_panel(h_frame, PANEL_WIDTH, current_faces, today_attendance, notification, known_people)
    display = np.hstack([frame, panel])

    cv2.imshow("Face Recognition Attendance", display)
    k = cv2.waitKey(1)

    if k == ord('o'):
        timestamp = datetime.now().strftime("%H:%M:%S")
        attendance_list = [(name, timestamp) for name, _, _, _, _, _ in current_faces]

        saved = save_attendance(filepath, attendance_list)
        recognized = [f for f in current_faces if f[0] != "Unknown"]
        unknowns = [f for f in current_faces if f[0] == "Unknown"]

        if saved > 0:
            names_saved = [n for n, t in attendance_list if n != "Unknown"]
            msg = f"Recorded: {', '.join(names_saved[:3])}"
            notification = {"text": msg, "until": datetime.now().timestamp() + NOTIFICATION_DISPLAY_SEC, "color": COLOR_GREEN}
            speak_text(f"Attendance taken for {', '.join(names_saved[:5])}")
            print(f"[{timestamp}] Recorded {saved}: {', '.join(names_saved)}")
        elif unknowns and not recognized:
            notification = {"text": "No recognized faces!", "until": datetime.now().timestamp() + NOTIFICATION_DISPLAY_SEC, "color": COLOR_RED}
            speak_text("No recognized faces to record.")
            print(f"[{timestamp}] No recognized faces in frame.")
        else:
            notification = {"text": "Already recorded today", "until": datetime.now().timestamp() + NOTIFICATION_DISPLAY_SEC, "color": COLOR_YELLOW}
            speak_text("Attendance already recorded.")
            print(f"[{timestamp}] Already recorded today.")

    if k == ord('q'):
        break

video.release()
cv2.destroyAllWindows()
print("Done.")

