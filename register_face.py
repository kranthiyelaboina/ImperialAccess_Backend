"""
register_face.py — Face Registration for the Lounge Agent

Uses the shared FaceEngine to register new premium members.
Extracts augmented embeddings and saves them to the database.

Usage:
  python register_face.py                 # direct camera access
  python register_face.py --camera 1      # alternate camera index
  python register_face.py --api           # use backend API (if agent is running)
"""

import cv2
import numpy as np
import argparse
import sys
import time

from face_engine import FaceEngine


# =====================================================
# CONFIGURATION
# =====================================================
MAX_SAMPLES = 50
CAPTURE_EVERY_N_FRAMES = 2
API_BASE = "http://127.0.0.1:5000"


def open_camera(index=0):
    """
    Try multiple OpenCV backends to open the camera.
    On Windows, DirectShow (CAP_DSHOW) is fastest and most reliable,
    but if the agent already holds the camera with DirectShow we fall
    back to MSMF, then to the default backend.
    """
    backends = [
        ("DirectShow (DSHOW)", cv2.CAP_DSHOW),
        ("MSMF", cv2.CAP_MSMF),
        ("Default", cv2.CAP_ANY),
    ]
    for name, backend in backends:
        print(f"  Trying {name} backend …", end=" ")
        cap = cv2.VideoCapture(index, backend)
        if cap.isOpened():
            # Verify we can actually grab a frame
            ret, _ = cap.read()
            if ret:
                print("OK")
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                return cap
            else:
                cap.release()
                print("opened but can't read frames")
        else:
            print("failed to open")
    return None


def register_via_api(name: str):
    """
    Register a face through the running Flask backend's API.
    The backend captures from the agent's camera loop — no camera
    conflict. The live feed is visible at /api/agent/register_feed.
    """
    try:
        import requests
    except ImportError:
        print("Error: 'requests' package is required for --api mode.")
        print("  Install it:  pip install requests")
        return

    print(f"\n[API mode] Registering '{name}' via backend at {API_BASE}")
    print(f"  Watch the live feed: {API_BASE}/api/agent/register_feed\n")

    # We need to authenticate first (login as admin or use a token).
    # For simplicity, do a direct POST. The backend register_face
    # endpoint requires login — let's login as admin first.
    session = requests.Session()

    # Login as admin
    login_resp = session.post(f"{API_BASE}/api/login", json={
        "username": "admin", "password": "admin123", "role": "admin",
    })
    if not login_resp.ok or not login_resp.json().get("success"):
        print("Error: Could not log in to backend. Is the server running?")
        print(f"  Response: {login_resp.text[:200]}")
        return

    token = login_resp.json().get("token", "")

    # Trigger face registration
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    resp = session.post(f"{API_BASE}/api/register_guest/face", json={
        "guest_id": "cli_register",
        "name": name,
    }, headers=headers, timeout=60)

    if resp.ok and resp.json().get("success"):
        data = resp.json()
        print(f"\nSuccess! Registered '{name}' with {data.get('embeddings_collected', '?')} embeddings.")
    else:
        err = resp.json().get("error", resp.text[:200]) if resp.ok else resp.text[:200]
        print(f"\nRegistration failed: {err}")


def register_direct(camera_index: int):
    """Register a face using direct camera access (original method)."""

    engine = FaceEngine()

    name = input("Enter passenger/member name: ").strip()
    if not name:
        print("Error: Name cannot be empty.")
        return

    print(f"\nOpening camera {camera_index} …")
    video = open_camera(camera_index)
    if video is None:
        print("\nError: Could not open camera on any backend.")
        print("Possible causes:")
        print("  1. The camera is in use by the agent (python backend/app.py)")
        print("     → Stop the agent first, or use:  python register_face.py --api")
        print("  2. No camera connected")
        print("  3. Camera permissions denied")
        return

    embeddings = []
    frame_count = 0

    print(f"\nRegistering '{name}' as a premium lounge member.")
    print(f"Collecting {MAX_SAMPLES} embedding samples.")
    print("Move your head slowly left/right/up/down for coverage.")
    print("Press 'q' to stop early.\n")

    while True:
        ret, frame = video.read()
        if not ret or frame is None:
            time.sleep(0.01)
            continue

        frame_count += 1
        faces = engine.detect(frame)

        for i in range(len(faces)):
            face = faces[i]
            x, y, w, h = int(face[0]), int(face[1]), int(face[2]), int(face[3])
            score = face[-1]

            if len(embeddings) < MAX_SAMPLES and frame_count % CAPTURE_EVERY_N_FRAMES == 0:
                try:
                    aug_embs = engine.get_augmented_embeddings(frame, face)
                    for emb in aug_embs:
                        if len(embeddings) < MAX_SAMPLES:
                            embeddings.append(np.asarray(emb, dtype=np.float32).flatten())
                except Exception:
                    pass

            # Progress display
            pct = int(len(embeddings) / MAX_SAMPLES * 100)
            progress = f"{len(embeddings)}/{MAX_SAMPLES} ({pct}%)"
            cv2.putText(frame, progress, (50, 50),
                        cv2.FONT_HERSHEY_COMPLEX, 1, (50, 50, 255), 2)
            cv2.rectangle(frame, (x, y), (x + w, y + h), (50, 50, 255), 2)
            cv2.putText(frame, "Move head slowly L/R/Up/Down", (50, 90),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
            cv2.putText(frame, f"det: {score:.2f}", (x, y - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

        cv2.imshow("Register Face - Press 'q' to quit", frame)
        k = cv2.waitKey(1)
        if k == ord('q') or len(embeddings) >= MAX_SAMPLES:
            break

    video.release()
    cv2.destroyAllWindows()

    if not embeddings:
        print("Error: No face samples captured. Try again.")
        return

    # Save using the engine
    saved = engine.save_embeddings(name, embeddings)
    print(f"\nSuccessfully registered '{name}' with {saved} embeddings.")
    print("You can now run: python run_agent.py")


def main():
    parser = argparse.ArgumentParser(description="Register face for lounge access")
    parser.add_argument("--camera", type=int, default=0, help="Camera index")
    parser.add_argument("--api", action="store_true",
                        help="Use the running backend API instead of direct camera access. "
                             "Avoids camera conflicts when the agent is running.")
    args = parser.parse_args()

    if args.api:
        name = input("Enter passenger/member name: ").strip()
        if not name:
            print("Error: Name cannot be empty.")
            return
        register_via_api(name)
    else:
        register_direct(args.camera)


if __name__ == "__main__":
    main()
