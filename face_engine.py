"""
face_engine.py — Reusable Face Detection & Recognition Engine

Wraps YuNet (detection + landmarks) and SFace (recognition) into a clean,
stateless engine that any module can import and use.
"""

import cv2
import pickle
import numpy as np
import os
from collections import defaultdict


# =====================================================
# CONFIGURATION
# =====================================================
YUNET_MODEL = "data/face_detection_yunet_2023mar.onnx"
SFACE_MODEL = "data/face_recognition_sface_2021dec.onnx"

DETECTION_SCORE_THRESHOLD = 0.75
COSINE_MATCH_THRESHOLD = 0.363     # OpenCV's recommended SFace cosine threshold
SECOND_BEST_MARGIN = 0.04          # margin over second-best to avoid confusion


class FaceEngine:
    """
    Core face detection and recognition engine.
    Stateless per-frame: give it a frame, get back detections + identities.
    """

    def __init__(self, yunet_path=YUNET_MODEL, sface_path=SFACE_MODEL,
                 detection_threshold=DETECTION_SCORE_THRESHOLD):
        self.detector = cv2.FaceDetectorYN.create(
            yunet_path, "", (320, 320), detection_threshold, 0.3, 5000
        )
        self.recognizer = cv2.FaceRecognizerSF.create(sface_path, "")
        self.database = {}      # {name: [embedding_vectors]}
        self.all_names = []     # flat list for backward compat
        self.all_embeddings = []

    # --------------------------------------------------
    # DATABASE
    # --------------------------------------------------
    def load_database(self, names_path="data/names.pkl",
                      faces_path="data/faces_data.pkl"):
        """Load registered face embeddings from pickle files."""
        if not os.path.isfile(names_path) or not os.path.isfile(faces_path):
            print("[FaceEngine] No face database found.")
            return

        with open(names_path, "rb") as f:
            labels = pickle.load(f)
        with open(faces_path, "rb") as f:
            embeddings = pickle.load(f)

        db = defaultdict(list)
        for label, emb in zip(labels, embeddings):
            db[label].append(emb)

        self.database = dict(db)
        self.all_names = labels
        self.all_embeddings = embeddings

        people = sorted(self.database.keys())
        total = sum(len(v) for v in self.database.values())
        print(f"[FaceEngine] Loaded {total} embeddings for {len(people)} people: {', '.join(people)}")

    def get_registered_names(self):
        """Return sorted list of registered people."""
        return sorted(self.database.keys())

    # --------------------------------------------------
    # DETECTION
    # --------------------------------------------------
    def detect(self, frame):
        """
        Detect faces in a frame using YuNet.
        Returns numpy array of detections (each row: x,y,w,h, landmarks..., score).
        """
        h, w = frame.shape[:2]
        self.detector.setInputSize((w, h))
        _, faces = self.detector.detect(frame)
        return faces if faces is not None else np.array([])

    # --------------------------------------------------
    # EMBEDDING
    # --------------------------------------------------
    def get_embedding(self, frame, face):
        """
        Extract 128-D SFace embedding using landmark-based alignment.
        Returns numpy array (128,) or None on failure.
        """
        try:
            aligned = self.recognizer.alignCrop(frame, face)
            return self.recognizer.feature(aligned).flatten()
        except Exception:
            return None

    def get_augmented_embeddings(self, frame, face):
        """
        Generate multiple embeddings from augmented crops for robust registration.
        """
        aligned = self.recognizer.alignCrop(frame, face)
        if aligned is None or aligned.size == 0:
            return []

        results = [self.recognizer.feature(aligned).flatten()]

        # Brightness variants
        for beta in [25, -25]:
            aug = cv2.convertScaleAbs(aligned, alpha=1.0, beta=beta)
            results.append(self.recognizer.feature(aug).flatten())

        # Horizontal flip
        flipped = cv2.flip(aligned, 1)
        results.append(self.recognizer.feature(flipped).flatten())

        return results

    # --------------------------------------------------
    # RECOGNITION
    # --------------------------------------------------
    @staticmethod
    def cosine_similarity(a, b):
        dot = np.dot(a, b)
        na, nb = np.linalg.norm(a), np.linalg.norm(b)
        return dot / (na * nb) if na > 0 and nb > 0 else 0.0

    def classify(self, embedding):
        """
        Classify a face embedding against the database.
        Returns (name: str, confidence: int 0-99).
        """
        if embedding is None or not self.database:
            return "Unknown", 0

        scores = {}
        for name, emb_list in self.database.items():
            sims = [self.cosine_similarity(embedding, stored) for stored in emb_list]
            sims.sort(reverse=True)
            top_k = min(15, len(sims))
            scores[name] = np.mean(sims[:top_k])

        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        best_name, best_score = sorted_scores[0]

        if best_score < COSINE_MATCH_THRESHOLD:
            return "Unknown", 0

        if len(sorted_scores) > 1:
            second_score = sorted_scores[1][1]
            if best_score - second_score < SECOND_BEST_MARGIN:
                return "Unknown", 0

        confidence = int(np.clip((best_score - 0.2) / 0.5 * 100, 30, 99))
        return best_name, confidence

    # --------------------------------------------------
    # HIGH-LEVEL: detect + recognize all faces in a frame
    # --------------------------------------------------
    def detect_and_recognize(self, frame):
        """
        Full pipeline: detect all faces → extract embeddings → classify.
        Returns list of dicts:
          {
            "bbox": (x, y, w, h),
            "score": float,
            "embedding": np.array(128,),
            "name": str,
            "confidence": int
          }
        """
        faces = self.detect(frame)
        results = []

        for i in range(len(faces)):
            face = faces[i]
            x, y, w, h = int(face[0]), int(face[1]), int(face[2]), int(face[3])
            det_score = float(face[-1])
            embedding = self.get_embedding(frame, face)
            name, confidence = self.classify(embedding)

            results.append({
                "bbox": (x, y, w, h),
                "score": det_score,
                "embedding": embedding,
                "name": name,
                "confidence": confidence,
                "raw_face": face,
            })

        return results

    # --------------------------------------------------
    # REGISTRATION
    # --------------------------------------------------
    def save_embeddings(self, name, embeddings,
                        names_path="data/names.pkl",
                        faces_path="data/faces_data.pkl"):
        """Save new embeddings to the database files."""
        os.makedirs("data", exist_ok=True)

        # Normalize every embedding to exactly 1-D (128,) before stacking
        flat = []
        for e in embeddings:
            arr = np.asarray(e, dtype=np.float32).flatten()
            if arr.size > 0:
                flat.append(arr)
        if not flat:
            return 0

        emb_array = np.vstack(flat)          # shape (N, 128)
        num = emb_array.shape[0]

        # Names
        if os.path.isfile(names_path):
            with open(names_path, "rb") as f:
                names = pickle.load(f)
            names = names + [name] * num
        else:
            names = [name] * num

        with open(names_path, "wb") as f:
            pickle.dump(names, f)

        # Embeddings — handle empty or corrupt existing data
        if os.path.isfile(faces_path):
            with open(faces_path, "rb") as f:
                existing = pickle.load(f)
            existing = np.asarray(existing, dtype=np.float32)
            # Only concatenate if existing actually has valid data
            if existing.ndim >= 2 and existing.shape[0] > 0 and existing.shape[1] > 0:
                combined = np.vstack([existing, emb_array])
            elif existing.ndim == 1 and existing.size > 0:
                # Single flat embedding — reshape to 2D then combine
                combined = np.vstack([existing.reshape(1, -1), emb_array])
            else:
                # Empty or corrupt — just use new data
                combined = emb_array
        else:
            combined = emb_array

        with open(faces_path, "wb") as f:
            pickle.dump(combined, f)

        # Reload into memory
        self.load_database(names_path, faces_path)
        return num
