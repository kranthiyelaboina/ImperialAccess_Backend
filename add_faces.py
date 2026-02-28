import cv2
import pickle
import numpy as np
import os

# =====================================================
# CONFIGURATION
# =====================================================
MAX_SAMPLES = 50              # number of face embeddings to collect
CAPTURE_EVERY_N_FRAMES = 2     # capture one sample every N frames
DETECTION_SCORE_THRESHOLD = 0.8  # minimum face detection confidence
AUGMENT = True                 # augment with brightness/flip for robustness

# Model paths
YUNET_MODEL = "data/face_detection_yunet_2023mar.onnx"
SFACE_MODEL = "data/face_recognition_sface_2021dec.onnx"

# =====================================================
# LOAD MODELS — YuNet detector + SFace recognizer
# =====================================================
print("Loading YuNet face detector...")
detector = cv2.FaceDetectorYN.create(YUNET_MODEL, "", (320, 320), DETECTION_SCORE_THRESHOLD, 0.3, 5000)

print("Loading SFace face recognizer...")
recognizer = cv2.FaceRecognizerSF.create(SFACE_MODEL, "")


def detect_and_align(frame):
    """
    Detect faces using YuNet. Returns face detections with landmarks.
    Each detection includes bounding box + 5 facial landmarks for alignment.
    """
    h, w = frame.shape[:2]
    detector.setInputSize((w, h))
    _, faces = detector.detect(frame)
    return faces if faces is not None else np.array([])


def get_face_embedding(frame, face):
    """
    Extract 128-D face embedding using SFace.
    SFace internally aligns the face using the 5 landmarks from YuNet,
    which is critical for accurate recognition.
    """
    aligned = recognizer.alignCrop(frame, face)
    embedding = recognizer.feature(aligned)
    return embedding.flatten()


def get_augmented_embeddings(frame, face):
    """
    Generate multiple embeddings from augmented face crops:
    - Original (aligned)
    - Brighter (+25)
    - Darker (-25)
    - Horizontally flipped
    """
    aligned = recognizer.alignCrop(frame, face)
    if aligned is None or aligned.size == 0:
        return []

    results = []

    # Original
    results.append(recognizer.feature(aligned).flatten())

    if not AUGMENT:
        return results

    # Brightness +25
    bright = cv2.convertScaleAbs(aligned, alpha=1.0, beta=25)
    results.append(recognizer.feature(bright).flatten())

    # Brightness -25
    dark = cv2.convertScaleAbs(aligned, alpha=1.0, beta=-25)
    results.append(recognizer.feature(dark).flatten())

    # Horizontal flip
    flipped = cv2.flip(aligned, 1)
    results.append(recognizer.feature(flipped).flatten())

    return results


# =====================================================
# INITIALIZE CAMERA
# =====================================================
video = cv2.VideoCapture(0)
if not video.isOpened():
    print("Error: Could not open camera.")
    exit(1)

embeddings = []
frame_count = 0

name = input("Enter Your Name: ").strip()
if not name:
    print("Error: Name cannot be empty.")
    video.release()
    exit(1)

print(f"\nRegistering face for '{name}'. Look at the camera...")
print(f"Collecting {MAX_SAMPLES} embedding samples. Press 'q' to stop early.")
print("Move your head slowly left/right/up/down for better coverage.\n")

while True:
    ret, frame = video.read()
    if not ret or frame is None:
        continue

    frame_count += 1
    faces = detect_and_align(frame)

    for i in range(len(faces)):
        face = faces[i]
        x, y, w, h = int(face[0]), int(face[1]), int(face[2]), int(face[3])
        score = face[-1]

        if len(embeddings) < MAX_SAMPLES and frame_count % CAPTURE_EVERY_N_FRAMES == 0:
            try:
                aug_embs = get_augmented_embeddings(frame, face)
                for emb in aug_embs:
                    if len(embeddings) < MAX_SAMPLES:
                        embeddings.append(np.asarray(emb, dtype=np.float32).flatten())
            except Exception:
                pass

        # Show progress on frame
        pct = int(len(embeddings) / MAX_SAMPLES * 100)
        progress = f"{len(embeddings)}/{MAX_SAMPLES} ({pct}%)"
        cv2.putText(frame, progress, (50, 50), cv2.FONT_HERSHEY_COMPLEX, 1, (50, 50, 255), 2)
        cv2.rectangle(frame, (x, y), (x+w, y+h), (50, 50, 255), 2)

        # Show guidance text
        cv2.putText(frame, "Move head slowly L/R/Up/Down", (50, 90),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)

        # Draw detection score
        cv2.putText(frame, f"det: {score:.2f}", (x, y - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

    cv2.imshow("Registering Face - Press 'q' to quit", frame)
    k = cv2.waitKey(1)
    if k == ord('q') or len(embeddings) >= MAX_SAMPLES:
        break

video.release()
cv2.destroyAllWindows()

num_samples = len(embeddings)
if num_samples == 0:
    print("Error: No face samples captured. Please try again.")
    exit(1)

print(f"Captured {num_samples} embedding samples for '{name}'.")

embeddings = np.asarray(embeddings)  # shape: (num_samples, 128)

# =====================================================
# SAVE DATA
# =====================================================
os.makedirs('data', exist_ok=True)

# Save names
names_path = 'data/names.pkl'
if not os.path.isfile(names_path):
    names = [name] * num_samples
else:
    with open(names_path, 'rb') as f:
        names = pickle.load(f)
    names = names + [name] * num_samples

with open(names_path, 'wb') as f:
    pickle.dump(names, f)

# Save face embeddings
faces_path = 'data/faces_data.pkl'
if not os.path.isfile(faces_path):
    with open(faces_path, 'wb') as f:
        pickle.dump(embeddings, f)
else:
    with open(faces_path, 'rb') as f:
        existing = pickle.load(f)
    combined = np.append(existing, embeddings, axis=0)
    with open(faces_path, 'wb') as f:
        pickle.dump(combined, f)

print(f"Successfully registered '{name}' with {num_samples} deep embedding samples.")
print(f"Total registered samples: {len(names)}")