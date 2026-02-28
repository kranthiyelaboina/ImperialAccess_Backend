"""
config.py — Backend configuration
"""
import os
from dotenv import load_dotenv

# Load .env file from backend/ directory
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# MongoDB connection string (defaults to local MongoDB)
MONGO_URI = os.environ.get(
    "MONGO_URI",
    "mongodb://localhost:27017/airport_lounge"
)
MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME", "airport_lounge")

# JWT
JWT_SECRET = os.environ.get("JWT_SECRET", "airport-lounge-secret-key-change-in-production")
JWT_EXPIRY_HOURS = 24

# Agent
CAMERA_INDEX = int(os.environ.get("CAMERA_INDEX", "0"))
AGENT_DEFAULT_MODE = os.environ.get("AGENT_DEFAULT_MODE", "gate")

# Face data paths (relative to project root, not backend/)
FACE_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
ATTENDANCE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "Attendance")

# Lounge pricing
LOUNGE_PRICE = 45.00
LOUNGE_CURRENCY = "USD"

# Credit card eligibility (simulated)
ELIGIBLE_CARD_NETWORKS = ["visa_infinite", "amex_platinum", "mastercard_world_elite"]
MAX_COMPLIMENTARY_VISITS = 4
