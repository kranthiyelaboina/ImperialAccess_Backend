"""Download pre-trained DNN models for face detection and recognition."""
import os
import urllib.request

MODEL_DIR = "data"
os.makedirs(MODEL_DIR, exist_ok=True)

MODELS = {
    "face_detection_yunet_2023mar.onnx": "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "face_recognition_sface_2021dec.onnx": "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
}

for filename, url in MODELS.items():
    filepath = os.path.join(MODEL_DIR, filename)
    if os.path.exists(filepath):
        size = os.path.getsize(filepath)
        print(f"[OK] {filename} already exists ({size:,} bytes)")
        continue

    print(f"Downloading {filename}...")
    try:
        urllib.request.urlretrieve(url, filepath)
        size = os.path.getsize(filepath)
        print(f"[OK] {filename} downloaded ({size:,} bytes)")
    except Exception as e:
        print(f"[FAIL] {filename}: {e}")
        print(f"  Please download manually from: {url}")
        print(f"  Save to: {filepath}")

print("\nDone. Check that all files are in the 'data/' folder.")
